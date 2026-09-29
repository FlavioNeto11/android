"""O bloco "Aprendizado desta execução" de `GET /api/runs/{id}/feedback` (ADR-054, D2): o que a execução ensinou ao
livro e o que ela usou dele, com o título e o estado de AGORA de cada item.

Os fatos saem da leitura (`LeituraDoAprendido`, só LEITURA); o agrupamento e o texto do papel, do domínio
(`domain/aprendido.py`); o título e o estado, do próprio livro (`LearningService.entrada`) — o mesmo que a página
Aprendizado mostra. Item apagado depois da execução fica com o ref e sem título.

O título passa de novo pela triagem de credencial antes de sair: o do fluxo é o `command_template`, isto é, o comando
que a pessoa digitou, e ele não passou pela triagem do livro ao nascer. Recusado, a linha sai só com o ref.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Protocol

from app.modules.learning.application.ports import TriagemDeTexto
from app.modules.learning.application.servico import LearningService
from app.modules.learning.domain.aprendido import FatosDaExecucao, ItemAprendido, aprendido_na_execucao
from app.modules.learning.domain.ciclo import NaoEncontrado

#: O título é uma linha no relatório: o comando longo ou a lição comprida são cortados aqui.
TITULO_MAX = 160


class LeituraDoAprendido(Protocol):
    def fatos(self, run_id: str) -> FatosDaExecucao | None:
        """`None` quando a execução não existe."""
        ...


class AprendizadoDaExecucao:
    def __init__(self, livro: LearningService, leitura: LeituraDoAprendido, triagem: TriagemDeTexto) -> None:
        self._livro = livro
        self._leitura = leitura
        self._triagem = triagem

    def da_execucao(self, run_id: str) -> tuple[ItemAprendido, ...]:
        fatos = self._leitura.fatos(run_id)
        if fatos is None:
            raise NaoEncontrado(f"Não há a execução '{run_id}'.")
        return tuple(self._do_livro(i) for i in aprendido_na_execucao(fatos))

    def _do_livro(self, item: ItemAprendido) -> ItemAprendido:
        if item.kind is None or item.ref is None:
            return item                             # a falha: o painel escreve o rótulo pelo tipo
        try:
            entrada = self._livro.entrada(item.kind, item.ref)
        except NaoEncontrado:
            return item
        return replace(item, titulo=self._titulo(entrada.title), estado=entrada.state)

    def _titulo(self, bruto: str) -> str | None:
        titulo = " ".join(bruto.split())
        if not titulo or self._triagem.recusa(titulo):
            return None
        return titulo if len(titulo) <= TITULO_MAX else titulo[:TITULO_MAX - 1].rstrip() + "…"


__all__ = ["TITULO_MAX", "AprendizadoDaExecucao", "LeituraDoAprendido"]
