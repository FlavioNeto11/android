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
    AUTORIA_NAO_CONFIRMADA_TEXTO,
    MARCA_DA_PERGUNTA,
    MOTIVO_ESCRITA_POR_APP,
    PAPEIS_DAS_PERGUNTAS,
    REPASSE_RESPOSTA_A_PERGUNTA,
    RESPOSTA_A_PERGUNTA,
    SEM_NUMERO_DA_PERGUNTA,
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


@pytest.mark.parametrize("nome", ["📌 Como responder aqui", "p-006. minúscula", "Re: P-006", "P-06. dois dígitos"])
async def test_sem_o_numero_no_nome_a_resposta_vai_marcada_para_conferir(tmp_path: Path, nome: str) -> None:
    """Revisão do #447: sem `P-NNN` no começo do nome, a resposta chega à orquestradora marcada, não calada."""
    c = await _cenario(tmp_path)
    acao = c.trello.comenta(DONO, C_MANUAL, "sim", lista=L_PERGUNTAS, nome=nome)
    await c.volta()
    linha = c.linha(str(acao["id"]))
    assert linha["responde_a"] == MARCA_DA_PERGUNTA
    previa = _previa(linha)
    assert previa["pergunta"] is None and previa["aviso"] == SEM_NUMERO_DA_PERGUNTA and c.avisos == []


async def test_com_o_numero_a_resposta_nao_leva_o_aviso(tmp_path: Path) -> None:
    c = await _cenario(tmp_path)
    acao = c.trello.comenta(DONO, C_MANUAL, "não", lista=L_PERGUNTAS, nome="P-1234 · quatro dígitos")
    await c.volta()
    previa = _previa(c.linha(str(acao["id"])))
    assert previa["pergunta"] == "P-1234" and "aviso" not in previa


async def test_comentario_com_robo_no_cartao_de_pergunta_e_ignorado(tmp_path: Path) -> None:
    """Revisão do #447: a Central e as sessões escrevem com o token do dono; o 🤖 no começo é o que separa (C-07)."""
    c = await _cenario(tmp_path)
    acao = c.trello.comenta(DONO, C_MANUAL, "🤖 ORQ · resposta registrada: NÃO", lista=L_PERGUNTAS,
                            nome="P-006. Uma pergunta de teste?")
    await c.volta()
    linha = c.linha(str(acao["id"]))
    assert linha["tipo"] == "outro" and not linha["do_dono"]
    assert c.avisos == [] and c.trello.comentarios == [] and c.acoes_da_central() == []
    # 28.52: a linha guarda a autoria da action, para medir que caminho de escrita leva `appCreator`.
    assert linha["responde_a"] == f"{MARCA_DA_PERGUNTA}P-006;autoria=digitado"


@pytest.mark.parametrize(("kw", "autoria"), [({"app": {"id": "x", "authType": "appKeyToken"}}, "app"),
                                             ({"sem_app": True}, "nao_confirmada")])
async def test_comentario_com_robo_guarda_a_autoria_e_nao_vale(tmp_path: Path, kw: dict[str, object],
                                                                 autoria: str) -> None:
    """28.52: o comentário de IA numa lista de perguntas segue `outro`, sem dono, e a linha diz de onde veio."""
    c = await _cenario(tmp_path)
    acao = c.trello.comenta(DONO, C_MANUAL, "🤖 teste de autoria", lista=L_PERGUNTAS,
                            nome="P-006. Uma pergunta de teste?", **kw)  # type: ignore[arg-type]
    await c.volta()
    linha = c.linha(str(acao["id"]))
    assert (linha["tipo"], bool(linha["do_dono"]), linha["estado"]) == ("outro", False, "ignorada")
    assert linha["responde_a"] == f"{MARCA_DA_PERGUNTA}P-006;autoria={autoria}"
    assert c.avisos == [] and c.trello.comentarios == [] and c.acoes_da_central() == []


async def test_comentario_com_robo_fora_das_listas_nao_guarda_nada(tmp_path: Path) -> None:
    c = await _cenario(tmp_path)
    acao = c.trello.comenta(DONO, C_MANUAL, "🤖 nota", lista="lista-de-outra-coisa", nome="P-006. Nome parecido")
    await c.volta()
    assert c.linha(str(acao["id"]))["responde_a"] is None


async def test_sim_no_cartao_de_pergunta_nao_aprova_a_pendencia_aberta(tmp_path: Path) -> None:
    """Revisão do #447: com uma aprovação aberta na Central, o "sim" num cartão de pergunta só vai à orquestradora."""
    c = await _cenario(tmp_path)
    for texto in ("sim", "Sim, pode aprovar", "aprovado"):
        c.trello.comenta(DONO, C_MANUAL, texto, lista=L_PERGUNTAS, nome="P-006. Uma pergunta de teste?")
    await c.volta()
    assert c.acoes_da_central() == []                               # nenhum `decidir`
    assert c.avisos == [] and c.repo.contagens() == {"orquestradora": 3}


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


APP = {"id": "6ac16d9365f66a5f4771ecb6", "authType": "appKeyToken"}


async def test_sem_app_creator_a_resposta_conta(tmp_path: Path) -> None:
    """Revisão do #447 (segundo fator): o que o dono digita no aplicativo ou no site vem com `appCreator` nulo."""
    c = await _cenario(tmp_path)
    acao = c.trello.comenta(DONO, C_MANUAL, "sim", lista=L_PERGUNTAS, nome="P-006. Uma pergunta de teste?", app=None)
    await c.volta()
    linha = c.linha(str(acao["id"]))
    assert linha["estado"] == "orquestradora" and "aviso" not in _previa(linha)


async def test_com_app_creator_a_resposta_e_ignorada(tmp_path: Path) -> None:
    """Escrita pela API com o token do dono (a Central, um script, uma sessão) sem 🤖: não vale como resposta dele."""
    c = await _cenario(tmp_path)
    acao = c.trello.comenta(DONO, C_MANUAL, "sim", lista=L_PERGUNTAS, nome="P-006. Uma pergunta de teste?", app=APP)
    await c.volta()
    linha = c.linha(str(acao["id"]))
    assert linha["estado"] == "ignorada"
    assert _previa(linha) == {"repasse": REPASSE_RESPOSTA_A_PERGUNTA, "motivo": MOTIVO_ESCRITA_POR_APP}
    assert c.avisos == [] and c.trello.comentarios == [] and c.acoes_da_central() == []


async def test_sem_o_campo_a_autoria_nao_se_confirma(tmp_path: Path) -> None:
    """Falha fechada: sem `appCreator` no retorno, a resposta chega à orquestradora marcada e não decide nada."""
    c = await _cenario(tmp_path)
    acao = c.trello.comenta(DONO, C_MANUAL, "sim", lista=L_PERGUNTAS, nome="P-006. Uma pergunta de teste?", sem_app=True)
    await c.volta()
    linha = c.linha(str(acao["id"]))
    assert linha["estado"] == "orquestradora"
    assert _previa(linha)["aviso"] == AUTORIA_NAO_CONFIRMADA_TEXTO
    assert c.avisos == [] and c.acoes_da_central() == []


def test_a_autoria_fica_so_nas_listas_de_perguntas(tmp_path: Path) -> None:
    """Fora das listas de perguntas o `appCreator` não muda nada: lá o segundo fator segue sendo o Telegram (28.30)."""
    c = Cenario(tmp_path)
    for kw in ({"app": APP}, {"sem_app": True}):
        acao = c.trello.comenta(DONO, C_MANUAL, "Autorizado", lista="lista-de-outra-coisa", **kw)  # type: ignore[arg-type]
        r = recebida_da_action(acao, c.cfg.file.trello, chave_do_cartao=lambda _card: None)
        assert r is not None and r.responde_a is None and r.do_dono
