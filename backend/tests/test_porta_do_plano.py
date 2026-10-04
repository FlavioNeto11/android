"""30.61: a prévia da porta numa execução `planned` (`GET /api/runs/{id}/porta`). O selo de cada etapa é a mesma conta
da porta do despacho, só lendo; a chave só sai para o item fechado que pede aprovação; o resto fica `na_execucao`.

Nível de prova: `simulated` (harness com aparelhos falsos, catálogo do Instagram, banco de teste). Nada real.
"""
from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient

from app.main import create_app
from app.models import ProfileCreate
from app.porta_do_plano import APROVACAO, NA_EXECUCAO, RECUSADO, previa_da_porta

from .test_capabilities import IG, SENHA

ALVO = "@anarabottinipsicopedagoga"
DM = {"username": ALVO, "content": "oi, tudo bem?", "content_verbatim": "true"}


def _plano(state: Any, etapas: list[dict[str, Any]], *, aparelho: str = "android-01", status: str = "planned",
           run_id: str = "run-p") -> str:
    """Uma execução com o plano materializado: um objetivo em `aparelho`, com perfil do Instagram, e as `etapas`."""
    db = state.db
    if not db.scalar("SELECT 1 FROM apps WHERE id='ig'"):
        db.execute("INSERT INTO apps(id, name, package, activity, builtin) VALUES ('ig','Instagram',?,NULL,0)", (IG,))
    db.execute("UPDATE instances SET app_id='ig' WHERE id=?", (aparelho,))
    pid = state.social.create_profile(ProfileCreate(username=f"lucas.{aparelho.replace('-', '')}", password=SENHA,
                                                    instance_id=aparelho)).id
    state.social_repo.update_profile(pid, {"automation_policy": '{"limits": {"warmup_days": 0, '
                                                                '"cooldown_between_external_actions_s": 0}}'})
    db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at, plan)"
               " VALUES (?,?,'x','plan',?,1,?,'2026-10-04T10:00:00Z',NULL)",
               (run_id, f"k-{run_id}", status, json.dumps([aparelho])))
    oid = f"{run_id}:{aparelho}"
    db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version, parameters, profile_id)"
               " VALUES (?,?,?,'pending',1,'{}',?)", (oid, run_id, aparelho, pid))
    for seq, e in enumerate(etapas, start=1):
        db.execute(
            "INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal, depends_on,"
            " side_effect, commit_guard, postcondition, timeout_s, max_attempts, status, capability, bindings, for_each)"
            " VALUES (?,?,?,?,1,?,?,?,'x',?,?,'[]','{\"kind\":\"model_judged\",\"value\":\"x\",\"description\":\"y\"}',"
            "180,1,'pending',?,?,?)",
            (f"{oid}:v1:{e['key']}", run_id, oid, aparelho, seq, e["key"], e.get("title", e["key"]),
             json.dumps(e.get("depende", [])), int(e.get("efeito", 1)), e.get("cap"), json.dumps(e.get("bindings", {})),
             e.get("for_each")))
    return pid


def _por_chave(previa: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(i["step_id"]).rsplit(":", 1)[-1]: i for i in previa["itens"]}


def _contagens(state: Any) -> tuple[object, ...]:
    """O que a porta do despacho escreveria: pedido, evento/decisão, exceção presa, rascunho na etapa."""
    return (*(int(state.db.scalar(f"SELECT COUNT(*) FROM {t}") or 0)
              for t in ("pending_approvals", "events", "excecoes_de_politica")),
            [tuple(r) for r in state.db.query("SELECT id, bindings, draft_meta, status FROM steps ORDER BY id")])


async def test_selos_chave_dependentes_e_o_que_fica_para_a_execucao(harness: Any) -> None:
    state = harness.state
    _plano(state, [
        {"key": "dm", "cap": "SEND_MESSAGE", "bindings": DM},
        {"key": "dm_de_novo", "cap": "SEND_MESSAGE", "bindings": DM},
        {"key": "rascunho", "cap": "SEND_MESSAGE", "bindings": {"username": "@outra.pessoa",
                                                                 "content_brief": "agradeça a visita"}},
        {"key": "resposta", "cap": "REPLY_COMMENT", "bindings": {"username": ALVO, "content": "obrigada!",
                                                                  "content_verbatim": "true"}},
        {"key": "curtir_qualquer", "cap": "LIKE_POST", "bindings": {"post_author": ALVO}},
        {"key": "curtir", "cap": "LIKE_POST", "bindings": {"post_author": ALVO, "caption_contains": "Setembro"}},
        {"key": "curtir_de_novo", "cap": "LIKE_POST", "bindings": {"post_author": ALVO, "caption_contains": "Setembro"}},
        {"key": "depois", "cap": None, "efeito": 0, "depende": ["dm"]},
        {"key": "neto", "cap": None, "efeito": 0, "depende": ["depois"]},
        {"key": "cada", "cap": "SEND_MESSAGE", "bindings": {"username": "{item}", "content": "oi",
                                                             "content_verbatim": "true"}, "for_each": "contatos"},
    ])
    antes = _contagens(state)
    previa = previa_da_porta(state, "run-p")
    assert _contagens(state) == antes                       # só lendo: nem decisão, nem pedido, nem rascunho
    itens = _por_chave(previa)

    dm = itens["dm"]
    assert dm["selo"] == APROVACAO and dm["chave"] and len(dm["chave"]) == 64 and dm["texto"] == "oi, tudo bem?"
    assert dm["dependentes"] == ["run-p:android-01:v1:depois", "run-p:android-01:v1:neto"]   # transitivo
    # a mesma mensagem duas vezes no plano: a segunda vira confirmação depois da primeira (sem chave: seria a mesma)
    assert itens["dm_de_novo"]["selo"] == NA_EXECUCAO and itens["dm_de_novo"]["chave"] is None
    # texto ainda por escrever: o rascunho na prévia é a fatia seguinte
    assert itens["rascunho"]["selo"] == NA_EXECUCAO and itens["rascunho"]["texto_na_execucao"]
    assert itens["rascunho"]["chave"] is None
    # REPLY_COMMENT: o objeto declarado não diz QUAL comentário; curtir "um post de @x": não diz qual post
    for chave in ("resposta", "curtir_qualquer"):
        assert itens[chave]["chave"] is None and itens[chave]["selo"] != APROVACAO, itens[chave]
    # o mesmo post curtido duas vezes no plano: o segundo não acontece
    assert itens["curtir"]["selo"] != RECUSADO and itens["curtir_de_novo"]["selo"] == RECUSADO
    assert "depois" not in itens and "cada" not in itens      # sem ação nem efeito; etapa-modelo do for_each
    assert previa["na_execucao"]["itens_for_each"] == 1 and previa["na_execucao"]["textos_da_tela"] == 1
    assert previa["estimativa"] and not previa["parcial"] and not previa["total"]
    assert len(previa["hash_do_plano"]) == 64 and previa["custo_rascunhos_usd"] == 0.0


async def test_a_mesma_previa_relida_da_a_mesma_chave_e_o_texto_mudado_outra(harness: Any) -> None:
    state = harness.state
    _plano(state, [{"key": "dm", "cap": "SEND_MESSAGE", "bindings": DM}])
    um = _por_chave(previa_da_porta(state, "run-p"))["dm"]["chave"]
    assert _por_chave(previa_da_porta(state, "run-p"))["dm"]["chave"] == um
    state.db.execute("UPDATE steps SET bindings=? WHERE key='dm'", (json.dumps({**DM, "content": "oi!"}),))
    outra = previa_da_porta(state, "run-p")
    assert _por_chave(outra)["dm"]["chave"] not in (None, um)


async def test_falha_de_um_item_nao_derruba_a_previa_e_ele_nao_ganha_chave(harness: Any, monkeypatch: Any) -> None:
    state = harness.state
    _plano(state, [{"key": "dm", "cap": "SEND_MESSAGE", "bindings": DM},
                   {"key": "outra", "cap": "SEND_MESSAGE", "bindings": {**DM, "username": "@outra.pessoa"}}])
    original = state.vereditos_da_porta

    def quebra_uma(obj: Any, srow: Any, run: Any) -> Any:
        if srow["key"] == "outra":
            raise RuntimeError("banco indisponível")
        return original(obj, srow, run)

    monkeypatch.setattr(state, "vereditos_da_porta", quebra_uma)
    previa = previa_da_porta(state, "run-p")
    itens = _por_chave(previa)
    assert itens["outra"]["falhou"] and itens["outra"]["selo"] == NA_EXECUCAO and itens["outra"]["chave"] is None
    assert itens["dm"]["chave"] and previa["parcial"] and not previa["total"]


async def test_aparelho_fora_do_gerenciador_fica_para_a_execucao(harness: Any) -> None:
    state = harness.state
    _plano(state, [{"key": "dm", "cap": "SEND_MESSAGE", "bindings": DM}])
    state.devices.devices.pop("android-01")
    item = _por_chave(previa_da_porta(state, "run-p"))["dm"]
    assert item["selo"] == NA_EXECUCAO and item["chave"] is None and "gerenciador" in item["motivo"]


async def test_pela_rota_404_409_e_200(harness: Any) -> None:
    state = harness.state
    _plano(state, [{"key": "dm", "cap": "SEND_MESSAGE", "bindings": DM}])
    _plano(state, [{"key": "dm", "cap": "SEND_MESSAGE", "bindings": DM}], aparelho="android-02", status="running",
           run_id="run-r")
    app = create_app(harness.cfg, state=state)
    app.state.poc = state
    cliente = TestClient(app, client=("127.0.0.1", 123))
    assert cliente.get("/api/runs/nao-existe/porta").status_code == 404
    fora = cliente.get("/api/runs/run-r/porta")
    assert fora.status_code == 409 and fora.json()["detail"]["code"] == "invalid_state"
    ok = cliente.get("/api/runs/run-p/porta")
    assert ok.status_code == 200 and ok.json()["itens"][0]["selo"] == APROVACAO
