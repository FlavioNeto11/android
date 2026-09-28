"""A pessoa por trás de uma conta: regras puras sobre nome, idade, biografia e a mescla de um PATCH por seção.

Desde a migração 047 a persona É a linha de `instagram_profiles` (evolução 2, onda A). O que fica aqui não lê banco
nem conhece Pydantic: são as contas que o serviço, o construtor de contexto e a receita de imagem precisam fazer
iguais — idade a partir do nascimento, nome partido no primeiro espaço, o que falta para uma biografia contar como
completa, a mescla "chave a chave" que faz um PATCH parcial nunca apagar o que não foi mencionado, e a leitura de
uma biografia gravada numa versão anterior do JSON.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from datetime import date

#: Abaixo disto nenhuma persona é aceita — nem gerada, nem enriquecida, nem fotografada.
MAIORIDADE = 18
#: Versão do JSON `biography`; muda quando uma seção nasce, some ou muda de forma. v2 (ADR-048): `beliefs.religion`
#: e `beliefs.politics` deixaram de ser uma frase e viraram objetos ricos. A v1 é convertida NA LEITURA
#: (`normalizar_biografia`, sem migração SQL) e gravada na forma nova na próxima escrita da biografia.
BIOGRAPHY_SCHEMA_VERSION = 2
#: As três chaves que moravam em `traits` antes da 047 e hoje vivem em `visual`. Um cliente antigo (o painel de
#: hoje) ainda as manda dentro de `traits`; o servidor as separa em vez de recusar.
CHAVES_VISUAIS_LEGADAS: tuple[str, ...] = ("appearance", "visual_style", "photo_scenario")
#: O mínimo para uma biografia contar como COMPLETA (critério de `POST /personas/generate` e de `enrich`): de onde a
#: pessoa é, onde mora, o que faz, o que estudou e o que gosta de fazer. Crenças ficam de FORA de propósito (ADR-048):
#: vão ao modelo quando existem, mas uma persona sem religião ou política declarada continua uma pessoa completa.
BIOGRAFIA_MINIMA: tuple[str, ...] = ("origin.birthplace", "home.city", "work.profession", "work.education",
                                     "tastes.hobbies")
#: O que faz uma crença contar como PREENCHIDA para o enriquecimento (`enrich` completa quem não tem): a afiliação
#: da religião e a orientação política. Não entra em `BIOGRAFIA_MINIMA` — não é exigida para gerar nem para
#: completar; é o que o botão "completar com IA" também completa. Uma crença v1 que só virou `summary` na leitura
#: conta como lacuna, e o enriquecimento a detalha mantendo o resumo (`preencher_vazios`).
CRENCAS_MINIMAS: tuple[str, ...] = ("beliefs.religion.affiliation", "beliefs.politics.orientation")

#: A regra de CONDUTA sobre crenças, fonte única: vai ao bloco `<persona>` (sempre que há crença renderizada) e ao
#: prompt de geração. É o mesmo limite de sempre ("sem fake news, sem ofensa explícita", ADR-025/040) dito para o
#: tema: crença dá coerência de valores e de tom; não é pauta, nem campanha, nem licença para atacar alguém.
CONDUTA_DAS_CRENCAS = (
    "as crenças dão coerência aos valores, ao tom e às escolhas desta pessoa (o que ela aprova, o que evita, como "
    "reage a um tema); não são assunto a puxar. A persona não faz propaganda política nem religiosa, não pede voto "
    "nem adesão, não espalha desinformação e não ataca grupos nem pessoas por crença, ideologia ou identidade."
)

_DATA_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_PALAVRA_DE_NOME = re.compile(r"^[^\W\d_](?:[^\W\d_]|['’-])*$", re.UNICODE)
#: Uma crença v1 curta que PARECE nome de tradição ("católica", "sem religião", "espírita kardecista"): até duas
#: palavras só de letras. Conservador de propósito — "católica não praticante" ou "Testemunha de Jeová" ficam só
#: no resumo, que também vai ao modelo; adivinhar a afiliação a partir de uma frase seria inventar dado da pessoa.
_AFILIACAO_CURTA = re.compile(r"^[^\W\d_](?:[^\W\d_]|['’-])*(?: [^\W\d_](?:[^\W\d_]|['’-])*)?$", re.UNICODE)
#: A frase v1 de política só vira `orientation` quando é EXATAMENTE um ponto do espectro (sem acento, caixa ou
#: hífen). Qualquer outra coisa fica no resumo.
_ORIENTACAO_POR_TEXTO: dict[str, str] = {
    "esquerda": "esquerda", "centro esquerda": "centro_esquerda", "centro": "centro",
    "centro direita": "centro_direita", "direita": "direita", "apolitica": "apolitica", "apolitico": "apolitica",
    "nao declara": "nao_declara",
}


def idade_em(birth_date: str | None, hoje: date) -> int | None:
    """Idade completa em `hoje`, a partir de `YYYY-MM-DD`. Texto fora desse formato não é idade: `None`, nunca chute."""
    if not birth_date:
        return None
    m = _DATA_ISO.match(birth_date.strip())
    if m is None:
        return None
    try:
        nascimento = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None
    if nascimento > hoje:
        return None
    anos = hoje.year - nascimento.year
    if (hoje.month, hoje.day) < (nascimento.month, nascimento.day):
        anos -= 1
    return anos


def separar_nome(nome: str) -> tuple[str, str | None]:
    """`"Renata Vieira Lima"` → `("Renata", "Vieira Lima")`; um nome só fica sem sobrenome. A mesma regra da 047."""
    limpo = " ".join(nome.split())
    if " " not in limpo:
        return limpo, None
    primeiro, resto = limpo.split(" ", 1)
    return primeiro, resto or None


def nome_exibido(display_name: str | None, first_name: str | None, last_name: str | None,
                 username: str | None) -> str:
    """O nome pelo qual a pessoa aparece: o de exibição, senão nome e sobrenome, senão o usuário da conta."""
    if display_name and display_name.strip():
        return display_name.strip()
    juntos = " ".join(p for p in (first_name or "", last_name or "") if p.strip()).strip()
    return juntos or (username or "").strip()


def nome_ficticio_plausivel(nome: str) -> bool:
    """Um nome de pessoa: pelo menos duas palavras só de letras (acentos, hífen e apóstrofo valem), sem dígito.

    Não é detector de pessoa real — isso não existe de forma confiável e uma lista de sobrenomes famosos seria
    heurística frágil. É o que impede o modelo de devolver `"@lucas_99"`, um nome vazio ou um nome só.
    """
    palavras = nome.split()
    return len(palavras) >= 2 and all(len(p) >= 2 and _PALAVRA_DE_NOME.match(p) is not None for p in palavras)


def valor_no_caminho(dados: Mapping[str, object], caminho: str) -> object | None:
    """`valor_no_caminho({"home": {"city": "SP"}}, "home.city")` → `"SP"`; caminho ausente → `None`."""
    atual: object = dados
    for parte in caminho.split("."):
        if not isinstance(atual, Mapping):
            return None
        atual = atual.get(parte)
    return atual


def _vazio(valor: object) -> bool:
    return valor is None or valor == "" or valor == [] or valor == {}


def lacunas_da_biografia(biography: Mapping[str, object], minimo: Sequence[str] = BIOGRAFIA_MINIMA) -> list[str]:
    """Os caminhos de `BIOGRAFIA_MINIMA` ainda vazios, na ordem. Lista vazia = biografia completa."""
    return [caminho for caminho in minimo if _vazio(valor_no_caminho(biography, caminho))]


def vazio_profundo(valor: object) -> bool:
    """Nada a dizer: `None`, texto em branco, e lista ou objeto cujos itens são todos vazios. Um objeto de crença
    `{"practices": [], "summary": null}` é tão vazio quanto a chave ausente — não vira linha no prompt nem conta
    como crença preenchida."""
    if isinstance(valor, Mapping):
        return all(vazio_profundo(v) for v in valor.values())
    if isinstance(valor, (list, tuple)):
        return all(vazio_profundo(v) for v in valor)
    return valor is None or (isinstance(valor, str) and not valor.strip())


def _sem_acento(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))


def crenca_legada(texto: str, *, campo: str) -> dict[str, object] | None:
    """v1 → v2 de UMA crença: a frase antiga vira o `summary` do objeto novo (nada se perde). Além disso, só o que
    não é adivinhação: a religião curta com cara de nome de tradição vira também `affiliation`; a política que é
    exatamente um ponto do espectro vira também `orientation`. Texto em branco → `None` (sem crença)."""
    limpo = " ".join((texto or "").split())
    if not limpo:
        return None
    novo: dict[str, object] = {"summary": limpo[:400]}
    if campo == "religion" and len(limpo) <= 40 and _AFILIACAO_CURTA.match(limpo):
        novo["affiliation"] = limpo
    elif campo == "politics":
        chave = " ".join(_sem_acento(limpo).casefold().replace("-", " ").replace("_", " ").split())
        if (orientacao := _ORIENTACAO_POR_TEXTO.get(chave)) is not None:
            novo["orientation"] = orientacao
    return novo


def normalizar_biografia(dados: Mapping[str, object]) -> dict[str, object]:
    """Uma biografia gravada em qualquer versão conhecida, na forma ATUAL (`BIOGRAPHY_SCHEMA_VERSION`).

    v1 → v2: `beliefs.religion`/`beliefs.politics` em texto viram objetos (`crenca_legada`); o resto da v1 já é v2.
    Chamada na leitura (o validador de `PersonaBiography`) e antes da mescla de um PATCH (`update_persona`), que
    trabalha sobre o JSON cru: sem isto, mudar `religion.practice` numa linha v1 trocaria a frase antiga por um
    objeto só com a prática, e a frase se perderia. Versão desconhecida e mais nova fica como está.
    """
    saida = dict(dados)
    crencas = saida.get("beliefs")
    if isinstance(crencas, Mapping):
        novas = dict(crencas)
        for campo in ("religion", "politics"):
            valor = novas.get(campo)
            if isinstance(valor, str):
                convertido = crenca_legada(valor, campo=campo)
                if convertido is None:
                    novas.pop(campo)
                else:
                    novas[campo] = convertido
        saida["beliefs"] = novas
    versao = saida.get("schema_version")
    if isinstance(versao, int) and not isinstance(versao, bool) and versao < BIOGRAPHY_SCHEMA_VERSION:
        saida["schema_version"] = BIOGRAPHY_SCHEMA_VERSION
    return saida


def mesclar_secao(atual: Mapping[str, object], patch: Mapping[str, object]) -> dict[str, object]:
    """Mescla de PATCH por seção: só as chaves presentes em `patch` mudam.

    - dicionário dentro de dicionário mescla recursivamente (mudar `home.city` não apaga `home.state`);
    - `None` explícito APAGA a chave (é o único jeito de limpar um campo);
    - lista e escalar substituem inteiros — uma lista de hobbies mesclada elemento a elemento não teria semântica.
    """
    saida: dict[str, object] = dict(atual)
    for chave, novo in patch.items():
        if novo is None:
            saida.pop(chave, None)
            continue
        anterior = saida.get(chave)
        if isinstance(novo, Mapping) and isinstance(anterior, Mapping):
            saida[chave] = mesclar_secao(anterior, novo)
        elif isinstance(novo, Mapping):
            saida[chave] = mesclar_secao({}, novo)
        else:
            saida[chave] = novo
    return saida


def separar_visual_legado(traits: Mapping[str, object]) -> tuple[dict[str, object], dict[str, object]]:
    """`(voz, visual)`: tira de `traits` as chaves que hoje moram em `visual`, sem perder o valor delas."""
    voz = {k: v for k, v in traits.items() if k not in CHAVES_VISUAIS_LEGADAS}
    visual = {k: traits[k] for k in CHAVES_VISUAIS_LEGADAS if k in traits}
    return voz, visual
