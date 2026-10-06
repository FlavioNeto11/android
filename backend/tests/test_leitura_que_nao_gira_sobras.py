"""30.78: sobras da leitura do 31.78 (a leitura julgada que não gira).

- Teste com duas saídas, uma estável e uma divergente: o caso em que a divergente SE ESTABILIZA (0, 7, 7) e a etapa
  anda. O caso em que ela segue divergente até o teto já é `test_releitura_do_mesmo_valor.py`.
- Fato neutro ao juiz quando o valor lido mudou na tentativa: vai ao `history` (os fatos da verificação), sem os valores
  e sem instrução.
- Aviso ao ator quando outra saída divergiu: com tudo lido e uma saída cuja última leitura diverge da anterior, o ator
  recebe, só na cópia dele, o pedido de reler para confirmar.

Nível de prova: `simulated` (harness na porta 5640, catálogo de teste no QA Messenger falso; nenhuma IA paga).
"""
from __future__ import annotations

from typing import Any

from .conftest import Harness
from .test_releitura_do_mesmo_valor import _detalhes, _rodar, contagem  # noqa: F401 - `contagem` é fixture

MUDOU = "(executor) o valor lido de 'outro' mudou nesta tentativa"
RELEIA = "(executor) 'outro': a última leitura deu um valor diferente"


def _com(historicos: list[list[str]], prefixo: str) -> list[int]:
    return [i for i, h in enumerate(historicos) if any(linha.startswith(prefixo) for linha in h)]


async def test_a_saida_que_se_estabiliza_deixa_a_etapa_andar_e_o_juiz_sabe_que_mudou(
        harness: Harness, contagem: None) -> None:  # noqa: F811
    """`outro` lê 0, depois 7, depois 7 de novo (estabilizou); a contagem lê 0 e o ator conclui. A etapa vai à
    verificação, o juiz recebe o fato neutro de que `outro` mudou (sem os valores), e a entrega é a última leitura."""
    visto, run = await _rodar(harness, ["outro:0", "outro:7", "outro:7", "0", "fim"], "contagem", "outro")
    db = harness.state.db                                                     # type: ignore[union-attr]
    assert harness.state.repo.run_row(run.id)["status"] == "completed", _detalhes(harness)   # type: ignore[union-attr]
    assert visto.get("verificacoes") == 1
    fatos = [f for f in visto["fatos"] if f.startswith(MUDOU)]
    assert len(fatos) == 1 and "0" not in fatos[0] and "7" not in fatos[0]
    assert not any(f.startswith(RELEIA) for f in visto["fatos"])             # o aviso ao ator não vai ao juiz
    saidas = {str(r["name"]): str(r["value"]) for r in db.query("SELECT name, value FROM step_outputs")}
    assert saidas.get("outro") == "7" and saidas.get("contagem") == "0", saidas
    assert _com(visto["historicos"], RELEIA) == []                          # estabilizada, nada a reler


async def test_com_tudo_lido_e_outra_divergente_o_ator_e_avisado_de_reler(harness: Harness, contagem: None) -> None:  # noqa: F811
    """`outro` diverge (0, depois 7) e a contagem se lê. Com tudo lido e `outro` divergente, cada decisão seguinte leva o
    aviso de reler `outro`; antes de tudo estar lido, não. O teto falha como divergente, como no 31.78."""
    visto, run = await _rodar(harness, ["outro:0", "outro:7", "0", "0", "0", "0"], "contagem", "outro")
    assert harness.state.repo.run_row(run.id)["status"] != "completed"         # type: ignore[union-attr]
    avisadas = _com(visto["historicos"], RELEIA)
    assert avisadas[:1] == [3] and not set(avisadas) & {0, 1, 2}, avisadas   # (o replano recomeça o roteiro)
    assert _com(visto["historicos"], MUDOU)                                 # o fato neutro também chega ao ator
    assert "verificacoes" not in visto


async def test_o_ator_que_rele_a_divergente_e_ela_confirma_segue(harness: Harness, contagem: None) -> None:  # noqa: F811
    """Avisado, o ator relê `outro` e o valor se repete: a divergência acaba, o aviso some e a etapa conclui."""
    visto, run = await _rodar(harness, ["outro:0", "0", "outro:7", "outro:7", "fim"], "contagem", "outro")
    assert harness.state.repo.run_row(run.id)["status"] == "completed", _detalhes(harness)   # type: ignore[union-attr]
    avisadas = _com(visto["historicos"], RELEIA)
    assert avisadas == [3], avisadas                                        # só entre a divergência e a confirmação
    assert visto.get("verificacoes") == 1
