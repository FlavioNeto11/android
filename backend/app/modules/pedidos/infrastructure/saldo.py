"""Saldo das contas de IA como razão para o laço de pedidos ADIAR o despacho (item 28.6, ADR-051).

Lê o MESMO serviço que alimenta `GET /api/ai/balances` (`planning.saldos.estado`): o saldo estimado é âncora menos o
gasto de `ai_calls`, sem nenhuma chamada paga e sem tocar a rede. O laço recebe esta função por injeção
(`LacoDePedidos(adiar_por_saldo=...)`, montada em `AppState`), como recebe o custo da execução, porque `Config` e
`planning` não são dele.
"""
from __future__ import annotations

from app.config import Config
from app.db import Database
from app.planning import saldos


def motivo_de_adiamento(db: Database, cfg: Config, minimo_usd: float) -> str | None:
    """Por que NÃO despachar agora; `None` libera.

    Só olha as contas que pagam alguma função de IA hoje (`SaldoConta.roles`): a conta só da imagem da persona não
    segura uma execução. Adia quando a conta está barrada pelo dono (`block_below`) ou esgotada pelo provedor, ou,
    com `minimo_usd > 0`, quando o saldo estimado dela caiu abaixo do mínimo dos pedidos (0 desliga esta segunda
    regra: o bloqueio do dono continua valendo). Conta sem leitura de saldo não adia: não há o que comparar."""
    for s in saldos.estado(db, cfg):
        if not s.roles:
            continue
        if s.bloqueia:
            return f"{s.label}: {s.message}"
        if minimo_usd > 0 and s.estimated_balance_usd is not None and s.estimated_balance_usd < minimo_usd:
            return (f"saldo estimado de {s.label} (US$ {s.estimated_balance_usd:.2f}) abaixo do mínimo dos pedidos "
                    f"(US$ {minimo_usd:.2f})")
    return None
