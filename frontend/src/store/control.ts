import { create } from 'zustand';
import { API_BASE, api } from '../api/client';
import type { Instance } from '../api/types';
import { useAppStore } from './app';
import { toast, toastError } from './toasts';

/**
 * Controle manual (lease). O `lease_id` vem da resposta de `control/take` e vive SÓ em memória:
 * um lease antigo guardado em disco seria errado após recarregar a página.
 * O evento `control.changed` não traz lease — quando o pedido volta `pending`, guardamos o lease e ele
 * passa a valer assim que a instância ficar com `control === 'user'` e `control_pending === false`.
 */
export interface Lease {
  leaseId: string;
  status: 'granted' | 'pending';
  /** Relógio local (ms) em que o lease foi obtido. */
  acquiredAt: number;
}

/**
 * Logo após o `take`, ainda podem chegar eventos emitidos ANTES do pedido (ex.: control='ai', pending=false).
 * Durante esta janela eles não derrubam o lease; se o lease for mesmo inválido, o próximo input devolve
 * `not_controller` e ele é descartado ali.
 */
const LEASE_GRACE_MS = 3000;

interface ControlStore {
  leases: Record<string, Lease>;
  busy: Record<string, boolean>;
  take: (instanceId: string) => Promise<void>;
  release: (instanceId: string) => Promise<void>;
  /** Reconciliação com o estado vindo do servidor (eventos/snapshot). */
  reconcile: (instance: Pick<Instance, 'id' | 'control' | 'control_pending'>) => void;
  drop: (instanceId: string) => void;
}

export const useControlStore = create<ControlStore>((set, get) => ({
  leases: {},
  busy: {},

  take: async (instanceId) => {
    if (get().busy[instanceId]) return;
    set((s) => ({ busy: { ...s.busy, [instanceId]: true } }));
    try {
      const res = await api.takeControl(instanceId);
      set((s) => ({ leases: { ...s.leases, [instanceId]: { leaseId: res.lease_id, status: res.status, acquiredAt: Date.now() } } }));
      // Reflete já a resposta; o evento `control.changed` confirma em seguida.
      if (res.status === 'granted') {
        useAppStore.getState().patchInstance(instanceId, { control: 'user', control_pending: false });
        toast({ tone: 'success', title: `Você está no controle de ${instanceId}`, message: 'A IA fica em espera até você devolver o controle.' });
      } else {
        useAppStore.getState().patchInstance(instanceId, { control_pending: true });
        toast({ tone: 'info', title: 'Controle solicitado', message: `Aguardando a IA concluir a ação atual em ${instanceId}…`, key: `take-${instanceId}` });
      }
    } catch (e) {
      toastError(`Não foi possível assumir o controle de ${instanceId}`, e);
    } finally {
      set((s) => ({ busy: { ...s.busy, [instanceId]: false } }));
    }
  },

  release: async (instanceId) => {
    const lease = get().leases[instanceId];
    if (!lease || get().busy[instanceId]) return;
    set((s) => ({ busy: { ...s.busy, [instanceId]: true } }));
    try {
      await api.releaseControl(instanceId, lease.leaseId);
      get().drop(instanceId);
      useAppStore.getState().patchInstance(instanceId, { control_pending: false });
      toast({ tone: 'success', title: `Controle de ${instanceId} devolvido`, message: 'A IA pode retomar a partir da tela atual.' });
    } catch (e) {
      toastError(`Não foi possível devolver o controle de ${instanceId}`, e);
    } finally {
      set((s) => ({ busy: { ...s.busy, [instanceId]: false } }));
    }
  },

  reconcile: (instance) => {
    const lease = get().leases[instance.id];
    if (!lease) return;
    if (instance.control === 'user' && !instance.control_pending) {
      if (lease.status !== 'granted') {
        set((s) => ({ leases: { ...s.leases, [instance.id]: { ...lease, status: 'granted' } } }));
        toast({ tone: 'success', title: `Você está no controle de ${instance.id}`, key: `take-${instance.id}` });
      }
      return;
    }
    // O servidor diz que o controle não é (nem será) nosso: o lease perdeu a validade.
    if (instance.control !== 'user' && !instance.control_pending) {
      if (Date.now() - lease.acquiredAt < LEASE_GRACE_MS) return;
      get().drop(instance.id);
      if (lease.status === 'granted') {
        toast({ tone: 'warning', title: `Controle de ${instance.id} encerrado`, message: 'O backend retomou o controle desta instância.' });
      }
    }
  },

  drop: (instanceId) =>
    set((s) => {
      if (!s.leases[instanceId]) return s;
      const { [instanceId]: _gone, ...rest } = s.leases;
      return { leases: rest };
    }),
}));

/** O usuário desta aba pode interagir agora? (lease concedido + servidor confirma o controle) */
export function userHasControl(instance: Pick<Instance, 'control' | 'control_pending'> | undefined, lease: Lease | undefined): boolean {
  return !!instance && !!lease && lease.status === 'granted' && instance.control === 'user' && !instance.control_pending;
}

/** Ao fechar/recarregar a aba, devolve os leases ativos para não deixar instâncias presas ao usuário. */
export function releaseAllLeasesOnUnload(): void {
  const { leases } = useControlStore.getState();
  for (const [instanceId, lease] of Object.entries(leases)) {
    try {
      void fetch(`${API_BASE}/instances/${encodeURIComponent(instanceId)}/control/release`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ lease_id: lease.leaseId }),
        keepalive: true,
        // Mesmo caminho de autenticação do resto do painel: sem o cookie, a devolução do lease tomaria 401 e o
        // aparelho ficaria preso ao usuário que fechou a aba.
        credentials: 'same-origin',
      }).catch(() => undefined);
    } catch {
      /* navegador encerrando — nada a fazer */
    }
  }
}
