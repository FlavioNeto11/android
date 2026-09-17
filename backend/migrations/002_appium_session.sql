-- Sessão Appium aberta por este projeto em cada aparelho. Após um reinício do backend, a sessão antiga
-- (que segura o systemPort) é encerrada por id antes de abrir outra.
ALTER TABLE instances ADD COLUMN appium_session_id TEXT;
