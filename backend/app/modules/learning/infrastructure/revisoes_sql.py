"""`RegistroDeRevisoes` sobre `learning_reviews` (069) e a leitura da janela do orçamento do curador (30.11, §8.7).

Custo: a 069 tem `usd REAL NOT NULL DEFAULT 0` e não tem `ai_call_id`. Até o 30.12 (custo do hub, coluna `origem` em
`ai_calls`) o curador grava `usd = 0`, que aqui quer dizer NÃO MEDIDO, nunca "de graça": só `usd > 0` entra como
custo medido (c̄ e mediana), e a revisão sem medida entra no gasto da curadoria pela ESTIMATIVA do tamanho do dossiê
gravado (a aplicação a calcula; aqui só se lê o tamanho). Nenhum custo é calculado à parte e gravado.
Quando a 073 existir (31.2: `ai_calls.origem`/`ai_calls.ref`), o `usd` vai sair de `costs.spent_usd(origem='curador')`;
o NULL que o combinado pede exige migração nova (a 069 declara a coluna NOT NULL), que é do 30.12.

`G_W` é o gasto de IA da operação: `SUM(learning_daily.usd)` na janela, sem filtro de falha (o relatório filtra
`failure_kind <> ''` porque fala de falhas; o orçamento fala do gasto todo). A curadoria não entra nele: a régua
diária só soma `ai_calls` de tentativas.
"""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta

from app.db import Database
from app.modules.learning.application.ports import LeituraDaJanela, NovaRevisao
from app.modules.learning.infrastructure import linhas
from app.util import to_iso


class RegistroDeRevisoesSql:
    def __init__(self, db: Database) -> None:
        self._db = db

    def existe(self, item_ref: str, dossie_hash: str) -> bool:
        return self._db.one("SELECT 1 AS x FROM learning_reviews WHERE item_ref=? AND dossie_hash=?",
                            (item_ref, dossie_hash)) is not None

    def ultima(self, item_ref: str) -> str | None:
        r = self._db.one("SELECT MAX(created_at) AS em FROM learning_reviews WHERE item_ref=?", (item_ref,))
        return linhas.texto_ou_nulo(r, "em") if r is not None else None

    def gravar(self, nova: NovaRevisao, agora: datetime) -> str | None:
        rid = f"lr-{secrets.token_hex(8)}"
        # `ON CONFLICT DO NOTHING` e não `except IntegrityError`: no PostgreSQL o erro abortaria a transação de quem
        # chama. A corrida entre réplicas perde aqui, no INSERT, e não no gasto.
        cur = self._db.execute(
            "INSERT INTO learning_reviews(id, created_at, item_ref, item_kind, scope_app, gatilho, dossie_hash, dossie,"
            " template_id, template_versao, provedor, modelo, simulated, usd, saida, validade, classe_de_risco,"
            " politica) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?,?,?)"
            " ON CONFLICT (item_ref, dossie_hash) DO NOTHING",
            (rid, to_iso(agora), nova.item_ref, nova.item_kind, nova.scope_app, nova.gatilho, nova.dossie_hash,
             json.dumps(nova.dossie, ensure_ascii=False, sort_keys=True), nova.template_id, nova.template_versao,
             nova.provedor, nova.modelo, int(nova.simulated),
             None if nova.saida is None else json.dumps(nova.saida, ensure_ascii=False, sort_keys=True),
             nova.validade, nova.classe_de_risco, nova.politica))
        return rid if (cur.rowcount or 0) == 1 else None

    def janela(self, agora: datetime, dias: int) -> LeituraDaJanela:
        inicio = agora - timedelta(days=dias)
        desde, hora, hoje = to_iso(inicio), to_iso(agora - timedelta(hours=1)), agora.strftime("%Y-%m-%d")
        g = self._db.one("SELECT SUM(usd) AS usd FROM learning_daily WHERE day >= ? AND day <= ?",
                         (inicio.strftime("%Y-%m-%d"), hoje))
        medidos: list[float] = []
        sem_medida: list[int] = []
        sem_medida_na_hora: list[int] = []
        gasto = gasto_hora = 0.0
        antes = de_hoje = 0
        # Só o que foi à IA (ou teria ido): a recusada por triagem ou custo não gastou nada.
        for r in self._db.query(
                "SELECT created_at, usd, dossie FROM learning_reviews WHERE created_at >= ? AND provedor <> ''",
                (desde,)):
            em = linhas.texto(r, "created_at")
            usd = linhas.real(r, "usd")
            na_hora = em >= hora
            if usd > 0:
                medidos.append(usd)
                gasto += usd
                gasto_hora += usd if na_hora else 0.0
            else:
                tamanho = len(linhas.texto(r, "dossie").encode("utf-8"))
                sem_medida.append(tamanho)
                if na_hora:
                    sem_medida_na_hora.append(tamanho)
            if em[:10] == hoje:
                de_hoje += 1
            else:
                antes += 1
        return LeituraDaJanela(gasto_da_operacao=linhas.real(g, "usd") if g is not None else 0.0,
                               custos_medidos=tuple(medidos), tamanhos_sem_medida=tuple(sem_medida),
                               tamanhos_sem_medida_na_hora=tuple(sem_medida_na_hora), gasto_medido=gasto,
                               gasto_medido_na_hora=gasto_hora, revisoes_antes_de_hoje=antes, revisoes_de_hoje=de_hoje)


__all__ = ["RegistroDeRevisoesSql"]
