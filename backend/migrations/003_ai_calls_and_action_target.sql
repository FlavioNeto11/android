-- Uso de IA por chamada (função, modelo, cache) — base do relatório de custo — e alvo estável de cada ação
-- executada (seletor reaproveitável por receitas). Sem BEGIN/COMMIT: o executor de migrações já abre a transação.

CREATE TABLE ai_calls (
  id            {{PK_AUTO}},
  ts            TEXT NOT NULL,
  run_id        TEXT,
  objective_id  TEXT,
  step_id       TEXT,
  role          TEXT NOT NULL,               -- plan | decide | verify
  model         TEXT NOT NULL,
  tier          INTEGER NOT NULL DEFAULT 0,  -- 0 = modelo da função; 1 = escalonado
  input_tokens  INTEGER NOT NULL DEFAULT 0,  -- entrada NÃO vinda do cache
  cache_read    INTEGER NOT NULL DEFAULT 0,
  cache_write   INTEGER NOT NULL DEFAULT 0,
  output_tokens INTEGER NOT NULL DEFAULT 0,
  with_image    INTEGER NOT NULL DEFAULT 0,
  ms            INTEGER NOT NULL DEFAULT 0,
  ok            INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX idx_ai_calls_run ON ai_calls(run_id);

ALTER TABLE actions ADD COLUMN target TEXT;                       -- JSON do elemento resolvido (UiElement.to_dict)
ALTER TABLE actions ADD COLUMN source TEXT NOT NULL DEFAULT 'ai'; -- ai | recipe
