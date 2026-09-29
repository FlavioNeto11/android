-- Proteção de contas (ADR-055, 29/09/2026): quarentena do aparelho com conta travada logada e rastro do status.
--
-- O dono, 28/09: "toda vez que para na tela de confirmar se você é humano é uma confirmação que a conta está
-- bloqueada; essa é uma das formas de perder a conta". Cinco das oito contas do Instagram estão bloqueadas. O que
-- faltava no esquema para a plataforma respeitar isso:
--
-- `device_locked_accounts`: o MARCADOR, por aparelho, de que ali está logada uma conta travada. É do aparelho e não
--   do vínculo: o android-04 ficou no ar com o felipe no desafio e SEM vínculo nenhum (o ADR-029 tinha bloqueado o
--   perfil e o vínculo tinha saído), e o parque aceitaria vincular outra persona ali, abrir o app, entregar versão e
--   até resetar. Por isso sobrevive ao desvínculo e à remoção do perfil — sem chave estrangeira: `profile_id` e
--   `app_id` são só referência — e só sai quando uma pessoa resolve (`resolved_at`/`resolved_by`) ou quando o disco
--   do aparelho é apagado (aí a conta não está mais logada lá).
--     handle   = a conta logada, normalizada (sem '@', minúsculas);
--     origin   = observado (a tela foi lida) | declarado (uma pessoa disse) | regra (uma regra decidiu);
--     seen_by  = quem viu ou declarou; evidence = o que foi visto (texto curto, nunca segredo);
--     since    = desde quando se sabe.
--   No máximo um marcador ABERTO por (aparelho, conta): `ux_locked_account_aberto`.
-- `instagram_profiles.blocked_at/blocked_evidence/blocked_origin`: o status `blocked` existia desde a 008 sem
--   quando, por quê nem de onde. São preenchidos a cada entrada em `blocked` e zerados quando a pessoa reativa (o
--   histórico fica nos eventos `profile.status`). Os perfis já bloqueados ficam NULOS: não se inventa data.
-- `instances.account_label_origin`: de onde veio o `account_label`. NULL = configuração ou digitado (o de sempre);
--   `vinculo`/`marcador` = derivado pela plataforma. Sem isto não havia como desfazer um rótulo derivado depois do
--   desvínculo sem apagar os rótulos da configuração — e o `qa-user-04` do android-04 (que tem o felipe) enganou um
--   experimento que tocou o desafio.
--
-- Carga: o marcador que o dono viu em 28/09/2026 às 21:36 (horário da máquina central, -03:00): o android-04 com o
-- felipe.nogueira93762026 na tela "Confirm you are human". Só entra onde o aparelho e o perfil existem (o banco do
-- ambiente central); banco novo e de teste nascem sem marcador.
--
-- Compatível com SQLite e PostgreSQL: tipos TEXT, `{{PK_AUTO}}`, índice único PARCIAL (os dois aceitam `WHERE`),
-- `CHECK` em `ADD COLUMN` (os dois aceitam) e `INSERT … SELECT … WHERE EXISTS` (sem `ON CONFLICT` depois de
-- `SELECT`, que o SQLite confunde com JOIN — a lição da 049).

CREATE TABLE device_locked_accounts (
    id           {{PK_AUTO}},
    instance_id  TEXT NOT NULL,
    handle       TEXT NOT NULL,
    profile_id   TEXT,
    app_id       TEXT,
    origin       TEXT NOT NULL CHECK (origin IN ('observado', 'declarado', 'regra')),
    seen_by      TEXT,
    evidence     TEXT,
    since        TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    resolved_at  TEXT,
    resolved_by  TEXT,
    resolution   TEXT
);
CREATE UNIQUE INDEX ux_locked_account_aberto ON device_locked_accounts(instance_id, handle) WHERE resolved_at IS NULL;
CREATE INDEX ix_locked_account_instance ON device_locked_accounts(instance_id);

ALTER TABLE instagram_profiles ADD COLUMN blocked_at TEXT;
ALTER TABLE instagram_profiles ADD COLUMN blocked_evidence TEXT;
ALTER TABLE instagram_profiles ADD COLUMN blocked_origin TEXT
    CHECK (blocked_origin IS NULL OR blocked_origin IN ('observado', 'declarado', 'regra'));
ALTER TABLE instances ADD COLUMN account_label_origin TEXT;                -- NULL | vinculo | marcador

INSERT INTO device_locked_accounts(instance_id, handle, profile_id, app_id, origin, seen_by, evidence, since,
                                   created_at)
SELECT 'android-04', 'felipe.nogueira93762026', p.id,
       (SELECT MIN(a.app_id) FROM profile_accounts a
         WHERE a.profile_id = p.id AND lower(a.handle) = 'felipe.nogueira93762026'),
       'declarado', 'dono',
       'Tela "Confirm you are human" do Instagram vista pelo dono no android-04 em 28/09/2026 21:36 (-03:00); o '
       || 'aparelho estava no ar com a conta logada e sem vínculo.',
       '2026-09-29T00:36:00.000Z', '2026-09-29T00:36:00.000Z'
  FROM instagram_profiles p
 WHERE lower(p.username) = 'felipe.nogueira93762026'
   AND EXISTS (SELECT 1 FROM instances i WHERE i.id = 'android-04');
