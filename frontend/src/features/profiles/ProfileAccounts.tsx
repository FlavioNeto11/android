/**
 * Persona com contas em vários apps (itens 12.1/12.2). A persona é a PESSOA (identidade, voz, aparelho, memória);
 * cada app — Instagram, Outlook, TikTok, Facebook… — é uma conta dela, com sessão e senha próprias.
 *
 * - `useContas`: as contas da persona, lidas uma vez pelo shell e repartidas entre Visão geral, Contas e acesso e a
 *   fileira de apps.
 * - `AppSwitcher`: o escopo por app, mostrado só nas guias que ele filtra (Memória e Interações). Escolher um app
 *   mostra o que aconteceu NAQUELE app; "Todos" mostra a identidade inteira (inclusive os fatos gerais, que valem em
 *   qualquer app).
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

/**
 * Escopo por app da persona. `null` = todos os apps (a identidade inteira). Só vale para Memória e Interações — a
 * tela só o mostra nessas duas guias — e diz isso: "Mostrando: Instagram" é o que está na tela agora, e a dica ao
 * lado nomeia as guias que ele filtra (antes era uma fileira solta acima de todas as guias, sem dizer a quê servia).
 */
export function AppSwitcher({ contas, valor, onChange, onAdicionar }: {
  contas: ProfileAccount[];
  valor: string | null;
  onChange: (appId: string | null) => void;
  onAdicionar: () => void;
}) {
  const atual = valor === null ? null : contas.find((c) => c.app_id === valor);
  const nomeAtual = atual ? atual.app_name ?? atual.app_id : null;
  return (
    <div className={styles.appSwitcher} role="group" aria-label="Filtrar por app">
      <p className={styles.appEscopo}>
        <span>Mostrando: <strong>{nomeAtual ?? 'todos os apps'}</strong></span>
        <span className={styles.muted}>Filtra só Memória e Interações; as outras guias não mudam.</span>
      </p>
      <div className={styles.appChips}>
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
    </div>
  );
}
