-- Item 29.100, nota da leitura do 29.96 (número reservado pela orquestradora em 05/10 07:00Z): a sessão grava a hora
-- em que o estado ATUAL começou.
--
-- Por quê: o item da sessão parada em Pendências mostrava `verified_at`, a hora da última verificação, que na parada
-- costuma estar vazia ou ser de dias atrás. O dono lia uma parada de agora como coisa antiga. `updated_at` não serve,
-- porque toda reescrita do MESMO estado o move (a reobservação, o "Verificar conta", a invalidação de quem já estava
-- `unknown`). `status_since` só muda quando o estado muda (`SocialRepository.set_account_session`).
--
-- Preenchimento das linhas que já existem: a última gravação (`updated_at`) onde o estado NÃO é `session_ready`. Numa
-- sessão parada, a porta deixou de reobservar ao parar, então a última gravação é a parada (ou um "Verificar conta"
-- depois dela, a melhor hora que há). `session_ready` fica nula: ali a última gravação é a última verificação, não a
-- hora em que ficou pronta, e o painel cai em `verified_at` quando o campo falta. Sessão nova ou que muda de estado
-- depois desta migração sempre grava.
--
-- Só ADD COLUMN, nula, sem CHECK, e um UPDATE limitado às nulas (repetir o UPDATE não muda nada). Compatível com SQLite
-- e PostgreSQL. Sem BEGIN/COMMIT: o executor abre a transação.

ALTER TABLE account_sessions ADD COLUMN status_since TEXT;

UPDATE account_sessions SET status_since = updated_at WHERE status_since IS NULL AND status <> 'session_ready';
