-- 31.307 (ressalva do 31.293): o laço de eventos parou 70 s no boot de 10/10/2026 (16:33Z, `data/logs/laco-travado-…Z-1..3.txt`)
-- dentro de UMA consulta, a de `DeviceManager._ultimo_dto_persistido`:
--     SELECT data FROM events WHERE kind='instance.updated' AND instance_id=? ORDER BY id DESC LIMIT 20
-- A tabela `events` só tinha índice em (run_id, id), (ts) e (kind, ts): sem um índice que sirva ao filtro por aparelho E à
-- ordem por id, o banco anda pela chave primária de trás para frente (ou lê todo o `instance.updated`) até achar 20 linhas do
-- aparelho, e isso, com o disco estrangulado pela suíte no mesmo host, passou de um minuto.
--
-- Índice ADITIVO e portável (SQLite e PostgreSQL, a mesma instrução): (instance_id, kind, id) serve ao filtro e à ordem
-- decrescente por id, então a consulta vira uma busca de 20 entradas. `IF NOT EXISTS` torna a reaplicação inofensiva. Nenhuma
-- linha é tocada. O `CREATE INDEX` trava a escrita em `events` enquanto roda: o deploy aplica com o backend parado, e a janela é
-- a do tamanho da tabela (data/poc.sqlite3 tinha 204 MB em 10/10/2026).
CREATE INDEX IF NOT EXISTS idx_events_instance_kind_id ON events(instance_id, kind, id);

-- 4º ponto (despejo das 16:56:41Z, laço parado 10 s): `/health` esperava, NA thread do laço, a trava do banco que a curadoria do aprendizado
-- segurava numa consulta só (`relatorio_sql._intervencoes_ligadas`):
--     SELECT attempt_id, step_id FROM learning_signals WHERE kind IN (?,?) AND created_at >= ? AND created_at < ? [AND simulated = 0]
-- `learning_signals` só tinha índice em (kind, source_ref, created_by), (app_package, capability, kind, created_at) e (run_id): o filtro por
-- `kind` + janela de tempo lia todas as linhas desses `kind`. (kind, created_at) o resolve por faixa.
CREATE INDEX IF NOT EXISTS ix_learning_signals_kind_criado ON learning_signals(kind, created_at);
