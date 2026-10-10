-- 134_persona_de_teste (31.314) — a persona de TESTE formal.
--
-- Até aqui a única marca de uma persona que existe só para provar o produto era o NOME ("TESTE Portal 31.283 (nao usar)"),
-- e nome não é regra: a persona entrava na seleção automática de alvos, nas operações em lote, nas contagens do painel e nos
-- avisos ao dono. Agora há uma coluna: `teste = 1` tira a pessoa de tudo o que é automático ou em massa (ver
-- `app/contracts/persona_de_teste.py`). Citada pelo nome ou pelo id, ela segue servindo, de propósito.
--
-- Aditiva: nenhuma linha anterior muda de valor (DEFAULT 0). A única marcada aqui é a que o Portal criou em 10/10/2026 para
-- o 31.283, por id; em banco que não a tem o UPDATE não toca linha nenhuma.

ALTER TABLE instagram_profiles ADD COLUMN teste INTEGER NOT NULL DEFAULT 0;      -- 0 = pessoa de verdade, 1 = persona de teste

UPDATE instagram_profiles SET teste = 1 WHERE id = 'ig-d3n4tia1rHELrY10';
