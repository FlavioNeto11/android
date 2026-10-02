"""A porta `DecisaoFechada`: modos, privacidade, timeout e recurso ao caminho atual (item 31.4, ADR-069).

Ordem do que acontece num pedido, e o que cada passo garante:

1. **Modo efetivo** = o MENOR entre o do pedido e o do consumidor na config (`off < shadow < on`); `ai.decisao_fechada.enabled`
   falso vence tudo. `off` (o padrão) não faz nada: nem valida, nem monta corpo, nem chama decisor.
2. **Privacidade** (`privacidade.validar`) ANTES de qualquer corpo. Recusa o pedido inteiro, na sombra também (na sombra a
   exposição já aconteceria: ela reduz o risco de efeito, não o de privacidade).
3. **Redação** de toda string (`privacidade.redigir`) e só então o decisor.
4. **`on`**: síncrono, timeout de 1 s, sem retentativa. **`shadow`**: roda numa thread própria, FORA do caminho crítico, com
   timeout de 5 s; o chamador recebe na hora um resultado neutro (`desligado`) e a resposta real só vai ao `observador`
   (registro sem o estado). Nada do caminho de trabalho usa a resposta da sombra.
5. **Reconferência**: o decisor não é confiado. Escolha fora das opções enviadas vira `unknown_choice`; confiança ausente ou
   abaixo do limiar vira `abaixo_do_limiar`. Em todo fallback a `escolha` é None: **um fallback nunca conta como acerto**.

Quem chama em código assíncrono usa `asyncio.to_thread(porta.consultar, pedido)`: o `on` bloqueia até 1 s.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturoExpirou
from dataclasses import dataclass, replace
from threading import Lock
from typing import TYPE_CHECKING, Final

from . import privacidade
from .contrato import (
    FalhaDeDecisao, Modo, PedidoDeDecisao, Pergunta, RespostaDeDecisao, ResultadoDeDecisao,
    resultado_de_fallback,
)
from .decisores import Decisor, DecisorNulo

if TYPE_CHECKING:
    from ...config import DecisaoFechadaCfg

log = logging.getLogger("poc.ai")

#: Timeouts do ADR-069 (roteiro §3 item 6): p95 medido de 425 a 443 ms cabe em 1 s no caminho crítico; a sombra tem folga.
TIMEOUT_ON_S: Final = 1.0
TIMEOUT_SHADOW_S: Final = 5.0
_ORDEM: Final[Mapping[str, int]] = {"off": 0, "shadow": 1, "on": 2}


@dataclass(frozen=True)
class RegistroDeDecisao:
    """O que o observador recebe: identificação e resultado, NUNCA o estado enviado (a tabela de sombra, 31.5, só guarda ids
    e categorias)."""

    origem: str
    classe: str
    modo: Modo
    run_id: str | None
    step_id: str | None
    ref: str | None
    resultado: ResultadoDeDecisao


Observador = Callable[[RegistroDeDecisao], None]


def modo_efetivo(pedido_modo: str, cfg: DecisaoFechadaCfg | None, origem: str) -> Modo:
    """Menor entre o modo pedido e o do consumidor; config ausente ou `enabled` falso = `off`."""
    if cfg is None or not cfg.enabled:
        return "off"
    consumidor = cfg.consumidores.get(origem, "off")
    menor = min((pedido_modo, consumidor), key=lambda m: _ORDEM.get(m, 0))
    return "shadow" if menor == "shadow" else "on" if menor == "on" else "off"


class Porta:
    def __init__(self, decisor: Decisor | None = None, *, cfg: DecisaoFechadaCfg | None = None,
                 observador: Observador | None = None) -> None:
        self.decisor: Decisor = decisor or DecisorNulo()
        self.cfg = cfg
        self.observador = observador
        self._pool: ThreadPoolExecutor | None = None
        self._pendentes: list[Future[None]] = []
        self._trava = Lock()

    # ------------------------------------------------------------------ API
    def consultar(self, pedido: PedidoDeDecisao, *,
                  ao_registrar: Callable[[], object] | None = None) -> ResultadoDeDecisao:
        """`ao_registrar` (opcional) roda logo DEPOIS de o observador gravar a linha, na mesma thread: na sombra, na thread
        da porta; na recusa e no `on`, aqui mesmo. É onde quem chama casa a decisão real sem esperar a linha aparecer."""
        modo = modo_efetivo(pedido.modo, self.cfg, pedido.origem)
        if modo == "off":
            return resultado_de_fallback(pedido, "desligado")
        recusa = privacidade.validar(replace(pedido, modo=modo), classes_yaml=self._classes_yaml())
        if not recusa.permitido:
            res = resultado_de_fallback(pedido, "privacidade")
            self._observar(pedido, modo, res, ao_registrar)  # a recusa também é medida (ids e categorias, sem estado)
            return res
        pronto = privacidade.redigir(pedido)
        if modo == "shadow":
            self._agendar(pronto, ao_registrar)
            return resultado_de_fallback(pedido, "desligado")  # nada do caminho de trabalho usa a sombra
        res = self._chamar_com_timeout(pronto, TIMEOUT_ON_S)
        self._observar(pedido, modo, res, ao_registrar)
        return res

    def aguardar_sombras(self, timeout_s: float = 10.0) -> None:
        """Espera as sombras pendentes terminarem (testes e desligamento limpo)."""
        with self._trava:
            futuros, self._pendentes = self._pendentes, []
        for f in futuros:
            try:
                f.result(timeout=timeout_s)
            except Exception:  # a sombra já engoliu e registrou o próprio erro
                pass

    # ------------------------------------------------------------------ internos
    def _classes_yaml(self) -> frozenset[str] | None:
        lista = None if self.cfg is None else self.cfg.classes_permitidas
        return None if lista is None else frozenset(lista)

    def _executor(self) -> ThreadPoolExecutor:
        with self._trava:
            if self._pool is None:
                self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="decisao-fechada")
            return self._pool

    def _agendar(self, pedido: PedidoDeDecisao, ao_registrar: Callable[[], object] | None = None) -> None:
        f = self._executor().submit(self._sombra, pedido, ao_registrar)
        with self._trava:
            self._pendentes = [p for p in self._pendentes if not p.done()] + [f]

    def _sombra(self, pedido: PedidoDeDecisao, ao_registrar: Callable[[], object] | None = None) -> None:
        t0 = time.perf_counter()
        try:
            bruto = self.decisor.decidir(pedido, TIMEOUT_SHADOW_S)
            ms = (time.perf_counter() - t0) * 1000
            res = (resultado_de_fallback(pedido, "rede", ms=ms) if ms > TIMEOUT_SHADOW_S * 1000
                   else self._conferir(pedido, bruto))
        except FalhaDeDecisao as exc:
            res = resultado_de_fallback(pedido, exc.motivo, ms=(time.perf_counter() - t0) * 1000)
        except Exception:
            log.warning("decisao_fechada: falha inesperada do decisor na sombra")
            res = resultado_de_fallback(pedido, "rede", ms=(time.perf_counter() - t0) * 1000)
        self._observar(pedido, "shadow", res, ao_registrar)

    def _chamar_com_timeout(self, pedido: PedidoDeDecisao, timeout_s: float) -> ResultadoDeDecisao:
        t0 = time.perf_counter()
        fut = self._executor().submit(self.decisor.decidir, pedido, timeout_s)
        try:
            return self._conferir(pedido, fut.result(timeout=timeout_s))
        except FuturoExpirou:
            fut.cancel()  # se já começou, a resposta tardia é descartada: o caminho de hoje não espera
            return resultado_de_fallback(pedido, "rede", ms=(time.perf_counter() - t0) * 1000)
        except FalhaDeDecisao as exc:
            return resultado_de_fallback(pedido, exc.motivo, ms=(time.perf_counter() - t0) * 1000)
        except Exception:
            log.warning("decisao_fechada: falha inesperada do decisor")
            return resultado_de_fallback(pedido, "rede", ms=(time.perf_counter() - t0) * 1000)

    def _observar(self, pedido: PedidoDeDecisao, modo: Modo, res: ResultadoDeDecisao,
                  ao_registrar: Callable[[], object] | None = None) -> None:
        if self.observador is None:
            return
        try:
            self.observador(RegistroDeDecisao(pedido.origem, pedido.classe, modo, pedido.run_id, pedido.step_id,
                                              pedido.ref, res))
        except Exception:  # medir nunca derruba o trabalho
            log.warning("decisao_fechada: observador falhou")
            return
        if ao_registrar is not None:
            try:
                ao_registrar()
            except Exception:  # idem: casar a decisão real é medição
                log.warning("decisao_fechada: falha ao casar a decisão real")

    @staticmethod
    def _conferir(pedido: PedidoDeDecisao, bruto: ResultadoDeDecisao) -> ResultadoDeDecisao:
        respostas = {p.id: _conferir_resposta(p, bruto.respostas.get(p.id)) for p in pedido.perguntas}
        return ResultadoDeDecisao(respostas, tokens=bruto.tokens, usd=bruto.usd, ms=bruto.ms,
                                  fallback_reason=bruto.fallback_reason)


def _conferir_resposta(p: Pergunta, r: RespostaDeDecisao | None) -> RespostaDeDecisao:
    """Defesa em profundidade: o que o decisor devolveu é conferido contra a pergunta ENVIADA."""
    if r is None:
        return RespostaDeDecisao(fallback_reason="parse")
    if r.fallback_reason is not None:
        return RespostaDeDecisao(probabilidades=r.probabilidades, fallback_reason=r.fallback_reason)
    if p.tipo == "choice":
        if r.escolha not in p.opcoes or any(k not in p.opcoes for k in r.probabilidades):
            return RespostaDeDecisao(fallback_reason="unknown_choice")
    elif p.tipo == "noul":
        if r.escolha not in (None, "sim", "nao"):
            return RespostaDeDecisao(fallback_reason="unknown_choice")
    elif r.escolha is not None:  # score não escolhe nada: devolve só a confiança
        return RespostaDeDecisao(fallback_reason="unknown_choice")
    if r.confianca is None or not 0.0 <= r.confianca <= 1.0 or r.confianca < p.limiar:
        return RespostaDeDecisao(probabilidades=r.probabilidades, confianca=r.confianca,
                                 fallback_reason="abaixo_do_limiar")
    return RespostaDeDecisao(escolha=r.escolha, probabilidades=r.probabilidades, confianca=r.confianca)


def construir_porta(cfg: DecisaoFechadaCfg | None = None, *, observador: Observador | None = None) -> Porta:
    """Porta padrão: decisor NULO. O 31.8 troca o decisor pelo real, depois do ADR-069 e da chave nova do dono."""
    return Porta(DecisorNulo(), cfg=cfg, observador=observador)


__all__ = ["Porta", "RegistroDeDecisao", "TIMEOUT_ON_S", "TIMEOUT_SHADOW_S", "construir_porta",
           "modo_efetivo"]
