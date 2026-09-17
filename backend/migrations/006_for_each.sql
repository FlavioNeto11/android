-- Repetição sobre listas lidas da tela (collect + for_each).
ALTER TABLE objectives ADD COLUMN collected TEXT;          -- JSON {chave_da_etapa_de_coleta: [itens]}
ALTER TABLE steps ADD COLUMN variables TEXT;               -- JSON das variáveis próprias da etapa (ex.: {"item": "QA-001"})
ALTER TABLE steps ADD COLUMN for_each TEXT;                -- etapa-modelo ainda não expandida (nunca é executada)
