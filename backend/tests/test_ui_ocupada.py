"""UI ocupada não é sessão morta, e rolagem não vira toque longo.

Execuções reais r-20260928195344-02ee9e e r-20260928165254-e31953 (android-06, 2 vCPU saturadas): o 500 do
UiAutomator2 "waiting for the root AccessibilityNodeInfo … hogging the main UI thread" era tratado como sessão morta
— DELETE + POST /session e reinstrumentação a cada vez (5 recriações = 305,6 s de 925 s na 02ee9e). E a rolagem
desenhada numa faixa de ~200 px da grade, com uma pausa depois de encostar o dedo, virava toque longo (menu de
contexto) no convidado lento. Tudo aqui é simulado: aparelho falso e driver falso."""
from __future__ import annotations

from typing import Any

import pytest
from selenium.common.exceptions import WebDriverException

from app.automation.appium_driver import AppiumSession
from app.automation.driver import DriverBusy, DriverError
from app.automation.hierarchy import UiTree, parse_hierarchy
from app.automation.tools import Scroll, ToolContext, TypeText, execute_tool
from app.config import AppiumCfg
from app.metricas import metricas
from app.taskqueue import executor as executor_mod

from .conftest import Harness
from .fake_device import CONTACTS

MSG_UI_OCUPADA = ("An unknown server-side error occurred while processing the command. Original error: Timed out "
                  "after 10000ms waiting for the root AccessibilityNodeInfo in the active window. Make sure the active "
                  "window is not constantly hogging the main UI thread (e.g. the application performs heavy "
                  "animation or there is an in-progress animation)")


def _espiar_recriacao(harness: Harness) -> list[str]:
    """`invalidate_automation` é o gatilho da recriação (DELETE + POST /session). No harness, `ensure_automation`
    devolve True direto (io_factory), então quem prova que NÃO houve recriação é esta contagem."""
    devices = harness.state.devices                                 # type: ignore[union-attr]
    original = devices.invalidate_automation
    vistos: list[str] = []

    def espiao(rt: Any, why: str) -> None:
        vistos.append(why)
        original(rt, why)

    devices.invalidate_automation = espiao                         # type: ignore[method-assign]
    return vistos


# ---------------------------------------------------------------- executor: leitura com UI ocupada
async def test_leitura_com_ui_ocupada_rele_sem_recriar_a_sessao(harness: Harness,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(executor_mod, "RECUO_UI_OCUPADA_S", 0.01, raising=False)
    metricas.limpar()
    recriacoes = _espiar_recriacao(harness)
    fake = harness.fakes["android-01"]
    fake.busy_reads = 2                                            # duas leituras com o 500; a terceira traz a árvore
    run = harness.run(["android-01"])
    detail = await harness.wait_run(run.id)
    assert fake.busy_reads == 0                                    # o 500 chegou de fato ao executor
    assert recriacoes == []                                        # nenhuma sessão derrubada por UI ocupada
    assert metricas.valor("automacao.ui_ocupada", resultado="relida") == 1     # relida, sem gastar volta nem erro
    assert detail.status == "completed" and len(fake.messages) == 1


async def test_ui_ocupada_que_persiste_conta_erro_mas_nao_derruba_a_sessao(harness: Harness,
                                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(executor_mod, "RECUO_UI_OCUPADA_S", 0.01, raising=False)
    metricas.limpar()
    recriacoes = _espiar_recriacao(harness)
    fake = harness.fakes["android-01"]
    fake.busy_reads = 2 * (executor_mod.RELEITURAS_UI_OCUPADA + 1)     # esgota as releituras duas vezes seguidas
    run = harness.run(["android-01"])
    detail = await harness.wait_run(run.id)
    assert fake.busy_reads == 0
    assert metricas.valor("automacao.ui_ocupada", resultado="persistiu") == 2
    assert recriacoes == []
    assert detail.status == "completed" and len(fake.messages) == 1


async def test_sessao_morta_de_verdade_continua_sendo_recriada(harness: Harness) -> None:
    recriacoes = _espiar_recriacao(harness)
    fake = harness.fakes["android-01"]
    fake.session_lost_reads = 1
    run = harness.run(["android-01"])
    detail = await harness.wait_run(run.id)
    assert fake.session_lost_reads == 0
    assert any("session" in r.lower() for r in recriacoes)         # erro de sessão ainda recria
    assert detail.status == "completed" and len(fake.messages) == 1


async def test_toque_com_ui_ocupada_nao_se_repete_e_fica_incerto(harness: Harness,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(executor_mod, "RECUO_UI_OCUPADA_S", 0.01, raising=False)
    recriacoes = _espiar_recriacao(harness)
    fake = harness.fakes["android-01"]
    fake.busy_after_tap = "QA-001"                                 # o toque abre a conversa E volta com o 500
    run = harness.run(["android-01"])
    detail = await harness.wait_run(run.id)
    assert fake.busy_after_tap is None
    y = 200 + CONTACTS.index("QA-001") * 120 + 30
    assert sum(1 for c in fake.calls if c == f"tap:360,{y}") == 1  # o toque NÃO foi repetido
    toques = [a for at in detail.attempts for a in at.actions if a.tool == "tap" and a.error and "AccessibilityNodeInfo" in a.error]
    assert len(toques) == 1 and toques[0].status == "unknown"      # efeito possível: incerto, não "falhou"
    assert recriacoes == []
    assert detail.status == "completed" and len(fake.messages) == 1


# ---------------------------------------------------------------- ferramentas: leitura depois do gesto
def _chamar() -> Any:
    async def call(fn: Any, *a: Any) -> Any:
        return fn(*a)
    return call


async def test_leitura_que_falha_depois_do_gesto_nao_apaga_o_efeito() -> None:
    """Uma leitura que falha DEPOIS do gesto não pode dizer "nada chegou ao aparelho": o texto já foi digitado.
    Com `effect_possible=False`, um `type_text` de commit viraria `fired=False` e poderia ser reenviado."""
    class Io:
        digitado = ""

        def type_text(self, text: str, *, clear_first: bool) -> None:
            self.digitado += text

        # A digitação atômica (pacote "digitacao") define o texto de uma vez; o efeito é o mesmo campo escrito.
        def set_text(self, text: str, *, clear_first: bool) -> None:
            self.type_text(text, clear_first=clear_first)

        def swipe(self, *a: Any) -> None: ...

    async def observe() -> UiTree:
        raise DriverBusy(MSG_UI_OCUPADA, effect_possible=False)

    tree = _grade(linhas=3)
    ctx = ToolContext(io=Io(), call=_chamar(), tree=tree, width=720, height=1280, image_scale=1.0,  # type: ignore[arg-type]
                      app_package=None, app_activity=None, observe=observe)
    with pytest.raises(DriverError) as e1:
        await execute_tool(ctx, "type_text", TypeText(rationale="r", text="oi", is_commit_action=True))
    assert e1.value.effect_possible is True
    with pytest.raises(DriverError) as e2:
        await execute_tool(ctx, "scroll", Scroll(rationale="r", direction="down"))
    assert e2.value.effect_possible is True


# ---------------------------------------------------------------- appium: classificação e gesto
class _Drv:
    """Driver Appium falso: `page_source` e `execute_script` levantam o que o teste mandar; `execute` grava o
    corpo W3C das ações (é o que a sessão real manda ao UiAutomator2)."""

    def __init__(self, erro: Exception | None = None):
        self.erro = erro
        self.enviado: list[dict[str, Any]] = []

    @property
    def page_source(self) -> str:
        if self.erro is not None:
            raise self.erro
        return "<hierarchy/>"

    def execute_script(self, script: str, args: dict[str, Any]) -> None:
        if self.erro is not None:
            raise self.erro

    def execute(self, comando: str, params: dict[str, Any]) -> dict[str, Any]:
        self.enviado.append(params)
        return {"value": None}


def _sessao(drv: _Drv) -> AppiumSession:
    ses = AppiumSession(AppiumCfg(), "127.0.0.1:15555", 8200, 7810, 8000)
    ses._drv = drv
    return ses


def test_appium_classifica_ui_ocupada_e_separa_de_sessao_morta() -> None:
    ocupada = _sessao(_Drv(WebDriverException(MSG_UI_OCUPADA)))
    with pytest.raises(DriverBusy) as leitura:
        ocupada.page_source()
    assert leitura.value.effect_possible is False                  # leitura: nada chegou ao app
    with pytest.raises(DriverBusy) as acao:
        ocupada.tap(10, 10)
    assert acao.value.effect_possible is True                      # ação: o gesto pode ter chegado
    sem_janela = _sessao(_Drv(WebDriverException("An unknown server-side error occurred while processing the "
                                                 "command. Original error: No active window found")))
    with pytest.raises(DriverBusy):
        sem_janela.page_source()
    morta = _sessao(_Drv(WebDriverException("A session is either terminated or not started")))
    with pytest.raises(DriverError) as sessao:
        morta.page_source()
    assert not isinstance(sessao.value, DriverBusy)
    vazia = _sessao(_Drv(RuntimeError("")))                         # mensagem vazia não vira IndexError
    with pytest.raises(DriverError):
        vazia.page_source()


def test_swipe_nao_pausa_depois_de_encostar_e_cruza_o_slop_de_cara() -> None:
    drv = _Drv()
    _sessao(drv).swipe(360, 900, 360, 400, 450)
    toque = next(a for a in drv.enviado[0]["actions"] if a["type"] == "pointer")
    passos = toque["actions"]
    assert [p["type"] for p in passos] == ["pointerMove", "pointerDown", "pointerMove", "pointerMove", "pointerUp"]
    posiciona, _, slop, principal, _ = passos
    assert (posiciona["x"], posiciona["y"], posiciona["duration"]) == (360, 900, 0)    # dedo no ar não espera
    # O primeiro movimento depois de encostar passa do touch slop (8 dp = 16 px em xhdpi; a checagem é estrita)
    # sem esperar: com o convidado lento, dedo parado além de 400 ms é toque longo.
    assert abs(slop["y"] - 900) > 16 and slop["x"] == 360 and slop["duration"] <= 20
    assert (principal["x"], principal["y"], principal["duration"]) == (360, 400, 450)


# ---------------------------------------------------------------- rolagem: área e sobreposição
def _no(cls: str, x1: int, y1: int, x2: int, y2: int, *, package: str = "com.instagram.android", **attrs: str) -> str:
    extra = "".join(f' {k.replace("_", "-")}="{v}"' for k, v in attrs.items())
    return f'<node class="{cls}" package="{package}"{extra} bounds="[{x1},{y1}][{x2},{y2}]"/>'


def _grade(linhas: int, *, rolavel: bool = True, extra: str = "") -> UiTree:
    """Perfil com a grade de miniaturas: o RecyclerView rolável (alto) e as linhas de ~200 px dentro dele."""
    nos = [_no("android.widget.TextView", 0, 60, 720, 120, text="mariana.costa", resource_id="app:id/title")]
    if rolavel:
        nos.append(_no("androidx.recyclerview.widget.RecyclerView", 0, 300, 720, 1200, scrollable="true",
                       resource_id="app:id/grid"))
    for i in range(linhas):
        y = 300 + i * 200
        nos.append(_no("android.widget.LinearLayout", 0, y, 720, y + 200, resource_id="app:id/row"))
        for c in range(3):
            nos.append(_no("android.widget.ImageView", c * 240, y, c * 240 + 238, y + 198, clickable="true",
                           content_desc=f"foto {i}-{c}", resource_id="app:id/thumb"))
    return parse_hierarchy("<hierarchy>" + "".join(nos) + extra + "</hierarchy>")


class _IoGestos:
    def __init__(self) -> None:
        self.swipes: list[tuple[int, int, int, int]] = []
        self.teclas: list[str] = []

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int) -> None:
        self.swipes.append((x1, y1, x2, y2))

    def press_key(self, key: str) -> None:
        self.teclas.append(key)


def _ctx(io: _IoGestos, tree: UiTree, depois: list[UiTree]) -> ToolContext:
    async def observe() -> UiTree:
        return depois.pop(0)
    return ToolContext(io=io, call=_chamar(), tree=tree, width=720, height=1280, image_scale=1.0,  # type: ignore[arg-type]
                       app_package=None, app_activity=None, observe=observe)


async def test_rolagem_em_faixa_estreita_usa_a_area_rolavel_maior() -> None:
    antes = _grade(linhas=4)
    linha = next(e for e in antes.elements if e.resource_id == "app:id/row")     # faixa de 200 px da grade
    io = _IoGestos()
    await execute_tool(_ctx(io, antes, [_grade(linhas=4)]), "scroll",
                       Scroll(rationale="r", direction="down", element_id=linha.id))
    (x1, y1, x2, y2), = io.swipes
    assert y1 - y2 > 500                                           # não os 140 px de dentro da faixa
    assert 300 <= y2 < y1 <= 1200                                  # dentro do RecyclerView, o ancestral rolável
    # Sem ancestral rolável: a área padrão da tela (0,2h–0,85h), nunca a faixa estreita.
    sem = _grade(linhas=4, rolavel=False)
    linha = next(e for e in sem.elements if e.resource_id == "app:id/row")
    io = _IoGestos()
    await execute_tool(_ctx(io, sem, [sem]), "scroll", Scroll(rationale="r", direction="down", element_id=linha.id))
    (_, y1, _, y2), = io.swipes
    assert y1 - y2 > 500 and int(1280 * 0.2) <= y2 < y1 <= int(1280 * 0.85)
    # Carrossel horizontal rolável: estreito na altura, largo no eixo do arrasto — continua sendo ele.
    carrossel = parse_hierarchy("<hierarchy>" + _no("androidx.recyclerview.widget.RecyclerView", 0, 150, 720, 330,
                                                    scrollable="true", resource_id="app:id/stories")
                                + "</hierarchy>")
    io = _IoGestos()
    await execute_tool(_ctx(io, carrossel, [carrossel]), "scroll",
                       Scroll(rationale="r", direction="right", element_id=carrossel.elements[0].id))
    (x1, y1, x2, y2), = io.swipes
    assert 150 <= y1 == y2 <= 330 and x1 > x2
    # Lista curta que rola e não está dentro de nada rolável (folha inferior): é a própria superfície. Arrastar na
    # área da tela começaria fora dela.
    folha = parse_hierarchy("<hierarchy>" + _no("android.widget.ListView", 0, 900, 720, 1260, scrollable="true",
                                                resource_id="app:id/sheet_list") + "</hierarchy>")
    io = _IoGestos()
    await execute_tool(_ctx(io, folha, [folha]), "scroll",
                       Scroll(rationale="r", direction="down", element_id=folha.elements[0].id))
    (_, y1, _, y2), = io.swipes
    assert 900 <= y2 < y1 <= 1260


async def test_rolagem_que_abre_sobreposicao_volta_e_nao_diz_que_rolou() -> None:
    antes = _grade(linhas=4)
    # Toque longo sobre a miniatura (e31953): o menu de contexto vira a janela ativa — a árvore encolhe e o
    # RecyclerView rolado some do dump.
    menu = parse_hierarchy("<hierarchy>"
                           + _no("android.widget.ListView", 100, 500, 620, 800, resource_id="app:id/menu")
                           + _no("android.widget.TextView", 100, 500, 620, 600, text="Denunciar", clickable="true")
                           + _no("android.widget.TextView", 100, 600, 620, 700, text="Copiar link", clickable="true")
                           + "</hierarchy>")
    io = _IoGestos()
    out = await execute_tool(_ctx(io, antes, [menu]), "scroll", Scroll(rationale="r", direction="down"))
    assert io.teclas == ["back"]                                    # fechou sem chamar a IA
    assert out.result["changed"] is False and out.result["at_end"] is False
    # Janela nova de outro pacote (a gaveta de notificações puxada por um arrasto perto do topo): também volta.
    gaveta = _grade(linhas=4, extra=_no("android.widget.FrameLayout", 0, 0, 720, 400, package="com.android.systemui",
                                         resource_id="com.android.systemui:id/notification_panel"))
    io = _IoGestos()
    out = await execute_tool(_ctx(io, antes, [gaveta]), "scroll", Scroll(rationale="r", direction="up"))
    assert io.teclas == ["back"] and out.result["changed"] is False
    # Rolagem de verdade que encolhe a árvore mas mantém o RecyclerView: NÃO é sobreposição — nada de voltar.
    io = _IoGestos()
    out = await execute_tool(_ctx(io, antes, [_grade(linhas=1)]), "scroll", Scroll(rationale="r", direction="down"))
    assert io.teclas == [] and out.result["changed"] is True
    # Tela ainda vazia antes (carregando): o pacote que aparece depois não é "janela nova".
    io = _IoGestos()
    out = await execute_tool(_ctx(io, parse_hierarchy("<hierarchy/>"), [antes]), "scroll",
                             Scroll(rationale="r", direction="down"))
    assert io.teclas == [] and "overlay_dismissed" not in out.result
