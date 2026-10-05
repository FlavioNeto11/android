"""Detector único de conta travada: para SEM tocar, e diz se é conta travada ou código (ADR-055, pacote "detector").

Decisão do dono (29/09/2026): "toda vez que para na tela de confirmar se você é humano é uma confirmação que a conta
está bloqueada; essa é uma das formas de perder a conta". Cinco das oito contas do Instagram estão bloqueadas
(beatriz, felipe, juliana, mariana, thiago). Os buracos que deixavam a regra sem efeito:

1. No meio de uma etapa, "Confirm you’re human" só contava como desafio se a tela tivesse campo de texto
   (`hierarchy.parse_hierarchy`); sem campo, decidiam a receita e o ator — e o ator era instruído a dispensar
   "diálogos inesperados" (tocar em "Continue", "Get support", voltar).
2. Quando detectava, o executor gravava `auth_required` (volta ao login automático), nunca `auth_challenge`: o
   bloqueio por desafio (ADR-029) não rodava por esse caminho, e o `StepBlocked` nem tinha um tipo para desafio.
3. O motor de sessão escolhia UMA tabela de idioma por `ro.product.locale`, e o padrão não casava o apóstrofo
   tipográfico (U+2019) em idioma nenhum: tela "desconhecida" → o motor tocava a dispensa e voltava.
4. O tipo e o trecho que casaram eram calculados e descartados.

E a outra metade da decisão: código de login por e-mail/2FA é "precisa de pessoa", SEM bloquear o perfil — bruno e
andre passaram por isso em 18/09 e estão vivos.

Nível de prova: `simulated` — o parser de hierarquia de verdade, o motor de sessão com o `FakeInstagram`, e o Harness
(porta base 5640) com o `FakeInstagram` e um ator por regras. A prova `real` numa conta travada fica `not_run`.
"""
from __future__ import annotations

import functools
import inspect
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator
from xml.sax.saxutils import quoteattr

import pytest
import pytest_asyncio

from app.automation.hierarchy import UiTree, parse_hierarchy
from app.integrations.app_declarado.conhecimento import do_app
from app.integrations.app_declarado.sessao import Outcome
from app.models import ProfileCreate, SessionStatus
from app.planning.provider import Decision, DecisionRequest, Usage
from app.state import AppState
from app.taskqueue import recipes as recipes_mod
from app.util import now_iso

from .conftest import CountingProvider, Harness
from .fake_instagram import PKG, AtorDoInstagram, FakeInstagram, Node
from .test_instagram_auth import FakeRt, build, cadastrar

#: A tela relatada pelo dono, com o apóstrofo TIPOGRÁFICO (U+2019) — o que o Instagram de fato desenha.
TRAVA_EN = "Confirm you\u2019re human to use your account, fulano"
TRAVA_PT = "Confirme que você é humano para usar sua conta"
#: Código de login por e-mail: precisa de pessoa, mas não é conta travada.
CODIGO_EN = "Enter the 6-digit code we sent to f***@gmail.com"
#: Uma verificação que nenhum sinal conhece: só o ator a reconhece, e relata com `step_blocked(kind="challenge")`.
VERIFICACAO_DESCONHECIDA = "Precisamos confirmar algumas informações antes de continuar"
IID = "android-01"
USUARIO = "tadeu.quintela4821"
SENHA = "$a=B7ee1#<b-C?S-{"
#: O que conta como tocar/teclar/reabrir no aparelho falso (a leitura da tela não aparece em `calls`).
TOQUES = ("tap:", "key:", "swipe", "type:", "submit", "open_app", "force_stop")


@dataclass
class InstagramComTrava(FakeInstagram):
    """`FakeInstagram` onde a verificação de segurança aparece por cima no meio do caminho.

    `trava` é o texto da tela (None = nunca aparece); `trava_ao` é a ação cujo toque a faz aparecer (`inbox`: tocar em
    Mensagens; `profile`: tocar na aba de perfil); `trava_com_campo` põe o campo do código. A tela tem "Continue",
    "Get support" e uma recusa com o id que o motor de sessão dispensa (`dialog_secondary`): tudo o que a automação
    antiga tocaria. Nenhum toque tira a tela da frente, e reabrir o app também não — como no aparelho real.
    `trava_vista_em` marca o índice de `calls` em que a tela foi desenhada pela primeira vez."""

    trava: str | None = None
    trava_ao: str = "inbox"
    trava_com_campo: bool = False
    trava_vista_em: int | None = None
    abriu: list[str] = field(default_factory=list)

    def _build(self) -> list[Node]:
        if self.screen != "trava":
            return super()._build()
        if self.trava_vista_em is None:
            self.trava_vista_em = len(self.calls)
        nos = [Node("android.widget.TextView", (40, 200, 680, 300), text=self.trava or ""),
               Node("android.widget.Button", (40, 900, 680, 960), text="Continue", clickable=True, action="nada"),
               Node("android.widget.Button", (40, 980, 680, 1040), text="Get support", clickable=True, action="nada"),
               Node("android.widget.Button", (40, 1060, 680, 1120), text="Not now", rid="dialog_secondary",
                    clickable=True, action="nada")]
        if self.trava_com_campo:
            nos.insert(1, Node("android.widget.EditText", (40, 320, 680, 390), rid="code", clickable=True,
                               editable=True, action="focus:code"))
        return nos

    def tap(self, x: int, y: int) -> None:
        antes = self.screen
        super().tap(x, y)
        if self.trava and antes != "trava" and self.screen == self.trava_ao:
            self.screen = "trava"
        elif antes == "trava":
            self.screen = "trava"                  # nenhum botão da verificação a tira da frente

    def open_app(self, package: str, activity: str | None) -> None:
        self.calls.append("open_app")
        if self.screen == "trava":
            return                                 # reabrir não faz a verificação sumir
        super().open_app(package, activity)

    def force_stop(self, package: str) -> None:
        self.calls.append("force_stop")
        super().force_stop(package)


def _depois_da_trava(app: InstagramComTrava) -> list[str]:
    assert app.trava_vista_em is not None, "a tela de verificação nunca foi desenhada"
    return [c for c in app.calls[app.trava_vista_em:] if c.startswith(TOQUES)]


@dataclass
class CodigoSemEditText(FakeInstagram):
    """A tela de código de login/2FA com o campo desenhado por um widget que NÃO é da classe `EditText` — o repositório
    registra que o Instagram usa widgets assim (`integrations/app_declarado/formulario.py`, `sessao.yaml` do app).
    "Voltar" nela leva ao formulário de login, como no app: quem tocar ali acaba digitando a senha de novo — o SEGUNDO
    envio que as decisões (b) e (e) do ADR-055 proíbem. `codigo_visto_em` marca o índice de `calls` em que a tela
    foi desenhada pela primeira vez (revisão do pacote "detector", sondas `probe_2fa*.py`)."""

    codigo_visto_em: int | None = None

    def _build(self) -> list[Node]:
        if self.screen != "two_factor":
            return super()._build()
        if self.codigo_visto_em is None:
            self.codigo_visto_em = len(self.calls)
        return [Node("android.widget.TextView", (40, 200, 680, 280), text="Enter the 6-digit security code"),
                Node("android.view.View", (40, 320, 680, 390), rid="code", clickable=True)]

    def press_key(self, key: str) -> None:
        if self.screen == "two_factor":
            self.calls.append(f"key:{key}")
            self.screen = "login"
            return
        super().press_key(key)

    def gestos_depois_do_codigo(self, desde: int = 0) -> list[str]:
        assert self.codigo_visto_em is not None, "a tela do código nunca foi desenhada"
        return [c for c in self.calls[max(desde, self.codigo_visto_em):] if c.startswith(TOQUES)]


def _tela(texto: str, *, campo: bool = False, pkg: str = PKG) -> UiTree:
    nos = f'<node class="android.widget.TextView" package="{pkg}" text={quoteattr(texto)} bounds="[40,200][680,300]"/>'
    nos += (f'<node class="android.widget.Button" package="{pkg}" text="Continue" clickable="true" '
            'bounds="[40,900][680,960]"/>')
    if campo:
        nos += (f'<node class="android.widget.EditText" package="{pkg}" resource-id="{pkg}:id/code" '
                'bounds="[40,320][680,390]"/>')
    return parse_hierarchy(f"<hierarchy>{nos}</hierarchy>")


# ================================================================== o detector, sem aparelho
def test_normaliza_apostrofo_tipografico_acento_e_caixa() -> None:
    from app.automation.hierarchy import normalizar_texto_de_tela

    assert normalizar_texto_de_tela("Confirm you\u2019re  HUMAN") == "confirm you're human"
    assert normalizar_texto_de_tela("Confirm you\u2018re human") == "confirm you're human"
    assert normalizar_texto_de_tela("Confirme que você é humano") == "confirme que voce e humano"


#: Variantes próximas da redação relatada (item 8.3). A hierarquia é SINTÉTICA (`_tela`): a tela real não existe mais,
#: a conta que a mostrou foi perdida — o que se prova aqui é o casamento da redação, não a tela do app.
VARIANTES_DA_TRAVA = ["Verify you're human", "Prove you are a human", "Confirm you're a real person",
                      "Confirmar que você é humano", "Comprove que você é uma pessoa real",
                      "Confirme que você é uma pessoa", "Verifique que você é humano"]


@pytest.mark.parametrize("texto", [TRAVA_EN, TRAVA_PT, "Confirm you are human", "Confirm you're human",
                                   *VARIANTES_DA_TRAVA])
def test_tela_de_conta_travada_sem_campo_e_detectada_e_sensivel(texto: str) -> None:
    """Buraco 1: sem campo de texto a tela passava como comum — a imagem ia ao modelo e ninguém parava."""
    from app.automation.conhecimento_de_telas import detectar_conta_travada

    tela = _tela(texto)
    assert tela.sensitive is True and tela.sensitive_reason and "desafio" in tela.sensitive_reason
    for k in (None, do_app(PKG).telas):
        trava = detectar_conta_travada(tela, k)
        assert trava is not None and trava.subtipo == "conta_travada", (texto, k)
        assert any(p in trava.trecho for p in ("human", "person", "pessoa"))


@pytest.mark.parametrize("texto", ["Can you confirm you're a person who likes it?", "Confirm your email address",
                                   "Verify you're coming tomorrow"])
def test_conversa_comum_nao_vira_conta_travada(texto: str) -> None:
    """O detector roda no meio da execução, em DMs e legendas: só frase de desafio, nunca verbo solto."""
    from app.automation.conhecimento_de_telas import detectar_conta_travada

    assert detectar_conta_travada(_tela(texto), do_app(PKG).telas) is None


@pytest.mark.parametrize("locale", ["en-US", "pt-BR", None])
@pytest.mark.parametrize("texto", [TRAVA_EN, TRAVA_PT, VARIANTES_DA_TRAVA[0], VARIANTES_DA_TRAVA[3]])
def test_classificacao_casa_a_uniao_dos_idiomas(locale: str | None, texto: str) -> None:
    """Buraco 3: o idioma do APARELHO não diz o idioma da tela de verificação (o Instagram mostrou inglês num
    aparelho em português). A tabela única por `ro.product.locale` deixava a tela "desconhecida"."""
    r = do_app(PKG).reconhecer(_tela(texto), package=PKG, locale=locale)
    assert r.tela == "challenge" and r.tipo == "desafio", (locale, texto, r.razao)
    assert r.trava is not None and r.trava.subtipo == "conta_travada"
    assert r.evidencia and r.evidencia[0] == r.trava.trecho          # o trecho que casou é guardado, não descartado


def test_codigo_de_login_e_subtipo_codigo_e_exige_campo() -> None:
    from app.automation.conhecimento_de_telas import detectar_conta_travada

    k = do_app(PKG).telas
    trava = detectar_conta_travada(_tela(CODIGO_EN, campo=True), k)
    assert trava is not None and trava.subtipo == "codigo" and "code" in trava.trecho
    r = do_app(PKG).reconhecer(_tela(CODIGO_EN, campo=True), package=PKG, locale="pt-BR")
    assert r.tela == "two_factor" and r.tipo == "dois_fatores" and r.trava is not None
    # A linha "Autenticação de dois fatores" do MENU de configurações não tem onde digitar: não é pedido de código.
    menu = _tela("Autenticação de dois fatores")
    assert detectar_conta_travada(menu, k) is None and menu.sensitive is False


_FRASES = [(f, "conta_travada") for f in (
    TRAVA_EN, "Confirm you are human", "We detected an unusual login attempt", "Suspicious login attempt",
    "Help us confirm it's you", "Confirm it's you", "Verify your account", "I'm not a robot", "Complete the captcha",
    TRAVA_PT, "Confirme que é você", "Detectamos uma tentativa de login incomum", "Atividade suspeita na sua conta",
    "Ajude a confirmar sua identidade", "Verifique sua conta", "Não sou um robô", "Confirme que você é uma pessoa",
)] + [(f, "codigo") for f in (
    CODIGO_EN, "Enter the security code", "Enter the confirmation code", "Two-factor authentication",
    "Enter the code we sent", "Autenticação de dois fatores", "Código de segurança", "Insira o código que enviamos",
    "Código de confirmação",
)]


@pytest.mark.parametrize("frase,subtipo", _FRASES)
def test_sinais_genericos_e_declarados_concordam_no_subtipo(frase: str, subtipo: str) -> None:
    """Dois classificadores discordando sobre a MESMA tela é pior do que ter um só: o genérico (`hierarchy`, que decide
    a imagem e vale para qualquer app) e o declarado (`telas.yaml`, na união dos idiomas) dão o MESMO subtipo — e o
    que bloqueia o perfil (conta travada) nunca vira o que só chama a pessoa (código), nem o contrário."""
    from app.automation import conhecimento_de_telas as ct
    from app.automation.hierarchy import normalizar_texto_de_tela

    tela = _tela(frase, campo=subtipo == "codigo")
    assert tela.conta_travada is not None and tela.conta_travada.subtipo == subtipo, frase
    k = do_app(PKG).telas
    tipo = "desafio" if subtipo == "conta_travada" else "dois_fatores"
    declarada = ct._trava_declarada(k, tipo, normalizar_texto_de_tela(frase), frase)  # noqa: SLF001
    assert declarada is not None and declarada.subtipo == subtipo, frase
    for locale in ("en-US", "pt-BR"):
        r = do_app(PKG).reconhecer(tela, package=PKG, locale=locale)
        assert r.trava is not None and r.trava.subtipo == subtipo and r.tipo == tipo, (frase, locale)


@pytest.mark.parametrize("texto", ["An unusual day at the beach", "achei suspeito isso aí", "detectamos o erro",
                                   "we detected a planet", "Suspicious minds"])
def test_palavra_solta_em_conteudo_de_usuario_nao_trava(texto: str) -> None:
    """O detector roda no meio da execução, em conversas e comentários: palavra solta ("suspeito", "unusual") numa DM
    bloquearia uma das três contas vivas. Só a FRASE da tela de segurança conta — com ou sem campo de texto."""
    from app.automation.conhecimento_de_telas import detectar_conta_travada

    for campo in (False, True):
        tela = _tela(texto, campo=campo)
        assert detectar_conta_travada(tela, do_app(PKG).telas) is None, (texto, campo)
        assert do_app(PKG).reconhecer(tela, package=PKG, locale="pt-BR").tipo not in ("desafio", "dois_fatores")


# ================================================================== o motor de sessão
@pytest.mark.parametrize("locale", ["en-US", "pt-BR"])
async def test_sessao_para_na_trava_sem_tocar_em_nenhum_idioma(tmp_path: Path, locale: str) -> None:
    """Buraco 3 no motor de sessão: tela desconhecida → dispensa ("Not now") e voltar. Agora: desafio, nada tocado,
    perfil bloqueado, e o tipo e o trecho ficam no evento."""
    app = InstagramComTrava(account=USUARIO, screen="trava", trava=TRAVA_EN, locale=locale)
    auth, repo, social, db = build(tmp_path, app)
    try:
        pid = cadastrar(social, username=USUARIO, senha=SENHA)
        r = await auth.ensure_session(FakeRt(app), pid)
        assert r.outcome is Outcome.AUTH_CHALLENGE, r.detail
        assert _depois_da_trava(app) == [], app.calls
        assert repo.session_row(pid)["status"] == SessionStatus.auth_challenge.value
        assert repo.profile_row(pid)["status"] == "blocked"
        eventos = [json.loads(e["data"] or "{}") for e in db.query("SELECT data FROM events WHERE kind='log'")]
        assert any(e.get("subtipo") == "conta_travada" and "human" in (e.get("trecho") or "") for e in eventos)
    finally:
        db.close()


async def test_ler_conta_para_na_trava_sem_tocar(tmp_path: Path) -> None:
    """A trava aparece DEPOIS de tocar na aba de perfil, na leitura da conta: o laço de `ler_conta` dispensava a
    tela ("Not now") e seguia tocando."""
    app = InstagramComTrava(account=USUARIO, screen="feed", trava=TRAVA_EN, trava_ao="profile", locale="pt-BR")
    auth, repo, social, db = build(tmp_path, app)
    try:
        pid = cadastrar(social, username=USUARIO, senha=SENHA)
        r = await auth.ensure_session(FakeRt(app), pid)
        assert r.outcome is Outcome.AUTH_CHALLENGE, r.detail
        assert _depois_da_trava(app) == [], app.calls
        assert repo.profile_row(pid)["status"] == "blocked"
    finally:
        db.close()


@pytest.mark.parametrize("locale", ["en-US", "pt-BR"])
async def test_sessao_para_no_codigo_cujo_campo_nao_e_edittext(tmp_path: Path, locale: str) -> None:
    """Revisão do pacote (bloqueio 1): o detector passou a exigir campo `EditText` também da regra DECLARADA
    `two_factor` do motor de sessão. Com o campo desenhado por outro widget, a tela virava "desconhecida", o motor
    voltava ("voltar" é toque) e caía no login: senha digitada e enviada de novo, `session_ready`. Na base, a regra
    casava só pelo texto. Agora: código, nada tocado, a pessoa chamada, o perfil segue ativo (decisão b)."""
    app = CodigoSemEditText(stored_password=SENHA, screen="two_factor", locale=locale)
    auth, repo, social, db = build(tmp_path, app)
    try:
        pid = cadastrar(social, username=USUARIO, senha=SENHA)
        r = await auth.ensure_session(FakeRt(app), pid)
        assert r.outcome is Outcome.AUTH_CHALLENGE, (r.outcome, r.detail)
        assert app.gestos_depois_do_codigo() == [], app.calls
        assert app.typed == []                                            # nenhuma senha digitada
        assert repo.session_row(pid)["status"] == SessionStatus.auth_challenge.value
        assert repo.profile_row(pid)["status"] == "active"
        assert "codigo" in (r.detail or ""), r.detail
    finally:
        db.close()


async def test_codigo_sem_edittext_depois_do_envio_nao_reenvia_a_senha(tmp_path: Path) -> None:
    """Revisão do pacote (bloqueio 1, 2ª sonda): login → envio → tela de código sem `EditText`. Antes, a 1ª chamada
    saía `uncertain` (sessão `unknown`), e o tick automático seguinte voltava da tela do código e ENVIAVA A SENHA DE
    NOVO. Agora a 1ª chamada é o código (pessoa, sem bloquear) e o tick automático não faz gesto nenhum: um envio só."""
    app = CodigoSemEditText(stored_password=SENHA, two_factor_on_login=True)
    auth, repo, social, db = build(tmp_path, app)
    try:
        pid = cadastrar(social, username=USUARIO, senha=SENHA)
        r = await auth.ensure_session(FakeRt(app), pid)
        assert r.outcome is Outcome.AUTH_CHALLENGE, (r.outcome, r.detail)
        assert repo.session_row(pid)["status"] == SessionStatus.auth_challenge.value
        assert repo.profile_row(pid)["status"] == "active"
        assert app.gestos_depois_do_codigo() == [], app.calls
        n = len(app.calls)
        r2 = await auth.ensure_session(FakeRt(app), pid, automatic=True)
        assert r2.outcome is Outcome.AUTH_CHALLENGE, (r2.outcome, r2.detail)
        assert app.gestos_depois_do_codigo(n) == [], app.calls[n:]
        assert app.calls.count("submit") == 1 and len(repo.auth_attempts(pid)) == 1
    finally:
        db.close()


def test_codigo_declarado_sem_campo_so_vale_no_motor_de_sessao() -> None:
    """A exigência de campo para o código é de CONTEXTO. No meio da execução (padrão do detector) a linha
    "Autenticação de dois fatores" do menu de configurações não é pedido de código. No motor de sessão a regra
    declarada casa pelo texto: parar ali custa o clique de quem olha (código não bloqueia), tocar custava um 2º envio
    de senha. O sinal genérico continua exigindo onde digitar nos dois."""
    from app.automation.conhecimento_de_telas import detectar_conta_travada

    k = do_app(PKG).telas
    for texto in ("Autenticação de dois fatores", "Enter the 6-digit security code"):
        tela = _tela(texto)
        assert tela.conta_travada is None, texto                              # genérico: sem campo, não é código
        assert detectar_conta_travada(tela, k) is None, texto                 # execução
        trava = detectar_conta_travada(tela, k, codigo_declarado_exige_campo=False)
        assert trava is not None and trava.subtipo == "codigo", texto         # motor de sessão
        r = do_app(PKG).reconhecer(tela, package=PKG, locale="en-US")
        assert r.tipo == "dois_fatores" and r.trava is not None and r.trava.subtipo == "codigo", texto


def test_trava_e_codigo_na_mesma_tela_sao_conta_travada() -> None:
    """A precedência não muda com a regra do código sem campo: a tela que pede "confirme que é humano" E cita um
    código é conta travada (bloqueia), no motor de sessão e no meio da execução."""
    from app.automation.conhecimento_de_telas import detectar_conta_travada

    texto = f"{TRAVA_EN}. Enter the 6-digit security code"
    k = do_app(PKG).telas
    for campo in (False, True):
        tela = _tela(texto, campo=campo)
        trava = detectar_conta_travada(tela, k)
        assert trava is not None and trava.subtipo == "conta_travada", campo
        r = do_app(PKG).reconhecer(tela, package=PKG, locale="pt-BR")
        assert r.trava is not None and r.trava.subtipo == "conta_travada" and r.tipo == "desafio", campo


async def test_codigo_de_login_depois_do_envio_pede_pessoa_sem_bloquear(tmp_path: Path) -> None:
    """Decisão (b): o código de login por e-mail/2FA é da pessoa, mas NÃO é conta travada — bruno e andre passaram por
    ele em 18/09 e seguem vivos. A tentativa registra o tipo e o trecho."""
    app = FakeInstagram(stored_password=SENHA, two_factor_on_login=True)
    auth, repo, social, db = build(tmp_path, app)
    try:
        pid = cadastrar(social, username=USUARIO, senha=SENHA)
        r = await auth.ensure_session(FakeRt(app), pid)
        assert r.outcome is Outcome.AUTH_CHALLENGE
        assert repo.session_row(pid)["status"] == SessionStatus.auth_challenge.value
        assert repo.profile_row(pid)["status"] == "active"
        tentativa = repo.auth_attempts(pid)[0]
        assert "codigo" in (tentativa["detail"] or "") and "security code" in (tentativa["detail"] or "")
    finally:
        db.close()


# ================================================================== o executor, no meio de uma etapa
class AtorQueVe(AtorDoInstagram):
    """O ator por regras do Instagram falso, que guarda o texto de cada tela que recebeu para decidir. Diante de uma
    verificação que ele não sabe conduzir, relata `step_blocked(kind="challenge")` — o que o prompt manda fazer."""

    def __init__(self) -> None:
        self.telas: list[str] = []

    async def decide(self, req: DecisionRequest) -> tuple[Decision, Usage]:
        texto = " ".join(req.screen.tree.texts())
        self.telas.append(texto)
        if VERIFICACAO_DESCONHECIDA in texto:
            return Decision(tool="step_blocked", args={"rationale": "[roteiro] tela de verificação",
                                                       "kind": "challenge", "needs_user": True,
                                                       "reason": "a tela pede uma verificação da conta"}), Usage()
        return await super().decide(req)


async def _parque(tmp_path: Path, **fake: Any) -> AsyncIterator[Harness]:
    h = Harness(tmp_path, 1, factory=lambda rt: InstagramComTrava(account=USUARIO, screen="feed", **fake))
    h.ai = CountingProvider(AtorQueVe())
    h.cfg.file.ai.recipes = "replay"
    await h.boot()
    await h.medir_a_internet()   # T.2: a sonda do monitor agora; o Instagram não espera os 6 s da 1ª volta
    s = _estado(h)
    s.db.execute("UPDATE instances SET app_id='instagram' WHERE id=?", (IID,))
    s.devices.get(IID).app_id = "instagram"
    try:
        yield h
    finally:
        if h.state is not None:
            await h.state.stop()


def _estado(h: Harness) -> AppState:
    assert h.state is not None
    return h.state


def _aparelho(h: Harness) -> InstagramComTrava:
    fake = h.fakes[IID]
    assert isinstance(fake, InstagramComTrava)
    return fake


def _ator(h: Harness) -> AtorQueVe:
    ator = h.ai.inner
    assert isinstance(ator, AtorQueVe)
    return ator


def _perfil_pronto(h: Harness) -> str:
    s = _estado(h)
    pid = s.social.create_profile(ProfileCreate(username=USUARIO, password=SENHA, instance_id=IID)).id
    s.social_repo.set_session(pid, status=SessionStatus.session_ready, instance_id=IID, observed_username=USUARIO,
                              verified_at=now_iso())
    return pid


async def _ate_parar(h: Harness, run_id: str) -> Any:
    s = _estado(h)

    def parado() -> bool:
        row = s.db.one("SELECT status FROM objectives WHERE id=?", (f"{run_id}:{IID}",))
        return bool(row) and row["status"] in ("waiting_user", "failed", "uncertain", "succeeded")

    await h.wait(parado, timeout=60, what="o objetivo parar")
    return s.db.one("SELECT * FROM objectives WHERE id=?", (f"{run_id}:{IID}",))


@pytest_asyncio.fixture
async def parque(tmp_path: Path) -> AsyncIterator[Harness]:
    async for h in _parque(tmp_path):
        yield h


COMANDO = "abra a conversa com @ana no instagram"


@pytest.mark.parametrize("texto,locale", [(TRAVA_EN, "en-US"), (TRAVA_EN, "pt-BR"), (TRAVA_PT, "pt-BR")])
async def test_trava_no_meio_da_etapa_nao_toca_e_bloqueia_o_perfil(tmp_path: Path, texto: str, locale: str) -> None:
    async for h in _parque(tmp_path, trava=texto, locale=locale):
        pid = _perfil_pronto(h)
        run = h.run([IID], command=COMANDO)
        objetivo = await _ate_parar(h, run.id)
        fake = _aparelho(h)
        assert _depois_da_trava(fake) == [], fake.calls                 # nenhum toque, tecla ou reabertura
        assert objetivo["status"] == "waiting_user", dict(objetivo)
        motivo = objetivo["blocked_reason"] or ""
        assert "auth_challenge" in motivo and "conta_travada" in motivo, motivo
        s = _estado(h)
        tentativa = s.db.one("SELECT t.error FROM attempts t JOIN steps p ON p.id=t.step_id WHERE p.run_id=?"
                             " ORDER BY t.id DESC LIMIT 1", (run.id,))
        assert tentativa is not None and "conta_travada" in (tentativa["error"] or "")
        assert s.social_repo.session_row(pid, IID)["status"] == SessionStatus.auth_challenge.value
        assert s.social_repo.profile_row(pid)["status"] == "blocked"
        # O ator nunca recebeu a tela de verificação para decidir.
        assert not any("human" in t.casefold() or "humano" in t.casefold() for t in _ator(h).telas)
        decisoes = [json.loads(e["data"] or "{}") for e in s.db.query("SELECT data FROM events WHERE kind='decision'"
                                                                       " AND run_id=?", (run.id,))]
        assert any(d.get("subtipo") == "conta_travada" and d.get("trecho") for d in decisoes)


async def test_receita_e_ator_nunca_veem_a_trava(parque: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    """A 1ª execução aprende as receitas; na 2ª, a receita toca em Mensagens e a verificação aparece por cima. A
    receita e o ator não podem receber essa tela: o detector roda antes dos dois."""
    pid = _perfil_pronto(parque)
    primeira = await parque.wait_run(parque.run([IID], command=COMANDO).id, timeout=60)
    assert primeira.status == "completed", (primeira.status, primeira.status_detail)
    s = _estado(parque)
    assert s.db.query("SELECT id FROM recipes WHERE status='active'"), "a 1ª execução devia ter aprendido receitas"

    vistas_pela_receita: list[str] = []
    original = recipes_mod.Replayer.next

    def espiar(self: Any, tree: UiTree) -> Any:
        vistas_pela_receita.append(" ".join(tree.texts()))
        return original(self, tree)

    monkeypatch.setattr(recipes_mod.Replayer, "next", espiar)
    fake = _aparelho(parque)
    fake.screen, fake.thread_with, fake.search_query, fake.trava = "feed", None, "", TRAVA_EN
    _ator(parque).telas.clear()
    run = parque.run([IID], command=COMANDO)
    objetivo = await _ate_parar(parque, run.id)
    assert objetivo["status"] == "waiting_user", dict(objetivo)
    assert vistas_pela_receita, "a receita devia ter conduzido a 2ª execução até a trava"
    assert not any("human" in t.casefold() for t in vistas_pela_receita)
    assert not any("human" in t.casefold() for t in _ator(parque).telas)
    assert _depois_da_trava(fake) == [], fake.calls
    assert s.social_repo.profile_row(pid)["status"] == "blocked"


async def test_codigo_no_meio_da_etapa_pede_pessoa_sem_bloquear(tmp_path: Path) -> None:
    async for h in _parque(tmp_path, trava=CODIGO_EN, trava_com_campo=True):
        pid = _perfil_pronto(h)
        run = h.run([IID], command=COMANDO)
        objetivo = await _ate_parar(h, run.id)
        assert _depois_da_trava(_aparelho(h)) == []
        motivo = objetivo["blocked_reason"] or ""
        assert objetivo["status"] == "waiting_user" and "auth_challenge" in motivo and "codigo" in motivo, motivo
        s = _estado(h)
        assert s.social_repo.session_row(pid, IID)["status"] == SessionStatus.auth_challenge.value
        assert s.social_repo.profile_row(pid)["status"] == "active"


async def test_ator_relata_verificacao_desconhecida_como_desafio(tmp_path: Path) -> None:
    """Buraco 2: `step_blocked` não tinha tipo para desafio e o prompt mandava usar `auth_required` (volta ao login
    automático). Uma verificação que só o ator reconhece vira `auth_challenge`: precisa de pessoa, sem bloquear o
    perfil — o bloqueio é do detector determinístico, não do julgamento do modelo."""
    async for h in _parque(tmp_path, trava=VERIFICACAO_DESCONHECIDA):
        pid = _perfil_pronto(h)
        run = h.run([IID], command=COMANDO)
        objetivo = await _ate_parar(h, run.id)
        assert _depois_da_trava(_aparelho(h)) == []
        assert objetivo["status"] == "waiting_user", dict(objetivo)
        s = _estado(h)
        assert s.social_repo.session_row(pid, IID)["status"] == SessionStatus.auth_challenge.value
        assert s.social_repo.profile_row(pid)["status"] == "active"


# ================================================================== a regra do perfil
async def test_codigo_nao_bloqueia_e_trava_depois_do_codigo_bloqueia(tmp_path: Path) -> None:
    """A sessão parada num código (sem bloqueio) não pode impedir o bloqueio quando a trava aparece depois: antes, a
    sessão já `auth_challenge` fazia a tela desmentida sair cedo e `bloquear_por_desafio` recusar ("não é entrada")."""
    h = Harness(tmp_path, 1)
    await h.boot()
    await h.medir_a_internet()   # T.2: a sonda do monitor agora; o Instagram não espera os 6 s da 1ª volta
    try:
        s = _estado(h)
        pid = s.social.create_profile(ProfileCreate(username=USUARIO, password=SENHA, instance_id=IID)).id
        s.social_repo.set_session(pid, status=SessionStatus.session_ready, instance_id=IID, verified_at=now_iso())
        marcadas = _espiar_quarentena(s.social_repo)
        s._sessao_desmentida(IID, "auth_challenge", "código de login", subtipo="codigo")
        assert s.social_repo.session_row(pid, IID)["status"] == SessionStatus.auth_challenge.value
        assert s.social_repo.profile_row(pid)["status"] == "active" and marcadas == []

        s._sessao_desmentida(IID, "auth_challenge", "confirm you're human", subtipo="conta_travada")
        assert s.social_repo.profile_row(pid)["status"] == "blocked"
        assert len(marcadas) == 1 and marcadas[0]["instance_id"] == IID and marcadas[0]["handle"] == USUARIO
        assert "human" in str(marcadas[0]["evidencia"])
        # O contrato com a quarentena: a origem é COMO se sabe (a tela foi lida); quem viu vai em `visto_por`.
        assert marcadas[0]["origem"] == "observado" and marcadas[0]["visto_por"] == "execução"
        assert marcadas[0]["profile_id"] == pid
        s._sessao_desmentida(IID, "auth_challenge", "confirm you're human", subtipo="conta_travada")
        assert len(marcadas) == 1                                        # confirmar a mesma trava não remarca
    finally:
        await h.state.stop()  # type: ignore[union-attr]


# ================================================================== o contrato com a quarentena
#: As origens que a quarentena aceita: `social.repository.ORIGENS_DE_BLOQUEIO` e o CHECK da migração 054 do pacote
#: quarentena ("observado" = a tela foi lida; "declarado" = uma pessoa disse; "regra" = uma regra decidiu). A cópia só
#: vale enquanto o pacote não está nesta árvore; com ele, quem recusa é o método real.
ORIGENS_DA_QUARENTENA = ("observado", "declarado", "regra")


def _marcar_como_a_quarentena(instance_id: str, handle: str, evidencia: str | None, origem: str, *,
                              visto_por: str = "sistema", profile_id: str | None = None,
                              app_id: str | None = None) -> bool:
    """Dublê com a assinatura e a recusa de `SocialRepository.marcar_conta_travada` (pacote quarentena)."""
    if origem not in ORIGENS_DA_QUARENTENA:
        raise ValueError(f"origem desconhecida: {origem!r} (use {', '.join(ORIGENS_DA_QUARENTENA)})")
    if not handle.strip().lstrip("@"):
        raise ValueError("a conta travada precisa de um @ (handle)")
    return True


def _espiar_quarentena(repo: object, *, falha: Exception | None = None) -> list[dict[str, object]]:
    """Liga a quarentena ao repositório e devolve as chamadas que ela recebeu, com os argumentos pelo nome.

    Revisão do pacote (bloqueio 2): o dublê antigo aceitava qualquer origem, e o detector mandava `sessao`/`execucao`
    — a quarentena real levanta `ValueError` para tudo fora de `observado|declarado|regra` (3 falhas na árvore mesclada
    com o pacote). Por isso: com o pacote quarentena na árvore, a chamada vai ao método REAL (a assinatura e a recusa
    são as dele); sem ele, ao dublê com a mesma assinatura e a mesma recusa. `falha` faz a quarentena levantar, para
    provar que o bloqueio e o aviso ao dono saem mesmo assim."""
    real = getattr(type(repo), "marcar_conta_travada", None)
    alvo = functools.partial(real, repo) if real is not None else _marcar_como_a_quarentena
    chamadas: list[dict[str, object]] = []

    def marcar(*args: object, **kwargs: object) -> bool:
        ligados = inspect.signature(alvo).bind(*args, **kwargs)        # TypeError se a assinatura não bate
        ligados.apply_defaults()
        chamadas.append(dict(ligados.arguments))
        if falha is not None:
            raise falha
        return bool(alvo(*args, **kwargs))

    setattr(repo, "marcar_conta_travada", marcar)
    return chamadas


def _eventos(db: Any, kind: str) -> list[tuple[str, str, dict[str, object]]]:
    return [(e["level"], e["message"], json.loads(e["data"] or "{}"))
            for e in db.query("SELECT level, message, data FROM events WHERE kind=? ORDER BY id", (kind,))]


async def test_sessao_leva_a_trava_a_quarentena_com_a_origem_que_ela_aceita(tmp_path: Path) -> None:
    app = InstagramComTrava(account=USUARIO, screen="trava", trava=TRAVA_EN, locale="en-US")
    auth, repo, social, db = build(tmp_path, app)
    try:
        pid = cadastrar(social, username=USUARIO, senha=SENHA)
        marcadas = _espiar_quarentena(repo)
        r = await auth.ensure_session(FakeRt(app), pid)
        assert r.outcome is Outcome.AUTH_CHALLENGE, r.detail
        assert len(marcadas) == 1, marcadas
        assert marcadas[0]["origem"] == "observado" and marcadas[0]["visto_por"] == "motor de sessão"
        assert marcadas[0]["handle"] == USUARIO and marcadas[0]["profile_id"] == pid
        assert "human" in str(marcadas[0]["evidencia"])
    finally:
        db.close()


async def test_quarentena_que_falha_nao_cala_o_desafio_na_sessao(tmp_path: Path) -> None:
    """Revisão do pacote (bloqueio 2, a): a chamada à quarentena não era protegida em `SessaoDeclarada._save` — o erro
    subia por `ensure_session`, e o evento da fila "Aguardando intervenção" e o aviso do desafio nunca saíam. O
    `getattr` tolerava a AUSÊNCIA da função, não uma chamada que levanta."""
    app = InstagramComTrava(account=USUARIO, screen="trava", trava=TRAVA_EN, locale="en-US")
    auth, repo, social, db = build(tmp_path, app)
    try:
        pid = cadastrar(social, username=USUARIO, senha=SENHA)
        marcadas = _espiar_quarentena(repo, falha=RuntimeError("banco indisponível"))
        r = await auth.ensure_session(FakeRt(app), pid)
        assert r.outcome is Outcome.AUTH_CHALLENGE, r.detail
        assert len(marcadas) == 1
        assert _depois_da_trava(app) == [], app.calls
        assert repo.session_row(pid)["status"] == SessionStatus.auth_challenge.value
        assert repo.profile_row(pid)["status"] == "blocked"
        assert any(d.get("active") is True and d.get("status") == "auth_challenge"
                   for _, _, d in _eventos(db, "session.needs_person"))
        logs = _eventos(db, "log")
        assert any(d.get("subtipo") == "conta_travada" for _, _, d in logs)          # o aviso do desafio saiu
        assert any(nivel == "error" and "quarentena" in msg and "RuntimeError" in msg for nivel, msg, _ in logs)
    finally:
        db.close()


async def test_quarentena_que_falha_nao_cala_o_desafio_no_meio_da_execucao(tmp_path: Path) -> None:
    """Revisão do pacote (bloqueio 2, b): no executor o erro era engolido pelo `except` de `_sessao_desmentida` — o
    perfil ficava `blocked`, mas a fila "Aguardando intervenção" não era avisada e nada dizia que o marcador faltou."""
    h = Harness(tmp_path, 1)
    await h.boot()
    await h.medir_a_internet()   # T.2: a sonda do monitor agora; o Instagram não espera os 6 s da 1ª volta
    try:
        s = _estado(h)
        pid = s.social.create_profile(ProfileCreate(username=USUARIO, password=SENHA, instance_id=IID)).id
        s.social_repo.set_session(pid, status=SessionStatus.session_ready, instance_id=IID, verified_at=now_iso())
        marcadas = _espiar_quarentena(s.social_repo, falha=ValueError("origem desconhecida"))
        s._sessao_desmentida(IID, "auth_challenge", "confirm you're human", subtipo="conta_travada")
        assert len(marcadas) == 1
        assert s.social_repo.session_row(pid, IID)["status"] == SessionStatus.auth_challenge.value
        assert s.social_repo.profile_row(pid)["status"] == "blocked"
        assert any(d.get("active") is True and d.get("profile_id") == pid
                   for _, _, d in _eventos(s.db, "session.needs_person"))
        assert any(nivel == "error" and "quarentena" in msg and "ValueError" in msg
                   for nivel, msg, _ in _eventos(s.db, "log"))
    finally:
        await h.state.stop()  # type: ignore[union-attr]
