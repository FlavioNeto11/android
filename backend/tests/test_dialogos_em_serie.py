"""Item 31.51: diálogos do site em série fechados pela árvore, sem IA (`app.taskqueue.dialogos.botao_que_fecha`).

As árvores abaixo foram remontadas a partir do que a r-20261004190200-5b56e6 (Chrome, android-10, 04/10) gravou: o
diálogo "Abra o app e ganhe frete grátis…" (bounds [0,160][720,1232], `cobre_*` da limpeza) com o botão "Agora não"
(`download-app-bottom-banner-close`, tocado em [360,1145]) e o aviso de cookies com "Configurar cookies"
([344,1160][588,1216]). O executor não grava a árvore inteira (só a contagem), então os nós vizinhos são os mínimos.
Nível de prova: `simulated`.
"""
from __future__ import annotations

from typing import Any

from app.automation.hierarchy import parse_hierarchy
from app.taskqueue.dialogos import LIMITE_DE_DIALOGOS, botao_que_fecha, dialogo_sem_saida, e_navegador

from .conftest import Harness
from .test_sobreposicao import TERMINAIS, _juiz_com_ref, _limpeza


def _no(i: int, texto: str, x1: int, y1: int, x2: int, y2: int, *, rid: str = "", classe: str = "android.widget.Button",
        clicavel: bool = True) -> str:
    return (f'<node index="{i}" text="{texto}" resource-id="{rid}" class="{classe}" package="com.android.chrome"'
            f' content-desc="" clickable="{str(clicavel).lower()}" enabled="true" bounds="[{x1},{y1}][{x2},{y2}]" />')


def _arvore(*nos: str) -> str:
    return '<hierarchy rotation="0">' + "".join(nos) + "</hierarchy>"


_PAGINA = _no(0, "Raspberry Pi 5 8gb | Mercado Livre", 0, 160, 720, 1280, classe="android.webkit.WebView",
              clicavel=False)
_DIALOGO_APP = _no(1, "Abra o app e ganhe frete grátis na sua primeira compra", 0, 160, 720, 1232,
                   classe="android.app.Dialog", clicavel=False)
_ABRIR_APP = _no(2, "Abrir o app", 40, 1030, 680, 1100)
_AGORA_NAO = _no(3, "Agora não", 40, 1110, 680, 1180, rid="download-app-bottom-banner-close")
_COOKIES_TEXTO = "Usamos cookies para melhorar sua experiência"
_COOKIES = _no(4, "Usamos cookies para melhorar sua experiência", 0, 1000, 720, 1232, rid="cookie-consent-banner",
               classe="android.view.View", clicavel=False)
_ACEITAR = _no(5, "Aceitar cookies", 120, 1160, 340, 1216)
_CONFIGURAR = _no(6, "Configurar cookies", 344, 1160, 588, 1216)


def test_o_primeiro_dialogo_da_5b56e6_fecha_pelo_agora_nao() -> None:
    tree = parse_hierarchy(_arvore(_PAGINA, _DIALOGO_APP, _ABRIR_APP, _AGORA_NAO))
    botao = botao_que_fecha(tree, (0, 160, 720, 1232))
    assert botao is not None and botao.text == "Agora não"


def test_em_serie_o_segundo_dialogo_aparece_depois_do_primeiro() -> None:
    """Fechado o primeiro, a árvore nova mostra o aviso de cookies; com o "Rejeitar", é ele que a regra toca."""
    rejeitar = _no(7, "Rejeitar", 592, 1160, 700, 1216)
    tree = parse_hierarchy(_arvore(_PAGINA, _COOKIES, _ACEITAR, _CONFIGURAR, rejeitar))
    botao = botao_que_fecha(tree)
    assert botao is not None and botao.text == "Rejeitar"


def test_aviso_que_so_aceita_ou_configura_nao_tem_botao_da_regra() -> None:
    """O aviso de cookies da 5b56e6 (aceitar ou configurar): a regra nunca aceita nem abre a configuração (foi o
    "Configurar cookies" que abriu o segundo modal): devolve `None`, e o executor falha com o motivo literal."""
    tree = parse_hierarchy(_arvore(_PAGINA, _COOKIES, _ACEITAR, _CONFIGURAR))
    assert botao_que_fecha(tree) is None


def test_recusar_vence_fechar_no_mesmo_dialogo() -> None:
    fechar = _no(7, "Fechar", 600, 1010, 700, 1060)
    recusar = _no(8, "Recusar opcionais", 344, 1100, 700, 1150)
    tree = parse_hierarchy(_arvore(_PAGINA, _COOKIES, fechar, recusar, _ACEITAR))
    botao = botao_que_fecha(tree)
    assert botao is not None and botao.text == "Recusar opcionais"


def test_botao_de_fechar_fora_do_que_cobre_nao_e_tocado() -> None:
    """Com a área do que cobria conhecida, o "Fechar" de outro lugar da página (um menu) não é o botão do diálogo."""
    fechar_do_menu = _no(7, "Fechar", 600, 170, 700, 230)
    tree = parse_hierarchy(_arvore(_PAGINA, fechar_do_menu))
    assert botao_que_fecha(tree, (0, 900, 720, 1232)) is None


def test_sem_dialogo_nada_e_tocado() -> None:
    tree = parse_hierarchy(_arvore(_PAGINA, _no(1, "Agora não", 40, 1110, 680, 1180)))
    assert botao_que_fecha(tree) is None                     # sem nada com cara de diálogo, o botão solto não conta


def test_id_que_fecha_sem_rotulo() -> None:
    xis = _no(7, "", 640, 1010, 700, 1060, rid="modal-close")
    tree = parse_hierarchy(_arvore(_PAGINA, _DIALOGO_APP, xis))
    botao = botao_que_fecha(tree, (0, 160, 720, 1232))
    assert botao is not None and botao.resource_id == "modal-close"


def test_o_teto_proprio_cobre_os_dois_dialogos_da_5b56e6() -> None:
    assert LIMITE_DE_DIALOGOS >= 2


# ------------------------------------------------------------------ critério de aceite: nunca aceitar, falhar com motivo
def test_aviso_que_so_aceita_e_dialogo_sem_saida_com_o_texto() -> None:
    from app.taskqueue.dialogos import dialogo_sem_saida
    tree = parse_hierarchy(_arvore(_PAGINA, _COOKIES, _ACEITAR, _CONFIGURAR))
    assert dialogo_sem_saida(tree) == _COOKIES_TEXTO


def test_dialogo_nao_reconhecido_e_sem_saida() -> None:
    from app.taskqueue.dialogos import dialogo_sem_saida
    estranho = _no(1, "Promoção relâmpago!", 0, 400, 720, 900, classe="android.app.Dialog", clicavel=False)
    tree = parse_hierarchy(_arvore(_PAGINA, estranho, _no(2, "Quero ver", 40, 820, 680, 880)))
    assert dialogo_sem_saida(tree) == "Promoção relâmpago!"


def test_abrir_o_app_escolhe_continuar_no_navegador() -> None:
    continuar = _no(7, "Continuar no navegador", 40, 1110, 680, 1180)
    tree = parse_hierarchy(_arvore(_PAGINA, _DIALOGO_APP, _ABRIR_APP, continuar))
    botao = botao_que_fecha(tree, (0, 160, 720, 1232))
    assert botao is not None and botao.text == "Continuar no navegador"


def test_com_saida_nao_ha_dialogo_sem_saida() -> None:
    from app.taskqueue.dialogos import dialogo_sem_saida
    tree = parse_hierarchy(_arvore(_PAGINA, _DIALOGO_APP, _ABRIR_APP, _AGORA_NAO))
    assert dialogo_sem_saida(tree, (0, 160, 720, 1232)) is None


def test_o_motivo_literal_tem_regra_de_falha() -> None:
    from app.modules.learning.domain.falhas import FailureKind, classificar_texto
    from app.taskqueue.dialogos import MOTIVO_SEM_SAIDA
    assert classificar_texto(f"{MOTIVO_SEM_SAIDA} 'Usamos cookies': nenhum botão de recusar, fechar ou continuar no "
                       "navegador; nada foi aceito.") == FailureKind.POS_CONDICAO_NAO_COMPROVADA

# ------------------------------------------------------------------ revisão do #308: privacidade
def test_1a_icone_sem_rotulo_com_id_de_aceite_nao_e_tocado() -> None:
    aceita = _no(7, "", 640, 1010, 700, 1060, rid="cookie-accept-and-close")
    tree = parse_hierarchy(_arvore(_PAGINA, _COOKIES, aceita))
    assert botao_que_fecha(tree) is None


def test_1b_rotulo_fora_da_lista_com_id_que_fecha_nao_e_tocado() -> None:
    for rotulo in ("Got it", "Entendi", "Okay", "Continuar"):
        botao = _no(7, rotulo, 344, 1160, 588, 1216, rid="consent-close")
        assert botao_que_fecha(parse_hierarchy(_arvore(_PAGINA, _COOKIES, botao))) is None, rotulo


def test_1c_o_veto_pega_espanhol_alemao_e_consentimento_implicito() -> None:
    for rotulo in ("Aceptar", "Akzeptieren", "Got it", "Entendi", "OK", "Okay"):
        botao = _no(7, rotulo, 344, 1160, 588, 1216, rid="banner-close")
        assert botao_que_fecha(parse_hierarchy(_arvore(_PAGINA, _COOKIES, botao))) is None, rotulo


def test_3a_banner_pequeno_que_sobra_na_pagina_nao_e_dialogo_sem_saida() -> None:
    faixa = _no(1, "Ofertas do dia", 0, 170, 720, 230, rid="promo-banner", classe="android.view.View", clicavel=False)
    assert dialogo_sem_saida(parse_hierarchy(_arvore(_PAGINA, faixa))) is None


def test_so_o_navegador_tem_a_regra() -> None:
    assert e_navegador("com.android.chrome")
    assert not e_navegador("com.instagram.android") and not e_navegador("com.pocqa.messenger") and not e_navegador(None)


# ------------------------------------------------------------------ revisão do #308: o laço do executor (harness)
def _regras(h: Harness, step_id: str) -> int:
    return int(h.state.db.scalar(  # type: ignore[union-attr]
        "SELECT COUNT(*) FROM actions a JOIN attempts t ON t.id=a.attempt_id WHERE t.step_id=? AND a.source='regra'",
        (step_id,)) or 0)


def _primeiro_clicavel(tree: Any, area: Any = None) -> Any:
    return next(e for e in tree.elements if e.clickable)


async def _rodar(harness: Harness, monkeypatch: Any, *, navegador: bool, botao: Any, sobra: Any) -> Any:
    from app.taskqueue import executor as modulo
    harness.pular_o_tempo()
    harness.encurtar_verificacao(1.5)
    monkeypatch.setattr(modulo, "e_navegador", lambda pacote: navegador)
    monkeypatch.setattr(modulo, "botao_que_fecha", botao)
    monkeypatch.setattr(modulo, "dialogo_sem_saida", sobra)
    _juiz_com_ref(harness.ai.inner, "verify_sent", cobertas=1)
    run = harness.run(["android-01"])
    await harness.wait_run(run.id, statuses=TERMINAIS)
    return run, _limpeza(harness, run.id)


async def test_o_laco_fecha_dois_dialogos_pela_regra_sem_ia(harness: Harness, monkeypatch: Any) -> None:
    from app.taskqueue import executor as modulo
    vezes = {"n": 0}

    def botao(tree: Any, area: Any = None) -> Any:
        vezes["n"] += 1
        return _primeiro_clicavel(tree) if vezes["n"] <= 2 else None

    monkeypatch.setattr(modulo, "ainda_cobre", lambda c, tree: vezes["n"] < 2)
    _run, limpeza = await _rodar(harness, monkeypatch, navegador=True, botao=botao, sobra=lambda tree, area=None: None)
    assert _regras(harness, limpeza["id"]) == 2


async def test_o_dialogo_que_volta_falha_no_teto_com_o_motivo(harness: Harness, monkeypatch: Any) -> None:
    run, limpeza = await _rodar(harness, monkeypatch, navegador=True, botao=_primeiro_clicavel,
                                sobra=lambda tree, area=None: None)
    assert _regras(harness, limpeza["id"]) == LIMITE_DE_DIALOGOS
    assert limpeza["status"] == "failed" and "voltou depois de" in limpeza["status_detail"]
    assert harness.state.repo.run_row(run.id)["status"] != "completed"                 # type: ignore[union-attr]


async def test_dialogo_sem_saida_falha_e_nao_e_pulado(harness: Harness, monkeypatch: Any) -> None:
    run, limpeza = await _rodar(harness, monkeypatch, navegador=True, botao=lambda tree, area=None: None,
                                sobra=lambda tree, area=None: "Usamos cookies")
    assert limpeza["status"] == "failed"                                               # 3c: não fica skipped
    assert limpeza["status_detail"].startswith("A limpeza não fechou o diálogo do site 'Usamos cookies'")
    assert _regras(harness, limpeza["id"]) == 0
    assert harness.state.repo.run_row(run.id)["status"] != "completed"                 # type: ignore[union-attr]


async def test_fora_do_navegador_a_regra_nao_toca(harness: Harness, monkeypatch: Any) -> None:
    """Escopo: no app de teste (como no Instagram), um bottom sheet com "Dismiss" não é tocado pela regra."""
    _run, limpeza = await _rodar(harness, monkeypatch, navegador=False, botao=_primeiro_clicavel,
                                 sobra=lambda tree, area=None: "Aviso")
    assert _regras(harness, limpeza["id"]) == 0
    assert "A limpeza não fechou o diálogo do site" not in (limpeza["status_detail"] or "")
