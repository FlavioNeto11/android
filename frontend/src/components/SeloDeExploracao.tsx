import { Compass } from 'lucide-react';
import { Badge } from './Badge';

export const ROTULO_ETAPA_EXPLORATORIA = 'Descoberta pela IA';
export const EXPLICACAO_ETAPA_EXPLORATORIA =
  'O catálogo do app não cobria este pedido e a IA descobriu a etapa explorando o app. Ninguém a demonstrou: é só proveniência.';
export const ROTULO_NASCEU_DE_EXPLORACAO = 'Nasceu de exploração';
export const EXPLICACAO_NASCEU_DE_EXPLORACAO =
  'Esta receita nasceu de uma etapa que a IA descobriu explorando o app (o catálogo não cobria o pedido). Ninguém a demonstrou.';

/** 31.299 (adendo v1.130, `steps[].exploratoria` e `PlanStep.exploratoria`): a etapa criada por exploração. Nada sem a marca (ou em backend anterior). */
export function SeloEtapaExploratoria({ exploratoria }: { exploratoria: boolean | null | undefined }) {
  if (exploratoria !== true) return null;
  return <Badge tone="info" size="sm" icon={Compass} title={EXPLICACAO_ETAPA_EXPLORATORIA}>{ROTULO_ETAPA_EXPLORATORIA}</Badge>;
}

/** 31.299 (adendo v1.130, `nasceu_de_exploracao` na linha do Livro e no dossiê): a receita cuja etapa de origem é exploratória. */
export function SeloNasceuDeExploracao({ nasceu }: { nasceu: boolean | null | undefined }) {
  if (nasceu !== true) return null;
  return <Badge tone="info" size="sm" icon={Compass} title={EXPLICACAO_NASCEU_DE_EXPLORACAO}>{ROTULO_NASCEU_DE_EXPLORACAO}</Badge>;
}
