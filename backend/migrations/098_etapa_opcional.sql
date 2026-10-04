-- Etapa de limpeza opcional (item 31.36, Fase 31). Número 098 dado pela orquestradora em 04/10.
--
-- Por quê: na execução f8722d (28.12-02, android-09) o planejador criou uma etapa só para fechar um banner de cookies
-- que não fecha; o juiz não aceitou, foram 3 tentativas, o replano reabriu a lista e a verba acabou (US$ 0,309, 23
-- decisões do ator). Uma etapa que só limpa a tela não pode derrubar o objetivo.
--
-- - `steps.opcional`: 1 = etapa opcional (sem efeito, sem saídas, sem for_each, sem commit_guard; o parsing garante).
--   Falhar a leva a `skipped` e o objetivo segue; a dependência e o progresso a contam como resolvida. NULO = a etapa
--   de sempre (inclusive todas as anteriores a esta migração).
ALTER TABLE steps ADD COLUMN opcional INTEGER;
