-- 131_conta_planejada — 31.281 (ADR-087, adendo v1.132): a conta externa pode existir só como PLANO.
--
-- `profile_accounts` ganha o ciclo de provisionamento. `handle` continua sendo o endereço CONFIRMADO e fica vazio
-- enquanto a conta é só planejada; o desejado mora em `desired_handle`. Colunas ADITIVAS: toda linha anterior entra
-- `confirmada` (DEFAULT), então nada que já funcionava muda de valor.
-- `confirmation_evidence` é JSON `{"kind": "sessao|declarada|igfarm", "ref": "..."}` (nulo nas contas anteriores).

ALTER TABLE profile_accounts ADD COLUMN provisioning_state TEXT NOT NULL DEFAULT 'confirmada';
ALTER TABLE profile_accounts ADD COLUMN desired_handle TEXT;
ALTER TABLE profile_accounts ADD COLUMN provisioning_detail TEXT;
ALTER TABLE profile_accounts ADD COLUMN resume_state TEXT;
ALTER TABLE profile_accounts ADD COLUMN confirmed_at TEXT;
ALTER TABLE profile_accounts ADD COLUMN confirmation_evidence TEXT;
