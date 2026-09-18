import { Eye, Save, Undo2, Wand2 } from 'lucide-react';
import { useMemo, useState } from 'react';
import { api } from '../../api/client';
import type { Instance, InstanceUpdate } from '../../api/types';
import { Button } from '../../components/Button';
import { Select, TextInput } from '../../components/Field';
import { StatusBadge } from '../../components/StatusBadge';
import { cx } from '../../lib/format';
import { INSTANCE_STATE, metaOf } from '../../lib/status';
import { formatDateTime } from '../../lib/time';
import { selectTaskInstances, useAppStore } from '../../store/app';
import { toast, toastError } from '../../store/toasts';
import styles from './Settings.module.css';

interface Draft {
  app_id?: string | null;
  account_label?: string;
}

function isDirty(inst: Instance, d: Draft | undefined): boolean {
  if (!d) return false;
  const appChanged = d.app_id !== undefined && d.app_id !== (inst.app_id ?? null);
  const labelChanged = d.account_label !== undefined && d.account_label.trim() !== (inst.account_label ?? '');
  return appChanged || labelChanged;
}

/** Só os campos alterados entram no PUT. Rótulo vazio vai como "" (limpa); "sem app" vai como null. */
function toPatch(inst: Instance, d: Draft): InstanceUpdate {
  const patch: InstanceUpdate = {};
  if (d.app_id !== undefined && d.app_id !== (inst.app_id ?? null)) patch.app_id = d.app_id;
  if (d.account_label !== undefined && d.account_label.trim() !== (inst.account_label ?? '')) patch.account_label = d.account_label.trim();
  return patch;
}

export function InstancesSection() {
  const instancesMap = useAppStore((s) => s.instances);
  const order = useAppStore((s) => s.instanceOrder);
  const apps = useAppStore((s) => s.apps);
  const upsertInstance = useAppStore((s) => s.upsertInstance);
  // App e conta são coisa de aparelho de tarefa: a loja não opera app nenhum.
  const instances = useMemo(() => selectTaskInstances({ instances: instancesMap, instanceOrder: order }), [instancesMap, order]);

  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [saving, setSaving] = useState<Record<string, boolean>>({});
  const [bulkApp, setBulkApp] = useState<string>('');
  const [savingAll, setSavingAll] = useState(false);

  const dirtyIds = instances.filter((i) => isDirty(i, drafts[i.id])).map((i) => i.id);

  const patchDraft = (id: string, patch: Draft) => setDrafts((d) => ({ ...d, [id]: { ...d[id], ...patch } }));
  const clearDraft = (id: string) =>
    setDrafts((d) => {
      const { [id]: _drop, ...rest } = d;
      return rest;
    });

  const saveOne = async (inst: Instance, quiet = false): Promise<boolean> => {
    const draft = drafts[inst.id];
    if (!draft || !isDirty(inst, draft)) return true;
    setSaving((s) => ({ ...s, [inst.id]: true }));
    try {
      const updated = await api.updateInstance(inst.id, toPatch(inst, draft));
      upsertInstance(updated);
      clearDraft(inst.id);
      if (!quiet) toast({ tone: 'success', title: `${inst.id} salva` });
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
      if (!isDirty(inst, drafts[inst.id])) continue;
      if (await saveOne(inst, true)) ok += 1;
      else failed += 1;
    }
    setSavingAll(false);
    if (ok > 0) toast({ tone: failed > 0 ? 'warning' : 'success', title: `${ok} instância(s) salva(s)${failed > 0 ? `, ${failed} com erro` : ''}` });
  };

  return (
    <>
      <p className={styles.sectionLead}>
        Associe cada instância a um aplicativo e dê um rótulo à conta que deveria estar conectada. A coluna “Observado” mostra o que a IA realmente viu no app — se divergir do rótulo, confira o login antes de executar.
      </p>

      <div className={styles.bulkRow}>
        <label htmlFor="bulk-app" style={{ color: 'var(--text-2)' }}>Aplicar app a todas:</label>
        <Select id="bulk-app" small style={{ width: 240 }} value={bulkApp} onChange={(e) => setBulkApp(e.target.value)}>
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

      <div className={styles.tableWrap}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th scope="col">Instância</th>
              <th scope="col">Estado</th>
              <th scope="col">Aplicativo</th>
              <th scope="col">Rótulo da conta</th>
              <th scope="col">Observado no app</th>
              <th scope="col"><span className="sr-only">Ações</span></th>
            </tr>
          </thead>
          <tbody>
            {instances.map((inst) => {
              const d = drafts[inst.id];
              const dirty = isDirty(inst, d);
              const appValue = d?.app_id !== undefined ? d.app_id ?? '' : inst.app_id ?? '';
              const labelValue = d?.account_label !== undefined ? d.account_label : inst.account_label ?? '';
              const unknownApp = appValue && !apps.some((a) => a.id === appValue);
              return (
                <tr key={inst.id} className={cx(dirty && styles.rowDirty)}>
                  <td className={styles.cellId}>{inst.id}</td>
                  <td><StatusBadge meta={metaOf(INSTANCE_STATE, inst.state)} size="sm" /></td>
                  <td>
                    <Select small aria-label={`Aplicativo de ${inst.id}`} value={appValue} onChange={(e) => patchDraft(inst.id, { app_id: e.target.value || null })} style={{ minWidth: 200 }}>
                      <option value="">Sem app associado</option>
                      {unknownApp ? <option value={appValue}>{appValue} (não cadastrado)</option> : null}
                      {apps.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
                    </Select>
                  </td>
                  <td>
                    <TextInput
                      small
                      aria-label={`Rótulo da conta de ${inst.id}`}
                      value={labelValue}
                      placeholder="ex.: qa-user-01"
                      maxLength={80}
                      onChange={(e) => patchDraft(inst.id, { account_label: e.target.value })}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') void saveOne(inst);
                      }}
                      style={{ minWidth: 180 }}
                    />
                  </td>
                  <td className={styles.cellObserved}>
                    {inst.account_evidence ? (
                      <>
                        <Eye size={11} aria-hidden style={{ verticalAlign: '-1px', marginRight: 5, color: 'var(--text-3)' }} />
                        {inst.account_evidence}
                        <span className={styles.cellObservedTs}>observado em {formatDateTime(inst.account_evidence_ts)}</span>
                      </>
                    ) : (
                      <span style={{ color: 'var(--text-3)' }}>Nada observado ainda</span>
                    )}
                  </td>
                  <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                    <Button size="sm" icon={Save} loading={!!saving[inst.id]} disabled={!dirty || savingAll} onClick={() => void saveOne(inst)}>Salvar</Button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}
