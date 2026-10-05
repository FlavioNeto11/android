-- 29.93: a execução cujo trabalho automático acabou com um objetivo esperando um gesto da pessoa (`waiting_user`) era
-- assentada como `completed_with_issues`, que é terminal: o painel, o Telegram e o contrato a davam por encerrada. Desde
-- o 29.93 o `recompute_run` a leva a `awaiting_person` (não terminal). Esta migração só leva ao estado novo as que já
-- estavam paradas assim, para não esperar que algo as recalcule. Só `waiting_user`: execução só com objetivo `uncertain`
-- segue `completed_with_issues` (decisão da orquestradora, 05/10).
--
-- `finished_at` fica como está: continua sendo o fim do trabalho automático, de onde conta o vencimento (31.50).
-- Idempotente: rodar de novo não acha nada. Não há CHECK em `runs.status`, então não há esquema a mudar.
-- Medido no banco do central em 05/10 (mode=ro): 0 linhas a mudar; o vencimento ligado já tinha fechado os parados.
--
-- Número 111 reservado pela orquestradora em 05/10 06:44Z.
UPDATE runs SET status = 'awaiting_person'
 WHERE status = 'completed_with_issues'
   AND EXISTS (SELECT 1 FROM objectives o WHERE o.run_id = runs.id AND o.status = 'waiting_user');
