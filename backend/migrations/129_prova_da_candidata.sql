-- 129_prova_da_candidata — item 31.271 (número dado pela orquestradora em 07/10).
--
-- A última consulta de cada receita: quando (`ultima_consulta_em`, ISO) e o resultado (`ultima_consulta_resultado`):
-- `concordou` e `divergiu` (veredito da sombra, `RecipeStore.shadow`), `nao_aplicavel` (a candidata não se aplicou na
-- tela de partida e não teve veredito, 31.262), `outro_escopo` e `quarentena` (`RecipeStore.find`). NULL = nunca foi
-- consultada desde a migração. É o que a aba Aprendido mostra da prova da receita candidata (31.270, campo aditivo
-- `prova_da_candidata` do Livro). Só `ADD COLUMN` anulável, igual nos dois bancos; nenhuma linha anterior muda.
ALTER TABLE recipes ADD COLUMN ultima_consulta_em TEXT;
ALTER TABLE recipes ADD COLUMN ultima_consulta_resultado TEXT;
