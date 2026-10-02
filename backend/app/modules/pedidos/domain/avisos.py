"""Vocabulário e identidade dos avisos do pedido (item 28.9; adendo v0.45, "Avisos"; 28.8 acrescenta dois, migração 076).

Puro: stdlib. Quem grava, lê e emite o aviso é `infrastructure/avisos.py`; aqui ficam só as palavras (as mesmas do CHECK de
`pedido_avisos.tipo`, migração 072, conferidas em `tests/test_pedidos_avisos.py`) e as chaves de deduplicação, para o
laço (28.4 a 28.7) e a API (28.9) dizerem o MESMO fato com a MESMA chave: é isso que impede o evento em dobro.
"""
from __future__ import annotations

import hashlib
from collections.abc import Mapping

#: `tipo -> (nivel, requer_pessoa)`. Os três últimos pedem uma pessoa: vão para as Pendências e para o canal de fora, e a
#: caixa de avisos do painel os filtra (`requer_pessoa=0`).
TIPOS: Mapping[str, tuple[str, bool]] = {
    "pausa_automatica": ("warn", False),
    "orcamento_80": ("warn", False),
    "orcamento_esgotado": ("warn", False),
    "ocorrencia_perdida": ("warn", False),
    "relatorio_pronto": ("info", False),
    "encerramento": ("info", False),
    "aprovacao_pendente": ("warn", True),
    "pergunta": ("warn", True),
    "ocorrencia_incerta": ("warn", True),
    # 28.8 (migração 076): fatos dos gatilhos de evento e de condição; vão à caixa do dono, não às Pendências.
    "eventos_perdidos": ("warn", False),
    "condicao_atendida": ("warn", False),
}
NIVEIS = frozenset({"info", "warn", "error"})

#: O aviso de orçamento sai quando o gasto chega a esta fração do teto (e antes de esgotá-lo).
FRACAO_DO_AVISO_DE_ORCAMENTO = 0.8


def id_do_aviso(chave: str) -> str:
    """Id estável da linha: o mesmo fato (mesma `chave_dedupe`) tem sempre o mesmo id, em qualquer réplica."""
    return "avs_" + hashlib.sha256(chave.encode("utf-8")).hexdigest()[:26]


def chave_da_pausa(pedido_id: str, em: str) -> str:
    """Pausa automática: uma por instante de pausa (`pedidos.atualizado_em` no momento em que o estado virou `pausado`)."""
    return f"pausa_automatica:{pedido_id}:{em}"


def chave_do_encerramento(pedido_id: str) -> str:
    """`encerrado` é terminal: um encerramento por pedido."""
    return f"encerramento:{pedido_id}"


def chave_do_orcamento_esgotado(pedido_id: str) -> str:
    return f"orcamento_esgotado:{pedido_id}"


def chave_do_orcamento_80(pedido_id: str, total_usd: float) -> str:
    """Uma vez por pedido ATÉ o orçamento subir: o teto entra na chave, e a pessoa que sobe o teto volta a ser avisada."""
    return f"orcamento_80:{pedido_id}:{total_usd:.6f}"


def chave_da_ocorrencia_perdida(ocorrencia_id: str) -> str:
    return f"ocorrencia_perdida:{ocorrencia_id}"


def chave_do_relatorio(relatorio_id: object) -> str:
    return f"relatorio_pronto:{relatorio_id}"


def passou_de_80(gasto_usd: float, total_usd: float | None) -> bool:
    """`True` com o gasto em [80%, 100%) do teto. Em 100% ou mais vale `orcamento_esgotado` (o aviso de 80% seria velho)."""
    if total_usd is None or total_usd <= 0:
        return False
    return FRACAO_DO_AVISO_DE_ORCAMENTO * total_usd <= gasto_usd < total_usd
