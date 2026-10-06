"""30.83 (C1 da leitura da fatia 4): varredura para o id do fluxo não voltar ao log nem ao texto das exceções.

O id antigo do fluxo é o slug do `plan.summary` literal (pode trazer nome ou @), e o log e o `detail` das respostas o
levariam para fora. No módulo do aprendizado, todo `log.*(...)` e todo `raise X(f"...")` que cita uma referência de
item passa por `quem_no_log` ou `ref_no_log` (sem banco: os `log.exception` rodam dentro da transação que falhou).
A varredura lê a árvore sintática, não roda nada.

Nível de prova: `simulated` (análise estática do código).
"""
from __future__ import annotations

import ast
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1] / "app"
ALVOS = [*sorted((RAIZ / "modules" / "learning").rglob("*.py")), RAIZ / "taskqueue" / "flows.py"]
#: Os nomes que carregam a referência de um item do livro (o id do fluxo, a chave da trilha `fluxo:<id>`).
ARRISCADOS = frozenset({"ref", "item_ref", "trail_ref", "flow_id", "fluxo_id"})
SANEADORES = frozenset({"quem_no_log", "ref_no_log"})
LOGS = frozenset({"debug", "info", "warning", "error", "exception", "critical"})


def _nome(no: ast.AST) -> str | None:
    if isinstance(no, ast.Name):
        return no.id
    if isinstance(no, ast.Attribute):
        return no.attr
    return None


def _cru(no: ast.AST) -> list[str]:
    """As referências que aparecem em `no` sem passar por um saneador."""
    if isinstance(no, ast.Call) and _nome(no.func) in SANEADORES:
        return []
    achados = [n for n in [_nome(no)] if n in ARRISCADOS]
    for filho in ast.iter_child_nodes(no):
        if isinstance(no, ast.Attribute) and filho is no.value:
            continue                                   # `e.ref` conta uma vez, pelo atributo
        achados.extend(_cru(filho))
    return achados


def _ocorrencias() -> list[str]:
    saida: list[str] = []
    for caminho in ALVOS:
        arvore = ast.parse(caminho.read_text(encoding="utf-8"))
        for no in ast.walk(arvore):
            if (isinstance(no, ast.Call) and isinstance(no.func, ast.Attribute) and no.func.attr in LOGS
                    and isinstance(no.func.value, ast.Name) and no.func.value.id == "log"):
                for arg in no.args[1:]:
                    saida.extend(f"{caminho.name}:{no.lineno} log {n}" for n in _cru(arg))
            if isinstance(no, ast.Raise) and isinstance(no.exc, ast.Call):
                for arg in no.exc.args:
                    if isinstance(arg, ast.JoinedStr):
                        saida.extend(f"{caminho.name}:{no.lineno} raise {n}" for n in _cru(arg))
    return saida


def test_nenhum_log_nem_excecao_do_aprendizado_cita_a_referencia_crua() -> None:
    assert _ocorrencias() == []


def test_a_varredura_acha_o_que_procura() -> None:
    """Sem isto, uma varredura quebrada passaria vazia."""
    codigo = ('log.info("x %s", e.ref)\nlog.info("y %s", quem_no_log(e.kind, e.ref))\n'
              'raise Erro(f"O fluxo {ref} sumiu")\nraise Erro(f"{quem_no_log(kind, ref)} sumiu")\n')
    arvore = ast.parse(codigo)
    achados = []
    for no in ast.walk(arvore):
        if isinstance(no, ast.Call) and _nome(no.func) == "info":
            achados.extend(_cru(no.args[1]))
        if isinstance(no, ast.Raise) and isinstance(no.exc, ast.Call):
            achados.extend(_cru(no.exc.args[0]))
    assert achados == ["ref", "ref"]
