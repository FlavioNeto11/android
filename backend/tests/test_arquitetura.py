"""Regras de arquitetura do backend, por AST, sem importar `app` (docs/design/evolucao-arquitetural.md §9, fase A).

Mesma técnica e mesma disciplina de `tests/test_cobertura_de_rotas.py`: a lista de exceções é a CONFISSÃO do que
ainda não está no lugar; uma exceção que deixa de ser necessária reprova o teste até sair da lista (exceção órfã),
então a contagem só diminui.

Quais imports contam para quê:
- direção/pureza (camadas, bibliotecas): TODOS — topo, local (dentro de função) e `if TYPE_CHECKING:`;
- ciclos e fecho do agente do worker: só os que EXECUTAM — topo e local; `TYPE_CHECKING` não roda.
- ponto cego declarado: `importlib.import_module` (hoje só em `app/modules/applications/infrastructure/registry.py`,
  `_importar`, que carrega os manifestos embutidos — antes da fase K1, em `app/planning/catalog/__init__.py`).
"""
from __future__ import annotations

import ast
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

RAIZ = Path(os.environ.get("ARQ_RAIZ") or Path(__file__).resolve().parents[1])   # backend/
APP = RAIZ / "app"
STDLIB = frozenset(sys.stdlib_module_names)


# ================================================================== grafo
@dataclass(frozen=True)
class Imp:
    origem: str          # módulo que importa (app.x.y)
    alvo: str            # módulo interno resolvido (app.a.b) ou raiz externa (fastapi)
    interno: bool
    linha: int
    tipo: str            # "topo" | "local" | "typecheck"

    @property
    def executa(self) -> bool:
        return self.tipo != "typecheck"


def _nome(p: Path) -> str:
    partes = list(p.relative_to(RAIZ).with_suffix("").parts)
    return ".".join(partes[:-1] if partes[-1] == "__init__" else partes)


@lru_cache(maxsize=1)
def modulos() -> dict[str, Path]:
    return {_nome(p): p for p in sorted(APP.rglob("*.py")) if "__pycache__" not in p.parts}


def _resolver(alvo: str) -> str | None:
    mods = modulos()
    while alvo:
        if alvo in mods:
            return alvo
        alvo = alvo.rpartition(".")[0]
    return None


def _e_type_checking(t: ast.expr) -> bool:
    return (isinstance(t, ast.Name) and t.id == "TYPE_CHECKING") or (
        isinstance(t, ast.Attribute) and t.attr == "TYPE_CHECKING")


def _visitar(nó: ast.AST, em_funcao: bool, em_tc: bool, saída: list[tuple[ast.stmt, str]]) -> None:
    for filho in ast.iter_child_nodes(nó):
        if isinstance(filho, (ast.Import, ast.ImportFrom)):
            saída.append((filho, "typecheck" if em_tc else "local" if em_funcao else "topo"))
        elif isinstance(filho, ast.If) and _e_type_checking(filho.test):
            for s in filho.body:
                _visitar(ast.Module(body=[s], type_ignores=[]), em_funcao, True, saída)
            for s in filho.orelse:
                _visitar(ast.Module(body=[s], type_ignores=[]), em_funcao, em_tc, saída)
        else:
            _visitar(filho, em_funcao or isinstance(filho, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)),
                     em_tc, saída)


@lru_cache(maxsize=1)
def imports() -> tuple[Imp, ...]:
    mods, fora = modulos(), []
    for mod, caminho in mods.items():
        pacote = mod if caminho.name == "__init__.py" else mod.rpartition(".")[0]
        achados: list[tuple[ast.stmt, str]] = []
        _visitar(ast.parse(caminho.read_text(encoding="utf-8"), filename=str(caminho)), False, False, achados)
        for nó, tipo in achados:
            if isinstance(nó, ast.Import):
                nomes = [a.name for a in nó.names]
            else:
                if nó.module == "__future__":
                    continue
                if nó.level:
                    base = pacote.split(".")[: len(pacote.split(".")) - (nó.level - 1)]
                    raiz_rel = ".".join(base + ([nó.module] if nó.module else []))
                else:
                    raiz_rel = nó.module or ""
                # `from . import x`: se `x` é submódulo, a dependência é o submódulo
                nomes = [f"{raiz_rel}.{a.name}" if f"{raiz_rel}.{a.name}" in mods else raiz_rel for a in nó.names]
            for n in nomes:
                if n.split(".")[0] == "app":
                    r = _resolver(n)
                    if r and r != mod:
                        fora.append(Imp(mod, r, True, nó.lineno, tipo))
                else:
                    fora.append(Imp(mod, n.split(".")[0], False, nó.lineno, tipo))
    return tuple(fora)


def grafo(*, so_executa: bool, so_topo: bool = False) -> dict[str, set[str]]:
    g: dict[str, set[str]] = defaultdict(set)
    for i in imports():
        if i.interno and (i.executa or not so_executa) and (i.tipo == "topo" or not so_topo):
            g[i.origem].add(i.alvo)
    return g


def fecho(inicio: set[str], g: dict[str, set[str]]) -> set[str]:
    """Tudo que roda quando se importa `inicio` — inclui os `__init__` dos pacotes do caminho."""
    vistos, pilha = set(), list(inicio)
    while pilha:
        m = pilha.pop()
        if m in vistos:
            continue
        vistos.add(m)
        partes = m.split(".")
        pilha.extend(p for p in (".".join(partes[:k]) for k in range(1, len(partes))) if p in modulos())
        pilha.extend(g.get(m, ()))
    return vistos


def componentes(g: dict[str, set[str]], nos: set[str] | None = None) -> list[frozenset[str]]:
    """Tarjan iterativo: só componentes com mais de um nó (ciclos)."""
    idx: dict[str, int] = {}
    low: dict[str, int] = {}
    pilha: list[str] = []
    na_pilha: set[str] = set()
    fora: list[frozenset[str]] = []
    contador = 0
    for raiz in sorted(nos if nos is not None else modulos()):
        if raiz in idx:
            continue
        trabalho = [(raiz, iter(sorted(g.get(raiz, ()))))]
        idx[raiz] = low[raiz] = contador; contador += 1; pilha.append(raiz); na_pilha.add(raiz)
        while trabalho:
            v, filhos = trabalho[-1]
            avancou = False
            for w in filhos:
                if w not in idx:
                    idx[w] = low[w] = contador; contador += 1; pilha.append(w); na_pilha.add(w)
                    trabalho.append((w, iter(sorted(g.get(w, ())))))
                    avancou = True
                    break
                if w in na_pilha:
                    low[v] = min(low[v], idx[w])
            if avancou:
                continue
            trabalho.pop()
            if trabalho:
                low[trabalho[-1][0]] = min(low[trabalho[-1][0]], low[v])
            if low[v] == idx[v]:
                comp = set()
                while True:
                    w = pilha.pop(); na_pilha.discard(w); comp.add(w)
                    if w == v:
                        break
                if len(comp) > 1:
                    fora.append(frozenset(comp))
    return fora


def _casa(mod: str, padrao: str) -> bool:
    """`app.modules.*.domain` casa `app.modules.skills.domain` e tudo abaixo dele."""
    a, b = mod.split("."), padrao.split(".")
    return len(a) >= len(b) and all(y == "*" or x == y for x, y in zip(a, b))


# ================================================================== regras (dados)
#: Bibliotecas de INFRAESTRUTURA. O domínio e os contratos não as veem, nem por import tardio nem por TYPE_CHECKING.
INFRA = frozenset({"fastapi", "starlette", "uvicorn", "sqlite3", "psycopg", "appium", "selenium", "anthropic",
                   "openai", "httpx", "websockets", "nats", "boto3", "PIL", "psutil", "subprocess", "socket",
                   "asyncio"})
#: Pacotes internos que são IMPLEMENTAÇÃO do central (banco, estado, API, fila, IA, aparelhos...).
CENTRAL = ("app.api", "app.main", "app.state", "app.db", "app.taskqueue", "app.planning", "app.commands",
           "app.releases", "app.social", "app.integrations", "app.training", "app.automation", "app.events",
           "app.storage", "app.supervisor", "app.vitrine", "app.desempenho", "app.apps_overview", "app.contexto",
           "app.tools", "app.devices.manager", "app.devices.proxy", "app.workers.local", "app.workers.registry",
           "app.workers.portao", "app.workers.captura")

#: camada → (padrões que ela abrange, padrões internos PERMITIDOS). Vale para módulos que ainda não existem:
#: a regra nasce antes do código, e o primeiro arquivo de `app/modules/<x>/domain/` já nasce sob ela.
#: `kernel` é o `app.shared` (D2/D5): raiz do grafo como `contracts`, visível a todo domínio, sem ver ninguém.
CAMADAS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "contratos": (("app.contracts",), ("app.contracts",)),
    "kernel": (("app.shared",), ("app.shared",)),
    "dominio": (("app.modules.*.domain",), ("app.contracts", "app.shared", "app.modules.*.domain", "app.util")),
    "aplicacao": (("app.modules.*.application",),
                  ("app.contracts", "app.shared", "app.modules.*.domain", "app.modules.*.application",
                   "app.modules.*.ports", "app.util")),
}

#: Terceiros que uma camada pura pode ver (além da stdlib fora de INFRA). Alinhado a 04-worker-contratos §3.2.
EXTERNOS_PUROS = frozenset({"pydantic"})

#: Onde cada biblioteca de infraestrutura pode aparecer no código LEGADO (medido em 82b1057). Fora daqui só nos
#: adaptadores novos (`app.modules.*.adapters`) e, para o framework HTTP, na camada de apresentação dos contextos
#: (`APRESENTACAO`). Entrada que deixa de importar a biblioteca reprova (órfã).
ONDE_A_INFRA_MORA: dict[str, frozenset[str]] = {
    "fastapi":   frozenset({"app.api", "app.main"}),
    "uvicorn":   frozenset({"app.main"}),
    "sqlite3":   frozenset({"app.db"}),
    "psycopg":   frozenset({"app.db"}),
    "anthropic": frozenset({"app.planning.anthropic_provider"}),
    "httpx":     frozenset({"app.planning.openai_provider"}),
    "appium":    frozenset({"app.automation.appium_driver"}),
    "selenium":  frozenset({"app.automation.appium_driver"}),
    "nats":      frozenset({"app.commands.transport"}),
    "boto3":     frozenset({"app.storage"}),
    "PIL":       frozenset({"app.devices.codificacao", "app.devices.manager", "app.taskqueue.executor"}),
}
#: A camada de apresentação de um contexto (`app.modules.<x>.presentation`, fase F) é HTTP por definição: o roteador
#: fala FastAPI. Só o framework HTTP entra por aqui — banco, IA e aparelho continuam nos adaptadores.
APRESENTACAO: dict[str, str] = {"fastapi": "app.modules.*.presentation", "starlette": "app.modules.*.presentation"}

#: O agente do worker: o que o fecho de `app.worker.*` pode tocar (docs/worker.md; CI `worker-agent-smoke`).
#: Lista EXATA: módulo que sai do fecho sai daqui (entrada órfã reprova). `app.models` saiu quando a sonda de rede
#: foi para `devices/sonda_rede.py` — o `adb.py` a alcançava por `conectividade`, e `models.py` não vai para o
#: agente instalado. `app.workers.*` e `app.devices.verbs` saíram quando o agente passou a importar o protocolo e o
#: vocabulário de `app.contracts.worker`. O que os instaladores copiam é `backend/worker-manifest.txt`, conferido
#: contra este mesmo fecho em `tests/test_pacote_do_agente.py`.
WORKER_INTERNOS = ("app", "app.worker", "app.contracts", "app.contracts.worker", "app.contracts.worker.protocol",
                   "app.contracts.worker.verbos", "app.devices", "app.devices.adb", "app.devices.avd",
                   "app.devices.codificacao", "app.devices.emulator", "app.devices.perfis", "app.devices.prontidao",
                   "app.devices.recursos", "app.devices.sdk", "app.devices.sonda_rede", "app.config", "app.util",
                   "app.version", "app.metricas", "app.security", "app.security.redaction")
#: `worker-requirements.txt`, pelo nome de import.
WORKER_EXTERNOS = frozenset({"pydantic", "pydantic_settings", "dotenv", "psutil", "websockets", "yaml", "PIL"})

#: Ciclos legados em tempo de execução (medido em 82b1057). Um componente pode encolher ou sumir; nunca ganhar
#: módulo nem nascer outro. Módulo de `app.modules`/`app.contracts` em ciclo reprova sempre.
#: Havia um segundo, `{api, state, devices.proxy, releases.service, taskqueue.scheduler, taskqueue.service,
#: vitrine, workers.local}` (8), fechado pelos 8 imports tardios `→ api`. Sumiu na fase A: o despacho foi para
#: `commands/despacho.py`, e o gerador de id legível (`util.novo_id_de_app`) e o cadastro de apps
#: (`AppRepository`) desfizeram o par que restava, `vitrine` ↔ `devices.proxy`.
CICLOS_LEGADOS: tuple[frozenset[str], ...] = (
    frozenset({"app.planning.anthropic_provider", "app.planning.openai_provider", "app.planning.parsing",
               "app.planning.prompts", "app.planning.provider", "app.planning.routing",
               "app.planning.simulated_provider", "app.planning.training"}),
)
#: Comandos `import` internos DENTRO de função, por pacote (quase todos contornam ciclo). Catraca: só desce.
IMPORTS_TARDIOS: dict[str, int] = {
    "app.api": 18, "app.automation": 1, "app.commands": 1, "app.config": 1, "app.devices": 3, "app.planning": 8,
    "app.releases": 15, "app.social": 5, "app.state": 5, "app.supervisor": 1, "app.training": 1,
    "app.vitrine": 1, "app.workers": 1,
}
#: `Any` em anotação (parâmetro, retorno, variável anotada), por pacote. Catraca: só desce. Código novo: zero.
#: Código MOVIDO leva o seu `Any` junto: `app.commands` subiu 1 (`executar_envelope(envelope: dict[str, Any])`, que
#: veio da API com o despacho) enquanto `app.api` desceu 9 — os outros 8 saíram tipados na mudança.
#: `app.integrations` saiu (27 → 0) na fatia 3 do ADR-052: o login do Instagram virou o motor genérico
#: `integrations/app_declarado/`, tipado sem `Any`, e o conhecimento do app virou dado.
ANY_LEGADO: dict[str, int] = {
    "app.api": 143, "app.taskqueue": 120, "app.social": 91, "app.devices": 91, "app.planning": 70, "app.state": 50,
    "app.worker": 45, "app.releases": 32, "app.desempenho": 26, "app.vitrine": 23,
    "app.commands": 26, "app.training": 22, "app.workers": 14, "app.automation": 16, "app.db": 12,
    "app.apps_overview": 9, "app.metricas": 9, "app.main": 8, "app.security": 8, "app.config": 6, "app.models": 5,
    "app.contexto": 3, "app.tools": 3, "app.events": 2, "app.identidade": 2, "app.storage": 2,
}


def _novo(mod: str) -> bool:
    return mod.startswith(("app.modules.", "app.contracts", "app.shared")) or mod in ("app.modules",)


# ================================================================== testes
def test_contratos_dominio_e_aplicacao_nao_veem_infraestrutura() -> None:
    erros = []
    for i in imports():
        for camada, (abrange, permitidos) in CAMADAS.items():
            if not any(_casa(i.origem, p) for p in abrange):
                continue
            if not i.interno and (i.alvo in INFRA or (i.alvo not in STDLIB and i.alvo not in EXTERNOS_PUROS)):
                erros.append(f"{camada}: {i.origem}:{i.linha} importa {i.alvo} ({i.tipo})")
            elif i.interno and not any(_casa(i.alvo, p) for p in permitidos):
                erros.append(f"{camada}: {i.origem}:{i.linha} importa {i.alvo} ({i.tipo})")
    assert not erros, "camada pura importando o que não devia:\n  " + "\n  ".join(erros)


def test_infraestrutura_so_mora_onde_ja_morava() -> None:
    erros, usados = [], defaultdict(set)
    for i in imports():
        if i.interno or i.alvo not in ONDE_A_INFRA_MORA:
            continue
        usados[i.alvo].add(i.origem)
        if i.origem not in ONDE_A_INFRA_MORA[i.alvo] and not _casa(i.origem, "app.modules.*.adapters")                 and not (i.alvo in APRESENTACAO and _casa(i.origem, APRESENTACAO[i.alvo])):
            erros.append(f"{i.origem}:{i.linha} importa {i.alvo} — fora de {sorted(ONDE_A_INFRA_MORA[i.alvo])}")
    orfas = [f"{lib} em {m}" for lib, ms in ONDE_A_INFRA_MORA.items() for m in ms if m not in usados[lib]]
    assert not erros, "biblioteca de infraestrutura em lugar novo:\n  " + "\n  ".join(erros)
    assert not orfas, f"exceção órfã (a catraca desceu — tire da lista): {orfas}"


def test_agente_do_worker_nao_carrega_o_central() -> None:
    g = grafo(so_executa=True)
    raiz = {m for m in modulos() if m == "app.worker" or m.startswith("app.worker.")}
    alcancados = fecho(raiz, g)
    intrusos = sorted(m for m in alcancados if m not in WORKER_INTERNOS and m not in raiz)
    assert not intrusos, f"o agente do worker passou a carregar código do central: {intrusos}"
    orfaos = sorted(m for m in WORKER_INTERNOS if m not in alcancados)
    assert not orfaos, f"entrada órfã em WORKER_INTERNOS (o fecho encolheu — tire da lista): {orfaos}"
    assert not [m for m in alcancados if m.startswith(CENTRAL)], "fecho do agente toca pacote do central"
    externos = {(i.alvo, f"{i.origem}:{i.linha}") for i in imports()
                if i.origem in alcancados and not i.interno and i.executa and i.alvo not in STDLIB}
    fora = sorted(e for e in externos if e[0] not in WORKER_EXTERNOS)
    assert not fora, f"dependência fora de worker-requirements.txt no fecho do agente: {fora}"


def test_nenhum_ciclo_no_import_de_topo() -> None:
    """Ciclo de topo é ImportError esperando a ordem certa de import. Hoje são zero — e ficam zero."""
    assert componentes(grafo(so_executa=True, so_topo=True)) == []


def test_ciclos_em_execucao_so_encolhem_e_nao_alcancam_codigo_novo() -> None:
    atuais = componentes(grafo(so_executa=True))
    novos = [sorted(c) for c in atuais if any(_novo(m) for m in c)]
    assert not novos, f"módulo novo em ciclo: {novos}"
    cresceram = [sorted(c) for c in atuais if not any(c <= base for base in CICLOS_LEGADOS)]
    assert not cresceram, f"ciclo novo ou maior que o legado: {cresceram}"
    desfeitos = [sorted(b) for b in CICLOS_LEGADOS if not any(c == b for c in atuais)]
    assert not desfeitos, f"ciclo legado encolheu ou sumiu — atualize CICLOS_LEGADOS: {desfeitos}"


def _pacote(mod: str) -> str:
    return ".".join(mod.split(".")[:2])


def _catraca(atual: dict[str, int], base: dict[str, int], o_que: str) -> None:
    subiu = {k: (v, base.get(k, 0)) for k, v in atual.items() if v > base.get(k, 0)}
    desceu = {k: (atual.get(k, 0), v) for k, v in base.items() if atual.get(k, 0) < v}
    assert not subiu, f"{o_que} aumentou (atual, teto): {subiu}"
    assert not desceu, f"{o_que} diminuiu — baixe a base para travar o ganho (atual, base): {desceu}"


def test_imports_tardios_so_diminuem() -> None:
    comandos = {(i.origem, i.linha) for i in imports() if i.interno and i.tipo == "local"}
    atual: dict[str, int] = defaultdict(int)
    for origem, _ in comandos:
        atual[_pacote(origem)] += 1
    assert not [o for o, _ in comandos if _novo(o)], "código novo não contorna ciclo com import tardio"
    _catraca(dict(atual), IMPORTS_TARDIOS, "imports internos dentro de função")


def _tem_any(n: ast.AST | None) -> bool:
    return n is not None and any((isinstance(x, ast.Name) and x.id == "Any") or
                                 (isinstance(x, ast.Attribute) and x.attr == "Any") for x in ast.walk(n))


def test_any_so_diminui() -> None:
    atual: dict[str, int] = defaultdict(int)
    for mod, caminho in modulos().items():
        for n in ast.walk(ast.parse(caminho.read_text(encoding="utf-8"))):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                a = n.args
                anot = [x.annotation for x in (*a.posonlyargs, *a.args, *a.kwonlyargs, a.vararg, a.kwarg)
                        if x is not None and x.arg not in ("self", "cls")]
                atual[_pacote(mod)] += sum(map(_tem_any, anot)) + _tem_any(n.returns)
            elif isinstance(n, ast.AnnAssign):
                atual[_pacote(mod)] += _tem_any(n.annotation)
    novos = {k: v for k, v in atual.items() if k in ("app.modules", "app.contracts", "app.shared") and v}
    assert not novos, f"Any em código novo: {novos}"
    _catraca({k: v for k, v in atual.items() if v}, ANY_LEGADO, "Any em anotação")


def test_contextos_novos_formam_um_dag() -> None:
    """`app.modules.<a>` → `app.modules.<b>` → `app.modules.<a>` reprova mesmo que os MÓDULOS não fechem ciclo."""
    g: dict[str, set[str]] = defaultdict(set)
    for i in imports():
        if i.interno and i.origem.startswith("app.modules.") and i.alvo.startswith("app.modules."):
            a, b = ".".join(i.origem.split(".")[:3]), ".".join(i.alvo.split(".")[:3])
            if a != b:
                g[a].add(b)
    nos = set(g) | {b for bs in g.values() for b in bs}
    assert componentes(g, nos) == [], f"contextos em ciclo: {componentes(g, nos)}"
