-- ONDE a execução rodou (item 4.1 do plano-100; achado #176).
--
-- O defeito, do jeito que doía: o vínculo id lógico → aparelho físico muda por configuração
-- (`instances.external`, `instances.worker_id`), e nada disso ficava fotografado no objetivo. O relatório de uma
-- execução antiga em `android-09` não dizia se aquilo foi o emulador desta máquina em 17/09 ou o aparelho do
-- notebook em 19/09 — os dois existem no banco com o MESMO id, e o histórico ficava irrecuperável.
--
-- Fotografado no `materialize`, junto com o `profile_id`, e RE-fotografado no despacho: um objetivo materializado
-- aqui e despachado depois de o aparelho mudar de dono conta a história de onde o trabalho realmente aconteceu.
--
-- `worker_id` é a MÁQUINA que hospeda o aparelho; `hosted_by` é o backend que despachou (`OWNER_ID`, o mesmo dono
-- de `steps.claimed_by`); `device_serial` é o endereço de ADB reconhecível no cartão; `physical_id` é a impressão
-- digital da migração 020 — é ela, e não o serial, que separa dois aparelhos com o mesmo id lógico. Tudo nulo =
-- ainda não se sabe, e o que não se sabe nunca afirma nada.
--
-- `commands.host_worker_id` é o mesmo dado para os verbos de ADB, que saem daqui pelo túnel e por isso NUNCA
-- carimbam `commands.worker_id` (esse carimbo significa "despachado para o agente" e decide roteamento,
-- cancelamento e reconciliação — sobrecarregá-lo mandaria cancelamento de `open_app` para um agente que nunca
-- recebeu o comando). Coluna própria, significado próprio: onde o aparelho morava quando o comando foi aberto.
--
-- Sem BEGIN/COMMIT: o executor de migrações já abre a transação.

ALTER TABLE objectives ADD COLUMN worker_id TEXT;       -- máquina que hospeda o aparelho
ALTER TABLE objectives ADD COLUMN hosted_by TEXT;       -- backend que despachou (OWNER_ID)
ALTER TABLE objectives ADD COLUMN device_serial TEXT;   -- endereço de ADB no momento
ALTER TABLE objectives ADD COLUMN physical_id TEXT;     -- impressão digital do aparelho (migração 020)

ALTER TABLE commands ADD COLUMN host_worker_id TEXT;    -- onde o aparelho morava quando o comando foi aberto

-- Filtrar execuções por servidor é a pergunta que a tela de execuções passa a fazer.
CREATE INDEX IF NOT EXISTS idx_objectives_onde ON objectives(worker_id, instance_id);
