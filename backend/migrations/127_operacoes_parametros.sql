-- 127_operacoes_parametros — item 31.154, adendo v1.95 (número provisório; confirmado pela orquestradora).
--
-- `operacoes.parametros` (TEXT, objeto JSON nome → valor): os parâmetros FIXOS que a operação passa a cada execução de
-- alvo (`username`, `caption_contains`). O plano da execução os usa com estes nomes, por cima dos que o planejador
-- escolheu para o mesmo valor, para a receita ensinada casar (`taskqueue/plano_da_operacao.py`). NULO = sem parâmetros
-- fixos (inclusive toda operação anterior). Só `ADD COLUMN`.
ALTER TABLE operacoes ADD COLUMN parametros TEXT;
