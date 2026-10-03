-- Origem do valor lido entre etapas (item 12.5, ADR-070; `.claude/handoffs/decisao-12-5.md` §2.4).
--
-- Um valor de `step_outputs` nasce da ÁRVORE do app (o texto do elemento, ADR-065 §3) ou, na tela cega declarada pelo
-- app, de LEITURA VISUAL conferida às cegas por um segundo leitor (ADR-070). Quem lê o relatório precisa saber qual
-- das duas, e quem consome o valor (a porta de política) precisa saber para não deixar valor visual alimentar efeito
-- externo sem uma pessoa. Quatro colunas:
--
--     origem        arvore | visual. CHECK porque o vocabulário é do contrato (como `value_kind`); o legado e todo
--                   valor lido do texto do elemento ficam `arvore`, que é o que sempre foram;
--     leitor        o modelo que transcreveu o recorte (`provedor/modelo`); só para `visual`;
--     frame_sha256  sha256 do recorte JPEG que o leitor viu (o texto do relatório cita os 8 primeiros); só `visual`;
--     evidence_id   a evidência (`evidence.id`) em que o recorte ficou guardado; só `visual`. Sem chave estrangeira,
--                   como as demais colunas de rastro: a retenção de evidências apaga a linha da evidência sem
--                   apagar o valor.
--
-- `origem` NÃO é nível de prova (`real`/`simulated`/`not_run`): é atributo de cada valor gravado.
--
-- Compatível com SQLite e PostgreSQL (`ADD COLUMN … NOT NULL DEFAULT … CHECK` vale nos dois). Sem BEGIN/COMMIT: o
-- executor de migrações já abre a transação.

ALTER TABLE step_outputs ADD COLUMN origem TEXT NOT NULL DEFAULT 'arvore' CHECK (origem IN ('arvore', 'visual'));
ALTER TABLE step_outputs ADD COLUMN leitor TEXT;
ALTER TABLE step_outputs ADD COLUMN frame_sha256 TEXT;
ALTER TABLE step_outputs ADD COLUMN evidence_id INTEGER;
