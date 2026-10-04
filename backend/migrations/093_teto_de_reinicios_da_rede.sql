-- O teto de reinícios da convergência de rede sobrevive ao reinício do backend (item 25.11, RA-12, parte Android).
-- Número 093 reservado pela orquestradora em 04/10; a 089 e a 091 ficam como lacunas.
--
-- Por quê: `rede.reinicios_max` conta os reinícios PEDIDOS por revisão da configuração (`rede_convergencia.py`,
-- `_Memoria.reinicios`), e essa conta vivia só na memória do processo. Cada reinício do backend zerava o teto: 88
-- reinícios de aparelho pedidos pela rede em 7 dias, com o mesmo aparelho recomeçando do zero a cada deploy. Agora a
-- conta da revisão em curso fica na linha do aparelho:
-- - `restart_rev`: a revisão (`desired_rev`) a que a conta se refere; NULL = nenhum reinício pedido nesta revisão;
-- - `restarts_requested`: quantos reinícios foram pedidos nela (aceitos ou recusados, como o teto sempre contou).
-- Revisão nova zera a conta (a revisão da linha deixa de ser a `restart_rev`). Só ADD COLUMN: linha antiga começa com
-- a conta zerada, que é o comportamento de antes.
ALTER TABLE device_network ADD COLUMN restart_rev INTEGER;
ALTER TABLE device_network ADD COLUMN restarts_requested INTEGER NOT NULL DEFAULT 0;
