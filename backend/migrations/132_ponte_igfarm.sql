-- 132_ponte_igfarm — ponte android ⇄ igfarm (número concedido pelo usuário em 09/10).
--
-- O igfarm cria a conta do Instagram pela API mobile e lê o código no e-mail da persona (caixa compartilhada
-- `*@dominio` → uma caixa só, via IMAP). A plataforma entrega as personas que ainda não têm conta
-- (`GET /api/instagram/personas-pendentes`) e registra a conta criada (`POST /api/instagram/contas`). Três tabelas
-- NOVAS, sem tocar `profile_accounts` (a 131, da Jev, é a conta planejada do ADR-087; a convergência é depois):
--
-- * `persona_reservas`: a SUGESTÃO persistente por pessoa (e-mail + @ + imagem) gravada em todo GET, para a chamada
--   repetida devolver o mesmo e-mail e o mesmo @ sem nova chamada de IA. `reservada_em` NULL = só sugestão; preenchida =
--   reserva com validade (`expira_em`, ISO): a pessoa sai das outras chamadas e a reserva vence sozinha para não
--   travá-la. A unicidade do e-mail e do @ sugeridos vale entre as sugestões (o @ de terceiro quem trata é o igfarm).
-- * `caixas_email`: o e-mail da conta registrada e a referência da senha dele no cofre (`secret_ref`/`key_id`, o mesmo
--   `SecretStore` do `account_credentials`); a senha nunca é texto aqui.
-- * `contas_igfarm`: o id da conta no igfarm e o @ registrado. `UNIQUE(profile_id, lower(username_registrado))` é a
--   chave de idempotência do POST (repetir o mesmo registro não duplica nada).
--
-- Datas em ISO-8601 (ordenam igual nos dois dialetos). Compatível com SQLite e PostgreSQL: só tipos comuns. Sem
-- BEGIN/COMMIT: o executor já abre a transação. Só tabelas novas: nenhuma linha anterior muda.

CREATE TABLE IF NOT EXISTS persona_reservas (
    profile_id        TEXT PRIMARY KEY REFERENCES instagram_profiles(id) ON DELETE CASCADE,
    email_sugerido    TEXT NOT NULL,
    username_sugerido TEXT NOT NULL,
    imagem_id         TEXT,                         -- imagem gerada na reserva (persona_images.id); NULL = ainda sem
    reservada_em      TEXT,                         -- NULL = só sugestão; preenchida = reserva vigente até `expira_em`
    expira_em         TEXT,
    criada_em         TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_persona_reservas_email ON persona_reservas(lower(email_sugerido));
CREATE UNIQUE INDEX IF NOT EXISTS ux_persona_reservas_username ON persona_reservas(lower(username_sugerido));

CREATE TABLE IF NOT EXISTS caixas_email (
    account_id  TEXT PRIMARY KEY REFERENCES profile_accounts(id) ON DELETE CASCADE,
    profile_id  TEXT NOT NULL REFERENCES instagram_profiles(id) ON DELETE CASCADE,
    endereco    TEXT NOT NULL,                      -- minúsculo
    dominio     TEXT NOT NULL,
    secret_ref  TEXT NOT NULL,                      -- a senha do e-mail, no cofre; nunca em texto
    key_id      TEXT NOT NULL,
    criada_em   TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_caixas_email_endereco ON caixas_email(lower(endereco));
CREATE INDEX IF NOT EXISTS ix_caixas_email_profile ON caixas_email(profile_id);

CREATE TABLE IF NOT EXISTS contas_igfarm (
    account_id         TEXT PRIMARY KEY REFERENCES profile_accounts(id) ON DELETE CASCADE,
    profile_id         TEXT NOT NULL REFERENCES instagram_profiles(id) ON DELETE CASCADE,
    igfarm_account_id  TEXT NOT NULL,
    username_registrado TEXT NOT NULL,               -- minúsculo, sem '@'
    criada_em_igfarm   TEXT NOT NULL,
    registrada_em      TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_contas_igfarm_chave ON contas_igfarm(profile_id, lower(username_registrado));
CREATE INDEX IF NOT EXISTS ix_contas_igfarm_igfarm ON contas_igfarm(igfarm_account_id);
