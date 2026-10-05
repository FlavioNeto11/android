"""A aprovação automática por política (item 30.55; a régua está em `domain/aprovacao_automatica.py`).

A cada volta (laço próprio, sob a trava de líder), o serviço lê as duas filas que esperam o dono ("Para aprovar" e
"Revisar"), monta os fatos de cada receita e fluxo e passa a régua:
- em `shadow`, marca UMA vez, no livro da sombra, o que decidiria (e o relatório diz o que falta aos outros);
- em `on`, decide: publica (`validated → published`) ou confirma que fica, pela MESMA porta da pessoa
  (`LearningService.mudar_estado` e `confirmar_que_fica`), com `by = "plataforma"` e o motivo `auto:<regra> v<n>`.
  A trilha, o veto, a guarda do fluxo e o CAS são os de sempre; o rótulo do parecer do curador NÃO é gravado (a
  decisão da plataforma não é rótulo humano: não entra no acerto do curador).

Os fatos são os mesmos que o resto do módulo usa:
- a classe é a de AGORA, a mais restritiva entre a do dossiê e a do parecer (sem dossiê, C);
- o parecer é o mais recente do curador;
- a evidência é a REAL e efetiva da versão atual (no fluxo, a marca do conteúdo) e sem as execuções invalidadas (30.23);
  a receita soma os contadores da fonte (reprodução e sombra de acerto), que são onde a evidência dela mora.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.modules.learning.application.nativos import marca_do_conteudo
from app.modules.learning.application.ports import FonteDeDossies, RepositorioDeAprendizado
from app.modules.learning.application.servico import LearningService
from app.modules.learning.domain.aprovacao_automatica import (PLATAFORMA, Acao, Avaliacao, FatosDaAprovacao, Fila,
                                                              Gesto, ModoDaAprovacao, MotivoDeFora,
                                                              ParecerParaAprovar, acao, avaliar, motivo,
                                                              regra_do_motivo)
from app.modules.learning.domain.ciclo import ErroDeAprendizado, SkillState
from app.modules.learning.domain.evidencia_invalida import run_invalidada
from app.modules.learning.domain.livro import EntradaDoLivro, LivroKind, ref_no_log
from app.modules.learning.domain.parecer import RevisaoGravada, mais_restritiva
from app.modules.learning.domain.politica_de_risco import ClasseDeRisco
from app.modules.learning.domain.promocao import contadores
from app.modules.learning.domain.saude import Saude
from app.util import to_iso

log = logging.getLogger("poc.aprendizado")

#: 30.63 (a): quantas revisões do item se leem para achar a real mais recente (as simuladas de um ensaio vêm por cima).
REVISOES_LIDAS = 20


class RevisoesDoItem(Protocol):
    def do_item(self, item_ref: str, limite: int) -> list[RevisaoGravada]: ...


@dataclass(frozen=True, slots=True)
class ContadoresDaReceita:
    """O que a fonte da receita conta (`recipes`): a reprodução e a sombra (a etapa da IA que a receita acertaria)."""

    replay_ok: int
    replay_fail: int
    sombra_acertos: int
    sombra_total: int


@dataclass(frozen=True, slots=True)
class DecisaoDaPlataforma:
    """Uma linha da trilha com `decided_by = plataforma` (o que o painel lista com o Desfazer)."""

    item_ref: str
    de: str | None
    para: str
    motivo: str
    em: str


class LivroDaAprovacao(Protocol):
    """O que a régua lê e grava fora do Livro: a sombra (`learning_signals`, `kind = aprovaria`: sem migração), os
    pacotes de teste, os contadores da receita e as decisões já tomadas pela plataforma."""

    def pacotes_de_teste(self) -> frozenset[str]: ...
    def contadores_da_receita(self, ref: str) -> ContadoresDaReceita | None: ...
    def ja_decididos(self) -> frozenset[str]: ...
    def decisoes(self, limite: int) -> list[DecisaoDaPlataforma]: ...
    def marcar(self, item_ref: str, dados: dict[str, object], *, app: str | None) -> bool: ...
    def marcados(self) -> int: ...


@dataclass(frozen=True, slots=True)
class AvaliacaoDoItem:
    item_ref: str
    fatos: FatosDaAprovacao
    avaliacao: Avaliacao


@dataclass(frozen=True, slots=True)
class ResultadoDaVolta:
    modo: ModoDaAprovacao
    avaliados: int
    decidiria: tuple[str, ...]          # os que passam na régua agora
    marcados: tuple[str, ...]           # os casos NOVOS da sombra nesta volta
    decididos: tuple[str, ...] = ()     # os que a plataforma decidiu nesta volta (só em `on`)
    fora: tuple[tuple[str, int], ...] = ()   # quantos itens cada motivo deixou com o dono


class ServicoDeAprovacaoAutomatica:
    def __init__(self, servico: LearningService, repo: RepositorioDeAprendizado, revisoes: RevisoesDoItem,
                 dossies: FonteDeDossies, livro: LivroDaAprovacao, *, modo: Callable[[], ModoDaAprovacao],
                 relogio: Callable[[], datetime]) -> None:
        self._servico = servico
        self._repo = repo
        self._revisoes = revisoes
        self._dossies = dossies
        self._livro = livro
        self._modo = modo
        self._relogio = relogio
        # A última volta deste processo (some no reinício; o relatório diz `null` até a primeira), para separar "avaliou
        # e ninguém passou" de "a volta nunca rodou".
        self._ultima: tuple[datetime, ResultadoDaVolta] | None = None

    @property
    def modo(self) -> ModoDaAprovacao:
        return self._modo()

    # ------------------------------------------------------------------ a volta
    def uma_volta(self) -> ResultadoDaVolta:
        modo = self._modo()
        if modo is ModoDaAprovacao.OFF:
            return ResultadoDaVolta(modo, 0, (), ())
        avaliacoes = self.avaliar_filas()
        decidiria, marcados, decididos = [], [], []
        fora: dict[str, int] = {}
        for x in avaliacoes:
            for m in x.avaliacao.motivos:
                fora[m.value] = fora.get(m.value, 0) + 1
            a = acao(modo, x.avaliacao)
            if a is Acao.NADA:
                continue
            decidiria.append(x.item_ref)
            if a is Acao.REGISTRAR:
                if self._livro.marcar(x.item_ref, self._dados(x, modo), app=(x.fatos.apps or (None,))[0]):
                    marcados.append(x.item_ref)
            elif self._decidir(x):
                decididos.append(x.item_ref)
        r = ResultadoDaVolta(modo, len(avaliacoes), tuple(decidiria), tuple(marcados), tuple(decididos),
                             tuple(sorted(fora.items())))
        self._ultima = (self._relogio(), r)
        # Uma linha por volta, mesmo vazia: é ela que prova no log que a régua roda.
        log.info("aprendizado: aprovação automática em %s: %d item(ns) avaliado(s), %d decidiria(m), %d caso(s) novo(s)"
                 " na sombra, %d decidido(s)", modo.value, len(avaliacoes), len(decidiria), len(marcados),
                 len(decididos))
        if decididos:
            log.warning("aprendizado: a plataforma decidiu %d item(ns): %s", len(decididos), ", ".join(decididos))
        return r

    def _decidir(self, x: AvaliacaoDoItem) -> bool:
        """O gesto pela porta da pessoa. A recusa (o item mudou no meio, veto novo, outra sessão decidiu antes) não
        derruba a volta: fica no log, e o item segue esperando o dono."""
        f, texto = x.fatos, motivo(x.fatos, x.avaliacao)
        kind = LivroKind(f.kind)
        try:
            if x.avaliacao.gesto is Gesto.PUBLICAR:
                self._servico.mudar_estado(kind, f.ref, SkillState.PUBLISHED, by=PLATAFORMA, reason=texto)
            else:
                self._servico.confirmar_que_fica(kind, f.ref, by=PLATAFORMA, motivo=texto)
        except ErroDeAprendizado as exc:
            log.warning("aprendizado: aprovação automática recusada para %s: %s", ref_no_log(x.item_ref), exc)
            return False
        return True

    # ------------------------------------------------------------------ os fatos
    def avaliar_filas(self) -> list[AvaliacaoDoItem]:
        filas: list[tuple[EntradaDoLivro, Fila]] = []
        for e in self._servico.pendentes():
            if e.kind in (LivroKind.RECEITA, LivroKind.FLUXO) and e.state is SkillState.VALIDATED:
                filas.append((e, Fila.PARA_APROVAR))
        filas.extend((e, Fila.REVISAR) for e in self._servico.revisar())
        if not filas:
            return []
        saudes = self._servico.saudes([e for e, _ in filas])
        teste = self._livro.pacotes_de_teste()
        ja = self._livro.ja_decididos()
        return [self._avaliar(e, fila, saudes.get(e.trail_ref), teste, ja) for e, fila in filas]

    def _avaliar(self, e: EntradaDoLivro, fila: Fila, saude: Saude | None, teste: frozenset[str],
                 ja: frozenset[str]) -> AvaliacaoDoItem:
        ref = e.trail_ref
        # 30.63 (a): a revisão REAL mais recente. A mais recente de todas podia ser simulada (um ensaio) e esconder um
        # parecer real anterior contra ou uma classe C; a simulada nunca pesou, então ela não pode tomar o lugar da real.
        r = next((x for x in self._revisoes.do_item(ref, REVISOES_LIDAS) if not x.simulated), None)
        d = self._dossies.dossie(e)
        classe = mais_restritiva(r.classe_efetiva if r is not None else None,
                                 d.classe if d is not None else ClasseDeRisco.C)
        a_favor, contra, falhas, execucoes, aparelhos = self._contagem(e)
        _, veto = self._servico.contexto_de_publicacao(e)
        apps = e.apps or ((e.app,) if e.app else ())
        fatos = FatosDaAprovacao(
            kind=e.kind.value, ref=e.ref, fila=fila, apps=tuple(apps), apps_de_teste=teste, classe=classe,
            a_favor=a_favor, contra=contra, falhas_de_reproducao=falhas, execucoes=execucoes, aparelhos=aparelhos,
            saude=saude.rotulo if saude is not None else None, parecer=_parecer(r),
            reaprendido=e.reaprendido is not None, texto_de_pessoa=e.human_origin, vetado=veto is not None,
            ja_decidido=ref in ja)
        return AvaliacaoDoItem(ref, fatos, avaliar(fatos))

    def _contagem(self, e: EntradaDoLivro) -> tuple[int, int, int, int, int]:
        """(a favor, contra, falhas de reprodução, execuções, aparelhos), só do real e da versão atual."""
        ref = e.trail_ref
        invalidas = {x for t in self._repo.trilha(ref) if (x := run_invalidada(t.reason)) is not None}
        # A marca do conteúdo é só do fluxo (o mesmo `fluxo:<id>` renasce com outro plano); a receita nova é outra linha.
        marca = marca_do_conteudo(e.content_hash) if e.kind is LivroKind.FLUXO and e.content_hash else None
        evidencias = [x for x in self._repo.evidencias(ref)
                      if (marca is None or (x.detail or "").startswith(marca)) and x.run_id not in invalidas]
        c = contadores(evidencias)
        a_favor, contra, falhas = c.a_favor, c.contra, 0
        if e.kind is LivroKind.RECEITA:
            rc = self._livro.contadores_da_receita(e.ref)
            if rc is not None:
                a_favor += rc.replay_ok + rc.sombra_acertos
                contra += max(0, rc.sombra_total - rc.sombra_acertos)
                falhas = rc.replay_fail
        return a_favor, contra, falhas, c.execucoes, c.aparelhos

    def _dados(self, x: AvaliacaoDoItem, modo: ModoDaAprovacao) -> dict[str, object]:
        f = x.fatos
        return {"modo": modo.value, "regra": x.avaliacao.regra.value if x.avaliacao.regra else None,
                "gesto": x.avaliacao.gesto.value if x.avaliacao.gesto else None, "fila": f.fila.value if f.fila else None,
                "motivo": motivo(f, x.avaliacao), "marcado_em": to_iso(self._relogio())}

    # ------------------------------------------------------------------ o relatório
    def relatorio(self, *, itens: bool = False) -> dict[str, object]:
        """O que a orquestradora lê no primeiro ciclo da sombra (e as métricas): o modo, a última volta, o que decidiria
        e, com `itens`, a régua item a item com os motivos de fora."""
        saida: dict[str, object] = {"modo": self._modo().value, "ultima_volta": self.ultima_volta(),
                                    "casos_na_sombra": self._livro.marcados(),
                                    "decididos_pela_plataforma": self._decisoes(self._livro.decisoes(50))}
        if itens:
            saida["itens"] = [_item(x) for x in self.avaliar_filas()]
        return saida

    def _entrada(self, item_ref: str) -> EntradaDoLivro | None:
        kind, _, ref = item_ref.partition(":")
        try:
            return self._servico.entrada(LivroKind(kind), ref)
        except (ErroDeAprendizado, ValueError):
            return None

    def _decisoes(self, decisoes: list[DecisaoDaPlataforma]) -> list[dict[str, object]]:
        """30.66: com a capability e o nome dela no catálogo, em lote, para o painel titular a decisão como as outras
        telas ("Enviar a mensagem (v1)"), e não pela chave interna da etapa."""
        entradas = {d.item_ref: self._entrada(d.item_ref) for d in decisoes}
        vivas = [e for e in entradas.values() if e is not None]
        capabilities = self._servico.capabilities(vivas) if vivas else {}
        nomes = self._servico.nomes_das_capabilities(vivas, capabilities) if vivas else {}
        return [self._decisao(d, entradas[d.item_ref], capabilities, nomes) for d in decisoes]

    def _decisao(self, d: DecisaoDaPlataforma, e: EntradaDoLivro | None,
                 capabilities: Mapping[str, str | None], nomes: Mapping[str, str | None]) -> dict[str, object]:
        """A linha da trilha com o item de AGORA (o painel lista com o título e oferece Desligar só ao que segue vivo).
        O item que saiu do livro fica sem título e sem estado, e a linha continua (a trilha é o registro)."""
        kind, _, ref = d.item_ref.partition(":")
        regra = regra_do_motivo(d.motivo)
        gesto = Gesto.PUBLICAR if (d.de, d.para) == ("validated", "published") else Gesto.CONFIRMAR
        return {"item_ref": d.item_ref, "kind": kind, "ref": ref, "de": d.de, "para": d.para, "motivo": d.motivo,
                "em": d.em, "gesto": gesto.value, "regra": regra[0] if regra else None,
                "versao": regra[1] if regra else None, "titulo": e.title if e is not None else None,
                "app": e.app if e is not None else None, "etapa": e.etapa if e is not None else None,
                "capability": capabilities.get(e.trail_ref) if e is not None else None,
                "capability_nome": nomes.get(e.trail_ref) if e is not None else None,
                "estado": e.state.value if e is not None and e.state is not None else None}

    def ultima_volta(self) -> dict[str, object] | None:
        if self._ultima is None:
            return None
        em, r = self._ultima
        return {"em": to_iso(em), "modo": r.modo.value, "avaliados": r.avaliados, "decidiria": list(r.decidiria),
                "marcados": len(r.marcados), "decididos": list(r.decididos), "fora": dict(r.fora)}


def _parecer(r: RevisaoGravada | None) -> ParecerParaAprovar | None:
    if r is None:
        return None
    return ParecerParaAprovar(id=r.id, decisao=r.parecer.decisao if r.parecer is not None else None,
                              simulado=r.simulated, decidido=r.decidida)


def _item(x: AvaliacaoDoItem) -> dict[str, object]:
    f = x.fatos
    return {"item_ref": x.item_ref, "fila": f.fila.value if f.fila else None,
            "regra": x.avaliacao.regra.value if x.avaliacao.regra else None, "decide": x.avaliacao.decide,
            "fora": [m.value for m in x.avaliacao.motivos], "classe": f.classe.value, "apps": list(f.apps),
            "a_favor": f.a_favor, "contra": f.contra, "falhas_de_reproducao": f.falhas_de_reproducao,
            "saude": f.saude.value if f.saude else None}


__all__ = ["AvaliacaoDoItem", "ContadoresDaReceita", "DecisaoDaPlataforma", "LivroDaAprovacao", "MotivoDeFora",
           "ResultadoDaVolta", "RevisoesDoItem", "ServicoDeAprovacaoAutomatica"]
