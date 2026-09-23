"""Localidade de perfil — E9 (item 4.4 do plano-100; achados #45, #69).

O defeito, do jeito que doía: os dados de um perfil — a sessão do Instagram — vivem na partição de dados do
aparelho, no disco de UMA máquina. O banco só conhecia o id LÓGICO (`device_profile_bindings.instance_id`), e o
id lógico muda de máquina por configuração: um PUT em `instances.worker_id`, ou uma linha nova em
`instances.external`, reaponta `android-09` para outro computador sem que nada no perfil perceba. A sessão seguia
`session_ready` em cache, a porta do despacho deixava passar, e a tarefa ia para um aparelho onde aquela conta
nunca fez login. É a frase do dono — "perfil armazenado num servidor NÃO está automaticamente disponível em
outro" — que até aqui não tinha modelo no código.

O que estes testes protegem:

* o vínculo FOTOGRAFA onde os dados foram gravados (máquina + impressão digital do aparelho);
* o id lógico que muda de servidor invalida a sessão e BLOQUEIA o despacho, com motivo legível;
* a política do perfil decide: `wait` (padrão) espera; `reauth_elsewhere` é decisão de pessoa e libera o login;
* o que não se sabe nunca invalida nada — vínculo sem localidade registrada não acusa troca;
* trocar o perfil de servidor, ou mover o aparelho de máquina, exige confirmação explícita quando há sessão;
* o DTO diz em que servidor o perfil vive, e quando aquele servidor está fora.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from app.main import create_app
from app.models import SessionStatus
from app.social.service import SocialError
from app.util import now, to_iso
from .conftest import Harness

OUTRO = "worker-lan-02"


def _perfil(h: Harness, instance_id: str, *, username: str = "conta_teste") -> str:
    """Perfil vinculado com sessão pronta — o estado em que a troca de servidor realmente dói."""
    s = h.state
    pid = s.social_repo.create_profile(username=username, first_name=None, last_name=None, display_name=None,
                                       birth_date=None, email=None, persona_id=None)
    s.social_repo.bind(pid, instance_id, reason="teste")
    s.social_repo.set_session(pid, status=SessionStatus.session_ready, instance_id=instance_id,
                              verified_at=to_iso(now()), detail=f"@{username} confirmado na tela")
    return pid


def _inscrever_worker(h: Harness, worker_id: str = OUTRO, *, state: str = "online") -> None:
    h.state.db.execute(
        "INSERT INTO workers(id, name, state, appium_mode, max_slots, enrolled_at, token_hash)"
        " VALUES (?,?,?,?,?,?,?)",
        (worker_id, "Notebook da sala", state, "central", 2, to_iso(now()), "hash-de-teste"))


def _mudar_de_maquina(h: Harness, instance_id: str, worker_id: str | None) -> Any:
    """O que um PUT de `worker_id` faz no banco e no runtime — sem passar pela recusa da API."""
    h.state.db.execute("UPDATE instances SET worker_id=? WHERE id=?", (worker_id, instance_id))
    rt = h.state.devices.devices[instance_id]
    rt.worker_id = worker_id
    return rt


@pytest.mark.asyncio
async def test_o_vinculo_fotografa_o_servidor_e_o_aparelho(tmp_path: Path) -> None:
    h = Harness(tmp_path, 2)
    await h.boot()
    try:
        _inscrever_worker(h)
        _mudar_de_maquina(h, "android-01", OUTRO)
        h.state.db.execute("UPDATE instances SET physical_id=? WHERE id=?", ("worker-lan-02|avd=w1", "android-01"))

        pid = _perfil(h, "android-01")

        vinculo = h.state.social_repo.binding_row(pid)
        assert vinculo["worker_id"] == OUTRO
        assert vinculo["physical_id"] == "worker-lan-02|avd=w1"
        assert vinculo["locality_at"] is not None
        loc = h.state.social_repo.profile_dto(pid).locality
        assert loc.worker_id == OUTRO and loc.worker_name == "Notebook da sala"
        assert loc.known and loc.available and not loc.moved
    finally:
        await h.state.stop()


@pytest.mark.asyncio
async def test_id_que_muda_de_servidor_bloqueia_o_despacho_e_invalida_a_sessao(tmp_path: Path) -> None:
    """O caso do achado #45: o perfil continuava `session_ready` num aparelho que virou de outra máquina."""
    h = Harness(tmp_path, 2)
    await h.boot()
    try:
        s = h.state
        pid = _perfil(h, "android-01")          # gravado aqui, no servidor local
        _inscrever_worker(h)
        rt = _mudar_de_maquina(h, "android-01", OUTRO)

        porta = s._session_gate(rt)
        assert porta is not None, "a porta tinha de recusar: os dados não estão nesta máquina"
        motivo, trabalho = porta
        assert trabalho is None, "esperar é a política padrão: ninguém refaz login sozinho em outro servidor"
        assert "outro servidor" in motivo and "android-01" in motivo
        assert s.social_repo.session_row(pid)["status"] == SessionStatus.unknown.value
        assert s.social_repo.profile_dto(pid).locality.moved is True
    finally:
        await h.state.stop()


@pytest.mark.asyncio
async def test_politica_de_reautenticar_em_outro_devolve_o_caso_ao_login_automatico(tmp_path: Path) -> None:
    h = Harness(tmp_path, 2)
    await h.boot()
    try:
        s = h.state
        pid = _perfil(h, "android-01")
        s.social_repo.update_profile(pid, {"offline_policy": "reauth_elsewhere"})
        _inscrever_worker(h)
        rt = _mudar_de_maquina(h, "android-01", OUTRO)

        porta = s._session_gate(rt)
        # A sessão de lá não existe, então a porta continua recusando o DESPACHO — mas pela porta de sessão, que
        # sabe pedir login. É a diferença entre "espere o servidor voltar" e "entre de novo aqui, por decisão de
        # pessoa": a localidade não é mais o motivo, e o perfil passa a viver na máquina nova.
        assert porta is not None
        assert "autorize a reautenticação" not in porta[0]
        assert s.social_repo.binding_row(pid)["worker_id"] == OUTRO, "a localidade passa a ser a máquina nova"
        assert s.social_repo.session_row(pid)["status"] == SessionStatus.unknown.value
    finally:
        await h.state.stop()


@pytest.mark.asyncio
async def test_aparelho_fisico_trocado_sob_o_mesmo_id_bloqueia(tmp_path: Path) -> None:
    h = Harness(tmp_path, 2)
    await h.boot()
    try:
        s = h.state
        s.db.execute("UPDATE instances SET physical_id=? WHERE id=?", ("local|avd=android-01", "android-01"))
        pid = _perfil(h, "android-01")
        rt = s.devices.devices["android-01"]
        rt.physical_id = "local|avd=OUTRO-AVD"          # o AVD por baixo do id mudou

        porta = s._session_gate(rt)
        assert porta is not None and porta[1] is None
        assert "aparelho físico" in porta[0]
        assert s.social_repo.session_row(pid)["status"] == SessionStatus.unknown.value
    finally:
        await h.state.stop()


@pytest.mark.asyncio
async def test_localidade_nao_registrada_nunca_acusa_troca(tmp_path: Path) -> None:
    """Vínculo anterior à migração 023: falta de registro não é prova de troca, e não pode virar bloqueio."""
    h = Harness(tmp_path, 2)
    await h.boot()
    try:
        s = h.state
        pid = _perfil(h, "android-01")
        s.db.execute("UPDATE device_profile_bindings SET locality_at=NULL, worker_id=NULL WHERE profile_id=?",
                     (pid,))
        _inscrever_worker(h)
        rt = _mudar_de_maquina(h, "android-01", OUTRO)

        assert s._session_gate(rt) is None, "sem localidade registrada a porta antiga continua valendo"
        assert s.social_repo.session_row(pid)["status"] == SessionStatus.session_ready.value
        assert s.social_repo.profile_dto(pid).locality.known is False
    finally:
        await h.state.stop()


@pytest.mark.asyncio
async def test_impressao_digital_lida_depois_preenche_a_localidade(tmp_path: Path) -> None:
    """No instante do vínculo o aparelho costuma estar desligado: o que se soube depois passa a valer."""
    h = Harness(tmp_path, 2)
    await h.boot()
    try:
        s = h.state
        pid = _perfil(h, "android-01")
        assert s.social_repo.binding_row(pid)["physical_id"] is None
        rt = s.devices.devices["android-01"]
        rt.physical_id = "local|avd=android-01"

        assert s._session_gate(rt) is None
        assert s.social_repo.binding_row(pid)["physical_id"] == "local|avd=android-01"
    finally:
        await h.state.stop()


@pytest.mark.asyncio
async def test_trocar_o_perfil_de_servidor_exige_confirmacao(tmp_path: Path) -> None:
    h = Harness(tmp_path, 3)
    await h.boot()
    try:
        s = h.state
        pid = _perfil(h, "android-01")
        _inscrever_worker(h)
        _mudar_de_maquina(h, "android-02", OUTRO)

        class Patch:
            def model_dump(self, **_: Any) -> dict[str, Any]:
                return {"instance_id": "android-02"}

        with pytest.raises(SocialError) as exc:
            s.social.update_profile(pid, Patch())
        assert exc.value.code == "locality_change_requires_confirmation"
        assert s.social_repo.binding_row(pid)["instance_id"] == "android-01", "nada mudou na recusa"

        class PatchConfirmado(Patch):
            def model_dump(self, **_: Any) -> dict[str, Any]:
                return {"instance_id": "android-02", "confirm_locality_change": True}

        dto = s.social.update_profile(pid, PatchConfirmado())
        assert dto.instance_id == "android-02" and dto.locality.worker_id == OUTRO
        assert s.social_repo.session_row(pid)["status"] == SessionStatus.unknown.value

        # Trocar de aparelho DENTRO da mesma máquina nunca foi este caso: segue sem confirmação.
        s.social_repo.set_session(pid, status=SessionStatus.session_ready, instance_id="android-02",
                                  verified_at=to_iso(now()), detail="confirmado")
        _mudar_de_maquina(h, "android-03", OUTRO)

        class ParaIrmao(Patch):
            def model_dump(self, **_: Any) -> dict[str, Any]:
                return {"instance_id": "android-03"}

        assert s.social.update_profile(pid, ParaIrmao()).instance_id == "android-03"
    finally:
        await h.state.stop()


@pytest.mark.asyncio
async def test_mover_o_aparelho_de_maquina_pela_api_exige_confirmacao(tmp_path: Path) -> None:
    h = Harness(tmp_path, 2)
    await h.boot()
    try:
        s = h.state
        pid = _perfil(h, "android-01", username="mora_aqui")
        # Aparelho local: `worker_id` já é o id DESTE servidor (o central também é um worker).
        aqui = s.devices.devices["android-01"].worker_id
        _inscrever_worker(h)
        app = create_app(h.cfg, state=s)
        app.state.poc = s
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            r = await c.put("/api/instances/android-01", json={"worker_id": OUTRO})
            assert r.status_code == 409, r.text
            assert r.json()["detail"]["code"] == "locality_change_requires_confirmation"
            assert "@mora_aqui" in r.json()["detail"]["message"]
            assert s.devices.devices["android-01"].worker_id == aqui, "a recusa não pode ter movido nada"

            r = await c.put("/api/instances/android-01",
                            json={"worker_id": OUTRO, "confirm_locality_change": True})
            assert r.status_code == 200, r.text
            assert s.devices.devices["android-01"].worker_id == OUTRO
            # Quem confirma recebe a sessão invalidada no mesmo movimento: o disco ficou na máquina antiga.
            assert s.social_repo.session_row(pid)["status"] == SessionStatus.unknown.value

            perfil = next(p for p in (await c.get("/api/instagram/profiles")).json() if p["id"] == pid)
            assert perfil["locality"]["worker_id"] == aqui, "os dados continuam onde foram gravados"
            assert perfil["locality"]["moved"] is True
            assert perfil["offline_policy"] == "wait"
    finally:
        await h.state.stop()


@pytest.mark.asyncio
async def test_servidor_fora_do_ar_deixa_o_perfil_indisponivel_no_dto(tmp_path: Path) -> None:
    h = Harness(tmp_path, 2)
    await h.boot()
    try:
        s = h.state
        _inscrever_worker(h, state="offline")
        _mudar_de_maquina(h, "android-01", OUTRO)
        pid = _perfil(h, "android-01")

        loc = s.social_repo.profile_dto(pid).locality
        assert loc.available is False and loc.moved is False
        assert "offline" in loc.detail and "Notebook da sala" in loc.detail
    finally:
        await h.state.stop()


@pytest.mark.asyncio
async def test_a_porta_nao_repete_o_aviso_a_cada_volta_do_agendador(tmp_path: Path) -> None:
    """A porta é consultada a cada tick enquanto o item está bloqueado: reescrever a sessão e emitir o mesmo
    aviso toda vez encheria o histórico do aparelho com a mesma linha."""
    h = Harness(tmp_path, 2)
    await h.boot()
    try:
        s = h.state
        pid = _perfil(h, "android-01")
        _inscrever_worker(h)
        rt = _mudar_de_maquina(h, "android-01", OUTRO)

        def avisos() -> int:
            return int(s.db.scalar(
                "SELECT COUNT(*) FROM events WHERE instance_id=? AND message LIKE '%perfil bloqueado%'",
                ("android-01",)) or 0)

        assert s._session_gate(rt) is not None
        primeiro = avisos()
        assert primeiro == 1
        marca = s.social_repo.session_row(pid)["updated_at"]

        for _ in range(5):
            assert s._session_gate(rt) is not None
        assert avisos() == primeiro, "o mesmo bloqueio não vira cinco linhas no histórico"
        assert s.social_repo.session_row(pid)["updated_at"] == marca, "sessão não é reescrita à toa"
    finally:
        await h.state.stop()
