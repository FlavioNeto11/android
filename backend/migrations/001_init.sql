-- Esquema inicial. Todas as datas são ISO-8601 UTC (texto).

CREATE TABLE apps (
  id              TEXT PRIMARY KEY,
  name            TEXT NOT NULL,
  package         TEXT NOT NULL,
  activity        TEXT,
  apk_path        TEXT,
  nav_hints       TEXT,
  known_selectors TEXT,            -- JSON
  builtin         INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE instances (
  id                  TEXT PRIMARY KEY,       -- android-01 …
  idx                 INTEGER NOT NULL UNIQUE,
  avd_name            TEXT NOT NULL,
  console_port        INTEGER NOT NULL UNIQUE,
  system_port         INTEGER NOT NULL UNIQUE,
  mjpeg_port          INTEGER NOT NULL UNIQUE,
  chromedriver_port   INTEGER NOT NULL UNIQUE,
  app_id              TEXT REFERENCES apps(id) ON DELETE SET NULL,
  account_label       TEXT,
  account_evidence    TEXT,
  account_evidence_ts TEXT,
  -- processo do emulador iniciado POR ESTE PROJETO (só estes são encerrados por nós)
  emulator_pid        INTEGER,
  emulator_started_at TEXT,
  boot_seconds        REAL
);

CREATE TABLE settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL              -- JSON
);

CREATE TABLE runs (
  id                  TEXT PRIMARY KEY,
  idempotency_key     TEXT NOT NULL UNIQUE,   -- clique duplo / repetição HTTP → mesma execução
  command             TEXT NOT NULL,
  mode                TEXT NOT NULL,          -- plan | execute
  status              TEXT NOT NULL,
  status_detail       TEXT,
  simulated           INTEGER NOT NULL DEFAULT 0,
  instance_ids        TEXT NOT NULL,          -- JSON: solicitadas
  instances_used      INTEGER NOT NULL DEFAULT 0,
  plan                TEXT,                   -- JSON
  pause_requested     INTEGER NOT NULL DEFAULT 0,
  cancel_requested    INTEGER NOT NULL DEFAULT 0,
  ai_input_tokens     INTEGER NOT NULL DEFAULT 0,
  ai_output_tokens    INTEGER NOT NULL DEFAULT 0,
  created_at          TEXT NOT NULL,
  started_at          TEXT,
  finished_at         TEXT
);

CREATE TABLE objectives (
  id               TEXT PRIMARY KEY,          -- <run_id>:<instance_id>
  run_id           TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  instance_id      TEXT NOT NULL,
  status           TEXT NOT NULL,
  status_detail    TEXT,
  blocked_reason   TEXT,
  needs            TEXT,
  plan_version     INTEGER NOT NULL DEFAULT 1,
  parameters       TEXT NOT NULL DEFAULT '{}',
  delivery_level   TEXT,
  effects          TEXT NOT NULL DEFAULT '[]',
  ai_calls         INTEGER NOT NULL DEFAULT 0,
  ai_input_tokens  INTEGER NOT NULL DEFAULT 0,
  ai_output_tokens INTEGER NOT NULL DEFAULT 0,
  started_at       TEXT,
  finished_at      TEXT,
  UNIQUE (run_id, instance_id)
);
CREATE INDEX idx_objectives_instance ON objectives(instance_id, status);

CREATE TABLE plan_versions (
  objective_id TEXT NOT NULL REFERENCES objectives(id) ON DELETE CASCADE,
  version      INTEGER NOT NULL,
  reason       TEXT NOT NULL,
  steps        TEXT NOT NULL,                 -- JSON
  created_at   TEXT NOT NULL,
  PRIMARY KEY (objective_id, version)
);

CREATE TABLE steps (
  id            TEXT PRIMARY KEY,             -- estável: <run>:<inst>:v<versão>:<key>
  run_id        TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  objective_id  TEXT NOT NULL REFERENCES objectives(id) ON DELETE CASCADE,
  instance_id   TEXT NOT NULL,
  plan_version  INTEGER NOT NULL,
  seq           INTEGER NOT NULL,
  key           TEXT NOT NULL,
  title         TEXT NOT NULL,
  goal          TEXT NOT NULL,
  depends_on    TEXT NOT NULL DEFAULT '[]',
  side_effect   INTEGER NOT NULL DEFAULT 0,
  commit_guard  TEXT NOT NULL DEFAULT '[]',   -- textos que precisam estar visíveis antes da ação com efeito
  precondition  TEXT,
  postcondition TEXT NOT NULL,                -- JSON
  timeout_s     INTEGER NOT NULL,
  max_attempts  INTEGER NOT NULL,
  attempts      INTEGER NOT NULL DEFAULT 0,
  status        TEXT NOT NULL,
  status_detail TEXT,
  next_retry_at TEXT,
  result        TEXT,
  started_at    TEXT,
  finished_at   TEXT
);
CREATE INDEX idx_steps_sched ON steps(instance_id, status);
CREATE INDEX idx_steps_objective ON steps(objective_id, plan_version, seq);

CREATE TABLE attempts (
  id              TEXT PRIMARY KEY,           -- estável: <step_id>:a<n> → o scheduler não dispara a mesma tentativa 2x
  step_id         TEXT NOT NULL REFERENCES steps(id) ON DELETE CASCADE,
  number          INTEGER NOT NULL,
  status          TEXT NOT NULL,
  started_at      TEXT NOT NULL,
  finished_at     TEXT,
  error           TEXT,
  recovery        TEXT,
  observed_result TEXT,
  UNIQUE (step_id, number)
);

-- Diário de ações: a intenção é gravada ANTES da chamada ao driver; o resultado, depois.
CREATE TABLE actions (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  attempt_id      TEXT NOT NULL REFERENCES attempts(id) ON DELETE CASCADE,
  seq             INTEGER NOT NULL,
  tool            TEXT NOT NULL,
  args            TEXT NOT NULL,
  rationale       TEXT,
  status          TEXT NOT NULL,              -- intended | done | failed | unknown | rejected
  side_effect     INTEGER NOT NULL DEFAULT 0, -- ação "commit" de uma etapa com efeito externo
  effect_possible INTEGER NOT NULL DEFAULT 0, -- o comando pode ter chegado ao aparelho
  intent_at       TEXT NOT NULL,
  done_at         TEXT,
  result          TEXT,
  error           TEXT,
  UNIQUE (attempt_id, seq)
);

CREATE TABLE events (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  ts           TEXT NOT NULL,
  kind         TEXT NOT NULL,
  level        TEXT NOT NULL DEFAULT 'info',
  run_id       TEXT,
  instance_id  TEXT,
  objective_id TEXT,
  step_id      TEXT,
  attempt_id   TEXT,
  message      TEXT NOT NULL,
  data         TEXT
);
CREATE INDEX idx_events_run ON events(run_id, id);

CREATE TABLE evidence (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id      TEXT NOT NULL,
  instance_id TEXT NOT NULL,
  step_id     TEXT,
  attempt_id  TEXT,
  ts          TEXT NOT NULL,
  kind        TEXT NOT NULL,                  -- screenshot | hierarchy | text | verifier
  note        TEXT,
  path        TEXT,
  redacted    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_evidence_run ON evidence(run_id, id);

CREATE TABLE measurements (
  id     INTEGER PRIMARY KEY AUTOINCREMENT,
  ts     TEXT NOT NULL,
  kind   TEXT NOT NULL,                       -- boot | scale | image_probe
  data   TEXT NOT NULL                        -- JSON
);
