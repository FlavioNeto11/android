-- 128_livro_escopo_de_assunto — item 31.200, adendo v1.113 (número dado pela orquestradora em 06/10).
--
-- `learning_items.scope_subject` (TEXT NOT NULL DEFAULT ''): o ASSUNTO canônico a que o item vale (minúsculas, sem
-- acento, sem pontuação; `learning/domain/livro.assunto_canonico`). '' = qualquer assunto, como os outros eixos do
-- escopo: todo item anterior fica igual. Hoje só o fato da pesquisa de uma operação (31.190) nasce com assunto; a lição
-- com assunto só vai ao prompt de quem pede o MESMO assunto (`licoes.nivel`), nunca a todo texto do app.
--
-- O índice único parcial `ux_learning_items_vivo` (055) passa a incluir o assunto: o assunto faz parte da identidade
-- do item, como os outros eixos (o mesmo fato sobre dois assuntos são dois itens). Recriado com o mesmo nome; o veto
-- por escopo (`learning_transitions.scope_key`) não muda para o item sem assunto. Só `ADD COLUMN` e o índice.
ALTER TABLE learning_items ADD COLUMN scope_subject TEXT NOT NULL DEFAULT '';
DROP INDEX IF EXISTS ux_learning_items_vivo;
CREATE UNIQUE INDEX IF NOT EXISTS ux_learning_items_vivo ON learning_items(kind, scope_app, scope_capability,
    scope_step_hash, scope_role, scope_profile_id, scope_subject, content_hash)
    WHERE state IN ('candidate', 'validated', 'published');
