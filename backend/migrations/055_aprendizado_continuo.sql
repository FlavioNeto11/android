-- Aprendizado contínuo (ADR-054): o livro de aprendizado com ciclo de vida, a trilha única, os sinais, a evidência,
-- as exposições das lições, o agregado diário durável e o backlog do que mais falha.
--
-- O livro é único na LEITURA e no CICLO DE VIDA, não na carga: receita (`recipes`), fluxo (`flows`), habilidade
-- (`skill_versions`) e memória (`memory_items`) continuam donos do próprio conteúdo. Aqui só entra o conhecimento sem
-- casa nativa (`learning_items`: tela, lição, voz, preferência) e o que é comum a todos (trilha, evidência, sinais).
-- Nada daqui altera `recipes`, `flows` nem `pending_approvals`.
--
-- Mesmo padrão da 041 à 043:
-- - sem CHECK nos estados e vocabulários (a lista muda sem migração; quem confere é o domínio);
-- - SEM chave estrangeira: a execução é purgada e a prova fica (`run_id`, `attempt_id`... são texto solto), e a
--   ferramenta de cópia entre bancos ordena por FK sem precisar resolver nada novo;
-- - filtro de tempo só por texto ISO-8601 (ordena igual nos dois dialetos);
-- - escopo em TEXT NOT NULL DEFAULT '' ('' = qualquer): com coluna anulável o índice único não deduplica, porque
--   NULLs são distintos no SQLite e no PostgreSQL (o precedente é `recipes.variant`).
--
-- Colunas novas em `attempts` e `steps` nascem NULAS e ficam assim no legado: a leitura classifica o que é antigo pelo
-- mesmo classificador puro (`modules/learning/domain/falhas.py`) e o marca como retroativo, sem gravar.
--
-- Sem BEGIN/COMMIT: o executor de migrações já abre a transação.

-- Tipo da falha no vocabulário fechado de `FailureKind`; a tela reconhecida na última observação.
ALTER TABLE attempts ADD COLUMN failure_kind TEXT;
ALTER TABLE attempts ADD COLUMN failure_screen TEXT;     -- tela do pacote declarado, ou 'desconhecida:<sig8>'
ALTER TABLE steps ADD COLUMN failure_kind TEXT;          -- o tipo da tentativa que decidiu o desfecho final
CREATE INDEX IF NOT EXISTS ix_attempts_failure_kind ON attempts(failure_kind, finished_at);

-- 1) Conhecimento sem casa nativa. candidate | validated | published | deprecated | disabled (SkillState sem draft).
--    O item nasce congelado: mudar o conteúdo é criar outro com `parent_id`.
CREATE TABLE IF NOT EXISTS learning_items (
    id                TEXT PRIMARY KEY,               -- 'li-<token>'
    kind              TEXT NOT NULL,                  -- tela | licao | voz | preferencia
    state             TEXT NOT NULL DEFAULT 'candidate',
    state_detail      TEXT,                           -- em_prova | fila_de_prova | medida:<efeito> | absorvida:<commit> | contradita
    scope_app         TEXT NOT NULL DEFAULT '',
    scope_capability  TEXT NOT NULL DEFAULT '',       -- '*' = etapa livre
    scope_step_hash   TEXT NOT NULL DEFAULT '',       -- steps.template_hash
    scope_role        TEXT NOT NULL DEFAULT '',       -- actor | planner | writer | resolver | classifier
    scope_profile_id  TEXT NOT NULL DEFAULT '',       -- só voz e preferência
    app_version       TEXT,                           -- a versão observada; só desempata
    side_effect       INTEGER NOT NULL DEFAULT 0,     -- bits do D1, calculados pelo domínio na criação; nenhuma rota os edita
    human_origin      INTEGER NOT NULL DEFAULT 0,
    content           TEXT NOT NULL,                  -- JSON canônico, sem segredo e sem texto livre de tela
    content_hash      TEXT NOT NULL,                  -- sha256 do JSON canônico (a regra de skill_versions)
    summary           TEXT NOT NULL,                  -- na lição, o texto exato que vai ao prompt
    tokens            INTEGER,
    source_kind       TEXT NOT NULL,
    provenance        TEXT NOT NULL DEFAULT '{}',     -- JSON: ids de origem (até 20), commit, regra e versões
    evidence_for      INTEGER NOT NULL DEFAULT 0,     -- contadores em cache; a verdade está em learning_evidence
    evidence_against  INTEGER NOT NULL DEFAULT 0,
    distinct_runs     INTEGER NOT NULL DEFAULT 0,
    distinct_devices  INTEGER NOT NULL DEFAULT 0,
    parent_id         TEXT,                           -- sem FK (nem para a própria tabela)
    created_by        TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    updated_at        TEXT,
    state_at          TEXT,
    state_by          TEXT,
    last_used_at      TEXT
);
-- PARCIAL: a mesma lição viva só soma evidência, e o conteúdo desligado pode ser reaprendido (o veto é do domínio).
CREATE UNIQUE INDEX IF NOT EXISTS ux_learning_items_vivo ON learning_items(kind, scope_app, scope_capability,
    scope_step_hash, scope_role, scope_profile_id, content_hash) WHERE state IN ('candidate', 'validated', 'published');
CREATE INDEX IF NOT EXISTS ix_learning_items_consumo ON learning_items(kind, state, scope_app, scope_capability);

-- 2) Trilha única de transições: itens do livro, receitas ('receita:<id>') e fluxos ('fluxo:<id>'). As habilidades
--    continuam na trilha delas (skill_version_transitions). Em tabela, e não em `events`, que tem retenção.
CREATE TABLE IF NOT EXISTS learning_transitions (
    id            {{PK_AUTO}},
    item_ref      TEXT NOT NULL,
    item_kind     TEXT NOT NULL,
    content_hash  TEXT,                               -- o do conteúdo naquele momento: é a base do veto
    scope_key     TEXT NOT NULL DEFAULT '',           -- o escopo canônico do conteúdo (veto é por conteúdo E escopo)
    app_version   TEXT,                               -- o veto do sistema cai quando a versão do app muda
    from_state    TEXT,
    to_state      TEXT NOT NULL,
    reason        TEXT NOT NULL,
    decided_by    TEXT NOT NULL,                      -- 'sistema' ou a sessão do painel; nunca vazio
    decided_at    TEXT NOT NULL,
    run_id        TEXT                                -- a execução que causou a transição do sistema, quando houve
);
CREATE INDEX IF NOT EXISTS ix_learning_transitions_item ON learning_transitions(item_ref, id);
CREATE INDEX IF NOT EXISTS ix_learning_transitions_hash ON learning_transitions(content_hash, scope_key);

-- 3) Sinais: o que hoje ninguém guarda estruturado (os `events` morrem em 14 dias). Vocabulário fechado no domínio.
CREATE TABLE IF NOT EXISTS learning_signals (
    id             {{PK_AUTO}},
    kind           TEXT NOT NULL,
    polarity       TEXT NOT NULL DEFAULT 'neutral',   -- positive | negative | neutral
    verdict        TEXT,                              -- certo | errado (só no feedback)
    reason         TEXT,                              -- vocabulário fechado
    note           TEXT,                              -- até 500 caracteres, redigida; NULL quando recusada
    note_refused   INTEGER NOT NULL DEFAULT 0,        -- 1 = a nota parecia credencial e não foi gravada
    source_ref     TEXT NOT NULL,                     -- 'run:<id>', 'objective:<id>', 'approval:<id>', 'attempt:<id>'...
    created_by     TEXT NOT NULL,                     -- 'sistema' ou o operador; nunca nulo, então deduplica
    run_id         TEXT,
    objective_id   TEXT,
    step_id        TEXT,
    attempt_id     TEXT,
    instance_id    TEXT,
    profile_id     TEXT,
    app_package    TEXT NOT NULL DEFAULT '',
    capability     TEXT NOT NULL DEFAULT '',
    step_hash      TEXT,
    failure_kind   TEXT,
    step_verified  INTEGER,                           -- a etapa estava verified=1 quando o sinal chegou?
    data           TEXT NOT NULL DEFAULT '{}',        -- JSON, sem texto de tela
    simulated      INTEGER NOT NULL DEFAULT 0,        -- de runs.simulated
    created_at     TEXT NOT NULL,
    updated_at     TEXT
);
-- Varredura idempotente sem cursor (INSERT ... ON CONFLICT DO NOTHING) e voto como upsert por pessoa e por item.
CREATE UNIQUE INDEX IF NOT EXISTS ux_learning_signals ON learning_signals(kind, source_ref, created_by);
CREATE INDEX IF NOT EXISTS ix_learning_signals_grupo ON learning_signals(app_package, capability, kind, created_at);
CREATE INDEX IF NOT EXISTS ix_learning_signals_run ON learning_signals(run_id);

-- 4) Evidência: liga um item (do livro ou nativo) a uma observação. Só simulated=0 conta para publicar.
CREATE TABLE IF NOT EXISTS learning_evidence (
    id           {{PK_AUTO}},
    item_ref     TEXT NOT NULL,
    stance       TEXT NOT NULL,                       -- for | against | conflict
    origin_ref   TEXT NOT NULL,                       -- 'attempt:<id>' | 'step:<id>' | 'signal:<id>' | 'run:<id>'
    run_id       TEXT,
    instance_id  TEXT,
    app_version  TEXT,
    simulated    INTEGER NOT NULL,
    detail       TEXT,                                -- até 200 caracteres, sem texto de tela
    observed_at  TEXT NOT NULL
);
-- `origin_ref` NÃO NULO: é o que faz o índice único deduplicar a mesma observação.
CREATE UNIQUE INDEX IF NOT EXISTS ux_learning_evidence ON learning_evidence(item_ref, origin_ref, stance);
CREATE INDEX IF NOT EXISTS ix_learning_evidence_item ON learning_evidence(item_ref, id);

-- 5) Exposições: a lição que foi ao prompt (braço `with`) ou ficou de fora de propósito (`holdout`).
CREATE TABLE IF NOT EXISTS learning_exposures (
    item_id       TEXT NOT NULL,
    unit_id       TEXT NOT NULL,                      -- 'step:<id>' no ator, 'plan:<run_id>' no planejador
    role          TEXT NOT NULL,
    arm           TEXT NOT NULL,                      -- with | holdout
    tokens        INTEGER NOT NULL DEFAULT 0,         -- 0 no holdout
    run_id        TEXT,
    objective_id  TEXT,
    app_package   TEXT,
    capability    TEXT,
    created_at    TEXT NOT NULL,
    -- preenchidos no digest da execução, antes da purga de ai_calls:
    outcome       TEXT,
    failure_kind  TEXT,
    ai_calls      INTEGER,
    usd           REAL,
    seconds       REAL,
    replanned     INTEGER,
    filled_at     TEXT,
    PRIMARY KEY (item_id, unit_id, role)
);
CREATE INDEX IF NOT EXISTS ix_learning_exposures_run ON learning_exposures(run_id);

-- 6) Agregado diário DURÁVEL: ai_calls morre em `log_retention_days`, o dia agregado fica. Recalculado por inteiro
--    (DELETE do dia + INSERT) só para dias ainda intactos; nunca no caminho quente. Só execuções reais.
CREATE TABLE IF NOT EXISTS learning_daily (
    day             TEXT NOT NULL,                    -- AAAA-MM-DD (UTC)
    app_package     TEXT NOT NULL,
    capability      TEXT NOT NULL,                    -- '*' = etapa livre
    failure_kind    TEXT NOT NULL,                    -- '' no sucesso
    driven_by       TEXT NOT NULL,                    -- ai | recipe | recipe+ai | ''
    attempts        INTEGER NOT NULL DEFAULT 0,
    steps           INTEGER NOT NULL DEFAULT 0,
    ai_calls        INTEGER NOT NULL DEFAULT 0,
    usd             REAL NOT NULL DEFAULT 0,
    seconds         REAL NOT NULL DEFAULT 0,
    interventions   INTEGER NOT NULL DEFAULT 0,
    human_negative  INTEGER NOT NULL DEFAULT 0,
    computed_at     TEXT NOT NULL,
    PRIMARY KEY (day, app_package, capability, failure_kind, driven_by)
);

-- 7) Backlog da plataforma: o que mais falha e as propostas, com a prova da correção.
CREATE TABLE IF NOT EXISTS learning_backlog (
    id               TEXT PRIMARY KEY,                -- 'fk-' + sha1(cluster_key)[:10]
    category         TEXT NOT NULL,                   -- falha | proposta
    cluster_key      TEXT NOT NULL UNIQUE,
    app_package      TEXT,
    capability       TEXT,
    failure_kind     TEXT,
    failure_screen   TEXT,
    title            TEXT NOT NULL,
    state            TEXT NOT NULL DEFAULT 'open',    -- open | triaged | planned | fixed_pending_proof | fixed | reopened | wontfix
    plan_item        TEXT,
    fixed_in_commit  TEXT,
    fixed_at         TEXT,
    baseline         TEXT,                            -- JSON: taxa e custo das 4 semanas antes da correção
    verification     TEXT,                            -- JSON: n, taxa depois, ids
    reopened_count   INTEGER NOT NULL DEFAULT 0,
    parent_id        TEXT,
    first_seen       TEXT NOT NULL,
    last_seen        TEXT NOT NULL,
    notes            TEXT,
    updated_by       TEXT,
    updated_at       TEXT
);
CREATE INDEX IF NOT EXISTS ix_learning_backlog_estado ON learning_backlog(state, category);
