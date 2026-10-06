-- A sessão de treino e o fluxo que nasceram de uma prova (item 31.130, Fase 31). Número 122 reservado pela orquestradora
-- em 06/10.
--
-- Por quê: os fluxos ensinados durante uma prova de sessão (três no Livro em 06/10) ficavam iguais a um fluxo real
-- desligado por uma pessoa, em Salvas e no Livro.
--
-- - `training_sessions.nascido_de_prova` e `flows.nascido_de_prova`: 1 quando a sessão foi aberta como prova
--   (`nascido_de_prova: true` no POST da sessão, adendo v1.87); o `save` leva a marca ao fluxo. NULO = uso real
--   (inclusive tudo o que é anterior a esta migração). Os já existentes se marcam pelo id com
--   `scripts/marcar-fluxo-de-prova.py`.
ALTER TABLE training_sessions ADD COLUMN nascido_de_prova INTEGER;
ALTER TABLE flows ADD COLUMN nascido_de_prova INTEGER;
