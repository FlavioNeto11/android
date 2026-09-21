import {
  AppWindow, Eraser, House, Moon, PackagePlus, Play, Plus, RotateCcw, Square, SquareStack, Sunrise, Undo2, type LucideIcon,
} from 'lucide-react';
import { createElement } from 'react';
import { create } from 'zustand';
import { api } from '../../api/client';
import type { Command, CommandState, InstanceAction, InstanceActionParams } from '../../api/types';
import { confirm } from '../../components/Confirm';
import { plural } from '../../lib/format';
import { useAppStore } from '../../store/app';
import { toast, toastError } from '../../store/toasts';

/**
 * `done` é o que se diz ao ENVIAR (sempre "solicitado"); `confirmed` é o que se diz quando o aparelho confirmou.
 * A distinção existe porque a interface mostrava toast verde no `202` e chamava de sucesso o que só tinha sido
 * aceito — em aparelho de outra máquina, "Resetar dados" era recusado no fundo e ficava indistinguível de êxito.
 */
export const ACTION_META: Record<InstanceAction, { label: string; done: string; confirmed: string; icon: LucideIcon }> = {
  create: { label: 'Criar AVD', done: 'Criação do AVD solicitada', confirmed: 'AVD criado', icon: Plus },
  start: { label: 'Iniciar', done: 'Inicialização solicitada', confirmed: 'Iniciada', icon: Play },
  stop: { label: 'Parar', done: 'Parada solicitada', confirmed: 'Parada', icon: Square },
  restart: { label: 'Reiniciar', done: 'Reinício solicitado', confirmed: 'Reiniciada', icon: RotateCcw },
  reset: { label: 'Resetar dados', done: 'Reset de dados solicitado', confirmed: 'Dados resetados', icon: Eraser },
  install_apk: { label: 'Instalar APK', done: 'Instalação do APK solicitada', confirmed: 'APK instalado', icon: PackagePlus },
  open_app: { label: 'Abrir app', done: 'Abertura do app solicitada', confirmed: 'App aberto', icon: AppWindow },
  home: { label: 'Início', done: 'Tecla Início enviada', confirmed: 'Tecla Início confirmada', icon: House },
  back: { label: 'Voltar', done: 'Tecla Voltar enviada', confirmed: 'Tecla Voltar confirmada', icon: Undo2 },
  recents: { label: 'Recentes', done: 'Tecla Recentes enviada', confirmed: 'Tecla Recentes confirmada', icon: SquareStack },
  hibernate: { label: 'Hibernar', done: 'Hibernação solicitada', confirmed: 'Hibernada', icon: Moon },
  wake: { label: 'Acordar', done: 'Despertar solicitado', confirmed: 'Acordada', icon: Sunrise },
};

/** Requisições de ação em voo, para desabilitar botões e evitar cliques duplos. */
interface BusyStore {
  busy: Record<string, InstanceAction | undefined>;
  bulkBusy: InstanceAction | null;
}

export const useBusyStore = create<BusyStore>(() => ({ busy: {}, bulkBusy: null }));

function setBusy(id: string, action: InstanceAction | undefined): void {
  useBusyStore.setState((s) => ({ busy: { ...s.busy, [id]: action } }));
}

async function confirmReset(ids: readonly string[]): Promise<boolean> {
  const list = ids.join(', ');
  const mapa = useAppStore.getState().instances;
  const temLoja = ids.some((i) => mapa[i]?.kind === 'store');
  const { confirmed } = await confirm({
    title: ids.length === 1 ? `Resetar dados de ${ids[0]}?` : `Resetar dados de ${ids.length} instâncias?`,
    danger: true,
    confirmLabel: 'Apagar dados e resetar',
    cancelLabel: 'Cancelar',
    body: createElement(
      'div',
      null,
      createElement('p', null, 'Isto ', createElement('strong', null, 'apaga todos os dados'), ' do emulador: apps instalados, contas conectadas e configurações. Não é possível desfazer.'),
      createElement('p', { style: { marginTop: 8 } }, createElement('strong', null, 'Afetadas: '), list),
      // A loja guarda o login da conta Google na Play Store: resetá-la obriga a entrar de novo, à mão, na janela dela.
      temLoja ? createElement('p', { style: { marginTop: 8 } },
        createElement('strong', null, 'Atenção: '),
        'este é o aparelho-loja. O reset apaga também o login da conta Google na Play Store, que terá de ser refeito '
        + 'à mão, na janela do emulador.') : null,
    ),
  });
  return confirmed;
}

const TERMINAL: readonly CommandState[] = ['succeeded', 'failed', 'uncertain', 'rejected', 'cancelled'];
/** Teto de espera pelo desfecho. Boot a frio passa de 100 s, então é generoso de propósito. */
const ESPERA_MAX_MS = 210_000;
const INTERVALO_MS = 1_500;

/** Acompanha o comando até o desfecho e conta a verdade — inclusive "não sei". */
async function relatarDesfecho(commandId: string, id: string, action: InstanceAction): Promise<void> {
  const limite = Date.now() + ESPERA_MAX_MS;
  const meta = ACTION_META[action];
  for (;;) {
    let cmd: Command;
    try {
      cmd = await api.command(commandId);
    } catch {
      return;                       // perdemos o acompanhamento; o histórico do comando continua no backend
    }
    if (TERMINAL.includes(cmd.state)) {
      if (cmd.state === 'succeeded') {
        toast({ tone: 'success', title: `${meta.confirmed} — ${id}`, key: `cmd-${id}` });
      } else if (cmd.state === 'uncertain') {
        toast({
          tone: 'warning', key: `cmd-${id}`,
          title: `${meta.label} em ${id}: resultado desconhecido`,
          details: cmd.reason ? [cmd.reason] : undefined,
          hint: 'Nada será repetido automaticamente. Confira o aparelho antes de tentar de novo.',
        });
      } else {
        const rotulo = cmd.state === 'rejected' ? 'recusado' : cmd.state === 'cancelled' ? 'cancelado' : 'falhou';
        toast({
          tone: 'danger', key: `cmd-${id}`,
          title: `${meta.label} em ${id}: ${rotulo}`,
          details: cmd.reason ? [cmd.reason] : undefined,
          ...(cmd.state === 'rejected' ? { hint: 'Nada foi executado no aparelho.' } : {}),
        });
      }
      return;
    }
    if (Date.now() > limite) {
      toast({
        tone: 'warning', key: `cmd-${id}`,
        title: `${meta.label} em ${id}: ainda em andamento`,
        hint: 'Parei de acompanhar por aqui; o desfecho continua sendo registrado no comando.',
      });
      return;
    }
    await new Promise((r) => setTimeout(r, INTERVALO_MS));
  }
}

/** Ação em UMA instância. `reset` sempre passa por confirmação e envia `{confirm:true}`. */
export async function runInstanceAction(id: string, action: InstanceAction, params?: InstanceActionParams): Promise<boolean> {
  if (useBusyStore.getState().busy[id]) return false;
  let finalParams: InstanceActionParams = { ...params };
  if (action === 'reset') {
    if (!(await confirmReset([id]))) return false;
    finalParams.confirm = true;
  }
  // Chave por clique: se a rede duplicar a requisição, o backend devolve o MESMO comando em vez de agir duas vezes.
  finalParams.idempotency_key ??= `${id}:${action}:${crypto.randomUUID()}`;
  setBusy(id, action);
  try {
    const aceito = await api.instanceAction(id, action, finalParams);
    // Tom neutro: a única coisa verdadeira neste instante é que o pedido foi aceito.
    toast({ tone: 'info', title: `${ACTION_META[action].done} — ${id}`, key: `act-${id}` });
    void relatarDesfecho(aceito.command_id, id, action);
    return true;
  } catch (e) {
    toastError(`Não foi possível executar “${ACTION_META[action].label}” em ${id}`, e);
    return false;
  } finally {
    setBusy(id, undefined);
  }
}

/** Ação em lote: o backend devolve quem aceitou e quem rejeitou (com motivo) — mostramos os dois. */
export async function runBulkAction(ids: readonly string[], action: InstanceAction, params?: InstanceActionParams): Promise<void> {
  if (ids.length === 0 || useBusyStore.getState().bulkBusy) return;
  let finalParams = params;
  if (action === 'reset') {
    if (!(await confirmReset(ids))) return;
    finalParams = { ...params, confirm: true };
  }
  useBusyStore.setState({ bulkBusy: action });
  try {
    const res = await api.bulk({ ids: [...ids], action, ...(finalParams ? { params: finalParams } : {}) });
    const accepted = Array.isArray(res?.accepted) ? res.accepted : [];
    const rejected = Array.isArray(res?.rejected) ? res.rejected : [];
    const label = ACTION_META[action].label;
    // Cada aparelho tem seu comando: o desfecho de um não fala pelo do outro.
    for (const c of res?.commands ?? []) {
      if (!c.deduplicated && accepted.includes(c.id)) void relatarDesfecho(c.command_id, c.id, action);
    }
    if (rejected.length === 0) {
      // `info`, não `success`: neste instante só se sabe que foram aceitas.
      toast({ tone: 'info', title: `${label}: ${plural(accepted.length, 'instância aceita', 'instâncias aceitas')}`,
              hint: 'O desfecho de cada aparelho aparece à medida que for confirmado.' });
    } else {
      toast({
        tone: accepted.length > 0 ? 'warning' : 'danger',
        title: `${label}: ${accepted.length} aceita(s), ${rejected.length} rejeitada(s)`,
        details: rejected.map((r) => `${r.id}: ${r.reason}`),
        hint: 'Resolva o motivo indicado e repita a ação apenas nas instâncias rejeitadas.',
      });
    }
  } catch (e) {
    toastError(`Não foi possível executar “${ACTION_META[action].label}” em lote`, e);
  } finally {
    useBusyStore.setState({ bulkBusy: null });
  }
}
