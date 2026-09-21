-- Fluxos (plano congelado de um comando repetível) e receitas (ações por seletor aprendidas com a IA e repetidas sem ela).

CREATE TABLE flows (
  id               TEXT PRIMARY KEY,
  name             TEXT NOT NULL,
  match_key        TEXT NOT NULL UNIQUE,        -- comando-modelo normalizado (chave de casamento)
  command_template TEXT NOT NULL,               -- comando com {parametro} no lugar dos valores
  plan             TEXT NOT NULL,               -- Plan JSON em forma de template
  app_id           TEXT,
  source_run_id    TEXT,
  status           TEXT NOT NULL DEFAULT 'active',   -- active | disabled
  uses             INTEGER NOT NULL DEFAULT 0,
  created_at       TEXT NOT NULL,
  last_used_at     TEXT
);

CREATE TABLE recipes (
  id                {{PK_AUTO}},
  app_package       TEXT NOT NULL,
  app_version       TEXT NOT NULL,
  step_hash         TEXT NOT NULL,              -- etapa em forma de template (chave + pós-condição + guardas)
  step_key          TEXT NOT NULL,
  version           INTEGER NOT NULL DEFAULT 1,
  status            TEXT NOT NULL DEFAULT 'active',  -- active | quarantined | superseded
  actions           TEXT NOT NULL,              -- JSON: [{tool, args, selectors, commit}]
  learned_from_step TEXT,
  replay_ok         INTEGER NOT NULL DEFAULT 0,
  replay_fail       INTEGER NOT NULL DEFAULT 0,
  consecutive_fail  INTEGER NOT NULL DEFAULT 0,
  shadow_agree      INTEGER NOT NULL DEFAULT 0,
  shadow_total      INTEGER NOT NULL DEFAULT 0,
  created_at        TEXT NOT NULL,
  last_used_at      TEXT,
  UNIQUE (app_package, app_version, step_hash, version)
);
CREATE INDEX idx_recipes_lookup ON recipes(app_package, app_version, step_hash, status);

ALTER TABLE runs  ADD COLUMN flow_id TEXT;
ALTER TABLE steps ADD COLUMN template_hash TEXT;     -- hash da etapa ANTES de resolver as variáveis
ALTER TABLE steps ADD COLUMN driven_by TEXT;         -- ai | recipe | recipe+ai
