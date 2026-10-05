"""31.87 F2: o ensino usa os dados da persona (decisão do dono, 05/10 15:13Z: todos os campos de perfil viram variável;
senha, código e 2FA seguem só pelo cofre, e nunca chegam aqui).

A pessoa demonstra digitando o e-mail DA PERSONA que ensinou ("ana@exemplo.test"). Sem isto, o valor virava parâmetro do
comando (`{email}`, que quem pede tem de dizer) ou ficava literal na receita, que digitaria o e-mail da Ana em todos os
aparelhos. Com isto, o valor vira o marcador da persona (`{perfil_email}`): o texto das etapas se resolve por aparelho
na materialização (ADR-040), e a receita digita o dado da persona de cada aparelho na reprodução.

O consumo é GENÉRICO por chave: as variáveis vêm de `profile_variables` (identidade, só não sigilosos por construção) e
qualquer chave nova que a identidade publicar entra sem mudar o ensino. Só funções puras: quem chama lê a persona.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

#: Abaixo disto o valor casaria com pedaço de outra palavra ("Ana" em "banana"): o mesmo piso de `learn_from_run`.
MINIMO = 3
_CAMPOS_DE_TEXTO = ("title", "goal", "precondition")


def _palavra(valor: str) -> re.Pattern[str]:
    """O valor como palavra inteira: nem letra nem dígito colado antes ou depois."""
    return re.compile(r"(?<![\w@.])" + re.escape(valor) + r"(?![\w@])")


def demonstrados(persona: Mapping[str, str], entradas: Iterable[Mapping[str, object]]) -> dict[str, str]:
    """Os dados da persona que a pessoa DIGITOU na demonstração, cada um como uma entrada INTEIRA (o campo recebeu
    exatamente o valor). Valor dentro de outro texto não conta: "centro" no endereço, ou um nome curto numa frase,
    seriam trocados onde não deviam (leitura da Jev); na dúvida, fica como a pessoa escreveu."""
    textos = {str(e.get("text") or "").strip().casefold() for e in entradas if e.get("type") == "text"}
    return {nome: valor for nome, valor in persona.items()
            if isinstance(valor, str) and len(valor.strip()) >= MINIMO and valor.strip().casefold() in textos}


def _trocar(texto: str, trocas: Mapping[str, str]) -> str:
    """`trocas`: valor ou `{param}` → `{marcador}`; o mais longo primeiro, para um valor não comer o outro."""
    for de, para in sorted(trocas.items(), key=lambda kv: -len(kv[0])):
        texto = texto.replace(de, para) if de.startswith("{") else _palavra(de).sub(para, texto)
    return texto


def _no_passo(st: Mapping[str, object], trocas: Mapping[str, str]) -> dict[str, object]:
    novo = dict(st)
    for campo in _CAMPOS_DE_TEXTO:
        texto = novo.get(campo)
        if isinstance(texto, str):
            novo[campo] = _trocar(texto, trocas)
    post = novo.get("postcondition")
    if isinstance(post, dict):
        post = dict(post)
        for campo in ("value", "description"):
            texto = post.get(campo)
            if isinstance(texto, str):
                post[campo] = _trocar(texto, trocas)
        novo["postcondition"] = post
    ligacoes = novo.get("bindings")
    if isinstance(ligacoes, list):
        novo["bindings"] = [{**b, "value": _trocar(b["value"], trocas)}
                            if isinstance(b, dict) and isinstance(b.get("value"), str) else b
                            for b in ligacoes]
    return novo


def _lista(valor: object) -> list[object]:
    return list(valor) if isinstance(valor, list) else []


def na_proposta(p: Mapping[str, object], persona: Mapping[str, str]) -> tuple[dict[str, object], list[str]]:
    """A proposta com os dados da persona `persona` (já filtrados por `demonstrados`) trocados pelos marcadores, e os
    marcadores que ficaram nas etapas. Dois casos:
    - o parâmetro do comando cujo exemplo É o dado da persona sai do comando e da lista, e `{param}` vira o marcador
      nas etapas (quem pede não precisa dizer o próprio e-mail da persona);
    - o valor literal da persona escrito numa etapa vira o marcador.
    O parâmetro de verdade (outro valor) não muda; sem persona, a proposta volta igual."""
    if not persona:
        return dict(p), []
    por_valor = {valor.strip().casefold(): nome for nome, valor in persona.items()}
    trocas: dict[str, str] = {}
    params: list[object] = []
    comando = str(p.get("command_template") or "")
    for x in _lista(p.get("parameters")):
        nome = x.get("name") if isinstance(x, dict) else None
        exemplo = x.get("example") if isinstance(x, dict) else None
        marcador = por_valor.get(str(exemplo or "").strip().casefold()) if nome else None
        if marcador is None:
            params.append(x)
            continue
        trocas["{" + str(nome) + "}"] = "{" + marcador + "}"
        comando = re.sub(r"\s*\{" + re.escape(str(nome)) + r"\}", "", comando)
    for nome, valor in persona.items():
        trocas.setdefault(valor, "{" + nome + "}")
    passos = [_no_passo(st, trocas) if isinstance(st, dict) else st for st in _lista(p.get("steps"))]
    novo: dict[str, object] = {**p, "parameters": params, "steps": passos}
    if "command_template" in p:
        novo["command_template"] = re.sub(r"\s{2,}", " ", comando).strip()
    usados = sorted(nome for nome in persona if "{" + nome + "}" in repr(passos))
    return novo, usados


def aviso(usados: Iterable[str]) -> list[str]:
    """A linha que o painel mostra no salvar e na prévia: o que vem do perfil da persona de cada aparelho."""
    marcadores = ", ".join("{" + n + "}" for n in usados)
    return [f"{marcadores}: vem do perfil da persona de cada aparelho (o aparelho sem esse dado não roda o fluxo)."
            ] if marcadores else []


__all__ = ["MINIMO", "aviso", "demonstrados", "na_proposta"]
