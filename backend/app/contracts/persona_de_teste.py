"""Persona de TESTE (item 31.314): uma pessoa marcada (`instagram_profiles.teste = 1`, migração 134) que existe para
provar o produto e nunca para trabalhar.

Fica fora do que é AUTOMÁTICO ou em MASSA: a sugestão de alvos do comando (31.274), a distribuição por aparelho, as operações
em lote, as contagens do painel e os avisos ao dono (Telegram e Trello). Citada PELO NOME ou pelo id, num comando ou numa
execução avulsa, ela serve como qualquer outra: o teste é feito de propósito, por uma pessoa.

Só texto de SQL e a regra de leitura, sem banco (a camada `contracts` não importa ninguém). Quem consulta usa o fragmento no
seu próprio SQL, para a regra morar em UM lugar.
"""
from __future__ import annotations

from collections.abc import Mapping

#: A coluna, em `instagram_profiles`.
COLUNA = "teste"


def sem_teste(alias: str = "") -> str:
    """Fragmento de `WHERE`: a linha de `instagram_profiles` (com o `alias` da tabela, se houver) NÃO é de teste."""
    prefixo = f"{alias}." if alias else ""
    return f"COALESCE({prefixo}{COLUNA}, 0) = 0"


def so_teste(alias: str = "") -> str:
    prefixo = f"{alias}." if alias else ""
    return f"COALESCE({prefixo}{COLUNA}, 0) = 1"


#: Os ids das personas de teste (uma consulta pequena, sem parâmetro).
SQL_IDS = f"SELECT id FROM instagram_profiles WHERE {so_teste()}"


def e_de_teste(linha: Mapping[str, object] | None) -> bool:
    """A linha de `instagram_profiles` (ou o que a represente) é de teste? Linha ausente ou sem a coluna: não."""
    return bool(linha is not None and linha.get(COLUNA))
