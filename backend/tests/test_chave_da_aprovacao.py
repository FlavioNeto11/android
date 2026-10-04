"""30.61: a chave do item aprovado no plano (desenho 30.61, §0.1). A mesma etapa relida dá a mesma chave; texto,
objeto, mídia, execução ou objetivo diferentes dão outra; o que não se sabe ainda falha fechado (`None`).

Nível de prova: `simulated` (catálogo do Instagram, banco de teste). Nada real.
"""
from __future__ import annotations

from pathlib import Path

from app.planning.capabilities import capability_of
from app.social.chave_da_aprovacao import (
    OBJETO_INSUFICIENTE,
    chave_da_aprovacao,
    midia_da_etapa,
    texto_exato,
)

from .test_capabilities import IG, build

ESCOPO = {"perfil": "p-1", "aparelho": "android-01", "pacote": IG, "run_id": "r-1", "objective_id": "r-1:android-01"}
DM = {"username": "@Fulana", "content": "oi, tudo bem?", "content_verbatim": "true"}


def _chave(acao: str, bindings: dict[str, object], **over: object) -> str | None:
    return chave_da_aprovacao(bindings, capability_of(IG, acao), **{**ESCOPO, **over})  # type: ignore[arg-type]


def test_a_mesma_etapa_relida_da_a_mesma_chave_e_o_alvo_e_normalizado() -> None:
    primeira = _chave("SEND_MESSAGE", DM)
    assert primeira is not None and len(primeira) == 64
    assert _chave("SEND_MESSAGE", dict(DM)) == primeira
    assert _chave("SEND_MESSAGE", {**DM, "username": "fulana"}) == primeira        # @Fulana e fulana: a mesma pessoa


def test_cada_coisa_que_muda_o_que_sai_muda_a_chave() -> None:
    base = _chave("SEND_MESSAGE", DM)
    variacoes = [
        _chave("SEND_MESSAGE", {**DM, "content": "oi, tudo bem? "}),                # o texto é literal
        _chave("SEND_MESSAGE", {**DM, "username": "@outra"}),
        _chave("SEND_MESSAGE", DM, perfil="p-2"),
        _chave("SEND_MESSAGE", DM, aparelho="android-02"),
        _chave("SEND_MESSAGE", DM, run_id="r-2"),
        _chave("SEND_MESSAGE", DM, objective_id="r-1:android-02"),
    ]
    assert base not in variacoes and len(set(variacoes)) == len(variacoes)


def test_texto_por_escrever_ou_objeto_por_resolver_falha_fechado() -> None:
    assert _chave("SEND_MESSAGE", {"username": "@fulana", "content_brief": "agradeça"}) is None
    assert _chave("SEND_MESSAGE", {"username": "@fulana", "content": "oi"}) is None    # sem verbatim: é intenção
    assert _chave("SEND_MESSAGE", {**DM, "username": "{item}"}) is None


def test_curtir_nao_escreve_texto_e_o_objeto_entra_na_chave() -> None:
    cap = capability_of(IG, "LIKE_POST")
    assert texto_exato(cap, {"post_author": "@a"}) == (True, None)
    um = _chave("LIKE_POST", {"post_author": "@a", "caption_contains": "Setembro Amarelo"})
    outro = _chave("LIKE_POST", {"post_author": "@a", "caption_contains": "Outubro Rosa"})
    assert um is not None and outro is not None and um != outro


def test_a_midia_entra_pelo_sha256_dos_bytes(tmp_path: Path) -> None:
    _svc, repo, _p, _db = build(tmp_path)
    assert midia_da_etapa(repo.db, {"caption": "x"}) == (False, None)
    assert midia_da_etapa(repo.db, {"image_id": "img-que-nao-existe"}) == (True, None)
    com_a = _chave("SEND_MESSAGE", DM, tem_imagem=True, midia_sha256="a" * 64)
    com_b = _chave("SEND_MESSAGE", DM, tem_imagem=True, midia_sha256="b" * 64)
    assert com_a is not None and com_b is not None and com_a != com_b
    assert _chave("SEND_MESSAGE", DM, tem_imagem=True, midia_sha256=None) is None   # imagem sem sha256: fechado


# ------------------------------------------------------------------ revisão antecipada da chave (04/10)
def test_objeto_ausente_ou_vazio_falha_fechado() -> None:
    """Curtir "um post de @x" sem dizer qual: na execução o primeiro da grade pode ser outro. Sem chave."""
    assert _chave("LIKE_POST", {"post_author": "@a"}) is None
    assert _chave("LIKE_POST", {"post_author": "@a", "caption_contains": "  "}) is None
    assert _chave("CREATE_COMMENT", {"post_author": "@a", "content": "lindo!", "content_verbatim": "true"}) is None


def test_texto_com_variavel_por_resolver_falha_fechado() -> None:
    assert _chave("SEND_MESSAGE", {**DM, "content": "oi {item}"}) is None
    assert _chave("SEND_MESSAGE", {**DM, "content": "oi {{saida:nome}}"}) is None


def test_alvo_vazio_falha_fechado() -> None:
    assert _chave("SEND_MESSAGE", {**DM, "username": ""}) is None
    assert _chave("SEND_MESSAGE", {"content": "oi", "content_verbatim": "true"}) is None


def test_argumento_fora_do_objeto_alvo_entra_na_chave() -> None:
    """A chave leva todos os argumentos da etapa, não só o `objeto_alvo`: o mesmo DM com outro argumento é outro item."""
    um = _chave("SEND_MESSAGE", {**DM, "thread_hint": "conversa de ontem"})
    outro = _chave("SEND_MESSAGE", {**DM, "thread_hint": "conversa de hoje"})
    assert um is not None and outro is not None and um != outro
    assert _chave("SEND_MESSAGE", {**DM, "thread_hint": "conversa de {item}"}) is None


def test_acao_com_objeto_insuficiente_nunca_se_aprova_no_plano() -> None:
    """REPLY_COMMENT declara só `username`: o mesmo @ tem vários comentários. Chave `None` sempre, até o catálogo dizer
    QUAL comentário (decisão da orquestradora, 04/10); aí a ação sai de `OBJETO_INSUFICIENTE`."""
    assert OBJETO_INSUFICIENTE == {"REPLY_COMMENT"}
    base = {"username": "@a", "content": "obrigada!", "content_verbatim": "true"}
    assert _chave("REPLY_COMMENT", {**base, "target": "comentário 'que lindo'"}) is None
    assert _chave("REPLY_COMMENT", base) is None
