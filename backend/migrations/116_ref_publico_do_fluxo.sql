-- 30.83: a referência pública do fluxo. Até aqui, o id do fluxo era o slug do `plan.summary` LITERAL, que pode trazer
-- nome de pessoa ou @ (achado S1 da leitura do 30.80 B, herdado do 30.21), e esse id saía em evento, `href` e log.
--
-- `ref_publico` é ALEATÓRIA (`f-` mais 12 hex de `secrets`), nunca derivada do conteúdo: um hash do nome deixaria
-- quem conhece o nome confirmar o palpite. O fluxo novo nasce com `id = ref_publico`. O fluxo que já existe mantém o
-- id (as referências a ele não têm ON UPDATE CASCADE) e ganha a referência na subida (`flows.preencher_refs_publicas`):
-- o SQL portátil não sorteia do mesmo jeito em SQLite e PostgreSQL, então a migração só cria a coluna e o índice.
--
-- Número 116 reservado pela orquestradora em 05/10.
ALTER TABLE flows ADD COLUMN ref_publico TEXT;
CREATE UNIQUE INDEX ux_flows_ref_publico ON flows(ref_publico);
