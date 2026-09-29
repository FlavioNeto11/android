"""Item 6.1 — registro de apps: o núcleo deixa de decidir por `if package == "com.instagram.android"`.

O que cada teste protege, em uma frase:

* um segundo aplicativo ganha catálogo, política e rótulo SEM que uma linha do núcleo mude;
* a porta de sessão é por app: tarefa de QA Messenger não passa pela porta do Instagram;
* instalar um app sem conta não invalida a sessão de outro app;
* a API não assume um pacote por omissão — nem em capacidades, nem na loja;
* execução que mistura apps com e sem catálogo é recusada ANTES de planejar, com motivo.
"""
from __future__ import annotations

from typing import Any, Iterator

import httpx
import pytest

from app.main import create_app
from app.models import ProfileCreate, SessionStatus
from app.planning.capabilities import Capability, CapabilityCatalog, capability_of, load_catalog
from app.planning import catalog as registro
from app.planning.catalog import capabilities_of, package_of_provider, register, registered, unregister

from .conftest import Harness

PACOTE_FALSO = "com.exemplo.apptesteo"
QA = "com.pocqa.messenger"
INSTAGRAM = "com.instagram.android"


def _catalogo_de_mentira() -> CapabilityCatalog:
    """Um app inventado, com uma ação de efeito externo. Nada dele existe no núcleo."""
    enviar = Capability(
        key="ENVIAR_RECADO", title="Enviar recado", goal="Mandar um recado para {destinatario}.",
        post_kind="model_judged", post_value="recado enviado",
        post_description="O recado aparece na conversa.", bindings=("destinatario",),
        # ação com limite declara quem é o alvo (ADR-055): é por ele que a frota conta contas por pessoa
        side_effect=True, risk="medium", default_policy="approval_required", limit_bucket="dms",
        counterparty="destinatario")
    return CapabilityCatalog(PACOTE_FALSO, [enviar])


@pytest.fixture
def app_falso() -> Iterator[CapabilityCatalog]:
    catalogo = _catalogo_de_mentira()
    register(PACOTE_FALSO, catalogo,
             capabilities_of(PACOTE_FALSO).__class__(package=PACOTE_FALSO, name="App de Exemplo",
                                                     session_provider=None, needs_profile=False))
    try:
        yield catalogo
    finally:
        unregister(PACOTE_FALSO)


# ==================================================================== o registro é o ponto de extensão
def test_segundo_app_ganha_catalogo_sem_tocar_no_nucleo(app_falso: CapabilityCatalog) -> None:
    """`load_catalog` e `capability_of` passam a responder por um app que o núcleo nunca ouviu falar."""
    assert load_catalog(PACOTE_FALSO) is app_falso
    cap = capability_of(PACOTE_FALSO, "ENVIAR_RECADO")
    assert cap is not None and cap.side_effect and cap.default_policy == "approval_required"
    # E o Instagram continua onde estava: um pacote de dado, descoberto sem ninguém importá-lo.
    assert load_catalog(INSTAGRAM) is not None
    assert load_catalog(QA) is None and capability_of(QA, "ENVIAR_RECADO") is None


def test_app_sem_registro_e_neutro_e_nunca_levanta() -> None:
    """Caminho quente: pacote desconhecido responde o perfil neutro, nunca `None` nem exceção."""
    caps = capabilities_of("com.nao.registrado")
    assert caps.has_catalog is False and caps.session_provider is None and caps.needs_profile is False
    assert capabilities_of(None).session_provider is None


def test_quem_prove_a_conta_sai_do_registro_e_nao_de_um_literal() -> None:
    assert package_of_provider("instagram") == INSTAGRAM
    assert package_of_provider("inexistente") is None
    assert INSTAGRAM in {c.package for c in registered()}


def test_registrar_sem_catalogo_nao_mente_sobre_ter_catalogo() -> None:
    try:
        caps = register(PACOTE_FALSO, None)
        assert caps.has_catalog is False and load_catalog(PACOTE_FALSO) is None
    finally:
        unregister(PACOTE_FALSO)


# ==================================================================== a porta de sessão é por app
async def _perfil_em(harness: Harness, instance_id: str, status: SessionStatus, detail: str) -> str:
    assert harness.state is not None
    s = harness.state
    perfil = s.social.create_profile(ProfileCreate(username="qa.conta.teste", instance_id=instance_id))
    s.social_repo.set_session(perfil.id, status=status, instance_id=instance_id, detail=detail)
    return perfil.id


async def test_tarefa_de_outro_app_nao_passa_pela_porta_do_instagram(harness: Harness) -> None:
    """Aparelho com perfil do Instagram em desafio de segurança: tarefa de QA Messenger despacha assim mesmo.

    Antes, a porta recebia só o aparelho: o item de OUTRO app ficava bloqueado por uma conta que ele não ia
    tocar (ou pior, o sistema abria o Instagram e tentava autenticar antes da tarefa).
    """
    assert harness.state is not None
    s = harness.state
    rt = s.devices.devices["android-01"]
    await _perfil_em(harness, "android-01", SessionStatus.auth_challenge, "desafio de segurança no aplicativo")

    assert s._session_gate(rt, QA) is None                       # tarefa de QA: a porta nem abre
    porta = s._session_gate(rt, INSTAGRAM)                       # tarefa de Instagram: continua parando
    assert porta is not None and porta[1] is None
    assert "desafio" in porta[0]
    # Sem pacote (chamador antigo) o comportamento não muda: quem não diz o app continua passando pela porta.
    assert s._session_gate(rt) is not None


async def test_instalar_app_sem_conta_nao_mexe_na_sessao_do_instagram(harness: Harness) -> None:
    """Instalar/atualizar o QA Messenger marcava a sessão do Instagram como 'unknown'. Agora não."""
    assert harness.state is not None
    s = harness.state
    pid = await _perfil_em(harness, "android-02", SessionStatus.session_ready, "conta verificada")

    # O caminho REAL: é isto que `install_on` chama depois de mexer no disco.
    s.releases._app_mudou("android-02", QA, "o aplicativo foi instalado neste aparelho")
    assert s.social_repo.session_row(pid)["status"] == SessionStatus.session_ready.value

    s.releases._app_mudou("android-02", INSTAGRAM, "o aplicativo foi instalado neste aparelho")
    assert s.social_repo.session_row(pid)["status"] == SessionStatus.unknown.value


async def test_apagar_o_aparelho_inteiro_continua_invalidando_sem_perguntar(harness: Harness) -> None:
    """Wipe leva o disco do aparelho junto: ali a sessão foi mesmo embora, e não se pergunta de que app era."""
    assert harness.state is not None
    s = harness.state
    pid = await _perfil_em(harness, "android-03", SessionStatus.session_ready, "conta verificada")
    s._invalidate_sessions("android-03", "o aparelho foi apagado")
    assert s.social_repo.session_row(pid)["status"] == SessionStatus.unknown.value


# ==================================================================== a API não assume um app
async def test_capacidades_e_loja_exigem_o_pacote(harness: Harness, app_falso: CapabilityCatalog) -> None:
    app = create_app(harness.cfg, state=harness.state)
    app.state.poc = harness.state
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        assert (await c.get("/api/capabilities")).status_code == 422       # sem pacote não há resposta plausível
        chaves = [x["key"] for x in (await c.get("/api/capabilities", params={"package": PACOTE_FALSO})).json()]
        assert chaves == ["ENVIAR_RECADO"]
        assert (await c.get("/api/capabilities", params={"package": QA})).json() == []

        r = await c.get("/api/store")                                       # loja sem pacote: 400 explicado
        assert r.status_code == 400 and r.json()["detail"]["code"] == "package_required"

        catalogo = {a["package"]: a for a in (await c.get("/api/app-catalog")).json()}
        assert catalogo[INSTAGRAM]["session_provider"] == "instagram"
        assert catalogo[PACOTE_FALSO]["has_catalog"] is True
        assert catalogo[PACOTE_FALSO]["session_provider"] is None
        # 23.10: é o que o painel usa no lugar de comparar nome ou pacote ("ehInstagram" fixo) para achar o app da
        # conta de cadastro da persona — só o Instagram (o fixture não registra um segundo âncora).
        assert catalogo[INSTAGRAM]["profile_anchor"] is True
        assert catalogo[PACOTE_FALSO]["profile_anchor"] is False


# ==================================================================== execução que mistura apps
async def test_execucao_que_mistura_apps_com_catalogo_e_recusada_antes_de_planejar(harness: Harness,
                                                                                   app_falso: Any) -> None:
    """Sem isto, o planejador recebia `catalog=None` e escrevia etapas livres — sem política, limite nem aprovação."""
    assert harness.state is not None
    s = harness.state
    s.db.execute("INSERT INTO apps(id, name, package, activity, builtin) VALUES (?,?,?,?,?)",
                 ("app-exemplo", "App de Exemplo", PACOTE_FALSO, ".Main", 0))
    s.db.execute("UPDATE instances SET app_id=? WHERE id=?", ("app-exemplo", "android-02"))

    app = create_app(harness.cfg, state=harness.state)
    app.state.poc = harness.state
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/runs", json={"command": "Abra o app e diga oi.", "mode": "execute",
                                            "instance_ids": ["android-01", "android-02"],
                                            "idempotency_key": "mistura-de-apps-0001"})
        assert r.status_code == 409 and r.json()["detail"]["code"] == "mixed_apps"
        assert "android-01" in r.json()["detail"]["message"] and "android-02" in r.json()["detail"]["message"]

        # Um app só: segue valendo.
        ok = await c.post("/api/runs", json={"command": "Abra o app e diga oi.", "mode": "plan",
                                            "instance_ids": ["android-01", "android-03"],
                                            "idempotency_key": "mistura-de-apps-0002"})
        assert ok.status_code == 200


async def test_mistura_sem_catalogo_nenhum_nao_e_recusada(harness: Harness) -> None:
    """Recusar dois apps que não têm catálogo seria inventar limitação: nada se perde ali."""
    assert harness.state is not None
    s = harness.state
    s.db.execute("INSERT INTO apps(id, name, package, activity, builtin) VALUES (?,?,?,?,?)",
                 ("app-sem-catalogo", "Outro QA", "com.pocqa.outro", ".Main", 0))
    s.db.execute("UPDATE instances SET app_id=? WHERE id=?", ("app-sem-catalogo", "android-02"))
    assert s.runs._mistura_de_apps(["android-01", "android-02"]) is None


# ==================================================================== fluxos declaram os apps de que precisam
async def test_fluxo_declara_os_apps_e_a_execucao_e_recusada_antes_de_agendar(harness: Harness) -> None:
    """Achado #81: a pendência aparecia só DEPOIS, como etapa que falha — sem ação clara para resolver."""
    assert harness.state is not None
    s = harness.state
    s.cfg.file.ai.flows = True                 # reaproveitar o plano de comando repetido é opcional na config
    s.db.execute("INSERT INTO apps(id, name, package, activity, builtin) VALUES (?,?,?,?,?)",
                 ("configuracoes", "Configurações", "com.android.settings", ".Settings", 0))
    s.db.execute(
        "INSERT INTO flows(id, name, match_key, command_template, plan, app_id, status, created_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        ("fl-1", "copiar e colar", "copie o código das configuracoes e cole no qa messenger",
         "Copie o código das Configuracoes e cole no QA Messenger",
         '{"summary":"copiar","app_id":"qa-messenger","parameters":{},"steps":[],'
         '"planner":{"provider":"x","model":"y","simulated":true}}',
         "qa-messenger", "active", "2026-09-22T10:00:00Z"))
    s.scheduler.flows.set_required_apps("fl-1", ["qa-messenger", "configuracoes"])
    assert s.scheduler.flows.required_apps("fl-1") == ["configuracoes", "qa-messenger"]

    comando = "Copie o código das Configuracoes e cole no QA Messenger"
    assert [a["id"] for a in s.runs.apps_exigidos(comando)] == ["configuracoes", "qa-messenger"]

    # O app exigido está AUSENTE neste aparelho (observado, e não "nunca olhado"): a recusa é antes de agendar.
    s.release_repo.upsert_app_state("android-01", "com.android.settings", state="missing",
                                    detail="o aparelho não tem este pacote")
    app = create_app(harness.cfg, state=harness.state)
    app.state.poc = harness.state
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/runs", json={"command": comando, "mode": "execute",
                                            "instance_ids": ["android-01"],
                                            "idempotency_key": "apps-exigidos-0001"})
        assert r.status_code == 409
        corpo = r.json()["detail"]
        assert corpo["code"] == "missing_required_app"
        assert corpo["missing"][0]["instance_id"] == "android-01"
        assert "Distribua Configurações em android-01" in corpo["acao"]

        # Com o app pronto no aparelho, deixa de ser recusa. (Uma versão desejada a caminho também basta: a
        # porta do app entrega antes da tarefa — mesmo ramo em `_exigir_apps_do_fluxo`.)
        s.release_repo.upsert_app_state("android-01", "com.android.settings", state="ready",
                                        detail="instalado e aberto")
        ok = await c.post("/api/runs", json={"command": comando, "mode": "plan",
                                             "instance_ids": ["android-01"],
                                             "idempotency_key": "apps-exigidos-0002"})
        assert ok.status_code == 200


async def test_aparelho_nunca_observado_nao_fecha_a_porta(harness: Harness) -> None:
    """O que não se sabe nunca recusa: sem linha em `device_app_state`, o app ainda pode ser entregue antes."""
    assert harness.state is not None
    s = harness.state
    s.cfg.file.ai.flows = True
    s.db.execute("INSERT INTO apps(id, name, package, activity, builtin) VALUES (?,?,?,?,?)",
                 ("configuracoes", "Configurações", "com.android.settings", ".Settings", 0))
    s.db.execute(
        "INSERT INTO flows(id, name, match_key, command_template, plan, app_id, status, created_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        ("fl-2", "x", "abra as configuracoes", "Abra as Configuracoes",
         '{"summary":"x","app_id":"qa-messenger","parameters":{},"steps":[],'
         '"planner":{"provider":"x","model":"y","simulated":true}}',
         "qa-messenger", "active", "2026-09-22T10:00:00Z"))
    s.scheduler.flows.set_required_apps("fl-2", ["configuracoes"])
    from app.models import RunCreate

    s.runs._exigir_apps_do_fluxo(RunCreate(command="Abra as Configuracoes", instance_ids=["android-01"],
                                           mode="plan", idempotency_key="nunca-observado-1"))


def test_catalogo_embutido_e_importado_relativo_ao_pacote() -> None:
    """Execução 8a9ffc: com o backend carregado como `backend.app`, `import_module("app.planning…")` não existia e
    o planejamento caiu com "No module named 'app'". O nome fica relativo e resolve contra este pacote."""
    for modulo, _atributo in registro._BUILTINS:
        assert modulo.startswith("."), modulo
    registro.unregister(INSTAGRAM)
    assert registro.get(INSTAGRAM) is not None and registro.capabilities_of(INSTAGRAM).has_catalog
