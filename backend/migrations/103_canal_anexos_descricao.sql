-- Item 28.24, fatia F3 (a IA lê a imagem que o dono mandou; número reservado pela orquestradora em 04/10): a descrição que
-- o modelo de visão deu à imagem fica NA linha do anexo, para a mesma imagem não ser paga duas vezes. Só ADD COLUMN na
-- `canal_anexos` (criada pela 101); todas nulas, então nada é reescrito (metadado no SQLite e no PostgreSQL 11+).
--
-- - `descricao`: o texto devolvido pelo modelo, JÁ passado pelo redator de credencial dos textos do canal (nunca o bruto).
--   Nulo = a imagem ainda não foi lida (ou a leitura falhou: falha nunca grava);
-- - `lida_em`: quando foi lida (texto ISO-8601, como `criado_em`);
-- - `modelo_leitura`: o modelo que RESPONDEU (é por ele que a API cobra);
-- - `custo_usd`: o custo da leitura em dólar, por tokens × `ai.prices` (a conta de `planning/costs.py`), nunca `ai_calls.usd`;
-- - `tokens_entrada` e `tokens_saida`: o que a API contou, para conferir a estimativa feita antes da chamada.
--
-- Mesmo padrão da 101: sem FK e sem CHECK. REAL como na 048 (`ai_calls.usd`), que já roda nos dois dialetos.
-- Compatível com SQLite e PostgreSQL. Sem BEGIN/COMMIT: o executor já abre a transação.

ALTER TABLE canal_anexos ADD COLUMN descricao      TEXT;
ALTER TABLE canal_anexos ADD COLUMN lida_em        TEXT;
ALTER TABLE canal_anexos ADD COLUMN modelo_leitura TEXT;
ALTER TABLE canal_anexos ADD COLUMN custo_usd      REAL;
ALTER TABLE canal_anexos ADD COLUMN tokens_entrada INTEGER;
ALTER TABLE canal_anexos ADD COLUMN tokens_saida    INTEGER;
