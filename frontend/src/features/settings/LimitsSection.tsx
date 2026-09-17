import { Save, SlidersHorizontal, Undo2 } from 'lucide-react';
import { useMemo, useState } from 'react';
import { api } from '../../api/client';
import type { Settings } from '../../api/types';
import { Button } from '../../components/Button';
import { EmptyState } from '../../components/EmptyState';
import { Field, TextInput } from '../../components/Field';
import { cx } from '../../lib/format';
import { useAppStore } from '../../store/app';
import { toast, toastError } from '../../store/toasts';
import styles from './Settings.module.css';
import { ALL_LIMIT_FIELDS, LIMIT_GROUPS, crossValidate, parseNumber, validateLimit } from './validation';

type Drafts = Partial<Record<keyof Settings, string>>;

function toText(n: number | undefined): string {
  return typeof n === 'number' && Number.isFinite(n) ? String(n).replace('.', ',') : '';
}

export function LimitsSection() {
  const settings = useAppStore((s) => s.settings);
  const setSettings = useAppStore((s) => s.setSettings);
  // Só os campos mexidos ficam no rascunho; os demais acompanham o servidor (evento settings.updated).
  const [drafts, setDrafts] = useState<Drafts>({});
  const [saving, setSaving] = useState(false);
  const [showAll, setShowAll] = useState(false);

  const { errors, patch, dirtyCount } = useMemo(() => {
    const errs: Partial<Record<keyof Settings, string>> = {};
    const values: Partial<Record<keyof Settings, number>> = {};
    const changed: Partial<Settings> = {};
    let dirty = 0;
    if (settings) {
      for (const f of ALL_LIMIT_FIELDS) {
        const text = drafts[f.key];
        if (text === undefined) {
          values[f.key] = settings[f.key];
          continue;
        }
        const n = parseNumber(text);
        if (Number.isFinite(n)) values[f.key] = n;
        if (n === settings[f.key]) continue;
        dirty += 1;
        const err = validateLimit(f, text);
        if (err) errs[f.key] = err;
        else changed[f.key] = n;
      }
      if (dirty > 0) Object.assign(errs, { ...crossValidate(values), ...errs });
    }
    return { errors: errs, patch: changed, dirtyCount: dirty };
  }, [drafts, settings]);

  if (!settings) {
    return <EmptyState icon={SlidersHorizontal} title="Limites indisponíveis" hint="Os valores aparecem assim que o backend responder ao snapshot." />;
  }

  const errorCount = Object.keys(errors).length;

  const save = async () => {
    setShowAll(true);
    if (errorCount > 0 || dirtyCount === 0) return;
    setSaving(true);
    try {
      const saved = await api.putSettings(patch);
      setSettings(saved);
      setDrafts({});
      setShowAll(false);
      toast({ tone: 'success', title: 'Limites salvos', message: 'Valem para as próximas etapas; execuções em andamento continuam com os valores que já carregaram.' });
    } catch (e) {
      toastError('Não foi possível salvar os limites', e);
    } finally {
      setSaving(false);
    }
  };

  return (
    <form
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        void save();
      }}
      style={{ display: 'flex', flexDirection: 'column', gap: 16 }}
    >
      <p className={styles.sectionLead}>Freios de segurança e ritmo da automação. Os valores são validados de novo pelo backend ao salvar.</p>

      <div className={styles.limitGroups}>
        {LIMIT_GROUPS.map((g) => (
          <fieldset key={g.title} className={styles.limitGroup}>
            <legend>{g.title}</legend>
            <p className={styles.fieldsetHint} style={{ marginTop: -6 }}>{g.description}</p>
            <div className={styles.limitFields}>
              {g.fields.map((f) => {
                const text = drafts[f.key] ?? toText(settings[f.key]);
                const touched = drafts[f.key] !== undefined;
                const error = touched || showAll ? errors[f.key] ?? null : null;
                return (
                  <Field key={f.key} label={f.label} unit={f.unit} hint={f.hint || undefined} error={error}>
                    {(ids) => (
                      <TextInput
                        id={ids.id}
                        aria-describedby={ids.describedBy}
                        invalid={ids.invalid}
                        inputMode={f.integer ? 'numeric' : 'decimal'}
                        value={text}
                        onChange={(e) => setDrafts((d) => ({ ...d, [f.key]: e.target.value }))}
                      />
                    )}
                  </Field>
                );
              })}
            </div>
          </fieldset>
        ))}
      </div>

      <div className={styles.saveBar}>
        <span className={cx(styles.saveNote, dirtyCount > 0 && styles.saveNoteDirty)} aria-live="polite">
          {dirtyCount === 0 ? 'Nenhuma alteração pendente.' : errorCount > 0 ? `${dirtyCount} alteração(ões) · corrija ${errorCount} campo(s) para salvar` : `${dirtyCount} alteração(ões) não salva(s)`}
        </span>
        <Button variant="ghost" icon={Undo2} disabled={dirtyCount === 0 || saving} onClick={() => { setDrafts({}); setShowAll(false); }}>
          Descartar alterações
        </Button>
        <Button
          type="submit"
          variant="primary"
          icon={Save}
          loading={saving}
          disabledReason={dirtyCount === 0 ? 'Nenhuma alteração para salvar.' : errorCount > 0 ? 'Corrija os campos destacados.' : null}
        >
          Salvar limites
        </Button>
      </div>
    </form>
  );
}
