/**
 * O resumo no topo de uma execução (revisão de UX, rodada 2, tarefa 14): o que foi pedido, como terminou e o que
 * depende da pessoa. Funções puras, para o teste não precisar montar a tela.
 */
import type { RunStatus, RunSummary } from '../../api/types';
import { formatDuration } from '../../lib/time';
import { EMPTY_COUNTS, objectivesTotal } from './model';

export type AbaDaExecucao = 'plano' | 'instancias' | 'textos' | 'timeline' | 'evidencias' | 'decisoes' | 'relatorio';

/**
 * A guia com que o detalhe abre quando o link NÃO diz qual (a guia do link, `?aba=`, manda sobre esta):
 * - concluída: o Relatório, que responde "deu certo? o que foi feito?";
 * - em andamento (executando, pausada, cancelando): a Linha do tempo, que mostra o que acontece agora;
 * - planejando, esperando informação ou plano pronto: o Plano;
 * - terminou com problemas, falhou ou foi cancelada: "Por aparelho", onde estão o que falhou e a correção da etapa.
 */
export function abaPadraoDaExecucao(status: RunStatus | undefined): AbaDaExecucao {
  switch (status) {
    case 'completed': return 'relatorio';
    case 'running': case 'paused': case 'cancelling': return 'timeline';
    case 'planning': case 'needs_input': case 'planned': return 'plano';
    default: return 'instancias';
  }
}

/** Abaixo disto o pedido cabe em duas linhas na maioria das larguras; acima, oferece "Ver pedido completo". */
export const PEDIDO_LONGO = 100;

export function pedidoEhLongo(comando: string): boolean {
  return comando.length > PEDIDO_LONGO || comando.includes('\n');
}

/**
 * A frase de resultado: "Concluída com sucesso em 2 min 03 s". Sem início registrado, não inventa duração. O formato
 * da duração é o do resto da tela (`formatSpan`: "2 min 03 s").
 */
export function resultadoDaExecucao(run: Pick<RunSummary, 'status' | 'started_at' | 'finished_at'>, agoraMs: number): string {
  const dur = run.started_at ? formatDuration(run.started_at, run.finished_at, agoraMs) : null;
  const em = (prefixo: string) => (dur && dur !== '—' ? `${prefixo} ${dur}` : '');
  switch (run.status) {
    case 'completed': return `Concluída com sucesso${em(' em')}`;
    case 'completed_with_issues': return `Concluída com problemas${em(' em')}`;
    // 29.93: o fim do trabalho automático (`finished_at`) é desde quando ela espera um gesto da pessoa no aparelho.
    case 'awaiting_person': return `Aguardando você${em(' após')}`;
    case 'failed': return `Falhou${em(' após')}`;
    case 'cancelled': return `Cancelada${em(' após')}`;
    case 'running': return `Em execução${em(' há')}`;
    case 'paused': return `Pausada${em(' após')}`;
    case 'cancelling': return 'Cancelando…';
    case 'planning': return 'Planejando…';
    case 'planned': return 'Plano pronto. Nada foi executado ainda';
    case 'needs_input': return 'Parou pedindo informação antes de executar';
    default: return '';
  }
}

/** Estados em que nada foi executado ainda: contar "0 de N com sucesso" leria como fracasso de algo que não rodou. */
const SEM_EXECUCAO: readonly RunStatus[] = ['planning', 'planned', 'needs_input'];

/** "1 de 1 objetivo com sucesso" — `null` quando ainda não há objetivos ou quando nada foi executado (B4, rodada 2). */
export function objetivosComSucesso(run: Pick<RunSummary, 'status' | 'counts' | 'instances_used'>): string | null {
  if (SEM_EXECUCAO.includes(run.status)) return null;
  const counts = run.counts ?? EMPTY_COUNTS;
  const total = Math.max(objectivesTotal(counts), run.instances_used, 0);
  if (total === 0) return null;
  return `${counts.succeeded} de ${total} ${total === 1 ? 'objetivo' : 'objetivos'} com sucesso`;
}

/** O que a execução espera de uma pessoa, em linhas curtas; vazio quando nada depende dela. */
export function oQuePrecisaDaPessoa(a: {
  status: RunStatus; perguntas: number; bloqueados: number; textosParaAprovar: number; terminal: boolean;
  /** 29.52: o tipo da credencial que a pergunta pede (evento `pergunta_sensivel`); ela não se responde aqui. */
  sensivel?: string | null;
}): { chave: 'perguntas' | 'bloqueios' | 'textos'; texto: string; aba: AbaDaExecucao | null }[] {
  const linhas: { chave: 'perguntas' | 'bloqueios' | 'textos'; texto: string; aba: AbaDaExecucao | null }[] = [];
  if (a.status === 'needs_input') {
    linhas.push({
      chave: 'perguntas',
      texto: a.sensivel
        ? (a.sensivel === 'codigo' || a.sensivel === '2fa'
          ? 'Digitar o código no aparelho e pedir de novo: ele não se responde aqui'
          : 'Guardar a senha na conta da persona e pedir de novo: ela não se responde aqui')
        : a.perguntas > 0
        ? `Responder ${a.perguntas === 1 ? 'a pergunta' : `às ${a.perguntas} perguntas`} da IA para a execução seguir`
        : 'Informar o que falta para a execução seguir',
      aba: null,
    });
  }
  if (a.bloqueados > 0 && !a.terminal) {
    linhas.push({
      chave: 'bloqueios',
      texto: `${a.bloqueados} ${a.bloqueados === 1 ? 'objetivo espera' : 'objetivos esperam'} a sua decisão`,
      aba: 'instancias',
    });
  }
  if (a.textosParaAprovar > 0) {
    linhas.push({
      chave: 'textos',
      texto: `${a.textosParaAprovar} ${a.textosParaAprovar === 1 ? 'texto espera' : 'textos esperam'} a sua aprovação`,
      aba: 'textos',
    });
  }
  return linhas;
}

/** 29.60: quem contou as cópias de um efeito repetido, em português (sem o código cru do contrato). */
export function fonteDoEfeitoRepetido(fonte: 'verificador' | 'acoes' | 'provedor'): string {
  if (fonte === 'provedor') return 'contado no próprio app de QA ao fim da validação'; // 30.31 (fatia 2)
  return fonte === 'verificador' ? 'contado na tela pelo verificador' : 'contado pelas ações gravadas desta execução';
}

/** 29.60: a frase do efeito repetido de uma etapa ("apareceu 2 vezes"). */
export function fraseDoEfeitoRepetido(copias: number): string {
  return `apareceu ${copias} vezes`;
}

/** 29.60: as etapas com efeito repetido de uma execução, para o resumo. Só a versão de plano atual de cada objetivo
 * não é filtrada de propósito: um efeito repetido numa versão anterior também saiu do aparelho. */
export function efeitosRepetidos<T extends { result: { efeito_repetido?: { copias: number; fonte: 'verificador' | 'acoes' | 'provedor' } } | null }>(
  steps: readonly T[],
): T[] {
  return steps.filter((s) => (s.result?.efeito_repetido?.copias ?? 0) >= 2);
}
