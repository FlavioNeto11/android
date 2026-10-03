import {
  CircleCheck, CircleHelp, CircleSlash, CircleX, LoaderCircle, Send, Radio, Hourglass, type LucideIcon,
} from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { api } from '../../api/client';
import type { Command, CommandState, InstanceAction } from '../../api/types';
import { Button } from '../../components/Button';
import { confirm } from '../../components/Confirm';
import { StatusBadge } from '../../components/StatusBadge';
import { cx } from '../../lib/format';
import { metaOf, type StatusMeta } from '../../lib/status';
import { rotuloDoComando } from '../../lib/rotulos';
import { tempoRelativo, useNow } from '../../lib/time';
import { useAppStore } from '../../store/app';
import { toast, toastError } from '../../store/toasts';
import { ACTION_META, cancelarComando, comandoAbertoDe } from './actions';
import styles from './Devices.module.css';

/**
 * A trilha do comando na interface: criado → enviado → recebido → iniciado → concluído.
 *
 * O backend sempre soube contar essa história (as marcas de tempo estão no comando), mas ela morria no store:
 * `lastCommand` não era renderizado em lugar nenhum e não havia histórico por aparelho. Depois do toast, um
 * comando `uncertain` ficava invisível — o de 21/09 sumiu da tela e continuou aberto no banco por um dia.
 */
export const COMMAND_STATE: Record<CommandState, StatusMeta> = {
  created: { label: 'Criado', tone: 'muted', icon: Hourglass, description: 'Gravado aqui; ainda não saiu.' },
  dispatched: { label: 'Enviado', tone: 'info', icon: Send, description: 'Saiu daqui; sem confirmação de recebimento.' },
  acked: { label: 'Recebido', tone: 'info', icon: Radio, description: 'A outra ponta confirmou o recebimento.' },
  running: { label: 'Executando', tone: 'info', icon: LoaderCircle, spin: true, description: 'A execução começou.' },
  cancel_requested: { label: 'Cancelando', tone: 'warning', icon: CircleSlash, description: 'Cancelamento pedido; ainda não confirmado.' },
  succeeded: { label: 'Concluído', tone: 'success', icon: CircleCheck, description: 'O efeito foi confirmado.' },
  failed: { label: 'Falhou', tone: 'danger', icon: CircleX, description: 'O desfecho é conhecido e é negativo.' },
  rejected: { label: 'Recusado', tone: 'neutral', icon: CircleSlash, description: 'Recusado antes de agir: o aparelho ficou intacto.' },
  cancelled: { label: 'Cancelado', tone: 'neutral', icon: CircleSlash, description: 'Cancelado antes de concluir.' },
  uncertain: { label: 'Sem resposta', tone: 'warning', icon: CircleHelp, description: 'Acabou sem que se saiba o efeito. Nada será repetido sozinho.' },
};

const MARCOS: { campo: keyof Command; rotulo: string; icone: LucideIcon }[] = [
  { campo: 'created_at', rotulo: 'criado', icone: Hourglass },
  { campo: 'dispatched_at', rotulo: 'enviado', icone: Send },
  { campo: 'acked_at', rotulo: 'recebido', icone: Radio },
  { campo: 'started_at', rotulo: 'iniciado', icone: LoaderCircle },
  { campo: 'finished_at', rotulo: 'concluído', icone: CircleCheck },
];

export function rotuloDoVerbo(verb: string): string {
  // Nunca o identificador cru ("app.distribute"): o que não é ação do aparelho vem do mapa de `lib/rotulos`.
  return ACTION_META[verb as InstanceAction]?.label ?? rotuloDoComando(verb);
}

function hora(iso: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleTimeString(undefined, { hour12: false });
}

/**
 * Quanto tempo faz, numa unidade só ("há 12 s", "há 3 min", "há 6 dias"), pelo formatador único (`lib/time`). Para um
 * comando em voo é o que diz se travou. Sem instante (ou no futuro) não diz nada: a trilha não mostra travessão.
 */
export function idade(iso: string | null, agora = Date.now()): string {
  if (!iso) return '';
  const ms = agora - new Date(iso).getTime();
  if (Number.isNaN(ms) || ms < 0) return '';
  return tempoRelativo(iso, agora);
}

/**
 * Idade viva: assina o relógio de 1 s só aqui, não no cartão inteiro. Calculada no render com `Date.now()`, ela
 * congelava — um comando em voo dizia "há 5 s" uma hora depois, e é justamente essa idade que diz se ele travou.
 */
function Idade({ iso }: { iso: string | null }) {
  const now = useNow();
  return <>{idade(iso, now)}</>;
}

/** Texto da confirmação ao decidir um comando `uncertain`: mesmo rito de `runs/runActions.ts::resolveObjective`. */
const DECISAO: Record<'succeeded' | 'failed', { title: string; confirmLabel: string; danger: boolean; feito: string }> = {
  succeeded: { title: 'Marcar como concluído?', confirmLabel: 'Sim, está concluído', danger: false, feito: 'concluído' },
  failed: { title: 'Marcar como falhou?', confirmLabel: 'Sim, falhou', danger: true, feito: 'falhou' },
};

/** A trilha inteira de um comando, marco a marco. Marco sem hora aparece apagado — é informação, não falha. */
export function CommandTrail({ cmd }: { cmd: Command }) {
  return (
    <ol className={styles.trail} aria-label={`Trilha do comando ${cmd.id}`}>
      {MARCOS.map(({ campo, rotulo, icone: Icone }) => {
        const valor = cmd[campo] as string | null;
        return (
          <li key={rotulo} className={cx(styles.trailStep, !valor && styles.trailStepVazio)}>
            <Icone size={12} aria-hidden />
            <span>{rotulo}</span>
            <span className="mono">{hora(valor)}</span>
          </li>
        );
      })}
    </ol>
  );
}

/**
 * "Cancelar" enquanto o comando está ABERTO — a ação que existia no protocolo e em lugar nenhum da interface.
 *
 * Some assim que o pedido é feito (`cancel_requested`): pedir de novo não adianta nada para quem olha, e o
 * rótulo "Cancelando" já conta o que está acontecendo. O desfecho continua vindo de quem executa.
 */
export function CancelCommandButton({ cmd, onDone }: { cmd: Command; onDone?: () => void }) {
  const [ocupado, setOcupado] = useState(false);
  const pedir = async () => {
    setOcupado(true);
    try {
      await cancelarComando(cmd);
      onDone?.();
    } finally {
      setOcupado(false);
    }
  };
  if (!comandoAbertoDe(cmd) || cmd.state === 'cancel_requested') return null;
  return (
    <Button
      size="sm" variant="dangerGhost" icon={CircleSlash} loading={ocupado}
      title={`Pedir o cancelamento de “${rotuloDoVerbo(cmd.verb)}” em ${cmd.instance_id}`}
      onClick={(e) => { e.stopPropagation(); void pedir(); }}
    >
      Cancelar
    </Button>
  );
}

/** Resumo de uma linha para o cartão: verbo, estado, idade — e a saída, enquanto o comando ainda age. */
export function CommandSummary({ cmd }: { cmd: Command }) {
  const meta = metaOf(COMMAND_STATE, cmd.state);
  const desde = cmd.finished_at ?? cmd.started_at ?? cmd.dispatched_at ?? cmd.created_at;
  return (
    <span className={styles.commandLine} title={cmd.reason ?? meta.description}>
      <StatusBadge meta={meta} size="sm" srPrefix="Comando" />
      <span className={styles.commandText} title={`Identificador: ${cmd.verb}`}>{rotuloDoVerbo(cmd.verb)}</span>
      <span className={styles.commandAge}><Idade iso={desde} /></span>
      <CancelCommandButton cmd={cmd} />
    </span>
  );
}

/**
 * O `uncertain` que um comando mais novo deixou para trás (`comandoSemDesfecho`). Ele continua sem desfecho no banco,
 * então fica no cartão, discreto e dito como ANTERIOR: a linha de cima é o último comando de verdade. Verificar ou
 * decidir é nos "Comandos recentes" do aparelho.
 */
export function ComandoAnteriorSemResposta({ cmd }: { cmd: Command }) {
  const desde = cmd.finished_at ?? cmd.started_at ?? cmd.dispatched_at ?? cmd.created_at;
  return (
    <span className={cx(styles.commandLine, styles.commandAnterior)}
          title={`${COMMAND_STATE.uncertain.description} Para verificar ou decidir, abra os Comandos recentes do aparelho.`}>
      <CircleHelp size={12} aria-hidden />
      <span className={styles.commandText}>Anterior sem resposta: {rotuloDoVerbo(cmd.verb)}</span>
      <span className={styles.commandAge}><Idade iso={desde} /></span>
    </span>
  );
}

/**
 * "Comandos recentes" do aparelho, com as duas saídas do `uncertain`: verificar pelo estado real e decidir.
 *
 * É a tela que faltava. Sem ela, "o desfecho continua sendo registrado no comando" apontava para lugar nenhum:
 * nenhuma parte da interface lia `GET /api/commands`.
 */
export function CommandHistory({ instanceId, semTitulo = false }: {
  instanceId: string;
  /** Dentro de uma seção que já se chama "Comandos recentes" (o Foco), o título próprio seria repetido. */
  semTitulo?: boolean;
}) {
  const [comandos, setComandos] = useState<Command[] | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const [ocupado, setOcupado] = useState<string | null>(null);
  // Recarrega quando o último comando daquele aparelho muda: o evento já chega ao store por WebSocket.
  const ultimo = useAppStore((s) => s.lastCommand[instanceId]);
  const workers = useAppStore((s) => s.workers);

  const carregar = useCallback(async () => {
    try {
      setComandos(await api.commands(instanceId, 20));
      setErro(null);
    } catch (e) {
      setErro(e instanceof Error ? e.message : 'não foi possível ler o histórico de comandos');
    }
  }, [instanceId]);

  useEffect(() => { void carregar(); }, [carregar, ultimo?.id, ultimo?.state]);

  const verificar = async (cmd: Command) => {
    setOcupado(cmd.id);
    try {
      const r = await api.verifyCommand(cmd.id);
      toast(r.changed
        ? { tone: 'success', title: `${rotuloDoVerbo(cmd.verb)} em ${instanceId}: ${metaOf(COMMAND_STATE, r.command.state).label.toLowerCase()}`,
            details: r.command.reason ? [r.command.reason] : undefined }
        : { tone: 'info', title: `${rotuloDoVerbo(cmd.verb)} em ${instanceId}: continua sem resposta`,
            hint: r.verifiable
              ? 'O estado do aparelho não comprova o efeito. Confira o aparelho e use “Marcar como…”.'
              : 'Este verbo não é verificável pelo estado do aparelho: só uma pessoa pode decidir.' });
      await carregar();
    } catch (e) {
      toastError('Não foi possível verificar o comando', e);
    } finally {
      setOcupado(null);
    }
  };

  const resolver = async (cmd: Command, outcome: 'succeeded' | 'failed') => {
    const copy = DECISAO[outcome];
    const verbo = rotuloDoVerbo(cmd.verb);
    // A decisão fecha o comando para sempre e ninguém mais o verifica: sem confirmação, um clique errado gravava
    // um desfecho que não aconteceu — enquanto o mesmo gesto nos objetivos da execução sempre perguntou (P2.6).
    const { confirmed, note } = await confirm({
      title: copy.title,
      danger: copy.danger,
      confirmLabel: copy.confirmLabel,
      body: (
        <div>
          <p>
            {outcome === 'succeeded'
              ? `Você confirma que verificou ${instanceId} e que “${verbo}” fez efeito. O comando passa a contar como concluído.`
              : `“${verbo}” em ${instanceId} é encerrado como falha. O aparelho não é tocado.`}
          </p>
          <p style={{ marginTop: 8 }}>A decisão fica registrada com este aparelho e não é verificada de novo.</p>
        </div>
      ),
      note: { label: 'Observação para o registro', placeholder: 'Ex.: conferi no aparelho e o emulador está ligado' },
    });
    if (!confirmed) return;
    setOcupado(cmd.id);
    try {
      // Só o texto da pessoa vai na nota: o "de onde" (`origin`) o backend compõe no motivo. No prefixo, um id de
      // aparelho fora do padrão fazia a triagem de credencial recusar a decisão inteira.
      const texto = note?.trim();
      await api.resolveCommand(cmd.id, { outcome, origin: 'panel', ...(texto ? { note: texto } : {}) });
      toast({ tone: 'info', title: `${verbo} em ${instanceId}: marcado como ${copy.feito}` });
      await carregar();
    } catch (e) {
      toastError('Não foi possível resolver o comando', e);
    } finally {
      setOcupado(null);
    }
  };

  const incertos = (comandos ?? []).filter((c) => c.state === 'uncertain');

  return (
    <section className={styles.commandGroup} aria-label="Comandos recentes">
      {semTitulo ? null : <h3 className={styles.commandGroupTitle}>Comandos recentes</h3>}
      {erro ? <p className={styles.commandHint}>{erro}</p> : null}
      {comandos !== null && comandos.length === 0 ? (
        <p className={styles.commandHint}>Nenhum comando registrado para este aparelho.</p>
      ) : null}
      {incertos.length > 0 ? (
        <p className={styles.commandHint}>
          {incertos.length === 1 ? 'Há 1 comando sem desfecho' : `Há ${incertos.length} comandos sem desfecho`}
          {' '}— nada será repetido sozinho até alguém (ou a verificação) fechar.
        </p>
      ) : null}
      <ul className={styles.commandList}>
        {(comandos ?? []).map((cmd) => (
          <li key={cmd.id} className={cx(styles.commandItem, cmd.state === 'uncertain' && styles.commandItemAberto)}>
            <div className={styles.commandHead}>
              <StatusBadge meta={metaOf(COMMAND_STATE, cmd.state)} size="sm" srPrefix="Comando" />
              <span className={cx(styles.commandVerb, styles.commandText)} title={`Identificador: ${cmd.verb}`}>{rotuloDoVerbo(cmd.verb)}</span>
              <span className={styles.commandAge}><Idade iso={cmd.finished_at ?? cmd.created_at} /></span>
              {cmd.worker_id ? (
                <span className={cx(styles.commandAge, styles.commandText)} title={`Identificador: ${cmd.worker_id}`}>{workers[cmd.worker_id]?.name ?? cmd.worker_id}</span>
              ) : null}
            </div>
            {cmd.reason ? <p className={styles.commandReason}>{cmd.reason}</p> : null}
            <LogDoEmulador cauda={cmd.emulator_log ?? null} />
            <CommandTrail cmd={cmd} />
            {comandoAbertoDe(cmd) ? (
              <div className={styles.commandActions}>
                <CancelCommandButton cmd={cmd} onDone={() => void carregar()} />
              </div>
            ) : null}
            {cmd.state === 'uncertain' ? (
              <div className={styles.commandActions}>
                <Button size="sm" variant="secondary" loading={ocupado === cmd.id}
                        onClick={() => void verificar(cmd)}>
                  Verificar agora
                </Button>
                <Button size="sm" variant="outline" disabled={ocupado === cmd.id}
                        onClick={() => void resolver(cmd, 'succeeded')}>
                  Marcar como concluído
                </Button>
                <Button size="sm" variant="dangerGhost" disabled={ocupado === cmd.id}
                        onClick={() => void resolver(cmd, 'failed')}>
                  Marcar como falhou
                </Button>
              </div>
            ) : null}
          </li>
        ))}
      </ul>
    </section>
  );
}

/**
 * O fim do log do emulador, quando o desfecho veio com ele.
 *
 * Existe porque o log do emulador REMOTO não tinha contraparte nenhuma: quando um boot da outra máquina falhava
 * ou ficava incerto, o operador via uma frase e o log ficava lá, fora de alcance. Agora a cauda vem no desfecho
 * (já sem o que parecer segredo) e é a mesma coisa para aparelho daqui e de lá.
 */
function LogDoEmulador({ cauda }: { cauda: string | null }) {
  if (!cauda) return null;
  return (
    <details className={styles.logDetalhe}>
      <summary className={styles.logResumo}>Log do emulador (fim)</summary>
      <pre className={styles.logCauda}>{cauda}</pre>
    </details>
  );
}
