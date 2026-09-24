-- Limites POR SERVIDOR (pedido do dono, 24/09): a tela Limites misturava o que é do parque inteiro com o que só
-- valia para ESTE servidor (vagas, boots em paralelo), e os números do notebook da LAN moravam num
-- `worker.yaml` que só se muda por SSH. O agendador também só tinha um teto global de "aparelhos trabalhando",
-- então uma máquina podia ocupar o teto inteiro enquanto a outra ficava ociosa.
--
-- `worker_limits` guarda o que o DONO decidiu para cada máquina, pelo painel. Coluna NULL = "use o que a
-- máquina declara" (o `worker.yaml` dela, via `hello`). Para ESTE servidor, vagas e boots continuam nas
-- configurações vivas (`max_online_devices`, `boot_parallelism`) — a linha dele aqui só usa `max_working`.
--
-- As colunas novas em `workers` são o que o agente DECLARA no `hello` (o arquivo dele), para o painel mostrar
-- "valor da máquina" ao lado do valor decidido e o "voltar ao da máquina" ter para onde voltar.
--
-- Compatível com SQLite e PostgreSQL: só tipos TEXT/INTEGER.

CREATE TABLE IF NOT EXISTS worker_limits (
    worker_id         TEXT PRIMARY KEY,
    max_slots         INTEGER,        -- aparelhos ligados ao mesmo tempo naquela máquina (vagas de RAM)
    boot_parallelism  INTEGER,        -- emuladores ligando ao mesmo tempo
    max_working       INTEGER,        -- aparelhos TRABALHANDO ao mesmo tempo (objetivo em execução)
    min_free_ram_mb   INTEGER,        -- piso de RAM livre que a máquina mantém depois de ligar mais um
    updated_at        TEXT NOT NULL,
    updated_by        TEXT
);

ALTER TABLE workers ADD COLUMN declared_boot_parallelism INTEGER;
ALTER TABLE workers ADD COLUMN declared_min_free_ram_mb INTEGER;
