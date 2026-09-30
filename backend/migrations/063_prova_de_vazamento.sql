-- Prova durável do teste de vazamento (item 29.2, pendência P16 da terceira evolução, 30/09/2026).
--
-- O teste de vazamento da política `exigida_com_bloqueio` para o cliente VPN e, quase sempre, custa um reinício do
-- aparelho. A prova dele vivia só na memória do backend: cada reinício a perdia, e a remedição seguinte refazia o
-- teste em todos os aparelhos com bloqueio (30/09: um reinício do backend, 11 reinícios de aparelho). Ela passa a
-- morar na linha do aparelho, presa à revisão e à instalação do cliente VPN:
--     leak_rev     = a revisão (`desired_rev`) em que o teste foi feito; NULL = nenhum teste;
--     leak_client  = a instalação do cliente VPN testada (`<versão> (<código>) <pasta de instalação>`; a pasta é
--                    sorteada pelo Android a cada instalação ou atualização);
--     leak_result  = 1 o Android recusou a sonda fora da VPN; 0 vazou; NULL não concluiu (nunca aprova);
--     leak_at      = quando;  leak_detail = o que a sonda mostrou, ou por que a prova foi apagada;
--     leak_pending = 1 entre a INTENÇÃO do teste (gravada antes de parar o cliente) e o desfecho. Quem encontra 1
--                    sem ensaio em curso (o backend reiniciou no meio) fecha como inconclusivo, sem parar o cliente
--                    de novo.
-- A prova vale quando `leak_rev = desired_rev`, `leak_client` é o cliente lido agora no aparelho e `leak_result = 1`.
-- O relógio não a apaga (a validade é da medição barata); revisão nova, cliente novo, wipe e `POST …/verify`, sim.
--
-- O número: 059 a 062 estão nomeadas nas linhas 28.1, 28.2, 28.7 e 28.10 do plano (Fase 28, ainda não aberta por
-- nenhuma sessão), e ficam para ela. O executor aplica todo arquivo que ainda não está em `schema_migrations`, em
-- ordem de nome: uma 059 criada depois é aplicada num banco que já tem esta.
--
-- Compatível com SQLite e PostgreSQL: um `ADD COLUMN` por instrução, `CHECK` de coluna, sem `{{PK_AUTO}}`.
-- Sem BEGIN/COMMIT: o executor de migrações já abre a transação.

ALTER TABLE device_network ADD COLUMN leak_rev INTEGER;
ALTER TABLE device_network ADD COLUMN leak_client TEXT;
ALTER TABLE device_network ADD COLUMN leak_result INTEGER CHECK (leak_result IS NULL OR leak_result IN (0, 1));
ALTER TABLE device_network ADD COLUMN leak_at TEXT;
ALTER TABLE device_network ADD COLUMN leak_detail TEXT;
ALTER TABLE device_network ADD COLUMN leak_pending INTEGER NOT NULL DEFAULT 0 CHECK (leak_pending IN (0, 1));
