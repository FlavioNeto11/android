"""Adaptador do System One da TypeSafe (Jev) como provedor semântico remoto.

Só serializa, envia, interpreta, mede e traduz erro. Nenhuma regra de produto mora aqui: o que pode sair da máquina
é a política (`domain/policy.py`) e a escolha entre local e semântico é o híbrido. O formato de fio é o do piloto
(`claude/jev-pilot:experiments/jev/provider.py`, lido da documentação oficial): `POST /v1/systemone` com
`{state, model, questions}`; resposta `{model, answers, usage}`; uma pergunta `choice` devolve `probabilities`
sobre o conjunto FECHADO de ids enviado, e é essa distribuição que vira o ranking.

Segurança do adaptador:
- a chave vem SÓ do ambiente (`TYPESAFE_API_KEY`), lida NO MOMENTO da chamada: não fica em atributo, repr, log nem
  exceção, e `available()` nunca toca a rede;
- a mensagem de toda `ProviderError` é um rótulo fixo: nunca o corpo da resposta, o payload ou um cabeçalho, e a
  exceção do `httpx` é descartada (`from None`) para não carregar URL ou cabeçalho no encadeamento;
- sem retentativa: o orçamento conta cada chamada, e o retrieval é fail-open (um erro vira o caminho local).
"""
from __future__ import annotations

import json
import os
import time
from collections.abc import Mapping, Sequence

import httpx

from ..domain.errors import (
    ProviderError, ProviderInvalidResponse, ProviderKeyMissing, ProviderOffline, ProviderOptionLimit,
    ProviderOverloaded, ProviderRateLimited, ProviderTimeout, ProviderUnavailable,
)
from ..domain.model import (
    Chunk, FileChoice, FilesReply, ProviderUsage, RegionChoice, RegionsReply, RepoMap,
)
from ..domain.ports import ProviderLocality

DEFAULT_BASE_URL = "https://api.typesafe.ai"
ENDPOINT = "/v1/systemone"
#: Nome OFICIAL da variável de ambiente da chave.
KEY_ENV = "TYPESAFE_API_KEY"
#: Versão fixada de propósito: `jev-latest` muda sem aviso.
DEFAULT_MODEL = "jev-1.13.0"
#: Preço oficial de ENTRADA (US$ por 1 M de tokens); a saída é grátis. Só serve para reportar custo estimado.
PRICE_USD_PER_MTOK_INPUT = 0.042
#: Teto de opções de uma pergunta `choice` (documentação oficial), CONTANDO a opção `nenhuma`. Acima disso o pedido é
#: recusado aqui, antes de montar o corpo: não vale pagar o round-trip para receber 422 (e nada é truncado em silêncio).
MAX_OPCOES = 255
#: Id opaco da opção "nenhuma das anteriores", sempre presente. Fica fora do vocabulário de caminhos de propósito; escolher
#: `nenhuma` é abster-se, e `_ranking` o descarta (o híbrido cai no caminho local, como em qualquer resposta vazia).
ID_NENHUMA = "opt:nenhuma"
_DESCRICAO_NENHUMA = "None of the above: no other option answers the question."

_INSTR_ARQUIVOS = ("Each entry of `entries` summarizes one file of a codebase (its language, the names it defines and "
                   "its purpose). Which files are most likely to contain the code that answers `question`? "
                   "Rank the file ids.")
_INSTR_REGIOES = ("Each entry of `entries` is a snippet of code from a codebase, identified by file and line range. "
                  "Which snippets most directly answer `question`? Rank the snippet ids.")


def _entrada_do_mapa(e: object) -> str:
    simbolos = ", ".join(e.symbols)
    return f"[{e.language}, {e.size}B]" + (f" {simbolos}" if simbolos else "") + (f" - {e.summary}" if e.summary else "")


class JevSemanticProvider:
    name = "jev"
    locality = ProviderLocality.REMOTE

    def __init__(self, *, model: str = DEFAULT_MODEL, base_url: str = DEFAULT_BASE_URL,
                 env: Mapping[str, str] | None = None, transport: httpx.BaseTransport | None = None,
                 key_env: str = KEY_ENV) -> None:
        self.model = model or DEFAULT_MODEL
        self.base_url = base_url.rstrip("/")
        self._env = env
        self._transport = transport
        self._key_env = key_env

    def __repr__(self) -> str:  # sem env nem transporte: nada que possa carregar a chave
        return f"JevSemanticProvider(model={self.model!r}, base_url={self.base_url!r})"

    # ------------------------------------------------------------------ porta
    def _chave(self) -> str:
        # `os.environ` é lido a cada chamada (e não congelado no __init__): a chave pode ser trocada sem reiniciar.
        env = self._env if self._env is not None else os.environ
        return (env.get(self._key_env) or "").strip()

    def available(self) -> tuple[bool, str | None]:
        return (True, None) if self._chave() else (False, "key_missing")

    def select_files(self, query: str, repo_map: RepoMap, *, max_files: int, timeout_s: float) -> FilesReply:
        entradas = {e.path: _entrada_do_mapa(e) for e in repo_map.entries}
        ranking, usage = self._ranquear(query, entradas, _INSTR_ARQUIVOS, max_files, timeout_s)
        return FilesReply(tuple(FileChoice(i, p) for i, p in ranking), usage)

    def select_regions(self, query: str, chunks: Sequence[Chunk], *, max_regions: int,
                       timeout_s: float) -> RegionsReply:
        por_id = {f"{c.path}:{c.start_line}-{c.end_line}": c for c in chunks}
        entradas = {i: c.text for i, c in por_id.items()}
        ranking, usage = self._ranquear(query, entradas, _INSTR_REGIOES, max_regions, timeout_s)
        return RegionsReply(tuple(RegionChoice(por_id[i].path, por_id[i].start_line, por_id[i].end_line, p)
                                  for i, p in ranking), usage)

    # ------------------------------------------------------------------ fio
    def _ranquear(self, query: str, entradas: dict[str, str], instrucao: str, limite: int,
                  timeout_s: float) -> tuple[list[tuple[str, float]], ProviderUsage]:
        if not entradas:
            raise ProviderInvalidResponse("sem entradas")
        if ID_NENHUMA in entradas:
            raise ProviderInvalidResponse("id reservado")
        if len(entradas) + 1 > MAX_OPCOES:  # +1: a `nenhuma`. Recusa local, antes de qualquer corpo ou rede
            raise ProviderOptionLimit("opcoes acima do teto")
        opcoes = {**entradas, ID_NENHUMA: _DESCRICAO_NENHUMA}
        corpo = {
            "state": {"question": query, "entries": opcoes},
            "model": self.model,
            "questions": {"best": {"type": "choice", "instructions": instrucao,
                                   "criteria": {i: None for i in opcoes}}},
        }
        bruto = json.dumps(corpo, ensure_ascii=False).encode("utf-8")
        resposta, latencia = self._postar(bruto, timeout_s)
        answers, usage = self._interpretar(resposta, len(bruto), latencia)
        ranking = self._ranking(answers.get("best"), set(opcoes), limite)
        return ranking, usage

    def _postar(self, corpo: bytes, timeout_s: float) -> tuple[object, float]:
        chave = self._chave()
        if not chave:
            raise ProviderKeyMissing("chave ausente")
        cabecalhos = {"Authorization": f"Bearer {chave}", "Content-Type": "application/json"}
        t0 = time.perf_counter()
        r: httpx.Response | None = None
        # O erro do httpx é só classificado aqui e a exceção do domínio sai FORA do `except`: assim ela não carrega
        # `__context__` com a exceção original (que pode trazer a requisição, e nela o cabeçalho com a chave).
        erro: ProviderError | None = None
        try:
            with httpx.Client(transport=self._transport, timeout=timeout_s) as cliente:
                r = cliente.post(self.base_url + ENDPOINT, content=corpo, headers=cabecalhos)
        except httpx.TimeoutException:
            erro = ProviderTimeout("timeout")
        except httpx.TransportError:
            erro = ProviderOffline("sem conexao")
        except httpx.HTTPError:
            erro = ProviderError("falha de transporte")
        if erro is not None or r is None:
            raise erro or ProviderError("sem resposta")
        latencia = (time.perf_counter() - t0) * 1000
        status = r.status_code
        if status in (401, 403):
            raise ProviderUnavailable(f"HTTP {status}")
        if status == 429:
            raise ProviderRateLimited("HTTP 429")
        if status in (503, 529):
            raise ProviderOverloaded(f"HTTP {status}")
        if status != 200:
            raise ProviderError(f"HTTP {status}")
        corpo_json: object = None
        valido = True
        try:
            corpo_json = r.json()
        except ValueError:
            valido = False
        if not valido:
            raise ProviderInvalidResponse("corpo nao e JSON")
        return corpo_json, latencia

    @staticmethod
    def _interpretar(raw: object, bytes_enviados: int, latencia_ms: float) -> tuple[Mapping[str, object], ProviderUsage]:
        if not isinstance(raw, Mapping) or not isinstance(raw.get("answers"), Mapping):
            raise ProviderInvalidResponse("resposta sem `answers`")
        u = raw.get("usage") if isinstance(raw.get("usage"), Mapping) else {}
        entrada = _inteiro(u.get("input_tokens"))
        if entrada is None:  # sem `usage`, a estimativa por bytes mantém o orçamento honesto
            entrada = bytes_enviados // 4
        saida = _inteiro(u.get("output_tokens")) or 0
        usage = ProviderUsage(input_tokens=entrada, output_tokens=saida,
                              cost_usd=entrada * PRICE_USD_PER_MTOK_INPUT / 1_000_000, latency_ms=latencia_ms)
        return raw["answers"], usage

    @staticmethod
    def _ranking(resposta: object, permitidos: set[str], limite: int) -> list[tuple[str, float]]:
        """Distribuição de probabilidade → ranking. Opção fora do conjunto enviado é resposta inválida, nunca ação."""
        if not isinstance(resposta, Mapping) or resposta.get("type") != "choice":
            raise ProviderInvalidResponse("resposta nao e `choice`")
        probs = resposta.get("probabilities")
        if not isinstance(probs, Mapping):
            raise ProviderInvalidResponse("`probabilities` ausente")
        pares: list[tuple[str, float]] = []
        for k, v in probs.items():
            if k not in permitidos:
                raise ProviderInvalidResponse("opcao desconhecida")
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0.0 <= float(v) <= 1.0 + 1e-9:
                raise ProviderInvalidResponse("probabilidade invalida")
            if k != ID_NENHUMA:  # validada acima como opção enviada, mas nunca vira arquivo nem região
                pares.append((k, min(float(v), 1.0)))
        pares.sort(key=lambda kv: -kv[1])  # sort estável: empate mantém a ordem em que o provedor listou
        return [(k, p) for k, p in pares if p > 0][:limite]


def _inteiro(v: object) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None
