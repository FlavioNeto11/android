-- Quem é o DONO de uma operação de aplicativo em curso (item 6.2 do plano-100; achado #85).
--
-- O defeito, do jeito que dói: `reconcile_after_restart` põe em `verifying` TODA linha de `device_app_state`
-- com `pending_op` — sem perguntar de quem é a operação. Com dois backends no mesmo PostgreSQL (cenário que
-- docs/banco.md declara suportado, e que a migração 016 tratou só para `steps`), o backend que sobe marca como
-- "interrompidas" as instalações VIVAS do outro. Elas continuam acontecendo no aparelho, e o banco passa a
-- dizer outra coisa.
--
-- `claimed_by` é o `owner_id` de quem abriu a operação: o mesmo desenho de `steps.claimed_by` (016). A
-- reconciliação de partida passa a mexer só no que é seu; o que é do outro backend fica onde está, e quem o
-- fecha é quem o abriu. Linha antiga (sem dono registrado) continua sendo reconciliada por quem subir — é o
-- comportamento de antes, e nada se perde: `verifying` sem operação pendente tem releitura automática.
--
-- Sem BEGIN/COMMIT: o executor de migrações já abre a transação.

ALTER TABLE device_app_state ADD COLUMN claimed_by TEXT;   -- owner_id do backend que abriu a operação

-- "O que EU deixei em aberto?" é a pergunta da reconciliação de partida.
CREATE INDEX IF NOT EXISTS idx_device_app_state_dono ON device_app_state(claimed_by) WHERE pending_op IS NOT NULL;
