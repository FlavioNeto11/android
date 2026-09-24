-- Item 13.1 do plano-100 (pedido do dono, 24/09): modo TREINAMENTO. A pessoa faz a tarefa no aparelho, pelo Foco,
-- e o sistema grava cada entrada junto com o ELEMENTO tocado e a tela em que ela aconteceu. Depois (13.2) a IA lê a
-- gravação e propõe uma habilidade generalizada — parâmetros, objetivo de cada etapa, o que foi erro — que vira
-- fluxo + receitas para os perfis escolhidos. Não é um gravador de macro: o que se guarda aqui é matéria-prima.
--
-- Sem chave estrangeira para `attempts`: a entrada manual acontece FORA de qualquer execução (é por isso que a
-- tabela `actions` não serve). Texto digitado em campo de senha, em tela sensível ou com cara de segredo não é
-- gravado: fica só `has_text=1` e o tamanho, para a IA saber que ali se digitou algo sem saber o quê.
--
-- Compatível com SQLite e PostgreSQL: só TEXT/INTEGER e índices.

CREATE TABLE IF NOT EXISTS training_sessions (
    id            TEXT PRIMARY KEY,
    instance_id   TEXT NOT NULL,
    profile_id    TEXT,                               -- quem estava no aparelho (pode não haver)
    app_id        TEXT,                               -- app de partida, se a pessoa disse
    intent        TEXT NOT NULL,                      -- o que a pessoa está ensinando, nas palavras dela
    status        TEXT NOT NULL DEFAULT 'recording',  -- recording | recorded | proposed | saved | discarded
    operator      TEXT,
    proposal      TEXT,                               -- JSON da proposta da IA (13.2)
    flow_id       TEXT,                               -- fluxo criado ao salvar (13.2)
    created_at    TEXT NOT NULL,
    finished_at   TEXT,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_training_sessions_instance ON training_sessions(instance_id, status);

CREATE TABLE IF NOT EXISTS training_inputs (
    session_id    TEXT NOT NULL REFERENCES training_sessions(id) ON DELETE CASCADE,
    seq           INTEGER NOT NULL,
    ts            TEXT NOT NULL,
    type          TEXT NOT NULL,                      -- tap | long_press | swipe | text | key | open_app
    x             INTEGER, y INTEGER, x2 INTEGER, y2 INTEGER,
    key_name      TEXT,
    text          TEXT,                               -- NULL quando não pôde ser guardado (senha/sensível/segredo)
    has_text      INTEGER NOT NULL DEFAULT 0,
    text_len      INTEGER,
    package       TEXT,                               -- app em primeiro plano ANTES da entrada
    app_id        TEXT,                               -- `open_app`: o app aberto
    target        TEXT,                               -- JSON: elemento tocado (sem id efêmero, sem senha) + seletores únicos
    screen_title  TEXT,
    screen_lines  TEXT,                               -- JSON: conteúdo da tela antes (poucas linhas, sem interface)
    sensitive     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (session_id, seq)
);

-- Item 13.2: a quem uma habilidade (fluxo) vale. Sem linha = vale para todos (como todo fluxo aprendido até hoje).
-- Com linhas: só para os perfis listados e os membros dos grupos de acesso listados.
CREATE TABLE IF NOT EXISTS flow_scope (
    flow_id     TEXT NOT NULL REFERENCES flows(id) ON DELETE CASCADE,
    profile_id  TEXT,
    group_id    TEXT
);
CREATE INDEX IF NOT EXISTS ix_flow_scope_flow ON flow_scope(flow_id);
ALTER TABLE flows ADD COLUMN source TEXT;              -- 'run' (aprendido de execução) | 'training:<sessão>'
