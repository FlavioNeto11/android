"""Retriever semântico em duas etapas: A escolhe ARQUIVOS no mapa do repositório, B escolhe REGIÕES nos chunks deles.

É a única peça que fala com um `SemanticProvider`, e por isso a ordem das conferências é o contrato de segurança:

1. provedor ausente ou indisponível → cai antes de montar qualquer coisa;
2. política de repositório → negada, o mapa NEM É montado (`map_source.repo_map()` não é chamado);
3. política da pergunta, do mapa filtrado e do payload serializado → segredo duro derruba o pedido;
4. etapa B: segredo MOLE tira só o chunk, segredo DURO bloqueia a etapa B inteira e preserva o resultado da A.

`retrieve` NUNCA levanta (fail-open): qualquer falha vira `ContextSelection.empty(..., fallback_used=True)` com a razão
fechada de `FallbackReason`, e o chamador segue com o caminho local. A metadata devolvida nunca carrega código, a
pergunta crua nem caminho bloqueado. Um resultado só da etapa A (B bloqueada ou falha) NÃO é fallback: o arquivo
escolhido vale, e o que faltou fica em `warnings` e em `metadata["stage_b_reason"]`.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..domain.errors import ProviderError
from ..domain.model import (
    Chunk, ContextSelection, FallbackReason, FileHit, MapEntry, PayloadLimits, ProviderUsage, Region, RepoMap,
    RetrievalRequest,
)
from ..domain.policy import ExternalContextPolicy
from ..domain.ports import ChunkSource, MapSource, ProviderLocality, ResponseCache, SemanticProvider

if TYPE_CHECKING:  # só tipos: o retriever recebe as instâncias prontas, não constrói orçamento nem cache
    from .budget import BudgetLedger, RequestBudget


class _Parar(Exception):
    """Interrompe o fluxo com uma razão de fallback. Interna: nunca sai de `retrieve`."""

    def __init__(self, reason: FallbackReason, warnings: tuple[str, ...] = ()) -> None:
        super().__init__(reason.value)
        self.reason = reason
        self.warnings = warnings


@dataclass
class _Corrida:
    """O que se acumula durante UM `retrieve` e entra no resultado, inclusive nos fallbacks (custo já gasto conta)."""

    cost_usd: float = 0.0
    input_tokens: int = 0
    rb: "RequestBudget | None" = None
    meta: dict[str, object] = field(default_factory=dict)

    def gastar(self, usage: ProviderUsage) -> None:
        self.cost_usd += usage.cost_usd
        self.input_tokens += usage.input_tokens
        if self.rb is not None:
            self.rb.record(usage)


def _normalizar_prefixo(p: str) -> str:
    p = p.replace(chr(92), "/").strip()
    while p.startswith("./"):
        p = p[2:]
    return p


class SemanticRetriever:
    name = "semantic"

    def __init__(self, *, provider: SemanticProvider | None, policy: ExternalContextPolicy, map_source: MapSource,
                 chunk_source: ChunkSource, ledger: "BudgetLedger", cache: ResponseCache,
                 limits: PayloadLimits) -> None:
        self.provider = provider
        self.policy = policy
        self.map_source = map_source
        self.chunk_source = chunk_source
        self.ledger = ledger
        self.cache = cache
        self.limits = limits

    # ------------------------------------------------------------------ entrada
    def retrieve(self, request: RetrievalRequest) -> ContextSelection:
        t0 = time.perf_counter()
        run = _Corrida()
        try:
            return self._executar(request, t0, run)
        except _Parar as p:
            return self._falha(t0, run, p.reason, p.warnings)
        except ProviderError as exc:
            return self._falha(t0, run, exc.reason, (f"provider_error:{exc.reason.value}",))
        except Exception as exc:  # fail-open de verdade: nem bug de fonte, cache ou política derruba o pedido
            return self._falha(t0, run, FallbackReason.PROVIDER_ERROR, (f"unexpected_error:{type(exc).__name__}",))

    def _falha(self, t0: float, run: _Corrida, reason: FallbackReason, warnings: tuple[str, ...]) -> ContextSelection:
        if run.rb is not None:
            run.meta["calls"] = run.rb.calls
        return ContextSelection.empty(
            self.name, fallback_used=True, fallback_reason=reason, warnings=warnings,
            latency_ms=(time.perf_counter() - t0) * 1000, cost_usd=run.cost_usd, input_tokens=run.input_tokens,
            metadata=run.meta)

    # ------------------------------------------------------------------ fluxo
    def _executar(self, request: RetrievalRequest, t0: float, run: _Corrida) -> ContextSelection:
        prov = self.provider
        if prov is None:
            raise _Parar(FallbackReason.NO_PROVIDER)
        ok, motivo = prov.available()
        if not ok:
            raise _Parar(FallbackReason.KEY_MISSING if motivo == "key_missing" else FallbackReason.PROVIDER_UNAVAILABLE)
        run.meta.update(provider=prov.name, model=prov.model)

        # Provedor remoto com política que acha que nada sai da máquina é configuração errada: falha FECHADA.
        if prov.locality is ProviderLocality.REMOTE and self.policy.locality is not ProviderLocality.REMOTE:
            raise _Parar(FallbackReason.PRIVACY_BLOCK, ("privacy_block:policy_locality_mismatch",))
        d = self.policy.can_send_repository()
        if not d.allowed:  # antes de qualquer mapa: o que não pode sair nem chega a ser lido
            raise _Parar(d.code or FallbackReason.PRIVACY_BLOCK, (f"privacy_block:{d.reason}",))
        d = self.policy.can_send_query(request.query)
        if not d.allowed:
            raise _Parar(d.code or FallbackReason.SECRET_BLOCK, (f"secret_block:{d.reason}",))

        run.rb = self.ledger.begin_request()
        candidatos = self._etapa_a(request, prov, run)
        regioes, avisos = self._etapa_b(request, prov, candidatos, run)

        run.meta["calls"] = run.rb.calls
        fonte = f"semantic:{prov.name}"
        arquivos = tuple(FileHit(path=p, score=s, source=fonte) for p, s in candidatos[:request.top_k])
        return ContextSelection(
            selected_files=arquivos,
            selected_regions=tuple(Region(p, s, e) for p, s, e, _ in regioes[:request.top_k]),
            source=self.name, confidence=None, latency_ms=(time.perf_counter() - t0) * 1000,
            cost_usd=run.cost_usd, input_tokens=run.input_tokens, fallback_used=False, warnings=avisos,
            metadata=run.meta)

    # ------------------------------------------------------------------ etapa A
    def _etapa_a(self, request: RetrievalRequest, prov: SemanticProvider, run: _Corrida) -> list[tuple[str, float]]:
        assert run.rb is not None
        mapa = self._mapa_filtrado(request, run)
        run.meta["files_considered"] = len(mapa.entries)
        run.meta["stage_a_cache"] = "miss"
        if not mapa.entries:
            raise _Parar(FallbackReason.EMPTY_SEMANTIC, ("stage_a_failed:no_map_entries",))
        texto = mapa.render()
        d = self.policy.can_send_payload(texto)
        if d.allowed is False:
            raise _Parar(d.code or FallbackReason.SECRET_BLOCK, (f"secret_block:{d.reason}",))
        permitidos = {e.path for e in mapa.entries}

        chave = self._chave(request, prov, "A", self._limites_na_chave())
        em_cache = self._ler_arquivos(self._cache_get(chave), permitidos)
        if em_cache:
            run.meta["stage_a_cache"] = "hit"
            return em_cache

        motivo = run.rb.check_call(est_input_tokens=len(texto) // 4)
        if motivo is not None:
            raise _Parar(motivo, ("stage_a_failed:" + motivo.value,))
        try:
            resposta = prov.select_files(request.query, mapa, max_files=self.limits.max_candidate_files,
                                         timeout_s=run.rb.timeout_s)
        except ProviderError as exc:
            raise _Parar(exc.reason, ("stage_a_failed:" + exc.reason.value,)) from None
        run.gastar(resposta.usage)

        validos: list[tuple[str, float]] = []
        vistos: set[str] = set()
        for c in resposta.choices:  # caminho que não está no mapa ENVIADO é alucinação: descarta, nunca confia
            if c.path in permitidos and c.path not in vistos:
                vistos.add(c.path)
                validos.append((c.path, float(c.score)))
        validos = validos[:self.limits.max_candidate_files]
        if not validos:
            razao = FallbackReason.INVALID_RESPONSE if resposta.choices else FallbackReason.EMPTY_SEMANTIC
            raise _Parar(razao, ("stage_a_failed:" + razao.value,))
        self._cache_put(chave, {"files": [[p, s] for p, s in validos]})
        return validos

    def _mapa_filtrado(self, request: RetrievalRequest, run: _Corrida | None = None) -> RepoMap:
        mapa = self.map_source.repo_map()
        prefixos = tuple(p for p in (_normalizar_prefixo(x) for x in request.scope) if p)
        mantidas: list[MapEntry] = []
        usados = 0
        omitidas = 0
        for e in mapa.entries:
            if len(mantidas) >= self.limits.max_map_files:
                break
            if prefixos and not e.path.startswith(prefixos):
                continue
            if not self.policy.can_send_file(e.path).allowed:
                continue
            # O corte em bytes é por ENTRADA inteira: o que conta em `files_considered` é exatamente o que vai.
            renderizada = RepoMap(mapa.revision, (e,)).render()
            c = self.policy.can_send_map_entry(renderizada)
            if not c.allowed:
                if c.hard:  # segredo duro no mapa derruba o pedido, como no payload serializado
                    raise _Parar(c.code or FallbackReason.SECRET_BLOCK, (f"secret_block:{c.reason}",))
                omitidas += 1  # segredo mole: a entrada some do payload (sem trocar por marcador, que viraria termo de ranking)
                continue
            tam = len(renderizada.encode("utf-8")) + 1
            if usados + tam > self.limits.max_bytes:
                break
            usados += tam
            mantidas.append(e)
        if run is not None and omitidas:
            run.meta["map_entries_withheld_secret"] = omitidas
        return RepoMap(mapa.revision, tuple(mantidas))

    # ------------------------------------------------------------------ etapa B
    def _etapa_b(self, request: RetrievalRequest, prov: SemanticProvider, candidatos: list[tuple[str, float]],
                 run: _Corrida) -> tuple[list[tuple[str, int, int, float]], tuple[str, ...]]:
        assert run.rb is not None
        run.meta["stage_b_cache"] = "skipped"
        run.meta["chunks_sent"] = 0
        paths = [p for p, _ in candidatos]
        try:
            brutos = list(self.chunk_source.chunks_for(paths, max_chunks=self.limits.max_chunks,
                                                       max_bytes=self.limits.max_bytes))
        except Exception:
            return self._b_falhou(run, "chunk_source_error")

        pedidos = set(paths)
        chunks: list[Chunk] = []
        usados = 0
        for c in brutos:  # teto reaplicado: a fonte é quem corta, mas o limite de payload é NOSSO
            if c.path not in pedidos:
                continue
            tam = len(c.text.encode("utf-8"))
            if len(chunks) >= self.limits.max_chunks or usados + tam > self.limits.max_bytes:
                continue
            usados += tam
            chunks.append(c)

        enviar: list[Chunk] = []
        mole = 0
        for c in chunks:
            d = self.policy.can_send_chunk(c.path, c.text)
            if d.allowed:
                enviar.append(c)
            elif d.hard:
                return self._b_bloqueada(run, d.code)
            else:
                mole += 1
        run.meta["chunks_dropped_soft"] = mole
        if not enviar:
            run.meta["stage_b_reason"] = "no_chunks"
            return [], ()

        payload = "\n".join(f"{c.path}:{c.start_line}-{c.end_line}\n{c.text}" for c in enviar)
        d = self.policy.can_send_payload(payload)
        if not d.allowed:
            return self._b_bloqueada(run, d.code)

        chunks_por_chave = {(c.path, c.start_line, c.end_line) for c in enviar}
        chave = self._chave(request, prov, "B", ",".join(paths) + "|" + self._limites_na_chave())
        em_cache = self._ler_regioes(self._cache_get(chave), chunks_por_chave)
        if em_cache:
            run.meta["stage_b_cache"] = "hit"
            return em_cache, ()

        motivo = run.rb.check_call(est_input_tokens=len(payload) // 4)
        if motivo is not None:
            return self._b_falhou(run, motivo.value)   # sem chamada: o rótulo segue "skipped", não "miss"
        run.meta["stage_b_cache"] = "miss"
        try:
            resposta = prov.select_regions(request.query, enviar, max_regions=request.top_k,
                                           timeout_s=run.rb.timeout_s)
        except ProviderError as exc:
            return self._b_falhou(run, exc.reason.value)
        except Exception:
            return self._b_falhou(run, FallbackReason.PROVIDER_ERROR.value)
        run.meta["chunks_sent"] = len(enviar)
        run.gastar(resposta.usage)

        validas: list[tuple[str, int, int, float]] = []
        vistas: set[tuple[str, int, int]] = set()
        for r in resposta.choices:  # região que não é um dos chunks enviados é inventada
            k = (r.path, r.start_line, r.end_line)
            if k in chunks_por_chave and k not in vistas:
                vistas.add(k)
                validas.append((r.path, r.start_line, r.end_line, float(r.score)))
        if not validas:
            return self._b_falhou(run, (FallbackReason.INVALID_RESPONSE if resposta.choices
                                        else FallbackReason.EMPTY_SEMANTIC).value)
        self._cache_put(chave, {"regions": [[p, s, e, sc] for p, s, e, sc in validas]})
        return validas, ()

    @staticmethod
    def _b_falhou(run: _Corrida, razao: str) -> tuple[list[tuple[str, int, int, float]], tuple[str, ...]]:
        run.meta["stage_b_reason"] = razao
        return [], (f"stage_b_failed:{razao}",)

    @staticmethod
    def _b_bloqueada(run: _Corrida, code: FallbackReason | None
                     ) -> tuple[list[tuple[str, int, int, float]], tuple[str, ...]]:
        # Bloqueio por segredo/caminho NÃO é falha do provedor nem fallback: a A vale, só não há regiões.
        codigo = code or FallbackReason.SECRET_BLOCK
        run.meta["stage_b_reason"] = codigo.value
        run.meta["stage_b_cache"] = "skipped"
        return [], ("stage_b_blocked:privacy" if codigo is FallbackReason.PRIVACY_BLOCK else "stage_b_blocked:secret",)

    # ------------------------------------------------------------------ cache
    def _limites_na_chave(self) -> str:
        l = self.limits
        return f"{l.max_map_files}/{l.max_candidate_files}/{l.max_chunks}/{l.max_bytes}"

    def _chave(self, request: RetrievalRequest, prov: SemanticProvider, etapa: str, extra: str) -> str:
        return self.cache.key(revision=request.revision, query=request.query, scope=tuple(request.scope),
                              provider=prov.name, model=prov.model, stage=etapa,
                              extra=f"{extra}|{self.policy.fingerprint()}")

    def _cache_get(self, chave: str) -> dict[str, object] | None:
        try:
            return self.cache.get(chave)
        except Exception:
            return None

    def _cache_put(self, chave: str, valor: dict[str, object]) -> None:
        try:
            self.cache.put(chave, valor)
        except Exception:
            pass  # cache é otimização: nunca derruba o retrieval

    @staticmethod
    def _ler_arquivos(valor: dict[str, object] | None, permitidos: set[str]) -> list[tuple[str, float]] | None:
        """Cache corrompido, de outra forma ou com caminho fora do mapa atual é miss silencioso."""
        try:
            itens = [(str(p), float(s)) for p, s in (valor or {})["files"]]
        except (KeyError, TypeError, ValueError):
            return None
        itens = [(p, s) for p, s in itens if p in permitidos]
        return itens or None

    @staticmethod
    def _ler_regioes(valor: dict[str, object] | None, validas: set[tuple[str, int, int]]
                     ) -> list[tuple[str, int, int, float]] | None:
        try:
            itens = [(str(p), int(a), int(b), float(s)) for p, a, b, s in (valor or {})["regions"]]
        except (KeyError, TypeError, ValueError):
            return None
        itens = [i for i in itens if (i[0], i[1], i[2]) in validas]
        return itens or None
