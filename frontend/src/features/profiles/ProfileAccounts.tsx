/**
 * Persona com contas em vários apps (itens 12.1/12.2). A persona é a PESSOA (identidade, voz, aparelho, memória);
 * cada app — Instagram, Outlook, TikTok, Facebook… — é uma conta dela, com sessão e senha próprias.
 *
 * - `useContas`: as contas da persona, lidas uma vez pelo shell e repartidas entre Visão geral, Contas e acesso e a
 *   fileira de apps.
 * - `AppSwitcher`: a fileira de apps acima das guias. Escolher um app filtra Memória e Interações para o que
 *   aconteceu NAQUELE app; "Todos" mostra a identidade inteira (inclusive os fatos gerais, que valem em qualquer app).
 *
 * A guia que lista e edita as contas é `GuiaContas.tsx` ("Contas e acesso").
 */
import { Plus } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { api } from '../../api/client';
import type { ProfileAccount } from '../../api/types';
import { Button } from '../../components/Button';
import { type LoadError, toLoadError } from '../../lib/loadError';
import { ACCOUNT_SESSION_STATUS, metaOf } from '../../lib/status';
import styles from './Profiles.module.css';

/** Contas da persona, a releitura e o erro de carga. Erro NÃO vira `[]`: a guia mostra o que houve e "Tentar de novo". */
export function useContas(profileId: string): [ProfileAccount[] | null, () => Promise<void>, LoadError | null] {
  const [contas, setContas] = useState<ProfileAccount[] | null>(null);
  const [erro, setErro] = useState<LoadError | null>(null);
  const recarregar = useCallback(async () => {
    try {
      setContas(await api.listAccounts(profileId));
      setErro(null);
    } catch (e) {
      setErro(toLoadError(e));
    }
  }, [profileId]);
  useEffect(() => {
    void recarregar();
  }, [recarregar]);
  return [contas, recarregar, erro];
}

/** Fileira de apps da persona. `null` = todos os apps (a identidade inteira). */
export function AppSwitcher({ contas, valor, onChange, onAdicionar }: {
  contas: ProfileAccount[];
  valor: string | null;
  onChange: (appId: string | null) => void;
  onAdicionar: () => void;
}) {
  return (
    <div className={styles.appSwitcher} role="group" aria-label="App da persona">
      <span className={styles.appSwitcherLabel}>Apps desta persona</span>
      <button type="button" className={styles.appChip} aria-pressed={valor === null} onClick={() => onChange(null)}>
        Todos
      </button>
      {contas.map((c) => (
        <button key={c.id} type="button" className={styles.appChip} aria-pressed={valor === c.app_id}
                title={c.handle ? `${c.app_name ?? c.app_id} · ${c.handle}` : undefined}
                onClick={() => onChange(c.app_id)}>
          <span className={styles.appChipDot} data-tone={metaOf(ACCOUNT_SESSION_STATUS, c.session?.status ?? c.session_status).tone} aria-hidden />
          {c.app_name ?? c.app_id}{c.host ? ` · ${c.host}` : ''}
        </button>
      ))}
      <Button size="sm" variant="ghost" icon={Plus} onClick={onAdicionar}>Conta em outro app</Button>
    </div>
  );
}
