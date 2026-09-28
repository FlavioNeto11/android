-- Linha de base da conciliação do saldo (ADR-051), por leitura.
--
-- O relatório de custo do provedor é por DIA, e a leitura do console cai no meio do dia. Sem linha de base, o gasto
-- de fora da plataforma feito ANTES da leitura (já descontado pelo próprio console) era descontado de novo: medido em
-- 28/09 na OpenAI, US$ 0,29 a menos logo depois de uma leitura fresca (7,96 estimado × 8,25 no console).
--
--   `provider_baseline_usd` = o que o relatório do provedor já mostrava na janela quando a leitura foi conciliada
--                             pela primeira vez (idealmente no mesmo instante do registro).
--   `local_baseline_usd`    = o que `ai_calls` tinha na mesma janela naquele instante.
--
-- Gasto externo desde a leitura = (provedor agora − base do provedor) − (local agora − base local). NULL = ainda não
-- houve conciliação desta leitura; a primeira que der certo grava a base.
--
-- Compatível com SQLite e PostgreSQL.

ALTER TABLE ai_balance_snapshots ADD COLUMN provider_baseline_usd REAL;
ALTER TABLE ai_balance_snapshots ADD COLUMN local_baseline_usd REAL;
