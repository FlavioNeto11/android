"""A pessoa por trás de uma conta: regras puras sobre nome, idade, biografia e a mescla de um PATCH por seção.

Desde a migração 047 a persona É a linha de `instagram_profiles` (evolução 2, onda A). O que fica aqui não lê banco
nem conhece Pydantic: são as contas que o serviço, o construtor de contexto e a receita de imagem precisam fazer
iguais — idade a partir do nascimento, nome partido no primeiro espaço, o que falta para uma biografia contar como
completa, e a mescla "chave a chave" que faz um PATCH parcial nunca apagar o que não foi mencionado.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import date

#: Abaixo disto nenhuma persona é aceita — nem gerada, nem enriquecida, nem fotografada.
MAIORIDADE = 18
#: Versão do JSON `biography`; muda quando uma seção nasce ou some.
BIOGRAPHY_SCHEMA_VERSION = 1
#: As três chaves que moravam em `traits` antes da 047 e hoje vivem em `visual`. Um cliente antigo (o painel de
#: hoje) ainda as manda dentro de `traits`; o servidor as separa em vez de recusar.
CHAVES_VISUAIS_LEGADAS: tuple[str, ...] = ("appearance", "visual_style", "photo_scenario")
#: O mínimo para uma biografia contar como COMPLETA (critério de `POST /personas/generate` e de `enrich`): de onde a
#: pessoa é, onde mora, o que faz, o que estudou e o que gosta de fazer. Religião e política ficam de fora de
#: propósito (`beliefs` é guardado, não exigido nem enviado ao modelo — decisão do dono pendente).
BIOGRAFIA_MINIMA: tuple[str, ...] = ("origin.birthplace", "home.city", "work.profession", "work.education",
                                     "tastes.hobbies")

_DATA_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_PALAVRA_DE_NOME = re.compile(r"^[^\W\d_](?:[^\W\d_]|['’-])*$", re.UNICODE)


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
