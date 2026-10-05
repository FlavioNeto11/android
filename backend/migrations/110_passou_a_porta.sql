-- Item 31.64, S1 da revisão do #350 (número reservado pela orquestradora em 05/10 02:02Z): a etapa marca que PASSOU a
-- porta de política (`_policy_gate` liberou o efeito), na mesma passada sem `await` em que a porta decide.
--
-- Por quê: a regra do objeto na família (31.53) conta as etapas das outras personas do mesmo pedido que estão publicando.
-- Ordenar pela tomada (`started_at`) deixava duas publicarem a mesma imagem quando uma voltava de `retry_wait` com a data
-- da PRIMEIRA tomada, e dependia do relógio de cada máquina. A marca não depende de relógio: quem já passou conta sempre
-- (enquanto não falhou nem foi cancelada); "só as mais antigas" vale só entre as que ainda não passaram.
--
-- Só ADD COLUMN, NOT NULL DEFAULT 0 (as etapas anteriores contam como "não passou", o mesmo de antes desta migração), sem
-- CHECK. Compatível com SQLite e PostgreSQL. Sem BEGIN/COMMIT: o executor abre a transação.

ALTER TABLE steps ADD COLUMN passou_a_porta INTEGER NOT NULL DEFAULT 0;
