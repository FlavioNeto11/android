-- Pacotes vizinhos em que a etapa pode concluir (item 31.123, Fase 31). Número 120 reservado pela orquestradora em 06/10.
--
-- Por quê: na execução r-20261006012340-d92795 (fluxo ensinado no Configurações, android-04) a busca do app é de outro
-- pacote, e o executor recusou concluir a etapa fora do app dela; a etapa estourou o orçamento de 10 chamadas de IA
-- (33 chamadas, US$ 0,331). A demonstração da pessoa já mostrava a etapa terminando nesse pacote.
--
-- - `steps.pacotes_aceitos`: lista JSON dos pacotes, além do app da etapa, em que a tela pode comprovar a conclusão.
--   O ensino a preenche com os pacotes vistos na demonstração (sem o systemui, o lançador e os apps cadastrados).
--   NULO = só o app da etapa (inclusive todas as anteriores a esta migração). Pacote desconhecido nunca comprova.
ALTER TABLE steps ADD COLUMN pacotes_aceitos TEXT;
