-- Item 30.61 (número 105 reservado pela orquestradora em 04/10): a aprovação antecipada no PLANO. O dono vê a prévia da
-- porta numa execução `planned` e aprova os itens antes de iniciar; a execução honra a aprovação só para o item
-- IDÊNTICO (31.49), pela chave. Só ADD COLUMN na `pending_approvals` (criada pela 010); nada é reescrito.
--
-- - `chave_sha256`: `social/chave_da_aprovacao.py` (perfil, aparelho, app, ação, alvo, objeto-alvo, texto exato, sha256
--   da mídia, execução e objetivo). Nula nas aprovações de hoje (origem `execucao`), que seguem como sempre;
-- - `origem`: `plano` (o gesto de aprovar a prévia) ou `execucao` (a porta do despacho, como hoje). As linhas que já
--   existem ficam `execucao`;
-- - `expires_at`: até quando a aprovação antecipada vale (texto ISO-8601 UTC). Vencida conta como ausente; nula = sem
--   prazo (as de origem `execucao`, como hoje);
-- - `plan_version`: a versão do plano do objetivo quando foi aprovada;
-- - `midia_sha256`: o sha256 dos bytes da imagem que a etapa vai publicar, congelado no gesto.
--
-- Compatível com SQLite e PostgreSQL. Sem BEGIN/COMMIT: o executor já abre a transação.

ALTER TABLE pending_approvals ADD COLUMN chave_sha256 TEXT;
ALTER TABLE pending_approvals ADD COLUMN origem       TEXT NOT NULL DEFAULT 'execucao';
ALTER TABLE pending_approvals ADD COLUMN expires_at   TEXT;
ALTER TABLE pending_approvals ADD COLUMN plan_version INTEGER;
ALTER TABLE pending_approvals ADD COLUMN midia_sha256 TEXT;

CREATE INDEX ix_pending_approvals_chave ON pending_approvals (chave_sha256);
