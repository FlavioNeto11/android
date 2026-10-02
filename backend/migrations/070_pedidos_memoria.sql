-- Memória, observações e relatório do pedido persistente (item 28.7, Fase 28, 02/10/2026).
--
-- O pedido que dura precisa de três coisas que a 067 não guarda: o que ele LEMBRA entre uma ocorrência e outra
-- (memória), o que cada ocorrência VIU e que não pode sumir com a purga da execução (observações) e o que se DIZ ao
-- dono sobre isso (relatório). Docs/design/pedidos-persistentes.md §6.6 e §8; o relatório é montado por código, sem IA
-- (`modules/pedidos/domain/relatorio.py`), e o resumo por IA é só um ponto de extensão desligado.
--
-- Três tabelas, uma por ideia:
--     pedido_memoria      o estado da TAREFA, chave/valor por pedido (`progresso`, `descoberta`, `decisao`,
--                         `pendencia`, `fonte`). Separada de `memory_items`: a da persona é sobre PESSOAS e vai ao
--                         prompt de conversa. `versao` sobe quando o valor MUDA (escrever o mesmo valor não sobe), e
--                         `UNIQUE (pedido_id, chave)` faz do upsert um aumento de versão;
--     pedido_observacoes  o que uma ocorrência observou: um valor lido entre etapas (`step_outputs`, 056) ou, quando
--                         não há valor estruturado, o resultado da execução com `valor` NULL ("ausente"). `fonte` diz
--                         de onde veio (app e etapa), `sha256` identifica a captura sem guardá-la, `capturado_em` é o
--                         instante. `UNIQUE (ocorrencia_id, alvo, nome)`: o fechamento da ocorrência é reentrante
--                         (a varredura repete) e insere com `ON CONFLICT DO NOTHING`;
--     pedido_relatorios   o relatório gerado: `conteudo` JSON com os três blocos que não se misturam (observado,
--                         conclusão, não coberto), o período e a versão do pedido. O INSTANTE de geração mora na
--                         linha (`gerado_em`), nunca no `conteudo`: mesmas entradas, mesmo conteúdo.
--
-- Decisões de forma:
--   * `ocorrencia_id`, `run_id` e `step_id` das observações SEM chave estrangeira, como `runs.pedido_id` na 067: a
--     observação existe justamente para sobreviver à purga da execução (a evidência crua some em 14 dias; o fato
--     observado, não). Só o `pedido_id` é chave com `ON DELETE CASCADE`: apagar o pedido leva a memória, as
--     observações e os relatórios dele.
--   * Nada sensível: o valor de uma observação passa pela recusa de segredo por FORMATO antes de gravar (a infraestrutura
--     troca por "ausente" o que parece credencial ou código de verificação, ADR-009); `trecho` e `valor` têm teto
--     conferido no domínio (como `SAIDA_VALOR_MAX` na 056), não em CHECK.
--   * Vocabulário fechado com CHECK (tipo da memória, tipo e situação da observação, gatilho do relatório): é do
--     contrato e não muda sem migração; `tests/test_pedidos_memoria.py` confere que o CHECK e o domínio dizem as
--     mesmas palavras.
--   * `UNIQUE (pedido_id, sequencia)` e o índice parcial do encerramento: o relatório de encerramento é UM por pedido
--     (gerar de novo é seguro), os demais (`sob_demanda`, `periodo`) se acumulam com `sequencia` crescente.
--   * `gerado_por` nasce `deterministico`; `resumo_texto`/`resumo_por`/`custo_usd` só são preenchidos por um resumo de IA
--     opcional (desligado de fábrica), que nunca substitui o `conteudo`.
--   * Todo instante é TEXT ISO em UTC (como o resto do esquema); dinheiro em USD como REAL (como `ai_calls`).
--
-- Só tabelas novas: nada de `runs`, `pedidos` ou `pedido_ocorrencias` é tocado.
--
-- O número: o plano dizia 061, que a main já ultrapassou; a 070 foi reservada pela coordenação para o 28.7 e a 069 é de
-- outra frente (ainda fora da main). O executor aplica todo arquivo que ainda não está em `schema_migrations`, em ordem
-- de nome: lacuna de número é tolerada e a ordem de chegada entre elas não importa.
--
-- Compatível com SQLite e PostgreSQL: TEXT, INTEGER, REAL, `REFERENCES … ON DELETE CASCADE`, `CHECK` de coluna e
-- índice único PARCIAL (os dois aceitam `WHERE`). Sem BEGIN/COMMIT: o executor de migrações já abre a transação.

CREATE TABLE pedido_memoria (
    id                  TEXT PRIMARY KEY,
    pedido_id           TEXT NOT NULL REFERENCES pedidos(id) ON DELETE CASCADE,
    chave               TEXT NOT NULL,                       -- `^[a-z][a-z0-9_.:-]{0,63}$`, conferido no domínio
    tipo                TEXT NOT NULL CHECK (tipo IN ('progresso', 'descoberta', 'decisao', 'pendencia', 'fonte')),
    valor               TEXT NOT NULL,                       -- texto ou JSON curto (teto no domínio); nunca segredo
    versao              INTEGER NOT NULL DEFAULT 1 CHECK (versao >= 1),   -- sobe quando o valor muda
    ocorrencia_id       TEXT,                                -- a ocorrência que gravou por último; sem chave estrangeira
    resolvida           INTEGER NOT NULL DEFAULT 0 CHECK (resolvida IN (0, 1)),   -- `pendencia` resolvida deixa de pesar no plano
    atualizada_em       TEXT NOT NULL,
    UNIQUE (pedido_id, chave)
);
CREATE INDEX ix_pedido_memoria_pedido ON pedido_memoria(pedido_id, tipo);

CREATE TABLE pedido_observacoes (
    id              TEXT PRIMARY KEY,
    pedido_id       TEXT NOT NULL REFERENCES pedidos(id) ON DELETE CASCADE,
    pedido_versao   INTEGER NOT NULL DEFAULT 1,              -- a versão do pedido em que a ocorrência nasceu
    ocorrencia_id   TEXT NOT NULL,                           -- sem chave estrangeira: sobrevive a tudo, menos ao pedido
    run_id          TEXT,                                    -- a execução de origem; a purga pode apagá-la
    step_id         TEXT,                                    -- idem
    alvo            TEXT NOT NULL DEFAULT '',                -- o aparelho (`objectives.instance_id`); '' = a execução inteira
    nome            TEXT NOT NULL,                           -- o nome da saída lida, ou `resultado` quando não há valor estruturado
    tipo            TEXT NOT NULL DEFAULT 'text' CHECK (tipo IN ('text', 'number', 'url', 'list', 'resultado')),
    situacao        TEXT NOT NULL CHECK (situacao IN ('observado', 'incerto', 'ausente')),
    valor           TEXT,                                    -- NULL = ausente (não houve valor, ou ele foi recusado)
    fonte           TEXT NOT NULL DEFAULT '',                -- app e etapa de onde veio; curto
    trecho          TEXT,                                    -- o motivo do fechamento quando ausente; curto
    sha256          TEXT,                                    -- da captura (o `valor`), para saber se a fonte mudou (§8.2)
    capturado_em    TEXT NOT NULL,
    UNIQUE (ocorrencia_id, alvo, nome)
);
CREATE INDEX ix_pedido_observacoes_pedido ON pedido_observacoes(pedido_id, capturado_em);

CREATE TABLE pedido_relatorios (
    id              TEXT PRIMARY KEY,
    pedido_id       TEXT NOT NULL REFERENCES pedidos(id) ON DELETE CASCADE,
    sequencia       INTEGER NOT NULL CHECK (sequencia >= 1), -- 1, 2, 3… por pedido
    gatilho         TEXT NOT NULL CHECK (gatilho IN ('sob_demanda', 'periodo', 'encerramento')),
    pedido_versao   INTEGER NOT NULL,                        -- a versão do pedido quando o relatório foi gerado
    periodo_de      TEXT,                                    -- UTC, formato canônico de `chave.py`; NULL = desde o começo
    periodo_ate     TEXT NOT NULL,
    gerado_por      TEXT NOT NULL DEFAULT 'deterministico',  -- ou o papel de IA do resumo opcional (nunca do `conteudo`)
    conteudo        TEXT NOT NULL,                           -- JSON: observado, conclusão, não coberto, custo (sem instante de geração)
    sha256          TEXT NOT NULL,                           -- do `conteudo`: dois relatórios com as mesmas entradas têm o mesmo
    resumo_texto    TEXT,                                    -- resumo por IA opcional; NULL de fábrica
    resumo_por      TEXT,
    custo_usd       REAL NOT NULL DEFAULT 0 CHECK (custo_usd >= 0),
    gerado_em       TEXT NOT NULL,
    UNIQUE (pedido_id, sequencia)
);
CREATE INDEX ix_pedido_relatorios_pedido ON pedido_relatorios(pedido_id, gerado_em);
CREATE UNIQUE INDEX ux_pedido_relatorios_encerramento ON pedido_relatorios(pedido_id) WHERE gatilho = 'encerramento';
