"""Pacote A2 do ADR-054: liga as costuras tipadas dos arquivos quentes (`app/taskqueue/costuras.py`) ao livro.

Duas partes:

- `ligar(servico)`, chamado pela montagem: abre o registro de EXTENSÕES das costuras deste serviço, onde os pacotes
  seguintes se penduram sem tocar os arquivos quentes — o fornecedor das lições (A7) e os observadores do fechamento
  de tentativa (A8, as telas vistas). O registro é por serviço (um `AppState`, um livro): nada global que um segundo
  `AppState` herdasse do primeiro;
- `costuras_do_livro(servico, db)`, chamado pelo `AppState` depois de montar executor, serviço de execução e
  gerenciador de aparelhos: o objeto que cumpre `CosturasDeAprendizado` e `CosturaDeControle` e que o `AppState`
  pendura em cada um.

O que este pacote grava por conta própria são SINAIS (`learning_signals`), idempotentes pela chave
`(kind, source_ref, created_by)`, sem IA:

- `confirmou_a_mao` (neutro: positivo para a ação e negativo para a verificação — nunca evidência a favor de
  aprendizado), `repetiu_item` (neutro) e `abandonou_item` (negativo), por `ao_resolver`, com a nota redigida (a que
  parece credencial não fica: `note_refused=1`);
- `repetiu_execucao` (neutro), por `ao_repetir`;
- `respondeu_pergunta` (neutro), um por campo, com o campo e o sha256 da resposta — nunca o valor;
- `tomou_controle` (negativo), um por tentativa tomada, sem árvore, texto ou coordenada.

Quem faz o gesto é uma pessoa pelo painel, mas o nome do operador não chega aos arquivos quentes: o sinal sai como
`panel` (a regra `_quem`: sem nome, `panel`), nunca como `sistema` — a régua diária só conta como negativo humano o que
não é do sistema. O app e a ação do sinal saem da etapa pela mesma regra da régua diária (`app_da_etapa`, ação nula
= `*`), para os dois agregarem na mesma chave; `simulated` vem de `runs.simulated`, e sinal de execução simulada
nunca promove nada.

`aprendizado.enabled: false` desliga tudo (lido a cada chamada); lições no modo `off` nem são pedidas.
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from typing import Protocol
from weakref import WeakKeyDictionary

from app.db import Database
from app.modules.learning.application.ports import NovoSinal
from app.modules.learning.application.servico import LearningService
from app.modules.learning.domain.vocabulario import Modo, Polaridade, SignalKind
from app.modules.learning.infrastructure import linhas
from app.taskqueue.costuras import (DecisaoSobreItem, FechamentoDeTentativa, PedidoDeLicoes, RepeticaoDeExecucao,
                                    ResolucaoDeItem, RespostaAPergunta, TomadaDeControle)
from app.taskqueue.projecao import QUALQUER, app_da_etapa

log = logging.getLogger("poc.aprendizado")

#: Quem deu o sinal de um gesto do painel (ver o docstring do módulo).
PAINEL = "panel"

_DO_GESTO: dict[DecisaoSobreItem, tuple[SignalKind, Polaridade]] = {
    "confirm_done": (SignalKind.CONFIRMOU_A_MAO, Polaridade.NEUTRAL),
    "retry": (SignalKind.REPETIU_ITEM, Polaridade.NEUTRAL),
    "abandon": (SignalKind.ABANDONOU_ITEM, Polaridade.NEGATIVE),
}

_FORA_DO_CAMPO = re.compile(r"[^a-z0-9_]+")


# ------------------------------------------------------------------ extensões (onde A7 e A8 se penduram)
class FornecedorDeLicoes(Protocol):
    """Quem escolhe as lições de um pedido (A7): modelo fechado, teto do papel, braço de controle e exposição."""

    def licoes_para(self, pedido: PedidoDeLicoes) -> list[str]: ...


class ObservadorDeTentativa(Protocol):
    """Quem aproveita a tentativa fechada (A8: a tela vista numa etapa comprovada). Roda no fechamento, no caminho da
    execução: grava barato e nunca chama IA."""

    nome: str

    def ao_fechar(self, fechamento: FechamentoDeTentativa) -> None: ...


@dataclass(slots=True)
class Extensoes:
    licoes: FornecedorDeLicoes | None = None
    observadores: list[ObservadorDeTentativa] = field(default_factory=list)

    def definir_licoes(self, fornecedor: FornecedorDeLicoes) -> None:
        self.licoes = fornecedor

    def observar(self, observador: ObservadorDeTentativa) -> None:
        """O mesmo `nome` não entra duas vezes (a montagem pode rodar de novo no mesmo serviço)."""
        if all(o.nome != observador.nome for o in self.observadores):
            self.observadores.append(observador)


_EXTENSOES: WeakKeyDictionary[LearningService, Extensoes] = WeakKeyDictionary()


def extensoes(servico: LearningService) -> Extensoes:
    """As extensões das costuras deste serviço (criadas na primeira vez)."""
    ext = _EXTENSOES.get(servico)
    if ext is None:
        ext = _EXTENSOES[servico] = Extensoes()
    return ext


def ligar(servico: LearningService) -> None:
    """Abre o registro das extensões deste serviço. As costuras em si o `AppState` pendura (`costuras_do_livro`): é
    ele quem tem o executor, o serviço de execução e o gerenciador de aparelhos."""
    extensoes(servico)


def costuras_do_livro(servico: LearningService, db: Database) -> CosturasDoLivro:
    return CosturasDoLivro(servico, db, extensoes(servico))


# ------------------------------------------------------------------ as costuras
@dataclass(frozen=True, slots=True)
class _Contexto:
    """O que o sinal precisa saber da execução (e da etapa, quando há), lido do banco no instante do gesto."""

    simulated: bool
    app_package: str = ""
    capability: str = ""
    step_hash: str | None = None
    objective_id: str | None = None
    instance_id: str | None = None
    profile_id: str | None = None


class CosturasDoLivro:
    """Cumpre `CosturasDeAprendizado` e `CosturaDeControle` (`app/taskqueue/costuras.py`). Toda exceção daqui é
    engolida por quem chama (`avisar`, `pedir_licoes`): o gesto e a etapa seguem."""

    def __init__(self, servico: LearningService, db: Database, ext: Extensoes) -> None:
        self._servico = servico
        self._db = db
        self._ext = ext

    def _ligado(self) -> bool:
        return self._servico.ajustes.enabled

    # ---------------------------------------------------------------- executor e planejamento
    def licoes_para(self, pedido: PedidoDeLicoes) -> list[str]:
        ajustes = self._servico.ajustes
        fornecedor = self._ext.licoes
        if not ajustes.enabled or ajustes.modo_licoes is Modo.OFF or fornecedor is None:
            return []
        return list(fornecedor.licoes_para(pedido))

    def ao_fechar_tentativa(self, fechamento: FechamentoDeTentativa) -> None:
        if not self._ligado():
            return
        for observador in tuple(self._ext.observadores):
            try:
                observador.ao_fechar(fechamento)
            except Exception:  # noqa: BLE001 - um observador quebrado não cala os outros nem a etapa
                log.exception("aprendizado: observador %s falhou na tentativa %s", observador.nome,
                              fechamento.attempt_id)

    # ---------------------------------------------------------------- gestos de uma pessoa
    def ao_resolver(self, resolucao: ResolucaoDeItem) -> None:
        if not self._ligado():
            return
        ctx = self._contexto(resolucao.run_id, objective_id=resolucao.objective_id, step_id=resolucao.step_id)
        if ctx is None:
            return
        kind, polaridade = _DO_GESTO[resolucao.resolucao]
        self._servico.registrar_sinal(NovoSinal(
            kind=kind, source_ref=f"resolve:{resolucao.objective_id}:{resolucao.ordem}", created_by=PAINEL,
            polarity=polaridade, note=resolucao.nota, run_id=resolucao.run_id, objective_id=resolucao.objective_id,
            step_id=resolucao.step_id, instance_id=ctx.instance_id, profile_id=ctx.profile_id,
            app_package=ctx.app_package, capability=ctx.capability, step_hash=ctx.step_hash,
            # A etapa que esperava a decisão estava parada (incerta, esperando, falhou): nunca comprovada.
            step_verified=False if resolucao.step_id else None, data={"resolucao": resolucao.resolucao},
            simulated=ctx.simulated))

    def ao_repetir(self, repeticao: RepeticaoDeExecucao) -> None:
        if not self._ligado() or not repeticao.objetivos:
            return
        ctx = self._contexto(repeticao.run_id)
        if ctx is None:
            return
        gesto = hashlib.sha1(",".join(sorted(repeticao.objetivos)).encode()).hexdigest()[:12]
        self._servico.registrar_sinal(NovoSinal(
            kind=SignalKind.REPETIU_EXECUCAO, source_ref=f"repeticao:{repeticao.run_id}:{gesto}", created_by=PAINEL,
            polarity=Polaridade.NEUTRAL, run_id=repeticao.run_id, app_package=ctx.app_package,
            data={"itens": len(repeticao.objetivos)}, simulated=ctx.simulated))

    def respondeu_pergunta(self, resposta: RespostaAPergunta) -> None:
        if not self._ligado():
            return
        ctx = self._contexto(resposta.run_id)
        if ctx is None:
            return
        for campo in dict.fromkeys(_campo(c) for c in (resposta.campos or ("",))):
            self._servico.registrar_sinal(NovoSinal(
                kind=SignalKind.RESPONDEU_PERGUNTA, source_ref=f"resposta:{resposta.run_id}:{campo}",
                created_by=PAINEL, polarity=Polaridade.NEUTRAL, run_id=resposta.run_id, app_package=ctx.app_package,
                data={"campo": campo, "resposta_sha256": resposta.resposta_sha256,
                      "run_sucessora": resposta.run_sucessora},
                simulated=ctx.simulated))

    def tomou_controle(self, tomada: TomadaDeControle) -> None:
        if not self._ligado():
            return
        ctx = self._contexto(tomada.run_id, objective_id=tomada.objective_id, step_id=tomada.step_id)
        if ctx is None:
            return
        linha = self._db.one("SELECT id FROM attempts WHERE step_id=? AND status='running' ORDER BY number DESC"
                             " LIMIT 1", (tomada.step_id,))
        tentativa = linhas.texto(linha, "id") if linha is not None else None
        self._servico.registrar_sinal(NovoSinal(
            kind=SignalKind.TOMOU_CONTROLE, source_ref=f"takeover:{tentativa or tomada.step_id}", created_by=PAINEL,
            polarity=Polaridade.NEGATIVE, run_id=tomada.run_id, objective_id=tomada.objective_id or ctx.objective_id,
            step_id=tomada.step_id, attempt_id=tentativa, instance_id=tomada.instance_id,
            profile_id=ctx.profile_id, app_package=ctx.app_package, capability=ctx.capability,
            step_hash=ctx.step_hash, step_verified=False, simulated=ctx.simulated))

    # ---------------------------------------------------------------- leitura
    def _contexto(self, run_id: str, *, objective_id: str | None = None,
                  step_id: str | None = None) -> _Contexto | None:
        """A execução (e a etapa, e o objetivo) do sinal. Sem a execução no banco não há a quem atribuir: nada é
        gravado (e nada é presumido real)."""
        execucao = self._db.one("SELECT simulated, app_ids FROM runs WHERE id=?", (run_id,))
        if execucao is None:
            return None
        simulado = bool(linhas.inteiro(execucao, "simulated"))
        etapa = self._db.one("SELECT objective_id, instance_id, capability, template_hash, app_id FROM steps"
                             " WHERE id=?", (step_id,)) if step_id else None
        objetivo_id = (linhas.texto_ou_nulo(etapa, "objective_id") if etapa is not None else None) or objective_id
        objetivo = self._db.one("SELECT instance_id, profile_id FROM objectives WHERE id=?",
                                (objetivo_id,)) if objetivo_id else None
        app = app_da_etapa(etapa["app_id"] if etapa is not None else None, execucao["app_ids"])
        return _Contexto(
            simulated=simulado, app_package=self._pacote(app),
            capability=(linhas.texto_ou_nulo(etapa, "capability") or "*") if etapa is not None else "",
            step_hash=linhas.texto_ou_nulo(etapa, "template_hash") if etapa is not None else None,
            objective_id=objetivo_id,
            instance_id=(linhas.texto_ou_nulo(etapa, "instance_id") if etapa is not None else None)
            or (linhas.texto_ou_nulo(objetivo, "instance_id") if objetivo is not None else None),
            profile_id=linhas.texto_ou_nulo(objetivo, "profile_id") if objetivo is not None else None)

    def _pacote(self, app: str) -> str:
        """O pacote do app (a chave da régua diária), ou o id quando o cadastro não o conhece; sem app, ''."""
        if not app or app == QUALQUER:
            return ""
        linha = self._db.one("SELECT package FROM apps WHERE id=?", (app,))
        return (linhas.texto_ou_nulo(linha, "package") if linha is not None else None) or app


def _campo(bruto: str) -> str:
    """O nome do campo perguntado, em snake_case ASCII e curto — é identificador, nunca texto de pessoa."""
    return _FORA_DO_CAMPO.sub("_", bruto.strip().lower()).strip("_")[:60]


__all__ = ["PAINEL", "CosturasDoLivro", "Extensoes", "FornecedorDeLicoes", "ObservadorDeTentativa",
           "costuras_do_livro", "extensoes", "ligar"]
