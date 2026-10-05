"""A conversa de volta pelo Trello (item 32.2, passo 4; `docs/design/trello-integracao.md`, §3 e §7.6 e 7.7): o dono comenta
ou move um cartão, e a Central responde no próprio cartão. É o canal fino sobre a `ConversaDoCanal` do 28.15: o Trello só
TRADUZ a action em `Recebida` (`recebida_da_action`, a mesma função do webhook do passo 5) e cumpre a `SaidaDaConversa`
(`SaidaDoTrello`); a gramática, a identidade, a credencial, o dedupe e as portas do painel são os do Telegram.

Regras de segurança deste canal (cada uma tem teste em `tests/test_trello_leitor.py`):
- **Só o autor LIDO PELA API comanda.** `do_dono` = `idMemberCreator == trello.membro_dono` (ou um de
  `trello.membros_autorizados`, vazia de fábrica) E o quadro é um dos `trello.quadros`. O texto de quem não é o dono é
  gravado sem texto e nunca vira comando. O operador gravado é `trello:<idMember>` do AUTOR da action.
- **Mover para ✅ Aprovado, `/aprovar` e "sim" NÃO aprovam**: no Trello nem o dono autoriza ação real em conta real. A
  Central responde que a aprovação se confirma no painel ou no Telegram e não chama o `decidir`. Vetar (mover para ⛔,
  `/vetar`, "não") veta, porque vetar não causa ação no mundo real.
- **Resposta a uma pergunta de execução** passa pela triagem de credencial (a forma do texto e a pergunta aberta); a
  credencial é recusada sem eco, e a resposta pede ao dono que apague o comentário (a Central não apaga conteúdo dele).
- **`/para` e texto livre ficam desligados** (`trello.comando_livre`). Ligado, o Trello só MOSTRA a prévia: não executa.
- **Nada do que a Central escreve no Trello leva** segredo, e-mail, @conta, IP, nem o texto de um pedido ou de uma
  evidência: as respostas são frases fixas com contagens e ids curtos, e a `SaidaDoTrello` ainda passa tudo pela redação
  dos avisos. Toda escrita começa com `🤖 ANA · HH:MMZ · ` e é ignorada na volta seguinte (o token é o do dono, então o
  comentário da Central chega como action do dono).
- **O convidado** (autor que não é o dono) não executa nada. Com `trello.responder_convidados` a pergunta pura recebe o
  resumo do `/status`; o pedido recebe um comentário fixo e um aviso ao dono, sem o texto nem o nome do convidado.

Cursor e 1ª subida: o cursor é a última action lida de cada quadro (`trello_cursor`). Quadro sem linha é a 1ª subida: o
cursor vai para a action mais nova e o histórico NÃO é tratado, como o Telegram descarta o dele. Roda só no líder da
trava `avisos`, a cada `trello.reconciliar_s`, e só com `trello.enabled`, o dono configurado e os dois segredos.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.config import Config, TrelloCfg
from app.models import Problem
from app.modules.avisos.adapters.trello import ClienteTrello, FalhaDoTrello
from app.modules.avisos.application.entrada import AJUDA, Intencao, rotear
from app.modules.avisos.application.entrega import FalhaDeEnvio
from app.modules.avisos.application.espelho import PREFIXO_DA_IA, prefixo_da_ia
from app.modules.avisos.domain.mensagem import ROTINA, Aviso, chave_do_fato, titulo_do_aviso
from app.modules.avisos.domain.privacidade import texto_seguro
from app.modules.avisos.infrastructure.entrada import (
    RESPOSTA_DO_REPASSE,
    ConversaDoCanal,
    Linha,
    PortasDaCentral,
    Recebida,
    SaidaDaConversa,
)
from app.modules.avisos.infrastructure.entrada_sql import EntradasDoCanal
from app.modules.avisos.infrastructure.espelho_sql import CartoesDoTrello, CursorDoTrello
from app.modules.avisos.infrastructure.trello_webhook import CadastroDoWebhook
from app.taskqueue.travas import AVISOS
from app.util import parse_iso, to_iso

log = logging.getLogger("poc.avisos.trello")

#: O fato do cartão vai em `responde_a`, com este prefixo: não é um reply (o Trello não tem), e o prefixo impede a regra do
#: Telegram ("reply a mensagem que a Central não mandou é da orquestradora") de se aplicar por engano.
PREFIXO_DO_FATO = "fato:"
#: Comentário que começa com isto é de uma IA (a Central ou uma sessão), nunca um pedido do dono, que não usa 🤖.
MARCA_DE_IA = "🤖"
#: O `since` de um quadro que ainda não tem action nenhuma.
SEM_HISTORICO = "1970-01-01T00:00:00.000Z"
#: A leitura que não anda há mais que isto vezes `reconciliar_s` vira `trello_leitor_atrasado`.
FATOR_DE_ATRASO = 3
#: Teto do comentário da Central (o Trello aceita 16 mil; a conversa é curta).
TAMANHO_MAX = 1000
#: De quanto em quanto tempo o cadastro do webhook confere o Trello.
CADASTRO_A_CADA_S = 3600.0
#: Quantas reações a convidados por hora: acima disso a Central se cala (um convidado não a faz falar sem parar).
CONVIDADOS_POR_HORA = 10
_CAUSA = {401: "chave ou token inválido, ou o token foi revogado", 403: "o token não tem permissão neste quadro"}

RESPOSTA_APROVAR_FORA = "para aprovar, confirme no painel ou no Telegram"
RESPOSTA_COMANDO_LIVRE = "comando livre está desligado no Trello; use o painel ou o Telegram"
RESPOSTA_SO_O_DONO = "só o dono decide aprovações e responde perguntas das execuções"
RESPOSTA_CONVIDADO = "recebido; aguardando o dono"
AJUDA_DO_TRELLO = ("\nNo Trello: /vetar e /responder valem aqui; /aprovar (e mover o cartão para Aprovado) só pede a "
                   "confirmação no painel ou no Telegram.")
#: A frase de quem não apagou, trocada pela do cartão: a Central não tenta apagar comentário nenhum do Trello.
_SEM_APAGAR_DO_CHAT = "mas não consegui apagar a mensagem daqui. Apague-a do chat."
_SEM_APAGAR_DO_CARTAO = "mas a Central não apaga comentários do Trello. Apague o comentário do cartão."

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_CONTA = re.compile(r"(?<![\w@])@\w{2,}")
_IP = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")


def _utc() -> datetime:
    return datetime.now(timezone.utc)


def _texto(v: object) -> str | None:
    return v if isinstance(v, str) and v else None


def _mapa(v: object) -> Mapping[str, object] | None:
    return v if isinstance(v, Mapping) else None


def sanear(texto: str, redigir: Callable[[str], str]) -> str:
    """A última barreira do que sai para o Trello (servidor de terceiros): a redação dos avisos (segredo) e, por cima, e-mail,
    @conta e IP. O desenho evita copiar texto de origem; isto cobre o que escapar (uma mensagem de erro do serviço)."""
    sem = _IP.sub("[ip]", _CONTA.sub("[conta]", _EMAIL.sub("[e-mail]", redigir(texto))))
    return sem.strip()[:TAMANHO_MAX]


@dataclass(frozen=True)
class RefDoTrello:
    """O que o Trello precisa saber da mensagem da pessoa, no único campo de referência da `Recebida`
    (`ref_mensagem`): o CARTÃO onde a Central responde, a ACTION (o dedupe e a ref do comentário) e o AUTOR (o operador
    `trello:<idMember>`; a `canal_entradas` não tem coluna de autor). Os ids do Trello são hexadecimais, sem barra."""

    card: str
    action: str
    autor: str

    def __str__(self) -> str:
        return f"{self.card}/{self.action}/{self.autor}"

    @classmethod
    def ler(cls, ref: object) -> RefDoTrello | None:
        partes = ref.split("/") if isinstance(ref, str) else []
        return cls(*partes) if len(partes) == 3 and partes[0] else None


def _epoch(data: object) -> float | None:
    try:
        d = parse_iso(data) if isinstance(data, str) else None
    except ValueError:
        return None
    return d.timestamp() if d is not None else None


def recebida_da_action(action: Mapping[str, object], cfg: TrelloCfg, *,
                       chave_do_cartao: Callable[[str], str | None],
                       da_central: Callable[[str], bool] | None = None, quadro: str | None = None) -> Recebida | None:
    """A TRADUÇÃO do Trello: uma action (a da reconciliação, ou a relida pela API a partir do aviso do webhook) vira
    `Recebida`. Pura: a chave do cartão e o reconhecimento do que a Central escreveu chegam por parâmetro.

    - `id_externo` = a id da action; `escrita_em` = `action.date` (a regra de idade vale também aqui);
    - `do_dono` = autor igual a `membro_dono` (ou autorizado) E o quadro é um de `quadros` E há cartão;
    - `commentCard` → `mensagem`; `updateCard` que leva um cartão de APROVAÇÃO para a lista Aprovado/Vetado → `botao`
      "sim"/"não"; o resto (cartão novo, outra lista, outro tipo de cartão, o comentário da própria Central, quadro
      fora da config) → `outro`, sem texto;
    - o fato vai em `responde_a` (`fato:<chave>`), e o cartão, a action e o autor em `ref_mensagem` (`RefDoTrello`).
    Sem `id` na action, `None`. `quadro` é o consultado, para a action que não traga `data.board`."""
    ident = _texto(action.get("id"))
    if ident is None:
        return None
    dados = _mapa(action.get("data")) or {}
    card = _texto((_mapa(dados.get("card")) or {}).get("id"))
    quadro_da_action = _texto((_mapa(dados.get("board")) or {}).get("id")) or quadro
    autor = _texto(action.get("idMemberCreator")) or ""
    escrita = _epoch(action.get("date"))
    no_quadro = quadro_da_action is not None and quadro_da_action in cfg.quadros and card is not None
    # Fecha por padrão: sem dono configurado, sem autor ou sem quadro, ninguém comanda ("" == "" não pode abrir a porta).
    do_dono = bool(no_quadro and autor and (autor == cfg.membro_dono or autor in cfg.membros_autorizados))
    ref = str(RefDoTrello(card, ident, autor)) if card is not None else None

    def outro() -> Recebida:
        return Recebida(id_externo=ident, ordem=None, tipo="outro", do_dono=False, texto="", ref_mensagem=ref,
                        escrita_em=escrita)

    if not no_quadro or card is None:
        return outro()
    chave = chave_do_cartao(card)
    fato = f"{PREFIXO_DO_FATO}{chave}" if chave else None
    tipo_da_action = action.get("type")
    if tipo_da_action == "commentCard":
        texto = str(dados.get("text") or "")
        # Qualquer 🤖 no começo é de IA, não do dono: a Central, a sessão Canais, a orquestradora (`🤖 ORQ`) e os comentários
        # antigos (`🤖 HH:MMZ ·`) escrevem todos com o token dele. Regra C-07 de docs/dominios/canais.md.
        if texto.lstrip().startswith((PREFIXO_DA_IA, MARCA_DE_IA)) or (da_central is not None and da_central(ident)):
            return outro()                    # o que a própria Central escreveu volta como action do dono (o token é o dele)
        return Recebida(id_externo=ident, ordem=None, tipo="mensagem", do_dono=do_dono, texto=texto,
                        ref_mensagem=ref, responde_a=fato, escrita_em=escrita)
    if tipo_da_action == "updateCard" and chave is not None and chave.startswith("approval:"):
        para = _texto((_mapa(dados.get("listAfter")) or {}).get("id"))
        listas = cfg.listas
        verbo = ("sim" if para == listas.get("aprovado") else "não" if para == listas.get("vetado") else None) \
            if para else None
        if verbo is not None:
            return Recebida(id_externo=ident, ordem=None, tipo="botao", do_dono=do_dono, texto=verbo,
                            ref_mensagem=ref, responde_a=fato, escrita_em=escrita)
    return outro()


class SaidaDoTrello:
    """`SaidaDaConversa` sobre o `ClienteTrello`: `responder` comenta no cartão (o da `RefDoTrello` que a conversa passa em
    `responde_a`), com o prefixo `🤖 ANA · HH:MMZ · `. Não há botões; `apagar` devolve False (a Central não apaga conteúdo do
    dono, e a resposta pede que ele apague). Sem cartão em `responde_a` (a ajuda inicial, o aviso de mensagens antigas) não
    há onde comentar: devolve `None`. Uma falha do Trello vira `FalhaDeEnvio` (o que a conversa já trata) e fica em `falha`
    para o leitor expor a recusa na saúde."""

    def __init__(self, cliente: ClienteTrello, redigir: Callable[[str], str], relogio: Callable[[], datetime] = _utc):
        self._cliente = cliente
        self._redigir = redigir
        self._relogio = relogio
        self.falha: FalhaDoTrello | None = None

    async def responder(self, texto: str, *, responde_a: str | None = None,
                        botoes: list[tuple[str, str]] | None = None) -> str | None:
        ref = RefDoTrello.ler(responde_a)
        if ref is None:
            return None
        corpo = prefixo_da_ia(self._relogio()) + sanear(texto, self._redigir)
        try:
            return await self._cliente.comentar(ref.card, corpo)
        except FalhaDoTrello as falha:
            self.falha = falha
            raise FalhaDeEnvio(falha.motivo, definitiva=falha.definitiva, status=falha.status) from None

    async def nome_do_cartao(self, card: str) -> str | None:
        """O nome do cartão para o pedido de confirmação do comentário do dono (28.30); `None` se o Trello falhar."""
        try:
            return await self._cliente.nome_do_cartao(card)
        except FalhaDoTrello as falha:
            log.warning("trello: nome do cartão não lido (%s)", falha.motivo)
            return None

    async def confirmar_botao(self, botao_id: str) -> None:
        return None

    async def tirar_botoes(self, ref_mensagem: str) -> None:
        return None

    async def apagar(self, ref_mensagem: str) -> bool:
        return False


#: 28.30: o comentário do dono num cartão sem aviso da Central (os cartões do plano).
REPASSE_COMENTARIO = "comentario"
#: O operador da linha sem autor lido: não casa com nenhum `membro_dono` (os ids do Trello são hexadecimais).
AUTOR_DESCONHECIDO = "desconhecido"
TIPO_DO_COMENTARIO = "trello.comentario"
#: O aviso de que o teto por hora segurou os pedidos: só informa (nível 3, vai à janela da rotina). O nome não começa
#: por `trello.comentario`, que a fila nunca agrupa (`SEM_AGRUPAR`).
TIPO_DO_TETO = "trello.teto_de_comentarios"
#: O trecho do comentário que vai ao Telegram (o inteiro fica no cartão e na linha da entrada).
TEXTO_DO_COMENTARIO_MAX = 300
RESPOSTA_COMENTARIO_SEM_TELEGRAM = ("Recebi o seu comentário e repassei à orquestradora. A confirmação pelo Telegram não "
                                    "saiu (o canal está desligado): confirme pelo painel ou pelo chat da sessão.")
#: As travas do laço (revisão da #314): o 🤖 é convenção, e uma sessão que o esquece gera pedido ao dono. No máximo UM
#: pedido em aberto por cartão (o que o dono ainda não respondeu, nas últimas `ABERTO_S`) e `COMENTARIOS_POR_HORA` pedidos
#: por hora no quadro; acima disso o comentário só vai à orquestradora, e o dono recebe UMA linha por hora dizendo que parou.
COMENTARIOS_POR_HORA = 6
ABERTO_S = 24 * 3600
RESPOSTA_COMENTARIO_JA_ABERTO = ("Recebi o seu comentário e repassei à orquestradora. Já há um pedido de confirmação deste "
                                 "cartão esperando você no Telegram: responda lá.")
RESPOSTA_COMENTARIO_NO_TETO = (f"Recebi o seu comentário e repassei à orquestradora. Parei de pedir confirmação no Telegram "
                               f"nesta hora (mais de {COMENTARIOS_POR_HORA} pedidos): confirme pelo chat da sessão.")


class ComentariosDoTrello:
    """28.30 (revisão da #314): a conferência que a conversa do Telegram faz antes de o sim do dono valer. Relê a action do
    comentário pela API: o mesmo texto que foi gravado é `igual`; outro texto, `mudou`; 404, `apagado`; outra falha ou o
    Trello desligado, `sem_conferir`; sem linha gravada, `desconhecido`."""

    def __init__(self, repo: EntradasDoCanal, cliente: Callable[[], ClienteTrello | None]):
        self.repo = repo
        self._cliente = cliente

    async def conferir(self, action: str) -> tuple[str, str | None]:
        ident = self.repo.id_de(action)
        linha = self.repo.linha(ident) if ident is not None else None
        gravado = _texto(linha.get("texto")) if linha is not None else None
        if gravado is None:
            return "desconhecido", None
        cliente = self._cliente()
        if cliente is None:
            return "sem_conferir", gravado
        try:
            bruta = await cliente.acao(action)
        except FalhaDoTrello as falha:
            return ("apagado" if falha.status == 404 else "sem_conferir"), gravado
        atual = _texto((_mapa((bruta if isinstance(bruta, dict) else {}).get("data")) or {}).get("text"))
        return ("igual" if atual is not None and atual.strip() == gravado.strip() else "mudou"), gravado


class ConversaDoTrello(ConversaDoCanal):
    """A `ConversaDoCanal` com as regras do Trello (ver o docstring do módulo). Só ESTREITA o comum: a gramática e as
    políticas de identidade, credencial, idade, tamanho e limite de taxa são as do Telegram."""

    def __init__(self, cfg: Config, repo: EntradasDoCanal, portas: PortasDaCentral, *, recusa: Callable[[str], bool],
                 redigir: Callable[[str], str], relogio: Callable[[], float] | None = None,
                 avisar_dono: Callable[[Aviso], bool] | None = None):
        super().__init__(cfg, repo, portas, recusa=recusa, redigir=redigir,
                         operador=f"trello:{cfg.file.trello.membro_dono}", relogio=relogio)
        #: 28.30: o pedido de confirmação do comentário do dono vai ao Telegram dele pela fila de avisos.
        self.avisar_dono = avisar_dono

    def _idade_max_s(self) -> float:
        return self.cfg.file.trello.idade_max_s

    # ------------------------------------------------------------------ identidade e fato
    def _autor(self, linha: Mapping[str, object]) -> str:
        ref = RefDoTrello.ler(linha.get("ref_mensagem"))
        return ref.autor if ref is not None else ""

    async def _tratar(self, saida: SaidaDaConversa, linha: Linha) -> None:
        # O operador é o AUTOR da action (`decided_by='trello:<idMember>'`), não um valor fixo da instância. Sem autor
        # lido, ninguém: a linha não vale como do dono (revisão da #312, 04/10 20:36Z).
        self.operador = f"trello:{self._autor(linha) or AUTOR_DESCONHECIDO}"
        await super()._tratar(saida, linha)

    def _intencao(self, linha: Linha) -> Intencao:
        texto = str(linha.get("texto") or "")
        responde_a = str(linha.get("responde_a") or "")
        fato = responde_a[len(PREFIXO_DO_FATO):] if responde_a.startswith(PREFIXO_DO_FATO) else None
        if fato is None and not texto.lstrip().startswith("/"):
            # Comentário num cartão que não é de aviso da Central (os cartões do plano, 28.30): não é pedido nem comando,
            # mas também não fica mudo. Vai à orquestradora e pede a confirmação do dono no Telegram (`_comentario`).
            if not texto.strip():
                return Intencao("vazia")
            return Intencao("orquestradora", texto=texto.strip(), repasse=REPASSE_COMENTARIO)
        return rotear(texto, fato=fato)

    # ------------------------------------------------------------------ o que cada intenção faz aqui
    async def _botao(self, saida: SaidaDaConversa, linha: Linha) -> None:
        """Mover o cartão de aprovação: o texto é "sim"/"não" e o fato é o cartão. Passa pelo MESMO caminho do comentário."""
        i = self._intencao(linha)
        if i.tipo in ("aprovar", "vetar"):
            await self._agir_do_autor(saida, linha, i)
        else:
            self.repo.marcar(self._id(linha), "ignorada", intencao=i.tipo, de=("recebida",))

    async def _agir(self, saida: SaidaDaConversa, linha: Linha, i: Intencao) -> None:
        await self._agir_do_autor(saida, linha, i)

    async def _agir_do_autor(self, saida: SaidaDaConversa, linha: Linha, i: Intencao) -> None:
        autor = self._autor(linha)
        dono = bool(autor) and autor == self.cfg.file.trello.membro_dono
        if i.repasse == REPASSE_COMENTARIO:
            if dono:
                await self._comentario(saida, linha, i)
            else:
                # A anotação de quem o dono autorizou segue como antes: nem pedido, nem pergunta a ele.
                self.repo.marcar(self._id(linha), "ignorada", intencao="vazia", de=("recebida",))
        elif not dono and i.tipo in ("aprovar", "vetar", "responder"):
            # Quem o dono autorizou a PEDIR não decide aprovação nem responde pergunta de execução.
            await self._feita(saida, linha, i, RESPOSTA_SO_O_DONO)
        elif i.tipo == "ajuda":
            await self._feita(saida, linha, i, AJUDA + AJUDA_DO_TRELLO)
        else:
            await ConversaDoCanal._agir(self, saida, linha, i)

    async def _comentario(self, saida: SaidaDaConversa, linha: Linha, i: Intencao) -> None:
        """28.30: o comentário do dono num cartão. O comentário sozinho não autoriza nada (a ANA e as sessões escrevem
        no quadro com a conta dele): vai à orquestradora, e o dono confirma com sim ou não no Telegram. O cartão recebe
        a resposta na hora. No Telegram só saem o nome do cartão e o texto que passam inteiros pelos filtros do 28.31."""
        ref = RefDoTrello.ler(linha.get("ref_mensagem"))
        pedida, resposta = False, RESPOSTA_COMENTARIO_SEM_TELEGRAM
        trava = self._trava_do_comentario(linha, ref) if ref is not None and self.avisar_dono is not None else None
        if trava is not None:
            resposta = trava
        elif ref is not None and self.avisar_dono is not None:
            nomes = self._nomes_de_persona()
            bruto = await saida.nome_do_cartao(ref.card) if isinstance(saida, SaidaDoTrello) else None
            nome = texto_seguro(bruto, nomes, self._redigir)
            texto = texto_seguro(i.texto, nomes, self._redigir)
            if texto is not None and len(texto) > TEXTO_DO_COMENTARIO_MAX:
                texto = texto[:TEXTO_DO_COMENTARIO_MAX].rstrip() + "…"
            pedida = self.avisar_dono(Aviso(
                chave=chave_do_fato("comentario", ref.action, ref.card), tipo=TIPO_DO_COMENTARIO,
                titulo=titulo_do_aviso(f"💬 Você comentou no cartão «{nome}»" if nome else
                                       "💬 Você comentou num cartão do Trello"),
                corpo="\n".join([
                    f"«{texto}»" if texto else "O texto fica no cartão: tem dado que não sai por aqui.",
                    "Comentário no Trello sozinho não autoriza nada; o sim daqui é que vale.",
                    "Espera você: responda sim ou não a esta mensagem."]),
                link=f"https://trello.com/c/{ref.card}"))
            if pedida:
                resposta = RESPOSTA_DO_REPASSE[REPASSE_COMENTARIO]
        self.repo.marcar(self._id(linha), "orquestradora", intencao=i.tipo, destino="orquestradora",
                         previa={"repasse": REPASSE_COMENTARIO, "texto": i.texto, "confirmacao_pedida": pedida},
                         de=("recebida", "pergunta"))
        await self._responder(saida, linha, resposta)

    def _trava_do_comentario(self, linha: Linha, ref: RefDoTrello) -> str | None:
        """A resposta do cartão quando uma das travas segura o pedido de confirmação, ou `None` (pode pedir)."""
        agora = datetime.fromtimestamp(self._agora(), timezone.utc)
        abertos = [a for a in self.repo.comentarios_com_pedido(desde=to_iso(agora - timedelta(seconds=ABERTO_S)),
                                                                card=ref.card, exceto=self._id(linha))
                   if not self.repo.comentario_respondido(a)]
        if abertos:
            return RESPOSTA_COMENTARIO_JA_ABERTO
        if len(self.repo.comentarios_com_pedido(desde=to_iso(agora - timedelta(hours=1)))) < COMENTARIOS_POR_HORA:
            return None
        if self.avisar_dono is not None:
            # Uma linha por hora (a chave é a hora): a fila deduplica o resto.
            self.avisar_dono(Aviso(
                chave=chave_do_fato("comentario-teto", agora.strftime("%Y-%m-%dT%H")), tipo=TIPO_DO_TETO, nivel=ROTINA,
                titulo=titulo_do_aviso("💬 Parei de pedir confirmação dos seus comentários nesta hora"),
                corpo=(f"Mais de {COMENTARIOS_POR_HORA} comentários pediram confirmação em 1 h. Os novos vão à orquestradora "
                       "sem pergunta até a próxima hora.\nNada a fazer."),
                link=None))
        return RESPOSTA_COMENTARIO_NO_TETO

    async def _decidir(self, saida: SaidaDaConversa, linha: Linha, i: Intencao) -> None:
        if i.tipo == "vetar":
            await super()._decidir(saida, linha, i)        # vetar não causa ação no mundo real: o mesmo serviço do painel
            return
        # Aprovar NÃO decide: no Trello nem o dono autoriza ação real em conta real. Confere o id (um id velho recebe o
        # "não achei"), mas o `decidir` não é chamado.
        aid, erro = self._um_id(i.ref or "", self.portas.aprovacoes_pendentes(), "aprovação pendente")
        await self._feita(saida, linha, i, erro if aid is None else RESPOSTA_APROVAR_FORA,
                          alvo=f"approval:{aid}" if aid is not None else None)

    async def _previa(self, saida: SaidaDaConversa, linha: Linha, i: Intencao, texto: str,
                      instance_ids: list[str] | None) -> None:
        if not self.cfg.file.trello.comando_livre:
            await self._feita(saida, linha, i, RESPOSTA_COMANDO_LIVRE)
            return
        # Ligado, o Trello só MOSTRA a prévia (leitura): sem botão, sem comando de executar. O texto do pedido não volta.
        p = self.portas.previa(texto, instance_ids)
        alvos = ", ".join(str(a.get("instance_id") or "a escolher") for a in p.alvos)
        resumo = f"Prévia: {alvos}." if alvos and not p.perguntas else "Prévia: a Central precisa saber o destino."
        await self._feita(saida, linha, i, f"{resumo} O Trello não executa pedido: para rodar, mande o mesmo pedido pelo "
                                           "painel ou pelo Telegram.")

    def _texto_pendencias(self) -> str:
        """Só o tipo e o id curto: o resumo de uma aprovação traz o alvo e o texto que sairia (@conta, nome de persona), e
        isso não vai a um servidor de terceiros."""
        itens = self.portas.pendencias()
        if not itens:
            return "Nada espera você agora."
        linhas = [f"{len(itens)} esperando você:"]
        for p in itens[:15]:
            linhas.append(f"- {p.ident[-6:]}: {'aprovação' if p.tipo == 'aprovacao' else 'pergunta de execução'}")
        if len(itens) > 15:
            linhas.append(f"... e mais {len(itens) - 15} no painel.")
        linhas.append("O que cada uma pede está no painel. /vetar veta daqui; para aprovar, confirme no painel ou no "
                      "Telegram.")
        return "\n".join(linhas)

    def _texto_do_desfecho(self, texto: str) -> str:
        # O desfecho do painel traz a "Evidência" (texto do aparelho, onde moram nome e @conta): fica de fora do Trello.
        # O laço (reenvio na falha passageira, plano esquecido) é o da conversa (28.38).
        curto = "\n".join(t for t in texto.splitlines() if not t.startswith("Evidência"))
        return self._redigir(curto) + "\nO detalhe está no painel."

    async def _enviar(self, saida: SaidaDaConversa, texto: str, *, origem: str, responde_a: str | None = None,
                      botoes: list[tuple[str, str]] | None = None, entrada_id: int | None = None,
                      exigir: bool = False) -> None:
        # `exigir` vale aqui como no Telegram, de propósito: é o envio que não pode falhar calado (a prévia da porta,
        # 28.27). Se a resposta no cartão não sai, a falha sobe e quem chamou abandona a linha, em vez de deixar o dono
        # sem o que aprovar. Os botões não existem no cartão; o `exigir` não depende deles.
        await super()._enviar(saida, texto.replace(_SEM_APAGAR_DO_CHAT, _SEM_APAGAR_DO_CARTAO), origem=origem,
                              responde_a=responde_a, botoes=None, entrada_id=entrada_id, exigir=exigir)

    def antiga(self, r: Recebida) -> bool:
        """Escrita há mais que `trello.idade_max_s` (a Central estava fora): nem o convidado recebe resposta a isso."""
        return r.escrita_em is not None and self._agora() - r.escrita_em > self._idade_max_s()

    async def responder_a(self, saida: SaidaDaConversa, r: Recebida, texto: str) -> None:
        """A resposta da Central a uma mensagem que NÃO foi tratada como comando (a do convidado)."""
        await self._enviar(saida, texto, origem="resposta", responde_a=r.ref_mensagem,
                           entrada_id=self.repo.id_de(r.id_externo))


def _pergunta_pura(texto: str) -> bool:
    """Pergunta, não pedido: `/status`, `/pendencias`, `/ajuda`, ou texto sem barra terminado em "?"."""
    t = texto.strip()
    if not t:
        return False
    if t.startswith("/"):
        return rotear(t).tipo in ("status", "pendencias", "ajuda")
    return t.endswith("?")


class LeitorDoTrello:
    """O laço da reconciliação (`GET /1/boards/{id}/actions?since=<cursor>`) sobre a `ConversaDoTrello`. Cada action vira
    uma `Recebida`; a conversa grava (dedupe pela id da action) e trata. O webhook do passo 5 chega à mesma tradução."""

    def __init__(self, cfg: Config, repo: EntradasDoCanal, cartoes: CartoesDoTrello, cursor: CursorDoTrello,
                 portas: PortasDaCentral, *, lider: Callable[[str], int | None], recusa: Callable[[str], bool],
                 redigir: Callable[[str], str], avisar_dono: Callable[[Aviso], bool] | None = None,
                 relogio: Callable[[], datetime] = _utc, cliente: ClienteTrello | None = None,
                 dormir: Callable[[float], Awaitable[None]] = asyncio.sleep, cadastro: CadastroDoWebhook | None = None):
        self.cfg = cfg
        self.repo = repo
        self.cartoes = cartoes
        self.cursor = cursor
        self.portas = portas
        self._lider = lider
        self._redigir = redigir
        self._avisar_dono = avisar_dono
        self._relogio = relogio
        self._cliente = cliente
        self._dormir = dormir
        self.conversa = ConversaDoTrello(cfg, repo, portas, recusa=recusa, redigir=redigir,
                                         relogio=lambda: relogio().timestamp(), avisar_dono=avisar_dono)
        self._recusada: FalhaDoTrello | None = None
        self._reacoes: deque[float] = deque()      # as reações a convidados na última hora (monotônico)
        #: O webhook (§8) só ANOTA o id da action e acorda o laço; a volta de avisos relê a action pela API.
        self._acordado = asyncio.Event()
        self._cadastro = cadastro
        self._cadastro_em: float | None = None

    # ------------------------------------------------------------------ configuração e saúde
    @property
    def ligado(self) -> bool:
        return bool(self.cfg.file.trello.enabled)

    def cliente(self) -> ClienteTrello | None:
        """O cliente pronto, ou `None` (desligado ou segredo faltando; a saúde já avisa `trello_sem_segredo`). Guardado: o
        balde de requisições vale para a vida do processo."""
        if not self.ligado:
            return None
        if self._cliente is None:
            env = self.cfg.env
            chave = env.trello_api_key.get_secret_value().strip() if env.trello_api_key else ""
            token = env.trello_token.get_secret_value().strip() if env.trello_token else ""
            if not (chave and token):
                return None
            self._cliente = ClienteTrello(chave, token)
        return self._cliente

    def problemas(self) -> list[Problem]:
        if not self.ligado:
            return []
        achados: list[Problem] = []
        f = self._recusada
        if f is not None:
            if f.status in _CAUSA:
                achados.append(Problem(
                    code="trello_recusado",
                    message=f"A leitura do Trello está parada: o Trello recusou a Central ({f.status}).",
                    hint=f"Causa provável: {_CAUSA[f.status]}. Confira TRELLO_API_KEY e TRELLO_TOKEN no .env "
                         "(docs/operacao.md) ou desligue trello.enabled; a Central tenta de novo sozinha a cada volta."))
            else:
                achados.append(Problem(
                    code="trello_pedido_invalido",
                    message=f"A leitura do Trello está parada: o Trello não aceitou o pedido ({f.status}).",
                    hint="Confira os ids em trello.quadros; um quadro apagado ou sem acesso produz isto. A Central tenta "
                         "de novo sozinha a cada volta."))
        # A "última leitura que deu certo" (`atualizado_em` do cursor, que anda mesmo sem action nova): um quadro quieto não
        # é atraso. Sem nenhuma linha ainda (1ª subida), nada a dizer.
        ultima = self.cursor.ultima_leitura()
        limite = FATOR_DE_ATRASO * float(self.cfg.file.trello.reconciliar_s)
        if ultima is not None and (self._relogio() - ultima).total_seconds() > limite:
            achados.append(Problem(
                code="trello_leitor_atrasado",
                message="A leitura das actions do Trello não anda: os comentários e os cartões movidos não estão sendo "
                        "tratados.",
                hint=f"A última leitura que deu certo foi há mais de {FATOR_DE_ATRASO} × trello.reconciliar_s. Veja se o "
                     "líder da trava `avisos` está de pé e se o Trello responde (trello_recusado, trello_limite); nada "
                     "foi executado nesse intervalo."))
        return achados

    # ------------------------------------------------------------------ uma volta
    async def uma_volta(self, *, so_avisos: bool = False) -> int:
        """Lê, grava e trata. Devolve quantas actions chegaram (0 quando pulou). `so_avisos`: só as que o webhook anotou
        (o laço acordou por ele), sem varrer os quadros."""
        cliente = self.cliente()
        trello = self.cfg.file.trello
        if cliente is None or not trello.membro_dono or self._lider(AVISOS) is None:
            return 0           # sem dono configurado ninguém comanda: nem se lê
        saida = SaidaDoTrello(cliente, self._redigir, self._relogio)
        lidas = 0
        try:
            lidas += await self._tratar_avisos(cliente, saida)      # antes dos quadros: a releitura deles cai no dedupe
            if not so_avisos:
                for quadro in trello.quadros:
                    lidas += await self._ler_quadro(cliente, saida, quadro)
            await self.conversa.tratar_pendentes(saida)
        except FalhaDoTrello as falha:
            self._falhou(falha)
            return lidas
        if saida.falha is not None:
            self._falhou(saida.falha)             # a resposta não saiu (a conversa engoliu o erro e tenta de novo)
        else:
            self._recusada = None
        return lidas

    def acordar(self) -> None:
        """O webhook anotou uma action: o laço trata os avisos sem esperar a próxima reconciliação."""
        self._acordado.set()

    async def _tratar_avisos(self, cliente: ClienteTrello, saida: SaidaDoTrello) -> int:
        """Relê, pela API, cada action que o webhook anotou. DALI, e só dali, saem autor, cartão, quadro, tipo, data e
        texto: o corpo do POST nunca foi a fonte (§8.5). A action que a API não devolve (apagada, 404) fica `ignorada`
        sem texto; a velha (acima de `idade_max_s`) também; a falha passageira deixa o aviso para a volta seguinte."""
        idade = self.cfg.file.trello.idade_max_s
        tratadas = 0
        for linha in self.repo.avisos_pendentes():
            ident, numero = str(linha["id_externo"]), int(str(linha["id"]))
            desde = parse_iso(str(linha.get("recebida_em") or ""))
            if desde is not None and (self._relogio() - desde).total_seconds() > idade:
                self.repo.marcar(numero, "ignorada", erro="aviso antigo: a Central estava fora do ar", de=("aviso",))
                continue
            try:
                bruta = await cliente.acao(ident)
            except FalhaDoTrello as falha:
                if falha.status == 404:
                    self.repo.marcar(numero, "ignorada", erro="a API não devolve a action", de=("aviso",))
                    continue
                raise
            if not isinstance(bruta, dict) or bruta.get("id") != ident:
                self.repo.marcar(numero, "ignorada", erro="a API devolveu outra coisa", de=("aviso",))
                continue
            # Tira a linha-aviso e grava a action de verdade logo em seguida (a chave única impede as duas). Uma queda
            # entre os dois passos perde só o atalho: a reconciliação por leitura ainda lê a action.
            self.repo.descartar_aviso(ident)
            await self._registrar(bruta, None, saida)
            tratadas += 1
        return tratadas

    async def _cadastro_se_devido(self) -> None:
        """O recadastro do webhook de cada quadro (§8.7): na partida e a cada hora, só no líder e só com a chave PRÓPRIA
        `webhook.cadastro_automatico` (o primeiro cadastro é manual; `webhook.enabled` só liga a rota)."""
        cad, cliente = self._cadastro, self.cliente()
        if cad is None or cliente is None or not cad.pode_agir_sozinho() or self._lider(AVISOS) is None:
            return
        agora = time.monotonic()
        if self._cadastro_em is not None and agora - self._cadastro_em < CADASTRO_A_CADA_S:
            return
        self._cadastro_em = agora
        try:
            await cad.reconciliar(cliente)
        except FalhaDoTrello as falha:
            self._falhou(falha)          # 401/403: a mesma recusa (`trello_recusado`) do resto da integração

    def _falhou(self, falha: FalhaDoTrello) -> None:
        if falha.definitiva:
            if self._recusada is None:
                log.error("trello: a leitura foi recusada (%s)", falha.motivo)
            self._recusada = falha
        else:
            log.warning("trello: leitura (%s)", falha.motivo)

    async def _ler_quadro(self, cliente: ClienteTrello, saida: SaidaDoTrello, quadro: str) -> int:
        marco = self.cursor.ler(quadro)
        if marco is None:
            # 1ª subida: o histórico do quadro é histórico. Um "sim" ou um /vetar de ontem não pode agir.
            recentes = [a for a in _lista(await cliente.acoes_do_quadro(quadro, None, limite=1))]
            mais_nova = recentes[0] if recentes else {}
            # Quadro sem nenhuma action dos tipos lidos: não há histórico a descartar, então o marco é o começo dos tempos
            # (um marco em "agora" perderia a action que chegasse no mesmo segundo, ou com o relógio do Trello atrasado).
            self.cursor.gravar(quadro, _texto(mais_nova.get("id")), _texto(mais_nova.get("date")) or SEM_HISTORICO)
            log.info("trello: 1ª subida do quadro; o histórico foi descartado")
            return 0
        desde = _texto(marco.get("ultima_action")) or _texto(marco.get("ultima_data"))
        acoes = _lista(await cliente.acoes_do_quadro(quadro, desde))
        # O Trello devolve da mais nova para a mais velha; trata-se na ordem em que aconteceram.
        acoes.sort(key=lambda a: (str(a.get("date") or ""), str(a.get("id") or "")))
        for acao in acoes:
            await self._registrar(acao, quadro, saida)
        ultima = acoes[-1] if acoes else {}
        self.cursor.gravar(quadro, _texto(ultima.get("id")), _texto(ultima.get("date")))     # anda `atualizado_em` sempre
        return len(acoes)

    async def _registrar(self, acao: Mapping[str, object], quadro: str | None, saida: SaidaDoTrello) -> None:
        r = recebida_da_action(acao, self.cfg.file.trello, chave_do_cartao=self.cartoes.chave_do_cartao,
                               da_central=lambda i: self.repo.enviada(i) is not None, quadro=quadro)
        if r is None:
            return
        nova = self.repo.id_de(r.id_externo) is None
        try:
            await self.conversa.registrar(r, saida)
        except Exception as exc:  # noqa: BLE001 - uma action ruim não trava a conversa; só o id vai ao log
            log.error("trello: action %s não pôde ser registrada (%s)", r.id_externo, type(exc).__name__)
            self.conversa.gravar_falha(r, f"erro ao registrar ({type(exc).__name__})")
            return
        if nova and r.tipo == "mensagem" and not r.do_dono and RefDoTrello.ler(r.ref_mensagem) is not None:
            await self._convidado(r, saida)

    # ------------------------------------------------------------------ o convidado
    def _cabe_na_cota(self) -> bool:
        agora = time.monotonic()
        while self._reacoes and agora - self._reacoes[0] > 3600:
            self._reacoes.popleft()
        if len(self._reacoes) >= CONVIDADOS_POR_HORA:
            return False
        self._reacoes.append(agora)
        return True

    async def _convidado(self, r: Recebida, saida: SaidaDoTrello) -> None:
        """Quem comentou não é o dono nem um autorizado. O texto dele já foi gravado SEM texto; aqui só se decide, em
        memória, o que a Central faz: nada executa. Pergunta pura (com `responder_convidados`) recebe o resumo do
        `/status`; pedido vira um aviso ao dono e, com `responder_convidados`, o comentário fixo. O texto e o nome do
        convidado não vão a lugar nenhum."""
        if self.conversa.antiga(r) or not self._cabe_na_cota():
            return
        responder = self.cfg.file.trello.responder_convidados
        if _pergunta_pura(r.texto):
            if responder:
                await self.conversa.responder_a(saida, r, self.portas.status())
            return
        if self._avisar_dono is not None:
            self._avisar_dono(Aviso(
                chave=chave_do_fato("trello-convidado", r.id_externo), tipo="trello.convidado",
                titulo=titulo_do_aviso("um convidado do Trello fez um pedido"),
                corpo="Nada foi executado. Veja o cartão no Trello; para atender, peça pelo painel ou pelo Telegram.",
                link=None))
        if responder:
            await self.conversa.responder_a(saida, r, RESPOSTA_CONVIDADO)

    # ------------------------------------------------------------------ laço
    async def laco(self) -> None:
        while True:
            try:
                if self.ligado:
                    await self.uma_volta()
                    await self._cadastro_se_devido()
                await self._esperar(float(self.cfg.file.trello.reconciliar_s))
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - o leitor nunca derruba o processo
                log.exception("trello: laço do leitor")
                await self._dormir(5)

    async def _esperar(self, segundos: float) -> None:
        """Dorme até a próxima reconciliação, mas acorda quando o webhook anota uma action: só os avisos são tratados."""
        fim = time.monotonic() + segundos
        while (restante := fim - time.monotonic()) > 0:
            try:
                await asyncio.wait_for(self._acordado.wait(), timeout=restante)
            except asyncio.TimeoutError:
                return
            self._acordado.clear()
            if self.ligado:
                await self.uma_volta(so_avisos=True)


def _lista(bruto: object) -> list[dict[str, object]]:
    return [a for a in bruto if isinstance(a, dict)] if isinstance(bruto, list) else []
