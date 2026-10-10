-- Etapa exploratória (item 31.273, ADR-084): o pedido fora do catálogo do app deixa de ser recusado e vira etapa livre
-- de exploração; esta marca fica na linha da etapa para o ensino achar, depois, a receita que a exploração comprovou.
-- Número 130 concedido pela orquestradora em 07/10 (steps.exploratoria; a 131 e a 132 já seguiram sem ela).
--
-- - `steps.exploratoria`: 1 = etapa que o planejador criou por EXPLORAÇÃO (o catálogo não cobria o pedido). NULO = a
--   etapa de sempre (inclusive todas as anteriores a esta migração). É só proveniência: não muda o que a etapa faz, a
--   receita que ela gera nem a aprovação. Quem lê é `FlowStore.etapas_descobertas` e o selo `nasceu_de_exploracao` do
--   Livro do aprendizado.
ALTER TABLE steps ADD COLUMN exploratoria INTEGER;
