"""Item 12.3: conferências de coerência do pacote de um app que a carga não faz (a carga confere a FORMA).

- `depois_do_envio` sem `conferir_conta` é recusado na carga: o login nunca confirmaria a conta;
- sinal de `telas.yaml` que ninguém cita (`sinais_sem_uso`) é, quase sempre, erro de digitação. Os apps reais têm uma dívida
  antiga (Outlook: `boas_vindas`); a catraca abaixo trava só os NOVOS, sem tocar no yaml do app real.

Nível de prova: `simulated` (os pacotes são os arquivos de verdade; nada roda em aparelho).
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from app.automation import conhecimento_de_telas as telas_
from app.integrations.app_declarado import conhecimento
from app.integrations.app_declarado.conhecimento import SessaoInvalida, sinais_sem_uso
from app.integrations.app_declarado.pacote import PASTA_DOS_APPS

from .test_sessao_declarada import SESSAO_DO_CORREIO, TELAS_DO_CORREIO

FICTICIOS = Path(__file__).resolve().parent / "apps_de_ensaio"

#: A dívida conhecida (pacote → idioma → sinais). Só diminui: apagar o sinal morto do yaml e tirá-lo daqui.
DIVIDA_CONHECIDA: dict[str, dict[str, list[str]]] = {
    "com.microsoft.office.outlook": {"en": ["boas_vindas"], "pt": ["boas_vindas"]},
}


def _pacotes() -> list[Path]:
    return sorted(p.parent for p in PASTA_DOS_APPS.glob("*/sessao.yaml")) + sorted(p.parent for p in FICTICIOS.glob("*/sessao.yaml"))


@pytest.mark.parametrize("pasta", _pacotes(), ids=lambda p: p.name)
def test_nenhum_sinal_novo_fica_sem_uso(pasta: Path) -> None:
    sem_uso = sinais_sem_uso(conhecimento.carregar(pasta))
    assert sem_uso == DIVIDA_CONHECIDA.get(pasta.name, {}), (
        f"{pasta.name}: sinal declarado em telas.yaml e citado em lugar nenhum: {sem_uso}")


def test_a_divida_conhecida_ainda_existe() -> None:
    """Quem pagou a dívida (tirou o sinal do yaml) precisa tirar a linha daqui: a lista não envelhece."""
    for pacote, esperado in DIVIDA_CONHECIDA.items():
        assert sinais_sem_uso(conhecimento.carregar(PASTA_DOS_APPS / pacote)) == esperado


def test_um_sinal_com_o_nome_trocado_aparece_como_sem_uso() -> None:
    sessao = copy.deepcopy(SESSAO_DO_CORREIO)
    sessao["formulario"]["sinal_do_botao"] = "outra_conta"          # o certo (`entrar`) deixa de ser citado
    k = conhecimento.de_dados(sessao, telas_.de_dados(copy.deepcopy(TELAS_DO_CORREIO)))
    assert sinais_sem_uso(k) == {"pt": ["entrar"]}


def test_depois_do_envio_sem_conferir_conta_e_recusado_na_carga() -> None:
    sessao: dict[str, Any] = copy.deepcopy(SESSAO_DO_CORREIO)
    sessao["depois_do_envio"] = [r for r in sessao["depois_do_envio"] if r["desfecho"] != "conferir_conta"]
    with pytest.raises(SessaoInvalida, match="conferir_conta"):
        conhecimento.de_dados(sessao, telas_.de_dados(copy.deepcopy(TELAS_DO_CORREIO)))
