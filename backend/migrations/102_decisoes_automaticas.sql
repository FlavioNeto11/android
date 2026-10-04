-- O registro único do que a plataforma decidiu sozinha (item 28.25, Fase 28; número 102 reservado pela orquestradora em
-- 04/10; a 101 está no PR #267, ainda fora da main).
--
-- Por quê: o pedido do dono (30.55) é que a plataforma decida sozinha o que hoje espera a aprovação dele no portal. O
-- que ela decide precisa de DUAS garantias: aparecer agrupado e legível para o dono (aba "Decidido sozinho" de
-- Pendências e um resumo no Telegram, nunca um aviso por decisão) e poder ser DESFEITO por ele dentro de um prazo.
-- Esta tabela é o livro dessas decisões, de todas as filas, no mesmo formato.
--
-- Quem escreve: o adaptador do 28.25 (lê os eventos de vencimento do 31.43 e as transições `decided_by='plataforma'` do
-- aprendizado) e, depois, as frentes que decidem, pela porta `registrar_decisao`. Sempre idempotente por `origem_ref`.
--
--     fila         pergunta | objetivo | aprendizado | pedido: a fila de pendências dona do item.
--     item_ref     o id do item na fila dona (execução, objetivo, `kind:ref` do livro, pedido). Id, nunca conteúdo.
--     origem_ref   a identidade do FATO que gerou a linha (`run:<id>:<regra>`, `aprendizado:<id da transição>`...).
--                  UNIQUE: o mesmo fato visto duas vezes (evento relido, duas réplicas) é UMA linha.
--     regra        a regra que decidiu, com a versão quando houver (`31.43-pergunta-24h`, `auto:qa_revisar@v1`).
--     efeito       uma frase curta em português do que aconteceu. Sem nome de persona, conta, e-mail, telefone, IP nem
--                  texto de comando.
--     fatos        JSON curto e plano (chave → texto ou número) com os fatos que a regra usou. Sem dado pessoal.
--     decidida_em  UTC, do fato (não da leitura). Os 7 dias do desfazer contam daqui.
--     resumida_em  UTC; quando entrou num resumo do Telegram. NULO = ainda não foi resumida.
--     desfeita_*   quando, por quem (a sessão do painel) e por quê o dono desfez. Desfazer duas vezes não muda isto.
--
-- `decisoes_automaticas_estado` guarda o que o adaptador e o resumo precisam lembrar entre voltas e reinícios: o cursor
-- de cada fonte (`cursor:eventos`, `cursor:aprendizado`) e a hora do último resumo (`ultimo_resumo_em`), que é o que
-- faz "no máximo UMA mensagem por janela" sobreviver a um reinício do backend.
--
-- Compatível com SQLite e PostgreSQL: `{{PK_AUTO}}`, só tipos comuns. Sem BEGIN/COMMIT: o executor já abre a transação.

CREATE TABLE decisoes_automaticas (
  id                 {{PK_AUTO}},
  fila               TEXT NOT NULL CHECK (fila IN ('pergunta', 'objetivo', 'aprendizado', 'pedido')),
  item_ref           TEXT NOT NULL,
  origem_ref         TEXT NOT NULL UNIQUE,
  regra              TEXT NOT NULL,
  efeito             TEXT NOT NULL,
  fatos              TEXT NOT NULL DEFAULT '{}',
  decidida_em        TEXT NOT NULL,
  resumida_em        TEXT,
  desfeita_em        TEXT,
  desfeita_por       TEXT,
  motivo_do_desfazer TEXT
);
CREATE INDEX ix_decisoes_automaticas_data ON decisoes_automaticas(decidida_em);
CREATE INDEX ix_decisoes_automaticas_resumo ON decisoes_automaticas(resumida_em, id);

CREATE TABLE decisoes_automaticas_estado (
  chave TEXT PRIMARY KEY,
  valor TEXT NOT NULL
);
