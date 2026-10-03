"""30.38 (a): a origem de uma execução é derivada da linha, por um vocabulário fechado numa fonte só
(`app/contracts/origem.py`). Nível de prova: `simulated` (a regra, a fonte e a listagem pelo harness).

O que se prova:
- a regra: prova de fluxo ganha da chave; `validacao:`, `telegram:` e `trello:` pela chave; sem marca, nada;
- o prefixo da validação é UM: nenhum arquivo de `app/` escreve o literal fora do contrato (mudar um lado sem o outro
  reprova aqui).
"""
from __future__ import annotations

from pathlib import Path

import httpx

from app.contracts.origem import ORIGENS_DA_EXECUCAO, PREFIXO_VALIDACAO, PREFIXOS_DE_ORIGEM, origem_da_execucao
from app.main import create_app

from .conftest import Harness

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


async def test_a_listagem_traz_a_origem_derivada_da_linha(harness: Harness) -> None:
    """O resumo da execução (`GET /api/runs`) leva `origem`/`origem_ref` pela mesma regra, sem coluna nova: a do
    canal pela chave, a da validação pela chave, a sucessora e a do painel sem origem."""
    assert harness.state is not None
    chaves = {"telegram": "telegram:9001", "validacao": f"{PREFIXO_VALIDACAO}lv-1", "sucessora": "sucessora-r-1-ab",
              "painel": None}
    ids = {nome: harness.run(["android-01"], command=f"abrir {nome}", key=chave).id for nome, chave in chaves.items()}
    app = create_app(harness.cfg, state=harness.state)
    app.state.poc = harness.state
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/runs", params={"limit": 50})
    assert r.status_code == 200
    por_id = {x["id"]: (x["origem"], x["origem_ref"]) for x in r.json()["runs"]}
    assert por_id[ids["telegram"]] == ("telegram", "9001")
    assert por_id[ids["validacao"]] == ("validacao_qa", "lv-1")
    assert por_id[ids["sucessora"]] == (None, None)
    assert por_id[ids["painel"]] == (None, None)
