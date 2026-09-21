-- Posse de etapa. Sem ela, um segundo backend subindo reconciliaria as etapas VIVAS do primeiro: o
-- `reconcile_after_restart` pegava toda etapa `running`, sem perguntar de quem era, e o dono legítimo perderia o
-- trabalho em andamento. Com PostgreSQL isso deixou de ser hipotético — dois backends passam a caber no mesmo banco.
--
-- O dono é a MÁQUINA (`OWNER_ID`, por omissão o hostname), não o processo. De propósito: assim o reinício do mesmo
-- backend continua reconciliando as próprias etapas na hora, que é o comportamento já provado, enquanto um backend
-- de OUTRA máquina não toca nelas enquanto o lease valer.
ALTER TABLE steps ADD COLUMN claimed_by TEXT;
ALTER TABLE steps ADD COLUMN claim_expires_at TEXT;

-- O índice serve às duas perguntas do despacho: "o que é meu para reconciliar" e "que lease de outro venceu".
CREATE INDEX IF NOT EXISTS idx_steps_posse ON steps(claimed_by, status);
