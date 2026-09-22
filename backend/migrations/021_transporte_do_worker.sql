-- O túnel como componente da plataforma (item 3.5 do plano-100; achado #179).
--
-- Tudo que é remoto passa por um único processo `ssh` (scripts/worker-tunnel.ps1): as portas de ADB (-L) e a
-- porta pela qual o agente do worker alcança a API (-R). Quando ele cai, o painel mostrava sintomas espalhados —
-- seis aparelhos "sem conexão ADB", worker "offline" 30 s depois — e nenhum lugar dizia "o túnel para o worker X
-- está fora desde HH:MM". Aqui o transporte passa a ser observável por worker, sondado por TCP nas portas locais
-- que o túnel encaminha (medido: `ssh -L` escuta local mesmo com o lado remoto fora do ar — refused = túnel
-- fora, aceito = túnel de pé, aparelho pode estar desligado do outro lado).
--
-- Sem BEGIN/COMMIT: o executor de migrações já abre a transação.

ALTER TABLE workers ADD COLUMN transport_state TEXT;    -- up | down | unknown (NULL = nunca sondado)
ALTER TABLE workers ADD COLUMN transport_detail TEXT;   -- causa legível: qual porta recusou, quando
ALTER TABLE workers ADD COLUMN transport_since TEXT;    -- desde quando o transport_state atual vale
