import { Link2, TriangleAlert } from 'lucide-react';
import { useMemo, useState } from 'react';
import { api, toApiError } from '../../api/client';
import type { PersonaDTO } from '../../api/types';
import { Button } from '../../components/Button';
import { Checkbox, Field, Select } from '../../components/Field';
import { selectTaskInstances, useAppStore } from '../../store/app';
import { toast } from '../../store/toasts';
import { nomeDe } from './pessoa';
import styles from './Profiles.module.css';
import { usePersonas } from './usePersonas';
import { recusaDoVinculo, type RecusaDoVinculo } from './vinculo';

/**
 * "Vincular" (N:N, ADR-043), dos dois lados: na persona (o aparelho é escolhido) e no Foco (a persona é escolhida).
 * Soma um vínculo sem tirar ninguém do aparelho; o app é opcional (sem app = os apps sem conta gerenciada). A recusa
 * fica NO formulário, com o nome de quem já usa aquele app lá — um toast sumiria antes de a pessoa ler o que fazer.
 */
export function VincularForm({ personaId, instanceId, jaVinculados = [], onVinculado, onCancelar }: {
  /** Persona fixa (guia Aparelhos da persona). Sem ela, o formulário pergunta qual. */
  personaId?: string;
  /** Aparelho fixo (Foco). Sem ele, o formulário pergunta qual. */
  instanceId?: string;
  /** Aparelhos que esta persona já tem (só para o rótulo "já vinculada"; outro app no mesmo aparelho é permitido). */
  jaVinculados?: readonly string[];
  onVinculado: (persona: PersonaDTO) => Promise<void> | void;
  onCancelar?: () => void;
}) {
  const instancesMap = useAppStore((s) => s.instances);
  const order = useAppStore((s) => s.instanceOrder);
  const apps = useAppStore((s) => s.apps);
  // A loja nunca recebe persona (o backend recusa com `store_instance`): nem aparece na lista.
  const aparelhos = useMemo(() => selectTaskInstances({ instances: instancesMap, instanceOrder: order }), [instancesMap, order]);
  const pessoas = usePersonas(!personaId);
  const [pessoa, setPessoa] = useState(personaId ?? '');
  const [aparelho, setAparelho] = useState(instanceId ?? '');
  const [appId, setAppId] = useState('');
  const [principal, setPrincipal] = useState(jaVinculados.length === 0 && !!personaId);
  const [enviando, setEnviando] = useState(false);
  const [recusa, setRecusa] = useState<RecusaDoVinculo | null>(null);

  const motivo = !pessoa ? 'Escolha a persona.' : !aparelho ? 'Escolha o aparelho.' : null;

  async function vincular() {
    if (motivo || enviando) return;
    setEnviando(true);
    setRecusa(null);
    try {
      const atualizada = await api.bindPersonaDevice(pessoa, { instance_id: aparelho, app_id: appId || null, primary: principal });
      toast({ tone: 'success', title: `${nomeDe(atualizada)} vinculada a ${aparelho}`,
              message: 'A sessão da conta neste aparelho começa por verificar.' });
      await onVinculado(atualizada);
    } catch (e) {
      const app = apps.find((a) => a.id === appId);
      setRecusa(await recusaDoVinculo(toApiError(e), {
        instanceId: aparelho, appId: appId || null, appNome: app?.name ?? null, profileId: pessoa,
      }));
    } finally {
      setEnviando(false);
    }
  }

  return (
    <div className={styles.vincularForm}>
      {personaId ? null : (
        <Field label="Persona">
          {({ id }) => (
            <Select id={id} value={pessoa} onChange={(e) => setPessoa(e.target.value)}>
              <option value="">{pessoas === null ? 'Carregando…' : 'Escolha…'}</option>
              {(pessoas ?? []).map((p) => (
                <option key={p.id} value={p.id}>{nomeDe(p)}{p.username ? ` (@${p.username})` : ''}</option>
              ))}
            </Select>
          )}
        </Field>
      )}
      {instanceId ? null : (
        <Field label="Aparelho">
          {({ id }) => (
            <Select id={id} value={aparelho} onChange={(e) => setAparelho(e.target.value)}>
              <option value="">Escolha…</option>
              {aparelhos.map((i) => (
                <option key={i.id} value={i.id}>{i.id}{jaVinculados.includes(i.id) ? ' (já vinculada)' : ''}</option>
              ))}
            </Select>
          )}
        </Field>
      )}
      <Field label="App do vínculo" unit="opcional"
             hint="A conta de qual app esta persona usa ali. Um aparelho tem uma conta por app.">
        {({ id, describedBy }) => (
          <Select id={id} aria-describedby={describedBy} value={appId} onChange={(e) => setAppId(e.target.value)}>
            <option value="">Nenhum (apps sem conta gerenciada)</option>
            {apps.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </Select>
        )}
      </Field>
      <Checkbox label="Tornar o aparelho principal desta persona" aria-label="Tornar o aparelho principal desta persona"
                checked={principal}
                onChange={(e) => setPrincipal(e.target.checked)} />
      {recusa ? (
        <div className={styles.vincularRecusa} role="alert">
          <p><TriangleAlert size={13} aria-hidden /> <strong>{recusa.titulo}.</strong> {recusa.texto}</p>
          <p className={styles.muted}>{recusa.mensagem}</p>
        </div>
      ) : null}
      <div className={styles.accountFormActions}>
        <Button size="sm" variant="primary" icon={Link2} loading={enviando} disabledReason={motivo}
                onClick={() => void vincular()}>
          Vincular
        </Button>
        {onCancelar ? <Button size="sm" variant="ghost" onClick={onCancelar}>Cancelar</Button> : null}
      </div>
    </div>
  );
}
