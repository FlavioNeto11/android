-- Workers: as máquinas que hospedam aparelhos.
--
-- Até aqui a distribuição existia só no nível do APARELHO: `instances.external` apontava para um `host:porta` e o
-- backend falava ADB por um túnel. Não havia nada na outra máquina para receber ordem, então criar AVD, ligar o
-- emulador, `emu kill`, snapshot e guarda de RAM simplesmente não existiam lá. Esta tabela dá identidade,
-- capacidade e saúde à máquina, para que o comando tenha para quem ir.
--
-- Sem BEGIN/COMMIT: o executor de migrações já abre a transação.

-- `state` é o que se OBSERVA da conexão; `maintenance` é uma DECISÃO de quem opera. São coisas diferentes e por
-- isso não compartilham coluna: um worker em manutenção continua online e precisa terminar o que já pegou.
--   online     = batendo o coração dentro do prazo
--   degraded   = conectado, mas com problema declarado (sem aceleração, disco cheio, Appium fora)
--   offline    = não bate há tempo demais, ou nunca conectou
CREATE TABLE workers (
    id             TEXT PRIMARY KEY,          -- id estável declarado pelo agente; sobrevive a reinstalação
    name           TEXT NOT NULL,
    os             TEXT,
    os_version     TEXT,
    agent_version  TEXT,
    protocol       INTEGER,
    appium_mode    TEXT NOT NULL DEFAULT 'central',   -- local | central
    appium_url     TEXT,
    max_slots      INTEGER NOT NULL DEFAULT 1,
    verbs          TEXT,                      -- JSON: o que este worker consegue executar
    state          TEXT NOT NULL DEFAULT 'offline',
    state_detail   TEXT,
    maintenance    INTEGER NOT NULL DEFAULT 0,
    resources      TEXT,                      -- JSON da última batida: CPU, RAM, disco
    devices        TEXT,                      -- JSON do inventário da última batida
    enrolled_at    TEXT NOT NULL,
    last_seen_at   TEXT,
    -- Só o hash. O segredo em claro existe uma vez, na hora de inscrever, e nunca mais — nem em log, nem aqui.
    token_hash     TEXT NOT NULL
);
CREATE INDEX idx_workers_state ON workers(state, last_seen_at DESC);

-- Token de inscrição: uso único, prazo curto. É o que responde "ao configurado aqui, o servidor principal tem
-- acesso" sem que ninguém precise digitar credencial permanente no instalador.
CREATE TABLE worker_enrollments (
    token_hash   TEXT PRIMARY KEY,
    label        TEXT,                        -- para quem é este token, em texto humano
    created_at   TEXT NOT NULL,
    expires_at   TEXT NOT NULL,
    used_at      TEXT,
    used_by      TEXT                         -- worker_id que o consumiu
);

-- Qual máquina hospeda cada instância. Nulo = esta máquina (o worker local).
--
-- Importa mais do que parece: o perfil se liga a `instance_id` LÓGICO, e nada guardava a máquina. Trocar o
-- mapeamento de `instances.external` trocava o aparelho físico por baixo de um perfil, e só a invalidação de
-- sessão percebia. Com o worker registrado, "este perfil vive naquela máquina" passa a ser um fato consultável.
ALTER TABLE instances ADD COLUMN worker_id TEXT;
CREATE INDEX idx_instances_worker ON instances(worker_id);
