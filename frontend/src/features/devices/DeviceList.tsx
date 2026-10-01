import { Eye, Maximize2, Store } from 'lucide-react';
import { memo, useEffect, useRef, type MouseEvent } from 'react';
import type { Instance, PersonaOnDevice } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Button } from '../../components/Button';
import { Checkbox } from '../../components/Field';
import { StatusBadge } from '../../components/StatusBadge';
import { cx } from '../../lib/format';
import { evidenciaLegivel } from '../../lib/rotulos';
import { metaOf, STEP_STATUS } from '../../lib/status';
import { useAppStore } from '../../store/app';
import { ACTION_META, comandoAbertoDe, motivoDoComando, runInstanceAction, useBusyStore } from './actions';
import { CommandSummary } from './CommandTrail';
import { personasDoCartao, rotuloDaPersona } from './DeviceCard';
import { MOTIVO_SERVIDOR_SEM_RESPOSTA, primaryActionFor, serverHintOf } from './deviceState';
import { aparelhoDesconhecido, seloDoAparelho } from './selos';
import styles from './Devices.module.css';

interface DeviceRowProps {
  instance: Instance;
  appName: string | null;
  personas?: readonly PersonaOnDevice[] | null;
  selected: boolean;
  focused: boolean;
  onToggle: (id: string) => void;
  onRange: (id: string) => void;
  onOpen: (id: string) => void;
}

/** Uma linha da visão em Lista: os mesmos dados do cartão, numa linha só, sem miniatura. */
const DeviceRow = memo(function DeviceRow({ instance, appName, personas: vinculadas, selected, focused, onToggle, onRange, onOpen }: DeviceRowProps) {
  const { id, state, current } = instance;
  const workers = useAppStore((s) => s.workers);
  const server = serverHintOf(instance, workers);
  const selo = seloDoAparelho(instance, workers);
  // RF-40: o mesmo do cartão — sem servidor, o verbo do estado guardado não é oferecido como se valesse.
  const semServidor = aparelhoDesconhecido(instance, workers) ? MOTIVO_SERVIDOR_SEM_RESPOSTA : null;
  const personas = personasDoCartao(vinculadas);
  const busyAction = useBusyStore((s) => s.busy[id]);
  const comandoAberto = useAppStore((s) => comandoAbertoDe(s.lastCommand[id]));
  const ultimo = useAppStore((s) => s.lastCommand[id]);
  const comando = comandoAberto ?? (ultimo?.state === 'uncertain' ? ultimo : undefined);
  const loja = instance.kind === 'store';
  const primary = primaryActionFor(state, instance);
  const ref = useRef<HTMLTableRowElement>(null);
  useEffect(() => {
    if (!focused) return undefined;
    const t = window.setTimeout(() => ref.current?.scrollIntoView?.({ block: 'nearest' }), 80);
    return () => window.clearTimeout(t);
  }, [focused]);

  const onClickCapture = (e: MouseEvent) => {
    if (!(e.ctrlKey || e.metaKey || e.shiftKey) || loja) return;
    e.preventDefault();
    e.stopPropagation();
    if (e.shiftKey) onRange(id);
    else onToggle(id);
  };

  return (
    <tr ref={ref} className={cx(styles.linha, selected && styles.linhaSelecionada, focused && styles.linhaFoco)}
        data-instance-row={id} aria-label={`Aparelho ${id} — ${selo.label}`} onClickCapture={onClickCapture}>
      <td className={styles.colSel}>
        {loja ? (
          <Badge size="sm" tone="accent" icon={Store} title="Aparelho-loja: guarda o aplicativo oficial da Play Store. Não executa tarefas.">Loja</Badge>
        ) : (
          <Checkbox checked={selected} onChange={() => onToggle(id)} aria-label={`Selecionar ${id}`} />
        )}
      </td>
      <th scope="row" className={styles.colId}><span className={styles.instId}>{id}</span></th>
      <td className={styles.colEstado}><StatusBadge meta={selo} size="sm" srPrefix="Estado" /></td>
      <td className={styles.colOpc}><span className="truncate">{server?.name ?? 'Central'}</span></td>
      <td className={styles.colConta}>
        {personas.length > 0 ? (
          <span className="truncate" title={personas.map((p) => `${p.name}${p.username ? ` (@${p.username})` : ''}`).join(' · ')}>
            {rotuloDaPersona(personas[0]!)}{personas.length > 1 ? ` +${personas.length - 1}` : ''}
          </span>
        ) : instance.account_label ? (
          <span className="truncate" title={instance.account_label}>{instance.account_label}</span>
        ) : null}
        {instance.account_evidence ? (
          <span className={styles.linhaEvidencia} title={`Conta confirmada na tela. ${evidenciaLegivel(instance.account_evidence)}`}>
            <Eye size={11} aria-hidden />
            <span className="sr-only">Conta confirmada na tela</span>
          </span>
        ) : null}
      </td>
      <td className={styles.colOpc}><span className="truncate" title={appName ?? undefined}>{appName}</span></td>
      <td className={styles.colOpc}>
        {comando ? <CommandSummary cmd={comando} /> : current?.step_title ? (
          <span className={styles.linhaAtividade} title={current.step_title}>
            {current.step_status ? <StatusBadge meta={metaOf(STEP_STATUS, current.step_status)} size="sm" plain /> : null}
            <span className="truncate">{current.step_title}</span>
            <span className={styles.frameAge}>{current.steps_done}/{current.steps_total}</span>
          </span>
        ) : null}
      </td>
      <td className={styles.colAcoes}>
        {primary ? (
          <Button size="sm" variant="secondary" icon={ACTION_META[primary].icon}
                  loading={busyAction === primary || comandoAberto?.verb === primary}
                  disabledReason={semServidor ?? motivoDoComando(comandoAberto, id, primary)}
                  onClick={() => void runInstanceAction(id, primary)}>
            {state === 'error' ? 'Tentar novamente' : ACTION_META[primary].label}
          </Button>
        ) : null}
        <Button size="sm" variant="ghost" icon={Maximize2} iconOnly label={`Abrir ${id}`} onClick={() => onOpen(id)} />
      </td>
    </tr>
  );
});

export function DeviceList(props: {
  instances: readonly Instance[];
  appNames: ReadonlyMap<string, string>;
  porAparelho: ReadonlyMap<string, readonly PersonaOnDevice[]>;
  selectedSet: ReadonlySet<string>;
  focusId: string | null;
  onToggle: (id: string) => void;
  onRange: (id: string) => void;
  onOpen: (id: string) => void;
}) {
  return (
    <div className={styles.listaWrap}>
      <table className={styles.lista} aria-label="Aparelhos em lista">
        <thead>
          <tr>
            <th scope="col" className={styles.colSel}><span className="sr-only">Seleção</span></th>
            <th scope="col">Aparelho</th>
            <th scope="col">Estado</th>
            <th scope="col" className={styles.colOpc}>Servidor</th>
            <th scope="col">Conta</th>
            <th scope="col" className={styles.colOpc}>Aplicativo</th>
            <th scope="col" className={styles.colOpc}>Atividade</th>
            <th scope="col" className={styles.colAcoes}><span className="sr-only">Ações</span></th>
          </tr>
        </thead>
        <tbody>
          {props.instances.map((inst) => (
            <DeviceRow key={inst.id} instance={inst}
                       appName={inst.app_id ? props.appNames.get(inst.app_id) ?? inst.app_id : null}
                       personas={props.porAparelho.get(inst.id) ?? null}
                       selected={props.selectedSet.has(inst.id)} focused={props.focusId === inst.id}
                       onToggle={props.onToggle} onRange={props.onRange} onOpen={props.onOpen} />
          ))}
        </tbody>
      </table>
    </div>
  );
}
