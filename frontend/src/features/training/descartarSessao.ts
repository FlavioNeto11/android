/**
 * 31.119: descartar uma sessão de treino que já terminou de gravar (gravada ou com proposta), a partir da revisão e da lista
 * "Para revisar". Antes só a gravação viva tinha "Descartar"; quem concluía e desistia ficava com a sessão "só gravada" para
 * sempre. Pede confirmação (nada do que foi gravado vira fluxo), chama `POST /api/training/{id}/discard` e, se esta aba está com
 * o controle do aparelho, devolve-o à IA (`release`): o treino que nasce de "Ensinar a corrigir" toma o controle, e quem
 * desiste não deve ter de ir buscá-lo noutro botão.
 */
import { api } from '../../api/client';
import type { TrainingSession } from '../../api/types';
import { confirm } from '../../components/Confirm';
import { plural } from '../../lib/format';
import { useControlStore } from '../../store/control';
import { toast, toastError } from '../../store/toasts';

type SessaoDescartavel = Pick<TrainingSession, 'id' | 'intent' | 'instance_id'> & { inputs?: unknown[]; input_count?: number };

/** `true` se a sessão foi descartada (quem chamou relê a lista ou fecha a revisão); `false` se a pessoa desistiu ou deu erro. */
export async function descartarSessaoConcluida(s: SessaoDescartavel): Promise<boolean> {
  const entradas = Math.max(s.input_count ?? 0, s.inputs?.length ?? 0);                  // a lista traz só `input_count`; a revisão traz as entradas
  const comControle = useControlStore.getState().leases[s.instance_id]?.status === 'granted';
  const { confirmed } = await confirm({
    title: 'Descartar o treinamento?',
    danger: true,
    confirmLabel: 'Descartar treinamento',
    cancelLabel: 'Cancelar',
    body: `“${s.intent}” e ${plural(entradas, 'entrada gravada', 'entradas gravadas')} se perdem; nada vira fluxo nem habilidade.`
      + (comControle ? ` O controle de ${s.instance_id} volta para a IA.` : ''),
  });
  if (!confirmed) return false;
  try {
    await api.discardTraining(s.id);
  } catch (e) {
    toastError('Não foi possível descartar o treinamento', e);
    return false;
  }
  toast({ tone: 'info', title: 'Treinamento descartado', message: `“${s.intent}” saiu da lista; nada foi salvo.` });
  if (comControle) await useControlStore.getState().release(s.instance_id);
  return true;
}
