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
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel

from ..automation.gestos import borda_de_saida
from ..taskqueue.flows import PLACEHOLDER
from .provider import AIError, validar_saida

TRAINER_SYSTEM = """Você observa uma pessoa ensinando uma tarefa num celular Android e transforma a gravação numa
HABILIDADE reutilizável por outros perfis. Você recebe a intenção declarada pela pessoa e, para cada entrada, a
tela em que ela aconteceu (título e algumas linhas), o elemento tocado (resource-id, texto, descrição) e o texto
digitado (quando pôde ser gravado).

Devolva:
- `command_template`: o comando em português que alguém digitaria para pedir esta tarefa, com os valores que
  variam como `{nome}` (ex.: "responda a DM de {contato} com {mensagem}"). Nomes em minúsculas, sem acento.
  Nunca use {instance_id}, {run_id} nem {account_label}: são do sistema e o salvar recusa (troque por um nome seu, ex.: {conta}).
  O comando começa pelo verbo, nunca por um parâmetro. Entre dois
  parâmetros sempre há pelo menos uma palavra fixa (nunca "{a} {b}" colados: não daria para separar os valores).
- `parameters`: cada `{nome}` do comando, com o valor usado nesta gravação em `example`. Todo `{nome}` do comando
  consta aqui e todo parâmetro daqui aparece no comando; o comando tem também texto fixo (ao menos duas palavras).
- `steps`: as etapas da tarefa, na ordem, cada uma com UM objetivo verificável. `inputs` lista os números das
  entradas que realizam a etapa. `postcondition` usa, sempre que possível, algo que a tela mostra:
  text_visible (um texto), element_present (um seletor como id=..., text==..., desc==...) ou app_foreground (o
  pacote); model_judged só quando nada disso serve. Use `{nome}` nos textos que dependem de parâmetro.
  A entrada pode trazer "textos na tela" (a tela em que ela aconteceu) e "apareceram depois" (o que surgiu na tela
  seguinte). O texto de text_visible ou element_present NÃO pode estar na tela da 1ª entrada da etapa: com ele, a
  etapa passaria sem agir. Prefira um dos que apareceram depois da última entrada da etapa.
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
- `questions`: dúvidas que só a pessoa responde (ex.: "o texto 'Bom dia' é sempre esse ou muda?"). Não invente. Se vier um bloco
  "Respostas da pessoa", use-o e não repita essas perguntas.

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
    #: 31.91: o que a pessoa respondeu às `questions` de propostas anteriores, [{question, answer}]; vazio = como antes
    answers: list[dict[str, str]] = field(default_factory=list)
    #: 31.114 F1: (largura, altura) da tela do aparelho na hora da proposta, ou `None` se ele não respondeu. Só serve para
    #: dizer de onde o arraste saiu (a borda); sem ela o texto diz "borda desconhecida" em vez de chutar.
    tela: tuple[int, int] | None = None


def descrever_arraste(x: int, y: int, x2: int, y2: int, tela: tuple[int, int] | None = None) -> str:
    """31.114 F1: o gesto do DEDO, em palavras que não se confundem com "rolar" (que é o movimento do conteúdo, o contrário):
    de onde saiu, para onde foi e quanto percorreu. A medição real (06/10) mostrou a IA lendo "rolou para cima" como o dedo
    subindo, quando o dedo tinha descido a partir da borda superior. Com a tela, diz a borda e a distância em %; sem ela
    (aparelho fora do ar, ou coordenadas que não cabem nela), diz a direção e "borda desconhecida"."""
    dx, dy = x2 - x, y2 - y
    vertical = abs(dy) >= abs(dx)
    if vertical:
        sentido = "de cima para baixo" if dy > 0 else "de baixo para cima"
    else:
        sentido = "da esquerda para a direita" if dx > 0 else "da direita para a esquerda"
    if tela is not None and not (0 <= x <= tela[0] and 0 <= x2 <= tela[0] and 0 <= y <= tela[1] and 0 <= y2 <= tela[1]):
        tela = None                                   # a tela lida não é a da gravação (rotação, outro tamanho): não chuta
    if tela is None:
        return f"arrastou o dedo {sentido} (borda de origem desconhecida)"
    largura, altura = tela
    borda = borda_de_saida(x, y, largura, altura)
    percorreu = round(100 * (abs(dy) / altura if vertical else abs(dx) / largura))
    saiu = f"saindo da borda {borda}" if borda else "saindo do meio da tela, sem tocar a borda"
    return f"arrastou o dedo {sentido}, {saiu}, por {percorreu} % da {'altura' if vertical else 'largura'}"


def linha_da_entrada(e: dict[str, Any], tela: tuple[int, int] | None = None) -> str:
    """Uma linha por entrada, compacta: é o que o modelo lê."""
    partes = [f"#{e['seq']} {e['type']}"]
    if e.get("package"):
        partes.append(f"app={e['package']}")
    if e.get("screen_title"):
        partes.append(f"tela=\"{e['screen_title']}\"")
    alvo = e.get("target") or {}
    if e["type"] in ("tap", "long_press") and e.get("sensitive") and e.get("x") is None and not alvo:
        # 31.94: tecla de PIN ou tela sensível: o gravador não guardou nem o elemento nem a coordenada
        partes.append("toque em teclado ou tela sensível (não gravado)")
    elif alvo:
        rid = (alvo.get("resource_id") or "").rsplit("/", 1)[-1]
        el = ", ".join(x for x in (f"id={rid}" if rid else "", f"texto=\"{alvo.get('text')}\"" if alvo.get("text") else "",
                                   f"desc=\"{alvo.get('desc')}\"" if alvo.get("desc") else "") if x)
        partes.append(f"elemento[{el or alvo.get('class_name', '?')}]")
    elif e["type"] in ("tap", "long_press"):
        ponto = f"ponto=({e.get('x')},{e.get('y')}) " if e.get("x") is not None else ""
        partes.append(f"{ponto}sem elemento identificado")
    if e["type"] == "swipe":
        if e.get("y") is None or e.get("y2") is None:
            # 31.97: arraste sobre teclado, padrão de bloqueio ou tela sensível: sem as coordenadas; não há direção a inventar
            partes.append("arraste em teclado, padrão de bloqueio ou tela sensível (não gravado)")
        else:
            partes.append(descrever_arraste(e["x"], e["y"], e["x2"], e["y2"], tela))
    if e["type"] == "text":
        partes.append(f"digitou \"{e['text']}\"" if e.get("text") is not None
                      else f"digitou {e.get('text_len') or '?'} caractere(s) SIGILOSOS (não gravados)")
    if e["type"] == "key":
        partes.append(f"tecla {e.get('key_name')}")
    if e["type"] == "open_app":
        partes.append(f"abriu o app {e.get('app_id')}")
    textos = e.get("textos_da_tela")
    if isinstance(textos, dict):
        # 31.148: a tela inteira (com o marcador da persona) no lugar das 6 linhas filtradas, e o que apareceu depois
        partes.append("textos na tela: " + " · ".join(textos.get("partida") or []))
        if "depois" in textos:
            partes.append("apareceram depois: " + (" · ".join(textos["depois"]) or "nada novo"))
    elif e.get("screen_lines"):
        partes.append("tela mostrava: " + " · ".join(e["screen_lines"][:6]))
    return " | ".join(partes)


def texto_das_respostas(pares: Iterable[tuple[str, str]]) -> str:
    """O bloco de respostas da pessoa que vai ao modelo, igual no ensino v2 (`generalizer`) e no `propose` do treino
    (31.91): UMA frase, um lugar. Sem pares, texto vazio."""
    linhas = [f"- {pergunta} → {resposta}" for pergunta, resposta in pares]
    if not linhas:
        return ""
    return "Respostas da pessoa às suas perguntas anteriores (não pergunte de novo):\n" + "\n".join(linhas)


def trainer_user(req: TrainingRequest) -> str:
    apps = "\n".join(f"- {a['id']}: {a['name']} ({a['package']})" for a in req.apps)
    cat = ""
    if req.catalog:
        cat = "\n\nCATÁLOGO de ações do app (use a chave em `capability`):\n" + "\n".join(
            f"- {c['key']}: {c['title']}{' [efeito externo]' if c.get('side_effect') else ''}"
            f"{' args=' + ','.join(c.get('bindings') or []) if c.get('bindings') else ''}" for c in req.catalog)
    entradas = "\n".join(linha_da_entrada(e, req.tela) for e in req.inputs)
    respostas = texto_das_respostas((r["question"], r["answer"]) for r in req.answers)
    return (f"Intenção declarada pela pessoa: {req.intent}\nApp principal: {req.app_id or 'não informado'}\n"
            f"Apps configurados:\n{apps}{cat}\n\nGravação ({len(req.inputs)} entradas):\n{entradas}"
            + (f"\n\n{respostas}" if respostas else ""))


_TAMANHO_MAX_DA_CHAVE = 40     # o padrão de `PlanStep.key` aceita 41 (`^[a-z][a-z0-9_]{1,40}$`); 40 deixa folga


def _chave(k: str) -> str:
    """Chave de etapa válida em `PlanStep.key` (letra, depois letras/dígitos/_), nunca com mais de 40 caracteres."""
    k = re.sub(r"[^a-z0-9_]+", "_", (k or "").strip().lower()).strip("_")[:_TAMANHO_MAX_DA_CHAVE]
    if not k:
        return "etapa"                        # título só com símbolos ou acentos
    if not re.match(r"^[a-z]", k) or len(k) < 2:
        return f"etapa_{k}"[:_TAMANHO_MAX_DA_CHAVE]
    return k


def _chave_unica(base: str, vistos: set[str]) -> str:
    """`base`, ou `base_2`, `base_3`… cortando a base para o sufixo caber. O laço antigo (`f"{k}_2"[:40]`) devolvia a
    mesma chave quando ela já tinha 40 caracteres e nunca saía, congelando o laço de eventos do backend inteiro (31.93).
    Aqui o sufixo muda a cada volta e sempre cabe; `vistos` é finito, então termina."""
    if base not in vistos:
        return base
    n = 2
    while True:
        sufixo = f"_{n}"
        k = f"{base[:_TAMANHO_MAX_DA_CHAVE - len(sufixo)]}{sufixo}"
        if k not in vistos:
            return k
        n += 1


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
        k = _chave_unica(_chave(s.get("key") or s.get("title") or "etapa"), vistos)
        vistos.add(k)
        etapas.append({**s, "key": k, "inputs": sorted({int(i) for i in s.get("inputs") or [] if int(i) in existentes}),
                       "bindings": [b for b in s.get("bindings") or [] if b.get("name")]})
    nomes = set(PLACEHOLDER.findall(p.get("command_template") or ""))      # o padrão do fluxo (aceita `_x`)
    parametros = [x for x in p.get("parameters") or [] if x.get("name") in nomes]
    descartadas: dict[int, dict[str, object]] = {}
    for d in p.get("discarded") or []:
        if int(d.get("seq", -1)) in existentes:
            descartadas.setdefault(int(d["seq"]), d)      # o modelo às vezes repete o descarte: fica o primeiro
    return {"summary": (p.get("summary") or req.intent)[:200], "command_template": (p.get("command_template") or req.intent).strip(),
            "parameters": parametros, "steps": etapas,
            "discarded": list(descartadas.values()),
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
        if tipo == "swipe" and (e.get("y") is None or e.get("y2") is None):
            descartes.append({"seq": seq, "why": "arraste não gravado (teclado, padrão de bloqueio ou tela sensível)"})
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
