"""Quarentena do aparelho com conta travada, sem reset automático, rastro de status e aviso na saúde (ADR-055).

O dono, 28/09/2026: "toda vez que para na tela de confirmar se você é humano é uma confirmação que a conta está
bloqueada; essa é uma das formas de perder a conta". Cinco das oito contas do Instagram estão bloqueadas (beatriz,
felipe, juliana, mariana, thiago). A investigação achou buracos que deixavam a regra dele sem efeito, e cada bloco
daqui reproduz um:

1. a plataforma não sabia, POR APARELHO, que ali estava logada uma conta travada: o android-04 ficou no ar com o
   felipe no desafio e sem vínculo, e aceitaria outra persona (vínculo, troca de aparelho, cadastro);
2. os comandos do painel (open_app, home, back, restart, reset, app.distribute), as entregas ao ligar e o reinício
   por interrupção não conferiam nada — em 27/09 01:47Z um `open_app` no android-04 com o felipe já bloqueado;
3. o 3º degrau da escada de reparo era `reset` sem olhar a conta logada (apagou a sessão do andre em 24/09);
4. mudar o status do perfil não deixava evento, nem quando, nem por quê, nem de onde;
5. `/api/health` não acusava conta travada logada em aparelho ligado;
6. `instances.account_label` desatualizado (qa-user-04 no android-04, que tem o felipe) enganou um experimento.

Prova: `simulated` (harness com aparelho falso). A migração roda em SQLite de verdade; no PostgreSQL só quando
`TEST_DATABASE_URL` existe — sem ela, do PostgreSQL só a renderização do dialeto é conferida.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import AsyncIterator

import httpx
import pytest
import pytest_asyncio

from app import db as db_mod
from app.commands import despacho
from app.commands.despacho import DespachoRecusado, remediar
from app.db import INTEGRITY_ERRORS, Database
from app.main import create_app
from app.models import ControlOwner, InstanceState, ProfilePatch
from app.modules.identity.application.session_rules import bloquear_por_desafio
from app.modules.identity.presentation.schemas import PersonaDeviceBody, ProfileCreate
from app.social.repository import AparelhoEmQuarentena
from app.social.service import SocialError
from app.vitrine import pendentes_ao_ligar, trabalho_ao_ligar

from .conftest import Harness
from .test_db import _banco, _copia_das_migracoes, _Falso, _tem_indice
from .test_loja_de_apps import estado, falsificar, ligar, versao

NOVA = "054_protecao_de_contas"
ORIGEM = Path(db_mod.__file__).resolve().parents[1] / "migrations"
TS = "2026-09-28T12:00:00.000Z"
FELIPE = "felipe.nogueira93762026"
#: 28/09/2026 21:36 no horário da máquina central (E. South America, -03:00): o instante em que o dono viu o desafio.
VISTO_PELO_DONO = "2026-09-29T00:36:00.000Z"


def _anterior() -> str:
    """A última migração antes da 054 que existe NESTA árvore (calculada: outras ondas podem numerar em paralelo)."""
    return max(f.stem for f in ORIGEM.glob("*.sql") if f.stem < NOVA)


# ============================================================ 0) a migração 054, nos dois bancos
def _semear_central(db: Database) -> None:
    """O retrato do ambiente central antes da 054: o android-04 no parque, o felipe bloqueado e sem vínculo."""
    db.execute("INSERT INTO apps(id, name, package, builtin)"
               " VALUES ('instagram','Instagram','com.instagram.android',1)")
    db.execute("INSERT INTO instances(id, idx, avd_name, console_port, system_port, mjpeg_port, chromedriver_port,"
               " app_id, account_label) VALUES (?,?,?,?,?,?,?,?,?)",
               ("android-04", 4, "android-04", 5560, 8203, 9203, 9518, "instagram", "qa-user-04"))
    db.execute("INSERT INTO instagram_profiles(id, username, status, created_at, updated_at) VALUES (?,?,?,?,?)",
               ("ig-felipe", FELIPE, "blocked", TS, TS))
    db.execute("INSERT INTO profile_accounts(id, profile_id, app_id, handle, status, created_at, updated_at)"
               " VALUES (?,?,?,?,?,?,?)", ("c-felipe", "ig-felipe", "instagram", FELIPE, "active", TS, TS))


def test_atualizacao_para_054_registra_o_marcador_do_android_04(tmp_path: Path,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    anterior = _anterior()
    destino = _copia_das_migracoes(tmp_path, monkeypatch, ate=anterior)
    db = _banco(tmp_path)
    try:
        assert db.migrate()[-1] == anterior
        assert "device_locked_accounts" not in db.tables()
        _semear_central(db)
        shutil.copy2(ORIGEM / f"{NOVA}.sql", destino / f"{NOVA}.sql")
        assert db.migrate() == [NOVA]
        assert db.divergencias() == []
        assert {"blocked_at", "blocked_evidence", "blocked_origin"} <= db.columns("instagram_profiles")
        assert "account_label_origin" in db.columns("instances")
        marcadores = db.query("SELECT * FROM device_locked_accounts")
        assert len(marcadores) == 1
        m = marcadores[0]
        assert (m["instance_id"], m["handle"], m["profile_id"], m["app_id"]) == ("android-04", FELIPE, "ig-felipe",
                                                                                 "instagram")
        assert (m["origin"], m["seen_by"], m["since"]) == ("declarado", "dono", VISTO_PELO_DONO)
        assert m["resolved_at"] is None and "Confirm you are human" in (m["evidence"] or "")
        # O perfil já bloqueado NÃO ganha data inventada: quando o bloqueio aconteceu ninguém registrou.
        perfil = db.one("SELECT status, blocked_at, blocked_origin FROM instagram_profiles WHERE id='ig-felipe'")
        assert perfil == {"status": "blocked", "blocked_at": None, "blocked_origin": None}
        assert db.migrate() == []                                    # nada se repete
    finally:
        db.close()


def test_banco_novo_nasce_sem_marcador_e_com_o_mesmo_esquema(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    anterior = _anterior()
    destino = _copia_das_migracoes(tmp_path, monkeypatch, ate=anterior)
    atualizado = _banco(tmp_path, "atualizado.sqlite3")
    atualizado.migrate()
    shutil.copy2(ORIGEM / f"{NOVA}.sql", destino / f"{NOVA}.sql")
    atualizado.migrate()
    novo = _banco(tmp_path, "novo.sqlite3")
    try:
        assert novo.migrate()[-1] == NOVA
        for tabela in ("device_locked_accounts", "instagram_profiles", "instances"):
            assert sorted(novo.columns(tabela)) == sorted(atualizado.columns(tabela)), tabela
        assert _tem_indice(novo, "ux_locked_account_aberto") and _tem_indice(atualizado, "ux_locked_account_aberto")
        # Sem o android-04 e o felipe, a carga não inventa marcador: banco novo e de teste nascem vazios.
        assert novo.scalar("SELECT COUNT(*) FROM device_locked_accounts") == 0
        assert atualizado.scalar("SELECT COUNT(*) FROM device_locked_accounts") == 0
    finally:
        novo.close()
        atualizado.close()


def test_um_marcador_aberto_por_aparelho_e_conta(tmp_path: Path) -> None:
    """O índice parcial é o piso para quem escreve por fora do repositório: dois marcadores ABERTOS da mesma conta
    no mesmo aparelho não convivem; resolvido, o próximo pode entrar (a história fica)."""
    db = _banco(tmp_path)
    db.migrate()
    try:
        sql = ("INSERT INTO device_locked_accounts(instance_id, handle, origin, since, created_at, resolved_at)"
               " VALUES (?,?,?,?,?,?)")
        db.execute(sql, ("android-04", FELIPE, "declarado", TS, TS, None))
        with pytest.raises(INTEGRITY_ERRORS):
            db.execute(sql, ("android-04", FELIPE, "observado", TS, TS, None))
        with pytest.raises(INTEGRITY_ERRORS):                        # origem fora do vocabulário
            db.execute(sql, ("android-05", FELIPE, "achismo", TS, TS, None))
        db.execute("UPDATE device_locked_accounts SET resolved_at=? WHERE instance_id='android-04'", (TS,))
        db.execute(sql, ("android-04", FELIPE, "observado", TS, TS, None))
        assert db.scalar("SELECT COUNT(*) FROM device_locked_accounts WHERE instance_id='android-04'") == 2
    finally:
        db.close()


@pytest.mark.parametrize("dialeto", ["sqlite", "postgres"])
def test_a_054_renderiza_nos_dois_dialetos(dialeto: str) -> None:
    texto = _Falso(dialeto).render((ORIGEM / f"{NOVA}.sql").read_text("utf-8"))
    assert "{{" not in texto and "@dialect" not in texto
    instrucoes = Database._instrucoes(texto)
    assert sum(i.startswith("CREATE TABLE") for i in instrucoes) == 1
    assert sum(i.startswith("ALTER TABLE") and "ADD COLUMN" in i for i in instrucoes) == 4
    assert sum(i.startswith("INSERT INTO device_locked_accounts") for i in instrucoes) == 1
    assert not any("ON CONFLICT" in i for i in instrucoes)          # o SQLite confunde depois de SELECT (049)


# ============================================================ harness
@pytest_asyncio.fixture
async def h(tmp_path: Path) -> AsyncIterator[Harness]:
    harness = Harness(tmp_path, 2)
    await harness.boot()
    try:
        yield harness
    finally:
        if harness.state is not None:
            await harness.state.stop()


async def _cliente(h: Harness) -> httpx.AsyncClient:
    app = create_app(h.cfg, state=h.state)
    app.state.poc = h.state
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _marcar(h: Harness, iid: str = "android-02", handle: str = FELIPE, origem: str = "declarado") -> bool:
    assert h.state is not None
    return h.state.social_repo.marcar_conta_travada(iid, handle, "tela 'Confirm you are human' do Instagram",
                                                    origem, visto_por="dono")


def _eventos(h: Harness, kind: str) -> list[dict[str, object]]:
    assert h.state is not None
    return [json.loads(e["data"]) for e in h.state.db.query("SELECT data FROM events WHERE kind=? ORDER BY id",
                                                            (kind,))]


# ============================================================ 1) vínculo, troca e cadastro no aparelho com marcador
async def test_aparelho_com_marcador_recusa_vinculo_troca_e_cadastro(h: Harness) -> None:
    s = h.state
    assert s is not None
    assert _marcar(h, handle="@Felipe.Nogueira93762026") is True
    assert s.social_repo.conta_travada_no_aparelho("android-02")["handle"] == FELIPE   # normalizado
    lucas = s.social.create_profile(ProfileCreate(username="lucas.almeida9484")).id

    with pytest.raises(SocialError) as e:
        s.social.bind_device(lucas, PersonaDeviceBody(instance_id="android-02", app_id="instagram"))
    assert (e.value.code, e.value.status) == ("aparelho_em_quarentena", 409)
    assert FELIPE in e.value.message
    with pytest.raises(SocialError) as e:
        s.social.update_profile(lucas, ProfilePatch(instance_id="android-02"))
    assert e.value.code == "aparelho_em_quarentena"
    # Cadastro com o aparelho: recusado ANTES de qualquer linha — nada nasce pela metade.
    with pytest.raises(SocialError) as e:
        s.social.create_profile(ProfileCreate(username="bruno.ferreira9267", instance_id="android-02"))
    assert e.value.code == "aparelho_em_quarentena"
    assert s.social_repo.profile_by_username("bruno.ferreira9267") is None
    # E o repositório recusa por conta própria, para quem vincula por fora do serviço.
    with pytest.raises(AparelhoEmQuarentena):
        s.social_repo.bind(lucas, "android-02", app_id="instagram")
    assert s.social_repo.profiles_of_instance("android-02") == []
    # Outro aparelho segue normal.
    s.social.bind_device(lucas, PersonaDeviceBody(instance_id="android-01", app_id="instagram"))
    assert s.social.instances_of(lucas) == ["android-01"]


async def test_marcador_sobrevive_ao_desvinculo_e_bloqueia_o_perfil_com_rastro(h: Harness) -> None:
    s = h.state
    assert s is not None
    felipe = s.social.create_profile(ProfileCreate(username=FELIPE, instance_id="android-02")).id
    assert _marcar(h, origem="observado") is True
    assert _marcar(h, origem="observado") is False                  # idempotente: um marcador aberto por conta
    perfil = s.social_repo.profile_row(felipe)
    assert perfil["status"] == "blocked" and perfil["blocked_origin"] == "observado"
    assert perfil["blocked_at"] and "Confirm you are human" in perfil["blocked_evidence"]
    assert [(e["status"], e["origem"], e["autor"]) for e in _eventos(h, "profile.status")] == [
        ("blocked", "observado", "dono")]
    assert [e["instance_id"] for e in _eventos(h, "device.locked_account")] == ["android-02"]

    s.social.update_profile(felipe, ProfilePatch(instance_id=None))  # desvincula
    assert s.social.instances_of(felipe) == []
    marcador = s.social_repo.conta_travada_no_aparelho("android-02")
    assert marcador is not None and marcador["handle"] == FELIPE and marcador["profile_id"] == felipe

    # Só uma pessoa resolve; o marcador resolvido fica como história.
    assert s.social_repo.resolver_conta_travada("android-02", por="dono", nota="conta recuperada") == 1
    assert s.social_repo.conta_travada_no_aparelho("android-02") is None
    assert s.db.scalar("SELECT resolved_by FROM device_locked_accounts WHERE instance_id='android-02'") == "dono"


# ============================================================ 2) verbos, entregas e reinícios no aparelho com marcador
async def test_aparelho_com_marcador_recusa_os_verbos_e_aceita_parar(h: Harness) -> None:
    """27/09 01:47Z: um `open_app` no android-04 com o felipe já bloqueado. Na quarentena, só parar e hibernar."""
    s = h.state
    assert s is not None
    _marcar(h)
    async with await _cliente(h) as c:
        for i, (verbo, corpo) in enumerate((("open_app", {}), ("home", {}), ("back", {}), ("recents", {}),
                                            ("restart", {}), ("reset", {"confirm": True}), ("start", {}),
                                            ("install_apk", {}))):
            r = await c.post(f"/api/instances/android-02/actions/{verbo}",
                             json={"idempotency_key": f"quarentena-{i:04d}", **corpo})
            assert r.status_code == 409, (verbo, r.text)
            detalhe = r.json()["detail"]
            assert detalhe["code"] == "locked_account", (verbo, detalhe)
            assert FELIPE in detalhe["message"]
            assert s.commands.get(detalhe["command_id"])["state"] == "rejected"
        r = await c.post("/api/instances/android-02/actions/stop", json={"idempotency_key": "quarentena-stop-1"})
        assert r.status_code == 202, r.text
        # Hibernar também passa pela quarentena (a recusa, se houver, é de outra regra).
        await h.wait(lambda: s.commands.open_for_instance("android-02") is None, what="stop encerrado")
        r = await c.post("/api/instances/android-02/actions/hibernate", json={"idempotency_key": "quarentena-hib-1"})
        assert r.status_code == 202 or r.json()["detail"]["code"] != "locked_account", r.text
        # O aparelho sem marcador segue normal.
        r = await c.post("/api/instances/android-01/actions/home", json={"idempotency_key": "quarentena-home-01"})
        assert r.status_code == 202, r.text


async def test_confirmacao_explicita_da_pessoa_passa_pela_quarentena(h: Harness) -> None:
    s = h.state
    assert s is not None
    _marcar(h)
    async with await _cliente(h) as c:
        r = await c.post("/api/instances/android-02/actions/home",
                         json={"idempotency_key": "quarentena-conf-1", "confirm_locked_account": True})
        assert r.status_code == 202, r.text
    avisos = [e["message"] for e in s.db.query("SELECT message FROM events WHERE instance_id='android-02'"
                                                " AND level='warn' ORDER BY id")]
    assert any("quarentena" in m and "confirm" in m for m in avisos), avisos
    # O pedido automático nunca carrega a confirmação: o reinício de saúde é recusado e fica no histórico.
    assert despacho.pedir_ciclo_de_vida(s, "android-02", "restart", "teste", requested_by="saude") is None
    ultimo = s.db.one("SELECT verb, state, reason FROM commands WHERE instance_id='android-02'"
                      " ORDER BY created_at DESC, id DESC LIMIT 1")
    assert ultimo["verb"] == "restart" and ultimo["state"] == "rejected" and FELIPE in ultimo["reason"]


async def test_verbos_de_app_e_sessao_recusados_no_aparelho_com_marcador(h: Harness) -> None:
    s = h.state
    assert s is not None
    _marcar(h)
    rt = s.devices.get("android-02")
    chamadas: list[str] = []

    async def fabrica() -> None:
        chamadas.append("tocou")

    r = despacho.pedir_trabalho_de_app(s, rt, "app.distribute", fabrica, label="entrega", params={},
                                       idempotency_key="quarentena-app-0001", requested_by="teste")
    assert r["accepted"] is False and FELIPE in str(r["reason"])
    assert s.commands.get(str(r["command_id"]))["state"] == "rejected"
    for verbo in ("app.install", "app.canary", "app.rollback", "session.connect", "session.verify",
                  "session.logout"):
        with pytest.raises(DespachoRecusado) as e:
            despacho._despachar_trabalho(s, rt, verbo, fabrica, label=verbo)
        assert e.value.code == "locked_account", verbo
    # Ler o que está instalado (inspeção por adb, sem abrir o app) segue permitido.
    r = despacho._despachar_trabalho(s, rt, "app.verify", fabrica, label="verificar")
    assert r["accepted"] is True
    await h.wait(lambda: chamadas == ["tocou"], what="só a verificação rodou")


async def test_portas_do_despacho_bloqueiam_o_aparelho_com_marcador_mesmo_sem_vinculo(h: Harness) -> None:
    """A porta de sessão dizia "aparelho sem perfil vinculado não tem porta" — o caso exato do android-04 — e a porta
    do app vem ANTES dela (instala e abre o app para a prova)."""
    s = h.state
    assert s is not None
    rt = s.devices.get("android-02")
    assert s._session_gate(rt, None, None) is None                  # sem marcador e sem vínculo: sem porta
    _marcar(h)
    porta = s._session_gate(rt, None, None)
    assert porta is not None and porta[1] is None and FELIPE in porta[0]
    porta = s._session_gate(rt, "com.pocqa.messenger", None)
    assert porta is not None and porta[1] is None
    porta = s._app_resolver(rt, "com.instagram.android", {"status": "pending"})
    assert porta is not None and porta[1] is None and FELIPE in porta[0]


async def test_remediar_com_vinculo_ativo_nunca_devolve_reset(h: Harness) -> None:
    """24/09: o 3º degrau (`reset`) apagou a sessão do andre. Com conta vinculada, o 3º degrau é o dono."""
    s = h.state
    assert s is not None
    s.social.create_profile(ProfileCreate(username="andre.carvalho9543", instance_id="android-01"))
    rt = s.devices.get("android-01")
    s.devices.set_desired_state(rt, InstanceState.online.value)
    verbos = await _escada(h, "android-01", 4)
    assert verbos == ["restart", "restart", "stop", None]
    assert "reset" not in [c["verb"] for c in s.db.query("SELECT verb FROM commands WHERE instance_id='android-01'")]
    assert rt.attention and "Precisa do dono" in rt.attention


async def test_remediar_com_marcador_nao_reinicia_e_so_para(h: Harness) -> None:
    s = h.state
    assert s is not None
    _marcar(h, "android-01")
    rt = s.devices.get("android-01")
    s.devices.set_desired_state(rt, InstanceState.online.value)
    assert await _escada(h, "android-01", 3) == ["stop", None, None]
    assert rt.attention and "Precisa do dono" in rt.attention and FELIPE in rt.attention


async def _escada(h: Harness, iid: str, vezes: int) -> list[str | None]:
    """`remediar` N vezes, fechando cada degrau sem curar (o que se testa é a DECISÃO, não o comando)."""
    s = h.state
    assert s is not None
    original = despacho._do_action

    async def _falso(*_a: object, **_k: object) -> None:
        return None

    despacho._do_action = _falso                                     # type: ignore[assignment]
    try:
        verbos: list[str | None] = []
        for _ in range(vezes):
            cid = remediar(s, iid, "o system_server caiu")
            verbos.append(s.commands.get(cid)["verb"] if cid else None)
            if cid:
                s.db.execute("UPDATE commands SET state='failed', finished_at=? WHERE id=?", (TS, cid))
        return verbos
    finally:
        despacho._do_action = original                               # type: ignore[assignment]


async def test_reinicio_por_interrupcao_nao_toca_aparelho_com_marcador(h: Harness) -> None:
    s = h.state
    assert s is not None
    d = s.devices
    rt = d.get("android-01")
    rt.state, rt.attention, rt.control = InstanceState.online, None, ControlOwner.none
    _marcar(h, "android-01")
    pedidos: list[str] = []
    d.on_health_restart = lambda iid, motivo: pedidos.append(iid) or None  # type: ignore[func-returns-value]
    total, irq = 1000.0, 0.0
    for _ in range(6):
        rt.io.pressure = {"load1": 0.5, "mem_total_mb": 2048.0, "mem_available_mb": 900.0, "ncpu": 2.0,
                          "cpu_total_ticks": total, "cpu_irq_ticks": irq}
        await d.conferir_saude(rt)
        total, irq = total + 1000, irq + 400                         # 40% em interrupção, ocioso
    assert pedidos == []
    assert rt.attention and FELIPE in rt.attention


async def test_devolver_o_controle_nao_reabre_a_conta_travada(h: Harness) -> None:
    """Quem olha o desafio na tela assume o controle e depois o devolve: a reobservação automática da sessão
    (achado #106) abriria o app da conta travada sem passar por porta nenhuma."""
    s = h.state
    assert s is not None
    s.social.create_profile(ProfileCreate(username=FELIPE, instance_id="android-01"))   # sessão `unknown`
    rt = s.devices.get("android-01")
    trabalhos: list[str] = []
    original = s.scheduler.run_device_job

    def _registrar(alvo: object, fabrica: object, *, label: str) -> bool:
        trabalhos.append(label)
        return True

    s.scheduler.run_device_job = _registrar                          # type: ignore[method-assign,assignment]
    try:
        s._reobservar_apos_intervencao(rt)
        assert trabalhos == ["reobservação após devolver o controle"]  # sem marcador: relê, como sempre
        trabalhos.clear()
        _marcar(h, "android-01")
        s._reobservar_apos_intervencao(rt)
        assert trabalhos == []
    finally:
        s.scheduler.run_device_job = original                        # type: ignore[method-assign]


async def test_rodizio_nao_liga_sozinho_o_aparelho_com_marcador(h: Harness) -> None:
    """O emulador desta máquina o rodízio liga direto, sem passar pelo pré-voo: a quarentena vale ali também."""
    s = h.state
    assert s is not None
    rt = s.devices.get("android-01")
    await s.devices.stop_instance(rt)
    _marcar(h, "android-01")
    assert s.devices.request_start(rt, "tarefa pendente") is False
    assert rt.state == InstanceState.stopped
    assert rt.attention and "quarentena" in rt.attention and FELIPE in rt.attention
    # O aparelho sem marcador liga como sempre.
    outro = s.devices.get("android-02")
    await s.devices.stop_instance(outro)
    assert s.devices.request_start(outro, "tarefa pendente") is True


async def test_distribuir_e_entregar_ao_ligar_pulam_o_aparelho_com_marcador(tmp_path: Path) -> None:
    parque = Harness(tmp_path, 4, store="android-04")
    await parque.boot()
    try:
        s = parque.state
        assert s is not None
        falsos = falsificar(parque)
        rid = versao(parque)
        _marcar(parque, "android-02")
        saida = {d["id"]: d for d in s.distribute(rid, instance_ids=["android-02", "android-03"])}
        assert saida["android-02"]["outcome"] == "kept" and FELIPE in saida["android-02"]["reason"]
        assert saida["android-03"]["outcome"] == "started"
        assert estado(parque, "android-02") is None                  # nem a versão desejada é gravada
        assert falsos["android-02"].calls == []

        # Entrega ao ligar: a versão já estava marcada ANTES de a conta travar; ligar não a instala.
        rt = s.devices.get("android-01")
        await s.devices.stop_instance(rt)
        s.distribute(rid, instance_ids=["android-01"])
        assert estado(parque, "android-01")["desired_release_id"] == rid
        _marcar(parque, "android-01")
        assert pendentes_ao_ligar(s, rt) == [] and trabalho_ao_ligar(s, rt) is None
        await ligar(parque, rt)
        await parque.wait(lambda: rt.id not in s.scheduler.workers, what="trabalho do ligar encerrado")
        assert "install" not in falsos["android-01"].calls
        assert estado(parque, "android-01")["installed_release_id"] is None
    finally:
        if parque.state is not None:
            await parque.state.stop()


# ============================================================ 4) status do perfil com rastro
async def test_mudanca_de_status_gera_evento_com_origem_e_autor(h: Harness) -> None:
    s = h.state
    assert s is not None
    pid = s.social.create_profile(ProfileCreate(username="beatriz.rocha9276")).id
    s.social.update_profile(pid, ProfilePatch(status="blocked"))
    linha = s.social_repo.profile_row(pid)
    assert linha["status"] == "blocked" and linha["blocked_at"] and linha["blocked_origin"] == "declarado"
    assert linha["blocked_evidence"]
    s.social.update_profile(pid, ProfilePatch(status="blocked"))    # sem mudança: sem evento novo
    s.social.update_profile(pid, ProfilePatch(status="active"))
    linha = s.social_repo.profile_row(pid)
    assert (linha["status"], linha["blocked_at"], linha["blocked_origin"], linha["blocked_evidence"]) == (
        "active", None, None, None)
    eventos = _eventos(h, "profile.status")
    assert [(e["anterior"], e["status"], e["origem"]) for e in eventos] == [
        ("active", "blocked", "declarado"), ("blocked", "active", "declarado")]
    assert all(e["autor"] and e["profile_id"] == pid for e in eventos)


async def test_bloqueio_pela_regra_do_desafio_tambem_deixa_rastro(h: Harness) -> None:
    """ADR-029 escreve o status pelo repositório, por fora do serviço: o rastro tem de valer por lá também."""
    s = h.state
    assert s is not None
    pid = s.social.create_profile(ProfileCreate(username="thiago.moreira4827")).id
    assert bloquear_por_desafio(s.social_repo, s.bus, profile_id=pid, instance_id="android-01",
                                anterior_status=None, detail="Confirm you are human", app_label="Instagram")
    linha = s.social_repo.profile_row(pid)
    assert linha["status"] == "blocked" and linha["blocked_at"] and linha["blocked_origin"] == "regra"
    assert [(e["status"], e["origem"]) for e in _eventos(h, "profile.status")] == [("blocked", "regra")]


# ============================================================ 5) saúde
async def test_saude_acusa_conta_travada_logada_em_aparelho_ligado(h: Harness) -> None:
    s = h.state
    assert s is not None
    assert not [p for p in s.health().problems if p.code == "locked_account_on_device"]
    _marcar(h, "android-01")
    problemas = [p for p in s.health().problems if p.code == "locked_account_on_device"]
    assert len(problemas) == 1 and "android-01" in problemas[0].message and FELIPE in problemas[0].message
    await s.devices.stop_instance(s.devices.get("android-01"))
    assert not [p for p in s.health().problems if p.code == "locked_account_on_device"]


# ============================================================ 6) account_label derivado
async def test_account_label_segue_o_vinculo_e_o_marcador(h: Harness) -> None:
    s = h.state
    assert s is not None
    s.db.execute("UPDATE instances SET app_id='instagram' WHERE id='android-02'")

    def rotulo(iid: str) -> tuple[object, object]:
        linha = s.db.one("SELECT account_label, account_label_origin FROM instances WHERE id=?", (iid,))
        return linha["account_label"], linha["account_label_origin"]

    assert rotulo("android-02") == ("qa-user-02", None)              # o da configuração, como sempre
    lucas = s.social.create_profile(ProfileCreate(username="lucas.almeida9484", instance_id="android-02")).id
    assert rotulo("android-02") == ("lucas.almeida9484", "vinculo")
    assert s.devices.dto(s.devices.get("android-02")).account_label == "lucas.almeida9484"
    s.social.update_profile(lucas, ProfilePatch(instance_id=None))
    assert rotulo("android-02") == (None, None)                      # o derivado sai com o vínculo
    _marcar(h, "android-02")
    assert rotulo("android-02") == (FELIPE, "marcador")
    dto = s.devices.dto(s.devices.get("android-02"))
    assert dto.account_label == FELIPE and dto.locked_account == FELIPE
    s.social_repo.resolver_conta_travada("android-02", por="dono")
    assert rotulo("android-02") == (None, None)
    # O aparelho de QA, sem vínculo nem marcador, fica com o rótulo da configuração: não é derivado.
    assert rotulo("android-01") == ("qa-user-01", None)


async def test_account_label_velho_e_corrigido_na_subida(h: Harness) -> None:
    """O android-04 subiu com `qa-user-04` gravado por anos; a partida seguinte do backend deriva de novo."""
    s = h.state
    assert s is not None
    s.db.execute("UPDATE instances SET app_id='instagram' WHERE id='android-02'")
    _marcar(h, "android-02")
    s.db.execute("UPDATE instances SET account_label='qa-user-02', account_label_origin=NULL WHERE id='android-02'")
    await h.crash()
    s = await h.boot()
    assert s.db.one("SELECT account_label, account_label_origin FROM instances WHERE id='android-02'") == {
        "account_label": FELIPE, "account_label_origin": "marcador"}


async def test_apagar_os_dados_do_aparelho_resolve_o_marcador(h: Harness) -> None:
    """O reset só chega a um aparelho em quarentena com a confirmação explícita da pessoa; depois dele, a conta não
    está mais logada ali — afirmar o contrário travaria o aparelho limpo para sempre.

    Mas só o disco apagado DE FATO resolve, pelo caminho real: o reset local apaga no boot seguinte (`-wipe-data`), e
    até lá o marcador fica — `wipe_next_boot` mora só na memória, e um backend reiniciado no meio subiria o disco
    antigo, com a conta travada, sobre um marcador já dado por resolvido. Religar sem apagar não resolve nada."""
    s = h.state
    assert s is not None
    rt = s.devices.get("android-02")
    await h.wait(lambda: rt.state == InstanceState.online, what="android-02 no ar")
    felipe = s.social.create_profile(ProfileCreate(username=FELIPE)).id
    _marcar(h, "android-02")

    await s.devices.restart_instance(rt)                            # boot sem apagar: a conta segue logada
    await h.wait(lambda: rt.state == InstanceState.online, what="android-02 religado")
    assert s.social_repo.conta_travada_no_aparelho("android-02") is not None

    await s.devices.reset_instance(rt)
    assert s.social_repo.conta_travada_no_aparelho("android-02") is not None   # o boot com wipe só foi enfileirado
    await h.wait(lambda: s.social_repo.conta_travada_no_aparelho("android-02") is None, what="marcador pelo wipe")
    assert rt.fresh_data is True
    linha = s.db.one("SELECT resolved_by, resolution FROM device_locked_accounts WHERE instance_id='android-02'")
    assert linha["resolved_by"] == "reset do aparelho" and "dados apagados (reset)" in linha["resolution"]
    assert [e["acao"] for e in _eventos(h, "device.locked_account")] == ["marcado", "resolvido"]
    # O perfil NÃO é reativado pelo wipe: reativar é decisão de pessoa.
    assert s.social_repo.profile_row(felipe)["status"] == "blocked"


async def test_reset_concluido_pelo_agente_resolve_o_marcador_e_o_incerto_nao(h: Harness) -> None:
    """Aparelho de outra máquina: só o `succeeded` do agente afirma que o disco foi apagado. `failed`/`uncertain` não
    autorizam afirmar nada sobre o aparelho — o marcador fica."""
    s = h.state
    assert s is not None
    rt = s.devices.get("android-02")
    _marcar(h, "android-02")
    for desfecho in ("failed", "uncertain"):
        await s.devices.aplicar_desfecho_remoto(rt, "reset", desfecho)
        assert s.social_repo.conta_travada_no_aparelho("android-02") is not None, desfecho
    await s.devices.aplicar_desfecho_remoto(rt, "reset", "succeeded")
    assert s.social_repo.conta_travada_no_aparelho("android-02") is None
    linha = s.db.one("SELECT resolved_by, resolution FROM device_locked_accounts WHERE instance_id='android-02'")
    assert linha["resolved_by"] == "reset do aparelho" and "agente" in linha["resolution"]


async def test_troca_de_identidade_do_aparelho_nao_resolve_o_marcador(h: Harness) -> None:
    """Revisão do pacote (29/09): a troca da impressão digital física (`conferir_identidade`) passava pelo mesmo gancho
    do wipe e resolvia o marcador sozinha, com `resolved_by='reset do aparelho'` e "dados do aparelho apagados" — sem
    reset, sem pessoa, sem disco apagado. Resolvido o marcador, o aparelho voltava a ser livre e a tela de desafio podia
    ser tocada de novo. Trocar o aparelho por trás do id não é prova de que a conta travada saiu dali: o marcador fica,
    e o cartão diz que precisa do dono."""
    s = h.state
    assert s is not None
    _marcar(h, "android-02")
    rt = s.devices.get("android-02")
    assert s.devices.conferir_identidade(rt, "host-a|ro.boot.qemu.avd_name=android-02") is False
    assert s.devices.conferir_identidade(rt, "host-b|ro.boot.qemu.avd_name=android-02") is True

    marcador = s.social_repo.conta_travada_no_aparelho("android-02")
    assert marcador is not None and marcador["handle"] == FELIPE
    assert s.db.scalar("SELECT COUNT(*) FROM device_locked_accounts WHERE resolved_at IS NOT NULL") == 0
    assert s.quarentena("android-02") is not None
    assert [e["acao"] for e in _eventos(h, "device.locked_account")] == ["marcado"]
    # O cartão diz o que aconteceu (e o que NÃO aconteceu): o disco não foi apagado, e quem decide é o dono.
    assert rt.attention is not None and FELIPE in rt.attention and "não foi apagado" in rt.attention
    # E a porta segue fechada para o app da conta travada.
    porta = s._app_resolver(rt, "com.instagram.android", {"status": "pending"})
    assert porta is not None and porta[1] is None and FELIPE in porta[0]
