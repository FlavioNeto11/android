"""Casar um comando com um modelo `{parâmetro}`: a MESMA semântica do fluxo legado, em código puro.

`match_key` e a extração de valores precisam ser idênticos aos de `taskqueue/flows.py` (`_norm`, `_extract`,
`_squash`): um fluxo adotado como habilidade continua casando exatamente com os mesmos comandos, e a unicidade de
comando entre `skill_versions` e `flows` compara chaves normalizadas do mesmo jeito. Esta é a cópia pura (o domínio
não vê `app.taskqueue`); `tests/test_habilidades_dominio.py` confere, por uma tabela de casos, que as duas dão a
mesma resposta. `FlowStore` delegar para cá, para a cópia sumir, continua proposto: a fase I só acrescentou
`extract_with_gaps` (o comando com um buraco vazio), que o fluxo legado não usa.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping

#: Nomes que são texto literal do comando, nunca parâmetro (`flows.py:18`).
RESERVED = frozenset({"instance_id", "run_id", "account_label"})
PLACEHOLDER = re.compile(r"\{([a-z_][a-z0-9_]*)\}")
#: Teto do valor capturado (`flows.py:175`): um "valor" maior que isso é o comando inteiro caindo num buraco.
MAX_VALUE_LEN = 500


def normalize_command(text: str) -> str:
    """A chave de casamento (`flows.match_key`): NFKC, espaços colapsados, sem caixa."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip().casefold()


def _squash(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(str.maketrans("“”„‟‘’", "\"\"\"\"''"))
    return re.sub(r"\s+", " ", text).strip()


def _captured(template: str, command: str, *, hole: str) -> dict[str, str] | None:
    """O texto que cada `{nome}` do modelo cobre no comando, já sem as bordas, ou `None` se o texto fixo não casa.

    O texto fixo passa por `_squash` com as bordas cortadas: o espaço encostado num buraco entra na captura e sai no
    `strip` — é isso que faz "com  @ana" e "com @ana" darem o mesmo valor.
    """
    names: list[str] = []
    pattern = ""
    pos = 0
    for m in PLACEHOLDER.finditer(template):
        pattern += re.escape(_squash(template[pos:m.start()]))
        if m.group(1) in RESERVED:
            pattern += re.escape(m.group(0))          # {instance_id}/{run_id} são texto literal do comando
        elif m.group(1) in names:
            pattern += f"(?P={m.group(1)})"
        else:
            names.append(m.group(1))
            pattern += f"(?P<{m.group(1)}>{hole})"
        pos = m.end()
    pattern += re.escape(_squash(template[pos:]))
    got = re.fullmatch(pattern, _squash(command), flags=re.IGNORECASE | re.DOTALL)
    if got is None:
        return None
    values = {k: v.strip() for k, v in got.groupdict().items() if isinstance(v, str)}
    return values if len(values) == len(names) else None


def extract_parameters(template: str, command: str) -> dict[str, str] | None:
    """Os valores que o comando dá a cada `{nome}` do modelo, ou `None` se o comando não casa com o modelo."""
    values = _captured(template, command, hole=".+?")
    if values is None:
        return None
    return values if all(values.values()) and all(len(v) <= MAX_VALUE_LEN for v in values.values()) else None


def extract_with_gaps(template: str, command: str) -> tuple[dict[str, str], tuple[str, ...]] | None:
    """O comando é o modelo com algum `{nome}` VAZIO ("abra a conversa com no instagram", "curtir")?

    Devolve `(valores dados, nomes vazios)`, ou `None` quando não é esse o caso: o texto fixo não casa nem assim, ou
    todos os buracos têm valor (aí quem responde é `extract_parameters`, que casa ou diz por que não). Um valor
    acima do teto também é `None`: é o comando inteiro caindo num buraco, não um buraco vazio.

    Serve à RESOLVE para PERGUNTAR o parâmetro que falta em vez de mandar o comando ao planejador (fase I). Nunca
    casa sozinho: quem recebe um comando com buraco vazio devolve uma pergunta, jamais um plano.
    """
    values = _captured(template, command, hole=".*?")
    if values is None or any(len(v) > MAX_VALUE_LEN for v in values.values()):
        return None
    vazios = tuple(k for k, v in values.items() if not v)
    if not vazios:
        return None
    return {k: v for k, v in values.items() if v}, vazios


def bind_template_parameters(parameters: Mapping[str, str], values: Mapping[str, str]) -> dict[str, str] | None:
    """Parâmetros de um plano-modelo (`{"perfil": "{perfil}", "fixo": "x"}`) com os valores do comando.

    `None` quando algum parâmetro-modelo ficou sem valor: como em `FlowStore.match`, esse modelo NÃO é o do comando.
    Os RESERVED (`account_label`, `instance_id`, `run_id`) não contam: nunca vêm do comando (`_captured` os trata
    como texto literal), o valor é do APARELHO e entra na materialização (`Repository.materialize`). O molde fica
    como está, igual ao `FlowStore.match` desde o LT-3. Exigir o valor aqui deixava morto todo fluxo cujo plano
    declara `{account_label}` (30.29: 5 fluxos ativos do QA, 0 usos), e o comando que casava com um deles pelo
    caminho da habilidade ia a `needs_input` sem o planejador.
    """
    ligados = {k: (values.get(k, v) if v == "{" + k + "}" else v) for k, v in parameters.items()}
    if any(v == "{" + k + "}" for k, v in ligados.items() if k not in RESERVED):
        return None
    return ligados


def specificity(template: str) -> tuple[int, int]:
    """Ordem entre modelos publicados que casam o mesmo comando: mais texto fixo primeiro, depois menos buracos.

    `skill_versions` não tem o `uses` que ordena os fluxos (`ORDER BY uses DESC`); um contador de uso mudaria a
    resposta com o tempo. Aqui a resposta é a mesma sempre: "curtir o post de {perfil}" ganha de "curtir {x}".
    """
    fixo = PLACEHOLDER.sub("", template)
    return (len(_squash(fixo)), -len(PLACEHOLDER.findall(template)))
