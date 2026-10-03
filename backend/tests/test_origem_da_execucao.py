"""30.38 (a): a origem de uma execução é derivada da linha, por um vocabulário fechado numa fonte só
(`app/contracts/origem.py`). Nível de prova: `simulated` (só a regra e a fonte; sem banco).

O que se prova:
- a regra: prova de fluxo ganha da chave; `validacao:`, `telegram:` e `trello:` pela chave; sem marca, nada;
- o prefixo da validação é UM: nenhum arquivo de `app/` escreve o literal fora do contrato (mudar um lado sem o outro
  reprova aqui).
"""
from __future__ import annotations

from pathlib import Path

from app.contracts.origem import ORIGENS_DA_EXECUCAO, PREFIXO_VALIDACAO, PREFIXOS_DE_ORIGEM, origem_da_execucao

APP = Path(__file__).resolve().parents[1] / "app"


def test_a_regra_da_origem() -> None:
    assert origem_da_execucao("fluxo-7", f"{PREFIXO_VALIDACAO}lv-1") == ("prova_fluxo", "fluxo-7")
    assert origem_da_execucao(None, f"{PREFIXO_VALIDACAO}lv-1") == ("validacao_qa", "lv-1")
    assert origem_da_execucao(None, "telegram:9001") == ("telegram", "9001")
    assert origem_da_execucao(None, "trello:abc") == ("trello", "abc")
    assert origem_da_execucao(None, "sucessora-r-1-abcd") == (None, None)
    assert origem_da_execucao(None, None) == (None, None)
    assert set(PREFIXOS_DE_ORIGEM.values()) | {"prova_fluxo"} == set(ORIGENS_DA_EXECUCAO)


def test_o_prefixo_da_validacao_so_existe_no_contrato() -> None:
    contrato = APP / "contracts" / "origem.py"
    literais = (f'"{PREFIXO_VALIDACAO}', f"'{PREFIXO_VALIDACAO}")
    fora = [str(p.relative_to(APP)) for p in APP.rglob("*.py")
            if p != contrato and any(x in p.read_text(encoding="utf-8") for x in literais)]
    assert fora == [], f"o prefixo da validação escrito à mão fora de app/contracts/origem.py: {fora}"
