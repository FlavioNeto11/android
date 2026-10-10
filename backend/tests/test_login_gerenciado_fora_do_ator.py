"""Item 12.3: o login de um app com `sessao.yaml` NUNCA vai ao ator (a IA livre).

A garantia é uma cadeia, e esta é a catraca de que ela vale para QUALQUER app, não só para o Instagram e o Outlook:

1. `sessao.yaml` + `provedor_de_sessao` vêm juntos no `app.yaml` (a descoberta recusa um sem o outro), e o registro responde
   `tem_provedor_de_sessao(pacote)`: um app novo só em arquivos de dado já nasce com login gerenciado;
2. conta de app gerenciado não oferece o nome da senha ao ator (`account_data`), recusa `type_secret` pelo nome
   (`resolve_secret`) e não tem senha digitável por app (`typable_secret_for`);
3. por isso a tela de senha no meio de uma tarefa (`pede_intervencao_humana` sem credencial) é de PESSOA: o ator não digita;
4. o bloco de dados que o planejador lê não traz nome de senha desse app.

Nível de prova: `simulated` (nenhum aparelho, nenhuma conta real; as contas são `AccountRecord` montados à mão e o app novo
é o Correio de Exemplo de `test_sessao_declarada.py`, gravado em pasta temporária).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.automation.hierarchy import parse_hierarchy
from app.integrations.app_declarado.pacote import PASTA_DOS_APPS, descobrir, manifesto_da_pasta
from app.modules.execution.infrastructure.providers import tem_provedor_de_sessao
from app.modules.identity.domain.available_data import (AccountRecord, account_data, account_name, resolve_secret,
                                                        typable_secret_for)
from app.planning.prompts import dados_block
from app.taskqueue.executor import pede_intervencao_humana

from .test_sessao_declarada import CORREIO, _gravar_correio

SENHA_DO_ENSAIO = "valor-que-nunca-aparece"


def _com_sessao() -> list[str]:
    return [m.definition.package for m in descobrir(PASTA_DOS_APPS) if (PASTA_DOS_APPS / m.definition.package / "sessao.yaml").is_file()]


def _conta(pacote: str, *, gerenciado: bool) -> AccountRecord:
    app_id = pacote.rsplit(".", 1)[-1]
    return AccountRecord(account_id=f"acc-{app_id}", app_id=app_id, package=pacote, app_label=app_id.title(),
                         handle="ana.exemplo", host=None, login_identifier="ana@exemplo.test", has_credential=True,
                         credential_status="active", consent_at="2026-10-10T00:00:00Z", secret_ref="ref-do-cofre",
                         managed=gerenciado)


def _tela_de_senha(pacote: str) -> object:
    return parse_hierarchy(
        f'<hierarchy rotation="0"><node class="android.widget.EditText" package="{pacote}" text="" resource-id="" '
        'content-desc="" clickable="true" enabled="true" focused="false" password="true" scrollable="false" '
        'bounds="[40,400][680,470]" /></hierarchy>')


def _garantias(pacote: str) -> None:
    conta = _conta(pacote, gerenciado=True)
    senha = account_name(conta.app_id, None, "senha")
    # 2. o ator não recebe o nome da senha, não consegue `type_secret` e não há senha digitável por app
    assert [d.name for d in account_data([conta]) if d.sensitive] == []
    recusa = resolve_secret([conta], senha)
    assert recusa.secret is None and recusa.refusal is not None and "login gerenciado" in recusa.refusal
    assert typable_secret_for([conta], pacote).secret is None
    # 4. o bloco que o planejador lê não tem nome de senha
    assert senha not in dados_block(list(account_data([conta])))
    # 3. a tela de senha no meio da tarefa é de pessoa, não do ator
    assert pede_intervencao_humana(_tela_de_senha(pacote), tem_credencial=False)       # type: ignore[arg-type]


@pytest.mark.parametrize("pacote", _com_sessao())
def test_app_com_sessao_yaml_tem_login_gerenciado_e_o_ator_nao_recebe_a_senha(pacote: str) -> None:
    assert tem_provedor_de_sessao(pacote), f"{pacote} tem sessao.yaml mas o registro não o trata como login gerenciado"
    _garantias(pacote)


def test_ha_ao_menos_um_app_declarado_com_sessao() -> None:
    """A parametrização acima não pode esvaziar em silêncio (pasta movida, descoberta quebrada)."""
    assert len(_com_sessao()) >= 2


def test_um_app_novo_so_em_arquivos_de_dado_nasce_com_login_gerenciado(tmp_path: Path) -> None:
    """O Correio de Exemplo: `sessao.yaml` e `telas.yaml` gravados em pasta, `app.yaml` com o provedor. Nenhuma linha de
    Python por app: o manifesto traz a fábrica de sessão e o tipo do provedor, e as garantias valem para ele também."""
    pasta = _gravar_correio(tmp_path / CORREIO)
    (pasta / "app.yaml").write_text(
        f"app: {CORREIO}\nnome: Correio de Exemplo\nprovedor_de_sessao: correio\n", encoding="utf-8")
    m = manifesto_da_pasta(pasta)
    assert m.session is not None and m.definition.session_provider == "correio"
    _garantias(CORREIO)


def test_sessao_yaml_sem_provedor_no_app_yaml_e_recusado(tmp_path: Path) -> None:
    """A outra metade da regra: um app com `sessao.yaml` e sem `provedor_de_sessao` ficaria com o login nas mãos do ator."""
    pasta = _gravar_correio(tmp_path / CORREIO)
    (pasta / "app.yaml").write_text(f"app: {CORREIO}\nnome: Correio de Exemplo\n", encoding="utf-8")
    with pytest.raises(Exception, match="provedor_de_sessao"):
        manifesto_da_pasta(pasta)
