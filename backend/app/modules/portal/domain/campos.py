"""Os campos do formulário de contato do site (29.77, ADR-075): tetos, normalização e validação. Puro.

Os tetos repetem os `maxlength` de `site/index.html` e o contrato da Canais (`portal.contato`), que valida de novo.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping

#: O mesmo padrão de `app.config.TELEFONE_PUBLICO` e do `TELEFONE_VALIDO` de `site/assets/site.js`. Repetido aqui de
#: propósito: o domínio não importa `app.config` (que vai no agente do worker), e há teste que confere os três iguais.
TELEFONE = re.compile(r"^[0-9+()\- ]{8,30}$")

TETOS: Mapping[str, int] = {"nome": 80, "empresa": 80, "telefone": 30, "mensagem": 1500}
OBRIGATORIOS = ("nome", "telefone", "mensagem")


def limpo(valor: object, *, linhas: bool = False) -> str | None:
    """Texto do visitante normalizado (NFC, sem caractere de controle, sem espaço nas pontas). `None` quando não é
    texto; ausente vira vazio. Quebra de linha só sobrevive onde `linhas` (a mensagem)."""
    if valor is None:
        return ""
    if not isinstance(valor, str):
        return None
    texto = unicodedata.normalize("NFC", valor).replace("\r\n", "\n").replace("\r", "\n")
    permitido = "\n" if linhas else ""
    return "".join(c for c in texto if c in permitido or unicodedata.category(c)[0] != "C").strip()


def validar(dados: Mapping[str, object]) -> tuple[dict[str, str], list[str]]:
    """Os campos limpos e a lista dos inválidos (vazia quando tudo serve)."""
    campos: dict[str, str] = {}
    invalidos: list[str] = []
    for nome, teto in TETOS.items():
        valor = limpo(dados.get(nome), linhas=nome == "mensagem")
        if valor is None or len(valor) > teto:
            invalidos.append(nome)
        else:
            campos[nome] = valor
    invalidos += [nome for nome in OBRIGATORIOS if nome not in invalidos and not campos.get(nome)]
    telefone = campos.get("telefone", "")
    if "telefone" not in invalidos and (not TELEFONE.match(telefone) or len(re.sub(r"\D", "", telefone)) < 8):
        invalidos.append("telefone")
    return campos, invalidos
