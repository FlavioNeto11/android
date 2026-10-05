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
                                    REJEICAO_TYPE_TEXT_FORA_DE_CAMPO, botao_que_fecha, rotulo_para_o_ator,
                                    toque_que_aceita)

from .conftest import Harness
from .fake_device import Node
from .test_dialogos_em_serie import _ACEITAR, _CONFIGURAR, _COOKIES, _COOKIES_TEXTO, _PAGINA, _arvore, _no

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
    alto = _no(13, "Anúncio", 0, 300, 720, 1060)
    tree = parse_hierarchy(_arvore(_PAGINA, _COOKIES, _ACEITAR, alto))
    assert _recusa(tree, "Anúncio", (360, 350)) is None                          # longe do aviso
    assert _recusa(tree, "Anúncio", (360, 1040)) == "Anúncio"                    # dentro do aviso
    # Z1 da 2ª leitura: o aviso que É a caixa externa (não há contêiner distinto em volta) fica com a faixa de 12 %,
    # como na 8324c9a9: 10 px acima dele o toque segue recusado (lado seguro).
    assert _recusa(tree, "Anúncio", (360, 990)) == "Anúncio"


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


# ------------------------------------------------------------------------- releitura do #386: K1 a K4, caixa, N6
def _xml(*nos: tuple[str, tuple[int, int, int, int], str, str, bool]) -> Any:
    """(texto, bounds, rid, classe, clicável), na ordem do documento."""
    corpo = "".join(
        f'<node index="0" text="{t}" resource-id="{rid}" class="{cls or "android.view.View"}" '
        f'package="com.android.chrome" content-desc="" clickable="{str(c).lower()}" enabled="true" '
        f'bounds="[{b[0]},{b[1]}][{b[2]},{b[3]}]" />' for t, b, rid, cls, c in nos)
    return parse_hierarchy(f'<hierarchy rotation="0">{corpo}</hierarchy>')


_TEXTO_DO_AVISO = ("Usamos cookies para melhorar sua experiência", (0, 1000, 720, 1060), "", "", False)


def test_k1_o_rotulo_exato_de_fechar_vence_o_nunca_no_rotulo_e_nao_no_id() -> None:
    tree = _xml(_TEXTO_DO_AVISO,
                ("Não aceitar", (20, 1080, 340, 1140), "", "android.widget.Button", True),
                ("Continuar sem aceitar", (360, 1080, 700, 1140), "", "android.widget.Button", True),
                ("Fechar", (600, 1150, 700, 1200), "cookie-accept-and-close", "android.widget.Button", True))
    assert _recusa(tree, "Não aceitar") is None
    assert _recusa(tree, "Continuar sem aceitar") is None
    assert _recusa(tree, "Fechar") == "Fechar"                       # o id diz aceitar: recusado sempre


def test_k2_a_interface_do_chrome_nao_vira_marca() -> None:
    """A página inicial anônima do Chrome (captura de 05/10, `anon-0-ntp-anonima`): "Block third-party cookies" é
    interface do navegador; com só isso na tela, a trava não age."""
    tree = _xml(("", (64, 1129, 656, 1232), "com.android.chrome:id/cookie_controls_card", "", True),
                ("Block third-party cookies", (96, 1161, 528, 1204),
                 "com.android.chrome:id/cookie_controls_card_title", "android.widget.TextView", False),
                ("Learn more", (64, 900, 300, 960), "com.android.chrome:id/learn_more", "", True),
                ("Permitir", (400, 900, 600, 960), "", "android.widget.Button", True))
    assert _recusa(tree, "Learn more") is None
    assert _recusa(tree, "Permitir") is None                         # sem marca de site, nada a julgar


def test_k2_a_palavra_de_aceite_no_rotulo_so_conta_no_comeco_da_palavra() -> None:
    """Fora da zona do aviso: "inaceitável" não é aceite no rótulo; no id, `btnAccept` segue recusado."""
    tree = _xml(_TEXTO_DO_AVISO,
                ("Denunciar conteúdo inaceitável", (20, 200, 700, 260), "", "android.widget.Button", True),
                ("Entrar", (20, 300, 700, 360), "btnAccept", "android.widget.Button", True))
    assert _recusa(tree, "Denunciar conteúdo inaceitável") is None
    assert _recusa(tree, "Entrar") == "Entrar"


def test_k2_o_botao_do_chrome_so_e_julgado_pelo_rotulo_e_dentro_da_zona() -> None:
    """O menu da barra de tradução do Chrome por cima da folha de cookies do gov.br (05/10): não é a página."""
    tree = _xml(("Para melhorar a sua experiência, usamos cookies", (0, 602, 720, 1232), "", "", False),
                ("Rejeitar cookies", (192, 1004, 528, 1068), "", "android.widget.Button", True),
                ("More options", (512, 1120, 608, 1232), "com.android.chrome:id/translate_infobar_menu_button",
                 "android.widget.ImageButton", True),
                ("Accept", (40, 1120, 200, 1232), "com.android.chrome:id/infobar_accept", "android.widget.Button", True),
                ("Allow", (40, 60, 200, 140), "com.android.chrome:id/allow", "android.widget.Button", True))
    assert _recusa(tree, "More options") is None
    assert _recusa(tree, "Accept") == "Accept"                       # dentro da zona, diz aceitar
    assert _recusa(tree, "Allow") is None                            # fora da zona, o Chrome não é julgado pelo rótulo


def test_a_regra_da_caixa_a_zona_e_a_folha_inteira() -> None:
    """gov.br (`gov-3-rodape`): a linha da página por baixo da folha de 49 % (antes dela no documento) é recusada. A
    folha é ela mesma marca e a caixa externa: pelo Z1 da 2ª leitura, a zona dela é a faixa, e a linha 42 px acima
    também é recusada (lado seguro); bem acima da faixa, passa."""
    tree = _xml(("Saúde e Vigilância Sanitária", (32, 480, 688, 560), "", "", True),
                ("Viagens e Turismo", (32, 918, 688, 1066), "", "", True),
                ("Para melhorar a sua experiência, usamos cookies", (0, 602, 720, 1232), "", "", False),
                ("Ao aceitar, você terá acesso a todas as funcionalidades", (44, 664, 676, 796), "", "", False),
                ("Rejeitar cookies", (192, 1004, 528, 1068), "", "android.widget.Button", True),
                ("Aceitar cookies", (192, 1100, 528, 1164), "", "android.widget.Button", True))
    assert _recusa(tree, "Viagens e Turismo") == "Viagens e Turismo"
    assert _recusa(tree, "Saúde e Vigilância Sanitária") == "Saúde e Vigilância Sanitária"
    assert _recusa(tree, "Rejeitar cookies") is None
    assert _recusa(tree, "Aceitar cookies") == "Aceitar cookies"


def test_o_rotulo_para_o_ator_tira_controle_e_formatacao() -> None:
    tree = _xml(("Ace​itar‮ todos\u2060", (0, 0, 100, 40), "", "", True))
    assert rotulo_para_o_ator(tree.elements[0]) == "Aceitar todos"


def test_n6_recusa_sufixo_publico() -> None:
    for sufixo in ("com.br", "gov.br", "github.io"):
        with pytest.raises(ValueError, match="sufixo público"):
            AiCfg(consentimento_aceito_em=[sufixo])
    assert AiCfg(consentimento_aceito_em=["loja.com.br"]).consentimento_aceito_em == ["loja.com.br"]


async def test_pelo_laco_k3_as_recusas_contam_por_etapa(harness: Harness, monkeypatch: Any) -> None:
    """K3: três recusas na 1ª etapa e três na 2ª somam seis na execução, mas nenhuma etapa chega ao limite (4): a trava
    não encerra nenhuma delas. Com a contagem por execução (antes), a 4ª recusa encerraria a 2ª etapa."""
    from app.planning.provider import Usage
    from app.taskqueue import executor as modulo
    harness.pular_o_tempo()
    harness.cfg.file.ai.screenshot_max_side = 1280
    monkeypatch.setattr(modulo, "e_navegador", lambda pacote: True)
    fake = _com_aviso(harness, AVISO, ACEITAR)
    toque = lambda t: _decisao("tap", element_id=_no_aceitar(t).id, is_commit_action=False)  # noqa: E731
    olhar = lambda t: _decisao("observe_screen")  # noqa: E731
    decide0 = harness.ai.inner.decide
    filas: dict[str, list[Any]] = {}

    async def decide(req: Any) -> Any:
        if req.ctx.step_key not in filas:
            filas[req.ctx.step_key] = [toque, olhar] * 3 if len(filas) < 2 else []
        fila = filas[req.ctx.step_key]
        if fila:
            return fila.pop(0)(req.screen.tree), Usage()
        return await decide0(req)

    harness.ai.inner.decide = decide
    antes = len(fake.calls)
    run = harness.run(["android-01"])
    final = await harness.wait_run(run.id, statuses=TERMINAIS)
    assert len(filas) >= 2                                             # o plano tem ao menos duas etapas com decisão
    assert _toques_em(fake, antes, ACEITAR.bounds) == 0
    assert len(_recusadas(harness, final.id, "tap")) == 6
    assert not harness.state.db.scalar(                                # type: ignore[union-attr]
        "SELECT COUNT(*) FROM steps WHERE run_id=? AND status_detail LIKE 'A IA insistiu em aceitar%'", (final.id,))


async def test_pelo_laco_k4_o_type_text_fora_de_campo_vindo_da_receita_diverge(harness: Harness,
                                                                                  monkeypatch: Any) -> None:
    """K4: o `type_text` da RECEITA que cairia no botão do aviso é recusado pelo B1 e conta como divergência da
    receita (`rr.diverged`), como o toque de aceite já contava: a receita não sai como reproduzida."""
    import json

    from app.metricas import metricas
    from app.taskqueue import executor as modulo
    harness.pular_o_tempo()
    harness.cfg.file.ai.recipes = "replay"
    harness.cfg.file.ai.screenshot_max_side = 1280
    monkeypatch.setattr(modulo, "e_navegador", lambda pacote: True)
    botao = Node("android.widget.Button", (600, 880, 700, 920), text="Aceitar cookies", rid="aceitar_cookies",
                 clickable=True)
    for aparelho in ("android-01", "android-02"):
        fake = harness.fakes[aparelho]
        original = fake._build
        fake._build = (lambda o: lambda: [*o(), AVISO, botao])(original)
    assert (await harness.wait_run(harness.run(["android-01"]).id, statuses=TERMINAIS)).status == "completed"
    db = harness.state.db                                                   # type: ignore[union-attr]
    trocadas = 0
    for linha in db.query("SELECT id, actions FROM recipes"):
        acoes = json.loads(linha["actions"])
        if any(a.get("tool") == "type_text" for a in acoes):
            for a in acoes:
                if a.get("tool") == "type_text":
                    a["selectors"] = [{"kind": "rid", "rid": "com.pocqa.messenger:id/aceitar_cookies"}]
            db.execute("UPDATE recipes SET actions=? WHERE id=?", (json.dumps(acoes), linha["id"]))
            trocadas += 1
    assert trocadas >= 1                                                    # o fluxo do app de teste tem um type_text
    metricas.limpar()
    fake2 = harness.fakes["android-02"]
    antes = len(fake2.calls)
    final = await harness.wait_run(harness.run(["android-02"]).id, statuses=TERMINAIS)
    assert _toques_em(fake2, antes, botao.bounds) == 0
    assert any(e.startswith(REJEICAO_TYPE_TEXT_FORA_DE_CAMPO) for e in _recusadas(harness, final.id, "type_text"))
    assert metricas.valor("receita.reproducao", resultado="divergiu") >= 1


# ------------------------------------------------------------------------ 2ª leitura do #386: Z1, K1b e K2b
def test_z1_o_paragrafo_com_link_nao_encolhe_a_zona() -> None:
    """O parágrafo do aviso com o link "política de cookies" dentro dele: sem contêiner distinto, a zona é a faixa, e os
    botões 100 px abaixo que não dizem aceitar seguem recusados."""
    tree = _xml(("Usamos cookies para melhorar sua experiência. Saiba mais na política de cookies",
                 (20, 1000, 700, 1060), "", "", False),
                ("política de cookies", (400, 1030, 600, 1058), "", "", True),
                ("Estou de acordo", (20, 1150, 340, 1200), "", "android.widget.Button", True),
                ("Ciente", (360, 1150, 500, 1200), "", "android.widget.Button", True),
                ("Prosseguir", (520, 1150, 700, 1200), "", "android.widget.Button", True))
    for rotulo in ("Estou de acordo", "Ciente", "Prosseguir"):
        assert _recusa(tree, rotulo) == rotulo, rotulo


def test_k1b_o_botao_que_fecha_escolhe_continuar_sem_aceitar() -> None:
    tree = _xml(("", (0, 900, 720, 1232), "cookie-banner", "", True),
                ("Usamos cookies para melhorar sua experiência", (20, 920, 700, 980), "", "", False),
                ("Aceitar", (20, 1100, 340, 1160), "", "android.widget.Button", True),
                ("Continuar sem aceitar", (360, 1100, 700, 1160), "", "android.widget.Button", True))
    escolhido = botao_que_fecha(tree)
    assert escolhido is not None and escolhido.text == "Continuar sem aceitar"


def test_k1_o_x_com_desc_accept_segue_vetado() -> None:
    """O caso do #308: text "X" e desc "Accept"; o rótulo exato de fechar não livra quando o outro campo diz aceitar."""
    corpo = ('<node index="0" text="Usamos cookies para melhorar sua experiência" resource-id="" class="android.view.View" '
             'package="com.android.chrome" content-desc="" clickable="false" enabled="true" bounds="[0,1000][720,1060]" />'
             '<node index="1" text="X" resource-id="" class="android.widget.Button" package="com.android.chrome" '
             'content-desc="Accept" clickable="true" enabled="true" bounds="[660,200][708,248]" />')
    tree = parse_hierarchy(f'<hierarchy rotation="0">{corpo}</hierarchy>')
    assert _recusa(tree, "X") == "X"


def test_k2b_so_o_link_de_privacidade_do_rodape_nao_tranca_a_tela() -> None:
    """Com só o link "Política de privacidade" do rodapé, a palavra de aceite longe dele passa; perto dele, não."""
    tree = _xml(("Política de privacidade", (20, 1180, 300, 1220), "", "", True),
                ("OK", (20, 300, 200, 360), "", "android.widget.Button", True),
                ("Permitir", (220, 300, 400, 360), "", "android.widget.Button", True),
                ("Aceitamos Pix", (420, 300, 700, 360), "", "", True),
                ("Concordo", (320, 1180, 500, 1220), "", "android.widget.Button", True))
    for rotulo in ("OK", "Permitir", "Aceitamos Pix"):
        assert _recusa(tree, rotulo) is None, rotulo
    assert _recusa(tree, "Concordo") == "Concordo"                     # na faixa do link, segue recusado


def test_k2b_com_aviso_de_verdade_a_palavra_de_aceite_e_recusada_em_qualquer_lugar() -> None:
    tree = _xml(("Usamos cookies para melhorar sua experiência", (0, 160, 720, 220), "", "", False),
                ("ACEITAR TODOS", (120, 1050, 600, 1110), "", "android.widget.Button", True))
    assert _recusa(tree, "ACEITAR TODOS") == "ACEITAR TODOS"


#: A tela de 2400 px de altura do celular: a faixa em volta da marca é de 288 px (`_MARGEM_DA_FAIXA`).
_TELA_2400 = ("", (0, 0, 1080, 2400), "", "android.widget.FrameLayout", False)


def test_z1c_a_caixa_soma_a_faixa_e_o_involucro_do_texto_nao_libera_o_irmao_de_baixo() -> None:
    """Z1c da leitura: um involucro só do texto, com id de `_PISTAS` que não é de consentimento (`banner-content`), vira
    a caixa da marca. A zona é a caixa MAIS a faixa: os botões no irmão de baixo, 200 px abaixo dele e dentro da faixa
    do texto, seguem recusados, mesmo sem a palavra de aceite."""
    for involucro in ("banner-content", "modal-body"):
        tree = _xml(_TELA_2400,
                    ("", (0, 1600, 1080, 1760), involucro, "", False),
                    ("Usamos cookies para melhorar sua experiência", (20, 1610, 1060, 1690), "", "", False),
                    ("política de cookies", (20, 1700, 400, 1750), "", "", True),
                    ("Estou de acordo", (20, 1950, 260, 2010), "", "android.widget.Button", True),
                    ("Ciente", (280, 1950, 520, 2010), "", "android.widget.Button", True),
                    ("Prosseguir", (540, 1950, 780, 2010), "", "android.widget.Button", True),
                    ("Continuar", (800, 1950, 1060, 2010), "", "android.widget.Button", True))
        for rotulo in ("Estou de acordo", "Ciente", "Prosseguir", "Continuar"):
            assert _recusa(tree, rotulo) == rotulo, (involucro, rotulo)


def test_k2c_o_titulo_curto_do_aviso_tem_cara_de_aviso() -> None:
    """K2c da leitura: o título "Sua privacidade" (curto, não clicável) com o corpo sem a palavra é aviso de verdade; a
    palavra de aceite 440 px abaixo, fora da faixa de 288 px, é recusada."""
    tree = _xml(_TELA_2400,
                ("Sua privacidade", (40, 1500, 600, 1560), "", "", False),
                ("Usamos dados para personalizar anúncios e conteúdo", (40, 1580, 1040, 1680), "", "", False),
                ("Aceitar todos", (40, 2000, 360, 2060), "", "android.widget.Button", True),
                ("Aceitar e continuar", (380, 2000, 760, 2060), "", "android.widget.Button", True),
                ("OK", (780, 2000, 1040, 2060), "", "android.widget.Button", True))
    for rotulo in ("Aceitar todos", "Aceitar e continuar", "OK"):
        assert _recusa(tree, rotulo) == rotulo, rotulo


def test_k2c_a_pergunta_do_aviso_tem_cara_de_aviso_e_o_link_do_rodape_nao() -> None:
    pergunta = _xml(_TELA_2400,
                    ("Aceitar cookies?", (40, 1500, 600, 1560), "", "", False),
                    ("Aceitar", (40, 2000, 400, 2060), "", "android.widget.Button", True))
    assert _recusa(pergunta, "Aceitar") == "Aceitar"
    # O link do rodapé é clicável: segue fora (o K2b), e a palavra de aceite longe dele passa.
    rodape = _xml(_TELA_2400,
                  ("Política de privacidade", (40, 2300, 500, 2360), "", "", True),
                  ("Aceitar", (40, 600, 400, 660), "", "android.widget.Button", True))
    assert _recusa(rodape, "Aceitar") is None


def test_31_104_pagina_esparsa_mede_a_tela_pela_janela_e_o_aviso_segue_marca() -> None:
    """31.104: a janela do dump (31.77) é a tela inteira, mas as folhas só ocupam o canto de cima. Pela extensão das
    folhas (400×300), o texto do aviso (400×200) passava de 60 % da "página", deixava de ser marca e o "Aceitar todos"
    passava. Pela maior das duas medidas, o aviso segue marca e o aceite é recusado."""
    aviso = _no(1, _COOKIES_TEXTO, 0, 40, 400, 240, rid="cookie-consent-banner", classe="android.view.View",
                clicavel=False)
    aceitar = _no(2, "Aceitar todos", 40, 250, 300, 300)
    xml = ('<hierarchy rotation="0"><node index="0" text="" resource-id="" class="android.widget.FrameLayout"'
           ' package="com.android.chrome" content-desc="" clickable="false" enabled="true" bounds="[0,0][720,1280]">'
           + aviso + aceitar + "</node></hierarchy>")
    tree = parse_hierarchy(xml)
    assert tree.janela == (0, 0, 720, 1280)
    assert max(e.bounds[2] for e in tree.elements) == 400 and max(e.bounds[3] for e in tree.elements) == 300
    assert _recusa(tree, "Aceitar todos") == "Aceitar todos"
