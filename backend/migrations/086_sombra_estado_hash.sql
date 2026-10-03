-- A linha da sombra guarda o hash do estado REDIGIDO que foi ao Jev (item 31.22, Fase 31; ADR-069 item 21; decisão da
-- orquestradora em 03/10, número 086 dado por ela).
--
-- Por quê: o item 21 do ADR-069 deixa o lote offline do 31.11 reenviar só comandos que JÁ saíram pela sombra da
-- intenção, e a orquestradora pediu a prova de "exposição nova zero": o texto que sai no lote tem de ser o mesmo que saiu
-- na sombra. O lote remonta o estado com o catálogo e o filtro de hoje; sem um hash do que saiu, a comparação era pela
-- versão do código do filtro (salvaguarda "b"). Com esta coluna, o lote compara o hash do estado remontado com o da linha
-- e só reenvia o caso que bate.
--
-- `estado_hash`: sha256 em hexadecimal (64 caracteres) do JSON canônico do estado DEPOIS do `privacidade.redigir`
-- (`porta.hash_do_estado`): chaves em ordem, sem espaços, UTF-8. Vai em TODAS as linhas da chamada, como `postado`.
-- NULO quando o pedido não passou da privacidade (nada foi redigido para sair), e no legado.
--
-- Só o hash: nada do estado, das opções nem do comando (a tabela continua só com ids, categorias e números). Sem índice:
-- o lote lê poucas linhas, por `run_id`. Sem BEGIN/COMMIT (o executor de migrações já abre a transação). A 084 e a 085 são
-- de outros branches, e a 087 do 32.2; a lacuna na numeração é esperada.

ALTER TABLE decisao_fechada_sombra ADD COLUMN estado_hash TEXT;
