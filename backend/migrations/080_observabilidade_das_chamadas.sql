-- Observabilidade das chamadas de IA (RA-10 da reavaliação de 03/10; pré-requisito do relatório do 31.10, do RA-16 e
-- do LT-10). Metade das perguntas do dono sobre a IA não se respondia por `ai_calls`: o rejulgamento era um `verify`
-- qualquer, não se sabia por que uma decisão foi ao modelo caro nem por que a imagem foi junto, e o veredito só existia
-- no texto da etapa. Quatro colunas, todas em vocabulário FECHADO no código (`planning/provider.py`):
--
--     verdict       o desfecho da chamada: no `verify`, yes | no | uncertain | unprovable; no `decide`, a ferramenta
--                   escolhida; no `plan`, plano | pergunta (só pergunta, nenhuma etapa);
--     escalate      POR QUE a chamada foi ao modelo de escalonamento (NULO = não foi): no `decide`, efeito |
--                   nova_tentativa | erros_seguidos | ciclo | piso | bloqueio; no `verify`, nivel | sim_com_efeito;
--     motivo        PARA QUE a chamada foi feita dentro do papel: julgamento | rejulgamento | vazio (verify),
--                   decisao | cascata (decide), plano (plan). São os grupos de `/api/usage`;
--     image_reason  por que a imagem foi, ou não, junto, na ordem em que o executor decide: sensivel | politica_nunca |
--                   politica_sempre | pedida | problema | primeira_julgada | arvore_pobre | arvore_rica.
--
-- E uma coluna na sombra da decisão fechada:
--
--     motivo_privacidade  o código FECHADO da recusa por privacidade (o porquê de `fallback_reason='privacidade'`),
--                         que hoje se perde; NULO nas demais linhas e nas anteriores.
--
-- Discordância do rejulgamento sem coluna própria: (escalate=nivel e verdict=yes) ou (escalate=sim_com_efeito e
-- verdict<>yes) — o modelo forte desfez o veredito do barato.
--
-- NULO nas linhas antigas, que ninguém reclassifica. Sem CHECK (o vocabulário muda sem migração; quem confere é o
-- código), sem índice (os grupos leem o recorte de `ts` que `/api/usage` já filtra). `ADD COLUMN` só: vale igual no
-- SQLite e no PostgreSQL. Aplica depois da 079 (a coluna `ambiguos` da mesma sombra), que também é só `ADD COLUMN`.
-- Sem BEGIN/COMMIT (o executor de migrações já abre a transação).

ALTER TABLE ai_calls ADD COLUMN verdict TEXT;
ALTER TABLE ai_calls ADD COLUMN escalate TEXT;
ALTER TABLE ai_calls ADD COLUMN motivo TEXT;
ALTER TABLE ai_calls ADD COLUMN image_reason TEXT;

ALTER TABLE decisao_fechada_sombra ADD COLUMN motivo_privacidade TEXT;
