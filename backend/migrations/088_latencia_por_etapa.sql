-- Latência por etapa como métrica de primeira classe (item 31.24, Fase 31; pedido do dono de 03/10: saber onde a
-- automação perde tempo). Número 088 reservado pela orquestradora em 03/10 21:18Z; a 087 é do 32.2 (Trello) e a lacuna
-- na numeração da main é esperada até ela entrar.
--
-- Por quê: a leitura de 24 h de 03/10 (`scripts/latencia-por-etapa.py`, tabela "antes" em docs/ia.md) dividiu a parede
-- de uma execução de trabalho em IA 46 %, ações no aparelho 5,5 % e um "resto dentro das tentativas" de 39 % que nada
-- no banco atribuía. A passagem da decisão para a ação leva 2 ms: o tempo está antes e em volta da chamada. Estas
-- colunas dão dono a esse tempo. Só medem: nenhum comportamento muda, e nenhuma coluna guarda texto.
--
-- C-3, início da chamada (`ai_calls`):
-- - `started_at`: a hora de parede (ISO-8601) em que `StepExecutor._ai` entrega a chamada ao provedor, já com a vaga
--   de IA na mão. `ts` continua sendo o fim. Então `(ts − started_at) − ms` é o que o provedor faz em volta da ida e
--   volta (montar o conteúdo, a imagem em base64, ler a resposta); `ms` segue sendo só a ida e volta.
-- - `vaga_ms`: a espera pela vaga do semáforo de IA (`max_ai_concurrency`) antes disso.
-- - NULOS nas chamadas que não passam por `_ai`: o curador e o leitor pelo hub, a sombra do Jev, a imagem da persona, a
--   resposta social. NULOS também no legado.
--
-- C-2, preparo de cada decisão do ator (`ai_calls`, só nas linhas `decide` de execução que responderam):
-- - `prep_settle_ms`: as esperas de assentamento (`action_settle_s`, `recipe_settle_s`) desde a volta anterior.
-- - `prep_observacao_ms`: a observação inteira que alimentou esta decisão: releituras incluídas, e a imagem completada
--   depois (quando a receita divergiu), se houve.
-- - `prep_arvore_ms` e `prep_imagem_ms` são PARTES dela: a leitura da hierarquia, e a imagem que foi ao modelo
--   (captura e codificação, a completada incluída). O resto da observação (classificar a árvore, a prévia do painel
--   tirada pela própria observação, as releituras) é `observacao − arvore − imagem`.
-- - `prep_prompt_ms`: o executor montando o pedido (tela, histórico, lições) entre o fim da observação e a entrada em
--   `_ai`, sem a imagem completada.
-- - Então, da ação anterior (ou do início da tentativa) até `started_at`: settle + observação + prompt + `vaga_ms` +
--   "outros". "Outros" (detector de trava, receita, o atalho LT-1 com o juiz, a nova tentativa de uma chamada com
--   erro) a leitura calcula por diferença. Medidas com `time.monotonic`; o executor não faz nenhuma chamada a mais ao
--   aparelho nem à IA para medi-las.
--
-- C-1, ação → decisão (`actions.ai_call_id`): o id da linha de `ai_calls` do decide que escolheu a ação. NULO quando
-- quem decidiu foi a receita, o executor ou a pessoa, e no legado. Sem chave estrangeira: `ai_calls` tem retenção
-- (`log_retention_days`) e `actions` não; o id órfão continua dizendo que houve uma decisão da IA.
--
-- C-4, juiz e evidência por tentativa (`attempts`, gravados no mesmo UPDATE da trilha da 045, ao fim da tentativa):
-- - `juiz_espera_ms`: as esperas deliberadas do verificador (`judge_wait_s`).
-- - `verificacao_ms`: o tempo inteiro dentro de `_verify` (sondagens, árvore do juiz, chamadas e esperas).
-- - `evidencia_ms`: as capturas e gravações de evidência da tentativa.
-- - NULOS na tentativa que não passou pelo executor e no legado; 0 = medido, não houve.
--
-- C-5, espera por motivo (`esperas`, tabela nova): uma linha por intervalo em que um objetivo esperou com motivo
-- tipado. Abre e fecha nas transições que já são centrais no repositório (`note_waiting`, `clear_wait_reason`,
-- `set_objective`), só quando o motivo muda.
-- - `motivo`: `aparelho` (device_slot), `perfil` (profile_limit), `rede`, `caminho` (pathfinder) e `pessoa`
--   (objetivo em `waiting_user`).
-- - A vaga de IA e a resposta do modelo NÃO entram: já estão em `ai_calls` (`vaga_ms`, `ms`).
-- - A aprovação já se mede em `pending_approvals` (`created_at` → `decided_at`).
-- - Tempo de parede em texto ISO-8601, porque o intervalo atravessa reinícios. `fim` NULO = aberta: a leitura a fecha
--   no fim do objetivo, ou agora. A duração se calcula na leitura, como em `app/desempenho.py`.
-- - "Execução pendurada" não se grava (gravar exigiria um laço de vigia): a leitura a deriva como o tempo de execução
--   aberta sem tentativa, chamada, ação nem espera aberta.
--
-- Mesmo padrão da 082 e da 085: sem CHECK no vocabulário (`motivo` cresce sem migração), SEM chave estrangeira, tempo
-- em texto ISO-8601. Sem retenção própria: cresce como `objectives` (poucas linhas por objetivo), que também fica.
-- `ADD COLUMN` e tabela nova só. Compatível com SQLite e PostgreSQL. Sem BEGIN/COMMIT (o executor de migrações já abre
-- a transação).

ALTER TABLE ai_calls ADD COLUMN started_at TEXT;
ALTER TABLE ai_calls ADD COLUMN vaga_ms INTEGER;
ALTER TABLE ai_calls ADD COLUMN prep_settle_ms INTEGER;
ALTER TABLE ai_calls ADD COLUMN prep_observacao_ms INTEGER;
ALTER TABLE ai_calls ADD COLUMN prep_arvore_ms INTEGER;
ALTER TABLE ai_calls ADD COLUMN prep_imagem_ms INTEGER;
ALTER TABLE ai_calls ADD COLUMN prep_prompt_ms INTEGER;

ALTER TABLE actions ADD COLUMN ai_call_id BIGINT;

ALTER TABLE attempts ADD COLUMN juiz_espera_ms INTEGER;
ALTER TABLE attempts ADD COLUMN verificacao_ms INTEGER;
ALTER TABLE attempts ADD COLUMN evidencia_ms INTEGER;

CREATE TABLE IF NOT EXISTS esperas (
    id            {{PK_AUTO}},
    run_id        TEXT,                           -- a execução do objetivo (sem FK)
    objective_id  TEXT NOT NULL,                  -- quem esperou (sem FK)
    motivo        TEXT NOT NULL,                  -- 'aparelho' | 'perfil' | 'rede' | 'caminho' | 'pessoa'
    inicio        TEXT NOT NULL,
    fim           TEXT                            -- NULL = aberta
);
-- A transição fecha "a aberta deste objetivo"; a leitura recorta por execução.
CREATE INDEX IF NOT EXISTS ix_esperas_objetivo ON esperas(objective_id, fim);
CREATE INDEX IF NOT EXISTS ix_esperas_run ON esperas(run_id);
