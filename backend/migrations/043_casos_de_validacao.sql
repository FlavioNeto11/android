-- Casos de validação (ValidationCase) e o que se OBSERVOU em cada um: docs/design/evolucao-arquitetural.md §10.7.
--
-- Mesmo desenho de `app_release_validations` (011): a promoção de uma versão lê observações registradas, com
-- aparelho, horário e nível de prova, nunca um booleano solto. `not_run` é a AUSÊNCIA de linha.
--
-- Segredo não entra em `parameters`: credencial é citada pelo NOME, como em `run_secrets` (040).
--
-- Sem BEGIN/COMMIT: o executor de migrações já abre a transação.

CREATE TABLE IF NOT EXISTS skill_validation_cases (
    id             TEXT PRIMARY KEY,
    skill_id       TEXT NOT NULL REFERENCES skill_definitions(id),
    name           TEXT NOT NULL,
    kind           TEXT NOT NULL,                   -- replay | simulated | device | negative
    since_version  INTEGER,                         -- vale a partir desta versão (nulo = todas)
    until_version  INTEGER,                         -- aposentado depois desta versão (nulo = em vigor)
    parameters     TEXT NOT NULL DEFAULT '{}',      -- JSON dos valores de entrada, sem segredo
    preconditions  TEXT NOT NULL DEFAULT '{}',      -- JSON: app, tela, sessão exigidos
    expected       TEXT NOT NULL,                   -- JSON: desfecho esperado e provas (pós-condições)
    source_kind    TEXT,                            -- demonstration | run | correction | manual
    source_ref     TEXT,
    status         TEXT NOT NULL DEFAULT 'active',  -- active | retired
    created_by     TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_skill_validation_cases_skill ON skill_validation_cases(skill_id, status);

-- Caso `device` só conta para `validated` com `proof='real'` (decisão P4); `simulated` basta para os demais.
CREATE TABLE IF NOT EXISTS skill_validation_results (
    id           {{PK_AUTO}},
    case_id      TEXT NOT NULL REFERENCES skill_validation_cases(id),
    version_id   TEXT NOT NULL REFERENCES skill_versions(id),
    proof        TEXT NOT NULL,                     -- real | simulated
    outcome      TEXT NOT NULL,                     -- passed | failed | uncertain | blocked
    run_id       TEXT,                              -- sem FK: a execução pode ser purgada, a prova fica
    instance_id  TEXT,
    physical_id  TEXT,                              -- impressão digital do aparelho (020)
    app_version  TEXT,
    variant      TEXT,
    detail       TEXT,
    observed_at  TEXT NOT NULL,
    observed_by  TEXT
);
CREATE INDEX IF NOT EXISTS ix_skill_validation_results ON skill_validation_results(version_id, case_id, id);
CREATE INDEX IF NOT EXISTS ix_skill_validation_results_run ON skill_validation_results(run_id);
