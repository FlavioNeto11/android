import { Fragment } from 'react';
import { abrirApp } from './apps';
import styles from './Aprendizado.module.css';

/** Os apps de um fluxo que atravessa apps, ou nada no item de um app só (30.33-C). */
export function eMultiApp(apps?: readonly string[] | null): apps is readonly string[] {
  return Array.isArray(apps) && apps.length > 1;
}

/** "Correio → Social" em texto, pelos nomes na ordem do plano; `null` no item de um app só. */
export function linhaDosApps(apps?: readonly string[] | null, nomes?: readonly string[] | null): string | null {
  return eMultiApp(apps) ? apps.map((p, i) => nomes?.[i] || p).join(' → ') : null;
}

/**
 * Os apps de um fluxo que atravessa apps (30.33-C: ler no Outlook e abrir o perfil no Instagram), na ordem em que o
 * plano os usa; cada nome leva ao app. O principal (onde rodam as etapas sem app próprio) se explica no `title`: antes
 * o item aparecia só nele e parecia arquivado no app errado (validação do deploy 10).
 */
export function AppsDoItem({ apps, nomes, principal }: {
  apps?: readonly string[] | null;
  nomes?: readonly string[] | null;
  principal?: string | null;
}) {
  if (!eMultiApp(apps)) return null;
  return (
    <span className={styles.appsDoItem}>
      {apps.map((p, i) => (
        <Fragment key={p}>
          {i > 0 ? <span aria-hidden="true"> → </span> : null}
          <button type="button" className={styles.linkBtn} onClick={() => abrirApp(p)}
                  title={`Abrir este aplicativo (${p})${p === principal ? ': o principal, onde rodam as etapas sem app próprio' : ''}`}>
            {nomes?.[i] || p}
          </button>
        </Fragment>
      ))}
    </span>
  );
}
