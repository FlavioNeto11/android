"""31.113 F1: o registro da execução guarda o marcador da persona (`{perfil_nome_exibicao}`), não o valor.

Achado da prova real do 31.87 (06/10): a reprodução levou o nome da persona à tela, como devia, mas o valor ficou em
claro em `events`, `actions`, `attempts.error`, `evidence.note` e `steps.status_detail`. A troca é a inversa da
materialização: valor → marcador, na FRONTEIRA de escrita (o executor segue com o valor em memória, e a tela não muda).
Camada irmã de `redact` e de `enderecos_limpos`, com o mapa por objetivo (quem o monta é o `Repository`).

Só funções puras. As regras são as do ensino (31.87 F2): o valor mais longo primeiro (o nome de exibição contém o
primeiro nome e o sobrenome), no mínimo 3 caracteres, como palavra inteira, sem diferença de maiúscula.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

#: Abaixo disto o valor casaria com pedaço de outra palavra ("Ana" em "banana"): o mesmo piso do ensino.
MINIMO = 3
#: Os dados que identificam a persona e entram sempre no mapa; os outros (cidade, idioma, gênero…) só quando o plano
#: os cita, senão "Brasil" ou "feminino" sumiriam de todo texto da execução.
IDENTIFICAM = ("perfil_nome", "perfil_sobrenome", "perfil_nome_exibicao", "perfil_email", "perfil_nascimento")


def mapa(variaveis: Mapping[str, str], citados: Iterable[str], parametros: Mapping[str, object]) -> dict[str, str]:
    """`{valor: "{nome}"}` do objetivo: os dados que identificam a persona e os que o plano cita. O valor igual a um
    parâmetro do comando fica de fora: o parâmetro vence a persona no mesmo valor (regra do F2), e a receita aprendida
    da execução continua guardando `{param}`."""
    dos_parametros = [str(v) for v in parametros.values() if isinstance(v, str)]
    saida: dict[str, str] = {}
    for nome in dict.fromkeys((*IDENTIFICAM, *citados)):
        valor = str(variaveis.get(nome) or "").strip()
        # Dentro do parâmetro também vence: "Zelda Sintetica" no parâmetro não vira "{perfil_nome} {perfil_sobrenome}"
        if len(valor) >= MINIMO and not any(_palavra(valor).search(x) for x in dos_parametros):
            saida.setdefault(valor, "{" + nome + "}")
    return saida


def digitado(texto: object, variaveis: Mapping[str, str], parametros: Mapping[str, object]) -> tuple[str, str] | None:
    """O dado da persona que o ator digitou INTEIRO (`type_text.args.text` igual ao valor), de qualquer chave da
    persona: `(valor, marcador)`, para o mapa do objetivo aprender. Fora disso, `None`."""
    if not isinstance(texto, str) or len(texto.strip()) < MINIMO:
        return None
    alvo = texto.strip().casefold()
    if alvo in {str(v).strip().casefold() for v in parametros.values() if isinstance(v, str)}:
        return None
    return next(((v.strip(), "{" + n + "}") for n, v in variaveis.items()
                 if isinstance(v, str) and v.strip().casefold() == alvo), None)


def _palavra(valor: str) -> re.Pattern[str]:
    """O valor como palavra inteira (a borda do ensino), sem diferença de maiúscula."""
    return re.compile(r"(?<![\w@.])" + re.escape(valor) + r"(?![\w@])", re.IGNORECASE)


def no_texto(texto: str | None, trocas: Mapping[str, str]) -> str | None:
    if not texto or not trocas:
        return texto
    for valor, marcador in sorted(trocas.items(), key=lambda kv: -len(kv[0])):
        texto = _palavra(valor).sub(marcador, texto)
    return texto


def no_objeto(obj: object, trocas: Mapping[str, str]) -> object:
    """O mesmo em toda string de um JSON (dict, lista); as chaves ficam."""
    if not trocas:
        return obj
    if isinstance(obj, str):
        return no_texto(obj, trocas)
    if isinstance(obj, dict):
        return {k: no_objeto(v, trocas) for k, v in obj.items()}
    if isinstance(obj, list):
        return [no_objeto(v, trocas) for v in obj]
    return obj


__all__ = ["IDENTIFICAM", "MINIMO", "digitado", "mapa", "no_objeto", "no_texto"]
