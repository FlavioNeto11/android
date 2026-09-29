"""Item 23.4 — sessão por conta na composição: a porta de sessão, a invalidação, a tela que desmente a sessão, a
reobservação depois do controle manual, a rota de Conectar/Verificar e o canal de comandos resolvem a CONTA da persona
NO APP do pacote, e nada do segundo app de login gerenciado escreve na conta do primeiro.

O segundo app é o Correio de Exemplo de `test_sessao_declarada.py`, registrado aqui SÓ por arquivos de dado
(`manifesto_da_pasta`) e tirado no fim — o motor de sessão dele é o genérico, fabricado com as dependências da
composição. O primeiro é o app âncora de sempre.

Nível de prova: `simulated` — Harness (porta base 5640), aparelhos falsos, provedores com `ensure_session` gravando
as chamadas. Nenhum aparelho, conta ou IA real.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import yaml
from pydantic import SecretStr

from app.integrations.app_declarado.pacote import manifesto_da_pasta
from app.main import create_app
from app.models import InstalledAppState, ProfileAccountCreate, ProfileCreate, ProfilePatch, SessionStatus
from app.modules.execution.infrastructure.command_bus import _Recusa, command_bus
from app.planning.catalog import capabilities_of, register_manifest, unregister
from app.social.service import SocialError
from app.state import AppState
from app.util import now_iso

from .conftest import Harness
from .fake_instagram import PKG as IG
from .test_localidade_do_perfil import OUTRO, _inscrever_worker, _mudar_de_maquina
from .test_sessao_declarada import CORREIO, _gravar_correio

IID = "android-01"


@pytest.fixture
def correio_registrado(tmp_path: Path) -> Iterator[None]:
    """O correio como app de login gerenciado, só com dado: `app.yaml` + `telas.yaml` + `sessao.yaml`."""
    raiz = tmp_path / "apps"
    pasta = _gravar_correio(raiz / CORREIO)
    (pasta / "app.yaml").write_text(yaml.safe_dump({"app": CORREIO, "nome": "Correio de Exemplo",
                                                    "rotulo": "Correio de Exemplo", "provedor_de_sessao": "correio"},
                                                   allow_unicode=True), encoding="utf-8")
    assert capabilities_of(CORREIO).session_provider is None, "o correio já estava registrado: teste contaminado"
    register_manifest(manifesto_da_pasta(pasta, raiz=raiz))
    try:
        yield
    finally:
        unregister(CORREIO)


def estado(h: Harness) -> AppState:
    assert h.state is not None
    return h.state


def persona_com_duas_contas(s: AppState, instance_id: str = IID) -> tuple[str, str, str]:
    """(persona, conta âncora, conta do correio): cada uma com a sua senha e o seu @."""
    if s.db.scalar("SELECT id FROM apps WHERE package=?", (CORREIO,)) is None:
        s.db.execute("INSERT INTO apps(id, name, package, builtin) VALUES ('correio', 'Correio de Exemplo', ?, 0)",
                     (CORREIO,))
    pid = s.social.create_profile(ProfileCreate(username="ana.ancora", password="Ancora#Senha1",
                                                instance_id=instance_id)).id
    conta = s.social.add_account(pid, ProfileAccountCreate(app_id="correio", handle="ana.correio",
                                                           password=SecretStr("Correio#Senha1"), consent=True)).id
    ancora = s.social_repo.conta_ancora(pid)
    assert ancora is not None
    return pid, str(ancora["id"]), conta


def gravador(chamadas: list[tuple[str, str, dict[str, Any]]], quem: str) -> Callable[..., Awaitable[None]]:
    async def ensure_session(rt: Any, profile_id: str, **kw: Any) -> None:
        chamadas.append((quem, profile_id, kw))
    return ensure_session


def ouvir_os_dois(s: AppState) -> list[tuple[str, str, dict[str, Any]]]:
    """Troca o `ensure_session` dos DOIS provedores (as instâncias da composição) por gravadores."""
    chamadas: list[tuple[str, str, dict[str, Any]]] = []
    correio = s.sessoes.for_package(CORREIO)
    assert correio is not None and correio is not s.instagram and correio.package == CORREIO
    correio.ensure_session = gravador(chamadas, "correio")  # type: ignore[method-assign]
    s.instagram.ensure_session = gravador(chamadas, "ancora")  # type: ignore[union-attr,method-assign]
    return chamadas


def status(s: AppState, pid: str, conta: str) -> str | None:
    linha = s.social_repo.account_session_row(pid, conta, IID)
    return linha["status"] if linha is not None else None


# ==================================================================== a porta de sessão
async def test_a_porta_de_cada_app_confere_e_pede_a_conta_dele(harness: Harness, correio_registrado: None,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    s = estado(harness)
    monkeypatch.setattr(s.sensitive_input, "available", lambda: True)
    rt = s.devices.get(IID)
    pid, ancora, conta = persona_com_duas_contas(s)
    chamadas = ouvir_os_dois(s)
    s.social_repo.set_account_session(pid, ancora, IID, status=SessionStatus.session_ready, verified_at=now_iso())

    assert s._session_gate(rt, IG, pid) is None                      # a âncora está conectada: a tarefa dela passa
    porta = s._session_gate(rt, CORREIO, pid)                         # a do correio nunca foi vista: pede login DELA
    assert porta is not None and porta[1] is not None
    await porta[1]()
    assert chamadas == [("correio", pid, {"account_id": conta, "automatic": True})]

    # O inverso: a conta do correio num desafio segura só a tarefa do correio; a da âncora segue.
    s.social_repo.set_account_session(pid, conta, IID, status=SessionStatus.auth_challenge,
                                      detail="o correio pediu confirmação")
    porta = s._session_gate(rt, CORREIO, pid)
    assert porta is not None and porta[1] is None and "confirmação" in porta[0]
    assert s._session_gate(rt, IG, pid) is None
    # e a credencial do correio em revisão não trava a âncora (a credencial é da conta)
    s.social_repo.mark_account_credential(pid, conta, status="review")
    assert s._session_gate(rt, IG, pid) is None
    assert s.social_repo.account_credential_row(pid, ancora)["status"] == "active"


# ==================================================================== invalidação por app e por aparelho
async def test_mexer_no_app_invalida_so_a_conta_dele_e_o_disco_invalida_todas(harness: Harness,
                                                                              correio_registrado: None) -> None:
    s = estado(harness)
    pid, ancora, conta = persona_com_duas_contas(s)

    def conectar_as_duas() -> None:
        for c in (ancora, conta):
            s.social_repo.set_account_session(pid, c, IID, status=SessionStatus.session_ready, verified_at=now_iso())

    conectar_as_duas()
    s.releases._app_mudou(IID, CORREIO, "o aplicativo foi atualizado neste aparelho")  # noqa: SLF001
    assert (status(s, pid, conta), status(s, pid, ancora)) == ("unknown", "session_ready")
    conectar_as_duas()
    s.releases._app_mudou(IID, IG, "o aplicativo foi atualizado neste aparelho")  # noqa: SLF001
    assert (status(s, pid, conta), status(s, pid, ancora)) == ("session_ready", "unknown")
    # O disco do APARELHO (reset, troca de máquina): a sessão de toda conta ali cai, sem perguntar de que app.
    conectar_as_duas()
    s.devices.on_session_invalidated(IID, "o aparelho foi resetado")  # type: ignore[attr-defined]
    assert (status(s, pid, conta), status(s, pid, ancora)) == ("unknown", "unknown")


# ==================================================================== a tela que desmente a sessão
async def test_a_tela_que_desmente_a_sessao_corrige_a_conta_do_app_da_tela(harness: Harness,
                                                                           correio_registrado: None) -> None:
    s = estado(harness)
    # Uma execução qualquer do aparelho, terminada: o objetivo dela vira o "em curso" e a etapa, a do correio.
    run = harness.run([IID])
    await harness.wait_run(run.id)
    oid = s.db.scalar("SELECT id FROM objectives WHERE run_id=? AND instance_id=?", (run.id, IID))
    passo = s.db.scalar("SELECT id FROM steps WHERE objective_id=? ORDER BY seq LIMIT 1", (oid,))
    assert oid and passo
    pid, ancora, conta = persona_com_duas_contas(s)
    for c in (ancora, conta):
        s.social_repo.set_account_session(pid, c, IID, status=SessionStatus.session_ready, verified_at=now_iso())

    # Quem viu diz o app: só a conta dele muda.
    s._sessao_desmentida(IID, "auth_required", "campo de senha do correio", package=CORREIO)
    assert (status(s, pid, conta), status(s, pid, ancora)) == ("auth_required", "session_ready")

    # Sem o app dito, vale o da etapa em curso no aparelho (o que o executor olhava).
    s.social_repo.set_account_session(pid, conta, IID, status=SessionStatus.session_ready, verified_at=now_iso())
    s.db.execute("UPDATE steps SET app_id='correio' WHERE id=?", (passo,))
    rt = s.devices.get(IID)
    s.scheduler._objetivo_do_worker[IID] = str(oid)  # noqa: SLF001
    rt.current = SimpleNamespace(step_id=passo)  # type: ignore[assignment]
    try:
        assert s._pacote_em_curso(IID) == CORREIO  # noqa: SLF001
        s._sessao_desmentida(IID, "wrong_account", "outra conta aberta no correio")
        assert (status(s, pid, conta), status(s, pid, ancora)) == ("wrong_account", "session_ready")
    finally:
        s.scheduler._objetivo_do_worker.pop(IID, None)  # noqa: SLF001
        rt.current = None

    # Sem app dito nem etapa em curso: o chamador antigo, a conta âncora — a do correio fica como estava.
    s._sessao_desmentida(IID, "auth_required", "campo de senha")
    assert (status(s, pid, conta), status(s, pid, ancora)) == ("wrong_account", "auth_required")


# ==================================================================== reobservação depois do controle manual
async def test_devolver_o_controle_rele_cada_conta_pelo_provedor_dela(harness: Harness,
                                                                      correio_registrado: None) -> None:
    s = estado(harness)
    rt = s.devices.get(IID)
    pid, ancora, conta = persona_com_duas_contas(s)
    chamadas = ouvir_os_dois(s)
    s.social_repo.set_account_session(pid, ancora, IID, status=SessionStatus.session_ready, verified_at=now_iso())
    s.social_repo.set_account_session(pid, conta, IID, status=SessionStatus.auth_challenge, detail="confirmação")
    trabalhos: list[Callable[[], Awaitable[None]]] = []
    original = s.scheduler.run_device_job

    def _registrar(alvo: object, fabrica: Callable[[], Awaitable[None]], *, label: str) -> bool:
        trabalhos.append(fabrica)
        return True

    s.scheduler.run_device_job = _registrar  # type: ignore[method-assign,assignment]
    try:
        s._reobservar_apos_intervencao(rt)  # noqa: SLF001
    finally:
        s.scheduler.run_device_job = original  # type: ignore[method-assign]
    assert len(trabalhos) == 1
    await trabalhos[0]()
    # só a conta que esperava uma pessoa, pelo provedor DO APP dela, sem digitar nada
    assert chamadas == [("correio", pid, {"account_id": conta, "observe_only": True})]


# ==================================================================== rota e canal de comandos
async def test_verificar_a_conta_do_correio_pela_rota_pede_a_conta_dele(harness: Harness,
                                                                        correio_registrado: None) -> None:
    s = estado(harness)
    rt = s.devices.get(IID)
    await harness.wait(lambda: rt.state.value == "online", what="aparelho online")
    pid, ancora, conta = persona_com_duas_contas(s)
    s.release_repo.upsert_app_state(IID, CORREIO, state=InstalledAppState.ready, installed_release_id=None,
                                    observed_version_name="1.0")
    chamadas = ouvir_os_dois(s)
    app = create_app(harness.cfg, state=s)
    app.state.poc = s
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post(f"/api/instagram/profiles/{pid}/accounts/{conta}/session/verify")
        assert r.status_code == 202, r.text
        assert r.json()["account_id"] == conta
    await harness.wait(lambda: len(chamadas) == 1, what="verificação da conta do correio")
    assert chamadas == [("correio", pid, {"account_id": conta, "force_login": False, "observe_only": True})]


async def test_o_canal_de_comandos_resolve_a_conta_do_app_do_pedido(harness: Harness,
                                                                    correio_registrado: None) -> None:
    s = estado(harness)
    rt = s.devices.get(IID)
    pid, ancora, conta = persona_com_duas_contas(s)
    chamadas = ouvir_os_dois(s)
    canal = command_bus(s)
    pedido = {"profile_id": pid, "app_id": "correio"}
    fabrica, _rotulo, params = canal._trabalho(rt, "session.verify", pedido, None)  # noqa: SLF001
    assert params["account_id"] == conta
    await fabrica()
    assert chamadas == [("correio", pid, {"account_id": conta, "observe_only": True})]
    # a conta dita tem de ser daquele app; e a persona sem conta nele não tem sessão a verificar
    with pytest.raises(_Recusa, match="não é da persona"):
        canal._trabalho(rt, "session.verify", {"profile_id": pid, "app_id": "correio",  # noqa: SLF001
                                               "account_id": ancora}, None)
    sem_conta = s.social.create_profile(ProfileCreate(username="bia.ancora", instance_id="android-02")).id
    with pytest.raises(_Recusa, match="não tem conta"):
        canal._trabalho(rt, "session.verify", {"profile_id": sem_conta, "app_id": "correio"}, None)  # noqa: SLF001
    assert len(chamadas) == 1


# ==================================================================== o disco que muda de máquina
async def test_mover_o_aparelho_com_so_a_sessao_do_segundo_app_pede_confirmacao(harness: Harness,
                                                                                correio_registrado: None) -> None:
    """A sessão do correio fica no mesmo disco que a da âncora: mover o aparelho de máquina com SÓ ela pronta pede
    confirmação, e quem confirma recebe as duas invalidadas."""
    s = estado(harness)
    pid, ancora, conta = persona_com_duas_contas(s)
    s.social_repo.set_account_session(pid, conta, IID, status=SessionStatus.session_ready, verified_at=now_iso())
    assert status(s, pid, ancora) != SessionStatus.session_ready.value
    _inscrever_worker(harness)
    app = create_app(harness.cfg, state=s)
    app.state.poc = s
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.put(f"/api/instances/{IID}", json={"worker_id": OUTRO})
        assert r.status_code == 409 and r.json()["detail"]["code"] == "locality_change_requires_confirmation", r.text
        assert "@ana.ancora" in r.json()["detail"]["message"]
        r = await c.put(f"/api/instances/{IID}", json={"worker_id": OUTRO, "confirm_locality_change": True})
        assert r.status_code == 200, r.text
    assert (status(s, pid, conta), status(s, pid, ancora)) == ("unknown", "unknown")


async def test_trocar_a_persona_de_servidor_com_so_a_sessao_do_segundo_app_pede_confirmacao(
        harness: Harness, correio_registrado: None) -> None:
    s = estado(harness)
    pid, ancora, conta = persona_com_duas_contas(s)
    s.social_repo.set_account_session(pid, conta, IID, status=SessionStatus.session_ready, verified_at=now_iso())
    assert s.social_repo.binding(pid, IID)["locality_at"] is not None
    _inscrever_worker(harness)
    _mudar_de_maquina(harness, "android-02", OUTRO)
    with pytest.raises(SocialError) as exc:
        s.social.update_profile(pid, ProfilePatch(instance_id="android-02"))
    assert exc.value.code == "locality_change_requires_confirmation"


async def test_so_conta_de_site_no_app_nao_cai_na_ancora_pela_porta(harness: Harness, correio_registrado: None) -> None:
    """A pessoa com conta do correio só de SITE (`host`) não tem a conta que o login gerenciado abre: a porta recusa
    com "sem conta" antes da localidade — que, sem conta, gravaria a troca de máquina na sessão da conta âncora."""
    s = estado(harness)
    persona_com_duas_contas(s)                                          # registra o app `correio` em `apps`
    bia = s.social.create_profile(ProfileCreate(username="bia.ancora", instance_id="android-02")).id
    s.social.add_account(bia, ProfileAccountCreate(app_id="correio", handle="bia", host="correio.exemplo.com"))
    ancora = s.social_repo.conta_ancora(bia)["id"]
    antes = s.social_repo.account_session_row(bia, ancora, "android-02")
    _inscrever_worker(harness)
    rt = _mudar_de_maquina(harness, "android-02", OUTRO)                # o disco da persona ficou na outra máquina
    porta = s._session_gate(rt, CORREIO, bia)  # noqa: SLF001
    assert porta is not None and porta[1] is None and "não tem conta em Correio de Exemplo" in porta[0]
    assert s.social_repo.account_session_row(bia, ancora, "android-02") == antes
