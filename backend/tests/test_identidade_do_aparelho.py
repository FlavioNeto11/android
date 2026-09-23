"""Estado de app e de sessão com identidade FÍSICA (item 3.2 do plano-100; achados #76, #111, #75).

O defeito, do jeito que doía: tudo o que o central afirmava sobre o disco de um aparelho era gravado por
`instance_id` LÓGICO e nunca invalidado quando o aparelho por baixo daquele id mudava. Em 21/09/2026 o painel
dizia Instagram `ready` em android-09/10 com `verified_at` de 18/09 — escrito quando aqueles ids eram emuladores
desta máquina, um dia antes de serem remapeados para AVDs do notebook, onde o Instagram nem está instalado.

E o outro lado: "Distribuir" era um ato pontual sobre quem existia naquele instante. android-12..15 entraram
depois e ficaram sem linha nenhuma — a porta do app não opinava e a tarefa ia para um aparelho sem o aplicativo.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.models import InstalledAppState, SessionStatus
from app.util import now, to_iso
from datetime import timedelta
from .conftest import Harness

PKG = "com.pocqa.messenger"


def _release(h: Harness, *, version_code: int = 1, promovida: bool = True) -> str:
    """Grava uma release direto no repositório: o que este arquivo prova é a REGRA de estado desejado, não o
    pipeline de importação (que `test_app_releases.py` já cobre)."""
    rid = f"rel-{version_code}"
    h.state.db.execute(
        "INSERT INTO app_releases(id, package_name, version_name, version_code, artifact_type, signature_sha256,"
        " min_sdk, target_sdk, supported_abis, catalog_dir, source_type, imported_at, status, channel)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (rid, PKG, f"1.{version_code}", version_code, "single", "a" * 64, 21, 21, '["x86_64"]', "apks/x",
         "inbox", to_iso(now()), "installable", "promoted" if promovida else "candidate"))
    return rid


# ---------------------------------------------------------------- identidade física
@pytest.mark.asyncio
async def test_troca_do_aparelho_por_baixo_do_id_invalida_app_e_sessao(tmp_path: Path) -> None:
    h = Harness(tmp_path, 2)
    await h.boot()
    try:
        s, d = h.state, h.state.devices
        rt = d.get("android-01")
        rid = _release(h)
        s.release_repo.upsert_app_state(rt.id, PKG, state=InstalledAppState.ready.value,
                                        installed_release_id=rid, desired_release_id=rid,
                                        verified_at=to_iso(now()))
        perfil = s.social_repo.create_profile(username="conta_teste", first_name=None, last_name=None,
                                              display_name=None, birth_date=None, email=None, persona_id=None)
        pid = perfil if isinstance(perfil, str) else perfil["id"]
        s.social_repo.bind(pid, rt.id)
        s.social_repo.set_session(pid, status=SessionStatus.session_ready, instance_id=rt.id,
                                  verified_at=to_iso(now()))

        # 1ª leitura: passa a haver identidade e NADA do que se sabia fica falso.
        assert d.conferir_identidade(rt, "worker-lan-01|ro.boot.qemu.avd_name=worker-01") is False
        assert s.release_repo.app_state(rt.id, PKG)["state"] == InstalledAppState.ready.value

        # Mesma identidade de novo: nada acontece (o aparelho continua o mesmo).
        assert d.conferir_identidade(rt, "worker-lan-01|ro.boot.qemu.avd_name=worker-01") is False
        # Não se observou nada: falta de informação nunca invalida.
        assert d.conferir_identidade(rt, None) is False
        assert s.release_repo.app_state(rt.id, PKG)["state"] == InstalledAppState.ready.value

        # O aparelho por trás do id MUDOU: é isto que android-09/10 viveram calados.
        assert d.conferir_identidade(rt, "worker-lan-01|ro.boot.qemu.avd_name=worker-09") is True
        assert s.release_repo.app_state(rt.id, PKG)["state"] == InstalledAppState.missing.value
        assert s.release_repo.app_state(rt.id, PKG)["installed_release_id"] is None
        assert s.social_repo.session_row(pid)["status"] == SessionStatus.unknown.value
        assert s.db.scalar("SELECT physical_id FROM instances WHERE id=?", (rt.id,)) \
            == "worker-lan-01|ro.boot.qemu.avd_name=worker-09"
    finally:
        await h.state.stop()


# ---------------------------------------------------------------- dado velho
@pytest.mark.asyncio
async def test_dado_velho_e_reobservado_quando_o_aparelho_entra_no_ar(tmp_path: Path) -> None:
    h = Harness(tmp_path, 2)
    await h.boot()
    try:
        s = h.state
        rid = _release(h)
        antigo = to_iso(now() - timedelta(hours=72))          # a idade real do "ready" de android-09/10
        s.release_repo.upsert_app_state("android-01", PKG, state=InstalledAppState.ready.value,
                                        installed_release_id=rid, verified_at=antigo)
        s.release_repo.upsert_app_state("android-02", PKG, state=InstalledAppState.ready.value,
                                        installed_release_id=rid, verified_at=to_iso(now()))
        assert s.pacotes_com_dado_velho("android-01") == [PKG]
        assert s.pacotes_com_dado_velho("android-02") == []

        relidos: list[tuple[str, str]] = []

        async def _verify(rt: Any, package: str, _inst: Any) -> dict:
            relidos.append((rt.id, package))
            return {}

        s.releases.verify_on = _verify  # type: ignore[assignment]
        s._reobservar_se_velho("android-01")
        await h.wait(lambda: relidos == [("android-01", PKG)], 5, "reobservação do app")
    finally:
        await h.state.stop()


@pytest.mark.asyncio
async def test_nunca_verificado_tambem_e_dado_velho(tmp_path: Path) -> None:
    h = Harness(tmp_path, 1)
    await h.boot()
    try:
        s = h.state
        rid = _release(h)
        # `ready` sem `verified_at` nenhum: marcado por uma instalação e nunca mais olhado.
        s.release_repo.upsert_app_state("android-01", PKG, state=InstalledAppState.ready.value,
                                        installed_release_id=rid, verified_at=None)
        assert s.pacotes_com_dado_velho("android-01") == [PKG]
    finally:
        await h.state.stop()


# ---------------------------------------------------------------- versão promovida como estado desejado
@pytest.mark.asyncio
async def test_aparelho_que_entra_depois_recebe_a_versao_promovida(tmp_path: Path) -> None:
    h = Harness(tmp_path, 2)
    await h.boot()
    try:
        s = h.state
        rid = _release(h)
        rt = s.devices.get("android-02")
        assert s.release_repo.app_state(rt.id, PKG) is None       # como android-12..15: sem linha nenhuma
        assert s.aplicar_versao_promovida(rt) == rid
        assert s.release_repo.app_state(rt.id, PKG)["desired_release_id"] == rid
        # Idempotente: chamar de novo não reescreve nem emite nada.
        assert s.aplicar_versao_promovida(rt) is None
    finally:
        await h.state.stop()


@pytest.mark.asyncio
async def test_versao_promovida_nao_rearma_entrega_que_falhou_nem_alcanca_a_loja(tmp_path: Path) -> None:
    h = Harness(tmp_path, 3, store="android-03")
    await h.boot()
    try:
        s = h.state
        rid = _release(h)
        s.release_repo.upsert_app_state("android-01", PKG, state="install_failed", detail="assinatura recusada")
        assert s.aplicar_versao_promovida(s.devices.get("android-01")) is None
        # Entrega que falhou HOJE (há comando de app recente) continua sem retry cego...
        s.commands.create(command_id="c-hoje", instance_id="android-01", verb="app.distribute", idempotency_key="k1",
                          requested_by="panel")
        assert s.aplicar_versao_promovida(s.devices.get("android-01")) is None
        # ...e volta a ser tentada UMA vez por dia: com o último comando de app há mais de 24 h, rearma.
        s.db.execute("UPDATE commands SET created_at='2026-09-01T00:00:00Z' WHERE id='c-hoje'")
        assert s.aplicar_versao_promovida(s.devices.get("android-01")) == rid
        assert s.release_repo.app_state("android-01", PKG)["state"] == "missing"
        # A loja é a FONTE do aplicativo: ela nunca recebe entrega do parque.
        assert s.aplicar_versao_promovida(s.devices.get("android-03")) is None
    finally:
        await h.state.stop()


@pytest.mark.asyncio
async def test_versao_apenas_candidata_nao_vira_estado_desejado(tmp_path: Path) -> None:
    """Só se distribui versão PROMOVIDA: sem prova, não há o que adotar."""
    h = Harness(tmp_path, 1)
    await h.boot()
    try:
        s = h.state
        _release(h, promovida=False)
        assert s.aplicar_versao_promovida(s.devices.get("android-01")) is None
        assert s.release_repo.app_state("android-01", PKG) is None
    finally:
        await h.state.stop()
