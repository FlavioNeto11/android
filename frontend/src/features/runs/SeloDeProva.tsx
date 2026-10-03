import { ClipboardCheck, FlaskConical, Send, SquareKanban, type LucideIcon } from 'lucide-react';
import { Badge } from '../../components/Badge';
import type { OrigemDaExecucao, RunSummary } from '../../api/types';

/** 30.37: o nome da execução que prova um fluxo. Um só texto, para a tela e para o teste. */
export const ROTULO_DE_PROVA = 'Prova de fluxo (validação)';

const DICA_DE_PROVA =
  'Execução do sistema: o curador roda o plano do próprio fluxo para comprovar que ele funciona. Não foi um pedido de '
  + 'uma pessoa e nunca espera por uma.';

/** 30.38 (a): o rótulo e a dica de cada origem do vocabulário fechado (`app/contracts/origem.py`). As duas primeiras
 *  são do sistema (não foram pedido de pessoa); as dos canais são pedido de pessoa que chegou por outra porta. */
export const ORIGENS: Record<OrigemDaExecucao, { rotulo: string; dica: string; icone: LucideIcon; doSistema: boolean }> = {
  prova_fluxo: { rotulo: ROTULO_DE_PROVA, dica: DICA_DE_PROVA, icone: ClipboardCheck, doSistema: true },
  validacao_qa: {
    rotulo: 'Validação do QA',
    dica: 'Execução automática do aprendizado: o sistema roda de novo o comando de origem, noutro aparelho, para juntar '
      + 'a evidência que faltava ao parecer. Não foi um pedido de uma pessoa.',
    icone: FlaskConical,
    doSistema: true,
  },
  telegram: { rotulo: 'Pelo Telegram', dica: 'Pedido de uma pessoa, recebido pelo Telegram.', icone: Send, doSistema: false },
  trello: { rotulo: 'Pelo Trello', dica: 'Pedido de uma pessoa, recebido por um cartão do Trello.', icone: SquareKanban, doSistema: false },
};

/** A origem da execução. Backend anterior ao 30.38 não manda `origem`: a prova de fluxo ainda se reconhece pelo
 *  `prova_fluxo_id` (30.37). `null` é o pedido de pessoa pelo painel. */
export function origemDaExecucao(run: Pick<RunSummary, 'origem' | 'prova_fluxo_id'>): OrigemDaExecucao | null {
  if (run.origem && run.origem in ORIGENS) return run.origem;
  return run.prova_fluxo_id ? 'prova_fluxo' : null;
}

/** `true` quando a execução é de prova (não é pedido de uma pessoa). */
export function ehProvaDeFluxo(run: Pick<RunSummary, 'prova_fluxo_id'>): boolean {
  return Boolean(run.prova_fluxo_id);
}

/** `true` quando a execução nasceu do sistema (prova de fluxo ou validação do QA), e não de uma pessoa. */
export function ehDoSistema(run: Pick<RunSummary, 'origem' | 'prova_fluxo_id'>): boolean {
  const origem = origemDaExecucao(run);
  return origem !== null && ORIGENS[origem].doSistema;
}

/** Selo discreto, neutro (não é alerta): fica junto da origem da execução, sem parecer pedido de pessoa. */
export function SeloDeOrigem({ origem, size = 'sm' }: { origem: OrigemDaExecucao; size?: 'sm' | 'md' }) {
  const o = ORIGENS[origem];
  return <Badge tone="neutral" icon={o.icone} size={size} title={o.dica}>{o.rotulo}</Badge>;
}

/** 30.37: o selo da prova de fluxo (mantido para quem já o usa). */
export function SeloDeProva({ size = 'sm' }: { size?: 'sm' | 'md' }) {
  return <SeloDeOrigem origem="prova_fluxo" size={size} />;
}
