import { CircleHelp } from 'lucide-react';
import type { Instance, Worker } from '../../api/types';
import { INSTANCE_STATE, metaOf, type StatusMeta } from '../../lib/status';
import { estadoContado } from '../../store/metricas';

/**
 * Selo de estado do aparelho, um só para cartão, lista e drawer: ícone + texto + cor, nunca só cor (tarefa 04).
 * Online, Parada, Hibernado… vêm de `INSTANCE_STATE`; "Desconhecido" é o que o painel diz quando o servidor do
 * aparelho está fora do ar — o `state` guardado é velho e mostrá-lo como verdade (ex.: "Parada") era afirmar sem saber.
 * A mesma regra do contador do painel (`estadoContado`), então o número e o selo nunca discordam.
 */
export const SELO_DESCONHECIDO: StatusMeta = {
  label: 'Desconhecido',
  tone: 'warning',
  icon: CircleHelp,
  description: 'O servidor deste aparelho não está respondendo; não dá para saber se o emulador está ligado.',
};

/**
 * O aparelho está em estado desconhecido (servidor fora do ar ou sem canal)? É a pergunta que cartão, lista, Foco,
 * Infraestrutura e barra em lote fazem antes de oferecer um verbo — a mesma de `estadoContado`, sem cópia (RF-40).
 */
export function aparelhoDesconhecido(
  inst: Pick<Instance, 'id' | 'worker_id' | 'state'>,
  workers: Readonly<Record<string, Worker>> | undefined | null,
): boolean {
  return estadoContado(inst, workers) === 'desconhecido';
}

export function seloDoAparelho(
  inst: Pick<Instance, 'id' | 'worker_id' | 'state'>,
  workers: Readonly<Record<string, Worker>> | undefined | null,
): StatusMeta {
  return aparelhoDesconhecido(inst, workers) ? SELO_DESCONHECIDO : metaOf(INSTANCE_STATE, inst.state);
}

/** Estados sem emulador de pé (nada a mostrar na tela): o cartão do Painel sai na versão compacta. */
export function ehAparelhoParado(inst: Pick<Instance, 'state'>): boolean {
  return inst.state === 'stopped' || inst.state === 'absent' || inst.state === 'hibernated';
}
