"""Interface de decisão do piloto JEV e o adaptador REAL (inerte).

Isolamento: nada do runtime do produto importa este módulo, e ele não importa nada de `backend/`. Só stdlib.

O formato de fio (`/v1/systemone`) é o da documentação OFICIAL, lido em texto bruto de
https://docs.typesafe.ai/api.md em 2026-10-01: request `{state, model, questions}`; resposta `{model, answers,
usage}`; respostas `noul` (`noul`), `choice` (`choice`, `probabilities`, `confidence`), `score` (`score`, `legend`,
`probabilities`, `confidence`). Baixa confiança é só SINAL — quem roteia é o chamador.

`RealJevProvider` é INERTE: sem `enabled=True` E sem a chave na variável de ambiente `TYPESAFE_API_KEY` ele levanta
antes de abrir qualquer conexão. O benchmark só o constrói com `--provider jev --confirm-external-send`.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from redact import redact

API_URL = "https://api.typesafe.ai/v1/systemone"
#: Nome OFICIAL da variável (docs.typesafe.ai/introduction/quickstart e SDKs). `TYPE_SAFE_AI_KEY` não existe.
KEY_ENV = "TYPESAFE_API_KEY"
#: Versão fixada de propósito: `jev-latest` muda sem aviso (docs.typesafe.ai/models).
PINNED_MODEL = "jev-1.13.0"
#: Preço oficial: US$ 0,042 por 1 M de tokens de ENTRADA; saída grátis (docs.typesafe.ai/models).
PRICE_USD_PER_MTOK_INPUT = 0.042
#: 32k tokens valem para `state` + a maior pergunta (docs.typesafe.ai/models). Em BYTES é só PROXY (≈4 B/token).
MAX_STATE_BYTES_PROXY = 100_000


# --------------------------------------------------------------------------- erros
class ProviderError(Exception):
    kind = "error"


class ProviderTimeout(ProviderError):
    kind = "timeout"


class InvalidResponse(ProviderError):
    kind = "invalid_response"


class UnknownChoice(InvalidResponse):
    kind = "unknown_choice"


class ProviderNotEnabled(ProviderError):
    kind = "not_enabled"


class MissingKey(ProviderNotEnabled):
    kind = "missing_key"


class PrivacyBlock(ProviderError):
    kind = "privacy_block"


# --------------------------------------------------------------------------- tipos
@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class ChoiceResult:
    choice: str
    probabilities: dict[str, float]
    confidence: float
    model: str = ""
    usage: Usage = Usage()
    latency_ms: float = 0.0


@dataclass(frozen=True)
class NoulResult:
    noul: float
    model: str = ""
    usage: Usage = Usage()
    latency_ms: float = 0.0


@dataclass(frozen=True)
class RetrievalResult:
    """Ranking de candidatos de uma pergunta. `ranked` = (id, probabilidade) em ordem decrescente."""
    ranked: list[tuple[str, float]]
    confidence: float
    exists: float | None
    model: str = ""
    usage: Usage = Usage()
    latency_ms: float = 0.0
    requests: int = 1


# --------------------------------------------------------------------------- construtores de pergunta (fio oficial)
def choice_q(instructions: Any, criteria: Mapping[str, Any]) -> dict[str, Any]:
    return {"type": "choice", "instructions": instructions, "criteria": dict(criteria)}


def noul_q(instructions: Any, true: str | None = None, false: str | None = None) -> dict[str, Any]:
    q: dict[str, Any] = {"type": "noul", "instructions": instructions}
    if true or false:
        q["criteria"] = {k: v for k, v in (("true", true), ("false", false)) if v}
    return q


def score_q(instructions: Any, levels: list[str]) -> dict[str, Any]:
    return {"type": "score", "instructions": instructions, "criteria": list(levels)}


def request_body(state: Any, questions: Mapping[str, Any], model: str = PINNED_MODEL) -> dict[str, Any]:
    return {"state": state, "model": model, "questions": dict(questions)}


def payload_bytes(body: Mapping[str, Any]) -> int:
    return len(json.dumps(body, ensure_ascii=False).encode("utf-8"))


# --------------------------------------------------------------------------- parsing (compartilhado real/fake)
def _num01(valor: Any, onde: str) -> float:
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        raise InvalidResponse(f"{onde}: esperado número, veio {type(valor).__name__}")
    v = float(valor)
    if not 0.0 <= v <= 1.0 + 1e-9:
        raise InvalidResponse(f"{onde}: fora de [0,1]: {v}")
    return min(v, 1.0)


def parse_answers(raw: Any) -> tuple[dict[str, dict[str, Any]], str, Usage]:
    if not isinstance(raw, Mapping) or not isinstance(raw.get("answers"), Mapping):
        raise InvalidResponse("resposta sem `answers` em forma de mapa")
    u = raw.get("usage") if isinstance(raw.get("usage"), Mapping) else {}
    usage = Usage(int(u.get("input_tokens", 0) or 0), int(u.get("output_tokens", 0) or 0))
    return dict(raw["answers"]), str(raw.get("model", "")), usage


def parse_choice(ans: Any, allowed: set[str], qid: str = "?") -> tuple[str, dict[str, float], float]:
    """Valida UMA resposta `choice` contra o conjunto FECHADO de opções. Escolha fora dele é erro, nunca ação."""
    if not isinstance(ans, Mapping) or ans.get("type") != "choice":
        raise InvalidResponse(f"{qid}: não é uma resposta `choice`")
    choice = ans.get("choice")
    probs = ans.get("probabilities")
    if not isinstance(choice, str) or not isinstance(probs, Mapping):
        raise InvalidResponse(f"{qid}: `choice`/`probabilities` ausentes")
    if choice not in allowed:
        raise UnknownChoice(f"{qid}: escolha fora do conjunto permitido")
    clean: dict[str, float] = {}
    for k, v in probs.items():
        if k not in allowed:
            raise UnknownChoice(f"{qid}: probabilidade para opção desconhecida")
        clean[k] = _num01(v, f"{qid}.probabilities")
    conf = _num01(ans.get("confidence"), f"{qid}.confidence")
    return choice, clean, conf


def parse_noul(ans: Any, qid: str = "?") -> float:
    if not isinstance(ans, Mapping) or ans.get("type") != "noul":
        raise InvalidResponse(f"{qid}: não é uma resposta `noul`")
    return _num01(ans.get("noul"), f"{qid}.noul")


def retrieval_request(question: str, candidates: Mapping[str, str]) -> tuple[dict[str, Any], dict[str, Any]]:
    """(state, questions) do rerank. Fonte ÚNICA: o `retrieve` e a estimativa de custo usam o mesmo payload."""
    instr = ("Which entry of `entries` best helps answer `question` about a Python codebase? "
             "Pick the entry id whose code most directly answers it.")
    state = {"question": question, "entries": dict(candidates)}
    questions = {
        "best": choice_q(instr, {i: None for i in candidates}),
        "exists": noul_q("Does any entry of `entries` directly answer `question`?"),
    }
    return state, questions


# --------------------------------------------------------------------------- interface
class DecisionProvider(ABC):
    """Contrato mínimo que o piloto precisa de um decisor tipado. `evaluate` fala o formato de fio do Jev; o resto
    (choose/noul/retrieve) é comum e valida a resposta contra o conjunto fechado de opções."""

    name = "abstract"

    @abstractmethod
    def evaluate(self, state: Any, questions: Mapping[str, Any]) -> dict[str, Any]:
        """Uma chamada: `state` + perguntas → corpo de resposta no formato oficial. Pode levantar ProviderError."""

    def choose(self, state: Any, instructions: Any, choices: Mapping[str, Any]) -> ChoiceResult:
        t0 = time.perf_counter()
        raw = self.evaluate(state, {"q": choice_q(instructions, choices)})
        answers, model, usage = parse_answers(raw)
        choice, probs, conf = parse_choice(answers.get("q"), set(choices), "q")
        return ChoiceResult(choice, probs, conf, model, usage, (time.perf_counter() - t0) * 1000)

    def noul(self, state: Any, instructions: Any) -> NoulResult:
        t0 = time.perf_counter()
        raw = self.evaluate(state, {"q": noul_q(instructions)})
        answers, model, usage = parse_answers(raw)
        return NoulResult(parse_noul(answers.get("q"), "q"), model, usage, (time.perf_counter() - t0) * 1000)

    def retrieve(self, question: str, candidates: Mapping[str, str]) -> RetrievalResult:
        """Rerank de candidatos por Choice (padrão `semantic_find` da doc oficial): UMA requisição com um `choice`
        sobre os ids dos candidatos e um `noul` "existe resposta?"."""
        if not candidates:
            raise InvalidResponse("sem candidatos")
        ids = list(candidates)
        state, questions = retrieval_request(question, candidates)
        t0 = time.perf_counter()
        raw = self.evaluate(state, questions)
        answers, model, usage = parse_answers(raw)
        choice, probs, conf = parse_choice(answers.get("best"), set(ids), "best")
        exists = None
        if "exists" in answers:
            exists = parse_noul(answers["exists"], "exists")
        ranked = sorted(probs.items(), key=lambda kv: (-kv[1], ids.index(kv[0])))
        for i in ids:                                  # opções sem probabilidade vão ao fim, em ordem de entrada
            if i not in probs:
                ranked.append((i, 0.0))
        return RetrievalResult(ranked, conf, exists, model, usage, (time.perf_counter() - t0) * 1000, 1)


# --------------------------------------------------------------------------- adaptador real (INERTE)
Transport = Callable[[str, dict[str, str], bytes, float], tuple[int, bytes]]


def _urllib_transport(url: str, headers: dict[str, str], body: bytes, timeout: float) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:      # noqa: S310 - URL fixa, https
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except TimeoutError as exc:
        raise ProviderTimeout(str(exc)) from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            raise ProviderTimeout(str(exc.reason)) from exc
        raise ProviderError(f"rede: {type(exc.reason).__name__}") from exc


@dataclass
class RealJevProvider(DecisionProvider):
    """Cliente HTTP do Jev. Nada é aberto no `__init__`; a rede só é tocada em `evaluate`, e só com `enabled`.

    Camadas de segurança (todas locais, antes de qualquer byte sair):
    1. `enabled=True` explícito (o benchmark exige `--provider jev --confirm-external-send`);
    2. chave em `TYPESAFE_API_KEY` — nunca argumento, nunca log, nunca exceção;
    3. `max_calls`: teto de requisições da rodada;
    4. todo texto do `state` passa por `redact`; achado "duro" (chave privada, Bearer, chave de API) BLOQUEIA;
    5. 429/529 → no máximo `max_retries` com backoff exponencial; 401/422 → falha dura.
    """

    enabled: bool = False
    model: str = PINNED_MODEL
    timeout_s: float = 20.0
    max_retries: int = 2
    max_calls: int = 0
    transport: Transport = field(default=_urllib_transport, repr=False)
    sleep: Callable[[float], None] = field(default=time.sleep, repr=False)
    env: Mapping[str, str] = field(default_factory=lambda: os.environ, repr=False)
    calls: int = 0
    redactions: dict[str, int] = field(default_factory=dict)
    name = "jev"

    def _key(self) -> str:
        key = (self.env.get(KEY_ENV) or "").strip()
        if not key:
            raise MissingKey(f"variável {KEY_ENV} ausente; nenhuma requisição foi feita")
        return key

    def _sanitize(self, state: Any, questions: Mapping[str, Any]) -> tuple[Any, Mapping[str, Any]]:
        def walk(v: Any) -> Any:
            if isinstance(v, str):
                r = redact(v)
                for k, n in {**r.hard, **r.soft}.items():
                    self.redactions[k] = self.redactions.get(k, 0) + n
                if r.blocked:
                    raise PrivacyBlock("achado sensível (" + ",".join(sorted(r.hard)) + "); payload NÃO enviado")
                return r.text
            if isinstance(v, Mapping):
                return {k: walk(x) for k, x in v.items()}
            if isinstance(v, (list, tuple)):
                return [walk(x) for x in v]
            return v
        return walk(state), walk(dict(questions))

    def evaluate(self, state: Any, questions: Mapping[str, Any]) -> dict[str, Any]:
        if not self.enabled:
            raise ProviderNotEnabled("RealJevProvider desabilitado (enabled=False); nenhuma requisição foi feita")
        key = self._key()
        if self.max_calls and self.calls >= self.max_calls:
            raise ProviderNotEnabled(f"teto de {self.max_calls} requisições da rodada atingido")
        state, questions = self._sanitize(state, questions)
        body = json.dumps(request_body(state, questions, self.model), ensure_ascii=False).encode("utf-8")
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        espera = 1.0
        for tentativa in range(self.max_retries + 1):
            self.calls += 1
            status, corpo = self.transport(API_URL, headers, body, self.timeout_s)
            if status == 200:
                try:
                    return json.loads(corpo.decode("utf-8"))
                except ValueError as exc:
                    raise InvalidResponse("corpo não é JSON") from exc
            if status in (429, 529) and tentativa < self.max_retries:
                self.sleep(espera)
                espera *= 2
                continue
            # Nunca inclui o corpo da resposta nem os cabeçalhos: o erro não carrega segredo nem conteúdo.
            if status in (401, 422):
                raise ProviderError(f"HTTP {status} (falha dura)")
            raise ProviderError(f"HTTP {status}")
        raise ProviderError("sem resposta")
