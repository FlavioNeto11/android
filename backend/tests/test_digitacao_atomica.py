"""Digitação atômica e conferência no campo do Instagram (execuções r-20260928165254-e31953 e
r-20260928195344-02ee9e, android-06 com 2 vCPU saturadas).

O que aconteceu de verdade: `mobile: type` troca o IME para o Unicode IME e roda `adb shell input text`; num convidado
saturado, passada a janela de cada chamada, a cauda da fila de teclas se perde — o compositor de comentário ficou com
47 e depois 22 dos 125 caracteres, e o resultado dizia `typed_chars=125`. A conferência que devia pegar isso era
inerte: o compositor é `AutoCompleteTextView`, e só `EditText` contava como editável, então o campo não era achado e
a conferência saía `verified=None` — que o executor não distingue de sucesso.

Aqui o aparelho é um WebDriver FALSO atrás das classes REAIS (`AppiumSession`/`AndroidDeviceIO`): o que se prova é
o caminho do código até o comando do Appium, não o aparelho (prova `simulated`).
"""
from __future__ import annotations

from xml.sax.saxutils import quoteattr

import pytest
from selenium.common.exceptions import InvalidElementStateException, NoSuchElementException

from app.automation.appium_driver import AndroidDeviceIO, AppiumSession
from app.automation.driver import DriverError
from app.automation.hierarchy import UiTree, parse_hierarchy
from app.automation.tools import ToolContext, ToolOutcome, execute_tool, validate_call
from app.config import AppiumCfg

from .test_sensitive_input import SECRET, apply_appium_rules

IG = "com.instagram.android"
ID_COMPOSITOR = f"{IG}:id/layout_comment_thread_edittext"
DICA = "Adicione um comentário para ana.teste..."
# 125 caracteres, como o comentário da execução e31953.
COMENTARIO = ("rapaz, que tema importante esse do Setembro Amarelo, parabéns pelo cuidado no post e por falar "
              "disso com toda a calma, valeu!")


def test_o_comentario_tem_o_tamanho_do_caso_real() -> None:
    assert len(COMENTARIO) == 125


# ------------------------------------------------------------------ o aparelho falso, visto pelo UiAutomator2
class _Campo:
    """O elemento em foco devolvido por `switch_to.active_element`."""

    def __init__(self, driver: "WebDriverFalso") -> None:
        self._d = driver
        self.id = "00000000-0000-0010-ffff-ffff0000001a"

    @property
    def tag_name(self) -> str:
        return self._d.classe_em_foco

    def clear(self) -> None:
        self._d.campo = ""

    def send_keys(self, *valor: str) -> None:
        # WebDriver `setValue` = ACTION_SET_TEXT com "texto atual + novo", feito pelo servidor do UiAutomator2.
        self._d.definicoes.append("send_keys")
        self._d.campo += "".join(valor)


class _Troca:
    def __init__(self, driver: "WebDriverFalso") -> None:
        self._d = driver

    @property
    def active_element(self) -> _Campo:
        if not self._d.foco_exposto:
            raise NoSuchElementException("An element could not be located on the page using the given search")
        return _Campo(self._d)


class WebDriverFalso:
    """O compositor de comentário do Instagram atrás do UiAutomator2.

    `teclas_por_chamada`: quantos caracteres de cada `mobile: type` chegam ao campo antes de a cauda da fila se perder
    (C5). `aplica_no_set`: quantos caracteres cada `replaceElementValue` deixa no campo, em ordem (lista vazia = tudo);
    o último vale para as chamadas seguintes. `set_recusa`: o servidor responde que o elemento não aceitou o texto.
    """

    session_id = "sessao-falsa"

    def __init__(self, *, teclas_por_chamada: int = 47, aplica_no_set: tuple[int, ...] = (),
                 foco_exposto: bool = True, set_recusa: bool = False,
                 classe_em_foco: str = "android.widget.AutoCompleteTextView") -> None:
        self.classe_em_foco = classe_em_foco
        self.campo = ""
        self.foco_exposto = foco_exposto
        self.teclas_por_chamada = teclas_por_chamada
        self.aplica_no_set = list(aplica_no_set)
        self.set_recusa = set_recusa
        self.scripts: list[str] = []
        self.teclado: list[str] = []            # o que cada `mobile: type` pediu
        self.definicoes: list[str] = []         # cada escrita pelo campo (ACTION_SET_TEXT)
        self.switch_to = _Troca(self)

    def execute_script(self, script: str, args: dict[str, object]) -> None:
        self.scripts.append(script)
        if script == "mobile: type":
            texto = str(args["text"])
            self.teclado.append(texto)
            self.campo += texto[: self.teclas_por_chamada]
        elif script == "mobile: replaceElementValue":
            texto = str(args["text"])
            self.definicoes.append("replace")
            if self.set_recusa:
                # A mensagem REAL do servidor (appium-uiautomator2-server 10.6.6) carrega o texto inteiro.
                raise InvalidElementStateException(
                    f"Cannot set the element to '{texto}'. Did you interact with the correct element?")
            corte = (self.aplica_no_set.pop(0) if len(self.aplica_no_set) > 1
                     else self.aplica_no_set[0] if self.aplica_no_set else len(texto))
            self.campo = texto[:corte]

    @property
    def page_source(self) -> str:
        texto = self.campo or DICA
        return (
            "<hierarchy rotation=\"0\">"
            f'<android.widget.TextView class="android.widget.TextView" package="{IG}" '
            'text="ana.teste Setembro Amarelo: falar é a melhor saída" resource-id="" clickable="false" '
            'enabled="true" focused="false" bounds="[24,300][696,380]" />'
            f'<android.widget.AutoCompleteTextView class="android.widget.AutoCompleteTextView" package="{IG}" '
            f'text={quoteattr(texto)} resource-id="{ID_COMPOSITOR}" clickable="true" enabled="true" '
            f'focused="{str(self.foco_exposto).lower()}" password="false" bounds="[96,1180][560,1240]" />'
            f'<android.widget.TextView class="android.widget.TextView" package="{IG}" text="Postar" '
            f'resource-id="{IG}:id/layout_comment_thread_post_button_click_area" clickable="true" enabled="true" '
            'focused="false" bounds="[580,1180][700,1240]" />'
            "</hierarchy>")


def _io(driver: WebDriverFalso) -> AndroidDeviceIO:
    sessao = AppiumSession(AppiumCfg(), "emulator-5640", 8201, 9201, 9601)
    sessao._drv = driver
    return AndroidDeviceIO(None, sessao)  # type: ignore[arg-type] - o adb só serve a captura, que não entra aqui


def _contexto(io: AndroidDeviceIO, *, observar: bool = True) -> ToolContext:
    async def call(fn, *a):  # type: ignore[no-untyped-def]
        return fn(*a)

    async def observe() -> UiTree:
        return parse_hierarchy(io.page_source())

    return ToolContext(io=io, call=call, tree=parse_hierarchy(io.page_source()), width=720, height=1280,
                       image_scale=1.0, app_package=IG, app_activity=None, allowed_packages={IG},
                       observe=observe if observar else None)


async def _digitar(ctx: ToolContext, texto: str, *, alvo: bool = True, enter: bool = False) -> ToolOutcome:
    compositor = next((e for e in ctx.tree.elements if e.resource_id == ID_COMPOSITOR), None)
    return await execute_tool(ctx, "type_text", validate_call("type_text", {
        "rationale": "comentar", "text": texto, "element_id": compositor.id if (alvo and compositor) else None,
        "clear_first": True, "press_enter": enter, "is_commit_action": False}))


# ------------------------------------------------------------------ a causa (C5) e a correção
async def test_comentario_no_autocomplete_do_instagram_entra_inteiro_e_e_conferido() -> None:
    """O caso real: 125 caracteres num compositor `AutoCompleteTextView` de um convidado que perde a cauda da fila
    de teclas. O texto tem de entrar de uma vez pelo campo (ACTION_SET_TEXT) e a conferência tem de ACHAR o campo."""
    driver = WebDriverFalso(teclas_por_chamada=47)
    saida = await _digitar(_contexto(_io(driver)), COMENTARIO)

    assert driver.campo == COMENTARIO                              # relido: o texto inteiro, nada cortado
    assert "mobile: type" not in driver.scripts                     # sem trocar de IME, sem fila de teclas
    assert driver.scripts.count("mobile: replaceElementValue") == 1
    assert saida.result["verified"] is True
    assert saida.result["typed_chars"] == len(driver.campo) == 125  # o que está NO campo, não len(text)


async def test_o_teclado_so_entra_sem_campo_em_foco_e_em_pedacos_de_ate_20() -> None:
    """Alternativa, não caminho: sem elemento em foco onde definir o texto, a digitação vai pelo teclado — em pedaços
    que cabem na janela de cada chamada (aqui cabem 47 por chamada; o texto de 125 inteiro só entraria cortado)."""
    driver = WebDriverFalso(teclas_por_chamada=47, foco_exposto=False)
    saida = await _digitar(_contexto(_io(driver)), COMENTARIO)

    assert driver.definicoes == []                                  # não havia campo: nada foi definido por ele
    assert driver.teclado and all(len(p) <= 20 for p in driver.teclado)
    assert "".join(driver.teclado) == COMENTARIO and driver.campo == COMENTARIO
    assert saida.result["verified"] is True and saida.result["typed_chars"] == 125


async def test_completacao_reaplica_o_set_text_e_nunca_chama_mobile_type() -> None:
    """O campo relido não tem tudo (filtro do app, escrita tardia): a completação REAPLICA a definição do texto
    inteiro — idempotente, não duplica mesmo que a primeira escrita chegue atrasada — e nunca redigita pelo teclado,
    que é justamente o caminho que corta."""
    driver = WebDriverFalso(aplica_no_set=(22, 125))
    saida = await _digitar(_contexto(_io(driver)), COMENTARIO)

    assert "mobile: type" not in driver.scripts
    assert driver.definicoes == ["replace", "replace"]
    assert driver.campo == COMENTARIO                               # inteiro e sem duplicar
    assert saida.result["verified"] is True and saida.result["completed_after_cut"] == 1
    assert saida.result["typed_chars"] == 125


async def test_completacao_com_limpeza_nao_transforma_a_dica_em_conteudo() -> None:
    """O compositor vazio mostra a DICA como texto ("…ana.teste..."), e comentário que começa com "." é hábito no
    Instagram (".@fulano"). Se a primeira escrita não chegou, o fim da dica casa com o começo do texto; com limpeza
    pedida, o campo tem de terminar com o texto exato — nunca com a dica colada na frente."""
    texto = ".@ana.teste que post necessário"
    driver = WebDriverFalso(aplica_no_set=(0, len(texto)))
    saida = await _digitar(_contexto(_io(driver)), texto)
    assert driver.campo == texto and saida.result["verified"] is True


async def test_campo_que_nao_muda_depois_de_reaplicar_encerra_com_o_motivo() -> None:
    """Sob pressão, insistir só gasta o prazo da etapa (e31953 redigitou até estourá-lo). Reaplicou e o campo não
    mudou: encerra dizendo por quê, com o que DE FATO está no campo, e sem Enter."""
    driver = WebDriverFalso(aplica_no_set=(22,))
    saida = await _digitar(_contexto(_io(driver)), COMENTARIO, enter=True)

    assert driver.definicoes == ["replace", "replace"]              # uma reaplicação, não o teto inteiro
    assert "mobile: type" not in driver.scripts and "mobile: pressKey" not in driver.scripts
    assert saida.result["verified"] is False and saida.result["enter"] is False
    assert saida.result["typed_chars"] == 22 and saida.result["missing"] == COMENTARIO[22:][:80]
    assert "reason" in saida.result and saida.result["reason"]


async def test_set_text_recusado_no_campo_nao_cai_no_teclado_nem_vaza_o_texto() -> None:
    """ACTION_SET_TEXT passa pela thread de interface do app: num convidado saturado o servidor desiste (devolve
    'não aceitou') e a ação ainda pode ser aplicada depois. Cair no teclado aí DUPLICARIA o texto. Então é efeito
    possível, não alternativa — e a mensagem do servidor, que traz o texto inteiro, não segue adiante."""
    driver = WebDriverFalso(set_recusa=True)
    with pytest.raises(DriverError) as exc:
        await _digitar(_contexto(_io(driver)), COMENTARIO)
    assert exc.value.effect_possible is True
    assert "mobile: type" not in driver.scripts
    assert COMENTARIO not in str(exc.value) and COMENTARIO[:40] not in str(exc.value)


async def test_foco_num_elemento_que_nao_recebe_texto_cai_no_teclado() -> None:
    """A outra recusa: o foco está num contêiner, que não aceita ACTION_SET_TEXT — nada foi escrito, e o teclado
    (em pedaços) é a alternativa segura."""
    driver = WebDriverFalso(set_recusa=True, classe_em_foco="android.widget.FrameLayout")
    saida = await _digitar(_contexto(_io(driver)), COMENTARIO)
    assert saida.result["via"] == "keyboard" and all(len(p) <= 20 for p in driver.teclado)
    assert driver.campo == COMENTARIO and saida.result["verified"] is True


def test_set_text_sem_limpar_acrescenta_pelo_proprio_campo() -> None:
    driver = WebDriverFalso()
    driver.campo = "@ana.teste "
    _io(driver).set_text("valeu!", clear_first=False)
    assert driver.campo == "@ana.teste valeu!" and driver.definicoes == ["send_keys"]
    assert "mobile: type" not in driver.scripts


# ------------------------------------------------------------------ (d): nunca `None` silencioso
class _IoSemCampo:
    """Aparelho cuja tela não mostra um campo identificável (dois campos, nenhum em foco, sem alvo)."""

    def __init__(self) -> None:
        self.escritas: list[str] = []
        self.teclas: list[str] = []

    def page_source(self) -> str:
        return ("<hierarchy>"
                f'<node class="android.widget.EditText" package="{IG}" text="" resource-id="{IG}:id/a" '
                'enabled="true" focused="false" bounds="[0,100][720,160]" />'
                f'<node class="android.widget.EditText" package="{IG}" text="" resource-id="{IG}:id/b" '
                'enabled="true" focused="false" bounds="[0,200][720,260]" />'
                "</hierarchy>")

    def set_text(self, text: str, *, clear_first: bool) -> None:
        self.escritas.append(text)

    def type_text(self, text: str, *, clear_first: bool) -> None:
        self.escritas.append(text)

    def press_key(self, key: str) -> None:
        self.teclas.append(key)


async def test_campo_nao_identificado_e_verified_false_e_nao_aperta_enter() -> None:
    io = _IoSemCampo()

    async def call(fn, *a):  # type: ignore[no-untyped-def]
        return fn(*a)

    async def observe() -> UiTree:
        return parse_hierarchy(io.page_source())

    ctx = ToolContext(io=io, call=call, tree=parse_hierarchy(io.page_source()),  # type: ignore[arg-type]
                      width=720, height=1280, image_scale=1.0, app_package=IG, app_activity=None,
                      allowed_packages={IG}, observe=observe)
    saida = await _digitar(ctx, "bom dia", alvo=False, enter=True)
    assert saida.result["verified"] is False                        # não é None: incerteza não passa por sucesso
    assert saida.result["typed_chars"] == 0 and saida.result["reason"]
    assert saida.result["enter"] is False and io.teclas == []
    assert io.escritas == ["bom dia"]                               # não insistiu num campo que não sabe qual é


async def test_sem_leitura_de_tela_tambem_nao_afirma_o_que_entrou() -> None:
    driver = WebDriverFalso()
    saida = await _digitar(_contexto(_io(driver), observar=False), "bom dia")
    assert saida.result["verified"] is False and saida.result["typed_chars"] == 0 and saida.result["reason"]


# ------------------------------------------------------------------ (b): AutoCompleteTextView é campo de texto
def _tela(*nos: str) -> str:
    return "<hierarchy>" + "".join(nos) + "</hierarchy>"


def _no(cls: str, *, text: str = "", rid: str = "", y: int = 0) -> str:
    return (f'<node class="{cls}" package="{IG}" text={quoteattr(text)} resource-id="{rid}" enabled="true" '
            f'bounds="[0,{y}][720,{y + 60}]" />')


@pytest.mark.parametrize("classe", ["android.widget.AutoCompleteTextView", "android.widget.MultiAutoCompleteTextView",
                                    "android.widget.EditText"])
def test_compositores_com_sugestao_sao_editaveis(classe: str) -> None:
    arvore = parse_hierarchy(_tela(_no(classe, rid=ID_COMPOSITOR)))
    assert arvore.elements[0].editable is True


def test_texto_ainda_no_compositor_nao_prova_envio() -> None:
    """Efeito em `sent_as_message`: com o compositor fora dos editáveis, o texto que AINDA ESTÁ no campo contava
    como mensagem publicada — prova de envio falsa. Agora ele é o campo: texto nele = não saiu."""
    so_no_campo = parse_hierarchy(_tela(_no("android.widget.AutoCompleteTextView", text="oi, tudo bem?",
                                            rid=ID_COMPOSITOR, y=1180)))
    assert so_no_campo.sent_as_message("oi, tudo bem?") is False

    publicado = parse_hierarchy(_tela(_no("android.widget.TextView", text="oi, tudo bem?", y=600),
                                      _no("android.widget.AutoCompleteTextView", text=DICA, rid=ID_COMPOSITOR, y=1180)))
    assert publicado.sent_as_message("oi, tudo bem?") is True


def test_tela_de_desafio_com_campo_autocomplete_e_sensivel() -> None:
    """Sem afrouxar: onde há onde digitar numa tela de desafio, ela é sensível — o campo de código com sugestão
    também conta como lugar de digitar."""
    tela = parse_hierarchy(_tela(_no("android.widget.TextView", text="Insira o código de segurança que enviamos"),
                                 _no("android.widget.AutoCompleteTextView", rid=f"{IG}:id/code", y=300)))
    assert tela.sensitive is True and tela.sensitive_reason and "desafio" in tela.sensitive_reason


# ------------------------------------------------------------------ (e): o texto do replaceElementValue não vai ao log
@pytest.mark.parametrize("valor", [SECRET, 'ela disse \\"oi\\" e saiu', "comentário com acento e emoji 🎗️"])
def test_payload_do_replace_element_value_e_mascarado_no_log_do_appium(valor: str) -> None:
    linha = ('[HTTP] --> POST /session/abc/execute/sync {"script":"mobile: replaceElementValue","args":'
             '[{"elementId":"00000000-0000-0010-ffff-ffff0000001a","text":"' + valor + '"}]}')
    mascarada = apply_appium_rules(linha)
    assert "**SECURE**" in mascarada
    assert valor not in mascarada and "oi" not in mascarada.split("replaceElementValue", 1)[1]
