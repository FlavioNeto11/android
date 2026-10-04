"""A porta de escrita do que a plataforma decidiu sozinha (item 28.25): o contrato que toda frente enxerga.

Fica no kernel, e não em `modules/decisoes`, de propósito: quem decide (o aprendizado, o executor, os pedidos) precisa
registrar a decisão sem importar o módulo que a lista e a desfaz, e esse módulo precisa chamar a inversa de cada fila
sem importá-las. O kernel é a raiz do grafo (`test_arquitetura::test_contextos_novos_formam_um_dag`).

A regra de privacidade é a do aviso externo: `efeito` e `fatos` dizem o TIPO do que aconteceu e números/ids curtos;
nunca nome de persona, conta, e-mail, telefone, IP nem texto de comando. `fatos_limpos` impõe a forma (plana, curta,
escalares) mas não adivinha conteúdo: quem registra responde por não pôr dado pessoal ali.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

#: As filas de pendência que a política de decisão automática cobre. É o `CHECK` da migração 102.
FILAS: tuple[str, ...] = ("pergunta", "objetivo", "aprendizado", "pedido")

#: Escalar que cabe em `fatos`: texto curto, número ou booleano (nunca estrutura).
Escalar = str | int | float | bool

MAX_REF = 200
MAX_REGRA = 120
MAX_EFEITO = 240
MAX_FATOS = 12
MAX_VALOR = 80
_CHAVE = re.compile(r"^[a-z0-9_]{1,40}$")


@dataclass(frozen=True, slots=True)
class NovaDecisao:
    """Uma decisão da plataforma a registrar. `origem_ref` é a identidade do FATO: a mesma duas vezes é uma linha só."""

    fila: str
    item_ref: str
    origem_ref: str
    regra: str
    efeito: str
    fatos: Mapping[str, Escalar]
    #: UTC ISO do fato. `None` = agora (o relógio do banco).
    decidida_em: str | None = None


class RegistroDeDecisoes(Protocol):
    def registrar(self, decisao: NovaDecisao) -> bool:
        """Grava a decisão. Devolve se a linha é nova (`False` = o fato já estava registrado). Nunca levanta por causa
        de uma decisão repetida; levanta `ValueError` para uma decisão malformada (fila fora do vocabulário, campo
        vazio), que é erro de quem chama."""
        ...


def _uma_linha(texto: object, limite: int) -> str:
    return " ".join(str(texto).split())[:limite]


def fatos_limpos(fatos: Mapping[str, Escalar] | None) -> dict[str, Escalar]:
    """Os fatos na forma que o livro guarda: no máximo `MAX_FATOS` chaves minúsculas, valores escalares de uma linha e
    até `MAX_VALOR` caracteres. O que não cabe na forma é descartado (e não vira erro: o registro não pode travar a
    decisão que já aconteceu)."""
    saida: dict[str, Escalar] = {}
    for chave, valor in (fatos or {}).items():
        if len(saida) >= MAX_FATOS:
            break
        if not _CHAVE.match(str(chave)) or isinstance(valor, (dict, list, tuple, set, type(None))):
            continue
        saida[str(chave)] = valor if isinstance(valor, (bool, int, float)) else _uma_linha(valor, MAX_VALOR)
    return saida


def decisao_valida(d: NovaDecisao) -> NovaDecisao:
    """A decisão com os campos de texto aparados no tamanho do livro, ou `ValueError` se ela é malformada."""
    if d.fila not in FILAS:
        raise ValueError(f"fila desconhecida: {d.fila!r}")
    item, origem = _uma_linha(d.item_ref, MAX_REF), _uma_linha(d.origem_ref, MAX_REF)
    regra, efeito = _uma_linha(d.regra, MAX_REGRA), _uma_linha(d.efeito, MAX_EFEITO)
    if not (item and origem and regra and efeito):
        raise ValueError("item_ref, origem_ref, regra e efeito são obrigatórios")
    return NovaDecisao(d.fila, item, origem, regra, efeito, fatos_limpos(d.fatos), d.decidida_em)


def registrar_decisao(registro: RegistroDeDecisoes, fila: str, item_ref: str, origem_ref: str, regra: str,
                      efeito: str, fatos: Mapping[str, Escalar] | None = None, *, decidida_em: str | None = None) -> bool:
    """A porta de escrita direta: quem decide sozinho chama isto no mesmo passo em que decide (item 28.25).

    Idempotente pela `origem_ref`. Devolve se a linha é nova. Uma falha de gravação NÃO deve desfazer a decisão que já
    aconteceu: quem chama pode embrulhar em try/except e logar; o adaptador do 28.25 também recolhe, depois, o que
    estiver nos eventos de vencimento e na trilha do aprendizado."""
    return registro.registrar(NovaDecisao(fila, item_ref, origem_ref, regra, efeito, dict(fatos or {}), decidida_em))
