"""31.114 F1: o arraste gravado chega à IA como o gesto do DEDO, de onde saiu e quanto percorreu.

O caso real (06/10, sessão da prova do 31.111): o dedo desceu da borda superior (y=5) até 60 % da altura, para abrir a gaveta
de notificações, e o texto antigo ("rolou para cima", que é o movimento do CONTEÚDO) levou a IA a ler "deslizar para cima".
Sem a tela do aparelho, o texto diz que a borda de origem é desconhecida em vez de chutar.

Nível de prova: `simulated` (função pura e aparelho falso do harness)."""
from __future__ import annotations

import pytest

from app.planning.training import TrainingRequest, descrever_arraste, linha_da_entrada

from .conftest import Harness
from .test_modo_treinamento import _entrada, _no_controle

TELA = (720, 1280)


@pytest.mark.parametrize(("arraste", "tela", "esperado"), [
    ((360, 5, 360, 768), TELA, "arrastou o dedo de cima para baixo, saindo da borda superior, por 60 % da altura"),      # o caso real
    ((360, 1270, 360, 500), TELA, "arrastou o dedo de baixo para cima, saindo da borda inferior, por 60 % da altura"),
    ((360, 900, 360, 300), TELA, "arrastou o dedo de baixo para cima, saindo do meio da tela, sem tocar a borda, por 47 % da altura"),
    ((715, 640, 200, 640), TELA, "arrastou o dedo da direita para a esquerda, saindo da borda direita, por 72 % da largura"),
    ((5, 640, 400, 640), TELA, "arrastou o dedo da esquerda para a direita, saindo da borda esquerda, por 55 % da largura"),
    ((360, 5, 360, 768), None, "arrastou o dedo de cima para baixo (borda de origem desconhecida)"),          # aparelho fora do ar
    ((360, 5, 360, 1500), TELA, "arrastou o dedo de cima para baixo (borda de origem desconhecida)"),         # não cabe nesta tela
])
def test_o_gesto_do_dedo_com_a_borda_e_a_distancia(arraste: tuple[int, int, int, int], tela: tuple[int, int] | None,
                                                   esperado: str) -> None:
    assert descrever_arraste(*arraste, tela) == esperado


def test_a_linha_da_entrada_leva_a_tela_e_o_texto_antigo_nao_volta() -> None:
    entrada = {"seq": 1, "type": "swipe", "x": 360, "y": 5, "x2": 360, "y2": 768, "target": None, "sensitive": False}
    assert linha_da_entrada(entrada, TELA) == "#1 swipe | arrastou o dedo de cima para baixo, saindo da borda superior, por 60 % da altura"
    assert "rolou" not in linha_da_entrada(entrada)
    sem_coordenada = {**entrada, "x": None, "y": None, "x2": None, "y2": None}          # 31.97 continua valendo
    assert "não gravado" in linha_da_entrada(sem_coordenada, TELA) and "arrastou" not in linha_da_entrada(sem_coordenada, TELA)


def test_o_pedido_a_ia_nasce_sem_tela_por_padrao() -> None:
    assert TrainingRequest(intent="x", app_id=None, apps=[], inputs=[]).tela is None


async def test_a_tela_so_e_lida_quando_ha_arraste_com_coordenada_e_falha_vira_none(harness: Harness,
                                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    st, rt, lease = await _no_controle(harness)
    chamadas: list[str] = []

    async def tamanho(r: object) -> tuple[int, int] | None:
        chamadas.append("lida")
        return TELA

    monkeypatch.setattr(st.devices, "tamanho_da_tela", tamanho)
    sessao = st.training.start("android-01", intent="Abrir a gaveta de notificações", lease_id=lease)
    st.training.record(rt, {"type": "key", "key": "back"}, None)
    sem_arraste = st.training.get(sessao["id"])
    assert await st.skills._tela_do_treino(sem_arraste) is None and chamadas == []        # nada para descrever: não toca no aparelho
    await _entrada(st, rt, lease, type="swipe", x=360, y=5, x2=360, y2=768)
    com_arraste = st.training.get(sessao["id"])
    assert await st.skills._tela_do_treino(com_arraste) == TELA and chamadas == ["lida"]

    async def fora_do_ar(r: object) -> tuple[int, int] | None:
        return None

    monkeypatch.setattr(st.devices, "tamanho_da_tela", fora_do_ar)
    assert await st.skills._tela_do_treino(com_arraste) is None                           # aparelho mudo: sem chute
