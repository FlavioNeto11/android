-- Trava de líder dos laços periódicos (item 28.1, Fase 28; design `docs/design/pedidos-persistentes.md` §7.3).
--
-- Hoje a ÚNICA coisa que impede um laço de fundo de rodar em dobro é o papel do processo (`ROLE=all/scheduler/api`):
-- dois backends com scheduler no mesmo banco rodam a retenção, a curadoria do aprendizado e o livro-caixa das contas
-- de IA cada um por si — duas consultas pagas ao relatório do provedor e dois "fechamentos do dia" na mesma conta.
-- Cada linha é UMA trava, por nome ('saldos', 'curadoria', 'retencao'; a Fase 28 acrescenta as suas), no molde das
-- vagas de IA (`ai_slots`, 027):
--     dono      = o OWNER_ID do backend que a tem; NULL = livre (saída limpa);
--     token     = cerca (fencing token, Kleppmann): cresce 1 a CADA mandato novo e nunca volta. Quem escreve depois
--                 de perder a trava (pausa longa, relógio parado) apresenta o token velho e é recusado;
--     expira_em = vencimento pelo relógio do BANCO; vencida, qualquer backend toma por CAS
--                 (`WHERE nome=? AND (dono IS NULL OR expira_em < agora)`);
--     tomada_em = início do mandato atual, para a saúde e para o diagnóstico.
-- As linhas nascem sob demanda e nunca são apagadas: apagar zeraria o token e reabriria a porta ao escritor velho.
--
-- O número: 059 era o nome no plano, mas a main já aplicou 063 e 064 no central; um arquivo de número menor criado
-- depois entra em ordem diferente num banco novo e no do central. A 065 ficou separada pela coordenação para outro
-- trabalho em curso (02/10); o executor aplica por nome e tolera o buraco.
--
-- Compatível com SQLite e PostgreSQL: só tipos comuns, sem `{{PK_AUTO}}` (a chave é o nome).
-- Sem BEGIN/COMMIT: o executor de migrações já abre a transação.

CREATE TABLE travas (
  nome       TEXT PRIMARY KEY,
  dono       TEXT,
  token      INTEGER NOT NULL DEFAULT 0 CHECK (token >= 0),
  expira_em  TEXT,
  tomada_em  TEXT
);
