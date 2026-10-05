"""Item 28.51 (parte A): o comentário do dono num cartão das listas de perguntas é a resposta dele, não um comentário de
cartão do plano. Defeito de 05/10 ~16:10Z: as cinco respostas às perguntas P-001 a P-005 pediram confirmação no Telegram
pelo caminho do 28.30, e a regra do dono é que pergunta e resposta fiquem só no Trello.

Prova `simulated` (`arquivo::teste`): Trello falso, banco de teste. Cobre: a resposta na lista de perguntas e na de
respondidas vai à orquestradora com o número da pergunta, sem pedido no Telegram e com uma linha só no cartão; o cartão
fora dessas listas segue o 28.30; quem não é o dono não responde; o `/` segue a gramática; e o papel sem lista configurada
não liga nada.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config import PAPEIS_DE_LISTA_DO_TRELLO
from app.modules.avisos.infrastructure.trello_leitor import (
    MARCA_DA_PERGUNTA,
    PAPEIS_DAS_PERGUNTAS,
    REPASSE_RESPOSTA_A_PERGUNTA,
    RESPOSTA_A_PERGUNTA,
    TIPO_DO_COMENTARIO,
    recebida_da_action,
)

from .test_trello_leitor import AMIGO, C_MANUAL, DONO, Cenario

L_PERGUNTAS, L_RESPONDIDAS = "lista-perguntas", "lista-respondidas"


async def _cenario(tmp_path: Path, **kw: object) -> Cenario:
    cen = Cenario(tmp_path, **kw)                                           # type: ignore[arg-type]
    cen.cfg.file.trello.listas.update(perguntas=L_PERGUNTAS, perguntas_respondidas=L_RESPONDIDAS)
    await cen.sobe()
    return cen


def _previa(linha: dict[str, object]) -> dict[str, object]:
    p = linha.get("previa")
    return json.loads(p) if isinstance(p, str) else dict(p or {})          # type: ignore[arg-type]


def test_os_papeis_das_perguntas_sao_aceitos_na_config() -> None:
    assert set(PAPEIS_DAS_PERGUNTAS) <= PAPEIS_DE_LISTA_DO_TRELLO


@pytest.mark.parametrize("lista", [L_PERGUNTAS, L_RESPONDIDAS])
async def test_a_resposta_na_lista_de_perguntas_nao_pede_confirmacao(tmp_path: Path, lista: str) -> None:
    c = await _cenario(tmp_path)
    acao = c.trello.comenta(DONO, C_MANUAL, "Não isso fica no Android.", lista=lista,
                            nome="P-006. Uma pergunta de teste?")
    await c.volta()
    assert c.avisos == []                                    # nada no Telegram
    linha = c.linha(str(acao["id"]))
    assert (linha["estado"], linha["destino"]) == ("orquestradora", "orquestradora")
    assert linha["responde_a"] == f"{MARCA_DA_PERGUNTA}P-006"
    assert _previa(linha) == {"repasse": REPASSE_RESPOSTA_A_PERGUNTA, "texto": "Não isso fica no Android.",
                              "pergunta": "P-006"}
    # Uma linha só no cartão; o eco dela (o token é o do dono) não vira nada.
    assert len(c.trello.comentarios) == 1 and c.trello.comentarios[0][0] == C_MANUAL
    assert RESPOSTA_A_PERGUNTA in c.trello.textos()[0]
    await c.volta()
    await c.volta()
    assert c.avisos == [] and len(c.trello.comentarios) == 1


async def test_sem_o_numero_no_nome_a_resposta_vai_sem_numero(tmp_path: Path) -> None:
    c = await _cenario(tmp_path)
    acao = c.trello.comenta(DONO, C_MANUAL, "sim", lista=L_PERGUNTAS, nome="📌 Como responder aqui")
    await c.volta()
    linha = c.linha(str(acao["id"]))
    assert linha["responde_a"] == MARCA_DA_PERGUNTA
    assert _previa(linha)["pergunta"] is None and c.avisos == []


async def test_fora_das_listas_de_perguntas_o_comentario_segue_o_28_30(tmp_path: Path) -> None:
    c = await _cenario(tmp_path)
    c.trello.comenta(DONO, C_MANUAL, "Autorizado", lista="lista-de-outra-coisa", nome="P-007. Nome parecido")
    await c.volta()
    assert [a.tipo for a in c.avisos] == [TIPO_DO_COMENTARIO]


async def test_quem_nao_e_o_dono_nao_responde_a_pergunta(tmp_path: Path) -> None:
    c = await _cenario(tmp_path, autorizados=[AMIGO])
    acao = c.trello.comenta(AMIGO, C_MANUAL, "sim", lista=L_PERGUNTAS, nome="P-006. Uma pergunta de teste?")
    await c.volta()
    assert c.linha(str(acao["id"]))["estado"] == "ignorada"
    assert c.avisos == [] and c.trello.comentarios == []


async def test_comando_na_lista_de_perguntas_segue_a_gramatica(tmp_path: Path) -> None:
    c = await _cenario(tmp_path)
    acao = c.trello.comenta(DONO, C_MANUAL, "/ajuda", lista=L_PERGUNTAS, nome="P-006. Uma pergunta de teste?")
    await c.volta()
    linha = c.linha(str(acao["id"]))
    assert _previa(linha).get("repasse") != REPASSE_RESPOSTA_A_PERGUNTA and c.avisos == []


def test_papel_sem_lista_configurada_nao_marca_nada(tmp_path: Path) -> None:
    """Com os papéis vazios (instalação sem as listas), nenhuma lista casa: nem a action sem `list`."""
    c = Cenario(tmp_path)
    t = c.cfg.file.trello
    t.listas.update(perguntas="", perguntas_respondidas="")
    for lista in (None, ""):
        acao = c.trello.comenta(DONO, C_MANUAL, "sim", lista=lista, nome="P-006. Uma pergunta de teste?")
        r = recebida_da_action(acao, t, chave_do_cartao=lambda _card: None)
        assert r is not None and r.responde_a is None
