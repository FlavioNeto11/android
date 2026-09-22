import {
  CircleCheck, CircleHelp, CircleSlash, CircleX, LoaderCircle, Send, Radio, Hourglass, type LucideIcon,
} from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { api } from '../../api/client';
import type { Command, CommandState, InstanceAction } from '../../api/types';
import { Button } from '../../components/Button';
import { StatusBadge } from '../../components/StatusBadge';
import { cx } from '../../lib/format';
import { metaOf, type StatusMeta } from '../../lib/status';
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
  uncertain: { label: 'Desconhecido', tone: 'warning', icon: CircleHelp, description: 'Acabou sem que se saiba o efeito. Nada será repetido sozinho.' },
};

const MARCOS: { campo: keyof Command; rotulo: string; icone: LucideIcon }[] = [
  { campo: 'created_at', rotulo: 'criado', icone: Hourglass },
  { campo: 'dispatched_at', rotulo: 'enviado', icone: Send },
  { campo: 'acked_at', rotulo: 'recebido', icone: Radio },
  { campo: 'started_at', rotulo: 'iniciado', icone: LoaderCircle },
  { campo: 'finished_at', rotulo: 'concluído', icone: CircleCheck },
];

export function rotuloDoVerbo(verb: string): string {
  return ACTION_META[verb as InstanceAction]?.label ?? verb;
}

function hora(iso: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleTimeString(undefined, { hour12: false });
}

/** Quanto tempo faz, em texto curto. Para um comando em voo é a informação que diz se ele travou. */
export function idade(iso: string | null, agora = Date.now()): string {
  if (!iso) return '';
  const ms = agora - new Date(iso).getTime();
  if (Number.isNaN(ms) || ms < 0) return '';
  const s = Math.round(ms / 1000);
  if (s < 60) return `há ${s} s`;
  const m = Math.floor(s / 60);
  return m < 60 ? `há ${m} min` : `há ${Math.floor(m / 60)} h`;
}

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
      <span>{rotuloDoVerbo(cmd.verb)}</span>
      <span className={styles.commandAge}>{idade(desde)}</span>
      <CancelCommandButton cmd={cmd} />
    </span>
  );
}

/**
 * "Comandos recentes" do aparelho, com as duas saídas do `uncertain`: verificar pelo estado real e decidir.
 *
 * É a tela que faltava. Sem ela, "o desfecho continua sendo registrado no comando" apontava para lugar nenhum:
 * nenhuma parte da interface lia `GET /api/commands`.
 */
export function CommandHistory({ instanceId }: { instanceId: string }) {
  const [comandos, setComandos] = useState<Command[] | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const [ocupado, setOcupado] = useState<string | null>(null);
  // Recarrega quando o último comando daquele aparelho muda: o evento já chega ao store por WebSocket.
  const ultimo = useAppStore((s) => s.lastCommand[instanceId]);

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
        : { tone: 'info', title: `${rotuloDoVerbo(cmd.verb)} em ${instanceId}: continua desconhecido`,
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
    setOcupado(cmd.id);
    try {
      await api.resolveCommand(cmd.id, { outcome, note: `decidido no painel a partir de ${instanceId}` });
      toast({ tone: 'info', title: `${rotuloDoVerbo(cmd.verb)} em ${instanceId}: marcado como `
        + `${outcome === 'succeeded' ? 'concluído' : 'falhou'}` });
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
      <h3 className={styles.commandGroupTitle}>Comandos recentes</h3>
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
              <span className={styles.commandVerb}>{rotuloDoVerbo(cmd.verb)}</span>
              <span className={styles.commandAge}>{idade(cmd.finished_at ?? cmd.created_at)}</span>
              {cmd.worker_id ? <span className={styles.commandAge}>{cmd.worker_id}</span> : null}
            </div>
            {cmd.reason ? <p className={styles.commandReason}>{cmd.reason}</p> : null}
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
