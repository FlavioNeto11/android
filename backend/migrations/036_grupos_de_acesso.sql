-- Grupos de acesso (pedido do dono, 24/09): um conjunto de políticas por ação e de limites que se atribui a
-- quantos perfis (bots) se quiser.
--
-- Até aqui cada perfil tinha só o próprio `automation_policy` e, fora dele, o padrão do catálogo. Com o grupo,
-- a ordem passa a ser: o que o PERFIL mudou deliberadamente (chave presente no `automation_policy` dele) →
-- o que o GRUPO diz → o padrão do catálogo (`DEFAULT_LIMITS`, para limites). Nada do que já estava gravado nos
-- perfis muda de sentido: aquelas chaves continuam sendo as escolhas deliberadas de cada um e continuam
-- sobrepondo o grupo.
--
-- Um perfil pertence a no máximo UM grupo — dois grupos pedindo coisas diferentes para a mesma ação precisariam
-- de uma regra de precedência que ninguém definiu.
--
-- `capabilities` e `limits` são JSON esparsos, no mesmo formato de `instagram_profiles.automation_policy`: só o
-- que o grupo muda em relação ao padrão. Sem FOREIGN KEY na coluna nova: apagar um grupo desvincula os membros
-- explicitamente no serviço (SQLite e PostgreSQL divergem no ALTER TABLE ... REFERENCES).
--
-- Compatível com SQLite e PostgreSQL: só tipos TEXT e índices.

CREATE TABLE IF NOT EXISTS policy_groups (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    description   TEXT NOT NULL DEFAULT '',
    capabilities  TEXT NOT NULL DEFAULT '{}',
    limits        TEXT NOT NULL DEFAULT '{}',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_policy_groups_name ON policy_groups(name);

ALTER TABLE instagram_profiles ADD COLUMN policy_group_id TEXT;

CREATE INDEX IF NOT EXISTS ix_instagram_profiles_policy_group ON instagram_profiles(policy_group_id);
