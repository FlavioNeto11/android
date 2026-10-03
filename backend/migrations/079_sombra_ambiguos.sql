-- Quantas etapas da cadeia de resolução da intenção terminaram em AMBIGUOUS (`StageOutcome.AMBIGUOUS`) na execução da
-- linha da sombra (Fase 31, RA-2 da reavaliação de 03/10; `docs/design/jev-golden-set.md`).
--
-- A métrica principal do 31.10 passou a ser "execuções sem fluxo que o Jev teria casado ao fluxo que o desfecho confirma",
-- e o relatório conta, ao lado, as execuções em que o casador determinístico ficou ambíguo: é ali, e só ali, que a R3
-- (desempate) tem o que medir. A contagem vem da trilha da RESOLVE (`IntentResolution.trace`), gravada pela sombra da
-- intenção na mesma hora em que casa a decisão real.
--
-- NULO = a linha não é da intenção, ou é anterior a esta migração. Só número: nada do comando nem do catálogo.
-- Sem BEGIN/COMMIT (o executor de migrações já abre a transação). A 077 (Jev, 28.10) e a 078 (Android, 12.5) estão
-- reservadas em outros branches; a lacuna na numeração é esperada, e o migrador aplica em ordem o que falta.

ALTER TABLE decisao_fechada_sombra ADD COLUMN ambiguos INTEGER;

-- Por que o chamador recusou o pedido por privacidade (reverificação B do 31.9, 03/10): `c7_*` quando o comando é C7
-- (`intencao.motivo_c7`), ou o motivo do filtro da C3 (`entidades.remover_entidades_com_motivo`), em vocabulário fechado
-- (`contrato.MOTIVOS_DE_PRIVACIDADE`; fora dele, `outro`). Só nas linhas com `fallback_reason = 'privacidade'`; NULO no
-- resto e no legado. Só o código: nada do comando. Estava reservada na 080 (RA-10); veio para cá porque o 31.9 a grava e
-- entra antes na suíte 7.
ALTER TABLE decisao_fechada_sombra ADD COLUMN motivo_privacidade TEXT;
