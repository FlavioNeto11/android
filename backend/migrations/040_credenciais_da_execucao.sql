-- Credencial fornecida pela pessoa para UMA execução (ADR-025, decisão do dono em 26/09): a automação passa a entrar
-- com a senha que a pessoa informou, com consentimento. O valor vai para o cofre (`secrets`, AES-256-GCM, mesma chave
-- mestra das contas); aqui fica só o NOME que o modelo conhece (ex.: "senha") e a referência. A linha e o segredo
-- são apagados quando a execução termina (`Repository.set_run_status`).
CREATE TABLE IF NOT EXISTS run_secrets (
    run_id      TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    secret_ref  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (run_id, name)
);
