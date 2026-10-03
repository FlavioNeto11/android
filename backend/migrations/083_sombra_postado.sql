-- A linha da sombra diz se a chamada ao Jev chegou ao POST e qual linha de `ai_calls` ela gerou (item 31.21, Fase 31;
-- ADR-069; proposta aprovada pela orquestradora em 03/10).
--
-- Até aqui, `fallback_reason = 'rede'` juntava quatro casos que a linha não separava: o prazo esgotado ANTES do POST (sem
-- linha em `ai_calls`), a exceção inesperada (antes ou depois, não se sabe), o POST feito cuja linha de gasto não gravou
-- ("régua cega") e o POST respondido acima do prazo da sombra (com linha `ok=1`). O cruzamento sombra × `ai_calls` da
-- leitura (`scripts/jev-leitura-intencao.py`) só podia informar.
--
-- `postado`: 1 quando o decisor chamou o transporte (o POST foi tentado), 0 quando parou antes (privacidade, orçamento,
-- prazo, chave ausente, decisor nulo), NULO quando não se sabe (exceção inesperada, prazo do caminho `on` estourado com o
-- futuro em voo) e no legado. `ai_call_id`: o `ai_calls.id` da chamada (o mesmo padrão da 075 em `learning_reviews`),
-- NULO quando não houve POST ou a linha de gasto não gravou. As duas vão em TODAS as linhas da chamada, como `ms` (não
-- como `usd`): identificam a chamada e não se somam.
--
-- Só número: nada do estado, das opções nem do comando. Sem FK (padrão da 055 e da 074: a sombra sobrevive às linhas de
-- `ai_calls`, que morrem em `log_retention_days`). Sem BEGIN/COMMIT (o executor de migrações já abre a transação). A 077
-- (Jev, 28.10) segue reservada em outro branch; a lacuna na numeração é esperada.

ALTER TABLE decisao_fechada_sombra ADD COLUMN postado INTEGER;
ALTER TABLE decisao_fechada_sombra ADD COLUMN ai_call_id INTEGER;
