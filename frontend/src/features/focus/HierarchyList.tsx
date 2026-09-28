import { MousePointerClick, RefreshCw, Search } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, hintForError, toApiError } from '../../api/client';
import type { HierarchyElement, HierarchyResponse } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Banner } from '../../components/Banner';
import { Button } from '../../components/Button';
import { TextInput } from '../../components/Field';
import { Spinner } from '../../components/Skeleton';
import { cx } from '../../lib/format';
import { formatClock } from '../../lib/time';
import styles from './Focus.module.css';

interface HierarchyListProps {
  instanceId: string;
  online: boolean;
  /** A seção que a contém está aberta: a primeira abertura lê a hierarquia; fechar apaga o realce na tela. */
  active: boolean;
  /** Com o controle em mãos, cada elemento ganha um botão "Tocar" (alternativa acessível ao clique na imagem). */
  canTap: boolean;
  onTap: (x: number, y: number) => void;
  onHighlight: (bounds: [number, number, number, number] | null) => void;
}

const MAX_ROWS = 250;

function labelOf(el: HierarchyElement): string {
  return el.text || el.desc || el.resource_id || el.class_name || el.id;
}

function validBounds(b: unknown): b is [number, number, number, number] {
  return Array.isArray(b) && b.length === 4 && b.every((n) => typeof n === 'number' && Number.isFinite(n));
}

export function HierarchyList({ instanceId, online, active, canTap, onTap, onHighlight }: HierarchyListProps) {
  const [data, setData] = useState<HierarchyResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<{ message: string; hint: string } | null>(null);
  const [query, setQuery] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.hierarchy(instanceId);
      setData({ ts: res?.ts ?? '', elements: Array.isArray(res?.elements) ? res.elements : [] });
    } catch (e) {
      const err = toApiError(e);
      setError({ message: err.message, hint: hintForError(err) });
    } finally {
      setLoading(false);
    }
  }, [instanceId]);

  const filtered = useMemo(() => {
    const list = data?.elements ?? [];
    const q = query.trim().toLowerCase();
    if (!q) return list;
    return list.filter((el) => [el.text, el.desc, el.resource_id, el.class_name].some((v) => typeof v === 'string' && v.toLowerCase().includes(q)));
  }, [data, query]);

  // A leitura da hierarquia custa uma ida ao aparelho: só acontece na primeira vez que a seção é aberta (como o
  // antigo `onFirstOpen` do Disclosure próprio, que virou a seção "Hierarquia" do Foco). Fechar apaga o realce.
  const jaLeu = useRef(false);
  useEffect(() => {
    if (!active) {
      onHighlight(null);
      return;
    }
    if (online && !jaLeu.current) {
      jaLeu.current = true;
      void load();
    }
  }, [active, online, load, onHighlight]);

  return (
    <>
      {!online ? (
        <p className={styles.groupHint}>A hierarquia só pode ser lida com o aparelho online.</p>
      ) : (
        <>
          <div className={styles.hierTools}>
            <TextInput small value={query} placeholder="Filtrar por texto, id ou classe" aria-label="Filtrar elementos" onChange={(e) => setQuery(e.target.value)} />
            <Button size="sm" icon={RefreshCw} iconOnly label="Recarregar hierarquia" loading={loading} onClick={() => void load()} />
          </div>
          {error ? (
            <Banner tone="danger" icon={Search} compact title="Não foi possível ler a hierarquia">{error.message} {error.hint}</Banner>
          ) : loading && !data ? (
            <Spinner label="Lendo a hierarquia da tela…" />
          ) : data && filtered.length === 0 ? (
            <p className={styles.groupHint}>{data.elements.length === 0 ? 'A tela atual não expôs elementos.' : 'Nenhum elemento corresponde ao filtro.'}</p>
          ) : data ? (
            <>
              <p className={cx(styles.groupHint, styles.hierMeta)}>
                {data.elements.length} elementos · lida às {formatClock(data.ts)}
                {filtered.length > MAX_ROWS ? ` · mostrando ${MAX_ROWS} de ${filtered.length} (use o filtro)` : ''}
              </p>
              <ul className={styles.hierList} onMouseLeave={() => onHighlight(null)}>
                {filtered.slice(0, MAX_ROWS).map((el, i) => {
                  const bounds = validBounds(el.bounds) ? el.bounds : null;
                  return (
                    <li
                      key={`${el.id}-${i}`}
                      className={styles.hierItem}
                      onMouseEnter={() => onHighlight(bounds)}
                      onFocus={() => onHighlight(bounds)}
                      onBlur={() => onHighlight(null)}
                    >
                      <div className={styles.hierMain}>
                        <p className={styles.hierLabel}>{labelOf(el)}</p>
                        <p className={styles.hierSub}>
                          {el.class_name ? el.class_name.split('.').pop() : '—'}
                          {el.resource_id ? ` · ${el.resource_id}` : ''}
                          {bounds ? ` · [${bounds.join(', ')}]` : ''}
                        </p>
                        <div className={styles.hierFlags}>
                          {el.clickable ? <Badge size="sm" tone="accent">clicável</Badge> : null}
                          {el.focused ? <Badge size="sm" tone="info">em foco</Badge> : null}
                          {!el.enabled ? <Badge size="sm" tone="muted">desativado</Badge> : null}
                        </div>
                      </div>
                      {canTap && bounds ? (
                        <Button
                          size="sm"
                          variant="ghost"
                          icon={MousePointerClick}
                          iconOnly
                          label={`Tocar em “${labelOf(el)}”`}
                          onClick={() => onTap(Math.round((bounds[0] + bounds[2]) / 2), Math.round((bounds[1] + bounds[3]) / 2))}
                        />
                      ) : null}
                    </li>
                  );
                })}
              </ul>
            </>
          ) : (
            <Button size="sm" onClick={() => void load()}>Carregar hierarquia</Button>
          )}
        </>
      )}
    </>
  );
}
