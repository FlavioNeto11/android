-- Comando remoto nos notebooks da rede (29.154, ADR-079): o registro de cada comando mandado a um worker e o
-- interruptor por worker.
--
-- Só `CREATE TABLE`. Sem BEGIN/COMMIT: o executor de migrações já abre a transação.

-- Um registro por comando. `id` é o `exec_id` que viaja no fio. Guarda a linha JÁ REDIGIDA (nunca a crua) e a saída JÁ
-- REDIGIDA e cortada: o que o operador digitou pode ter sido um segredo, e uma linha recusada por parecer credencial
-- fica aqui só como auditoria, com `linha_redigida` marcada e o motivo em `reason`.
--   created     = aceito pela rota, na fila do worker
--   dispatched  = enviado ao agente, sem confirmação de início
--   running     = o agente confirmou que o processo existe
--   succeeded | failed | timed_out | cancelled = desfechos conhecidos
--   uncertain   = o canal ou o agente caiu no meio: o comando pode ter feito efeito; NUNCA é repetido às cegas
--   rejected    = recusado antes de ser despachado (credencial na linha, interruptor, worker sem a feature)
CREATE TABLE worker_comandos (
    id               TEXT PRIMARY KEY,
    worker_id        TEXT NOT NULL,
    requested_by     TEXT NOT NULL,             -- nome da sessão do operador; nunca texto vindo do corpo do pedido
    idempotency_key  TEXT NOT NULL UNIQUE,
    modo             TEXT NOT NULL,             -- linha | argv
    linha_redigida   TEXT NOT NULL,
    pasta            TEXT,
    timeout_s        REAL NOT NULL,
    state            TEXT NOT NULL,
    exit_code        INTEGER,
    stdout           TEXT,                      -- já redigido e cortado
    stderr           TEXT,
    truncated        INTEGER NOT NULL DEFAULT 0,
    duration_ms      INTEGER,
    reason           TEXT,                      -- por que foi recusado ou o que deu errado (texto humano, sem segredo)
    created_at       TEXT NOT NULL,
    dispatched_at    TEXT,
    finished_at      TEXT
);

CREATE INDEX ix_worker_comandos_worker ON worker_comandos (worker_id, created_at);
CREATE INDEX ix_worker_comandos_estado ON worker_comandos (state);

-- O interruptor POR WORKER, decidido ao vivo no painel. Sem linha = desligado (o padrão de todos os lados). Vale só
-- junto do interruptor do central (`comando_remoto.ativo` no config.yaml) e do `comando_remoto: true` do agente.
CREATE TABLE worker_comando_remoto (
    worker_id     TEXT PRIMARY KEY,
    ligado        INTEGER NOT NULL DEFAULT 0,
    changed_by    TEXT NOT NULL,
    changed_at    TEXT NOT NULL
);
