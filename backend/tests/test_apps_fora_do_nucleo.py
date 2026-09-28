"""Nenhuma comparação com "instagram" fora do que é do Instagram (fase K1; design §18, critério da fase K).

O núcleo pergunta ao registro de apps (`AppDefinition`, `session_provider_of`, `SessionProviders`) e não decide por
nome de app. Este teste, por AST e sem importar `app`, reprova uma comparação que decida pelo Instagram:

- operando com texto que contém "instagram" (`== "instagram"`, `in ("com.instagram.android",)`, `case "instagram":`);
- operando com uma cadeia de atributos que passa por `.instagram` (`cfg.file.instagram.package`);
- operando com um nome importado de um módulo do Instagram (`from ...catalog.instagram import PACKAGE`).

Desde o ADR-052 não há mais "o que É do Instagram" em Python: o app é um pacote de DADO em
`app/conhecimento/apps/com.instagram.android/`, e `app/integrations/` só tem o motor genérico (`app_declarado/`). Fica
de fora nada (o `InstagramCfg` de `app/config.py` saiu na fatia 4). Texto que só MENCIONA o Instagram (mensagem, docstring,
argumento de função) não é comparação e não conta aqui: decidir é comparar. O texto tem catraca própria
(`TEXTO_LEGADO`), e a pasta de integrações também (`test_integracoes_so_tem_o_motor_generico`).

`EXCECOES` é a catraca, por (arquivo, função), como em `test_arquitetura.py`: só encolhe, e entrada órfã reprova.
Nível de prova: `simulated` (análise estática).
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]          # backend/

#: Onde comparar com o Instagram ainda seria o assunto do arquivo. Zerou com o ADR-052: saíram `app/integrations/` e
#: `app/planning/catalog/` (não têm mais nada do Instagram) e `app/config.py` (o `InstagramCfg` virou `contas:`).
FORA: tuple[str, ...] = ()

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
    # Desde o ADR-052 a pasta de integrações não é mais exceção: a comparação escondida lá também é pega.
    assert [(a, f, linha) for a, f, linha, _ in achados] == [
        ("app/integrations/instagram/auth.py", "f", 2), ("app/nucleo.py", "porta", 3), ("app/nucleo.py", "porta", 5),
        ("app/nucleo.py", "porta", 7), ("app/nucleo.py", "porta", 9), ("app/nucleo.py", "casar", 12)]


# ==================================================================== ADR-052: zero Python por app
def test_integracoes_so_tem_o_motor_generico() -> None:
    """Um app é uma pasta de DADO em `app/conhecimento/apps/<pacote>/`. Em `app/integrations/` fica só o motor que lê
    esse dado (`app_declarado/`): um `integrations/<app>/` novo é o caminho que o ADR-052 fechou."""
    pasta = RAIZ / "app" / "integrations"
    dentro = sorted(p.name for p in pasta.iterdir() if p.name != "__pycache__")
    assert dentro == ["__init__.py", "app_declarado"], f"código de app em integrations/: {dentro}"


#: Nomes HISTÓRICOS que contêm "instagram" e não são conhecimento de app: a tabela do perfil (`instagram_profiles`,
#: `instagram_credentials`), o prefixo das rotas do perfil (`/api/instagram/...`) e o nome antigo da variável da chave
#: mestra. Renomeá-los é migração e versão de contrato, não mudança de comportamento; ficam fora desta contagem.
NOMES_HISTORICOS = re.compile(r"\binstagram_(?:profiles|credentials)\b|^/instagram/|INSTAGRAM_CREDENTIALS_MASTER_KEY")

#: Texto (fora de docstring e de nome histórico) que cita o Instagram, por arquivo de `app/`. Não decide nada — quem
#: decide é pego acima —, mas é conhecimento de app escrito em Python, e a meta do ADR-052 é zero. Catraca: só
#: desce; arquivo que zera sai (entrada órfã reprova).
TEXTO_LEGADO: dict[str, int] = {
    "app/config.py": 1,                         # `BLOCOS_QUE_SAIRAM`: recusa o bloco antigo `instagram:` com o destino
    "app/modules/execution/domain/command_refinement.py": 1,    # exemplo ao modelo ("Outlook e Instagram")
    "app/planning/prompts.py": 1,               # exemplo ao modelo
}


def _docstrings(arvore: ast.AST) -> set[int]:
    ids: set[int] = set()
    for no in ast.walk(arvore):
        if isinstance(no, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and no.body:
            primeiro = no.body[0]
            if isinstance(primeiro, ast.Expr) and isinstance(primeiro.value, ast.Constant):
                ids.add(id(primeiro.value))
    return ids


def texto_do_instagram(raiz: Path) -> dict[str, list[tuple[int, str]]]:
    """arquivo → [(linha, texto)] de cada texto do código de `raiz/app` que cita o Instagram."""
    achados: dict[str, list[tuple[int, str]]] = {}
    for caminho in sorted((raiz / "app").rglob("*.py")):
        if "__pycache__" in caminho.parts:
            continue
        arvore = ast.parse(caminho.read_text(encoding="utf-8"))
        docs = _docstrings(arvore)
        for no in ast.walk(arvore):
            if (isinstance(no, ast.Constant) and isinstance(no.value, str) and id(no) not in docs
                    and "instagram" in NOMES_HISTORICOS.sub("", no.value).lower()):
                achados.setdefault(caminho.relative_to(raiz).as_posix(), []).append((no.lineno, no.value[:80]))
    return achados


def test_texto_do_instagram_no_codigo_so_desce() -> None:
    achados = texto_do_instagram(RAIZ)
    subiu = [f"{a}: {len(v)} (teto {TEXTO_LEGADO.get(a, 0)}) — {v}" for a, v in achados.items()
             if len(v) > TEXTO_LEGADO.get(a, 0)]
    assert not subiu, ("conhecimento de app escrito em Python (ADR-052): ponha no pacote do app ou pergunte ao "
                       "registro (rótulo, pacote âncora):\n  " + "\n  ".join(subiu))
    desceu = sorted(a for a, teto in TEXTO_LEGADO.items() if len(achados.get(a, [])) < teto)
    assert not desceu, f"a catraca desceu — baixe o teto em TEXTO_LEGADO: {desceu}"


def test_o_detector_de_texto_ignora_docstring_e_nome_historico(tmp_path: Path) -> None:
    app = tmp_path / "app"
    app.mkdir()
    (app / "m.py").write_text(
        '"""módulo que fala de instagram"""\n'
        "def f(db):\n"
        '    """docstring do Instagram"""\n'
        "    db.q('SELECT * FROM instagram_profiles')\n"
        "    rota = '/instagram/profiles'\n"
        "    return 'com.instagram.android', rota\n", encoding="utf-8")
    assert [linha for linha, _ in texto_do_instagram(tmp_path)["app/m.py"]] == [6]
