import { AppWindow, Lock, PackageSearch, Pencil, Plus, Save, Trash2, X } from 'lucide-react';
import { useMemo, useState } from 'react';
import { api, hintForError, toApiError } from '../../api/client';
import type { AppConfig } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Banner } from '../../components/Banner';
import { Button } from '../../components/Button';
import { confirm } from '../../components/Confirm';
import { Dialog } from '../../components/Dialog';
import { EmptyState } from '../../components/EmptyState';
import { Field, Select, TextArea, TextInput } from '../../components/Field';
import { localId } from '../../lib/ids';
import { selectInstanceList, useAppStore } from '../../store/app';
import { toast, toastError } from '../../store/toasts';
import styles from './Settings.module.css';
import { draftToInput, validateApp, type AppDraft, type AppErrors } from './validation';

function toDraft(app: AppConfig | null): AppDraft {
  return {
    name: app?.name ?? '',
    package: app?.package ?? '',
    activity: app?.activity ?? '',
    apk_path: app?.apk_path ?? '',
    nav_hints: app?.nav_hints ?? '',
    selectors: Object.entries(app?.known_selectors ?? {}).map(([name, value]) => ({ key: localId('sel'), name, value })),
  };
}

export function AppsSection() {
  const apps = useAppStore((s) => s.apps);
  const setApps = useAppStore((s) => s.setApps);
  const instances = useAppStore((s) => s.instances);
  const [editing, setEditing] = useState<{ app: AppConfig | null } | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);

  const usage = useMemo(() => {
    const m = new Map<string, number>();
    for (const i of Object.values(instances)) if (i.app_id) m.set(i.app_id, (m.get(i.app_id) ?? 0) + 1);
    return m;
  }, [instances]);

  const remove = async (app: AppConfig) => {
    const used = usage.get(app.id) ?? 0;
    const { confirmed } = await confirm({
      title: `Excluir “${app.name}”?`,
      danger: true,
      confirmLabel: 'Excluir aplicativo',
      cancelLabel: 'Cancelar',
      body: used > 0
        ? `${used} instância(s) usam este aplicativo e ficarão sem app associado. A configuração (dicas e seletores) será perdida.`
        : 'A configuração (dicas de navegação e seletores) será perdida. O app instalado nos emuladores não é removido.',
    });
    if (!confirmed) return;
    setDeleting(app.id);
    try {
      await api.deleteApp(app.id);
      setApps(useAppStore.getState().apps.filter((a) => a.id !== app.id));
      toast({ tone: 'success', title: `Aplicativo “${app.name}” excluído` });
    } catch (e) {
      toastError(`Não foi possível excluir “${app.name}”`, e);
    } finally {
      setDeleting(null);
    }
  };

  return (
    <>
      <div className={styles.sectionIntro}>
        <p className={styles.sectionLead}>
          Aplicativos que a IA sabe abrir e operar. As dicas de navegação e os seletores conhecidos ajudam o agente a acertar de primeira.
        </p>
        <Button variant="primary" icon={Plus} onClick={() => setEditing({ app: null })}>Novo aplicativo</Button>
      </div>

      {apps.length === 0 ? (
        <EmptyState icon={AppWindow} title="Nenhum aplicativo cadastrado" hint="Cadastre o app que será testado: basta o nome e o pacote Android." actions={<Button variant="outline" icon={Plus} onClick={() => setEditing({ app: null })}>Cadastrar aplicativo</Button>} />
      ) : (
        <ul className={styles.appList}>
          {apps.map((app) => {
            const used = usage.get(app.id) ?? 0;
            const selectors = Object.keys(app.known_selectors ?? {}).length;
            return (
              <li key={app.id} className={styles.app}>
                <div className={styles.appTop}>
                  <AppWindow size={16} aria-hidden style={{ color: 'var(--accent-text)', flex: 'none' }} />
                  <span className={`${styles.appName} truncate`}>{app.name}</span>
                  {app.builtin ? <Badge tone="info" icon={Lock} size="sm">embutido</Badge> : null}
                </div>
                <span className={styles.appPkg}>{app.package}{app.activity ? `/${app.activity}` : ''}</span>
                <span className={styles.appMeta}>
                  {used} instância(s) · {selectors} seletor(es) · {app.apk_path ? 'APK configurado' : 'sem APK'} · {app.nav_hints ? 'com dicas de navegação' : 'sem dicas'}
                </span>
                <div className={styles.appActions}>
                  <Button size="sm" icon={Pencil} onClick={() => setEditing({ app })}>Editar</Button>
                  <Button
                    size="sm"
                    variant="dangerGhost"
                    icon={Trash2}
                    loading={deleting === app.id}
                    disabledReason={app.builtin ? 'Aplicativos embutidos não podem ser excluídos.' : null}
                    onClick={() => void remove(app)}
                  >
                    Excluir
                  </Button>
                </div>
              </li>
            );
          })}
        </ul>
      )}

      {editing ? <AppEditor key={editing.app?.id ?? 'new'} app={editing.app} onClose={() => setEditing(null)} /> : null}
    </>
  );
}

function AppEditor({ app, onClose }: { app: AppConfig | null; onClose: () => void }) {
  const setApps = useAppStore((s) => s.setApps);
  const [draft, setDraft] = useState<AppDraft>(() => toDraft(app));
  const [errors, setErrors] = useState<AppErrors>({});
  const [saving, setSaving] = useState(false);
  const [picking, setPicking] = useState(false);

  const set = <K extends keyof AppDraft>(key: K, value: AppDraft[K]) => setDraft((d) => ({ ...d, [key]: value }));

  const save = async () => {
    const found = validateApp(draft);
    setErrors(found);
    if (Object.keys(found).length > 0) return;
    setSaving(true);
    try {
      const body = draftToInput(draft);
      const saved = app ? await api.updateApp(app.id, body) : await api.createApp(body);
      const current = useAppStore.getState().apps;
      setApps(current.some((a) => a.id === saved.id) ? current.map((a) => (a.id === saved.id ? saved : a)) : [...current, saved]);
      toast({ tone: 'success', title: app ? `“${saved.name}” atualizado` : `“${saved.name}” cadastrado` });
      onClose();
    } catch (e) {
      toastError('Não foi possível salvar o aplicativo', e);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog
      open
      onClose={onClose}
      size="md"
      icon={AppWindow}
      title={app ? `Editar ${app.name}` : 'Novo aplicativo'}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>Cancelar</Button>
          <Button variant="primary" icon={Save} loading={saving} onClick={() => void save()}>Salvar</Button>
        </>
      }
    >
      <form
        className={styles.form}
        noValidate
        onSubmit={(e) => {
          e.preventDefault();
          void save();
        }}
      >
        <div className={styles.formRow}>
          <Field label="Nome" error={errors.name}>
            {(f) => <TextInput id={f.id} aria-describedby={f.describedBy} invalid={f.invalid} value={draft.name} placeholder="QA Messenger" onChange={(e) => set('name', e.target.value)} autoFocus />}
          </Field>
          <Field label="Pacote Android" error={errors.package} hint="Identificador do app, ex.: com.exemplo.app">
            {(f) => <TextInput id={f.id} aria-describedby={f.describedBy} invalid={f.invalid} mono value={draft.package} placeholder="com.exemplo.app" onChange={(e) => set('package', e.target.value)} />}
          </Field>
        </div>

        {picking ? (
          <PackagePicker
            onPick={(pkg) => {
              set('package', pkg);
              setPicking(false);
            }}
            onCancel={() => setPicking(false)}
          />
        ) : (
          <div>
            <Button size="sm" variant="outline" icon={PackageSearch} onClick={() => setPicking(true)}>Escolher app já instalado</Button>
          </div>
        )}

        <div className={styles.formRow}>
          <Field label="Activity inicial" unit="opcional" error={errors.activity} hint="Deixe vazio para usar a activity padrão do pacote.">
            {(f) => <TextInput id={f.id} aria-describedby={f.describedBy} invalid={f.invalid} mono value={draft.activity} placeholder=".MainActivity" onChange={(e) => set('activity', e.target.value)} />}
          </Field>
          <Field label="Caminho do APK" unit="opcional" error={errors.apk_path} hint="Caminho local na máquina do backend; ele valida se o arquivo existe.">
            {(f) => <TextInput id={f.id} aria-describedby={f.describedBy} invalid={f.invalid} mono value={draft.apk_path} placeholder="C:\apks\app.apk" onChange={(e) => set('apk_path', e.target.value)} />}
          </Field>
        </div>

        <Field label="Dicas de navegação" unit="opcional" hint="Texto livre que a IA lê antes de planejar. Ex.: “A lista de conversas fica na aba Chats; o botão enviar é o ícone de avião”.">
          {(f) => <TextArea id={f.id} aria-describedby={f.describedBy} rows={4} value={draft.nav_hints} onChange={(e) => set('nav_hints', e.target.value)} />}
        </Field>

        <fieldset style={{ border: 0, padding: 0, margin: 0 }}>
          <legend className={styles.fieldsetTitle}>Seletores conhecidos <span className={styles.fieldsetHint}>· opcional · nome → resource-id, texto ou accessibility id</span></legend>
          <div className={styles.selectorList} style={{ marginTop: 6 }}>
            {draft.selectors.map((row, idx) => (
              <div key={row.key} className={styles.selectorRow}>
                <TextInput
                  mono
                  value={row.name}
                  placeholder="send_button"
                  aria-label={`Nome do seletor ${idx + 1}`}
                  onChange={(e) => set('selectors', draft.selectors.map((r) => (r.key === row.key ? { ...r, name: e.target.value } : r)))}
                />
                <TextInput
                  mono
                  value={row.value}
                  placeholder="com.exemplo.app:id/send"
                  aria-label={`Valor do seletor ${idx + 1}`}
                  onChange={(e) => set('selectors', draft.selectors.map((r) => (r.key === row.key ? { ...r, value: e.target.value } : r)))}
                />
                <Button variant="ghost" icon={X} iconOnly label={`Remover seletor ${idx + 1}`} onClick={() => set('selectors', draft.selectors.filter((r) => r.key !== row.key))} />
              </div>
            ))}
            {errors.selectors ? <p role="alert" style={{ color: 'var(--danger-text)', fontSize: 'var(--fs-xs)' }}>{errors.selectors}</p> : null}
            <div>
              <Button size="sm" icon={Plus} onClick={() => set('selectors', [...draft.selectors, { key: localId('sel'), name: '', value: '' }])}>Adicionar seletor</Button>
            </div>
          </div>
        </fieldset>
        {/* Enter dentro de um campo salva o formulário */}
        <button type="submit" hidden aria-hidden tabIndex={-1} />
      </form>
    </Dialog>
  );
}

function PackagePicker({ onPick, onCancel }: { onPick: (pkg: string) => void; onCancel: () => void }) {
  const instancesMap = useAppStore((s) => s.instances);
  const order = useAppStore((s) => s.instanceOrder);
  const online = useMemo(
    () => selectInstanceList({ instances: instancesMap, instanceOrder: order }).filter((i) => i.state === 'online'),
    [instancesMap, order],
  );
  const [instanceId, setInstanceId] = useState<string>(() => online[0]?.id ?? '');
  const [packages, setPackages] = useState<string[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState('');

  const load = async () => {
    if (!instanceId) return;
    setLoading(true);
    setError(null);
    try {
      const res = await api.packages(instanceId);
      setPackages(Array.isArray(res?.packages) ? res.packages.slice().sort() : []);
    } catch (e) {
      const err = toApiError(e);
      setPackages(null);
      setError(`${err.message} ${hintForError(err)}`);
    } finally {
      setLoading(false);
    }
  };

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (packages ?? []).filter((p) => !q || p.toLowerCase().includes(q));
  }, [packages, query]);

  return (
    <div className={styles.picker} role="group" aria-label="Escolher app já instalado">
      <div className={styles.pickerRow}>
        <strong style={{ flex: 1 }}>Escolher app já instalado</strong>
        <Button size="sm" variant="ghost" icon={X} onClick={onCancel}>Fechar</Button>
      </div>
      {online.length === 0 ? (
        <Banner tone="warning" icon={PackageSearch} compact>Nenhuma instância online. Inicie um emulador no Painel para listar os pacotes instalados nele.</Banner>
      ) : (
        <>
          <div className={styles.pickerRow}>
            <Select aria-label="Instância de onde ler os pacotes" value={instanceId} onChange={(e) => { setInstanceId(e.target.value); setPackages(null); }}>
              {online.map((i) => <option key={i.id} value={i.id}>{i.id}</option>)}
            </Select>
            <Button icon={PackageSearch} loading={loading} onClick={() => void load()}>Carregar pacotes</Button>
          </div>
          {error ? <Banner tone="danger" icon={PackageSearch} compact title="Não foi possível listar os pacotes">{error}</Banner> : null}
          {packages ? (
            <>
              <TextInput small value={query} placeholder={`Filtrar ${packages.length} pacotes…`} aria-label="Filtrar pacotes" onChange={(e) => setQuery(e.target.value)} />
              {filtered.length === 0 ? (
                <p className={styles.fieldsetHint}>Nenhum pacote corresponde ao filtro.</p>
              ) : (
                <div className={styles.pkgList} role="listbox" aria-label="Pacotes instalados">
                  {filtered.slice(0, 400).map((p) => (
                    <button key={p} type="button" role="option" aria-selected={false} className={styles.pkgItem} onClick={() => onPick(p)}>{p}</button>
                  ))}
                </div>
              )}
            </>
          ) : null}
        </>
      )}
    </div>
  );
}
