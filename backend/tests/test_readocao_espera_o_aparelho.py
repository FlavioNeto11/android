"""29.151: o teto da espera pela resposta do framework na readoção vem da medida do próprio aparelho, com piso maior.

Achado do deploy 40: depois do reinício do backend a readoção dava 60 s fixos e marcava `error` quem levava 65 ou 75 s.
"""
from __future__ import annotations

import pytest

from app.devices import manager as manager_mod
from app.devices.manager import RESPOSTA_POS_BOOT_S, teto_da_resposta_s


def test_boot_novo_segue_com_o_teto_de_sempre() -> None:
    assert teto_da_resposta_s(False, None) == RESPOSTA_POS_BOOT_S
    assert teto_da_resposta_s(False, 400.0) == RESPOSTA_POS_BOOT_S      # a medida só vale na readoção


def test_a_readocao_sem_medida_tem_o_piso_maior_que_os_60_s() -> None:
    assert teto_da_resposta_s(True, None) == RESPOSTA_POS_BOOT_S * manager_mod.FATOR_PISO_DA_READOCAO
    assert teto_da_resposta_s(True, None) > 75.0                         # os 65 e 75 s do deploy 40 cabem


@pytest.mark.parametrize("medida", [0.0, 30.0, 110.0])
def test_a_medida_curta_nao_derruba_o_piso(medida: float) -> None:
    assert teto_da_resposta_s(True, medida) == RESPOSTA_POS_BOOT_S * manager_mod.FATOR_PISO_DA_READOCAO


def test_a_medida_do_aparelho_acima_do_piso_vale() -> None:
    assert teto_da_resposta_s(True, 200.0) == 200.0


def test_a_medida_absurda_e_cortada_no_limite() -> None:
    assert teto_da_resposta_s(True, 5000.0) == RESPOSTA_POS_BOOT_S * manager_mod.FATOR_LIMITE_DA_READOCAO


def test_o_piso_acompanha_o_teto_de_sempre_quando_o_teste_o_reduz(monkeypatch: pytest.MonkeyPatch) -> None:
    """Os testes de prontidão encurtam `RESPOSTA_POS_BOOT_S`; a readoção desses testes encurta junto, não vira 120 s."""
    monkeypatch.setattr(manager_mod, "RESPOSTA_POS_BOOT_S", 0.05)
    assert manager_mod.teto_da_resposta_s(True, None) == pytest.approx(0.1)
