"""30.61: a prévia da porta numa execução `planned` (`GET /api/runs/{id}/porta`). O selo de cada etapa é a mesma conta
da porta do despacho, só lendo; a chave só sai para o item fechado que pede aprovação; o resto fica `na_execucao`.

Nível de prova: `simulated` (harness com aparelhos falsos, catálogo do Instagram, banco de teste). Nada real.
"""
from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from fastapi.testclient import TestClient

from app.main import create_app
from app.models import ProfileCreate
from app.porta_do_plano import (
    APROVACAO,
    NA_EXECUCAO,
    RECUSADO,
    AprovarPlanoBody,
    ItemAprovado,
    PortaIndisponivel,
    aprovar_plano,
    PreviaDoItemBody,
    previa_da_porta,
    previa_do_item,
    renovar_plano,
)
from app.taskqueue.repository import MOTIVO_REJEICAO
from app.util import now, parse_iso, to_iso

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


# ------------------------------------------------------------------ o gesto "Aprovar N e iniciar" e a porta que o honra
def _sem_iniciar(state: Any, monkeypatch: Any) -> list[tuple[str, str]]:
    """`runs.start` registrado e sem despachar: o que se mede aqui é o gesto e a porta, não o agendador."""
    chamadas: list[tuple[str, str]] = []

    def start(run_id: str, *, por: str = "sistema") -> Any:
        chamadas.append((run_id, por))
        return state.repo.run_summary(state.repo.run_row(run_id))

    monkeypatch.setattr(state.runs, "start", start)
    return chamadas


async def _gate(state: Any, chave_da_etapa: str, run_id: str = "run-p") -> Any:
    db = state.db
    etapa = db.one("SELECT * FROM steps WHERE id=?", (f"{run_id}:android-01:v1:{chave_da_etapa}",))
    return await state._policy_gate(db.one("SELECT * FROM objectives WHERE id=?", (etapa["objective_id"],)),  # noqa: SLF001
                                    etapa, db.one("SELECT * FROM runs WHERE id=?", (run_id,)))


def _chave_do_texto(state: Any, step_id: str, texto: str) -> str:
    """30.68: a chave que o dono VÊ na prévia do texto editado, a que o gesto exige."""
    return str(previa_do_item(state, "run-p", PreviaDoItemBody(step_id=step_id, texto=texto))["item"]["chave"])  # type: ignore[index]


def _plano_com_dm(state: Any) -> dict[str, dict[str, Any]]:
    _plano(state, [
        {"key": "dm", "cap": "SEND_MESSAGE", "bindings": DM},
        {"key": "dm2", "cap": "SEND_MESSAGE", "bindings": {**DM, "username": "@outra.pessoa"}},
        {"key": "depois", "cap": None, "efeito": 0, "depende": ["dm2"]},
    ])
    return _por_chave(previa_da_porta(state, "run-p"))


async def test_aprovar_grava_o_sim_do_plano_tira_as_dependentes_e_inicia(harness: Any, monkeypatch: Any) -> None:
    state = harness.state
    iniciou = _sem_iniciar(state, monkeypatch)
    itens = _plano_com_dm(state)
    corpo = AprovarPlanoBody(aprovar=[ItemAprovado(step_id=itens["dm"]["step_id"], chave=itens["dm"]["chave"])],
                             tirar=[itens["dm2"]["step_id"]])
    saida = aprovar_plano(state, "run-p", corpo, por="flavio")
    [linha] = state.db.query("SELECT * FROM pending_approvals")
    assert (linha["origem"], linha["status"], linha["step_id"]) == ("plano", "approved", itens["dm"]["step_id"])
    assert linha["chave_sha256"] == itens["dm"]["chave"] and linha["chave_v"] == 1 and linha["plan_version"] == 1
    assert linha["decided_by"] == "flavio" and linha["generated_content"] == "oi, tudo bem?"
    assert parse_iso(linha["expires_at"]) > now() + timedelta(hours=23)
    status = {r["key"]: r["status"] for r in state.db.query("SELECT key, status FROM steps")}
    assert status["dm2"] == "cancelled" and status["depois"] == "cancelled" and status["dm"] != "cancelled"
    # decisão, não lacuna: o prefixo da rejeição impede o `recovery_steps` de recriar as tiradas
    assert all(r["status_detail"].startswith(MOTIVO_REJEICAO) for r in state.db.query(
        "SELECT status_detail FROM steps WHERE key IN ('dm2','depois')"))
    assert saida["tiradas"] == ["run-p:android-01:v1:depois", "run-p:android-01:v1:dm2"]
    assert iniciou == [("run-p", "flavio")]
    # o segundo gesto não grava outro sim
    try:
        aprovar_plano(state, "run-p", corpo, por="flavio")
        raise AssertionError("o segundo gesto devia ser recusado")
    except PortaIndisponivel as exc:
        assert exc.codigo in ("invalid_state", "plano_mudou")
    assert int(state.db.scalar("SELECT COUNT(*) FROM pending_approvals")) == 1


async def test_item_que_mudou_devolve_409_e_nada_e_gravado(harness: Any, monkeypatch: Any) -> None:
    state = harness.state
    iniciou = _sem_iniciar(state, monkeypatch)
    itens = _plano_com_dm(state)
    state.db.execute("UPDATE steps SET bindings=? WHERE key='dm'", (json.dumps({**DM, "content": "oi!"}),))
    corpo = AprovarPlanoBody(aprovar=[ItemAprovado(step_id=itens["dm"]["step_id"], chave=itens["dm"]["chave"])],
                             tirar=[itens["dm2"]["step_id"]])
    try:
        aprovar_plano(state, "run-p", corpo, por="flavio")
        raise AssertionError("devia devolver plano_mudou")
    except PortaIndisponivel as exc:
        assert exc.codigo == "plano_mudou" and exc.status == 409
        assert [m["step_id"] for m in exc.extra["mudaram"]] == [itens["dm"]["step_id"]]   # type: ignore[union-attr]
        assert "itens" in exc.extra["previa"]                                              # type: ignore[operator]
    assert not state.db.scalar("SELECT COUNT(*) FROM pending_approvals") and not iniciou
    assert state.db.scalar("SELECT status FROM steps WHERE key='dm2'") != "cancelled"


async def test_a_porta_honra_o_sim_identico_e_descarta_o_mudado(harness: Any, monkeypatch: Any) -> None:
    state = harness.state
    _sem_iniciar(state, monkeypatch)
    itens = _plano_com_dm(state)
    aprovar_plano(state, "run-p", AprovarPlanoBody(aprovar=[ItemAprovado(step_id=itens["dm"]["step_id"],
                                                                         chave=itens["dm"]["chave"])]), por="flavio")
    assert await _gate(state, "dm") is None                      # o mesmo item: segue sem parar
    assert int(state.db.scalar("SELECT COUNT(*) FROM pending_approvals")) == 1
    state.db.execute("UPDATE steps SET bindings=? WHERE key='dm'", (json.dumps({**DM, "content": "oi!"}),))
    veredito = await _gate(state, "dm")                          # texto mudou: o sim do plano não cobre
    assert veredito is not None and not veredito.allowed and veredito.policy == "approval_required"
    linhas = {r["origem"]: r for r in state.db.query("SELECT * FROM pending_approvals")}
    assert linhas["plano"]["status"] == "expired" and "chave divergiu" in linhas["plano"]["decided_note"]
    assert linhas["execucao"]["status"] == "pending"


async def test_o_sim_do_plano_vencido_nao_vale(harness: Any, monkeypatch: Any) -> None:
    state = harness.state
    _sem_iniciar(state, monkeypatch)
    itens = _plano_com_dm(state)
    aprovar_plano(state, "run-p", AprovarPlanoBody(aprovar=[ItemAprovado(step_id=itens["dm"]["step_id"],
                                                                         chave=itens["dm"]["chave"])]), por="flavio")
    state.db.execute("UPDATE pending_approvals SET expires_at=?", (to_iso(now() - timedelta(minutes=1)),))
    veredito = await _gate(state, "dm")
    assert veredito is not None and not veredito.allowed
    # Duas saídas certas: a porta o descarta ("… venceu") ou a faxina da subida do harness já o marcou ("… vencido
    # (validade)") antes do `_gate`. Em ambas o sim sai `expired` e a porta recusa; a nota não decide o teste.
    linha = state.db.one("SELECT status, decided_note FROM pending_approvals WHERE origem='plano'")
    assert linha["status"] == "expired" and "venc" in linha["decided_note"]


async def test_o_gesto_pela_rota(harness: Any, monkeypatch: Any) -> None:
    state = harness.state
    _sem_iniciar(state, monkeypatch)
    itens = _plano_com_dm(state)
    app = create_app(harness.cfg, state=state)
    app.state.poc = state
    cliente = TestClient(app, client=("127.0.0.1", 123))
    errado = cliente.post("/api/runs/run-p/aprovar-plano",
                          json={"aprovar": [{"step_id": itens["dm"]["step_id"], "chave": "0" * 64}]})
    assert errado.status_code == 409 and errado.json()["detail"]["code"] == "plano_mudou"
    assert errado.json()["detail"]["previa"]["itens"]
    ok = cliente.post("/api/runs/run-p/aprovar-plano",
                      json={"aprovar": [{"step_id": itens["dm"]["step_id"], "chave": itens["dm"]["chave"]}]})
    assert ok.status_code == 200 and len(ok.json()["aprovacoes"]) == 1


async def test_renovar_pela_rota(harness: Any, monkeypatch: Any) -> None:
    """`POST /api/runs/{id}/porta/renovar` pela HTTP: 200 com `renovadas` e `vencidas`, 409 `sim_vencido` quando nada
    renova, 409 `invalid_state` na execução terminada e 404 na inexistente."""
    state = harness.state
    _sem_iniciar(state, monkeypatch)
    itens = _plano_com_dm(state)
    aprovar_plano(state, "run-p", AprovarPlanoBody(aprovar=[ItemAprovado(step_id=itens["dm"]["step_id"],
                                                                         chave=itens["dm"]["chave"])]), por="flavio")
    app = create_app(harness.cfg, state=state)
    app.state.poc = state
    cliente = TestClient(app, client=("127.0.0.1", 123))
    ok = cliente.post("/api/runs/run-p/porta/renovar")
    assert ok.status_code == 200 and ok.json()["renovadas"] == 1 and ok.json()["vencidas"] == 0
    state.db.execute("UPDATE pending_approvals SET expires_at=?", (to_iso(now() - timedelta(minutes=1)),))
    vencido = cliente.post("/api/runs/run-p/porta/renovar")
    assert vencido.status_code == 409 and vencido.json()["detail"]["code"] == "sim_vencido"
    state.runs.cancel("run-p")
    fora = cliente.post("/api/runs/run-p/porta/renovar")
    assert fora.status_code == 409 and fora.json()["detail"]["code"] == "invalid_state"
    assert cliente.post("/api/runs/nao-existe/porta/renovar").status_code == 404


async def test_renovar_estende_a_validade_e_cancelar_encerra_o_sim(harness: Any, monkeypatch: Any) -> None:
    state = harness.state
    _sem_iniciar(state, monkeypatch)
    itens = _plano_com_dm(state)
    aprovar_plano(state, "run-p", AprovarPlanoBody(aprovar=[ItemAprovado(step_id=itens["dm"]["step_id"],
                                                                         chave=itens["dm"]["chave"])]), por="flavio")
    state.db.execute("UPDATE pending_approvals SET expires_at=?", (to_iso(now() + timedelta(hours=1)),))
    saida = renovar_plano(state, "run-p")
    assert saida["renovadas"] == 1
    assert parse_iso(state.db.scalar("SELECT expires_at FROM pending_approvals")) > now() + timedelta(hours=23)
    assert await _gate(state, "dm") is None                      # renovado, a mesma chave segue valendo
    state.runs.cancel("run-p")                                   # ainda `planned`: cancela antes de iniciar
    linha = state.db.one("SELECT status, decided_note FROM pending_approvals")
    assert linha["status"] == "expired" and linha["decided_note"].startswith("sim do plano encerrado")
    try:
        renovar_plano(state, "run-p")
        raise AssertionError("execução cancelada não renova")
    except PortaIndisponivel as exc:
        assert exc.codigo == "invalid_state"


async def test_sim_de_versao_anterior_do_plano_nao_conta_como_pedido_em_aberto(harness: Any, monkeypatch: Any) -> None:
    """Revisão, item 6: o sim do plano da v1 não migra para a v2 e nunca ganha interação; não pode contar contra ela."""
    state = harness.state
    _sem_iniciar(state, monkeypatch)
    itens = _plano_com_dm(state)
    aprovar_plano(state, "run-p", AprovarPlanoBody(aprovar=[ItemAprovado(step_id=itens["dm"]["step_id"],
                                                                         chave=itens["dm"]["chave"])]), por="flavio")
    pid = itens["dm"]["profile_id"]
    desde = to_iso(now() - timedelta(days=1))
    assert len(state.social_repo.pedidos_da_acao(pid, "SEND_MESSAGE", since=desde)) == 1
    state.db.execute("UPDATE objectives SET plan_version=2 WHERE id='run-p:android-01'")
    assert state.social_repo.pedidos_da_acao(pid, "SEND_MESSAGE", since=desde) == []


async def test_a_faxina_vence_o_sim_do_plano_fora_da_validade(harness: Any, monkeypatch: Any) -> None:
    state = harness.state
    _sem_iniciar(state, monkeypatch)
    itens = _plano_com_dm(state)
    aprovar_plano(state, "run-p", AprovarPlanoBody(aprovar=[ItemAprovado(step_id=itens["dm"]["step_id"],
                                                                         chave=itens["dm"]["chave"])]), por="flavio")
    assert state.approvals.vencer_do_plano(to_iso(now())) == 0
    state.db.execute("UPDATE pending_approvals SET expires_at=?", (to_iso(now() - timedelta(minutes=1)),))
    assert await state._expiracao_uma_vez() in (True, False)     # noqa: SLF001 - só roda no líder da trava
    state.approvals.vencer_do_plano(to_iso(now()))
    linha = state.db.one("SELECT status, decided_note FROM pending_approvals")
    assert linha["status"] == "expired" and "validade" in linha["decided_note"]
    assert state.db.scalar("SELECT status FROM runs WHERE id='run-p'") == "planned"   # a execução fica como está


async def test_renovar_nao_ressuscita_o_sim_vencido_que_a_faxina_nao_marcou(harness: Any, monkeypatch: Any) -> None:
    """Revisão da parte 17: o sim vencido e ainda `approved` (a faxina roda a cada ciclo) não volta a valer pelo Renovar."""
    state = harness.state
    _sem_iniciar(state, monkeypatch)
    itens = _plano_com_dm(state)
    aprovar_plano(state, "run-p", AprovarPlanoBody(aprovar=[ItemAprovado(step_id=itens["dm"]["step_id"],
                                                                         chave=itens["dm"]["chave"])]), por="flavio")
    # Síncrono de propósito entre o UPDATE e o Renovar: um `await` aqui deixaria a faxina do harness marcar antes.
    state.db.execute("UPDATE pending_approvals SET expires_at=?", (to_iso(now() - timedelta(minutes=1)),))
    try:
        renovar_plano(state, "run-p")
        raise AssertionError("o Renovar devia recusar o sim vencido")
    except PortaIndisponivel as exc:
        assert exc.codigo == "sim_vencido" and exc.extra["vencidas"] == 1
    linha = state.db.one("SELECT status, expires_at FROM pending_approvals")
    assert linha["status"] == "expired" and parse_iso(linha["expires_at"]) < now()
    assert state.db.scalar("SELECT COUNT(*) FROM events WHERE run_id='run-p' AND kind='decision'"
                           " AND message LIKE '%não se renovaram%'") == 1


async def test_editar_o_texto_no_cartao_do_plano_recalcula_a_chave(harness: Any, monkeypatch: Any) -> None:
    """Contrato da chave: a gravada é a do texto que vai sair, não a do texto da prévia."""
    state = harness.state
    _sem_iniciar(state, monkeypatch)
    itens = _plano_com_dm(state)
    vista = itens["dm"]["chave"]
    editada = _chave_do_texto(state, itens["dm"]["step_id"], "oi! tudo certo por aí?")
    aprovar_plano(state, "run-p", AprovarPlanoBody(aprovar=[ItemAprovado(
        step_id=itens["dm"]["step_id"], chave=editada, texto="oi! tudo certo por aí?")]), por="flavio")
    linha = state.db.one("SELECT * FROM pending_approvals")
    assert linha["chave_sha256"] == editada != vista and linha["generated_content"] == "oi! tudo certo por aí?"
    assert json.loads(state.db.scalar("SELECT bindings FROM steps WHERE key='dm'"))["content"] == "oi! tudo certo por aí?"
    assert await _gate(state, "dm") is None                      # a etapa relida tem a chave gravada


async def test_texto_editado_com_variavel_ou_vazio_e_recusado_sem_gravar(harness: Any, monkeypatch: Any) -> None:
    state = harness.state
    iniciou = _sem_iniciar(state, monkeypatch)
    itens = _plano_com_dm(state)
    for texto in ("oi {item}", "   "):
        try:
            aprovar_plano(state, "run-p", AprovarPlanoBody(aprovar=[ItemAprovado(
                step_id=itens["dm"]["step_id"], chave=itens["dm"]["chave"], texto=texto)]), por="flavio")
            raise AssertionError("devia recusar")
        except PortaIndisponivel as exc:
            assert exc.status == 422 and exc.codigo == "invalid_body"
    assert not state.db.scalar("SELECT COUNT(*) FROM pending_approvals") and not iniciou
    assert json.loads(state.db.scalar("SELECT bindings FROM steps WHERE key='dm'"))["content"] == DM["content"]


async def test_renovar_misto_renova_o_valido_e_devolve_o_vencido(harness: Any, monkeypatch: Any) -> None:
    state = harness.state
    _sem_iniciar(state, monkeypatch)
    itens = _plano_com_dm(state)
    aprovar_plano(state, "run-p", AprovarPlanoBody(aprovar=[
        ItemAprovado(step_id=itens["dm"]["step_id"], chave=itens["dm"]["chave"]),
        ItemAprovado(step_id=itens["dm2"]["step_id"], chave=itens["dm2"]["chave"])]), por="flavio")
    # Síncrono de propósito entre o UPDATE e o Renovar (a faxina do harness não pode correr no meio).
    state.db.execute("UPDATE pending_approvals SET expires_at=? WHERE step_id=?",
                     (to_iso(now() - timedelta(minutes=1)), itens["dm2"]["step_id"]))
    saida = renovar_plano(state, "run-p")
    assert (saida["renovadas"], saida["vencidas"]) == (1, 1)
    estados = {r["step_id"]: r["status"] for r in state.db.query("SELECT step_id, status FROM pending_approvals")}
    assert estados == {itens["dm"]["step_id"]: "approved", itens["dm2"]["step_id"]: "expired"}


async def test_texto_editado_que_muda_o_selo_volta_409_e_desfaz_a_edicao(harness: Any, monkeypatch: Any) -> None:
    """Revisão do painel, B1: o selo se refaz com o texto editado. Se ele deixa de ser 🔒, nada se grava e o dono ouve o
    porquê. O selo recusado é FORÇADO aqui (monkeypatch): hoje nenhuma regra real tira o item do 🔒 pela edição — a
    recusa do comentário repetido é pelo objeto, não pelo texto, e a DM repetida pede confirmação. O teste com a regra
    real possível é o da DM (`test_dm_editada_para_um_texto_ja_enviado…`)."""
    import app.porta_do_plano as porta

    state = harness.state
    iniciou = _sem_iniciar(state, monkeypatch)
    itens = _plano_com_dm(state)
    original = porta._item

    def com_texto_repetido(*args: Any, **kw: Any) -> Any:
        item = original(*args, **kw)
        if item is not None and item.get("texto") == "já mandei isto":
            return {**item, "selo": porta.RECUSADO, "chave": None, "motivo": "esta conta já mandou este texto"}
        return item

    monkeypatch.setattr(porta, "_item", com_texto_repetido)
    try:
        aprovar_plano(state, "run-p", AprovarPlanoBody(aprovar=[ItemAprovado(
            step_id=itens["dm"]["step_id"], chave=itens["dm"]["chave"], texto="já mandei isto")]), por="flavio")
        raise AssertionError("devia devolver plano_mudou")
    except PortaIndisponivel as exc:
        assert exc.codigo == "plano_mudou"
        [mudou] = exc.extra["mudaram"]                                                   # type: ignore[misc]
        assert mudou["motivo"] == "com o texto editado: esta conta já mandou este texto"
    assert not state.db.scalar("SELECT COUNT(*) FROM pending_approvals") and not iniciou
    assert json.loads(state.db.scalar("SELECT bindings FROM steps WHERE key='dm'"))["content"] == DM["content"]


async def test_chave_solta_e_texto_final_e_o_longo_tem_mensagem_propria(harness: Any, monkeypatch: Any) -> None:
    """B2: só o marcador de modelo (`{nome}`) é variável; o texto acima do limite recebe a mensagem própria."""
    state = harness.state
    _sem_iniciar(state, monkeypatch)
    itens = _plano_com_dm(state)
    try:
        aprovar_plano(state, "run-p", AprovarPlanoBody(aprovar=[ItemAprovado(
            step_id=itens["dm"]["step_id"], chave=itens["dm"]["chave"], texto="x" * 2201)]), por="flavio")
        raise AssertionError("devia recusar o texto longo")
    except PortaIndisponivel as exc:
        assert exc.codigo == "texto_longo" and "2200" in exc.mensagem
    aprovar_plano(state, "run-p", AprovarPlanoBody(aprovar=[ItemAprovado(
        step_id=itens["dm"]["step_id"], chave=_chave_do_texto(state, itens["dm"]["step_id"], "oi :-{ até logo"),
        texto="oi :-{ até logo")]), por="flavio")
    assert state.db.scalar("SELECT generated_content FROM pending_approvals") == "oi :-{ até logo"


async def test_chave_solta_aprovada_no_plano_e_honrada_pela_porta_na_execucao(harness: Any, monkeypatch: Any) -> None:
    """Nota da Ferramentas (04/10), 2a: o texto com `{` literal (`:-{`) ganha chave na prévia, o sim do plano a grava e a
    porta da execução, que recalcula a chave da etapa relida pela mesma função, segue sem perguntar."""
    state = harness.state
    _sem_iniciar(state, monkeypatch)
    _plano(state, [{"key": "dm", "cap": "SEND_MESSAGE", "bindings": {**DM, "content": "oi :-{ até logo"}}])
    item = _por_chave(previa_da_porta(state, "run-p"))["dm"]
    assert item["selo"] == "aprovacao" and item["chave"]
    aprovar_plano(state, "run-p", AprovarPlanoBody(aprovar=[ItemAprovado(step_id=item["step_id"], chave=item["chave"])]),
                  por="flavio")
    assert state.db.scalar("SELECT chave_sha256 FROM pending_approvals") == item["chave"]
    assert await _gate(state, "dm") is None


async def test_a_segunda_dm_ao_mesmo_alvo_editada_nao_vira_cadeado(harness: Any, monkeypatch: Any) -> None:
    """A1 da revisão do 30.68: a 2ª DM do mesmo perfil ao mesmo alvo fica para a execução na prévia; editar o texto
    dela (pela rota ou pelo gesto) não a põe no 🔒, porque o duplicado no plano não depende do texto."""
    state = harness.state
    _sem_iniciar(state, monkeypatch)
    _plano(state, [{"key": "dm", "cap": "SEND_MESSAGE", "bindings": DM},
                   {"key": "dm2", "cap": "SEND_MESSAGE", "bindings": {**DM, "content": "e aí?"}}])
    itens = _por_chave(previa_da_porta(state, "run-p"))
    assert itens["dm"]["selo"] == "aprovacao" and itens["dm2"]["selo"] == "na_execucao"
    antes = _contagens(state)
    try:
        previa_do_item(state, "run-p", PreviaDoItemBody(step_id=itens["dm2"]["step_id"], texto="outro texto"))
        raise AssertionError("devia recusar")
    except PortaIndisponivel as exc:
        assert exc.codigo == "plano_mudou" and exc.status == 409
    try:
        aprovar_plano(state, "run-p", AprovarPlanoBody(aprovar=[ItemAprovado(
            step_id=itens["dm2"]["step_id"], chave="d" * 64, texto="outro texto")]), por="flavio")
        raise AssertionError("devia recusar")
    except PortaIndisponivel as exc:
        assert exc.codigo == "plano_mudou"
        assert [m["step_id"] for m in exc.extra["mudaram"]] == [itens["dm2"]["step_id"]]
    assert _contagens(state) == antes                            # nada gravado, o texto da etapa intacto
    # A 1ª DM, editada, segue no 🔒 (os `vistos` do plano não a confundem com a 2ª).
    r = previa_do_item(state, "run-p", PreviaDoItemBody(step_id=itens["dm"]["step_id"], texto="oi de novo"))
    assert r["item"]["selo"] == "aprovacao" and r["item"]["chave"]


async def test_o_segundo_comentario_no_mesmo_objeto_editado_segue_recusado(harness: Any, monkeypatch: Any) -> None:
    """A1 da revisão do 30.68: o 2º comentário no mesmo post é recusado na prévia; editar o texto não o abre."""
    state = harness.state
    _sem_iniciar(state, monkeypatch)
    post = {"post_author": "ana", "caption_contains": "praia", "content_verbatim": "true"}
    _plano(state, [{"key": "c1", "cap": "CREATE_COMMENT", "bindings": {**post, "content": "que lugar!"}},
                   {"key": "c2", "cap": "CREATE_COMMENT", "bindings": {**post, "content": "lindo"}}])
    itens = _por_chave(previa_da_porta(state, "run-p"))
    assert itens["c1"]["selo"] == "aprovacao" and itens["c2"]["selo"] == "recusado"
    for chamada in (lambda: previa_do_item(state, "run-p", PreviaDoItemBody(step_id=itens["c2"]["step_id"], texto="outro")),
                    lambda: aprovar_plano(state, "run-p", AprovarPlanoBody(aprovar=[ItemAprovado(
                        step_id=itens["c2"]["step_id"], chave="d" * 64, texto="outro")]), por="flavio")):
        try:
            chamada()
            raise AssertionError("devia recusar")
        except PortaIndisponivel as exc:
            assert exc.codigo == "plano_mudou"
    assert state.db.scalar("SELECT COUNT(*) FROM pending_approvals") == 0


async def test_a_publicacao_no_plano_leva_a_imagem_que_vai_ao_feed(harness: Any) -> None:
    """29.30/30.68: o item com imagem pronta traz `image_id` (o painel a mostra); imagem sem sha256 não traz."""
    state = harness.state
    pid = _plano(state, [{"key": "pub", "cap": "CREATE_POST",
                          "bindings": {"image_id": "img-1", "content": "praia hoje", "content_verbatim": "true"}},
                         {"key": "pub2", "cap": "CREATE_POST",
                          "bindings": {"image_id": "img-2", "content": "outra", "content_verbatim": "true"}}])
    state.db.execute("INSERT INTO persona_images(id, persona_id, source, status, is_primary, created_at, bytes_sha256)"
                     " VALUES ('img-1', ?, 'generated', 'ready', 0, '2026-10-04T10:00:00Z', ?)", (pid, "c" * 64))
    itens = _por_chave(previa_da_porta(state, "run-p"))
    assert itens["pub"]["tem_imagem"] is True and itens["pub"]["image_id"] == "img-1"
    assert itens["pub"]["imagem_sha256"] == "c" * 64
    assert itens["pub2"]["tem_imagem"] is True and itens["pub2"]["image_id"] is None    # sem imagem pronta


async def test_dm_editada_para_um_texto_ja_enviado_segue_no_cadeado_com_a_regra_real(harness: Any,
                                                                                    monkeypatch: Any) -> None:
    """B1, cenário real (`_repetido` de verdade, sem monkeypatch): a única regra da porta que depende do TEXTO é a da
    DM repetida, e ela pede CONFIRMAÇÃO (nunca recusa). Editar a DM para um texto já enviado ao mesmo alvo mantém o item
    🔒 com chave: o sim do plano é a confirmação daquele texto, e a execução o honra. O comentário não entra aqui: a
    recusa dele é pelo OBJETO (o post), não pelo texto, então editar o texto não muda o selo."""
    state = harness.state
    _sem_iniciar(state, monkeypatch)
    itens = _plano_com_dm(state)
    pid = str(itens["dm"]["profile_id"])
    state.social_repo.record_interaction(pid, type="dm_sent", direction="outbound", status="confirmed",
                                         counterparty=ALVO, outgoing_content="já mandei isto", app_id="ig",
                                         run_id="r-antiga", occurred_at=to_iso(now() - timedelta(days=1)))
    # 30.68: o dono VÊ o motivo novo na prévia do texto editado antes do sim, e a chave do gesto é a dessa prévia.
    vista = previa_do_item(state, "run-p", PreviaDoItemBody(step_id=itens["dm"]["step_id"], texto="já mandei isto"))
    proposto = vista["item"]                                                                # type: ignore[index]
    assert proposto["selo"] == APROVACAO and "repetição passa por confirmação" in proposto["motivo"]
    aprovar_plano(state, "run-p", AprovarPlanoBody(aprovar=[ItemAprovado(
        step_id=itens["dm"]["step_id"], chave=proposto["chave"], texto="já mandei isto")]), por="flavio")
    linha = state.db.one("SELECT status, generated_content FROM pending_approvals WHERE origem='plano'")
    assert (linha["status"], linha["generated_content"]) == ("approved", "já mandei isto")
    previa_refeita = _por_chave(previa_da_porta(state, "run-p"))["dm"]
    assert previa_refeita["selo"] == APROVACAO and "repetição passa por confirmação" in previa_refeita["motivo"]
    assert await _gate(state, "dm") is None                      # a mesma chave: o sim do plano cobre a confirmação


async def test_previa_do_item_so_le_e_o_gesto_exige_a_chave_do_texto_editado(harness: Any, monkeypatch: Any) -> None:
    """30.68: a rota da prévia do item não grava nada; o gesto com o texto editado e a chave da prévia ANTIGA (a do
    texto que o dono não aprovou) volta 409 e não grava."""
    state = harness.state
    iniciou = _sem_iniciar(state, monkeypatch)
    itens = _plano_com_dm(state)
    antes = _contagens(state)
    vista = previa_do_item(state, "run-p", PreviaDoItemBody(step_id=itens["dm"]["step_id"], texto="  outro texto  "))
    assert _contagens(state) == antes and vista["texto"] == "outro texto"
    try:
        aprovar_plano(state, "run-p", AprovarPlanoBody(aprovar=[ItemAprovado(
            step_id=itens["dm"]["step_id"], chave=itens["dm"]["chave"], texto="outro texto")]), por="flavio")
        raise AssertionError("a chave do texto antigo não aprova o texto novo")
    except PortaIndisponivel as exc:
        assert exc.codigo == "plano_mudou"
        assert exc.extra["mudaram"][0]["motivo"].startswith("com o texto editado:")          # type: ignore[index]
    assert not state.db.scalar("SELECT COUNT(*) FROM pending_approvals") and not iniciou
    for texto, codigo in (("", "invalid_body"), ("x" * 2201, "texto_longo"), ("oi {nome}", "invalid_body")):
        try:
            previa_do_item(state, "run-p", PreviaDoItemBody(step_id=itens["dm"]["step_id"], texto=texto))
            raise AssertionError(texto)
        except PortaIndisponivel as exc:
            assert exc.codigo == codigo and exc.status == 422


async def test_previa_do_item_pela_rota(harness: Any, monkeypatch: Any) -> None:
    state = harness.state
    _sem_iniciar(state, monkeypatch)
    itens = _plano_com_dm(state)
    app = create_app(harness.cfg, state=state)
    app.state.poc = state
    cliente = TestClient(app, client=("127.0.0.1", 123))
    ok = cliente.post("/api/runs/run-p/porta/item", json={"step_id": itens["dm"]["step_id"], "texto": "oi de novo"})
    assert ok.status_code == 200 and ok.json()["item"]["selo"] == APROVACAO and len(ok.json()["item"]["chave"]) == 64
    fora = cliente.post("/api/runs/run-p/porta/item", json={"step_id": "nao-existe", "texto": "x"})
    assert fora.status_code == 409 and fora.json()["detail"]["code"] == "plano_mudou"
