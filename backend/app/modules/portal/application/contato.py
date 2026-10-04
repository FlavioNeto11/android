"""O caminho de um contato do site, da validação até a Canais (29.77, ADR-075). Sem HTTP: a rota traduz o resultado.

Ordem das defesas, e por que nesta ordem:

1. **Isca e token** primeiro. Robô recebe a MESMA resposta de um contato aceito e nada é gravado: quem testa o
   formulário não aprende o que o barrou. Só o token expirado (pessoa de verdade com a aba aberta há horas) recebe
   aviso próprio.
2. **Campos e consentimento.** Os tetos repetem os da página; a Canais repete de novo (defesa em camadas).
3. **Taxa por cliente** (hora e dia, contadas no banco: sobrevivem a reinício e valem entre réplicas) e **teto diário
   global**. Acima do teto diário a linha entra como `descartado` e SEM o conteúdo: conta para a taxa e para o resumo,
   mas não guarda dado pessoal de quem não vai ser atendido; a pessoa recebe o 429 que aponta os telefones.
4. **Grava antes de avisar.** Só então a Canais é chamada, com o teto de avisos por hora: acima dele a linha fica
   `retido` e o laço manda quando a janela abrir. Canal desligado ou ausente deixa `pendente` para o laço.

O texto do visitante é DADO: não vai para IA, `runs`, `pedidos` nem aprovações, e o log só leva o id e o motivo.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol

from app.modules.portal.application.protecao import cliente_pseudonimo, conferir_token
from app.modules.portal.domain.campos import validar

log = logging.getLogger("poc.portal")

#: Resposta de um contato aceito, de uma isca e de um token cedo ou falso: idênticas de propósito.
ACEITO: Mapping[str, object] = {"ok": True}

#: Quantas falhas da Canais (exceção ou `falha_interna`) um contato aguenta antes de virar `descartado` com motivo
#: `falhas_demais`. Sem teto, um conteúdo que faz a Canais levantar voltaria a cada volta e, com 20 assim, prenderia
#: o reenvio de todos os seguintes em silêncio (revisão do #333, A2). `canal_desligado` não conta: é espera, não falha.
FALHAS_MAX = 10

MUITAS = "Recebemos muitas mensagens agora. Tente mais tarde, ou ligue ou chame no WhatsApp pelos telefones da página."


class Limites(Protocol):
    por_cliente_hora: int
    por_cliente_dia: int
    telegram_hora: int
    guardados_dia: int
    token_min_s: int
    token_max_s: int


class Guardado(Protocol):
    id: int
    tentativas: int
    nome: str
    empresa: str
    telefone: str
    mensagem: str


class Repositorio(Protocol):
    def sal(self) -> bytes: ...
    def gravar(self, *, nome: str, empresa: str, telefone: str, mensagem: str, cliente_hash: str, agora: datetime,
               estado: str = ..., motivo: str | None = ...) -> int: ...
    def do_cliente_desde(self, cliente_hash: str, desde: datetime) -> int: ...
    def guardados_desde(self, desde: datetime) -> int: ...
    def entregues_desde(self, desde: datetime) -> int: ...
    def marcar(self, contato_id: int, estado: str, motivo: str | None, agora: datetime, *,
               tentou: bool = ...) -> None: ...
    def a_reenviar(self, limite: int) -> list[Guardado]: ...
    def retidos(self) -> int: ...
    def descartados_desde(self, desde: datetime) -> int: ...


#: O tipo `ContatoDoPortal` e a função `avisar_contato_do_portal` da Canais (28.32), vistos daqui só pela forma: o
#: construtor com os cinco campos, e a resposta pelos atributos `enfileirado` e `motivo` (lidos com `getattr`).
TipoDoContato = Callable[..., object]
Avisar = Callable[[object], object]
#: `avisar_resumo_do_portal(retidos, descartados, janela_h)` da Canais (#335): um aviso por hora UTC, só com números.
AvisarResumo = Callable[[int, int, int], object]


@dataclass(frozen=True, slots=True)
class Resposta:
    status: int
    corpo: Mapping[str, object] = field(default_factory=lambda: dict(ACEITO))


def _erro(status: int, code: str, message: str, **extra: object) -> Resposta:
    return Resposta(status, {"detail": {"code": code, "message": message, **extra}})


class ServicoDeContato:
    def __init__(self, repo: Repositorio, limites: Callable[[], Limites], avisar: Callable[[], Avisar | None],
                 tipo_do_contato: Callable[[], TipoDoContato | None],
                 avisar_resumo: Callable[[], AvisarResumo | None] = lambda: None) -> None:
        """`avisar` e `tipo_do_contato` são resolvidos a cada uso: o código da Canais pode não existir nesta base, e
        sem ele o contato fica `pendente` até existir."""
        self.repo = repo
        self.limites = limites
        self._avisar = avisar
        self._tipo = tipo_do_contato
        self._avisar_resumo = avisar_resumo
        self._sal: bytes | None = None

    def sal(self) -> bytes:
        if self._sal is None:
            self._sal = self.repo.sal()
        return self._sal

    def receber(self, dados: object, cliente: str, agora: datetime) -> Resposta:
        if not isinstance(dados, Mapping):
            return _erro(422, "campo_invalido", "Confira os campos do formulário.", campos=[])
        lim = self.limites()
        isca = dados.get("site")
        if isinstance(isca, str) and isca.strip():
            return Resposta(202)                                       # isca preenchida: robô
        veredito = conferir_token(self.sal(), str(dados.get("token") or ""), agora.timestamp(),
                                  minimo_s=lim.token_min_s, maximo_s=lim.token_max_s)
        if veredito == "expirado":
            return _erro(400, "token_expirado", "A página ficou aberta por muito tempo. Recarregue e envie de novo.")
        if veredito != "ok":
            return Resposta(202)                                       # sem página ou rápido demais: robô
        campos, invalidos = validar(dados)
        if invalidos:
            return _erro(422, "campo_invalido", "Confira os campos do formulário.", campos=invalidos)
        if dados.get("consentimento") is not True:
            return _erro(422, "consentimento_ausente", "Para enviar, marque a concordância com o uso dos dados.")

        quem = cliente_pseudonimo(self.sal(), cliente)
        if (self.repo.do_cliente_desde(quem, agora - timedelta(hours=1)) >= lim.por_cliente_hora
                or self.repo.do_cliente_desde(quem, agora - timedelta(days=1)) >= lim.por_cliente_dia):
            return _erro(429, "muitas_mensagens", MUITAS)
        if self.repo.guardados_desde(agora - timedelta(days=1)) >= lim.guardados_dia:
            contato_id = self.repo.gravar(nome="", empresa="", telefone="", mensagem="", cliente_hash=quem,
                                          agora=agora, estado="descartado", motivo="teto_diario")
            log.warning("portal: contato %s descartado pelo teto diário", contato_id)
            return _erro(429, "muitas_mensagens", MUITAS)

        contato_id = self.repo.gravar(nome=campos["nome"], empresa=campos["empresa"], telefone=campos["telefone"],
                                      mensagem=campos["mensagem"], cliente_hash=quem, agora=agora)
        self.entregar(contato_id, campos, agora)
        return Resposta(202)

    def entregar(self, contato_id: int, campos: Mapping[str, str], agora: datetime) -> str:
        """Tenta passar o contato à Canais; devolve o estado em que a linha ficou. Nunca levanta: o contato já está
        guardado, e o laço tenta de novo."""
        if self.repo.entregues_desde(agora - timedelta(hours=1)) >= self.limites().telegram_hora:
            self.repo.marcar(contato_id, "retido", "teto_por_hora", agora)
            return "retido"
        avisar, tipo = self._avisar(), self._tipo()
        if avisar is None or tipo is None:
            self.repo.marcar(contato_id, "pendente", "canal_ausente", agora)
            return "pendente"
        try:
            resultado = avisar(tipo(contato_id=contato_id, nome=campos["nome"], empresa=campos["empresa"] or None,
                                    telefone=campos["telefone"] or None, mensagem=campos["mensagem"]))
        except Exception as erro:  # noqa: BLE001 - a falha da Canais não pode perder o contato nem derrubar a rota
            # Só o tipo: a mensagem (e o traceback) de uma exceção lá dentro pode citar o nome ou o telefone do visitante.
            log.error("portal: contato %s, falha ao avisar (%s)", contato_id, type(erro).__name__)
            self.repo.marcar(contato_id, "pendente", "falha_interna", agora, tentou=True)
            return "pendente"
        if getattr(resultado, "enfileirado", False) is True:
            self.repo.marcar(contato_id, "entregue", None, agora, tentou=True)
            return "entregue"
        motivo = str(getattr(resultado, "motivo", None) or "falha_interna")[:40]
        estado = "descartado" if motivo == "campo_invalido" else "pendente"
        self.repo.marcar(contato_id, estado, motivo, agora, tentou=motivo != "canal_desligado")
        log.info("portal: contato %s ficou %s (%s)", contato_id, estado, motivo)
        return estado

    def reenviar(self, agora: datetime, *, lote: int = 20) -> dict[str, int]:
        """Uma volta do laço: tenta os não entregues, em ordem, até o teto da hora. Devolve a contagem por estado."""
        contagem: dict[str, int] = {}
        for contato in self.repo.a_reenviar(lote):
            if contato.tentativas >= FALHAS_MAX:
                self.repo.marcar(contato.id, "descartado", "falhas_demais", agora)
                log.warning("portal: contato %s descartado depois de %s falhas da Canais", contato.id, contato.tentativas)
                contagem["descartado"] = contagem.get("descartado", 0) + 1
                continue
            campos = {"nome": contato.nome, "empresa": contato.empresa, "telefone": contato.telefone,
                      "mensagem": contato.mensagem}
            estado = self.entregar(contato.id, campos, agora)
            contagem[estado] = contagem.get(estado, 0) + 1
            if estado == "retido":
                break                                                  # o teto da hora vale para todos os seguintes
        return contagem

    def resumir(self, agora: datetime) -> str | None:
        """O "+N" acima dos tetos ao dono: os `retido` agora e os `descartado` da última hora, pelo aviso
        `portal.resumo` da Canais, que tem chave por hora UTC (chamar de novo na mesma hora não duplica). Sem nada acima
        dos tetos, ou sem a porta da Canais nesta base, não chama. Devolve o motivo da recusa, ou `None`."""
        retidos, descartados = self.repo.retidos(), self.repo.descartados_desde(agora - timedelta(hours=1))
        avisar = self._avisar_resumo()
        if (retidos == 0 and descartados == 0) or avisar is None:
            return None
        try:
            resultado = avisar(retidos, descartados, 1)
        except Exception as erro:  # noqa: BLE001 - o resumo é aviso; a falha não para o reenvio
            log.error("portal: resumo dos tetos falhou (%s)", type(erro).__name__)
            return "falha_interna"
        if getattr(resultado, "enfileirado", False) is True:
            return None
        motivo = str(getattr(resultado, "motivo", None) or "falha_interna")[:40]
        log.info("portal: resumo dos tetos não entrou na fila (%s)", motivo)
        return motivo
