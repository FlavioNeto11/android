"""Credencial fornecida pela pessoa para a execução (ADR-025).

Execução `r-20260926161438-22d65f` (26/09/2026): "abra o Chrome e entre no site… Senha: …". A senha ficou em claro em
`runs.command`, na API de execuções e no prompt do planejador (só os eventos saíam mascarados); e o planejador recusou
porque só recebeu as ações do Instagram e porque "o sistema não digita credenciais". Decisão do dono no mesmo dia: a
automação entra com a credencial que a pessoa forneceu, com consentimento — pelo campo `credentials`, pelo cofre e
pelo canal sensível, nunca pelo texto do comando.

O valor usado aqui é gerado no próprio teste; nenhum segredo real.
"""
from __future__ import annotations

import secrets as pysecrets
from typing import Any

import pytest
from pydantic import SecretStr

from app.automation.driver import DriverError
from app.automation.hierarchy import MOTIVO_DESAFIO, MOTIVO_SENHA, parse_hierarchy
from app.automation.tools import OpenUrl, ToolContext, TypeSecret, execute_tool, urls_do_texto
from app.models import RunCreate, RunStatus
from app.planning.provider import AppContext
from app.security.sensitive_input import SensitiveInputChannel
from app.taskqueue.executor import pede_intervencao_humana
from app.taskqueue.recipes import UNSAFE_TO_REPLAY
from app.taskqueue.service import RunError, pede_outro_alvo

from .conftest import Harness

COMANDO = "abra o Chrome e entre no site (https://portal.exemplo.test/#/) com o usuário qa-operador e avance até logar"


def _valor() -> str:
    return "Tst-" + pysecrets.token_urlsafe(9)


def _pedido(harness: Harness, *, command: str = COMANDO, credentials: dict[str, str] | None = None,
            consent: bool = False, mode: str = "plan") -> RunCreate:
    return RunCreate(command=command, instance_ids=["android-01"], mode=mode,  # type: ignore[arg-type]
                     idempotency_key=f"cred-{pysecrets.token_hex(6)}",
                     credentials={k: SecretStr(v) for k, v in (credentials or {}).items()},
                     consent_credentials=consent)


def _em_lugar_nenhum(harness: Harness, valor: str) -> None:
    """O valor não aparece em nenhuma tabela de texto que a API, o painel ou o modelo leem."""
    db = harness.state.db
    for tabela, colunas in (("runs", "command, status_detail, plan"), ("events", "message, data"),
                            ("actions", "args, result"), ("ai_calls", "error_message")):
        for linha in db.query(f"SELECT {colunas} FROM {tabela}"):
            assert valor not in " ".join(str(v) for v in dict(linha).values()), tabela


# ---------------------------------------------------------------- 1. senha no texto do comando: recusada, nada gravado
async def test_senha_no_comando_e_recusada_antes_de_gravar_e_de_planejar(harness: Harness,
                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    s = harness.state
    valor = _valor()
    planejou: list[Any] = []
    monkeypatch.setattr(s.runs.provider, "plan", lambda req: planejou.append(req))
    antes = s.db.one("SELECT COUNT(*) AS n FROM runs")["n"]
    with pytest.raises(RunError) as erro:
        s.runs.create(_pedido(harness, command=f"{COMANDO}\nSenha: {valor}"))
    assert erro.value.code == "credencial_no_comando" and erro.value.status == 409
    assert valor not in erro.value.message
    assert s.db.one("SELECT COUNT(*) AS n FROM runs")["n"] == antes and planejou == []
    _em_lugar_nenhum(harness, valor)


def test_redacao_no_repositorio_e_a_segunda_linha(harness: Harness) -> None:
    """Mesmo quem chama o repositório direto (sem o serviço) não grava a senha em claro."""
    valor = _valor()
    row, _ = harness.state.repo.create_run(_pedido(harness, command=f"entre no portal. senha: {valor}"),
                                           simulated=True)
    assert valor not in row["command"]


# ---------------------------------------------------------------- 2. consentimento
async def test_credencial_sem_consentimento_pede_confirmacao_e_nao_cria_nada(harness: Harness) -> None:
    s = harness.state
    valor = _valor()
    antes = s.db.one("SELECT COUNT(*) AS n FROM runs")["n"]
    with pytest.raises(RunError) as erro:
        s.runs.create(_pedido(harness, credentials={"senha": valor}))
    assert erro.value.code == "consentimento_de_credencial" and erro.value.status == 409
    assert erro.value.details["credentials"] == ["senha"] and erro.value.details["instance_ids"] == ["android-01"]
    msg = erro.value.message
    assert valor not in msg and "provedor de IA" in msg and "cofre" in msg
    assert s.db.one("SELECT COUNT(*) AS n FROM runs")["n"] == antes
    assert s.db.one("SELECT COUNT(*) AS n FROM run_secrets")["n"] == 0


def test_nome_de_credencial_invalido_e_valor_vazio_sao_recusados() -> None:
    with pytest.raises(ValueError):
        RunCreate(command="abra o site", instance_ids=["android-01"], idempotency_key="cred-nome-ruim",
                  credentials={"Minha Senha": SecretStr("x")})
    with pytest.raises(ValueError):
        RunCreate(command="abra o site", instance_ids=["android-01"], idempotency_key="cred-vazia-01",
                  credentials={"senha": SecretStr("")})


# ---------------------------------------------------------------- 3. com consentimento: cofre, só o nome circula
async def test_com_consentimento_a_credencial_vai_ao_cofre_e_o_modelo_so_ve_o_nome(
        harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    s = harness.state
    valor = _valor()
    pedidos: list[Any] = []
    original = s.runs.provider.plan

    async def espiao(req: Any) -> Any:
        pedidos.append(req)
        return await original(req)
    monkeypatch.setattr(s.runs.provider, "plan", espiao)
    run = s.runs.create(_pedido(harness, credentials={"senha": valor}, consent=True))
    await harness.wait(lambda: bool(pedidos), what="planejamento")
    refs = s.repo.run_secret_refs(run.id)
    assert list(refs) == ["senha"]
    assert s.secrets.get_secret(refs["senha"]) == valor           # cifrado no cofre, recuperável só por ali
    cifra = s.db.one("SELECT ciphertext FROM secrets WHERE ref=?", (refs["senha"],))["ciphertext"]
    assert valor.encode() not in bytes(cifra)
    assert pedidos[0].secret_names == ["senha"] and valor not in pedidos[0].command
    await harness.wait_run(run.id, statuses=("planned", "needs_input", "failed", "completed"), timeout=30)
    _em_lugar_nenhum(harness, valor)


async def test_execucao_terminada_apaga_a_credencial_do_cofre(harness: Harness) -> None:
    s = harness.state
    run = s.runs.create(_pedido(harness, credentials={"senha": _valor()}, consent=True))
    ref = s.repo.run_secret_refs(run.id)["senha"]
    await harness.wait_run(run.id, statuses=("planned", "needs_input", "failed", "completed"), timeout=30)
    s.repo.set_run_status(run.id, RunStatus.cancelled, "teste")
    assert s.repo.run_secret_refs(run.id) == {} and not s.secrets.exists(ref)


# ---------------------------------------------------------------- 4. executor: tela de senha e canal sensível
def _arvore(motivo: str) -> Any:
    senha = motivo == MOTIVO_SENHA
    xml = ('<hierarchy><node class="android.widget.EditText" resource-id="x:id/campo" text="" content-desc="" '
           f'clickable="true" password="{str(senha).lower()}" bounds="[0,0][100,50]" /></hierarchy>')
    tree = parse_hierarchy(xml)
    tree.sensitive, tree.sensitive_reason = True, motivo
    return tree


def test_tela_de_senha_so_pede_pessoa_quando_nao_ha_credencial() -> None:
    assert pede_intervencao_humana(_arvore(MOTIVO_SENHA)) is True
    assert pede_intervencao_humana(_arvore(MOTIVO_SENHA), tem_credencial=True) is False
    # Desafio (código não fornecido, captcha) continua com a pessoa, com ou sem credencial.
    assert pede_intervencao_humana(_arvore(MOTIVO_DESAFIO), tem_credencial=True) is True


async def test_type_secret_preenche_o_campo_de_senha_pelo_canal_sensivel(harness: Harness,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    s = harness.state
    rt = s.devices.get("android-01")
    fake = harness.fakes["android-01"]
    fake.screen = "login"
    valor = _valor()
    ref = s.secrets.store_secret(valor)
    ex = s.scheduler.executor
    monkeypatch.setattr(ex, "sensitive_input", SensitiveInputChannel(lambda: True))   # mascaramento "provado"

    async def observar() -> Any:
        return s.devices.arvore(rt, await rt.executor.run(rt.io.page_source, timeout=10, label="hierarquia"))
    vista = await observar()
    preencher = ex.preenchedor(rt, {"senha": ref}, vista, observar)
    assert preencher is not None
    ctx = ToolContext(io=rt.io, call=lambda fn, *a: rt.executor.run(fn, *a, timeout=10), tree=vista, width=720,
                      height=1280, image_scale=1.0, app_package=None, app_activity=None, fill_secret=preencher)
    out = await execute_tool(ctx, "type_secret", TypeSecret(name="senha", rationale="teste"))
    assert fake.login_fields.get("login_pin") == valor and "login_account" not in fake.login_fields
    assert out.result["typed_secret"] == "senha" and valor not in str(out.result)
    # nome que ninguém forneceu: recusa sem efeito
    with pytest.raises(DriverError) as erro:
        await execute_tool(ctx, "type_secret", TypeSecret(name="token", rationale="teste"))
    assert erro.value.effect_possible is False


async def test_type_secret_nunca_digita_em_campo_comum(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    """Campo que não é de senha mostraria o valor na hierarquia — e a hierarquia vai ao modelo."""
    s = harness.state
    rt = s.devices.get("android-01")
    fake = harness.fakes["android-01"]
    fake.screen = "login"
    ex = s.scheduler.executor
    monkeypatch.setattr(ex, "sensitive_input", SensitiveInputChannel(lambda: True))

    async def observar() -> Any:
        return s.devices.arvore(rt, await rt.executor.run(rt.io.page_source, timeout=10, label="hierarquia"))
    vista = await observar()
    conta = next(e for e in vista.elements if e.resource_id.endswith("login_account"))
    preencher = ex.preenchedor(rt, {"senha": s.secrets.store_secret(_valor())}, vista, observar)
    with pytest.raises(DriverError):
        await preencher("senha", conta.id)                        # type: ignore[misc]
    assert fake.login_fields == {}


async def test_sem_credencial_type_secret_recusa_sem_efeito() -> None:
    ctx = ToolContext(io=None, call=None, tree=parse_hierarchy("<hierarchy/>"), width=1, height=1,  # type: ignore[arg-type]
                      image_scale=1.0, app_package=None, app_activity=None)
    with pytest.raises(DriverError) as erro:
        await execute_tool(ctx, "type_secret", TypeSecret(name="senha", rationale="teste"))
    assert erro.value.effect_possible is False


# ---------------------------------------------------------------- 5. open_url: só o que a pessoa escreveu
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
    for ruim in ("https://outro.exemplo.test/", "javascript:alert(1)", "https://portal.exemplo.test/#/'; reboot"):
        with pytest.raises(DriverError):
            await execute_tool(ctx, "open_url", OpenUrl(url=ruim, rationale="teste"))
    assert fake.urls_abertas == ["https://portal.exemplo.test/#/"]


def test_credencial_e_endereco_nao_viram_receita() -> None:
    assert {"type_secret", "open_url"} <= UNSAFE_TO_REPLAY


# ---------------------------------------------------------------- 6. planejador: o app que o comando pede
def test_comando_de_site_ou_de_outro_app_nao_fica_preso_ao_catalogo_do_aparelho() -> None:
    apps = [AppContext("instagram", "Instagram", "com.instagram.android", None, None, None),
            AppContext("chrome", "Chrome", "com.android.chrome", None, None, None)]
    insta = {"com.instagram.android"}
    assert pede_outro_alvo(COMANDO, apps, insta)
    assert pede_outro_alvo("entre em https://exemplo.test e confira o saldo", apps, insta)
    assert pede_outro_alvo("abra o chrome", apps, insta)
    assert not pede_outro_alvo('envie "oi" para @fulano no direct', apps, insta)
    assert not pede_outro_alvo("curtir a última foto de @fulano no Instagram", apps, insta)
