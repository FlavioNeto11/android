-- O custo MEDIDO da revisão do curador (frente Aprendizado; 30.12 ligou o curador ao hub de IA).
--
-- Desde o 30.12 a resposta do curador traz `usd` e `ai_call_id` lidos da própria linha de `ai_calls` (o hub mede; o
-- aprendizado nunca calcula custo à parte). A 069 tem `usd REAL NOT NULL DEFAULT 0` e não tem onde guardar a chamada.
--
-- Por que NÃO tornar `usd` nulável: no SQLite isso exige recriar a tabela (`learning_reviews` é a auditoria do curador
-- e NUNCA é purgada). `usd = 0` continua querendo dizer "não medido", nunca "de graça". O `usd` medido só passa a ser
-- gravado depois de unificar o saldo na rubrica (pendência em `design/hub-de-ia-fora-de-execucao.md`); até lá a coluna
-- nova liga a revisão à chamada paga, que é o que a auditoria precisa.
--
-- Sem chave estrangeira: `ai_calls` é purgada pela retenção e a revisão não pode perder a linha por isso.
-- Sem BEGIN/COMMIT (o executor de migrações já abre a transação).

ALTER TABLE learning_reviews ADD COLUMN ai_call_id INTEGER;
