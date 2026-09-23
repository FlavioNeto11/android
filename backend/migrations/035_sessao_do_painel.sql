-- Fase 9 do plano-100 — item 9.1 (login, sessão e identidade). Achados #119, #60.
--
-- Até aqui a API tinha credencial mas não tinha PESSOA: o `API_TOKEN` prova que quem chama conhece o segredo e
-- não diz quem é, e por isso toda linha de auditoria (`commands.requested_by`, aprovações, resoluções à mão)
-- dizia `panel`. Pior: o cabeçalho `Authorization` é a única forma que o token tinha de viajar, e nem o
-- WebSocket do navegador nem uma `<img>` conseguem mandá-lo — o painel só funcionava aberto na própria máquina
-- central.
--
-- Uma sessão resolve as duas coisas de uma vez: o cookie viaja sozinho em `<img>`, `fetch` e no handshake do
-- WebSocket, e carrega um NOME. Guardamos o SHA-256 do token, nunca o token: quem lê o banco (backup, réplica,
-- dump de suporte) não ganha como se passar por ninguém — exatamente a regra que `secret_store` já segue.
--
-- `revoked_at` em vez de DELETE: "esta sessão foi encerrada às 14h02" é parte da trilha, e apagar a linha
-- apagaria a resposta. A limpeza remove o que já expirou HÁ MUITO, não o que acabou de sair.
--
-- Compatível com SQLite e PostgreSQL: só tipos TEXT e índices.

CREATE TABLE IF NOT EXISTS panel_sessions (
    token_hash   TEXT PRIMARY KEY,
    operator     TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    expires_at   TEXT NOT NULL,
    revoked_at   TEXT
);

CREATE INDEX IF NOT EXISTS idx_panel_sessions_validade ON panel_sessions(expires_at);
CREATE INDEX IF NOT EXISTS idx_panel_sessions_operador ON panel_sessions(operator);

-- A OUTRA metade do item: a decisão humana passa a ter dono. `pending_approvals` guardava `decided_at` e
-- `decided_note` — quando e por quê — e nunca QUEM. Nulo nas linhas antigas quer dizer o que sempre quis:
-- decidido antes de existir identidade.
ALTER TABLE pending_approvals ADD COLUMN decided_by TEXT;
