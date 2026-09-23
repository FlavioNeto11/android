-- ONDE os dados do perfil vivem (item 4.4 do plano-100 / E9; achados #45, #69).
--
-- O defeito, do jeito que dói: a sessão do Instagram mora na partição de dados do AVD, no disco de UMA máquina.
-- O banco só conhecia o id LÓGICO do aparelho (`device_profile_bindings.instance_id`), e o id lógico muda de
-- máquina por configuração: um PUT em `instances.worker_id` ou uma linha nova em `instances.external` reaponta
-- `android-09` para outro computador sem que nada no perfil perceba. A sessão segue `session_ready` em cache, a
-- porta do despacho (state.py `_session_gate`) deixa passar, e a tarefa roda num aparelho onde aquela conta
-- nunca fez login. É a frase do dono — "perfil armazenado num servidor NÃO está automaticamente disponível em
-- outro" — que até aqui não tinha modelo no código.
--
-- `worker_id` e `physical_id` são fotografados NO VÍNCULO: são a máquina e a impressão digital (migração 020) do
-- aparelho onde os dados foram gravados, e não mudam quando a configuração muda. A comparação com o que a
-- instância vale AGORA é o que torna "este perfil mudou de servidor" um fato, e não uma surpresa na execução.
--
-- `locality_at` é o que distingue "vive no servidor local" (worker_id NULL, gravado) de "não se sabe onde vive"
-- (vínculo anterior a esta migração). Sem ele, NULL seria as duas coisas — e o que não se sabe NUNCA pode
-- invalidar sessão nenhuma.
--
-- `offline_policy` é a decisão de PESSOA sobre o que fazer quando o servidor do perfil não está disponível:
--   wait            (padrão) = esperar aquele servidor voltar; o perfil não é reautenticado em outro lugar
--   reauth_elsewhere         = pode reautenticar noutro aparelho, aceitando que a sessão de lá é outra
-- Nulo = `wait`: o padrão nunca é o que faz login de novo sozinho.
--
-- Sem BEGIN/COMMIT: o executor de migrações já abre a transação.

ALTER TABLE device_profile_bindings ADD COLUMN worker_id TEXT;    -- máquina onde os dados deste perfil vivem
ALTER TABLE device_profile_bindings ADD COLUMN physical_id TEXT;  -- impressão digital do aparelho no vínculo
ALTER TABLE device_profile_bindings ADD COLUMN locality_at TEXT;  -- quando a localidade foi registrada

ALTER TABLE instagram_profiles ADD COLUMN offline_policy TEXT;    -- wait | reauth_elsewhere (nulo = wait)

-- "Quais perfis vivem naquele servidor?" é a pergunta que a tela de Perfis e o pré-voo passam a fazer.
CREATE INDEX IF NOT EXISTS idx_binding_worker ON device_profile_bindings(worker_id) WHERE active = 1;
