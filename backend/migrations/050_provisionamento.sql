-- Provisionamento de aparelho pela plataforma, no servidor local (segunda evolução, onda D; ADR-001, ADR-015).
--
-- Até aqui nenhuma das três origens de instância criava aparelho NOVO pela plataforma: a configuração
-- (`instances.count`), a adoção de aparelho anunciado por um worker (migração 024) e o verbo `create`, que só
-- cria o AVD de uma instância já declarada. `POST /api/instances` passa a inserir a linha (`origin='dynamic'`,
-- como a adoção) e a abrir o `create` pelo caminho de sempre. O que faltava no esquema:
--
--   `worker_limits.max_devices`     = teto de APARELHOS (existentes, não ligados) que o dono aceita naquela
--                                     máquina. NULL = sem teto. Distinto de `max_slots`, que é RAM (ligados ao
--                                     mesmo tempo): criar AVD custa disco e inventário, não RAM.
--   `instances.android_overrides`   = JSON com o que a pessoa pediu para ESTA instância (`system_image`,
--                                     `ram_mb`, `window`…), mesclado por cima do `android` padrão na criação e no
--                                     boot. `cfg.instance_android` só lê o YAML, e o YAML não conhece instância
--                                     criada em tempo de execução.
--   `instances.retired_at`          = aposentadoria. A linha NÃO se apaga: o histórico de objetivos, comandos e
--                                     vínculos referencia o id, e o idx/portas não são reaproveitados.
--
-- Compatível com SQLite e PostgreSQL: só tipos TEXT/INTEGER, sem marca de dialeto.

ALTER TABLE worker_limits ADD COLUMN max_devices INTEGER;      -- teto de aparelhos da máquina; NULL = sem teto
ALTER TABLE instances ADD COLUMN android_overrides TEXT;       -- JSON: sobreposição do `android` por instância
ALTER TABLE instances ADD COLUMN retired_at TEXT;              -- aposentada em (ISO); NULL = ativa
