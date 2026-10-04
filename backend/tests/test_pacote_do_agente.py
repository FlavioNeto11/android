"""O pacote do agente do worker: `backend/worker-manifest.txt` é o fecho de import, e a cópia feita só com ele importa.

K-034: a suíte roda com a árvore do repositório COMPLETA, então um import novo no agente fora do que os instaladores
copiam passava em tudo e só dava `ImportError` em `C:\\farm\\agent` — e só no dia em que alguém atualizasse o agente,
o que exige autorização. Foi o caso de `devices/adb.py` → `conectividade` → `models` (relatório 04 §1.1).

Aqui ficam as duas metades da prova, sem adb, rede nem worker:
- o manifesto é EXATAMENTE o fecho de import de `app.worker.*`, medido por AST com o grafo de
  `tests/test_arquitetura.py` (nada faltando; nada sobrando fora de `contracts/`, que é puro);
- uma cópia feita só com o manifesto, num subprocesso com `PYTHONPATH` apontando SÓ para ela (o `backend/` do
  repositório fora do caminho), importa todos os módulos do pacote e passa pelos imports tardios.

Os instaladores de verdade (`-SoPacote`/`--so-pacote`) são exercitados em `tests/test_instalacao_do_worker.py`,
com o mesmo `importar_da_copia` daqui.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from .test_arquitetura import fecho, grafo, modulos

BACKEND = Path(__file__).resolve().parents[1]
APP = BACKEND / "app"
MANIFESTO = BACKEND / "worker-manifest.txt"


def ler_manifesto(caminho: Path = MANIFESTO) -> list[str]:
    """As entradas, na ordem do arquivo — a mesma leitura dos dois instaladores (`#` comenta, linha vazia some)."""
    entradas = []
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        entrada = linha.split("#", 1)[0].strip()
        if entrada:
            entradas.append(entrada)
    return entradas


def arquivos_do_pacote(entradas: list[str], app: Path = APP) -> set[str]:
    """Os arquivos que o pacote leva, relativos a `app/` e com barra normal (`pasta/` expande, sem `__pycache__`)."""
    fora: set[str] = set()
    for entrada in entradas:
        if entrada.endswith("/"):
            fora |= {p.relative_to(app).as_posix() for p in (app / entrada).rglob("*")
                     if p.is_file() and "__pycache__" not in p.parts}
        else:
            fora.add(entrada)
    return fora


def modulo_de(relativo: str) -> str:
    partes = ["app", *relativo.removesuffix(".py").split("/")]
    return ".".join(partes[:-1] if partes[-1] == "__init__" else partes)


def modulos_do_pacote(entradas: list[str]) -> set[str]:
    return {modulo_de(r) for r in arquivos_do_pacote(entradas) if r.endswith(".py")}


def fecho_do_agente() -> set[str]:
    raiz = {m for m in modulos() if m == "app.worker" or m.startswith("app.worker.")}
    return fecho(raiz, grafo(so_executa=True))


def copiar_pelo_manifesto(destino_app: Path, entradas: list[str]) -> None:
    """O que os instaladores fazem, em Python: cada entrada de `backend/app/` para `destino_app/`."""
    for entrada in entradas:
        rel = entrada.rstrip("/")
        alvo = destino_app / rel
        alvo.parent.mkdir(parents=True, exist_ok=True)
        if entrada.endswith("/"):
            shutil.copytree(APP / rel, alvo, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(APP / rel, alvo)


#: Roda DENTRO da cópia. Importa os pontos de entrada, passa pelos imports tardios que só acontecem em execução
#: (`AndroidCfg.perfil` → `devices.perfis`; a sonda de rede do `adb.py`) e depois cada módulo do pacote.
_IMPORTAR = r"""
import importlib, json, subprocess, sys
com_pil, modulos = sys.argv[1] == "1", json.loads(sys.argv[2])
if not com_pil:
    sys.modules["PIL"] = None          # agente sem Pillow: some a observação na origem, o resto funciona
import app.worker.agent, app.worker.__main__
from app.config import AndroidCfg
AndroidCfg().ram_efetiva()
from app.devices.adb import Adb
adb = Adb(None, "emulator-5554")
adb._run = lambda args, timeout=0: subprocess.CompletedProcess(args, 0, "R=1\nV=1\nD=1\nT=1\n", "")
assert adb.connectivity_probe() == {"route": True, "validated": True, "dns": True, "tcp_443": True}
for m in modulos:
    if com_pil or m != "app.devices.codificacao":
        importlib.import_module(m)
try:
    import app.models
    models_visivel = True
except ImportError:
    models_visivel = False
from app.worker.agent import FEATURES
carregados = sorted(n for n in sys.modules if n == "app" or n.startswith("app."))
print(json.dumps({"carregados": carregados, "models_visivel": models_visivel, "features": list(FEATURES),
                  "origens": {n: getattr(sys.modules[n], "__file__", None) for n in carregados}}))
"""


def importar_da_copia(raiz: Path, entradas: list[str], *, com_pil: bool = True) -> dict[str, object]:
    """Importa o pacote instalado em `raiz/app` num subprocesso que só enxerga `raiz` (e o venv, pelos terceiros).

    Confere, além do código de saída, que TUDO que carregou veio da cópia: um `app.*` resolvido no `backend/` do
    repositório tornaria a prova vazia."""
    env = {**os.environ, "PYTHONPATH": str(raiz)}
    r = subprocess.run([sys.executable, "-B", "-c", _IMPORTAR, "1" if com_pil else "0",
                        json.dumps(sorted(modulos_do_pacote(entradas)))],
                       capture_output=True, text=True, timeout=180, cwd=str(raiz), env=env)
    assert r.returncode == 0, f"o agente instalado não importa:\n{r.stdout}\n{r.stderr}"
    saida: dict[str, object] = json.loads(r.stdout.strip().splitlines()[-1])
    base = os.path.normcase(str(raiz.resolve()))
    origens = saida["origens"]
    assert isinstance(origens, dict)
    fora = {n: o for n, o in origens.items()
            if not (isinstance(o, str) and os.path.normcase(str(Path(o).resolve())).startswith(base))}
    assert not fora, f"módulo carregado de fora da cópia instalada: {fora}"
    assert saida["models_visivel"] is False, "o subprocesso enxerga o backend do repositório: a prova seria vazia"
    carregados = saida["carregados"]
    assert isinstance(carregados, list)
    alem = sorted(set(carregados) - modulos_do_pacote(entradas))
    assert not alem, f"carregou módulo que o manifesto não leva: {alem}"
    return saida


# ---------------------------------------------------------------- testes
def test_o_manifesto_tem_entradas_validas() -> None:
    entradas = ler_manifesto()
    assert len(entradas) == len(set(entradas)), "entrada repetida no manifesto"
    for e in entradas:
        assert "\\" not in e and ".." not in e and not e.startswith("/") and ":" not in e, e
        assert (APP / e.rstrip("/")).is_dir() if e.endswith("/") else (APP / e).is_file(), f"não existe: {e}"


def test_o_manifesto_e_exatamente_o_fecho_de_import_do_agente() -> None:
    no_pacote, no_fecho = modulos_do_pacote(ler_manifesto()), fecho_do_agente()
    faltam = sorted(no_fecho - no_pacote)
    assert not faltam, (f"o agente importa e o manifesto não leva (ImportError no agente instalado): {faltam}"
                        " — acrescente em backend/worker-manifest.txt")
    # `contracts/` vai inteiro: é puro por regra (test_arquitetura) e copiá-lo sobrando não custa nada.
    sobram = sorted(m for m in no_pacote - no_fecho if not m.startswith("app.contracts"))
    assert not sobram, f"o manifesto leva o que o agente não importa (código do central na máquina do worker): {sobram}"


@pytest.mark.parametrize("com_pil", [True, False], ids=["com_pillow", "sem_pillow"])
def test_a_copia_pelo_manifesto_importa_sem_o_backend(tmp_path: Path, com_pil: bool) -> None:
    entradas = ler_manifesto()
    copiar_pelo_manifesto(tmp_path / "app", entradas)
    saida = importar_da_copia(tmp_path, entradas, com_pil=com_pil)
    features = saida["features"]
    assert isinstance(features, list)
    assert ("observe_local" in features) is com_pil, features
    assert "boot_reservations" in features


def test_a_impressao_do_codigo_e_a_mesma_no_checkout_e_na_copia_do_instalador(tmp_path: Path) -> None:
    """Item 29.59: o central calcula a impressão pelo manifesto; o agente, varrendo o `app/` que o instalador montou.
    Se os dois conjuntos de arquivos não coincidirem, todo worker real aparece defasado — e nenhum teste de unidade
    do registro veria. O que o instalador e o Python acrescentam na cópia (selo de build, cache) fica de fora."""
    from app.version import codigo_do_agente, entradas_do_manifesto

    assert entradas_do_manifesto(MANIFESTO) == ler_manifesto()
    copia = tmp_path / "app"
    copiar_pelo_manifesto(copia, ler_manifesto())
    (copia / "BUILD_VERSION").write_text("0.1.0+outro00\n", encoding="ascii")
    (copia / "worker" / "__pycache__").mkdir(exist_ok=True)
    (copia / "worker" / "__pycache__" / "agent.cpython-312.pyc").write_bytes(b"\x00cache")
    (copia / "solto.pyc").write_bytes(b"\x00cache")
    for sobra in ("agent.py.orig", ".agent.py.swp", "agent.py~"):     # sobras de merge e de editor
        (copia / "worker" / sobra).write_text("x\n", encoding="utf-8")

    no_checkout = codigo_do_agente.__wrapped__(APP)
    assert no_checkout is not None
    assert codigo_do_agente.__wrapped__(copia) == no_checkout

    # Fim de linha do Windows no checkout (autocrlf) não é outro código.
    alvo = copia / "worker" / "agent.py"
    alvo.write_bytes(alvo.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    assert codigo_do_agente.__wrapped__(copia) == no_checkout

    # Uma linha a mais no código do agente é outro código.
    alvo.write_bytes(alvo.read_bytes() + b"\n# mudou\n")
    assert codigo_do_agente.__wrapped__(copia) != no_checkout


def test_codigo_so_do_central_nao_muda_a_impressao_do_agente(tmp_path: Path) -> None:
    """O que não vai para o worker (docs, plano, módulos só do central) não pode mudar a impressão: é exatamente o
    commit que acendia o selo sem motivo."""
    from app.version import codigo_do_agente

    raiz = tmp_path / "backend"
    copiar_pelo_manifesto(raiz / "app", ler_manifesto())
    shutil.copy2(MANIFESTO, raiz / "worker-manifest.txt")
    antes = codigo_do_agente.__wrapped__(raiz / "app")
    (raiz / "app" / "api.py").write_text("# só do central\n", encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text("docs\n", encoding="utf-8")
    assert codigo_do_agente.__wrapped__(raiz / "app") == antes
    (raiz / "app" / "version.py").write_text((raiz / "app" / "version.py").read_text(encoding="utf-8") + "\n#\n",
                                             encoding="utf-8")
    assert codigo_do_agente.__wrapped__(raiz / "app") != antes
