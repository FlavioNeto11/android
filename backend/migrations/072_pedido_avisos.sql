-- Caixa de avisos do pedido persistente (item 28.9, Fase 28; adendo v0.45 de `docs/api-contract.md`, seção
-- "Avisos").
--
-- Um aviso é um FATO que a pessoa precisa saber sobre o pedido (pausou sozinho, orçamento em 80%, relatório pronto...).
-- Até aqui ele só existia como evento `pedido.aviso` (retenção de 14 dias, sem `lido_em`, sem dedupe). Esta tabela o torna
-- durável e marcável como lido; o evento continua saindo, mas SÓ depois de a linha ser gravada e SÓ se ela for nova.
--
--     chave_dedupe  = identidade do fato (`pausa_automatica:<pedido>:<instante>`, `orcamento_80:<pedido>:<teto>`,
--                     `encerramento:<pedido>`...). UNIQUE: o mesmo fato visto pelo laço e pela API, ou o mesmo gesto
--                     repetido, vira UMA linha e UM evento (`INSERT ... ON CONFLICT DO NOTHING`).
--     requer_pessoa = 1 para `aprovacao_pendente`, `pergunta` e `ocorrencia_incerta`: existem para o canal de fora e para
--                     as Pendências (ADR-062), e a caixa de avisos do painel os filtra (`requer_pessoa=0`).
--     lido_em       = NULL enquanto não lido; `POST /api/pedidos/avisos/ler` o preenche e repetir é seguro.
--     dados         = JSON pequeno e SEM segredo nem texto de terceiros (o contrato recusa os dois).
--
-- `ocorrencia_id` sem chave estrangeira de propósito (como em `pedido_observacoes`, 070): o aviso sobrevive à ocorrência
-- purgada. O aviso morre com o pedido (`ON DELETE CASCADE`).
--
-- Número dado pela coordenação (072). Compatível com SQLite e PostgreSQL: só tipos comuns. Sem BEGIN/COMMIT: o executor
-- já abre a transação.

CREATE TABLE pedido_avisos (
    id             TEXT PRIMARY KEY,
    pedido_id      TEXT NOT NULL REFERENCES pedidos(id) ON DELETE CASCADE,
    ocorrencia_id  TEXT,
    tipo           TEXT NOT NULL CHECK (tipo IN ('pausa_automatica', 'orcamento_80', 'orcamento_esgotado',
                                                 'ocorrencia_perdida', 'relatorio_pronto', 'encerramento',
                                                 'aprovacao_pendente', 'pergunta', 'ocorrencia_incerta')),
    nivel          TEXT NOT NULL CHECK (nivel IN ('info', 'warn', 'error')),
    mensagem       TEXT NOT NULL,
    dados          TEXT NOT NULL DEFAULT '{}',
    requer_pessoa  INTEGER NOT NULL DEFAULT 0 CHECK (requer_pessoa IN (0, 1)),
    chave_dedupe   TEXT NOT NULL UNIQUE,
    criado_em      TEXT NOT NULL,
    lido_em        TEXT
);
CREATE INDEX ix_pedido_avisos_pedido ON pedido_avisos(pedido_id, lido_em);
