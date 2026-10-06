-- Operação com N agentes (prova de 07/10, J2, adendo v1.94): um objetivo único entregue a N alvos, cada alvo =
-- persona + conta (dela, no app da operação) + aparelho, numa execução PRÓPRIA. Uma execução só não serve: `objectives`
-- tem UNIQUE(run_id, instance_id), e 30 contas não cabem em 9 aparelhos de uma vez; a fila por aparelho serializa as
-- execuções do mesmo aparelho.
--
-- Sem BEGIN/COMMIT: o executor de migrações já abre a transação.

--   acao_final: preparar = cada alvo para antes da etapa com efeito fora do aparelho (texto gerado, interface pronta);
--               executar = segue até a ação e a verificação, sob as portas de sempre
--   status:     em_curso | concluida | concluida_com_bloqueios | cancelada
CREATE TABLE operacoes (
    id               TEXT PRIMARY KEY,
    command          TEXT NOT NULL,
    app_id           TEXT NOT NULL,
    acao_final       TEXT NOT NULL CHECK (acao_final IN ('preparar', 'executar')),
    max_usd          REAL NOT NULL,
    -- O que precisa ser compreendido, dito pelo operador, e as fontes públicas que ele indica (JSON, lista de URLs).
    -- São a única origem da pesquisa externa da operação: a consulta nunca nasce de texto de persona nem de tela.
    assunto          TEXT,
    fontes           TEXT NOT NULL DEFAULT '[]',
    status           TEXT NOT NULL,
    idempotency_key  TEXT NOT NULL UNIQUE,
    corpo_sha256     TEXT NOT NULL,             -- a mesma chave com outro corpo é recusada (409 chave_em_uso)
    criada_por       TEXT,                      -- nome da sessão do operador, quando houver
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    finished_at      TEXT
);

-- Um alvo por linha. `run_id` nulo = o alvo não ganhou execução (parou em `conta`/`sessao`/`aparelho`, ou foi barrado
-- pelo teto de custo antes de nascer). `estagio`/`estado`/`motivo` gravados aqui são os da CRIAÇÃO e os marcados por quem
-- os conhece fora da execução (o conhecimento recuperado, pela frente de aprendizado); o resto é derivado na leitura do
-- que a execução já grava (etapas, rascunho, efeitos), sem gancho no meio do despacho.
--   estado: pendente | em_curso | concluido | bloqueado | cancelado
CREATE TABLE operacao_alvos (
    operacao_id      TEXT NOT NULL REFERENCES operacoes(id) ON DELETE CASCADE,
    seq              INTEGER NOT NULL,
    profile_id       TEXT NOT NULL,
    account_id       TEXT,
    instance_id      TEXT,
    run_id           TEXT,
    estagio          TEXT NOT NULL,
    estado           TEXT NOT NULL,
    motivo           TEXT,
    marcas           TEXT NOT NULL DEFAULT '{}', -- JSON {estagio: hora} marcados de fora (registrar_estagio)
    updated_at       TEXT NOT NULL,
    PRIMARY KEY (operacao_id, profile_id)
);

CREATE INDEX ix_operacao_alvos_run ON operacao_alvos (run_id);

ALTER TABLE runs ADD COLUMN operacao_id TEXT;
CREATE INDEX ix_runs_operacao ON runs (operacao_id);
