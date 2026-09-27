"""Fase E da evolução arquitetural (docs/design/evolucao-arquitetural.md §12): DSL `automation/v1alpha1` → IR →
`Plan` de hoje. Tudo puro: sem banco, sem aparelho, sem IA.

Goldens em `tests/fixtures/dsl/v1alpha1/`:
- `validos/*.yaml` compilam, e o `Plan` sai igual ao `*.esperado.json` (regere com `ATUALIZAR_GOLDEN=1` e revise o
  diff: ele É a mudança de comportamento);
- `invalidos/<CODIGO>.yaml` dá exatamente os erros de `*.esperado.json`, e todo código `E_*` tem a sua;
- `auxiliares/*.yaml` são skills que só existem para ser compostas (ciclo, profundidade).

A invariante da §12.1 (decisão 4): o compilador é o único produtor de `Plan` para skill nova e nunca gera nem executa
Python — o documento do LLM é dado.
"""
from __future__ import annotations

import ast
import builtins
import inspect
import json
import os
import re
import typing
from collections.abc import Mapping
from pathlib import Path

import pytest
import yaml

from app.contracts.skills.v1alpha1 import SkillDocument
from app.models import Plan, PlanStep, Postcondition
from app.modules.capabilities.infrastructure.catalog_registry import CatalogCapabilityRegistry
from app.modules.skills.domain import compiler as compilador
from app.modules.skills.domain.compiler import CapabilityLookup, LockedSkill, SkillCompiler, match_key
from app.modules.skills.domain.errors import Code, pointer
from app.modules.skills.domain.ir import content_hash
from app.modules.skills.infrastructure.lowering import SkillPlanCompiler, plan_hash
from app.planning.capabilities import CapabilityNode, load_catalog
from app.planning.catalog.instagram import PACKAGE

BACKEND = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures" / "dsl" / "v1alpha1"
APPS = {"instagram": PACKAGE, "qa": "com.example.qa"}
ATUALIZAR = os.environ.get("ATUALIZAR_GOLDEN") == "1"


def _carregar(p: Path) -> dict[str, object]:
    dado = yaml.safe_load(p.read_text(encoding="utf-8"))
    assert isinstance(dado, dict)
    return dado


def _docs() -> dict[str, dict[str, object]]:
    saida: dict[str, dict[str, object]] = {}
    for pasta in ("validos", "auxiliares"):
        for p in sorted((FIXTURES / pasta).glob("*.yaml")):
            d = _carregar(p)
            saida[str(typing.cast(dict[str, object], d["metadata"])["id"])] = d
    return saida


class _Skills:
    """As skills das fixtures, todas na versão 1, com o hash canônico do documento como trava."""

    def __init__(self) -> None:
        self.docs = _docs()

    def locked(self, skill_id: str, version: int) -> LockedSkill | None:
        d = self.docs.get(skill_id)
        if d is None or version != 1:
            return None
        return LockedSkill(SkillDocument.model_validate(d), content_hash(d))  # type: ignore[arg-type]


def _compilador() -> SkillPlanCompiler:
    return SkillPlanCompiler(APPS.get, _Skills())


def _doc(**spec: object) -> dict[str, object]:
    base: dict[str, object] = {"invocation": {"command_template": "abra as mensagens no instagram"},
                               "nodes": [{"id": "abrir_inbox", "capability": "OPEN_INBOX"}]}
    return {"apiVersion": "automation/v1alpha1", "kind": "Skill",
            "metadata": {"id": "ig.teste", "name": "Teste", "app": "instagram"}, "spec": {**base, **spec}}


# ================================================================== goldens
VALIDOS = sorted(p.stem for p in (FIXTURES / "validos").glob("*.yaml"))
INVALIDOS = sorted(p.stem for p in (FIXTURES / "invalidos").glob("*.yaml"))


@pytest.mark.parametrize("nome", VALIDOS)
def test_golden_valido_compila_para_o_plano_esperado(nome: str) -> None:
    doc = _carregar(FIXTURES / "validos" / f"{nome}.yaml")
    esperado_arq = FIXTURES / "validos" / f"{nome}.esperado.json"
    esperado = json.loads(esperado_arq.read_text(encoding="utf-8")) if esperado_arq.exists() else {"parametros": {}}
    r = _compilador().compilar(doc, version=1, parameters=esperado["parametros"])
    assert r.ok and r.executable is not None, [i.as_dict() for i in r.issues]
    plano = r.executable.plan
    atual = {"parametros": esperado["parametros"], "avisos": sorted({i.code.value for i in r.warnings}),
             "plano": plano.model_dump(mode="json")}
    if ATUALIZAR:
        esperado_arq.write_text(json.dumps(atual, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
                                newline="\n")
        esperado = atual
    assert atual == esperado, "o plano compilado mudou: ATUALIZAR_GOLDEN=1 e revise o diff do esperado.json"
    # §12.1, teste 2: provedor "skill" e ida e volta idêntica
    assert plano.planner.provider == "skill" and plano.planner.model == f"skill:{doc['metadata']['id']}@1"  # type: ignore[index]
    assert Plan.model_validate(plano.model_dump(mode="json")) == plano
    assert Plan.model_validate_json(plano.model_dump_json()) == plano
    # toda etapa tem origem, e a chave é o id do nó (identidade de receita)
    assert all(s.origin is not None and s.origin.node_id == s.key for s in plano.steps)
    # a compilação sem valores (draft → candidate) também passa
    assert _compilador().compilar(doc, version=1).ok


@pytest.mark.parametrize("nome", INVALIDOS)
def test_golden_invalido_da_o_erro_esperado(nome: str) -> None:
    esperado = json.loads((FIXTURES / "invalidos" / f"{nome}.esperado.json").read_text(encoding="utf-8"))
    contexto = esperado.get("contexto", {})
    r = _compilador().compilar(_carregar(FIXTURES / "invalidos" / f"{nome}.yaml"), version=1,
                               parameters=contexto.get("parametros"),
                               occupied_match_keys=contexto.get("comandos_ocupados", ()))
    assert not r.ok and r.executable is None
    assert sorted({i.code.value for i in r.errors}) == esperado["erros"], [i.as_dict() for i in r.issues]
    principal = next(i for i in r.errors if i.code.value == nome)
    assert principal.path == esperado["caminho"]
    assert principal.message and principal.as_dict()["severity"] == "error"


def test_todo_codigo_de_erro_tem_fixture_invalida() -> None:
    codigos = {c.value for c in Code if c.value.startswith("E_")}
    assert codigos - set(INVALIDOS) == set(), f"código sem fixture inválida: {sorted(codigos - set(INVALIDOS))}"
    assert set(INVALIDOS) - codigos == set(), f"fixture de código que não existe: {sorted(set(INVALIDOS) - codigos)}"


# ================================================================== §12.4 e §12.5, conferidos à mão
def test_abrir_conversa_sai_identica_ao_que_o_planejador_monta_pelo_catalogo() -> None:
    r = _compilador().compilar(_carregar(FIXTURES / "validos" / "ig.abrir_conversa.yaml"), version=1,
                               parameters={"username": "@ana.teste"})
    assert r.executable is not None
    plano = r.executable.plan
    assert [s.key for s in plano.steps] == ["abrir_inbox", "abrir_conversa"]
    inbox, conversa = plano.steps
    assert inbox.depends_on == [] and conversa.depends_on == ["abrir_inbox"]
    assert conversa.bindings == {"username": "{username}"} and conversa.title == "Abrir a conversa com {username}"
    assert conversa.max_attempts == 3 and conversa.timeout_s == 180 and plano.parameters == {"username": "@ana.teste"}
    # o mesmo `build_step` do planejador: só a origem é a mais
    catalogo = load_catalog(PACKAGE)
    assert catalogo is not None
    do_planejador = catalogo.build_step(CapabilityNode(key="abrir_conversa", capability="OPEN_THREAD",
                                                       depends_on=["abrir_inbox"],
                                                       bindings={"username": "{username}"}))
    assert conversa.model_copy(update={"origin": None}) == do_planejador
    assert conversa.origin is not None and conversa.origin.strategies == ["recipe", "ai_actor"]


def test_ler_conversa_expande_a_composicao_e_religa_as_dependencias() -> None:
    r = _compilador().compilar(_carregar(FIXTURES / "validos" / "ig.ler_conversa.yaml"), version=1,
                               parameters={"contato": "@ana.teste"})
    assert r.executable is not None and r.graph is not None
    passos = {s.key: s for s in r.executable.plan.steps}
    assert list(passos) == ["abrir_abrir_inbox", "abrir_abrir_conversa", "ler"]
    assert passos["abrir_abrir_inbox"].depends_on == []
    assert passos["abrir_abrir_conversa"].depends_on == ["abrir_abrir_inbox"]
    assert passos["ler"].depends_on == ["abrir_abrir_conversa"]
    assert passos["abrir_abrir_conversa"].bindings == {"username": "{contato}"}
    origens = {k: (s.origin.skill_id, s.origin.skill_version) for k, s in passos.items() if s.origin}
    assert origens == {"abrir_abrir_inbox": ("ig.abrir_conversa", 1), "abrir_abrir_conversa": ("ig.abrir_conversa", 1),
                       "ler": ("ig.ler_conversa", 1)}
    assert r.executable.plan.planner.model == "skill:ig.ler_conversa@1"
    trava = _Skills().docs["ig.abrir_conversa"]
    assert [(u.skill, u.version, u.content_hash) for u in r.graph.uses_lock] == [
        ("ig.abrir_conversa", 1, content_hash(trava))]  # type: ignore[arg-type]
    assert [o.node_id for o in r.graph.outputs] == ["ler"]
    # os recursos do filho são herdados
    assert {res.kind for res in r.graph.resources} == {"device.state", "app.installation", "app.session"}


def test_foreach_sobre_skill_composta_e_when_estatico() -> None:
    doc = _carregar(FIXTURES / "validos" / "ig.conversas_da_caixa.yaml")
    sem = _compilador().compilar(doc, version=1, parameters={})
    com = _compilador().compilar(doc, version=1, parameters={"incluir_arquivadas": "true"})
    rascunho = _compilador().compilar(doc, version=1)
    assert sem.executable and com.executable and rascunho.executable
    assert [s.key for s in sem.executable.plan.steps] == ["caixa", "listar", "cada_abrir_inbox", "cada_abrir_conversa"]
    assert [s.key for s in com.executable.plan.steps][-1] == "arquivadas"
    assert "arquivadas" in {s.key for s in rascunho.executable.plan.steps}      # sem valores: todos os ramos
    bloco = [s for s in sem.executable.plan.steps if s.for_each]
    assert [s.for_each for s in bloco] == ["listar", "listar"]
    assert bloco[0].depends_on == ["listar"] and bloco[1].bindings == {"username": "{item}"}
    assert sem.executable.plan.parameters == {"incluir_arquivadas": "false"}
    assert rascunho.executable.plan.parameters == {"incluir_arquivadas": "{incluir_arquivadas}"}


# ================================================================== invariante: documento é dado, nunca código
ARQUIVOS_DO_COMPILADOR = [BACKEND / "app" / "contracts" / "skills" / "v1alpha1.py",
                          *sorted((BACKEND / "app" / "modules" / "skills").rglob("*.py"))]
PROIBIDAS = {"eval", "exec", "compile", "__import__"}
MODULOS_PROIBIDOS = {"importlib", "pickle", "marshal"}
#: D15 diz "não importam planning.*"; o lowering PRECISA de `planning.capabilities.build_step` (§12.1). O que se
#: proíbe é a IA: nenhum módulo que chame provedor.
IA = ("app.planning.provider", "app.planning.routing", "app.planning.anthropic_provider",
      "app.planning.openai_provider", "app.planning.simulated_provider", "app.planning.prompts",
      "app.planning.parsing", "app.planning.training", "app.adapters.ai")


def _importados(arvore: ast.AST, arquivo: Path) -> set[str]:
    pacote = ".".join(arquivo.relative_to(BACKEND).with_suffix("").parts[:-1])
    nomes: set[str] = set()
    for n in ast.walk(arvore):
        if isinstance(n, ast.Import):
            nomes.update(a.name for a in n.names)
        elif isinstance(n, ast.ImportFrom):
            base = n.module or ""
            if n.level:
                partes = pacote.split(".")
                base = ".".join(partes[:len(partes) - (n.level - 1)] + ([n.module] if n.module else []))
            nomes.add(base)
            nomes.update(f"{base}.{a.name}" for a in n.names)
    return nomes


def test_compilador_nao_avalia_nem_carrega_codigo_por_ast() -> None:
    """D15 (§9): nada de eval/exec/compile/__import__, importlib, pickle, marshal, FunctionType, nem módulo de IA."""
    assert len(ARQUIVOS_DO_COMPILADOR) >= 5
    achados = []
    for arq in ARQUIVOS_DO_COMPILADOR:
        arvore = ast.parse(arq.read_text(encoding="utf-8"))
        for n in ast.walk(arvore):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in PROIBIDAS:
                achados.append(f"{arq.name}:{n.lineno} chama {n.func.id}")
            if isinstance(n, ast.Attribute) and n.attr == "FunctionType":
                achados.append(f"{arq.name}:{n.lineno} usa FunctionType")
            if isinstance(n, ast.Name) and n.id in PROIBIDAS:              # `f = eval` também conta
                achados.append(f"{arq.name}:{n.lineno} referencia {n.id}")
        for mod in _importados(arvore, arq):
            if mod.split(".")[0] in MODULOS_PROIBIDOS or mod.startswith(IA):
                achados.append(f"{arq.name} importa {mod}")
    assert not achados, achados


def test_codigo_python_num_texto_e_texto_inerte(monkeypatch: pytest.MonkeyPatch) -> None:
    """§12.1, teste 3: código num campo de texto passa como texto — e nada o executa."""
    codigo = "__import__('os').system('rm -rf /')"
    doc = _doc(nodes=[{"id": "abrir_inbox", "capability": "OPEN_INBOX"},
                      {"id": "olhar", "goal": {"title": codigo, "goal": f"exec({codigo!r})"},
                       "verification": {"postcondition": {"kind": "model_judged", "value": "eval('1+1')"}}}])

    def explode(*_a: object, **_k: object) -> typing.NoReturn:
        raise AssertionError("o compilador tentou executar código")

    # `__import__` fica de fora: o catálogo legado importa dentro de função (`load_catalog`), e o próprio pytest
    # importa ao formatar erro. O uso dele no compilador é barrado pelo teste de AST acima.
    for nome in ("eval", "exec", "compile"):
        monkeypatch.setattr(builtins, nome, explode)
    r = _compilador().compilar(doc, version=1)
    monkeypatch.undo()
    assert r.ok and r.executable is not None, [i.as_dict() for i in r.issues]
    olhar = r.executable.plan.steps[-1]
    assert olhar.title == codigo and olhar.goal == f"exec({codigo!r})" and olhar.postcondition.value == "eval('1+1')"


@pytest.mark.parametrize("campo,valor,codigo", [
    ("script", "import os", Code.E_SCHEMA),                                  # campo fora do esquema
    ("__class__", "x", Code.E_SCHEMA),
    ("when", "__import__('os').system('x')", Code.E_WHEN_UNSUPPORTED),      # when não é expressão Python
    ("when", "${parameters.x.__class__}", Code.E_WHEN_UNSUPPORTED),
])
def test_campo_de_codigo_no_no_e_recusado(campo: str, valor: str, codigo: Code) -> None:
    doc = _doc(nodes=[{"id": "abrir_inbox", "capability": "OPEN_INBOX", campo: valor}])
    r = _compilador().compilar(doc, version=1)
    assert not r.ok and codigo in {i.code for i in r.errors}, [i.as_dict() for i in r.issues]


@pytest.mark.parametrize("expressao,codigo", [
    ("${__import__('os')}", Code.E_EXPRESSION),
    ("${parameters.__class__}", Code.E_UNKNOWN_PARAMETER),                 # só um nome procurado, nunca atributo
    ("${lambda: 1}", Code.E_EXPRESSION),
    ("${item}", Code.E_EXPRESSION),                                          # fora de foreach
    ("${steps.abrir_inbox.output}", Code.E_OUTPUT_REF_UNSUPPORTED),
    ("${secrets.token}", Code.E_SECRET_INLINE),
    ("${parameters.x", Code.E_EXPRESSION),                                   # malformada
    ("{username}", Code.E_RAW_PLACEHOLDER),
])
def test_expressao_fora_da_gramatica_e_recusada(expressao: str, codigo: Code) -> None:
    r = _compilador().compilar(_doc(success_criteria=[f"caixa {expressao} aberta"]), version=1)
    assert [i.code for i in r.errors] == [codigo], [i.as_dict() for i in r.issues]
    assert r.errors[0].path == "/spec/success_criteria/0"


# ================================================================== determinismo
def _invertido(valor: object) -> object:
    """Mesmo documento, chaves na ordem inversa: o IR não pode depender da ordem de um mapa."""
    if isinstance(valor, Mapping):
        return {k: _invertido(v) for k, v in reversed(list(valor.items()))}
    if isinstance(valor, list):
        return [_invertido(v) for v in valor]
    return valor


@pytest.mark.parametrize("nome", VALIDOS)
def test_mesma_entrada_mesmo_ir_e_mesmo_hash(nome: str) -> None:
    doc = _carregar(FIXTURES / "validos" / f"{nome}.yaml")
    parametros = json.loads((FIXTURES / "validos" / f"{nome}.esperado.json").read_text(encoding="utf-8"))["parametros"]
    a = _compilador().compilar(doc, version=1, parameters=parametros)
    b = _compilador().compilar(_invertido(doc), version=1, parameters=dict(reversed(list(parametros.items()))))
    assert a.graph is not None and b.graph is not None and a.executable and b.executable
    assert a.graph == b.graph and a.graph.content_hash() == b.graph.content_hash()
    assert a.executable.plan_hash == b.executable.plan_hash == plan_hash(b.executable.plan)
    assert a.executable.plan.model_dump_json() == b.executable.plan.model_dump_json()
    # versão, valor de parâmetro e conteúdo mudam o hash
    c = _compilador().compilar(doc, version=2, parameters=parametros)
    assert c.graph is not None and c.graph.content_hash() != a.graph.content_hash()


def test_hash_ignora_grafia_do_texto_mas_nao_o_conteudo() -> None:
    comp = SkillCompiler(CatalogCapabilityRegistry(APPS.get))
    um = comp.compilar(_doc(success_criteria=["a ${parameters.x} b"], parameters=[{"name": "x", "example": "1",
                                                                                   "required": False}]), version=1)
    dois = comp.compilar(_doc(success_criteria=["a ${ parameters.x } b"], parameters=[{"name": "x", "example": "1",
                                                                                      "required": False}]), version=1)
    tres = comp.compilar(_doc(success_criteria=["a ${parameters.x} c"], parameters=[{"name": "x", "example": "1",
                                                                                    "required": False}]), version=1)
    assert um.graph and dois.graph and tres.graph
    assert um.graph.content_hash() == dois.graph.content_hash() != tres.graph.content_hash()


# ================================================================== regras pontuais
def test_parametros_ligados_na_execucao() -> None:
    doc = _carregar(FIXTURES / "validos" / "ig.abrir_conversa.yaml")
    falta = _compilador().compilar(doc, version=1, parameters={})
    assert [(i.code, i.path) for i in falta.errors] == [(Code.E_MISSING_ARGUMENT, "/spec/parameters/0")]
    sobra = _compilador().compilar(doc, version=1, parameters={"username": "@a", "outro": "x"})
    assert [i.code for i in sobra.errors] == [Code.E_UNKNOWN_PARAMETER]


def test_copias_do_foreach_que_colidem_depois_de_truncar() -> None:
    """`_copy_key` corta a chave em 41 - len("_iN"): duas chaves-modelo com o mesmo começo viram a mesma cópia."""
    from app.taskqueue.foreach import _copy_key

    for chave, n in (("a" * 41, 1), ("abc", 12), ("x" * 38 + "yz", 150)):
        assert compilador._copy_key(chave, n) == _copy_key(chave, n)
    comum = "abrir_conversa_com_um_nome_bem_comprido"                       # 39 caracteres
    doc = _doc(nodes=[{"id": "abrir_inbox", "capability": "OPEN_INBOX"},
                      {"id": "listar", "capability": "COLLECT_THREADS"},
                      {"id": comum + "_a", "capability": "OPEN_THREAD", "foreach": "${steps.listar.output}",
                       "with": {"username": "${item}"}},
                      {"id": comum + "_b", "capability": "OPEN_THREAD", "foreach": "${steps.listar.output}",
                       "with": {"username": "${item}"}}])
    r = _compilador().compilar(doc, version=1)
    assert [i.code for i in r.errors] == [Code.E_NODE_ID_COLLISION], [i.as_dict() for i in r.issues]


def test_nome_de_parametro_com_cara_de_credencial() -> None:
    """Mesmas palavras de `security/redaction.py`, mas por pedaço do nome: `opiniao` tem "pin" e não é segredo."""
    from app.security.redaction import _PALAVRAS_DE_SEGREDO

    simples = [p for p in _PALAVRAS_DE_SEGREDO.split("|") if re.fullmatch(r"[a-z]+", p)]
    compostas = [p.replace("[_-]?", "_").replace("(?:", "").replace(")?", "") for p in _PALAVRAS_DE_SEGREDO.split("|")
                 if "[_-]?" in p]
    assert simples and compostas
    for palavra in simples + compostas + ["senha_do_portal", "api_key", "otp", "codigo_2fa"]:
        assert compilador._SECRET_NAME.search(palavra), palavra
    for inocente in ("opiniao", "pinned", "username", "tokenizar", "contato"):
        assert not compilador._SECRET_NAME.search(inocente), inocente


def test_match_key_e_a_normalizacao_dos_fluxos() -> None:
    from app.taskqueue.flows import _norm

    for texto in ("Abra   a conversa com {username}", "ＡＢＲＡ as mensagens", "  x\ty  "):
        assert match_key(texto) == _norm(texto)


def test_plano_legado_continua_byte_a_byte_igual() -> None:
    """`PlanStep.origin` é aditivo: plano que não veio de skill não ganha a chave (runs.plan, plan_versions)."""
    passo = PlanStep(key="abrir", title="t", goal="g",
                     postcondition=Postcondition(kind="model_judged", value="v", description="d"))
    assert "origin" not in passo.model_dump() and '"origin"' not in passo.model_dump_json()
    assert PlanStep.model_validate(passo.model_dump(mode="json")).origin is None


def test_registro_de_capabilities_cumpre_a_porta_do_compilador() -> None:
    membros = [n for n, v in vars(CapabilityLookup).items() if inspect.isfunction(v) and not n.startswith("_")]
    assert sorted(membros) == ["app_known", "definition", "has_catalog", "offered"]
    for nome in membros:
        porta, impl = getattr(CapabilityLookup, nome), getattr(CatalogCapabilityRegistry, nome)
        assert list(inspect.signature(porta).parameters) == list(inspect.signature(impl).parameters), nome
        assert typing.get_type_hints(porta) == typing.get_type_hints(impl), nome


def test_erro_nunca_vira_excecao() -> None:
    comp = _compilador()
    for lixo in (None, [], "texto", 42, {"apiVersion": "automation/v1alpha1"}, {"spec": {"nodes": "x"}}):
        r = comp.compilar(typing.cast(dict[str, object], lixo), version=1)
        assert not r.ok and r.errors and all(i.code.startswith("E_") for i in r.errors)
    assert pointer("spec", "a/b", "c~d", 0) == "/spec/a~1b/c~0d/0"
