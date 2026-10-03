-- A validação do fluxo pelo próprio fluxo (item 30.37, Fase 30; desenho aprovado pela orquestradora em 03/10, emenda
-- datada à D1 do ADR-054).
--
-- Até aqui, o pedido de validação de um fluxo re-executava o comando de origem: o planejador livre (fluxo candidato) ou
-- o fluxo ativo (o comando casa com ele mesmo). Os dois mediam a coisa errada: o primeiro media o planejador, e o segundo
-- não deixava evidência nenhuma (K-086: a evidência de fluxo só nascia da sombra). Agora a execução de validação de um
-- fluxo é uma EXECUÇÃO DE PROVA: roda o plano do próprio fluxo, com os parâmetros do comando de origem, e a evidência sai
-- das etapas dela.
--
-- - `runs.prova_fluxo_id`: o fluxo que esta execução prova. Nulo em toda execução comum. A prova não grava
--   `runs.flow_id` (não é reuso do fluxo, não conta `flows.uses`) e fica fora das sombras e do aprendizado de fluxo.
-- - `learning_validations.teto_usd`: o teto de gasto de IA do pedido (`aprendizado.validacao.teto_por_pedido_usd`);
--   `teto_usd_da_execucao` usa o menor entre ele e o do pedido do 28.6. Nulo nos pedidos de antes desta migração.
--
-- Sem FK (o mesmo padrão da 082: a execução e o fluxo podem ser purgados; o pedido fica) e sem índice: a leitura é por
-- `runs.id` e `learning_validations.run_id`. Só `ADD COLUMN`, válido nos dois dialetos. A ordem com a 083 é livre (o
-- migrador aplica, em ordem de nome, todo arquivo ainda ausente de `schema_migrations`). Sem BEGIN/COMMIT: o executor
-- já abre a transação.

ALTER TABLE runs ADD COLUMN prova_fluxo_id TEXT;
ALTER TABLE learning_validations ADD COLUMN teto_usd REAL;
