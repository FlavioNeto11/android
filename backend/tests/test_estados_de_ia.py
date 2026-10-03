"""Item 7.3 do plano-100 (fase 7 — hub de IA). Achados #93, #68, #101.

O que cada bloco prova:

- **Recusa do provedor não é "IA indisponível".** Repetir a etapa tende a dar a mesma recusa: a etapa não
  gasta tentativa, o objetivo vai para `waiting_user` com `blocked_kind='ai'` e uma dica que manda reescrever a
  intenção (não "confira a chave").
- **Recusa no planejamento vira `needs_input`**, não `failed` — o comando pode ser reescrito, a execução não é
  um defeito nosso.
- **Recusa na geração social tem código e dica próprios** (`ai_refusal`), separados de `ai_budget`/`ai_error`.
- **A chamada com erro grava o modelo pedido e o tipo do erro** em vez do pseudo-modelo `'(erro)'`, e
  `/api/usage` para de contar isso como "modelo sem preço" (achado #101).
- **A espera pela vaga de IA e pela resposta do modelo é ESTRUTURADA** (`wait_reason`), não só um texto livre.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from app.db import Database
from app.events import EventBus
from app.models import RunStatus
from app.planning.provider import AIError, SocialRequest, Usage
from app.planning.simulated_provider import SimulatedProvider
from app.security.secret_store import MemoryKeyProvider, SecretStore
from app.social.repository import SocialRepository
from app.social.service import SocialError, SocialService
from app.taskqueue import executor as executor_mod

from .conftest import Harness, make_config


class RecusaNaDecisao:
    """`decide()` recusa por política (achado #93) enquanto `fail` estiver ligado; o resto passa pelo simulado."""

    def __init__(self, inner: SimulatedProvider, *, model: str = "modelo-recusado"):
        self.inner = inner
        self.fail = True
        self.calls = 0
        self.model_recusado = model
        self.name, self.model, self.simulated = inner.name, inner.model, inner.simulated

    def status(self) -> Any:
        return self.inner.status()

    async def decide(self, req: object) -> object:
        self.calls += 1
        if self.fail:
            raise AIError("O provedor recusou a requisição por política de segurança.", kind="refusal",
                          model=self.model_recusado)
        return await self.inner.decide(req)

    async def verify(self, req: object) -> object:
        return await self.inner.verify(req)

    async def plan(self, req: object) -> object:
        return await self.inner.plan(req)

    async def generate_social_response(self, req: object) -> object:
        raise NotImplementedError


class RecusaNoPlanejamento:
    """`plan()` recusa por política — o comando inteiro nunca chega a virar etapas."""

    def __init__(self, inner: SimulatedProvider):
        self.inner = inner
        self.name, self.model, self.simulated = inner.name, inner.model, inner.simulated

    def status(self) -> Any:
        return self.inner.status()

    async def plan(self, req: object) -> object:
        raise AIError("O provedor recusou a requisição por política de segurança.", kind="refusal",
                      model="modelo-recusado")

    async def decide(self, req: object) -> object:
        return await self.inner.decide(req)

    async def verify(self, req: object) -> object:
        return await self.inner.verify(req)

    async def generate_social_response(self, req: object) -> object:
        raise NotImplementedError


async def test_recusa_na_decisao_nao_consome_tentativa_e_bloqueia_como_ia(tmp_path: Path,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    # O gancho é a decisão do ator na 1ª etapa (abrir o app), que o LT-6 (29.45) passou a fazer sem IA.
    monkeypatch.setattr(executor_mod, "OPEN_APP_SEM_IA", False)
    h = Harness(tmp_path, 3)
    provider = RecusaNaDecisao(SimulatedProvider())
    h.ai = provider  # type: ignore[assignment]
    await h.boot()
    assert h.state is not None
    try:
        run = h.run(["android-01"])
        detail = await h.wait_run(run.id)

        assert provider.calls >= 1
        obj = detail.objectives[0]
        assert obj.status == "waiting_user"
        # Achado #93, ponto 4: blocked_kind='ai' distingue "a IA travou" de política/limite/aprovação.
        assert obj.blocked_kind == "ai"
        assert "recus" in (obj.blocked_reason or "").lower()
        assert "reescreva" in (obj.needs or "").lower()

        step = next(s for s in detail.steps if s.id.startswith(obj.id))
        # Nenhuma tentativa gasta: recusa não é "tentar de novo com o mesmo pedido e torcer".
        assert step.attempts == 0

        # Achado #101: a linha de erro grava o modelo REALMENTE pedido e o tipo do erro — nunca '(erro)'.
        erros = h.state.repo.db.query("SELECT * FROM ai_calls WHERE run_id=? AND ok=0", (run.id,))
        assert erros and all(r["model"] == "modelo-recusado" for r in erros)
        assert all(r["error_kind"] == "refusal" for r in erros)
        assert all(r["error_message"] for r in erros)
        assert not any(r["model"] == "(erro)" for r in erros)
    finally:
        await h.state.stop()  # type: ignore[union-attr]


async def test_recusa_no_planejamento_vira_needs_input_nao_failed(tmp_path: Path) -> None:
    h = Harness(tmp_path, 3)
    h.ai = RecusaNoPlanejamento(SimulatedProvider())  # type: ignore[assignment]
    await h.boot()
    assert h.state is not None
    try:
        run = h.run(["android-01"])
        await h.wait(lambda: h.state.repo.run_row(run.id)["status"] in ("needs_input", "failed"),  # type: ignore[union-attr]
                     what="planejamento recusado")
        row = h.state.repo.run_row(run.id)  # type: ignore[union-attr]
        assert row["status"] == RunStatus.needs_input.value
        assert "recus" in (row["status_detail"] or "").lower()
    finally:
        await h.state.stop()


async def test_api_usage_exclui_grupo_100pc_erro_de_unpriced_e_conta_por_tipo(tmp_path: Path) -> None:
    h = Harness(tmp_path, 3)
    provider = RecusaNaDecisao(SimulatedProvider())
    h.ai = provider  # type: ignore[assignment]
    await h.boot()
    assert h.state is not None
    try:
        from app.main import create_app

        run = h.run(["android-01"])
        await h.wait_run(run.id)
        app = create_app(h.cfg, state=h.state)
        app.state.poc = h.state
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            u = (await c.get("/api/usage", params={"run_id": run.id})).json()
        # o grupo decide/'modelo-recusado' é 100% erro (nenhuma chamada ok): não é "modelo sem preço" — é
        # chamada que não custou nada, e dizer o contrário é o "Total parcial" falso do achado #101.
        assert "modelo-recusado" not in u["unpriced_models"]
        assert u["errors_by_kind"].get("refusal", 0) >= 1
    finally:
        await h.state.stop()


async def test_api_usage_aponta_cache_inativo_em_decisao(tmp_path: Path) -> None:
    """Achado de 24/09: 46 decisões no Sonnet 5 sem uma leitura nem gravação de cache e o relatório só somava.
    Decisão sempre leva as ferramentas (prefixo acima do mínimo de qualquer modelo): zero cache é defeito.
    Verificação (system curto) e provedor simulado ficam de fora; menos de 10 chamadas ainda é ruído."""
    h = Harness(tmp_path, 1)
    await h.boot()
    assert h.state is not None
    try:
        from app.main import create_app

        db = h.state.db
        cols = ("ts, run_id, objective_id, step_id, role, model, tier, input_tokens, cache_read, cache_write,"
                " output_tokens, with_image, ms, ok")

        def linha(role: str, model: str, cache: int, n: int, run: str) -> None:
            for i in range(n):
                db.execute(f"INSERT INTO ai_calls({cols}) VALUES (?,?,?,?,?,?,0,11000,?,?,300,0,3000,1)",
                           (f"2026-09-24T00:{i:02d}:00.000Z", run, None, None, role, model, cache, 0 if cache else 0))

        linha("decide", "claude-sonnet-5", 0, 12, "r-frio")          # defeito: 12 decisões sem cache
        linha("verify", "claude-haiku-4-5", 0, 12, "r-frio")         # legítimo: system curto não cacheia
        linha("decide", "claude-opus-5", 6000, 12, "r-quente")       # saudável: cache lido
        linha("decide", "claude-sonnet-5", 0, 3, "r-pouco")          # ruído: menos de 10 chamadas
        app = create_app(h.cfg, state=h.state)
        app.state.poc = h.state
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            frio = (await c.get("/api/usage", params={"run_id": "r-frio"})).json()
            quente = (await c.get("/api/usage", params={"run_id": "r-quente"})).json()
            pouco = (await c.get("/api/usage", params={"run_id": "r-pouco"})).json()
        assert frio["cache_inativo"] == [{"model": "claude-sonnet-5", "calls": 12}]
        assert quente["cache_inativo"] == [] and pouco["cache_inativo"] == []
    finally:
        await h.state.stop()


async def test_geracao_social_recusada_tem_codigo_e_dica_proprios(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    cfg.ensure_dirs()
    db = Database(cfg.db_dsn)
    db.migrate()
    repo = SocialRepository(db)
    svc = SocialService(repo, SecretStore(db, MemoryKeyProvider()), EventBus(db),
                        known_instances=lambda: ["android-01"], provider=SimulatedProvider())

    async def runner_recusa(f: Any) -> Any:
        raise AIError("O provedor recusou a requisição por política de segurança.", kind="refusal",
                      model="modelo-recusado")

    req = SocialRequest(profile_id="", username="alguem", kind="dm_initiate", context_text="contexto")
    with pytest.raises(SocialError) as excinfo:
        await svc._generate(req, runner=runner_recusa)  # noqa: SLF001 - é o próprio caminho que a execução usa
    # Achado #93, ponto 3: código PRÓPRIO — nem 'ai_budget' (não é orçamento) nem o 'ai_error' genérico que
    # mandava "confira a chave e a persona" quando não havia nada de errado com nenhum dos dois.
    assert excinfo.value.code == "ai_refusal"

    async def runner_orcamento(f: Any) -> Any:
        raise AIError("Orçamento esgotado.", kind="budget")

    with pytest.raises(SocialError) as excinfo2:
        await svc._generate(req, runner=runner_orcamento)  # noqa: SLF001
    assert excinfo2.value.code == "ai_budget"


async def test_ai_marca_espera_por_vaga_e_por_resposta_do_modelo(tmp_path: Path) -> None:
    """Achado #68: `_ai` anota `wait_reason` antes de entrar no limite (vaga) e enquanto a chamada está em voo
    (resposta) — e limpa ao sair, sucesso ou erro."""
    h = Harness(tmp_path, 1)
    await h.boot()
    assert h.state is not None
    try:
        vistos: list[tuple[str | None, str | None]] = []
        limpezas: list[str | None] = []
        original = h.state.repo.note_waiting
        original_clear = h.state.repo.clear_wait_reason

        def espiao(objective_id: str, detail: str, *, wait_reason: str | None = None) -> None:
            vistos.append((detail, wait_reason))
            original(objective_id, detail, wait_reason=wait_reason)

        def espiao_clear(objective_id: str, *, restore_detail: str | None = None) -> None:
            original_clear(objective_id, restore_detail=restore_detail)
            # Logo após a limpeza, o texto livre NÃO pode ter ficado preso em "aguardando…" (era o bug: só o
            # campo estruturado voltava a NULL, o texto continuava escrito até a PRÓXIMA chamada de IA).
            row = h.state.repo.objective_row(objective_id)  # type: ignore[union-attr]
            limpezas.append(row["status_detail"])

        h.state.repo.note_waiting = espiao  # type: ignore[assignment]
        h.state.repo.clear_wait_reason = espiao_clear  # type: ignore[assignment]
        run = h.run(["android-01"])
        detail = await h.wait_run(run.id)
        assert any(w == "ai_capacity" for _, w in vistos)
        assert any(w == "model_response" for _, w in vistos)
        # terminado o run, nenhum objetivo deveria seguir "esperando o modelo"
        assert all(o.wait_reason is None for o in detail.objectives)
        assert limpezas, "clear_wait_reason nunca foi chamado"
        assert not any((d or "").startswith(("aguardando vaga de IA", "aguardando resposta do modelo"))
                       for d in limpezas), limpezas
    finally:
        await h.state.stop()


class TokensPesadosNoDecidirEVerificar:
    """Achado #99, ponto 2: o SIMULADO de sempre reporta uso zero (`objectives.ai_calls` não prova nada em
    tokens) — este invólucro devolve `Usage` com tokens de verdade em `decide`/`verify`, para exercitar o teto
    de TOKENS por execução (`ai_max_tokens_per_run`) de ponta a ponta, sem provedor real."""

    def __init__(self, inner: SimulatedProvider, *, tokens_por_chamada: int = 1000):
        self.inner = inner
        self.tokens = tokens_por_chamada
        self.name, self.model, self.simulated = inner.name, inner.model, inner.simulated

    def status(self) -> Any:
        return self.inner.status()

    async def plan(self, req: object) -> object:
        plano, _ = await self.inner.plan(req)
        return plano, Usage()

    async def decide(self, req: object) -> object:
        decisao, _ = await self.inner.decide(req)
        return decisao, Usage(calls=1, role="decide", model="modelo-caro", input_tokens=self.tokens, output_tokens=10)

    async def verify(self, req: object) -> object:
        veredito, _ = await self.inner.verify(req)
        return veredito, Usage(calls=1, role="verify", model="modelo-caro", input_tokens=self.tokens, output_tokens=10)

    async def generate_social_response(self, req: object) -> object:
        raise NotImplementedError


async def test_orcamento_de_tokens_da_execucao_bloqueia_chamada_seguinte_e_aparece_no_painel(tmp_path: Path) -> None:
    """Achado #99, ponto 2: teste de orçamento por TOKENS de ponta a ponta (decidir/verificar), com provedor
    simulado — sem rede, sem aparelho, sem execução paga. `ai_max_tokens_per_run` é conferido em `_ai` ANTES de
    cada chamada (executor.py); com um teto menor que uma única chamada, a SEGUNDA chamada de IA da execução já
    encontra o teto estourado e vira `AIError(kind='budget')` sem nunca chamar o provedor de novo."""
    h = Harness(tmp_path, 1)
    provider = TokensPesadosNoDecidirEVerificar(SimulatedProvider(), tokens_por_chamada=1000)
    h.ai = provider  # type: ignore[assignment]
    await h.boot()
    assert h.state is not None
    try:
        # Teto do tamanho de UMA chamada (mínimo aceito pelo schema): a primeira passa (o teto é conferido com o
        # que já foi GASTO antes dela, que é zero), a segunda já encontra o run com 1000 tokens gastos >= 1000.
        h.state.settings.update({"ai_max_tokens_per_run": 1000})

        run = h.run(["android-01"])
        detail = await h.wait_run(run.id)

        row = h.state.repo.run_row(run.id)  # type: ignore[union-attr]
        # A execução gastou tokens de verdade (não fica em zero, como o simulado puro deixaria) e o teto
        # bloqueou o resto: nunca sobe muito além do necessário para provar o estouro.
        assert row["ai_input_tokens"] >= 1000
        assert row["status"] in (RunStatus.failed.value, RunStatus.completed_with_issues.value)
        assert any("orçamento" in (o.status_detail or "").lower() for o in detail.objectives)

        # O painel (/api/usage) mostra o erro por TIPO — 'budget' — o que ele NÃO fazia antes desta correção:
        # o teto de tokens recusa ANTES de chamar o provedor, e essa recusa não virava linha em `ai_calls`.
        from app.main import create_app

        app = create_app(h.cfg, state=h.state)
        app.state.poc = h.state
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            u = (await c.get("/api/usage", params={"run_id": run.id})).json()
        assert u["errors_by_kind"].get("budget", 0) >= 1
    finally:
        await h.state.stop()
