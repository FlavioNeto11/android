"""31.112: a pergunta da IA guardada na proposta não traz o dado da persona em claro (achado da prova real do 31.87).

A troca do 31.87 F2 cobria as etapas e o comando, mas não `questions[]` nem `answers[].question` (31.91): o nome que
a pessoa digitou ficava em claro no banco, no texto da pergunta da IA. Agora a pergunta leva o mesmo marcador ao
GUARDAR (o `propose`, com e sem respostas) e ao MOSTRAR (`training.get` e `training.list`, para a sessão gravada antes).
A resposta da pessoa fica como ela escreveu, e a pergunta devolvida com o valor ainda casa com a guardada.

Nível de prova: `simulated` (harness com aparelho falso e provedor simulado com espião; nenhuma IA paga).
"""
from __future__ import annotations

from app.db import dumps
from app.training import dado_da_persona as dp

from .conftest import Harness
from .test_treino_dado_da_persona import EMAIL, _sessao_com_persona
from .test_treino_proposta_com_respostas import _espiar, _resp

PERGUNTA = f"O e-mail {EMAIL} é sempre o mesmo?"
MASCARADA = "O e-mail {perfil_email} é sempre o mesmo?"
OUTRA = "Precisa confirmar o envio?"


def _bruta(st: object, sid: str) -> str:
    """A coluna como está no banco, sem a leitura do gravador."""
    return str(st.db.scalar("SELECT proposal FROM training_sessions WHERE id=?", (sid,)))  # type: ignore[attr-defined]


# ------------------------------------------------------------------ a função pura
def test_a_pergunta_e_a_pergunta_respondida_levam_o_marcador_e_a_resposta_fica() -> None:
    p = {"questions": [PERGUNTA, OUTRA, 3], "answers": [_resp(PERGUNTA, f"sim, {EMAIL}"), "torta"], "summary": "x"}
    nova = dp.nas_perguntas(p, {"perfil_email": EMAIL})
    assert nova["questions"] == [MASCARADA, OUTRA, 3]
    assert nova["answers"] == [_resp(MASCARADA, f"sim, {EMAIL}"), "torta"]     # a resposta é da pessoa
    assert nova["summary"] == "x" and p["questions"][0] == PERGUNTA             # não muda a original


def test_sem_persona_ou_valor_dentro_de_outra_palavra_nada_muda() -> None:
    p = {"questions": ["A banana chegou?"], "answers": [_resp("A banana chegou?", "sim")]}
    assert dp.nas_perguntas(p, {}) == p
    assert dp.nas_perguntas(p, {"perfil_nome": "nana"}) == p
    assert dp.nas_perguntas({"answers": None}, {"perfil_email": EMAIL}) == {"answers": None}


# ------------------------------------------------------------------ ao guardar
async def test_o_propose_guarda_a_pergunta_nova_da_ia_com_o_marcador(harness: Harness) -> None:
    st, sid = await _sessao_com_persona(harness)
    _espiar(harness, [PERGUNTA, OUTRA])
    await st.skills.propose(sid)
    assert EMAIL not in _bruta(st, sid) and MASCARADA in _bruta(st, sid)
    assert st.training.get(sid)["proposal"]["questions"] == [MASCARADA, OUTRA]


async def test_a_resposta_casa_com_a_pergunta_mascarada_e_com_a_em_claro(harness: Harness) -> None:
    """O painel mostra a mascarada e a devolve; um cliente aberto antes do 31.112 devolve a em claro. As duas casam com
    a guardada, e o banco fica só com o marcador na pergunta."""
    st, sid = await _sessao_com_persona(harness)
    pedidos = _espiar(harness, [PERGUNTA, OUTRA])
    await st.skills.propose(sid)
    await st.skills.propose(sid, {"answers": [_resp(MASCARADA, "sempre o mesmo")]})
    assert st.training.get(sid)["proposal"]["answers"] == [_resp(MASCARADA, "sempre o mesmo")]
    await st.skills.propose(sid, {"answers": [_resp(PERGUNTA, "muda às vezes")]})       # a mesma pergunta, em claro
    guardada = st.training.get(sid)["proposal"]
    assert guardada["answers"] == [_resp(MASCARADA, "muda às vezes")]                   # substituiu, não somou
    assert guardada["questions"] == [OUTRA]
    assert EMAIL not in _bruta(st, sid)
    assert list(pedidos[-1].answers) == [_resp(MASCARADA, "muda às vezes")]   # a IA recebe o marcador


# ------------------------------------------------------------------ ao mostrar
async def test_a_sessao_guardada_antes_sai_com_o_marcador_na_leitura(harness: Harness) -> None:
    st, sid = await _sessao_com_persona(harness)
    antiga = {"summary": "s", "steps": [], "questions": [PERGUNTA], "answers": [_resp(PERGUNTA, "sim")]}
    st.db.execute("UPDATE training_sessions SET proposal=?, status='proposed' WHERE id=?", (dumps(antiga), sid))
    lida = st.training.get(sid)["proposal"]
    assert lida["questions"] == [MASCARADA] and lida["answers"] == [_resp(MASCARADA, "sim")]
    [da_lista] = [s for s in st.training.list() if s["id"] == sid]
    assert EMAIL not in str(da_lista["proposal"]) and MASCARADA in str(da_lista["proposal"])


async def test_o_treino_sem_persona_mostra_a_pergunta_como_veio(harness: Harness) -> None:
    st, sid = await _sessao_com_persona(harness, persona=None)
    _espiar(harness, [PERGUNTA])
    await st.skills.propose(sid)
    assert st.training.get(sid)["proposal"]["questions"] == [PERGUNTA]
