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

/** Resumo do que promover fez no parque (ADR-026: todo aparelho que tem o app persegue a promovida sozinho). */
export function resumoDaPromocao(devices: { outcome: string }[] | undefined): string | undefined {
  if (!devices || devices.length === 0) return undefined;
  const n = (o: string) => devices.filter((d) => d.outcome === o).length;
  const partes = [
    n('started') ? `${n('started')} instalando agora` : '',
    n('pending') ? `${n('pending')} quando ficarem livres ou ligarem` : '',
    n('already') ? `${n('already')} já na versão` : '',
    n('kept') + n('incompatible') ? `${n('kept') + n('incompatible')} ficam como estão (motivo na tabela)` : '',
  ].filter(Boolean);
  return `Aparelhos com o app: ${partes.join(', ')}. Nenhum aparelho é ligado por isso.`;
}

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
