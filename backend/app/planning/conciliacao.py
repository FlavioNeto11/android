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

from ..config import Config
from ..db import Database
from ..modules.billing.adapters import relatorios_de_custo as relatorios
from ..util import now, parse_iso, to_iso
from . import costs, saldos

log = logging.getLogger("poc.ai.conciliacao")

#: Depois disto o cache é considerado velho e a próxima leitura do painel busca de novo.
IDADE_MAX_S = 15 * 60
PRAZO_S = 20.0


Busca = Callable[..., Awaitable[float]]


def usd_do_uso_anthropic(prices: dict[str, list[float]], linhas: list[relatorios.UsoPorModelo]) -> float:
    """Tokens do relatório de uso × `ai.prices` — a MESMA tabela do custo local, então o que sobra na comparação é
    consumo de fora da plataforma, não diferença de preço. Validado em 28/09 (12h–18h UTC): US$ 5,4946 pelo
    relatório × 5,4693 em `ai_calls`. A tabela tem um preço só de gravação de cache (a de 5 min, 1,25× a entrada);
    a de 1 h custa 2× a entrada, e é cobrada assim."""
    total = 0.0
    for u in linhas:
        preco, _ = costs.effective_price(prices, u.model)
        total += costs.usd(prices, u.model, [u.uncached_input, u.cache_read, u.cache_write_5m, u.output])
        total += u.cache_write_1h * preco[0] * 2 / 1_000_000
    return round(total, 6)


async def _custo_anthropic(cliente: relatorios.Cliente, chave: str, desde: datetime, ate: datetime,
                           cfg: Config) -> float:
    return usd_do_uso_anthropic(cfg.file.ai.prices, await relatorios.uso_anthropic(cliente, chave, desde, ate))


async def _custo_openai(cliente: relatorios.Cliente, chave: str, desde: datetime, ate: datetime, cfg: Config) -> float:
    return await relatorios.custo_openai(cliente, chave, desde, ate)
#: conta → (busca do custo em [desde, ate), até onde o relatório daquela conta cobre).
BUSCAS: dict[str, tuple[Busca, Callable[[datetime], datetime]]] = {
    "anthropic": (_custo_anthropic, relatorios.cobertura_anthropic),
    "openai": (_custo_openai, relatorios.cobertura_openai),
}


async def atualizar(db: Database, cfg: Config, *, forcar: bool = False,
                    client: relatorios.Cliente | None = None) -> dict[str, saldos.Conciliacao]:
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
            ancora = db.one("SELECT id, observed_at, provider_baseline_usd, local_baseline_usd FROM ai_balance_snapshots"
                            " WHERE account=? ORDER BY observed_at DESC, id DESC LIMIT 1", (conta,))
            if ancora is None:
                continue
            janela = saldos.janela_de(ancora["observed_at"], agora, conta=conta)
            atual = saldos.CONCILIACOES.get(conta)
            if (not forcar and atual is not None and atual.snapshot_id == int(ancora["id"])
                    and (agora - (parse_iso(atual.fetched_at) or agora)).total_seconds() < IDADE_MAX_S):
                continue
            desde: datetime = parse_iso(janela) or agora
            # O local se compara na MESMA janela que o relatório cobre — senão o gasto de hoje, que a Anthropic
            # ainda não reporta, sairia como "a plataforma gastou mais do que o provedor cobrou".
            ate = max(desde, cobertura(agora))
            local = saldos.gasto_usd_por_conta(db, cfg, janela, until=to_iso(ate)).get(conta, 0.0)
            try:
                custo, erro = await buscar(cliente, chave, desde, ate, cfg), None
            except Exception as exc:  # noqa: BLE001 - relatório fora do ar não derruba o painel de saldo
                custo, erro = None, relatorios.erro_legivel(exc)
                log.warning("conciliação de %s falhou: %s", conta, erro)
            base_p, base_l = ancora["provider_baseline_usd"], ancora["local_baseline_usd"]
            if base_p is None and custo is not None and desde > (parse_iso(ancora["observed_at"]) or agora):
                # Janela que começa DEPOIS da leitura (Anthropic, só dias fechados): nada nela é anterior à leitura.
                base_p, base_l = 0.0, 0.0
                db.execute("UPDATE ai_balance_snapshots SET provider_baseline_usd=0, local_baseline_usd=0 WHERE id=?",
                           (int(ancora["id"]),))
            elif base_p is None and custo is not None:
                # Primeira conciliação desta leitura: o que já havia na janela é a base (o console já descontou).
                # A rota de registro concilia no mesmo instante, então a base é a do momento da leitura.
                base_p, base_l = custo, local
                db.execute("UPDATE ai_balance_snapshots SET provider_baseline_usd=?, local_baseline_usd=? WHERE id=?",
                           (float(custo), float(local), int(ancora["id"])))
            saldos.CONCILIACOES[conta] = saldos.Conciliacao(
                account=conta, snapshot_id=int(ancora["id"]), window_start=janela, window_end=to_iso(ate),
                provider_usd=custo, local_usd=round(local, 6), fetched_at=to_iso(agora), error=erro,
                provider_baseline_usd=float(base_p or 0.0), local_baseline_usd=float(base_l or 0.0))
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
