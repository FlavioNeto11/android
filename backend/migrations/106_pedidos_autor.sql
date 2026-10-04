-- Item 28.31, fatia F2a (número reservado pela orquestradora em 04/10 20:11Z): o pedido guarda QUEM o criou e a chave de
-- lote, na criação. É o que deixa o aviso dizer do que se trata: o rótulo (título do pedido) só vai ao Telegram no pedido
-- criado pelo dono, porque nome de terceiro que não é persona não se detecta por regra; e o aviso do pedido de uma frente
-- (`lote:`) vai à janela de rotina, não sai um a um.
--
-- - `criado_por_tipo`: `dono` (sessão do painel, ou a conversa do dono no Telegram/Trello), `convidado`, `frente` (sem
--   sessão, com `idempotency_key` que começa com `lote:`), `ia` ou `desconhecido` (sem sessão e sem `lote:`). Nulo = pedido
--   anterior a esta migração: sem backfill por adivinhação, e o aviso dele segue pelo id curto, como `desconhecido`.
-- - `lote`: a `idempotency_key` quando ela começa com `lote:` (a marca do lote da frente, `lote:<frente>:<id>`); senão nulo.
--
-- Só ADD COLUMN, nulas, sem CHECK (o vocabulário fica em `pedidos/domain/autor.py`, como o de `pedido_avisos.tipo` fica
-- no domínio), então nada é reescrito. Compatível com SQLite e PostgreSQL. Sem BEGIN/COMMIT: o executor abre a transação.

ALTER TABLE pedidos ADD COLUMN criado_por_tipo TEXT;
ALTER TABLE pedidos ADD COLUMN lote            TEXT;
