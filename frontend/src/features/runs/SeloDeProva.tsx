import { ClipboardCheck } from 'lucide-react';
import { Badge } from '../../components/Badge';
import type { RunSummary } from '../../api/types';

/** 30.37: o nome da execução que prova um fluxo. Um só texto, para a tela e para o teste. */
export const ROTULO_DE_PROVA = 'Prova de fluxo (validação)';

const DICA_DE_PROVA =
  'Execução do sistema: o curador roda o plano do próprio fluxo para comprovar que ele funciona. Não foi um pedido de '
  + 'uma pessoa e nunca espera por uma.';

/** `true` quando a execução é de prova (não é pedido de uma pessoa). */
export function ehProvaDeFluxo(run: Pick<RunSummary, 'prova_fluxo_id'>): boolean {
  return Boolean(run.prova_fluxo_id);
}

/** Selo discreto, neutro (não é alerta): fica junto da origem da execução, sem parecer pedido de pessoa. */
export function SeloDeProva({ size = 'sm' }: { size?: 'sm' | 'md' }) {
  return <Badge tone="neutral" icon={ClipboardCheck} size={size} title={DICA_DE_PROVA}>{ROTULO_DE_PROVA}</Badge>;
}
