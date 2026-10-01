import { AppWindow, Check, Eye, Save, TriangleAlert, Undo2, Wand2, X } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { api, profileAvatarUrl } from '../../api/client';
import type { Instance, InstagramProfile, InstanceUpdate } from '../../api/types';
import { Avatar } from '../../components/Avatar';
import { Badge } from '../../components/Badge';
import { Button } from '../../components/Button';
import { AutoGrid, PageSection } from '../../components/Page';
import { Popover } from '../../components/Popover';
import { Select, TextInput } from '../../components/Field';
import { StatusBadge } from '../../components/StatusBadge';
import ui from '../../components/ui.module.css';
import { cx } from '../../lib/format';
import { formatDateTime } from '../../lib/time';
import { selectTaskInstances, useAppStore } from '../../store/app';
import { seloDoAparelho } from '../devices/selos';
import { toast, toastError } from '../../store/toasts';
import {
  EMPTY_FILTERS, groupByServer, hasActiveFilter, matchesFilters, observedMatchOf, type InstanceFilters,
} from './instancesView';
import styles from './Settings.module.css';

interface Draft {
  app_id?: string | null;
  account_label?: string;
  /** `''` = central (o backend guarda `NULL`). Achado #64: sem este campo, amarrar/desamarrar só por curl. */
  worker_id?: string;
}

/**
 * Em que servidor o aparelho está, do jeito que o SELECT entende: `''` para o central. O central tem linha em
 * `workers` desde que virou um worker como outro qualquer, então tanto `NULL` quanto o id dele significam "aqui".
 */
export function workerValueOf(inst: Pick<Instance, 'worker_id'>, centralId: string | null): string {
  const w = inst.worker_id ?? '';
  return w && w === centralId ? '' : w;
}

function isDirty(inst: Instance, d: Draft | undefined, centralId: string | null = null): boolean {
  if (!d) return false;
  const appChanged = d.app_id !== undefined && d.app_id !== (inst.app_id ?? null);
  const labelChanged = d.account_label !== undefined && d.account_label.trim() !== (inst.account_label ?? '');
  const workerChanged = d.worker_id !== undefined && d.worker_id !== workerValueOf(inst, centralId);
  return appChanged || labelChanged || workerChanged;
}

/** Só os campos alterados entram no PUT. Rótulo vazio vai como "" (limpa); "sem app" vai como null. */
function toPatch(inst: Instance, d: Draft, centralId: string | null = null): InstanceUpdate {
  const patch: InstanceUpdate = {};
  if (d.app_id !== undefined && d.app_id !== (inst.app_id ?? null)) patch.app_id = d.app_id;
  if (d.account_label !== undefined && d.account_label.trim() !== (inst.account_label ?? '')) patch.account_label = d.account_label.trim();
  // `''` vira `null` no backend (api.py:830): "central" é a ausência de worker remoto.
  if (d.worker_id !== undefined && d.worker_id !== workerValueOf(inst, centralId)) patch.worker_id = d.worker_id || null;
  return patch;
}

const OBSERVED_META: Record<'match' | 'diverge' | 'none', { icon: typeof Check; tone: 'success' | 'warning' | 'neutral'; label: string }> = {
  match: { icon: Check, tone: 'success', label: 'bate' },
  diverge: { icon: TriangleAlert, tone: 'warning', label: 'diverge' },
  none: { icon: Eye, tone: 'neutral', label: 'não observada' },
};

export function InstancesSection() {
  const instancesMap = useAppStore((s) => s.instances);
  const order = useAppStore((s) => s.instanceOrder);
  const apps = useAppStore((s) => s.apps);
  const workersMap = useAppStore((s) => s.workers);
  const upsertInstance = useAppStore((s) => s.upsertInstance);
  const hydrateCount = useAppStore((s) => s.hydrateCount);
  // App e conta são coisa de aparelho de tarefa: a loja não opera app nenhum.
  const instances = useMemo(() => selectTaskInstances({ instances: instancesMap, instanceOrder: order }), [instancesMap, order]);
  const todosOsWorkers = useMemo(() => Object.values(workersMap), [workersMap]);
  const centralId = useMemo(() => todosOsWorkers.find((w) => w.local)?.id ?? null, [todosOsWorkers]);
  const workers = useMemo(
    () => todosOsWorkers.filter((w) => !w.local).sort((a, b) => a.name.localeCompare(b.name)), [todosOsWorkers]);

  // Perfis não vêm no snapshot nem em eventos: são poucos e mudam devagar, então basta recarregar a cada snapshot
  // (mesmo padrão de `DeviceGrid`/`InfraPage`).
  const [profiles, setProfiles] = useState<InstagramProfile[]>([]);
  useEffect(() => {
    let vivo = true;
    void api.listProfiles().then((p) => vivo && setProfiles(p)).catch(() => undefined);
    return () => {
      vivo = false;
    };
  }, [hydrateCount]);
  const profileByInstance = useMemo(
    () => new Map(profiles.filter((p) => p.instance_id).map((p) => [p.instance_id as string, p])),
    [profiles]);

  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [saving, setSaving] = useState<Record<string, boolean>>({});
  const [bulkApp, setBulkApp] = useState<string>('');
  const [savingAll, setSavingAll] = useState(false);
  const [filters, setFilters] = useState<InstanceFilters>(EMPTY_FILTERS);

  const dirtyIds = instances.filter((i) => isDirty(i, drafts[i.id], centralId)).map((i) => i.id);

  const patchDraft = (id: string, patch: Draft) => setDrafts((d) => ({ ...d, [id]: { ...d[id], ...patch } }));
  const clearDraft = (id: string) =>
    setDrafts((d) => {
      const { [id]: _drop, ...rest } = d;
      return rest;
    });

  const saveOne = async (inst: Instance, quiet = false): Promise<boolean> => {
    const draft = drafts[inst.id];
    if (!draft || !isDirty(inst, draft, centralId)) return true;
    setSaving((s) => ({ ...s, [inst.id]: true }));
    try {
      const updated = await api.updateInstance(inst.id, toPatch(inst, draft, centralId));
      upsertInstance(updated);
      clearDraft(inst.id);
      if (!quiet) toast({ tone: 'success', title: `${inst.id} salvo` });
      return true;
    } catch (e) {
      toastError(`Não foi possível salvar ${inst.id}`, e);
      return false;
    } finally {
      setSaving((s) => ({ ...s, [inst.id]: false }));
    }
  };

  const saveAll = async () => {
    setSavingAll(true);
    let ok = 0;
    let failed = 0;
    for (const inst of instances) {
      if (!isDirty(inst, drafts[inst.id], centralId)) continue;
      if (await saveOne(inst, true)) ok += 1;
      else failed += 1;
    }
    setSavingAll(false);
    if (ok > 0) toast({ tone: failed > 0 ? 'warning' : 'success', title: `${ok} aparelho(s) salvo(s)${failed > 0 ? `, ${failed} com erro` : ''}` });
  };

  // Grupos de TODOS os aparelhos (sem filtro): as etiquetas de servidor mostram todo mundo, não só quem sobrou.
  const allServerGroups = useMemo(() => groupByServer(instances, workersMap, centralId), [instances, workersMap, centralId]);
  const hasNoApp = useMemo(() => instances.some((i) => !i.app_id), [instances]);

  const filtered = useMemo(
    () => instances.filter((i) => matchesFilters(i, filters, centralId, (id) => profileByInstance.has(id))),
    [instances, filters, centralId, profileByInstance]);
  const visibleGroups = useMemo(() => groupByServer(filtered, workersMap, centralId), [filtered, workersMap, centralId]);

  return (
    <>
      <PageSection
        title="Aparelhos, apps e contas"
        subtitle="Associe cada aparelho a um servidor e a um aplicativo, e dê um rótulo à conta que deveria estar conectada. “Servidor” é a máquina que hospeda o aparelho: mudar para o central desamarra o aparelho do worker. O indicador ao lado da conta mostra se o que a IA viu no app bate com o rótulo — clique num cartão para editar."
        bodyClassName={styles.stack}
      >
        {/* `ui.chip` marca o filtro ativo por `aria-pressed`: estado visível, anunciado e igual ao do Comando (P3.6). */}
        <div className={styles.instanceFilters} role="group" aria-label="Filtrar aparelhos">
          <span className={styles.instanceFiltersLabel}>Servidor:</span>
          <button type="button" className={ui.chip} aria-pressed={filters.server === null} onClick={() => setFilters((f) => ({ ...f, server: null }))}>
            Todos
          </button>
          {allServerGroups.map((g) => (
            <button
              key={g.key || '(central)'}
              type="button"
              className={ui.chip} aria-pressed={filters.server === g.key}
              onClick={() => setFilters((f) => ({ ...f, server: f.server === g.key ? null : g.key }))}
            >
              {g.name} ({g.items.length})
            </button>
          ))}
          <span className={styles.instanceFiltersLabel}>App:</span>
          <button type="button" className={ui.chip} aria-pressed={filters.app === null} onClick={() => setFilters((f) => ({ ...f, app: null }))}>
            Todos
          </button>
          {apps.map((a) => (
            <button
              key={a.id}
              type="button"
              className={ui.chip} aria-pressed={filters.app === a.id}
              onClick={() => setFilters((f) => ({ ...f, app: f.app === a.id ? null : a.id }))}
            >
              {a.name}
            </button>
          ))}
          {hasNoApp ? (
            <button type="button" className={ui.chip} aria-pressed={filters.app === ''} onClick={() => setFilters((f) => ({ ...f, app: f.app === '' ? null : '' }))}>
              Sem app
            </button>
          ) : null}
          <button
            type="button"
            className={ui.chip} aria-pressed={filters.onlyDivergent}
            onClick={() => setFilters((f) => ({ ...f, onlyDivergent: !f.onlyDivergent }))}
          >
            <TriangleAlert size={12} aria-hidden /> Só divergências
          </button>
          <button
            type="button"
            className={ui.chip} aria-pressed={filters.noProfile}
            onClick={() => setFilters((f) => ({ ...f, noProfile: !f.noProfile }))}
          >
            Sem perfil
          </button>
          {hasActiveFilter(filters) ? (
            <Button size="sm" variant="ghost" icon={X} onClick={() => setFilters(EMPTY_FILTERS)}>Limpar filtros</Button>
          ) : null}
        </div>
  
        <div className={styles.bulkRow}>
          <label htmlFor="bulk-app" className={styles.bulkLabel}>Aplicar app a todas:</label>
          <Select id="bulk-app" small className={styles.bulkSelect} value={bulkApp} onChange={(e) => setBulkApp(e.target.value)}>
            <option value="">Escolha um aplicativo…</option>
            {apps.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </Select>
          <Button
            size="sm"
            icon={Wand2}
            disabledReason={bulkApp ? null : 'Escolha o aplicativo primeiro.'}
            onClick={() => {
              setDrafts((d) => {
                const next = { ...d };
                for (const inst of instances) next[inst.id] = { ...next[inst.id], app_id: bulkApp };
                return next;
              });
              toast({ tone: 'info', title: 'App aplicado às linhas — ainda não salvo', message: 'Revise e clique em “Salvar alterações”.' });
            }}
          >
            Aplicar app a todas
          </Button>
          <span className={styles.bulkRowSpacer} />
          <Button size="sm" variant="ghost" icon={Undo2} disabled={dirtyIds.length === 0 || savingAll} onClick={() => setDrafts({})}>Descartar</Button>
          <Button size="sm" variant="primary" icon={Save} loading={savingAll} disabledReason={dirtyIds.length > 0 ? null : 'Nenhuma alteração pendente.'} onClick={() => void saveAll()}>
            Salvar alterações{dirtyIds.length > 0 ? ` (${dirtyIds.length})` : ''}
          </Button>
        </div>
  
        {visibleGroups.length === 0 ? (
          <p className={styles.fieldsetHint}>Nenhum aparelho corresponde aos filtros.</p>
        ) : null}
      </PageSection>

      {/* Um cartão por servidor: o nome da máquina vira o cabeçalho do cartão, e não uma linha solta no meio da grade. */}
      {visibleGroups.map((group) => (
        <PageSection
          key={group.key || '(central)'}
          level={3}
          title={group.name}
          subtitle={`${group.items.length} aparelho(s)`}
          actions={!group.enrolled ? <Badge tone="danger" size="sm">servidor não inscrito</Badge> : undefined}
        >
          <AutoGrid min="240px" className={styles.instanceGrid}>
            {group.items.map((inst) => {
              const d = drafts[inst.id];
              const dirty = isDirty(inst, d, centralId);
              const appValue = d?.app_id !== undefined ? d.app_id ?? '' : inst.app_id ?? '';
              const labelValue = d?.account_label !== undefined ? d.account_label : inst.account_label ?? '';
              const workerValue = d?.worker_id !== undefined ? d.worker_id : workerValueOf(inst, centralId);
              const unknownWorker = workerValue && !workers.some((w) => w.id === workerValue);
              const unknownApp = appValue && !apps.some((a) => a.id === appValue);
              const appName = apps.find((a) => a.id === (inst.app_id ?? ''))?.name ?? null;
              const observed = observedMatchOf(inst);
              const om = OBSERVED_META[observed];
              const profile = profileByInstance.get(inst.id);
              return (
                <Popover
                  key={inst.id}
                  align="start"
                  label={`Editar ${inst.id}`}
                  title={`Editar ${inst.id}`}
                  triggerClassName={cx(styles.instanceCard, dirty && styles.instanceCardDirty)}
                  trigger={
                    <>
                      <div className={styles.instanceCardTop}>
                        <span className={styles.instanceCardId}>{inst.id}</span>
                        {/* RF-40: servidor sem resposta = "Desconhecido", a regra das outras telas. */}
                        <StatusBadge meta={seloDoAparelho(inst, workersMap)} size="sm" />
                      </div>
                      <div className={styles.instanceCardApp}>
                        <AppWindow size={13} aria-hidden className={styles.instanceCardAppIcon} />
                        <span>{appName ?? 'Sem app associado'}</span>
                      </div>
                      <div className={styles.instanceCardLabel}>
                        {inst.account_label || <span className={styles.instanceCardNoLabel}>sem rótulo</span>}
                      </div>
                      <div className={styles.instanceCardObserved} data-tone={om.tone} title={inst.account_evidence ?? undefined}>
                        <om.icon size={12} aria-hidden />
                        <span className={styles.instanceCardObservedText}>
                          {om.label}
                          {inst.account_evidence ? ` · ${inst.account_evidence}` : ''}
                        </span>
                      </div>
                      <div className={styles.instanceCardProfile}>
                        {profile ? (
                          <>
                            <Avatar src={profileAvatarUrl(profile.id)} name={profile.display_name ?? profile.username} size={18} />
                            <span>@{profile.username}</span>
                          </>
                        ) : (
                          <span className={styles.instanceCardNoProfile}>sem perfil vinculado</span>
                        )}
                      </div>
                    </>
                  }
                >
                  {(close) => (
                    <div className={styles.instanceEditPanel}>
                      <div className={styles.instanceEditField}>
                        <label htmlFor={`srv-${inst.id}`}>Servidor</label>
                        <Select id={`srv-${inst.id}`} small value={workerValue} onChange={(e) => patchDraft(inst.id, { worker_id: e.target.value })}>
                          <option value="">Este servidor (central)</option>
                          {unknownWorker ? <option value={workerValue}>{workerValue} (não inscrito)</option> : null}
                          {workers.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
                        </Select>
                      </div>
                      <div className={styles.instanceEditField}>
                        <label htmlFor={`app-${inst.id}`}>Aplicativo</label>
                        <Select id={`app-${inst.id}`} small value={appValue} onChange={(e) => patchDraft(inst.id, { app_id: e.target.value || null })}>
                          <option value="">Sem app associado</option>
                          {unknownApp ? <option value={appValue}>{appValue} (não cadastrado)</option> : null}
                          {apps.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
                        </Select>
                      </div>
                      <div className={styles.instanceEditField}>
                        <label htmlFor={`lbl-${inst.id}`}>Rótulo da conta</label>
                        <TextInput
                          id={`lbl-${inst.id}`}
                          small
                          value={labelValue}
                          placeholder="ex.: qa-user-01"
                          maxLength={80}
                          onChange={(e) => patchDraft(inst.id, { account_label: e.target.value })}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter') void saveOne(inst).then((ok) => ok && close());
                          }}
                        />
                      </div>
                      {inst.account_evidence ? (
                        <p className={styles.fieldsetHint}>
                          Observado: {inst.account_evidence} · {formatDateTime(inst.account_evidence_ts)}
                        </p>
                      ) : null}
                      <div className={styles.instanceEditActions}>
                        <Button size="sm" variant="ghost" onClick={close}>Fechar</Button>
                        <Button
                          size="sm"
                          variant="primary"
                          icon={Save}
                          loading={!!saving[inst.id]}
                          disabled={!dirty}
                          onClick={() => void saveOne(inst).then((ok) => ok && close())}
                        >
                          Salvar
                        </Button>
                      </div>
                    </div>
                  )}
                </Popover>
              );
            })}
          </AutoGrid>
        </PageSection>
      ))}
    </>
  );
}
