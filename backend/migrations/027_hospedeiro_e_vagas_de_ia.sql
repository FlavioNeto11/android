-- Fase 5 do plano-100 — itens 5.1 (hospedeiro e papéis), 5.2 (limite de IA global) e 5.3 (guarda de relógio).
-- Achados #171, #26, #27, #35, #94, #32.
--
-- 1) `instances.hosted_by` — QUEM hospeda o aparelho
--
-- Até aqui só a ETAPA tinha dono (016) e só a operação de app tinha dono (026). Tudo ACIMA da etapa supunha um
-- processo único: um segundo backend no mesmo banco enxergava TODOS os objetivos e fazia coisa destrutiva com os
-- que não eram dele — bloqueava ("Instância não existe na configuração atual"), marcava o aparelho como offline
-- e, com o rodízio ligado, criava no PRÓPRIO disco um AVD novo com o mesmo id lógico para atender a tarefa: um
-- aparelho vazio, sem a sessão do perfil. Com esta coluna o despacho, o rodízio e as reconciliações de partida
-- passam a atuar SÓ no que este backend hospeda, e o objetivo alheio é IGNORADO — nunca bloqueado.
--
-- Não confundir com `objectives.hosted_by` (022): lá é a fotografia de quem DESPACHOU aquele objetivo; aqui é de
-- quem hospeda o aparelho agora. NULO = ninguém reivindicou ainda; o primeiro backend que hospeda aparelhos
-- carimba o que está na configuração dele, e nunca por cima de outro dono.
ALTER TABLE instances ADD COLUMN hosted_by TEXT;       -- OWNER_ID do backend que tem o emulador/túnel
CREATE INDEX IF NOT EXISTS idx_instances_hospedeiro ON instances(hosted_by);

-- 2) `runs.planned_by` — quem começou o planejamento
--
-- `resume_planning_after_restart` replaneja TODA execução em `status='planning'`. Com dois backends, o que sobe
-- dispara um segundo planejamento PAGO da execução que o outro está planejando naquele instante (achado #26).
-- Com o dono carimbado, cada um retoma só o que era seu.
ALTER TABLE runs ADD COLUMN planned_by TEXT;           -- OWNER_ID de quem abriu o planejamento

-- 3) `ai_slots` — as vagas de chamada de IA, compartilhadas por TODOS os backends do mesmo banco
--
-- `max_ai_concurrency` era um semáforo em memória: com dois backends, limite 3 virava 6 chamadas pagas
-- simultâneas (achados #35 e #94). Cada linha é UMA vaga, tomada por compare-and-swap (`WHERE slot=? AND (holder
-- IS NULL OR expires_at < ?)`) — e não por "conte as vivas e insira", que em READ COMMITTED deixa dois backends
-- lerem o mesmo zero, o mesmo erro que `claim_step` já aprendeu.
--
-- `expires_at` existe para sobreviver à QUEDA de um backend: vaga cujo dono parou de renovar volta ao mercado
-- sozinha, sem ninguém para liberá-la. `role` nasce aqui porque o hub de IA vai pedir limite POR FUNÇÃO, e é mais
-- barato ter a coluna do que migrar de novo.
--
-- `boot_parallelism` continua por processo, DE PROPÓSITO: lá o recurso protegido é a RAM desta máquina, e
-- torná-lo global faria duas máquinas de 64 GB esperarem uma pela outra para ligar aparelho.
CREATE TABLE ai_slots (
  slot        INTEGER PRIMARY KEY,   -- 1..max_ai_concurrency; as linhas nascem sob demanda
  holder      TEXT,                  -- OWNER_ID de quem está usando a vaga; NULO = livre
  role        TEXT,                  -- plan | decide | verify | social — para o limite por função do hub de IA
  taken_at    TEXT,
  expires_at  TEXT                   -- relógio do BANCO; vencido = a vaga pode ser tomada de quem caiu
);
CREATE INDEX IF NOT EXISTS idx_ai_slots_dono ON ai_slots(holder);
