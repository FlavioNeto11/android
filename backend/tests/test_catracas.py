"""29.98: os arquivos de catracas (`tests/catracas.txt` e `scripts/tests/catracas.txt`) só citam teste que existe.

Catraca é o teste que inspeciona o código inteiro ou assinaturas (AST, `inspect`, varredura de pastas, lista de exceção
explícita) em vez de exercitar um comportamento. Ela quebra longe do arquivo editado, e o dirigido não a roda por conta
própria: o #350 chegou a final com `test_social_profiles::test_todo_metodo_por_perfil_exige_profile_id` vermelho. Por
isso todo dirigido que toque `backend/app` roda `pytest @tests/catracas.txt` (`.claude/rules/testes.md`).

O `@arquivo` do pytest é o `fromfile_prefix_chars` do argparse: cada linha vira um argumento, sem comentário. Uma linha
`# …` ou vazia não dá erro: a coleta inteira volta vazia ("no tests collected", medido no pytest 9.1.1). Daí a trava
de formato, além da de existência, conferida por `ast`, sem importar os módulos.

Nível de prova: `simulated` (só o repositório). Nada real.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
RAIZ = BACKEND.parent
LISTAS = {
    "backend": (BACKEND / "tests" / "catracas.txt", BACKEND),
    "scripts": (RAIZ / "scripts" / "tests" / "catracas.txt", RAIZ),
}


def _linhas(arquivo: Path) -> list[str]:
    return arquivo.read_text(encoding="utf-8").splitlines()


def _existe(base: Path, ident: str) -> str | None:
    """`None` se o id aponta para um teste que existe; senão, o motivo."""
    caminho, *partes = ident.split("::")
    arquivo = base / caminho
    if not arquivo.is_file():
        return f"arquivo não existe: {caminho}"
    if not partes:
        return None
    arvore = ast.parse(arquivo.read_text(encoding="utf-8"))
    nos: list[ast.stmt] = arvore.body
    for nome in partes[:-1]:
        classe = next((n for n in nos if isinstance(n, ast.ClassDef) and n.name == nome), None)
        if classe is None:
            return f"classe {nome} não existe em {caminho}"
        nos = classe.body
    funcao = partes[-1]
    if not any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == funcao for n in nos):
        return f"teste {funcao} não existe em {caminho}"
    return None


@pytest.mark.parametrize("lista", sorted(LISTAS))
def test_toda_linha_e_um_id_de_teste_sem_comentario_nem_linha_vazia(lista: str) -> None:
    arquivo, _ = LISTAS[lista]
    ruins = [n for n, linha in enumerate(_linhas(arquivo), 1)
             if not linha.strip() or linha != linha.strip() or linha.startswith("#")
             or not linha.split("::")[0].endswith(".py")]
    assert not ruins, f"{arquivo.name}: linhas que o pytest não lê como id (a coleta volta vazia): {ruins}"


@pytest.mark.parametrize("lista", sorted(LISTAS))
def test_todo_id_existe_e_nenhum_se_repete(lista: str) -> None:
    arquivo, base = LISTAS[lista]
    linhas = _linhas(arquivo)
    repetidos = sorted({linha for linha in linhas if linhas.count(linha) > 1})
    assert not repetidos, f"id repetido: {repetidos}"
    faltam = [f"{ident}: {motivo}" for ident in linhas if (motivo := _existe(base, ident)) is not None]
    assert not faltam, "\n".join(faltam)


def test_esta_trava_esta_na_propria_lista() -> None:
    assert "tests/test_catracas.py" in _linhas(LISTAS["backend"][0])


def test_a_trava_reprova_id_que_nao_existe(tmp_path: Path) -> None:
    """A trava discrimina: classe, função e arquivo que faltam viram motivo; o que existe passa."""
    (tmp_path / "test_x.py").write_text("class C:\n    def test_a(self):\n        pass\n\ndef test_b():\n    pass\n",
                                        encoding="utf-8")
    assert _existe(tmp_path, "test_x.py::C::test_a") is None
    assert _existe(tmp_path, "test_x.py::test_b") is None
    assert _existe(tmp_path, "test_x.py") is None
    assert _existe(tmp_path, "test_x.py::test_a") == "teste test_a não existe em test_x.py"
    assert _existe(tmp_path, "test_x.py::D::test_a") == "classe D não existe em test_x.py"
    assert _existe(tmp_path, "test_y.py::test_b") == "arquivo não existe: test_y.py"
