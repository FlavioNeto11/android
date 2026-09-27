-- Habilidade (SkillDefinition) e versões imutáveis (SkillVersion): docs/design/evolucao-arquitetural.md §10, ADR-034.
--
-- `flows` continua sendo o que é: a habilidade LEGADA. Nada aqui copia, altera ou apaga linha de `flows`,
-- `recipes` ou `training_sessions`; o adaptador de leitura apresenta cada fluxo como a versão 1 de uma habilidade
-- `flow:<id>` (ADR-037). Uma habilidade nova só nasce aqui.
--
-- Sem CHECK nos estados (mesmo motivo da 041: a lista muda sem migração). Sem ciclo de chave estrangeira: a versão
-- publicada NÃO é um ponteiro em `skill_definitions` (a ferramenta de cópia entre bancos ordena por FK e não
-- resolve ciclo); "uma publicada por habilidade" é índice único parcial, como 008 e 018 já fazem.
--
-- Sem BEGIN/COMMIT: o executor de migrações já abre a transação.

CREATE TABLE IF NOT EXISTS skill_definitions (
    id              TEXT PRIMARY KEY,               -- slug estável (`ig.abrir_conversa`); `flow:` é do adaptador
    name            TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    app_id          TEXT,                           -- app principal (sem FK, como `flows.app_id`)
    legacy_flow_id  TEXT,                           -- fluxo adotado por esta habilidade (sem FK: fluxo se apaga)
    created_by      TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_skill_definitions_fluxo ON skill_definitions(legacy_flow_id)
    WHERE legacy_flow_id IS NOT NULL;

-- draft | candidate | validated | published | deprecated | disabled. Fora de `draft` o conteúdo não muda.
CREATE TABLE IF NOT EXISTS skill_versions (
    id               TEXT PRIMARY KEY,              -- '<skill_id>@<version>'
    skill_id         TEXT NOT NULL REFERENCES skill_definitions(id),
    version          INTEGER NOT NULL,
    state            TEXT NOT NULL DEFAULT 'draft',
    schema_version   INTEGER NOT NULL DEFAULT 1,    -- 1 = automation/v1alpha1; 0 = plano legado (fluxo adotado)
    content          TEXT NOT NULL,                 -- JSON canônico: parâmetros, nós, ResourceSpec, critérios
    content_hash     TEXT NOT NULL,                 -- sha256 do JSON canônico (chaves ordenadas)
    command_template TEXT,                          -- copiado de `content` para casar comando sem abrir o JSON
    match_key        TEXT,                          -- comando-modelo normalizado (mesma regra de `flows.match_key`)
    parent_version   INTEGER,                       -- de qual versão esta derivou
    source_kind      TEXT NOT NULL,                 -- legacy_flow | teaching | run | manual | import
    source_ref       TEXT,                          -- id do fluxo, do ensino ou da execução de origem
    provenance       TEXT NOT NULL DEFAULT '{}',    -- JSON: modelo que generalizou, candidata aceita, notas
    created_by       TEXT,
    created_at       TEXT NOT NULL,
    state_at         TEXT NOT NULL,
    state_by         TEXT,
    state_detail     TEXT,
    UNIQUE (skill_id, version)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_skill_versions_publicada ON skill_versions(skill_id) WHERE state = 'published';
CREATE UNIQUE INDEX IF NOT EXISTS ux_skill_versions_comando ON skill_versions(match_key)
    WHERE state = 'published' AND match_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_skill_versions_estado ON skill_versions(state, skill_id);

-- Trilha das transições de estado. Em tabela, e não em `events`: `events` tem retenção (EventLog.purge_older_than).
CREATE TABLE IF NOT EXISTS skill_version_transitions (
    id          {{PK_AUTO}},
    version_id  TEXT NOT NULL REFERENCES skill_versions(id),
    from_state  TEXT,
    to_state    TEXT NOT NULL,
    reason      TEXT,
    decided_by  TEXT,                               -- nome da sessão do painel (padrão da 035) ou 'sistema'
    decided_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_skill_version_transitions ON skill_version_transitions(version_id, id);

-- Apps exigidos pela versão, derivados de `content`. Sem FK para `apps` de propósito (ao contrário da 025): a
-- linha espelha conteúdo imutável, e apagar o app não pode sumir com a exigência.
CREATE TABLE IF NOT EXISTS skill_version_apps (
    version_id  TEXT NOT NULL REFERENCES skill_versions(id),
    app_id      TEXT NOT NULL,
    PRIMARY KEY (version_id, app_id)
);
CREATE INDEX IF NOT EXISTS ix_skill_version_apps_app ON skill_version_apps(app_id);

-- A quem a habilidade vale: decisão de distribuição, não de conteúdo, por isso fica na definição (muda sem versão
-- nova). Mesmo formato de `flow_scope` (038). Sem linha = vale para todos.
CREATE TABLE IF NOT EXISTS skill_scope (
    skill_id    TEXT NOT NULL REFERENCES skill_definitions(id) ON DELETE CASCADE,
    profile_id  TEXT,
    group_id    TEXT
);
CREATE INDEX IF NOT EXISTS ix_skill_scope_skill ON skill_scope(skill_id);
