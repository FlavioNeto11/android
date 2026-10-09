-- 133_egresso_igfarm — egresso do device pelo IP da criação (número dado pela coordenação).
--
-- O igfarm cria a conta pelo IP residencial (via igfarm). Se o device depois abre o app por outro IP,
-- dispara checkpoint. Fazer o device sair pelo mesmo IP da criação evita o checkpoint.
--
-- Colunas ADITIVAS em contas_igfarm: nenhuma linha anterior muda de valor.
-- proxy_secret_ref/proxy_key_id são rastreio (para apagar na limpeza); a fonte do egresso é network_profiles.secret_ref.

ALTER TABLE contas_igfarm ADD COLUMN proxy_secret_ref TEXT;   -- rastreio da senha do proxy (fonte é network_profiles)
ALTER TABLE contas_igfarm ADD COLUMN proxy_key_id TEXT;       -- idem
ALTER TABLE contas_igfarm ADD COLUMN ip_criacao TEXT;         -- IPv4 público da criação (vai a params.egress_esperado)
ALTER TABLE contas_igfarm ADD COLUMN reaquecer INTEGER NOT NULL DEFAULT 0;  -- flag de observabilidade
