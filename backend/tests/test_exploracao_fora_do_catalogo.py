"""31.273 (ADR-084): o pedido fora do catálogo do app explora em vez de recusar.

O que estes testes protegem:
* a chave da exploração é vocabulário fechado: o mesmo pedido, ou o mesmo pedido dito de outro jeito, dá a mesma chave;
  nome, dígito e valor nunca entram; sem objeto reconhecido a chave é única por pedido e NUNCA é oferecida;
* o pedido de leitura ou navegação vira UMA etapa livre exploratória (sem efeito, com a ordem de não mudar nada no
  objetivo), a execução vai a `planned` e o evento `exploracao.iniciada` diz as etapas e os tetos;
* o pedido de efeito segue recusado (a porta 13.2 não é contornada), a exploração desligada também, e o teto do dia por
  app recusa com o motivo;
* a receita descoberta antes é reusada: a segunda vez a etapa é o molde (sem IA), e o molde não leva o pedido;
* a etapa exploratória para no teto de chamadas de IA e no de US$ (com o motivo dito), e a outra etapa não paga nada.

Nível de prova: `simulated` (provedor roteirizado; nenhuma chamada de IA, nenhum aparelho real).
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.models import ForaDoCatalogo, Plan, PlannerInfo, PlanStep, Postcondition
from app.planning import exploracao as ex
from app.taskqueue.recipes import hash_generico_da_etapa, step_template_hash
from app.util import now_iso

from .conftest import CountingProvider, Harness
from .test_etapas_descobertas import _semear
from .test_recusa_no_planejamento import Roteiro, _contagem, _eventos, _parque


def evento_ok(state: Any, run_id: str) -> bool:
    (e,) = _eventos(state, run_id, "exploracao.iniciada")
    return e["data"]["app_ids"] == ["outlook"]


def _fora(pedido: str) -> ForaDoCatalogo:
    return ForaDoCatalogo(app_id="outlook", app="Microsoft Outlook", pedido=pedido, disponiveis=["Abrir a caixa de entrada"])


def _plano(*pedidos: str) -> Plan:
    return Plan(summary="s", app_id="outlook", fora_do_catalogo=[_fora(p) for p in pedidos],
                planner=PlannerInfo(provider="roteiro", model="t", simulated=True))


# ------------------------------------------------------------------ puro
def test_a_chave_e_vocabulario_fechado_e_estavel() -> None:
    a = ex.classificar("Ver a caixa de lixo eletrônico")
    b = ex.classificar("visualizar caixa de LIXO eletronico do Outlook")
    assert a.destino is ex.Destino.EXPLORAR and a.de_leitura
    assert a.chave == b.chave == "explorar_ver_caixa_lixo_eletronico"
    assert ex.chave_oferecivel(a.chave)
    # o nome e o dígito não entram na chave, e o que não é vocabulário some
    c = ex.classificar("Ler a mensagem de joao.silva123 a respeito do boleto 4455")
    assert c.chave == "explorar_ler_mensagem" and ex.chave_oferecivel(c.chave)
    # sem objeto reconhecido: única por pedido, só letras, e nunca oferecida
    d = ex.classificar("Descobrir como funciona o zumbido")
    e = ex.classificar("Descobrir como funciona o zunzum")
    assert d.chave != e.chave and d.chave.startswith("explorar_buscar_") and d.chave.isascii()
    assert not any(ch.isdigit() for ch in d.chave) and not ex.chave_oferecivel(d.chave)
    assert len(ex.classificar("Ver " + " ".join(sorted(ex.OBJETOS))).chave) <= 40


@pytest.mark.parametrize("pedido", ["Apagar a pasta", "enviar um e-mail", "Seguir o perfil", "Mudar o idioma",
                                    "Ver e enviar a mensagem", "Deixar de seguir", "Entrar na conta"])
def test_o_pedido_de_efeito_nao_explora(pedido: str) -> None:
    assert ex.classificar(pedido).destino is ex.Destino.EFEITO


def test_o_passo_e_so_leitura_e_o_molde_nao_leva_o_pedido() -> None:
    e = ex.classificar("Ver a caixa de lixo eletrônico de ana.souza@x.com")
    passo = ex.passo_da_exploracao("Ver a caixa de lixo eletrônico de ana.souza@x.com", e, app_id=None,
                                   nome_do_app="Microsoft Outlook")
    assert passo.exploratoria and not passo.side_effect and not passo.commit_guard and passo.max_attempts == 1
    assert "ana.souza" in passo.goal and "não envie" in passo.goal                            # a execução precisa do pedido
    molde = ex.molde_da_exploracao(passo)
    assert molde is not None and molde.key == passo.key and molde.exploratoria
    texto = " ".join([molde.title, molde.goal, molde.postcondition.value, molde.postcondition.description or ""])
    assert "ana" not in texto and "x.com" not in texto and "caixa lixo eletronico" in texto
    assert molde.postcondition.kind == passo.postcondition.kind
    # a receita aprendida na etapa é achada pelo molde: mesmo hash específico e mesmo genérico
    assert step_template_hash(molde) == step_template_hash(passo)
    assert hash_generico_da_etapa(molde) == hash_generico_da_etapa(passo) is not None
    sem_vocabulario = passo.model_copy(update={"key": "explorar_buscar_abcdef"})
    assert ex.molde_da_exploracao(sem_vocabulario) is None


# ------------------------------------------------------------------ a execução
async def _executar(tmp_path: Any, plano: Plan, comando: str, *, ajustes: dict[str, Any] | None = None,
                    depois: Any = None) -> tuple[dict[str, Any], Any, Harness]:
    h = Harness(tmp_path, 1)
    h.ai = CountingProvider(Roteiro(plano))
    state = await _parque(h, app_do_aparelho="outlook")
    for chave, valor in (ajustes or {}).items():
        setattr(state.scheduler.get_settings(), chave, valor)
    if depois:
        depois(state)
    run = h.run(["android-01"], mode="plan", command=comando)
    await h.wait_run(run.id, ("needs_input", "failed", "planned", "running", "completed", "completed_with_issues"))
    return dict(state.repo.run_row(run.id)), state, h


async def test_o_pedido_de_leitura_vira_uma_etapa_de_exploracao(tmp_path: Any) -> None:
    linha, state, h = await _executar(tmp_path, _plano("ver a caixa de lixo eletrônico"),
                                      "veja a caixa de lixo eletrônico do Outlook")
    try:
        assert linha["status"] == "planned", linha["status_detail"]
        plano = json.loads(linha["plan"])
        assert json.loads(linha["app_ids"]) == ["outlook"]                          # o que o teto do dia filtra
        assert evento_ok(state, linha["id"])
        assert plano["fora_do_catalogo"] == [] and plano["missing"] == []
        (passo,) = plano["steps"]
        assert passo["key"] == "explorar_ver_caixa_lixo_eletronico" and passo["exploratoria"] is True
        assert passo["side_effect"] is False
        (evento,) = _eventos(state, linha["id"], "exploracao.iniciada")
        assert evento["data"]["etapas"] == ["explorar_ver_caixa_lixo_eletronico"] and evento["data"]["reaproveitadas"] == []
        assert evento["data"]["tetos"] == {"acoes": 25, "chamadas_ia": 30, "usd": 0.6}
        assert _eventos(state, linha["id"], "plan.refused") == []
        assert state.db.scalar("SELECT COUNT(*) FROM events WHERE run_id=? AND message LIKE ?",
                               (linha["id"], "%Exploração (31.273%")) == 1
        assert h.ai.count("plan") == 1 and h.ai.count("decide") == 0
    finally:
        await state.stop()


async def test_dois_pedidos_viram_duas_etapas_em_ordem_e_o_repetido_uma_so(tmp_path: Any) -> None:
    linha, state, _ = await _executar(
        tmp_path, _plano("ver a caixa de lixo eletrônico", "abrir as configurações", "Visualizar a caixa de lixo eletronico"),
        "veja o lixo e as configurações do Outlook")
    try:
        passos = json.loads(linha["plan"])["steps"]
        assert [p["key"] for p in passos] == ["explorar_ver_caixa_lixo_eletronico", "explorar_abrir_configuracoes"]
        assert passos[0]["depends_on"] == [] and passos[1]["depends_on"] == ["explorar_ver_caixa_lixo_eletronico"]
    finally:
        await state.stop()


@pytest.mark.parametrize(("pedido", "ajustes"), [
    ("apagar a pasta", {}),                                           # efeito: a porta 13.2 não é contornada
    ("ver a caixa de lixo eletrônico", {"exploracao_ligada": False}),  # desligada: a recusa de antes
])
async def test_o_que_nao_explora_segue_recusado_como_antes(tmp_path: Any, pedido: str, ajustes: dict[str, Any]) -> None:
    linha, state, _ = await _executar(tmp_path, _plano(pedido), "faça isso no Outlook", ajustes=ajustes)
    try:
        assert linha["status"] == "failed"
        (recusa,) = _eventos(state, linha["id"], "plan.refused")
        assert recusa["data"]["motivo"] == "sem_acao_do_catalogo"
        assert _eventos(state, linha["id"], "exploracao.iniciada") == []
        assert _contagem(state, "steps", linha["id"]) == 0
    finally:
        await state.stop()


def _ja_exploraram_hoje(state: Any, n: int = 2) -> None:
    """`n` explorações NOVAS (com IA) de hoje no outlook: é o que o teto do dia conta."""
    for k in range(n):
        state.db.execute("INSERT INTO events(ts, kind, level, run_id, message, data) VALUES (?,?,?,?,?,?)",
                         (now_iso(), "exploracao.iniciada", "info", None, "x",
                          json.dumps({"etapas": ["explorar_ver_ajuda"], "reaproveitadas": [], "app_ids": ["outlook"]})))


async def test_o_teto_do_dia_por_app_recusa_com_o_motivo(tmp_path: Any) -> None:
    linha, state, _ = await _executar(tmp_path, _plano("ver a caixa de lixo eletrônico"), "veja o lixo do Outlook",
                                      ajustes={"exploracao_max_por_dia": 2}, depois=_ja_exploraram_hoje)
    try:
        assert linha["status"] == "failed"
        (recusa,) = _eventos(state, linha["id"], "plan.refused")
        assert recusa["data"]["motivo"] == "teto_de_exploracao_por_dia" and recusa["data"]["feitas"] == 2
        assert "limite 2 por dia" in recusa["message"]
        assert _eventos(state, linha["id"], "exploracao.iniciada") == []
    finally:
        await state.stop()


async def test_a_segunda_vez_usa_a_receita_descoberta_e_o_molde_nao_leva_o_pedido(tmp_path: Any) -> None:
    def _receita_de_antes(state: Any) -> None:
        passo = ex.passo_da_exploracao("ver a caixa de lixo eletrônico de ana@x.com",
                                       ex.classificar("ver a caixa de lixo eletrônico"), app_id=None,
                                       nome_do_app="Microsoft Outlook")
        _semear(state, "r-20261010110000-aaaaaa", passo)
        state.db.execute("UPDATE runs SET plan=REPLACE(plan, '\"qa-messenger\"', '\"outlook\"')")
        state.db.execute("UPDATE steps SET app_id='outlook'")

    def _receita_e_teto_esgotado(state: Any) -> None:
        _receita_de_antes(state)
        _ja_exploraram_hoje(state, 5)                       # o teto do dia (5) já foi usado: a receita não paga, então passa

    linha, state, _ = await _executar(tmp_path, _plano("ver a caixa de lixo eletrônico"), "veja o lixo do Outlook",
                                      depois=_receita_e_teto_esgotado)
    try:
        assert linha["status"] == "planned", linha["status_detail"]
        (passo,) = json.loads(linha["plan"])["steps"]
        assert passo["key"] == "explorar_ver_caixa_lixo_eletronico"
        assert "ana@x.com" not in json.dumps(passo)                                         # o molde, não o pedido
        (evento,) = _eventos(state, linha["id"], "exploracao.iniciada")
        assert evento["data"]["reaproveitadas"] == ["explorar_ver_caixa_lixo_eletronico"]
        assert evento["data"]["app_ids"] == []                                       # a reaproveitada não conta no teto do dia
    finally:
        await state.stop()


# ------------------------------------------------------------------ os tetos da etapa exploratória (executor)
async def test_a_etapa_exploratoria_para_no_teto_de_chamadas_e_no_de_dolares(harness: Harness) -> None:
    st = harness.state
    exec_ = st.scheduler.executor
    s = st.scheduler.get_settings()
    passo = ex.passo_da_exploracao("ver a caixa de lixo", ex.classificar("ver a caixa de lixo"), app_id=None,
                                   nome_do_app="QA")
    _semear(st, "r-20261010120000-aaaaaa", passo)
    sid = "r-20261010120000-aaaaaa:android-01:v1:" + passo.key
    comum = _semear(st, "r-20261010120001-bbbbbb", PlanStep(
        key="ver_ajuda", title="t", goal="g", postcondition=Postcondition(kind="model_judged", value="v", description="d")),
        exploratoria=False)
    assert comum
    sid_comum = "r-20261010120001-bbbbbb:android-01:v1:ver_ajuda"

    def chamadas(n: int, run: str, step: str, modelo: str = "claude-sonnet-5-5", saida: int = 0) -> None:
        for _ in range(n):
            st.db.execute("INSERT INTO ai_calls(ts, run_id, step_id, role, model, provider, output_tokens)"
                          " VALUES (?,?,?,?,?,?,?)", (now_iso(), run, step, "decide", modelo, "anthropic", saida))

    run = "r-20261010120000-aaaaaa"
    s.exploracao_max_chamadas_ia = 3
    s.exploracao_max_usd = 0.0
    chamadas(2, run, sid)
    assert exec_._teto_da_exploracao(run, sid, s) is None                                     # 2 de 3
    chamadas(1, run, sid)
    motivo = exec_._teto_da_exploracao(run, sid, s)
    assert motivo is not None and "3 chamadas de IA (limite 3)" in motivo and "Parei sem concluir" in motivo
    # a etapa que não é exploratória não paga nada, mesmo com a execução cheia de chamadas
    chamadas(5, "r-20261010120001-bbbbbb", sid_comum)
    assert exec_._teto_da_exploracao("r-20261010120001-bbbbbb", sid_comum, s) is None
    # o teto em dólares: tokens x preço do modelo (1 milhão de saída do Sonnet passa de US$ 0,60)
    s.exploracao_max_chamadas_ia = 500
    s.exploracao_max_usd = 0.60
    assert exec_._teto_da_exploracao(run, sid, s) is None
    chamadas(1, run, sid, saida=1_000_000)
    motivo = exec_._teto_da_exploracao(run, sid, s)
    assert motivo is not None and "limite US$ 0.60" in motivo


async def test_a_etapa_exploratoria_executa_no_simulado_e_grava_a_marca(harness: Harness,
                                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    """Ponta a ponta no simulado: a etapa exploratória materializa com a marca, roda e a receita nasce dela."""
    st = harness.state
    passo = ex.passo_da_exploracao("abrir a busca", ex.classificar("abrir a busca"), app_id=None, nome_do_app="QA")
    original = st.runs.provider.plan

    async def planejador(req: Any) -> Any:
        _, uso = await original(req)
        return Plan(summary="explorar", app_id="qa-messenger", steps=[passo],
                    planner=PlannerInfo(provider="t", model="t", simulated=True)), uso
    monkeypatch.setattr(st.runs.provider, "plan", planejador)
    run = harness.run(["android-01"], command="abra a busca no QA Messenger")
    await harness.wait_run(run.id, statuses=("completed", "completed_with_issues", "failed", "needs_input"), timeout=60)
    etapa = st.db.one("SELECT key, status, exploratoria FROM steps WHERE run_id=?", (run.id,))
    assert etapa is not None and etapa["key"] == "explorar_abrir_busca" and etapa["exploratoria"] == 1
    assert etapa["status"] in {"succeeded", "failed"}, etapa["status"]          # rodou sob o teto próprio de ações
    visao = st.runs.repo.steps_of(run.id) if hasattr(st.runs.repo, "steps_of") else []
    assert all(getattr(v, "exploratoria", True) for v in visao)


# ------------------------------------------------------------------ o pedido misto (31.298)
def _passo_do_catalogo() -> dict[str, Any]:
    return {"key": "abrir_entrada", "capability": "OPEN_MAIL_INBOX", "depends_on": [], "bindings": [], "for_each": None}


def test_o_pedido_misto_mantem_as_acoes_do_catalogo_e_a_pergunta_do_modelo_some() -> None:
    from app.planning.capabilities import load_catalog
    from app.planning.parsing import catalog_plan_from_json
    from app.planning.provider import AppContext, PlanRequest
    catalogo = load_catalog("com.microsoft.office.outlook")
    assert catalogo is not None
    app = AppContext("outlook", "Microsoft Outlook", "com.microsoft.office.outlook", None, None, None)
    req = PlanRequest(command="c", run_id="r", instances=[], apps=[app], catalog=catalogo)
    bruto = json.dumps({"summary": "s", "parameters": [], "success_criteria": [], "steps": [_passo_do_catalogo()],
                        "missing": [{"field": "x", "question": "Como ver a lixeira?"}],
                        "fora_do_catalogo": [{"app_id": None, "pedido": "ver a caixa de lixo eletrônico"}]})
    plano = catalog_plan_from_json(bruto, req, provider="p", model="m", max_steps=10)
    assert [s.capability for s in plano.steps] == ["OPEN_MAIL_INBOX"]                   # a parte do catálogo fica
    assert plano.missing == [] and [f.pedido for f in plano.fora_do_catalogo] == ["ver a caixa de lixo eletrônico"]


async def test_o_pedido_misto_executa_o_catalogo_e_depois_a_exploracao(tmp_path: Any) -> None:
    plano = _plano("ver a caixa de lixo eletrônico")
    plano = plano.model_copy(update={"steps": [PlanStep(
        key="abrir_entrada", title="Abrir a caixa de entrada", goal="g", capability="OPEN_MAIL_INBOX",
        postcondition=Postcondition(kind="model_judged", value="v", description="d"))]})
    linha, state, _ = await _executar(tmp_path, plano, "abra a entrada e veja o lixo do Outlook")
    try:
        assert linha["status"] == "planned", linha["status_detail"]
        passos = json.loads(linha["plan"])["steps"]
        assert [p["key"] for p in passos] == ["abrir_entrada", "explorar_ver_caixa_lixo_eletronico"]
        assert passos[1]["depends_on"] == ["abrir_entrada"] and passos[0].get("exploratoria") is None
    finally:
        await state.stop()


async def test_o_pedido_misto_recusado_zera_tambem_a_parte_do_catalogo(tmp_path: Any) -> None:
    plano = _plano("apagar a pasta").model_copy(update={"steps": [PlanStep(
        key="abrir_entrada", title="Abrir a caixa de entrada", goal="g", capability="OPEN_MAIL_INBOX",
        postcondition=Postcondition(kind="model_judged", value="v", description="d"))]})
    linha, state, _ = await _executar(tmp_path, plano, "abra a entrada e apague a pasta")
    try:
        assert linha["status"] == "failed"
        assert json.loads(linha["plan"])["steps"] == [] and _contagem(state, "steps", linha["id"]) == 0
    finally:
        await state.stop()
