-- Saídas de etapa (ADR-058, contrato C2 da terceira evolução, 29/09/2026): o valor que uma etapa LÊ e outra USA.
--
-- O comando entre aplicativos ("leia o assunto do último e-mail no Outlook e procure o perfil citado no Instagram";
-- código de verificação NUNCA, ADR-009) precisa de um lugar para o valor lido
-- numa etapa chegar à seguinte. Até aqui só existiam as variáveis da execução (parâmetros, dados da persona) e os
-- itens da coleta (`for_each`); nada guardava um valor produzido no meio do plano. Esta migração cria só a FORMA; quem
-- grava é o executor (Fase 24) e quem resolve `{{saida:<nome>}}` antes da etapa é o 24.3.
--
-- `step_outputs`: uma linha por NOME no objetivo (`UNIQUE(objective_id, name)`: a última escrita vence — a etapa
--   repetida depois de uma falha reescreve o valor, não duplica).
--     name       = `^[a-z][a-z0-9_]{0,39}$`, conferido no domínio (`Repository.save_step_output`), não em CHECK;
--     value      = até 2000 caracteres, também conferido no domínio; nunca credencial (a senha só passa por
--                  `type_secret` e não vira saída);
--     value_kind = text | number | url | list (CHECK: o vocabulário é do contrato, não muda sem migração);
--     app_id     = o app em que a etapa leu o valor (NULL = o app do plano).
--   Chaves estrangeiras como as de `steps`: execução, objetivo e etapa, com `ON DELETE CASCADE` — a saída não
--   sobrevive à etapa que a produziu.
-- `steps.saidas`: os nomes que a etapa declara produzir (`PlanStep.saidas`), em JSON; NULL quando a etapa não produz
--   nada, que é todo o legado. O plano (`runs.plan`, `plan_versions.steps`) já guarda a lista pelo JSON do `PlanStep`;
--   a coluna é para quem lê a LINHA da etapa (o executor) não precisar reabrir o plano.
--
-- Compatível com SQLite e PostgreSQL: só TEXT, `REFERENCES … ON DELETE CASCADE` (os dois aceitam) e `CHECK`.
-- Sem BEGIN/COMMIT: o executor de migrações já abre a transação.

CREATE TABLE step_outputs (
    id            TEXT PRIMARY KEY,                   -- '<objective_id>:<name>': estável, a regravação é o mesmo id
    run_id        TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    objective_id  TEXT NOT NULL REFERENCES objectives(id) ON DELETE CASCADE,
    step_id       TEXT NOT NULL REFERENCES steps(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    value         TEXT NOT NULL,
    value_kind    TEXT NOT NULL DEFAULT 'text' CHECK (value_kind IN ('text', 'number', 'url', 'list')),
    app_id        TEXT,
    created_at    TEXT NOT NULL,
    UNIQUE (objective_id, name)
);
CREATE INDEX ix_step_outputs_step ON step_outputs(step_id);

ALTER TABLE steps ADD COLUMN saidas TEXT;               -- JSON: nomes das saídas que a etapa produz; NULL = nenhuma
