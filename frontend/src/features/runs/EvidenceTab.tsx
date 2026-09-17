import { EyeOff, FileText, Image as ImageIcon, ImageOff, ListTree, ScanSearch, type LucideIcon } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { api, evidenceUrl, hintForError, toApiError } from '../../api/client';
import type { Evidence, RunDetail } from '../../api/types';
import { Banner } from '../../components/Banner';
import { Dialog } from '../../components/Dialog';
import { EmptyState } from '../../components/EmptyState';
import { CodeBlock, KvList, KvRow } from '../../components/JsonTree';
import { Spinner } from '../../components/Skeleton';
import { EVIDENCE_KIND } from '../../lib/status';
import { formatClock, formatDateTime } from '../../lib/time';
import styles from './Runs.module.css';

const KIND_ICON: Record<Evidence['kind'], LucideIcon> = {
  screenshot: ImageIcon,
  hierarchy: ListTree,
  text: FileText,
  verifier: ScanSearch,
};

interface Group {
  instanceId: string;
  steps: { stepId: string | null; title: string; items: Evidence[] }[];
}

export function EvidenceTab({ detail }: { detail: RunDetail }) {
  const [opened, setOpened] = useState<Evidence | null>(null);

  const groups = useMemo<Group[]>(() => {
    const stepTitle = new Map(detail.steps.map((s) => [s.id, `${s.seq}. ${s.title}${s.plan_version > 1 ? ` (plano v${s.plan_version})` : ''}`]));
    const byInstance = new Map<string, Map<string, Evidence[]>>();
    for (const ev of detail.evidence) {
      const steps = byInstance.get(ev.instance_id) ?? new Map<string, Evidence[]>();
      const key = ev.step_id ?? '';
      const list = steps.get(key) ?? [];
      list.push(ev);
      steps.set(key, list);
      byInstance.set(ev.instance_id, steps);
    }
    return Array.from(byInstance.entries())
      .sort((a, b) => a[0].localeCompare(b[0]))
      .map(([instanceId, steps]) => ({
        instanceId,
        steps: Array.from(steps.entries()).map(([stepId, items]) => ({
          stepId: stepId || null,
          title: stepId ? stepTitle.get(stepId) ?? 'Etapa de um plano anterior' : 'Sem etapa associada',
          items: items.slice().sort((a, b) => (a.ts < b.ts ? -1 : a.ts > b.ts ? 1 : a.id - b.id)),
        })),
      }));
  }, [detail.evidence, detail.steps]);

  if (groups.length === 0) {
    return (
      <EmptyState icon={ImageIcon} compact title="Nenhuma evidência registrada ainda" hint="Capturas de tela e resultados do verificador aparecem aqui conforme as etapas são concluídas." />
    );
  }

  return (
    <div>
      {groups.map((g) => (
        <section key={g.instanceId} className={styles.evGroup} aria-label={`Evidências de ${g.instanceId}`}>
          <h3 className={styles.evGroupTitle}>{g.instanceId}</h3>
          {g.steps.map((s) => (
            <div key={s.stepId ?? 'none'}>
              <p className={styles.evStep}>{s.title}</p>
              <div className={styles.gallery}>
                {s.items.map((ev) => <EvidenceTile key={ev.id} evidence={ev} onOpen={() => setOpened(ev)} />)}
              </div>
            </div>
          ))}
        </section>
      ))}
      {opened ? <EvidenceDialog evidence={opened} onClose={() => setOpened(null)} /> : null}
    </div>
  );
}

function EvidenceTile({ evidence: ev, onOpen }: { evidence: Evidence; onOpen: () => void }) {
  const [broken, setBroken] = useState(false);
  const Icon = KIND_ICON[ev.kind] ?? FileText;
  const kindLabel = EVIDENCE_KIND[ev.kind] ?? ev.kind;
  const caption = (
    <span className={styles.evCaption}>
      <span className={styles.evNote}>{ev.note ?? kindLabel}</span>
      <span className={styles.evTs}>{formatClock(ev.ts)} · {kindLabel}</span>
    </span>
  );

  if (ev.redacted) {
    return (
      <div className={styles.evTile} role="group" aria-label={`${kindLabel} — conteúdo ocultado`}>
        <span className={styles.evThumb}>
          <span className={styles.evRedacted}>
            <EyeOff size={20} aria-hidden />
            conteúdo ocultado
          </span>
        </span>
        {caption}
      </div>
    );
  }

  return (
    <button type="button" className={styles.evTile} onClick={onOpen} aria-label={`Ampliar ${kindLabel.toLowerCase()} de ${formatClock(ev.ts)}${ev.note ? `: ${ev.note}` : ''}`}>
      <span className={styles.evThumb}>
        {ev.kind === 'screenshot' && !broken ? (
          <img src={evidenceUrl(ev)} alt="" loading="lazy" decoding="async" onError={() => setBroken(true)} />
        ) : ev.kind === 'screenshot' ? (
          <ImageOff size={22} aria-hidden />
        ) : (
          <Icon size={26} aria-hidden />
        )}
      </span>
      {caption}
    </button>
  );
}

function EvidenceDialog({ evidence: ev, onClose }: { evidence: Evidence; onClose: () => void }) {
  const isImage = ev.kind === 'screenshot';
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState<{ message: string; hint: string } | null>(null);
  const [imgBroken, setImgBroken] = useState(false);

  useEffect(() => {
    if (isImage) return;
    const abort = new AbortController();
    setText(null);
    setError(null);
    api.evidenceText(ev, abort.signal).then(
      (t) => setText(t),
      (e: unknown) => {
        if (abort.signal.aborted) return;
        const err = toApiError(e);
        setError({ message: err.message, hint: hintForError(err) });
      },
    );
    return () => abort.abort();
  }, [ev, isImage]);

  let pretty: unknown = text;
  if (text !== null) {
    try {
      pretty = JSON.parse(text);
    } catch {
      pretty = text;
    }
  }

  return (
    <Dialog open onClose={onClose} size="lg" title={`${EVIDENCE_KIND[ev.kind] ?? ev.kind} — ${ev.instance_id}`} icon={KIND_ICON[ev.kind] ?? FileText}>
      {isImage ? (
        imgBroken ? (
          <Banner tone="danger" icon={ImageOff} title="Não foi possível abrir a imagem">O arquivo pode ter sido removido pela política de retenção de evidências.</Banner>
        ) : (
          <div className={styles.evFull}>
            <img src={evidenceUrl(ev)} alt={ev.note ?? `Captura de tela de ${ev.instance_id}`} onError={() => setImgBroken(true)} />
          </div>
        )
      ) : error ? (
        <Banner tone="danger" icon={ImageOff} title="Não foi possível carregar o conteúdo">{error.message} {error.hint}</Banner>
      ) : text === null ? (
        <Spinner label="Carregando o conteúdo da evidência…" />
      ) : (
        <CodeBlock value={pretty} />
      )}
      <KvList>
        <KvRow label="Observação">{ev.note ?? '—'}</KvRow>
        <KvRow label="Registrada em">{formatDateTime(ev.ts)}</KvRow>
        <KvRow label="Etapa"><span className="mono">{ev.step_id ?? '—'}</span></KvRow>
      </KvList>
    </Dialog>
  );
}
