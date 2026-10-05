-- 30.80: a receita que "não se aplicou" (a tela de partida era outra: a AÇÃO 1 não achou o alvo, antes de a receita agir,
-- e a etapa terminou comprovada pela IA) não conta como falha dela. A r-20261005133833-122345 partiu de dentro de uma
-- conversa, e a receita 194, ensinada no modo treinamento, saiu com uma falha que não era dela.
--
-- `nao_aplicavel_seguidas` conta esses casos SEGUIDOS. O executor (`RecipeStore.nao_aplicavel`) faz a 3ª contar como
-- falha comum, para um 1º seletor quebrado (atualização do app) não ficar isento da quarentena para sempre. Zera no
-- ok e na falha (`RecipeStore.result`). As receitas que já existem começam em 0.
--
-- Número 115 reservado pela orquestradora em 05/10.
ALTER TABLE recipes ADD COLUMN nao_aplicavel_seguidas INTEGER NOT NULL DEFAULT 0;
