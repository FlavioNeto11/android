-- Item 29.81 (número reservado pela orquestradora em 04/10): a foto ENVIADA pelo dono pode ter sido feita por IA. A
-- regra do dono (03/10) manda a foto realista de IA sair com o rótulo do Instagram; o 29.79 decide o rótulo pela origem
-- (`source`), e o upload nascia sempre "sem rótulo". Esta coluna guarda o que o dono disse do upload.
--
-- - `feita_por_ia`: 1 = feita por IA (o upload sai COM o rótulo); 0 = foto real (sai sem); nulo = não informado (sai sem,
--   com o aviso "sem rótulo de IA (imagem enviada por você)"). Só o upload a usa: a gerada é sempre de IA e a importada
--   leva o rótulo pelo lado seguro.
--
-- Só ADD COLUMN, nula: nada é reescrito (metadado no SQLite e no PostgreSQL 11+). INTEGER como `is_primary` (048).
-- Compatível com SQLite e PostgreSQL. Sem BEGIN/COMMIT: o executor já abre a transação.

ALTER TABLE persona_images ADD COLUMN feita_por_ia INTEGER;
