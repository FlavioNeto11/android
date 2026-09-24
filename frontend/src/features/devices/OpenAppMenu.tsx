/**
 * "Abrir app" com escolha do app (item 12.2). O botão antigo abria sempre o app PADRÃO do aparelho — e não mandava
 * `app_id` nenhum. Com o perfil tendo contas em vários apps no mesmo aparelho (Instagram, Outlook, TikTok…), abrir
 * "o app" deixou de significar alguma coisa: aqui a pessoa escolhe qual, e o padrão do aparelho vem marcado.
 */
import { AppWindow, ChevronDown } from 'lucide-react';
import { Button } from '../../components/Button';
import { Popover } from '../../components/Popover';
import ui from '../../components/ui.module.css';
import { cx } from '../../lib/format';
import { useAppStore } from '../../store/app';
import styles from './Devices.module.css';

export function OpenAppMenu({ onPick, padrao, disabledReason, loading, disabled, size = 'md', label = 'Abrir app' }: {
  onPick: (appId: string) => void;
  /** App padrão do aparelho (o que o botão antigo abria), marcado na lista. */
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
      <Button size={size} icon={AppWindow} loading={loading} disabled={disabled}
              disabledReason={disabledReason ?? (apps.length === 0 ? 'Nenhum aplicativo cadastrado.' : null)}>
        {label}
      </Button>
    );
  }
  const ordenados = [...apps].sort((a, b) => (a.id === padrao ? -1 : b.id === padrao ? 1 : a.name.localeCompare(b.name)));
  return (
    <Popover label={`${label}: escolher o aplicativo`} title="Abrir qual aplicativo?"
             triggerClassName={cx(ui.btn, size === 'sm' && ui.btnSm)}
             trigger={<><AppWindow size={size === 'sm' ? 13 : 15} aria-hidden /> {label} <ChevronDown size={12} aria-hidden /></>}>
      {(fechar) => (
        <ul className={styles.appMenu}>
          {ordenados.map((a) => (
            <li key={a.id}>
              <button type="button" className={styles.appMenuItem}
                      onClick={() => {
                        fechar();
                        onPick(a.id);
                      }}>
                <span>{a.name}</span>
                {a.id === padrao ? <span className={styles.appMenuHint}>padrão do aparelho</span> : null}
              </button>
            </li>
          ))}
        </ul>
      )}
    </Popover>
  );
}
