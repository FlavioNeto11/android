-- Chaves WireGuard geradas pela plataforma (ADR-056, item 25.4, 29/09/2026): a do servidor sing-box do central e
-- uma por aparelho que usa um perfil de VPN servido por ele (`network_profiles.params.servidor = "central"`).
--
-- `network_keys`:
--     owner       = 'servidor' ou o `instance_id` do aparelho (sem FK, como `device_network`: a linha é do id);
--     kind        = servidor | aparelho (CHECK);
--     secret_ref  = a referência no COFRE da chave PRIVADA. A chave nunca mora aqui: quem a lê é só o consumidor
--                   restrito de rede (`security/segredo_de_rede.py`), pela linha desta tabela — nunca por uma
--                   referência que alguém passe;
--     public_key  = a chave pública (não é segredo: é o que o servidor precisa para aceitar o par);
--     address     = o endereço do aparelho no túnel (ex.: 10.66.0.3). UNIQUE: dois aparelhos com o mesmo endereço
--                   derrubariam um ao outro no servidor. NULL no servidor (o dele sai da sub-rede configurada).
--
-- Compatível com SQLite e PostgreSQL. Sem BEGIN/COMMIT: o executor de migrações já abre a transação.

CREATE TABLE network_keys (
    owner        TEXT PRIMARY KEY,
    kind         TEXT NOT NULL CHECK (kind IN ('servidor', 'aparelho')),
    secret_ref   TEXT NOT NULL,
    public_key   TEXT NOT NULL,
    address      TEXT UNIQUE,
    created_at   TEXT NOT NULL
);
