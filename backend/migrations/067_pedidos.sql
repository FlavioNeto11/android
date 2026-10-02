-- Modelo do pedido persistente (item 28.2, Fase 28, 02/10/2026): o objetivo que dura, e as ocorrências dele.
--
-- Hoje o dono manda UM comando e ele vira UMA execução (`runs`). Um pedido que volta ("veja o preço toda manhã por
-- uma semana", "acompanhe esse perfil") não tem onde morar: a recorrência seria um laço em memória que o reinício
-- esquece. Esta migração cria só a FORMA (docs/design/pedidos-persistentes.md §6); quem lê e escreve é o laço de
-- pedidos (28.4), a recorrência (28.3) e a API (28.9). Nada aqui muda o planejador, o executor nem o verificador.
--
-- Três camadas, uma por tabela:
--     pedidos            o pedido: texto-base, alvos, teto de autonomia, fuso, limites, estado (`rascunho`, `ativo`,
--                        `pausado`, `aguardando_pessoa`, `concluido`, `encerrado`, `cancelado`) e `versao`, que sobe a
--                        cada edição: nenhuma ocorrência muda de versão depois de despachada;
--     pedido_gatilhos    o que faz o pedido acontecer (`agora`, `horario`, `recorrencia`, `evento`, `condicao`,
--                        `persona`). `spec` é JSON que ESTA migração não interpreta (`dtstart` local + `rrule`, tipo
--                        de evento + filtro, ou condição): a leitura é do 28.3 e do 28.8;
--     pedido_ocorrencias cada vez que o pedido pede uma execução. `chave` é UNIQUE: o laço insere com
--                        `ON CONFLICT DO NOTHING` (portável nos dois bancos) e dois backends, dois laços ou um reinício
--                        no meio produzem UMA ocorrência. A chave é determinística (`ped:<pedido>:<gatilho>:<instante>`,
--                        `modules/pedidos/domain/chave.py`) e vira `idempotency_key` da execução (`chave:t<n>`).
--                        `UNIQUE (pedido_id, gatilho_id, previsto_para)` é a segunda cerca, sobre os campos.
--
-- Decisões de forma:
--   * `runs.pedido_id`, `runs.ocorrencia_id` e `pedido_ocorrencias.run_id` SEM chave estrangeira. A ocorrência e o
--     custo dela têm de sobreviver à purga de execução (§1 do desenho: o relatório não depende da retenção de
--     evidência), e as duas pontas se apontam (referência circular entre `runs` e `pedido_ocorrencias`). A ligação é
--     conferida pelo código e pela chave. `ocorrencia_id` NÃO é UNIQUE: as tentativas `:t1`, `:t2` da mesma
--     ocorrência são execuções diferentes.
--   * `pedidos.pai_id` também sem chave estrangeira: a colaboração entre pedidos (28.10, migração própria) decide
--     profundidade, ciclo e linhagem; aqui é só o campo.
--   * O vocabulário fechado (estado, autonomia, sobreposição, tipo de gatilho, origem) tem CHECK, como `value_kind`
--     em `step_outputs`: ele é do contrato, não muda sem migração. `tests/test_pedidos_modelo.py` confere que o CHECK
--     e o domínio (`modules/pedidos/domain/estados.py`) dizem as mesmas palavras.
--   * `autonomia` nasce `observar`, o teto mais restrito (§6.4): um pedido criado sem escolha nunca age.
--   * `runs.prioridade`: número MAIOR passa na frente; todo o legado é 0 (o que sempre foi). Os valores e o uso em
--     `dispatchable_objectives` são do 28.6; esta migração só reserva a coluna.
--   * Todo instante é TEXT ISO em UTC (como o resto do esquema); dinheiro em USD como REAL (como `ai_calls`).
--
-- Só tabelas novas e colunas novas e vazias: nenhuma linha de `runs` é tocada, e a execução anterior continua sem
-- pedido (`pedido_id` NULL), que é o que ela de fato é.
--
-- O número: 059 a 062 foram reservados pelo plano na época; a 063 e a 064 já existem; a 065 e a 066 (travas, item
-- 28.1) correm em paralelo noutro branch. O executor aplica todo arquivo que ainda não está em `schema_migrations`,
-- em ordem de nome, então a ordem de chegada entre elas não importa.
--
-- Compatível com SQLite e PostgreSQL: TEXT, INTEGER, REAL, `REFERENCES … ON DELETE CASCADE` e `CHECK` de coluna; um
-- `ADD COLUMN` por instrução. Sem BEGIN/COMMIT: o executor de migrações já abre a transação.

CREATE TABLE pedidos (
    id                       TEXT PRIMARY KEY,
    titulo                   TEXT NOT NULL,
    objetivo                 TEXT NOT NULL,                  -- texto-base do comando: sem destino nem segredo (mesma recusa de `RunService.create`)
    contexto                 TEXT,
    criterios_sucesso        TEXT,                           -- JSON: lista verificável; NULL = sem critério (só prazo, contagem ou orçamento encerram)
    alvos                    TEXT,                           -- JSON: foto no formato de `runs.targets`
    autonomia                TEXT NOT NULL DEFAULT 'observar' CHECK (autonomia IN ('observar', 'preparar', 'agir')),
    fuso                     TEXT NOT NULL DEFAULT 'America/Sao_Paulo',   -- nome IANA; a recorrência é lida nele (28.3)
    inicio_em                TEXT,                           -- UTC
    fim_em                   TEXT,                           -- UTC; NULL = sem prazo
    max_ocorrencias          INTEGER CHECK (max_ocorrencias IS NULL OR max_ocorrencias > 0),
    orcamento_total_usd      REAL CHECK (orcamento_total_usd IS NULL OR orcamento_total_usd >= 0),
    orcamento_ocorrencia_usd REAL CHECK (orcamento_ocorrencia_usd IS NULL OR orcamento_ocorrencia_usd >= 0),
    sobreposicao             TEXT NOT NULL DEFAULT 'pular' CHECK (sobreposicao IN ('pular', 'guardar_uma', 'permitir_todas')),
    janela_recuperacao_s     INTEGER CHECK (janela_recuperacao_s IS NULL OR janela_recuperacao_s >= 0),   -- NULL = o padrão por tipo de gatilho (§7.5)
    coalescer                INTEGER NOT NULL DEFAULT 1 CHECK (coalescer IN (0, 1)),
    max_tentativas           INTEGER NOT NULL DEFAULT 2 CHECK (max_tentativas >= 1),    -- por ocorrência (§7.6)
    pausa_por_falha          INTEGER NOT NULL DEFAULT 3 CHECK (pausa_por_falha >= 1),   -- N falhas seguidas pausam (§6.2, §7.6)
    estado                   TEXT NOT NULL DEFAULT 'rascunho'
                             CHECK (estado IN ('rascunho', 'ativo', 'pausado', 'aguardando_pessoa', 'concluido',
                                               'encerrado', 'cancelado')),
    versao                   INTEGER NOT NULL DEFAULT 1,
    proxima_em               TEXT,                           -- UTC; CACHE da próxima materialização, recalculável pelo gatilho
    criado_por               TEXT,
    pausado_motivo           TEXT,                           -- sempre gravado quando o estado vira `pausado` (§6.2)
    encerrado_motivo         TEXT,                           -- prazo | contagem | orcamento | abandonado quando `encerrado` (§6.5); `concluido` e `cancelado` já dizem o motivo
    pai_id                   TEXT,                           -- pedido pai (§9, 28.10); sem chave estrangeira de propósito
    criado_em                TEXT NOT NULL,
    atualizado_em            TEXT NOT NULL
);
CREATE INDEX ix_pedidos_estado_proxima ON pedidos(estado, proxima_em);

CREATE TABLE pedido_gatilhos (
    id          TEXT PRIMARY KEY,
    pedido_id   TEXT NOT NULL REFERENCES pedidos(id) ON DELETE CASCADE,
    tipo        TEXT NOT NULL CHECK (tipo IN ('agora', 'horario', 'recorrencia', 'evento', 'condicao', 'persona')),
    spec        TEXT NOT NULL DEFAULT '{}',                  -- JSON; a interpretação é de quem trata o tipo (28.3, 28.8)
    cursor      TEXT,                                        -- último evento lido ou última avaliação (§7.8)
    ativo       INTEGER NOT NULL DEFAULT 1 CHECK (ativo IN (0, 1)),
    criado_em   TEXT NOT NULL
);
CREATE INDEX ix_pedido_gatilhos_pedido ON pedido_gatilhos(pedido_id, ativo);

CREATE TABLE pedido_ocorrencias (
    id                  TEXT PRIMARY KEY,
    pedido_id           TEXT NOT NULL REFERENCES pedidos(id) ON DELETE CASCADE,
    pedido_versao       INTEGER NOT NULL,                    -- a versão do pedido em que a ocorrência nasceu
    gatilho_id          TEXT REFERENCES pedido_gatilhos(id),   -- NULL: gesto manual ou backfill; apagar o gatilho NÃO apaga o histórico
    previsto_para       TEXT NOT NULL,                       -- UTC, no formato canônico de `chave.py` (segundo cheio, sufixo Z)
    chave               TEXT NOT NULL UNIQUE,                -- identidade e idempotência (§6.3)
    origem              TEXT NOT NULL CHECK (origem IN ('agenda', 'recuperacao', 'evento', 'condicao', 'persona',
                                                        'manual', 'backfill')),
    estado              TEXT NOT NULL DEFAULT 'prevista'
                        CHECK (estado IN ('prevista', 'devida', 'despachada', 'rodando', 'concluida', 'falhou',
                                          'incerta', 'cancelada', 'pulada', 'perdida')),
    tentativa           INTEGER NOT NULL DEFAULT 0,          -- quantas execuções já foram pedidas; `chave:t<n>` é a da n-ésima
    run_id              TEXT,                                -- a execução da tentativa atual; sem chave estrangeira (ver o topo)
    motivo              TEXT,                                -- por que `pulada`, `perdida`, `cancelada`, `falhou` ou `incerta`
    custo_usd           REAL NOT NULL DEFAULT 0,             -- acumulado de todas as tentativas, gravado antes da purga
    resumo              TEXT,
    materializada_token INTEGER,                             -- token da trava de líder que a materializou (cerca, §7.3)
    dono                TEXT,                                -- quem a despacha, para o despacho não correr em dobro
    prazo_posse         TEXT,                                -- UTC; a posse vence e outro laço assume
    criada_em           TEXT NOT NULL,
    iniciada_em         TEXT,
    terminada_em        TEXT,
    UNIQUE (pedido_id, gatilho_id, previsto_para)
);
CREATE INDEX ix_pedido_ocorrencias_pedido ON pedido_ocorrencias(pedido_id, estado);
CREATE INDEX ix_pedido_ocorrencias_estado ON pedido_ocorrencias(estado, previsto_para);

-- A execução que uma ocorrência cria: `RunService.create` com `idempotency_key = chave:t<n>`.
ALTER TABLE runs ADD COLUMN pedido_id TEXT;
ALTER TABLE runs ADD COLUMN ocorrencia_id TEXT;
ALTER TABLE runs ADD COLUMN prioridade INTEGER NOT NULL DEFAULT 0;
CREATE INDEX ix_runs_pedido ON runs(pedido_id);
