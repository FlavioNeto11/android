-- A Conta é a entidade única (segunda evolução, onda B; ADR-040): persona × app, com credencial, consentimento e
-- sessão POR APARELHO. O Instagram passa a ser uma conta como as outras — a diferença é que o app dele tem
-- `SessionProvider` (login determinístico, ADR-039). A credencial que a automação digita vem da conta da persona,
-- não mais de um campo da execução (`run_secrets`, 040, fica sem escritor e sai numa migração posterior).
--
-- O que muda no esquema:
-- - `account_credentials` (037) ganha o que `instagram_credentials` (008) sempre teve — `status`, `failed_attempts`,
--   `blocked_until`, `created_at`, `last_used_at` — e o consentimento POR CONTA (`consent_at`, `consent_by`): é a
--   pessoa dizendo, ao guardar a senha, que a automação pode digitá-la (ADR-025 passa de "por execução" a "por
--   conta"). Nulo = ainda não consentiu; `type_secret` recusa.
-- - `profile_accounts.host`: conta de PORTAL ou site (app `chrome` + `host='portal.exemplo.gov.br'`). É onde a
--   credencial pode ser digitada no navegador; a unicidade passa a (perfil, app, host), com `NULL` valendo como ''.
-- - `account_sessions`: UMA sessão por (conta, aparelho). Conta já é (perfil, app), então é a sessão por
--   (perfil, app, aparelho) que o ADR-039 propunha. Vocabulário ÚNICO de status, o mesmo para app com e sem
--   provedor: unknown | session_ready | auth_required (o antigo `logged_out`) | auth_challenge | wrong_account |
--   needs_person. `profile_accounts.session_status` (037) deixa de ser FONTE: vira agregado que ninguém escreve
--   mais (a coluna fica; migração aplicada não se edita).
-- - `authentication_attempts.account_id`: a tentativa é da conta, não só do perfil.
--
-- Carga inicial, só quando o app do Instagram está registrado (mesma condição da 037): a conta Instagram de cada
-- perfil recebe a linha de `instagram_credentials` REUSANDO o mesmo `secret_ref` — o dado autenticado é a
-- referência; nada é recifrado — e `instagram_sessions` só onde há vínculo ATIVO entre o perfil e o aparelho da
-- sessão: sessão de aparelho que o perfil não tem mais (os perfis bloqueados pelo ADR-029) não vem, porque uma
-- sessão é do par (conta, aparelho) e esse par não existe mais. As credenciais migradas recebem
-- `consent_at = updated_at`, `consent_by = 'migração 049'` (design persona-e-parque §4.3, decisão 5): foram
-- cadastradas pelo dono no portal justamente para o "Conectar" automático digitá-las pelo canal sensível, e o
-- provedor de sessão passa a exigir o consentimento como o `type_secret` — recusá-las pararia o login de produção.
-- `instagram_credentials`/`instagram_sessions` ficam só leitura a partir daqui.
--
-- Compatível com SQLite e PostgreSQL: tipos TEXT/INTEGER, índice de expressão com a expressão entre parênteses
-- (o PostgreSQL exige; o SQLite aceita) e `INSERT … SELECT … WHERE NOT EXISTS` (idempotente nos dois; sem
-- `ON CONFLICT` depois de `SELECT`, que o SQLite confunde com JOIN).

ALTER TABLE account_credentials ADD COLUMN status TEXT NOT NULL DEFAULT 'active';   -- active | invalid
ALTER TABLE account_credentials ADD COLUMN failed_attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE account_credentials ADD COLUMN blocked_until TEXT;
ALTER TABLE account_credentials ADD COLUMN created_at TEXT;
ALTER TABLE account_credentials ADD COLUMN last_used_at TEXT;
ALTER TABLE account_credentials ADD COLUMN consent_at TEXT;
ALTER TABLE account_credentials ADD COLUMN consent_by TEXT;

ALTER TABLE profile_accounts ADD COLUMN host TEXT;
DROP INDEX IF EXISTS ux_profile_accounts_app;
CREATE UNIQUE INDEX IF NOT EXISTS ux_profile_accounts_app_host
    ON profile_accounts(profile_id, app_id, (COALESCE(host, '')));

CREATE TABLE IF NOT EXISTS account_sessions (
    account_id      TEXT NOT NULL REFERENCES profile_accounts(id) ON DELETE CASCADE,
    instance_id     TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'unknown',
    observed_handle TEXT,
    verified_at     TEXT,
    detail          TEXT,
    unknown_streak  INTEGER NOT NULL DEFAULT 0,
    updated_at      TEXT NOT NULL,
    PRIMARY KEY (account_id, instance_id)
);
CREATE INDEX IF NOT EXISTS ix_account_sessions_instance ON account_sessions(instance_id);

ALTER TABLE authentication_attempts ADD COLUMN account_id TEXT;

-- 1) Perfil sem a conta do Instagram (cadastrado enquanto o app não estava registrado) ganha a dele, com o mesmo
--    id que a 037 dava: `acc-<perfil>`.
INSERT INTO profile_accounts(id, profile_id, app_id, handle, status, created_at, updated_at)
SELECT 'acc-' || p.id, p.id, a.id, p.username, 'active', p.created_at, p.updated_at
  FROM instagram_profiles p
  JOIN apps a ON a.package = 'com.instagram.android'
 WHERE NOT EXISTS (SELECT 1 FROM profile_accounts x
                    WHERE x.profile_id = p.id AND x.app_id = a.id AND x.host IS NULL);

-- 2) A credencial do Instagram vira a credencial da conta Instagram do perfil. Mesmo `secret_ref`, sem recifrar;
--    consentimento datado do cadastro, assinado pela migração.
INSERT INTO account_credentials(account_id, login_identifier, secret_ref, key_id, status, failed_attempts,
                                blocked_until, created_at, updated_at, last_used_at, consent_at, consent_by)
SELECT a.id, c.login_identifier, c.secret_ref, c.key_id, c.status, c.failed_attempts, c.blocked_until,
       c.created_at, c.updated_at, c.last_used_at, c.updated_at, 'migração 049'
  FROM instagram_credentials c
  JOIN apps ap ON ap.package = 'com.instagram.android'
  JOIN profile_accounts a ON a.profile_id = c.profile_id AND a.app_id = ap.id AND a.host IS NULL
 WHERE NOT EXISTS (SELECT 1 FROM account_credentials x WHERE x.account_id = a.id);

-- 3) A sessão do Instagram vira a sessão da conta NAQUELE aparelho — só com vínculo ativo.
INSERT INTO account_sessions(account_id, instance_id, status, observed_handle, verified_at, detail, unknown_streak,
                             updated_at)
SELECT a.id, s.instance_id, s.status, s.observed_username, s.verified_at, s.detail, COALESCE(s.unknown_streak, 0),
       s.updated_at
  FROM instagram_sessions s
  JOIN apps ap ON ap.package = 'com.instagram.android'
  JOIN profile_accounts a ON a.profile_id = s.profile_id AND a.app_id = ap.id AND a.host IS NULL
  JOIN device_profile_bindings b ON b.profile_id = s.profile_id AND b.instance_id = s.instance_id AND b.active = 1
 WHERE s.instance_id IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM account_sessions x WHERE x.account_id = a.id AND x.instance_id = s.instance_id);

-- 4) Conta de app SEM provedor com marcação da pessoa em `profile_accounts.session_status` (037): a marcação vai
--    para a sessão da conta no aparelho vinculado, no vocabulário único (`logged_out` → `auth_required`). As
--    contas do Instagram ficam de fora: ali a cópia da 037 é velha, e a verdade acabou de vir de
--    `instagram_sessions`. Em produção não havia conta sem provedor quando esta migração foi escrita.
INSERT INTO account_sessions(account_id, instance_id, status, observed_handle, verified_at, detail, unknown_streak,
                             updated_at)
SELECT a.id, b.instance_id,
       CASE a.session_status WHEN 'logged_out' THEN 'auth_required' ELSE a.session_status END,
       NULL, a.session_verified_at, a.session_detail, 0, a.updated_at
  FROM profile_accounts a
  JOIN device_profile_bindings b ON b.profile_id = a.profile_id AND b.active = 1
 WHERE a.session_status <> 'unknown'
   AND NOT EXISTS (SELECT 1 FROM apps ap WHERE ap.id = a.app_id AND ap.package = 'com.instagram.android')
   AND NOT EXISTS (SELECT 1 FROM account_sessions x WHERE x.account_id = a.id AND x.instance_id = b.instance_id);

-- 5) Cada tentativa de autenticação passa a apontar para a conta Instagram do perfil.
UPDATE authentication_attempts
   SET account_id = (SELECT a.id FROM profile_accounts a
                       JOIN apps ap ON ap.id = a.app_id
                      WHERE a.profile_id = authentication_attempts.profile_id
                        AND ap.package = 'com.instagram.android' AND a.host IS NULL)
 WHERE account_id IS NULL;
