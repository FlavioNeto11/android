"""Herança de argumento dentro de um plano (rodada seguinte ao ADR-053).

A guarda de cartão (C10) só agia se o planejador repetisse `caption_contains` em CADA etapa da publicação: bastava ele
citar a legenda só em OPEN_POST para a curtida, o balão dos comentários e o comentário voltarem a valer em qualquer
cartão — exatamente o erro de r-20260928165254-e31953. Agora a ação declara, no catálogo (dado, ADR-052), quais
argumentos opcionais HERDA (`inherited_bindings`): sem valor na etapa, o valor vem da etapa anterior mais próxima do
mesmo plano que declara esse argumento. A regra é genérica; nenhum nome de argumento de app mora no código.

Nível de prova: `simulated` (composição do plano, sem aparelho). O caminho até o executor está em
`test_alvo_por_legenda.py::test_legenda_so_na_abertura_ainda_guarda_o_balao_dos_comentarios`.
"""
from __future__ import annotations

from app.planning.capabilities import CapabilityCatalog, CapabilityNode, catalogo_de_dados, compose, load_catalog

from .fake_instagram import PKG

LEGENDA = "Ainda sobre Setembro Amarelo 2024"


def _instagram() -> CapabilityCatalog:
    catalogo = load_catalog(PKG)
    assert catalogo is not None
    return catalogo


# ============================================================ o caso do pedido: legenda só na abertura do post
def test_legenda_so_no_open_post_e_herdada_por_curtir_comentarios_e_comentar() -> None:
    steps, missing = compose(_instagram(), [
        CapabilityNode(key="post", capability="OPEN_POST",
                       bindings={"target": f"publicação com o texto \"{LEGENDA}\"", "caption_contains": LEGENDA}),
        CapabilityNode(key="curtir", capability="LIKE_POST", depends_on=["post"]),
        CapabilityNode(key="coment", capability="OPEN_COMMENTS", depends_on=["curtir"]),
        CapabilityNode(key="c1", capability="CREATE_COMMENT", depends_on=["coment"],
                       bindings={"content_brief": "elogiar"})])
    assert not missing
    por_chave = {s.key: s for s in steps}
    for chave in ("curtir", "coment", "c1"):
        assert por_chave[chave].bindings.get("caption_contains") == LEGENDA, chave
    # e com ela as guardas que o catálogo monta a partir do argumento: o toque de efeito exige a legenda na tela
    assert por_chave["curtir"].commit_guard == [LEGENDA]
    assert por_chave["c1"].commit_guard == [LEGENDA]            # o `{content}` ainda nasce por perfil, no gate
    # o que a etapa não recebeu nem herdou continua fora: a intenção do comentário não vai para a curtida
    assert "content_brief" not in por_chave["curtir"].bindings


def test_as_acoes_que_herdam_a_legenda_estao_declaradas_no_catalogo() -> None:
    """O dado é quem decide: OPEN_POST começa um alvo novo (não herda), as etapas seguintes da publicação herdam."""
    herdam = {c.key: c.inherited_bindings for c in _instagram().capabilities if c.inherited_bindings}
    # ADR-055: o autor da publicação (`post_author`) nasce em OPEN_POST e segue para curtir, descurtir e comentar.
    assert herdam == {"OPEN_COMMENTS": ("caption_contains",), "LIKE_POST": ("caption_contains", "post_author"),
                      "UNLIKE_POST": ("post_author",), "CREATE_COMMENT": ("caption_contains", "post_author")}


def test_valor_da_propria_etapa_vence_o_herdado() -> None:
    steps, missing = compose(_instagram(), [
        CapabilityNode(key="post", capability="OPEN_POST", bindings={"target": "x", "caption_contains": LEGENDA}),
        CapabilityNode(key="curtir", capability="LIKE_POST", bindings={"caption_contains": "Outubro Rosa"})])
    assert not missing
    assert steps[1].bindings["caption_contains"] == "Outubro Rosa"


def test_um_novo_open_post_por_posicao_corta_a_heranca() -> None:
    """"Curta o post X de @a e a primeira publicação de @b": o segundo OPEN_POST é por posição. Nem ele herda X (não
    declara herança), nem a curtida seguinte — a etapa anterior mais próxima que declara o argumento o deixou vazio.
    Herdar ali exigiria a legenda de @a no post de @b e travaria a etapa."""
    steps, missing = compose(_instagram(), [
        CapabilityNode(key="post_a", capability="OPEN_POST",
                       bindings={"target": "post X", "caption_contains": LEGENDA}),
        CapabilityNode(key="curtir_a", capability="LIKE_POST"),
        CapabilityNode(key="perfil_b", capability="OPEN_PROFILE", bindings={"username": "@b"}),
        CapabilityNode(key="post_b", capability="OPEN_POST", bindings={"target": "primeira publicação da grade"}),
        CapabilityNode(key="curtir_b", capability="LIKE_POST")])
    assert not missing
    por_chave = {s.key: s for s in steps}
    assert por_chave["curtir_a"].bindings.get("caption_contains") == LEGENDA
    for chave in ("post_b", "curtir_b"):
        assert not por_chave[chave].bindings.get("caption_contains"), chave
    assert por_chave["curtir_b"].commit_guard == []              # por posição, como hoje


def test_etapa_que_nao_declara_o_argumento_nao_corta_a_heranca() -> None:
    """OPEN_PROFILE não fala de legenda: entre abrir o post e curti-lo, ela não muda de que publicação se trata."""
    steps, _ = compose(_instagram(), [
        CapabilityNode(key="post", capability="OPEN_POST", bindings={"target": "x", "caption_contains": LEGENDA}),
        CapabilityNode(key="perfil", capability="OPEN_PROFILE", bindings={"username": "@a"}),
        CapabilityNode(key="curtir", capability="LIKE_POST")])
    assert steps[2].bindings.get("caption_contains") == LEGENDA


def test_texto_do_comentario_anterior_nao_e_herdado() -> None:
    """`content_brief` é opcional em CREATE_COMMENT, mas não é herdável: o segundo comentário sem intenção vira
    pergunta ao usuário, em vez de repetir a do primeiro."""
    _, missing = compose(_instagram(), [
        CapabilityNode(key="post", capability="OPEN_POST", bindings={"target": "x", "caption_contains": LEGENDA}),
        CapabilityNode(key="coment", capability="OPEN_COMMENTS"),
        CapabilityNode(key="c1", capability="CREATE_COMMENT", bindings={"content_brief": "elogiar"}),
        CapabilityNode(key="c2", capability="CREATE_COMMENT")])
    assert [m.field for m in missing] == ["create_comment"]
    assert "content_brief" in missing[0].question


# ============================================================ a regra é genérica: outro app, só com dado
def _acao(key: str, **campos: object) -> dict[str, object]:
    base: dict[str, object] = {"key": key, "title": key.title(), "goal": f"{key}.", "post_kind": "model_judged",
                               "post_value": "feito", "post_description": "Feito."}
    base.update(campos)
    return base


def _email() -> CapabilityCatalog:
    return catalogo_de_dados({"app": "com.exemplo.email", "contract_version": 1, "acoes": [
        _acao("ABRIR_PASTA", optional_bindings=["pasta"]),
        _acao("LEVANTAR", collect=True, post_kind="items_collected"),
        _acao("LER", optional_bindings=["pasta", "remetente"], inherited_bindings=["pasta"]),
        _acao("ARQUIVAR", optional_bindings=["pasta", "remetente"], inherited_bindings=["pasta", "remetente"],
              commit_guard=["{pasta}", "{remetente}"]),
    ]})


def test_heranca_vale_para_qualquer_app_que_a_declare() -> None:
    steps, missing = compose(_email(), [
        CapabilityNode(key="pasta", capability="ABRIR_PASTA", bindings={"pasta": "Trabalho"}),
        CapabilityNode(key="ler", capability="LER", bindings={"remetente": "ana"}),
        CapabilityNode(key="arquivar", capability="ARQUIVAR")])
    assert not missing
    ler, arquivar = steps[1], steps[2]
    assert ler.bindings == {"remetente": "ana", "pasta": "Trabalho"}
    # herança em cadeia, pelo valor EFETIVO: `pasta` chegou a LER herdada e segue para ARQUIVAR
    assert arquivar.bindings == {"pasta": "Trabalho", "remetente": "ana"}
    assert arquivar.commit_guard == ["Trabalho", "ana"]


def test_argumento_opcional_nao_declarado_herdavel_nao_e_herdado() -> None:
    steps, _ = compose(_email(), [
        CapabilityNode(key="ler_1", capability="LER", bindings={"pasta": "Trabalho", "remetente": "ana"}),
        CapabilityNode(key="ler_2", capability="LER")])
    assert steps[1].bindings == {"pasta": "Trabalho"}           # `remetente` é opcional em LER, mas não herdável


def test_valor_de_dentro_de_um_for_each_nao_vaza_para_fora_do_bloco() -> None:
    """Dentro do bloco, `{item}` é resolvido em cada cópia; fora dele, viraria o texto literal "{item}" numa guarda."""
    steps, missing = compose(_email(), [
        CapabilityNode(key="levantar", capability="LEVANTAR"),
        CapabilityNode(key="ler", capability="LER", for_each="levantar", bindings={"pasta": "{item}"}),
        CapabilityNode(key="arquivar_i", capability="ARQUIVAR", for_each="levantar"),
        CapabilityNode(key="arquivar_fim", capability="ARQUIVAR")])
    assert not missing
    por_chave = {s.key: s for s in steps}
    assert por_chave["arquivar_i"].bindings.get("pasta") == "{item}"        # mesmo bloco: herda
    assert "pasta" not in por_chave["arquivar_fim"].bindings                 # fora do bloco: não
    assert por_chave["arquivar_fim"].commit_guard == []


def test_valor_fixo_de_antes_do_bloco_vale_dentro_dele() -> None:
    steps, _ = compose(_email(), [
        CapabilityNode(key="pasta", capability="ABRIR_PASTA", bindings={"pasta": "Trabalho"}),
        CapabilityNode(key="levantar", capability="LEVANTAR"),
        CapabilityNode(key="ler", capability="LER", for_each="levantar", bindings={"remetente": "{item}"})])
    assert steps[2].bindings == {"remetente": "{item}", "pasta": "Trabalho"}
