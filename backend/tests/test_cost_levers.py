"""Alavancas de custo de IA: contadas NO PROVEDOR (ver CountingProvider), nunca pelo banco.
- depois do toque de commit não há outra decisão: vai direto à verificação;
- a verificação julgada por visão só chama o modelo de novo quando a tela mudou, e respeita o teto;
- etapa com efeito externo (e nova tentativa) decide no modelo de escalonamento;
- política de imagem `auto`: hierarquia rica → decisão sem imagem;
- a árvore local é completa; só as linhas do prompt são limitadas, por relevância; campo de senha nunca escapa."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.automation.hierarchy import parse_hierarchy
from app.planning.capabilities import capability_of
from app.taskqueue.executor import side_effect_tier

from .conftest import Harness


async def test_commit_vai_direto_a_verificacao_e_usa_modelo_forte(harness: Harness) -> None:
    run = harness.run(["android-01"])
    detail = await harness.wait_run(run.id)
    assert detail.status == "completed" and len(harness.fakes["android-01"].messages) == 1
    ai = harness.ai
    # uma única decisão na etapa de envio (o toque); antes eram duas (toque + step_done)
    assert ai.count("decide", step="send_message") == 1
    assert ai.count("decide", step="send_message", tier=1) == 1          # efeito externo → modelo de escalonamento
    assert ai.count("decide", step="open_conversation", tier=0) >= 1     # etapas comuns → modelo barato
    # uso por chamada gravado com a função (base do relatório de custo)
    rows = harness.state.db.query("SELECT role, COUNT(*) n FROM ai_calls WHERE run_id=? GROUP BY role", (run.id,))  # type: ignore[union-attr]
    by_role = {r["role"]: r["n"] for r in rows}
    assert by_role["plan"] == 1 and by_role["decide"] == ai.count("decide") and by_role["verify"] == ai.count("verify")


async def test_verificacao_nao_rejulga_tela_igual_e_respeita_o_teto(harness: Harness) -> None:
    fake = harness._factory(type("RT", (), {"id": "android-01", "index": 1})())   # cria o aparelho antes do boot da etapa
    fake.sent_after_s, fake.delivered_after_s = 4.0, 4.5          # fica "enviando…" por 4 s: tela igual por vários ciclos
    run = harness.run(["android-01"])
    detail = await harness.wait_run(run.id, timeout=60)
    assert detail.status == "completed"
    # com a tela parada em "enviando…" por 4 s o verificador antigo gastava até 5 chamadas por etapa julgada;
    # agora só rejulga quando a tela muda e respeita o teto por etapa
    cap = harness.cfg.file.ai.verify_max_model_calls
    assert 1 <= harness.ai.count("verify", step="send_message") <= cap
    assert 1 <= harness.ai.count("verify", step="verify_sent") <= cap
    assert harness.ai.count("verify") <= 3
    assert len(fake.messages) == 1


async def test_politica_de_imagem_auto_decide_pela_hierarquia(harness: Harness) -> None:
    harness.cfg.file.ai.image_policy = "auto"
    harness.cfg.file.ai.rich_tree_min_elements = 3
    run = harness.run(["android-02"])
    assert (await harness.wait_run(run.id)).status == "completed"
    decides = [c for c in harness.ai.calls if c["role"] == "decide"]
    assert decides and sum(1 for c in decides if c["image"]) < len(decides) / 2      # a maioria sem imagem
    assert any(not c["image"] for c in decides if c["step"] == "open_conversation")


def test_arvore_completa_linhas_priorizadas_e_senha_nunca_escapa() -> None:
    filler = "".join(f'<node class="android.view.View" resource-id="app:id/deco{i}" bounds="[0,{i}][10,{i + 5}]"/>'
                     for i in range(300))
    xml = ("<hierarchy>" + filler
           + '<node class="android.widget.Button" text="Enviar" clickable="true" resource-id="app:id/send" bounds="[0,900][100,960]"/>'
           + '<node class="android.widget.EditText" password="true" text="1234" resource-id="app:id/pin" bounds="[0,1000][100,1060]"/>'
           + "</hierarchy>")
    tree = parse_hierarchy(xml)
    assert len(tree.elements) == 302 and tree.sensitive                       # nada é cortado localmente
    assert tree.find_selector("id=send|text=Enviar")                          # seletores veem a árvore inteira
    lines = tree.prompt_lines(40)
    assert len(lines) == 41 and "omitidos" in lines[-1]
    assert any('text="Enviar"' in ln for ln in lines)                         # o operável entra antes do decorativo
    assert not any("1234" in ln for ln in lines)                              # senha mascarada

    capped = parse_hierarchy(xml, max_elements=50)                            # mesmo com teto, a senha é detectada
    assert len(capped.elements) == 50 and capped.sensitive


def test_limites_no_prompt_usam_o_espaco_da_imagem_reduzida() -> None:
    """Com screenshot_max_side < lado do aparelho, um x,y tirado dos limites mostrados tem de cair no elemento certo."""
    import re

    from app.automation.tools import ToolContext, resolve_point

    xml = ('<hierarchy><node class="android.widget.FrameLayout" bounds="[0,0][720,1280]">'
           '<node class="android.widget.Button" text="Enviar" clickable="true" resource-id="app:id/send" bounds="[600,1180][700,1240]"/>'
           '<node class="android.widget.EditText" text="" clickable="true" resource-id="app:id/input" bounds="[20,1180][580,1240]"/>'
           "</node></hierarchy>")
    tree = parse_hierarchy(xml)
    scale = 1280 / 768                                                        # aparelho 720×1280 visto como 432×768
    ctx = ToolContext(io=None, call=None, tree=tree, width=720, height=1280, image_scale=scale,  # type: ignore[arg-type]
                      app_package=None, app_activity=None)
    for line in tree.prompt_lines(90, scale):
        if "omitidos" in line:
            continue
        x1, y1, x2, y2 = map(int, re.search(r"\[(\d+),(\d+),(\d+),(\d+)\]$", line).groups())
        assert x2 <= 432 and y2 <= 768                                        # nunca fora da imagem que o modelo recebe
        if "id=send" in line:
            _, _, el = resolve_point(ctx, None, (x1 + x2) // 2, (y1 + y2) // 2)
            assert el is not None and el.resource_id.endswith("send")
    assert tree.find_selector("id=send")[0].line().endswith("[600,1180,700,1240]")           # escala 1 = pixels do aparelho, como antes


# ---------------------------------------------------------------- achado #102: DM longa comprovada sem truncar
def _mensagem_no_fio(conteudo: str, *, tambem_no_campo: bool = False) -> str:
    """Tela mínima de conversa: a mensagem já publicada (TextView, não editável) + o campo de escrita (EditText)."""
    campo_texto = conteudo if tambem_no_campo else ""
    return ('<hierarchy><node class="android.widget.FrameLayout" bounds="[0,0][720,1280]">'
            f'<node class="android.widget.TextView" text="{conteudo}" resource-id="app:id/message_text" '
            'bounds="[40,200][680,260]"/>'
            f'<node class="android.widget.EditText" text="{campo_texto}" clickable="true" '
            'resource-id="app:id/row_arrow_edit_text" bounds="[20,1180][580,1240]"/>'
            "</node></hierarchy>")


def test_prompt_lines_nao_corta_texto_protegido_com_81_e_300_caracteres() -> None:
    """`protect` evita o corte em 80 chars (achado #102): o próprio conteúdo comprovado chega inteiro ao modelo."""
    for tamanho in (81, 300):
        conteudo = "x" * tamanho
        tree = parse_hierarchy(_mensagem_no_fio(conteudo))
        sem_protecao = tree.prompt_lines(40)
        assert any(f'text="{"x" * 80}"' in ln for ln in sem_protecao)          # comportamento antigo: cortado em 80
        assert not any(conteudo in ln for ln in sem_protecao)

        protegido = tree.prompt_lines(40, protect=(conteudo,))
        assert any(f'text="{conteudo}"' in ln for ln in protegido)             # com proteção: inteiro


def test_prova_local_de_dm_enviada_exige_fora_do_campo_de_escrita() -> None:
    """`UiTree.sent_as_message`: só prova quando o conteúdo está numa mensagem do fio E ausente do campo — nunca
    reprova por si só (None/False caem para o julgamento do modelo, nunca viram 'falhou')."""
    for tamanho in (81, 300):
        conteudo = "y" * tamanho
        enviada = parse_hierarchy(_mensagem_no_fio(conteudo))
        assert enviada.sent_as_message(conteudo) is True

        ainda_no_campo = parse_hierarchy(_mensagem_no_fio(conteudo, tambem_no_campo=True))
        assert ainda_no_campo.sent_as_message(conteudo) is False              # só no campo: não prova envio

    vazia = parse_hierarchy(_mensagem_no_fio("y" * 81))
    assert vazia.sent_as_message("") is None                                   # sem conteúdo conhecido: não opina
    assert vazia.sent_as_message("outra coisa que não está na tela") is False


def test_catalogo_do_instagram_declara_prova_local_para_enviar_mensagem() -> None:
    cap = capability_of("com.instagram.android", "SEND_MESSAGE")
    assert cap is not None and cap.local_proof == "sent_text"


# ---------------------------------------------------------------- provas locais por seletor (24/09)
def _post_com_curtida(desc_curtir: str, *, comentarios: tuple[tuple[str, str], ...] = ()) -> str:
    """Publicação aberta (título "Posts") com o botão de curtir na descrição dada; opcionalmente uma folha de
    comentários, cada um com (autor, desc do coração) na própria faixa vertical."""
    linhas = "".join(
        f'<node class="android.widget.TextView" text="{autor} said oi" bounds="[40,{400 + i * 200}][500,{460 + i * 200}]"/>'
        f'<node class="android.widget.ImageView" content-desc="{desc}" clickable="true" '
        f'resource-id="com.instagram.android:id/row_comment_like_button" bounds="[600,{400 + i * 200}][660,{460 + i * 200}]"/>'
        for i, (autor, desc) in enumerate(comentarios))
    return ('<hierarchy><node class="android.widget.FrameLayout" bounds="[0,0][720,1280]">'
            '<node class="android.widget.TextView" text="Posts" resource-id="com.instagram.android:id/action_bar_title" '
            'bounds="[100,60][300,120]"/>'
            f'<node class="android.widget.ImageView" content-desc="{desc_curtir}" clickable="true" '
            'resource-id="com.instagram.android:id/row_feed_button_like" bounds="[40,900][100,960]"/>'
            f"{linhas}</node></hierarchy>")


def test_find_selector_exato_nao_casa_liked() -> None:
    """`desc=Like` por substring casa "Liked": num botão de curtir isso é curtir ou descurtir. `==` casa exato."""
    curtido = parse_hierarchy(_post_com_curtida("Liked"))
    assert curtido.find_selector("desc=Like") and not curtido.find_selector("desc==Like")
    assert curtido.find_selector("desc==Liked") and curtido.find_selector("id=action_bar_title|text==Posts")
    assert not curtido.find_selector("id=action_bar_title|text==Post")


def test_prova_local_por_seletor_positiva_negativa_e_none() -> None:
    from app.taskqueue.proofs import local_proof_holds

    etapa = SimpleNamespace(bindings={"username": "@ana"}, band_guard=["@ana"])
    curtido, nao = parse_hierarchy(_post_com_curtida("Liked")), parse_hierarchy(_post_com_curtida("Like"))
    assert local_proof_holds("selector:desc==Liked", etapa, curtido) is True
    assert local_proof_holds("selector:desc==Liked", etapa, nao) is False        # negativa: o modelo julga
    assert local_proof_holds(None, etapa, curtido) is None
    assert local_proof_holds("selector:text=={outra}", etapa, curtido) is None    # variável sem valor: não opina
    # `{username}` resolvido e a arroba opcional: a tela mostra "ana", a etapa conhece "@ana"
    perfil = parse_hierarchy('<hierarchy><node class="android.widget.TextView" text="ana" '
                             'resource-id="com.instagram.android:id/action_bar_title" bounds="[0,0][200,50]"/></hierarchy>')
    assert local_proof_holds("selector:id=action_bar_title|text=={username}", etapa, perfil) is True
    # faixa: o coração marcado tem de estar na linha do comentário de @ana, não na de cima
    de_cima = parse_hierarchy(_post_com_curtida("Like", comentarios=(("bia", "Liked"), ("ana", "Like"))))
    o_dela = parse_hierarchy(_post_com_curtida("Like", comentarios=(("bia", "Like"), ("ana", "Liked"))))
    assert local_proof_holds("selector_band:desc==Liked", etapa, de_cima) is False
    assert local_proof_holds("selector_band:desc==Liked", etapa, o_dela) is True
    assert local_proof_holds("selector_band:desc==Liked", SimpleNamespace(bindings={}, band_guard=[]), o_dela) is False


def test_catalogo_do_instagram_declara_provas_locais_bem_formadas() -> None:
    from app.planning.capabilities import local_proof_error

    esperado = {"LIKE_POST": "selector:desc==Liked", "UNLIKE_POST": "selector:desc==Like",
                "LIKE_COMMENT": "selector_band:desc==Liked", "OPEN_PROFILE": "selector:id=action_bar_title|text=={username}",
                "OPEN_THREAD": "selector:text=={username}", "SEND_MESSAGE": "sent_text"}
    for chave, prova in esperado.items():
        cap = capability_of("com.instagram.android", chave)
        assert cap is not None and cap.local_proof == prova and local_proof_error(cap.local_proof) is None, chave
    # o commit de curtir é EXATO: "Like" não pode casar "Liked"
    assert capability_of("com.instagram.android", "LIKE_POST").commit_selector == "desc==Like"  # type: ignore[union-attr]
    assert local_proof_error("selector:") and local_proof_error("xpath:/x") and local_proof_error(None) is None
    for chave, valor in (("OPEN_POST", "id=action_bar_title|text==Posts"), ("OPEN_COMMENTS", "id=title_text_view|text==Comments"),
                         ("OPEN_FEED", "id=main_feed_action_bar")):        # lido da hierarquia real do app 447
        cap = capability_of("com.instagram.android", chave)
        assert cap is not None and cap.post_kind == "element_present" and cap.post_value == valor


# ---------------------------------------------------------------- escalonamento por risco (24/09)
def _etapa(*, side_effect: bool = True, commit_selector: str | None = None) -> Any:
    return SimpleNamespace(side_effect=side_effect, commit_selector=commit_selector)


def test_tier_por_risco_medium_com_seletor_fica_no_modelo_barato() -> None:
    """LIKE_POST: risco médio e `commit_selector` declarado — o executor já trava o alvo; o modelo caro não
    acrescentava nada e era 39 % das decisões."""
    cap = capability_of("com.instagram.android", "LIKE_POST")
    assert cap is not None and cap.risk == "medium" and cap.commit_selector
    assert side_effect_tier(_etapa(), cap, "by_risk") == (0, "")


def test_tier_por_risco_high_sobe_com_o_motivo_na_linha_do_tempo() -> None:
    cap = capability_of("com.instagram.android", "FOLLOW")
    assert cap is not None and cap.risk == "high"
    tier, motivo = side_effect_tier(_etapa(), cap, "by_risk")
    assert tier == 1 and motivo.startswith("etapa com efeito externo") and "FOLLOW" in motivo


def test_tier_por_risco_medium_sem_seletor_de_commit_sobe() -> None:
    cap = SimpleNamespace(key="X", risk="medium", commit_selector=None)
    assert side_effect_tier(_etapa(), cap, "by_risk")[0] == 1
    assert side_effect_tier(_etapa(commit_selector="id=ok"), cap, "by_risk") == (0, "")   # o da etapa também vale


def test_tier_por_risco_sem_catalogo_mantem_o_de_hoje() -> None:
    """QA Messenger não tem catálogo: risco desconhecido continua subindo (é o que `test_commit_vai_direto…` prova)."""
    tier, motivo = side_effect_tier(_etapa(), None, "by_risk")
    assert tier == 1 and motivo.startswith("etapa com efeito externo")


def test_tier_true_e_false_preservam_o_comportamento_antigo() -> None:
    cap = capability_of("com.instagram.android", "LIKE_POST")
    assert side_effect_tier(_etapa(), cap, True) == (1, "etapa com efeito externo")
    assert side_effect_tier(_etapa(), cap, False) == (0, "")
    assert side_effect_tier(_etapa(side_effect=False), None, True) == (0, "")           # sem efeito, nada sobe
