"""Fase I, com banco: a RESOLVE pelo `IntentResolver` sobre habilidades publicadas de verdade e fluxos legados.

- tabela golden de frases em português (com e sem @, link de perfil, espaços/caixa/acentos, frases que NÃO casam);
- paridade com o `FlowStore.match` legado (mesmo fluxo, mesmos valores, mesmo `Plan`), inclusive a ordem por uso;
- escopo por perfil e por grupo, também no empate;
- empate, parâmetro vazio e tipo inválido viram pergunta, nunca plano.

Nível de prova: `simulated` (banco de teste pela fábrica da suíte, catálogo em código). Nenhum aparelho, nenhuma IA.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from app.db import Database
from app.modules.skills.domain.intent import QuestionReason, ResolutionMethod, ResolutionStatus
from app.modules.skills.domain.lifecycle import ContentTampered, SkillState
from app.modules.skills.domain.refs import SkillRef
from app.modules.skills.domain.versions import Provenance, SourceKind
from app.taskqueue.flows import FlowStore

from .fake_skills import PLANO_CURTIR, banco, fluxo, perfil, sem_gatilho_046
from .test_habilidades_na_execucao import ABRIR, LER, PESSOA, Mundo, carregar


@pytest.fixture
def db(tmp_path: Path) -> Any:
    d = banco(tmp_path)
    yield d
    d.close()


def publicar_doc(m: Mundo, doc: dict[str, Any]) -> SkillRef:
    """draft → candidate → validated (manual, com motivo: P4) → published."""
    v = m.repo.create_draft(doc["metadata"]["id"], doc, source=Provenance(SourceKind.MANUAL), by=PESSOA)
    m.repo.transition(v.ref, SkillState.CANDIDATE, by=PESSOA, reason="submetida")
    m.repo.transition(v.ref, SkillState.VALIDATED, by=PESSOA, reason="teste da fase I", manual=True)
    m.repo.transition(v.ref, SkillState.PUBLISHED, by=PESSOA, reason="publicar")
    return v.ref


def doc_abrir(skill_id: str, modelo: str, parametro: str, *, tipo: str = "handle") -> dict[str, Any]:
    """Uma variação de `ig.abrir_conversa` com outro comando/parâmetro: compila, e empata quando o texto fixo é igual."""
    d = copy.deepcopy(carregar(ABRIR))
    d["metadata"]["id"] = skill_id
    d["metadata"]["name"] = f"Variação {skill_id}"
    d["spec"]["invocation"] = {"command_template": modelo, "examples": []}
    d["spec"]["parameters"] = [{"name": parametro, "type": tipo, "required": True,
                                "example": "3" if tipo == "integer" else "@ana"}]
    d["spec"]["nodes"][1]["with"] = {"username": "${parameters." + parametro + "}"}
    d["spec"]["success_criteria"] = ["A conversa com ${parameters." + parametro + "} está aberta."]
    d["spec"]["validation"] = {"cases": []}
    return d


#: Os tipos inteiro, handle, enum e booleano (com padrão) numa habilidade só, com um nó `goal` sem efeito.
DOC_VER_POSTS: dict[str, Any] = {
    "apiVersion": "automation/v1alpha1", "kind": "Skill",
    "metadata": {"id": "ig.ver_posts", "name": "Ver os últimos posts de um perfil", "app": "instagram"},
    "spec": {
        "invocation": {"command_template": "veja os últimos {quantos} posts de {perfil} na aba {aba}"},
        "parameters": [{"name": "quantos", "type": "integer", "example": "3"},
                       {"name": "perfil", "type": "handle", "example": "@ana"},
                       {"name": "aba", "type": "enum", "values": ["feed", "reels"], "example": "feed"},
                       {"name": "detalhar", "type": "boolean", "required": False, "default": False, "example": "sim"}],
        "nodes": [
            {"id": "ver", "depends_on": [], "side_effect": False,
             "goal": {"title": "Ver os posts de ${parameters.perfil}",
                      "goal": "Abrir ${parameters.perfil} na aba ${parameters.aba} e ver os últimos "
                              "${parameters.quantos} posts."},
             "verification": {"postcondition": {"kind": "model_judged", "value": "posts à vista"}}},
            {"id": "detalhe", "side_effect": False, "when": "${parameters.detalhar} == 'true'",
             "goal": {"title": "Abrir o primeiro post", "goal": "Abrir o primeiro post da lista."},
             "verification": {"postcondition": {"kind": "model_judged", "value": "post aberto"}}}]}}


# ==================================================================== tabela golden de frases
#: frase → (status, habilidade, parâmetros que vão ao compilador) — ou o motivo da pergunta.
GOLDEN_FRASES: list[tuple[str, str, str | None, dict[str, str] | QuestionReason | None]] = [
    ("abra a conversa com @ana no instagram", "resolved", "ig.abrir_conversa@1", {"username": "@ana"}),
    ("abra a conversa com ana no instagram", "resolved", "ig.abrir_conversa@1", {"username": "@ana"}),
    ("Abra   a CONVERSA com @Ana.Teste no Instagram ", "resolved", "ig.abrir_conversa@1", {"username": "@ana.teste"}),
    ("abra a conversa com “@ana” no instagram", "resolved", "ig.abrir_conversa@1", {"username": "@ana"}),
    ("abra a conversa com https://www.instagram.com/ana.teste/ no instagram", "resolved", "ig.abrir_conversa@1",
     {"username": "@ana.teste"}),
    ("abra a conversa com instagram.com/bia_2?igsh=xyz no instagram", "resolved", "ig.abrir_conversa@1",
     {"username": "@bia_2"}),
    ("leia a conversa com @Ana no instagram", "resolved", "ig.ler_conversa@1", {"contato": "@ana"}),
    ("veja os últimos três posts de https://www.instagram.com/Ana/ na aba REELS", "resolved", "ig.ver_posts@1",
     {"quantos": "3", "perfil": "@ana", "aba": "reels"}),
    ("VEJA os últimos 10 posts de @nasa na aba Feed", "resolved", "ig.ver_posts@1",
     {"quantos": "10", "perfil": "@nasa", "aba": "feed"}),
    # tipo inválido: pergunta sobre ESTA habilidade, com o que veio
    ("abra a conversa com a Ana no instagram", "needs_input", "ig.abrir_conversa@1", QuestionReason.INVALID_PARAMETER),
    ("abra a conversa com @joão no instagram", "needs_input", "ig.abrir_conversa@1", QuestionReason.INVALID_PARAMETER),
    ("abra a conversa com https://www.instagram.com/p/Cxyz/ no instagram", "needs_input", "ig.abrir_conversa@1",
     QuestionReason.INVALID_PARAMETER),
    ("veja os últimos muitos posts de @nasa na aba feed", "needs_input", "ig.ver_posts@1",
     QuestionReason.INVALID_PARAMETER),
    ("veja os últimos 3 posts de @nasa na aba stories", "needs_input", "ig.ver_posts@1",
     QuestionReason.INVALID_PARAMETER),
    # buraco vazio: pergunta o que falta, em vez de mandar ao planejador
    ("abra a conversa com no instagram", "needs_input", "ig.abrir_conversa@1", QuestionReason.MISSING_PARAMETER),
    ("veja os últimos posts de @nasa na aba feed", "needs_input", "ig.ver_posts@1", QuestionReason.MISSING_PARAMETER),
    # NÃO casam: o texto fixo é outro (verbo, app, fim cortado, acento no texto fixo) — o planejador fica com eles
    ("abrir a conversa com @ana no instagram", "no_match", None, None),
    ("abra a conversa com @ana no facebook", "no_match", None, None),
    ("abra a conversa com @ana", "no_match", None, None),
    ("abra a conversa no instagram", "no_match", None, None),
    ("ábra a conversa com @ana no instagram", "no_match", None, None),
    ("poste uma foto do gato", "no_match", None, None),
]


@pytest.fixture
def mundo(db: Database) -> Mundo:
    m = Mundo(db)
    m.publicar(ABRIR)
    m.publicar(LER, manual=True)
    publicar_doc(m, DOC_VER_POSTS)
    return m


@pytest.mark.parametrize("frase, status, ref, esperado", GOLDEN_FRASES)
def test_golden_de_frases(mundo: Mundo, frase: str, status: str, ref: str | None,
                          esperado: dict[str, str] | QuestionReason | None) -> None:
    r = mundo.planejador.resolve_intent(frase, None)
    assert r.status.value == status, r.as_dict()
    skill = r.skill
    assert (str(skill.ref) if skill is not None else None) == ref
    rp = mundo.planejador.for_command(frase, None)
    if isinstance(esperado, dict):
        assert r.intent is not None and dict(r.intent.skill.parameters) == esperado
        assert rp is not None and rp.plan is not None
        assert {k: v for k, v in rp.plan.parameters.items() if k in esperado} == esperado
    elif isinstance(esperado, QuestionReason):
        assert r.questions and {q.reason for q in r.questions} == {esperado}
        assert all(q.skill == ref for q in r.questions)
        assert rp is not None and rp.plan is None and rp.questions == r.questions and not rp.issues
    else:
        assert rp is None


def test_tipos_chegam_ao_plano_e_o_padrao_fica_com_o_compilador(mundo: Mundo) -> None:
    rp = mundo.planejador.for_command("veja os últimos vinte e cinco posts de @nasa na aba reels", None)
    assert rp is not None and rp.plan is not None
    assert rp.plan.parameters == {"quantos": "25", "perfil": "@nasa", "aba": "reels", "detalhar": "false"}
    assert [s.key for s in rp.plan.steps] == ["ver"]                  # `when` falso: o nó de detalhe saiu
    r = mundo.planejador.resolve_intent("veja os últimos vinte e cinco posts de @nasa na aba reels", None)
    assert r.intent is not None
    assert [(p.name, p.value, p.origin.value, p.raw) for p in r.intent.parameters] == [
        ("quantos", "25", "command", "vinte e cinco"), ("perfil", "@nasa", "command", "@nasa"),
        ("aba", "reels", "command", "reels"), ("detalhar", "false", "default", None)]


def test_resultado_identico_ao_da_fase_g_para_o_que_ja_casava(mundo: Mundo) -> None:
    """O plano da fatia G (`@ana` com arroba) sai byte a byte igual: a normalização do handle é a identidade aí."""
    rp = mundo.planejador.for_command("abra a conversa com @ana no instagram", None)
    assert rp is not None and rp.plan is not None
    assert rp.plan.parameters == {"username": "@ana"} and rp.plan.planner.model == "skill:ig.abrir_conversa@1"
    assert rp.resolution.intent is not None and rp.resolution.intent.method is ResolutionMethod.TEMPLATE


# ==================================================================== paridade com o FlowStore.match legado
PLANO_MANDE = {**PLANO_CURTIR, "summary": "Mandar {texto} para {usuario}",
               "parameters": {"texto": "{texto}", "usuario": "{usuario}"},
               "steps": [{"key": "mandar", "title": "Mandar {texto}", "goal": "mandar {texto} para {usuario}",
                          "postcondition": {"kind": "text_visible", "value": "{texto}", "description": "enviada"}}]}

#: (comando, perfis da execução) — inclui empate entre fluxos (a ordem por uso decide, como hoje), escopo, frase
#: pela metade (fluxo não pergunta) e frases que não casam.
PARIDADE: list[tuple[str, list[str | None] | None]] = [
    ("curtir o post de @nasa", None),
    ("Curtir   o POST de nasa ", None),
    ("curtir @nasa", None),
    ("curtir o post de", None),
    ("curtir", None),
    ("seguir @nasa agora", None),
    ("seguir @nasa agora", ["p1"]),
    ("seguir @nasa agora", ["p2"]),
    ("seguir @nasa agora", ["p1", "p2"]),
    ("seguir @nasa agora", ["g1-p"]),
    ("seguir @nasa agora", []),
    ("mande “oi, tudo bem?” para fulano", None),
    ("mande \"oi\" para @Beltrano", None),
    ("descurtir @nasa", None),
    ("", None),
]


@pytest.fixture
def fluxos(db: Database) -> Mundo:
    perfil(db, "p1")
    perfil(db, "p2")
    perfil(db, "g1-p", grupo="g1")
    fluxo(db, "curtir-post", "curtir o post de {perfil}", uses=5)
    fluxo(db, "curtir", "curtir {perfil}", uses=1)
    fluxo(db, "seguir", "seguir {perfil} agora", perfis=("p1",), grupos=("g1",))
    fluxo(db, "mande", "mande “{texto}” para {usuario}", plano=PLANO_MANDE)
    return Mundo(db, skills=True, flows=True)


@pytest.mark.parametrize("comando, perfis", PARIDADE)
def test_paridade_com_o_flowstore_match(db: Database, fluxos: Mundo, comando: str,
                                        perfis: list[str | None] | None) -> None:
    legado = FlowStore(db).match(comando, perfis)
    rp = fluxos.planejador.for_command(comando, perfis)
    if legado is None:
        assert rp is None
        assert fluxos.planejador.resolve_intent(comando, perfis).status is ResolutionStatus.NO_MATCH
        return
    row, plano = legado
    assert rp is not None and rp.plan is not None
    assert rp.legacy_flow_id == row["id"] and rp.plan == plano
    valores = FlowStore._extract(row["command_template"], comando)
    assert rp.resolution.intent is not None and dict(rp.resolution.intent.skill.parameters) == valores
    assert all(p.type is None for p in rp.resolution.intent.parameters)       # fluxo não tem tipo


def test_empate_entre_fluxos_continua_decidido_pelo_uso(db: Database, fluxos: Mundo) -> None:
    """Os dois fluxos casam "curtir o post de @nasa"; o `FlowStore` fica com o mais usado, e a cadeia também."""
    assert FlowStore._extract("curtir {perfil}", "curtir o post de @nasa") == {"perfil": "o post de @nasa"}
    rp = fluxos.planejador.for_command("curtir o post de @nasa", None)
    assert rp is not None and rp.legacy_flow_id == "curtir-post"
    db.execute("UPDATE flows SET uses=9 WHERE id='curtir'")
    rp = fluxos.planejador.for_command("curtir o post de @nasa", None)
    assert rp is not None and rp.legacy_flow_id == "curtir" and rp.plan is not None
    assert rp.plan == FlowStore(db).match("curtir o post de @nasa")[1]  # type: ignore[index]


def test_skill_publicada_ganha_do_fluxo_e_buraco_vazio_perde_para_fluxo_inteiro(db: Database, fluxos: Mundo) -> None:
    publicar_doc(fluxos, doc_abrir("ig.curtir_perfil", "curtir o post de {perfil} agora", "perfil"))
    # a skill casa com buraco vazio ("curtir o post de agora"), mas o fluxo "curtir {perfil}" casa inteiro: o fluxo
    r = fluxos.planejador.resolve_intent("curtir o post de agora", None)
    assert r.status is ResolutionStatus.RESOLVED and r.intent is not None
    assert str(r.intent.ref) == "flow:curtir-post@1"            # o mais usado dos dois que casam, como hoje
    # com o fluxo desligado, sobra a pergunta da skill
    fluxos.flows_on = False
    r = fluxos.planejador.resolve_intent("curtir o post de agora", None)
    assert r.status is ResolutionStatus.NEEDS_INPUT and [q.field for q in r.questions] == ["perfil"]
    # casando inteira, a skill ganha do fluxo (precedência de sempre)
    fluxos.flows_on = True
    r = fluxos.planejador.resolve_intent("curtir o post de @nasa agora", None)
    assert r.intent is not None and str(r.intent.ref) == "ig.curtir_perfil@1"


# ==================================================================== empate vira pergunta
def test_duas_habilidades_empatadas_viram_pergunta_nunca_o_primeiro_id(db: Database) -> None:
    m = Mundo(db)
    m.publicar(ABRIR)
    publicar_doc(m, doc_abrir("ig.abrir_dm", "abra a conversa com {usuario} no instagram", "usuario"))
    frase = "abra a conversa com @ana no instagram"
    r = m.planejador.resolve_intent(frase, None)
    assert r.status is ResolutionStatus.NEEDS_INPUT and r.subject is None
    [q] = r.questions
    assert q.reason is QuestionReason.AMBIGUOUS_INTENT and q.options == ("ig.abrir_conversa@1", "ig.abrir_dm@1")
    assert [t.outcome.value for t in r.trace] == ["ambiguous", "ambiguous", "skipped", "not_run"]
    rp = m.planejador.for_command(frase, None)
    assert rp is not None and rp.plan is None and rp.resolved is None and rp.ref is None and rp.questions == (q,)
    # a precedência crua do registro continua a de antes (o primeiro por id); quem executa não a usa mais
    assert str(m.registro.resolve(frase, None).ref) == "ig.abrir_conversa@1"  # type: ignore[union-attr]


def test_empate_que_os_tipos_desfazem(db: Database) -> None:
    m = Mundo(db)
    m.publicar(ABRIR)
    publicar_doc(m, doc_abrir("ig.abrir_numero", "abra a conversa com {n} no instagram", "n", tipo="integer"))
    r = m.planejador.resolve_intent("abra a conversa com @ana no instagram", None)
    assert r.intent is not None and str(r.intent.ref) == "ig.abrir_conversa@1"
    assert r.intent.method is ResolutionMethod.TYPED
    # "3" serve para os dois tipos (inteiro e usuário): aí não há o que desempate
    r = m.planejador.resolve_intent("abra a conversa com 3 no instagram", None)
    assert r.status is ResolutionStatus.NEEDS_INPUT and r.questions[0].reason is QuestionReason.AMBIGUOUS_INTENT


def test_adulterada_entre_as_empatadas_e_recusa(db: Database) -> None:
    """A regra de sempre (publicada adulterada que casa é recusa) vale para toda empatada devolvida."""
    m = Mundo(db)
    m.publicar(ABRIR)
    publicar_doc(m, doc_abrir("ig.abrir_dm", "abra a conversa com {usuario} no instagram", "usuario"))
    sem_gatilho_046(db)
    db.execute("UPDATE skill_versions SET content=? WHERE skill_id='ig.abrir_dm'",
               (json.dumps({"adulterado": True}),))
    with pytest.raises(ContentTampered):
        m.planejador.resolve_intent("abra a conversa com @ana no instagram", None)


# ==================================================================== escopo por perfil e grupo
def test_escopo_por_perfil_e_grupo_como_hoje(db: Database) -> None:
    m = Mundo(db)
    m.publicar(ABRIR)
    perfil(db, "p1")
    perfil(db, "p2")
    perfil(db, "g1-p", grupo="g1")
    m.repo.set_scope(ABRIR, profile_ids=["p1"], group_ids=["g1"])
    frase = "abra a conversa com @ana no instagram"

    def status(perfis: list[str | None] | None) -> str:
        return m.planejador.resolve_intent(frase, perfis).status.value

    assert status(None) == "resolved"                 # prévia sem aparelhos: qualquer escopo casa
    assert status(["p1"]) == "resolved"
    assert status(["g1-p"]) == "resolved"             # pelo grupo
    assert status(["p1", "g1-p"]) == "resolved"
    assert status(["p2"]) == "no_match"               # fora do escopo: o planejador fica com o comando
    assert status(["p1", "p2"]) == "no_match"         # TODOS os perfis dentro, senão não
    assert status([None]) == "no_match"               # aparelho sem perfil não está em escopo nenhum
    assert status([]) == "no_match"


def test_escopo_vale_no_empate(db: Database) -> None:
    """Uma das empatadas fora do escopo não conta: com o perfil de fora, a outra resolve sozinha."""
    m = Mundo(db)
    m.publicar(ABRIR)
    publicar_doc(m, doc_abrir("ig.abrir_dm", "abra a conversa com {usuario} no instagram", "usuario"))
    perfil(db, "p1")
    perfil(db, "p2")
    m.repo.set_scope("ig.abrir_dm", profile_ids=["p1"], group_ids=[])
    frase = "abra a conversa com @ana no instagram"
    r = m.planejador.resolve_intent(frase, ["p2"])
    assert r.intent is not None and str(r.intent.ref) == "ig.abrir_conversa@1"
    assert m.planejador.resolve_intent(frase, ["p1"]).status is ResolutionStatus.NEEDS_INPUT


# ==================================================================== interruptores
def test_interruptores_continuam_mandando(db: Database) -> None:
    m = Mundo(db, skills=False, flows=False)
    m.publicar(ABRIR)
    # comando próprio (outro nome de buraco): o mesmo comando nos dois lugares a publicação recusaria
    fluxo(db, "abrir", "abra a conversa com {perfil} no instagram")
    frase = "abra a conversa com a Ana no instagram"      # com a skill ligada, seria pergunta
    assert m.planejador.resolve_intent(frase, None).status is ResolutionStatus.NO_MATCH
    m.flows_on = True
    r = m.planejador.resolve_intent(frase, None)          # o fluxo não tem tipo: passa como veio, como sempre
    assert r.intent is not None and str(r.intent.ref) == "flow:abrir@1"
    assert dict(r.intent.skill.parameters) == {"perfil": "a Ana"}
    m.skills_on = True
    r = m.planejador.resolve_intent(frase, None)
    assert r.status is ResolutionStatus.NEEDS_INPUT and str(r.subject.ref) == "ig.abrir_conversa@1"  # type: ignore[union-attr]
