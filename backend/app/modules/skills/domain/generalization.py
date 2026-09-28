"""Da proposta do generalizador à candidata `{document, annotations}` + perguntas (§13.2). Puro: sem IA, sem banco.

A proposta chega no formato que o modo treinamento já pede ao modelo (`planning/training.py`: comando com
`{parâmetros}`, etapas com objetivo e pós-condição, descartes e dúvidas) e é tratada como DADO não confiável: cada
campo é conferido no tipo, e o que não serve é descartado — nada aqui é avaliado como código (decisão 4). O
documento que sai vai ao `SkillDocument` (`extra="forbid"`) e ao compilador, que dizem se ele serve.

O generalizador PERGUNTA em vez de inventar. As perguntas têm chave estável (`efeito:<nó>`, `sigilo:<seq>`,
`catalogo:<nó>`, `ai:<hash>`): a mesma dúvida numa geração seguinte tem a mesma chave, e a que já foi respondida
não volta. A resposta não reescreve o documento aqui — ela vira suposição anotada e vai, na IA real, para o prompt
da próxima geração; quem muda o documento é o generalizador, nunca uma leitura de "sim/não" feita por regra.
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.modules.skills.domain.document import JsonObject, JsonValue
from app.modules.skills.domain.teaching import (AnsweredQuestion, CandidateAnnotations, CandidateEnvelope,
                                                InferredParameter, ProposedQuestion, QuestionKind, RecordedInput,
                                                TurnAuthor)

API_VERSION = "automation/v1alpha1"
_PLACEHOLDER = re.compile(r"\{([a-z_][a-z0-9_]*)\}")
_NODE_ID = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
_PARAM = re.compile(r"^[a-z_][a-z0-9_]*$")
_POST_KINDS = frozenset({"text_visible", "app_foreground", "element_present", "model_judged"})
#: Erros do compilador que só uma pessoa resolve: viram pergunta (origem `compiler`) em vez de rejeitar a candidata.
PERSON_RESOLVABLE = frozenset({"E_CAPABILITY_REQUIRED", "E_COMMAND_AMBIGUOUS"})


@dataclass(frozen=True, slots=True)
class GeneralizationContext:
    skill_id: str
    app_id: str
    instruction: str
    has_catalog: bool                       # o app principal tem catálogo de capabilities
    inputs: tuple[RecordedInput, ...] = ()
    answers: tuple[AnsweredQuestion, ...] = ()
    corrections: tuple[str, ...] = ()
    simulated: bool = False                 # proposta do modo SIMULADO (regras fixas): fica dito na anotação


def infer_type(example: str) -> str:
    """O tipo do parâmetro pelo exemplo. Conservador: na dúvida, `string` (o tipo mais largo que o compilador aceita
    sem regra extra); `text` quando há espaço, porque texto livre (mensagem, legenda) não é identificador."""
    e = example.strip()
    if re.fullmatch(r"-?\d{1,9}", e):
        return "integer"
    if re.match(r"https?://", e, re.IGNORECASE):
        return "url"
    if re.fullmatch(r"@[A-Za-z0-9._]{2,30}", e) or re.fullmatch(r"[a-z0-9]+[._][a-z0-9._]+", e):
        return "handle"
    if any(c.isspace() for c in e):
        return "text"
    return "string"


def envelope_from_proposal(proposal: JsonObject,
                           ctx: GeneralizationContext) -> tuple[CandidateEnvelope, tuple[ProposedQuestion, ...]]:
    comando = _texto(proposal.get("command_template")).strip() or ctx.instruction.strip()
    resumo = _texto(proposal.get("summary")).strip() or ctx.instruction.strip() or ctx.skill_id
    respondidas = {a.key: a for a in ctx.answers}

    # ---- parâmetros: só os que o comando usa (o compilador exige a ida e a volta)
    no_comando = set(_PLACEHOLDER.findall(comando))
    parametros: list[JsonObject] = []
    inferidos: list[InferredParameter] = []
    for p in _objetos(proposal.get("parameters")):
        nome = _texto(p.get("name")).strip()
        if not _PARAM.fullmatch(nome) or nome not in no_comando or any(x.get("name") == nome for x in parametros):
            continue
        exemplo = _texto(p.get("example"))[:200]
        descricao = _texto(p.get("description"))[:2000]
        tipo = infer_type(exemplo) if exemplo else "string"
        spec: JsonObject = {"name": nome, "type": tipo, "required": True}
        if exemplo:
            spec["example"] = exemplo
        if descricao:
            spec["description"] = descricao
        parametros.append(spec)
        inferidos.append(InferredParameter(nome, tipo, (exemplo,) if exemplo else (), descricao))
    declarados = {_texto(p.get("name")) for p in parametros}

    def expr(texto: str) -> str:
        """`{nome}` de parâmetro declarado vira `${parameters.nome}` (a forma da DSL); o resto fica como veio, e o
        compilador aponta `E_RAW_PLACEHOLDER` se sobrar chave crua."""
        return _PLACEHOLDER.sub(lambda m: "${parameters." + m.group(1) + "}" if m.group(1) in declarados
                                else m.group(0), texto)

    # ---- nós
    nos: list[JsonObject] = []
    evidencia: dict[str, tuple[int, ...]] = {}
    pos: list[JsonObject] = []
    efeitos: list[JsonObject] = []
    riscos: list[str] = []
    perguntas: list[ProposedQuestion] = []
    apps = [ctx.app_id]
    anterior: str | None = None
    usados: set[str] = set()
    for i, etapa in enumerate(_objetos(proposal.get("steps"))):
        no_id = _node_id(_texto(etapa.get("key")) or _texto(etapa.get("title")), i, usados)
        titulo = _texto(etapa.get("title")).strip() or no_id
        objetivo = _texto(etapa.get("goal")).strip() or titulo
        efeito = etapa.get("side_effect") is True
        capability = _texto(etapa.get("capability")).strip()
        app_da_etapa = _texto(etapa.get("app_id")).strip()
        no: JsonObject = {"id": no_id, "depends_on": [anterior] if anterior else []}
        if app_da_etapa and app_da_etapa != ctx.app_id:
            no["app"] = app_da_etapa
            apps.append(app_da_etapa)
        if capability:
            no["capability"] = capability[:64]
            vinculos: JsonObject = {}
            for b in _objetos(etapa.get("bindings")):
                nome_b, valor_b = _texto(b.get("name")).strip(), _texto(b.get("value"))
                if nome_b:
                    vinculos[nome_b] = expr(valor_b)
            if vinculos:
                no["with"] = vinculos
            pos.append({"node": no_id, "kind": "catalog", "value": capability})
        else:
            post = _objeto(etapa.get("postcondition"))
            tipo = _texto(post.get("kind"))
            valor = _texto(post.get("value")).strip()
            if tipo not in _POST_KINDS or not valor:
                # Sem pós-condição que a tela mostre, a prova fica com o julgamento do modelo: é dito como risco.
                tipo, valor = "model_judged", objetivo
            descricao = _texto(post.get("description")).strip()
            postcondicao: JsonObject = {"kind": tipo, "value": expr(valor)[:2000]}
            if descricao:
                postcondicao["description"] = expr(descricao)[:2000]
            no["goal"] = {"title": expr(titulo)[:200], "goal": expr(objetivo)[:2000]}
            no["verification"] = {"postcondition": postcondicao}
            if efeito:
                no["side_effect"] = True
            pos.append({"node": no_id, "kind": tipo, "value": valor})
            if tipo == "model_judged":
                riscos.append(f"O nó {no_id} é conferido pelo julgamento do modelo: cada verificação custa uma "
                              "chamada de IA e é menos firme que um texto ou elemento na tela.")
        if efeito:
            efeitos.append({"node": no_id, "capability": capability or None, "description": titulo})
            chave = f"efeito:{no_id}"
            if chave not in respondidas:
                perguntas.append(ProposedQuestion(
                    chave, QuestionKind.EFFECT_CONFIRMATION,
                    f"A etapa “{titulo}” muda algo fora do aparelho (envia, publica, curte, segue...). É esse o "
                    "efeito que a habilidade deve ter?", TurnAuthor.AI, {"node": no_id}))
        seqs = tuple(sorted({s for s in _lista(etapa.get("inputs")) if isinstance(s, int)
                             and not isinstance(s, bool)}))
        evidencia[no_id] = seqs
        if ctx.inputs and not seqs:
            riscos.append(f"Nenhuma entrada gravada sustenta o nó {no_id}: a IA conduz essa etapa.")
        nos.append(no)
        anterior = no_id

    if not nos:
        # Só instrução (ou nada que virasse etapa): UMA etapa, com o objetivo que a pessoa escreveu, conduzida pela
        # IA e conferida pelo julgamento do modelo. Não se inventa passo nem prova de tela — pergunta-se.
        pedido = ctx.instruction.strip() or resumo
        nos.append({"id": "executar", "depends_on": [],
                    "goal": {"title": expr(resumo)[:200], "goal": expr(pedido)[:2000]},
                    "verification": {"postcondition": {"kind": "model_judged", "value": expr(pedido)[:2000]}}})
        evidencia["executar"] = ()
        pos.append({"node": "executar", "kind": "model_judged", "value": pedido})
        riscos.append("Sem demonstração: uma etapa só, conduzida pela IA e conferida pelo julgamento do modelo.")
        if "etapas:instrucao" not in respondidas:
            perguntas.append(ProposedQuestion(
                "etapas:instrucao", QuestionKind.SCOPE,
                "Sem demonstração, a candidata tem uma etapa só, que a IA conduz e o modelo confere. Pode ficar "
                "assim, ou você vai demonstrar a tarefa no aparelho para ela ganhar etapas e provas na tela?",
                TurnAuthor.AI, {"node": "executar"}))
        if ctx.has_catalog and "efeito:instrucao" not in respondidas:
            perguntas.append(ProposedQuestion(
                "efeito:instrucao", QuestionKind.POLICY,
                f"A tarefa muda algo fora do aparelho em {ctx.app_id} (envia, publica, curte, segue)? Se muda, qual "
                "ação do catálogo ela usa? Sem a ação do catálogo a etapa não pode ter efeito.", TurnAuthor.AI,
                {"node": "executar"}))

    # ---- o que a gravação tinha de sigiloso vira pergunta, nunca parâmetro
    for entrada in ctx.inputs:
        if entrada.secret_text:
            riscos.append(f"A entrada #{entrada.seq} digitou {entrada.text_len or '?'} caractere(s) sigilosos, que "
                          "não foram gravados.")
            chave = f"sigilo:{entrada.seq}"
            if chave not in respondidas:
                perguntas.append(ProposedQuestion(
                    chave, QuestionKind.MISSING_PARAMETER,
                    f"Na entrada #{entrada.seq} foi digitado um texto sigiloso (não gravado). Se é credencial, ela "
                    "não entra na habilidade: fica na conta da persona (guia Contas e acesso). O que a tarefa deve "
                    "digitar ali? Responda sem o valor secreto.", TurnAuthor.AI, {"input": entrada.seq}))

    for texto in _textos(proposal.get("questions"))[:8]:
        chave = "ai:" + hashlib.sha1(texto.strip().lower().encode("utf-8")).hexdigest()[:12]
        if texto.strip() and chave not in respondidas:
            perguntas.append(ProposedQuestion(chave, QuestionKind.AMBIGUITY, texto.strip()[:500], TurnAuthor.AI))

    exemplos = {_texto(p.get("name")): _texto(p.get("example")) for p in parametros}
    invocacao: JsonObject = {"command_template": comando[:500]}
    if parametros and all(exemplos.values()):
        invocacao["examples"] = [_PLACEHOLDER.sub(lambda m: exemplos.get(m.group(1), m.group(0)), comando)[:500]]
    documento: JsonObject = {
        "apiVersion": API_VERSION, "kind": "Skill",
        "metadata": {"id": ctx.skill_id, "name": resumo[:200], "app": ctx.app_id,
                     **({"description": ctx.instruction.strip()[:2000]} if ctx.instruction.strip() else {})},
        "spec": {"invocation": invocacao, "parameters": list(parametros),
                 "requires": {"apps": list(dict.fromkeys(apps))}, "nodes": list(nos)},
    }

    suposicoes = [f"App principal: {ctx.app_id}."]
    if ctx.simulated:
        suposicoes.append("Candidata do modo SIMULADO (regras fixas, não é IA): serve para exercitar o ensino.")
    suposicoes += [f"Pergunta “{a.question}” respondida: “{a.answer}”." for a in ctx.answers]
    suposicoes += [f"Correção da pessoa: {c}" for c in ctx.corrections]
    precondicoes = [f"O app {a} está instalado no aparelho." for a in dict.fromkeys(apps)]
    if ctx.has_catalog:
        precondicoes.append(f"A conta do perfil está conectada em {ctx.app_id}.")
    provas: list[JsonObject] = []
    if parametros and all(exemplos.values()):
        provas.append({"name": "exemplo-da-demonstracao", "kind": "simulated",
                       "parameters": {k: v for k, v in exemplos.items()}, "expected": "succeeded"})
    for e in efeitos:
        provas.append({"name": f"efeito-{e['node']}", "kind": "device", "expected": "succeeded",
                       "note": "efeito externo só se prova em aparelho real (P4)"})
    descartes = tuple((d["seq"], _texto(d.get("why"))) for d in _objetos(proposal.get("discarded"))
                      if isinstance(d.get("seq"), int) and not isinstance(d.get("seq"), bool))
    anotacoes = CandidateAnnotations(
        evidence=evidencia, discarded=tuple((s, w) for s, w in descartes if isinstance(s, int)),
        assumptions=tuple(suposicoes), parameters=tuple(inferidos), preconditions=tuple(precondicoes),
        postconditions=tuple(pos), suggested_proofs=tuple(provas), effects=tuple(efeitos), risks=tuple(riscos))
    return CandidateEnvelope(documento, anotacoes), tuple(perguntas)


def compiler_questions(errors: Sequence[str], document: JsonObject,
                       answered: Mapping[str, AnsweredQuestion]) -> tuple[list[ProposedQuestion], list[str]]:
    """Separa os erros de compilação em PERGUNTAS (o que só a pessoa resolve e ainda não foi respondido) e ERROS
    que rejeitam a candidata. Um erro "resolvível" cuja pergunta já foi respondida volta a ser erro: a resposta não
    bastou, e perguntar de novo seria um laço."""
    perguntas: list[ProposedQuestion] = []
    restantes: list[str] = []
    nos = [n for n in _lista(_objeto(document.get("spec")).get("nodes")) if isinstance(n, dict)]
    for erro in errors:
        codigo = erro.split(" ", 1)[0].rstrip(":")
        if codigo not in PERSON_RESOLVABLE:
            restantes.append(erro)
            continue
        if codigo == "E_CAPABILITY_REQUIRED":
            m = re.search(r"/spec/nodes/(\d+)", erro)
            no = nos[int(m.group(1))] if m and int(m.group(1)) < len(nos) else {}
            no_id = _texto(no.get("id")) or "?"
            titulo = _texto(_objeto(no.get("goal")).get("title")) or no_id
            chave = f"catalogo:{no_id}"
            texto = (f"A etapa “{titulo}” muda algo fora do aparelho num app com catálogo de ações: qual ação do "
                     "catálogo ela realiza? Sem isso a habilidade passaria por fora da aprovação e dos limites do "
                     "perfil.")
            pergunta = ProposedQuestion(chave, QuestionKind.POLICY, texto, TurnAuthor.COMPILER, {"node": no_id})
        else:
            pergunta = ProposedQuestion(
                "comando:ambiguo", QuestionKind.AMBIGUITY,
                "O comando tem dois parâmetros colados (“{a} {b}”): que palavra fixa separa os dois valores?",
                TurnAuthor.COMPILER, {"field": "command_template"})
        if pergunta.key in answered:
            restantes.append(erro)
        elif all(p.key != pergunta.key for p in perguntas):
            perguntas.append(pergunta)
    return perguntas, restantes


def _node_id(bruto: str, i: int, usados: set[str]) -> str:
    base = re.sub(r"[^a-z0-9_]+", "_", bruto.strip().lower()).strip("_")[:36]
    if not base or not base[0].isalpha() or len(base) < 2:
        base = f"etapa_{i + 1}"
    no_id, n = base, 2
    while no_id in usados or not _NODE_ID.fullmatch(no_id):
        no_id = f"{base[:36]}_{n}"
        n += 1
    usados.add(no_id)
    return no_id


def _texto(v: JsonValue) -> str:
    return v if isinstance(v, str) else ""


def _lista(v: JsonValue) -> list[JsonValue]:
    return v if isinstance(v, list) else []


def _objeto(v: JsonValue) -> JsonObject:
    return v if isinstance(v, dict) else {}


def _objetos(v: JsonValue) -> list[JsonObject]:
    return [x for x in _lista(v) if isinstance(x, dict)]


def _textos(v: JsonValue) -> list[str]:
    return [x for x in _lista(v) if isinstance(x, str)]
