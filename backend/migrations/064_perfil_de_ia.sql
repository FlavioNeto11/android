-- Perfil de IA por execução e canário (item 17.7, 02/10/2026).
--
-- `ai.profiles.<nome>` troca funções de IA (provedor, modelo, prazo) só para as execuções que o escolhem, sem
-- reiniciar o central. A execução guarda QUAL perfil usou e POR QUÊ, para que a comparação A/B possa separar os
-- braços pelo banco (`ai_calls` junta por `run_id`):
--     ai_profile        = o nome do perfil em `ai.profiles`; NULL = as funções padrão (`ai.roles`);
--     ai_profile_source = 'explicit' (o pedido escolheu, `RunCreate.ai_profile`) | 'canary' (sorteado pela fatia de
--                         `ai.canary`); NULL quando não há perfil.
-- Só colunas novas e vazias: execução anterior continua no padrão, que é o que ela de fato usou.
-- Sem BEGIN/COMMIT: o executor de migrações já abre a transação.

ALTER TABLE runs ADD COLUMN ai_profile TEXT;
ALTER TABLE runs ADD COLUMN ai_profile_source TEXT CHECK (ai_profile_source IS NULL OR ai_profile_source IN ('explicit', 'canary'));
