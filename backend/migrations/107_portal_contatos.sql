-- Os contatos do formulário do site institucional (item 29.77, ADR-075; número 107 reservado pela orquestradora).
--
-- Por quê: o visitante do site público manda nome, empresa, telefone e mensagem, e isso tem de chegar ao dono pelo
-- Telegram. A linha é gravada ANTES de chamar a Canais: com o canal desligado, o Telegram fora do ar ou o teto de
-- avisos por hora estourado, o contato fica guardado e o laço do portal tenta de novo. Nada daqui vai para `runs`,
-- `pedidos`, aprovações ou IA: o texto do visitante é DADO, nunca instrução.
--
--     nome, empresa,     o que o visitante digitou, já validado pela rota (tamanho e caracteres). Dado pessoal: some
--     telefone, mensagem com a linha, 180 dias depois de `criado_em` (o prazo escrito no aviso de privacidade da página).
--     cliente_hash       HMAC do cliente (IP da borda; IPv6 pelo /64) com o sal da instalação, `settings` chave
--                        `portal.sal`. É a chave da taxa por cliente; o IP cru nunca é gravado.
--     estado             pendente:   gravado, ainda não entregue à Canais (ou a Canais respondeu `canal_desligado`).
--                        entregue:   a Canais enfileirou o aviso (idempotente pela chave `portal:<id>`).
--                        retido:     o teto de avisos por hora estourou; o laço manda quando a janela abrir.
--                        descartado: a Canais recusou o campo (`campo_invalido`) ou o teto diário estourou.
--     tentativas         quantas vezes a Canais foi chamada para esta linha.
--     motivo             o último motivo da Canais ou do teto, em texto curto. Sem conteúdo do visitante.
--
-- Compatível com SQLite e PostgreSQL: `{{PK_AUTO}}`, só tipos comuns. Sem BEGIN/COMMIT: o executor já abre a transação.

CREATE TABLE portal_contatos (
  id            {{PK_AUTO}},
  criado_em     TEXT NOT NULL,
  nome          TEXT NOT NULL,
  empresa       TEXT NOT NULL DEFAULT '',
  telefone      TEXT NOT NULL,
  mensagem      TEXT NOT NULL,
  cliente_hash  TEXT NOT NULL,
  estado        TEXT NOT NULL DEFAULT 'pendente'
                CHECK (estado IN ('pendente', 'entregue', 'retido', 'descartado')),
  tentativas    INTEGER NOT NULL DEFAULT 0,
  atualizado_em TEXT NOT NULL,
  motivo        TEXT
);
CREATE INDEX ix_portal_contatos_data ON portal_contatos(criado_em);
CREATE INDEX ix_portal_contatos_cliente ON portal_contatos(cliente_hash, criado_em);
CREATE INDEX ix_portal_contatos_estado ON portal_contatos(estado, id);
