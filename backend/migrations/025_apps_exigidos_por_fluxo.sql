-- Quais APLICATIVOS um fluxo precisa para acontecer (item 6.1 do plano-100 / E10; achado #81).
--
-- O defeito, do jeito que dói: um fluxo que usa dois apps (copiar algo das Configurações e colar no Instagram)
-- não tinha como declarar isso. `flows.app_id` é UM app, sem FK e sem lista, e `scheduler._app_context` resolve
-- UM pacote por execução. Nenhum fluxo era conferido contra `device_app_state` antes de ser agendado: a
-- pendência só aparecia depois, como etapa que falha ou item bloqueado — sem ação clara para resolver.
--
-- Aqui o fluxo declara os apps de que precisa, e a criação da execução confere, POR APARELHO, se cada pacote
-- exigido está entregável. A recusa passa a ser explicada antes de agendar, com a ação ("distribua X em
-- android-12"), que é a regra do pedido: a limitação se explica ANTES, não no meio.
--
-- `app_id` referencia `apps` de verdade (a coluna `flows.app_id` nunca teve FK): um app removido da
-- configuração leva junto a exigência que ninguém poderia mais satisfazer.
--
-- Sem BEGIN/COMMIT: o executor de migrações já abre a transação.

CREATE TABLE flow_required_apps (
  flow_id  TEXT NOT NULL REFERENCES flows(id) ON DELETE CASCADE,
  app_id   TEXT NOT NULL REFERENCES apps(id) ON DELETE CASCADE,
  PRIMARY KEY (flow_id, app_id)
);

-- "De que apps este fluxo precisa?" é a pergunta do pré-voo, feita a cada criação de execução que casa.
CREATE INDEX IF NOT EXISTS idx_flow_required_apps_app ON flow_required_apps(app_id);
