-- Capabilities, políticas, aprovação e limites.
-- Nada aqui cria estado novo de objetivo ou de etapa: o motivo de um bloqueio vira COLUNA (`blocked_kind`), porque
-- `_counts` deriva a contagem do objetivo a partir dos status conhecidos e um status novo quebraria o recálculo.

-- `capability` é a chave de POLÍTICA e LIMITE desta etapa (ex.: SEND_MESSAGE). Não confundir com `template_key`,
-- que é identidade de RECEITA: de qual etapa-modelo esta cópia de for_each saiu.
ALTER TABLE steps ADD COLUMN capability TEXT;
ALTER TABLE steps ADD COLUMN template_key TEXT;
-- Seletor do elemento que dispara o efeito externo, declarado pela capability. Nulo = caminho antigo (vocabulário).
ALTER TABLE steps ADD COLUMN commit_selector TEXT;
-- Guardas que precisam estar na MESMA FAIXA VERTICAL do alvo (linha de lista), e não em qualquer lugar da tela.
ALTER TABLE steps ADD COLUMN band_guard TEXT;
-- Argumentos da capability (JSON): alvo e conteúdo desta etapa. É o que identifica a contraparte no histórico
-- e o que a tela de aprovação mostra para a pessoa decidir.
ALTER TABLE steps ADD COLUMN bindings TEXT;

-- Por que o objetivo parou, em campo próprio: 'approval' (espera decisão), 'limit' (represado), 'session', 'auth'…
ALTER TABLE objectives ADD COLUMN blocked_kind TEXT;
-- Tempo em que o objetivo esteve represado por limite. Desconta do prazo total: esperar não é demorar.
ALTER TABLE objectives ADD COLUMN paused_s INTEGER NOT NULL DEFAULT 0;
-- Perfil dono deste objetivo, fotografado na criação. O vínculo aparelho↔perfil pode mudar no meio da execução;
-- histórico e limites não podem migrar de dono junto.
ALTER TABLE objectives ADD COLUMN profile_id TEXT;

-- Aprovação com três verbos. `generated_content` é o que a IA escreveu; `approved_content` é o que a pessoa
-- deixou sair. Guardar os dois é o que permite auditar depois o que foi editado.
CREATE TABLE pending_approvals (
    id                TEXT PRIMARY KEY,
    profile_id        TEXT REFERENCES instagram_profiles(id) ON DELETE CASCADE,
    run_id            TEXT,
    objective_id      TEXT,
    step_id           TEXT,
    interaction_id    TEXT,
    capability        TEXT NOT NULL,
    target            TEXT,                          -- alvo da ação (contraparte, post, comentário)
    summary           TEXT NOT NULL DEFAULT '',
    generated_content TEXT,
    approved_content  TEXT,
    status            TEXT NOT NULL DEFAULT 'pending',   -- pending | approved | edited | rejected | expired
    decided_at        TEXT,
    decided_note      TEXT,
    created_at        TEXT NOT NULL
);
CREATE INDEX ix_approvals_pendentes ON pending_approvals(status, created_at);
CREATE INDEX ix_approvals_objetivo ON pending_approvals(objective_id, status);

-- Identidade da receita ganha ASSINATURA do pacote e VARIANTE de interface (idioma + faixa de densidade).
-- Sem isso a quarentena é global: um perfil com o aparelho em outro idioma colocaria em quarentena a receita que
-- funciona para todos os outros. A tabela é reconstruída porque a unicidade antiga não incluía os campos novos.
CREATE TABLE recipes_novo (
    id                {{PK_AUTO}},
    app_package       TEXT NOT NULL,
    app_version       TEXT NOT NULL,
    app_signature     TEXT NOT NULL DEFAULT '',
    variant           TEXT NOT NULL DEFAULT '',
    step_hash         TEXT NOT NULL,
    step_key          TEXT NOT NULL,
    version           INTEGER NOT NULL DEFAULT 1,
    status            TEXT NOT NULL DEFAULT 'active',
    actions           TEXT NOT NULL,
    learned_from_step TEXT,
    replay_ok         INTEGER NOT NULL DEFAULT 0,
    replay_fail       INTEGER NOT NULL DEFAULT 0,
    consecutive_fail  INTEGER NOT NULL DEFAULT 0,
    shadow_agree      INTEGER NOT NULL DEFAULT 0,
    shadow_total      INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL,
    last_used_at      TEXT,
    UNIQUE (app_package, app_version, app_signature, variant, step_hash, version)
);
INSERT INTO recipes_novo (id, app_package, app_version, step_hash, step_key, version, status, actions,
                          learned_from_step, replay_ok, replay_fail, consecutive_fail, shadow_agree, shadow_total,
                          created_at, last_used_at)
    SELECT id, app_package, app_version, step_hash, step_key, version, status, actions, learned_from_step,
           replay_ok, replay_fail, consecutive_fail, shadow_agree, shadow_total, created_at, last_used_at
      FROM recipes;
DROP TABLE recipes;
ALTER TABLE recipes_novo RENAME TO recipes;
CREATE INDEX ix_recipes_busca ON recipes(app_package, app_version, app_signature, variant, step_hash, status);
