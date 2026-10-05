import { create } from 'zustand';
import { API_BASE, api, toApiError, type ApiError } from '../api/client';
import type { Instance } from '../api/types';
import { confirm } from '../components/Confirm';
import { formatClock } from '../lib/time';
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

/** Quando ESTA aba fez a última tomada explícita de cada aparelho: o `control.changed` dela não derruba o lease novo. */
const tomadasDestaAba: Record<string, number> = {};

interface ControlStore {
  leases: Record<string, Lease>;
  busy: Record<string, boolean>;
  /** `tomar`: a tomada explícita do controle de outra pessoa (29.143), depois da confirmação. */
  take: (instanceId: string, tomar?: boolean) => Promise<void>;
  release: (instanceId: string) => Promise<void>;
  /** Reconciliação com o estado vindo do servidor (eventos/snapshot). */
  reconcile: (instance: Pick<Instance, 'id' | 'control' | 'control_pending'>) => void;
  drop: (instanceId: string) => void;
  /** `control.changed` de uma tomada (29.143): o lease desta aba, se havia, deixou de valer. */
  tomado: (instanceId: string, por: string) => void;
}

export const useControlStore = create<ControlStore>((set, get) => ({
  leases: {},
  busy: {},

  take: async (instanceId, tomar = false) => {
    if (get().busy[instanceId]) return;
    set((s) => ({ busy: { ...s.busy, [instanceId]: true } }));
    let recusa: ApiError | null = null;
    try {
      if (tomar) tomadasDestaAba[instanceId] = Date.now();
      const res = await api.takeControl(instanceId, tomar);
      set((s) => ({ leases: { ...s.leases, [instanceId]: { leaseId: res.lease_id, status: res.status, acquiredAt: Date.now() } } }));
      // S1 da leitura: a janela conta da RESPOSTA. Com a tomada lenta (o backend encerra a gravação no mesmo pedido), a
      // marca de antes do pedido já teria vencido quando o `control.changed` chegasse, e ele derrubaria o lease novo.
      if (tomar) tomadasDestaAba[instanceId] = Date.now();
      // Reflete já a resposta; o evento `control.changed` confirma em seguida.
      if (res.status === 'granted') {
        useAppStore.getState().patchInstance(instanceId, { control: 'user', control_pending: false });
        toast({ tone: 'success', title: `Você está no controle de ${instanceId}`, message: 'A IA fica em espera até você devolver o controle.' });
      } else {
        useAppStore.getState().patchInstance(instanceId, { control_pending: true });
        toast({ tone: 'info', title: 'Controle solicitado', message: `Aguardando a IA concluir a ação atual em ${instanceId}…`, key: `take-${instanceId}` });
      }
    } catch (e) {
      const erro = toApiError(e);
      if (erro.code === 'controlled_by_other' && !tomar) recusa = erro;
      else toastError(`Não foi possível assumir o controle de ${instanceId}`, e);
    } finally {
      set((s) => ({ busy: { ...s.busy, [instanceId]: false } }));
    }
    // 29.143: outra pessoa está no controle. O painel diz quem e desde quando, e a tomada só vai com a confirmação.
    if (recusa && await confirmarTomada(instanceId, recusa)) await get().take(instanceId, true);
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

  tomado: (instanceId, por) => {
    const lease = get().leases[instanceId];
    if (!lease || Date.now() - (tomadasDestaAba[instanceId] ?? 0) < LEASE_GRACE_MS) return;
    get().drop(instanceId);
    toast({
      tone: 'warning', title: `${por} tomou o controle de ${instanceId}`, key: `tomado-${instanceId}`,
      message: 'O seu controle deste aparelho acabou. Se havia uma gravação de treinamento, ela foi encerrada e está em "Para revisar".',
    });
  },

  drop: (instanceId) =>
    set((s) => {
      if (!s.leases[instanceId]) return s;
      const { [instanceId]: _gone, ...rest } = s.leases;
      return { leases: rest };
    }),
}));

/**
 * A recusa `controlled_by_other` (adendo v1.67) com `dono` e `desde`. Com o pedido de outra pessoa ainda pendente
 * (`desde` nulo), o backend recusa também a tomada: o painel só explica. Devolve se a pessoa confirmou a tomada.
 */
async function confirmarTomada(instanceId: string, recusa: ApiError): Promise<boolean> {
  const dono = typeof recusa.detail?.dono === 'string' && recusa.detail.dono ? recusa.detail.dono : 'Outra pessoa';
  const desde = typeof recusa.detail?.desde === 'string' ? recusa.detail.desde : null;
  if (!desde) {
    toast({ tone: 'info', title: `${dono} já pediu o controle de ${instanceId}`, key: `take-${instanceId}`,
            message: 'O pedido espera a IA terminar a ação atual. Tente de novo depois que essa pessoa devolver o controle.' });
    return false;
  }
  const { confirmed } = await confirm({
    title: `${dono} está no controle de ${instanceId}`, danger: true,
    confirmLabel: 'Tomar o controle', cancelLabel: 'Cancelar',
    body: `Desde ${formatClock(desde)}. Tomar o controle tira o aparelho dessa pessoa na hora. Se ela estiver gravando um `
      + 'treinamento, a gravação é encerrada e fica em "Para revisar" (não é descartada).',
  });
  return confirmed;
}

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
