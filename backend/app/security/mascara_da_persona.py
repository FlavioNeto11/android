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
#: 31.243: o usuário de cada conta da persona (`conta_<app>[_<host>]_usuario`, `_2` na colisão de host) também identifica
#: e entra sempre, com e sem a arroba (a borda de `_palavra` não casa depois de `@`, de propósito: ver `no_alvo`).
USUARIO_DA_CONTA = re.compile(r"^conta_\w+_usuario(?:_\d+)?$")


def mapa(variaveis: Mapping[str, str], citados: Iterable[str], parametros: Mapping[str, object]) -> dict[str, str]:
    """`{valor: "{nome}"}` do objetivo: os dados que identificam a persona e os que o plano cita. O valor igual a um
    parâmetro do comando fica de fora: o parâmetro vence a persona no mesmo valor (regra do F2), e a receita aprendida
    da execução continua guardando `{param}`."""
    dos_parametros = [str(v) for v in parametros.values() if isinstance(v, str)]
    contas = [n for n in variaveis if USUARIO_DA_CONTA.match(n)]
    saida: dict[str, str] = {}
    for nome in dict.fromkeys((*IDENTIFICAM, *contas, *citados)):
        conta = nome in contas
        valor = str(variaveis.get(nome) or "").strip()
        valor = valor.lstrip("@") if conta else valor
        # Dentro do parâmetro também vence: "Zelda Sintetica" no parâmetro não vira "{perfil_nome} {perfil_sobrenome}"
        if len(valor) >= MINIMO and not any(_palavra(valor).search(x.lstrip("@")) for x in dos_parametros):
            saida.setdefault(valor, "{" + nome + "}")
            if conta:
                saida.setdefault("@" + valor, "@{" + nome + "}")
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


def na_mesma_caixa(texto: str | None, trocas: Mapping[str, str]) -> str | None:
    """31.113 F3: a troca do texto que VOLTA a sair (o rascunho, a edição do dono, o alvo e o texto do pedido de
    aprovação): palavra inteira e a MESMA caixa, para a volta (marcador → valor) dar o texto exato. Quem grava confere a
    volta (`Repository.texto_reversivel`) e, se ela não for exata, grava o texto literal."""
    if not texto or not trocas:
        return texto
    for valor, marcador in sorted(trocas.items(), key=lambda kv: -len(kv[0])):
        texto = re.sub(r"(?<![\w@.])" + re.escape(valor) + r"(?![\w@])", lambda _m, m=marcador: m, texto)
    return texto


def no_alvo(alvo: str | None, trocas: Mapping[str, str]) -> str | None:
    """31.113 F3: o alvo normalizado da ação (`@pessoa`, sem caixa) que É um dado da persona vira o marcador. A borda da
    palavra (`_palavra`) não o pega depois do `@`, de propósito (um @ de terceiro não é o nome da persona); aqui o valor
    inteiro tem de ser o dado. Fora disso, a troca de sempre."""
    if not alvo or not trocas:
        return alvo
    inteiro = alvo.strip().lstrip("@").casefold()
    return next((m for v, m in trocas.items() if v.casefold() == inteiro), None) or no_texto(alvo, trocas)


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


__all__ = ["IDENTIFICAM", "MINIMO", "USUARIO_DA_CONTA", "digitado", "mapa", "na_mesma_caixa", "no_alvo", "no_objeto", "no_texto"]
