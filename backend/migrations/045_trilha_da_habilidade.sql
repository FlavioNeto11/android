-- A trilha da execução passa a dizer QUAL habilidade, QUAL versão, QUAL nó e COM QUAL estratégia cada etapa rodou,
-- QUAIS recursos o aparelho resolveu, e a que TENTATIVA cada chamada de IA pertence (§14.4).
--
-- Tudo nulo por padrão, sem FK (mesmo motivo de `runs.flow_id` e da 036) e SEM preencher linhas antigas: execução
-- anterior a esta migração é lida pelo adaptador (`runs.flow_id` -> habilidade legada `flow:<id>` versão 1), e o
-- `skill_hash` nulo quer dizer "não se sabe", nunca é inventado.
-- ADD COLUMN nulo é só metadado no SQLite e no PostgreSQL 11+: não reescreve `steps`, `runs` nem `ai_calls`.
--
-- O que NÃO entra: `steps.capability` já existe (010) e é a ação do catálogo (chave de política e limite);
-- `steps.driven_by` (005) continua sendo o que DE FATO conduziu a etapa (o painel a tipa como união fechada).
--
-- Sem BEGIN/COMMIT: o executor de migrações já abre a transação.

ALTER TABLE runs ADD COLUMN skill_id TEXT;            -- habilidade resolvida no planejamento (nulo = planejador)
ALTER TABLE runs ADD COLUMN skill_version INTEGER;
ALTER TABLE runs ADD COLUMN skill_hash TEXT;          -- sha256 do conteúdo executado (vale também para fluxo legado)

ALTER TABLE objectives ADD COLUMN resource_plan TEXT; -- JSON: ResourceSpec resolvido para ESTE aparelho

ALTER TABLE steps ADD COLUMN skill_id TEXT;           -- difere de runs.skill_id quando a habilidade compõe outra
ALTER TABLE steps ADD COLUMN skill_version INTEGER;
ALTER TABLE steps ADD COLUMN node_id TEXT;            -- nó da habilidade que gerou a etapa (sobrevive a replanejamento)
-- Estratégia PLANEJADA, do enum StrategyKind: deterministic | recipe | app_provider | ui_generic | ai_actor | human.
-- Com mais de uma, a cadeia em ordem, separada por '>' (ex.: recipe>ai_actor).
ALTER TABLE steps ADD COLUMN strategy TEXT;

-- Estratégia EFETIVA da tentativa, no mesmo vocabulário; a cadeia exercida quando uma receita diverge e a IA
-- assume na mesma tentativa (ex.: recipe>ai_actor).
ALTER TABLE attempts ADD COLUMN strategy TEXT;
ALTER TABLE attempts ADD COLUMN recipe_id INTEGER;    -- receita reproduzida (hoje só existe no texto da decisão)

-- Papel e modelo de IA POR TENTATIVA: sem isto o custo só se atribui à etapa (`step_id`), e a tentativa que a
-- receita resolveu fica indistinguível da que a IA pagou.
ALTER TABLE ai_calls ADD COLUMN attempt_id TEXT;

CREATE INDEX IF NOT EXISTS idx_runs_habilidade ON runs(skill_id, skill_version);
CREATE INDEX IF NOT EXISTS idx_steps_habilidade ON steps(skill_id, skill_version, node_id);
