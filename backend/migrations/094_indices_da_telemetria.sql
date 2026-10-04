-- Índices de tempo para a purga e as leituras por tipo de `events` e `measurements` (item 14.13, RA-11, parte Android).
-- Número 094 reservado pela orquestradora em 04/10.
--
-- Por quê: a purga por retenção (`state.py`: a telemetria em 48 h, o resto em `log_retention_days`) e as leituras
-- por tipo (`WHERE kind = ? AND ts >= ?`: o relatório do Aprendizado, a medida de boot, os painéis de medida) varriam a
-- tabela inteira; o único índice de `events` era `(run_id, id)`, da linha do tempo de uma execução, e `measurements` não
-- tinha nenhum (a purga dela é `DELETE ... WHERE ts < ?`, daí também `measurements(ts)`). Só índice novo: nenhuma
-- linha muda. `IF NOT EXISTS` vale nos dois dialetos.
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_kind_ts ON events(kind, ts);
CREATE INDEX IF NOT EXISTS idx_measurements_kind_ts ON measurements(kind, ts);
CREATE INDEX IF NOT EXISTS idx_measurements_ts ON measurements(ts);
