-- Exceção de uso único à regra de uma conta por alvo (item 30.65; número 104 reservado pela orquestradora em 04/10).
--
-- Por quê: o dono autorizou UMA mensagem direta de prova (31.26) entre duas contas nossas, mas a porta de frota (ADR-055,
-- `PolicyEngine._fleet_gate`) recusa sem caminho de aprovação quando outra conta da frota já mexeu com o alvo em 30 dias,
-- e a única alavanca de configuração (`fleet_target_window_days`) é global. Esta tabela guarda a exceção pontual: um
-- perfil, um alvo, uma ação, com prazo, criada por rota de pessoa citando a autorização. Ela NÃO libera sozinha: a etapa
-- casada vira `approval_required` (o dono vê o item em Pendências com o porquê) e as demais regras seguem valendo.
--
--     regra         a regra que a exceção afrouxa. Só `uma_conta_por_alvo` existe.
--     profile_id    o perfil de ORIGEM (`instagram_profiles.id`), o único que pode usá-la.
--     alvo          o alvo normalizado (`@nome`), como a porta compara.
--     capability    a ação (`SEND_MESSAGE`...). Exceção de uma ação não vale para outra no mesmo alvo.
--     motivo        por que existe, em uma frase. Sem senha, e-mail, telefone nem texto da mensagem.
--     autorizacao   quem autorizou, por onde e quando (ex.: dono, Telegram, 04/10 19:02:26Z, entrada 1189).
--     autor         quem criou pela rota (o operador da sessão; sem sessão, o rótulo do loopback, `panel`).
--     autor_com_sessao  1 se havia sessão de operador. Só então o cartão diz "autorizada pelo dono"; sem sessão ele
--                   diz quem criou e cita a autorização como texto.
--     criada_em / expira_em   UTC. A rota recusa prazo acima de 72 h.
--     step_id / presa_em      a etapa que a porta casou com ela. Outra etapa só a toma se a presa terminou sem efeito.
--     usada_em / interaction_id   quando o efeito saiu: uso único, gasta no `open_effect`.
--     vencida_em    quando venceu por prazo sem uso (`vencer`, na porta e na leitura da rota). Continua apontando a
--                   etapa: a reserva no commit a encontra e falha.
--     etapa_do_uso / run_do_uso   a etapa e a execução que a reservaram, gravadas na reserva e NUNCA limpas: a trilha
--                   da exceção a uma regra de ADR diz para sempre quem a usou (o `step_id` é a ligação viva da porta e
--                   pode ser solto depois).
--     em_uso_em     quando o executor a reservou, logo antes do gesto (UPDATE condicional). Em uso não volta a
--                   aberta, não vence e não é revogada; o `settle_effect` a liquida (usada ou `sem_efeito`).
--     encerrada_em / encerrada_por / encerramento   quando foi encerrada sem uso: `recusada` (a pessoa rejeitou o
--                   cartão da etapa presa), `revogada` (pela rota), `sem_efeito` (reservada, e o gesto não teve
--                   efeito) ou `incerta` (reservada, e a etapa terminou sem liquidação: o efeito pode ter saído).
--                   Encerrada não volta a valer.
--
-- O alvo é sempre conta nossa viva (a rota recusa pessoa real) e só há uma em aberto por perfil, alvo e ação.
--
-- Compatível com SQLite e PostgreSQL: só tipos comuns. Sem BEGIN/COMMIT: o executor já abre a transação.

CREATE TABLE excecoes_de_politica (
  id               TEXT PRIMARY KEY,
  regra            TEXT NOT NULL CHECK (regra IN ('uma_conta_por_alvo')),
  profile_id       TEXT NOT NULL,
  alvo             TEXT NOT NULL,
  capability       TEXT NOT NULL,
  motivo           TEXT NOT NULL,
  autorizacao      TEXT NOT NULL,
  autor            TEXT NOT NULL,
  autor_com_sessao INTEGER NOT NULL DEFAULT 0,
  criada_em        TEXT NOT NULL,
  expira_em        TEXT NOT NULL,
  step_id          TEXT,
  presa_em         TEXT,
  usada_em         TEXT,
  interaction_id   TEXT,
  vencida_em       TEXT,
  em_uso_em        TEXT,
  etapa_do_uso     TEXT,
  run_do_uso       TEXT,
  encerrada_em     TEXT,
  encerrada_por    TEXT,
  encerramento     TEXT CHECK (encerramento IS NULL OR encerramento IN ('recusada','revogada','sem_efeito','incerta'))
);

CREATE INDEX ix_excecoes_de_politica_alvo ON excecoes_de_politica (profile_id, alvo, capability);
CREATE INDEX ix_excecoes_de_politica_etapa ON excecoes_de_politica (step_id);
