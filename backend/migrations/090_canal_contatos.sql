-- Quem fala com o bot da Central e não é o dono (item 28.18; regras C-08 a C-11 de docs/dominios/canais.md; emenda de
-- 04/10 ao ADR-071). Até aqui a conversa de volta (085) aceitava só o chat do dono e gravava o resto sem texto. Agora
-- a pessoa nova recebe a apresentação da ANA e a pergunta do nome, o dono é avisado e decide, e só com o "sim" dele o
-- chat passa a ser atendido como convidado (que nunca executa nem decide).
--
-- Duas tabelas:
-- - `canal_contatos`: uma linha por chat que falou com o bot. Guarda a identidade como o Telegram a dá (`perfil`, JSON:
--   nome, sobrenome, @, idioma, se é bot), o nome que a pessoa informou e o estado. O histórico é pedido do dono (03/10
--   22:44Z: "o nome, o código do chat e o máximo de informações que puder"). O vínculo id↔pessoa mora SÓ aqui: nunca no
--   Trello, no Git, em log ou no chat de outra pessoa.
-- - `canal_contato_eventos`: o que aconteceu com cada chat (mensagem, pergunta do nome, aviso ao dono, decisão, bot
--   posto ou tirado de grupo). O texto da mensagem entra já redigido e cortado; o que parece credencial entra só como
--   `retido`, com o tamanho, sem texto.
--
-- A faxina por retenção (28.16) cobre as duas tabelas.
--
-- Mesmo padrão da 085: sem CHECK nos vocabulários (quem confere é o domínio), sem chave estrangeira, tempo em texto
-- ISO-8601. Compatível com SQLite e PostgreSQL. Sem BEGIN/COMMIT: o executor já abre a transação.

CREATE TABLE IF NOT EXISTS canal_contatos (
    canal          TEXT NOT NULL,                  -- 'telegram'
    chat_id        TEXT NOT NULL,                  -- o id do chat (no privado, o mesmo id da pessoa)
    estado         TEXT NOT NULL,                  -- 'aguardando_nome' | 'aguardando_dono' | 'autorizado' | 'recusado'
    nome_informado TEXT,                           -- o que a pessoa respondeu à pergunta do nome (limpo e cortado)
    perfil         TEXT,                           -- JSON do `from` do Telegram (first_name, last_name, username, ...)
    nome_pedido_em TEXT,                           -- quando a pergunta do nome SAIU (NULL: ainda não saiu; repete)
    primeira_em    TEXT NOT NULL,
    ultima_em      TEXT NOT NULL,
    decidido_em    TEXT,                           -- quando o dono autorizou ou recusou
    PRIMARY KEY (canal, chat_id)
);

CREATE TABLE IF NOT EXISTS canal_contato_eventos (
    id        {{PK_AUTO}},
    canal     TEXT NOT NULL,
    chat_id   TEXT NOT NULL,
    tipo      TEXT NOT NULL,                       -- 'mensagem' | 'pergunta_nome' | 'nome_informado' | 'aviso_dono'
                                                   -- | 'autorizado' | 'recusado' | 'retido' | 'bot_posto' | 'bot_tirado'
    detalhe   TEXT,                                -- redigido e cortado; NULL no `retido`
    tamanho   INTEGER NOT NULL DEFAULT 0,
    em        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_canal_contato_eventos_chat ON canal_contato_eventos(canal, chat_id, em);
