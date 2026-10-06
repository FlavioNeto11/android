"""31.114 F2: o arraste que termina a etapa vira receita (item `scroll` relativo) SÓ quando a pessoa confirmou e a saída não foi
da borda. As três recusas conservam o motivo de sempre ("rolagem sem ação-alvo depois dela").

Nível de prova: `simulated` (função pura da destilação e sessão com aparelho falso; nenhuma IA)."""
from __future__ import annotations

import pytest

from app.planning.training import linha_da_entrada
from app.taskqueue.recipes import distill_training
from app.training.arraste import arrastes_finais, confirmou, pergunta, pode_ser_receita

TELA = (720, 1280)
TOQUE = {"seq": 1, "type": "tap", "x": 1, "y": 2, "sensitive": False,
         "target": {"resource_id": "com.exemplo:id/lista", "unique": ["rid"]}}


def _arraste(seq: int, y: int, y2: int, x: int = 360) -> dict[str, object]:
    return {"seq": seq, "type": "swipe", "x": x, "y": y, "x2": x, "y2": y2, "target": None, "sensitive": False}


def test_sem_confirmacao_o_motivo_de_sempre() -> None:
    receita, motivo = distill_training([_arraste(1, 900, 300)], {}, side_effect=False)
    assert receita is None and motivo == "rolagem sem ação-alvo depois dela"


def test_confirmado_cada_rolagem_da_cauda_vira_um_scroll_relativo_sem_pixel() -> None:
    receita, motivo = distill_training([_arraste(1, 900, 300), _arraste(2, 900, 300)], {}, side_effect=False, arraste_final=True)
    assert motivo == "ok" and receita is not None
    assert [(a["tool"], a["args"]) for a in receita] == [("scroll", {"direction": "down"})] * 2    # dedo sobe = conteúdo desce
    assert all(a["commit"] is False and "x" not in str(a["args"]) for a in receita)               # nunca pixel absoluto


def test_o_arraste_no_meio_da_etapa_segue_como_dica_de_rolagem_da_proxima_acao() -> None:
    receita, _ = distill_training([_arraste(1, 900, 300), TOQUE], {}, side_effect=False, arraste_final=True)
    assert receita is not None and len(receita) == 1 and receita[0]["tool"] == "tap" and receita[0]["scroll"]["direction"] == "down"


def test_a_cauda_e_so_o_que_vem_depois_da_ultima_entrada_que_nao_e_arraste() -> None:
    entradas = [_arraste(1, 900, 300), TOQUE | {"seq": 2}, _arraste(3, 300, 900)]
    assert [e["seq"] for e in arrastes_finais(entradas)] == [3]
    assert arrastes_finais([TOQUE]) == []
    assert arrastes_finais([{**_arraste(1, 900, 300), "x": None, "y": None, "x2": None, "y2": None}]) == []     # 31.97


@pytest.mark.parametrize(("arrastes", "tela", "esperado"), [
    ([_arraste(1, 900, 300)], TELA, True),
    ([_arraste(1, 5, 768)], TELA, False),                  # saiu da borda superior: gesto de sistema (F3 fora)
    ([_arraste(1, 1275, 500)], TELA, False),               # borda inferior
    ([_arraste(1, 900, 300, x=5)], TELA, False),           # borda esquerda
    ([_arraste(1, 900, 300)], None, False),                # aparelho mudo: sem tela não se sabe, não chuta
    ([], TELA, False),
])
def test_so_vira_receita_com_a_tela_conhecida_e_sem_borda(arrastes: list[dict[str, object]], tela: tuple[int, int] | None,
                                                          esperado: bool) -> None:
    assert pode_ser_receita(arrastes, tela) is esperado


def test_a_pergunta_e_fixa_por_etapa_e_so_sim_confirma() -> None:
    q = pergunta("rolar_lista")
    assert q == pergunta("rolar_lista") and "rolar_lista" in q and pergunta("outra") != q
    assert confirmou([{"question": q, "answer": "Sim"}], "rolar_lista")
    assert confirmou([{"question": q.upper(), "answer": " sim. "}], "rolar_lista")            # caixa e ponto não importam
    assert not confirmou([{"question": q, "answer": "não"}], "rolar_lista")
    assert not confirmou([{"question": q, "answer": "sim"}], "outra")                        # a resposta é da etapa dela
    assert not confirmou([{"question": "outra coisa?", "answer": "sim"}], "rolar_lista")


# ------------------------------------------------------------------------- a sessão inteira: pergunta, resposta, receita
async def test_propose_pergunta_e_so_o_sim_da_pessoa_faz_a_receita_nascer(harness, monkeypatch) -> None:   # type: ignore[no-untyped-def]
    from types import SimpleNamespace

    from app.training.skills import _destilar

    from .test_modo_treinamento import _entrada, _no_controle

    st, rt, lease = await _no_controle(harness)
    sessao = st.training.start("android-01", intent="Rolar a lista de conversas", lease_id=lease)
    sid = sessao["id"]
    await _entrada(st, rt, lease, type="swipe", x=360, y=900, x2=360, y2=300)               # do meio da tela, para o conteúdo descer
    st.training.stop(sid, lease_id=lease)

    async def tela(_rt: object) -> tuple[int, int] | None:
        return TELA

    async def ia(req):                                                                        # type: ignore[no-untyped-def]
        assert req.tela == TELA and "saindo do meio da tela" in linha_da_entrada(req.inputs[0], req.tela)
        return ({"summary": "rolar a lista", "command_template": "role a lista de conversas do app", "app_id": None,
                 "parameters": [], "steps": [{"key": "rolar", "title": "Rolar", "goal": "Rolar a lista", "inputs": [1],
                                              "side_effect": False, "capability": None, "bindings": [], "app_id": None,
                                              "postcondition": {"kind": "model_judged", "value": "lista", "description": "rolou"}}],
                 "discarded": [], "questions": []}, SimpleNamespace())

    monkeypatch.setattr(st.devices, "tamanho_da_tela", tela)
    monkeypatch.setattr(st.provider, "generalize", ia)
    q = pergunta("rolar")
    primeira = (await st.skills.propose(sid))["proposal"]
    assert primeira["screen"] == [720, 1280] and primeira["questions"] == [q]
    sem_resposta = _destilar(st.training.get(sid), primeira, [SimpleNamespace(side_effect=False)], {}, {})
    assert sem_resposta[0].acoes is None and sem_resposta[0].motivo == "rolagem sem ação-alvo depois dela"

    depois = (await st.skills.propose(sid, {"answers": [{"question": q, "answer": "sim"}]}))["proposal"]
    assert depois["questions"] == [] and depois["answers"] == [{"question": q, "answer": "sim"}]     # não pergunta de novo
    destilada = _destilar(st.training.get(sid), depois, [SimpleNamespace(side_effect=False)], {}, {})
    assert destilada[0].motivo == "ok" and [a["tool"] for a in destilada[0].acoes] == ["scroll"]

    nao = (await st.skills.propose(sid, {"answers": [{"question": q, "answer": "não"}]}))["proposal"]
    assert _destilar(st.training.get(sid), nao, [SimpleNamespace(side_effect=False)], {}, {})[0].acoes is None


async def test_arraste_de_borda_ou_sem_tela_nao_pergunta(harness, monkeypatch) -> None:           # type: ignore[no-untyped-def]
    from types import SimpleNamespace

    from .test_modo_treinamento import _entrada, _no_controle

    st, rt, lease = await _no_controle(harness)
    sid = st.training.start("android-01", intent="Abrir a gaveta", lease_id=lease)["id"]
    await _entrada(st, rt, lease, type="swipe", x=360, y=5, x2=360, y2=768)                    # o caso real do 31.111
    st.training.stop(sid, lease_id=lease)

    async def ia(req):                                                                        # type: ignore[no-untyped-def]
        return ({"summary": "gaveta", "command_template": "abra a gaveta de notificações", "app_id": None, "parameters": [],
                 "steps": [{"key": "gaveta", "title": "Gaveta", "goal": "Abrir", "inputs": [1], "side_effect": False,
                            "capability": None, "bindings": [], "app_id": None,
                            "postcondition": {"kind": "model_judged", "value": "x", "description": "x"}}],
                 "discarded": [], "questions": []}, SimpleNamespace())

    monkeypatch.setattr(st.provider, "generalize", ia)
    for tamanho in ((720, 1280), None):
        async def tela(_rt: object, tamanho=tamanho) -> tuple[int, int] | None:               # type: ignore[no-untyped-def]
            return tamanho
        monkeypatch.setattr(st.devices, "tamanho_da_tela", tela)
        assert (await st.skills.propose(sid))["proposal"]["questions"] == []                   # borda ou tela mudo: nada a confirmar
