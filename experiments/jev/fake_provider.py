"""Provedor FALSO que fala o formato de fio do Jev. Serve para testar o harness, NÃO para estimar o Jev real.

Os números que ele produz não dizem nada sobre a qualidade do Jev: a pontuação é uma sobreposição lexical simples,
independente do gabarito, e os modos só injetam o comportamento (confiança alta/baixa, timeout, erro, resposta
inválida, escolha fora do conjunto) que o resto do código precisa tratar.
"""
from __future__ import annotations

import math
from enum import Enum
from typing import Any, Mapping

from provider import DecisionProvider, ProviderError, ProviderTimeout, payload_bytes, request_body
from textutil import tokenize


class FakeMode(str, Enum):
    HIGH_CONFIDENCE = "HIGH_CONFIDENCE"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    TIMEOUT = "TIMEOUT"
    ERROR = "ERROR"
    INVALID_RESPONSE = "INVALID_RESPONSE"
    UNKNOWN_CHOICE = "UNKNOWN_CHOICE"


def choice_confidence(probs: list[float]) -> float:
    """Fórmula pública da doc oficial (docs.typesafe.ai/confidence) para Choice: (N·pico − 1)/(N − 1)."""
    n = len(probs)
    if n <= 1:
        return 1.0
    return max(0.0, min(1.0, (n * max(probs) - 1) / (n - 1)))


def _softmax(scores: list[float], temperature: float) -> list[float]:
    m = max(scores)
    exps = [math.exp((s - m) / temperature) for s in scores]
    total = sum(exps)
    return [e / total for e in exps]


def _overlap(query_tokens: set[str], text: str) -> float:
    toks = tokenize(text)
    if not toks:
        return 0.0
    hits = sum(1 for t in toks if t in query_tokens)
    return hits / math.sqrt(len(toks))


class FakeJevProvider(DecisionProvider):
    name = "fake"

    def __init__(self, mode: FakeMode | str = FakeMode.HIGH_CONFIDENCE) -> None:
        self.mode = FakeMode(mode)
        self.calls = 0
        self.bytes_in = 0

    def evaluate(self, state: Any, questions: Mapping[str, Any]) -> dict[str, Any]:
        self.calls += 1
        this_call = payload_bytes(request_body(state, questions))
        self.bytes_in += this_call
        if self.mode is FakeMode.TIMEOUT:
            raise ProviderTimeout("timeout simulado")
        if self.mode is FakeMode.ERROR:
            raise ProviderError("HTTP 529 simulado")
        if self.mode is FakeMode.INVALID_RESPONSE:
            return {"model": "fake", "answers": "isto não é um mapa", "usage": {}}
        answers: dict[str, Any] = {}
        entries = state.get("entries", {}) if isinstance(state, Mapping) else {}
        question = state.get("question", "") if isinstance(state, Mapping) else str(state)
        qtoks = set(tokenize(str(question)))
        temp = 0.15 if self.mode is FakeMode.HIGH_CONFIDENCE else 50.0
        for qid, q in questions.items():
            kind = q.get("type")
            if kind == "choice":
                opts = list(q["criteria"])
                if self.mode is FakeMode.UNKNOWN_CHOICE:
                    answers[qid] = {"type": "choice", "choice": "ZZZ-nao-existe",
                                    "probabilities": {o: 1 / len(opts) for o in opts}, "confidence": 0.9}
                    continue
                sc = [_overlap(qtoks, str(entries.get(o, o))) for o in opts]
                probs = _softmax(sc, temp)
                best = max(range(len(opts)), key=lambda i: (probs[i], -i))
                answers[qid] = {"type": "choice", "choice": opts[best],
                                "probabilities": {o: probs[i] for i, o in enumerate(opts)},
                                "confidence": choice_confidence(probs)}
            elif kind == "noul":
                top = max((_overlap(qtoks, str(t)) for t in entries.values()), default=0.0)
                answers[qid] = {"type": "noul", "noul": 0.9 if top > 1.0 else 0.4}
            else:
                answers[qid] = {"type": "score", "score": 0.0, "legend": {}, "probabilities": {}, "confidence": 0.0}
        approx = max(1, this_call // 4)       # PROXY: ~4 bytes/token; o tokenizador do Jev é desconhecido
        return {"model": "fake-1", "answers": answers, "usage": {"input_tokens": approx, "output_tokens": 0}}
