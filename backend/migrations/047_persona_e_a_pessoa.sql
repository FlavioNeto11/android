-- @foreign_keys:off
-- A persona passa a ser a PESSOA, e a pessoa é a linha de `instagram_profiles` (evolução 2, onda A).
--
-- Até aqui havia duas tabelas para uma coisa só: `personas` (voz: resumo, traços, prompt) e `instagram_profiles`
-- (identidade: nome, nascimento, e-mail, conta do Instagram, vínculo, política). Uma persona podia existir sem
-- perfil (as 11 órfãs de produção) e um perfil sem persona (os 5 bloqueados do ADR-029). O modelo alvo é um só
-- objeto: a pessoa, com voz, biografia, identidade visual e as contas dela nos apps. O nome da tabela fica
-- (`037` explica: renomear quebraria ~30 pontos e o pacote do agente).
--
-- O que muda no esquema:
-- - `instagram_profiles` ganha `summary`, `traits` (SÓ a voz), `persona_prompt`, `gender`, `locale`, `biography`,
--   `visual` e `generation` (JSON em TEXT, como `automation_policy`);
-- - `username` passa a ser OPCIONAL: uma persona pode existir antes de ter conta em app nenhum. A convenção é
--   `''` = sem conta (a coluna segue `NOT NULL`, agora com `DEFAULT ''`; o DTO traduz `''` para `null`), e o
--   índice único `lower(username)` vira parcial (`WHERE username <> ''`).
--   No SQLite a tabela é RECONSTRUÍDA (molde da `010`, `recipes_novo`), e não por causa do `NOT NULL`: o banco de
--   produção nasceu com a 008 antiga, em que a coluna é `UNIQUE COLLATE NOCASE` (lido em `sqlite_master`,
--   27/09: `sqlite_autoindex_instagram_profiles_2`; a 028 registra a divergência). Essa UNIQUE de coluna não se
--   remove com `ALTER`, e recusaria a SEGUNDA pessoa sem conta — só em produção, nunca num banco novo nem no
--   CI. A reconstrução pede a marca `@foreign_keys:off` no topo: sem ela o `DROP TABLE` da tabela antiga
--   dispararia o `ON DELETE CASCADE` dos filhos (medido antes desta migração); com ela, o migrador desliga a
--   chave fora da transação e confere `PRAGMA foreign_key_check` antes do COMMIT. No PostgreSQL basta `ALTER`;
-- - `persona_id` e a FK para `personas` FICAM: viram o rastro da linha legada que a pessoa absorveu.
--
-- O que muda nos dados (a "dobra" de `personas`), só quando há linhas — banco vazio continua vazio:
-- (a) perfil COM `persona_id`: copia resumo, voz e prompt da persona para a linha do perfil;
-- (b) persona órfã cujo `name` bate (sem caixa, sem espaço nas pontas) com `display_name` ou com
--     `first_name || ' ' || last_name` de um perfil SEM `persona_id` (os 5 desvinculados em 27/09, ADR-029):
--     o perfil ganha o `persona_id` e recebe a dobra como em (a). Se dois nomes empatam, decide o menor id —
--     determinístico, e nunca duas personas no mesmo perfil (`ux_profiles_persona`);
-- (c) as demais órfãs viram linhas novas de `instagram_profiles` com `username = ''`, `display_name = name`,
--     nome e sobrenome separados no primeiro espaço, `status = 'active'`, `id = 'ig-' || persona.id` (único por
--     construção).
-- Em todos os grupos: `traits.appearance/visual_style/photo_scenario` saem de `traits` e vão para `visual`
-- (`PersonaTraits` tem `extra="forbid"`: chave desconhecida em `traits` quebraria `GET /personas`);
-- `biography` nasce com `tastes.interests` (de `traits.interests`) e `approx_age` (o "NN anos" do resumo — nunca
-- se inventa `birth_date`); `generation` recebe `{"source":"legacy_persona","persona_id":…}`, e é ela o
-- predicado de "já dobrado": rodar as instruções de novo não mexe em nada.
--
-- `personas` fica intacta e SEM LEITORES a partir do código desta migração; some numa migração posterior, junto
-- com a FK `instagram_profiles.persona_id` (no SQLite, outra reconstrução).

-- ---------------------------------------------------------------------------------------------- esquema
-- @dialect:sqlite
CREATE TABLE instagram_profiles_novo (
    id                TEXT PRIMARY KEY,
    username          TEXT NOT NULL DEFAULT '',                 -- '' = pessoa sem conta ainda
    display_name      TEXT,
    first_name        TEXT,
    last_name         TEXT,
    birth_date        TEXT,
    email             TEXT,
    persona_id        TEXT REFERENCES personas(id) ON DELETE SET NULL,   -- rastro da linha legada absorvida
    status            TEXT NOT NULL DEFAULT 'active',           -- active | blocked | disabled
    automation_policy TEXT NOT NULL DEFAULT '{}',
    metadata          TEXT NOT NULL DEFAULT '{}',
    last_verified_at  TEXT,
    last_activity_at  TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    offline_policy    TEXT,                                     -- 023
    policy_group_id   TEXT,                                     -- 036
    summary           TEXT,                                     -- quem é, numa frase
    traits            TEXT NOT NULL DEFAULT '{}',               -- JSON: SÓ a voz (`PersonaTraits`)
    persona_prompt    TEXT NOT NULL DEFAULT '',
    gender            TEXT,
    locale            TEXT,                                     -- pt-BR, en-US…
    biography         TEXT NOT NULL DEFAULT '{}',               -- JSON `PersonaBiography` (com schema_version)
    visual            TEXT NOT NULL DEFAULT '{}',               -- JSON `PersonaVisual`: aparência, estilo, cenário
    generation        TEXT NOT NULL DEFAULT '{}'                -- JSON: de onde a persona veio (manual | ai | legado)
);
-- Colunas nomeadas uma a uma: a ordem de `SELECT *` num banco que nasceu antes da 023/036 não é a deste arquivo.
INSERT INTO instagram_profiles_novo (id, username, display_name, first_name, last_name, birth_date, email,
                                     persona_id, status, automation_policy, metadata, last_verified_at,
                                     last_activity_at, created_at, updated_at, offline_policy, policy_group_id)
SELECT id, username, display_name, first_name, last_name, birth_date, email, persona_id, status,
       automation_policy, metadata, last_verified_at, last_activity_at, created_at, updated_at, offline_policy,
       policy_group_id
  FROM instagram_profiles;
DROP TABLE instagram_profiles;
ALTER TABLE instagram_profiles_novo RENAME TO instagram_profiles;
CREATE UNIQUE INDEX ux_instagram_profiles_username ON instagram_profiles(lower(username)) WHERE username <> '';
CREATE UNIQUE INDEX ux_profiles_persona ON instagram_profiles(persona_id) WHERE persona_id IS NOT NULL;
CREATE INDEX ix_instagram_profiles_policy_group ON instagram_profiles(policy_group_id);
-- @dialect:end

-- @dialect:postgres
ALTER TABLE instagram_profiles ALTER COLUMN username SET DEFAULT '';
ALTER TABLE instagram_profiles ADD COLUMN summary TEXT;
ALTER TABLE instagram_profiles ADD COLUMN traits TEXT NOT NULL DEFAULT '{}';
ALTER TABLE instagram_profiles ADD COLUMN persona_prompt TEXT NOT NULL DEFAULT '';
ALTER TABLE instagram_profiles ADD COLUMN gender TEXT;
ALTER TABLE instagram_profiles ADD COLUMN locale TEXT;
ALTER TABLE instagram_profiles ADD COLUMN biography TEXT NOT NULL DEFAULT '{}';
ALTER TABLE instagram_profiles ADD COLUMN visual TEXT NOT NULL DEFAULT '{}';
ALTER TABLE instagram_profiles ADD COLUMN generation TEXT NOT NULL DEFAULT '{}';
DROP INDEX IF EXISTS ux_instagram_profiles_username;
CREATE UNIQUE INDEX ux_instagram_profiles_username ON instagram_profiles(lower(username)) WHERE username <> '';
-- @dialect:end

-- ---------------------------------------------------------------------------------------------- dados
-- (b) casamento por nome: persona órfã × perfil sem persona. Igual nos dois dialetos.
CREATE TEMPORARY TABLE _dobra_persona AS
SELECT per.id AS persona_id, MIN(p.id) AS profile_id
  FROM personas per
  JOIN instagram_profiles p
    ON p.persona_id IS NULL
   AND lower(trim(per.name)) IN (lower(trim(COALESCE(p.display_name, ''))),
                                lower(trim(COALESCE(p.first_name, '') || ' ' || COALESCE(p.last_name, ''))))
 WHERE trim(per.name) <> ''
   AND NOT EXISTS (SELECT 1 FROM instagram_profiles q WHERE q.persona_id = per.id)
 GROUP BY per.id;
-- Duas personas com o mesmo nome para um perfil só: fica a de menor id; a outra segue para o grupo (c).
DELETE FROM _dobra_persona
 WHERE persona_id NOT IN (SELECT MIN(persona_id) FROM _dobra_persona GROUP BY profile_id);
UPDATE instagram_profiles
   SET persona_id = (SELECT d.persona_id FROM _dobra_persona d WHERE d.profile_id = instagram_profiles.id)
 WHERE id IN (SELECT profile_id FROM _dobra_persona);
DROP TABLE _dobra_persona;

-- (a) e (c). O JSON é onde os dialetos divergem de verdade (json_* × jsonb), daí os dois blocos.
-- `approx_age`: os dois dígitos imediatamente antes de " anos" no resumo, sem dígito antes deles. No SQLite não há
-- regex; a primeira ocorrência de " anos" é o que os resumos do parque têm ("Mulher de 31 anos, …").
-- @dialect:sqlite
UPDATE instagram_profiles AS p
   SET summary = per.summary,
       persona_prompt = COALESCE(per.persona_prompt, ''),
       traits = CASE WHEN json_valid(per.traits)
                     THEN json_remove(per.traits, '$.appearance', '$.visual_style', '$.photo_scenario')
                     ELSE '{}' END,
       visual = CASE WHEN json_valid(per.traits)
                     THEN json_patch('{}', json_object('appearance', json_extract(per.traits, '$.appearance'),
                                                       'visual_style', json_extract(per.traits, '$.visual_style'),
                                                       'photo_scenario', json_extract(per.traits, '$.photo_scenario')))
                     ELSE '{}' END,
       biography = json_patch('{"schema_version":1}', json_object(
           'tastes', CASE WHEN json_valid(per.traits) AND json_type(per.traits, '$.interests') = 'array'
                          THEN json_object('interests', json_extract(per.traits, '$.interests')) END,
           'approx_age', CASE WHEN per.summary IS NOT NULL AND instr(per.summary, ' anos') > 2
                              AND substr(per.summary, instr(per.summary, ' anos') - 2, 2) GLOB '[0-9][0-9]'
                              AND substr(per.summary, instr(per.summary, ' anos') - 3, 1) NOT GLOB '[0-9]'
                              THEN CAST(substr(per.summary, instr(per.summary, ' anos') - 2, 2) AS INTEGER) END)),
       generation = json_object('source', 'legacy_persona', 'persona_id', per.id),
       updated_at = CASE WHEN per.updated_at > p.updated_at THEN per.updated_at ELSE p.updated_at END
  FROM personas AS per
 WHERE per.id = p.persona_id AND p.generation = '{}';

INSERT INTO instagram_profiles (id, username, display_name, first_name, last_name, persona_id, status,
                                automation_policy, metadata, created_at, updated_at, summary, traits,
                                persona_prompt, biography, visual, generation)
SELECT 'ig-' || per.id, '', per.name,
       CASE WHEN instr(per.name, ' ') > 0 THEN substr(per.name, 1, instr(per.name, ' ') - 1) ELSE per.name END,
       CASE WHEN instr(per.name, ' ') > 0 THEN trim(substr(per.name, instr(per.name, ' ') + 1)) ELSE NULL END,
       per.id, 'active', '{}', '{}', per.created_at, per.updated_at, per.summary,
       CASE WHEN json_valid(per.traits)
            THEN json_remove(per.traits, '$.appearance', '$.visual_style', '$.photo_scenario') ELSE '{}' END,
       COALESCE(per.persona_prompt, ''),
       json_patch('{"schema_version":1}', json_object(
           'tastes', CASE WHEN json_valid(per.traits) AND json_type(per.traits, '$.interests') = 'array'
                          THEN json_object('interests', json_extract(per.traits, '$.interests')) END,
           'approx_age', CASE WHEN per.summary IS NOT NULL AND instr(per.summary, ' anos') > 2
                              AND substr(per.summary, instr(per.summary, ' anos') - 2, 2) GLOB '[0-9][0-9]'
                              AND substr(per.summary, instr(per.summary, ' anos') - 3, 1) NOT GLOB '[0-9]'
                              THEN CAST(substr(per.summary, instr(per.summary, ' anos') - 2, 2) AS INTEGER) END)),
       CASE WHEN json_valid(per.traits)
            THEN json_patch('{}', json_object('appearance', json_extract(per.traits, '$.appearance'),
                                              'visual_style', json_extract(per.traits, '$.visual_style'),
                                              'photo_scenario', json_extract(per.traits, '$.photo_scenario')))
            ELSE '{}' END,
       json_object('source', 'legacy_persona', 'persona_id', per.id)
  FROM personas AS per
 WHERE NOT EXISTS (SELECT 1 FROM instagram_profiles q WHERE q.persona_id = per.id);
-- @dialect:end

-- @dialect:postgres
UPDATE instagram_profiles AS p
   SET summary = per.summary,
       persona_prompt = COALESCE(per.persona_prompt, ''),
       traits = CASE WHEN per.traits IS JSON OBJECT
                     THEN (per.traits::jsonb - 'appearance' - 'visual_style' - 'photo_scenario')::text
                     ELSE '{}' END,
       visual = CASE WHEN per.traits IS JSON OBJECT
                     THEN jsonb_strip_nulls(jsonb_build_object(
                              'appearance', per.traits::jsonb -> 'appearance',
                              'visual_style', per.traits::jsonb -> 'visual_style',
                              'photo_scenario', per.traits::jsonb -> 'photo_scenario'))::text
                     ELSE '{}' END,
       biography = jsonb_strip_nulls(jsonb_build_object(
           'schema_version', 1,
           'tastes', CASE WHEN per.traits IS JSON OBJECT AND jsonb_typeof(per.traits::jsonb -> 'interests') = 'array'
                          THEN jsonb_build_object('interests', per.traits::jsonb -> 'interests') END,
           'approx_age', NULLIF(substring(COALESCE(per.summary, '') from '(?:^|\D)(\d{2}) anos'), '')::int))::text,
       generation = jsonb_build_object('source', 'legacy_persona', 'persona_id', per.id)::text,
       updated_at = GREATEST(per.updated_at, p.updated_at)
  FROM personas AS per
 WHERE per.id = p.persona_id AND p.generation = '{}';

INSERT INTO instagram_profiles (id, username, display_name, first_name, last_name, persona_id, status,
                                automation_policy, metadata, created_at, updated_at, summary, traits,
                                persona_prompt, biography, visual, generation)
SELECT 'ig-' || per.id, '', per.name,
       CASE WHEN strpos(per.name, ' ') > 0 THEN substr(per.name, 1, strpos(per.name, ' ') - 1) ELSE per.name END,
       CASE WHEN strpos(per.name, ' ') > 0 THEN btrim(substr(per.name, strpos(per.name, ' ') + 1)) ELSE NULL END,
       per.id, 'active', '{}', '{}', per.created_at, per.updated_at, per.summary,
       CASE WHEN per.traits IS JSON OBJECT
            THEN (per.traits::jsonb - 'appearance' - 'visual_style' - 'photo_scenario')::text ELSE '{}' END,
       COALESCE(per.persona_prompt, ''),
       jsonb_strip_nulls(jsonb_build_object(
           'schema_version', 1,
           'tastes', CASE WHEN per.traits IS JSON OBJECT AND jsonb_typeof(per.traits::jsonb -> 'interests') = 'array'
                          THEN jsonb_build_object('interests', per.traits::jsonb -> 'interests') END,
           'approx_age', NULLIF(substring(COALESCE(per.summary, '') from '(?:^|\D)(\d{2}) anos'), '')::int))::text,
       CASE WHEN per.traits IS JSON OBJECT
            THEN jsonb_strip_nulls(jsonb_build_object(
                     'appearance', per.traits::jsonb -> 'appearance',
                     'visual_style', per.traits::jsonb -> 'visual_style',
                     'photo_scenario', per.traits::jsonb -> 'photo_scenario'))::text
            ELSE '{}' END,
       jsonb_build_object('source', 'legacy_persona', 'persona_id', per.id)::text
  FROM personas AS per
 WHERE NOT EXISTS (SELECT 1 FROM instagram_profiles q WHERE q.persona_id = per.id);
-- @dialect:end
