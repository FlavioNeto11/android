-- O motivo que a pessoa deu ao cancelar um pedido (item 28.22; achado real do 28.12 em 04/10). Número 099 reservado
-- pela orquestradora.
--
-- Por quê: `POST /api/pedidos/{id}/cancelar` aceitava `motivo` (até 200 caracteres) e não o gravava em lugar nenhum. O
-- cancelamento do pedido 28.12-04 às 12:07:24Z não deixou motivo no banco. `encerrado_motivo` não serve: é um enum do
-- contrato (`prazo`, `contagem`, `orcamento`, `abandonado`, `pai`), que o painel traduz por rótulo, e vai no `view()`
-- do pedido, que o evento `pedido.updated` carrega inteiro.
--
-- `cancelado_motivo` é texto livre de pessoa: só o `GET /api/pedidos/{id}` o devolve. Fica fora do `view()` e, por
-- isso, do evento, dos avisos, do Telegram e do Trello. Nulo quando a pessoa não deu motivo, no legado e nos
-- descendentes cancelados em cascata.
--
-- Sem CHECK e sem índice (o tamanho é limitado na rota; nada consulta por ele). Só `ADD COLUMN`, válido nos dois
-- dialetos. Sem BEGIN/COMMIT (o executor de migrações já abre a transação).

ALTER TABLE pedidos ADD COLUMN cancelado_motivo TEXT;
