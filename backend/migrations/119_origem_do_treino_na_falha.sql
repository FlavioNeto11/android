-- 31.111 F1: a sessão de ensino que nasce de uma etapa que FALHOU numa execução (P-014 B do dono, 06/10). Três ids opacos
-- ligam a sessão ao que deu errado: a execução, a etapa e a tentativa que falhou. A trilha, a tela e o diagnóstico não
-- se copiam: o ensino aponta para `steps`, `attempts` e `evidence`, que já os guardam. Sem chave estrangeira de
-- propósito: a limpeza de execuções velhas não pode apagar nem travar a sessão (a origem vira só um rótulo sem destino).
-- A sessão que não vem de falha (a gravação de hoje) fica com as três colunas vazias.
--
-- Número 119 reservado pela orquestradora em 06/10.
ALTER TABLE training_sessions ADD COLUMN origin_run_id TEXT;
ALTER TABLE training_sessions ADD COLUMN origin_step_id TEXT;
ALTER TABLE training_sessions ADD COLUMN origin_attempt_id TEXT;
CREATE INDEX ix_training_sessions_origin ON training_sessions(origin_run_id);
