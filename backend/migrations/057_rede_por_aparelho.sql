-- Rede por aparelho (ADR-056, contrato C3 da terceira evolução, 29/09/2026): perfis de VPN e de proxy, o desejado e
-- o observado de cada aparelho, e as medições da saída de dentro do aparelho.
--
-- Esta migração cria só a FORMA; aplicar, medir e liberar tarefa é da Fase 25 (25.2 a 25.9). As tabelas da 041
-- (`proxy_profiles`, `device_proxy_state`) FICAM: o proxy global legado é lido como `configurado`, no máximo.
--
-- `network_profiles`: um perfil nomeado (`name` único).
--     kind     = vpn | proxy;  protocol = wireguard | singbox | http | socks5 (CHECK nos dois);
--     endpoint_host/endpoint_port = para onde o cliente conecta;
--     secret_ref = a referência no COFRE (chave privada, senha do proxy, certificado). O segredo NUNCA mora aqui: nem
--       nesta coluna (é só o nome da referência), nem em `params`. O DTO devolve só `has_secret`;
--     params   = JSON sem segredo (MTU, DNS, lista de apps…).
-- `device_network`: desejado × observado por aparelho (o molde de `device_proxy_state`).
--     instance_id sem FK para `instances`, como a 041 e a 054: o aparelho é aposentado e recriado, e a linha é do id.
--     vpn_profile_id / proxy_profile_id REFERENCIAM `network_profiles` SEM `ON DELETE`: apagar um perfil em uso é
--       recusado pelo banco (a rota do 25.2 responde com o motivo), em vez de deixar o aparelho sem rede calado;
--     policy   = livre | exigida | exigida_com_bloqueio (padrão livre: nada muda para quem não pediu);
--     desired_rev/applied_rev = a revisão pedida e a aplicada (applied_rev NULL = nunca aplicada);
--     state    = pendente | configurado | conectado | trafego_verificado | parcial. Só `trafego_verificado` libera
--       tarefa com política exigida (ADR-056 §3);
--     egress_ipv4/egress_ipv6/verified_at = a última saída MEDIDA (configuração não prova IP).
-- `network_measurements`: cada medição, só acrescentada. udp_ok e leak_blocked são 0/1/NULL (NULL = não medido);
--     per_app = JSON {pacote: resultado}: o navegador não prova os outros apps.
--
-- Compatível com SQLite e PostgreSQL: TEXT/INTEGER, `{{PK_AUTO}}`, `CHECK` e `REFERENCES` sem ação.
-- Sem BEGIN/COMMIT: o executor de migrações já abre a transação.

CREATE TABLE network_profiles (
    id             TEXT PRIMARY KEY,
    name           TEXT NOT NULL UNIQUE,
    kind           TEXT NOT NULL CHECK (kind IN ('vpn', 'proxy')),
    protocol       TEXT NOT NULL CHECK (protocol IN ('wireguard', 'singbox', 'http', 'socks5')),
    endpoint_host  TEXT NOT NULL,
    endpoint_port  INTEGER NOT NULL CHECK (endpoint_port BETWEEN 1 AND 65535),
    secret_ref     TEXT,
    params         TEXT NOT NULL DEFAULT '{}',
    created_at     TEXT NOT NULL,
    created_by     TEXT
);

CREATE TABLE device_network (
    instance_id       TEXT PRIMARY KEY,
    vpn_profile_id    TEXT REFERENCES network_profiles(id),
    proxy_profile_id  TEXT REFERENCES network_profiles(id),
    policy            TEXT NOT NULL DEFAULT 'livre'
                      CHECK (policy IN ('livre', 'exigida', 'exigida_com_bloqueio')),
    desired_rev       INTEGER NOT NULL DEFAULT 0,
    applied_rev       INTEGER,
    state             TEXT NOT NULL DEFAULT 'pendente'
                      CHECK (state IN ('pendente', 'configurado', 'conectado', 'trafego_verificado', 'parcial')),
    detail            TEXT,
    error             TEXT,
    egress_ipv4       TEXT,
    egress_ipv6       TEXT,
    verified_at       TEXT,
    updated_at        TEXT NOT NULL,
    updated_by        TEXT
);

CREATE TABLE network_measurements (
    id            {{PK_AUTO}},
    instance_id   TEXT NOT NULL,
    measured_at   TEXT NOT NULL,
    method        TEXT NOT NULL,
    egress_ipv4   TEXT,
    egress_ipv6   TEXT,
    dns_resolver  TEXT,
    udp_ok        INTEGER CHECK (udp_ok IS NULL OR udp_ok IN (0, 1)),
    per_app       TEXT NOT NULL DEFAULT '{}',
    leak_blocked  INTEGER CHECK (leak_blocked IS NULL OR leak_blocked IN (0, 1)),
    detail        TEXT
);
CREATE INDEX ix_network_measurements_instance ON network_measurements(instance_id, measured_at);
