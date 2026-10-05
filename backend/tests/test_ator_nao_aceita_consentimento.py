"""Item 31.72: a regra do 31.51 vale para o ATOR. Na r-20261005071303-f24955 (Chrome, android-09, 05/10) o ator tocou
"Aceitar cookies" duas vezes por conta própria; a regra só trancava a limpeza. Agora o gesto que aceitaria um aviso de
consentimento é recusado ANTES de chegar ao aparelho (`dialogos.toque_que_aceita`, chamada no `_run_step`): toque e
toque longo por elemento ou coordenada, o início e o fim do `drag`, e o toque que o `type_text` dá no elemento.

As árvores de unidade reaproveitam os nós de `test_dialogos_em_serie` (os da 5b56e6, Mercado Livre). Os testes pelo
laço põem o aviso na tela do aparelho falso, com a geometria de verdade: só o `e_navegador` é trocado (o app de teste
não é o Chrome). Nível de prova: `simulated`.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.automation.hierarchy import parse_hierarchy
from app.config import AiCfg
from app.modules.learning.domain.falhas import classificar_texto
from app.taskqueue.dialogos import (LIMITE_DE_RECUSAS_DE_ACEITE, MOTIVO_ACEITE_RECUSADO,
                                    REJEICAO_TYPE_TEXT_FORA_DE_CAMPO, rotulo_para_o_ator, toque_que_aceita)

from .conftest import Harness
from .fake_device import Node
from .test_dialogos_em_serie import _ACEITAR, _CONFIGURAR, _COOKIES, _PAGINA, _arvore, _no

TERMINAIS = ("completed", "completed_with_issues", "failed", "waiting_user", "needs_input", "uncertain")


def _alvo(tree: Any, texto: str) -> Any:
    return next(e for e in tree.elements
                if (e.text or e.desc or e.resource_id or e.class_name.rsplit(".", 1)[-1]) == texto)


def _recusa(tree: Any, texto: str, ponto: tuple[int, int] | None = None) -> str | None:
    e = toque_que_aceita(tree, _alvo(tree, texto), ponto)
    return None if e is None else (e.text or e.resource_id or e.class_name.rsplit(".", 1)[-1])


# ------------------------------------------------------------------------------------------------------------ unidade
def test_o_aceitar_cookies_da_f24955_e_recusado() -> None:
    tree = parse_hierarchy(_arvore(_PAGINA, _COOKIES, _ACEITAR, _CONFIGURAR))
    assert _recusa(tree, "Aceitar cookies") == "Aceitar cookies"
    assert _recusa(tree, "Configurar cookies") == "Configurar cookies"         # abre outro modal


def test_recusar_e_fechar_passam() -> None:
    rejeitar = _no(7, "Rejeitar", 592, 1160, 700, 1216)
    fechar = _no(8, "", 680, 1004, 716, 1040, rid="cookie-banner-close")
    tree = parse_hierarchy(_arvore(_PAGINA, _COOKIES, _ACEITAR, rejeitar, fechar))
    assert _recusa(tree, "Rejeitar") is None
    assert _recusa(tree, "cookie-banner-close") is None


def test_na_faixa_sem_a_palavra_tambem_e_recusado() -> None:
    """Os casos frágeis: o botão sem "aceitar" na faixa do aviso. Falso positivo antes de aceite em silêncio."""
    continuar = _no(7, "Continuar", 120, 1160, 340, 1216)
    fechar_e_aceitar = _no(8, "Fechar e aceitar", 344, 1160, 588, 1216)
    imagem = _no(9, "", 592, 1160, 700, 1216, classe="android.widget.Image")
    tree = parse_hierarchy(_arvore(_PAGINA, _COOKIES, continuar, fechar_e_aceitar, imagem))
    assert _recusa(tree, "Continuar") == "Continuar"
    assert _recusa(tree, "Fechar e aceitar") == "Fechar e aceitar"
    assert _recusa(tree, "Image") == "Image"                                   # o texto está só na imagem


def test_botao_irmao_do_texto_vale_pela_faixa() -> None:
    """No Chrome o texto do aviso e os botões costumam ser irmãos num contêiner sem id, que o leitor da árvore descarta:
    a zona é a faixa em volta do texto."""
    texto = _no(11, "Usamos cookies para melhorar sua experiência", 20, 990, 700, 1150, classe="android.view.View",
                clicavel=False)
    ciente = _no(12, "Ciente", 120, 1160, 340, 1216)
    tree = parse_hierarchy(_arvore(_PAGINA, texto, ciente))
    assert _recusa(tree, "Ciente") == "Ciente"


@pytest.mark.parametrize("rotulo", ["Aceitar", "ACEITAR TODOS", "Allow all", "Concordo"])
def test_aceite_fora_da_faixa_tambem_e_recusado(rotulo: str) -> None:
    """Leitura do #386, 3: aviso alto, com o botão 750 px abaixo do texto. Com marca na tela, o rótulo que diz aceitar é
    recusado em qualquer lugar."""
    texto = _no(11, "Usamos cookies para melhorar sua experiência", 0, 160, 720, 300, classe="android.view.View",
                clicavel=False)
    botao = _no(12, rotulo, 120, 1050, 600, 1110)
    tree = parse_hierarchy(_arvore(_PAGINA, texto, botao))
    assert _recusa(tree, rotulo) == rotulo


def test_o_ponto_tocado_decide_e_nao_o_centro_do_elemento() -> None:
    """N2 da leitura: um elemento alto (o anúncio que vai do meio da página até a faixa do aviso) julgado pelo ponto."""
    alto = _no(13, "Anúncio", 0, 300, 720, 1000)
    tree = parse_hierarchy(_arvore(_PAGINA, _COOKIES, _ACEITAR, alto))
    assert _recusa(tree, "Anúncio", (360, 350)) is None                          # longe da faixa
    assert _recusa(tree, "Anúncio", (360, 990)) == "Anúncio"                     # dentro da faixa


def test_fora_do_aviso_a_pagina_segue_livre() -> None:
    """O anúncio da página, longe do aviso; e a página com "privacidade" no título não vira zona (mais de 60 % da tela)."""
    anuncio = _no(13, "Raspberry Pi 5 8GB", 0, 200, 720, 400)
    tree = parse_hierarchy(_arvore(_PAGINA, _COOKIES, _ACEITAR, anuncio))
    assert _recusa(tree, "Raspberry Pi 5 8GB") is None
    pagina = _no(0, "Política de privacidade | Loja", 0, 160, 720, 1280, classe="android.webkit.WebView", clicavel=False)
    tree = parse_hierarchy(_arvore(pagina, anuncio))
    assert _recusa(tree, "Raspberry Pi 5 8GB") is None


def test_sem_consentimento_um_ok_ou_permitir_segue_livre() -> None:
    dialogo = _no(1, "Deseja sair da página?", 0, 400, 720, 900, classe="android.app.Dialog", clicavel=False)
    ok = _no(2, "OK", 40, 800, 340, 880)
    permitir = _no(3, "Permitir", 380, 800, 680, 880)
    tree = parse_hierarchy(_arvore(_PAGINA, dialogo, ok, permitir))
    assert _recusa(tree, "OK") is None and _recusa(tree, "Permitir") is None
    assert toque_que_aceita(tree, None) is None


def test_o_rotulo_para_o_ator_normaliza_os_espacos() -> None:
    """S1 da leitura: o rótulo é texto da página; vai só ao ator, sem quebra de linha e até 60 caracteres."""
    tree = parse_hierarchy(_arvore(_PAGINA, _no(1, "Aceitar&#10;  todos&#9;os cookies " + "x" * 80, 0, 0, 10, 10)))
    rotulo = rotulo_para_o_ator(tree.elements[1])
    assert "\n" not in rotulo and "\t" not in rotulo and "  " not in rotulo and len(rotulo) == 60


def test_os_motivos_literais_tem_regra_de_falha() -> None:
    assert classificar_texto(f"A IA insistiu em aceitar: {MOTIVO_ACEITE_RECUSADO} (e5, Button); nada foi "
                             "aceito.").value == "pos_condicao_nao_comprovada"
    assert classificar_texto("A IA insistiu em type_text fora de campo editável.").value == "ia_chamada_invalida"


def test_a_lista_de_hosts_nasce_vazia_e_recusa_o_que_nunca_casaria() -> None:
    """N6 da leitura: `*.loja.com` e `https://loja.com` nunca casariam em silêncio; a carga recusa com erro claro."""
    assert AiCfg().consentimento_aceito_em == []
    assert AiCfg(consentimento_aceito_em=[" Loja.Exemplo.com.br "]).consentimento_aceito_em == ["loja.exemplo.com.br"]
    for ruim in ("*.loja.com", "https://loja.com", "loja.com/x", "localhost"):
        with pytest.raises(ValueError, match="consentimento_aceito_em"):
            AiCfg(consentimento_aceito_em=[ruim])


# ---------------------------------------------------------------- pelo laço do executor, com a geometria de verdade
AVISO = Node("android.view.View", (0, 820, 720, 860), text="Usamos cookies para melhorar sua experiência",
             rid="cookie-consent-banner")
#: Entre a lista de conversas (até ~560) e o campo de mensagem (1160): a faixa do aviso não cobre o que o app toca.
ACEITAR = Node("android.widget.Button", (600, 880, 700, 920), text="Aceitar cookies", clickable=True)


def _com_aviso(harness: Harness, *nos: Node) -> Any:
    """Põe o aviso em toda tela do aparelho falso (por cima do que ela já tem)."""
    fake = harness.fakes["android-01"]
    original = fake._build
    fake._build = lambda: [*original(), *nos]
    return fake


def _toques_em(fake: Any, antes: int, caixa: tuple[int, int, int, int]) -> int:
    x1, y1, x2, y2 = caixa
    pontos = [tuple(int(v) for v in c.split(":", 1)[1].split(",")) for c in fake.calls[antes:] if c.startswith("tap:")]
    return sum(1 for x, y in pontos if x1 <= x <= x2 and y1 <= y <= y2)


async def _pelo_laco(harness: Harness, monkeypatch: Any, gestos: list[Any], *nos: Node) -> tuple[Any, Any, int]:
    """O ator roteirizado faz os `gestos` (funções da árvore à `Decision`) nas primeiras decisões; depois, o de sempre."""
    from app.planning.provider import Usage
    from app.taskqueue import executor as modulo
    harness.pular_o_tempo()
    harness.cfg.file.ai.screenshot_max_side = 1280                 # coordenada da imagem = do aparelho (720 x 1280)
    monkeypatch.setattr(modulo, "e_navegador", lambda pacote: True)
    fake = _com_aviso(harness, *(nos or (AVISO, ACEITAR)))
    decide0, fila = harness.ai.inner.decide, list(gestos)

    async def decide(req: Any) -> Any:
        if fila:
            return fila.pop(0)(req.screen.tree), Usage()
        return await decide0(req)

    harness.ai.inner.decide = decide
    antes = len(fake.calls)
    run = harness.run(["android-01"])
    final = await harness.wait_run(run.id, statuses=TERMINAIS)
    return final, fake, antes


def _decisao(tool: str, **args: Any) -> Any:
    from app.planning.provider import Decision
    return Decision(tool=tool, args={"rationale": "[simulado]", **args})


def _no_aceitar(tree: Any) -> Any:
    return next(e for e in tree.elements if e.text == "Aceitar cookies")


def _recusadas(harness: Harness, run_id: str, tool: str) -> list[str]:
    return [r["error"] for r in harness.state.db.query(                    # type: ignore[union-attr]
        "SELECT a.error FROM actions a JOIN attempts t ON t.id=a.attempt_id JOIN steps s ON s.id=t.step_id "
        "WHERE s.run_id=? AND a.tool=? AND a.status='rejected' ORDER BY a.id", (run_id, tool))]


async def test_pelo_laco_o_toque_no_aceitar_nao_chega_ao_aparelho(harness: Harness, monkeypatch: Any) -> None:
    final, fake, antes = await _pelo_laco(harness, monkeypatch, [
        lambda t: _decisao("tap", element_id=_no_aceitar(t).id, is_commit_action=False)])
    assert _toques_em(fake, antes, ACEITAR.bounds) == 0
    erros = _recusadas(harness, final.id, "tap")
    assert len(erros) == 1 and erros[0].startswith(MOTIVO_ACEITE_RECUSADO)
    assert "Aceitar" not in erros[0] and "Button" in erros[0]          # S1: no `error`, só o id e o tipo


async def test_pelo_laco_type_text_no_botao_e_recusado(harness: Harness, monkeypatch: Any) -> None:
    """B1 da leitura: `type_text(element_id=<Aceitar>, text="")` tocaria o botão antes de escrever."""
    final, fake, antes = await _pelo_laco(harness, monkeypatch, [
        lambda t: _decisao("type_text", element_id=_no_aceitar(t).id, text="")])
    assert _toques_em(fake, antes, ACEITAR.bounds) == 0
    erros = _recusadas(harness, final.id, "type_text")
    assert len(erros) == 1 and erros[0].startswith(REJEICAO_TYPE_TEXT_FORA_DE_CAMPO)


@pytest.mark.parametrize("delta", [0, 5], ids=["drag_zero", "drag_curto"])
async def test_pelo_laco_o_drag_no_aceitar_nao_chega_ao_aparelho(harness: Harness, monkeypatch: Any,
                                                                  delta: int) -> None:
    final, fake, antes = await _pelo_laco(harness, monkeypatch, [
        lambda t: _decisao("drag", from_x=650, from_y=900, to_x=650 + delta, to_y=900 + delta)])
    assert not [c for c in fake.calls[antes:] if c.startswith("swipe")]
    assert len(_recusadas(harness, final.id, "drag")) == 1


async def test_pelo_laco_drag_de_rolagem_longe_do_aviso_passa(harness: Harness, monkeypatch: Any) -> None:
    final, fake, antes = await _pelo_laco(harness, monkeypatch, [
        lambda t: _decisao("drag", from_x=360, from_y=560, to_x=360, to_y=260)])
    assert [c for c in fake.calls[antes:] if c.startswith("swipe")]
    assert _recusadas(harness, final.id, "drag") == []


async def test_pelo_laco_o_aceite_fora_da_faixa_e_recusado(harness: Harness, monkeypatch: Any) -> None:
    aviso_no_topo = Node("android.view.View", (0, 160, 720, 300), text="Usamos cookies para melhorar sua experiência",
                         rid="cookie-consent-banner")
    aceitar_longe = Node("android.widget.Button", (120, 1050, 600, 1110), text="ACEITAR TODOS", clickable=True)
    final, fake, antes = await _pelo_laco(harness, monkeypatch, [
        lambda t: _decisao("tap", element_id=next(e for e in t.elements if e.text == "ACEITAR TODOS").id,
                           is_commit_action=False)], aviso_no_topo, aceitar_longe)
    assert _toques_em(fake, antes, aceitar_longe.bounds) == 0
    assert len(_recusadas(harness, final.id, "tap")) == 1


async def test_pelo_laco_host_declarado_libera_o_aceite(harness: Harness, monkeypatch: Any) -> None:
    """`ai.consentimento_aceito_em` preenchida (decisão do dono): no host listado, o toque chega ao aparelho."""
    harness.cfg.file.ai.consentimento_aceito_em = ["exemplo.com.br"]
    barra = Node("android.widget.EditText", (0, 0, 720, 60), text="loja.exemplo.com.br/oferta",
                 rid="com.android.chrome:id/url_bar")
    final, fake, antes = await _pelo_laco(harness, monkeypatch, [
        lambda t: _decisao("tap", element_id=_no_aceitar(t).id, is_commit_action=False)], AVISO, ACEITAR, barra)
    assert _toques_em(fake, antes, ACEITAR.bounds) == 1
    assert _recusadas(harness, final.id, "tap") == []


async def test_pelo_laco_recusas_alternadas_somam_na_execucao(harness: Harness, monkeypatch: Any) -> None:
    """N8 da leitura: aceite e `observe_screen` alternados zeram o `errors_in_row`; as recusas desta trava somam na
    execução e encerram a etapa no limite, com o motivo."""
    toque = lambda t: _decisao("tap", element_id=_no_aceitar(t).id, is_commit_action=False)  # noqa: E731
    olhar = lambda t: _decisao("observe_screen")  # noqa: E731
    final, fake, antes = await _pelo_laco(harness, monkeypatch, [toque, olhar] * (LIMITE_DE_RECUSAS_DE_ACEITE + 2))
    assert _toques_em(fake, antes, ACEITAR.bounds) == 0
    assert len(_recusadas(harness, final.id, "tap")) == LIMITE_DE_RECUSAS_DE_ACEITE
    assert final.status != "completed"
    detalhe = harness.state.db.scalar(                                  # type: ignore[union-attr]
        "SELECT status_detail FROM steps WHERE run_id=? AND status='failed' ORDER BY seq LIMIT 1", (final.id,))
    assert "A IA insistiu em aceitar" in (detalhe or "")
    assert "Aceitar cookies" not in (detalhe or "")                     # S1: o rótulo da página não vai ao detalhe


async def test_fora_do_navegador_a_trava_nao_age(harness: Harness, monkeypatch: Any) -> None:
    """Escopo: no app (QA Messenger, como no Instagram) a trava não age; as folhas de app são do catálogo (29.87)."""
    from app.taskqueue import executor as modulo
    harness.pular_o_tempo()
    monkeypatch.setattr(modulo, "e_navegador", lambda pacote: False)
    _com_aviso(harness, AVISO, ACEITAR)
    run = harness.run(["android-01"])
    final = await harness.wait_run(run.id, statuses=TERMINAIS)
    assert final.status == "completed" and _recusadas(harness, final.id, "tap") == []
