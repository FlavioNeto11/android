"""28.31 F3: o resumo abre com "Precisa de você", diz só o que mudou e não sai quando nada mudou.

Prova `simulated`: só a parte pura (`montar`), sem central, plano, Trello nem Telegram.
Rodar da raiz: `backend/.venv/Scripts/python.exe -m pytest -q .claude/canais/test_resumo_laco.py`.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import resumo_laco as r  # noqa: E402

AGORA = datetime(2026, 10, 4, 22, 0, tzinfo=timezone.utc)
OK = "🟢 <b>Central saudável</b> · deploy 32 no ar"


def _estado(**kw: object) -> dict:
    base: dict = {"saude": OK, "plano": "<b>Plano geral: 500 de 700 itens concluídos (71 %)</b>", "plano_detalhe": "",
                  "frentes": {"Android": "boots no notebook"}, "parados": [], "n_parados": 0, "pendencias": [],
                  "mudou": [], "nao_curados": 0, "nao_curados_desde": "", "eventos_linha": 10}
    base.update(kw)
    return base


def test_primeiro_envio_abre_com_precisa_de_voce_e_mostra_como_esta() -> None:
    texto, retrato = r.montar(_estado(pendencias=["responder sim ao 29.41"]), None, AGORA, "19:09Z")
    assert texto is not None
    linhas = texto.split("\n")
    assert linhas[2] == "<b>🙋 Precisa de você: 1</b>" and linhas[3] == "▪️ responder sim ao 29.41"
    assert "🆕" not in texto                                    # no primeiro envio tudo é novo: não marca
    assert "<b>Como está</b>" in texto and OK in texto
    assert retrato["pendencias"] == ["responder sim ao 29.41"]


def test_nada_mudou_nao_envia() -> None:
    _, retrato = r.montar(_estado(pendencias=["p1"]), None, AGORA)
    texto, _ = r.montar(_estado(pendencias=["p1"]), retrato, AGORA, "21:40Z")
    assert texto is None                                         # a pendência segue, mas não é nova


def test_so_o_que_mudou_e_a_pendencia_nova_marcada() -> None:
    _, retrato = r.montar(_estado(pendencias=["p1"], frentes={"Android": "a", "Jev": "b"}), None, AGORA)
    texto, _ = r.montar(_estado(pendencias=["p1", "p2"], frentes={"Android": "a", "Jev": "c"}), retrato, AGORA, "21:40Z")
    assert texto is not None
    assert "<b>🙋 Precisa de você: 2</b>" in texto and "▪️ 🆕 p2" in texto and "▪️ p1" in texto
    assert "<b>Mudou desde 21:40Z</b>" in texto
    assert "<b>Jev</b> · c" in texto and "Android" not in texto  # a frente que não mudou não se repete
    assert OK not in texto and "Plano geral" not in texto        # saúde e plano iguais não aparecem


def test_saude_com_problema_aparece_sempre_e_a_resolvida_e_contada() -> None:
    ruim = "🟡 <b>Central com 1 problema(s)</b>"
    _, retrato = r.montar(_estado(saude=ruim, pendencias=["p1"]), None, AGORA)
    texto, retrato = r.montar(_estado(saude=ruim, pendencias=[]), retrato, AGORA, "21:40Z")
    assert texto is not None and ruim in texto and "<b>🙋 Nada espera você agora.</b>" in texto
    assert "✔️ 1 pendência sua saiu da lista" in texto


def test_parados_so_os_novos_e_leitura_falha_nao_conta_como_mudanca() -> None:
    _, retrato = r.montar(_estado(parados=["cartão A"], n_parados=1), None, AGORA)
    texto, retrato2 = r.montar(_estado(parados=["cartão A", "cartão B"], n_parados=2), retrato, AGORA, "21:40Z")
    assert texto is not None and "cartão B" in texto and "cartão A" not in texto
    texto, _ = r.montar(_estado(parados=None, n_parados=None), retrato2, AGORA, "22:00Z")
    assert texto is None                                         # a leitura do Trello falhou: não inventa mudança


def test_mudou_extra_e_eventos_nao_curados_contam_como_mudanca() -> None:
    _, retrato = r.montar(_estado(), None, AGORA)
    texto, _ = r.montar(_estado(mudou=["deploy 32 no ar"]), retrato, AGORA, "21:40Z")
    assert texto is not None and "▪️ deploy 32 no ar" in texto
    texto, _ = r.montar(_estado(nao_curados=3, nao_curados_desde="21:50Z"), retrato, AGORA, "21:40Z")
    assert texto is not None and "3 novidades desde 21:50Z" in texto


def test_texto_dinamico_passa_pela_redacao() -> None:
    _, retrato = r.montar(_estado(), None, AGORA)
    texto, _ = r.montar(_estado(frentes={"Android": "<script>"}), retrato, AGORA, "21:40Z")
    assert texto is not None and "<script>" not in texto and "&lt;script&gt;" in texto
