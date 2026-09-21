-- Busca de memória sem acento no PostgreSQL, igualando o que o SQLite já faz.
--
-- O defeito que isto corrige: a migração 009 monta o `tsvector` com `to_tsvector('simple', …)`, que **preserva
-- acento**. O índice guardava `'são'` e a busca por "sao paulo" pedia `'sao'` — nenhuma linha casava. Pior que um
-- erro: a busca não falhava, apenas não encontrava, e quem chamou caía no caminho alternativo e recebia a lembrança
-- errada. Do lado do SQLite a intenção sempre foi explícita (`tokenize="unicode61 remove_diacritics 2"`), então os
-- dois bancos respondiam coisas diferentes para a mesma pergunta.
--
-- Escapou ao inventário do E6 porque o teste que cobre a busca abria SQLite direto, mesmo quando a suíte era
-- apontada para o PostgreSQL. Cobertura que parecia existir e não existia.

-- @dialect:postgres
-- Por que `translate` e não a extensão `unaccent`, que seria a resposta óbvia: coluna gerada exige expressão
-- IMMUTABLE, e `unaccent(text)` é STABLE. O contorno conhecido é embrulhá-la numa função declarada IMMUTABLE —
-- ou seja, prometer o que o PostgreSQL se recusa a prometer — e isso traz três problemas de uma vez: a extensão
-- pode já existir em OUTRO schema (e aí `CREATE EXTENSION IF NOT EXISTS … SCHEMA public` não a move, deixando a
-- função quebrada em silêncio), o dicionário precisaria de REINDEX se mudasse, e a instalação passaria a depender
-- do contrib.
--
-- `translate` é nativo, imutável de verdade, sem extensão e sem schema para acertar. O preço é ser uma lista
-- explícita em vez de um dicionário universal: cobre o alfabeto latino, que é o que este parque usa (conteúdo em
-- português, nomes de perfil, texto do Instagram). Não cobriria grego ou cirílico — e o `remove_diacritics 2` do
-- FTS5, do outro lado, também se limita ao Latin-1.
CREATE OR REPLACE FUNCTION sem_acento(txt text) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
    RETURN translate(txt,
        'áàâãäåéèêëíìîïóòôõöøúùûüçñýÿÁÀÂÃÄÅÉÈÊËÍÌÎÏÓÒÔÕÖØÚÙÛÜÇÑÝ',
        'aaaaaaeeeeiiiioooooouuuucnyyAAAAAAEEEEIIIIOOOOOOUUUUCNY');

-- Recriar a coluna, e não alterá-la: expressão de coluna gerada não é alterável no lugar. Nenhum dado se perde —
-- `busca` é derivada de `subject` e `content`, e o banco a recalcula inteira ao criar.
DROP INDEX IF EXISTS ix_memory_busca;
ALTER TABLE memory_items DROP COLUMN IF EXISTS busca;
ALTER TABLE memory_items ADD COLUMN busca tsvector
    GENERATED ALWAYS AS (to_tsvector('simple', sem_acento(coalesce(subject,'') || ' ' || coalesce(content,'')))) STORED;
CREATE INDEX ix_memory_busca ON memory_items USING GIN (busca);
-- @dialect:end

-- No SQLite não há nada a fazer: o FTS5 já foi criado com `remove_diacritics 2`. Esta migração existe para os dois
-- bancos passarem a responder igual, não para mudar o que já estava certo.
