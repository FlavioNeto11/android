-- Fase 7 do plano-100 — itens 7.1 e 7.2 (hub de IA). Achados #91, #92, #97.
--
-- 1) `ai_calls` passa a distinguir o modelo PEDIDO do modelo que RESPONDEU.
--
-- Até aqui a tabela gravava só `model`, vindo de `resp.model` — o modelo que respondeu. Com o fallback de recusa
-- do lado do servidor (`fallbacks: "default"`) ligado por padrão, isso significa que uma troca de modelo era
-- indistinguível de "estava configurado assim": o painel mostrava uma linha de um modelo que ninguém escolheu e
-- não havia como perguntar ao banco quantas vezes o fallback disparou. Agora `requested_model` guarda o que foi
-- pedido e `fallback` diz POR QUE houve troca ('refusal' = recusa reexecutada pelo servidor do provedor;
-- '<provedor>' = o provedor desta função falhou e a função DECLARA `fallback_provider`). NULL = não houve troca.
--
-- `provider` existe porque o modelo deixou de identificar o endpoint: com o provedor compatível com OpenAI, dois
-- endpoints podem servir o mesmo nome de modelo, e o relatório de custo precisa saber qual deles cobrou.
--
-- Compatível com SQLite e PostgreSQL: só ALTER TABLE ADD COLUMN com DEFAULT constante. Sem BEGIN/COMMIT — o
-- executor de migrações já abre a transação.

ALTER TABLE ai_calls ADD COLUMN requested_model TEXT;   -- modelo PEDIDO; NULL nas linhas anteriores a esta migração
ALTER TABLE ai_calls ADD COLUMN fallback TEXT;          -- NULL | 'refusal' | '<nome do provedor de fallback>'
ALTER TABLE ai_calls ADD COLUMN provider TEXT;          -- qual endpoint respondeu (anthropic, local, simulated…)

-- O teto de gasto por dia e o cartão "gasto de hoje" varrem por `ts`; sem índice isso é varredura de tabela a
-- cada chamada de IA, e a tabela cresce uma linha por chamada (908 em 7 dias).
CREATE INDEX idx_ai_calls_ts ON ai_calls(ts);
