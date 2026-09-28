"""Fase I, puro: a extração tipada (`domain/intent.py`), o casamento com buraco vazio (`matching.extract_with_gaps`) e a
cadeia `IntentResolver` com candidatos falsos — sem banco, sem aparelho, sem IA.

Nível de prova: `simulated` (unidade). As etapas 3 e 4 (semântica e LLM) só rodam aqui com dublês de teste que provam
que a cadeia é plugável; em produção a única implementação delas é a nula, que não chama IA (`not_run`).
"""
from __future__ import annotations

from collections.abc import Sequence

import pytest

from app.contracts.skills.v1alpha1 import ParameterSpec
from app.modules.skills.application.intent_resolver import (IntentRequest, IntentResolver, NullDisambiguator,
                                                            NullSemanticClassifier, ParameterExtractor, TemplateStage)
from app.modules.skills.domain.document import JsonObject
from app.modules.skills.domain.intent import (ParameterOrigin, QuestionReason, ResolutionMethod, ResolutionStatus,
                                              SkillMatch, StageOutcome, ValueProblem, extract_typed,
                                              handle_from_link, normalize_value)
from app.modules.skills.domain.lifecycle import SkillState
from app.modules.skills.domain.matching import extract_parameters, extract_with_gaps
from app.modules.skills.domain.refs import SkillRef
from app.modules.skills.domain.versions import (SCHEMA_DSL_V1, SCHEMA_LEGACY_PLAN, Provenance, ResolvedSkill,
                                                SkillDefinition, SkillVersion, SourceKind)
from app.modules.skills.infrastructure.profile_links import profile_links_for
from app.taskqueue.flows import FlowStore

#: As regras de link de perfil do Instagram, lidas do `app.yaml` dele pelo registro (ADR-052, fatia 4).
IG = profile_links_for("com.instagram.android")


def spec(tipo: str, **extra: object) -> ParameterSpec:
    return ParameterSpec.model_validate({"name": "p", "type": tipo, **extra})


# ================================================================== tabela golden da normalização por tipo
#: (tipo, extras do ParameterSpec, texto do comando, valor esperado — ou None quando o valor NÃO serve)
GOLDEN_TIPOS: list[tuple[str, dict[str, object], str, str | None]] = [
    # handle: com e sem @, caixa, aspas, link de perfil do Instagram → @usuario
    ("handle", {}, "@ana", "@ana"),
    ("handle", {}, "ana", "@ana"),
    ("handle", {}, "@Ana.Teste", "@ana.teste"),
    ("handle", {}, "\"@ana_teste\"", "@ana_teste"),
    ("handle", {}, "https://www.instagram.com/ana.teste/", "@ana.teste"),
    ("handle", {}, "instagram.com/ana_teste?igsh=abc123", "@ana_teste"),
    ("handle", {}, "http://m.instagram.com/Ana", "@ana"),
    ("handle", {}, "https://instagr.am/ana", "@ana"),
    ("handle", {}, "https://www.instagram.com/p/Cxyz123/", None),          # post, não perfil
    ("handle", {}, "https://www.instagram.com/stories/ana/123/", None),    # story: de quem é, na dúvida pergunta
    ("handle", {}, "https://www.instagram.com/", None),
    ("handle", {}, "https://instagram.com.evil.com/ana", None),            # domínio que só COMEÇA igual
    ("handle", {}, "https://evilinstagram.com/ana", None),
    ("handle", {}, "https://twitter.com/ana", None),                       # outro app: sem regra, não adivinha
    ("handle", {}, "a Ana", None),                                         # espaço: frase, não usuário
    ("handle", {}, "@@ana", None),
    ("handle", {}, "@joão", None),                                         # acento não existe em usuário
    ("handle", {}, "a" * 31, None),
    # integer: dígitos, sinal, zeros, por extenso (com e sem acento, qualquer caixa), milhar pt-BR
    ("integer", {}, "3", "3"),
    ("integer", {}, "+3", "3"),
    ("integer", {}, "007", "7"),
    ("integer", {}, "-2", "-2"),
    ("integer", {}, "três", "3"),
    ("integer", {}, "TRES", "3"),
    ("integer", {}, "duas", "2"),
    ("integer", {}, "vinte e cinco", "25"),
    ("integer", {}, "cem", "100"),
    ("integer", {}, "1.000", "1000"),
    ("integer", {}, "1,5", None),
    ("integer", {}, "1.5", None),
    ("integer", {}, "muitos", None),
    ("integer", {}, "vinte e dez", None),
    # boolean: português, com e sem acento, qualquer caixa → true/false (o texto que o `when` lê)
    ("boolean", {}, "sim", "true"),
    ("boolean", {}, "Não", "false"),
    ("boolean", {}, "nao", "false"),
    ("boolean", {}, "VERDADEIRO", "true"),
    ("boolean", {}, "desligado", "false"),
    ("boolean", {}, "talvez", None),
    # enum: o valor DECLARADO sai, casando sem caixa e sem acento
    ("enum", {"values": ["Feed", "Reels", "Stories"]}, "reels", "Reels"),
    ("enum", {"values": ["Feed", "Reels", "Stories"]}, "Feed", "Feed"),
    ("enum", {"values": ["Ação", "Comédia"]}, "acao", "Ação"),
    ("enum", {"values": ["Feed", "Reels", "Stories"]}, "igtv", None),
    ("enum", {"values": ["acao", "Ação"]}, "AÇÃO", None),                  # casa com duas: pergunta, não escolhe
    # url: http(s), com o esquema acrescentado quando falta
    ("url", {}, "https://Example.com/a?b=1", "https://example.com/a?b=1"),
    ("url", {}, "example.com/x", "https://example.com/x"),
    ("url", {}, "ftp://example.com/x", None),
    ("url", {}, "não é link", None),
    # string/text: como veio; `max_length` e `pattern` do documento valem
    ("string", {}, "oi, tudo bem?", "oi, tudo bem?"),
    ("text", {}, "linha longa de texto livre", "linha longa de texto livre"),
    ("string", {"max_length": 5}, "abcdef", None),
    ("string", {"pattern": "[a-z]+"}, "abc", "abc"),
    ("string", {"pattern": "[a-z]+"}, "ab1", None),
]


@pytest.mark.parametrize("tipo, extra, bruto, esperado", GOLDEN_TIPOS)
def test_golden_da_normalizacao_por_tipo(tipo: str, extra: dict[str, object], bruto: str, esperado: str | None) -> None:
    valor = normalize_value(spec(tipo, **extra), bruto, IG)
    if esperado is None:
        assert isinstance(valor, ValueProblem) and valor.expected and valor.detail
    else:
        assert valor == esperado


def test_link_de_perfil_so_vira_usuario_no_app_que_tem_a_regra() -> None:
    assert handle_from_link("https://www.instagram.com/ana/", IG) == "ana"
    assert handle_from_link("https://www.instagram.com/ana/", ()) is None
    assert isinstance(normalize_value(spec("handle"), "https://www.instagram.com/ana/", ()), ValueProblem)


# ================================================================== extração tipada
PARAMS = [spec("handle").model_copy(update={"name": "perfil", "example": "@ana"}),
          ParameterSpec(name="quantos", type="integer", required=False, default=3),
          ParameterSpec(name="confirmar", type="boolean", required=False, default=True),
          ParameterSpec(name="aba", type="enum", values=["feed", "reels"], required=False),
          ParameterSpec(name="nota", type="string", required=False)]


def test_valores_do_comando_padroes_mostrados_e_opcionais_sem_valor_ausentes() -> None:
    r = extract_typed(PARAMS, {"perfil": "https://www.instagram.com/Ana/", "aba": "REELS"}, links=IG)
    assert r.ok
    assert [(p.name, p.type, p.value, p.origin, p.raw) for p in r.parameters] == [
        ("perfil", "handle", "@ana", ParameterOrigin.COMMAND, "https://www.instagram.com/Ana/"),
        ("quantos", "integer", "3", ParameterOrigin.DEFAULT, None),
        ("confirmar", "boolean", "true", ParameterOrigin.DEFAULT, None),       # o texto de `_texto_do_valor`
        ("aba", "enum", "reels", ParameterOrigin.COMMAND, "REELS")]
    # ao compilador vão só os valores do COMANDO: o padrão continua sendo aplicado por ele (`_ligar`)
    assert r.command_values() == {"perfil": "@ana", "aba": "reels"}


def test_obrigatorio_faltando_e_tipo_invalido_viram_perguntas_estruturadas() -> None:
    r = extract_typed(PARAMS, {"quantos": "muitos", "aba": "igtv"}, skill="ig.x@1", skill_name="X")
    assert not r.ok
    por_campo = {q.field: q for q in r.questions}
    assert set(por_campo) == {"perfil", "quantos", "aba"}
    assert por_campo["perfil"].reason is QuestionReason.MISSING_PARAMETER and "(ex.: @ana)" in por_campo["perfil"].question
    assert por_campo["quantos"].reason is QuestionReason.INVALID_PARAMETER
    assert por_campo["quantos"].received == "muitos" and por_campo["quantos"].expected
    assert por_campo["aba"].options == ("feed", "reels")
    assert all(q.skill == "ig.x@1" and "“X”" in q.question for q in r.questions)
    assert set(por_campo["aba"].as_dict()) == {"field", "question", "reason", "expected", "options", "received",
                                               "skill"}


def test_buraco_vazio_sempre_pergunta_mesmo_com_padrao() -> None:
    """Casar pela metade não é casar: o padrão não decide por quem deixou o valor em branco."""
    r = extract_typed(PARAMS, {"perfil": "@ana"}, missing=("quantos",))
    assert [(q.field, q.reason) for q in r.questions] == [("quantos", QuestionReason.MISSING_PARAMETER)]
    assert "quantos" not in {p.name for p in r.parameters}


def test_conteudo_legado_passa_como_veio() -> None:
    """Sem tipo declarado (fluxo, v1 de fluxo adotado): a mesma resposta do `FlowStore.match` de sempre."""
    r = extract_typed(None, {"perfil": "@Ana", "texto": "  oi  "})
    assert r.ok and r.command_values() == {"perfil": "@Ana", "texto": "  oi  "}
    assert all(p.type is None for p in r.parameters)


def test_nome_nao_declarado_passa_sem_tipo_para_o_compilador_recusar() -> None:
    r = extract_typed(PARAMS[:1], {"perfil": "@ana", "intruso": "x"})
    assert r.command_values() == {"perfil": "@ana", "intruso": "x"}


# ================================================================== casamento com buraco vazio
@pytest.mark.parametrize("modelo, comando, esperado", [
    ("abra a conversa com {u} no instagram", "abra a conversa com no instagram", ({}, ("u",))),
    ("abra a conversa com {u} no instagram", "Abra a  conversa COM   no instagram", ({}, ("u",))),
    ("abrir conversa com {usuario}", "abrir conversa com", ({}, ("usuario",))),
    ("mande {texto} para {contato}", "mande para @ana", ({"contato": "@ana"}, ("texto",))),
    ("mande {texto} para {contato}", "mande oi para", ({"texto": "oi"}, ("contato",))),
    ("abra a conversa com {u} no instagram", "abra a conversa com @ana no instagram", None),   # inteiro: não é buraco
    ("abra a conversa com {u} no instagram", "abra a conversa no instagram", None),             # o texto fixo mudou
    ("abra a conversa com {u} no instagram", "abra a conversa com @ana", None),
    ("seguir {perfil}", "seguir " + "x" * 501, None),                                            # teto: não é buraco
    ("sem parâmetro", "sem parâmetro", None),
])
def test_buraco_vazio(modelo: str, comando: str, esperado: tuple[dict[str, str], tuple[str, ...]] | None) -> None:
    assert extract_with_gaps(modelo, comando) == esperado


@pytest.mark.parametrize("modelo, comando", [
    ("abrir conversa com {usuario}", "abrir conversa com"),
    ("abra a conversa com {u} no instagram", "abra a conversa com no instagram"),
    ("curtir o post de {perfil}", "Curtir   o POST de @nasa "),
])
def test_a_extracao_estrita_continua_a_do_fluxo_legado(modelo: str, comando: str) -> None:
    """A refatoração de `extract_parameters` (buraco `.+?` num padrão compartilhado) não mudou a resposta."""
    assert extract_parameters(modelo, comando) == FlowStore._extract(modelo, comando)


# ================================================================== a cadeia, com candidatos falsos
def documento(skill_id: str, modelo: str, parametros: Sequence[dict[str, object]]) -> JsonObject:
    return {"apiVersion": "automation/v1alpha1", "kind": "Skill",
            "metadata": {"id": skill_id, "name": skill_id, "app": "instagram"},
            "spec": {"invocation": {"command_template": modelo}, "parameters": list(parametros),  # type: ignore[dict-item]
                     "nodes": [{"id": "abrir", "capability": "OPEN_INBOX"}]}}


def habilidade(skill_id: str, modelo: str, valores: dict[str, str], parametros: Sequence[dict[str, object]] = (),
               *, legado: bool = False) -> ResolvedSkill:
    ref = SkillRef(skill_id, 1)
    conteudo: JsonObject = ({"schema_version": 0, "command_template": modelo, "plan": {}} if legado
                            else documento(skill_id, modelo, parametros))
    versao = SkillVersion.frozen(ref, conteudo, state=SkillState.PUBLISHED,
                                 schema_version=SCHEMA_LEGACY_PLAN if legado else SCHEMA_DSL_V1,
                                 command_template=modelo, app_ids=("instagram",),
                                 provenance=Provenance(SourceKind.MANUAL), at="t", by=None, detail=None)
    definicao = SkillDefinition(id=skill_id, name=f"Habilidade {skill_id}", description="", app_id="instagram",
                                legacy_flow_id=None, created_by=None, created_at="t", updated_at="t")
    return ResolvedSkill(definition=definicao, version=versao, parameters=valores)


class Fonte:
    def __init__(self, *matches: SkillMatch) -> None:
        self.matches = matches

    def candidates(self, command: str, profile_ids: Sequence[str | None] | None) -> Sequence[SkillMatch]:
        return self.matches


HANDLE = {"name": "u", "type": "handle", "example": "@ana"}
INTEIRO = {"name": "u", "type": "integer", "example": "3"}


def resolver(*matches: SkillMatch) -> IntentResolver:
    return IntentResolver.standard(Fonte(*matches), ParameterExtractor(lambda app_id: IG))


def etapas(r: object) -> list[tuple[str, StageOutcome]]:
    return [(t.stage, t.outcome) for t in getattr(r, "trace")]


def test_um_candidato_valido_resolve_pelo_modelo_com_o_valor_normalizado() -> None:
    a = habilidade("ig.a", "abra {u}", {"u": "Ana"}, [HANDLE])
    r = resolver(SkillMatch(a)).resolve(IntentRequest("abra Ana"))
    assert r.status is ResolutionStatus.RESOLVED and r.intent is not None
    assert r.intent.method is ResolutionMethod.TEMPLATE and r.intent.skill.parameters == {"u": "@ana"}
    assert etapas(r) == [("template", StageOutcome.MATCHED), ("typed", StageOutcome.VALIDATED),
                         ("semantic", StageOutcome.SKIPPED), ("llm", StageOutcome.SKIPPED)]
    assert r.as_dict()["intent"] == {"skill_ref": "ig.a@1", "skill_id": "ig.a", "version": 1,
                                     "name": "Habilidade ig.a", "backend": "skill", "method": "template",
                                     "parameters": [{"name": "u", "type": "handle", "value": "@ana",
                                                     "origin": "command", "raw": "Ana"}]}


def test_legado_resolve_com_os_valores_de_sempre() -> None:
    a = habilidade("ig.a", "curtir {perfil}", {"perfil": "@NASA"}, legado=True)
    r = resolver(SkillMatch(a)).resolve(IntentRequest("curtir @NASA"))
    assert r.intent is not None and r.intent.skill.parameters == {"perfil": "@NASA"}


def test_empate_vira_pergunta_e_as_etapas_por_ia_ficam_not_run() -> None:
    a = habilidade("ig.a", "abra {u}", {"u": "@ana"}, [HANDLE])
    b = habilidade("ig.b", "abra {v}", {"v": "@ana"}, [{**HANDLE, "name": "v"}])
    r = resolver(SkillMatch(a), SkillMatch(b)).resolve(IntentRequest("abra @ana"))
    assert r.status is ResolutionStatus.NEEDS_INPUT and r.intent is None and r.subject is None
    [q] = r.questions
    assert q.reason is QuestionReason.AMBIGUOUS_INTENT and q.options == ("ig.a@1", "ig.b@1") and q.field == "skill"
    assert [c.ref for c in r.candidates] == [a.ref, b.ref]
    assert etapas(r)[-2:] == [("semantic", StageOutcome.SKIPPED), ("llm", StageOutcome.NOT_RUN)]


def test_entre_empatados_os_tipos_desempatam_quando_so_um_serve() -> None:
    a = habilidade("ig.a", "abra {u}", {"u": "@ana"}, [HANDLE])
    b = habilidade("ig.b", "abra {u}", {"u": "@ana"}, [INTEIRO])
    r = resolver(SkillMatch(a), SkillMatch(b)).resolve(IntentRequest("abra @ana"))
    assert r.intent is not None and r.intent.ref == a.ref and r.intent.method is ResolutionMethod.TYPED
    assert ("typed", StageOutcome.TIE_BROKEN) in etapas(r)


def test_candidato_unico_com_tipo_invalido_pergunta_sobre_ele() -> None:
    a = habilidade("ig.a", "abra {u}", {"u": "a Ana"}, [HANDLE])
    r = resolver(SkillMatch(a)).resolve(IntentRequest("abra a Ana"))
    assert r.status is ResolutionStatus.NEEDS_INPUT and r.subject is not None and r.subject.ref == a.ref
    assert [(q.field, q.reason, q.received) for q in r.questions] == [("u", QuestionReason.INVALID_PARAMETER, "a Ana")]


def test_buraco_vazio_pergunta_e_nao_resolve() -> None:
    a = habilidade("ig.a", "abra {u} agora", {}, [HANDLE])
    r = resolver(SkillMatch(a, missing=("u",))).resolve(IntentRequest("abra agora"))
    assert r.status is ResolutionStatus.NEEDS_INPUT
    assert [(q.field, q.reason) for q in r.questions] == [("u", QuestionReason.MISSING_PARAMETER)]
    assert etapas(r) == [("template", StageOutcome.PARTIAL), ("typed", StageOutcome.INVALID),
                         ("semantic", StageOutcome.NOT_RUN), ("llm", StageOutcome.SKIPPED)]


def test_nada_casa_e_o_planejador_fica_com_o_comando() -> None:
    r = resolver().resolve(IntentRequest("qualquer coisa"))
    assert r.status is ResolutionStatus.NO_MATCH and not r.questions
    assert etapas(r) == [("template", StageOutcome.NO_MATCH), ("typed", StageOutcome.SKIPPED),
                         ("semantic", StageOutcome.NOT_RUN), ("llm", StageOutcome.SKIPPED)]


def test_os_provedores_padrao_das_etapas_por_ia_sao_os_nulos() -> None:
    assert NullSemanticClassifier().available is False and NullDisambiguator().available is False
    assert NullSemanticClassifier().classify("x", ()) is None and NullDisambiguator().choose("x", ()) is None
    assert resolver().stages == ("template", "typed", "semantic", "llm")


# ------------------------------------------------------------------ a cadeia é plugável (dublês de teste)
class Desempate:
    available = True

    def __init__(self, escolha: SkillMatch | None) -> None:
        self.escolha = escolha

    def choose(self, command: str, candidates: Sequence[SkillMatch]) -> SkillMatch | None:
        return self.escolha


class Classificador:
    available = True

    def __init__(self, achado: SkillMatch | None) -> None:
        self.achado = achado

    def classify(self, command: str, candidates: Sequence[SkillMatch]) -> SkillMatch | None:
        return self.achado


def test_desempate_plugado_so_vale_se_escolher_um_dos_candidatos() -> None:
    a = habilidade("ig.a", "abra {u}", {"u": "@ana"}, [HANDLE])
    b = habilidade("ig.b", "abra {v}", {"v": "@ana"}, [{**HANDLE, "name": "v"}])
    fora = habilidade("ig.fora", "x {u}", {"u": "@ana"}, [HANDLE])
    extrator = ParameterExtractor(lambda app_id: IG)
    escolhe_b = IntentResolver.standard(Fonte(SkillMatch(a), SkillMatch(b)), extrator,
                                        disambiguator=Desempate(SkillMatch(b)))
    r = escolhe_b.resolve(IntentRequest("abra @ana"))
    assert r.intent is not None and r.intent.ref == b.ref and r.intent.method is ResolutionMethod.LLM
    inventa = IntentResolver.standard(Fonte(SkillMatch(a), SkillMatch(b)), extrator,
                                      disambiguator=Desempate(SkillMatch(fora)))
    assert inventa.resolve(IntentRequest("abra @ana")).status is ResolutionStatus.NEEDS_INPUT


def test_classificador_plugado_passa_pelos_mesmos_tipos() -> None:
    extrator = ParameterExtractor(lambda app_id: IG)
    bom = habilidade("ig.a", "abra {u}", {"u": "https://www.instagram.com/ana/"}, [HANDLE])
    r = IntentResolver.standard(Fonte(), extrator, classifier=Classificador(SkillMatch(bom))).resolve(
        IntentRequest("fala com a ana"))
    assert r.intent is not None and r.intent.method is ResolutionMethod.SEMANTIC
    assert r.intent.skill.parameters == {"u": "@ana"}
    ruim = habilidade("ig.a", "abra {u}", {"u": "a ana"}, [HANDLE])
    r2 = IntentResolver.standard(Fonte(), extrator, classifier=Classificador(SkillMatch(ruim))).resolve(
        IntentRequest("fala com a ana"))
    assert r2.status is ResolutionStatus.NEEDS_INPUT and r2.questions[0].reason is QuestionReason.INVALID_PARAMETER


def test_etapas_sao_plugaveis_so_modelos_sem_tipos() -> None:
    """Sem a etapa de tipos, o valor passa como veio — a cadeia não depende de uma etapa específica existir."""
    a = habilidade("ig.a", "abra {u}", {"u": "Ana"}, [HANDLE])
    r = IntentResolver((TemplateStage(Fonte(SkillMatch(a))),)).resolve(IntentRequest("abra Ana"))
    assert r.intent is not None and r.intent.skill.parameters == {"u": "Ana"}
