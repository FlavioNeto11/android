-- O fuso do aparelho na leitura da última atualização do app (item 30.77, frente Aprendizado; da leitura do 30.74).
--
-- O `lastUpdateTime` do `dumpsys package` vem no fuso DO APARELHO e sem fuso escrito. Sem saber o fuso, a regra do 30.74
-- (`versao_estavel_na_execucao`) conta o pior caso, a lida +12 h, e a prova que começa até 12 h depois de uma
-- atualização grava evidência sem versão. Medido em 05/10: os 12 emuladores estão em America/Sao_Paulo (-0300), então
-- de 3 a 12 h depois de cada atualização a versão se perdia à toa.
--
-- A inspeção lê `date +%z` no mesmo shell do `dumpsys` e guarda aqui (`-0300`). Nula nas linhas antigas e na leitura
-- que não trouxe o fuso: aí a regra segue na folga de 12 h.
-- Sem BEGIN/COMMIT (o executor de migrações já abre a transação).

ALTER TABLE device_app_state ADD COLUMN last_update_offset TEXT;
