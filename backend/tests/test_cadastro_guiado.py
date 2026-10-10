"""31.310 (ADR-087, adendo v1.137): o cadastro guiado da conta planejada, de ponta a ponta com um app e um aparelho FALSOS.

O app de teste tem o próprio `cadastro.yaml` (numa pasta temporária; nenhum app real o declara) e uma tela por estado do
fluxo: boas-vindas, formulário, código por e-mail, sucesso e as telas que só uma pessoa resolve. O aparelho é uma `Mesa`
falsa que devolve a árvore do app de teste; a conta, o ciclo e a sessão são os REAIS (banco do harness, `transicao` com
comparar e trocar). A caixa de e-mail é uma classe do teste que só entrega mensagem recebida depois do `desde` pedido.

Prova `simulated`. Nenhuma conta em nenhum serviço, nenhum aparelho de verdade. Criar uma conta real num provedor é `not_run`
até o dono autorizar (decisões D1 e D2 do desenho).
"""
from __future__ import annotations

import asyncio
import json
import re
import secrets as pysecrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from xml.sax.saxutils import quoteattr

import pytest

from app.automation.hierarchy import UiElement, UiTree, parse_hierarchy
from app.integrations.app_declarado import cadastro_conhecimento
from app.integrations.app_declarado.cadastro import FalhaNaMesa, MotorDeCadastro
from app.integrations.app_declarado.cadastro_conhecimento import CadastroInvalido, de_dados
from app.models import InstanceState
from app.modules.identity.domain.cadastro import Parada, Passo
from app.modules.identity.infrastructure import cadastro_guiado
from app.modules.identity.infrastructure.cadastro_guiado import VERBO, CadastroGuiado, CicloNoBanco
from app.social.erros import SocialError

from .conftest import Harness
from .test_conta_planejada import _api, _persona, _preparar, _senha_do_cofre, _varrer

PACOTE = "com.exemplo.cadastro"
APP_ID = "exemplo"
CODIGO = "482915"

YAML = """
app: com.exemplo.cadastro
rotulo: Exemplo
passos_max: 30
espera_s: 0
envio_espera_s: 0
codigo_espera_s: 5
telas:
  - {nome: usuario_indisponivel, sinais: ["nome de usuario ja esta em uso"], acao: parar, motivo: usuario_indisponivel}
  - {nome: captcha, sinais: ["prove que voce nao e um robo"], acao: parar, motivo: captcha}
  - {nome: telefone, sinais: ["informe seu telefone"], acao: parar, motivo: telefone}
  - {nome: inicio, sinais: ["bem-vindo ao exemplo"], acao: tocar, botao: {texto: "criar conta"}}
  - nome: formulario
    sinais: ["cadastre-se"]
    acao: preencher
    campos:
      - {dado: usuario, id: ":id/username"}
      - {dado: nome, id: ":id/fullname"}
      - {dado: email, id: ":id/email"}
      - {dado: senha, id: ":id/password", segredo: true}
    botao: {texto: "cadastrar"}
    envia: true
  - {nome: codigo, sinais: ["digite o codigo enviado"], acao: codigo, campo: {id: ":id/code"}, botao: {texto: "continuar"}}
  - {nome: sucesso, sinais: ["conta criada com sucesso"], acao: sucesso, conta: {id: ":id/profile_name"}}
"""


# ------------------------------------------------------------------------------------------------ o app e o aparelho falsos
def _no(cls: str, rid: str, texto: str, y: int, *, senha: bool = False, clicavel: bool = False) -> str:
    id_ = f"{PACOTE}:id/{rid}" if rid else ""
    extra = (' password="true"' if senha else "") + (' clickable="true"' if clicavel else "")
    return (f'<node class="{cls}" package="{PACOTE}" resource-id="{id_}" text={quoteattr(texto)} enabled="true"{extra} '
            f'bounds="[0,{y}][100,{y + 90}]"/>')


@dataclass
class AppFalso:
    """Um app de cadastro com a tela de cada passo. `depois_do_envio` e `depois_do_codigo` dizem para onde ele vai."""

    depois_do_envio: str = "codigo"
    depois_do_codigo: str = "sucesso"
    handle_final: str | None = None                       # o @ que a tela de sucesso mostra (padrão: o digitado)
    tela: str = "inicio"
    valores: dict[str, str] = field(default_factory=lambda: {"username": "", "fullname": "", "email": "", "password": "",
                                                              "code": ""})
    foco: str | None = None
    envios: int = 0
    toques: list[str] = field(default_factory=list)
    digitados: list[str] = field(default_factory=list)     # texto COMUM digitado (a senha e o código nunca entram aqui)
    sensiveis: list[tuple[str, str]] = field(default_factory=list)   # (campo, valor) recebidos pelo canal sensível
    fora_do_ar: bool = False
    humano_apos: str | None = None                        # o desafio surge depois deste campo ser preenchido (id do campo)
    outro_app_apos: str | None = None                     # idem, mas é outro app que passa para a frente
    cancela_apos_envio: bool = False                      # a tarefa é cancelada logo depois do toque em "Cadastrar"

    def _elementos(self) -> list[tuple[str, str, str, str, bool, bool]]:
        """(classe, id, texto, nome-do-toque, senha, clicável) da tela de agora, de cima para baixo."""
        v = self.valores
        t = self.tela
        if t == "formulario" and self.humano_apos and v[self.humano_apos]:
            t = "humano"
        if t == "inicio":
            return [("android.widget.TextView", "", "Bem-vindo ao Exemplo", "", False, False),
                    ("android.widget.Button", "start", "Criar conta", "criar", False, True)]
        if t == "formulario":
            return [("android.widget.TextView", "", "Cadastre-se", "", False, False),
                    ("android.widget.EditText", "username", v["username"], "campo:username", False, True),
                    ("android.widget.EditText", "fullname", v["fullname"], "campo:fullname", False, True),
                    ("android.widget.EditText", "email", v["email"], "campo:email", False, True),
                    ("android.widget.EditText", "password", "•••" if v["password"] else "", "campo:password", True, True),
                    ("android.widget.Button", "submit", "Cadastrar", "cadastrar", False, True)]
        if t == "codigo":
            return [("android.widget.TextView", "", "Digite o codigo enviado ao seu e-mail", "", False, False),
                    ("android.widget.EditText", "code", "•••" if v["code"] else "", "campo:code", False, True),
                    ("android.widget.Button", "go", "Continuar", "continuar", False, True)]
        if t == "sucesso":
            return [("android.widget.TextView", "", "Conta criada com sucesso", "", False, False),
                    ("android.widget.TextView", "profile_name", "@" + (self.handle_final or v["username"]), "", False, False)]
        textos = {"usuario_indisponivel": "Este nome de usuario ja esta em uso. Tente outro.",
                  "captcha": "Prove que voce nao e um robo", "telefone": "Informe seu telefone para continuar",
                  "humano": "Confirm you're human to use your account", "estranha": "Alguma coisa que ninguem declarou"}
        return [("android.widget.TextView", "", textos[t], "", False, False)]

    def arvore(self) -> UiTree:
        nos = [_no(c, rid, txt, i * 100, senha=senha, clicavel=clic)
               for i, (c, rid, txt, _, senha, clic) in enumerate(self._elementos())]
        return parse_hierarchy("<hierarchy>" + "".join(nos) + "</hierarchy>")

    def tocar(self, x: int, y: int) -> None:
        indice = y // 100
        nome = self._elementos()[indice][3] if indice < len(self._elementos()) else ""
        self.toques.append(nome)
        if nome.startswith("campo:"):
            self.foco = nome.split(":")[1]
        elif nome == "criar":
            self.tela = "formulario"
        elif nome == "cadastrar":
            self.envios += 1
            self.tela = self.depois_do_envio
        elif nome == "continuar":
            self.tela = self.depois_do_codigo if self.depois_do_codigo != "recusado" else "codigo"


class MesaFalsa:
    def __init__(self, app: AppFalso) -> None:
        self.app = app

    async def observar(self) -> tuple[UiTree, str | None]:
        if self.app.fora_do_ar:
            raise FalhaNaMesa("DriverTimeout")
        a = self.app
        if a.outro_app_apos and a.tela == "formulario" and a.valores[a.outro_app_apos]:
            return a.arvore(), "com.outro.app"
        return a.arvore(), PACOTE

    async def tocar(self, x: int, y: int) -> None:
        enviados = self.app.envios
        self.app.tocar(x, y)
        if self.app.cancela_apos_envio and self.app.envios > enviados:
            raise asyncio.CancelledError                       # o toque chegou ao app; a tarefa morre antes de qualquer gravação

    async def digitar(self, texto: str) -> None:
        assert self.app.foco is not None
        self.app.digitados.append(texto)
        self.app.valores[self.app.foco] = texto

    async def digitar_sensivel(self, localizar: Callable[[UiTree], UiElement | None], segredo: Callable[[], str]) -> None:
        campo = localizar(self.app.arvore())
        if campo is None:
            raise FalhaNaMesa("SensitiveInputError")
        rid = campo.resource_id.split("/")[-1]
        valor = segredo()
        self.app.valores[rid] = valor
        self.app.sensiveis.append((rid, valor))

    async def esperar(self, segundos: float) -> None:
        return None


class CaixaFalsa:
    """A caixa de e-mail da conta: guarda (recebido_em, código) e só entrega o que chegou depois do `desde` pedido."""

    def __init__(self) -> None:
        self.mensagens: list[tuple[datetime, str]] = []
        self.pedidos: list[datetime] = []

    def chegou(self, codigo: str, *, ha_s: float = 0) -> None:
        self.mensagens.append((datetime.now(UTC) - timedelta(seconds=ha_s), codigo))

    async def codigo_depois_de(self, account_id: str, desde: datetime, *, espera_s: float) -> str | None:
        self.pedidos.append(desde)
        validas = [c for quando, c in self.mensagens if quando >= desde - timedelta(seconds=5)]
        return validas[-1] if validas else None


RT = SimpleNamespace(id="android-01", state=InstanceState.online)


@dataclass
class Cenario:
    h: Harness
    pid: str
    aid: str
    app: AppFalso
    caixa: CaixaFalsa
    cadastro: CadastroGuiado
    despachos: list[dict[str, Any]]
    desejado: str
    endereco: str

    async def rodar(self) -> RuntimeError | None:
        """Executa o comando que a rota despachou (a fábrica que ela entregou ao despacho). Uma parada faz o comando terminar
        `failed`: a exceção é só o código fechado, e é devolvida para o teste olhar."""
        try:
            await self.despachos[-1]["factory"]()
        except RuntimeError as exc:
            assert str(exc).startswith("cadastro guiado parado: "), exc
            return exc
        return None

    def estado(self) -> str:
        return str(self.h.state.db.scalar("SELECT provisioning_state FROM profile_accounts WHERE id=?", (self.aid,)))

    def info(self) -> Any:
        return self.h.state.social.get_account(self.pid, self.aid).provisioning


async def _cenario(h: Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, app: AppFalso | None = None,
                   com_caixa: bool = True, yaml: str | None = YAML, preparar: bool = True,
                   sem_usuario: bool = False) -> Cenario:
    if yaml is not None:
        pasta = tmp_path / "apps" / PACOTE
        pasta.mkdir(parents=True)
        (pasta / "cadastro.yaml").write_text(yaml, encoding="utf-8", newline="\n")
    monkeypatch.setattr(cadastro_conhecimento, "PASTA_DOS_APPS", tmp_path / "apps")
    cadastro_conhecimento.do_app.cache_clear()
    if not h.state.db.scalar("SELECT id FROM apps WHERE id=?", (APP_ID,)):
        h.state.db.execute("INSERT INTO apps(id, name, package, activity, builtin) VALUES (?,?,?,?,0)",
                           (APP_ID, "Exemplo (simulado)", PACOTE, "Main"))
    pid = _persona(h)
    sufixo = pysecrets.token_hex(3)
    desejado, endereco = f"maria.teste{sufixo}", f"maria.teste{sufixo}@parque.exemplo.test"
    async with _api(h) as c:
        corpo: dict[str, object] = {"app_id": APP_ID}
        if not sem_usuario:
            corpo["desired_handle"] = desejado
        r = await c.post(f"/api/instagram/profiles/{pid}/accounts/planned", json=corpo)
        assert r.status_code in (200, 201), r.text
        aid = r.json()["id"]
        if preparar:
            assert (await _preparar(c, pid, aid, modo="gerar")).status_code == 200
    if com_caixa:
        h.state.db.execute(
            "INSERT INTO caixas_email(account_id, profile_id, endereco, dominio, secret_ref, key_id, criada_em)"
            " VALUES (?,?,?,?,?,?,?)", (aid, pid, endereco, "parque.exemplo.test", "ref-que-nao-e-lida", "k1", "2026-10-10T00:00:00Z"))
    app = app or AppFalso()
    caixa = CaixaFalsa()
    despachos: list[dict[str, Any]] = []

    def pedir(s: object, rt: object, verb: str, factory: Callable[[], Any], **kw: Any) -> dict[str, object]:
        despachos.append({"verb": verb, "factory": factory, **kw})
        return {"accepted": True, "command_id": f"c-{len(despachos)}", "state": "dispatched", "deduplicated": False,
                "instance_id": "android-01"}

    monkeypatch.setattr(cadastro_guiado, "pedir_trabalho_de_app", pedir)
    monkeypatch.setattr(cadastro_guiado, "MesaDoAparelho", lambda s, rt: MesaFalsa(app))
    monkeypatch.setattr(cadastro_guiado, "CodigoDoEmailDoParque", lambda db, leitor: caixa)
    monkeypatch.setattr(CadastroGuiado, "_aparelho", lambda self, profile_id, instance_id: RT)
    return Cenario(h, pid, aid, app, caixa, CadastroGuiado(h.state), despachos, desejado, endereco)


def _iniciar(cen: Cenario, **kw: Any) -> dict[str, object]:
    return cen.cadastro.iniciar(cen.pid, cen.aid, instance_id=None, by="painel:teste", **kw)


@pytest.fixture(autouse=True)
def _limpar_cache() -> Any:
    yield
    cadastro_conhecimento.do_app.cache_clear()


# ------------------------------------------------------------------------------------------------ o ciclo feliz
async def test_ciclo_completo_ate_confirmada_pela_sessao_observada(harness: Harness, tmp_path: Path,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    cen = await _cenario(harness, tmp_path, monkeypatch)
    cen.caixa.chegou(CODIGO, ha_s=-1)                       # chega "depois do envio" (o relógio do teste anda para frente)
    resposta = _iniciar(cen)
    assert resposta["accepted"] and cen.despachos[0]["verb"] == VERBO == "session.cadastrar"
    assert cen.despachos[0]["requested_by"] == "painel:teste"
    assert cen.estado() == "credencial_preparada"           # o comando ainda não rodou: só a rota validou e despachou
    assert await cen.rodar() is None

    assert cen.estado() == "confirmada"
    info = cen.info()
    assert info.evidence == {"kind": "sessao", "ref": "android-01"} and info.confirmed_at and info.proximo_passo is None
    conta = harness.state.social.get_account(cen.pid, cen.aid)
    assert conta.handle == cen.desejado
    sessao = harness.state.social_repo.account_session_row(cen.pid, cen.aid, "android-01")
    assert sessao is not None and sessao["status"] == "session_ready"
    # o formulário foi enviado UMA vez, com o texto comum pelo caminho comum e a senha só pelo canal sensível
    app = cen.app
    assert app.envios == 1 and app.toques.count("cadastrar") == 1
    senha = _senha_do_cofre(harness, cen.aid)
    assert (("password", senha) in app.sensiveis) and ("code", CODIGO) in app.sensiveis
    assert senha not in app.digitados and CODIGO not in app.digitados
    assert cen.desejado in app.digitados and cen.endereco in app.digitados
    # o código pedido à caixa vale a partir do envio, nunca de antes
    assert cen.caixa.pedidos and cen.caixa.pedidos[0] <= datetime.now(UTC)


async def test_a_trilha_do_cadastro_so_tem_ids_e_codigos_fechados(harness: Harness, tmp_path: Path,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    cen = await _cenario(harness, tmp_path, monkeypatch)
    cen.caixa.chegou(CODIGO, ha_s=-1)
    _iniciar(cen)
    await cen.rodar()
    eventos = [json.loads(r["data"]) for r in harness.state.db.query(
        "SELECT data FROM events WHERE kind IN ('identity.conta.provisionamento','identity.cadastro') ORDER BY id")]
    transicoes = [(e["evento"], e.get("passo")) for e in eventos if "evento" in e and e["account_id"] == cen.aid]
    assert ("iniciar_cadastro", "inicio") in transicoes and ("enviado", "envio") in transicoes
    assert ("confirmar", "confirmacao") in transicoes
    final = [e for e in eventos if e.get("resultado") == "confirmada"]
    assert final and set(final[0]) <= {"profile_id", "account_id", "instance_id", "resultado", "motivo", "duracao_s"}
    senha = _senha_do_cofre(harness, cen.aid)
    for valor in (senha, CODIGO, cen.desejado, cen.endereco):
        assert valor not in json.dumps(eventos), valor
    _varrer(harness, senha)
    _varrer(harness, CODIGO)


# ------------------------------------------------------------------------------------------------ as paradas
@pytest.mark.parametrize("app, parada, estado_final, resume", [
    (AppFalso(depois_do_envio="captcha"), "captcha", "falha", "aguardando_verificacao"),
    (AppFalso(depois_do_envio="telefone"), "telefone", "falha", "aguardando_verificacao"),
    (AppFalso(depois_do_envio="humano"), "desafio", "falha", "aguardando_verificacao"),
    (AppFalso(depois_do_envio="usuario_indisponivel"), "usuario_indisponivel", "falha", "aguardando_cadastro_externo"),
    (AppFalso(depois_do_envio="estranha"), "tela_desconhecida", "falha", "aguardando_verificacao"),
    (AppFalso(depois_do_envio="codigo", depois_do_codigo="recusado"), "codigo_nao_chegou", "falha",
     "aguardando_verificacao"),
    (AppFalso(depois_do_envio="codigo", depois_do_codigo="sucesso", handle_final="@outra.pessoa"), "conta_nao_lida", "falha",
     "aguardando_verificacao"),
    (AppFalso(depois_do_envio="codigo", depois_do_codigo="humano"), "desafio", "falha", "aguardando_verificacao"),
])
async def test_cada_parada_vira_falha_com_codigo_fechado_e_ninguem_mais_toca(
        harness: Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, app: AppFalso, parada: str, estado_final: str,
        resume: str) -> None:
    cen = await _cenario(harness, tmp_path, monkeypatch, app=app)
    cen.caixa.chegou(CODIGO, ha_s=-1)
    _iniciar(cen)
    erro = await cen.rodar()
    assert erro is not None and str(erro) == f"cadastro guiado parado: {parada}"          # o comando termina `failed`
    assert cen.estado() == estado_final
    info = cen.info()
    assert info.proximo_passo == f"aguardando_pessoa:{parada}" and info.detail == info.proximo_passo
    assert info.resume_state == resume and info.confirmed_at is None
    assert cen.app.envios == 1                              # o formulário nunca foi enviado duas vezes
    ultimo = json.loads(harness.state.db.query(
        "SELECT data FROM events WHERE kind='identity.cadastro' ORDER BY id DESC LIMIT 1")[0]["data"])
    assert ultimo["resultado"] == "parada" and ultimo["motivo"] == parada
    n = len(cen.app.toques)
    await cen.rodar()                                       # rodar de novo, em `falha`, não toca em nada
    assert len(cen.app.toques) == n and cen.estado() == "falha" and cen.info().proximo_passo == f"aguardando_pessoa:{parada}"


async def test_o_formulario_ainda_na_tela_depois_do_envio_nao_e_reenviado(harness: Harness, tmp_path: Path,
                                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    app = AppFalso(depois_do_envio="formulario")           # o envio "não pega": a tela volta ao formulário, sem erro declarado
    cen = await _cenario(harness, tmp_path, monkeypatch, app=app)
    _iniciar(cen)
    await cen.rodar()
    assert app.envios == 1 and cen.info().proximo_passo == "aguardando_pessoa:tela_desconhecida"


async def test_parada_depois_do_envio_registra_o_envio_e_retomar_nao_reenvia(harness: Harness, tmp_path: Path,
                                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    """CAPTCHA depois do toque em "Cadastrar": o formulário JÁ foi enviado, então a conta passa a `aguardando_verificacao` antes
    de parar. Retomar + rodar de novo cai na mesma tela e para outra vez, sem tocar no formulário."""
    app = AppFalso(depois_do_envio="captcha")
    cen = await _cenario(harness, tmp_path, monkeypatch, app=app)
    _iniciar(cen)
    await cen.rodar()
    assert cen.info().resume_state == "aguardando_verificacao" and app.envios == 1
    async with _api(harness) as c:
        r = await c.post(f"/api/instagram/profiles/{cen.pid}/accounts/{cen.aid}/provisioning",
                         json={"evento": "retomar", "estado_esperado": "falha"})
        assert r.status_code == 200, r.text
    assert cen.estado() == "aguardando_verificacao"
    _iniciar(cen)
    await cen.rodar()
    assert app.envios == 1 and app.toques.count("cadastrar") == 1
    assert cen.info().proximo_passo == "aguardando_pessoa:captcha"


async def test_o_usuario_indisponivel_volta_ao_cadastro_e_pode_tentar_outro(harness: Harness, tmp_path: Path,
                                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    app = AppFalso(depois_do_envio="usuario_indisponivel")
    cen = await _cenario(harness, tmp_path, monkeypatch, app=app)
    _iniciar(cen)
    await cen.rodar()
    assert cen.estado() == "falha" and cen.info().resume_state == "aguardando_cadastro_externo"


async def test_desafio_que_surge_entre_o_preenchimento_e_o_toque_nao_e_tocado(harness: Harness, tmp_path: Path,
                                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    app = AppFalso(humano_apos="password")
    cen = await _cenario(harness, tmp_path, monkeypatch, app=app)
    _iniciar(cen)
    erro = await cen.rodar()
    assert erro is not None and str(erro) == "cadastro guiado parado: desafio"
    assert app.envios == 0 and "cadastrar" not in app.toques
    assert cen.info().resume_state == "aguardando_cadastro_externo"


async def test_desafio_entre_dois_campos_para_antes_de_digitar_a_senha(harness: Harness, tmp_path: Path,
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    app = AppFalso(humano_apos="fullname")
    cen = await _cenario(harness, tmp_path, monkeypatch, app=app)
    _iniciar(cen)
    erro = await cen.rodar()
    assert erro is not None and str(erro) == "cadastro guiado parado: desafio"
    assert app.sensiveis == [] and app.envios == 0 and not app.valores["email"]


async def test_a_senha_nunca_vai_a_campo_de_outro_app_que_passou_para_a_frente(harness: Harness, tmp_path: Path,
                                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    app = AppFalso(outro_app_apos="fullname")
    cen = await _cenario(harness, tmp_path, monkeypatch, app=app)
    _iniciar(cen)
    erro = await cen.rodar()
    assert erro is not None and str(erro) == "cadastro guiado parado: app_fora_do_ar"
    assert app.sensiveis == [] and app.envios == 0


async def test_tarefa_cancelada_logo_depois_do_toque_em_enviar_nao_reabre_o_formulario(
        harness: Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """O toque chegou ao app e a tarefa morreu (cancelamento não é `Exception`) antes de qualquer gravação POSTERIOR: o envio
    já estava gravado ANTES do toque, então a conta está em `aguardando_verificacao` e uma nova execução continua pela tela."""
    app = AppFalso(cancela_apos_envio=True)
    cen = await _cenario(harness, tmp_path, monkeypatch, app=app)
    cen.caixa.chegou(CODIGO, ha_s=-1)
    _iniciar(cen)
    with pytest.raises(asyncio.CancelledError):
        await cen.rodar()
    assert app.envios == 1 and cen.estado() == "aguardando_verificacao"
    app.cancela_apos_envio = False
    _iniciar(cen)
    await cen.rodar()
    assert cen.estado() == "confirmada" and app.envios == 1 and app.toques.count("cadastrar") == 1


async def test_se_o_envio_nao_puder_ser_gravado_o_botao_nao_e_tocado(harness: Harness, tmp_path: Path,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    cen = await _cenario(harness, tmp_path, monkeypatch)
    _iniciar(cen)

    def quebra(self: CicloNoBanco, passo: Passo) -> None:
        raise ValueError("banco fora")

    monkeypatch.setattr(CicloNoBanco, "enviado", quebra)
    erro = await cen.rodar()
    assert erro is not None and str(erro) == "cadastro guiado parado: falha_interna"
    assert cen.app.envios == 0 and "cadastrar" not in cen.app.toques


async def test_usuario_indisponivel_retomado_pode_enviar_de_novo_com_outro_arroba(
        harness: Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """O provedor recusou o @: nada foi criado, então esta é a única parada em que retomar volta ao formulário."""
    app = AppFalso(depois_do_envio="usuario_indisponivel")
    cen = await _cenario(harness, tmp_path, monkeypatch, app=app)
    _iniciar(cen)
    await cen.rodar()
    assert cen.info().resume_state == "aguardando_cadastro_externo"
    async with _api(harness) as c:
        r = await c.post(f"/api/instagram/profiles/{cen.pid}/accounts/{cen.aid}/provisioning",
                         json={"evento": "retomar", "estado_esperado": "falha"})
        assert r.status_code == 200, r.text
    assert cen.estado() == "aguardando_cadastro_externo"


async def test_erro_inesperado_vira_falha_interna_sem_a_mensagem_do_erro(harness: Harness, tmp_path: Path,
                                                                        monkeypatch: pytest.MonkeyPatch,
                                                                        caplog: pytest.LogCaptureFixture) -> None:
    cen = await _cenario(harness, tmp_path, monkeypatch)
    suspeito = f"cofre-ref-secreta-{cen.endereco}"

    async def quebra(account_id: str, desde: datetime, *, espera_s: float) -> str | None:
        raise ValueError(suspeito)                          # um erro nosso que traria referência de segredo e endereço

    monkeypatch.setattr(cen.caixa, "codigo_depois_de", quebra)
    _iniciar(cen)
    with caplog.at_level("DEBUG"):
        erro = await cen.rodar()
    assert erro is not None and str(erro) == "cadastro guiado parado: falha_interna"
    assert cen.info().proximo_passo == "aguardando_pessoa:falha_interna" and cen.app.envios == 1
    assert cen.estado() == "falha" and cen.info().resume_state == "aguardando_verificacao"
    assert suspeito not in caplog.text and "cofre-ref-secreta" not in caplog.text
    corpo = json.dumps([dict(r) for r in harness.state.db.query("SELECT kind, data FROM events")]) + json.dumps(
        [dict(r) for r in harness.state.db.query("SELECT provisioning_detail FROM profile_accounts WHERE id=?", (cen.aid,))])
    assert "cofre-ref-secreta" not in corpo and cen.endereco not in corpo


async def test_o_app_fora_do_ar_para_sem_tocar(harness: Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = AppFalso(fora_do_ar=True)
    cen = await _cenario(harness, tmp_path, monkeypatch, app=app)
    _iniciar(cen)
    await cen.rodar()
    assert app.toques == [] and cen.info().proximo_passo == "aguardando_pessoa:app_fora_do_ar"


# ------------------------------------------------------------------------------------------------ retomar e reiniciar
async def test_retomar_depois_do_envio_continua_pela_tela_e_nao_reenvia(harness: Harness, tmp_path: Path,
                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    cen = await _cenario(harness, tmp_path, monkeypatch)
    _iniciar(cen)
    await cen.rodar()                                       # o e-mail não chegou: para na tela do código
    assert cen.info().proximo_passo == "aguardando_pessoa:codigo_nao_chegou" and cen.app.tela == "codigo"
    async with _api(harness) as c:
        r = await c.post(f"/api/instagram/profiles/{cen.pid}/accounts/{cen.aid}/provisioning",
                         json={"evento": "retomar", "estado_esperado": "falha"})
        assert r.status_code == 200, r.text
    assert cen.estado() == "aguardando_verificacao"
    cen.caixa.chegou(CODIGO, ha_s=-1)
    _iniciar(cen)
    await cen.rodar()
    assert cen.estado() == "confirmada" and cen.app.envios == 1 and cen.app.toques.count("cadastrar") == 1


async def test_a_conta_que_ja_enviou_e_foi_reiniciada_registra_o_envio_pela_tela(
        harness: Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """O processo caiu entre o toque em enviar e a gravação do envio: o estado ficou `aguardando_cadastro_externo` e o app já
    está na tela do código. O motor vê a tela, registra `enviado` e segue — sem tocar no formulário de novo."""
    app = AppFalso(tela="codigo")
    cen = await _cenario(harness, tmp_path, monkeypatch, app=app)
    async with _api(harness) as c:
        r = await c.post(f"/api/instagram/profiles/{cen.pid}/accounts/{cen.aid}/provisioning",
                         json={"evento": "iniciar_cadastro", "estado_esperado": "credencial_preparada"})
        assert r.status_code == 200, r.text
    app.valores["username"] = cen.desejado
    cen.caixa.chegou(CODIGO, ha_s=-1)
    _iniciar(cen)
    await cen.rodar()
    assert cen.estado() == "confirmada" and app.envios == 0 and "cadastrar" not in app.toques


async def test_codigo_velho_na_caixa_nunca_serve(harness: Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cen = await _cenario(harness, tmp_path, monkeypatch)
    cen.caixa.chegou("111111", ha_s=3600)                   # de uma tentativa de uma hora atrás
    _iniciar(cen)
    await cen.rodar()
    assert cen.info().proximo_passo == "aguardando_pessoa:codigo_nao_chegou"
    assert ("code", "111111") not in cen.app.sensiveis and cen.app.sensiveis[-1][0] == "password"


# ------------------------------------------------------------------------------------------------ a rota e as recusas
async def test_a_rota_responde_202_e_as_recusas_trazem_codigo(harness: Harness, tmp_path: Path,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    cen = await _cenario(harness, tmp_path, monkeypatch)
    cen.caixa.chegou(CODIGO, ha_s=-1)
    async with _api(harness) as c:
        r = await c.post(f"/api/instagram/profiles/{cen.pid}/accounts/{cen.aid}/provisioning/signup", json={})
        assert r.status_code == 202, r.text
        corpo = r.json()
        assert corpo["accepted"] and corpo["account_id"] == cen.aid
        assert _senha_do_cofre(harness, cen.aid) not in r.text
        sem_corpo = await c.post(f"/api/instagram/profiles/{cen.pid}/accounts/{cen.aid}/provisioning/signup")
        assert sem_corpo.status_code == 202                  # o corpo é opcional
        inexistente = await c.post(f"/api/instagram/profiles/{cen.pid}/accounts/nao-existe/provisioning/signup")
        assert inexistente.status_code == 404
        extra = await c.post(f"/api/instagram/profiles/{cen.pid}/accounts/{cen.aid}/provisioning/signup",
                             json={"qualquer": 1})
        assert extra.status_code == 422                      # o corpo não aceita campo solto


def _recusa(cen: Cenario) -> SocialError:
    with pytest.raises(SocialError) as e:
        _iniciar(cen)
    return e.value


async def test_planejada_sem_credencial_nao_inicia(harness: Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cen = await _cenario(harness, tmp_path, monkeypatch, preparar=False)
    erro = _recusa(cen)
    assert (erro.code, erro.status) == ("estado_inesperado", 409) and erro.details == {"estado_atual": "planejada"}
    assert cen.despachos == []


async def test_falha_pede_retomar_antes(harness: Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cen = await _cenario(harness, tmp_path, monkeypatch, app=AppFalso(depois_do_envio="captcha"))
    _iniciar(cen)
    await cen.rodar()
    erro = _recusa(cen)
    assert erro.code == "estado_inesperado" and erro.details == {"estado_atual": "falha"}


async def test_sem_cadastro_yaml_sem_caixa_e_sem_usuario_desejado(harness: Harness, tmp_path: Path,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    sem_yaml = await _cenario(harness, tmp_path / "a", monkeypatch, yaml=None)
    assert _recusa(sem_yaml).code == "sem_conhecimento_de_cadastro"
    sem_caixa = await _cenario(harness, tmp_path / "b", monkeypatch, com_caixa=False)
    assert _recusa(sem_caixa).code == "sem_caixa_de_email"
    sem_usuario = await _cenario(harness, tmp_path / "c", monkeypatch, sem_usuario=True)
    assert _recusa(sem_usuario).code == "sem_usuario_desejado"


async def test_uma_conta_por_vez_no_parque_e_aparelho_ocupado(harness: Harness, tmp_path: Path,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    cen = await _cenario(harness, tmp_path, monkeypatch)
    monkeypatch.setattr(harness.state.commands, "open_commands", lambda **kw: [{"verb": VERBO}])
    assert _recusa(cen).code == "cadastro_em_andamento"
    monkeypatch.undo()
    cen2 = await _cenario(harness, tmp_path / "b", monkeypatch)
    monkeypatch.setattr(cadastro_guiado, "pedir_trabalho_de_app",
                        lambda *a, **kw: {"accepted": False, "reason": "aparelho_ocupado"})
    erro = _recusa(cen2)
    assert (erro.code, erro.status) == ("aparelho_ocupado", 409)


async def test_conta_sem_senha_consentida_e_recusada(harness: Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cen = await _cenario(harness, tmp_path, monkeypatch)
    harness.state.db.execute("UPDATE account_credentials SET consent_at=NULL WHERE account_id=?", (cen.aid,))
    assert _recusa(cen).code == "sem_consentimento"


# ------------------------------------------------------------------------------------------------ o motor sem o banco
async def test_o_motor_so_digita_a_senha_em_campo_que_o_android_diz_ser_de_senha(
        harness: Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class TelaSemSenha(AppFalso):
        def _elementos(self) -> list[tuple[str, str, str, str, bool, bool]]:
            base = super()._elementos()
            return [(c, rid, t, n, False if rid == "password" else s, cl) for c, rid, t, n, s, cl in base]

    app = TelaSemSenha()
    cen = await _cenario(harness, tmp_path, monkeypatch, app=app)
    _iniciar(cen)
    await cen.rodar()
    assert app.sensiveis == [] and cen.info().proximo_passo == "aguardando_pessoa:tela_desconhecida"
    assert _senha_do_cofre(harness, cen.aid) not in app.digitados


# ------------------------------------------------------------------------------------------------ o cadastro.yaml
def _yaml(**mudancas: object) -> dict[str, Any]:
    import yaml

    base = yaml.safe_load(YAML)
    base.update(mudancas)
    return base


def test_o_yaml_de_teste_carrega_e_declara_o_que_usa() -> None:
    k = de_dados(_yaml())
    assert k.app == PACOTE and k.pede_codigo and k.dados_usados == {"usuario", "nome", "email", "senha"}
    assert [t.nome for t in k.telas][:3] == ["usuario_indisponivel", "captcha", "telefone"]
    arvore = AppFalso(tela="captcha").arvore()
    assert k.reconhecer(arvore).nome == "captcha"            # type: ignore[union-attr]
    assert k.reconhecer(AppFalso(tela="estranha").arvore()) is None


@pytest.mark.parametrize("mudanca, trecho", [
    ({"app": ""}, "pacote é obrigatório"),
    ({"extra": 1}, "chave desconhecida"),
    ({"passos_max": 1}, "inteiro entre"),
    ({"telas": []}, "ao menos uma"),
    ({"envio_espera_s": -1}, "número entre"),
])
def test_yaml_invalido_no_topo(mudanca: dict[str, object], trecho: str) -> None:
    with pytest.raises(CadastroInvalido, match=trecho):
        de_dados(_yaml(**mudanca))


def _sem_tela(nome: str) -> list[dict[str, Any]]:
    return [t for t in _yaml()["telas"] if t["nome"] != nome]


def _com(nome: str, **mudancas: object) -> list[dict[str, Any]]:
    return [{**t, **mudancas} if t["nome"] == nome else t for t in _yaml()["telas"]]


@pytest.mark.parametrize("telas, trecho", [
    (_sem_tela("sucesso"), "UMA tela de `sucesso`"),
    (_sem_tela("formulario"), "UM formulário com `envia: true`"),
    (_com("formulario", envia=False), "UM formulário com `envia: true`"),
    (_com("sucesso", sinais=[]), "ao menos um"),
    (_com("sucesso", sinais=["("]), "expressão regular inválida"),
    (_com("captcha", motivo="codigo_nao_chegou"), "motivo"),
    (_com("inicio", motivo="captcha"), "`motivo` só vale em `parar`"),
    (_com("sucesso", envia=True), "`envia` só vale em `preencher` ou `tocar`"),
    (_com("inicio", envia=True), "exatamente UM formulário"),     # 31.324: o envio pode ser um toque, mas continua sendo UM só
    (_com("inicio", botao=None), "precisa de"),
    (_com("formulario", campos=[{"dado": "senha", "id": ":id/p"}]), "segredo: true"),
    (_com("formulario", campos=[{"dado": "usuario", "id": ":id/u", "segredo": True}]), "só `senha` é segredo"),
    (_com("formulario", campos=[{"dado": "cpf", "id": ":id/u"}]), "não está em"),
    (_com("formulario", campos=[{"dado": "nome", "id": ":id/u"}]), "precisa preencher o `usuario`"),
    (_com("formulario", campos=[{"dado": "usuario"}]), "diga `id`, `texto`, `classe`"),
    (_com("formulario", acao="fazer"), "acao"),
])
def test_yaml_invalido_nas_telas(telas: list[dict[str, Any]], trecho: str) -> None:
    with pytest.raises(CadastroInvalido, match=re.escape(trecho)):
        de_dados(_yaml(telas=telas))


def test_nomes_repetidos_e_dois_codigos_sao_recusados() -> None:
    telas = _yaml()["telas"]
    with pytest.raises(CadastroInvalido, match="nomes repetidos"):
        de_dados(_yaml(telas=[*telas, telas[0]]))
    outra = {**next(t for t in telas if t["nome"] == "codigo"), "nome": "codigo2"}
    with pytest.raises(CadastroInvalido, match="no máximo uma tela de `codigo`"):
        de_dados(_yaml(telas=[*telas, outra]))


def test_pasta_com_o_pacote_de_outro_app_e_recusada(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pasta = tmp_path / "apps" / "com.outro.app"
    pasta.mkdir(parents=True)
    (pasta / "cadastro.yaml").write_text(YAML, encoding="utf-8", newline="\n")
    monkeypatch.setattr(cadastro_conhecimento, "PASTA_DOS_APPS", tmp_path / "apps")
    cadastro_conhecimento.do_app.cache_clear()
    with pytest.raises(CadastroInvalido, match="traz o cadastro de"):
        cadastro_conhecimento.do_app("com.outro.app")
    assert cadastro_conhecimento.do_app("com.sem.arquivo") is None


def test_nenhum_app_real_declara_cadastro_ainda() -> None:
    """D1 e D2 estão com o dono: o motor existe, e nenhum `cadastro.yaml` de app real foi escrito."""
    reais = list(cadastro_conhecimento.PASTA_DOS_APPS.glob("*/cadastro.yaml"))
    assert reais == [], reais


def test_a_senha_da_parada_nunca_carrega_texto() -> None:
    from app.modules.identity.domain.cadastro import detalhe_da_parada, proximo_passo

    for p in Parada:
        assert proximo_passo("falha", detalhe_da_parada(p)) == f"aguardando_pessoa:{p.value}"
    assert proximo_passo("falha", "qualquer coisa") is None and proximo_passo("confirmada", "aguardando_pessoa:captcha") is None
    assert proximo_passo("falha", None) is None


async def test_o_motor_nao_depende_do_aparelho_de_verdade(harness: Harness) -> None:
    assert MotorDeCadastro.__init__.__annotations__["mesa"] == "Mesa"
    _ = pysecrets
