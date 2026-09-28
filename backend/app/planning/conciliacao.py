"""Conciliação do saldo estimado com o custo que o PROVEDOR informa (ADR-051).

O saldo estimado só desconta o que passou por `ai_calls`. O que foi gasto por fora (console, playground, scripts que
não gravam ali) só aparece no relatório de custo da organização, que Anthropic e OpenAI publicam por API com chave
de ADMINISTRADOR (`ANTHROPIC_ADMIN_KEY`, `OPENAI_ADMIN_KEY` no `.env`). O Google AI Studio não publica o crédito
pré-pago, então o Gemini fica só com a estimativa local.

Os dois relatórios são por DIA (UTC), e a leitura do saldo cai no meio de um dia. A regra, conservadora de
propósito: na janela que começa à meia-noite UTC do dia da leitura, o que o provedor cobrou ALÉM do que `ai_calls`
registrou na mesma janela conta como gasto externo e sai do saldo. O que foi gasto no dia antes da leitura também
entra — a estimativa fica mais baixa, nunca mais alta. O relatório atrasa algumas horas; atraso só deixa o
externo em zero (o local continua descontando).

O resultado fica em `saldos.CONCILIACOES` (memória do processo), atualizado por quem pede (`GET /api/ai/balances
?refresh=1`, ou quando a última busca passou de `IDADE_MAX_S`). O roteador e a saúde só leem o cache: nenhuma
chamada de modelo espera HTTP de relatório.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Protocol

from ..config import Config
from ..db import Database
from ..modules.billing.adapters import relatorios_de_custo as relatorios
from ..util import now, parse_iso, to_iso
from . import saldos

log = logging.getLogger("poc.ai.conciliacao")

#: Depois disto o cache é considerado velho e a próxima leitura do painel busca de novo.
IDADE_MAX_S = 15 * 60
PRAZO_S = 20.0


class ClienteHttp(Protocol):
    async def aclose(self) -> None: ...


Busca = Callable[..., Awaitable[float]]
#: conta → (busca do custo em [desde, ate), até onde o relatório daquela conta cobre).
BUSCAS: dict[str, tuple[Busca, Callable[[datetime], datetime]]] = {
    "anthropic": (relatorios.custo_anthropic, relatorios.cobertura_anthropic),
    "openai": (relatorios.custo_openai, relatorios.cobertura_openai),
}


async def atualizar(db: Database, cfg: Config, *, forcar: bool = False,
                    client: ClienteHttp | None = None) -> dict[str, saldos.Conciliacao]:
    """Busca o custo de cada conta com chave de administrador e leitura registrada. Falha vira `error`."""
    agora = now()
    proprio = client is None
    cliente = client if client is not None else relatorios.novo_cliente(PRAZO_S)
    try:
        for conta, (buscar, cobertura) in BUSCAS.items():
            chave = saldos.chave_admin(cfg, conta)
            if not chave:
                saldos.CONCILIACOES.pop(conta, None)
                continue
            ancora = db.one("SELECT observed_at FROM ai_balance_snapshots WHERE account=? "
                            "ORDER BY observed_at DESC, id DESC LIMIT 1", (conta,))
            if ancora is None:
                continue
            janela = saldos.janela_de(ancora["observed_at"], agora)
            atual = saldos.CONCILIACOES.get(conta)
            if (not forcar and atual is not None and atual.window_start == janela
                    and (agora - (parse_iso(atual.fetched_at) or agora)).total_seconds() < IDADE_MAX_S):
                continue
            desde: datetime = parse_iso(janela) or agora
            # O local se compara na MESMA janela que o relatório cobre — senão o gasto de hoje, que a Anthropic
            # ainda não reporta, sairia como "a plataforma gastou mais do que o provedor cobrou".
            ate = max(desde, cobertura(agora))
            local = saldos.gasto_usd_por_conta(db, cfg, janela, until=to_iso(ate)).get(conta, 0.0)
            try:
                custo, erro = await buscar(cliente, chave, desde, ate), None
            except Exception as exc:  # noqa: BLE001 - relatório fora do ar não derruba o painel de saldo
                custo, erro = None, relatorios.erro_legivel(exc)
                log.warning("conciliação de %s falhou: %s", conta, erro)
            saldos.CONCILIACOES[conta] = saldos.Conciliacao(
                account=conta, window_start=janela, window_end=to_iso(ate), provider_usd=custo,
                local_usd=round(local, 6),
                fetched_at=to_iso(agora), error=erro)
    finally:
        if proprio:
            await cliente.aclose()
    return saldos.CONCILIACOES


def precisa_atualizar(cfg: Config) -> bool:
    agora = now()
    for conta in BUSCAS:
        if not saldos.chave_admin(cfg, conta):
            continue
        atual = saldos.CONCILIACOES.get(conta)
        if atual is None or (agora - (parse_iso(atual.fetched_at) or agora)).total_seconds() >= IDADE_MAX_S:
            return True
    return False
