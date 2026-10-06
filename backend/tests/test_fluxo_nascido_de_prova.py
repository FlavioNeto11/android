"""31.130 (migração 122, adendo v1.87): a sessão de treino e o fluxo que nasceram de uma PROVA levam a marca.

Em 06/10, três fluxos ensinados durante provas de sessão ficavam, no Livro e em Salvas, iguais a fluxos reais
desligados por uma pessoa. Agora:

- `POST /api/instances/{id}/training` aceita `nascido_de_prova: true`; a marca fica na sessão e o `save` a leva ao fluxo;
- ela sai no GET e na listagem das sessões, em `GET /api/flows` e na `origem` do conteúdo do fluxo no Livro, com o
  filtro `nascido_de_prova=true|false` nas duas listagens;
- `PUT /api/flows/{id}` aceita `motivo`, que vai à trilha do livro ("fluxo de prova do 31.xxx, desligado de propósito").

Nível de prova: `simulated` (harness com aparelho falso; nenhuma IA).
"""
from __future__ import annotations

from typing import Any

from .conftest import Harness
from .test_modo_treinamento import _no_controle
from .test_perfil_bloqueado_e_capacidades import _cliente
from .test_treino_segredo_na_gravacao import _arvore, _el
from .test_treino_validacao_do_salvar import _etapa

PKG = "com.pocqa.messenger"


def _proposta(comando: str) -> dict[str, Any]:
    return {"summary": comando, "command_template": comando, "app_id": "qa-messenger", "parameters": [],
            "discarded": [], "questions": [], "steps": [_etapa("abrir", [1]), _etapa("conversa", [2])]}


async def _ensinar(harness: Harness, c: Any, *, prova: bool | None, comando: str) -> tuple[str, str]:
    """Abre a sessão PELA ROTA (com ou sem a marca), grava dois toques, para e salva. Devolve (sessão, fluxo)."""
    st, rt, lease = await _no_controle(harness)
    corpo: dict[str, Any] = {"intent": comando, "lease_id": lease, "app_id": "qa-messenger"}
    if prova is not None:
        corpo["nascido_de_prova"] = prova
    r = await c.post("/api/instances/android-01/training", json=corpo)
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    for n in (1, 2):
        alvo = _el(f"e{n}", text=f"Botão {n}", rid=f"{PKG}:id/b{n}", clickable=True, bounds=(0, 0, 100, 100))
        st.training.record(rt, {"type": "tap", "x": 50, "y": 50}, _arvore(alvo))
    st.training.stop(sid, lease_id=lease)
    salvo = await st.skills.save(sid, proposal=_proposta(comando), profile_ids=[], group_ids=[])
    st.devices.release_control(rt, lease)
    return sid, str(salvo["flow_id"])


async def test_a_marca_vai_da_sessao_ao_fluxo_e_sai_nas_leituras_com_filtro(harness: Harness) -> None:
    async with _cliente(harness) as c:
        s_prova, f_prova = await _ensinar(harness, c, prova=True, comando="abra a conversa de prova no app")
        s_real, f_real = await _ensinar(harness, c, prova=None, comando="abra a conversa real no app")

        assert (await c.get(f"/api/training/{s_prova}")).json()["nascido_de_prova"] is True
        assert (await c.get(f"/api/training/{s_real}")).json()["nascido_de_prova"] is False
        todas = (await c.get("/api/training")).json()
        assert {s["id"]: s["nascido_de_prova"] for s in todas} == {s_prova: True, s_real: False}
        assert [s["id"] for s in (await c.get("/api/training?nascido_de_prova=true")).json()] == [s_prova]
        assert [s["id"] for s in (await c.get("/api/training?nascido_de_prova=false")).json()] == [s_real]

        fluxos = {f["id"]: f["nascido_de_prova"] for f in (await c.get("/api/flows")).json()}
        assert fluxos[f_prova] is True and fluxos[f_real] is False
        assert [f["id"] for f in (await c.get("/api/flows?nascido_de_prova=true")).json()] == [f_prova]
        assert f_prova not in [f["id"] for f in (await c.get("/api/flows?nascido_de_prova=false")).json()]

        for fid, marca in ((f_prova, True), (f_real, False)):
            r = await c.get(f"/api/aprendizado/fluxo/{fid}")
            assert r.status_code == 200, r.text
            assert r.json()["conteudo"]["origem"]["nascido_de_prova"] is marca


async def test_o_motivo_de_quem_desliga_vai_a_trilha_e_o_invalido_e_recusado(harness: Harness) -> None:
    async with _cliente(harness) as c:
        _, fid = await _ensinar(harness, c, prova=True, comando="abra a conversa de prova no app")
        motivo = "fluxo de prova do 31.130, desligado de propósito"
        r = await c.put(f"/api/flows/{fid}", json={"status": "disabled", "motivo": motivo})
        assert r.status_code == 200 and r.json()["status"] == "disabled" and r.json()["nascido_de_prova"] is True, r.text
        razoes = [str(x["reason"]) for x in harness.state.db.query(
            "SELECT reason FROM learning_transitions WHERE item_ref=? ORDER BY id", (f"fluxo:{fid}",))]
        assert razoes and razoes[-1] == motivo
        for ruim in ("", "   ", 7, "x" * 301):
            r = await c.put(f"/api/flows/{fid}", json={"status": "active", "motivo": ruim})
            assert r.status_code == 400 and r.json()["detail"]["code"] == "invalid", r.text
        r = await c.put(f"/api/flows/{fid}", json={"status": "active"})               # sem motivo, como antes
        assert r.status_code == 200, r.text
        assert str(harness.state.db.scalar("SELECT reason FROM learning_transitions WHERE item_ref=? ORDER BY id DESC"
                                           " LIMIT 1", (f"fluxo:{fid}",))) == "ligado na lista de fluxos do painel"
        r = await c.post("/api/instances/android-01/training",
                         json={"intent": "x", "lease_id": "l", "nascido_de_prova": "sim"})
        assert r.status_code == 422, r.text                                          # tipo errado no corpo
