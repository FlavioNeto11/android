"""O rótulo de intenção (item 30.25), a camada de APLICAÇÃO: o minerador que abre a pergunta e o gesto que a responde.

- **O minerador** roda no digest da execução assentada (`LearningService.digerir_execucao`, em thread). Pergunta ao
  banco se a execução é um sucesso comprovado e real (`domain/intencao.sucesso_comprovado`) ANTES de refazer a RESOLVE,
  que é a parte mais cara; depois pergunta à cadeia (a mesma da sombra do 31.9, sem efeito e sem IA) e ao catálogo que
  ela enxerga. Grava UMA linha por execução em `learning_reviews` (`template_id = intencao`, provedor vazio, sem
  `saida`). Um segundo digest da mesma execução não grava outra, nem com o catálogo mudado.
- **O gesto** (`rotular`) confere a escolha contra o dossiê GRAVADO e grava a decisão com CAS (`decisao_final IS NULL`),
  na mesma transação do sinal `parecer_decidido` (que traz a data da decisão: `learning_reviews` não tem a coluna). O
  segundo gesto sobre a mesma execução é 409.

Nada aqui conta como parecer do curador: os leitores do curador filtram `template_id = curador`, e o sinal leva o
`template_id` em `data` para quem medir a concordância separar os dois.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.modules.learning.application.ports import NovaRevisao, NovoSinal
from app.modules.learning.domain.ciclo import ConflitoDeEstado, NaoEncontrado
from app.modules.learning.domain.intencao import (KIND_DO_ROTULO, NENHUM, TEMPLATE_DO_ROTULO, VERSAO_DO_ROTULO,
                                                  DossieDoRotulo, FatosDaExecucao, GatilhoDoRotulo, cadeia_a_rotular,
                                                  conferir_escolha, entra_no_rotulo, item_ref_da_execucao)
from app.modules.learning.domain.vocabulario import Polaridade, SignalKind

log = logging.getLogger("poc.aprendizado")

#: Quantas perguntas abertas o painel lista de uma vez (a mais recente primeiro).
PENDENTES_MAX = 200


@dataclass(frozen=True, slots=True)
class CadeiaDaExecucao:
    """O que a cadeia de resolução diz HOJE do comando da execução (refeita, sem efeito) e o app principal dela."""

    resolvida: str | None
    sem_casamento: bool
    empatados: tuple[str, ...]
    app: str | None


@dataclass(frozen=True, slots=True)
class RotuloGravado:
    """Uma linha de rótulo já lida. `dossie` `None`: forma que não é a do rótulo (linha estranha, nunca decidida)."""

    id: str
    criado_em: str
    run_id: str
    app: str
    dossie: DossieDoRotulo | None
    decisao_final: str | None
    decidido_por: str | None


@dataclass(frozen=True, slots=True)
class PerguntaAberta:
    """O que o painel mostra de uma pergunta: o rótulo, o comando da execução (lido agora, nunca do dossiê), quando ela
    terminou e o nome de cada candidato pelo catálogo de agora (o id quando a habilidade saiu dele)."""

    rotulo: RotuloGravado
    comando: str | None
    terminou_em: str | None
    nomes: dict[str, str]


class RegistroDeRotulos(Protocol):
    """`learning_reviews` com `template_id = intencao`, e as leituras da execução que o rótulo precisa."""

    def transacao(self) -> AbstractContextManager[None]: ...
    def fatos(self, run_id: str) -> FatosDaExecucao | None: ...
    def tem_rotulo(self, run_id: str) -> bool: ...
    def gravar(self, nova: NovaRevisao, agora: datetime) -> str | None: ...
    def da_execucao(self, run_id: str) -> RotuloGravado | None: ...
    def pendentes(self, limite: int) -> list[RotuloGravado]: ...
    def contar_pendentes(self) -> int: ...
    def execucoes(self, run_ids: Sequence[str]) -> dict[str, tuple[str | None, str | None]]: ...
    def decidir(self, review_id: str, *, decisao_final: str, decidido_por: str) -> bool: ...


class LivroDoRotulo(Protocol):
    """O que o rótulo usa do serviço do Livro: só o sinal. O `LearningService` o cumpre (tipagem estrutural)."""

    def registrar_sinal(self, sinal: NovoSinal) -> int | None: ...


class ServicoDeRotulos:
    def __init__(self, livro: LivroDoRotulo, registro: RegistroDeRotulos, *,
                 cadeia: Callable[[str], CadeiaDaExecucao | None], catalogo: Callable[[], Sequence[tuple[str, str]]],
                 relogio: Callable[[], datetime]) -> None:
        self._livro = livro
        self._registro = registro
        self._cadeia = cadeia
        self._catalogo = catalogo
        self._relogio = relogio

    # ------------------------------------------------------------------ o minerador
    def minerador(self) -> MineradorDoRotulo:
        return MineradorDoRotulo(self)

    def abrir(self, run_id: str) -> int:
        """Abre a pergunta da execução, quando ela é uma. Devolve quantas linhas gravou (0 ou 1)."""
        fatos = self._registro.fatos(run_id)
        if fatos is None or not entra_no_rotulo(fatos) or self._registro.tem_rotulo(run_id):
            return 0
        cadeia = self._cadeia(run_id)
        if cadeia is None:
            return 0
        motivo = cadeia_a_rotular(resolvida=cadeia.resolvida, sem_casamento=cadeia.sem_casamento,
                                  empatados=cadeia.empatados)
        if motivo is None:
            return 0
        dossie = DossieDoRotulo.montar(motivo, (sid for sid, _ in self._catalogo()), cadeia.empatados)
        if not dossie.candidatos:
            return 0                                     # sem catálogo a resposta só podia ser `nenhum`: não pergunta
        gravado = self._registro.gravar(NovaRevisao(
            item_ref=item_ref_da_execucao(run_id), item_kind=KIND_DO_ROTULO, scope_app=cadeia.app or "",
            gatilho=GatilhoDoRotulo.EXECUCAO_SEM_INTENCAO.value, dossie_hash=dossie.dossie_hash,
            dossie=dossie.como_dados(), template_id=TEMPLATE_DO_ROTULO, template_versao=VERSAO_DO_ROTULO, provedor="",
            modelo="", simulated=False, validade="ok", saida=None, classe_de_risco=None, politica=None),
            self._relogio())
        return 1 if gravado is not None else 0

    # ------------------------------------------------------------------ o painel
    def pendentes(self, limite: int = PENDENTES_MAX) -> tuple[list[PerguntaAberta], int]:
        """As perguntas sem resposta, da mais recente para a mais antiga, e o total (a lista pode vir cortada)."""
        rotulos = self._registro.pendentes(max(1, min(limite, PENDENTES_MAX)))
        execucoes = self._registro.execucoes([r.run_id for r in rotulos])
        nomes = self._nomes()
        saida: list[PerguntaAberta] = []
        for r in rotulos:
            comando, terminou = execucoes.get(r.run_id, (None, None))
            cands = r.dossie.candidatos if r.dossie is not None else ()
            saida.append(PerguntaAberta(r, comando, terminou, {c: nomes.get(c, c) for c in cands}))
        return saida, self._registro.contar_pendentes()

    def _nomes(self) -> dict[str, str]:
        try:
            return {sid: nome for sid, nome in self._catalogo()}
        except Exception:  # noqa: BLE001 - sem nome, o painel mostra o id
            log.exception("aprendizado: catálogo da cadeia para os nomes do rótulo")
            return {}

    def rotular(self, run_id: str, escolha: str, *, by: str) -> RotuloGravado:
        """A resposta da pessoa. 404 sem pergunta; 422 fora do catálogo gravado; 409 já respondida (CAS)."""
        with self._registro.transacao():
            r = self._registro.da_execucao(run_id)
            if r is None or r.dossie is None:
                raise NaoEncontrado(f"A execução '{run_id}' não tem pergunta de intenção.")
            if r.decisao_final is not None:
                raise ConflitoDeEstado(f"A intenção da execução '{run_id}' já foi respondida.")
            decisao = conferir_escolha(escolha, r.dossie)
            if not self._registro.decidir(r.id, decisao_final=decisao, decidido_por=by):
                raise ConflitoDeEstado(f"A intenção da execução '{run_id}' já foi respondida.")
            self._livro.registrar_sinal(NovoSinal(
                kind=SignalKind.PARECER_DECIDIDO, source_ref=f"parecer:{r.id}", created_by=by,
                polarity=Polaridade.NEUTRAL, app_package=r.app, run_id=run_id,
                data={"review_id": r.id, "item_ref": item_ref_da_execucao(run_id), "decisao_final": decisao,
                      "override": False, "viu": False, "template_id": TEMPLATE_DO_ROTULO}))
        return RotuloGravado(r.id, r.criado_em, r.run_id, r.app, r.dossie, decisao, by)


class MineradorDoRotulo:
    """Cumpre `Minerador`. Nunca chama IA."""

    nome = "rotulo_de_intencao"

    def __init__(self, servico: ServicoDeRotulos) -> None:
        self._servico = servico

    def minerar(self, run_id: str) -> int:
        return self._servico.abrir(run_id)


__all__ = ["NENHUM", "PENDENTES_MAX", "CadeiaDaExecucao", "LivroDoRotulo", "MineradorDoRotulo", "PerguntaAberta",
           "RegistroDeRotulos", "RotuloGravado", "ServicoDeRotulos"]
