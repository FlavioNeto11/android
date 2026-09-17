import { CheckCheck, ServerCrash, Smartphone, X } from 'lucide-react';
import { useCallback, useMemo } from 'react';
import type { InstanceAction } from '../../api/types';
import { Button } from '../../components/Button';
import { EmptyState } from '../../components/EmptyState';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { plural } from '../../lib/format';
import { selectInstanceList, useAppStore } from '../../store/app';
import { reconnectNow } from '../../store/live';
import { useUiStore } from '../../store/ui';
import { ACTION_META, runBulkAction, useBusyStore } from './actions';
import { DeviceCard } from './DeviceCard';
import styles from './Devices.module.css';

const BULK_ACTIONS: InstanceAction[] = ['start', 'stop', 'restart', 'install_apk', 'open_app'];

export function DeviceGrid() {
  const hydrated = useAppStore((s) => s.hydrated);
  const connStatus = useAppStore((s) => s.conn.status);
  const connError = useAppStore((s) => s.conn.lastError);
  const instancesMap = useAppStore((s) => s.instances);
  const order = useAppStore((s) => s.instanceOrder);
  const apps = useAppStore((s) => s.apps);
  const selectedIds = useUiStore((s) => s.selectedIds);
  const focusId = useUiStore((s) => s.focusInstanceId);
  const toggleSelected = useUiStore((s) => s.toggleSelected);
  const selectRange = useUiStore((s) => s.selectRange);
  const setSelection = useUiStore((s) => s.setSelection);
  const clearSelection = useUiStore((s) => s.clearSelection);
  const openFocus = useUiStore((s) => s.openFocus);

  const instances = useMemo(() => selectInstanceList({ instances: instancesMap, instanceOrder: order }), [instancesMap, order]);
  const appNames = useMemo(() => new Map(apps.map((a) => [a.id, a.name])), [apps]);
  const selectedSet = useMemo(() => new Set(selectedIds), [selectedIds]);

  const onRange = useCallback((id: string) => selectRange(id, order), [selectRange, order]);

  const total = instances.length;
  const allSelected = total > 0 && selectedIds.length === total;

  return (
    <section className={styles.section} aria-labelledby="devices-title">
      <div className={styles.sectionHeader}>
        <h2 id="devices-title" className={styles.sectionTitle}>Aparelhos</h2>
        <span className={styles.sectionHint}>
          <kbd>Ctrl</kbd> + clique alterna · <kbd>Shift</kbd> + clique seleciona um intervalo
        </span>
        <div className={styles.sectionActions}>
          <span className={styles.selSummary} aria-live="polite">
            {hydrated ? `${selectedIds.length} de ${total} selecionadas` : ''}
          </span>
          <Button size="sm" variant="ghost" icon={CheckCheck} disabled={!hydrated || allSelected} onClick={() => setSelection(order)}>
            Selecionar todas
          </Button>
          <Button size="sm" variant="ghost" icon={X} disabled={selectedIds.length === 0} onClick={clearSelection}>
            Limpar
          </Button>
        </div>
      </div>

      {!hydrated ? (
        connStatus === 'connecting' ? (
          <LoadingRegion label="Carregando aparelhos…" className={styles.grid}>
            {Array.from({ length: 10 }, (_, i) => (
              <div key={i} className={styles.card} style={{ padding: 10, gap: 10, display: 'flex', flexDirection: 'column' }}>
                <Skeleton width="55%" height={16} />
                <Skeleton height={212} radius={8} />
                <Skeleton width="80%" />
                <Skeleton width="60%" />
                <Skeleton height={6} radius={99} />
              </div>
            ))}
          </LoadingRegion>
        ) : (
          <EmptyState
            icon={ServerCrash}
            tone="danger"
            title="Sem conexão com o backend"
            hint={<>Inicie o backend (FastAPI em <span className="mono">127.0.0.1:8000</span>) e aguarde: a reconexão é automática.{connError ? ` Último erro: ${connError}` : ''}</>}
            actions={<Button variant="outline" onClick={reconnectNow}>Tentar agora</Button>}
          >
            Ainda não foi possível carregar a lista de aparelhos.
          </EmptyState>
        )
      ) : total === 0 ? (
        <EmptyState
          icon={Smartphone}
          title="Nenhuma instância cadastrada"
          hint="O backend deveria listar android-01 … android-10. Abra o Diagnóstico para conferir o SDK e a configuração."
        >
          O snapshot veio sem instâncias.
        </EmptyState>
      ) : (
        <div className={styles.grid}>
          {instances.map((inst) => (
            <DeviceCard
              key={inst.id}
              instance={inst}
              appName={inst.app_id ? appNames.get(inst.app_id) ?? inst.app_id : null}
              selected={selectedSet.has(inst.id)}
              focused={focusId === inst.id}
              onToggle={toggleSelected}
              onRange={onRange}
              onOpen={openFocus}
            />
          ))}
        </div>
      )}

      {selectedIds.length > 0 && hydrated ? <BulkBar ids={selectedIds} hasAbsent={instances.some((i) => selectedSet.has(i.id) && i.state === 'absent')} /> : null}
    </section>
  );
}

function BulkBar({ ids, hasAbsent }: { ids: string[]; hasAbsent: boolean }) {
  const bulkBusy = useBusyStore((s) => s.bulkBusy);
  const clearSelection = useUiStore((s) => s.clearSelection);
  const actions: InstanceAction[] = hasAbsent ? ['create', ...BULK_ACTIONS] : BULK_ACTIONS;

  return (
    <div className={styles.bulkDock}>
      <div className={styles.bulk} role="toolbar" aria-label={`Ação em ${plural(ids.length, 'instância', 'instâncias')}`}>
        <span className={styles.bulkLabel}>
          <Smartphone size={15} aria-hidden />
          Ação em {plural(ids.length, 'instância', 'instâncias')}
        </span>
        {actions.map((a) => (
          <Button
            key={a}
            size="sm"
            icon={ACTION_META[a].icon}
            loading={bulkBusy === a}
            disabled={bulkBusy !== null && bulkBusy !== a}
            onClick={() => void runBulkAction(ids, a)}
          >
            {ACTION_META[a].label}
          </Button>
        ))}
        <span className={styles.bulkSep} aria-hidden />
        <Button
          size="sm"
          variant="dangerGhost"
          icon={ACTION_META.reset.icon}
          loading={bulkBusy === 'reset'}
          disabled={bulkBusy !== null && bulkBusy !== 'reset'}
          onClick={() => void runBulkAction(ids, 'reset')}
        >
          Resetar dados…
        </Button>
        <Button size="sm" variant="ghost" icon={X} iconOnly label="Limpar seleção" onClick={clearSelection} />
      </div>
    </div>
  );
}
