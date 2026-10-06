-- A versão do texto que a IA leu no parecer do curador (item 30.76, frente Aprendizado; da leitura do 30.73).
--
-- `learning_reviews.template_versao` é a FORMA do dossiê (`dossie-v1`) e não muda de sentido: subir a versão do dossiê
-- mudaria o hash de todo dossiê e reabriria a revisão do livro inteiro (N1 da leitura do 30.73). A versão da instrução
-- do curador (`VERSAO_DO_TEMPLATE`, hoje `curador-v2`) ia só no código. Esta coluna a guarda por parecer: o adaptador
-- que mandou o texto à IA diz qual foi.
--
-- Nula por padrão: as linhas antigas, as recusas (custo, triagem), o curador simulado e o rótulo de intenção não mandam
-- texto a uma IA. Não entra no hash nem na elegibilidade.
-- Sem BEGIN/COMMIT (o executor de migrações já abre a transação).

ALTER TABLE learning_reviews ADD COLUMN instrucao_versao TEXT;
