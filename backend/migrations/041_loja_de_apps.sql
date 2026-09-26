-- Loja de aplicativos no painel (pedido do dono, 26/09): cadastrar Outlook, TikTok, Facebook, VPN… ao lado do
-- Instagram, distribuir uma versão para N aparelhos (ou os escolhidos, ou todos) e empurrar a atualização a quem
-- ficou na versão antiga. E, decisão do dono na mesma conversa, o PROXY do aparelho distribuído do mesmo jeito.
--
-- `apps.category` — a vitrine filtra por categoria. Lista FIXA, decidida pelo dono (validada no modelo, não
-- aqui, para a lista mudar sem migração de CHECK): social, mensagens, email, rede, utilitario, qa. NULO = sem
-- categoria (o app nasceu antes desta migração, ou foi cadastrado sozinho pelo import de uma versão).
--
-- `proxy_profiles` — um proxy HTTP nomeado (host e porta). Sem usuário e senha DE PROPÓSITO: o proxy global do
-- Android (`settings global http_proxy`) não tem campo de autenticação, e credencial de proxy seria segredo — que
-- não mora em coluna de texto. Proxy com senha fica fora desta entrega.
--
-- `device_proxy_state` — desejado × observado por aparelho, no mesmo molde de `device_app_state`: o banco é
-- cache, a verdade é o que `settings get global http_proxy` responde. `desired_proxy_id` NULO numa linha que
-- existe quer dizer "sem proxy, e isso foi pedido" (diferente de não ter linha: aparelho que ninguém gerenciou).
--
-- Compatível com SQLite e PostgreSQL: só TEXT/INTEGER. Sem BEGIN/COMMIT: o executor de migrações já abre a
-- transação.

ALTER TABLE apps ADD COLUMN category TEXT;
UPDATE apps SET category = 'social' WHERE package = 'com.instagram.android';
UPDATE apps SET category = 'qa' WHERE builtin = 1;

CREATE TABLE IF NOT EXISTS proxy_profiles (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    host            TEXT NOT NULL,
    port            INTEGER NOT NULL,
    created_at      TEXT NOT NULL,
    created_by      TEXT
);

CREATE TABLE IF NOT EXISTS device_proxy_state (
    instance_id       TEXT PRIMARY KEY,
    desired_proxy_id  TEXT REFERENCES proxy_profiles(id),
    observed_value    TEXT,               -- o que o aparelho respondeu na última leitura (`host:porta` ou vazio)
    state             TEXT NOT NULL DEFAULT 'pending',   -- pending | applying | applied | failed
    detail            TEXT,
    updated_at        TEXT NOT NULL,
    verified_at       TEXT
);
