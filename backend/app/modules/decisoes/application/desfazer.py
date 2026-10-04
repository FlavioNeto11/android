"""O desfazer de uma decisão automática (item 28.25): prazo, idempotência e a ação inversa da fila dona, por uma porta.

Este módulo NÃO importa o aprendizado, a execução nem os pedidos (`test_arquitetura::test_contextos_novos_formam_um_dag`:
quem decide registra pelo kernel, e o composto em `app/state.py` injeta a inversa de cada fila). Sem inversa segura, a
decisão diz o PORQUÊ (`SemInversaSegura.motivo`) e a rota responde 409 `sem_inversa_segura`: desfazer é um gesto do dono
sobre uma fila que tem regras próprias, e inventar uma volta insegura seria pior que não ter.

Idempotente: desfazer uma decisão já desfeita devolve o estado dela e não chama a inversa de novo.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.modules.decisoes.domain.decisao import Decisao, prazo_ate, prazo_expirou
from app.util import parse_iso

PREFIXO_SEM_INVERSA = "não dá para desfazer automaticamente: "


class SemInversaSegura(Exception):
    """A fila dona não tem como desfazer isto com segurança. `motivo` é uma frase em português para o dono."""

    def __init__(self, motivo: str):
        super().__init__(motivo)
        self.motivo = motivo


class DecisaoNaoEncontrada(Exception):
    pass


class PrazoVencido(Exception):
    def __init__(self, dias: float):
        super().__init__(f"O prazo de {dias:g} dia(s) para desfazer esta decisão passou.")
        self.dias = dias


class InversaDaFila(Protocol):
    def por_que_nao(self, decisao: Decisao) -> str | None:
        """O motivo de NÃO haver inversa segura agora (frase em português), ou `None` se há. Só lê."""
        ...

    def desfazer(self, decisao: Decisao, *, por: str, motivo: str | None) -> None:
        """Desfaz na fila dona. Levanta `SemInversaSegura`. Se o efeito já está desfeito, não faz nada (idempotente)."""
        ...


class SemInversa:
    """A fila cuja inversa não é segura: sempre recusa, com o motivo que o painel mostra no lugar do botão."""

    def __init__(self, motivo: str):
        self.motivo = motivo

    def por_que_nao(self, decisao: Decisao) -> str | None:
        return self.motivo

    def desfazer(self, decisao: Decisao, *, por: str, motivo: str | None) -> None:
        raise SemInversaSegura(self.motivo)


class RegistroParaDesfazer(Protocol):
    def agora(self) -> datetime: ...
    def obter(self, decisao_id: int) -> Decisao | None: ...
    def marcar_desfeita(self, decisao_id: int, *, por: str, motivo: str | None) -> bool: ...


#: O verbo do botão por fila: o desfazer do aprendizado é DESLIGAR o item (published → disabled), o das outras é desfazer.
ROTULO_DO_DESFAZER: Mapping[str, str] = {"aprendizado": "Desligar"}
ROTULO_PADRAO = "Desfazer"


def rotulo_do_desfazer(fila: str) -> str:
    return ROTULO_DO_DESFAZER.get(fila, ROTULO_PADRAO)


@dataclass(frozen=True, slots=True)
class Situacao:
    """O que o painel mostra sobre o desfazer de uma decisão."""

    pode: bool
    #: Por que não, em português; vazio quando `pode`.
    por_que_nao: str | None
    #: Até quando vale o desfazer (UTC ISO).
    prazo_ate: str


class DesfazerDecisoes:
    def __init__(self, registro: RegistroParaDesfazer, inversas: Mapping[str, InversaDaFila], *,
                 dias: Callable[[], float]):
        self._registro = registro
        self._inversas = inversas
        self._dias = dias

    def _inversa(self, decisao: Decisao) -> InversaDaFila:
        return self._inversas.get(decisao.fila) or SemInversa("esta fila ainda não tem a volta ligada")

    def situacao(self, d: Decisao) -> Situacao:
        quando = parse_iso(d.decidida_em) or self._registro.agora()
        ate = prazo_ate(quando, self._dias()).isoformat().replace("+00:00", "Z")
        if d.desfeita:
            return Situacao(False, "já foi desfeita", ate)
        if prazo_expirou(quando, self._registro.agora(), self._dias()):
            return Situacao(False, f"o prazo de {self._dias():g} dia(s) para desfazer passou", ate)
        motivo = self._inversa(d).por_que_nao(d)
        return Situacao(motivo is None, None if motivo is None else PREFIXO_SEM_INVERSA + motivo, ate)

    def desfazer(self, decisao_id: int, *, por: str, motivo: str | None) -> tuple[Decisao, bool]:
        """Desfaz. Devolve (a decisão como ficou, se foi ESTA chamada que desfez). Levanta `DecisaoNaoEncontrada`,
        `PrazoVencido` ou `SemInversaSegura`."""
        d = self._registro.obter(decisao_id)
        if d is None:
            raise DecisaoNaoEncontrada(str(decisao_id))
        if d.desfeita:
            return d, False
        quando = parse_iso(d.decidida_em) or self._registro.agora()
        if prazo_expirou(quando, self._registro.agora(), self._dias()):
            raise PrazoVencido(self._dias())
        try:
            self._inversa(d).desfazer(d, por=por, motivo=motivo)
        except SemInversaSegura as exc:
            raise SemInversaSegura(PREFIXO_SEM_INVERSA + exc.motivo) from exc
        fez = self._registro.marcar_desfeita(decisao_id, por=por, motivo=motivo)
        depois = self._registro.obter(decisao_id)
        assert depois is not None
        return depois, fez
