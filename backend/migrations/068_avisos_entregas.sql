-- Fila durável dos avisos fora do painel (item 28.11, Fase 28; design `docs/design/pedidos-persistentes.md` §11).
--
-- O canal é o Telegram (decisão do dono, 02/10): um bot, só saída. O aviso externo é ESPELHO da caixa de Pendências
-- (ADR-062), não um conceito novo; esta tabela é a fila do espelho. Uma linha por aviso a entregar.
--
-- Por que fila e não "mandar no ato": o envio NÃO é idempotente (duas mensagens são duas mensagens) e o backend cai,
-- o Telegram demora ou responde 429. Três cercas, de fora para dentro:
--     chave   = identidade do fato (`approval:<id>`, `run:<id>`, `evento:<id>`, ...). UNIQUE: o mesmo fato visto por
--               duas réplicas, ou o mesmo evento relido, vira UMA linha (`INSERT ... ON CONFLICT DO NOTHING`);
--     líder   = só o dono da trava 'avisos' (migração 066) envia, e a reivindicação da linha acontece na transação
--               cercada pelo token dela;
--     estado  = `enviando` é gravado ANTES da chamada de rede. Um processo que cai no meio deixa a linha `enviando`;
--               ela NUNCA é reenviada sozinha: vira `incerto` (pode ter saído, pode não ter) — falha ou incerteza não
--               contam como sucesso, e duplicar um aviso é pior que perder um que o painel já mostra.
--
--     estado           pendente -> enviando -> enviado | pendente (nova tentativa, com `proximo_envio_em`)
--                                            | falhou (esgotou `tentativas`) | incerto (queda no meio do envio)
--                      descartado = `pendente` que venceu (`avisos.validade_h`): notícia velha não sai; fica o registro.
--                      Com o canal desligado ou sem segredo NADA é enfileirado (não há linha `descartado` por isso).
--     tentativas       quantas vezes a rede foi chamada; o teto é `avisos.max_tentativas`.
--     proximo_envio_em UTC; adia a próxima tentativa (backoff, `Retry-After` do 429). NULL = já.
--     ultimo_erro      texto curto e REDIGIDO (nunca o token, nunca a URL do bot).
--
-- `titulo`, `corpo` e `link` são o texto que sai: tipo do evento e o link da caixa. Nunca nome de persona, conta,
-- conteúdo de mensagem nem dado de terceiro (a montagem é do domínio, `app/modules/avisos/domain`).
--
-- O número é PROVISÓRIO (068, o próximo maior da main em 02/10); a coordenação confirma antes do merge.
--
-- Compatível com SQLite e PostgreSQL: `{{PK_AUTO}}`, só tipos comuns. Sem BEGIN/COMMIT: o executor já abre a
-- transação.

CREATE TABLE avisos_entregas (
  id               {{PK_AUTO}},
  chave            TEXT NOT NULL UNIQUE,
  tipo             TEXT NOT NULL,
  titulo           TEXT NOT NULL,
  corpo            TEXT NOT NULL DEFAULT '',
  link             TEXT,
  canal            TEXT NOT NULL DEFAULT 'telegram',
  estado           TEXT NOT NULL DEFAULT 'pendente'
                   CHECK (estado IN ('pendente', 'enviando', 'enviado', 'falhou', 'incerto', 'descartado')),
  tentativas       INTEGER NOT NULL DEFAULT 0 CHECK (tentativas >= 0),
  proximo_envio_em TEXT,
  iniciado_em      TEXT,
  enviado_em       TEXT,
  ultimo_erro      TEXT,
  criado_em        TEXT NOT NULL
);
CREATE INDEX ix_avisos_entregas_estado ON avisos_entregas(estado, proximo_envio_em);
