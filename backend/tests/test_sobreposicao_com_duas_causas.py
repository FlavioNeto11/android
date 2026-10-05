"""Item 31.73: a recusa por `sobreposicao` só vale quando o que o juiz citou esconde o alvo de verdade.

Achado real (r-20261005071303-f24955, Chrome, android-09, 05/10): o juiz marcou `sobreposicao` citando o banner "Abra
o app e ganhe frete grátis" ([0,160][720,308], 11,6 % da tela 720 x 1280), mas o próprio texto dele dizia que a causa
principal era o conteúdo errado. A limpeza foi inserida à toa, falhou, e o desfecho escondeu a causa. Na
r-20261004190200-5b56e6 o mesmo banner era modal ([0,160][720,1232], 83,8 %) e cobria de fato.

Duas camadas: o prompt do juiz (o que está VISÍVEL fora do aviso mostra outra causa = false) e a regra estrutural
(`executor.sobreposicao_vale`): o citado cobre 15 % da tela, ou a caixa com pista que o contém (o "X" de um modal),
ou outra folha com texto cruza a área (a faixa FIXA sobre o conteúdo). As árvores "reais" abaixo são recortes das
capturas de 05/10 no android-09 (`data/diag-31-72/ml-1-topo.json` e `gov-3-rodape.json`), na mesma ordem do documento.
Nível de prova: `simulated`.
"""
from __future__ import annotations

import json
from typing import Any

from app.automation.hierarchy import parse_hierarchy
from app.metricas import metricas
from app.planning import prompts
from app.planning.provider import Usage, Verdict
from app.taskqueue.dialogos import FRACAO_QUE_COBRE
from app.taskqueue.executor import FRACAO_DA_SOBREPOSICAO, sobreposicao_vale

from .conftest import Harness
from .fake_device import Node
from .test_sobreposicao import TERMINAIS, _juiz_com_ref, _limpeza


def _no(texto: str, b: tuple[int, int, int, int], *, rid: str = "", classe: str = "android.view.View",
        clicavel: bool = False) -> str:
    return (f'<node index="0" text="{texto}" resource-id="{rid}" class="{classe}" package="com.android.chrome" '
            f'content-desc="" clickable="{str(clicavel).lower()}" enabled="true" '
            f'bounds="[{b[0]},{b[1]}][{b[2]},{b[3]}]" />')


def _arvore(*nos: str) -> Any:
    return parse_hierarchy('<hierarchy rotation="0">' + "".join(nos) + "</hierarchy>")


def _id(tree: Any, texto: str) -> str:
    return next(e.id for e in tree.elements if texto in (e.text, e.resource_id))


#: ml-1-topo: a página, o banner, os filhos dele (o texto e o "Abrir"), e o 1º texto da página logo abaixo. A barra do
#: Chrome encosta 2 px no banner, sem texto.
REAL_F24955 = _arvore(
    _no("", (0, 48, 720, 162), rid="com.android.chrome:id/control_container", classe="android.widget.FrameLayout"),
    _no("Raspberry Pi 5 8gb | Mercado Livre", (0, 160, 720, 1232), classe="android.webkit.WebView"),
    _no("Abra o app e ganhe frete grátis na sua primeira compra", (0, 160, 720, 308)),
    _no("Frete", (168, 258, 248, 294), classe="android.widget.TextView"),
    _no("Abrir", (578, 202, 688, 266), rid="download-app-top-banner-button", clicavel=True),
    _no("Mercado Livre Brasil - Onde comprar e vender de Tudo", (20, 324, 108, 388), clicavel=True),
)


def test_a_constante_e_uma_so() -> None:
    assert FRACAO_DA_SOBREPOSICAO is FRACAO_QUE_COBRE and FRACAO_QUE_COBRE == 0.15


def test_o_banner_real_da_f24955_no_fluxo_nao_vale() -> None:
    """Nada da página cruza o banner do topo além do ancestral, dos filhos (que vêm logo depois dele no documento) e de
    2 px da barra do Chrome, sem texto: é faixa no fluxo, e a recusa vale como "não" comum."""
    ref = _id(REAL_F24955, "Abra o app e ganhe frete grátis na sua primeira compra")
    assert sobreposicao_vale(REAL_F24955, ref, 720, 1280) is False


def test_o_modal_da_5b56e6_vale() -> None:
    tree = _arvore(_no("Abra o app e ganhe frete grátis na sua primeira compra", (0, 160, 720, 1232)))
    assert sobreposicao_vale(tree, tree.elements[0].id, 720, 1280) is True


def test_os_15_por_cento_exatos() -> None:
    """720 x 192 = 15 % de 720 x 1280: vale; um pixel a menos de altura, não (sem mais nada na tela)."""
    t192 = _arvore(_no("Aviso", (0, 1000, 720, 1192)))
    t191 = _arvore(_no("Aviso", (0, 1000, 720, 1191)))
    assert sobreposicao_vale(t192, _id(t192, "Aviso"), 720, 1280) is True
    assert sobreposicao_vale(t191, _id(t191, "Aviso"), 720, 1280) is False


def test_j1_o_x_dentro_de_um_modal_vale_pela_caixa() -> None:
    """O prompt manda citar "o diálogo, o banner ou o botão de fechar dele": o "X" de 48 x 48 (0,25 %) dentro de um
    modal de 60 % é sobreposição de verdade. Sem a pista na caixa, o "X" sozinho não vale."""
    tree = _arvore(_no("Aviso de cookies", (0, 200, 720, 968), rid="cookie_modal", classe="android.app.Dialog"),
                   _no("", (660, 210, 708, 258), rid="fechar", clicavel=True))
    assert sobreposicao_vale(tree, _id(tree, "fechar"), 720, 1280) is True
    sem_pista = _arvore(_no("Bloco da página", (0, 200, 720, 968), rid="bloco"),
                        _no("", (660, 210, 708, 258), rid="fechar", clicavel=True))
    assert sobreposicao_vale(sem_pista, _id(sem_pista, "fechar"), 720, 1280) is False


def test_j2_sem_citado_na_arvore_vale_como_antes() -> None:
    """A heurística de `cobertura_na_arvore` (o 1º com pista, um `cookie_icon` pequeno) não decide."""
    tree = _arvore(_no("", (10, 10, 40, 40), rid="cookie_icon", clicavel=True))
    assert sobreposicao_vale(tree, None, 720, 1280) is True
    assert sobreposicao_vale(tree, "e99", 720, 1280) is True
    assert sobreposicao_vale(tree, _id(tree, "cookie_icon"), 0, 0) is True     # sem o tamanho da tela, como antes


def test_j3_a_faixa_fixa_sobre_a_mensagem_vale() -> None:
    """O aviso FIXO no rodapé (10 %) por cima da última mensagem, que passa da faixa: outra folha com texto cruza."""
    tree = _arvore(_no("Oi, tudo certo?", (20, 1080, 500, 1150), classe="android.widget.TextView"),
                   _no("Usamos cookies", (0, 1104, 720, 1232), rid="cookie-bar"))
    assert sobreposicao_vale(tree, _id(tree, "Usamos cookies"), 720, 1280) is True


def test_j3_a_linha_da_pagina_inteira_dentro_da_faixa_vale_pela_ordem() -> None:
    """Achado da medida no gov.br (05/10, `gov-3-rodape`): as linhas da página por baixo do aviso fixo ficam INTEIRAS
    dentro da área dele, e a árvore não diz quem é filho de quem. A ordem do documento diz: os filhos do aviso vêm em
    sequência logo depois dele; a linha da página veio antes. Barra pequena (10 %), para o J3 decidir sozinho."""
    pagina = _arvore(_no("Viagens e Turismo", (132, 1150, 410, 1190), classe="android.widget.TextView"),
                     _no("Usamos cookies", (0, 1104, 720, 1232), rid="cookie-bar"),
                     _no("Aceitar", (560, 1150, 700, 1200), clicavel=True))
    assert sobreposicao_vale(pagina, _id(pagina, "Usamos cookies"), 720, 1280) is True
    so_filhos = _arvore(_no("Usamos cookies", (0, 1104, 720, 1232), rid="cookie-bar"),
                        _no("Viagens e Turismo", (132, 1150, 410, 1190), classe="android.widget.TextView"),
                        _no("Aceitar", (560, 1150, 700, 1200), clicavel=True))
    assert sobreposicao_vale(so_filhos, _id(so_filhos, "Usamos cookies"), 720, 1280) is False


def test_o_prompt_do_juiz_separa_o_visivel_do_escondido() -> None:
    assert "o que está VISÍVEL fora do aviso já mostra outra causa" in prompts.VERIFIER_SYSTEM
    assert "O que está só escondido pelo aviso não é outra causa." in prompts.VERIFIER_SYSTEM


# ------------------------------------------------------------------------------------------------- pelo laço
def _juiz_citando(inner: Any, chave: str, escolher: Any) -> None:
    """O juiz da etapa `chave` diz uma vez "não, algo cobre", citando o elemento que `escolher(tree)` devolve."""
    verify0 = inner.verify
    vezes: dict[str, int] = {}

    async def verify(req: Any) -> Any:
        vezes[req.ctx.step_key] = vezes.get(req.ctx.step_key, 0) + 1
        if req.ctx.step_key == chave and vezes[chave] == 1:
            return Verdict(satisfied="no", evidence="[simulado] um aviso cobre; e o conteúdo é outro",
                           sobreposicao=True, cobre=escolher(req.screen.tree).id), Usage()
        return await verify0(req)

    inner.verify = verify


def _area(e: Any) -> int:
    return (e.bounds[2] - e.bounds[0]) * (e.bounds[3] - e.bounds[1])


async def _rodar(harness: Harness, escolher: Any) -> Any:
    harness.pular_o_tempo()
    harness.encurtar_verificacao(1.5)
    _juiz_citando(harness.ai.inner, "verify_sent", escolher)
    run = harness.run(["android-01"])
    await harness.wait_run(run.id, statuses=TERMINAIS)
    return _limpeza(harness, run.id)


async def test_pelo_laco_elemento_pequeno_no_fluxo_nao_insere_a_limpeza_e_conta(harness: Harness) -> None:
    """O juiz cita o MENOR elemento da tela do app de teste (nada acima de 4,7 %): a recusa vale como "não" comum, sem
    limpeza, e a métrica conta."""
    metricas.limpar()
    limpeza = await _rodar(harness, lambda tree: min(tree.elements, key=_area))
    assert limpeza is None
    assert metricas.valor("juiz.sobreposicao_descartada", motivo="cobertura_pequena") == 1


async def test_pelo_laco_aviso_que_cobre_segue_inserindo_a_limpeza_sem_contar(harness: Harness) -> None:
    """O juiz cita um aviso que cobre 62,5 % da tela (`AVISO_QUE_COBRE`): a sobreposição vale, como no 31.40."""
    metricas.limpar()
    harness.pular_o_tempo()
    harness.encurtar_verificacao(1.5)
    _juiz_com_ref(harness, "verify_sent", cobertas=1)
    run = harness.run(["android-01"])
    await harness.wait_run(run.id, statuses=TERMINAIS)
    assert _limpeza(harness, run.id) is not None
    assert metricas.valor("juiz.sobreposicao_descartada", motivo="cobertura_pequena") == 0


async def test_pelo_laco_j1_o_x_do_modal_insere_a_limpeza(harness: Harness) -> None:
    """O juiz cita o "X" (48 x 48) de um modal de 60 % posto na tela do aparelho falso: a limpeza é inserida, e a
    `Cobertura` dela é o próprio "X"."""
    fake = harness.fakes["android-01"]
    original = fake._build
    modal = Node("android.app.Dialog", (0, 200, 720, 968), text="Aviso de cookies", rid="cookie_modal")
    fechar = Node("android.widget.Button", (660, 210, 708, 258), desc="Fechar", rid="fechar_modal", clickable=True)
    fake._build = lambda: [*original(), modal, fechar]
    limpeza = await _rodar(harness, lambda tree: next(e for e in tree.elements if e.desc == "Fechar"))
    assert limpeza is not None
    assert json.loads(limpeza["variables"])["cobre_bounds"] == "660,210,708,258"
