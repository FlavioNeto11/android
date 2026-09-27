"""Casar um comando com um modelo `{parâmetro}`: a MESMA semântica do fluxo legado, em código puro.

`match_key` e a extração de valores precisam ser idênticos aos de `taskqueue/flows.py` (`_norm`, `_extract`,
`_squash`): um fluxo adotado como habilidade continua casando exatamente com os mesmos comandos, e a unicidade de
comando entre `skill_versions` e `flows` compara chaves normalizadas do mesmo jeito. Esta é a cópia pura (o domínio
não vê `app.taskqueue`); `tests/test_habilidades_dominio.py` confere, por uma tabela de casos, que as duas dão a
mesma resposta. Na fase I, `FlowStore` passa a delegar para cá e a cópia some.
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


def extract_parameters(template: str, command: str) -> dict[str, str] | None:
    """Os valores que o comando dá a cada `{nome}` do modelo, ou `None` se o comando não casa com o modelo."""
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
            pattern += f"(?P<{m.group(1)}>.+?)"
        pos = m.end()
    pattern += re.escape(_squash(template[pos:]))
    got = re.fullmatch(pattern, _squash(command), flags=re.IGNORECASE | re.DOTALL)
    if got is None:
        return None
    values = {k: v.strip() for k, v in got.groupdict().items() if isinstance(v, str)}
    if len(values) != len(names):
        return None
    return values if all(values.values()) and all(len(v) <= MAX_VALUE_LEN for v in values.values()) else None


def bind_template_parameters(parameters: Mapping[str, str], values: Mapping[str, str]) -> dict[str, str] | None:
    """Parâmetros de um plano-modelo (`{"perfil": "{perfil}", "fixo": "x"}`) com os valores do comando.

    `None` quando algum parâmetro-modelo ficou sem valor: como em `FlowStore.match`, esse modelo NÃO é o do comando.
    """
    ligados = {k: (values.get(k, v) if v == "{" + k + "}" else v) for k, v in parameters.items()}
    if any(v == "{" + k + "}" for k, v in ligados.items()):
        return None
    return ligados


def specificity(template: str) -> tuple[int, int]:
    """Ordem entre modelos publicados que casam o mesmo comando: mais texto fixo primeiro, depois menos buracos.

    `skill_versions` não tem o `uses` que ordena os fluxos (`ORDER BY uses DESC`); um contador de uso mudaria a
    resposta com o tempo. Aqui a resposta é a mesma sempre: "curtir o post de {perfil}" ganha de "curtir {x}".
    """
    fixo = PLACEHOLDER.sub("", template)
    return (len(_squash(fixo)), -len(PLACEHOLDER.findall(template)))
