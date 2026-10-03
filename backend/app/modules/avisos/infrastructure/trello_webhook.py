"""O webhook do Trello (item 32.2, passo 5; `docs/design/trello-integracao.md`, §8): a entrada rápida, que NÃO é a fonte da
verdade. A reconciliação do `LeitorDoTrello` (§7.6) segue lendo as actions pela API; o webhook só a faz acordar antes.

`PortaDoWebhook` é a regra da rota `POST /api/canais/trello/webhook`, sem HTTP (a rota fica em `presentation/`):
1. desligado (`trello.webhook.enabled: false`) → a rota não existe (404). Ligado, mas sem o segredo do aplicativo
   (`TRELLO_API_SECRET`) ou sem a URL configurada → FECHADA (401): nada é gravado;
2. o corpo cru é lido uma vez, com teto (`trello.webhook.max_bytes`); acima disso, 413 sem ler o resto;
3. a assinatura `X-Trello-Webhook` = base64(HMAC-SHA1(segredo, corpo + `callback_url` CONFIGURADA)), comparada em tempo
   constante. A URL é a configurada, nunca a do pedido: atrás do túnel a Central vê outro host e outro esquema;
4. só então o JSON é lido, e dele sai UMA coisa: o id da action. Autor, cartão, quadro, tipo e texto do corpo são
   DESCARTADOS (quem tivesse o segredo forjaria um corpo assinado dizendo que o autor é o dono). A linha gravada é um
   AVISO (`canal_entradas.estado = 'aviso'`, sem texto), e o líder relê a action por `GET /1/actions/{id}`;
5. nada do corpo vai a log, evento ou resposta: só o id da action, o tipo e o veredito.

`CadastroDoWebhook` mantém UM webhook por quadro no Trello (`POST /1/webhooks`, com o token só no cabeçalho): cria o que
falta e recria o desativado ou com URL diferente. Tem modo ensaio (`planejar` não escreve nada), e o
`scripts/trello-webhook.py` o usa. O cadastro é SEPARADO da flag de receber: `trello.webhook.enabled` só faz a rota
responder; o primeiro cadastro é manual (`--aplicar`, com o "vai" da orquestradora) e o recadastro de hora em hora no líder
só roda com `trello.webhook.cadastro_automatico` (desligada de fábrica). Ligar uma flag nunca vale como "vai".
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import re
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from app.config import Config
from app.models import Problem
from app.modules.avisos.adapters.trello import ClienteTrello, FalhaDoTrello
from app.modules.avisos.infrastructure.entrada_sql import EntradasDoCanal

log = logging.getLogger("poc.avisos.trello.webhook")

CABECALHO = "X-Trello-Webhook"
#: Só estas actions interessam à conversa. O resto (e o `updateCard` que não move de lista, como a descrição que o
#: próprio espelho atualiza) nem vira aviso: a reconciliação cobre qualquer engano.
TIPOS_QUE_AVISAM = frozenset({"commentCard", "updateCard"})
#: O id de uma action do Trello (hexadecimal de 24 caracteres); mais frouxo aqui, só para não gravar lixo.
_ID = re.compile(r"[A-Za-z0-9]{8,64}$")
#: A fila de avisos não cresce sem fim: cheia, o aviso é descartado (a reconciliação lê a action de qualquer jeito).
MAX_AVISOS = 500
#: Assinaturas inválidas por janela que viram problema na saúde.
LIMITE_INVALIDAS = 5
JANELA_INVALIDAS_S = 600.0
#: Teto da memória das recusadas: a rota é pública e a regra da Cloudflare só cobre o login, então assinatura inválida
#: em massa não pode crescer a memória (revisão da Android do #177). Basta para contar até LIMITE_INVALIDAS na janela.
MAX_INVALIDAS_GUARDADAS = 1000
#: Log de recusa por amostragem: no máximo uma linha por motivo a cada tantos segundos, com quantas ficaram de fora.
LOG_DE_RECUSA_A_CADA_S = 60.0
#: O que o cadastro escreve na descrição do webhook: é como a Central reconhece os DELA (e só esses).
DESCRICAO = "central-de-aparelhos:"


def assinatura_do_corpo(segredo: str, corpo: bytes, callback_url: str) -> str:
    """O `X-Trello-Webhook` esperado: base64(HMAC-SHA1(segredo do aplicativo, corpo + callbackURL))."""
    digest = hmac.new(segredo.encode("utf-8"), corpo + callback_url.encode("utf-8"), hashlib.sha1).digest()
    return base64.b64encode(digest).decode("ascii")


def _segredo(valor: object) -> str:
    obter = getattr(valor, "get_secret_value", None)
    return str(obter()).strip() if callable(obter) else ""


@dataclass(frozen=True)
class Veredito:
    """A resposta HTTP (`status`) e o motivo curto que vai ao log: `ok`, `ignorada`, `cheia`, `assinatura`, `grande`,
    `sem_config`, `desligado`. Nunca carrega nada do corpo."""

    status: int
    motivo: str


class PortaDoWebhook:
    def __init__(self, cfg: Config, repo: EntradasDoCanal, *, acordar: Callable[[], None] | None = None,
                 relogio: Callable[[], float] = time.monotonic):
        self.cfg = cfg
        self.repo = repo
        self._acordar = acordar
        self._relogio = relogio
        self._invalidas: deque[float] = deque(maxlen=MAX_INVALIDAS_GUARDADAS)
        self._ultimo_log: dict[str, float] = {}
        self._calados: dict[str, int] = {}

    # ------------------------------------------------------------------ configuração
    @property
    def ligada(self) -> bool:
        return bool(self.cfg.file.trello.webhook.enabled)

    @property
    def max_bytes(self) -> int:
        return int(self.cfg.file.trello.webhook.max_bytes)

    def _callback(self) -> str:
        return (self.cfg.file.trello.webhook.callback_url or "").strip()

    def pronta(self) -> bool:
        """Ligada E com o segredo E com a URL: só então a rota verifica (e o HEAD diz 200). Assim nunca nasce um webhook que
        a Central não consiga verificar."""
        return self.ligada and bool(_segredo(self.cfg.env.trello_api_secret)) and bool(self._callback())

    # ------------------------------------------------------------------ o pedido
    def head(self) -> Veredito:
        """O Trello faz um HEAD na URL ao criar o webhook; sem 200 ele não nasce. Sem assinatura (o Trello não manda)."""
        return Veredito(200, "ok") if self.pronta() else Veredito(404, "desligado")

    def receber(self, corpo: bytes, assinatura: str | None) -> Veredito:
        """O pedido já veio com o corpo lido (dentro do teto, que a rota confere antes). Fecha por padrão."""
        if not self.ligada:
            return Veredito(404, "desligado")
        if not self.pronta():
            return Veredito(401, "sem_config")
        esperada = assinatura_do_corpo(_segredo(self.cfg.env.trello_api_secret), corpo, self._callback())
        if not assinatura or not hmac.compare_digest(assinatura.strip().encode("utf-8"), esperada.encode("ascii")):
            self._invalidas.append(self._relogio())
            self._podar()
            return Veredito(401, "assinatura")
        return self._anotar(corpo)

    def _anotar(self, corpo: bytes) -> Veredito:
        """Assinatura conferida: lê o JSON e guarda SÓ o id da action. Corpo ilegível ou irrelevante é 200 (o Trello
        repetiria à toa) e não grava nada."""
        try:
            bruto = json.loads(corpo)
        except ValueError:
            return Veredito(200, "ignorada")
        acao = bruto.get("action") if isinstance(bruto, dict) else None
        if not isinstance(acao, dict):
            return Veredito(200, "ignorada")
        ident, tipo = acao.get("id"), acao.get("type")
        dados = acao.get("data") if isinstance(acao.get("data"), dict) else {}
        if not isinstance(ident, str) or not _ID.match(ident) or tipo not in TIPOS_QUE_AVISAM:
            return Veredito(200, "ignorada")
        if tipo == "updateCard" and not (isinstance(dados, dict) and isinstance(dados.get("listAfter"), dict)):
            return Veredito(200, "ignorada")           # não moveu de lista: nada que a conversa trate
        if self.repo.total_avisos() >= MAX_AVISOS:
            return Veredito(200, "cheia")
        if self.repo.gravar_aviso(ident) and self._acordar is not None:
            self._acordar()
        return Veredito(200, "ok")

    # ------------------------------------------------------------------ saúde
    def _podar(self) -> None:
        agora = self._relogio()
        while self._invalidas and agora - self._invalidas[0] > JANELA_INVALIDAS_S:
            self._invalidas.popleft()

    def invalidas_na_janela(self) -> int:
        self._podar()
        return len(self._invalidas)

    def amostra_de_log(self, motivo: str) -> int | None:
        """Recusa em massa não vira uma linha de log por pedido: devolve None (calar) ou quantas recusas do mesmo motivo
        ficaram de fora desde a última linha (0 na primeira)."""
        agora = self._relogio()
        ultimo = self._ultimo_log.get(motivo)
        if ultimo is not None and agora - ultimo < LOG_DE_RECUSA_A_CADA_S:
            self._calados[motivo] = self._calados.get(motivo, 0) + 1
            return None
        self._ultimo_log[motivo] = agora
        return self._calados.pop(motivo, 0)

    def problemas(self) -> list[Problem]:
        if not self.ligada or self.invalidas_na_janela() < LIMITE_INVALIDAS:
            return []
        return [Problem(
            code="trello_webhook_assinatura_invalida",
            message=f"O webhook do Trello recusou {self.invalidas_na_janela()} chamadas com assinatura inválida nos "
                    f"últimos {round(JANELA_INVALIDAS_S / 60)} min.",
            hint="Ou o TRELLO_API_SECRET do .env não é o do aplicativo (foi regerado?), ou trello.webhook.callback_url "
                 "difere da URL cadastrada no Trello, ou alguém está sondando a rota. Nada foi gravado nem executado. "
                 "A reconciliação por leitura segue cobrindo (docs/design/trello-integracao.md, §8).")]


# ---------------------------------------------------------------------------------------------- o cadastro
@dataclass(frozen=True)
class Passo:
    """Uma coisa que o cadastro faz (ou faria, no ensaio). `acao`: `criar`, `recriar`, `apagar` ou `manter`."""

    acao: str
    quadro: str
    id_webhook: str | None
    motivo: str


def callback_valida(url: str) -> str | None:
    """O motivo de a URL não servir, ou `None`. O Trello só chama https, e a URL é pública: sem parâmetro (por onde um
    token iria), sem credencial embutida, sem âncora."""
    if not url.startswith("https://"):
        return "trello.webhook.callback_url tem de começar com https://"
    if any(c in url for c in ("?", "#", "@")) or any(c.isspace() for c in url):
        return "trello.webhook.callback_url não pode ter parâmetro, âncora, credencial nem espaço"
    return None


class CadastroDoWebhook:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        #: Os quadros em que o webhook falta ou foi desativado pelo Trello, e que a última volta não conseguiu consertar.
        self._sem_webhook: list[str] = []
        self._motivo: str | None = None

    @property
    def ligado(self) -> bool:
        return bool(self.cfg.file.trello.webhook.enabled)

    def pode_agir_sozinho(self) -> bool:
        """O líder só recadastra sozinho com a chave PRÓPRIA (`cadastro_automatico`), além de `enabled` e dos três segredos.
        Sem ela, nenhuma chamada a `/1/webhooks` (nem a leitura) parte do líder."""
        env = self.cfg.env
        w = self.cfg.file.trello.webhook
        return bool(w.enabled and w.cadastro_automatico
                    and all(_segredo(v) for v in (env.trello_api_key, env.trello_token, env.trello_api_secret)))

    async def planejar(self, cliente: ClienteTrello, *, desligar: bool = False) -> list[Passo]:
        """Compara o que o Trello tem com o que a configuração pede. SÓ LÊ (`GET /1/members/me/tokens`): é o ensaio.
        `desligar`: o pedido explícito de REMOVER os webhooks da Central (só os da descrição `central-de-aparelhos:`); sem
        ele, a flag desligada não apaga nada, é erro (`ValueError`): quem desliga a rota não está pedindo para apagar."""
        trello = self.cfg.file.trello
        callback = (trello.webhook.callback_url or "").strip()
        nossos = [w for w in await cliente.webhooks_do_membro() if str(w.get("description") or "").startswith(DESCRICAO)]
        if desligar:
            return [Passo("apagar", str(w["description"])[len(DESCRICAO):], _id(w), "remoção pedida (--desligar)")
                    for w in nossos]
        if not self.ligado:
            raise ValueError("trello.webhook.enabled é false: ligue a flag antes de cadastrar "
                             "(para REMOVER os webhooks da Central, peça --desligar)")
        passos: list[Passo] = []
        erro = callback_valida(callback) if callback else "trello.webhook.callback_url não configurada"
        if erro is not None:
            raise ValueError(erro)
        quadros = list(trello.quadros)
        for quadro in quadros:
            seus = [w for w in nossos if w.get("description") == DESCRICAO + quadro]
            bons = [w for w in seus if w.get("active") is True and w.get("callbackURL") == callback]
            if bons:
                passos.append(Passo("manter", quadro, _id(bons[0]), "ativo e com a URL configurada"))
                passos += [Passo("apagar", quadro, _id(w), "duplicado") for w in seus if w is not bons[0]]
            elif seus:
                passos += [Passo("recriar" if i == 0 else "apagar", quadro, _id(w),
                                 "desativado pelo Trello" if w.get("active") is not True else "URL diferente da configurada")
                           for i, w in enumerate(seus)]
            else:
                passos.append(Passo("criar", quadro, None, "ainda não existe"))
        passos += [Passo("apagar", str(w["description"])[len(DESCRICAO):], _id(w), "quadro fora de trello.quadros")
                   for w in nossos if str(w["description"])[len(DESCRICAO):] not in quadros]
        return passos

    async def aplicar(self, cliente: ClienteTrello, passos: list[Passo]) -> None:
        """Escreve no Trello. O HEAD que o Trello faz na URL só passa com a Central no ar e a rota pronta."""
        callback = (self.cfg.file.trello.webhook.callback_url or "").strip()
        for p in passos:
            if p.acao in ("apagar", "recriar") and p.id_webhook:
                await cliente.apagar_webhook(p.id_webhook)
            if p.acao in ("criar", "recriar"):
                await cliente.criar_webhook(p.quadro, callback, DESCRICAO + p.quadro)

    async def reconciliar(self, cliente: ClienteTrello) -> list[Passo]:
        """Planeja e aplica; guarda na saúde o que não deu certo. Erro do Trello sobe (quem chama decide: 401/403 é
        `trello_recusado`)."""
        passos: list[Passo] = []
        try:
            passos = await self.planejar(cliente)
            faltavam = [p.quadro for p in passos if p.acao in ("criar", "recriar")]
            await self.aplicar(cliente, [p for p in passos if p.acao != "manter"])
        except ValueError as exc:
            self._motivo, self._sem_webhook = str(exc), []
            return passos
        except FalhaDoTrello as falha:
            self._motivo = falha.motivo
            if falha.definitiva and falha.status in (401, 403):
                raise
            self._sem_webhook = [p.quadro for p in passos if p.acao in ("criar", "recriar")]
            return passos
        self._motivo, self._sem_webhook = None, []
        if faltavam:
            log.info("trello: webhook cadastrado em %d quadro(s)", len(faltavam))
        return passos

    def problemas(self) -> list[Problem]:
        if not self.pode_agir_sozinho() or (not self._sem_webhook and self._motivo is None):
            return []
        return [Problem(
            code="trello_webhook_inativo",
            message="O webhook do Trello não está cadastrado ou foi desativado, e a Central não conseguiu cadastrá-lo."
                    + (f" ({self._motivo})" if self._motivo else ""),
            hint="O Trello faz um HEAD na URL antes de criar o webhook: ela precisa estar no ar (túnel, ADR-073) e "
                 "trello.webhook.callback_url tem de ser a pública, com https. Enquanto isso a reconciliação por leitura "
                 "cobre, a cada trello.reconciliar_s; nada se perde. `python scripts/trello-webhook.py --ensaio` mostra "
                 "o que seria feito; desligue trello.webhook.cadastro_automatico para a Central parar de tentar.")]


def _id(w: dict[str, object]) -> str | None:
    v = w.get("id")
    return v if isinstance(v, str) and v else None
