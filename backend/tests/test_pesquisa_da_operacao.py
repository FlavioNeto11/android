"""prova30 A2 (31.158): a pesquisa externa da OPERAÇÃO, uma vez, na memória dela, com fonte, confiança e frescor.

Cobre:
    * a consolidação por código: só conta a URL que a busca trouxe; dois domínios = confirmado, um = hipótese, nenhum =
      descartado;
    * o serviço: desligada, sem assunto ou sem lacuna não pesquisa; pesquisa grava fontes (observação `url` + entrada
      `fonte`) e fatos (`descoberta`, origem `pesquisa`, evidência = as observações, frescor); não repete; o teto POR
      OPERAÇÃO barra; a falha deixa a marca de espera, sem 30 tentativas;
    * o custo: a linha das buscas em `ai_calls` (`model='web_search'`, `usd` declarado) e a soma por operação;
    * o provedor Anthropic: a ferramenta do servidor vai no pedido, e os resultados, as citações e as buscas são lidos
      da resposta (resposta falsa, sem rede);
    * a porta de escrita (harness, provedor simulado): duas execuções da mesma operação, UMA pesquisa, e os fatos
      chegam ao texto da segunda com a hipótese marcada.

Nível de prova: `simulated` (banco de teste, provedor simulado e respostas falsas; nenhuma chamada paga). A 124 (a
operação, da Jev) é imitada só no que esta parte lê: `runs.operacao_id` e `operacoes(id, assunto, fontes)`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app import gates as gates_mod
from app.config import PesquisaCfg
from app.db import Database
from app.modules.pedidos.domain import conhecimento_da_operacao as dominio
from app.modules.pedidos.infrastructure.pesquisa_da_operacao import CHAVE_DO_ESTADO, PesquisaDaOperacao
from app.planning.anthropic_provider import AnthropicProvider
from app.planning.capabilities import capability_of
from app.planning.pesquisa import Citacao, PesquisaBruta, PesquisaRequest, Resultado, fatos_consolidados
from app.planning.provider import Usage

from .apoio_politica import IG
from .test_conhecimento_da_operacao import LEGENDA, _com_operacao
from .test_pedidos_modelo import _banco, _run
from .test_porta_do_plano import _plano

A, B, C = "https://loja.exemplo.com/outono", "https://www.jornal.exemplo.org/materia", "https://loja.exemplo.com/sobre"
PRECOS = {"claude-sonnet-5": [2.0, 0.2, 2.5, 10.0]}


def _operacoes(db: Database, *, assunto: str | None = "coleção de outono da loja", fontes: str = "[]") -> None:
    """O que a 124 da Jev põe no banco e esta parte lê: `operacoes.assunto` e `operacoes.fontes`."""
    _com_operacao(db)                                # a operação (124, ou a imitação dela) sem execução ainda
    db.execute("UPDATE operacoes SET assunto=?, fontes=? WHERE id='op-1'", (assunto, fontes))


def _bruta(*fatos: tuple[str, list[str]], buscas: int = 2) -> PesquisaBruta:
    texto = "Pesquisei. " + json.dumps({"fatos": [{"texto": t, "fontes": u} for t, u in fatos], "lacunas": ["preço"]})
    return PesquisaBruta(texto, (Resultado(A, "Loja", "2 dias"), Resultado(B, "Jornal")), (Citacao(A, "tecido reciclado"),),
                         buscas)


@pytest.fixture
def banco(tmp_path: Path) -> Database:
    db = _banco(tmp_path)
    db.migrate()
    _run(db, "r-a")
    _com_operacao(db, "r-a")
    return db


# ===================================================================== 1. consolidação por código
def test_so_conta_a_url_que_a_busca_trouxe_e_a_confianca_e_do_codigo() -> None:
    c = fatos_consolidados(_bruta(("tecido reciclado", [A, B]), ("costura à mão", [A, C]), ("inventado", ["https://x.y/z"])),
                           max_fatos=8)
    assert [(f.texto, f.confianca) for f in c.fatos] == [("tecido reciclado", "confirmado"), ("costura à mão", "hipotese")]
    assert c.descartados == 1 and c.lacunas == ("preço",)                   # C não veio da busca; x.y nunca existiu
    fonte_a = next(f for f in c.fontes if f.url == A)
    assert (fonte_a.titulo, fonte_a.trecho, fonte_a.idade) == ("Loja", "tecido reciclado", "2 dias")
    assert fatos_consolidados(PesquisaBruta("sem json"), max_fatos=8).fatos == ()


# ===================================================================== 2. serviço
async def test_pesquisa_uma_vez_com_fonte_confianca_e_frescor(banco: Database) -> None:
    _operacoes(banco, fontes=json.dumps([A, "javascript:alert(1)"]))
    servico = PesquisaDaOperacao(banco, PesquisaCfg(enabled=True), PRECOS)
    pedidos: list[PesquisaRequest] = []

    async def chamar(req: PesquisaRequest) -> PesquisaBruta:
        pedidos.append(req)
        return _bruta(("tecido reciclado", [A, B]), ("entrega em todo o Brasil", [A]))

    assert servico.lacuna("op-1")
    feito = await servico.pesquisar_se_preciso("op-1", run_id="r-a", contexto=LEGENDA, chamar=chamar)
    assert feito is not None and (feito.fatos, feito.confirmados, feito.fontes, feito.buscas) == (2, 1, 2, 2)
    (req,) = pedidos
    assert req.assunto == "coleção de outono da loja" and req.fontes_indicadas == (A,) and req.contexto == LEGENDA
    obs = {o["valor"]: o for o in servico.repo.observacoes_da_operacao("op-1") if o["tipo"] == "url"}
    assert set(obs) == {A, B} and obs[A]["fonte"] == "Loja" and obs[A]["trecho"] == "tecido reciclado"
    entradas = {e.chave: e for e in servico.repo.entradas_da_operacao("op-1")}
    fatos = {e.valor: e for e in entradas.values() if e.tipo == "descoberta"}
    assert fatos["tecido reciclado"].confianca == "confirmado" and fatos["entrega em todo o Brasil"].confianca == "hipotese"
    assert set(fatos["tecido reciclado"].evidencia) == {str(obs[A]["id"]), str(obs[B]["id"])}
    assert all(e.origem == "pesquisa" and e.frescor_ate for e in entradas.values())
    assert "acessado em" in next(e.valor for e in entradas.values() if e.tipo == "fonte")
    # O bloco que vai às personas: fato, hipótese marcada e fonte.
    bloco = dominio.bloco(entradas.values(), agora=banco.agora_iso())
    assert "[fato] pesquisa." in bloco and "[hipótese, não confirmada]" in bloco and "[fonte] fonte." in bloco
    assert CHAVE_DO_ESTADO not in bloco
    # A segunda execução da operação encontra os fatos: não pesquisa de novo.
    assert not servico.lacuna("op-1")
    assert await servico.pesquisar_se_preciso("op-1", run_id="r-a", contexto="", chamar=chamar) is None
    assert len(pedidos) == 1


async def test_desligada_sem_assunto_teto_e_falha_nao_repetem(banco: Database) -> None:
    chamadas: list[str] = []

    async def falha(req: PesquisaRequest) -> PesquisaBruta:
        chamadas.append(req.operacao_id)
        raise RuntimeError("rede")

    _operacoes(banco, assunto=None)
    ligada = PesquisaDaOperacao(banco, PesquisaCfg(enabled=True), PRECOS)
    assert await PesquisaDaOperacao(banco, PesquisaCfg(), PRECOS).pesquisar_se_preciso(
        "op-1", run_id="r-a", contexto="", chamar=falha) is None                       # desligada de fábrica
    assert await ligada.pesquisar_se_preciso("op-1", run_id="r-a", contexto="", chamar=falha) is None  # sem assunto
    banco.execute("UPDATE operacoes SET assunto='coleção de outono' WHERE id='op-1'")
    banco.execute("INSERT INTO ai_calls(ts, run_id, role, model, tier, input_tokens, cache_read, cache_write,"
                  " output_tokens, with_image, ms, ok, provider, usd, origem, ref) VALUES"
                  " ('2026-10-06T17:00:00Z','r-a','plan','web_search',0,0,0,0,0,0,0,1,'anthropic',0.30,'pesquisa','op-1')")
    assert ligada.gasto("op-1") == pytest.approx(0.30)
    assert await ligada.pesquisar_se_preciso("op-1", run_id="r-a", contexto="", chamar=falha) is None  # teto
    banco.execute("DELETE FROM ai_calls")
    assert await ligada.pesquisar_se_preciso("op-1", run_id="r-a", contexto="", chamar=falha) is None
    assert chamadas == ["op-1"] and not ligada.lacuna("op-1")                         # a falha marcou a espera
    assert await ligada.pesquisar_se_preciso("op-1", run_id="r-a", contexto="", chamar=falha) is None
    assert chamadas == ["op-1"]


# ===================================================================== 3. custo
def test_as_buscas_viram_linha_propria_de_custo(harness: Any) -> None:
    repo = harness.state.repo
    repo.db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, instance_ids, created_at)"
                    " VALUES ('r-c','k-c','x','execute','running','[]','2026-10-06T17:00:00Z')")
    repo.add_usage("r-c", None, Usage(calls=1, input_tokens=1000, output_tokens=200, role="plan", model="claude-sonnet-5",
                                      provider="anthropic", origem="pesquisa", ref="op-1", buscas=3, usd_das_buscas=0.03))
    linhas = {r["model"]: r for r in repo.db.query("SELECT model, usd, origem, ref FROM ai_calls WHERE run_id='r-c'")}
    assert linhas["web_search"]["usd"] == pytest.approx(0.03) and linhas["web_search"]["ref"] == "op-1"
    assert linhas["claude-sonnet-5"]["usd"] is None
    servico = PesquisaDaOperacao(repo.db, PesquisaCfg(enabled=True), PRECOS)
    assert servico.gasto("op-1") == pytest.approx(0.03 + (1000 * 2.0 + 200 * 10.0) / 1_000_000)


# ===================================================================== 4. provedor Anthropic (resposta falsa)
async def test_o_provedor_manda_a_ferramenta_e_le_resultados_citacoes_e_buscas(monkeypatch: Any) -> None:
    provedor = AnthropicProvider.__new__(AnthropicProvider)
    provedor.models = {"plan": "claude-sonnet-5"}
    provedor.cfg = SimpleNamespace(env=SimpleNamespace(ai_effort_planner="medium"))
    vistos: dict[str, Any] = {}
    resposta = SimpleNamespace(stop_reason="end_turn", model="claude-sonnet-5", content=[
        SimpleNamespace(type="text", text="vou buscar", citations=None),
        SimpleNamespace(type="server_tool_use"),
        SimpleNamespace(type="web_search_tool_result", content=[
            SimpleNamespace(type="web_search_result", url=A, title="Loja", page_age="2 dias")]),
        SimpleNamespace(type="text", text='{"fatos": [{"texto": "x", ', citations=[
            SimpleNamespace(type="web_search_result_location", url=A, cited_text="tecido reciclado", title="Loja")]),
        SimpleNamespace(type="text", text='"fontes": ["%s"]}]}' % A, citations=None)],
        usage=SimpleNamespace(server_tool_use=SimpleNamespace(web_search_requests=2)))

    async def criar(**kw: Any) -> Any:
        vistos.update(kw)
        return resposta, Usage(calls=1, role="plan", model="claude-sonnet-5")

    monkeypatch.setattr(provedor, "_create", criar)
    bruta, usage = await provedor.pesquisar(PesquisaRequest(operacao_id="op-1", assunto="outono", max_buscas=2))
    assert vistos["ferramentas"] == [{"type": "web_search_20250305", "name": "web_search", "max_uses": 2}]
    assert "outono" in vistos["content"][0]["text"] and vistos["cachear"] is False
    assert bruta.texto.startswith('{"fatos"') and "vou buscar" not in bruta.texto      # só o texto depois da busca
    assert bruta.resultados == (Resultado(A, "Loja", "2 dias"),) and bruta.citacoes[0].trecho == "tecido reciclado"
    assert (bruta.buscas, usage.buscas, usage.usd_das_buscas) == (2, 2, 0.02)


# ===================================================================== 5. porta de escrita (harness)
async def test_duas_execucoes_uma_pesquisa_e_os_fatos_chegam_ao_texto(harness: Any, monkeypatch: Any) -> None:
    state = harness.state
    briefing = {"content": "comente o lançamento", "caption_contains": "outono", "post_author": "@loja.nossa"}
    _plano(state, [{"key": "comentar", "cap": "CREATE_COMMENT", "bindings": briefing}], run_id="run-a")
    _plano(state, [{"key": "comentar", "cap": "CREATE_COMMENT", "bindings": briefing}], aparelho="android-02",
           run_id="run-b")
    _com_operacao(state.db, "run-a", "run-b")
    _operacoes(state.db)
    monkeypatch.setattr(state.cfg.file.ai, "pesquisa", PesquisaCfg(enabled=True))
    # O invólucro de contagem da harness não repassa a pesquisa; no backend, quem a tem é o hub (`AIRouter.pesquisar`).
    monkeypatch.setattr(state.provider, "pesquisar", state.provider.inner.pesquisar, raising=False)
    monkeypatch.setattr(gates_mod, "screen_reader_of",
                        lambda _p: SimpleNamespace(visible_content=lambda arvore: arvore.texto))

    async def ler_tela(_rt: Any, _pacote: Any) -> Any:
        return SimpleNamespace(sensitive=False, texto=LEGENDA, packages={IG})

    monkeypatch.setattr(state.portoes, "_ler_tela", ler_tela)
    pedidos: list[dict[str, Any]] = []

    async def draft_response(_pid: str, **kw: Any) -> Any:
        pedidos.append(kw)
        return SimpleNamespace(content=f"texto {len(pedidos)}", refused=False, refusal_reason=None, rationale="r",
                               memory_candidates=[]), None

    monkeypatch.setattr(state.social, "draft_response", draft_response)
    monkeypatch.setitem(sys.modules, "app.modules.operacoes.infrastructure.estagios",
                        SimpleNamespace(registrar_estagio=lambda *_a: None))
    cap = capability_of(IG, "CREATE_COMMENT")
    # 31.169: a pesquisa roda na criação da operação, antes de qualquer alvo; os alvos só reusam
    tarefa = state.portoes.agendar_pesquisa_da_operacao("op-1")
    assert tarefa is not None
    await tarefa
    assert state.db.scalar("SELECT COUNT(*) FROM ai_calls WHERE model='web_search'") == 1
    assert state.db.scalar("SELECT run_id FROM ai_calls WHERE model='web_search'") == "run-a"     # o 1º alvo paga
    for run, aparelho in (("run-a", "android-01"), ("run-b", "android-02")):
        obj = state.db.one("SELECT * FROM objectives WHERE run_id=?", (run,))
        etapa = state.db.one("SELECT * FROM steps WHERE id=?", (f"{run}:{aparelho}:v1:comentar",))
        assert await state.portoes._draft_gate(obj, etapa, cap, obj["profile_id"], pacote=IG) is None  # noqa: SLF001

    assert state.db.scalar("SELECT COUNT(*) FROM ai_calls WHERE model='web_search'") == 1   # UMA pesquisa
    await state.portoes.agendar_pesquisa_da_operacao("op-1")                  # a repetição: sem lacuna, não pesquisa
    assert state.db.scalar("SELECT COUNT(*) FROM ai_calls WHERE model='web_search'") == 1
    for kw in pedidos:
        assert "[fato] pesquisa." in kw["fatos_da_operacao"]
        assert "[hipótese, não confirmada]" in kw["fatos_da_operacao"] and "[fonte] fonte." in kw["fatos_da_operacao"]
        assert LEGENDA not in kw["fatos_da_operacao"]                  # a leitura já vai na tela de quem a viu
        assert kw["assunto_da_operacao"] == "coleção de outono da loja"   # o assunto vai junto da intenção
    meta = json.loads(state.db.scalar("SELECT draft_meta FROM steps WHERE id='run-a:android-01:v1:comentar'"))
    assert meta["fatos_da_operacao"]["assunto"] is True
    assert state.db.scalar("SELECT COUNT(*) FROM memory_items") == 0


async def test_os_alvos_nao_pesquisam_so_reusam(harness: Any, monkeypatch: Any) -> None:
    """31.169: sem a pesquisa da criação (desligada naquela hora, por exemplo), a porta de escrita não pesquisa: o texto sai
    sem os fatos da pesquisa, e nenhuma chamada paga acontece no meio dos alvos."""
    state = harness.state
    briefing = {"content": "comente o lançamento", "caption_contains": "outono", "post_author": "@loja.nossa"}
    _plano(state, [{"key": "comentar", "cap": "CREATE_COMMENT", "bindings": briefing}], run_id="run-a")
    _com_operacao(state.db, "run-a")
    _operacoes(state.db)
    monkeypatch.setattr(state.cfg.file.ai, "pesquisa", PesquisaCfg(enabled=True))
    monkeypatch.setattr(state.provider, "pesquisar", state.provider.inner.pesquisar, raising=False)
    monkeypatch.setattr(gates_mod, "screen_reader_of",
                        lambda _p: SimpleNamespace(visible_content=lambda arvore: arvore.texto))

    async def ler_tela(_rt: Any, _pacote: Any) -> Any:
        return SimpleNamespace(sensitive=False, texto=LEGENDA, packages={IG})

    async def draft_response(_pid: str, **kw: Any) -> Any:
        return SimpleNamespace(content="texto", refused=False, refusal_reason=None, rationale="r",
                               memory_candidates=[]), None

    monkeypatch.setattr(state.portoes, "_ler_tela", ler_tela)
    monkeypatch.setattr(state.social, "draft_response", draft_response)
    monkeypatch.setitem(sys.modules, "app.modules.operacoes.infrastructure.estagios",
                        SimpleNamespace(registrar_estagio=lambda *_a: None))
    obj = state.db.one("SELECT * FROM objectives WHERE run_id='run-a'")
    etapa = state.db.one("SELECT * FROM steps WHERE id='run-a:android-01:v1:comentar'")
    assert await state.portoes._draft_gate(obj, etapa, capability_of(IG, "CREATE_COMMENT"), obj["profile_id"],  # noqa: SLF001
                                           pacote=IG) is None
    assert state.db.scalar("SELECT COUNT(*) FROM ai_calls WHERE model='web_search'") == 0


async def test_criar_a_operacao_pela_rota_pesquisa_uma_vez_antes_dos_alvos(harness: Any, monkeypatch: Any) -> None:
    """31.169: `POST /api/operacoes` agenda a pesquisa; ela roda uma vez, paga na execução do 1º alvo (é o que o
    `custo.pesquisa_usd` da operação soma), e a repetição idempotente não pesquisa de novo."""
    import asyncio

    import httpx

    from app.main import create_app

    from .conftest import COMMAND
    from .test_operacoes import APP, _conta, _persona
    st = harness.state
    monkeypatch.setattr(st.cfg.file.ai, "pesquisa", PesquisaCfg(enabled=True))
    monkeypatch.setattr(st.provider, "pesquisar", st.provider.inner.pesquisar, raising=False)
    pid = _persona(harness, "Iara", "android-01")
    _conta(harness, pid, "qa-user-21", sessao_em="android-01")
    app = create_app(harness.cfg, state=st)
    app.state.poc = st
    corpo = {"command": COMMAND, "app_id": APP, "alvos": [{"profile_id": pid}], "idempotency_key": "teste-op-pesq1",
             "max_usd": 0.5, "assunto": "o lançamento da coleção"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/operacoes", json=corpo)
        assert r.status_code == 201, r.text
        op = r.json()
        await asyncio.gather(*st.portoes._tarefas_da_pesquisa)                         # noqa: SLF001
        run_do_alvo = op["alvos"][0]["run_id"]
        assert [x["run_id"] for x in st.db.query("SELECT run_id FROM ai_calls WHERE model='web_search'")] == [run_do_alvo]
        assert st.db.scalar("SELECT COUNT(*) FROM pedido_memoria WHERE operacao_id=? AND chave LIKE 'pesquisa.%'",
                            (op["id"],)) > 0
        assert (await c.post("/api/operacoes", json=corpo)).status_code == 201         # a mesma chave: a mesma operação
        await asyncio.gather(*st.portoes._tarefas_da_pesquisa)                         # noqa: SLF001
        assert st.db.scalar("SELECT COUNT(*) FROM ai_calls WHERE model='web_search'") == 1
