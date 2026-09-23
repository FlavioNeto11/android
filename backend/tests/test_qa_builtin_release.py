"""Bootstrap do app de QA embutido (item 6.3, achado #83 — regressão).

`install_apk` deixou de fazer `adb install` direto de `apps.apk_path` e passou a exigir a release PROMOVIDA do
pacote (unificação com a rota de instalação por destino). O QA Messenger era o único app com `apk_path` e nunca
tinha sido importado como release — o botão "Instalar" nele passou a recusar com "nenhuma versão promovida", e é
exatamente o app do aceite 4 (instalar e abrir um app diferente do Instagram, sem tocar em conta real).

Este arquivo protege `ReleaseService.ensure_builtin_release` (o bootstrap em si, testado isolado, sem precisar de
aparelho nem de scheduler) e, de ponta a ponta, que o verbo do painel volta a funcionar depois da subida.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import httpx
import pytest

from app.config import load_config
from app.devices.sdk import SdkTools
from app.models import InstanceState, ReleaseChannel, ReleaseState

from .conftest import Harness
from .test_app_releases import QA_APK, StubInspector, build_service, part

PACKAGE = "com.pocqa.messenger"


def _apk_embutido(tmp_path: Path) -> Path:
    """O caminho que `apps[].apk_path` aponta na configuração real: `qa-app/dist/qa-messenger.apk`."""
    destino = tmp_path / "qa-app" / "dist" / "qa-messenger.apk"
    destino.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(QA_APK, destino)
    return destino


def _inspector_de_mentira() -> StubInspector:
    return StubInspector({"qa-messenger.apk": part(package_name=PACKAGE, version_code=1, version_name="1.0.0")})


# ---------------------------------------------------------------- ReleaseService.ensure_builtin_release, isolado
def test_primeira_subida_importa_e_promove(tmp_path: Path) -> None:
    apk = _apk_embutido(tmp_path)
    svc, repo, _db = build_service(tmp_path, _inspector_de_mentira())

    outcome = svc.ensure_builtin_release(package_name=PACKAGE, apk_path=apk, app_label="QA Messenger")

    assert outcome is not None and outcome.ok, outcome
    row = repo.release_row(outcome.release_id)
    assert row is not None
    assert row["status"] == ReleaseState.installable.value, row["detail"]
    assert row["channel"] == ReleaseChannel.promoted.value, row["channel_detail"]
    # motivo tem de estar escrito, não só o estado: é o que a tela de Aplicativos mostra a quem perguntar por quê.
    assert "builtin" in (row["channel_detail"] or "")
    # o APK está versionado no repositório (git ls-files confirma): o bootstrap NUNCA pode apagá-lo — `import_dir`
    # apaga a origem, e é exatamente por isso que o bootstrap não usa `import_dir`.
    assert apk.is_file()


def test_segunda_subida_nao_duplica_nem_repromove(tmp_path: Path) -> None:
    apk = _apk_embutido(tmp_path)
    svc, repo, _db = build_service(tmp_path, _inspector_de_mentira())

    primeira = svc.ensure_builtin_release(package_name=PACKAGE, apk_path=apk, app_label="QA Messenger")
    assert primeira is not None and primeira.ok

    segunda = svc.ensure_builtin_release(package_name=PACKAGE, apk_path=apk, app_label="QA Messenger")

    assert segunda is None, "sem release nova nem repromoção: subir de novo é no-op"
    releases = repo.list_releases(PACKAGE)
    assert len(releases) == 1
    assert releases[0].id == primeira.release_id


def test_sem_o_arquivo_o_backend_sobe_e_registra_o_porque(tmp_path: Path) -> None:
    svc, repo, db = build_service(tmp_path, StubInspector({}))
    ausente = tmp_path / "qa-app" / "dist" / "qa-messenger.apk"      # nunca criado
    assert not ausente.exists()

    outcome = svc.ensure_builtin_release(package_name=PACKAGE, apk_path=ausente, app_label="QA Messenger")

    assert outcome is None                        # nunca levanta: é conveniência, não pré-condição de arranque
    assert repo.list_releases(PACKAGE) == []
    avisos = db.query("SELECT message FROM events WHERE level='warn' ORDER BY id DESC LIMIT 1")
    assert avisos and "QA Messenger" in avisos[0]["message"] and "não encontrado" in avisos[0]["message"]


# ---------------------------------------------------------------- de ponta a ponta: o verbo do painel
async def test_install_apk_volta_a_funcionar_no_qa_messenger_apos_a_subida(tmp_path: Path) -> None:
    """O caminho que hoje recusa com "promovida": sem esta correção, `POST .../actions/install_apk` falha com
    `sem_versao_promovida` porque o QA Messenger nunca virou release. Depois da subida (bootstrap em
    `AppState.__init__`), o mesmo verbo tem de cair na camada de releases e suceder.
    """
    tools = SdkTools(load_config())
    if not tools.can_inspect_apk():
        pytest.skip("aapt2/apksigner não instalados nesta máquina")

    h = Harness(tmp_path, 2)
    _apk_embutido(tmp_path)
    for a in h.cfg.file.apps:
        if a.id == "qa-messenger":
            a.apk_path = "qa-app/dist/qa-messenger.apk"        # mesmo caminho relativo da configuração real

    await h.boot()
    s = h.state
    assert s is not None
    try:
        # A subida já tem de ter deixado uma release promovida: é o que o bootstrap promete.
        promovida = s.releases.promoted_release(PACKAGE)
        assert promovida is not None, "bootstrap não deixou release promovida do QA Messenger"

        rt = s.devices.devices["android-01"]
        rt.state = InstanceState.online            # instances.default_app já é qa-messenger em make_config

        pedidos: list[tuple[str, str]] = []

        async def instalar(rt_: object, release_id: str, _installer: object, **_kw: object) -> dict:
            pedidos.append((rt_.id, release_id))  # type: ignore[attr-defined]
            return {"ok": True}

        s.releases.install_on = instalar  # type: ignore[assignment]

        from app.main import create_app

        app = create_app(h.cfg, state=s)
        app.state.poc = s
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/api/instances/android-01/actions/install_apk", json={"confirm": True})
            assert r.status_code == 202, r.text
            cid = r.json()["command_id"]
            await h.wait(lambda: s.commands.get(cid)["state"] in ("failed", "succeeded", "uncertain"),
                        what="o verbo fechar o comando")
        linha = s.commands.get(cid)
        assert linha["state"] == "succeeded", linha["reason"]
        assert pedidos == [("android-01", promovida.id)]
    finally:
        await s.stop()
