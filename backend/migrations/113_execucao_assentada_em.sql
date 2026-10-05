-- 29.93 e 29.103 (#382): a marca de que a execução JÁ foi assentada (digest do aprendizado, trava de rascunho, pedidos).
-- O assentamento tem dois caminhos: o worker, no fim dele (`Scheduler._settle_run`), e a rede do `set_run_status` para
-- quem fecha sem worker (saída da espera da pessoa, cancelamento órfão). Quem ganha o compare-and-set
-- `UPDATE runs SET assentada_em=? WHERE id=? AND assentada_em IS NULL` assenta; o outro não faz nada. Assim a mesma
-- execução nunca assenta em dobro, nem com dois backends (o `_finish_cancel` pode fechar em outro processo).
--
-- O preenchimento: toda execução que JÁ está em estado final (inclusive `completed_with_issues`) foi assentada pelo
-- worker antes desta migração, e ganha a marca. Sem isto, uma `completed_with_issues` antiga cancelada depois do deploy
-- ganharia o compare-and-set e assentaria de novo. A tabela `runs` não tem `updated_at`: a data é o `finished_at`, ou o
-- `started_at`, ou o `created_at`, nessa ordem. As que a 111 levou a `awaiting_person` ficam nulas: assentam na saída.
--
-- Número 113 reservado pela orquestradora em 05/10 07:45Z.
ALTER TABLE runs ADD COLUMN assentada_em TEXT;
UPDATE runs SET assentada_em = COALESCE(finished_at, started_at, created_at)
 WHERE status IN ('completed', 'completed_with_issues', 'failed', 'cancelled') AND assentada_em IS NULL;
