import {
  AppWindow, Eraser, House, PackagePlus, Play, Plus, RotateCcw, Square, SquareStack, Undo2, type LucideIcon,
} from 'lucide-react';
import { createElement } from 'react';
import { create } from 'zustand';
import { api } from '../../api/client';
import type { InstanceAction, InstanceActionParams } from '../../api/types';
import { confirm } from '../../components/Confirm';
import { plural } from '../../lib/format';
import { toast, toastError } from '../../store/toasts';

export const ACTION_META: Record<InstanceAction, { label: string; done: string; icon: LucideIcon }> = {
  create: { label: 'Criar AVD', done: 'Criação do AVD solicitada', icon: Plus },
  start: { label: 'Iniciar', done: 'Inicialização solicitada', icon: Play },
  stop: { label: 'Parar', done: 'Parada solicitada', icon: Square },
  restart: { label: 'Reiniciar', done: 'Reinício solicitado', icon: RotateCcw },
  reset: { label: 'Resetar dados', done: 'Reset de dados solicitado', icon: Eraser },
  install_apk: { label: 'Instalar APK', done: 'Instalação do APK solicitada', icon: PackagePlus },
  open_app: { label: 'Abrir app', done: 'Abertura do app solicitada', icon: AppWindow },
  home: { label: 'Início', done: 'Tecla Início enviada', icon: House },
  back: { label: 'Voltar', done: 'Tecla Voltar enviada', icon: Undo2 },
  recents: { label: 'Recentes', done: 'Tecla Recentes enviada', icon: SquareStack },
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
    ),
  });
  return confirmed;
}

/** Ação em UMA instância. `reset` sempre passa por confirmação e envia `{confirm:true}`. */
export async function runInstanceAction(id: string, action: InstanceAction, params?: InstanceActionParams): Promise<boolean> {
  if (useBusyStore.getState().busy[id]) return false;
  let finalParams = params;
  if (action === 'reset') {
    if (!(await confirmReset([id]))) return false;
    finalParams = { ...params, confirm: true };
  }
  setBusy(id, action);
  try {
    await api.instanceAction(id, action, finalParams);
    toast({ tone: 'success', title: `${ACTION_META[action].done} — ${id}`, key: `act-${id}` });
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
    if (rejected.length === 0) {
      toast({ tone: 'success', title: `${label}: ${plural(accepted.length, 'instância aceita', 'instâncias aceitas')}` });
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
