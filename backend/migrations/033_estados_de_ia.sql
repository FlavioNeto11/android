-- Item 7.3 do plano-100 (fase 7 — hub de IA). Achados #93, #68, #101.
--
-- 1) `ai_calls` passa a gravar POR QUE uma chamada falhou, não só que falhou.
--
-- Até aqui uma chamada com erro entrava como `Usage(calls=1, role=role, model='(erro)')`: nem o tipo do erro
-- (crédito, recusa, limite de taxa, 5xx…) nem o modelo que de fato foi chamado ficavam gravados, e o
-- pseudo-modelo '(erro)' entrava indevidamente na lista de "modelo sem preço" do relatório de custo — um total
-- "parcial" que não era verdade, porque chamada com erro não tem custo a calcular. `error_kind` espelha
-- `AIError.kind` (not_configured | refusal | budget | billing | invalid_output | error); `error_status` é o
-- status HTTP do provedor quando houve um; `error_message` é o texto (truncado) para quem for depurar sem casar
-- horário de log. O `model` da linha de erro passa a ser o modelo REALMENTE pedido, nunca mais '(erro)'.
--
-- 2) `objectives` ganha `wait_reason`: motivo de espera ESTRUTURADO (device_slot | profile_limit | ai_capacity |
-- model_response | human), separado de `status_detail` (o texto livre, que continua existindo). Sem isto, a
-- interface não distinguia "aguardando aparelho" de "aguardando vaga de IA" de "aguardando o modelo responder" —
-- só um texto em português que qualquer ajuste de redação no backend quebrava em silêncio (regex em
-- `slotWaitDetail`, frontend/src/lib/status.ts).
--
-- Compatível com SQLite e PostgreSQL: só ALTER TABLE ADD COLUMN, sem BEGIN/COMMIT — o executor de migrações já
-- abre a transação.

ALTER TABLE ai_calls ADD COLUMN error_kind TEXT;       -- NULL nas chamadas ok=1 e nas anteriores a esta migração
ALTER TABLE ai_calls ADD COLUMN error_status INTEGER;  -- status HTTP do provedor, quando houve um
ALTER TABLE ai_calls ADD COLUMN error_message TEXT;    -- texto do AIError, truncado

ALTER TABLE objectives ADD COLUMN wait_reason TEXT;    -- device_slot | profile_limit | ai_capacity | model_response | human
