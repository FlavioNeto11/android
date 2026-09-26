/** Peças da loja de apps usadas pela vitrine e pela página do app. */
import { useState } from 'react';
import { releaseIconUrl } from '../../api/client';
import type { AppCategory, AppStoreEntry } from '../../api/types';
import styles from './Loja.module.css';

/** Categorias da vitrine: lista fixa decidida pelo dono em 26/09, na ordem dos filtros. */
export const CATEGORIAS: Record<AppCategory, string> = {
  social: 'Social',
  mensagens: 'Mensagens',
  email: 'E-mail',
  rede: 'Rede (VPN/proxy)',
  utilitario: 'Utilitário',
  qa: 'QA',
};

/** O ícone extraído do APK; sem ícone servível (o adaptativo em XML), a inicial do nome. */
export function IconeDoApp({ entry, grande }: { entry: Pick<AppStoreEntry, 'name' | 'icon_release_id'>; grande?: boolean }) {
  const [falhou, setFalhou] = useState(false);
  return (
    <span className={`${styles.icon} ${grande ? styles.iconBig : ''}`} aria-hidden>
      {entry.icon_release_id && !falhou
        ? <img src={releaseIconUrl(entry.icon_release_id)} alt="" onError={() => setFalhou(true)} />
        : entry.name.slice(0, 1).toUpperCase()}
    </span>
  );
}
