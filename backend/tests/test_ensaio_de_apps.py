"""Item 12.3: o ensaio de pacote roda o motor de sessão real contra o aparelho de mentira do `ensaio.yaml` de cada app.

Descobre os roteiros em dois lugares: `tests/apps_de_ensaio/<pacote>/` (apps FICTÍCIOS, só de teste) e
`app/conhecimento/apps/<pacote>/` (quando um app real ganhar `ensaio.yaml`; hoje nenhum tem, e não se escreve um antes da decisão
do dono, P-045). Cada cenário é um teste. Nível de prova: `simulated`.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.integrations.app_declarado.pacote import PASTA_DOS_APPS, manifesto_da_pasta

from . import ensaio_de_app as ensaio

FICTICIOS = Path(__file__).resolve().parent / "apps_de_ensaio"


def _roteiros() -> list[ensaio.Roteiro]:
    return [ensaio.carregar_roteiro(p) for p in ensaio.pastas_com_roteiro(FICTICIOS) + ensaio.pastas_com_roteiro(PASTA_DOS_APPS)]


_CASOS = [(r, c) for r in _roteiros() for c in r.cenarios]


@pytest.mark.parametrize("roteiro, cenario", _CASOS, ids=[f"{r.app}::{c.nome}" for r, c in _CASOS])
async def test_cenario_do_ensaio_de_pacote(roteiro: ensaio.Roteiro, cenario: ensaio.Cenario, tmp_path: Path) -> None:
    r = await ensaio.ensaiar(roteiro, cenario, tmp_path)
    assert not r.problemas, f"{roteiro.app}::{cenario.nome}: " + "; ".join(r.problemas)


def test_o_app_ficticio_do_ensaio_tem_ao_menos_um_cenario_de_cada_familia() -> None:
    """Sem isto a parametrização esvaziava em silêncio (pasta movida) e o ensaio parecia verde."""
    desfechos = {c.espera["desfecho"] for r in _roteiros() for c in r.cenarios}
    assert {"session_ready", "invalid_credential", "auth_challenge", "wrong_account", "uncertain"} <= desfechos


def test_o_pacote_ficticio_e_um_pacote_valido_de_verdade() -> None:
    """A mesma validação que a descoberta de apps faz (`app.yaml` + `telas.yaml` + `sessao.yaml`): o exemplo não é um molde errado."""
    m = manifesto_da_pasta(FICTICIOS / "com.exemplo.email")
    assert m.session is not None and m.definition.session_provider == "correio"


def test_o_exemplo_nao_esta_na_pasta_dos_apps_reais() -> None:
    assert not (PASTA_DOS_APPS / "com.exemplo.email").exists()


def _roteiro_base() -> dict[str, Any]:
    return yaml.safe_load((FICTICIOS / "com.exemplo.email" / ensaio.ARQUIVO).read_text(encoding="utf-8"))


@pytest.mark.parametrize("mexe, trecho", [
    (lambda d: d.update(extra=1), "campo desconhecido extra"),
    (lambda d: d.update(app="com.outro.app"), "o roteiro é de 'com.outro.app'"),
    (lambda d: d.update(inicial="nao_existe"), "inicial: a tela 'nao_existe' não existe"),
    (lambda d: d["telas"]["entrada"][2].update(ao_tocar="ir:sumiu"), "a tela de 'ir:sumiu' não existe"),
    (lambda d: d["telas"]["entrada"][2].update(ao_tocar="clicar"), "ao_tocar"),
    (lambda d: d["envio"][0].update(se="talvez"), "envio[0].se"),
    (lambda d: d["envio"][0].update(vai_para="sumiu"), "envio[0].vai_para"),
    (lambda d: d["cenarios"][0]["espera"].update(desfecho="deu_certo"), "espera.desfecho"),
    (lambda d: d["cenarios"][1].update(nome=d["cenarios"][0]["nome"]), "repetido"),
    (lambda d: d["cenarios"][0].update(aparelho={"tela": "sumiu"}), "aparelho.tela"),
    (lambda d: d.update(cenarios=[]), "cenarios"),
])
def test_roteiro_invalido_e_recusado_com_o_caminho_do_campo(mexe: Any, trecho: str) -> None:
    dados = _roteiro_base()
    mexe(dados)
    with pytest.raises(ensaio.RoteiroInvalido, match=re.escape(trecho)):
        ensaio.de_dados(dados, FICTICIOS / "com.exemplo.email")
