-- Sessão de ensino v2 (TeachingSession): instrução, várias demonstrações, correções, perguntas e respostas,
-- candidatas e status de validação. docs/design/evolucao-arquitetural.md §13.
--
-- Tabelas NOVAS em vez de estender `training_sessions` (038): lá `instance_id` é NOT NULL e o gravador casa
-- "gravando neste aparelho" por `status='recording'`. Uma sessão v2 começa sem aparelho e junta demonstrações de
-- vários; afrouxar o NOT NULL no SQLite exige reconstruir a tabela (como a 010 fez com `recipes`). Aqui cada
-- demonstração APONTA para uma gravação v1, que continua sendo feita pelo mesmo gravador, sem mudança.
--
-- Texto da pessoa (instrução, respostas, correções) passa pela redação antes de chegar aqui, como a gravação faz.
--
-- Sem BEGIN/COMMIT: o executor de migrações já abre a transação.

-- open | demonstrating | asking | proposing | validating | ready | published | discarded
CREATE TABLE IF NOT EXISTS teaching_sessions (
    id                 TEXT PRIMARY KEY,
    instruction        TEXT NOT NULL,
    skill_id           TEXT REFERENCES skill_definitions(id),   -- nulo = habilidade nova
    base_version       INTEGER,                                 -- ensino que melhora a versão N
    app_id             TEXT,
    profile_id         TEXT,
    status             TEXT NOT NULL DEFAULT 'open',
    validation_status  TEXT NOT NULL DEFAULT 'none',            -- none | running | passed | failed | partial
    result_version_id  TEXT REFERENCES skill_versions(id),      -- a versão que o ensino gerou
    operator           TEXT,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    closed_at          TEXT
);
CREATE INDEX IF NOT EXISTS ix_teaching_sessions_status ON teaching_sessions(status, updated_at);

CREATE TABLE IF NOT EXISTS teaching_demonstrations (
    id                   TEXT PRIMARY KEY,
    teaching_id          TEXT NOT NULL REFERENCES teaching_sessions(id) ON DELETE CASCADE,
    seq                  INTEGER NOT NULL,
    kind                 TEXT NOT NULL DEFAULT 'recording',     -- recording | run
    training_session_id  TEXT REFERENCES training_sessions(id), -- a gravação v1 (entradas em training_inputs)
    run_id               TEXT,                                  -- execução comprovada usada como exemplo
    instance_id          TEXT,
    -- JSON com versão, variante e assinatura do app NO MOMENTO da gravação: lidas no `save`, como o treino v1 faz,
    -- a receita destilada ficaria presa à versão de depois, não à que foi demonstrada.
    app_snapshot         TEXT,
    note                 TEXT,
    created_at           TEXT NOT NULL,
    UNIQUE (teaching_id, seq)
);
-- Uma gravação pertence a no máximo um ensino.
CREATE UNIQUE INDEX IF NOT EXISTS ux_teaching_demo_gravacao ON teaching_demonstrations(training_session_id)
    WHERE training_session_id IS NOT NULL;

-- Conversa do ensino: instrução, pergunta da IA, resposta, correção. `id` inteiro dá a ordem.
CREATE TABLE IF NOT EXISTS teaching_turns (
    id            {{PK_AUTO}},
    teaching_id   TEXT NOT NULL REFERENCES teaching_sessions(id) ON DELETE CASCADE,
    kind          TEXT NOT NULL,                    -- instruction | question | answer | correction | note
    author        TEXT NOT NULL,                    -- person | ai | compiler | system
    reply_to      INTEGER,                          -- resposta -> pergunta (id desta tabela, sem FK)
    target        TEXT,                             -- JSON: nó, etapa ou entrada a que se refere
    body          TEXT,                             -- texto já redigido
    payload       TEXT,                             -- JSON estruturado (correção: campo e valor novo)
    candidate_id  TEXT,                             -- candidata a que a pergunta ou a correção se aplica
    created_by    TEXT,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_teaching_turns ON teaching_turns(teaching_id, id);

-- Propostas (da IA ou da pessoa). Só a ACEITA vira `skill_versions`; as rejeitadas não gastam número de versão.
CREATE TABLE IF NOT EXISTS teaching_candidates (
    id                 TEXT PRIMARY KEY,
    teaching_id        TEXT NOT NULL REFERENCES teaching_sessions(id) ON DELETE CASCADE,
    seq                INTEGER NOT NULL,
    -- Envelope `{document, annotations}`: `document` é o SkillDocument v1alpha1 e é SÓ ELE que vai para
    -- `skill_versions.content` na aceitação; `annotations` guarda evidência por nó, descartes, suposições e
    -- exemplos de parâmetro, que não fazem parte do que se valida nem do hash da versão.
    content            TEXT NOT NULL,
    content_hash       TEXT NOT NULL,
    generated_by       TEXT NOT NULL,               -- ai:<modelo> | person | merge
    status             TEXT NOT NULL DEFAULT 'proposed',   -- proposed | rejected | accepted | superseded
    validation_status  TEXT NOT NULL DEFAULT 'none',
    version_id         TEXT REFERENCES skill_versions(id),
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    UNIQUE (teaching_id, seq)
);
