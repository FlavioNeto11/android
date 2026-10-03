-- O tipo do erro de IA que encerrou a tentativa (RA-22; reavaliação de 03/10, `.claude/handoffs/reavaliacao-2026-10-03.md`).
--
-- `attempts.failure_kind` (055, ADR-054) sai do TEXTO do desfecho (`domain/falhas.py::REGRAS`): reescrever uma mensagem
-- do executor muda a classificação, e o teto do pedido ("Orçamento do pedido atingido…", `AIError(kind='budget')`) caía
-- em `outro`. Uma coluna:
--
--     error_kind    o `AIError.kind` (planning/provider.py) que ENCERROU a tentativa, quando foi um erro de IA:
--                   budget | billing | balance | refusal | not_configured | step_deadline | invalid_output | error.
--                   Nulo = a tentativa não terminou por erro de IA, ou é anterior a esta migração. Sem CHECK: o
--                   vocabulário é do provedor (um kind novo não pode derrubar o fechamento da tentativa) e o
--                   classificador ignora o que não conhece.
--
-- Com ele, o classificador puro decide pelo tipo antes do texto (`classificar_falha(..., error_kind)`), no gravado e no
-- retroativo. Sem backfill: o legado segue pelas regras de texto e, na leitura retroativa, por `ai_calls.error_kind`.
--
-- Compatível com SQLite e PostgreSQL. Sem BEGIN/COMMIT: o executor de migrações já abre a transação.

ALTER TABLE attempts ADD COLUMN error_kind TEXT;
