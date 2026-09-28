"""Fase J: o descompilador `Plan` → `automation/v1alpha1`, conferido pela ida e volta no compilador REAL.

Os fluxos são do formato de produção, gravados pelo código que os grava em produção:
- `FlowStore.learn_from_run` sobre o plano que o planejador por catálogo monta (`compose` → `build_step`, o caminho
  de `catalog_plan_from_json`, do `AtorDoInstagram` e do planejador simulado) — com o alvo em `parameters` e a etapa
  apontando `{username}`, como o planejador faz;
- `FlowStore.learn_from_plan`, o fluxo que o treino salva (capability com `{contato}` + etapa livre);
- `fake_skills.PLANO_CURTIR`, o fluxo de teste da fase D (etapa livre, parâmetro fixo).

A ida e volta: o plano que o fluxo dá a um comando (`FlowStore.match`, o legado) e o plano que o compilador dá ao
documento descompilado com os valores do MESMO comando são iguais etapa por etapa (fora a `origin`, que só o plano
compilado tem) — mesmas chaves, capabilities, pós-condições, argumentos, guardas — e com a mesma identidade de receita
(`step_template_hash(para_hash(...))`). O que a DSL não representa sai como erro ou aviso explícito, nunca calado.

Nível de prova: `simulated` (banco de teste, catálogo em código). Nenhum aparelho, nenhuma IA.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from app.db import Database
from app.models import Plan, PlannerInfo, PlanStep, Postcondition
from app.modules.skills.domain.matching import extract_parameters
from app.modules.skills.domain.refs import SkillRef
from app.modules.skills.domain.versions import Provenance, SourceKind
from app.modules.skills.infrastructure.decompiler import (DecompileCode, IssueOrigin, PlanDecompiler,
                                                          plan_to_document, suggested_skill_id)
from app.modules.skills.infrastructure.legacy_flows import LegacyFlowAdapter
from app.modules.skills.infrastructure.lowering import SkillPlanCompiler
from app.planning.capabilities import CapabilityNode, compose, load_catalog
from app.planning.provider import AppContext, PlanRequest
from app.planning.simulated_provider import QA_PACKAGE, SimulatedProvider
from app.taskqueue.flows import FlowStore
from app.taskqueue.recipes import para_hash, step_template_hash

from .fake_skills import banco, fluxo

PACKAGE = "com.instagram.android"
SKILL = "ig.convertida"


@pytest.fixture
def db(tmp_path: Path) -> Any:
    d = banco(tmp_path)
    d.execute("INSERT INTO apps(id, name, package, builtin) VALUES ('instagram','Instagram',?,0)", (PACKAGE,))
    d.execute("INSERT INTO apps(id, name, package, builtin) VALUES ('qa','QA Messenger',?,0)", (QA_PACKAGE,))
    yield d
    d.close()


def pacote(db: Database) -> Any:
    return lambda app_id: db.scalar("SELECT package FROM apps WHERE id=?", (app_id,))


def decompilador(db: Database) -> PlanDecompiler:
    return PlanDecompiler(SkillPlanCompiler(pacote(db)))


# ------------------------------------------------------------------ fluxos no formato de produção
def plano_por_catalogo(nos: list[CapabilityNode], parametros: dict[str, str], resumo: str) -> Plan:
    """O plano do planejador por catálogo: etapas do `build_step`, alvo em `parameters` (`catalog_plan_from_json`)."""
    catalogo = load_catalog(PACKAGE)
    assert catalogo is not None
    etapas, faltando = compose(catalogo, nos)
    assert not faltando, faltando
    return Plan(summary=resumo, app_id="instagram", app_package=PACKAGE, parameters=parametros, steps=etapas,
                planner=PlannerInfo(provider="anthropic", model="claude", simulated=False))


def aprendido(db: Database, plano: Plan, comando: str, run_id: str = "r-origem") -> str:
    """O fluxo que `_learn_flow` grava quando a execução termina toda comprovada."""
    flow_id = FlowStore(db).learn_from_run({"id": run_id, "plan": plano.model_dump_json(), "flow_id": None,
                                            "skill_id": None, "command": comando})
    assert flow_id is not None
    return flow_id


def aprendido_antes_da_correcao(db: Database, plano: Plan, comando: str, run_id: str = "r-origem") -> str:
    """O fluxo como `learn_from_run` o gravava até 28/09 (antes de r-20260928165254-e31953): textos em modelo, mas
    argumentos, guardas da linha e critérios com o VALOR da execução-fonte, copiados do plano. Fluxos assim seguem no
    banco de quem os aprendeu, e é deles que o descompilador e a conversão precisam dizer a causa."""
    flow_id = aprendido(db, plano, comando, run_id)
    congelado = Plan.model_validate_json(str(db.scalar("SELECT plan FROM flows WHERE id=?", (flow_id,))))
    for etapa, original in zip(congelado.steps, plano.steps, strict=True):
        etapa.bindings, etapa.band_guard = dict(original.bindings), list(original.band_guard)
    congelado.success_criteria = list(plano.success_criteria)
    db.execute("UPDATE flows SET plan=? WHERE id=?", (congelado.model_dump_json(), flow_id))
    return flow_id


def abrir_conversa(db: Database) -> tuple[str, str]:
    plano = plano_por_catalogo([CapabilityNode(key="abrir_inbox", capability="OPEN_INBOX"),
                                CapabilityNode(key="abrir_conversa", capability="OPEN_THREAD",
                                               depends_on=["abrir_inbox"], bindings={"username": "{username}"})],
                               {"username": "@ana"}, "Abrir a conversa com @ana")
    return aprendido(db, plano, "abra a conversa com @ana no instagram"), "abra a conversa com @bia.2 no instagram"


def enviar_mensagem(db: Database) -> tuple[str, str]:
    """Efeito externo: commit_selector, commit_guard sem o `{content}` (o texto nasce por perfil), uma tentativa."""
    plano = plano_por_catalogo(
        [CapabilityNode(key="abrir_inbox", capability="OPEN_INBOX"),
         CapabilityNode(key="abrir_conversa", capability="OPEN_THREAD", depends_on=["abrir_inbox"],
                        bindings={"username": "{username}"}),
         CapabilityNode(key="enviar", capability="SEND_MESSAGE", depends_on=["abrir_conversa"],
                        bindings={"username": "{username}", "content_brief": "{assunto}"})],
        {"username": "@ana", "assunto": "desejar bom dia"}, "Mandar mensagem para @ana")
    return (aprendido(db, plano, "mande para @ana uma mensagem para desejar bom dia"),
            "mande para @bia uma mensagem para marcar a reunião")


def seguir(db: Database) -> tuple[str, str]:
    plano = plano_por_catalogo([CapabilityNode(key="abrir_perfil", capability="OPEN_PROFILE",
                                               bindings={"username": "{perfil}"}),
                                CapabilityNode(key="seguir", capability="FOLLOW", depends_on=["abrir_perfil"],
                                               bindings={"username": "{perfil}"})],
                               {"perfil": "@nasa"}, "Seguir @nasa")
    return aprendido(db, plano, "siga @nasa no instagram"), "siga @esa no instagram"


def cada_conversa(db: Database) -> tuple[str, str]:
    """Bloco `for_each`: a coleta do catálogo e a etapa com `{item}`."""
    plano = plano_por_catalogo([CapabilityNode(key="caixa", capability="OPEN_INBOX"),
                                CapabilityNode(key="listar", capability="COLLECT_THREADS", depends_on=["caixa"]),
                                CapabilityNode(key="abrir_cada", capability="OPEN_THREAD", depends_on=["listar"],
                                               bindings={"username": "{item}"}, for_each="listar")],
                               {}, "Abrir cada conversa da caixa")
    comando = "abra cada conversa da caixa de entrada"
    return aprendido(db, plano, comando), comando


def treino(db: Database) -> tuple[str, str]:
    """O que `/api/training/{sid}/save` grava: capability com `{contato}` e etapa livre, parâmetros-modelo."""
    catalogo = load_catalog(PACKAGE)
    assert catalogo is not None
    abrir = catalogo.build_step(CapabilityNode(key="abrir", capability="OPEN_THREAD", bindings={"username": "{contato}"}))
    ler = PlanStep(key="conferir", title="Conferir a última mensagem", goal="Ler a última mensagem de {contato}.",
                   depends_on=["abrir"], postcondition=Postcondition(kind="model_judged", value="última mensagem lida",
                                                                     description="A última mensagem está visível."))
    plano = Plan(summary="Conferir a conversa com {contato}", app_id="instagram", app_package=PACKAGE,
                 parameters={"contato": "{contato}"}, steps=[abrir, ler],
                 planner=PlannerInfo(provider="treinamento", model="treinamento:t-1", simulated=False))
    return (FlowStore(db).learn_from_plan(plano, "confira a conversa com {contato}", source="training:t-1"),
            "confira a conversa com @ana")


def curtir(db: Database) -> tuple[str, str]:
    fluxo(db, "curtir", "curtir o post de {perfil}")
    return "curtir", "curtir o post de @nasa"


FLUXOS = {"abrir_conversa": abrir_conversa, "enviar_mensagem": enviar_mensagem, "seguir": seguir,
          "cada_conversa": cada_conversa, "treino": treino, "curtir": curtir}


def versao(db: Database, flow_id: str) -> Any:
    return LegacyFlowAdapter(db).get(SkillRef.legacy(flow_id))


def identidade(plano: Plan) -> list[str]:
    """A chave de receita de cada etapa, como `_insert_steps` a calcula (`para_hash` com os valores da execução)."""
    return [step_template_hash(para_hash(s, plano.parameters)) for s in plano.steps]


# ================================================================== ida e volta
@pytest.mark.parametrize("nome", sorted(FLUXOS))
def test_o_documento_descompilado_compila_de_volta_no_mesmo_plano(db: Database, nome: str) -> None:
    flow_id, comando = FLUXOS[nome](db)
    v = versao(db, flow_id)
    d = decompilador(db).decompile_version(v, skill_id=SKILL, app_id="instagram")
    assert d.ok, [i.text() for i in d.errors]
    # nenhuma etapa deriva (o `curtir` de teste não diz o pacote: esse aviso é do plano, não de etapa)
    assert not [i for i in d.warnings if i.code == DecompileCode.W_ROUNDTRIP and i.path.startswith("/steps")], \
        [i.text() for i in d.warnings]
    assert d.document is not None
    # nó = etapa, pela chave (a identidade de receita começa aqui)
    assert [n["id"] for n in d.document["spec"]["nodes"]] == [s.key for s in Plan.model_validate(v.document()["plan"]).steps]

    casado = FlowStore(db).match(comando)
    assert casado is not None, comando
    legado = casado[1]
    valores = extract_parameters(v.command_template, comando)
    assert valores is not None
    r = SkillPlanCompiler(pacote(db)).compilar(d.document, version=2, parameters=valores)
    assert r.ok and r.executable is not None, [i.as_dict() for i in r.errors]
    novo = r.executable.plan

    assert [s.model_dump(exclude={"origin"}) for s in novo.steps] == [s.model_dump(exclude={"origin"})
                                                                         for s in legado.steps]
    assert [(s.key, s.capability, s.postcondition) for s in novo.steps] == [
        (s.key, s.capability, s.postcondition) for s in legado.steps]
    assert novo.parameters == legado.parameters and novo.app_id == legado.app_id
    assert identidade(novo) == identidade(legado)
    assert all(s.origin is not None and s.origin.node_id == s.key for s in novo.steps)


def test_o_documento_descompilado_e_o_esperado_para_o_fluxo_de_abrir_conversa(db: Database) -> None:
    """Golden do formato: capability nomeada, argumento em `${parameters.x}`, tempo e tentativas explícitos,
    `depends_on` sempre explícito, parâmetro `string` (a extração do fluxo, sem normalização de tipo)."""
    flow_id, _ = abrir_conversa(db)
    d = decompilador(db).decompile_version(versao(db, flow_id), skill_id=SKILL, app_id="instagram",
                                           description="Convertida do fluxo")
    assert d.document == {
        "apiVersion": "automation/v1alpha1", "kind": "Skill",
        "metadata": {"id": SKILL, "name": "Abrir a conversa com {username}", "app": "instagram",
                     "description": "Convertida do fluxo"},
        "spec": {
            "invocation": {"command_template": "abra a conversa com {username} no instagram"},
            "parameters": [{"name": "username", "type": "string", "required": True}],
            "requires": {"apps": ["instagram"]},
            "nodes": [
                {"id": "abrir_inbox", "depends_on": [], "timeout_s": 180, "retries": 2, "capability": "OPEN_INBOX"},
                {"id": "abrir_conversa", "depends_on": ["abrir_inbox"], "timeout_s": 180, "retries": 2,
                 "with": {"username": "${parameters.username}"}, "capability": "OPEN_THREAD"},
            ],
            "success_criteria": [],
        },
    }
    # só os avisos do compilador (sem exemplo, sem caso de validação): nada da ida e volta
    assert {i.code for i in d.warnings} == {"W_PARAMETER_NO_EXAMPLE", "W_NO_VALIDATION_CASE"}
    assert all(i.origin is IssueOrigin.DOCUMENT for i in d.warnings)


def test_parametro_fixo_do_plano_vira_padrao_e_o_plano_ligado_sai_igual(db: Database) -> None:
    flow_id, _ = curtir(db)
    d = decompilador(db).decompile_version(versao(db, flow_id), skill_id=SKILL, app_id="instagram")
    assert d.ok and d.document is not None
    # (a ordem é a do conteúdo canônico da versão, alfabética; não importa ao compilador)
    assert {p["name"]: p for p in d.document["spec"]["parameters"]} == {
        "perfil": {"name": "perfil", "type": "string", "required": True},
        "fixo": {"name": "fixo", "type": "string", "required": False, "default": "x"}}
    [no] = d.document["spec"]["nodes"]
    assert no["goal"] == {"title": "Abrir o perfil de ${parameters.perfil}", "goal": "abrir ${parameters.perfil}"}
    assert no["verification"] == {"postcondition": {"kind": "app_foreground", "value": "instagram",
                                                    "description": "app aberto"}}
    assert no["side_effect"] is False
    # o fluxo de teste não dizia o pacote; o compilado diz: aviso, não erro (o app é o mesmo)
    assert [i.path for i in d.warnings if i.code == DecompileCode.W_ROUNDTRIP] == ["/app_package"]


# ================================================================== o que não se representa: explícito
def test_variavel_do_runtime_no_texto_e_erro_explicito(db: Database) -> None:
    """O fluxo do QA Messenger (planejador simulado) confere `{account_label}` na tela: a v1alpha1 não tem forma
    para variável do runtime num texto. Recusa com o caminho, e o compilador também aponta o texto cru."""
    req = PlanRequest(command='Abra o QA Messenger, entre na conversa com QA-001 e envie "oi"', run_id="r-qa",
                      instances=[], apps=[AppContext("qa", "QA Messenger", QA_PACKAGE, None, None, None)])
    plano, _ = asyncio.run(SimulatedProvider().plan(req))
    assert not plano.missing
    flow_id = aprendido(db, plano, req.command)
    d = decompilador(db).decompile_version(versao(db, flow_id), skill_id="qa.enviar", app_id="qa")
    assert not d.ok
    runtime = [i for i in d.errors if i.code == DecompileCode.E_RUNTIME_VARIABLE]
    assert runtime and all("{account_label}" in i.message for i in runtime)
    assert {i.path for i in runtime} >= {"/steps/1/goal", "/steps/1/postcondition/value"}
    assert any(i.code == "E_RAW_PLACEHOLDER" and i.origin is IssueOrigin.DOCUMENT for i in d.errors)


def test_argumento_literal_com_texto_em_modelo_e_recusado_com_a_causa(db: Database) -> None:
    """O planejador de verdade pode pôr o VALOR no argumento (`username: "@ana"`) e guardar o alvo em `parameters`.
    Até 28/09, `learn_from_run` trocava o valor por `{username}` nos textos, não nos argumentos: o plano congelado
    ficava com a pós-condição em modelo e o argumento fixo (hoje ele troca nos dois; o fluxo antigo continua no
    banco). Recompilar pelo catálogo poria "@ana" na pós-condição — outra identidade de etapa. Recusa, dizendo a
    causa."""
    plano = plano_por_catalogo([CapabilityNode(key="abrir_inbox", capability="OPEN_INBOX"),
                                CapabilityNode(key="abrir_conversa", capability="OPEN_THREAD",
                                               depends_on=["abrir_inbox"], bindings={"username": "@ana"})],
                               {"username": "@ana"}, "Abrir a conversa com @ana")
    flow_id = aprendido_antes_da_correcao(db, plano, "abra a conversa com @ana no instagram")
    congelado = Plan.model_validate(versao(db, flow_id).document()["plan"])
    assert congelado.steps[1].bindings == {"username": "@ana"}
    assert congelado.steps[1].postcondition.value == "conversa com {username} aberta"
    d = decompilador(db).decompile_version(versao(db, flow_id), skill_id=SKILL, app_id="instagram")
    assert not d.ok
    [pos] = [i for i in d.errors if i.path == "/steps/1/postcondition/value"]
    assert pos.code == DecompileCode.E_ROUNDTRIP and "guarda um valor fixo" in pos.message


def test_deriva_do_catalogo_no_texto_e_aviso_e_na_identidade_e_erro(db: Database) -> None:
    flow_id, _ = abrir_conversa(db)
    v = versao(db, flow_id)
    plano = Plan.model_validate(v.document()["plan"])
    comando = v.command_template
    # título reescrito desde o congelamento: a v2 usa o de hoje, avisando
    antigo = plano.model_copy(deep=True)
    antigo.steps[1].title = "Abrir a DM de {username}"
    d = decompilador(db).decompile(antigo, skill_id=SKILL, command_template=comando, required_apps=["instagram"])
    assert d.ok and [i.path for i in d.warnings if i.code == DecompileCode.W_ROUNDTRIP] == ["/steps/1/title"]
    # pós-condição congelada diferente da do catálogo: mudaria a identidade de receita — erro
    antigo.steps[1].postcondition.value = "DM com {username} aberta"
    d = decompilador(db).decompile(antigo, skill_id=SKILL, command_template=comando, required_apps=["instagram"])
    assert not d.ok and [i.path for i in d.errors] == ["/steps/1/postcondition/value"]


def test_coleta_livre_efeito_sem_capability_e_parametro_fora_do_comando(db: Database) -> None:
    passo = PlanStep(key="listar", title="Listar", goal="Listar as conversas.",
                     postcondition=Postcondition(kind="items_collected", value="conversas", description="lidas"))
    plano = Plan(summary="Listar", app_id="qa", steps=[passo], planner=PlannerInfo(provider="x", model="y",
                                                                                   simulated=True))
    d = decompilador(db).decompile(plano, skill_id="qa.listar", command_template="liste as conversas")
    assert any(i.code == DecompileCode.E_UNREPRESENTABLE and i.path == "/steps/0/postcondition/kind"
               for i in d.errors)

    # efeito externo em app COM catálogo precisa ser a capability: o compilador recusa, e o erro chega como é
    envio = PlanStep(key="enviar", title="Enviar", goal="Tocar em Enviar.", side_effect=True, max_attempts=1,
                     postcondition=Postcondition(kind="model_judged", value="enviada", description="enviada"))
    plano = Plan(summary="Enviar", app_id="instagram", steps=[envio],
                 planner=PlannerInfo(provider="x", model="y", simulated=True))
    d = decompilador(db).decompile(plano, skill_id=SKILL, command_template="envie")
    assert [(i.code, i.origin) for i in d.errors] == [("E_CAPABILITY_REQUIRED", IssueOrigin.DOCUMENT)]

    # parâmetro-modelo que o comando não traz: o fluxo nunca casaria
    plano = Plan(summary="Enviar", app_id="qa", parameters={"quem": "{quem}"}, steps=[envio],
                 planner=PlannerInfo(provider="x", model="y", simulated=True))
    d = decompilador(db).decompile(plano, skill_id="qa.enviar", command_template="envie")
    assert any(i.code == DecompileCode.E_UNREPRESENTABLE and i.path == "/parameters/quem" for i in d.errors)


def test_tentativas_com_efeito_viram_uma_e_isso_e_avisado(db: Database) -> None:
    """P5: etapa com efeito externo tem uma tentativa só. Um plano congelado com outra coisa converte, avisando."""
    envio = PlanStep(key="enviar", title="Enviar para {quem}", goal="Tocar em Enviar para {quem}.", side_effect=True,
                     max_attempts=2, commit_guard=["{quem}"],
                     postcondition=Postcondition(kind="text_visible", value="Enviada", description="Aparece Enviada"))
    plano = Plan(summary="Enviar", app_id="qa", app_package=QA_PACKAGE, parameters={"quem": "{quem}"}, steps=[envio],
                 planner=PlannerInfo(provider="x", model="y", simulated=True))
    d = decompilador(db).decompile(plano, skill_id="qa.enviar", command_template="envie para {quem}")
    assert d.ok, [i.text() for i in d.errors]
    assert [i.path for i in d.warnings if i.code == DecompileCode.W_ROUNDTRIP] == ["/steps/0/max_attempts"]
    assert d.document is not None
    [no] = d.document["spec"]["nodes"]
    assert "retries" not in no and no["commit_guard"] == ["${parameters.quem}"] and no["side_effect"] is True


def test_documento_da_dsl_nao_se_descompila(db: Database) -> None:
    from .fake_skills import documento, repositorio

    repo, _ = repositorio(db)
    v = repo.create_draft("ig.abc", documento("ig.abc", "abc {x}"), source=Provenance(SourceKind.MANUAL), by="p")
    d = decompilador(db).decompile_version(v, skill_id="ig.abc")
    assert d.document is None and [i.code for i in d.errors] == [DecompileCode.E_UNREPRESENTABLE]


def test_sem_app_nao_ha_documento() -> None:
    plano = Plan(summary="x", steps=[], planner=PlannerInfo(provider="x", model="y", simulated=True))
    documento, problemas = plan_to_document(plano, skill_id=SKILL, command_template="x")
    assert documento is None and [p.path for p in problemas] == ["/app_id"]


@pytest.mark.parametrize(("flow_id", "app_id", "esperado"), [
    ("curtir-o-post-de-perfil", "instagram", "instagram.curtir-o-post-de-perfil"),
    ("3-passos", "qa-messenger", "qa-messenger.3-passos"),
    ("fluxo", None, "fluxo.fluxo"),
    ("x" * 80, "instagram", ("instagram." + "x" * 80)[:64]),
    ("Ação!", "IG", "ig.a-o"),
])
def test_id_sugerido_e_valido_e_estavel(flow_id: str, app_id: str | None, esperado: str) -> None:
    assert suggested_skill_id(flow_id, app_id) == esperado
