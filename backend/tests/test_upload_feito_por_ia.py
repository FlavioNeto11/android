"""29.81: o dono diz se a foto que ENVIOU foi feita por IA, e o rótulo de IA do Instagram (29.79) segue o que ele disse.

Regra do dono (03/10): foto realista de IA publicada pela automação leva o rótulo. Até aqui a origem decidia sozinha
(`upload` = sem rótulo), e a foto de IA que o dono subiria à mão sairia sem ele. A coluna `feita_por_ia` (migração 108)
guarda a resposta: 1 = com rótulo, 0 = foto real, nulo = não informado (as linhas antigas: o rótulo de cada uma fica
como estava). Gerada e importada continuam com o rótulo, digam o que disserem.
"""
from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from PIL import Image

from app.main import create_app
from app.modules.identity.adapters.simulated_images import SimulatedImageGenerator
from app.models import PersonaCreate
from app.porta_do_plano import previa_da_porta
from app.social.chave_da_aprovacao import rotulo_ia_da_imagem

from .conftest import make_config
from .test_persona_imagens import _servico
from .test_porta_do_plano import _plano, _por_chave
from .test_rotulo_ia import _imagem


def _png(largura: int = 40, altura: int = 30) -> bytes:
    saida = io.BytesIO()
    Image.new("RGB", (largura, altura), (10, 120, 200)).save(saida, format="PNG")
    return saida.getvalue()


# ---------------------------------------------------------------- o serviço e a origem
async def test_o_upload_guarda_a_resposta_e_a_correcao_so_vale_para_upload(tmp_path: Path) -> None:
    svc, db, _storage, _eventos, _contas = _servico(tmp_path, SimulatedImageGenerator())
    try:
        sem_resposta = await svc.registrar_upload("ig-1", _png(), "image/png")
        de_ia = await svc.registrar_upload("ig-1", _png(20, 20), "image/png", feita_por_ia=True)
        assert sem_resposta.feita_por_ia is None and de_ia.feita_por_ia is True
        assert svc.obter("ig-1", de_ia.id).feita_por_ia is True               # releitura do banco
        assert svc.marcar_feita_por_ia("ig-1", sem_resposta.id, False)[1] is True              # mudança real
        assert svc.marcar_feita_por_ia("ig-1", sem_resposta.id, False)[1] is False           # a mesma: não mudou
        assert svc.obter("ig-1", sem_resposta.id).feita_por_ia is False
        assert svc.marcar_feita_por_ia("ig-1", sem_resposta.id, None)[0].feita_por_ia is None   # volta a "não informado"
        assert svc.marcar_feita_por_ia("ig-1", sem_resposta.id, None)[1] is False            # a mesma: não mudou
        db.execute("UPDATE persona_images SET source='generated' WHERE id=?", (de_ia.id,))
        with pytest.raises(ValueError, match="gerada"):
            svc.marcar_feita_por_ia("ig-1", de_ia.id, False)                  # a gerada não se marca
        with pytest.raises(KeyError):
            svc.marcar_feita_por_ia("ig-1", "img-nao-existe", True)
    finally:
        db.close()


async def test_a_origem_e_a_resposta_decidem_o_rotulo(harness: Any) -> None:
    """Upload: "true" só com a resposta "feita por IA"; sem resposta (as linhas de antes da 108) ou "foto real", "false",
    como era. Gerada e importada: "true" sempre, mesmo marcadas como foto real (a marca só vale para o upload)."""
    state = harness.state
    pid = _plano(state, [])
    _imagem(state, "up-nulo", pid, "upload")
    _imagem(state, "up-ia", pid, "upload")
    _imagem(state, "up-real", pid, "upload")
    _imagem(state, "gerada", pid, "generated")
    _imagem(state, "legado", pid, "imported_legacy")
    state.db.execute("UPDATE persona_images SET feita_por_ia=1 WHERE id='up-ia'")
    state.db.execute("UPDATE persona_images SET feita_por_ia=0 WHERE id IN ('up-real', 'gerada', 'legado')")
    assert rotulo_ia_da_imagem(state.db, "up-nulo") == "false"
    assert rotulo_ia_da_imagem(state.db, "up-ia") == "true"
    assert rotulo_ia_da_imagem(state.db, "up-real") == "false"
    assert rotulo_ia_da_imagem(state.db, "gerada") == "true"
    assert rotulo_ia_da_imagem(state.db, "legado") == "true"
    assert rotulo_ia_da_imagem(state.db, "nao-existe") is None


# ---------------------------------------------------------------- a correção chega às etapas abertas
async def test_corrigir_o_upload_regrava_o_rotulo_das_etapas_abertas_e_muda_a_chave(harness: Any) -> None:
    """O dono diz, DEPOIS do plano, que a foto enviada é de IA: a etapa que ainda vai publicá-la passa a pedir o rótulo,
    e a chave da aprovação muda — o sim dado à publicação sem rótulo não cobre a publicação com ele. A etapa que já
    terminou e a que publica outra imagem não mudam."""
    state = harness.state
    pid = _plano(state, [
        {"key": "publicar", "cap": "CREATE_POST",
         "bindings": {"image_id": "img-enviada", "content": "praia", "content_verbatim": "true"}},
        {"key": "outra", "cap": "CREATE_POST",
         "bindings": {"image_id": "img-outra", "content": "praia", "content_verbatim": "true"}},
        {"key": "feita", "cap": "CREATE_POST",
         "bindings": {"image_id": "img-enviada", "content": "praia", "content_verbatim": "true"}}])
    _imagem(state, "img-enviada", pid, "upload")
    _imagem(state, "img-outra", pid, "upload")
    # Como a central grava ao materializar o plano (`_com_rotulo_ia`): upload sem resposta → "false".
    for e in state.db.query("SELECT id, bindings FROM steps WHERE run_id='run-p'"):
        state.db.execute("UPDATE steps SET bindings=? WHERE id=?",
                         (json.dumps({**json.loads(e["bindings"]), "rotulo_ia": "false"}), e["id"]))
    state.db.execute("UPDATE steps SET status='succeeded' WHERE key='feita'")
    antes = _por_chave(previa_da_porta(state, "run-p"))["publicar"]
    assert antes["rotulo_ia"] is False and antes["chave"]

    state.db.execute("UPDATE persona_images SET feita_por_ia=1 WHERE id='img-enviada'")
    assert state.repo.ressincronizar_rotulo_ia("img-enviada") == 1          # só a aberta que publica ESTA imagem

    rotulos = {r["key"]: json.loads(r["bindings"]).get("rotulo_ia")
               for r in state.db.query("SELECT key, bindings FROM steps WHERE run_id='run-p'")}
    assert rotulos == {"publicar": "true", "outra": "false", "feita": "false"}
    depois = _por_chave(previa_da_porta(state, "run-p"))["publicar"]
    assert depois["rotulo_ia"] is True and depois["chave"] and depois["chave"] != antes["chave"]
    assert state.repo.ressincronizar_rotulo_ia("img-enviada") == 0          # idempotente


# ---------------------------------------------------------------- as rotas
async def test_rotas_do_upload_e_da_correcao(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    cfg.ensure_dirs()
    app = create_app(cfg)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        async with app.router.lifespan_context(app):
            pid = (await c.post("/api/personas", json=PersonaCreate(
                name="Marina Lopes", birth_date="1995-03-10").model_dump(mode="json"))).json()["id"]
            png = {"content": _png(), "headers": {"content-type": "image/png"}}
            de_ia = await c.post(f"/api/personas/{pid}/images?feita_por_ia=true", **png)
            assert de_ia.status_code == 201 and de_ia.json()["feita_por_ia"] is True
            sem = await c.post(f"/api/personas/{pid}/images", **png)
            assert sem.status_code == 201 and sem.json()["feita_por_ia"] is None
            assert (await c.post(f"/api/personas/{pid}/images?feita_por_ia=talvez", **png)).status_code == 422

            url = f"/api/personas/{pid}/images/{sem.json()['id']}/feita-por-ia"
            corrigida = await c.put(url, json={"feita_por_ia": True})
            assert corrigida.status_code == 200 and corrigida.json()["feita_por_ia"] is True
            assert (await c.put(url, json={"feita_por_ia": None})).json()["feita_por_ia"] is None
            assert (await c.put(url, json={})).status_code == 422                     # o campo é obrigatório
            assert (await c.put(f"/api/personas/{pid}/images/img-nao-existe/feita-por-ia",
                                json={"feita_por_ia": True})).status_code == 404
            app.state.poc.db.execute("UPDATE persona_images SET source='generated' WHERE id=?", (de_ia.json()["id"],))
            recusa = await c.put(f"/api/personas/{pid}/images/{de_ia.json()['id']}/feita-por-ia",
                                 json={"feita_por_ia": False})
            assert recusa.status_code == 409 and recusa.json()["detail"]["code"] == "nao_e_upload"
            lista = (await c.get(f"/api/personas/{pid}/images")).json()
            assert {i["id"]: i["feita_por_ia"] for i in lista if i["source"] == "upload"}[sem.json()["id"]] is None


def test_a_migracao_108_so_acrescenta_a_coluna_nula() -> None:
    """As linhas antigas ficam nulas ("não informado"): o rótulo de cada uma é o de antes da 108 (upload sem, gerada e
    importada com). Nenhum rótulo "true" vira "false" pela migração."""
    texto = (Path(__file__).resolve().parents[1] / "migrations" / "108_imagem_feita_por_ia.sql").read_text(encoding="utf-8")
    comandos = [linha for linha in texto.splitlines() if linha.strip() and not linha.lstrip().startswith("--")]
    assert comandos == ["ALTER TABLE persona_images ADD COLUMN feita_por_ia INTEGER;"]


async def test_so_o_motivo_mudando_tambem_muda_a_chave(harness: Any) -> None:
    """Pedido da orquestradora (04/10): "não informado" → "foto real" mantém o rótulo "false", mas o dono passa a ler
    outro item ("você disse que é foto real" em vez de "ninguém informou"). O porquê é argumento da etapa e entra na
    chave: o sim dado ao item "não informado" não cobre o item "foto real". E os três estados chegam ao item da prévia."""
    state = harness.state
    pid = _plano(state, [
        {"key": "enviada", "cap": "CREATE_POST",
         "bindings": {"image_id": "img-enviada", "content": "praia", "content_verbatim": "true"}},
        {"key": "gerada", "cap": "CREATE_POST",
         "bindings": {"image_id": "img-gerada", "content": "praia", "content_verbatim": "true"}}])
    _imagem(state, "img-enviada", pid, "upload")
    _imagem(state, "img-gerada", pid, "generated")
    # Como a central grava ao materializar (`_com_rotulo_ia`): o rótulo e o porquê.
    assert state.repo.ressincronizar_rotulo_ia("img-enviada") == 1
    assert state.repo.ressincronizar_rotulo_ia("img-gerada") == 1
    itens = _por_chave(previa_da_porta(state, "run-p"))
    assert (itens["enviada"]["rotulo_ia"], itens["enviada"]["rotulo_ia_motivo"]) == (False, "nao_informado")
    assert (itens["gerada"]["rotulo_ia"], itens["gerada"]["rotulo_ia_motivo"]) == (True, "ia")
    antes = itens["enviada"]["chave"]
    assert antes

    state.db.execute("UPDATE persona_images SET feita_por_ia=0 WHERE id='img-enviada'")
    assert state.repo.ressincronizar_rotulo_ia("img-enviada") == 1
    depois = _por_chave(previa_da_porta(state, "run-p"))["enviada"]
    assert (depois["rotulo_ia"], depois["rotulo_ia_motivo"]) == (False, "foto_real")
    assert depois["chave"] and depois["chave"] != antes


async def test_a_rota_das_aprovacoes_entrega_o_porque(tmp_path: Path) -> None:
    """`GET /approvals` entrega o `rotulo_ia_motivo` ao lado do `rotulo_ia` (este coberto desde a suíte 33 por
    `test_rotulo_ia_na_aprovacao.py`): a aba Textos e a guia Aprovações mostram os três estados."""
    cfg = make_config(tmp_path)
    cfg.ensure_dirs()
    app = create_app(cfg)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        async with app.router.lifespan_context(app):
            state = app.state.poc
            pid = _plano(state, [{"key": f"pub{k}", "cap": "CREATE_POST",
                                  "bindings": {"image_id": f"img-{k}", "content": "praia", "content_verbatim": "true"}}
                                 for k in (1, 2, 3)])
            _imagem(state, "img-1", pid, "generated")
            _imagem(state, "img-2", pid, "upload")
            _imagem(state, "img-3", pid, "upload")
            state.db.execute("UPDATE persona_images SET feita_por_ia=0 WHERE id='img-3'")
            for k in (1, 2, 3):
                state.repo.ressincronizar_rotulo_ia(f"img-{k}")
                state.approvals.open(profile_id=pid, capability="CREATE_POST", summary=f"publicar {k}",
                                     run_id="run-p", objective_id="run-p:android-01",
                                     step_id=f"run-p:android-01:v1:pub{k}", content="praia")
            lista = (await c.get("/api/approvals", params={"profile_id": pid})).json()
            vistos = {a["image_id"]: (a["rotulo_ia"], a["rotulo_ia_motivo"]) for a in lista}
            assert vistos == {"img-1": (True, "ia"), "img-2": (False, "nao_informado"), "img-3": (False, "foto_real")}


async def test_mesma_resposta_nao_mexe_e_falha_no_meio_nao_grava_nada(tmp_path: Path) -> None:
    """Revisão da Ferramentas (05/10). N3: remarcar com a MESMA resposta não é correção — a etapa aberta segue como
    estava (sem regravar nem mudar a chave do sim já dado). N2: a marca e a regravação das etapas vão numa transação
    só; se a regravação falha no meio, a marca também não fica (o selo não mente)."""
    cfg = make_config(tmp_path)
    cfg.ensure_dirs()
    app = create_app(cfg)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        async with app.router.lifespan_context(app):
            state = app.state.poc
            pid = _plano(state, [{"key": "pub", "cap": "CREATE_POST",
                                  "bindings": {"image_id": "img-u", "content": "praia", "content_verbatim": "true",
                                               "rotulo_ia": "false"}}])     # etapa gravada antes do porquê
            _imagem(state, "img-u", pid, "upload")
            antes = state.db.scalar("SELECT bindings FROM steps WHERE key='pub'")
            url = f"/api/personas/{pid}/images/img-u/feita-por-ia"
            chamadas: list[str] = []
            original = state.repo.ressincronizar_rotulo_ia
            state.repo.ressincronizar_rotulo_ia = lambda image_id: chamadas.append(image_id) or original(image_id)
            try:
                assert (await c.put(url, json={"feita_por_ia": None})).status_code == 200  # já era "não informado"
            finally:
                state.repo.ressincronizar_rotulo_ia = original
            assert chamadas == []                                                          # nada a ressincronizar
            assert state.db.scalar("SELECT bindings FROM steps WHERE key='pub'") == antes

            def quebra(_image_id: str) -> int:
                raise RuntimeError("falhou no meio")
            original = state.repo.ressincronizar_rotulo_ia
            state.repo.ressincronizar_rotulo_ia = quebra
            try:
                with pytest.raises(RuntimeError):
                    await c.put(url, json={"feita_por_ia": True})
            finally:
                state.repo.ressincronizar_rotulo_ia = original
            assert state.db.scalar("SELECT feita_por_ia FROM persona_images WHERE id='img-u'") is None
            assert state.db.scalar("SELECT bindings FROM steps WHERE key='pub'") == antes

            assert (await c.put(url, json={"feita_por_ia": True})).json()["feita_por_ia"] is True
            assert json.loads(state.db.scalar("SELECT bindings FROM steps WHERE key='pub'"))["rotulo_ia"] == "true"
