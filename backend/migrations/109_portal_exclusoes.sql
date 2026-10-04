-- Registro das exclusões a pedido do titular (item 29.83, ADR-075): uma linha por "Apagar definitivamente" no painel.
--
-- É a prova do atendimento e por isso NÃO tem prazo de retenção (decisão da orquestradora, 04/10). Pela mesma razão
-- não guarda NADA do titular: nem nome, nem telefone, nem hash do telefone, nem a mensagem. Guardar uma identidade
-- de quem pediu seria guardar o dado que ele mandou apagar. "Quem pediu" fica só como o canal do pedido.
--
--     executado_por       o operador da sessão do painel (`request.state.operador`), nunca o corpo da requisição.
--     pedido_por          por onde o pedido chegou: 'formulario' | 'telefone' | 'outro'. Sem texto livre: texto
--                         livre vira lugar de dado pessoal.
--     ids                 JSON com os ids numéricos de `portal_contatos` apagados nesta exclusão. Depois do DELETE
--                         eles não apontam para mais nada.
--     mantidos            JSON `[{id, motivo}]` dos escolhidos que NÃO foram apagados (em_envio, falhou,
--                         canal_sem_exclusao); só id e motivo.
--     mensagens_apagadas  quantas mensagens do bot o canal apagou no chat.
--     mensagens_a_mao     quantas ficaram para o dono apagar à mão (mais de 48 h, canal desligado, recusa).
--
-- Compatível com SQLite e PostgreSQL: `{{PK_AUTO}}`, só tipos comuns. Sem BEGIN/COMMIT: o executor já abre a
-- transação.
CREATE TABLE portal_exclusoes (
  id                 {{PK_AUTO}},
  executado_em       TEXT NOT NULL,
  executado_por      TEXT NOT NULL,
  pedido_por         TEXT NOT NULL CHECK (pedido_por IN ('formulario', 'telefone', 'outro')),
  ids                TEXT NOT NULL DEFAULT '[]',
  mantidos           TEXT NOT NULL DEFAULT '[]',
  mensagens_apagadas INTEGER NOT NULL DEFAULT 0,
  mensagens_a_mao    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX ix_portal_exclusoes_data ON portal_exclusoes(executado_em);
