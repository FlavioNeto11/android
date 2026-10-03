"""Casos reais da limpeza do Trello de 03/10 (achados-da-limpeza.md): o que sai e o que fica.

Rodar: backend/.venv/Scripts/python.exe -m pytest -q .claude/trello/test_redacao.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from gerar import cortar, limpo  # noqa: E402
from redacao import lista, redigir  # noqa: E402


@pytest.mark.parametrize(("entrada", "esperado"), [
    # credencial dentro de URL (5.1) e atribuição de segredo
    ("postgresql://postgres:abc123x@localhost:5432/db", "postgresql://postgres:[senha]@localhost:5432/db"),
    ("TEST_DATABASE_URL=postgresql://u:s3nh4@127.0.0.1/x", "TEST_DATABASE_URL=postgresql://u:[senha]@[ip]/x"),
    ("senha=Abc12345 e token: xyz98765", "senha=[segredo] e token: [segredo]"),
    # nome de pessoa em campo de autoria (9.1); operador da Central fica
    ("requested_by = 'Maria Souza'", "requested_by = '[nome]'"),
    ("decided_by='telegram:dono'", "decided_by='telegram:dono'"),
    # persona de teste com data de nascimento (16.3)
    ("fisioterapeuta → Marina Exemplo, 1995-04-12, biografia", "fisioterapeuta → [persona de teste], [data], biografia"),
    # @ que não é conta (11.6, 11.9, 16.1) e hash de commit
    ("avatar+@usuário do perfil", "avatar+@usuário do perfil"),
    ("faixas por `@container page`", "faixas por `@container page`"),
    ("commit @ea1df281 ok", "commit @ea1df281 ok"),
    ("abra a conversa com @fulano_99", "abra a conversa com @[conta]"),
    # sobrenome que é palavra comum (4.5, 30.3, 1.8, 15.7, 26.1)
    ("três fontes; fontes.py; Marcos e deploys; Pesquisa em fontes primárias",
     "três fontes; fontes.py; Marcos e deploys; Pesquisa em fontes primárias"),
    ("IP 192.168.1.11", "IP [ip]"),
])
def test_redigir(entrada: str, esperado: str) -> None:
    assert redigir(entrada) == esperado


def test_cortar_em_palavra_inteira_e_fecha_crase() -> None:
    t = cortar("prova com `arquivo_muito_longo.py` e mais texto", 22)
    assert t.endswith("…") and t.count("`") % 2 == 0 and "arqu…" not in t


def test_limpo_devolve_a_barra_do_caminho() -> None:
    assert limpo("C:\\Android\x07rquivo") == "C:\\Android\\arquivo"


def test_lista_frase_fica_inteira_e_caminhos_se_partem() -> None:
    assert lista("suíte 7 inteira (-n 8, Idle) no 22f66a6e: 6768 passed", 4) == [
        "suíte 7 inteira (-n 8, Idle) no 22f66a6e: 6768 passed"]
    assert lista("tests/a.py, tests/b.py", 4) == ["tests/a.py", "tests/b.py"]
