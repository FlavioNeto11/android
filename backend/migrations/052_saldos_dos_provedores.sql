-- Saldo das contas de IA (ADR-051): Anthropic, OpenAI e Google AI Studio (Gemini).
--
-- Nenhum dos três consoles publica o SALDO pré-pago por API (as APIs de administração só dão custo, e com chave
-- de administrador). Então o saldo da plataforma é uma ESTIMATIVA: a última leitura registrada (âncora, com data e
-- origem) menos o que `ai_calls` diz ter sido gasto nessa conta desde então. A leitura vem do dono (painel), da
-- IDE (lendo o console no Chrome dele) ou de um erro de cobrança do próprio provedor (saldo 0).
--
--   `ai_billing_accounts`  = regra por conta: moeda, câmbio (unidades da moeda por US$ 1, porque `ai_calls` custa
--                            em US$ e o AI Studio cobra em R$), limite de aviso e de bloqueio, na moeda da conta.
--                            NULL = limite desligado. O bloqueio sai de fábrica desligado: é decisão do dono.
--                            Sem carga aqui: conta sem linha usa os padrões de `planning/saldos.py::CONTAS`, e a
--                            linha nasce no primeiro ajuste (um banco recém-migrado continua VAZIO para a
--                            ferramenta de migração de dados, que recusa copiar por cima).
--   `ai_balance_snapshots` = cada leitura. Nunca se edita; a mais recente por `observed_at` é a âncora.
--
-- Compatível com SQLite e PostgreSQL.

CREATE TABLE ai_billing_accounts (
    account        TEXT PRIMARY KEY,                -- anthropic | openai | gemini
    currency       TEXT NOT NULL DEFAULT 'USD',
    units_per_usd  REAL NOT NULL DEFAULT 1.0,       -- câmbio: quanto de `currency` vale US$ 1
    warn_below     REAL,                            -- aviso abaixo disto (moeda da conta); NULL = sem aviso
    block_below    REAL,                            -- IA desta conta barrada abaixo disto; NULL = sem bloqueio
    stale_after_h  INTEGER NOT NULL DEFAULT 72,     -- leitura mais velha que isto vira "desatualizada"
    updated_at     TEXT NOT NULL
);

CREATE TABLE ai_balance_snapshots (
    id             {{PK_AUTO}},
    account        TEXT NOT NULL,
    balance        REAL NOT NULL,                   -- na moeda da conta
    currency       TEXT NOT NULL,
    units_per_usd  REAL NOT NULL,                   -- câmbio vigente na leitura
    source         TEXT NOT NULL,                   -- manual | console | provider_error
    observed_at    TEXT NOT NULL,                   -- quando o saldo foi LIDO no console (ISO UTC)
    created_at     TEXT NOT NULL,
    note           TEXT
);
CREATE INDEX idx_ai_balance_snapshots_account ON ai_balance_snapshots(account, observed_at);
