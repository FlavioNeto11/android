"""31.324 (G1): o cadastro guiado para o app de conteúdo do servidor (Bloks), onde o campo NÃO tem id, texto nem descrição.

O Instagram é assim (captura offline da Ponte, 10/10): só três containers têm resource-id; o rótulo é um `View` com `text`, o campo é um
`EditText` sem nada, logo ABAIXO do rótulo (e com os bounds sobrepostos a ele); o botão é um `Button` com `desc` mais um `View` filho que
repete o texto; o campo de senha é o único com `password=true`. Aqui o app é FALSO, mas com essa forma, e o `cadastro.yaml` é o de um
app de teste (numa árvore declarada à mão): nenhum app real declara o cadastro ainda (a tela do Instagram espera a captura da igfarm).

O que as provas deste arquivo seguram:
- os critérios novos do `Alvo` (`classe`, `abaixo_do_rotulo`, `ordem`, `senha`) e o que o carregador recusa;
- a data de nascimento da persona digitada nas rodas (ordem de leitura, com zero à esquerda) e conferida na tela;
- o código por e-mail que o app manda ANTES do envio final (`dispara_codigo` / `antes_do_envio`): o piso do `desde` é o toque que o pediu,
  um e-mail mais velho não serve, e esse código NÃO conta como envio da conta;
- o envio como um TOQUE (`envia` em `tocar`): marcado antes do toque e uma vez só;
- a retomada, as paradas e a senha só em campo de senha;
- o serviço: sem data de nascimento ou menor de idade, 409 antes de tocar no aparelho.

Prova `simulated`. Nenhuma conta, nenhum aparelho, nenhum e-mail de verdade.
"""
from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from xml.sax.saxutils import quoteattr

import pytest
import yaml

from app.automation.hierarchy import UiElement, UiTree, parse_hierarchy
from app.integrations.app_declarado import cadastro_conhecimento
from app.integrations.app_declarado.cadastro import Dados, Desfecho, FalhaNaMesa, MotorDeCadastro
from app.integrations.app_declarado.cadastro_conhecimento import (Alvo, CadastroInvalido, ConhecimentoDeCadastro,
                                                                  de_dados)
from app.modules.identity.domain.cadastro import Parada
from app.modules.identity.domain.provisionamento import Estado
from app.modules.identity.infrastructure import cadastro_guiado
from app.social.erros import SocialError

from .conftest import Harness
from .test_cadastro_guiado import PACOTE as PACOTE_DO_SERVICO
from .test_cadastro_guiado import YAML as YAML_DO_SERVICO
from .test_cadastro_guiado import _cenario, _iniciar

PACOTE = "com.exemplo.bloks"
CODIGO = "731904"
SENHA = "Segredo!Fake-1"
T0 = datetime(2026, 10, 10, 12, 0, 0, tzinfo=UTC)
NASCIMENTO = date(1994, 3, 7)                       # dia 7 (um dígito) e mês 3: o zero à esquerda não pode atrapalhar

YAML_BLOKS = """
app: com.exemplo.bloks
rotulo: Exemplo Bloks
passos_max: 40
espera_s: 0
envio_espera_s: 0
codigo_espera_s: 5
telas:
  - {nome: captcha, sinais: ["prove you are not a robot"], acao: parar, motivo: captcha}
  - {nome: entrada, sinais: ["mobile number or email", "create new account"], acao: tocar, botao: {texto: "create new account"}}
  - nome: email
    sinais: ["enter your email address"]
    acao: preencher
    campos:
      - {dado: email, classe: EditText, abaixo_do_rotulo: "email"}
    botao: {texto: "next"}
    dispara_codigo: true
  - nome: codigo
    sinais: ["enter the confirmation code"]
    acao: codigo
    campo: {classe: EditText, abaixo_do_rotulo: "confirmation code"}
    botao: {texto: "next"}
    antes_do_envio: true
  - nome: nascimento
    sinais: ["add your birthday"]
    acao: preencher
    campos:
      - {dado: nascimento_mes, classe: EditText, ordem: 1}
      - {dado: nascimento_dia, classe: EditText, ordem: 2}
      - {dado: nascimento_ano, classe: EditText, ordem: 3}
    botao: {texto: "next"}
  - nome: perfil
    sinais: ["choose your name and username"]
    acao: preencher
    campos:
      - {dado: nome, classe: EditText, abaixo_do_rotulo: "full name"}
      - {dado: usuario, classe: EditText, abaixo_do_rotulo: "username"}
    botao: {texto: "next"}
  - nome: senha
    sinais: ["create a password"]
    acao: preencher
    campos:
      - {dado: senha, classe: EditText, senha: true, segredo: true}
    botao: {texto: "next"}
  - {nome: revisar, sinais: ["review and sign up"], acao: tocar, botao: {texto: "sign up"}, envia: true}
  - {nome: sucesso, sinais: ["welcome to instagram"], acao: sucesso, conta: {abaixo_do_rotulo: "welcome to instagram"}}
"""


def _yaml(**trocas: str) -> str:
    texto = YAML_BLOKS
    for velho, novo in trocas.items():
        assert velho in texto
        texto = texto.replace(velho, novo)
    return texto


# ------------------------------------------------------------------------------------------------ a árvore de um app Bloks
def _xml(cls: str, b: tuple[int, int, int, int], texto: str = "", desc: str = "", *, clicavel: bool = False,
         senha: bool = False) -> str:
    extra = (' clickable="true"' if clicavel else "") + (' password="true"' if senha else "")
    return (f'<node class="{cls}" package="{PACOTE}" resource-id="" text={quoteattr(texto)} content-desc={quoteattr(desc)} '
            f'enabled="true"{extra} bounds="[{b[0]},{b[1]}][{b[2]},{b[3]}]"/>')


@dataclass
class Nos:
    """Os nós de uma tela (em XML) e, para cada um que reage a toque, o nome do que ele é: `campo:<chave>` ou `botao:<ação>`."""

    xml: list[str] = field(default_factory=list)
    alvos: list[tuple[tuple[int, int, int, int], str]] = field(default_factory=list)

    def texto(self, t: str, y: int) -> None:
        self.xml.append(_xml("android.view.View", (64, y, 656, y + 37), t))

    def rotulo(self, t: str, y: int) -> None:
        self.xml.append(_xml("android.view.View", (64, y, 395, y + 37), t))

    def campo(self, chave: str, y: int, *, senha: bool = False, x: tuple[int, int] = (64, 656), alto: int = 39, topo: int = 18) -> None:
        b = (x[0], y + topo, x[1], y + topo + alto)             # começa 18 px abaixo do topo do rótulo: os bounds se sobrepõem
        self.xml.append(_xml("android.widget.EditText", b, senha=senha, clicavel=True))
        self.alvos.append((b, f"campo:{chave}"))

    def botao(self, t: str, y: int, acao: str) -> None:
        b = (32, y, 688, y + 88)
        self.xml.append(_xml("android.widget.Button", b, desc=t, clicavel=True))
        self.xml.append(_xml("android.view.View", b, t))        # o filho que repete o texto e não é clicável
        self.alvos.append((b, f"botao:{acao}"))


@dataclass
class AppBloks:
    """O fluxo: entrada → e-mail → código → nascimento → nome e @ → senha → revisar (Sign up) → sucesso."""

    tela: str = "entrada"
    valores: dict[str, str] = field(default_factory=dict)
    foco: str | None = None
    eventos: list[str] = field(default_factory=list)          # a trilha única da mesa e do ciclo, na ordem
    digitados: list[tuple[str, str]] = field(default_factory=list)
    sensiveis: list[tuple[str, str]] = field(default_factory=list)
    toques: list[str] = field(default_factory=list)
    envios: int = 0
    senha_em_campo_comum: bool = False                         # a tela da senha traz um campo que o Android não diz ser de senha
    ficam: set[str] = field(default_factory=set)              # ações que NÃO avançam (o app ignora o toque)
    ignoram: set[str] = field(default_factory=set)            # campos que ignoram o que se digita
    zero_a_esquerda: bool = False                             # as rodas mostram "07"
    handle_final: str | None = None
    fora_do_ar: bool = False

    def nos(self) -> Nos:
        n, t = Nos(), self.tela
        if t == "entrada":
            n.rotulo("Mobile number or email", 516)
            n.campo("login", 516)
            n.botao("Create new account", 1044, "criar")
        elif t == "email":
            n.texto("Enter your email address", 200)
            n.rotulo("Email", 300)
            n.campo("email", 300)
            n.botao("Next", 1000, "email")
        elif t == "codigo":
            n.texto("Enter the confirmation code", 200)
            n.rotulo("Confirmation code", 300)
            n.campo("codigo", 300)
            n.botao("Next", 1000, "codigo")
        elif t == "nascimento":
            n.texto("Add your birthday", 200)
            n.campo("mes", 380, x=(64, 220), topo=0)           # mês, dia e ano na MESMA linha, nesta ordem (o app é dos EUA)
            n.campo("dia", 380, x=(250, 400), topo=0)
            n.campo("ano", 380, x=(430, 600), topo=0)
            n.botao("Next", 1000, "nascimento")
        elif t == "perfil":
            n.texto("Choose your name and username", 200)
            n.rotulo("Full name", 300)
            n.campo("nome", 300)
            n.rotulo("Username", 420)
            n.campo("usuario", 420)
            n.botao("Next", 1000, "perfil")
        elif t == "senha":
            n.texto("Create a password", 200)
            n.rotulo("Password", 300)
            n.campo("senha", 300, senha=not self.senha_em_campo_comum)
            n.botao("Next", 1000, "senha")
        elif t == "revisar":
            n.texto("Review and sign up", 200)
            n.botao("Sign up", 1000, "signup")
        elif t == "sucesso":
            n.texto("Welcome to Instagram", 200)
            n.texto("@" + (self.handle_final or self.valores.get("usuario", "")), 260)
        elif t == "captcha":
            n.texto("Prove you are not a robot", 200)
        return n

    def mostrado(self, chave: str) -> str:
        v = self.valores.get(chave, "")
        if chave in ("senha", "codigo"):
            return "•" * len(v)
        if self.zero_a_esquerda and chave in ("mes", "dia") and v:
            return v.zfill(2)
        return v

    def arvore(self) -> UiTree:
        n = self.nos()
        xml = list(n.xml)
        # o texto de cada campo entra no XML depois de montado (o campo é lido pelo que mostra)
        for i, (b, nome) in enumerate(n.alvos):
            if nome.startswith("campo:"):
                chave = nome.split(":")[1]
                alvo = f'bounds="[{b[0]},{b[1]}][{b[2]},{b[3]}]"'
                for j, x in enumerate(xml):
                    if alvo in x and "EditText" in x:
                        xml[j] = x.replace('text=""', f"text={quoteattr(self.mostrado(chave))}", 1)
        return parse_hierarchy("<hierarchy>" + "".join(xml) + "</hierarchy>")

    def tocar(self, x: int, y: int) -> None:
        for b, nome in self.nos().alvos:
            if b[0] <= x < b[2] and b[1] <= y < b[3]:
                tipo, _, valor = nome.partition(":")
                if tipo == "campo":
                    self.foco = valor
                    return
                self.toques.append(valor)
                self.eventos.append(f"tap:{valor}")
                self.agir(valor)
                return

    def agir(self, acao: str) -> None:
        if acao in self.ficam:
            return
        if acao == "criar":
            self.tela = "email"
        elif acao == "email":
            self.tela = "codigo"
        elif acao == "codigo":
            self.tela = "nascimento" if self.valores.get("codigo") == CODIGO else "codigo"
        elif acao == "nascimento":
            self.tela = "perfil"
        elif acao == "perfil":
            self.tela = "senha"
        elif acao == "senha":
            self.tela = "revisar"
        elif acao == "signup":
            self.envios += 1
            self.tela = "sucesso"

    def chave_do(self, e: UiElement) -> str | None:
        for b, nome in self.nos().alvos:
            if nome.startswith("campo:") and tuple(e.bounds) == b:
                return nome.split(":")[1]
        return None


class MesaBloks:
    def __init__(self, app: AppBloks) -> None:
        self.app = app

    async def observar(self) -> tuple[UiTree, str | None]:
        if self.app.fora_do_ar:
            raise FalhaNaMesa("DriverTimeout")
        return self.app.arvore(), PACOTE

    async def tocar(self, x: int, y: int) -> None:
        self.app.tocar(x, y)

    async def digitar(self, texto: str) -> None:
        a = self.app
        assert a.foco is not None
        a.digitados.append((a.foco, texto))
        if a.foco not in a.ignoram:
            a.valores[a.foco] = texto

    async def digitar_sensivel(self, localizar: Any, segredo: Any) -> None:
        campo = localizar(self.app.arvore())
        if campo is None:
            raise FalhaNaMesa("SensitiveInputError")
        chave = self.app.chave_do(campo)
        assert chave is not None
        valor = segredo()
        self.app.valores[chave] = valor
        self.app.sensiveis.append((chave, valor))

    async def esperar(self, segundos: float) -> None:
        return None


class CicloFalso:
    """As transições da conta, na mesma trilha do app (para provar a ORDEM: o envio é marcado antes do toque)."""

    def __init__(self, app: AppBloks, estado: Estado = Estado.CREDENCIAL_PREPARADA, desde: datetime | None = None) -> None:
        self.app, self._estado, self._desde = app, estado, desde or T0 - timedelta(minutes=10)
        self.confirmado: str | None = None

    def estado(self) -> Estado:
        return self._estado

    def desde_do_envio(self) -> datetime | None:
        return self._desde

    def iniciar(self) -> None:
        self.app.eventos.append("iniciar")
        self._estado = Estado.AGUARDANDO_CADASTRO_EXTERNO

    def enviado(self, passo: Any) -> None:
        if self._estado is not Estado.AGUARDANDO_CADASTRO_EXTERNO:
            raise RuntimeError(f"enviado em {self._estado.value}")
        self.app.eventos.append("enviado")
        self._estado = Estado.AGUARDANDO_VERIFICACAO

    def parar(self, parada: Parada, passo: Any) -> None:
        self.app.eventos.append(f"parou:{parada.value}")

    def confirmar(self, handle: str) -> bool:
        self.confirmado = handle
        self._estado = Estado.CONFIRMADA
        return True


class CaixaFalsa:
    """Só entrega o e-mail recebido NO MOMENTO do `desde` pedido ou depois (sem folga: o teste diz exatamente quando chegou)."""

    def __init__(self, *mensagens: tuple[datetime, str]) -> None:
        self.mensagens = list(mensagens)
        self.pedidos: list[datetime] = []

    async def __call__(self, desde: datetime, espera_s: float) -> str | None:
        self.pedidos.append(desde)
        validas = [c for quando, c in self.mensagens if quando >= desde]
        return validas[-1] if validas else None


def _motor(app: AppBloks, *, estado: Estado = Estado.CREDENCIAL_PREPARADA, caixa: CaixaFalsa | None = None,
           nascimento: date | None = NASCIMENTO, yaml_texto: str = YAML_BLOKS, desde: datetime | None = None
           ) -> tuple[MotorDeCadastro, CicloFalso, CaixaFalsa]:
    k = de_dados(yaml.safe_load(yaml_texto))
    caixa = caixa or CaixaFalsa((T0 + timedelta(seconds=0.5), CODIGO))
    ciclo = CicloFalso(app, estado, desde)
    contador = itertools.count()
    dados = Dados(usuario="maria.teste", nome="Maria Souza", primeiro_nome="Maria", sobrenome="Souza",
                  email="maria@parque.exemplo.test", senha=lambda: SENHA, codigo=caixa, nascimento=nascimento)
    motor = MotorDeCadastro(k, MesaBloks(app), ciclo, dados, relogio=lambda: T0 + timedelta(seconds=next(contador)))
    return motor, ciclo, caixa


# ------------------------------------------------------------------------------------------------ o Alvo, em árvores da captura
def _login() -> UiTree:
    """A tela de login deslogada da captura (rótulo [64,516,395,553] sobre o EditText [64,534,656,573]; "Create new account"
    [32,1044,688,1132]). O rótulo e o campo da senha são extensão minha da mesma geometria."""
    nos = [_xml("android.view.View", (64, 516, 395, 553), "Mobile number or email"),
           _xml("android.widget.EditText", (64, 534, 656, 573), clicavel=True),
           _xml("android.view.View", (64, 600, 300, 637), "Password"),
           _xml("android.widget.EditText", (64, 618, 656, 657), senha=True, clicavel=True),
           _xml("android.widget.Button", (32, 1044, 688, 1132), desc="Create new account", clicavel=True),
           _xml("android.view.View", (32, 1044, 688, 1132), "Create new account")]
    return parse_hierarchy("<hierarchy>" + "".join(nos) + "</hierarchy>")


def _alvo(**kw: Any) -> Alvo:
    return de_dados_alvo(kw)


def de_dados_alvo(m: dict[str, Any]) -> Alvo:
    return cadastro_conhecimento._alvo(m, "teste")


def test_o_campo_sem_id_nem_texto_se_acha_pelo_rotulo_logo_acima() -> None:
    arvore = _login()
    campo = _alvo(classe="EditText", abaixo_do_rotulo="mobile number or email").unico(arvore, editavel=True)
    assert campo is not None and campo.bounds == (64, 534, 656, 573) and not campo.password
    # o campo de senha é o do rótulo "Password", e `senha: true` o acha mesmo sem rótulo
    senha = _alvo(classe="EditText", abaixo_do_rotulo="password").unico(arvore, editavel=True)
    assert senha is not None and senha.password
    so_senha = _alvo(senha=True).unico(arvore, editavel=True)
    assert so_senha is not None and so_senha.bounds == senha.bounds


def test_sem_criterio_de_rotulo_dois_campos_sao_ambiguos_e_a_ordem_desfaz() -> None:
    arvore = _login()
    assert _alvo(classe="EditText").unico(arvore, editavel=True) is None
    primeiro = _alvo(classe="EditText", ordem=1).unico(arvore, editavel=True)
    segundo = _alvo(classe="EditText", ordem=2).unico(arvore, editavel=True)
    assert primeiro is not None and primeiro.bounds[1] == 534 and segundo is not None and segundo.password
    assert _alvo(classe="EditText", ordem=3).unico(arvore, editavel=True) is None


def test_o_rotulo_ausente_ou_repetido_ou_sem_campo_abaixo_nao_acha_nada() -> None:
    arvore = _login()
    assert _alvo(classe="EditText", abaixo_do_rotulo="outra coisa").unico(arvore, editavel=True) is None
    duplo = parse_hierarchy("<hierarchy>" + _xml("android.view.View", (64, 100, 300, 137), "Email")
                            + _xml("android.view.View", (64, 300, 300, 337), "Email")
                            + _xml("android.widget.EditText", (64, 318, 656, 357), clicavel=True) + "</hierarchy>")
    assert _alvo(classe="EditText", abaixo_do_rotulo="email").unico(duplo, editavel=True) is None        # rótulo repetido
    sem_campo_abaixo = parse_hierarchy("<hierarchy>" + _xml("android.widget.EditText", (64, 100, 656, 139), clicavel=True)
                                       + _xml("android.view.View", (64, 300, 300, 337), "Email") + "</hierarchy>")
    assert _alvo(classe="EditText", abaixo_do_rotulo="email").unico(sem_campo_abaixo, editavel=True) is None


def test_o_campo_do_outro_lado_da_tela_nao_e_o_do_rotulo() -> None:
    """Duas colunas: o campo da direita fica na mesma altura, mas não se sobrepõe ao rótulo da esquerda na horizontal."""
    arvore = parse_hierarchy("<hierarchy>" + _xml("android.view.View", (20, 300, 300, 337), "Email")
                             + _xml("android.widget.EditText", (400, 318, 700, 357), clicavel=True)
                             + _xml("android.widget.EditText", (20, 400, 300, 439), clicavel=True) + "</hierarchy>")
    campo = _alvo(classe="EditText", abaixo_do_rotulo="email").unico(arvore, editavel=True)
    assert campo is not None and campo.bounds == (20, 400, 300, 439)


def test_a_ordem_de_leitura_e_de_cima_para_baixo_e_da_esquerda_para_a_direita() -> None:
    arvore = parse_hierarchy("<hierarchy>"
                             + _xml("android.widget.EditText", (430, 402, 600, 440), clicavel=True)       # linha 1, à direita
                             + _xml("android.widget.EditText", (64, 398, 220, 442), clicavel=True)        # linha 1, à esquerda
                             + _xml("android.widget.EditText", (64, 600, 220, 640), clicavel=True)        # linha 2
                             + "</hierarchy>")
    esquerdas = [_alvo(classe="EditText", ordem=n).unico(arvore, editavel=True) for n in (1, 2, 3)]
    assert [e.bounds[0] for e in esquerdas if e is not None] == [64, 430, 64]
    assert [e.bounds[1] for e in esquerdas if e is not None] == [398, 402, 600]


# ------------------------------------------------------------------------------------------------ o carregador
def test_o_yaml_do_app_bloks_carrega_e_diz_o_que_pede() -> None:
    k = de_dados(yaml.safe_load(YAML_BLOKS))
    assert k.pede_nascimento and k.pede_codigo
    assert {"nascimento_dia", "nascimento_mes", "nascimento_ano", "email", "usuario", "senha"} <= k.dados_usados
    envio = [t for t in k.telas if t.envia]
    assert [t.nome for t in envio] == ["revisar"] and envio[0].acao == "tocar"
    assert [t.nome for t in k.telas if t.dispara_codigo] == ["email"]
    assert [t.nome for t in k.telas if t.antes_do_envio] == ["codigo"]


def _com_tela(nome: str, **mudancas: Any) -> dict[str, Any]:
    d = yaml.safe_load(YAML_BLOKS)
    tela = next(t for t in d["telas"] if t["nome"] == nome)
    tela.update(mudancas)
    return d


def _campo_da(nome_da_tela: str, indice: int, **mudancas: Any) -> dict[str, Any]:
    d = yaml.safe_load(YAML_BLOKS)
    tela = next(t for t in d["telas"] if t["nome"] == nome_da_tela)
    tela["campos"][indice].update(mudancas)
    return d


def _sem_chave(nome: str, chave: str) -> dict[str, Any]:
    d = yaml.safe_load(YAML_BLOKS)
    next(t for t in d["telas"] if t["nome"] == nome).pop(chave)
    return d


@pytest.mark.parametrize("dados, trecho", [
    (_campo_da("nascimento", 0, ordem=0), "ordem"),
    (_campo_da("nascimento", 0, ordem=21), "ordem"),
    (_campo_da("nascimento", 0, ordem="1"), "ordem"),
    (_campo_da("nascimento", 0, ordem=True), "ordem"),
    (_campo_da("nascimento", 0, classe="Edit Text"), "classe"),
    (_campo_da("nascimento", 0, classe=3), "classe"),
    (_campo_da("perfil", 0, abaixo_do_rotulo="("), "expressão regular inválida"),
    (_campo_da("senha", 0, senha="sim"), "senha"),
    (_campo_da("nascimento", 0, ordem=1, classe=None), "diga `id`"),                      # só a ordem casaria com qualquer coisa
    (_campo_da("nascimento", 0, chute=1), "chave"),
    (_com_tela("email", dispara_codigo="sim"), "dispara_codigo"),
    (_com_tela("sucesso", dispara_codigo=True), "`dispara_codigo` só vale em `preencher` ou `tocar`"),
    (_com_tela("revisar", dispara_codigo=True), "não pode `envia` e `dispara_codigo`"),
    (_com_tela("email", antes_do_envio=True), "`antes_do_envio` só vale em `codigo`"),
    (_com_tela("sucesso", envia=True), "`envia` só vale em `preencher` ou `tocar`"),
    (_sem_chave("email", "dispara_codigo"), "andam juntos"),
    (_sem_chave("codigo", "antes_do_envio"), "andam juntos"),
    (_com_tela("perfil", dispara_codigo=True), "no máximo uma tela com `dispara_codigo`"),
    (_com_tela("nascimento", envia=True), "exatamente UM"),
])
def test_o_carregador_recusa_o_que_nao_se_sustenta(dados: dict[str, Any], trecho: str) -> None:
    with pytest.raises(CadastroInvalido, match=re.escape(trecho)):
        de_dados(dados)


def test_sem_nenhuma_tela_que_preencha_o_usuario_o_carregador_recusa() -> None:
    d = _campo_da("perfil", 1, dado="nome")
    with pytest.raises(CadastroInvalido, match="usuario"):
        de_dados(d)


# ------------------------------------------------------------------------------------------------ o motor, de ponta a ponta
async def test_do_login_a_conta_confirmada_sem_nenhum_id_no_app() -> None:
    app = AppBloks()
    motor, ciclo, caixa = _motor(app)
    desfecho = await motor.executar()
    assert desfecho == Desfecho(True, None, desfecho.passo), app.eventos
    assert ciclo.estado() is Estado.CONFIRMADA and ciclo.confirmado == "maria.teste"
    # o e-mail e o código foram para o campo abaixo do rótulo certo; o nome e o @ cada um no seu
    assert ("email", "maria@parque.exemplo.test") in app.digitados
    assert ("nome", "Maria Souza") in app.digitados and ("usuario", "maria.teste") in app.digitados
    # a data de nascimento: mês, dia e ano nas rodas, na ordem de leitura da tela (mês primeiro neste app)
    assert [(k, v) for k, v in app.digitados if k in ("mes", "dia", "ano")] == [("mes", "3"), ("dia", "7"), ("ano", "1994")]
    # a senha e o código só pelo canal sensível; nunca no texto comum
    assert ("senha", SENHA) in app.sensiveis and SENHA not in {v for _, v in app.digitados}
    assert CODIGO not in {v for _, v in app.digitados}
    assert app.valores["codigo"] == CODIGO
    assert app.envios == 1 and app.toques.count("signup") == 1


async def test_o_codigo_de_antes_do_envio_tem_o_piso_no_toque_que_o_pediu_e_nao_conta_como_envio() -> None:
    app = AppBloks()
    motor, ciclo, caixa = _motor(app)
    assert (await motor.executar()).confirmada
    # primeiro relógio lido = o toque em "Next" do e-mail (dispara_codigo); o segundo = o toque em "Sign up" (envia)
    assert caixa.pedidos == [T0]
    assert motor._enviado_em == T0 + timedelta(seconds=1)
    ev = app.eventos
    assert ev.count("enviado") == 1
    # o código foi digitado e confirmado ANTES do envio final: o "enviado" só aparece depois do tap do código e antes do Sign up
    assert ev.index("tap:email") < ev.index("tap:codigo") < ev.index("enviado") < ev.index("tap:signup")
    assert ev[ev.index("enviado") + 1] == "tap:signup"            # marcado imediatamente ANTES do toque
    assert ev.index("iniciar") < ev.index("tap:criar")


async def test_o_email_mais_velho_que_o_pedido_nao_serve_e_nada_e_enviado() -> None:
    app = AppBloks()
    velho = CaixaFalsa((T0 - timedelta(hours=1), CODIGO))
    motor, ciclo, caixa = _motor(app, caixa=velho)
    desfecho = await motor.executar()
    assert desfecho.confirmada is False and desfecho.parada is Parada.CODIGO_NAO_CHEGOU
    assert caixa.pedidos == [T0]
    assert app.envios == 0 and "enviado" not in app.eventos          # a conta continua aguardando o cadastro externo
    assert ciclo.estado() is Estado.AGUARDANDO_CADASTRO_EXTERNO
    assert app.sensiveis == [] and app.valores.get("codigo") is None


async def test_o_botao_que_dispara_o_codigo_so_e_tocado_uma_vez() -> None:
    app = AppBloks(ficam={"email"})                                  # o app ignora o toque: a tela do e-mail continua
    motor, _, _ = _motor(app)
    desfecho = await motor.executar()
    assert desfecho.parada is Parada.TELA_DESCONHECIDA and desfecho.confirmada is False
    assert app.toques.count("email") == 1 and app.tela == "email"


async def test_o_envio_por_toque_acontece_uma_vez_so_mesmo_se_o_app_ignorar() -> None:
    app = AppBloks(ficam={"signup"})
    motor, ciclo, _ = _motor(app)
    desfecho = await motor.executar()
    assert desfecho.parada is Parada.TELA_DESCONHECIDA
    assert app.toques.count("signup") == 1 and app.eventos.count("enviado") == 1
    assert ciclo.estado() is Estado.AGUARDANDO_VERIFICACAO


async def test_o_envio_e_marcado_antes_do_toque_mesmo_que_o_toque_falhe() -> None:
    app = AppBloks(tela="revisar")
    motor, ciclo, _ = _motor(app, estado=Estado.AGUARDANDO_CADASTRO_EXTERNO)
    original = motor.mesa.tocar

    async def tocar_e_quebrar(x: int, y: int) -> None:
        if app.tela == "revisar":
            raise FalhaNaMesa("DriverTimeout")
        await original(x, y)

    motor.mesa.tocar = tocar_e_quebrar                     # type: ignore[method-assign]
    desfecho = await motor.executar()
    assert desfecho.confirmada is False
    assert ciclo.estado() is Estado.AGUARDANDO_VERIFICACAO and app.eventos.count("enviado") == 1
    assert app.toques == []


async def test_a_roda_com_zero_a_esquerda_confere_como_o_mesmo_numero() -> None:
    app = AppBloks(zero_a_esquerda=True)                       # o app mostra "07" para o que se digitou "7"
    motor, _, _ = _motor(app)
    assert (await motor.executar()).confirmada
    assert ("dia", "7") in app.digitados


async def test_roda_que_ignora_o_que_se_digita_para_sem_enviar_nada() -> None:
    app = AppBloks(ignoram={"dia"})
    motor, ciclo, _ = _motor(app)
    desfecho = await motor.executar()
    assert desfecho.parada is Parada.TELA_DESCONHECIDA and app.envios == 0
    assert app.tela == "nascimento" and "nascimento" not in app.toques        # não avança com a data errada


async def test_sem_data_de_nascimento_o_motor_nao_inventa_uma() -> None:
    app = AppBloks()
    motor, _, _ = _motor(app, nascimento=None)
    desfecho = await motor.executar()
    assert desfecho.parada is Parada.TELA_DESCONHECIDA and app.tela == "nascimento"
    assert [k for k, _ in app.digitados if k in ("mes", "dia", "ano")] == []


async def test_a_senha_nao_vai_a_campo_que_o_android_nao_diz_ser_de_senha() -> None:
    app = AppBloks(senha_em_campo_comum=True)
    motor, _, _ = _motor(app)
    desfecho = await motor.executar()
    assert desfecho.parada is Parada.TELA_DESCONHECIDA
    assert [k for k, _ in app.sensiveis] == ["codigo"]                 # só o código (outra tela, outro campo); a senha não saiu
    assert SENHA not in {v for _, v in app.sensiveis} | {v for _, v in app.digitados} and app.envios == 0


async def test_um_desafio_declarado_no_meio_para_sem_tocar_em_mais_nada() -> None:
    app = AppBloks(tela="captcha")
    motor, _, _ = _motor(app, estado=Estado.AGUARDANDO_CADASTRO_EXTERNO)
    desfecho = await motor.executar()
    assert desfecho.parada is Parada.CAPTCHA
    assert app.toques == [] and app.digitados == [] and app.sensiveis == []


async def test_o_app_fora_do_ar_para_na_primeira_leitura() -> None:
    app = AppBloks(fora_do_ar=True)
    motor, _, _ = _motor(app)
    assert (await motor.executar()).parada is Parada.APP_FORA_DO_AR


async def test_o_handle_lido_diferente_do_desejado_nao_confirma() -> None:
    app = AppBloks(handle_final="outra.pessoa")
    motor, ciclo, _ = _motor(app)
    desfecho = await motor.executar()
    assert desfecho.parada is Parada.CONTA_NAO_LIDA and ciclo.estado() is not Estado.CONFIRMADA


# ------------------------------------------------------------------------------------------------ retomada
async def test_retomada_na_tela_do_codigo_nao_conta_envio_e_usa_o_piso_do_cadastro() -> None:
    """A execução caiu depois de o app mandar o código e antes de o digitar: a conta ainda aguarda o cadastro (não houve envio)."""
    app = AppBloks(tela="codigo")
    desde = T0 - timedelta(minutes=2)
    motor, ciclo, caixa = _motor(app, estado=Estado.AGUARDANDO_CADASTRO_EXTERNO, desde=desde,
                                 caixa=CaixaFalsa((T0 - timedelta(minutes=1), CODIGO)))
    desfecho = await motor.executar()
    assert desfecho.confirmada, app.eventos
    assert caixa.pedidos == [desde]                             # sem o toque na memória, o piso é o começo do cadastro
    assert app.eventos.index("tap:codigo") < app.eventos.index("enviado") and app.eventos.count("enviado") == 1


async def test_retomada_depois_do_envio_so_confere_a_conta() -> None:
    app = AppBloks(tela="sucesso", valores={"usuario": "maria.teste"})
    motor, ciclo, _ = _motor(app, estado=Estado.AGUARDANDO_VERIFICACAO)
    assert (await motor.executar()).confirmada
    assert app.toques == [] and app.digitados == [] and app.envios == 0


async def test_retomada_com_o_formulario_de_volta_depois_do_envio_nao_preenche_de_novo() -> None:
    app = AppBloks(tela="email")
    motor, _, _ = _motor(app, estado=Estado.AGUARDANDO_VERIFICACAO)
    desfecho = await motor.executar()
    assert desfecho.parada is Parada.TELA_DESCONHECIDA
    assert app.digitados == [] and app.toques == []


async def test_retomada_na_tela_de_revisao_com_o_envio_ja_marcado_nao_toca_de_novo() -> None:
    app = AppBloks(tela="revisar")
    motor, _, _ = _motor(app, estado=Estado.AGUARDANDO_VERIFICACAO)
    desfecho = await motor.executar()
    assert desfecho.parada is Parada.TELA_DESCONHECIDA and app.toques == []


# ------------------------------------------------------------------------------------------------ o serviço
@pytest.fixture(autouse=True)
def _limpar_cache() -> Any:
    yield
    cadastro_conhecimento.do_app.cache_clear()


_YAML_COM_NASCIMENTO = YAML_DO_SERVICO.replace('- {dado: nome, id: ":id/fullname"}',
                                               '- {dado: nome, id: ":id/fullname"}\n      - {dado: nascimento_ano, id: ":id/year"}')


async def test_o_servico_recusa_antes_de_tocar_no_aparelho_sem_data_de_nascimento(harness: Harness, tmp_path: Path,
                                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    assert _YAML_COM_NASCIMENTO != YAML_DO_SERVICO
    cen = await _cenario(harness, tmp_path, monkeypatch, yaml=_YAML_COM_NASCIMENTO)
    harness.state.db.execute("UPDATE instagram_profiles SET birth_date=NULL WHERE id=?", (cen.pid,))
    with pytest.raises(SocialError) as erro:
        _iniciar(cen)
    assert erro.value.code == "sem_nascimento" and erro.value.status == 409
    assert cen.despachos == []


async def test_o_servico_recusa_persona_menor_de_idade_sem_dizer_a_data(harness: Harness, tmp_path: Path,
                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    cen = await _cenario(harness, tmp_path, monkeypatch, yaml=_YAML_COM_NASCIMENTO)
    recente = (datetime.now(UTC).date() - timedelta(days=365 * 15)).isoformat()
    harness.state.db.execute("UPDATE instagram_profiles SET birth_date=? WHERE id=?", (recente, cen.pid))
    with pytest.raises(SocialError) as erro:
        _iniciar(cen)
    assert erro.value.code == "persona_menor_de_idade" and erro.value.status == 409
    assert recente not in str(erro.value) and recente[:4] not in erro.value.message
    assert cen.despachos == []


async def test_o_servico_entrega_a_data_da_persona_ao_motor(harness: Harness, tmp_path: Path,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    cen = await _cenario(harness, tmp_path, monkeypatch, yaml=_YAML_COM_NASCIMENTO)
    vistos: list[Dados] = []

    class MotorQueSoGuarda:
        def __init__(self, k: ConhecimentoDeCadastro, mesa: object, ciclo: object, dados: Dados) -> None:
            vistos.append(dados)

        async def executar(self) -> Desfecho:
            return Desfecho(True)

    monkeypatch.setattr(cadastro_guiado, "MotorDeCadastro", MotorQueSoGuarda)
    resposta = _iniciar(cen)
    assert resposta["accepted"]
    await cen.despachos[-1]["factory"]()
    assert vistos and vistos[0].nascimento == date(1994, 3, 12)          # a data que o `_persona` do harness grava
    assert vistos[0].valor("nascimento_ano") == "1994" and vistos[0].valor("nascimento_mes") == "3"


async def test_app_que_nao_pede_nascimento_nao_exige_a_data(harness: Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cen = await _cenario(harness, tmp_path, monkeypatch)
    harness.state.db.execute("UPDATE instagram_profiles SET birth_date=NULL WHERE id=?", (cen.pid,))
    assert _iniciar(cen)["accepted"] and PACOTE_DO_SERVICO
    assert len(cen.despachos) == 1


def test_dados_sem_data_devolvem_none_para_os_tres_numeros() -> None:
    d = Dados(usuario="u", nome="n", primeiro_nome="n", sobrenome="", email=None, senha=lambda: "x")
    assert [d.valor(x) for x in ("nascimento_dia", "nascimento_mes", "nascimento_ano")] == [None, None, None]
    d.nascimento = date(2001, 12, 1)
    assert [d.valor(x) for x in ("nascimento_dia", "nascimento_mes", "nascimento_ano")] == ["1", "12", "2001"]
