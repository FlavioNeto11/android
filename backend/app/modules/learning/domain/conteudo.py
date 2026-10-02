"""O conteúdo legível dos itens do Livro (30.3, `docs/design/aprendizado-vivo.md` §4): o que a receita, o fluxo, a
habilidade, a lição e a tela FAZEM, em português estruturado, montado só do que já está no banco.

Puro: recebe linhas já lidas (`dict`/dataclass) e devolve JSON; quem lê o SQL é `infrastructure/fontes.py`.

A regra que manda aqui é a do segredo e a do valor: o texto digitado de uma receita é 100 % parâmetro (`{nome}`), então
só os NOMES dos parâmetros saem, nunca um valor nem um pedaço literal digitado. A ação `type_secret` e o parâmetro
com cara de segredo (`SENSITIVE_PARAM`) saem só como `segredo: true`, sem nome. Por isso a leitura é por LISTA
BRANCA de campos: nada de `args` é copiado em bloco, e um campo novo da receita não vaza sozinho.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from app.modules.learning.domain.livro import fluxo_tem_efeito, receita_tem_efeito
from app.modules.skills.domain.document import JsonObject, JsonValue

#: Os mesmos padrões de `taskqueue/recipes.py` (`TEMPLATE_RE`, `SENSITIVE_PARAM`). O domínio não importa o executor;
#: `tests/test_learning_conteudo.py` confere que as cópias não divergem.
PARAMETRO_RE = re.compile(r"\{([a-z_][a-z0-9_]*)\}")
SENSITIVE_PARAM = re.compile(r"pass|senha|pin\b|otp|token|secret|segredo|c[oó]digo|code", re.IGNORECASE)
PREFIXO_DE_TREINO = "training:"
#: O rótulo que ocupa o lugar de um parâmetro sigiloso dentro de um texto de seletor.
SEGREDO = "{segredo}"

_SELETORES = ("rid", "text", "desc")
_NOME_DO_SELETOR = {"rid": "rid", "text": "texto", "desc": "desc"}


# ------------------------------------------------------------------ o que a infraestrutura entrega
@dataclass(frozen=True, slots=True)
class ReceitaLida:
    """A linha de `recipes`, já tipada."""

    id: int
    app: str
    app_version: str
    assinatura: str
    variante: str
    step_hash: str
    step_key: str
    versao: int
    status: str
    acoes: JsonValue
    aprendida_de: str | None
    replay_ok: int
    replay_fail: int
    consecutive_fail: int
    shadow_agree: int
    shadow_total: int
    last_used_at: str | None


@dataclass(frozen=True, slots=True)
class EtapaDeOrigem:
    """A linha de `steps` que originou a receita (`recipes.learned_from_step`)."""

    id: str
    run_id: str
    capability: str | None


@dataclass(frozen=True, slots=True)
class Vizinha:
    """A receita da mesma chave numa versão vizinha."""

    id: int
    versao: int
    status: str


# ------------------------------------------------------------------ parâmetros e seletores
def _sigiloso(nome: str) -> bool:
    return SENSITIVE_PARAM.search(nome) is not None


def _nomes(texto: str) -> list[str]:
    return list(dict.fromkeys(PARAMETRO_RE.findall(texto)))


def _sem_sigilosos(texto: str) -> str:
    """O texto de um seletor com os parâmetros sigilosos trocados por `{segredo}` (os demais ficam `{nome}`)."""
    return PARAMETRO_RE.sub(lambda m: SEGREDO if _sigiloso(m.group(1)) else m.group(0), texto)


def _alvo(seletores: JsonValue) -> list[JsonObject]:
    """Cada seletor da receita (em ordem de confiança), só com `rid`, `texto` e `desc` que ele tem. `tipo` é o
    `kind` que a receita gravou (`rid+text`, `rid`, `desc`, `text`...)."""
    saida: list[JsonObject] = []
    for s in seletores if isinstance(seletores, list) else []:
        if not isinstance(s, dict):
            continue
        item: JsonObject = {"tipo": s.get("kind") if isinstance(s.get("kind"), str) else "+".join(
            k for k in _SELETORES if isinstance(s.get(k), str))}
        for campo in _SELETORES:
            valor = s.get(campo)
            if isinstance(valor, str):
                item[_NOME_DO_SELETOR[campo]] = _sem_sigilosos(valor)
        saida.append(item)
    return saida


def _parametros_do_seletor(seletores: JsonValue) -> list[str]:
    achados: list[str] = []
    for s in seletores if isinstance(seletores, list) else []:
        if isinstance(s, dict):
            for campo in ("text", "desc"):
                valor = s.get(campo)
                if isinstance(valor, str):
                    achados.extend(_nomes(valor))
    return achados


def _inteiro(valor: JsonValue) -> int | None:
    return valor if isinstance(valor, int) and not isinstance(valor, bool) else None


def acao_legivel(indice: int, acao: JsonValue) -> JsonObject:
    """Uma ação da receita em palavras de máquina: ferramenta, alvo por seletor, `commit` e os NOMES dos parâmetros.

    `segredo: true` quando a ação é `type_secret` ou algum parâmetro tem cara de segredo; nesse caso nenhum nome
    sigiloso sai (nem aqui, nem dentro de um seletor)."""
    if not isinstance(acao, dict):
        return {"indice": indice, "ferramenta": None, "commit": False, "alvo": [], "parametros": [], "segredo": False}
    ferramenta = acao.get("tool") if isinstance(acao.get("tool"), str) else None
    args = acao.get("args") if isinstance(acao.get("args"), dict) else {}
    seletores = acao.get("selectors")
    nomes = _nomes(args["text"]) if isinstance(args.get("text"), str) else []
    nomes += _parametros_do_seletor(seletores)
    nomes = list(dict.fromkeys(nomes))
    segredo = ferramenta == "type_secret" or any(_sigiloso(n) for n in nomes)
    saida: JsonObject = {
        "indice": indice, "ferramenta": ferramenta, "commit": acao.get("commit") is True,
        "alvo": _alvo(seletores),
        "parametros": [n for n in nomes if not _sigiloso(n)], "segredo": segredo}
    if ferramenta == "type_text":
        saida["digita"] = {"limpa_antes": args.get("clear_first") is not False,
                           "enter": args.get("press_enter") is True,
                           "so_parametro": True}     # a receita só grava texto 100 % coberto por parâmetros
    if ferramenta == "open_app" and isinstance(args.get("package"), str):
        saida["pacote"] = args["package"]
    if ferramenta == "long_press" and _inteiro(args.get("duration_ms")) is not None:
        saida["duracao_ms"] = _inteiro(args.get("duration_ms"))
    if ferramenta == "collect_list":
        item = args.get("item_selector")
        exclui = args.get("exclude")
        saida["coleta"] = {"seletor_do_item": _sem_sigilosos(item) if isinstance(item, str) else None,
                           "exclusoes": len(exclui) if isinstance(exclui, list) else 0}
    rolagem = acao.get("scroll")
    if isinstance(rolagem, dict):
        saida["rolagem"] = {"direcao": rolagem.get("direction") if isinstance(rolagem.get("direction"), str) else None,
                            "max": _inteiro(rolagem.get("max"))}
    return saida


def acoes_da_receita(acoes: JsonValue) -> list[JsonObject]:
    return [acao_legivel(i, a) for i, a in enumerate(acoes if isinstance(acoes, list) else [])]


def efeito_da_receita(acoes: JsonValue) -> JsonObject:
    """O selo de efeito externo (`receita_tem_efeito`) e QUAL ação faz o commit (índices de `acoes[]`, base 0)."""
    commits = [i for i, a in enumerate(acoes if isinstance(acoes, list) else [])
               if isinstance(a, dict) and a.get("commit") is True]
    return {"externo": receita_tem_efeito(acoes), "acoes_commit": list(commits)}


# ------------------------------------------------------------------ capability e origem
def capability_da_receita(origem: EtapaDeOrigem | None, do_mesmo_template: Iterable[str]) -> JsonObject | None:
    """A capability que a receita NÃO grava, derivada. A etapa de origem manda; sem ela (ou sem capability nela), as
    etapas com o mesmo `step_hash` no mesmo app: uma só vale, várias são listadas e marcadas `ambigua`. `None`:
    nenhuma fonte diz (a etapa livre `*` ou a origem sumiu)."""
    if origem is not None and origem.capability:
        return {"nomes": [origem.capability], "ambigua": False, "fonte": "origem"}
    nomes = sorted({c for c in do_mesmo_template if c})
    if not nomes:
        return None
    return {"nomes": nomes, "ambigua": len(nomes) > 1, "fonte": "mesmo_step_hash"}


def origem_da_receita(aprendida_de: str | None, etapa: EtapaDeOrigem | None) -> JsonObject:
    """`treino` (prefixo `training:`), `execucao` (com `run_id` e `step_id`) ou `desconhecida`."""
    if not aprendida_de:
        return {"tipo": "desconhecida"}
    if aprendida_de.startswith(PREFIXO_DE_TREINO):
        return {"tipo": "treino", "ref": aprendida_de[len(PREFIXO_DE_TREINO):]}
    return {"tipo": "execucao", "step_id": aprendida_de, "run_id": etapa.run_id if etapa is not None else None}


def _vizinha(v: Vizinha | None) -> JsonObject | None:
    return None if v is None else {"id": v.id, "versao": v.versao, "estado": v.status}


def receita_legivel(r: ReceitaLida, *, etapa: EtapaDeOrigem | None, do_mesmo_template: Iterable[str],
                    anterior: Vizinha | None, seguinte: Vizinha | None) -> JsonObject:
    return {
        "tipo": "receita",
        "identidade": {"app": r.app, "app_version": r.app_version, "assinatura": r.assinatura or None,
                       "variante": r.variante or None, "step_key": r.step_key, "step_hash": r.step_hash,
                       "versao": r.versao, "estado": r.status},
        "acoes": acoes_da_receita(r.acoes),
        "efeito": efeito_da_receita(r.acoes),
        "capability": capability_da_receita(etapa, do_mesmo_template),
        "origem": origem_da_receita(r.aprendida_de, etapa),
        "uso": {"replay_ok": r.replay_ok, "replay_fail": r.replay_fail, "consecutive_fail": r.consecutive_fail,
                "last_used_at": r.last_used_at},
        "sombra": {"shadow_agree": r.shadow_agree, "shadow_total": r.shadow_total},
        "substitui": _vizinha(anterior),
        "substituida_por": _vizinha(seguinte),
    }


# ------------------------------------------------------------------ fluxo
def _texto(valor: JsonValue) -> str | None:
    return valor if isinstance(valor, str) and valor else None


def etapa_de_fluxo(indice: int, passo: JsonValue) -> JsonObject:
    """Uma etapa do plano do fluxo: chave, capability, alvo (o seletor que dispara o efeito), pós-condição e os NOMES
    dos argumentos da capability (`bindings`; os valores são `{param}` ou texto do plano e não saem)."""
    if not isinstance(passo, dict):
        return {"indice": indice, "chave": None, "capability": None, "alvo": None, "efeito": False,
                "pos_condicao": None, "parametros": [], "segredo": False}
    pos = passo.get("postcondition")
    bindings = passo.get("bindings")
    chaves = [k for k in bindings if isinstance(k, str)] if isinstance(bindings, dict) else []
    return {
        "indice": indice, "chave": _texto(passo.get("key")), "capability": _texto(passo.get("capability")),
        "alvo": _texto(passo.get("commit_selector")), "efeito": passo.get("side_effect") is True,
        "pos_condicao": ({"tipo": _texto(pos.get("kind")), "descricao": _texto(pos.get("description"))}
                         if isinstance(pos, dict) else None),
        "parametros": [k for k in chaves if not _sigiloso(k)], "segredo": any(_sigiloso(k) for k in chaves)}


def fluxo_legivel(plano: JsonValue, *, nome: str, comando_modelo: str, fonte: str | None,
                  source_run_id: str | None) -> JsonObject:
    passos = plano.get("steps") if isinstance(plano, dict) else None
    etapas = [etapa_de_fluxo(i, p) for i, p in enumerate(passos if isinstance(passos, list) else [])]
    treino = bool(fonte and fonte.startswith("training"))
    return {
        "tipo": "fluxo", "nome": nome, "comando_modelo": comando_modelo,
        "origem": {"tipo": "treino" if treino else "execucao", "fonte": fonte, "source_run_id": source_run_id},
        "etapas": etapas,
        "efeito": {"externo": fluxo_tem_efeito(plano),
                   "etapas_com_efeito": [e["indice"] for e in etapas if e["efeito"] is True]}}


# ------------------------------------------------------------------ habilidade
def habilidade_legivel(documento: JsonValue, *, skill_id: str, versao: int, schema_version: int, estado: str,
                       source_kind: str, source_ref: str | None, parent_version: int | None,
                       command_template: str | None, content_hash: str) -> JsonObject:
    """O resumo da versão (a edição e o ciclo são da rota das habilidades): id, versão, origem, parâmetros, nós.
    Documento legado (`schema_version` 0, plano de fluxo adotado) ou ilegível: só a identidade."""
    spec = documento.get("spec") if isinstance(documento, dict) else None
    spec = spec if isinstance(spec, dict) else {}
    parametros = spec.get("parameters")
    nos = spec.get("nodes")
    nomes = [p["name"] for p in parametros if isinstance(p, dict) and isinstance(p.get("name"), str)
             and not _sigiloso(p["name"])] if isinstance(parametros, list) else []
    resumo_dos_nos: list[JsonValue] = []
    for n in nos if isinstance(nos, list) else []:
        if isinstance(n, dict) and isinstance(n.get("id"), str):
            resumo_dos_nos.append({"id": n["id"], "tipo": _texto(n.get("kind")) or _texto(n.get("type"))})
    return {
        "tipo": "habilidade", "skill_id": skill_id, "versao": versao, "schema_version": schema_version,
        "estado": estado, "source_kind": source_kind, "source_ref": source_ref, "parent_version": parent_version,
        "command_template": command_template, "content_hash": content_hash, "parametros": list(nomes),
        "nos": resumo_dos_nos, "total_de_nos": len(resumo_dos_nos),
        "rota": f"/api/skills/{skill_id}/versions/{versao}"}


# ------------------------------------------------------------------ lição e tela
def licao_legivel(content: JsonObject, *, texto: str, app: str, capability: str, step_hash: str, role: str,
                  tokens: int | None) -> JsonObject:
    """A lição: o texto exato que o ator lê (é o `summary`, que a lista já mostra), o modelo, a ação e o alvo.
    O `alvo` do tipo `parametro` leva o NOME do parâmetro, nunca o valor (a regra da própria lição)."""
    alvo = content.get("alvo")
    return {
        "tipo": "licao", "texto": texto, "modelo": _texto(content.get("modelo")), "acao": _texto(content.get("acao")),
        "alvo": ({"tipo": _texto(alvo.get("tipo")), "valor": _texto(alvo.get("valor"))}
                 if isinstance(alvo, dict) else None),
        "escopo": {"app": app or None, "capability": capability or None, "step_hash": step_hash or None,
                   "role": role or None},
        "tokens": tokens}


def tela_legivel(content: JsonObject) -> JsonObject:
    """A regra de tela aprendida: o nome da tela, os ids que ela exige (`ids_todos`), se casa e a razão. Só ids de
    interface; a regra de conteúdo (`regra_do_conteudo`) já recusou o que não é id."""
    ids = content.get("ids_todos")
    return {
        "tipo": "tela", "tela": _texto(content.get("tela")), "casa": content.get("casa") is True,
        "autenticada": content.get("autenticada") is True,
        "ids_todos": [i for i in ids if isinstance(i, str)] if isinstance(ids, list) else [],
        "razao": _texto(content.get("razao"))}
