"""Achado #166: as rotas que só tinham teste de SERVIÇO, agora pelo contrato HTTP.

Teste de serviço prova a REGRA. O que ele não prova é o que o painel consome: qual código HTTP cada erro vira,
se o corpo é validado, e se a resposta tem o formato que o frontend lê. Foi exatamente aí que a lacuna morava —
controle manual remoto (aceite 3 do pedido) não tinha uma única chamada HTTP de ponta a ponta, e worker,
aprovações, entrada manual e evidências não tinham nenhuma.

Quem impede a lacuna de voltar é `test_cobertura_de_rotas.py`; este arquivo é o que a fecha.
"""
from __future__ import annotations

from typing import Any

import httpx

from app.main import create_app
from app.models import InstanceState
from app.util import now_iso

from .conftest import Harness


def _cliente(h: Harness) -> httpx.AsyncClient:
    app = create_app(h.cfg, state=h.state)
    app.state.poc = h.state
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _erro(r: httpx.Response) -> dict[str, Any]:
    """O corpo de erro que o frontend lê: `{"detail": {"code", "message"}}`."""
    corpo = r.json()["detail"]
    assert isinstance(corpo, dict) and corpo.get("code") and corpo.get("message"), r.text
    return corpo


# ============================================================ aceite 3: controle manual de tela, por HTTP
async def test_controle_manual_de_ponta_a_ponta_por_http(harness: Harness) -> None:
    """take → frame (com os cabeçalhos `X-Frame-*`) → input → release, tudo pela rota.

    A regra do frame velho tinha teste de serviço (test_execution); o que faltava era provar que ela chega ao
    painel como **409** com um código legível, e não como 500 ou como 200 silencioso.
    """
    st = harness.state
    assert st is not None
    rt = st.devices.get("android-01")
    async with _cliente(harness) as c:
        pegou = await c.post("/api/instances/android-01/control/take")
        assert pegou.status_code == 200
        lease = pegou.json()["lease_id"]
        assert pegou.json()["status"] in ("granted", "pending") and lease

        await st.devices.observe(rt, timeout=5)                     # um retrato existe antes de a tela pedir um
        img = await c.get("/api/instances/android-01/frame")
        assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"
        assert img.content, "o frame não pode voltar vazio"
        # Os cabeçalhos SÃO o contrato do controle manual: o clique do usuário viaja com o id do frame que ele viu.
        frame_id = img.headers["X-Frame-Id"]
        assert frame_id and img.headers["X-Frame-Ts"]
        assert int(img.headers["X-Frame-Width"]) > 0 and int(img.headers["X-Frame-Height"]) > 0
        assert img.headers["X-Frame-Orientation"] in ("portrait", "landscape")
        assert img.headers["Cache-Control"] == "no-store"

        velho = await c.post("/api/instances/android-01/input",
                             json={"lease_id": lease, "frame_id": "frame-de-outro-momento", "type": "tap",
                                   "x": 10, "y": 10})
        assert velho.status_code == 409 and _erro(velho)["code"] == "stale_frame"

        outro = await c.post("/api/instances/android-01/input",
                             json={"lease_id": "lease-de-outra-aba", "frame_id": frame_id, "type": "tap",
                                   "x": 10, "y": 10})
        assert outro.status_code == 409 and _erro(outro)["code"] == "not_controller"

        ok = await c.post("/api/instances/android-01/input",
                          json={"lease_id": lease, "frame_id": frame_id, "type": "key", "key": "back"})
        assert ok.status_code == 200 and ok.json() == {"ok": True}

        solto = await c.post("/api/instances/android-01/control/release", json={"lease_id": lease})
        assert solto.status_code == 200 and solto.json()["status"] == "released"

        # Depois de soltar, o mesmo clique não vale mais: o que protege o aparelho é o lease, não a boa vontade.
        depois = await c.post("/api/instances/android-01/input",
                              json={"lease_id": lease, "frame_id": frame_id, "type": "key", "key": "back"})
        assert depois.status_code == 409 and _erro(depois)["code"] == "not_controller"


async def test_frame_e_hierarquia_recusam_com_codigo_legivel(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    async with _cliente(harness) as c:
        desconhecido = await c.get("/api/instances/nao-existe/frame")
        assert desconhecido.status_code == 404 and _erro(desconhecido)["code"] == "not_found"

        arvore = await c.get("/api/instances/android-01/hierarchy")
        assert arvore.status_code == 200
        corpo = arvore.json()
        assert corpo["ts"] and isinstance(corpo["elements"], list) and corpo["elements"]
        assert "id" in corpo["elements"][0]

        rt = st.devices.get("android-02")
        await st.devices.stop_instance(rt)
        for rota in (f"/api/instances/{rt.id}/hierarchy", f"/api/instances/{rt.id}/packages"):
            r = await c.get(rota)
            assert r.status_code == 409 and _erro(r)["code"] == "offline", rota
        sem_frame = await c.get(f"/api/instances/{rt.id}/frame")
        assert sem_frame.status_code in (200, 404)          # desligado sem retrato guardado → 404 `no_frame`
        if sem_frame.status_code == 404:
            assert _erro(sem_frame)["code"] == "no_frame"


async def test_lista_de_pacotes_do_aparelho_online(harness: Harness) -> None:
    async with _cliente(harness) as c:
        r = await c.get("/api/instances/android-01/packages")
        # O aparelho do harness é falso: o que importa é o contrato — 200 com a lista, ou a recusa do adb como 503.
        assert r.status_code in (200, 503)
        if r.status_code == 200:
            assert isinstance(r.json()["packages"], list)
        else:
            assert _erro(r)["code"] == "adb_error"


# ============================================================ workers
async def test_rotas_de_worker_por_http(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    async with _cliente(harness) as c:
        r = await c.get("/api/workers/worker-que-nao-existe")
        assert r.status_code == 404 and _erro(r)["code"] == "not_found"

        m = await c.post("/api/workers/worker-que-nao-existe/maintenance", json={"on": True})
        assert m.status_code == 404, m.text

        # Com um worker de verdade no registro, a leitura e a manutenção respondem o DTO que o painel desenha.
        # O registro nasce do `hello` do agente; aqui a linha entra direto porque o alvo é a ROTA, não o canal.
        agora = now_iso()
        st.db.execute(
            "INSERT INTO workers(id, name, os, state, enrolled_at, last_seen_at, max_slots, token_hash) "
            "VALUES('worker-http-01','Notebook HTTP','windows','online',?,?,4,'hash-de-fixture')", (agora, agora))
        lido = await c.get("/api/workers/worker-http-01")
        assert lido.status_code == 200 and lido.json()["id"] == "worker-http-01"
        assert lido.json()["maintenance"] is False

        ligou = await c.post("/api/workers/worker-http-01/maintenance", json={"on": True})
        assert ligou.status_code == 200 and ligou.json()["maintenance"] is True
        lista = await c.get("/api/workers")
        assert lista.status_code == 200
        assert any(w["id"] == "worker-http-01" and w["maintenance"] for w in lista.json())


# ============================================================ aprovações
async def test_aprovacoes_em_lote_uma_recusada_nao_derruba_as_outras(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    async with _cliente(harness) as c:
        vazia = await c.get("/api/approvals")
        assert vazia.status_code == 200 and vazia.json() == []

        um = st.approvals.open(profile_id=None, capability="SEND_MESSAGE", summary="Responder a QA-001",
                               content="bom dia")
        dois = st.approvals.open(profile_id=None, capability="SEND_MESSAGE", summary="Responder a QA-002",
                                 content="boa tarde")

        pendentes = await c.get("/api/approvals")
        assert pendentes.status_code == 200 and {a["id"] for a in pendentes.json()} == {um.id, dois.id}

        lote = await c.post("/api/approvals/decide", json={"decisions": [
            {"id": um.id, "verb": "approve"},
            {"id": dois.id, "verb": "edit", "content": "Boa tarde! Já te respondo."},
            {"id": "apr-que-nao-existe", "verb": "approve"},
        ]})
        assert lote.status_code == 200
        corpo = lote.json()
        assert len(corpo["decided"]) == 2 and len(corpo["refused"]) == 1
        assert corpo["refused"][0]["id"] == "apr-que-nao-existe"     # a resposta DIZ qual falhou
        assert st.approvals.get(um.id).status == "approved"
        assert st.approvals.get(dois.id).status == "edited"

        # Corpo inválido é 422 antes de tocar em qualquer aprovação — `edit` sem texto é 400 na regra.
        vazio = await c.post("/api/approvals/decide", json={"decisions": []})
        assert vazio.status_code == 422


async def test_decidir_uma_aprovacao_por_http(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    async with _cliente(harness) as c:
        pedido = st.approvals.open(profile_id=None, capability="SEND_MESSAGE", summary="Responder", content="oi")
        sem_texto = await c.post(f"/api/approvals/{pedido.id}/decide", json={"verb": "edit"})
        assert sem_texto.status_code == 400 and _erro(sem_texto)["code"] == "empty_content"

        ok = await c.post(f"/api/approvals/{pedido.id}/decide", json={"verb": "approve"})
        assert ok.status_code == 200
        de_novo = await c.post(f"/api/approvals/{pedido.id}/decide", json={"verb": "approve"})
        assert de_novo.status_code in (400, 409) and _erro(de_novo)["code"] == "already_decided"


# ============================================================ leituras do painel
async def test_leituras_do_painel_respondem_o_formato_que_a_tela_consome(harness: Harness) -> None:
    async with _cliente(harness) as c:
        # Uma chamada literal por rota, de propósito: é assim que `test_cobertura_de_rotas` enxerga a cobertura,
        # e um laço sobre uma tupla de caminhos esconderia a rota do cruzamento por AST.
        ia = await c.get("/api/ai")
        assert ia.status_code == 200 and isinstance(ia.json(), dict)
        diag = await c.get("/api/diagnostics")
        assert diag.status_code == 200 and isinstance(diag.json(), dict)

        assert isinstance((await c.get("/api/apps")).json(), list)
        assert isinstance((await c.get("/api/app-state")).json(), list)
        assert isinstance((await c.get("/api/personas")).json(), list)
        ajustes = (await c.get("/api/settings")).json()
        assert "max_ai_concurrency" in ajustes

        r = await c.get("/api/personas/persona-que-nao-existe")
        assert r.status_code == 404 and _erro(r)["code"]

        # `/metrics` é honesta quando ainda não mediu nada: 503 `not_ready` em vez de zeros inventados.
        m = await c.get("/api/metrics")
        assert m.status_code in (200, 503)
        if m.status_code == 503:
            assert _erro(m)["code"] == "not_ready"
        else:
            assert isinstance(m.json(), dict)


# ============================================================ releases
async def test_rotas_de_release_por_http(harness: Harness) -> None:
    async with _cliente(harness) as c:
        importou = await c.post("/api/releases/import", json={})
        assert importou.status_code == 202 and importou.json()["imported"] == []   # pasta de entrada vazia

        assinatura = await c.post("/api/releases/rel-que-nao-existe/approve-signature", json={"note": "confiro"})
        assert assinatura.status_code == 404 and _erro(assinatura)["code"] == "not_found"

        icone = await c.get("/api/releases/rel-que-nao-existe/icon")
        assert icone.status_code == 404 and _erro(icone)["code"] == "sem_icone"


# ============================================================ evidências
async def test_evidencia_inexistente_e_404_com_codigo(harness: Harness) -> None:
    async with _cliente(harness) as c:
        r = await c.get("/api/evidence/999999")
        assert r.status_code == 404 and _erro(r)["code"] == "not_found"
        texto = await c.get("/api/evidence/nao-e-numero")
        assert texto.status_code == 422                     # o id é inteiro: entrada torta não vira consulta


# ============================================================ execuções
async def test_resolver_objetivo_inexistente_por_http(harness: Harness) -> None:
    async with _cliente(harness) as c:
        r = await c.post("/api/runs/run-que-nao-existe/objectives/obj-que-nao-existe/resolve",
                         json={"resolution": "confirm_done", "note": "resolvido à mão"})
        assert r.status_code in (400, 404, 409), r.text
        assert _erro(r)["code"]

        # Corpo com verbo inventado é recusado ANTES de qualquer efeito: o conjunto é fechado.
        torto = await c.post("/api/runs/run-que-nao-existe/objectives/obj/resolve",
                             json={"resolution": "faz-o-que-der"})
        assert torto.status_code == 422


async def test_pausar_retomar_e_cancelar_pela_rota(harness: Harness) -> None:
    """A rota genérica `POST /runs/{id}/{op}` só era tocada por um 403 de origem: o handler nunca rodava."""
    st = harness.state
    assert st is not None
    run = harness.run(["android-01"])
    async with _cliente(harness) as c:
        pausou = await c.post(f"/api/runs/{run.id}/pause")
        assert pausou.status_code == 200, pausou.text
        assert st.repo.run_row(run.id)["pause_requested"]

        retomou = await c.post(f"/api/runs/{run.id}/resume")
        assert retomou.status_code == 200 and not st.repo.run_row(run.id)["pause_requested"]

        cancelou = await c.post(f"/api/runs/{run.id}/cancel")
        assert cancelou.status_code == 200 and st.repo.run_row(run.id)["cancel_requested"]

        desconhecida = await c.post("/api/runs/run-que-nao-existe/pause")
        assert desconhecida.status_code == 404

        invalida = await c.post(f"/api/runs/{run.id}/verbo-que-nao-existe")
        assert invalida.status_code in (400, 404, 422)


# ============================================================ catálogo e receitas
async def test_apps_receitas_e_fluxos_recusam_id_desconhecido(harness: Harness) -> None:
    async with _cliente(harness) as c:
        r = await c.put("/api/apps/app-que-nao-existe", json={"name": "X"})
        assert r.status_code == 404 and _erro(r)["code"]

        d = await c.delete("/api/apps/app-que-nao-existe")
        assert d.status_code in (204, 404)

        f = await c.put("/api/flows/flow-que-nao-existe", json={"status": "disabled"})
        assert f.status_code == 404 and _erro(f)["code"]

        rec = await c.delete("/api/recipes/987654")
        assert rec.status_code == 204                       # apagar o que não existe é sucesso: a rota é idempotente
        torta = await c.delete("/api/recipes/nao-e-numero")
        assert torta.status_code == 422                     # o id é inteiro: texto não vira consulta


async def test_abrir_a_loja_recusa_sem_aparelho_loja_configurado(harness: Harness) -> None:
    async with _cliente(harness) as c:
        r = await c.post("/api/store/open-listing", json={"package": "com.instagram.android"})
        assert r.status_code == 409 and _erro(r)["code"] == "no_store"


# ============================================================ perfis
async def test_perfil_sem_aparelho_recusa_conectar_verificar_e_sair(harness: Harness) -> None:
    """Perfil sem vínculo não pode conectar: a recusa é 409 com código, não 500 nem 202 mentiroso."""
    async with _cliente(harness) as c:
        criado = await c.post("/api/instagram/profiles",
                              json={"username": "perfil.sem.aparelho", "first_name": "Sem"})
        assert criado.status_code == 201, criado.text
        pid = criado.json()["id"]

        mudou = await c.patch(f"/api/instagram/profiles/{pid}", json={"display_name": "Sem Aparelho"})
        assert mudou.status_code == 200 and mudou.json()["display_name"] == "Sem Aparelho"

        conectar = await c.post(f"/api/instagram/profiles/{pid}/connect")
        assert conectar.status_code == 409 and _erro(conectar)["code"] == "no_binding"
        verificar = await c.post(f"/api/instagram/profiles/{pid}/verify")
        assert verificar.status_code == 409 and _erro(verificar)["code"] == "no_binding"
        sair = await c.post(f"/api/instagram/profiles/{pid}/logout")
        assert sair.status_code == 409 and _erro(sair)["code"] == "no_binding"

        foto = await c.get(f"/api/instagram/profiles/{pid}/avatar")
        assert foto.status_code == 404 and _erro(foto)["code"] == "sem_foto"

        assert (await c.post("/api/instagram/profiles/nao-existe/connect")).status_code == 404
        assert (await c.post("/api/instagram/profiles/nao-existe/verify")).status_code == 404
        assert (await c.post("/api/instagram/profiles/nao-existe/logout")).status_code == 404


def test_estado_de_instancia_e_enumeracao_conhecida() -> None:
    """Guarda barata: os testes acima falam de `online`/`stopped` por nome."""
    assert InstanceState.online.value == "online" and InstanceState.stopped.value == "stopped"
