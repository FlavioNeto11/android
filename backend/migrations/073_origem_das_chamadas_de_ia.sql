-- Origem das chamadas de IA (item 31.2, Fase 31; docs/design/hub-de-ia-fora-de-execucao.md §2, decisão P2).
--
-- O hub de IA atende usos que NÃO são uma execução (ensino, orquestração, assistente do comando, resposta social,
-- persona, curador do Livro, decisão por conjunto fechado). Todos gravam `role` do papel que emprestam (`plan`, na
-- maioria) e `run_id` nulo, então uma linha de `ai_calls` não dizia PARA QUÊ foi paga. Duas colunas resolvem:
--
--     origem   quem pediu a chamada, em vocabulário FECHADO no código (`planning/provider.py::ORIGENS_DE_IA`):
--              execucao | ensino | orquestracao | assistente | social | persona | curador | decisao_fechada.
--              NULL nas linhas antigas, que ninguém reclassifica (a leitura trata NULL como "sem origem");
--     ref      o id do item de origem quando há um (para o curador, o `learning_reviews.id`); texto solto, sem chave
--              estrangeira, como as demais colunas de rastro de `ai_calls`.
--
-- O índice `(origem, ts)` serve ao filtro por origem do gasto do dia (`costs.spent_usd(origem=...)`), que `_budget`
-- consulta a cada chamada fora de execução para aplicar a fatia daquela origem.
--
-- Sem CHECK no vocabulário (a lista muda sem migração; quem confere é o código), sem BEGIN/COMMIT (o executor de
-- migrações já abre a transação).

ALTER TABLE ai_calls ADD COLUMN origem TEXT;
ALTER TABLE ai_calls ADD COLUMN ref TEXT;

CREATE INDEX IF NOT EXISTS idx_ai_calls_origem ON ai_calls(origem, ts);
