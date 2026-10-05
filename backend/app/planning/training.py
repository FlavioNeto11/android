"""Generalização assistida do modo treinamento (item 13.2).

A pessoa fez a tarefa no aparelho; a gravação (13.1) diz, entrada a entrada, em que tela ela estava, que elemento
tocou e o que digitou. O modelo lê isso e devolve o que um colega faria ao assistir: "isto é abrir a conversa com
{contato}; isto foi um toque errado seguido de voltar; o texto é o conteúdo da mensagem e muda a cada vez". Sai uma
PROPOSTA — comando com parâmetros, etapas com objetivo e pós-condição verificável, entradas descartadas e perguntas
— que a pessoa revisa antes de salvar. Não é um replay literal: o que vira receita são os ELEMENTOS (seletores) e o
que vira plano são os OBJETIVOS; o valor digitado vira parâmetro.

Só texto vai ao modelo (sem imagem): cada entrada já carrega o elemento e umas linhas da tela, o que basta para
entender a intenção e custa uma fração de uma chamada com screenshot.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel

from .provider import AIError, validar_saida

TRAINER_SYSTEM = """Você observa uma pessoa ensinando uma tarefa num celular Android e transforma a gravação numa
HABILIDADE reutilizável por outros perfis. Você recebe a intenção declarada pela pessoa e, para cada entrada, a
tela em que ela aconteceu (título e algumas linhas), o elemento tocado (resource-id, texto, descrição) e o texto
digitado (quando pôde ser gravado).

Devolva:
- `command_template`: o comando em português que alguém digitaria para pedir esta tarefa, com os valores que
  variam como `{nome}` (ex.: "responda a DM de {contato} com {mensagem}"). Nomes em minúsculas, sem acento. Entre dois
  parâmetros sempre há pelo menos uma palavra fixa (nunca "{a} {b}" colados: não daria para separar os valores).
- `parameters`: cada `{nome}` do comando, com o valor usado nesta gravação em `example`. Todo `{nome}` do comando
  consta aqui e todo parâmetro daqui aparece no comando; o comando tem também texto fixo (ao menos duas palavras).
- `steps`: as etapas da tarefa, na ordem, cada uma com UM objetivo verificável. `inputs` lista os números das
  entradas que realizam a etapa. `postcondition` usa, sempre que possível, algo que a tela mostra:
  text_visible (um texto), element_present (um seletor como id=..., text==..., desc==...) ou app_foreground (o
  pacote); model_judged só quando nada disso serve. Use `{nome}` nos textos que dependem de parâmetro.
- `side_effect`: true quando a etapa muda algo fora do aparelho (enviar, publicar, curtir, seguir, comentar,
  salvar). Se for fornecido um CATÁLOGO de ações do app, toda etapa com efeito DEVE indicar em `capability` a ação
  do catálogo que ela realiza, e `bindings` com os argumentos (ex.: username={contato}, content={mensagem});
  etapa sem efeito também pode indicar a ação de navegação do catálogo quando houver.
- `app_id`: o app da etapa, quando a tarefa atravessa apps (null = o app principal).
- `discarded`: entradas que NÃO fazem parte da tarefa (toque errado, voltar logo em seguida, rolagem à toa,
  abrir algo por engano), com o motivo.
  Regras de cobertura e de teclas (a pessoa só salva se elas valerem):
  * TODA entrada gravada aparece em UMA etapa (`inputs`) ou em `discarded`, com o motivo; nenhuma fica de fora.
  * Teclas de apagar (`delete`, `del`, backspace) usadas só para limpar um campo antes de digitar vão para
    `discarded`: a reprodução limpa o campo sozinha. Nunca ponha dezenas de teclas na etapa do texto.
  * `back` ou `home` que só desfazem um engano de quem ensinou vão para `discarded`. Tecla que faz parte da tarefa
    (ex.: enter para enviar) fica na etapa.
  * Toda etapa tem `postcondition` com `description` verificável (o que a tela mostra quando deu certo), mesmo
    quando o tipo é model_judged.
- `questions`: dúvidas que só a pessoa responde (ex.: "o texto 'Bom dia' é sempre esse ou muda?"). Não invente.

Nunca trate como parâmetro algo que parece senha ou código; entrada com texto não gravado é sigilosa."""


class _ParamT(BaseModel):
    name: str
    example: str
    description: str


class _PostT(BaseModel):
    kind: Literal["text_visible", "app_foreground", "element_present", "model_judged"]
    value: str
    description: str


class _BindT(BaseModel):
    name: str
    value: str


class _StepT(BaseModel):
    key: str
    title: str
    goal: str
    inputs: list[int]
    side_effect: bool
    capability: str | None
    bindings: list[_BindT]
    app_id: str | None
    postcondition: _PostT


class _DiscT(BaseModel):
    seq: int
    why: str


class _TrainOut(BaseModel):
    summary: str
    command_template: str
    parameters: list[_ParamT]
    steps: list[_StepT]
    discarded: list[_DiscT]
    questions: list[str]


@dataclass(slots=True)
class TrainingRequest:
    intent: str
    app_id: str | None
    apps: list[dict[str, str]]                      # [{id, name, package}]
    inputs: list[dict[str, Any]]                    # linhas de training_inputs já lidas
    catalog: list[dict[str, Any]] = field(default_factory=list)   # [{key, title, side_effect, bindings}]
    session_id: str | None = None


def linha_da_entrada(e: dict[str, Any]) -> str:
    """Uma linha por entrada, compacta: é o que o modelo lê."""
    partes = [f"#{e['seq']} {e['type']}"]
    if e.get("package"):
        partes.append(f"app={e['package']}")
    if e.get("screen_title"):
        partes.append(f"tela=\"{e['screen_title']}\"")
    alvo = e.get("target") or {}
    if alvo:
        rid = (alvo.get("resource_id") or "").rsplit("/", 1)[-1]
        el = ", ".join(x for x in (f"id={rid}" if rid else "", f"texto=\"{alvo.get('text')}\"" if alvo.get("text") else "",
                                   f"desc=\"{alvo.get('desc')}\"" if alvo.get("desc") else "") if x)
        partes.append(f"elemento[{el or alvo.get('class_name', '?')}]")
    elif e["type"] in ("tap", "long_press"):
        partes.append(f"ponto=({e.get('x')},{e.get('y')}) sem elemento identificado")
    if e["type"] == "swipe":
        dy = (e.get("y2") or 0) - (e.get("y") or 0)
        partes.append("rolou para baixo" if dy < 0 else "rolou para cima")
    if e["type"] == "text":
        partes.append(f"digitou \"{e['text']}\"" if e.get("text") is not None
                      else f"digitou {e.get('text_len') or '?'} caractere(s) SIGILOSOS (não gravados)")
    if e["type"] == "key":
        partes.append(f"tecla {e.get('key_name')}")
    if e["type"] == "open_app":
        partes.append(f"abriu o app {e.get('app_id')}")
    if e.get("screen_lines"):
        partes.append("tela mostrava: " + " · ".join(e["screen_lines"][:6]))
    return " | ".join(partes)


def trainer_user(req: TrainingRequest) -> str:
    apps = "\n".join(f"- {a['id']}: {a['name']} ({a['package']})" for a in req.apps)
    cat = ""
    if req.catalog:
        cat = "\n\nCATÁLOGO de ações do app (use a chave em `capability`):\n" + "\n".join(
            f"- {c['key']}: {c['title']}{' [efeito externo]' if c.get('side_effect') else ''}"
            f"{' args=' + ','.join(c.get('bindings') or []) if c.get('bindings') else ''}" for c in req.catalog)
    entradas = "\n".join(linha_da_entrada(e) for e in req.inputs)
    return (f"Intenção declarada pela pessoa: {req.intent}\nApp principal: {req.app_id or 'não informado'}\n"
            f"Apps configurados:\n{apps}{cat}\n\nGravação ({len(req.inputs)} entradas):\n{entradas}")


def _chave(k: str) -> str:
    k = re.sub(r"[^a-z0-9_]+", "_", (k or "").strip().lower()).strip("_")[:40]
    return k if re.match(r"^[a-z]", k) and len(k) >= 2 else f"etapa_{k or 'x'}"


def proposal_from_json(raw: str, req: TrainingRequest) -> dict[str, Any]:
    texto = (raw or "").strip()
    if texto.startswith("```"):
        texto = re.sub(r"^```[a-zA-Z]*\s*", "", texto)
        texto = re.sub(r"\s*```$", "", texto).strip()
    falha: str | None = None
    try:
        dados = json.loads(texto)
    except ValueError as exc:                # o `str` do JSONDecodeError diz linha e coluna, não o documento
        falha = str(exc)
    if falha is not None:
        # 31.67 (V1b): fora do `except`, sem a causa (o `.doc` do `JSONDecodeError` é a proposta inteira do modelo).
        raise AIError(f"Proposta de treinamento inválida devolvida pelo modelo: {falha}", kind="invalid_output")
    # 31.63 (V3): a validação sem a entrada (`input_value=` traria a proposta do modelo), levantada fora do `except`.
    out = validar_saida(_TrainOut, dados, "Proposta de treinamento inválida devolvida pelo modelo")
    return normalizar_proposta(out.model_dump(), req)


def normalizar_proposta(p: dict[str, Any], req: TrainingRequest) -> dict[str, Any]:
    """Limpa o que o modelo devolveu: chaves únicas, entradas que existem, parâmetros usados no comando."""
    existentes = {int(e["seq"]) for e in req.inputs}
    vistos: set[str] = set()
    etapas = []
    for s in p.get("steps") or []:
        k = _chave(s.get("key") or s.get("title") or "etapa")
        while k in vistos:
            k = f"{k}_2"[:40]
        vistos.add(k)
        etapas.append({**s, "key": k, "inputs": sorted({int(i) for i in s.get("inputs") or [] if int(i) in existentes}),
                       "bindings": [b for b in s.get("bindings") or [] if b.get("name")]})
    nomes = set(re.findall(r"\{([a-z][a-z0-9_]*)\}", p.get("command_template") or ""))
    parametros = [x for x in p.get("parameters") or [] if x.get("name") in nomes]
    return {"summary": (p.get("summary") or req.intent)[:200], "command_template": (p.get("command_template") or req.intent).strip(),
            "parameters": parametros, "steps": etapas,
            "discarded": [d for d in p.get("discarded") or [] if int(d.get("seq", -1)) in existentes],
            "questions": [q for q in p.get("questions") or [] if q][:8]}


def proposta_simulada(req: TrainingRequest) -> dict[str, Any]:
    """Generalização por regras fixas — MODO SIMULADO, não é IA. Existe para os testes e para o painel funcionar
    sem chave: agrupa as entradas em etapas (abrir app / tocar num item / escrever / enviar), descarta 'voltar' e
    transforma o texto digitado e o item tocado em parâmetros."""
    etapas: list[dict[str, Any]] = []
    parametros: list[dict[str, str]] = []
    descartes: list[dict[str, Any]] = []
    for e in req.inputs:
        seq, tipo, alvo = int(e["seq"]), e["type"], e.get("target") or {}
        if tipo == "key":
            descartes.append({"seq": seq, "why": f"tecla {e.get('key_name')} — navegação de quem ensinou"})
            continue
        if tipo == "open_app":
            etapas.append({"key": "abrir_app", "title": f"Abrir {e.get('app_id')}", "goal": f"Abrir o app {e.get('app_id')}",
                           "inputs": [seq], "side_effect": False, "capability": None, "bindings": [], "app_id": None,
                           "postcondition": {"kind": "app_foreground",
                                             "value": next((a["package"] for a in req.apps if a["id"] == e.get("app_id")), ""),
                                             "description": "app em primeiro plano"}})
            continue
        if tipo == "text":
            if e.get("text") is None:
                descartes.append({"seq": seq, "why": "texto sigiloso não vira parâmetro"})
                continue
            parametros.append({"name": "mensagem", "example": e["text"], "description": "texto digitado"})
            etapas.append({"key": "escrever", "title": "Escrever {mensagem}", "goal": "Digitar {mensagem} no campo",
                           "inputs": [seq], "side_effect": False, "capability": None, "bindings": [], "app_id": None,
                           "postcondition": {"kind": "text_visible", "value": "{mensagem}", "description": "texto no campo"}})
            continue
        texto = (alvo.get("text") or alvo.get("desc") or "").strip()
        rid = (alvo.get("resource_id") or "").rsplit("/", 1)[-1]
        efeito = texto.lower() in ("enviar", "send", "publicar", "post")
        if efeito:
            etapas.append({"key": "enviar", "title": "Enviar", "goal": "Tocar em enviar", "inputs": [seq],
                           "side_effect": True, "capability": None, "bindings": [], "app_id": None,
                           "postcondition": {"kind": "text_visible", "value": "{mensagem}", "description": "mensagem enviada"}})
        elif rid in ("conversation_name",) and texto:
            parametros.append({"name": "contato", "example": texto, "description": "item tocado na lista"})
            etapas.append({"key": "abrir_conversa", "title": "Abrir a conversa com {contato}", "goal": "Abrir {contato}",
                           "inputs": [seq], "side_effect": False, "capability": None, "bindings": [], "app_id": None,
                           "postcondition": {"kind": "text_visible", "value": "{contato}", "description": "conversa aberta"}})
        elif etapas and not etapas[-1]["side_effect"]:
            etapas[-1]["inputs"].append(seq)             # toque de apoio (focar o campo) entra na etapa anterior
        else:
            descartes.append({"seq": seq, "why": "toque sem elemento reconhecível"})
    nomes = [p["name"] for p in parametros]
    # Rótulo antes de cada parâmetro: dois `{x} {y}` colados são ambíguos e o fluxo nunca casaria.
    comando = req.intent + "".join(f" — {n}: {{{n}}}" for n in nomes if f"{{{n}}}" not in req.intent)
    return normalizar_proposta({"summary": req.intent, "command_template": comando, "parameters": parametros,
                                "steps": etapas, "discarded": descartes, "questions": []}, req)
