-- Fase 5 do plano-100 — item 5.7 (storage de evidências e de APKs). Achados #172, #89.
--
-- `evidence.path` deixou de ser "caminho relativo ao disco de quem executou a etapa" e passou a ser CHAVE de
-- storage. A chave é a mesma nos dois back-ends e nos dois sistemas operacionais (`run/instância/arquivo.jpg`,
-- sempre com barra normal); as linhas já gravadas no Windows têm `\` e continuam abrindo, porque o
-- `DiskStorage` normaliza na leitura. Não há migração de dados aqui de propósito: mexer em 976 linhas para
-- trocar um separador seria risco sem ganho.
--
-- Duas colunas novas, e cada uma existe por um 404 diferente:
--
-- `storage` — ONDE o arquivo está. Sem ela, um parque que migra para S3 no meio do caminho não sabe dizer se a
-- linha antiga aponta para o disco ou para o bucket, e passa a errar nos dois sentidos.
--
-- `stored_by` — QUEM gravou, quando o destino é disco. É o que impede a retenção de uma réplica de apagar do
-- banco compartilhado a linha de uma evidência que está no disco da OUTRA: o arquivo continuaria lá, ocupando
-- espaço, e a prova da execução teria sumido do banco sem que ninguém tenha apagado arquivo nenhum. Com
-- `storage='s3'` a pergunta não se aplica — o arquivo é de todos, e qualquer réplica pode apagá-lo.
--
-- NULO nas duas = linha anterior a esta migração: disco, de quem estava rodando. É o comportamento de antes, e
-- com um backend só nada muda.
ALTER TABLE evidence ADD COLUMN storage TEXT;
ALTER TABLE evidence ADD COLUMN stored_by TEXT;

CREATE INDEX IF NOT EXISTS idx_evidence_onde ON evidence(storage, stored_by);
