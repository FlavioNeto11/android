"""Decisores da porta: o nulo (padrão), o falso (testes) e o REAL, `DecisorJev` (item 31.14).

O real fala com a TypeSafe pelo transporte e pela chave do adaptador de retrieval (`modules/context_retrieval/adapters/jev.py`,
único arquivo autorizado a conhecer o host: o teste de cliente único o garante). Contrato que todo decisor cumpre:

- `decidir(pedido, timeout_s)` recebe um pedido JÁ validado e redigido por `Porta`, é síncrono (o POST de hoje é síncrono),
  não retenta, e devolve `ResultadoDeDecisao` ou levanta `FalhaDeDecisao(motivo)` com o motivo fechado;
- `Porta` ainda reconfere cada resposta (opção conhecida, limiar) e aplica o timeout por fora: o decisor não é confiado.

Existir não é enviar: com `JEV_RUNTIME_SEND_APPROVED` falso a porta recusa todo pedido ANTES de chegar a um decisor, e o
`DecisorJev` só é ligado por `ai.decisao_fechada.decisor: jev` (de fábrica, `nulo`).
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

from ...modules.context_retrieval.domain.errors import (
    ProviderError, ProviderInvalidResponse, ProviderKeyMissing, ProviderOverloaded, ProviderRateLimited,
    ProviderRejected, ProviderUnavailable,
)
from ...modules.context_retrieval.domain.model import ProviderUsage
from .contrato import (
    FalhaDeDecisao, FallbackReason, PedidoDeDecisao, Pergunta, RespostaDeDecisao, ResultadoDeDecisao,
    resultado_de_fallback,
)

log = logging.getLogger("poc.ai")


class Decisor(Protocol):
    def decidir(self, pedido: PedidoDeDecisao, timeout_s: float) -> ResultadoDeDecisao: ...


class DecisorNulo:
    """O padrão: nunca decide e nunca toca rede. Toda pergunta volta com `fallback_reason='desligado'`."""

    def decidir(self, pedido: PedidoDeDecisao, timeout_s: float) -> ResultadoDeDecisao:
        return resultado_de_fallback(pedido, "desligado")


class DecisorFalso:
    """Para testes: respostas programadas por id de pergunta; cada chamada registrada é UMA chamada (fan-out).

    Pergunta sem resposta programada volta com `fallback_reason=desligado`. Uma `FalhaDeDecisao` programada é levantada
    como o decisor real faria. `atraso_s` simula um decisor lento (teste de timeout)."""

    def __init__(self, respostas: Mapping[str, RespostaDeDecisao] | None = None, *, falha: FalhaDeDecisao | None = None,
                 atraso_s: float = 0.0, custo_tokens: int = 0, custo_usd: float = 0.0) -> None:
        self.respostas: dict[str, RespostaDeDecisao] = dict(respostas or {})
        self.falha = falha
        self.atraso_s = atraso_s
        self.custo_tokens = custo_tokens
        self.custo_usd = custo_usd
        #: Cada item é um pedido recebido = uma chamada. Nada de rede: é só o registro.
        self.chamadas: list[PedidoDeDecisao] = []

    def decidir(self, pedido: PedidoDeDecisao, timeout_s: float) -> ResultadoDeDecisao:
        self.chamadas.append(pedido)
        t0 = time.perf_counter()
        if self.atraso_s:
            time.sleep(self.atraso_s)
        if self.falha is not None:
            raise self.falha
        respostas = {p.id: self.respostas.get(p.id, RespostaDeDecisao(fallback_reason="desligado"))
                     for p in pedido.perguntas}
        return ResultadoDeDecisao(respostas, tokens=self.custo_tokens, usd=self.custo_usd,
                                  ms=(time.perf_counter() - t0) * 1000)


# ---------------------------------------------------------------------------------------------------- o real (31.14)
@dataclass(frozen=True)
class ChamadaAoJev:
    """Uma chamada ao Jev para a linha de `ai_calls` (31.14): só ids, números e o motivo fechado. O estado, as opções e as
    instruções nunca entram (a linha é lida pelo painel de uso e pelas réguas de gasto, não é registro de decisão)."""

    modelo: str
    origem: str                     # o consumidor da porta (curador, intencao, desempate, apps)
    run_id: str | None
    step_id: str | None
    ref: str | None
    tokens_entrada: int
    tokens_saida: int
    usd: float
    ms: float
    ok: bool
    motivo: FallbackReason | None   # por que falhou; None quando `ok`


class TransporteDoJev(Protocol):
    """O que o `DecisorJev` usa do adaptador de retrieval (`JevSemanticProvider.consultar`): o fio e nada mais."""

    model: str

    def consultar(self, estado: Mapping[str, object], perguntas: Mapping[str, Mapping[str, object]], *,
                  timeout_s: float) -> tuple[Mapping[str, object], ProviderUsage]: ...


def _motivo_da_falha(exc: ProviderError) -> FallbackReason:
    """Erro do transporte → motivo fechado. A chave ausente é tratada antes (nada saiu); aqui o POST foi tentado."""
    if isinstance(exc, ProviderUnavailable):         # 401/403
        return "401"
    if isinstance(exc, ProviderRejected):
        return "422"
    if isinstance(exc, ProviderRateLimited):
        return "429"
    if isinstance(exc, ProviderOverloaded):          # 503/529
        return "529"
    if isinstance(exc, ProviderInvalidResponse):
        return "parse"
    return "rede"                                    # timeout, sem conexão, HTTP inesperado


def _probabilidade(valor: object) -> float | None:
    if isinstance(valor, bool) or not isinstance(valor, (int, float)) or not 0.0 <= float(valor) <= 1.0 + 1e-9:
        return None
    return min(float(valor), 1.0)


def _resposta_choice(bruta: object) -> RespostaDeDecisao:
    """A resposta `choice` do fio (`choice`, `probabilities`, `confidence`) em `RespostaDeDecisao`. Só o FORMATO é conferido
    aqui (o que não é número em [0, 1] ou texto vira `parse`); opção conhecida e limiar são da porta, que não confia em mim."""
    if not isinstance(bruta, Mapping) or bruta.get("type") != "choice":
        return RespostaDeDecisao(fallback_reason="parse")
    escolha, probs, confianca = bruta.get("choice"), bruta.get("probabilities"), _probabilidade(bruta.get("confidence"))
    if not isinstance(escolha, str) or not isinstance(probs, Mapping) or confianca is None:
        return RespostaDeDecisao(fallback_reason="parse")
    limpas: dict[str, float] = {}
    for opcao, valor in probs.items():
        p = _probabilidade(valor)
        if not isinstance(opcao, str) or p is None:
            return RespostaDeDecisao(fallback_reason="parse")
        limpas[opcao] = p
    return RespostaDeDecisao(escolha=escolha, probabilidades=limpas, confianca=confianca)


def _pergunta_do_fio(p: Pergunta) -> dict[str, object]:
    return {"type": "choice", "instructions": p.instrucoes, "criteria": dict(p.opcoes)}


class DecisorJev:
    """O decisor REAL (31.14): uma chamada ao Jev por pedido, pelo transporte do adaptador de retrieval.

    Ordem, e o que cada passo garante:
    1. Só `choice` vai ao fio. `noul` e `score` ainda não têm consumidor: respondem `desligado` sem sair (o formato deles
       entra junto do primeiro consumidor, com teste).
    2. **Gasto conferido ANTES do POST** (`conferir_gasto`: teto do pedido, da execução e do dia, fatia do Jev e saldo da
       conta do Jev, a mesma rubrica do hub). Barrado, ou sem como conferir, é `orcamento` e nada sai: falha fechada.
    3. O POST usa o que resta do prazo depois da conferência; sem prazo é `rede`, sem POST.
    4. **Toda chamada tentada vira linha em `ai_calls`** (`registrar`), com `usd` e tokens de entrada: é por ela que a fatia do
       Jev, o teto do dia e o saldo da conta enxergam o gasto. A falha também vira linha (`ok=0`, motivo fechado). Chave
       ausente não é chamada: nada saiu e nada é registrado.
    5. A resposta só tem o FORMATO conferido aqui; a porta reconfere opção e limiar.
    """

    def __init__(self, transporte: TransporteDoJev, *, conferir_gasto: Callable[[PedidoDeDecisao], None] | None,
                 registrar: Callable[[ChamadaAoJev], None] | None = None) -> None:
        self._transporte = transporte
        self._conferir_gasto = conferir_gasto
        self._registrar = registrar

    def __repr__(self) -> str:  # sem o transporte: nada que possa carregar a chave
        return f"DecisorJev(modelo={self._transporte.model!r})"

    def decidir(self, pedido: PedidoDeDecisao, timeout_s: float) -> ResultadoDeDecisao:
        t0 = time.perf_counter()
        enviaveis = [p for p in pedido.perguntas if p.tipo == "choice"]
        if not enviaveis:
            return resultado_de_fallback(pedido, "desligado")
        if not self._gasto_liberado(pedido):
            raise FalhaDeDecisao("orcamento")
        restante = timeout_s - (time.perf_counter() - t0)
        if restante <= 0:
            raise FalhaDeDecisao("rede")
        # O motivo é só CLASSIFICADO dentro do `except`; a `FalhaDeDecisao` sai fora dele, para não carregar `__context__`
        # com a exceção do transporte (o mesmo cuidado do adaptador).
        motivo: FallbackReason = "rede"
        resposta: tuple[Mapping[str, object], ProviderUsage] | None = None
        enviado = time.perf_counter()
        try:
            resposta = self._transporte.consultar(dict(pedido.estado), {p.id: _pergunta_do_fio(p) for p in enviaveis},
                                                  timeout_s=restante)
        except ProviderKeyMissing:
            motivo = "desligado"                     # sem chave nada sai: não é chamada, não vira linha
        except ProviderError as exc:
            motivo = _motivo_da_falha(exc)
        if resposta is None:
            if motivo != "desligado":
                self._anotar(pedido, ok=False, motivo=motivo, ms=(time.perf_counter() - enviado) * 1000)
            raise FalhaDeDecisao(motivo)
        respostas_cruas, uso = resposta
        self._anotar(pedido, ok=True, uso=uso)
        respostas = {p.id: _resposta_choice(respostas_cruas.get(p.id)) if p.tipo == "choice"
                     else RespostaDeDecisao(fallback_reason="desligado") for p in pedido.perguntas}
        return ResultadoDeDecisao(respostas, tokens=uso.input_tokens + uso.output_tokens, usd=uso.cost_usd,
                                  ms=uso.latency_ms)

    def _gasto_liberado(self, pedido: PedidoDeDecisao) -> bool:
        """Sem conferência ligada, ou com ela quebrada, não sai: o gasto que ninguém conferiu não acontece."""
        if self._conferir_gasto is None:
            return False
        try:
            self._conferir_gasto(pedido)
        except Exception:  # noqa: BLE001 - régua estourada (AIError de orçamento ou de saldo) ou leitura quebrada
            log.warning("decisao_fechada: gasto do Jev barrado ou não conferido; nada sai")
            return False
        return True

    def _anotar(self, pedido: PedidoDeDecisao, *, ok: bool, motivo: FallbackReason | None = None,
                uso: ProviderUsage | None = None, ms: float = 0.0) -> None:
        if self._registrar is None:
            return
        try:
            self._registrar(ChamadaAoJev(
                modelo=self._transporte.model, origem=pedido.origem, run_id=pedido.run_id, step_id=pedido.step_id,
                ref=pedido.ref, tokens_entrada=uso.input_tokens if uso else 0, tokens_saida=uso.output_tokens if uso else 0,
                usd=uso.cost_usd if uso else 0.0, ms=uso.latency_ms if uso else ms, ok=ok, motivo=motivo))
        except Exception:  # noqa: BLE001 - medir nunca derruba o trabalho (a régua fica cega para ESTA chamada; fica o log)
            log.warning("decisao_fechada: não foi possível registrar a chamada ao Jev em ai_calls")
