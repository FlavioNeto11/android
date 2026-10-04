"""O desfazer de uma decisão automática (item 28.25): prazo, idempotência e a ação inversa da fila dona, por uma porta.

Este módulo NÃO importa o aprendizado, a execução nem os pedidos (`test_arquitetura::test_contextos_novos_formam_um_dag`:
quem decide registra pelo kernel, e o composto em `app/state.py` injeta a inversa de cada fila). Sem inversa segura, a
decisão diz o PORQUÊ (`SemInversaSegura.motivo`) e a rota responde 409 `sem_inversa_segura`: desfazer é um gesto do dono
sobre uma fila que tem regras próprias, e inventar uma volta insegura seria pior que não ter.

Idempotente: desfazer uma decisão já desfeita devolve o estado dela e não chama a inversa de novo.

O registro reflete o estado de AGORA do item, por qualquer caminho (28.29): se a pessoa desfez o efeito pela tela da fila
dona (o Desligar do Aprendizado é a rota do livro, não esta), `reconciliar` grava a decisão como desfeita com quem, quando
e por quê, lidos da trilha da fila. Sem isso a decisão seguia "não desfeita" e oferecia o mesmo gesto de novo.
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


@dataclass(frozen=True, slots=True)
class DesfeitaPorFora:
    """O efeito da decisão já foi desfeito por outro caminho: quem, quando (UTC ISO) e o motivo que ficou na trilha."""

    por: str
    em: str | None
    motivo: str | None


@dataclass(frozen=True, slots=True)
class Descricao:
    """Qual item a decisão tocou, para o painel dizer e abrir (28.29): o nome legível e a execução, quando há."""

    nome: str | None = None
    run_id: str | None = None


class InversaDaFila(Protocol):
    def por_que_nao(self, decisao: Decisao) -> str | None:
        """O motivo de NÃO haver inversa segura agora (frase em português), ou `None` se há. Só lê."""
        ...

    def desfazer(self, decisao: Decisao, *, por: str, motivo: str | None) -> None:
        """Desfaz na fila dona. Levanta `SemInversaSegura`. Se o efeito já está desfeito, não faz nada (idempotente)."""
        ...

    def desfeita_por_fora(self, decisao: Decisao) -> DesfeitaPorFora | None:
        """Se o efeito da decisão já foi desfeito por outro caminho, quem, quando e por quê. Só lê."""
        ...

    def descrever(self, decisao: Decisao) -> Descricao | None:
        """O nome do item e a execução dele, para o painel. Só lê; `None` quando a fila não sabe dizer."""
        ...


class SemInversa:
    """A fila cuja inversa não é segura: sempre recusa, com o motivo que o painel mostra no lugar do botão."""

    def __init__(self, motivo: str):
        self.motivo = motivo

    def por_que_nao(self, decisao: Decisao) -> str | None:
        return self.motivo

    def desfazer(self, decisao: Decisao, *, por: str, motivo: str | None) -> None:
        raise SemInversaSegura(self.motivo)

    def desfeita_por_fora(self, decisao: Decisao) -> DesfeitaPorFora | None:
        return None

    def descrever(self, decisao: Decisao) -> Descricao | None:
        return None


class RegistroParaDesfazer(Protocol):
    def agora(self) -> datetime: ...
    def obter(self, decisao_id: int) -> Decisao | None: ...
    def marcar_desfeita(self, decisao_id: int, *, por: str, motivo: str | None, em: str | None = None) -> bool: ...


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

    def reconciliar(self, d: Decisao) -> Decisao:
        """A decisão como o registro deve mostrá-la agora: se o efeito foi desfeito por outro caminho, grava (CAS, uma
        vez) com quem, quando e o motivo da trilha da fila dona. Uma falha de leitura da fila não esconde a decisão."""
        if d.desfeita:
            return d
        try:
            fora = self._inversa(d).desfeita_por_fora(d)
        except Exception:  # noqa: BLE001 - a lista não quebra por uma fila que não leu; o desfazer confere de novo
            return d
        if fora is None:
            return d
        self._registro.marcar_desfeita(d.id, por=fora.por, motivo=fora.motivo, em=fora.em)
        return self._registro.obter(d.id) or d

    def descrever(self, d: Decisao) -> Descricao:
        """O nome e a execução do item, pela fila dona; a falha de leitura da fila só tira o nome da tela."""
        try:
            return self._inversa(d).descrever(d) or Descricao()
        except Exception:  # noqa: BLE001 - a lista não quebra por uma fila que não leu
            return Descricao()

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
        d = self.reconciliar(d)
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
