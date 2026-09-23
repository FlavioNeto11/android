-- Servidor e aparelho novos sem editar YAML (item 4.5 do plano-100; achados #16, #151, #47, #13).
--
-- O defeito, do jeito que dói: acrescentar um aparelho de outra máquina exigia editar `config.yaml` em dois
-- blocos (`instances.count` e `instances.external`), reinstalar a tarefa agendada do túnel com o `-Mapa` novo,
-- reiniciar o backend e ainda fazer um PUT de `worker_id` por instância. O inventário passava a viver em TRÊS
-- lugares sem ninguém conferir: `instances.external` (a porta do túnel), `instances.worker_id` (a máquina) e o
-- `worker.yaml` da outra ponta (os aparelhos que ela de fato hospeda). Um erro em qualquer um fazia `reset`
-- agir num AVD e a tela em outro, sem alarme — os dados para detectar isso já trafegavam no `hello` e eram
-- descartados.
--
-- Aqui o mapa passa a ter UM lugar: a própria instância.
--   `external_serial`  = endereço de ADB que ESTE servidor usa (ex.: `127.0.0.1:15555`). Sobrepõe
--                        `config.instances.external`, que continua valendo para o que já está declarado lá.
--   `tunnel_port`      = a porta local daqui que o túnel encaminha; é o que o arquivo de mapa do túnel carrega,
--                        para a tarefa agendada deixar de levar o mapa fixo nos argumentos.
--   `remote_adb_port`  = a porta de ADB do lado do worker (a que o agente declara em `hello.devices[].adb_port`).
--                        É contra ela que a conferência cruzada compara o que o worker diz hospedar.
--   `origin`           = `config` (nulo, o padrão) ou `dynamic`: instância criada em tempo de execução a partir
--                        de um aparelho anunciado por um worker, sem editar YAML nem reiniciar nada.
--
-- Sem BEGIN/COMMIT: o executor de migrações já abre a transação.

ALTER TABLE instances ADD COLUMN external_serial TEXT;    -- endereço de ADB usado por este servidor
ALTER TABLE instances ADD COLUMN tunnel_port INTEGER;     -- porta local daqui encaminhada pelo túnel
ALTER TABLE instances ADD COLUMN remote_adb_port INTEGER; -- porta de ADB do lado do worker
ALTER TABLE instances ADD COLUMN origin TEXT;             -- config | dynamic

CREATE INDEX IF NOT EXISTS idx_instances_origem ON instances(origin);
