-- Um aparelho, uma etapa ativa — imposto pelo BANCO, não só pela contagem em `claim_step`.
--
-- O defeito: `claim_step` fazia `SELECT COUNT(*) ... WHERE instance_id=? AND status IN ('running','verifying')` e
-- só depois o `UPDATE`. Em SQLite, com um único backend, a serialização das escritas escondia a corrida. Em
-- PostgreSQL (READ COMMITTED, conexão em autocommit) dois backends passam pela MESMA contagem e assumem etapas
-- diferentes do mesmo aparelho: dois executores dirigindo um emulador ao mesmo tempo. Nenhum `FOR UPDATE`,
-- nenhum advisory lock e nenhum índice impediam isso — a promessa de "dono único do executor" era só uma leitura.
--
-- Índice único PARCIAL: vale em SQLite (desde 3.8) e em PostgreSQL com a mesma sintaxe, e só cobre os estados
-- ATIVOS — um aparelho continua podendo ter dezenas de etapas `pending`, `succeeded` ou `failed`.
--
-- Sem BEGIN/COMMIT: o executor de migrações já abre a transação.

-- Antes de impor, limpar: se um backend caiu deixando DUAS etapas ativas no mesmo aparelho (a corrida que este
-- índice passa a impedir), criar o índice falharia e o servidor não subiria. Então UMA delas (a de menor `id`,
-- critério só de desempate — o id é texto, não relógio) fica onde está e as demais voltam para `ready`, que é
-- EXATAMENTE o que a reconciliação de reinício já faz com etapa interrompida: nada é reexecutado às cegas, o
-- executor reobserva a tela antes de agir.
UPDATE steps SET status='ready',
                 status_detail='outra etapa já estava ativa neste aparelho; devolvida para reconciliação pela tela'
 WHERE status IN ('running','verifying')
   AND instance_id IS NOT NULL
   AND id NOT IN (SELECT MIN(id) FROM steps
                   WHERE status IN ('running','verifying') AND instance_id IS NOT NULL
                   GROUP BY instance_id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_steps_um_ativo_por_aparelho
    ON steps(instance_id) WHERE status IN ('running','verifying');
