-- Comando do painel passa a ser ENTIDADE, com o mesmo rigor que as etapas da IA já tinham.
--
-- O defeito que isto corrige: `POST /api/instances/{id}/actions/{action}` respondia `202 {"accepted": true}` e
-- disparava a ação sem registro nenhum. Sem id, sem ACK, sem estado terminal. Qualquer exceção era engolida e
-- virava um evento `log` que o frontend nem trata, então a interface mostrava toast VERDE para ação que não
-- aconteceu. Em aparelho de outra máquina isso ficou grosseiro: "Resetar dados" era recusado no fundo e
-- indistinguível de sucesso; "Parar" era desfeito pelo monitor em ≤36 s sem avisar ninguém.
--
-- A assimetria era o ponto: a IA já tinha "enviei / recebi / concluí / não sei" (`actions.status`:
-- intended → done | failed | unknown | rejected, gravado ANTES de tocar no aparelho). O comando do painel tinha
-- só "aceitei a requisição". Esta tabela dá ao comando o mesmo vocabulário.
--
-- Sem BEGIN/COMMIT: o executor de migrações já abre a transação.

-- created          = gravado, ainda não entregue a ninguém
-- dispatched       = entregue ao worker (ou à tarefa local), sem confirmação de recebimento
-- acked            = o worker confirmou que RECEBEU (ainda não agiu)
-- running          = o worker começou a executar
-- succeeded        = concluído e confirmado
-- failed           = falhou, e sabemos que não teve efeito
-- uncertain        = NÃO sabemos se teve efeito (queda no meio, timeout de ação com efeito externo).
--                    Nunca é repetido às cegas: alguém decide, ou o estado é verificado antes.
-- rejected         = recusado no pré-voo; NUNCA foi despachado, então nada aconteceu no aparelho
-- cancel_requested = pedimos cancelamento; o worker ainda não confirmou
-- cancelled        = cancelamento CONFIRMADO pelo worker
CREATE TABLE commands (
    id               TEXT PRIMARY KEY,
    instance_id      TEXT NOT NULL,
    worker_id        TEXT,                      -- nulo enquanto o único executor é o local (preenchido em E4)
    verb             TEXT NOT NULL,             -- start | stop | hibernate | restart | reset | install_apk | …
    params           TEXT,                      -- JSON dos argumentos, já sem segredo
    -- Mesma chave, mesmo comando: repetir a requisição não repete o efeito. É o que torna seguro o cliente
    -- reenviar quando não sabe se a primeira chegou.
    idempotency_key  TEXT NOT NULL UNIQUE,
    state            TEXT NOT NULL,
    -- Cerca (fencing token) monotônica por aparelho. Um worker que perdeu a autorização e voltou do limbo
    -- apresenta cerca velha, e o resultado dele é RECUSADO em vez de sobrescrever o estado atual. Sem isto,
    -- "worker antigo continua executando" é indetectável.
    fence            INTEGER NOT NULL DEFAULT 0,
    requested_by     TEXT NOT NULL,             -- panel | scheduler | system
    reason           TEXT,                      -- por que foi recusado, ou o que deu errado (texto humano)
    result           TEXT,                      -- JSON do que o executor devolveu
    attempt          INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL,
    dispatched_at    TEXT,
    acked_at         TEXT,
    started_at       TEXT,
    finished_at      TEXT
);

-- A consulta que a interface faz: "os últimos comandos deste aparelho", mais recente primeiro.
CREATE INDEX idx_commands_instance ON commands(instance_id, created_at DESC);
-- A consulta da reconciliação no boot: "o que ficou em voo quando o processo caiu?".
CREATE INDEX idx_commands_abertos ON commands(state, created_at);
