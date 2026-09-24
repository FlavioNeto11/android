import { OpenAppMenu } from './OpenAppMenu';
import { CheckCheck, ServerCrash, Smartphone, X } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '../../api/client';
import type { Instance, InstagramProfile } from '../../api/types';
import { Button } from '../../components/Button';
import { EmptyState } from '../../components/EmptyState';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { plural } from '../../lib/format';
import { selectInstanceList, selectTaskOrder, useAppStore } from '../../store/app';
import { reconnectNow } from '../../store/live';
import { useUiStore } from '../../store/ui';
import { ACTION_META, runBulkAction, useBusyStore } from './actions';
import { DeviceCard } from './DeviceCard';
import {
  QUICK_VERBS, STATE_SUMMARY_LABEL, bulkActionsFor, bulkBlockersFor, countByServer, countByState, type BulkContext,
} from './deviceState';
import styles from './Devices.module.css';

export function DeviceGrid() {
  const hydrated = useAppStore((s) => s.hydrated);
  const connStatus = useAppStore((s) => s.conn.status);
  const connError = useAppStore((s) => s.conn.lastError);
  const instancesMap = useAppStore((s) => s.instances);
  const order = useAppStore((s) => s.instanceOrder);
  const apps = useAppStore((s) => s.apps);
  const workersMap = useAppStore((s) => s.workers);
  const selectedIds = useUiStore((s) => s.selectedIds);
  const focusId = useUiStore((s) => s.focusInstanceId);
  const toggleSelected = useUiStore((s) => s.toggleSelected);
  const selectRange = useUiStore((s) => s.selectRange);
  const setSelection = useUiStore((s) => s.setSelection);
  const clearSelection = useUiStore((s) => s.clearSelection);
  const openFocus = useUiStore((s) => s.openFocus);

  const instances = useMemo(() => selectInstanceList({ instances: instancesMap, instanceOrder: order }), [instancesMap, order]);
  const hydrateCount = useAppStore((s) => s.hydrateCount);
  // Perfis não vêm no snapshot nem em eventos: são poucos e mudam devagar, então basta recarregar a cada snapshot.
  const [profiles, setProfiles] = useState<InstagramProfile[]>([]);
  useEffect(() => {
    let vivo = true;
    void api.listProfiles().then((p) => vivo && setProfiles(p)).catch(() => undefined);
    return () => {
      vivo = false;
    };
  }, [hydrateCount]);
  const porAparelho = useMemo(
    () => new Map(profiles.filter((p) => p.instance_id).map((p) => [p.instance_id as string, p])),
    [profiles]);
  const appNames = useMemo(() => new Map(apps.map((a) => [a.id, a.name])), [apps]);
  const selectedSet = useMemo(() => new Set(selectedIds), [selectedIds]);
  const stateCounts = useMemo(() => countByState(instances), [instances]);
  const hibernation = useAppStore((s) => s.health?.features?.hibernation === true);

  // Seleção é escolha de ALVO de comando: a loja aparece na grade, mas nunca é alvo.
  const taskOrder = useMemo(() => selectTaskOrder({ instances: instancesMap, instanceOrder: order }), [instancesMap, order]);
  const onRange = useCallback((id: string) => selectRange(id, taskOrder), [selectRange, taskOrder]);
  // 11.5: seleção rápida por servidor, ao lado da seleção por estado — só aparece quando há mais de um servidor
  // em jogo (o caso comum é tudo local, e um botão único ali seria ruído).
  const serverBuckets = useMemo(
    () => countByServer(taskOrder.map((id) => ({ id, worker_id: instancesMap[id]?.worker_id ?? null })), workersMap),
    [taskOrder, instancesMap, workersMap]);

  const total = taskOrder.length;
  const allSelected = total > 0 && selectedIds.length === total;

  return (
    <section className={styles.section} aria-labelledby="devices-title">
      <div className={styles.sectionHeader}>
        <h2 id="devices-title" className={styles.sectionTitle}>Aparelhos</h2>
        {hydrated && total > 0 ? (
          <span className={styles.stateSummary} aria-label="Aparelhos por estado">
            {/* 11.5: cada contador seleciona os aparelhos daquele estado (ex.: "3 parados" → liga os três de uma vez),
                em vez de marcar cartão por cartão. A loja nunca entra: seleção é alvo de comando. */}
            {stateCounts.map(({ state, count }, i) => {
              const alvos = taskOrder.filter((id) => instancesMap[id]?.state === state);
              const rotulo = STATE_SUMMARY_LABEL[state][count === 1 ? 0 : 1];
              return (
                <span key={state}>
                  {i > 0 ? ' · ' : ''}
                  {alvos.length > 0 ? (
                    <button type="button" className={styles.stateQuick} onClick={() => setSelection(alvos)}
                            title={`Selecionar ${alvos.length} aparelho(s): ${rotulo}`}>
                      <b>{count}</b> {rotulo}
                    </button>
                  ) : <><b>{count}</b> {rotulo}</>}
                </span>
              );
            })}
          </span>
        ) : null}
        {hydrated && serverBuckets.length > 1 ? (
          <span className={styles.stateSummary} aria-label="Aparelhos por servidor">
            {serverBuckets.map((b, i) => (
              <span key={b.id ?? 'aqui'}>
                {i > 0 ? ' · ' : ''}
                <button type="button" className={styles.stateQuick} onClick={() => setSelection(b.ids)}
                        title={`Selecionar ${b.ids.length} aparelho(s) em ${b.name}`}>
                  <b>{b.ids.length}</b> {b.name}
                </button>
              </span>
            ))}
          </span>
        ) : null}
        <span className={styles.sectionHint}>
          <kbd>Ctrl</kbd> + clique alterna · <kbd>Shift</kbd> + clique seleciona um intervalo
        </span>
        <div className={styles.sectionActions}>
          <span className={styles.selSummary} aria-live="polite">
            {hydrated ? `${selectedIds.length} de ${total} selecionadas` : ''}
          </span>
          <Button size="sm" variant="ghost" icon={CheckCheck} disabled={!hydrated || allSelected} onClick={() => setSelection(taskOrder)}>
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
              profile={porAparelho.get(inst.id) ?? null}
              selected={selectedSet.has(inst.id)}
              focused={focusId === inst.id}
              onToggle={toggleSelected}
              onRange={onRange}
              onOpen={openFocus}
            />
          ))}
        </div>
      )}

      {selectedIds.length > 0 && hydrated ? (
        <BulkBar
          ids={selectedIds}
          hasAbsent={instances.some((i) => selectedSet.has(i.id) && i.state === 'absent')}
          hasHibernated={instances.some((i) => selectedSet.has(i.id) && i.state === 'hibernated')}
          hibernation={hibernation}
          // Numa seleção mista, o verbo só é oferecido se TODOS aceitarem: era assim que `create` chegava a um
          // aparelho de outra máquina e criava um AVD que nunca seria usado.
          selected={instances.filter((i) => selectedSet.has(i.id))}
        />
      ) : null}
    </section>
  );
}

/** `selected` aqui exige o `id`: a barra precisa DIZER quem impede o verbo, não só escondê-lo (achado #62). */
interface BulkBarProps extends Omit<BulkContext, 'selected'> {
  ids: string[];
  selected: readonly Pick<Instance, 'id' | 'supported_verbs'>[];
}

function BulkBar({ ids, hasAbsent, hasHibernated, hibernation, selected }: BulkBarProps) {
  const bulkBusy = useBusyStore((s) => s.bulkBusy);
  const clearSelection = useUiStore((s) => s.clearSelection);
  const actions = bulkActionsFor({ hasAbsent, hasHibernated, hibernation, selected });
  // O verbo que sumiu da barra era um mistério: reaparece desabilitado, com quem o impede. Só os verbos que
  // o foco também oferece — `create`/`wake` dependem do estado da seleção, não de capacidade.
  const bloqueados = QUICK_VERBS
    .filter((q) => !q.needsHibernation || hibernation)
    .map((q) => ({ action: q.action, quem: bulkBlockersFor(selected, q.action) }))
    .filter((b) => b.quem.length > 0);

  return (
    <div className={styles.bulkDock}>
      <div className={styles.bulk} role="toolbar" aria-label={`Ação em ${plural(ids.length, 'instância', 'instâncias')}`}>
        <span className={styles.bulkLabel}>
          <Smartphone size={15} aria-hidden />
          Ação em {plural(ids.length, 'instância', 'instâncias')}
        </span>
        {actions.map((a) => a === 'open_app' ? (
          <OpenAppMenu key={a} size="sm" loading={bulkBusy === a} disabled={bulkBusy !== null && bulkBusy !== a}
                       onPick={(appId) => void runBulkAction(ids, a, { app_id: appId })} />
        ) : (
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
        {bloqueados.map(({ action, quem }) => (
          <Button
            key={`x-${action}`}
            size="sm"
            icon={ACTION_META[action].icon}
            disabledReason={`${quem.length === 1 ? quem[0] : quem.join(', ')} não ${quem.length === 1 ? 'aceita' : 'aceitam'} `
              + `“${ACTION_META[action].label}”. Tire ${quem.length === 1 ? 'esse aparelho' : 'esses aparelhos'} da seleção para usar o verbo.`}
          >
            {ACTION_META[action].label}
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
