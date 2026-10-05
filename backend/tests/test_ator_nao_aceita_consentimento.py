"""Item 31.72: a regra do 31.51 vale para o ATOR. Na r-20261005071303-f24955 (Chrome, android-09, 05/10) o ator tocou
"Aceitar cookies" duas vezes por conta própria; a regra só trancava a limpeza. Agora o toque que aceitaria um aviso de
consentimento é recusado ANTES de chegar ao aparelho (`dialogos.toque_que_aceita`, chamada no `_run_step`), pelo
elemento ou pela coordenada, sem depender de o modelo obedecer ao prompt.

As árvores reaproveitam os nós de `test_dialogos_em_serie` (os da 5b56e6, Mercado Livre). Nível de prova: `simulated`.
"""
from __future__ import annotations

from typing import Any

from app.automation.hierarchy import parse_hierarchy
from app.config import AiCfg
from app.modules.learning.domain.falhas import classificar_texto
from app.taskqueue.dialogos import MOTIVO_ACEITE_RECUSADO, toque_que_aceita

from .conftest import Harness
from .test_dialogos_em_serie import _ACEITAR, _CONFIGURAR, _COOKIES, _PAGINA, _arvore, _no


def _alvo(tree: Any, texto: str) -> Any:
    return next(e for e in tree.elements
                if (e.text or e.desc or e.resource_id or e.class_name.rsplit(".", 1)[-1]) == texto)


def test_o_aceitar_cookies_da_f24955_e_recusado() -> None:
    tree = parse_hierarchy(_arvore(_PAGINA, _COOKIES, _ACEITAR, _CONFIGURAR))
    assert toque_que_aceita(tree, _alvo(tree, "Aceitar cookies")) == "Aceitar cookies"
    assert toque_que_aceita(tree, _alvo(tree, "Configurar cookies")) == "Configurar cookies"   # abre outro modal


def test_recusar_e_fechar_passam() -> None:
    rejeitar = _no(7, "Rejeitar", 592, 1160, 700, 1216)
    fechar = _no(8, "", 680, 1004, 716, 1040, rid="cookie-banner-close")
    tree = parse_hierarchy(_arvore(_PAGINA, _COOKIES, _ACEITAR, rejeitar, fechar))
    assert toque_que_aceita(tree, _alvo(tree, "Rejeitar")) is None
    assert toque_que_aceita(tree, _alvo(tree, "cookie-banner-close")) is None


def test_na_caixa_sem_a_palavra_tambem_e_recusado() -> None:
    """Os casos frágeis: o botão sem "aceitar" na caixa de consentimento. Falso positivo antes de aceite em silêncio."""
    continuar = _no(7, "Continuar", 120, 1160, 340, 1216)
    fechar_e_aceitar = _no(8, "Fechar e aceitar", 344, 1160, 588, 1216)
    imagem = _no(9, "", 592, 1160, 700, 1216, classe="android.widget.Image")
    tree = parse_hierarchy(_arvore(_PAGINA, _COOKIES, continuar, fechar_e_aceitar, imagem))
    assert toque_que_aceita(tree, _alvo(tree, "Continuar")) == "Continuar"
    assert toque_que_aceita(tree, _alvo(tree, "Fechar e aceitar")) == "Fechar e aceitar"
    assert toque_que_aceita(tree, _alvo(tree, "Image")) is not None                  # o texto está só na imagem


def test_botao_irmao_do_texto_vale_pela_faixa() -> None:
    """No Chrome o texto do aviso e os botões costumam ser irmãos num contêiner sem id, que o leitor da árvore descarta:
    a zona é a faixa em volta do texto."""
    caixa = _no(10, "", 0, 980, 720, 1232, classe="android.view.View", clicavel=False)
    texto = _no(11, "Usamos cookies para melhorar sua experiência", 20, 990, 700, 1150, classe="android.view.View",
                clicavel=False)
    ok = _no(12, "Entendi", 120, 1160, 340, 1216)
    tree = parse_hierarchy(_arvore(_PAGINA, caixa, texto, ok))
    assert toque_que_aceita(tree, _alvo(tree, "Entendi")) == "Entendi"


def test_fora_do_aviso_a_pagina_segue_livre() -> None:
    """O anúncio da página, longe do aviso; e a página com "privacidade" no título não vira zona (mais de 60 % da tela)."""
    anuncio = _no(13, "Raspberry Pi 5 8GB", 0, 200, 720, 400)
    tree = parse_hierarchy(_arvore(_PAGINA, _COOKIES, _ACEITAR, anuncio))
    assert toque_que_aceita(tree, _alvo(tree, "Raspberry Pi 5 8GB")) is None
    pagina = _no(0, "Política de privacidade | Loja", 0, 160, 720, 1280, classe="android.webkit.WebView", clicavel=False)
    tree = parse_hierarchy(_arvore(pagina, anuncio))
    assert toque_que_aceita(tree, _alvo(tree, "Raspberry Pi 5 8GB")) is None


def test_sem_consentimento_um_ok_ou_permitir_segue_livre() -> None:
    dialogo = _no(1, "Deseja sair da página?", 0, 400, 720, 900, classe="android.app.Dialog", clicavel=False)
    ok = _no(2, "OK", 40, 800, 340, 880)
    permitir = _no(3, "Permitir", 380, 800, 680, 880)
    tree = parse_hierarchy(_arvore(_PAGINA, dialogo, ok, permitir))
    assert toque_que_aceita(tree, _alvo(tree, "OK")) is None
    assert toque_que_aceita(tree, _alvo(tree, "Permitir")) is None
    assert toque_que_aceita(tree, None) is None


def test_o_motivo_literal_tem_regra_de_falha() -> None:
    assert classificar_texto(f"A IA insistiu em aceitar: {MOTIVO_ACEITE_RECUSADO} ('Aceitar cookies'); nada foi "
                             "aceito.").value == "pos_condicao_nao_comprovada"


def test_a_lista_de_hosts_nasce_vazia() -> None:
    assert AiCfg().consentimento_aceito_em == []


# ------------------------------------------------------------------------------- pelo laço do executor, com o harness
async def _pelo_laco(harness: Harness, monkeypatch: Any, *, navegador: bool) -> tuple[Any, list[str], list[str]]:
    """Todo toque resolvido conta como aceite (a função é trocada): o ator roteirizado do harness TOCA, e o que se
    confere é o que chegou ao aparelho falso."""
    from app.taskqueue import executor as modulo
    harness.pular_o_tempo()
    monkeypatch.setattr(modulo, "e_navegador", lambda pacote: navegador)
    monkeypatch.setattr(modulo, "toque_que_aceita", lambda tree, alvo: "Aceitar cookies" if alvo is not None else None)
    fake = harness.fakes["android-01"]
    antes = len(fake.calls)
    run = harness.run(["android-01"])
    final = await harness.wait_run(run.id, statuses=("completed", "completed_with_issues", "failed", "waiting_user",
                                                      "needs_input", "uncertain"))
    toques = [c for c in fake.calls[antes:] if c.startswith("tap:")]
    erros = [r["error"] for r in harness.state.db.query(                      # type: ignore[union-attr]
        "SELECT a.error FROM actions a JOIN attempts t ON t.id=a.attempt_id JOIN steps s ON s.id=t.step_id "
        "WHERE s.run_id=? AND a.tool='tap' AND a.status='rejected'", (run.id,))]
    return final, toques, erros


async def test_pelo_laco_o_toque_de_aceite_nao_chega_ao_aparelho(harness: Harness, monkeypatch: Any) -> None:
    final, toques, erros = await _pelo_laco(harness, monkeypatch, navegador=True)
    assert toques == []                                           # nenhum toque chegou ao aparelho
    assert erros and all(e.startswith(MOTIVO_ACEITE_RECUSADO) for e in erros)
    assert final.status != "completed"                            # nunca vira sucesso por aceite
    detalhe = harness.state.db.scalar(                            # type: ignore[union-attr]
        "SELECT status_detail FROM steps WHERE run_id=? AND status='failed' ORDER BY seq LIMIT 1", (final.id,))
    assert "A IA insistiu em aceitar" in (detalhe or "")


async def test_fora_do_navegador_a_trava_nao_age(harness: Harness, monkeypatch: Any) -> None:
    """Escopo: no app (QA Messenger, como no Instagram) a trava não age; as folhas de app são do catálogo (29.87)."""
    final, toques, erros = await _pelo_laco(harness, monkeypatch, navegador=False)
    assert toques and not erros and final.status == "completed"


# ------------------------------------------------------- o drag (leitura da orquestradora): o início e o fim do arrasto
def _aceite_do_drag(harness: Harness, de: tuple[int, int], para: tuple[int, int]) -> str | None:
    from app.automation.tools import Drag, ToolContext
    anuncio = _no(13, "Raspberry Pi 5 8GB", 0, 200, 720, 400)
    tree = parse_hierarchy(_arvore(_PAGINA, _COOKIES, _ACEITAR, anuncio))
    ctx = ToolContext(io=None, call=None, tree=tree, width=720, height=1280, image_scale=1.0,  # type: ignore[arg-type]
                      app_package="com.android.chrome", app_activity=None)
    args = Drag(rationale="r", from_x=de[0], from_y=de[1], to_x=para[0], to_y=para[1])
    executor = harness.state.scheduler.executor                          # type: ignore[union-attr]
    return executor._aceite_do_toque(ctx, args, tree, AiCfg())


def test_drag_curto_dentro_do_botao_e_recusado(harness: Harness) -> None:
    assert _aceite_do_drag(harness, (200, 1180), (210, 1185)) == "Aceitar cookies"


def test_drag_que_comeca_fora_e_termina_no_botao_e_recusado(harness: Harness) -> None:
    assert _aceite_do_drag(harness, (360, 300), (200, 1180)) == "Aceitar cookies"


def test_drag_que_comeca_no_botao_e_termina_fora_e_recusado(harness: Harness) -> None:
    assert _aceite_do_drag(harness, (200, 1180), (360, 300)) == "Aceitar cookies"


def test_drag_de_rolagem_longe_do_aviso_passa(harness: Harness) -> None:
    assert _aceite_do_drag(harness, (360, 600), (360, 300)) is None


async def test_pelo_laco_o_drag_de_aceite_nao_chega_ao_aparelho(harness: Harness, monkeypatch: Any) -> None:
    """O ator roteirizado ARRASTA uma vez; com a trava, nenhum `swipe` chega ao aparelho falso."""
    from app.planning.provider import Decision, Usage
    from app.taskqueue import executor as modulo
    harness.pular_o_tempo()
    monkeypatch.setattr(modulo, "e_navegador", lambda pacote: True)
    monkeypatch.setattr(modulo, "toque_que_aceita", lambda tree, alvo: "Aceitar cookies" if alvo is not None else None)
    decide0, feito = harness.ai.inner.decide, {"n": 0}

    async def decide(req: Any) -> Any:
        if feito["n"] == 0:
            feito["n"] = 1
            return Decision(tool="drag", args={"rationale": "[simulado] arrasta no aviso", "from_x": 200,
                                               "from_y": 600, "to_x": 210, "to_y": 605}), Usage()
        return await decide0(req)

    harness.ai.inner.decide = decide
    fake = harness.fakes["android-01"]
    antes = len(fake.calls)
    run = harness.run(["android-01"])
    await harness.wait_run(run.id, statuses=("completed", "completed_with_issues", "failed", "waiting_user",
                                             "needs_input", "uncertain"))
    assert not [c for c in fake.calls[antes:] if c.startswith("swipe")]
    assert harness.state.db.scalar(                                       # type: ignore[union-attr]
        "SELECT COUNT(*) FROM actions a JOIN attempts t ON t.id=a.attempt_id JOIN steps s ON s.id=t.step_id "
        "WHERE s.run_id=? AND a.tool='drag' AND a.status='rejected'", (run.id,)) == 1
