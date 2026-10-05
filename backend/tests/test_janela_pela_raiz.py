"""31.77: da leitura do delta do #391 (31.73).

- K2: uma fração só, pública (`dialogos.FRACAO_DA_PAGINA`), para a página do 31.72 e a janela do 31.73.
- L2-a: a janela flutuante se decide pela JANELA do dump (a união dos nós de topo, `UiTree.janela`), não pela extensão
  das folhas que o leitor deixou: a página esparsa sem ids (Compose, Flutter) fica com folhas abaixo de 60 % da tela e
  passaria por janela.

Prova `simulated` (dumps sintéticos no formato do Appium, aninhados). O real fica `not_run` até um dump BRUTO de um
diálogo nativo e de uma página esparsa (a rota `/hierarchy` devolve só as folhas, sem a raiz).
"""

from __future__ import annotations

import dataclasses

from app.automation.hierarchy import parse_hierarchy
from app.taskqueue import dialogos, executor
from app.taskqueue.executor import sobreposicao_vale


def _no(texto: str, b: tuple[int, int, int, int], *, classe: str = "android.widget.TextView", rid: str = "",
        pkg: str = "com.exemplo.app", filhos: str = "") -> str:
    attrs = (f'text="{texto}" resource-id="{rid}" class="{classe}" package="{pkg}" content-desc="" clickable="false" '
             f'enabled="true" bounds="[{b[0]},{b[1]}][{b[2]},{b[3]}]"')
    return f"<node {attrs}>{filhos}</node>" if filhos else f"<node {attrs} />"


def _dump(*topo: str) -> str:
    return '<hierarchy index="0" class="hierarchy" rotation="0" width="720" height="1280">' + "".join(topo) + "</hierarchy>"


def _id(tree, texto: str) -> str:
    return next(e.id for e in tree.elements if e.text == texto)


#: A página esparsa: a raiz da janela é a tela inteira, e o leitor só guarda três folhas com texto, sem id, que somam
#: bem menos de 60 % da tela (como numa tela Compose ou Flutter, que não expõe contêiner com id).
_FOLHAS_ESPARSAS = (_no("Ofertas do dia", (40, 100, 680, 160)) + _no("Frete grátis hoje", (40, 180, 680, 240))
                    + _no("Abra o app e ganhe desconto", (40, 260, 680, 320)))
_PAGINA_ESPARSA = _dump(_no("", (0, 0, 720, 1280), classe="android.widget.FrameLayout",
                            filhos=_no("", (0, 48, 720, 1280), classe="android.view.View", filhos=_FOLHAS_ESPARSAS)))


def test_k2_a_fracao_e_uma_so_e_publica() -> None:
    assert executor.FRACAO_DA_PAGINA is dialogos.FRACAO_DA_PAGINA and dialogos.FRACAO_DA_PAGINA == 0.6
    assert not hasattr(executor, "_FRACAO_DA_JANELA") and not hasattr(dialogos, "_FRACAO_DA_PAGINA")


def test_a_janela_e_a_raiz_do_dump_e_nao_as_folhas() -> None:
    tree = parse_hierarchy(_PAGINA_ESPARSA)
    assert tree.janela == (0, 0, 720, 1280)
    assert [e.text for e in tree.elements] == ["Ofertas do dia", "Frete grátis hoje", "Abra o app e ganhe desconto"]


def test_l2a_a_pagina_esparsa_nao_passa_por_janela() -> None:
    """O caso que a extensão das folhas errava: as folhas cobrem 640 x 220 (15 % da tela) e o L2 antigo dava a recusa
    do juiz como válida. Com a janela da raiz (a tela inteira), o L2 não decide; o banner no fluxo, sem caixa e sem
    nada que cruze, não vale."""
    tree = parse_hierarchy(_PAGINA_ESPARSA)
    assert sobreposicao_vale(tree, _id(tree, "Abra o app e ganhe desconto"), 720, 1280) is False


def test_l2a_o_dialogo_nativo_aninhado_vale_pela_janela() -> None:
    """O dump do Appium é só a janela ativa: no diálogo nativo, a raiz é o painel do diálogo (aqui 640 x 340, 24 %)."""
    filhos = (_no("", (40, 480, 680, 820), classe="android.widget.LinearLayout", rid="android:id/parentPanel",
                  pkg="android",
                  filhos=_no("Permitir acesso à localização?", (88, 520, 632, 580), rid="android:id/alertTitle",
                             pkg="android")
                  + _no("OK", (480, 740, 632, 800), classe="android.widget.Button", rid="android:id/button1",
                        pkg="android")))
    tree = parse_hierarchy(_dump(_no("", (40, 480, 680, 820), classe="android.widget.FrameLayout", pkg="android",
                                     filhos=filhos)))
    assert tree.janela == (40, 480, 680, 820)
    assert sobreposicao_vale(tree, _id(tree, "Permitir acesso à localização?"), 720, 1280) is True


def test_l2a_a_barra_do_sistema_como_janela_propria_nao_infla_a_janela() -> None:
    """Se um dump trouxer a barra de status como nó de topo (`com.android.systemui`), ela não entra na união: senão o
    diálogo nunca pareceria janela."""
    barra = _no("", (0, 0, 720, 48), classe="android.widget.FrameLayout", pkg="com.android.systemui",
                filhos=_no("14:52", (16, 8, 96, 40), pkg="com.android.systemui"))
    dialogo = _no("", (40, 480, 680, 820), classe="android.widget.FrameLayout", pkg="android",
                  filhos=_no("Permitir acesso à localização?", (88, 520, 632, 580), pkg="android"))
    tree = parse_hierarchy(_dump(barra, dialogo))
    assert tree.janela == (40, 480, 680, 820)


def test_sem_a_janela_o_l2_nao_decide() -> None:
    """Árvore montada fora de `parse_hierarchy` (`janela=None`): o L2 não cai de volta na extensão das folhas, que é o
    erro que ele corrige; quem decide são as outras regras (aqui, nada cobre: não vale)."""
    tree = dataclasses.replace(parse_hierarchy(_PAGINA_ESPARSA), janela=None)
    assert sobreposicao_vale(tree, _id(tree, "Abra o app e ganhe desconto"), 720, 1280) is False


def test_dump_ilegivel_fica_sem_janela() -> None:
    assert parse_hierarchy("<hierarchy").janela is None
    assert parse_hierarchy('<hierarchy rotation="0"></hierarchy>').janela is None
