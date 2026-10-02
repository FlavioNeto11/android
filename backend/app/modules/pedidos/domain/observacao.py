"""Observação: o que uma ocorrência viu, no formato em que fica guardado (docs/design/pedidos-persistentes.md §6.1, §8).

Puro. Decide COMO uma leitura entra em `pedido_observacoes`, sem tocar banco: a infraestrutura lê a saída da etapa
(`step_outputs`, 056) e chama `preparar`. A regra que importa é a de sempre: **falha ou incerteza nunca contam como sucesso**.

    * valor lido numa ocorrência que terminou `concluida`                  → `observado` (pode sustentar conclusão);
    * valor lido numa ocorrência que `falhou`, ficou `incerta`, foi `cancelada`… → `incerto` (o fato "li X" é guardado, mas
      não comprovado, e o relatório o põe em "não coberto");
    * nenhum valor estruturado, ou valor recusado                          → `ausente`, com o motivo em `trecho`.

Segredo: o valor que tem FORMATO de credencial ou de código de verificação (ADR-009) é RECUSADO e vira `ausente`; a
checagem chega como `parece_segredo` porque o domínio não pode importar `app.security`.
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass

TIPOS_DE_VALOR = ("text", "number", "url", "list", "resultado")        # CHECK de `pedido_observacoes.tipo` (070)
SITUACOES = ("observado", "incerto", "ausente")                        # CHECK de `pedido_observacoes.situacao` (070)
NOME_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")                         # o mesmo de `step_outputs.name` (056)
NOME_DO_RESULTADO = "resultado"
VALOR_MAX = 2000
TRECHO_MAX = 200
FONTE_MAX = 120


@dataclass(frozen=True)
class Preparada:
    situacao: str
    valor: str | None
    tipo: str
    trecho: str | None
    sha256: str | None


def curto(texto: str | None, limite: int) -> str | None:
    t = " ".join((texto or "").split())
    if not t:
        return None
    return t if len(t) <= limite else t[: limite - 1] + "…"


def sha256_do_valor(valor: str) -> str:
    return hashlib.sha256(valor.encode("utf-8")).hexdigest()


def preparar(valor: str | None, *, tipo: str, estado_final: str, motivo: str | None,
             parece_segredo: Callable[[str], bool]) -> Preparada:
    """Como a leitura `valor` (ou a falta dela) fica gravada, dado o estado em que a ocorrência terminou."""
    tipo = tipo if tipo in TIPOS_DE_VALOR else "text"
    if valor is None or not valor.strip():
        return Preparada("ausente", None, "resultado", curto(motivo or f"ocorrência {estado_final}", TRECHO_MAX), None)
    if parece_segredo(valor):
        return Preparada("ausente", None, tipo, "valor recusado: tem formato de credencial ou código de verificação", None)
    cortado = len(valor) > VALOR_MAX
    guardado = valor[:VALOR_MAX]
    situacao = "observado" if estado_final == "concluida" else "incerto"
    trecho = "valor cortado no teto" if cortado else (curto(motivo, TRECHO_MAX) if situacao == "incerto" else None)
    return Preparada(situacao, guardado, tipo, trecho, sha256_do_valor(valor))
