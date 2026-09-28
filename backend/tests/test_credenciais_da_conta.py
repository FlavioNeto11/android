"""A credencial é da CONTA da persona, não da execução (ADR-040; substitui `test_credenciais_da_execucao.py`).

O que continua igual ao ADR-025: senha nunca no texto do comando; o modelo conhece só NOMES; a digitação é só pelo
canal sensível, só em campo de senha; desafio, 2FA e CAPTCHA seguem com a pessoa. O que mudou: a senha fica guardada
na conta da persona (perfil × app × host), com consentimento POR CONTA, e `type_secret(name)` a resolve pelo perfil
do objetivo — só no pacote da conta e, no navegador, só no `host` dela. `RunCreate` não aceita mais `credentials`.
Apps com provedor de sessão (Instagram) ficam fora do `type_secret`: o login deles é determinístico.

O valor usado aqui é gerado no próprio teste; nenhum segredo real. Nível de prova: `simulated` (harness na porta 5640).
"""
from __future__ import annotations

import json
import secrets as pysecrets
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from pydantic import SecretStr

from app.automation.driver import DriverError
from app.automation.hierarchy import MOTIVO_DESAFIO, MOTIVO_SENHA, parse_hierarchy
from app.automation.tools import OpenUrl, ToolContext, TypeSecret, execute_tool, urls_do_texto
from app.models import (CredentialUpdate, Plan, PlannerInfo, PlanStep, Postcondition, ProfileAccountCreate,
                        ProfileCreate, RunCreate)
from app.modules.identity.application.available_data import (available_data, common_data, profile_variables,
                                                              resolve_secret, typable_secret_for)
from app.modules.identity.domain.available_data import account_name, slug
from app.modules.skills.domain.lifecycle import SkillState
from app.modules.skills.domain.versions import Provenance, SourceKind
from app.planning.prompts import dados_block, planner_capability_user, planner_user, step_block
from app.planning.provider import AppContext, PlanRequest, StepContext
from app.security.sensitive_input import SensitiveInputChannel
from app.social.service import SocialError
from app.taskqueue.executor import pede_intervencao_humana, urls_da_pessoa
from app.taskqueue.recipes import UNSAFE_TO_REPLAY
from app.taskqueue.service import RunError, pede_outro_alvo

from .conftest import Harness

COMANDO = "abra o Chrome e entre no site (https://portal.exemplo.test/#/) com o usuário qa-operador e avance até logar"
HOST = "portal.exemplo.test"
CHROME = "com.android.chrome"
SENHA_CHROME = account_name("chrome", HOST, "senha")            # conta_chrome_portal_exemplo_test_senha
USUARIO_CHROME = account_name("chrome", HOST, "usuario")
FIXTURES = Path(__file__).parent / "fixtures" / "dsl" / "v1alpha1" / "validos"
DONO = "painel:teste"


def _valor() -> str:
    return "Tst-" + pysecrets.token_urlsafe(9)


def _pedido(*, command: str = COMANDO, ids: list[str] | None = None, mode: str = "plan") -> RunCreate:
    return RunCreate(command=command, instance_ids=ids or ["android-01"], mode=mode,  # type: ignore[arg-type]
                     idempotency_key=f"cred-{pysecrets.token_hex(6)}")


def _chrome(harness: Harness) -> None:
    if not harness.state.db.scalar("SELECT id FROM apps WHERE id='chrome'"):
        harness.state.db.execute("INSERT INTO apps(id, name, package, activity, builtin) VALUES "
                                 "('chrome','Chrome',?, 'com.google.android.apps.chrome.Main', 0)", (CHROME,))


def _perfil(harness: Harness, instance: str | None, *, username: str | None = None, email: str | None = None) -> str:
    nome = username or f"persona.{pysecrets.token_hex(3)}"
    return harness.state.social.create_profile(ProfileCreate(username=nome, instance_id=instance, email=email,
                                                             first_name="Qa")).id


def _conta_chrome(harness: Harness, pid: str, *, senha: str, host: str | None = HOST, consent: bool = True) -> str:
    _chrome(harness)
    return harness.state.social.add_account(pid, ProfileAccountCreate(
        app_id="chrome", handle="qa-operador", host=host, password=SecretStr(senha), consent=consent), by=DONO).id


def _em_lugar_nenhum(harness: Harness, valor: str) -> None:
    """O valor não aparece em nenhuma tabela de texto que a API, o painel ou o modelo leem — inteira."""
    db = harness.state.db
    for tabela in ("runs", "events", "actions", "ai_calls", "objectives", "steps", "evidence", "profile_accounts",
                   "account_credentials", "account_sessions", "authentication_attempts", "memory_items"):
        for linha in db.query(f"SELECT * FROM {tabela}"):                      # noqa: S608 - nome fixo do teste
            assert valor not in json.dumps(dict(linha), default=str), tabela


def _cliente(harness: Harness) -> httpx.AsyncClient:
    from app.main import create_app

    app = create_app(harness.cfg, state=harness.state)
    app.state.poc = harness.state
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


# ---------------------------------------------------------------- 1. senha no texto do comando: recusada, nada gravado
async def test_senha_no_comando_e_recusada_antes_de_gravar_e_de_planejar(harness: Harness,
                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    s = harness.state
    valor = _valor()
    planejou: list[Any] = []
    monkeypatch.setattr(s.runs.provider, "plan", lambda req: planejou.append(req))
    antes = s.db.one("SELECT COUNT(*) AS n FROM runs")["n"]
    with pytest.raises(RunError) as erro:
        s.runs.create(_pedido(command=f"{COMANDO}\nSenha: {valor}"))
    assert erro.value.code == "credencial_no_comando" and erro.value.status == 409
    assert valor not in erro.value.message and "conta da persona" in erro.value.message
    assert s.db.one("SELECT COUNT(*) AS n FROM runs")["n"] == antes and planejou == []
    _em_lugar_nenhum(harness, valor)


def test_redacao_no_repositorio_e_a_segunda_linha(harness: Harness) -> None:
    """Mesmo quem chama o repositório direto (sem o serviço) não grava a senha em claro."""
    valor = _valor()
    row, _ = harness.state.repo.create_run(_pedido(command=f"entre no portal. senha: {valor}"), simulated=True)
    assert valor not in row["command"]


async def test_a_execucao_nao_aceita_mais_credencial(harness: Harness) -> None:
    """`credentials`/`consent_credentials` saíram de `RunCreate` (opção A): cliente antigo recebe 422, sem eco do valor."""
    valor = _valor()
    with pytest.raises(ValueError):
        RunCreate(command="abra o site", instance_ids=["android-01"], idempotency_key="cred-antiga-01",
                  credentials={"senha": valor})                                      # type: ignore[call-arg]
    base = {"command": "abra o site", "instance_ids": ["android-01"], "idempotency_key": "cred-422-teste"}
    async with _cliente(harness) as c:
        r = await c.post("/api/runs", json={**base, "credentials": {"senha": valor}, "consent_credentials": True})
        assert r.status_code == 422 and valor not in r.text
    assert harness.state.db.scalar("SELECT COUNT(*) FROM run_secrets") == 0        # ninguém escreve mais nela


# ---------------------------------------------------------------- 2. consentimento por conta
def test_senha_de_conta_so_com_consentimento(harness: Harness) -> None:
    s = harness.state
    pid = _perfil(harness, "android-01")
    valor = _valor()
    segredos = s.db.scalar("SELECT COUNT(*) FROM secrets")
    with pytest.raises(SocialError) as sem:
        _conta_chrome(harness, pid, senha=valor, consent=False)
    assert sem.value.code == "consentimento_de_credencial" and sem.value.status == 409
    assert valor not in sem.value.message and "consent" in sem.value.message
    assert [c.app_id for c in s.social.list_accounts(pid)] == ["instagram"]       # nem a conta ficou
    assert s.db.scalar("SELECT COUNT(*) FROM secrets") == segredos

    aid = _conta_chrome(harness, pid, senha=valor, consent=True)
    conta = s.social.get_account(pid, aid)
    assert conta.credential_configured and conta.host == HOST
    linha = s.db.one("SELECT * FROM account_credentials WHERE account_id=?", (aid,))
    assert linha["consent_at"] and linha["consent_by"] == DONO and linha["login_identifier"] == "qa-operador"
    assert valor not in json.dumps(dict(linha)) and s.secrets.get_secret(linha["secret_ref"]) == valor
    # trocar a senha sem repetir o consentimento numa conta que JÁ consentiu: passa, e o consentimento fica
    outro = _valor()
    s.social.set_account_credential(pid, aid, CredentialUpdate(password=SecretStr(outro)), by=DONO)
    depois = s.db.one("SELECT * FROM account_credentials WHERE account_id=?", (aid,))
    assert depois["consent_at"] == linha["consent_at"] and depois["secret_ref"] == linha["secret_ref"]
    assert s.secrets.get_secret(depois["secret_ref"]) == outro
    _em_lugar_nenhum(harness, valor)
    _em_lugar_nenhum(harness, outro)


async def test_put_credential_sem_consentimento_e_409_tambem_pelo_apelido_por_perfil(harness: Harness) -> None:
    s = harness.state
    pid = _perfil(harness, "android-01")
    valor = _valor()
    async with _cliente(harness) as c:
        r = await c.put(f"/api/instagram/profiles/{pid}/credential", json={"password": valor})
        assert r.status_code == 409 and r.json()["detail"]["code"] == "consentimento_de_credencial", r.text
        assert valor not in r.text and not s.social.get_profile(pid).credential.configured
        r = await c.put(f"/api/instagram/profiles/{pid}/credential", json={"password": valor, "consent": True})
        assert r.status_code == 200 and r.json()["credential"]["configured"] and valor not in r.text
    cred = s.social_repo.credential_row(pid)
    assert cred is not None and cred["consent_at"] is not None
    _em_lugar_nenhum(harness, valor)


def test_consentir_sem_redigitar_e_apagar_do_cofre(harness: Harness) -> None:
    s = harness.state
    pid = _perfil(harness, "android-01")
    aid = _conta_chrome(harness, pid, senha=_valor())
    s.db.execute("UPDATE account_credentials SET consent_at=NULL, consent_by=NULL WHERE account_id=?", (aid,))
    assert resolve_secret(s.runs.dados, pid, SENHA_CHROME).pending_consent
    s.social.consent_account_credential(pid, aid, by=DONO)
    assert resolve_secret(s.runs.dados, pid, SENHA_CHROME).secret is not None
    ref = s.db.scalar("SELECT secret_ref FROM account_credentials WHERE account_id=?", (aid,))
    s.social.delete_account_credential(pid, aid)
    assert not s.secrets.exists(ref) and not s.social.get_account(pid, aid).credential_configured
    with pytest.raises(SocialError):
        s.social.consent_account_credential(pid, aid, by=DONO)                    # sem senha, nada a consentir


def test_apagar_credencial_preserva_o_segredo_que_a_linha_legada_ainda_referencia(harness: Harness) -> None:
    """Até a migração que remove `instagram_credentials`, uma referência compartilhada com a linha legada fica."""
    s = harness.state
    pid = _perfil(harness, "android-01")
    aid = _conta_chrome(harness, pid, senha=_valor())
    ref = s.db.scalar("SELECT secret_ref FROM account_credentials WHERE account_id=?", (aid,))
    s.db.execute("INSERT INTO instagram_credentials(profile_id, login_identifier, secret_ref, key_id, created_at,"
                 " updated_at) VALUES (?,?,?,?,?,?)", (pid, "x", ref, "k", "2026-09-27T00:00:00Z", "2026-09-27T00:00:00Z"))
    s.social.delete_account_credential(pid, aid)
    assert s.secrets.exists(ref)


def test_login_identifier_igual_ao_handle_nao_substitui_o_email_gravado(harness: Harness) -> None:
    """Relatório 01 §10.1: a aba Contas mandava `login_identifier: handle` e trocava o e-mail pelo @ (que o Instagram
    recusa). Transitório: com provedor, o handle que chega igual ao gravado não substitui o identificador."""
    s = harness.state
    pid = s.social.create_profile(ProfileCreate(username="lucas.teste", instance_id="android-01",
                                                email="lucas@exemplo.test", password=SecretStr(_valor()))).id
    ig = next(c for c in s.social.list_accounts(pid) if c.app_id == "instagram")
    assert s.social_repo.credential_row(pid)["login_identifier"] == "lucas@exemplo.test"
    s.social.set_account_credential(pid, ig.id, CredentialUpdate(password=SecretStr(_valor()),
                                                                login_identifier="lucas.teste", consent=True), by=DONO)
    assert s.social_repo.credential_row(pid)["login_identifier"] == "lucas@exemplo.test"
    s.social.set_account_credential(pid, ig.id, CredentialUpdate(password=SecretStr(_valor()),
                                                                login_identifier="outro@exemplo.test"), by=DONO)
    assert s.social_repo.credential_row(pid)["login_identifier"] == "outro@exemplo.test"   # explícito, muda


# ---------------------------------------------------------------- 3. o catálogo de dados: nomes, nunca valores
def test_dados_disponiveis_listam_nomes_e_nunca_valores(harness: Harness) -> None:
    s = harness.state
    valor = _valor()
    pid = s.social.create_profile(ProfileCreate(username="ana.teste", instance_id="android-01",
                                                email="ana@exemplo.test", first_name="Ana",
                                                password=SecretStr(_valor()))).id
    _conta_chrome(harness, pid, senha=valor)
    dados = available_data(s.runs.dados, pid)
    nomes = {d.name: d for d in dados}
    assert {"perfil_nome", "perfil_email", "conta_instagram_usuario", USUARIO_CHROME, SENHA_CHROME} <= set(nomes)
    assert "perfil_nascimento" not in nomes                             # sem valor, sem nome: nada de variável vazia
    assert "conta_instagram_senha" not in nomes                         # app com provedor: fora do type_secret
    assert nomes[SENHA_CHROME].sensitive and nomes[SENHA_CHROME].app_id == "chrome" and nomes[SENHA_CHROME].host == HOST
    assert not nomes[USUARIO_CHROME].sensitive
    variaveis = profile_variables(s.runs.dados, pid)
    assert variaveis["perfil_email"] == "ana@exemplo.test" and variaveis[USUARIO_CHROME] == "qa-operador"
    assert SENHA_CHROME not in variaveis and valor not in json.dumps(variaveis)
    # o que vai ao modelo: os três prompts trazem o bloco, sem valor
    req = PlanRequest(command=COMANDO, run_id="r1", instances=[{"instance_id": "android-01"}],
                      apps=[AppContext("chrome", "Chrome", CHROME, None, None, None)], available_data=list(dados))
    texto = planner_user(req, 10)
    assert SENHA_CHROME in texto and "{perfil_email}" in texto and valor not in texto and "ana@exemplo" not in texto
    ctx = StepContext(run_id="r1", instance_id="android-01", objective_summary=COMANDO, parameters={}, step_key="k",
                      step_title="t", step_goal="g", side_effect=False, commit_done=False, commit_guard=[],
                      precondition=None, postcondition_description="d", remaining_steps=[],
                      app=AppContext("chrome", "Chrome", CHROME, None, None, None), account_label=None,
                      available_data=list(dados))
    assert SENHA_CHROME in step_block(ctx) and valor not in step_block(ctx)
    assert "type_secret" in dados_block(list(dados)) and "(nenhum)" in dados_block([])
    # slug: o id com hífen vira snake_case, no alfabeto de TEMPLATE_RE
    assert slug("configura-es-do-android") == "configura_es_do_android"


def test_planejador_por_catalogo_tambem_recebe_a_lista(harness: Harness) -> None:
    from app.planning.capabilities import load_catalog

    pid = _perfil(harness, "android-01", email="cat@exemplo.test")
    dados = list(available_data(harness.state.runs.dados, pid))
    req = PlanRequest(command="curta o post de @nasa", run_id="r1", instances=[{"instance_id": "android-01"}],
                      apps=[AppContext("instagram", "Instagram", "com.instagram.android", None, None, None)],
                      catalog=load_catalog("com.instagram.android"), available_data=dados)
    assert "{perfil_email}" in planner_capability_user(req, 10)


def test_a_lista_do_planejador_e_a_comum_a_todos_os_aparelhos(harness: Harness) -> None:
    s = harness.state
    a = _perfil(harness, "android-01", email="a@exemplo.test")
    b = _perfil(harness, "android-02", email="b@exemplo.test")
    _conta_chrome(harness, a, senha=_valor())
    comuns = {d.name for d in common_data(s.runs.dados, [a, b])}
    assert "perfil_email" in comuns and SENHA_CHROME not in comuns          # só o perfil A tem a conta do portal
    assert common_data(s.runs.dados, [a, None]) == ()                         # aparelho sem perfil zera a lista


async def test_o_planejamento_recebe_a_lista_e_a_variavel_resolve_por_aparelho(harness: Harness,
                                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    s = harness.state
    a = _perfil(harness, "android-01", email="a@exemplo.test")
    b = _perfil(harness, "android-02", email="b@exemplo.test")
    valor = _valor()
    _conta_chrome(harness, a, senha=valor)
    _conta_chrome(harness, b, senha=_valor())
    pedidos: list[Any] = []
    original = s.runs.provider.plan

    async def espiao(req: Any) -> Any:
        pedidos.append(req)
        return await original(req)
    monkeypatch.setattr(s.runs.provider, "plan", espiao)
    run = s.runs.create(_pedido(ids=["android-01", "android-02"]))
    await harness.wait(lambda: bool(pedidos), what="planejamento")
    nomes = {d.name for d in pedidos[0].available_data}
    assert {"perfil_email", SENHA_CHROME, USUARIO_CHROME} <= nomes and valor not in str(pedidos[0])
    await harness.wait_run(run.id, statuses=("planned", "needs_input", "failed", "completed"), timeout=30)
    # `{perfil_email}` e `{conta_…_usuario}` resolvem por aparelho na materialização; o nome da senha NUNCA resolve
    post = Postcondition(kind="text_visible", value="{perfil_email}", description="logado como {perfil_email}")
    plano = Plan(summary="login", app_id="chrome", app_package=CHROME,
                 parameters={"usuario": "{" + USUARIO_CHROME + "}", "email": "{perfil_email}",
                             "senha": "{" + SENHA_CHROME + "}"},
                 steps=[PlanStep(key="entrar", title="Entrar como {perfil_email}", goal="g", postcondition=post)],
                 planner=PlannerInfo(provider="t", model="t", simulated=True))
    s.db.execute("DELETE FROM steps WHERE run_id=?", (run.id,))
    s.db.execute("DELETE FROM objectives WHERE run_id=?", (run.id,))
    s.repo.save_plan(run.id, plano)
    s.repo.materialize(run.id, plano, [{"instance_id": "android-01", "profile_id": a},
                                       {"instance_id": "android-02", "profile_id": b}])
    for iid, email in (("android-01", "a@exemplo.test"), ("android-02", "b@exemplo.test")):
        params = json.loads(s.repo.objective_row(f"{run.id}:{iid}")["parameters"])
        assert params["email"] == email and params["usuario"] == "qa-operador"
        assert params["senha"] == "{" + SENHA_CHROME + "}"                  # segredo não é variável
        etapa = s.db.one("SELECT title, postcondition FROM steps WHERE id=?", (f"{run.id}:{iid}:v1:entrar",))
        assert email in etapa["title"] and email in etapa["postcondition"]
    _em_lugar_nenhum(harness, valor)


# ---------------------------------------------------------------- 4. executor: tela de senha e canal sensível
def _arvore(motivo: str) -> Any:
    senha = motivo == MOTIVO_SENHA
    xml = ('<hierarchy><node class="android.widget.EditText" resource-id="x:id/campo" text="" content-desc="" '
           f'clickable="true" password="{str(senha).lower()}" bounds="[0,0][100,50]" /></hierarchy>')
    tree = parse_hierarchy(xml)
    tree.sensitive, tree.sensitive_reason = True, motivo
    return tree


def test_tela_de_senha_so_pede_pessoa_quando_nao_ha_senha_da_conta_do_app(harness: Harness) -> None:
    """`tem_credencial` vale por app e etapa: a senha da conta do Chrome não torna a tela de senha do QA "só mais
    uma tela"."""
    s = harness.state
    pid = _perfil(harness, "android-01")
    _conta_chrome(harness, pid, senha=_valor())
    chrome = typable_secret_for(s.runs.dados, pid, CHROME)
    qa = typable_secret_for(s.runs.dados, pid, "com.pocqa.messenger")
    assert chrome.secret is not None and qa.secret is None
    assert pede_intervencao_humana(_arvore(MOTIVO_SENHA), tem_credencial=chrome.secret is not None) is False
    assert pede_intervencao_humana(_arvore(MOTIVO_SENHA), tem_credencial=qa.secret is not None) is True
    # Desafio (código não fornecido, captcha) continua com a pessoa, com ou sem credencial.
    assert pede_intervencao_humana(_arvore(MOTIVO_DESAFIO), tem_credencial=True) is True
    # Senha guardada SEM consentimento: a tela de senha é de pessoa, e a pendência tem nome.
    s.db.execute("UPDATE account_credentials SET consent_at=NULL WHERE account_id IN"
                 " (SELECT id FROM profile_accounts WHERE profile_id=? AND app_id='chrome')", (pid,))
    pendente = typable_secret_for(s.runs.dados, pid, CHROME)
    assert pendente.secret is None and pendente.pending_consent


async def _preparar_login(harness: Harness, monkeypatch: pytest.MonkeyPatch, *, host: str | None = HOST,
                          no_chrome: bool = True) -> tuple[Any, Any, Any, Any, str, str]:
    s = harness.state
    rt = s.devices.get("android-01")
    fake = harness.fakes["android-01"]
    fake.screen = "login"
    if no_chrome:
        monkeypatch.setattr(fake, "current_package", lambda: CHROME)
        fake.barra_de_endereco = f"{HOST}/login"
    ex = s.scheduler.executor
    monkeypatch.setattr(ex, "sensitive_input", SensitiveInputChannel(lambda: True))   # mascaramento "provado"
    pid = _perfil(harness, "android-01")
    valor = _valor()
    _conta_chrome(harness, pid, senha=valor, host=host)

    async def observar() -> Any:
        return s.devices.arvore(rt, await rt.executor.run(rt.io.page_source, timeout=10, label="hierarquia"))
    return rt, fake, ex, observar, pid, valor


def _preencher(ex: Any, rt: Any, pid: str, vista: Any, observar: Any, **kw: Any) -> Any:
    return ex.preenchedor(rt, lambda nome: resolve_secret(ex.dados, pid, nome), vista, observar, profile_id=pid, **kw)


async def test_type_secret_preenche_o_campo_de_senha_pelo_canal_sensivel(harness: Harness,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    rt, fake, ex, observar, pid, valor = await _preparar_login(harness, monkeypatch)
    vista = await observar()
    preencher = _preencher(ex, rt, pid, vista, observar, run_id="r-teste", step_id="s1")
    assert preencher is not None
    ctx = ToolContext(io=rt.io, call=lambda fn, *a: rt.executor.run(fn, *a, timeout=10), tree=vista, width=720,
                      height=1280, image_scale=1.0, app_package=None, app_activity=None, fill_secret=preencher)
    out = await execute_tool(ctx, "type_secret", TypeSecret(name=SENHA_CHROME, rationale="teste"))
    assert fake.login_fields.get("login_pin") == valor and "login_account" not in fake.login_fields
    assert out.result["typed_secret"] == SENHA_CHROME and valor not in str(out.result)
    # a conta registra o uso; a execução registra QUAL conta (nome), nunca o valor
    s = harness.state
    assert s.db.scalar("SELECT last_used_at FROM account_credentials c JOIN profile_accounts a ON a.id=c.account_id"
                       " WHERE a.profile_id=? AND a.app_id='chrome'", (pid,)) is not None
    decisao = s.db.query("SELECT message FROM events WHERE kind='decision' AND run_id='r-teste'")
    assert decisao and "Chrome (portal.exemplo.test)" in decisao[-1]["message"] and valor not in decisao[-1]["message"]
    # nome que a persona não tem: recusa sem efeito, e a mensagem lista o que há
    with pytest.raises(DriverError) as erro:
        await execute_tool(ctx, "type_secret", TypeSecret(name="conta_banco_senha", rationale="teste"))
    assert erro.value.effect_possible is False and SENHA_CHROME in str(erro.value)
    _em_lugar_nenhum(harness, valor)


async def test_type_secret_nunca_digita_em_campo_comum(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    """Campo que não é de senha mostraria o valor na hierarquia — e a hierarquia vai ao modelo."""
    rt, fake, ex, observar, pid, _ = await _preparar_login(harness, monkeypatch)
    vista = await observar()
    conta = next(e for e in vista.elements if e.resource_id.endswith("login_account"))
    preencher = _preencher(ex, rt, pid, vista, observar)
    with pytest.raises(DriverError):
        await preencher(SENHA_CHROME, conta.id)                    # type: ignore[misc]
    assert fake.login_fields == {}


async def test_sem_perfil_type_secret_recusa_sem_efeito(harness: Harness) -> None:
    ex = harness.state.scheduler.executor
    assert ex.preenchedor(harness.state.devices.get("android-03"), lambda n: resolve_secret(ex.dados, None, n),
                          parse_hierarchy("<hierarchy/>"), None, profile_id=None) is None  # type: ignore[arg-type]
    ctx = ToolContext(io=None, call=None, tree=parse_hierarchy("<hierarchy/>"), width=1, height=1,  # type: ignore[arg-type]
                      image_scale=1.0, app_package=None, app_activity=None)
    with pytest.raises(DriverError) as erro:
        await execute_tool(ctx, "type_secret", TypeSecret(name=SENHA_CHROME, rationale="teste"))
    assert erro.value.effect_possible is False


async def test_type_secret_so_no_pacote_da_conta(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    """A senha da conta do Chrome não vai para a tela de senha de outro app, seja qual for a etapa."""
    rt, fake, ex, observar, pid, _ = await _preparar_login(harness, monkeypatch, no_chrome=False)
    preencher = _preencher(ex, rt, pid, await observar(), observar)
    with pytest.raises(DriverError) as erro:
        await preencher(SENHA_CHROME, None)                        # type: ignore[misc]
    assert erro.value.effect_possible is False and fake.login_fields == {} and CHROME in str(erro.value)


async def test_type_secret_so_no_host_da_conta(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    rt, fake, ex, observar, pid, valor = await _preparar_login(harness, monkeypatch)
    for barra in ("outro.exemplo.test/login", "portal-parecido.exemplo.test/login", None):
        fake.barra_de_endereco = barra
        preencher = _preencher(ex, rt, pid, await observar(), observar)
        with pytest.raises(DriverError):
            await preencher(SENHA_CHROME, None)                    # type: ignore[misc]
        assert fake.login_fields == {}, barra
    fake.barra_de_endereco = "sso.portal.exemplo.test/entrar"      # subdomínio do site da conta
    preencher = _preencher(ex, rt, pid, await observar(), observar)
    await preencher(SENHA_CHROME, None)                            # type: ignore[misc]
    assert fake.login_fields.get("login_pin") == valor


async def test_conta_de_navegador_sem_host_nao_recebe_a_senha_em_site_nenhum(harness: Harness,
                                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    """Sem `host`, a trava de site não degrada para "qualquer site": recusa."""
    rt, fake, ex, observar, pid, _ = await _preparar_login(harness, monkeypatch, host=None)
    nome = account_name("chrome", None, "senha")
    assert resolve_secret(ex.dados, pid, nome).secret is not None
    preencher = _preencher(ex, rt, pid, await observar(), observar)
    with pytest.raises(DriverError) as erro:
        await preencher(nome, None)                                # type: ignore[misc]
    assert fake.login_fields == {} and "não tem host" in str(erro.value)


async def test_type_secret_exige_o_consentimento_da_conta(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    rt, fake, ex, observar, pid, _ = await _preparar_login(harness, monkeypatch)
    harness.state.db.execute("UPDATE account_credentials SET consent_at=NULL WHERE account_id IN"
                             " (SELECT id FROM profile_accounts WHERE profile_id=? AND app_id='chrome')", (pid,))
    preencher = _preencher(ex, rt, pid, await observar(), observar)
    with pytest.raises(DriverError) as erro:
        await preencher(SENHA_CHROME, None)                        # type: ignore[misc]
    assert erro.value.effect_possible is False and "consent" in str(erro.value) and fake.login_fields == {}


async def test_type_secret_acha_o_campo_quando_o_teclado_rola_a_pagina(harness: Harness,
                                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    """O modelo aponta o campo pela observação dele; ao focar, a página rola e as posições mudam."""
    rt, fake, ex, observar, pid, valor = await _preparar_login(harness, monkeypatch)
    fake.rola_ao_focar = True
    vista = await observar()
    pin = next(e for e in vista.elements if e.password)
    preencher = _preencher(ex, rt, pid, vista, observar)
    await preencher(SENHA_CHROME, pin.id)                          # type: ignore[misc]
    assert fake.login_fields.get("login_pin") == valor


async def test_senha_que_saiu_do_cofre_vira_falha_controlada(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    rt, fake, ex, observar, pid, _ = await _preparar_login(harness, monkeypatch)
    s = harness.state
    ref = s.db.scalar("SELECT secret_ref FROM account_credentials c JOIN profile_accounts a ON a.id=c.account_id"
                      " WHERE a.profile_id=? AND a.app_id='chrome'", (pid,))
    s.secrets.delete_secret(ref)                                   # a conta foi apagada no meio
    preencher = _preencher(ex, rt, pid, await observar(), observar)
    with pytest.raises(DriverError) as erro:
        await preencher(SENHA_CHROME, None)                        # type: ignore[misc]
    assert erro.value.effect_possible is False and not fake.login_fields.get("login_pin")


def test_instagram_fica_fora_do_type_secret(harness: Harness) -> None:
    """App com provedor de sessão: o login é determinístico, antes da tarefa; a senha não é oferecida ao modelo."""
    s = harness.state
    pid = s.social.create_profile(ProfileCreate(username="fora.do.type", instance_id="android-01",
                                                password=SecretStr(_valor()))).id
    assert s.social_repo.credential_row(pid) is not None
    assert "conta_instagram_senha" not in {d.name for d in available_data(s.runs.dados, pid)}
    recusa = resolve_secret(s.runs.dados, pid, "conta_instagram_senha")
    assert recusa.secret is None and "gerenciado" in (recusa.refusal or "")
    assert typable_secret_for(s.runs.dados, pid, "com.instagram.android").secret is None


# ---------------------------------------------------------------- 5. open_url: o comando e os sites das contas
async def test_open_url_so_abre_endereco_do_comando(harness: Harness) -> None:
    rt = harness.state.devices.get("android-01")
    fake = harness.fakes["android-01"]
    permitidas = set(urls_do_texto(COMANDO))
    assert permitidas == {"https://portal.exemplo.test/#/"}
    ctx = ToolContext(io=rt.io, call=lambda fn, *a: rt.executor.run(fn, *a, timeout=10),
                      tree=parse_hierarchy("<hierarchy/>"), width=1, height=1, image_scale=1.0, app_package=None,
                      app_activity=None, allowed_urls=permitidas)
    await execute_tool(ctx, "open_url", OpenUrl(url="https://portal.exemplo.test/#/", rationale="teste"))
    assert fake.urls_abertas == ["https://portal.exemplo.test/#/"]
    for ruim in ("https://outro.exemplo.test/", "javascript:alert(1)", "https://portal.exemplo.test/#/'; reboot",
                 "https://portal.exemplo.test/entrar"):
        with pytest.raises(DriverError):
            await execute_tool(ctx, "open_url", OpenUrl(url=ruim, rationale="teste"))
    assert fake.urls_abertas == ["https://portal.exemplo.test/#/"]


async def test_open_url_aceita_o_site_de_uma_conta_da_persona(harness: Harness) -> None:
    """Com o `host` da conta de portal (ADR-040), "entre no portal" abre o site sem a URL no comando — só ele e seus
    subdomínios; o parecido continua fora."""
    rt = harness.state.devices.get("android-01")
    fake = harness.fakes["android-01"]
    ctx = ToolContext(io=rt.io, call=lambda fn, *a: rt.executor.run(fn, *a, timeout=10),
                      tree=parse_hierarchy("<hierarchy/>"), width=1, height=1, image_scale=1.0, app_package=None,
                      app_activity=None, allowed_urls=set(), allowed_hosts={HOST})
    await execute_tool(ctx, "open_url", OpenUrl(url="https://portal.exemplo.test/entrar", rationale="teste"))
    await execute_tool(ctx, "open_url", OpenUrl(url="https://sso.portal.exemplo.test/login?x=1", rationale="teste"))
    for ruim in ("https://portal-parecido.exemplo.test/", "https://exemplo.test/portal.exemplo.test"):
        with pytest.raises(DriverError):
            await execute_tool(ctx, "open_url", OpenUrl(url=ruim, rationale="teste"))
    assert fake.urls_abertas == ["https://portal.exemplo.test/entrar", "https://sso.portal.exemplo.test/login?x=1"]


def test_credencial_e_endereco_nao_viram_receita() -> None:
    assert {"type_secret", "open_url"} <= UNSAFE_TO_REPLAY


def test_so_o_comando_autoriza_endereco_e_o_host_da_conta_autoriza_a_senha() -> None:
    """O planejador pode completar "portal MTR" com um domínio que ninguém escreveu: parâmetro do plano não autoriza
    `open_url`; e onde `type_secret` digita é o `host` da conta, nunca uma URL do comando ou do plano."""
    assert urls_da_pessoa("entre no portal MTR e faça login") == set()
    assert urls_da_pessoa(COMANDO) == {"https://portal.exemplo.test/#/"}


# ---------------------------------------------------------------- 6. pré-voo: `requires.secrets` da skill
async def test_pre_voo_recusa_aparelho_sem_a_credencial_que_a_skill_exige(tmp_path: Path) -> None:
    h = Harness(tmp_path, 3)
    h.cfg.file.skills.enabled = True
    s = await h.boot()
    try:
        doc = yaml.safe_load((FIXTURES / "ig.abrir_conversa.yaml").read_text(encoding="utf-8"))
        doc["spec"]["requires"] = {"apps": ["instagram"], "secrets": [SENHA_CHROME]}
        v = s.skill_repo.create_draft("ig.abrir_conversa", doc, source=Provenance(SourceKind.MANUAL), by=DONO)
        s.skill_repo.transition(v.ref, SkillState.CANDIDATE, by=DONO, reason="submetida")
        s.skill_repo.transition(v.ref, SkillState.VALIDATED, by=DONO, manual=True, reason="teste")
        s.skill_repo.transition(v.ref, SkillState.PUBLISHED, by=DONO, reason="teste")
        comando = "abra a conversa com @ana no instagram"
        assert s.runs._segredos_exigidos(comando) == (SENHA_CHROME,)               # noqa: SLF001
        assert s.runs._segredos_exigidos("comando que não casa com nada") == ()      # noqa: SLF001
        com = _perfil(h, "android-01")
        _conta_chrome(h, com, senha=_valor())
        _perfil(h, "android-02")                                                    # perfil sem a conta do portal
        impedidos = s.runs.pre_voo(["android-01", "android-02", "android-03"], secret_names=[SENHA_CHROME])
        assert "android-01" not in impedidos
        assert impedidos["android-02"]["code"] == "missing_credential" and "consentimento" in impedidos["android-02"]["motivo"]
        assert impedidos["android-03"]["code"] == "missing_credential" and "perfil" in impedidos["android-03"]["motivo"]
        with pytest.raises(RunError) as erro:
            s.runs.create(_pedido(command=comando, ids=["android-01", "android-02"]))
        assert erro.value.code == "preflight" and erro.value.details["ready"] == ["android-01"]
        assert [d["instance_id"] for d in erro.value.details["devices"]] == ["android-02"]
    finally:
        await s.stop()


# ---------------------------------------------------------------- 7. planejador: o app que o comando pede (inalterado)
def test_comando_de_site_ou_de_outro_app_nao_fica_preso_ao_catalogo_do_aparelho() -> None:
    apps = [AppContext("instagram", "Instagram", "com.instagram.android", None, None, None),
            AppContext("chrome", "Chrome", CHROME, None, None, None)]
    insta = {"com.instagram.android"}
    assert pede_outro_alvo(COMANDO, apps, insta)
    assert pede_outro_alvo("entre em https://exemplo.test e confira o saldo", apps, insta)
    assert pede_outro_alvo("abra o chrome", apps, insta)
    assert not pede_outro_alvo('envie "oi" para @fulano no direct', apps, insta)
    assert not pede_outro_alvo("curtir a última foto de @fulano no Instagram", apps, insta)


async def test_url_com_usuario_e_senha_no_comando_e_recusada(harness: Harness) -> None:
    valor = _valor()
    with pytest.raises(RunError) as erro:
        harness.state.runs.create(_pedido(command=f"abra https://qa-operador:{valor}@portal.exemplo.test/"))
    assert erro.value.code == "credencial_no_comando" and valor not in erro.value.message


def test_url_ou_site_dentro_da_mensagem_nao_tira_do_catalogo() -> None:
    apps = [AppContext("instagram", "Instagram", "com.instagram.android", None, None, None),
            AppContext("chrome", "Chrome", CHROME, None, None, None)]
    insta = {"com.instagram.android"}
    assert not pede_outro_alvo('envie a mensagem "veja https://exemplo.test" para @fulano', apps, insta)
    assert not pede_outro_alvo("comente 'conheça nosso site' no post de @fulano", apps, insta)
    assert pede_outro_alvo("acesse o portal https://exemplo.test e confira o saldo", apps, insta)


def test_parentese_que_faz_parte_da_url_fica() -> None:
    assert urls_do_texto("leia https://pt.wikipedia.org/wiki/Java_(linguagem) agora") == [
        "https://pt.wikipedia.org/wiki/Java_(linguagem)"]
    assert urls_do_texto("(veja https://exemplo.test/a).") == ["https://exemplo.test/a"]


def test_pagina_no_comando_do_instagram_nao_tira_do_catalogo() -> None:
    apps = [AppContext("instagram", "Instagram", "com.instagram.android", None, None, None)]
    assert not pede_outro_alvo("entre no perfil @fulano e curta o post da página principal", apps,
                               {"com.instagram.android"})
