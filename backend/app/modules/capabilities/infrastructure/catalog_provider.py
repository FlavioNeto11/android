"""`CatalogCapabilityProvider`: a porta `CapabilityProvider` (design §7) sobre o catálogo e a prova local de hoje.

O que é real desde já: `supports` e `verify`. `verify` embrulha `taskqueue/proofs.py::local_proof_holds` com as
mesmas regras que o executor aplica em `_verify`, e acrescenta só o que é leitura pura da mesma tela.

O que NÃO é, e por quê (decisão desta fase, §18 C: "tudo tipado, sem fiação em runtime"):

- `observe` precisa do driver do aparelho. Quem observa hoje é o executor (`devices.observe`), dono do aparelho
  durante a etapa; um segundo caminho de leitura disputaria o driver. Na fase G o executor passa a observação pronta.
- `execute` é a cadeia de estratégias do nó (receita, depois IA), que mora no executor (`run_step`/`_run_step`) e só
  sai de lá atrás da `ExecutionStrategy`, na fase G. Reimplementar aqui seria um segundo executor.
- `reconcile` é `commit_state` + o reconciliador do scheduler, ambos com escrita no banco e posse da etapa (R4,
  R11): só o hospedeiro reconcilia, e esta classe não tem como provar que é ele.

Nenhuma das três toca aparelho: levantam `NotImplementedError` com o motivo, para que uma fiação apressada falhe
alto em vez de "verificar" sem ter observado.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.automation.hierarchy import UiTree
from app.planning.capabilities import guardas_do_cartao
from app.taskqueue.proofs import local_proof_holds, marcas_pendentes_na_tela

from ..domain.definition import CapabilityRef, PostconditionKind
from ..domain.strategy import StrategyContext, StrategyResult
from ..domain.verification import Observation, StepView, VerifyOutcome, VerifyResult
from .catalog_registry import CatalogCapabilityRegistry


@dataclass(slots=True)
class _EtapaDaProva:
    """A forma que `local_proof_holds` lê (`bindings` como dicionário, `band_guard` e `card_guard` como lista)."""

    bindings: dict[str, str]
    band_guard: list[str]
    #: Já resolvido pelos argumentos da etapa: vazio quando a publicação é por posição (sem `caption_contains`).
    card_guard: list[str] = field(default_factory=list)


def _nao_afirma(motivo: str) -> VerifyResult:
    return VerifyResult(VerifyOutcome.unknown, motivo)


class CatalogCapabilityProvider:
    def __init__(self, registry: CatalogCapabilityRegistry) -> None:
        self._registry = registry

    def supports(self, cap: CapabilityRef) -> bool:
        return self._registry.by_ref(cap) is not None

    async def observe(self, ctx: StrategyContext) -> Observation:
        raise NotImplementedError("observar exige o driver do aparelho, que é do executor durante a etapa "
                                  "(fase G: o executor entrega a observação)")

    async def execute(self, node: StepView, ctx: StrategyContext) -> StrategyResult:
        raise NotImplementedError("executar é a cadeia de estratégias do nó, que mora no executor até a fase G "
                                  "(ExecutionStrategy)")

    async def reconcile(self, node: StepView, ctx: StrategyContext) -> VerifyResult:
        raise NotImplementedError("reconciliar é do hospedeiro da etapa (commit_state e o reconciliador do "
                                  "scheduler, R4/R11); a fiação entra na fase G")

    async def verify(self, node: StepView, obs: Observation) -> VerifyResult:
        definicao = self._registry.by_ref(node.capability) if node.capability else None
        if definicao is None:
            return _nao_afirma("etapa sem capability do catálogo: quem julga é o verificador")
        tela = obs.screen
        if not isinstance(tela, UiTree):
            return _nao_afirma("observação sem árvore de tela reconhecível")
        if not tela.elements:
            # Tela carregando ou em branco não prova estado nenhum (item 7.10: falsos positivos em tela vazia).
            return _nao_afirma("tela sem elementos na hierarquia não prova nada")
        # Marca de falha visível é a tela DESMENTINDO o efeito ("Not delivered"): é a única negativa afirmável
        # sem modelo. Mesma conferência por texto que o executor faz depois de assentar a tela.
        marcas = [m for m in definicao.side_effect.failure_marks if m and tela.contains_text(m)]
        if marcas:
            return VerifyResult(VerifyOutcome.not_proved,
                                "a tela mostra " + ", ".join(f'"{m}"' for m in marcas) + " depois do efeito")
        # Efeito a caminho ("Sending…"): antes da prova local, porque a bolha com o texto e o campo limpo também estão
        # na tela enquanto o app ainda envia — foi assim que a DM da beatriz passou por enviada em 19/09 (ADR-055).
        pendentes = marcas_pendentes_na_tela(definicao.side_effect.pending_marks, tela)
        if pendentes:
            return VerifyResult(VerifyOutcome.pending, "envio pendente: a tela ainda mostra "
                                + ", ".join(f'"{m}"' for m in pendentes) + " (pendente não conta como feito)")
        if definicao.postcondition.kind is not PostconditionKind.model_judged:
            # Pós-condição determinística (seletor, texto, app em primeiro plano) é conferida pelo executor
            # (`_deterministic`, e a legenda de `card_guard` junto); a prova local só existe como atalho do
            # julgamento por modelo.
            return _nao_afirma("pós-condição determinística: conferida pelo executor")
        if node.required_delivery_level is not None:
            return _nao_afirma(f"nível de entrega {node.required_delivery_level} exige o verificador: "
                               "a árvore prova envio, não entrega")
        if not definicao.local_proof:
            return _nao_afirma("a capability não declara prova local")
        # A legenda da publicação alvo restringe a prova ao cartão dela (r-20260928165254-e31953: `desc==Liked` de
        # outro cartão fechava a curtida). Sem `caption_contains`, `card_guard` fica vazio e a prova é a de sempre.
        etapa = _EtapaDaProva(bindings=dict(node.bindings), band_guard=list(node.band_guard),
                              card_guard=list(guardas_do_cartao(definicao.card_guard, dict(node.bindings))))
        if local_proof_holds(definicao.local_proof, etapa, tela) is True:
            return VerifyResult(VerifyOutcome.proved,
                                f"pós-condição comprovada pela árvore local, sem IA ({definicao.local_proof})")
        # Negativa ou indisponível NUNCA reprova sozinha (`taskqueue/proofs.py`): cai para o verificador.
        return _nao_afirma(f"prova local ({definicao.local_proof}) não confirmou: quem julga é o verificador")
