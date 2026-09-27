"""Nenhuma comparação com "instagram" fora do que é do Instagram (fase K1; design §18, critério da fase K).

O núcleo pergunta ao registro de apps (`AppDefinition`, `session_provider_of`, `SessionProviders`) e não decide por
nome de app. Este teste, por AST e sem importar `app`, reprova uma comparação que decida pelo Instagram:

- operando com texto que contém "instagram" (`== "instagram"`, `in ("com.instagram.android",)`, `case "instagram":`);
- operando com uma cadeia de atributos que passa por `.instagram` (`cfg.file.instagram.package`);
- operando com um nome importado de um módulo do Instagram (`from ...catalog.instagram import PACKAGE`).

Fica de fora o que É do Instagram: `app/integrations/**`, `app/planning/catalog/**` (o catálogo e o shim do
registro) e `app/config.py` (`InstagramCfg`). Texto que só MENCIONA o Instagram (mensagem, docstring, argumento de
função como `package_of_provider("instagram")`) não é comparação e não conta: decidir é comparar.

`EXCECOES` é a catraca, por (arquivo, função), como em `test_arquitetura.py`: só encolhe, e entrada órfã reprova.
Nível de prova: `simulated` (análise estática).
"""
from __future__ import annotations

import ast
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]          # backend/

#: Onde comparar com o Instagram é o assunto do arquivo.
FORA: tuple[str, ...] = ("app/integrations/", "app/planning/catalog/", "app/config.py")

#: (arquivo relativo a backend/, função que contém a comparação) → por que ainda não saiu. Vazio desde a K1.
EXCECOES: dict[tuple[str, str], str] = {}


def _e_do_instagram(nome_de_modulo: str) -> bool:
    return "instagram" in nome_de_modulo.lower()


def _nomes_importados_do_instagram(arvore: ast.AST) -> set[str]:
    nomes: set[str] = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.ImportFrom) and _e_do_instagram(no.module or ""):
            nomes.update(a.asname or a.name for a in no.names)
        elif isinstance(no, ast.Import):
            nomes.update((a.asname or a.name).split(".")[0] for a in no.names if _e_do_instagram(a.name))
    return nomes


def _cita_instagram(operando: ast.AST, importados: set[str]) -> str | None:
    for no in ast.walk(operando):
        if isinstance(no, ast.Constant) and isinstance(no.value, str) and "instagram" in no.value.lower():
            return repr(no.value)
        if isinstance(no, ast.Attribute) and no.attr.lower() == "instagram":
            return f".{no.attr}"
        if isinstance(no, ast.Name) and no.id in importados:
            return no.id
    return None


def _funcoes(arvore: ast.AST) -> dict[ast.AST, str]:
    """Cada nó → a função (ou `<módulo>`) que o contém, pelo nome qualificado."""
    dono: dict[ast.AST, str] = {}

    def visitar(no: ast.AST, nome: str) -> None:
        for filho in ast.iter_child_nodes(no):
            proximo = (f"{nome}.{filho.name}".lstrip(".") if isinstance(filho, (ast.FunctionDef, ast.AsyncFunctionDef,
                                                                                  ast.ClassDef)) else nome)
            dono[filho] = proximo or "<módulo>"
            visitar(filho, proximo)

    visitar(arvore, "")
    return dono


def comparacoes_com_instagram(raiz: Path) -> list[tuple[str, str, int, str]]:
    """(arquivo, função, linha, o que foi citado) de cada comparação que decide pelo Instagram sob `raiz/app`."""
    achados: list[tuple[str, str, int, str]] = []
    for caminho in sorted((raiz / "app").rglob("*.py")):
        relativo = caminho.relative_to(raiz).as_posix()
        if "__pycache__" in caminho.parts or relativo.startswith(FORA):
            continue
        arvore = ast.parse(caminho.read_text(encoding="utf-8"), filename=relativo)
        importados = _nomes_importados_do_instagram(arvore)
        dono = _funcoes(arvore)
        for no in ast.walk(arvore):
            operandos: list[ast.AST] = []
            if isinstance(no, ast.Compare):
                operandos = [no.left, *no.comparators]
            elif isinstance(no, ast.match_case):
                operandos = [no.pattern]
            for operando in operandos:
                citado = _cita_instagram(operando, importados)
                if citado is not None:
                    linha = getattr(no, "lineno", getattr(no.pattern, "lineno", 0)) if isinstance(no, ast.match_case) \
                        else no.lineno
                    achados.append((relativo, dono.get(no, "<módulo>"), linha, citado))
                    break
    return achados


def test_nenhuma_comparacao_com_instagram_fora_do_que_e_do_instagram() -> None:
    achados = comparacoes_com_instagram(RAIZ)
    novos = [f"{a}:{linha} em {f} compara com {c}" for a, f, linha, c in achados if (a, f) not in EXCECOES]
    assert not novos, ("o núcleo voltou a decidir pelo nome do app — pergunte ao registro de apps "
                       "(capabilities_of / session_provider_of / SessionProviders):\n  " + "\n  ".join(novos))
    orfas = sorted(set(EXCECOES) - {(a, f) for a, f, _, _ in achados})
    assert not orfas, f"exceção órfã (a comparação saiu — tire da lista): {orfas}"


def test_o_detector_reprova_cada_forma_e_ignora_o_que_nao_decide(tmp_path: Path) -> None:
    """Autoteste: sem ele, o teste de cima passaria sem olhar para nada (o cuidado de `test_cobertura_de_rotas`)."""
    app = tmp_path / "app"
    (app / "integrations" / "instagram").mkdir(parents=True)
    (app / "nucleo.py").write_text(
        "from app.planning.catalog.instagram import PACKAGE as IG\n"
        "def porta(p, cfg):\n"
        "    if p == 'instagram':\n"
        "        return 1\n"
        "    if p != cfg.file.instagram.package:\n"
        "        return 2\n"
        "    if p in ('com.instagram.android', 'x'):\n"
        "        return 3\n"
        "    return p == IG\n"
        "def casar(p):\n"
        "    match p:\n"
        "        case 'instagram':\n"
        "            return 4\n"
        "def nao_decide(registro):\n"
        "    '''docstring que fala de instagram'''\n"
        "    registro.package_of_provider('instagram')\n"
        "    return f'sessão do Instagram: {registro}'\n", encoding="utf-8")
    (app / "integrations" / "instagram" / "auth.py").write_text("def f(p):\n    return p == 'instagram'\n",
                                                               encoding="utf-8")
    achados = comparacoes_com_instagram(tmp_path)
    assert [(f, linha) for _, f, linha, _ in achados] == [
        ("porta", 3), ("porta", 5), ("porta", 7), ("porta", 9), ("casar", 12)]
    assert all(a == "app/nucleo.py" for a, _, _, _ in achados)
