/**
 * "Instalar app" com o app e a versão explícitos ANTES do clique. O botão antigo ("Instalar versão promovida")
 * instalava o app PADRÃO do aparelho sem dizer qual, nem que versão — e ficava ao lado de "Abrir app", o que
 * fazia parecer que dependia da escolha feita lá. Instalar e abrir são operações independentes: cada uma escolhe
 * o seu app.
 */
import { ChevronDown, PackagePlus } from 'lucide-react';
import type { AppConfig } from '../../api/types';
import { Button } from '../../components/Button';
import { Popover } from '../../components/Popover';
import ui from '../../components/ui.module.css';
import { cx } from '../../lib/format';
import { useAppStore } from '../../store/app';
import styles from './Devices.module.css';

/** O rótulo de uma linha do menu: o que será instalado, com a versão. Puro, para testar sem montar o componente. */
export function installItemLabel(app: Pick<AppConfig, 'name' | 'promoted_version_name'>): string {
  return app.promoted_version_name
    ? `Instalar ${app.name} ${app.promoted_version_name} (promovida)`
    : `${app.name}: nenhuma versão promovida`;
}

export function InstallAppMenu({ onPick, padrao, disabledReason, loading, disabled, size = 'md', label = 'Instalar app' }: {
  onPick: (appId: string) => void;
  padrao?: string | null;
  disabledReason?: string | null;
  loading?: boolean;
  disabled?: boolean;
  size?: 'sm' | 'md';
  label?: string;
}) {
  const apps = useAppStore((s) => s.apps);
  if (disabledReason || loading || disabled || apps.length === 0) {
    return (
      <Button size={size} icon={PackagePlus} loading={loading} disabled={disabled}
              disabledReason={disabledReason ?? (apps.length === 0 ? 'Nenhum aplicativo cadastrado.' : null)}>
        {label}
      </Button>
    );
  }
  const ordenados = [...apps].sort((a, b) => (a.id === padrao ? -1 : b.id === padrao ? 1 : a.name.localeCompare(b.name)));
  return (
    <Popover label={`${label}: escolher o aplicativo e a versão`} title="Instalar qual aplicativo?"
             triggerClassName={cx(ui.btn, size === 'sm' && ui.btnSm)}
             trigger={<><PackagePlus size={size === 'sm' ? 13 : 15} aria-hidden /> {label} <ChevronDown size={12} aria-hidden /></>}>
      {(fechar) => (
        <>
          <p className={styles.appMenuNote}>
            Instala a versão promovida pelo catálogo do painel, via ADB — não usa a Play Store do aparelho e não abre
            o app. Só vira “instalado” depois de o aparelho confirmar a versão.
          </p>
          <ul className={styles.appMenu}>
            {ordenados.map((a) => (
              <li key={a.id}>
                <button type="button" className={styles.appMenuItem} disabled={!a.promoted_version_name}
                        title={a.promoted_version_name ? `${a.package} · versão ${a.promoted_version_name}`
                          + (a.promoted_version_code != null ? ` (${a.promoted_version_code})` : '')
                          : 'Importe o APK em Aplicativos, prove num aparelho e promova-o.'}
                        onClick={() => {
                          fechar();
                          onPick(a.id);
                        }}>
                  <span>{installItemLabel(a)}</span>
                  {a.id === padrao ? <span className={styles.appMenuHint}>padrão do aparelho</span> : null}
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
    </Popover>
  );
}
