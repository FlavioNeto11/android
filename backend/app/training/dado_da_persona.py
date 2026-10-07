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
#: A palavra de ligação que sai do comando junto com o parâmetro ("entre com {email} e…" → "entre e…"; N1 da leitura).
#: Lista fechada: outra palavra antes do parâmetro fica.
_LIGACOES = ("com", "de", "do", "da", "para", "pra", "em", "no", "na", "por", "pelo", "pela", "ao", "à", "a", "o")


def _palavra(valor: str) -> re.Pattern[str]:
    """O valor como palavra inteira: nem letra nem dígito colado antes ou depois."""
    return re.compile(r"(?<![\w@.])" + re.escape(valor) + r"(?![\w@])")


def demonstrados(persona: Mapping[str, str], entradas: Iterable[Mapping[str, object]]) -> dict[str, str]:
    """Os dados da persona que a pessoa DIGITOU na demonstração, cada um como uma entrada INTEIRA (o campo recebeu
    exatamente o valor). Valor dentro de outro texto não conta: "centro" no endereço, ou um nome curto numa frase,
    seriam trocados onde não deviam (leitura da Jev); na dúvida, fica como a pessoa escreveu. A entrada que o `save` já
    marcou (31.118, `{perfil_…}` inteiro) conta como o dado daquele marcador."""
    textos = {str(e.get("text") or "").strip().casefold() for e in com_valores(entradas, persona) if e.get("type") == "text"}
    return {nome: valor for nome, valor in persona.items()
            if isinstance(valor, str) and len(valor.strip()) >= MINIMO and valor.strip().casefold() in textos}


def _trocar(texto: str, trocas: Mapping[str, str]) -> str:
    """`trocas`: valor ou `{param}` → `{marcador}`; o mais longo primeiro, para um valor não comer o outro. Troca o valor
    como PALAVRA dentro do texto: só para o que descreve a etapa (título, objetivo, descrição)."""
    for de, para in sorted(trocas.items(), key=lambda kv: -len(kv[0])):
        texto = texto.replace(de, para) if de.startswith("{") else _palavra(de).sub(para, texto)
    return texto


def _trocar_o_campo_inteiro(texto: str, trocas: Mapping[str, str]) -> str:
    """O que a etapa DIGITA ou CONFERE (`bindings[].value`, `postcondition.value`): o `{param}` vira o marcador em
    qualquer lugar, mas o valor literal só quando é o campo INTEIRO (casefold). Dentro de uma frase ("Oi Ana, tudo bem?"
    para uma destinatária homônima, "conversa com Ana aberta") ele fica literal: trocar mandaria a terceiros o nome da
    persona de cada aparelho, e a conferência olharia o nome errado (C1 da leitura do 31.87 F2)."""
    for de, para in sorted(trocas.items(), key=lambda kv: -len(kv[0])):
        if de.startswith("{"):
            texto = texto.replace(de, para)
        elif texto.strip().casefold() == de.strip().casefold():
            return para
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
                post[campo] = (_trocar_o_campo_inteiro if campo == "value" else _trocar)(texto, trocas)
        novo["postcondition"] = post
    ligacoes = novo.get("bindings")
    if isinstance(ligacoes, list):
        novo["bindings"] = [{**b, "value": _trocar_o_campo_inteiro(b["value"], trocas)}
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
        comando = re.sub(r"(?:\s+(?:" + "|".join(_LIGACOES) + r"))?\s*\{" + re.escape(str(nome)) + r"\}", "", comando,
                         flags=re.IGNORECASE)
    for nome, valor in persona.items():
        trocas.setdefault(valor, "{" + nome + "}")
    passos = [_no_passo(st, trocas) if isinstance(st, dict) else st for st in _lista(p.get("steps"))]
    novo: dict[str, object] = {**p, "parameters": params, "steps": passos}
    if "command_template" in p:
        novo["command_template"] = re.sub(r"\s{2,}", " ", comando).strip()
    usados = sorted(nome for nome in persona if "{" + nome + "}" in repr(passos))
    return novo, usados


def nas_perguntas(p: Mapping[str, object], persona: Mapping[str, str]) -> dict[str, object]:
    """31.112: as perguntas da IA (`questions[]`) e a pergunta de cada resposta guardada (`answers[].question`, 31.91)
    com o dado da persona trocado pelo mesmo marcador da proposta. A pergunta é texto que DESCREVE, então troca por
    palavra, como o título. A resposta da pessoa (`answer`) fica como ela escreveu. Serve à proposta e ao corpo do
    `propose`: a pergunta que o cliente devolve casa com a guardada nos dois jeitos, com o valor ou com o marcador."""
    if not persona:
        return dict(p)
    trocas = {valor: "{" + nome + "}" for nome, valor in persona.items()}
    novo = dict(p)
    if isinstance(p.get("questions"), list):
        novo["questions"] = [_trocar(q, trocas) if isinstance(q, str) else q for q in _lista(p.get("questions"))]
    if isinstance(p.get("answers"), list):
        novo["answers"] = [{**r, "question": _trocar(r["question"], trocas)}
                           if isinstance(r, dict) and isinstance(r.get("question"), str) else r
                           for r in _lista(p.get("answers"))]
    return novo


def marcas_das_entradas(entradas: Iterable[Mapping[str, object]], ligados: Mapping[str, str]) -> list[tuple[int, str]]:
    """31.118: `(seq, marcador)` de cada entrada de texto que o `save` troca na gravação. `ligados`: `{nome: valor}` dos
    dados da persona que a habilidade salva usa (o marcador está no plano). Só o campo INTEIRO (casefold), a mesma régua
    de `demonstrados`: o texto que só contém o dado fica como a pessoa escreveu."""
    por_valor = {str(v).strip().casefold(): "{" + n + "}" for n, v in ligados.items()
                 if isinstance(v, str) and len(v.strip()) >= MINIMO}
    marcas: list[tuple[int, str]] = []
    for e in entradas:
        marca = por_valor.get(str(e.get("text") or "").strip().casefold()) if e.get("type") == "text" else None
        seq = e.get("seq")
        if marca is not None and isinstance(seq, int):
            marcas.append((seq, marca))
    return marcas


def com_valores(entradas: Iterable[Mapping[str, object]], persona: Mapping[str, str]) -> list[dict[str, object]]:
    """31.118: as entradas com o marcador da gravação salva (`{perfil_…}` no campo inteiro) trocado pelo valor da
    persona, SÓ em memória, para quem precisa do que foi digitado (refazer as receitas, mascarar as perguntas). Sem o
    dado na persona, a entrada fica com o marcador."""
    saida: list[dict[str, object]] = []
    for e in entradas:
        texto = e.get("text")
        nome = texto.strip()[1:-1] if isinstance(texto, str) and re.fullmatch(r"\{\w+\}", texto.strip()) else None
        valor = persona.get(nome) if nome else None
        saida.append({**e, "text": valor} if e.get("type") == "text" and isinstance(valor, str) and valor.strip()
                     else dict(e))
    return saida


def com_marcador(texto: str, persona: Mapping[str, str]) -> str:
    """31.122: o texto que DESCREVE (aviso, recusa) com cada dado da persona trocado pelo marcador, por palavra, como o
    título da etapa. Valor curto (menos de `MINIMO`) não é trocado."""
    return _trocar(texto, {v: "{" + n + "}" for n, v in persona.items()
                           if isinstance(v, str) and len(v.strip()) >= MINIMO})


def _na_leitura(texto: str, persona: Mapping[str, str]) -> str:
    """O texto mostrado com o dado da persona trocado pelo marcador: como `com_marcador`, mas sem diferença de caixa e
    também logo depois de um @ (o "@ana_souza" do resultado de busca tocado), e o espaço do valor casa com qualquer
    espaço (o NBSP e a quebra de linha do `content_desc`). Só para a LEITURA: nada que a etapa digite ou confira passa
    por aqui. Limite conhecido: casa o valor INTEIRO; o primeiro nome sozinho de "Ana Lopes", ou o telefone em outro
    formato, fica."""
    for nome, valor in sorted(persona.items(), key=lambda kv: -len(kv[1].strip())):
        v = valor.strip().lstrip("@")
        if len(v) >= MINIMO:
            padrao = r"\s+".join(re.escape(parte) for parte in v.split())
            texto = re.sub(r"(?<![\w.])" + padrao + r"(?![\w@])", "{" + nome + "}", texto, flags=re.IGNORECASE)
    return texto


#: O que a leitura da gravação mostra de cada entrada e pode trazer o dado da persona (o texto do elemento tocado, a
#: descrição dele, o título e as linhas da tela, o texto digitado).
_CAMPOS_DO_ALVO = ("text", "desc", "content_desc", "hint", "label")


def _no_alvo(alvo: Mapping[str, object], persona: Mapping[str, str]) -> dict[str, object]:
    """O alvo tocado e os `filhos` dele (o @ e o nome de uma linha de resultado costumam estar no filho)."""
    d = {k: _na_leitura(v, persona) if k in _CAMPOS_DO_ALVO and isinstance(v, str) else v for k, v in alvo.items()}
    if isinstance(alvo.get("filhos"), list):
        d["filhos"] = [_no_alvo(f, persona) if isinstance(f, dict) else f for f in _lista(alvo.get("filhos"))]
    return d


def na_gravacao(entradas: Iterable[Mapping[str, object]], persona: Mapping[str, str]) -> list[dict[str, object]]:
    """K-pendente do 31.160: a gravação crua com o dado da persona trocado pelo marcador, para o GET da sessão. O painel
    não precisa do valor, e a gravação do ensino com o alvo = o perfil da própria persona devolvia o @ e o nome dela no
    `target.text`, no `target.desc` ("Photo by …") e nas linhas da tela. Por palavra, sem diferença de caixa, com TODOS
    os dados da persona (não só os digitados): é leitura, e trocar a mais só esconde uma palavra. A destilação lê a
    gravação crua (`TrainingRecorder.get(..., crua=True)`)."""
    persona = {n: v for n, v in persona.items() if isinstance(v, str) and len(v.strip().lstrip("@")) >= MINIMO}
    saida: list[dict[str, object]] = []
    for e in entradas:
        d = dict(e)
        if persona:
            alvo = d.get("target")
            if isinstance(alvo, dict):
                d["target"] = _no_alvo(alvo, persona)
            d["screen_lines"] = [_na_leitura(x, persona) if isinstance(x, str) else x
                                 for x in _lista(d.get("screen_lines"))]
            for campo in ("screen_title", "text"):
                if isinstance(d.get(campo), str):
                    d[campo] = _na_leitura(str(d[campo]), persona)
        saida.append(d)
    return saida


def parametros_da_persona(p: Mapping[str, object], persona: Mapping[str, str]) -> list[tuple[str, str]]:
    """`(parâmetro, marcador)` de cada parâmetro do comando que `na_proposta` tira porque o exemplo É o dado da persona
    que ensina (K-pendente do 31.160): a etapa passa a mirar o dado de cada persona, não um alvo dito no pedido."""
    por_valor = {valor.strip().casefold(): nome for nome, valor in persona.items()}
    saida = []
    for x in _lista(p.get("parameters")):
        if isinstance(x, dict) and x.get("name"):
            marcador = por_valor.get(str(x.get("example") or "").strip().casefold())
            if marcador is not None:
                saida.append((str(x["name"]), marcador))
    return saida


def aviso_dos_parametros(trocados: Iterable[tuple[str, str]]) -> list[str]:
    """A linha da prévia e do salvar para cada parâmetro que saiu do comando por ser o dado da própria persona."""
    return [f"{{{nome}}} saiu do comando: o exemplo é o dado da própria persona que ensinou, e a etapa vai usar "
            f"{{{marcador}}} de cada aparelho. Para um alvo dito no pedido, ensine com um exemplo que não seja a persona."
            for nome, marcador in trocados]


def aviso(usados: Iterable[str]) -> list[str]:
    """A linha que o painel mostra no salvar e na prévia: o que vem do perfil da persona de cada aparelho."""
    marcadores = ", ".join("{" + n + "}" for n in usados)
    return [f"{marcadores}: vem do perfil da persona de cada aparelho (o aparelho sem esse dado não roda o fluxo)."
            ] if marcadores else []


__all__ = ["MINIMO", "aviso", "aviso_dos_parametros", "com_marcador", "com_valores", "demonstrados", "marcas_das_entradas",
           "na_gravacao", "na_proposta", "nas_perguntas", "parametros_da_persona"]
