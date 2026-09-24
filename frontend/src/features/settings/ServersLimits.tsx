import { Cpu, MemoryStick, RotateCcw, Save, Server, Smartphone, Undo2 } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '../../api/client';
import type { ServerLimits } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Button } from '../../components/Button';
import { EmptyState } from '../../components/EmptyState';
import { Field, TextInput } from '../../components/Field';
import { ProgressBar } from '../../components/ProgressBar';
import { cx } from '../../lib/format';
import { useAppStore } from '../../store/app';
import { toast, toastError } from '../../store/toasts';
import styles from './Settings.module.css';
import { SERVER_LIMIT_FIELDS, buildServerPatch, declaredText, gb, ramUsed, shownValue, type ServerDrafts } from './serverLimits';

/** Releitura da carga (CPU, RAM, aparelhos) enquanto a tela está aberta. */
const REFRESH_MS = 10_000;

/** Tela Limites → Por servidor: um cartão por máquina, com o que ela aguenta e o que está usando agora. */
export function ServersLimits() {
  const [servers, setServers] = useState<ServerLimits[] | null>(null);
  const [failed, setFailed] = useState(false);

  const load = useCallback(async () => {
    try {
      setServers(await api.getServerLimits());
      setFailed(false);
    } catch {
      setFailed(true);
    }
  }, []);

  useEffect(() => {
    void load();
    const t = setInterval(() => void load(), REFRESH_MS);
    return () => clearInterval(t);
  }, [load]);

  if (!servers) {
    return failed
      ? <EmptyState icon={Server} title="Servidores indisponíveis" hint="Os limites por servidor aparecem assim que o backend responder." />
      : <p className={styles.sectionLead}>Carregando servidores…</p>;
  }

  return (
    <div className={styles.serverCards}>
      {servers.map((s) => (
        <ServerCard key={s.worker_id} server={s} onSaved={(novo) => setServers((lista) => (lista ?? []).map((x) => (x.worker_id === novo.worker_id ? novo : x)))} />
      ))}
    </div>
  );
}

function ServerCard({ server, onSaved }: { server: ServerLimits; onSaved: (s: ServerLimits) => void }) {
  const [drafts, setDrafts] = useState<ServerDrafts>({});
  const [saving, setSaving] = useState(false);
  const [showAll, setShowAll] = useState(false);
  const { patch, errors, dirty } = useMemo(() => buildServerPatch(server, drafts), [server, drafts]);
  const errorCount = Object.keys(errors).length;
  const ram = ramUsed(server);
  const cpu = server.cpu_percent;
  const status = server.maintenance ? { tone: 'warning' as const, text: 'em manutenção' }
    : server.connected ? { tone: 'success' as const, text: 'conectado' }
    : { tone: 'danger' as const, text: 'desconectado' };

  const save = async () => {
    setShowAll(true);
    if (errorCount > 0 || dirty === 0) return;
    setSaving(true);
    try {
      const novo = await api.putServerLimits(server.worker_id, patch);
      onSaved(novo);
      if (server.is_host && ('max_slots' in patch || 'boot_parallelism' in patch)) {
        // Vagas e boots DESTE servidor moram nas configurações vivas: a barra superior ("vagas N") lê de lá.
        api.getSettings().then(useAppStore.getState().setSettings).catch(() => undefined);
      }
      setDrafts({});
      setShowAll(false);
      toast({
        tone: 'success', title: `Limites de ${server.name} salvos`,
        message: server.is_host ? 'Valem a partir do próximo tick do agendador.'
          : server.connected ? 'Enviados ao agente da máquina, que já está aplicando.'
          : 'A máquina está desconectada: recebe os limites assim que voltar.',
      });
    } catch (e) {
      toastError(`Não foi possível salvar os limites de ${server.name}`, e);
    } finally {
      setSaving(false);
    }
  };

  return (
    // `div role=group`, e não `form`: o cartão mora dentro do formulário do parque (LimitsSection), e formulário
    // aninhado é HTML inválido — o Enter num campo daqui enviaria o formulário de fora.
    <div
      role="group"
      className={cx(styles.limitGroup, styles.serverCard)}
      aria-label={`Limites de ${server.name}`}
      onKeyDown={(e) => {
        if (e.key === 'Enter' && (e.target as HTMLElement).tagName === 'INPUT') {
          e.preventDefault();
          void save();
        }
      }}
    >
      <div className={styles.serverHead}>
        <Server size={16} aria-hidden />
        <strong className={styles.serverName} title={server.name}>{server.name}</strong>
        <Badge size="sm" tone={server.is_host ? 'accent' : 'neutral'}>{server.is_host ? 'este servidor' : 'worker'}</Badge>
        <Badge size="sm" tone={status.tone}>{status.text}</Badge>
      </div>

      <div className={styles.serverLoad}>
        <div className={styles.serverMeter}>
          <span className={styles.serverMeterLabel}><Cpu size={13} aria-hidden /> CPU</span>
          <ProgressBar value={(cpu ?? 0) / 100} label={`CPU de ${server.name}`} tone={cpu !== null && cpu > 85 ? 'danger' : cpu !== null && cpu > 65 ? 'warning' : 'accent'}
                       text={cpu === null ? '—' : `${Math.round(cpu)}%${server.cpu_count ? ` de ${server.cpu_count} núcleos` : ''}`} />
        </div>
        <div className={styles.serverMeter}>
          <span className={styles.serverMeterLabel}><MemoryStick size={13} aria-hidden /> RAM</span>
          <ProgressBar value={ram ?? 0} label={`RAM em uso em ${server.name}`} tone={ram !== null && ram > 0.9 ? 'danger' : ram !== null && ram > 0.75 ? 'warning' : 'accent'}
                       text={ram === null ? '—' : `${gb(server.ram_free_mb)} livres de ${gb(server.ram_total_mb)}`} />
        </div>
        <p className={styles.serverCounts}>
          <Smartphone size={13} aria-hidden />
          <span><strong>{server.online}</strong> de {server.devices} ligados</span>
          <span>·</span>
          <span><strong>{server.working}</strong> trabalhando</span>
          {server.effective.max_working ? <span className={styles.serverCountsMuted}>(teto {server.effective.max_working})</span> : null}
        </p>
      </div>

      <div className={styles.limitFields}>
        {SERVER_LIMIT_FIELDS.map((f) => {
          const locked = server.locked[f.key];
          const draft = drafts[f.key];
          const text = draft === null ? (server.declared[f.key] === null ? '' : String(server.declared[f.key])) : draft ?? shownValue(server, f.key);
          const touched = draft !== undefined;
          const error = touched || showAll ? errors[f.key] ?? null : null;
          const decided = server.decided[f.key] !== null && draft !== null;
          const hint = locked ?? (
            <>
              {f.hint} <span className={styles.serverDeclared}>Valor da máquina: {declaredText(server, f.key)}.</span>
            </>
          );
          return (
            <Field key={f.key} label={f.label} unit={f.unit} hint={hint} error={error}>
              {(ids) => (
                <div className={styles.serverInputRow}>
                  <TextInput
                    id={ids.id}
                    aria-describedby={ids.describedBy}
                    invalid={ids.invalid}
                    inputMode="numeric"
                    disabled={!!locked}
                    placeholder={f.optional ? 'sem teto' : undefined}
                    value={text}
                    onChange={(e) => setDrafts((d) => ({ ...d, [f.key]: e.target.value }))}
                  />
                  {decided && !locked ? (
                    <Button size="sm" variant="ghost" icon={RotateCcw} title="Voltar ao valor da máquina"
                            onClick={() => setDrafts((d) => ({ ...d, [f.key]: null }))}>
                      Da máquina
                    </Button>
                  ) : null}
                </div>
              )}
            </Field>
          );
        })}
      </div>

      <div className={styles.serverActions}>
        <span className={cx(styles.saveNote, dirty > 0 && styles.saveNoteDirty)} aria-live="polite">
          {dirty === 0 ? (errorCount > 0 ? `Corrija ${errorCount} campo(s)` : 'Sem alterações') : `${dirty} alteração(ões) não salva(s)`}
        </span>
        <Button size="sm" variant="ghost" icon={Undo2} disabled={(dirty === 0 && errorCount === 0) || saving}
                onClick={() => { setDrafts({}); setShowAll(false); }}>
          Descartar
        </Button>
        <Button size="sm" variant="primary" icon={Save} loading={saving} onClick={() => void save()}
                disabledReason={dirty === 0 ? 'Nenhuma alteração para salvar.' : errorCount > 0 ? 'Corrija os campos destacados.' : null}>
          Salvar
        </Button>
      </div>
    </div>
  );
}
