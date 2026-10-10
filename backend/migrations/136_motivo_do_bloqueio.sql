-- 136_motivo_do_bloqueio — por que a conta caiu na tela humana, guardado como DADO na lápide (31.322).
--
-- Coluna ADITIVA: nenhuma linha anterior muda de valor (as lápides antigas ficam com NULL = "sem motivo registrado").
-- O texto é um JSON pequeno, sem segredo e sem o @: egresso esperado x medido, quantos IPs distintos desde a criação,
-- minutos entre a criação no igfarm e o primeiro login, e um trecho curto da tela. Serve para o dono ver o PADRÃO
-- (egresso divergente, IP rotacionado, tempo) em vez de só "a conta caiu".

ALTER TABLE contas_retiradas ADD COLUMN motivo_do_bloqueio TEXT;
