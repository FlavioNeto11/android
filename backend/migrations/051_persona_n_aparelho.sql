-- Persona N:N aparelho (segunda evolução, onda C; design persona-e-parque §7 e §10.5; ADR-043).
--
-- Até aqui `device_profile_bindings` era 1:1 por dois índices únicos parciais da 008 (um perfil ativo por aparelho,
-- um aparelho ativo por perfil). A hierarquia alvo é Servidor → Aparelho → Persona(s): uma persona em N aparelhos
-- e N personas num aparelho, desde que sejam de APPS diferentes. O que muda no esquema:
--
--   `app_id`      = de que app é este vínculo. NULL = "apps sem conta gerenciada" (a persona usa o aparelho para o
--                   que não tem conta). `'instagram'` = a conta do Instagram desta persona vive neste aparelho.
--   `is_primary`  = o aparelho PRINCIPAL da persona: alvo padrão de conectar/verificar/sair e do contexto
--                   operacional, e o que `PersonaDTO.instance_id` continua mostrando. No máximo um por persona.
--
-- Índices: saem os dois da 008; entram
--   `ux_binding_par_ativo`                  um vínculo ativo por (persona, aparelho, app);
--   `ux_binding_conta_do_app_no_aparelho`   uma conta por app em cada aparelho (decisão D2-a: duas contas do MESMO
--                                           app no mesmo aparelho ficam proibidas enquanto a troca de conta no
--                                           Instagram for manual — achado #115);
--   `ux_binding_principal`                  no máximo um principal por persona.
--
-- Carga inicial: todo vínculo ativo de hoje vira principal (era o único), e ganha o `app_id` da conta da persona
-- quando ela tem exatamente UMA conta (o Instagram, na prática) — sem isto o índice D2-a não protegeria nenhum
-- vínculo vivo. Persona com zero ou várias contas fica com NULL: não se adivinha de qual app é o vínculo.
--
-- `runs.targets`: a foto dos ALVOS resolvidos da execução (JSON: aparelho, persona, app, origem de cada um, e o
-- comando sem os destinos que o texto trazia). O planejamento roda em segundo plano e é retomado depois de um
-- reinício a partir da linha de `runs` — sem a foto, um aparelho com duas personas não teria como saber por qual
-- delas a execução foi pedida. NULL nas execuções antigas: elas seguem por `instance_ids`, como sempre.
--
-- Compatível com SQLite e PostgreSQL: só tipos TEXT/INTEGER; o índice de expressão leva a expressão entre
-- parênteses (o PostgreSQL exige; o SQLite aceita); `UPDATE … SET x = (SELECT …)` é aceito nos dois.

DROP INDEX IF EXISTS idx_binding_profile_ativo;
DROP INDEX IF EXISTS idx_binding_device_ativo;

ALTER TABLE device_profile_bindings ADD COLUMN app_id TEXT;                          -- NULL = apps sem conta gerenciada
ALTER TABLE device_profile_bindings ADD COLUMN is_primary INTEGER NOT NULL DEFAULT 0; -- 1 = aparelho principal da persona

UPDATE device_profile_bindings SET is_primary = 1 WHERE active = 1;
UPDATE device_profile_bindings
   SET app_id = (SELECT a.app_id FROM profile_accounts a WHERE a.profile_id = device_profile_bindings.profile_id)
 WHERE active = 1
   AND (SELECT COUNT(*) FROM profile_accounts a WHERE a.profile_id = device_profile_bindings.profile_id) = 1;

CREATE UNIQUE INDEX ux_binding_par_ativo
    ON device_profile_bindings(profile_id, instance_id, (COALESCE(app_id, ''))) WHERE active = 1;
CREATE UNIQUE INDEX ux_binding_conta_do_app_no_aparelho
    ON device_profile_bindings(instance_id, app_id) WHERE active = 1 AND app_id IS NOT NULL;
CREATE UNIQUE INDEX ux_binding_principal
    ON device_profile_bindings(profile_id) WHERE active = 1 AND is_primary = 1;

ALTER TABLE runs ADD COLUMN targets TEXT;                                            -- JSON: alvos resolvidos (onda C)
