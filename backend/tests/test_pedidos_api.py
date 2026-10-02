"""Item 28.9 — a API de pedidos (`/api/pedidos`), de ponta a ponta com `TestClient`, SQLite e relógio falso.

Prova `simulated` (`arquivo::teste`): provedor de IA simulado, aparelhos falsos, laço de pedidos de verdade com o
planejamento desligado. NADA aqui prova o ambiente real (o 28.12 liga o laço no central). Cobre: prévia sem efeito, piso
de frequência, criação com selo e idempotência (id determinístico), listagem e detalhe, ações com `sem_mudanca`, edição
com compare-and-set e selo, os eventos `pedido.*`, o laço despachando um pedido criado pela API (o formato dos alvos),
os avisos como eventos, a fatia `pedidos` do snapshot com a pendência agrupada sem contar em dobro, e `RunCreate` público
recusando campo de pedido.
"""
from __future__ import annotations

import json

import pytest
import pytest_asyncio
from pydantic import ValidationError
from starlette.testclient import TestClient

from app.config import PedidosCfg
from app.main import create_app
from app.models import RunCreate
from app.modules.pedidos.domain.chave import TAMANHO_MAXIMO_DO_ID
from app.modules.pedidos.domain.previa import id_do_pedido
from app.modules.pedidos.infrastructure.laco import LacoDePedidos
from app.taskqueue.travas import Lideranca

from .conftest import COMMAND, Harness
from .test_travas import Relogio

OBJETIVO = "Pesquise o preço do café na loja de teste e anote o valor encontrado."
DIARIO = {"tipo": "recorrencia", "spec": {"dtstart": "2026-10-03T08:00:00", "rrule": "FREQ=DAILY;BYHOUR=8"}}


def _corpo(**kw) -> dict:
    base = {"objetivo": OBJETIVO, "alvos": {"instance_ids": ["android-01"]}, "gatilhos": [DIARIO]}
    base.update(kw)
    return base


@pytest_asyncio.fixture
async def h(harness: Harness, monkeypatch) -> Harness:
    monkeypatch.setattr(harness.state.runs, "_spawn_planning", lambda run_id: None)
    harness.state.pedidos.relogio = Relogio()
    return harness


def _cliente(h: Harness) -> TestClient:
    app = create_app(h.cfg, state=h.state)
    app.state.poc = h.state          # sem `with TestClient`, o lifespan não roda: o estado entra à mão
    return TestClient(app, client=("127.0.0.1", 123))


def _criar(c: TestClient, chave: str = "chave-de-teste-0001", **kw):
    previa = c.post("/api/pedidos/previa", json=_corpo(**kw)).json()
    assert previa["valido"], previa["bloqueios"]
    return c.post("/api/pedidos", json={**_corpo(**kw), "idempotency_key": chave, "confirmacao": previa["confirmacao"]})


def _eventos(h: Harness, kind: str) -> list[dict]:
    return h.state.db.query("SELECT * FROM events WHERE kind=? ORDER BY id", (kind,))


# =============================================================================================== prévia
async def test_previa_valida_nao_grava_nada_e_devolve_selo_e_datas(h: Harness) -> None:
    c = _cliente(h)
    r = c.post("/api/pedidos/previa", json=_corpo())
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["valido"] and p["bloqueios"] == [] and p["confirmacao"].startswith("sha256:")
    assert len(p["proximas"]) == 5 and p["intervalo_minimo_s"] == 86_400
    assert p["alvos"]["targets"][0]["instance_id"] == "android-01"
    assert p["custo"]["base"] == "sem_base" and p["custo"]["por_mes_usd"] is None, "nenhum número inventado"
    assert p["autonomia"]["teto"] == "observar" and p["autonomia"]["exige_aprovacao"] == []
    db = h.state.db
    assert db.scalar("SELECT COUNT(*) FROM pedidos") == 0 and db.scalar("SELECT COUNT(*) FROM pedido_gatilhos") == 0
    assert db.scalar("SELECT COUNT(*) FROM events WHERE kind LIKE 'pedido.%'") == 0
    assert c.post("/api/pedidos/previa", json=_corpo()).json()["confirmacao"] == p["confirmacao"], "selo estável"
    outro = c.post("/api/pedidos/previa", json=_corpo(autonomia="preparar")).json()["confirmacao"]
    assert outro != p["confirmacao"], "qualquer campo selado muda o selo"


async def test_previa_lista_bloqueios_e_nao_da_selo(h: Harness) -> None:
    c = _cliente(h)
    rapido = {"tipo": "recorrencia", "spec": {"dtstart": "2026-10-03T08:00:00", "rrule": "FREQ=HOURLY;BYMINUTE=0,5"}}
    p = c.post("/api/pedidos/previa", json=_corpo(gatilhos=[rapido])).json()
    assert not p["valido"] and p["confirmacao"] is None
    [b] = p["bloqueios"]
    assert b["codigo"] == "frequencia_abaixo_do_piso" and "piso_s=900" in b["mensagem"] and "observado_s=300" in b["mensagem"]
    com_efeito = c.post("/api/pedidos/previa", json=_corpo(autonomia="agir", gatilhos=[{
        "tipo": "recorrencia", "spec": {"dtstart": "2026-10-03T08:00:00", "rrule": "FREQ=HOURLY;BYMINUTE=0,30"}}])).json()
    assert [x["codigo"] for x in com_efeito["bloqueios"]] == ["frequencia_abaixo_do_piso"], "agir: piso de 1 h"
    ruins = c.post("/api/pedidos/previa", json=_corpo(
        fuso="Marte/Olimpo", sobreposicao="permitir_todas", autonomia="preparar", max_ocorrencias=0,
        gatilhos=[{"tipo": "evento", "spec": {}}, {"tipo": "recorrencia", "spec": {"dtstart": "x", "rrule": "FREQ=NUNCA"}}])).json()
    codigos = {x["codigo"] for x in ruins["bloqueios"]}
    assert {"fuso_desconhecido", "sobreposicao_incompativel", "limite_invalido"} <= codigos
    assert c.post("/api/pedidos/previa", json=_corpo(gatilhos=[{"tipo": "evento", "spec": {}}])).json()["bloqueios"][0][
        "codigo"] == "gatilho_nao_suportado"


async def test_previa_recusa_credencial_no_objetivo_sem_eco(h: Harness) -> None:
    r = _cliente(h).post("/api/pedidos/previa", json=_corpo(objetivo="Entre no app. Senha: Hunter2abc!"))
    assert r.status_code == 409 and r.json()["detail"]["code"] == "credencial_no_comando"
    assert "Hunter2" not in r.text


async def test_corpo_malformado_e_campo_extra_sao_422(h: Harness) -> None:
    c = _cliente(h)
    for extra in ({"estado": "ativo"}, {"pai_id": "x"}, {"criado_por": "eu"}):
        assert c.post("/api/pedidos/previa", json=_corpo(**extra)).status_code == 422
    assert c.post("/api/pedidos/previa", json=_corpo(gatilhos=[])).status_code == 422


# =============================================================================================== criação
async def test_criacao_com_selo_nasce_ativa_e_repetir_devolve_o_mesmo_pedido(h: Harness) -> None:
    c = _cliente(h)
    r = _criar(c)
    assert r.status_code == 201
    p = r.json()
    assert p["estado"] == "ativo" and p["versao"] == 1 and p["id"] == id_do_pedido("chave-de-teste-0001")
    assert len(p["id"]) <= TAMANHO_MAXIMO_DO_ID
    assert p["alvos"]["targets"][0]["instance_id"] == "android-01" and p["alvos"]["device_policy"] == "one"
    assert p["acoes_permitidas"] == ["editar", "pausar", "cancelar"]
    assert p["gatilhos_resumo"][0]["descricao"].startswith("Todo dia às 08:00")
    repetida = _criar(c)
    assert repetida.status_code == 200 and repetida.json()["deduplicated"] is True and repetida.json()["id"] == p["id"]
    assert h.state.db.scalar("SELECT COUNT(*) FROM pedidos") == 1
    assert h.state.db.scalar("SELECT COUNT(*) FROM pedido_gatilhos") == 1, "repetir não duplica o gatilho"
    conflito = c.post("/api/pedidos", json={**_corpo(objetivo="Outro objetivo bem diferente do primeiro."),
                                            "idempotency_key": "chave-de-teste-0001"})
    assert conflito.status_code == 409 and conflito.json()["detail"]["code"] == "idempotency_conflict"


async def test_criacao_sem_selo_fica_rascunho_e_ativar_confere_o_selo(h: Harness) -> None:
    c = _cliente(h)
    r = c.post("/api/pedidos", json={**_corpo(), "idempotency_key": "chave-rascunho-01", "titulo": "Meu café"})
    p = r.json()
    assert r.status_code == 201 and p["estado"] == "rascunho" and p["titulo"] == "Meu café"
    assert "ativar" in p["acoes_permitidas"]
    errada = c.post(f"/api/pedidos/{p['id']}/ativar", json={"confirmacao": "sha256:errado"})
    assert errada.status_code == 409 and errada.json()["detail"]["code"] == "previa_desatualizada"
    selo = c.post("/api/pedidos/previa", json=_corpo()).json()["confirmacao"]
    ativa = c.post(f"/api/pedidos/{p['id']}/ativar", json={"confirmacao": selo})
    assert ativa.status_code == 200 and ativa.json()["pedido"]["estado"] == "ativo" and not ativa.json()["sem_mudanca"]
    de_novo = c.post(f"/api/pedidos/{p['id']}/ativar", json={"confirmacao": selo})
    assert de_novo.json()["sem_mudanca"] is True


async def test_criacao_com_selo_velho_e_previa_desatualizada_e_piso_e_422(h: Harness) -> None:
    c = _cliente(h)
    r = c.post("/api/pedidos", json={**_corpo(), "idempotency_key": "chave-selo-velho", "confirmacao": "sha256:0"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "previa_desatualizada"
    assert h.state.db.scalar("SELECT COUNT(*) FROM pedidos") == 0
    rapido = {"tipo": "recorrencia", "spec": {"dtstart": "2026-10-03T08:00:00", "rrule": "FREQ=HOURLY;BYMINUTE=0,5"}}
    r2 = c.post("/api/pedidos", json={**_corpo(gatilhos=[rapido]), "idempotency_key": "chave-rapida-001"})
    assert r2.status_code == 422 and r2.json()["detail"]["code"] == "frequencia_abaixo_do_piso"
    r3 = c.post("/api/pedidos", json={**_corpo(), "idempotency_key": "x"})
    assert r3.status_code == 422, "a chave tem de 8 a 100 caracteres"
    r4 = c.post("/api/pedidos", json={**_corpo(alvos={"instance_ids": ["nao-existe"]}), "idempotency_key": "chave-sem-alvo-1"})
    assert r4.status_code == 400 and r4.json()["detail"]["code"] == "unknown_instance" and h.state.db.scalar("SELECT COUNT(*) FROM pedidos") == 0


# =============================================================================================== leitura
async def test_lista_com_filtros_detalhe_e_404(h: Harness) -> None:
    c = _cliente(h)
    a = _criar(c, "chave-lista-0001").json()
    b = c.post("/api/pedidos", json={**_corpo(objetivo="Verifique a vitrine de promoções da loja."),
                                     "idempotency_key": "chave-lista-0002", "titulo": "Vitrine"}).json()
    lista = c.get("/api/pedidos").json()
    assert {i["id"] for i in lista["items"]} == {a["id"], b["id"]} and lista["proximo_cursor"] is None
    assert lista["total_por_estado"]["ativo"] == 1 and lista["total_por_estado"]["rascunho"] == 1
    assert [i["id"] for i in c.get("/api/pedidos?estado=rascunho").json()["items"]] == [b["id"]]
    assert c.get("/api/pedidos?estado=rascunho").json()["total_por_estado"]["ativo"] == 1, "os chips ignoram o filtro"
    assert [i["id"] for i in c.get("/api/pedidos?q=vitrine").json()["items"]] == [b["id"]]
    assert c.get("/api/pedidos?estado=inexistente").status_code == 422
    assert [i["id"] for i in c.get("/api/pedidos?tipo=recorrencia&autonomia=observar&ordem=proxima").json()["items"]]
    pag = c.get("/api/pedidos?limit=1").json()
    assert len(pag["items"]) == 1 and pag["proximo_cursor"]
    seg = c.get(f"/api/pedidos?limit=1&cursor={pag['proximo_cursor']}").json()
    assert seg["items"][0]["id"] != pag["items"][0]["id"] and seg["proximo_cursor"] is None
    d = c.get(f"/api/pedidos/{a['id']}").json()
    assert d["gatilhos"][0]["spec"]["rrule"] == "FREQ=DAILY;BYHOUR=8" and len(d["proximas"]) == 5
    assert d["ocorrencias_recentes"] == [] and d["pendencias"] == [] and d["relatorios_recentes"] == []
    assert d["memoria"] == [] and d["observacoes_recentes"] == []
    nf = c.get("/api/pedidos/nao-existe")
    assert nf.status_code == 404 and nf.json()["detail"]["code"] == "not_found"
    assert c.get("/api/pedidos/avisos").status_code == 200, "/avisos vem antes de /{id}"


# =============================================================================================== ações
async def test_acoes_repetidas_sao_200_sem_mudanca_e_o_invalido_e_409(h: Harness) -> None:
    c = _cliente(h)
    pid = _criar(c).json()["id"]
    p1 = c.post(f"/api/pedidos/{pid}/pausar", json={}).json()
    assert p1["pedido"]["estado"] == "pausado" and p1["pedido"]["pausado_motivo"] == "Pausado pela pessoa"
    assert p1["sem_mudanca"] is False
    assert c.post(f"/api/pedidos/{pid}/pausar", json={"motivo": "outro"}).json()["sem_mudanca"] is True
    assert c.post(f"/api/pedidos/{pid}/pausar").json()["sem_mudanca"] is True, "corpo é opcional"
    r = c.post(f"/api/pedidos/{pid}/retomar", json={"modo": "daqui"}).json()
    assert r["pedido"]["estado"] == "ativo" and r["sem_mudanca"] is False and r["puladas"] == 0
    assert c.post(f"/api/pedidos/{pid}/retomar", json={}).json()["sem_mudanca"] is True
    pausa = c.post(f"/api/pedidos/{pid}/pausar", json={"motivo": "férias"}).json()
    assert pausa["pedido"]["pausado_motivo"] == "férias"
    nec = c.post(f"/api/pedidos/{pid}/cancelar", json={})
    assert nec.status_code == 409 and nec.json()["detail"]["code"] == "confirmacao_necessaria"
    assert nec.json()["detail"]["ocorrencias_futuras"] == 0 and h.state.repo.db.one(
        "SELECT estado FROM pedidos WHERE id=?", (pid,))["estado"] == "pausado"
    ok = c.post(f"/api/pedidos/{pid}/cancelar", json={"confirmar": True}).json()
    assert ok["pedido"]["estado"] == "cancelado" and ok["sem_mudanca"] is False and ok["execucoes_em_curso"] == []
    assert c.post(f"/api/pedidos/{pid}/cancelar", json={"confirmar": True}).json()["sem_mudanca"] is True
    inv = c.post(f"/api/pedidos/{pid}/pausar", json={})
    assert inv.status_code == 409 and inv.json()["detail"]["code"] == "invalid_state"
    assert inv.json()["detail"]["estado"] == "cancelado" and inv.json()["detail"]["acoes_permitidas"] == []
    assert c.post(f"/api/pedidos/{pid}/retomar", json={}).status_code == 409
    assert c.post("/api/pedidos/nao-existe/pausar", json={}).status_code == 404


async def test_edicao_com_versao_dry_run_e_selo(h: Harness) -> None:
    c = _cliente(h)
    p = _criar(c).json()
    pid = p["id"]
    velha = c.patch(f"/api/pedidos/{pid}", json={"versao": 9, "titulo": "Novo"})
    assert velha.status_code == 409 and velha.json()["detail"] == {
        "code": "versao_desatualizada", "message": velha.json()["detail"]["message"], "versao_atual": 1}
    t = c.patch(f"/api/pedidos/{pid}", json={"versao": 1, "titulo": "Café novo"}).json()
    assert t["aplicado"] and t["pedido"]["titulo"] == "Café novo" and t["pedido"]["versao"] == 2
    sem = c.patch(f"/api/pedidos/{pid}", json={"versao": 2, "autonomia": "preparar"})
    assert sem.status_code == 409 and sem.json()["detail"]["code"] == "previa_nao_confirmada"
    seco = c.patch(f"/api/pedidos/{pid}", json={"versao": 2, "autonomia": "preparar", "dry_run": True}).json()
    assert seco["aplicado"] is False and seco["pedido"]["autonomia"] == "observar" and seco["confirmacao"]
    assert [m["campo"] for m in seco["mudancas"]] == ["autonomia"]
    aplicada = c.patch(f"/api/pedidos/{pid}", json={"versao": 2, "autonomia": "preparar",
                                                    "confirmacao": seco["confirmacao"]}).json()
    assert aplicada["aplicado"] and aplicada["pedido"]["autonomia"] == "preparar" and aplicada["pedido"]["versao"] == 3
    errada = c.patch(f"/api/pedidos/{pid}", json={"versao": 3, "autonomia": "agir", "confirmacao": seco["confirmacao"]})
    assert errada.status_code == 409 and errada.json()["detail"]["code"] == "previa_desatualizada"
    piso = c.patch(f"/api/pedidos/{pid}", json={"versao": 3, "gatilhos": [{
        "tipo": "recorrencia", "spec": {"dtstart": "2026-10-03T08:00:00", "rrule": "FREQ=HOURLY;BYMINUTE=0,5"}}],
        "dry_run": True})
    assert piso.status_code == 422 and piso.json()["detail"]["code"] == "frequencia_abaixo_do_piso"
    c.post(f"/api/pedidos/{pid}/cancelar", json={"confirmar": True})
    assert c.patch(f"/api/pedidos/{pid}", json={"versao": 3, "titulo": "x"}).status_code == 409


# =============================================================================================== laço e eventos
async def test_pedido_criado_pela_api_e_despachado_pelo_laco_e_emite_eventos(h: Harness) -> None:
    c = _cliente(h)
    r = Relogio()
    h.state.pedidos.relogio = r
    laco = LacoDePedidos(h.state.db, h.state.runs, Lideranca(h.state.db, dono="laco-api", relogio=r),
                         PedidosCfg(enabled=True, prazo_inicio_s=604_800), relogio=r)
    laco.notificar = h.state.pedidos_api.publicar
    p = _criar(c, gatilhos=[{"tipo": "agora", "spec": {}}]).json()
    assert [e["kind"] for e in _eventos(h, "pedido.updated")] == ["pedido.updated", "pedido.updated"][:len(
        _eventos(h, "pedido.updated"))] and _eventos(h, "pedido.updated"), "a criação emite pedido.updated"
    res = laco.uma_volta()
    assert (res.materializadas, res.despachadas) == (1, 1), "o formato dos alvos gravados é o que o laço lê"
    [o] = h.state.db.query("SELECT * FROM pedido_ocorrencias WHERE pedido_id=?", (p["id"],))
    assert o["estado"] == "despachada" and o["run_id"]
    kinds = [json.loads(e["data"])["ocorrencia"]["estado"] for e in _eventos(h, "pedido.ocorrencia.updated")]
    assert kinds == ["despachada"], "um evento por ocorrência e por volta, com o estado em que ela ficou"
    d = c.get(f"/api/pedidos/{p['id']}").json()
    assert d["ocorrencias_recentes"][0]["run"]["id"] == o["run_id"] and d["ocorrencias_recentes"][0]["run_disponivel"]
    assert d["execucoes_em_curso"][0]["run_id"] == o["run_id"]
    oc = c.get(f"/api/pedidos/{p['id']}/ocorrencias?estado=despachada").json()
    assert [i["id"] for i in oc["items"]] == [o["id"]] and oc["proximo"] is None
    ex = c.get(f"/api/pedidos/{p['id']}/execucoes").json()
    assert ex[0]["id"] == o["run_id"] and ex[0]["pedido_id"] == p["id"] and ex[0]["ocorrencia_id"] == o["id"]
    assert c.get(f"/api/pedidos/{p['id']}/relatorios").json() == {"items": [], "proximo_cursor": None}
    assert c.get(f"/api/pedidos/{p['id']}/observacoes").json() == {"items": [], "proximo_cursor": None}
    antiga = h.state.db.one("SELECT pedido_id, ocorrencia_id FROM runs WHERE id=?", (o["run_id"],))
    assert antiga["pedido_id"] == p["id"]


async def test_avisos_saem_como_evento_e_a_rota_os_le(h: Harness) -> None:
    c = _cliente(h)
    pid = _criar(c).json()["id"]
    api, repo = h.state.pedidos_api, h.state.pedidos.repo
    assert repo.mudar_estado_do_pedido(pid, "ativo", "pausado", "2026-10-02T10:00:00.000Z",
                                       pausado_motivo="3 falhas seguidas")
    api.publicar(repo.descarregar())
    [ev] = [e for e in _eventos(h, "pedido.aviso")]
    assert ev["level"] == "warn"
    lista = c.get(f"/api/pedidos/avisos?pedido_id={pid}&requer_pessoa=0").json()
    [a] = lista["items"]
    assert a["tipo"] == "pausa_automatica" and a["requer_pessoa"] is False and a["pedido_id"] == pid
    assert "3 falhas seguidas" in a["mensagem"] and c.get("/api/pedidos/avisos?requer_pessoa=1").json()["items"] == []
    ups = _eventos(h, "pedido.updated")
    assert json.loads(ups[-1]["data"])["pedido"]["estado"] == "pausado" and ups[-1]["level"] == "warn"
    assert c.post(f"/api/pedidos/{pid}/retomar", json={}).json()["pedido"]["estado"] == "ativo"
    assert _eventos(h, "pedido.updated")[-1]["level"] == "info"
    assert len(_eventos(h, "pedido.aviso")) == 1, "o gesto da pessoa não gera aviso"


# =============================================================================================== pendências
async def test_pedido_aguardando_pessoa_e_um_item_com_os_filhos_agrupados(h: Harness) -> None:
    c = _cliente(h)
    r = Relogio()
    h.state.pedidos.relogio = r
    laco = LacoDePedidos(h.state.db, h.state.runs, Lideranca(h.state.db, dono="laco-pend", relogio=r),
                         PedidosCfg(enabled=True, prazo_inicio_s=604_800), relogio=r)
    pid = _criar(c, gatilhos=[{"tipo": "agora", "spec": {}}]).json()["id"]
    laco.uma_volta()
    [o] = h.state.db.query("SELECT * FROM pedido_ocorrencias WHERE pedido_id=?", (pid,))
    db = h.state.db
    db.execute("UPDATE runs SET status='needs_input' WHERE id=?", (o["run_id"],))
    db.execute("INSERT INTO pending_approvals(id, run_id, capability, summary, status, created_at)"
               " VALUES ('ap-1', ?, 'comentar', 'resumo', 'pending', '2026-10-02T10:00:00.000Z')", (o["run_id"],))
    db.execute("UPDATE pedidos SET estado='aguardando_pessoa' WHERE id=?", (pid,))
    # uma execução AVULSA em needs_input (sem pedido): essa continua contando sozinha
    avulsa = h.state.runs.create(RunCreate(command=COMMAND, instance_ids=["android-01"],
                                           idempotency_key="chave-avulsa-0001"))
    db.execute("UPDATE runs SET status='needs_input' WHERE id=?", (avulsa.id,))
    snap = c.get("/api/snapshot").json()
    ped = snap["pedidos"]
    assert ped["por_estado"]["aguardando_pessoa"] == 1 and ped["avisos_nao_lidos"] == 0
    [item] = ped["aguardando_pessoa"]
    assert item["id"] == pid and item["acoes_permitidas"] == ["editar", "retomar", "cancelar"]
    assert {(x["tipo"], x["ref"]) for x in item["pendencias"]} == {("pergunta", o["run_id"]), ("aprovacao", "ap-1")}
    filhos_do_pedido = {x["run_id"] for x in item["pendencias"]}
    runs = {x["id"]: x for x in snap["runs"]}
    assert runs[o["run_id"]]["pedido_id"] == pid and runs[o["run_id"]]["ocorrencia_id"] == o["id"]
    soltas = [x for x in snap["runs"] if x["status"] == "needs_input" and x["id"] not in filhos_do_pedido]
    assert [x["id"] for x in soltas] == [avulsa.id], "a execução do pedido não é solta: é filha dele"
    assert len(ped["aguardando_pessoa"]) + len(soltas) == 2, "pedido (1, com pergunta e aprovação) + avulsa (1)"
    d = c.get(f"/api/pedidos/{pid}").json()
    assert len(d["pendencias"]) == 2
    pend = c.post(f"/api/pedidos/{pid}/retomar", json={})
    assert pend.status_code == 409 and pend.json()["detail"]["code"] == "pendencia_aberta"
    assert len(pend.json()["detail"]["pendencias"]) == 2
    assert c.post(f"/api/pedidos/{pid}/retomar", json={"modo": "daqui"}).status_code == 422
    assert c.post(f"/api/pedidos/{pid}/pausar", json={}).status_code == 409, "aguardando_pessoa não vai a pausado"
    db.execute("UPDATE runs SET status='cancelled' WHERE id=?", (o["run_id"],))
    db.execute("UPDATE pending_approvals SET status='approved' WHERE id='ap-1'")
    livre = c.post(f"/api/pedidos/{pid}/retomar", json={}).json()
    assert livre["pedido"]["estado"] == "ativo" and livre["sem_mudanca"] is False
    assert c.get("/api/snapshot").json()["pedidos"]["aguardando_pessoa"] == []
    assert pid in {x["id"] for x in c.get("/api/pedidos?estado=ativo").json()["items"]}


async def test_run_create_publico_continua_recusando_campo_de_pedido() -> None:
    base = {"command": COMMAND, "instance_ids": ["android-01"], "idempotency_key": "chave-de-teste-9"}
    RunCreate(**base)
    for extra in ({"pedido_id": "p1"}, {"ocorrencia_id": "o1"}, {"origem": ["p", "o"]}, {"prioridade": 3}):
        with pytest.raises(ValidationError):
            RunCreate(**base, **extra)
