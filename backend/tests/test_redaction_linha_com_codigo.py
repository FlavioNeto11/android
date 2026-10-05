"""31.82: `parece_linha_com_codigo`, a linha de tela ou de SMS que fala de código com o número perto."""
from __future__ import annotations

import pytest

from app.security.redaction import parece_linha_com_codigo


@pytest.mark.parametrize("linha", [
    "G-123456 is your Google verification code",
    "Use 123 456 to verify your Instagram account.",
    "Seu código de verificação é 8845-12",
    "Your PIN is 4821",
    "OTP 48213 expires soon",
    "Token: 884512",
    "SENHA temporaria 123456",
])
def test_linha_que_fala_de_codigo_com_o_numero_perto_cai(linha: str) -> None:
    assert parece_linha_com_codigo(linha), linha


@pytest.mark.parametrize("linha", ["Recife 2024", "Pedido 48213 entregue", "há 5 min", "", None,
                                   "Digite o código que enviamos por e-mail", "Escolha uma senha forte",
                                   "Pedido 48213", "spinner 1234"])
def test_linha_comum_ou_sem_numero_perto_nao_cai(linha: str | None) -> None:
    assert not parece_linha_com_codigo(linha), linha


def test_a_distancia_maxima_e_de_40_caracteres() -> None:
    assert parece_linha_com_codigo("code " + "x" * 36 + " 4821")          # 36 + 2 espaços... dentro de 40
    assert not parece_linha_com_codigo("code " + "x" * 45 + " 4821")
    assert parece_linha_com_codigo("4821 " + "x" * 30 + " verification")
    assert not parece_linha_com_codigo("4821 " + "x" * 50 + " verification")
