-- Tamanho das partes do prompt do ator (item 31.35, Fase 31). Número 097 reservado em .claude/reservas.md (04/10); as
-- 089 a 096 são de outros itens e entram antes.
--
-- Por quê: na ocorrência r-20261004090000-bbfe54 (28.12, android-09) cada decisão do ator trouxe ~2,8 mil tokens de
-- entrada nova além dos 8.607 fixos do cache, e o banco não dizia quanto disso era a árvore da tela e quanto era o
-- histórico. Sem essa divisão, podar a árvore ou o histórico seria palpite. Só mede; nenhuma coluna guarda texto.
--
-- - `prompt_arvore_chars`: caracteres das linhas da árvore que foram ao modelo (depois do corte e da poda).
-- - `prompt_historico_chars`: caracteres do histórico de ações desta tentativa que foram ao modelo.
-- - `prompt_podados`: elementos da interface do navegador tirados da árvore do prompt (poda do 31.35); 0 = nada podado.
--
-- NULO fora da decisão do ator (plano, juiz, sombra) e nas linhas anteriores a esta migração.
ALTER TABLE ai_calls ADD COLUMN prompt_arvore_chars INTEGER;
ALTER TABLE ai_calls ADD COLUMN prompt_historico_chars INTEGER;
ALTER TABLE ai_calls ADD COLUMN prompt_podados INTEGER;
