-- Item 8.3 do plano-100 (fase 8 — qualidade da operação do Instagram). Achado #104.
--
-- Sem teto, uma tela que `classify()` não reconhece (sinal ausente da tabela, app perdido no meio de um
-- onboarding não mapeado, etc.) deixa a sessão em `unknown` para sempre — e `unknown` NÃO está entre os status
-- que a porta de sessão (`AppState._session_gate`) trata como "só uma pessoa resolve": ela reabre o app e
-- reobserva A CADA TICK do agendador, sem parar, sem aviso de que está preso. `unknown_streak` conta quantas
-- vezes SEGUIDAS a sessão foi gravada como `unknown` (zera assim que qualquer outro status é gravado); a porta
-- passa a tratar o perfil como bloqueado — precisa de pessoa — quando o teto configurado é alcançado.
--
-- Compatível com SQLite e PostgreSQL: só ALTER TABLE ADD COLUMN.

ALTER TABLE instagram_sessions ADD COLUMN unknown_streak INTEGER NOT NULL DEFAULT 0;
