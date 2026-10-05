import { Save, SlidersHorizontal, Undo2 } from 'lucide-react';
import { useId, useMemo, useState } from 'react';
import { api } from '../../api/client';
import type { Health, Settings } from '../../api/types';
import { Button } from '../../components/Button';
import { EmptyState } from '../../components/EmptyState';
import { Checkbox, Field, Select, TextInput } from '../../components/Field';
import { PageSection } from '../../components/Page';
import { cx } from '../../lib/format';
import { useAppStore } from '../../store/app';
import { toast, toastError } from '../../store/toasts';
import { ServersLimits } from './ServersLimits';
import styles from './Settings.module.css';
import {
  LIMIT_GROUPS, buildSettingsPatch, limitToText, type ChoiceField, type LimitDrafts, type LimitsFormState, type ToggleField,
} from './validation';

const EMPTY_FORM: LimitsFormState = { errors: {}, patch: {}, dirtyCount: 0 };

export function LimitsSection() {
  const settings = useAppStore((s) => s.settings);
  const setSettings = useAppStore((s) => s.setSettings);
  const features = useAppStore((s) => s.health?.features ?? null);
  // Só os campos mexidos ficam no rascunho; os demais acompanham o servidor (evento settings.updated).
  const [drafts, setDrafts] = useState<LimitDrafts>({});
  const [saving, setSaving] = useState(false);
  const [showAll, setShowAll] = useState(false);

  const { errors, patch, dirtyCount } = useMemo(
    () => (settings ? buildSettingsPatch(settings, drafts) : EMPTY_FORM),
    [drafts, settings],
  );

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
      className={styles.stack}
    >
      <PageSection
        title="Por servidor"
        titleId="limites-por-servidor"
        subtitle="O que cada máquina aguenta: aparelhos ligados, ligando e trabalhando ao mesmo tempo, quantos aparelhos ela pode ter e a RAM que guarda para si. O agendador e o balanceamento (“Distribuir entre servidores”, no painel de comando) usam estes números para decidir onde cada aparelho trabalha. Cada cartão salva sozinho."
      >
        <ServersLimits />
      </PageSection>

      {/* A barra de salvar é o rodapé DESTE cartão: antes era fixa ao pé da página com margem negativa, e no
          celular cortava o botão na borda direita. */}
      <PageSection
        title="Parque — vale para todos os servidores"
        titleId="limites-do-parque"
        subtitle="Freios de segurança e ritmo da automação. Os valores são validados de novo pelo backend ao salvar."
        footer={(
          <>
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
          </>
        )}
      >
        <div className={styles.limitGroups}>
          {LIMIT_GROUPS.map((g) => (
            // Desabilitado enquanto salva: a resposta zera os rascunhos, e o que a pessoa mexesse com o PUT em voo sumia sem
            // ser salvo nem avisado (29.115). O `fieldset` desabilita todos os campos do grupo de uma vez.
            <fieldset key={g.title} className={styles.limitGroup} disabled={saving}>
              <legend>{g.title}</legend>
              <p className={cx(styles.fieldsetHint, styles.legendHint)}>{g.description}</p>
              {(g.toggles ?? []).map((t) => (
                <ToggleRow
                  key={t.key}
                  field={t}
                  checked={drafts[t.key] ?? settings[t.key] === true}
                  onChange={(value) => setDrafts((d) => ({ ...d, [t.key]: value }))}
                />
              ))}
              {(g.choices ?? []).map((c) => (
                <ChoiceRow
                  key={c.key}
                  field={c}
                  value={drafts[c.key] ?? settings[c.key]}
                  onChange={(value) => setDrafts((d) => ({ ...d, [c.key]: value }))}
                />
              ))}
              <div className={styles.limitFields}>
                {g.fields.map((f) => {
                  const text = drafts[f.key] ?? limitToText(settings[f.key]);
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
              {g.id === 'rotation' ? <FeaturesLine features={features} /> : null}
            </fieldset>
          ))}
        </div>
      </PageSection>
    </form>
  );
}

function ToggleRow({ field, checked, onChange }: { field: ToggleField; checked: boolean; onChange: (value: boolean) => void }) {
  const hintId = useId();
  return (
    <div className={styles.toggleRow}>
      <Checkbox label={field.label} checked={checked} aria-describedby={hintId} onChange={(e) => onChange(e.target.checked)} />
      <p id={hintId} className={styles.fieldsetHint}>{field.hint}</p>
    </div>
  );
}

type ChoiceValue = NonNullable<Settings[ChoiceField['key']]>;

/**
 * Escolha entre valores nomeados. Backend sem o campo (anterior ao adendo que o criou) não tem o que escolher: a
 * caixa aparece desabilitada dizendo isso, em vez de oferecer um valor que o servidor não guardaria.
 */
function ChoiceRow({ field, value, onChange }: { field: ChoiceField; value: ChoiceValue | undefined; onChange: (value: ChoiceValue) => void }) {
  const known = value !== undefined;
  return (
    <Field label={field.label} hint={known ? field.hint : 'O backend não informou este ajuste (versão anterior): ele captura a prévia sempre.'}>
      {(ids) => (
        <Select
          id={ids.id}
          aria-describedby={ids.describedBy}
          value={value ?? ''}
          disabled={!known}
          onChange={(e) => {
            const picked = field.options.find((o) => o.value === e.target.value);
            if (picked) onChange(picked.value);
          }}
        >
          {known ? null : <option value="">—</option>}
          {field.options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </Select>
      )}
    </Field>
  );
}

/** Somente leitura: o que o backend tem ligado (vem de `health.features`, definido no arquivo de configuração). */
function FeaturesLine({ features }: { features: Health['features'] | null }) {
  if (!features) {
    return <p className={styles.featuresLine}>Recursos do backend: indisponíveis (o backend não informou <span className="mono">health.features</span>).</p>;
  }
  return (
    <p className={styles.featuresLine}>
      Recursos do backend (somente leitura): hibernação <strong>{features.hibernation ? 'ligada' : 'desligada'}</strong>
      {' · '}imagem do sistema: <span className="mono">{features.system_image || '—'}</span>
    </p>
  );
}
