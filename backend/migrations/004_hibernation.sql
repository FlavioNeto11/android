-- Hibernação por snapshot (rodízio de instâncias). O snapshot é de USO ÚNICO: `snapshot_valid` é zerado ANTES de
-- todo spawn, porque carregar um snapshot antigo reverteria o disco (logins, mensagens) para aquele instante.
ALTER TABLE instances ADD COLUMN snapshot_valid INTEGER NOT NULL DEFAULT 0;
ALTER TABLE instances ADD COLUMN snapshot_hw TEXT;          -- assinatura do hardware/imagem com que o snapshot foi salvo
ALTER TABLE instances ADD COLUMN hibernated_at TEXT;
