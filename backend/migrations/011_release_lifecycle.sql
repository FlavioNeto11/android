-- Ciclo de vida da release: canário, promoção, quarentena e rollback. Aditiva de propósito — `app_releases` não é
-- reconstruída, porque `status` já é lido em vários lugares e um segundo eixo é mais honesto que sobrecarregar o
-- primeiro. `status` continua respondendo "este arquivo pode ser instalado?"; `channel` responde "esta versão já
-- provou que funciona?". Sem BEGIN/COMMIT: o executor de migrações já abre a transação.

-- candidate  = importada e nunca provada em aparelho nenhum
-- canary      = em prova num aparelho só
-- promoted    = provou no canário; é a versão desejada para os demais aparelhos
-- quarantined = falhou a prova; instalação bloqueada até alguém decidir o contrário
-- rolled_back = foi substituída por uma anterior de propósito
ALTER TABLE app_releases ADD COLUMN channel TEXT NOT NULL DEFAULT 'candidate';
ALTER TABLE app_releases ADD COLUMN channel_at TEXT;
ALTER TABLE app_releases ADD COLUMN channel_detail TEXT;
ALTER TABLE app_releases ADD COLUMN canary_instance_id TEXT;

-- Cada prova observada num aparelho vira uma linha. Promoção lê daqui, nunca de um booleano solto: o que promove é
-- uma observação registrada, com aparelho e horário, e não a lembrança de quem clicou.
CREATE TABLE app_release_validations (
    id           {{PK_AUTO}},
    release_id   TEXT NOT NULL REFERENCES app_releases(id) ON DELETE CASCADE,
    instance_id  TEXT NOT NULL,
    stage        TEXT NOT NULL,             -- install | launch
    ok           INTEGER NOT NULL,
    detail       TEXT,
    observed_at  TEXT NOT NULL
);
CREATE INDEX idx_app_release_validations ON app_release_validations(release_id, instance_id, id);

-- Para onde voltar num rollback. Gravada ANTES de a instalação mexer no aparelho: se a linha fosse sobrescrita
-- primeiro, o alvo do rollback deixaria de existir exatamente quando ele passa a ser necessário.
ALTER TABLE device_app_state ADD COLUMN previous_release_id TEXT REFERENCES app_releases(id) ON DELETE SET NULL;
-- install | reinstall | upgrade | downgrade | rollback | uninstall — o que a última operação fez com o disco.
ALTER TABLE device_app_state ADD COLUMN last_operation TEXT;
-- Os splits que ESTE aparelho recebeu, quando o conjunto foi filtrado por densidade, ABI e idioma. Sem guardar a
-- escolha, a verificação seguinte cobraria os splits de outra configuração e acusaria divergência para sempre.
ALTER TABLE device_app_state ADD COLUMN expected_splits TEXT NOT NULL DEFAULT '[]';
