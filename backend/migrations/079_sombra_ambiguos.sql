-- Quantas etapas da cadeia de resolução da intenção terminaram em AMBIGUOUS (`StageOutcome.AMBIGUOUS`) na execução da
-- linha da sombra (Fase 31, RA-2 da reavaliação de 03/10; `docs/design/jev-golden-set.md`).
--
-- A métrica principal do 31.10 passou a ser "execuções sem fluxo que o Jev teria casado ao fluxo que o desfecho confirma",
-- e o relatório conta, ao lado, as execuções em que o casador determinístico ficou ambíguo: é ali, e só ali, que a R3
-- (desempate) tem o que medir. A contagem vem da trilha da RESOLVE (`IntentResolution.trace`), gravada pela sombra da
-- intenção na mesma hora em que casa a decisão real.
--
-- NULO = a linha não é da intenção, ou é anterior a esta migração. Só número: nada do comando nem do catálogo.
-- Sem BEGIN/COMMIT (o executor de migrações já abre a transação). A 077 (28.10) está reservada em outro branch; a lacuna
-- na numeração é esperada.

ALTER TABLE decisao_fechada_sombra ADD COLUMN ambiguos INTEGER;
