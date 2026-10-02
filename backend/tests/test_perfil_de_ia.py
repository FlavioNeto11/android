"""Perfil de IA por execução e canário (item 17.7): A/B sem reiniciar o central.

O que cada bloco prova, e por que ele existe:

- **O perfil é conferido na partida.** Um perfil com função desconhecida, provedor ausente ou modelo sem capacidade
  declarada recusa a configuração, como `ai.roles` — não a primeira execução que o escolher.
- **O perfil muda só o que escreve.** Um perfil que troca o modelo do `decide` deixa as outras quatro funções (e os
  outros campos do `decide`) iguais ao padrão: a diferença entre os braços do A/B é a que está no YAML.
- **Um hub só.** As chamadas da execução com perfil passam pelo MESMO roteador, com o mesmo teto em US$; a execução
  sem perfil continua no padrão. Perfil gravado que sumiu da configuração é erro, não queda silenciosa no padrão.
- **A execução guarda o perfil e a origem.** `explicit` quando o pedido escolheu, `canary` quando o sorteio escolheu;
  o sorteio é injetável. Nome desconhecido é recusado (422) antes de criar a execução.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import uuid

import httpx
import pytest
from pydantic import ValidationError

from app.config import AiCanaryCfg, AiProfileCfg, AppConfigFile, ModelCaps, RoleCfg
from app.db import INTEGRITY_ERRORS, Database
from app.main import create_app
from app.planning import costs
from app.planning.provider import AIError, DecisionRequest, PlanRequest
from app.planning.routing import RoutingProvider

from .conftest import COMMAND, Harness, _dsn_de_teste
from .test_hub_de_ia import SCREEN, FakeProvider, FakeRepo, com_hub, ctx

PROVEDORES = {"anthropic": {"kind": "anthropic"}}


def _com_perfil(tmp: Path, perfis: dict[str, Any], roles: dict[str, Any] | None = None) -> Any:
    cfg = com_hub(tmp, providers=PROVEDORES, roles=roles or {})
    cfg.file.ai.profiles = {nome: AiProfileCfg.model_validate(p) for nome, p in perfis.items()}
    return cfg


def _instala(r: RoutingProvider, papel: str, perfil: str | None, fake: FakeProvider) -> None:
    rr = r.roles_por_perfil[perfil][papel] if perfil else r.roles[papel]
    r._por_chave[(rr.provider, rr.kind, rr.model, rr.timeout_s,                 # noqa: SLF001
                  rr.max_retries, rr.refusal_fallback)] = fake


def _banco(tmp: Path, perfis_por_execucao: dict[str, str | None]) -> Database:
    db = Database(_dsn_de_teste() or tmp / "perfil.sqlite3")
    db.migrate()
    for run_id, perfil in perfis_por_execucao.items():
        db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, instance_ids, created_at, ai_profile,"
                   " ai_profile_source) VALUES (?,?,?,?,?,?,?,?,?)",
                   (run_id, f"k-{run_id}", "c", "execute", "running", "[]", "2026-10-02T00:00:00Z", perfil,
                    "explicit" if perfil else None))
    return db


def _decide(run_id: str) -> DecisionRequest:
    return DecisionRequest(ctx=ctx(run_id), screen=SCREEN, history=[], tier=0)


# ====================================================================== configuração
def test_perfil_muda_so_o_que_escreve(tmp_path: Path) -> None:
    cfg = _com_perfil(tmp_path, {"cand": {"roles": {"decide": {"model": "claude-haiku-4-5"}}}},
                      roles={"decide": {"timeout_s": 33}})
    padrao, cand = cfg.ai_roles(), cfg.ai_roles("cand")
    assert cand["decide"].model == "claude-haiku-4-5" and padrao["decide"].model != "claude-haiku-4-5"
    assert cand["decide"].timeout_s == 33.0 == padrao["decide"].timeout_s       # o campo não escrito vem do padrão
    for papel in ("plan", "verify", "escalation", "social"):
        assert cand[papel] == padrao[papel], papel


@pytest.mark.parametrize(("ai", "erro"), [
    ({"profiles": {"cand": {"roles": {"pensar": {"model": "x"}}}}}, r"ai.profiles.cand.roles.pensar: função desconhecida"),
    ({"profiles": {"cand": {"roles": {"decide": {"provider": "sumido"}}}}},
     r"ai.profiles.cand.roles.decide.provider: provedor 'sumido' não está em ai.providers"),
    ({"providers": {"local": {"kind": "openai", "base_url": "http://x/v1"}},
      "profiles": {"cand": {"roles": {"decide": {"provider": "local", "model": "inexistente"}}}}},
     r"ai.profiles.cand.roles.decide.model: 'inexistente' não está declarado em ai.models"),
    ({"canary": {"profile": "fantasma", "fraction": 0.1}}, r"ai.canary.profile: perfil 'fantasma' não está em ai.profiles"),
])
def test_perfil_invalido_recusa_a_partida(ai: dict[str, Any], erro: str) -> None:
    with pytest.raises(ValidationError, match=erro):
        AppConfigFile.model_validate({"ai": ai})


def test_perfil_sem_visao_recusa_o_roteador_na_partida(tmp_path: Path) -> None:
    """A mesma regra 2 do hub: capacidade declarada vale para a função do perfil, conferida ao construir."""
    cfg = _com_perfil(tmp_path, {"cego": {"roles": {"decide": {"model": "modelo-cego"}}}})
    cfg.file.ai.models["modelo-cego"] = ModelCaps(vision=False)
    with pytest.raises(Exception, match="modelo-cego"):
        RoutingProvider(cfg)


# ====================================================================== roteador
async def test_execucao_com_perfil_vai_ao_provedor_do_perfil_e_a_sem_perfil_ao_padrao(tmp_path: Path) -> None:
    cfg = _com_perfil(tmp_path, {"cand": {"roles": {"decide": {"model": "claude-haiku-4-5"}}}})
    r = RoutingProvider(cfg)
    padrao, candidato = FakeProvider("anthropic", "padrao"), FakeProvider("anthropic", "candidato")
    _instala(r, "decide", None, padrao)
    _instala(r, "decide", "cand", candidato)
    r.repo = FakeRepo(_banco(tmp_path, {"r-a": None, "r-b": "cand"}))
    await r.decide(_decide("r-a"))
    await r.decide(_decide("r-b"))
    await r.decide(_decide("r-b"))
    assert padrao.calls == ["decide"] and candidato.calls == ["decide", "decide"]
    assert r.perfil_da_execucao("r-b") == "cand" and r.perfil_da_execucao("r-a") is None
    assert r.perfil_da_execucao(None) is None                              # prévia de persona, refino: padrão


async def test_planejamento_da_execucao_tambem_usa_o_perfil(tmp_path: Path) -> None:
    cfg = _com_perfil(tmp_path, {"cand": {"roles": {"plan": {"model": "claude-haiku-4-5"}}}})
    r = RoutingProvider(cfg)
    candidato = FakeProvider("anthropic", "candidato")
    _instala(r, "plan", "cand", candidato)
    r.repo = FakeRepo(_banco(tmp_path, {"r-p": "cand"}))
    await r.plan(PlanRequest(command="c", run_id="r-p", instances=[], apps=[]))
    assert candidato.calls == ["plan"]


async def test_perfil_que_sumiu_da_configuracao_nao_cai_no_padrao(tmp_path: Path) -> None:
    cfg = _com_perfil(tmp_path, {})
    r = RoutingProvider(cfg)
    padrao = FakeProvider("anthropic", "padrao")
    _instala(r, "decide", None, padrao)
    r.repo = FakeRepo(_banco(tmp_path, {"r-x": "removido"}))
    with pytest.raises(AIError, match="'removido'") as exc:
        await r.decide(_decide("r-x"))
    assert exc.value.kind == "not_configured" and padrao.calls == []


async def test_execucao_com_perfil_passa_pelo_mesmo_teto_em_usd(tmp_path: Path) -> None:
    db = _banco(tmp_path, {"r-c": "cand"})
    db.execute("INSERT INTO ai_calls(ts, run_id, role, model, input_tokens, output_tokens) VALUES (?,?,?,?,?,?)",
               (costs.day_start_iso(), "r-c", "decide", "claude-opus-5",
                1_000_000, 0))
    cfg = _com_perfil(tmp_path, {"cand": {"roles": {"decide": {"model": "claude-haiku-4-5"}}}})
    r = RoutingProvider(cfg)
    candidato = FakeProvider("anthropic", "candidato")
    _instala(r, "decide", "cand", candidato)
    r.attach(repo=FakeRepo(db), settings_getter=lambda: SimpleNamespace(ai_max_usd_per_run=1.0, ai_max_usd_per_day=0.0))
    with pytest.raises(AIError) as exc:
        await r.decide(_decide("r-c"))
    assert exc.value.kind == "budget" and candidato.calls == []


def test_perfil_que_manda_dados_para_fora_aparece_no_aviso(tmp_path: Path) -> None:
    cfg = com_hub(tmp_path, providers={"local": {"kind": "openai", "base_url": "http://127.0.0.1:8001/v1",
                                                 "sends_data_externally": False},
                                       "anthropic": {"kind": "anthropic"}},
                  models={"qwen-vl": {"vision": True, "tools": True, "structured_output": "json_object",
                                      "thinking": False, "effort": False}},
                  roles={papel: {"provider": "local", "model": "qwen-vl"}
                         for papel in ("plan", "decide", "verify", "escalation", "social")})
    assert not RoutingProvider(cfg).status().sends_data_externally
    cfg.file.ai.profiles = {"nuvem": AiProfileCfg(roles={"decide": RoleCfg(provider="anthropic",
                                                                           model="claude-haiku-4-5")})}
    status = RoutingProvider(cfg).status()
    assert status.sends_data_externally and "nuvem (decide)" in status.notice


# ====================================================================== banco
def test_migracao_064_aceita_so_as_origens_conhecidas(tmp_path: Path) -> None:
    db = _banco(tmp_path, {"r-1": "cand"})
    assert db.one("SELECT ai_profile, ai_profile_source FROM runs WHERE id='r-1'")["ai_profile_source"] == "explicit"
    with pytest.raises(INTEGRITY_ERRORS):
        db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, instance_ids, created_at,"
                   " ai_profile_source) VALUES ('r-2','k-2','c','execute','running','[]','x','sorteio')")


# ====================================================================== execução
def _perfis(h: Harness, fracao: float = 0.0) -> None:
    h.cfg.file.ai.profiles = {"cand": AiProfileCfg(roles={"decide": RoleCfg(model="claude-haiku-4-5")})}
    h.cfg.file.ai.canary = AiCanaryCfg(profile="cand" if fracao else None, fraction=fracao)


async def _cria(h: Harness, **campos: Any) -> Any:
    from app.models import RunCreate
    assert h.state is not None
    resumo = h.state.runs.create(RunCreate(command=COMMAND, instance_ids=["android-01"],
                                           idempotency_key=f"perfil-{uuid.uuid4()}", **campos))
    await h.wait_run(resumo.id, timeout=60)
    return resumo


async def test_perfil_escolhido_fica_na_execucao(harness: Harness) -> None:
    _perfis(harness)
    resumo = await _cria(harness, ai_profile="cand")
    assert (resumo.ai_profile, resumo.ai_profile_source) == ("cand", "explicit")
    linha = harness.state.db.one("SELECT ai_profile, ai_profile_source FROM runs WHERE id=?", (resumo.id,))  # type: ignore[union-attr]
    assert (linha["ai_profile"], linha["ai_profile_source"]) == ("cand", "explicit")


async def test_canario_sorteia_so_quem_nao_escolheu(harness: Harness) -> None:
    _perfis(harness, fracao=0.25)
    assert harness.state is not None
    harness.state.runs.sorteio = lambda: 0.10                       # dentro da fatia
    dentro = await _cria(harness)
    assert (dentro.ai_profile, dentro.ai_profile_source) == ("cand", "canary")
    harness.state.runs.sorteio = lambda: 0.90                       # fora da fatia: padrão
    fora = await _cria(harness)
    assert (fora.ai_profile, fora.ai_profile_source) == (None, None)
    harness.state.runs.sorteio = lambda: 0.10                       # quem escolheu não é sorteado
    escolheu = await _cria(harness, ai_profile="cand")
    assert escolheu.ai_profile_source == "explicit"


async def test_sem_perfis_nada_muda(harness: Harness) -> None:
    resumo = await _cria(harness)
    assert (resumo.ai_profile, resumo.ai_profile_source) == (None, None)


async def test_perfil_desconhecido_e_recusado_antes_de_criar(harness: Harness) -> None:
    _perfis(harness)
    assert harness.state is not None
    app = create_app(harness.cfg, state=harness.state)
    app.state.poc = harness.state
    antes = harness.state.db.scalar("SELECT COUNT(*) FROM runs")         # type: ignore[union-attr]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/runs", json={"command": "Envie oi para Bruno", "instance_ids": ["android-01"],
                                            "idempotency_key": "perfil-desconhecido-1", "ai_profile": "fantasma"})
        assert r.status_code == 422
        detalhe = r.json()["detail"]
        assert detalhe["code"] == "ai_profile_desconhecido" and "cand" in detalhe["message"]
        invalido = await c.post("/api/runs", json={"command": "Envie oi para Bruno", "instance_ids": ["android-01"],
                                                   "idempotency_key": "perfil-desconhecido-2", "ai_profile": "a b"})
        assert invalido.status_code == 422
    assert harness.state.db.scalar("SELECT COUNT(*) FROM runs") == antes  # type: ignore[union-attr]
