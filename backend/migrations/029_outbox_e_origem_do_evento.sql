-- Fase 5 do plano-100 — item 5.6 (outbox de comandos + EventBus entre réplicas). Achado #30.
--
-- 1) `command_outbox` — a entrega DEVIDA, gravada na mesma transação que aceita o comando
--
-- Até aqui o "transporte" era `asyncio.create_task(_do_action(...))` logo depois de gravar o estado. Entre as
-- duas linhas não há durabilidade nenhuma: o processo que cai ali deixa um comando que o banco diz ter sido
-- aceito e que ninguém nunca enviou — e no boot seguinte ele vira `uncertain`, que é uma mentira em dois
-- sentidos (nada foi executado, e ninguém vai repetir).
--
-- Com a linha desta tabela gravada na MESMA transação em que o comando é aceito, a promessa passa a ser
-- verificável: existe linha `pending` = a entrega é devida, e quem subir de novo a executa. É o padrão outbox
-- clássico, e é o que permite trocar o transporte (WebSocket em processo hoje, NATS JetStream entre réplicas
-- quando houver broker) sem mexer em quem aceita o comando.
--
-- NÃO é exatamente-uma-vez. Fila durável entrega AO MENOS uma vez: a linha pode ser publicada, o processo cair
-- antes de marcá-la `sent`, e a mesma ordem sair de novo. Quem garante que o efeito não acontece duas vezes é o
-- diário do agente (`worker/diario.py`), que responde o desfecho guardado em vez de reexecutar o verbo.
CREATE TABLE command_outbox (
    command_id  TEXT PRIMARY KEY,
    instance_id TEXT NOT NULL,
    worker_id   TEXT,                                  -- quem deve executar; NULO = caminho local deste backend
    verb        TEXT NOT NULL,
    -- Os argumentos COMO FORAM ACEITOS, já sem segredo. Não dá para reconstruí-los de `commands.params`, que
    -- guarda só o `app_id`: `confirm` (a autorização humana do `reset`) se perderia no reenvio.
    payload     TEXT NOT NULL,
    state       TEXT NOT NULL DEFAULT 'pending',       -- pending | sent
    transport   TEXT NOT NULL DEFAULT 'websocket',     -- websocket | nats
    attempts    INTEGER NOT NULL DEFAULT 0,            -- quantas vezes a entrega SAIU daqui (1 = caminho feliz)
    created_at  TEXT NOT NULL,
    sent_at     TEXT
);

-- A consulta do dreno de partida e do despachante: "o que ainda devo entregar", mais antigo primeiro.
CREATE INDEX idx_command_outbox_pendentes ON command_outbox(state, created_at);

-- 2) `events.origin` — QUEM publicou o evento
--
-- O `EventBus` transmite para os WebSockets conectados AO PRÓPRIO processo. Com dois backends no mesmo
-- PostgreSQL (topologia que a posse de etapa e o `hosted_by` da 027 já admitem), o painel ligado na réplica B
-- não via nada do que a réplica A fazia até reconectar e pedir o histórico — um objetivo inteiro executava sem
-- uma linha na tela de quem estava olhando.
--
-- Com a origem gravada, cada réplica lê do banco o que as OUTRAS publicaram (`origin <> eu`) e entrega aos seus
-- assinantes. Filtrar pela origem é o que evita entregar duas vezes o evento local, que já saiu pelo caminho
-- direto. NULO = evento anterior a esta migração, ou banco de uma máquina só.
ALTER TABLE events ADD COLUMN origin TEXT;
