-- A pessoa resolve uma ocorrência `incerta` (item 28.21; achado real do 28.12 em 04/10). Número 095 reservado pela
-- orquestradora; a lacuna na numeração da main é esperada até os outros branches entrarem.
--
-- Por quê: `incerta` é fim de linha da ocorrência (a execução pode ter agido e ninguém sabe se agiu) e leva o pedido a
-- `aguardando_pessoa`. `PedidosApi.pendencias` só dava a incerta por resolvida quando uma ocorrência POSTERIOR fechava
-- `concluida` — e um pedido parado em `aguardando_pessoa` não materializa nada, então isso nunca acontecia: o `retomar`
-- recusava com 409 `pendencia_aberta` para sempre e a única saída era cancelar o pedido. A 067 deixou a heurística
-- declarada ("não há marca de resolvida"); estas colunas são a marca.
--
-- Três colunas nulas em `pedido_ocorrencias`, gravadas juntas por `POST /api/pedidos/{id}/ocorrencias/{oid}/resolver`:
-- - `resolvida_em`: o instante (UTC, ISO-8601, o relógio do serviço de pedidos) em que uma pessoa conferiu no mundo real
--   e deu a ocorrência por resolvida. É o que `pendencias` consulta: preenchida, a incerta deixa de ser pendência.
-- - `resolvida_por`: quem fez o gesto (o operador da sessão, `panel` sem sessão, como o resto da trilha).
-- - `resolvida_nota`: o que a pessoa conferiu (obrigatória na rota; a coluna segue nula para o legado).
--
-- A ocorrência CONTINUA `incerta`: o estado conta o que a execução fez, e conferir não o muda (o relatório e a memória
-- do pedido seguem tratando-a como incerta). Só deixa de esperar a pessoa. Nada é reexecutado.
--
-- Sem FK, sem CHECK e sem índice (a leitura de `pendencias` já filtra por `pedido_id` e `estado`, que têm o índice da
-- 067). Só `ADD COLUMN`, válido nos dois dialetos. Sem BEGIN/COMMIT (o executor de migrações já abre a transação).

ALTER TABLE pedido_ocorrencias ADD COLUMN resolvida_em TEXT;
ALTER TABLE pedido_ocorrencias ADD COLUMN resolvida_por TEXT;
ALTER TABLE pedido_ocorrencias ADD COLUMN resolvida_nota TEXT;
