"""A autopublicação do fluxo de classe B em SOMBRA (item 30.34; a regra está em `domain/autopublicacao.py`).

A cada volta (laço próprio, sob a trava de líder), o serviço avalia os fluxos que a D1 segura em `validated` e marca, no
livro da sombra, o que publicaria: um CASO por item, uma vez. O balanço (casos, regressões, taxa) sai das marcas e dos
eventos depois de cada uma, e é ele que diz, no relatório, se o `on` poderia ligar.

Nesta fatia o modo vai só até `shadow` (o config recusa `on`): publicar de verdade exige um caminho próprio pela trava
da D1 (`conferir_transicao` e `_mover_fluxo`), e esse caminho vem depois do relatório da sombra, à parte.

Os fatos de cada fluxo são os mesmos que o resto do módulo usa:
- a classe é a de AGORA, a mais restritiva entre a do dossiê e a do parecer (a regra do aceite, `pareceres.py`);
- o parecer é o mais recente do curador; desatualizado se o item mudou de estado desde ele;
- a evidência é a REAL do conteúdo atual (a marca do conteúdo) e sem as execuções invalidadas (30.23).
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.modules.learning.application.nativos import marca_do_conteudo
from app.modules.learning.application.ports import FonteDeDossies, RepositorioDeAprendizado
from app.modules.learning.application.servico import LearningService
from app.modules.learning.domain.autopublicacao import (Acao, Avaliacao, BalancoDaSombra, CasoDaSombra, EventoDoCaso,
                                                        FatosDaAutopublicacao, ModoDaAutopublicacao,
                                                        ParametrosDaAutopublicacao, ParecerParaAutopublicar, acao,
                                                        avaliar, balanco, desfecho)
from app.modules.learning.domain.ciclo import SkillState
from app.modules.learning.domain.evidencia_invalida import run_invalidada
from app.modules.learning.domain.livro import EntradaDoLivro, LivroKind
from app.modules.learning.domain.parecer import RevisaoGravada, mais_restritiva
from app.modules.learning.domain.politica_de_risco import ClasseDeRisco
from app.modules.learning.domain.promocao import Contadores, contadores
from app.util import to_iso

log = logging.getLogger("poc.aprendizado")


class RevisoesDoItem(Protocol):
    def do_item(self, item_ref: str, limite: int) -> list[RevisaoGravada]: ...


class LivroDaSombra(Protocol):
    """Onde os casos ficam (`learning_signals`, `kind = autopublicaria`: sem migração) e de onde saem os eventos."""

    def casos(self) -> list[CasoDaSombra]: ...
    def marcar(self, item_ref: str, dados: dict[str, object], *, app: str | None) -> bool: ...
    def eventos(self, item_ref: str, desde: datetime) -> list[EventoDoCaso]: ...


@dataclass(frozen=True, slots=True)
class AvaliacaoDoFluxo:
    item_ref: str
    avaliacao: Avaliacao
    review_id: str | None


@dataclass(frozen=True, slots=True)
class ResultadoDaVolta:
    modo: ModoDaAutopublicacao
    avaliados: int
    publicaria: tuple[str, ...]        # os que passam na regra agora
    marcados: tuple[str, ...]          # os casos NOVOS desta volta (o já marcado não conta de novo)


class ServicoDeAutopublicacao:
    def __init__(self, servico: LearningService, repo: RepositorioDeAprendizado, revisoes: RevisoesDoItem,
                 dossies: FonteDeDossies, livro_da_sombra: LivroDaSombra, *, modo: Callable[[], ModoDaAutopublicacao],
                 relogio: Callable[[], datetime],
                 parametros: ParametrosDaAutopublicacao = ParametrosDaAutopublicacao()) -> None:
        self._servico = servico
        self._repo = repo
        self._revisoes = revisoes
        self._dossies = dossies
        self._sombra = livro_da_sombra
        self._modo = modo
        self._relogio = relogio
        self.parametros = parametros

    @property
    def modo(self) -> ModoDaAutopublicacao:
        return self._modo()

    # ------------------------------------------------------------------ a volta
    def uma_volta(self) -> ResultadoDaVolta:
        modo = self._modo()
        if modo is ModoDaAutopublicacao.OFF:
            return ResultadoDaVolta(modo, 0, (), ())
        ja = {c.item_ref for c in self._sombra.casos()}
        b = self.balanco()
        avaliados, publicaria, marcados = 0, [], []
        for e in self._candidatos():
            avaliados += 1
            x = self.avaliar(e)
            a = acao(modo, x.avaliacao, b)
            if a is Acao.NADA:
                continue
            publicaria.append(x.item_ref)
            if a is Acao.PUBLICAR:
                # Inalcançável nesta fatia: o config não aceita `on`. Se chegar, registra e avisa; nunca publica calado.
                log.error("aprendizado: autopublicação em 'on' sem o caminho de publicar (30.34): %s só registrado",
                          x.item_ref)
            if x.item_ref not in ja and self._sombra.marcar(x.item_ref, self._dados(e, x, modo), app=e.app):
                marcados.append(x.item_ref)
        if marcados:
            log.info("aprendizado: autopublicação em %s marcou %d caso(s): %s", modo.value, len(marcados),
                     ", ".join(marcados))
        return ResultadoDaVolta(modo, avaliados, tuple(publicaria), tuple(marcados))

    def _candidatos(self) -> list[EntradaDoLivro]:
        """Os fluxos que a D1 segura em `validated`: o resto nem chega à regra (ela os recusaria pelo estado)."""
        return [e for e in self._servico.livro().itens
                if e.kind is LivroKind.FLUXO and e.state is SkillState.VALIDATED and e.requires_owner]

    def avaliar(self, e: EntradaDoLivro) -> AvaliacaoDoFluxo:
        ref = e.trail_ref
        r = next(iter(self._revisoes.do_item(ref, 1)), None)
        d = self._dossies.dossie(e)
        # A regra do aceite (`pareceres._classe_de_agora`): endurece, nunca afrouxa; sem dossiê de agora, C.
        classe = mais_restritiva(r.classe_efetiva if r is not None else None,
                                 d.classe if d is not None else ClasseDeRisco.C)
        fatos = FatosDaAutopublicacao(kind=e.kind.value, estado=e.state,
                                      requer_dono=e.requires_owner, reaprendido=e.reaprendido is not None,
                                      classe=classe, parecer=self._parecer(r, e), contadores=self._contadores(e))
        return AvaliacaoDoFluxo(ref, avaliar(fatos, self.parametros), r.id if r is not None else None)

    @staticmethod
    def _parecer(r: RevisaoGravada | None, e: EntradaDoLivro) -> ParecerParaAutopublicar | None:
        if r is None:
            return None
        p = r.parecer
        estado = e.state.value if e.state is not None else None
        return ParecerParaAutopublicar(decisao=p.decisao if p is not None else None,
                                       confianca=p.confianca if p is not None else None, simulado=r.simulated,
                                       desatualizado=r.estado_no_parecer != estado, decidido=r.decidida)

    def _contadores(self, e: EntradaDoLivro) -> Contadores:
        ref = e.trail_ref
        invalidas = {x for t in self._repo.trilha(ref) if (x := run_invalidada(t.reason)) is not None}
        marca = marca_do_conteudo(e.content_hash) if e.content_hash else None
        evidencias = [x for x in self._repo.evidencias(ref)
                      if (marca is None or (x.detail or "").startswith(marca)) and x.run_id not in invalidas]
        return contadores(evidencias)

    def _dados(self, e: EntradaDoLivro, x: AvaliacaoDoFluxo, modo: ModoDaAutopublicacao) -> dict[str, object]:
        c = self._contadores(e)
        return {"modo": modo.value, "review_id": x.review_id, "execucoes": c.execucoes, "aparelhos": c.aparelhos,
                "a_favor": c.a_favor, "content_hash": e.content_hash, "titulo": e.title,
                "marcado_em": to_iso(self._relogio())}

    # ------------------------------------------------------------------ o balanço e o relatório
    def balanco(self) -> BalancoDaSombra:
        agora = self._relogio()
        return balanco((desfecho(c, self._sombra.eventos(c.item_ref, c.marcado_em), agora, self.parametros)
                        for c in self._sombra.casos()), self.parametros)

    def relatorio(self) -> dict[str, object]:
        """O bloco das métricas (`curador.autopublicacao`, aditivo ao adendo v0.89) e do relatório à orquestradora."""
        b = self.balanco()
        p = self.parametros
        return {"modo": self._modo().value, "casos": b.casos, "abertos": b.abertos, "limpos": b.limpos,
                "regrediram": b.regrediram, "taxa_sem_regressao": b.taxa_sem_regressao, "libera": b.libera,
                "limiares": {"casos_fechados": p.casos_min, "taxa_sem_regressao": p.taxa_sem_regressao_min,
                             "janela_dias": p.janela_de_regressao_dias}}

    def casos(self) -> Sequence[tuple[CasoDaSombra, str]]:
        """Cada caso com o desfecho de agora (o relatório lista item a item)."""
        agora = self._relogio()
        return [(c, desfecho(c, self._sombra.eventos(c.item_ref, c.marcado_em), agora, self.parametros).value)
                for c in self._sombra.casos()]


__all__ = ["AvaliacaoDoFluxo", "LivroDaSombra", "ResultadoDaVolta", "RevisoesDoItem", "ServicoDeAutopublicacao"]
