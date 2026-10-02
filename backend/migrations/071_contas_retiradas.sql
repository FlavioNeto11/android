-- Lápide das contas que saíram da plataforma por bloqueio confirmado (item 29.23, ADR-068).
--
-- A conta bloqueada sai de TUDO (credencial, sessão, vínculo, linha de `profile_accounts`), mas o produto precisa
-- continuar sabendo que aquele @ foi NOSSO: sem isso, uma persona nossa trataria a conta antiga como terceira e
-- poderia responder ou engajar com ela, o que é engajamento simulado (ADR-050). Aqui fica SÓ o hash do @ normalizado
-- (sha256 de "@" + handle em minúsculas e sem espaços), nunca o texto: `eh_conta_nossa(db, handle)` compara o hash do
-- que chega com estes e com os @ vivos.
--
-- Por que tabela própria e sem chave estrangeira: a lápide NÃO pode morrer com o `DELETE` do perfil nem com o da conta
-- (as duas linhas somem de propósito); `profile_id` é só texto de auditoria. Nunca é purgada: a retenção do sistema
-- apaga por lista explícita de tabelas (events, ai_calls, outbox...) e esta não está nela.
--
-- A unicidade (app_id, handle_sha256) torna a retirada idempotente (`INSERT ... ON CONFLICT DO NOTHING`).
--
-- Compatível com SQLite e PostgreSQL: só tipos comuns. Sem BEGIN/COMMIT: o executor já abre a transação.

CREATE TABLE IF NOT EXISTS contas_retiradas (
    app_id        TEXT NOT NULL,
    handle_sha256 TEXT NOT NULL,                 -- 64 hex; o @ nunca é gravado em texto
    retirada_em   TEXT NOT NULL,                 -- ISO-8601 (ordena igual nos dois dialetos)
    profile_id    TEXT,                          -- só auditoria: a persona de então (sem FK; pode já ter sido apagada)
    UNIQUE (app_id, handle_sha256)
);
CREATE INDEX IF NOT EXISTS ix_contas_retiradas_hash ON contas_retiradas(handle_sha256);
