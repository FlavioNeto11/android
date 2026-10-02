import type { LucideIcon } from 'lucide-react';
import { useEffect, useRef, type KeyboardEvent, type ReactNode } from 'react';
import { cx } from '../lib/format';
import ui from './ui.module.css';

export interface TabDef<T extends string> {
  id: T;
  label: string;
  icon?: LucideIcon;
  count?: number | null;
  /** Destaca o contador (ex.: bloqueios pendentes). */
  alert?: boolean;
}

interface TabsProps<T extends string> {
  tabs: readonly TabDef<T>[];
  active: T;
  onChange: (id: T) => void;
  /** Prefixo dos ids: aba = `${idBase}-tab-${id}`, painel = `${idBase}-panel-${id}`. */
  idBase: string;
  label: string;
}

/** Abas WAI-ARIA: setas ←/→, Home/End, tabindex itinerante. */
export function Tabs<T extends string>({ tabs, active, onChange, idBase, label }: TabsProps<T>) {
  const refs = useRef(new Map<string, HTMLButtonElement>());
  const lista = useRef<HTMLDivElement>(null);

  // No celular a faixa de abas rola na horizontal e a aba ativa (vinda de um link) podia nascer fora da tela. Rola só
  // a FAIXA (scrollLeft), nunca a página: `scrollIntoView` puxaria a tela inteira para a faixa.
  useEffect(() => {
    const faixa = lista.current;
    const aba = refs.current.get(active);
    if (!faixa || !aba || faixa.scrollWidth <= faixa.clientWidth) return;
    const inicio = aba.offsetLeft - faixa.offsetLeft;
    const fim = inicio + aba.offsetWidth;
    if (inicio < faixa.scrollLeft) faixa.scrollLeft = Math.max(0, inicio - 16);
    else if (fim > faixa.scrollLeft + faixa.clientWidth) faixa.scrollLeft = fim - faixa.clientWidth + 16;
  }, [active]);

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const idx = tabs.findIndex((t) => t.id === active);
    let next = idx;
    if (e.key === 'ArrowRight') next = (idx + 1) % tabs.length;
    else if (e.key === 'ArrowLeft') next = (idx - 1 + tabs.length) % tabs.length;
    else if (e.key === 'Home') next = 0;
    else if (e.key === 'End') next = tabs.length - 1;
    else return;
    e.preventDefault();
    const tab = tabs[next];
    if (!tab) return;
    onChange(tab.id);
    refs.current.get(tab.id)?.focus();
  };

  return (
    <div ref={lista} role="tablist" aria-label={label} className={ui.tabs} onKeyDown={onKeyDown}>
      {tabs.map((t) => {
        const selected = t.id === active;
        const Icon = t.icon;
        return (
          <button
            key={t.id}
            ref={(el) => {
              if (el) refs.current.set(t.id, el);
              else refs.current.delete(t.id);
            }}
            type="button"
            role="tab"
            id={`${idBase}-tab-${t.id}`}
            aria-selected={selected}
            aria-controls={`${idBase}-panel-${t.id}`}
            tabIndex={selected ? 0 : -1}
            className={ui.tab}
            onClick={() => onChange(t.id)}
          >
            {Icon ? <Icon size={14} aria-hidden /> : null}
            {t.label}
            {typeof t.count === 'number' && t.count > 0 ? (
              <span className={cx(ui.tabCount, t.alert && ui.tabCountAlert)}>{t.count}</span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}

interface TabPanelProps {
  idBase: string;
  id: string;
  className?: string;
  children: ReactNode;
}

export function TabPanel({ idBase, id, className, children }: TabPanelProps) {
  return (
    <div role="tabpanel" id={`${idBase}-panel-${id}`} aria-labelledby={`${idBase}-tab-${id}`} tabIndex={0} className={className}>
      {children}
    </div>
  );
}
