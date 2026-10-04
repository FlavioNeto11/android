import type { CanalAvisoTelegram, CanalConversaTelegram, CanalTrello, MotivoDeFalhaDeAviso } from '../../api/types';
import { plural } from '../../lib/format';
import { parseTs } from '../../lib/time';

/**
 * O que a tela Canais diz a partir do estado que o backend devolve (item 32.5). Tudo aqui é puro: recebe números, horas
 * e códigos e devolve frases em português. Nada vem do conteúdo de aviso, mensagem ou cartão (o backend não o manda).
 */

export type Selo = 'ligado' | 'desligado' | 'problema';

/** O motivo da última falha de envio, em palavras de quem opera (lista fechada do backend). */
export const MOTIVO_DA_FALHA: Record<MotivoDeFalhaDeAviso, string> = {
  rede: 'falha de rede',
  '401': 'token recusado (401)',
  '429': 'Telegram pediu para esperar (429)',
  tempo_esgotado: 'tempo esgotado',
  outro: 'outro erro',
};

/** Os códigos de problema da saúde, traduzidos. Código novo cai no texto genérico, que mostra o código. */
const PROBLEMA: Record<string, string> = {
  avisos_sem_segredo: 'Falta o token do bot ou o chat do Telegram no .env: nenhum aviso é enviado.',
  telegram_entrada_conflito: 'Outro processo lê o mesmo bot (conflito no Telegram): a conversa está parada.',
  telegram_entrada_recusada: 'O Telegram recusou a leitura do bot: confira o token e o chat no .env.',
  telegram_entrada_pedido_invalido: 'O Telegram não aceitou o pedido de leitura: veja o log da Central.',
  trello_sem_segredo: 'Falta a chave ou o token do Trello no .env: a Central não fala com o Trello.',
  trello_webhook_sem_segredo: 'Falta o segredo do aplicativo ou a URL pública do webhook do Trello.',
  trello_recusado: 'O Trello recusou a Central: confira a chave e o token no .env.',
  trello_pedido_invalido: 'O Trello não aceitou o pedido: confira os quadros e as listas na configuração.',
  trello_leitor_atrasado: 'A leitura dos comentários e dos cartões movidos no Trello está atrasada.',
  trello_webhook_assinatura_invalida: 'O webhook do Trello recusou chamadas com assinatura inválida.',
  trello_webhook_inativo: 'O webhook do Trello não está cadastrado ou foi desativado; a leitura periódica cobre.',
};

export function textoDoProblema(codigo: string): string {
  return PROBLEMA[codigo] ?? `Problema registrado (${codigo}).`;
}

/** Como o estado da entrada (conversa ou webhook) aparece numa frase: [singular, plural]. */
const ROTULO_DA_ENTRADA: Record<string, [string, string]> = {
  recebida: ['aguardando tratamento', 'aguardando tratamento'],
  pergunta: ['esperando a sua confirmação', 'esperando a sua confirmação'],
  executando: ['em execução', 'em execução'],
  feita: ['atendida', 'atendidas'],
  cancelada: ['cancelada', 'canceladas'],
  falhou: ['com falha', 'com falha'],
  recusada: ['recusada', 'recusadas'],
  limitada: ['barrada pelo limite', 'barradas pelo limite'],
  ignorada: ['ignorada', 'ignoradas'],
  orquestradora: ['entregue à orquestradora', 'entregues à orquestradora'],
  aviso: ['aviso do webhook esperando leitura', 'avisos do webhook esperando leitura'],
  outro: ['em outro estado', 'em outro estado'],
};

const ORDEM_DA_ENTRADA = ['feita', 'recebida', 'pergunta', 'executando', 'aviso', 'orquestradora', 'cancelada', 'falhou',
                          'recusada', 'limitada', 'ignorada', 'outro'];

const n = (m: Record<string, number>, estado: string): number => m[estado] ?? 0;

/** "3 avisos enviados, 1 na fila". Sem nada: "Nenhum aviso ainda." */
export function frasesDaFila(fila: Record<string, number>): string {
  const partes: string[] = [];
  const enviados = n(fila, 'enviado');
  const naFila = n(fila, 'pendente') + n(fila, 'enviando');
  if (enviados > 0) partes.push(`${plural(enviados, 'aviso enviado', 'avisos enviados')}`);
  if (naFila > 0) partes.push(`${naFila} na fila`);
  if (n(fila, 'falhou') > 0) partes.push(plural(n(fila, 'falhou'), 'falhou', 'falharam'));
  if (n(fila, 'incerto') > 0) partes.push(plural(n(fila, 'incerto'), 'com envio incerto', 'com envio incerto'));
  if (n(fila, 'descartado') > 0) partes.push(plural(n(fila, 'descartado'), 'descartado', 'descartados'));
  return partes.length > 0 ? partes.join(', ') : 'Nenhum aviso ainda.';
}

/** "5 entradas: 3 atendidas, 1 recusada, 1 ignorada". Sem nada: "Nenhuma mensagem recebida." */
export function frasesDasEntradas(entradas: Record<string, number>, vazio = 'Nenhuma mensagem recebida.'): string {
  const total = Object.values(entradas).reduce((soma, v) => soma + v, 0);
  if (total === 0) return vazio;
  const partes = ORDEM_DA_ENTRADA.filter((e) => n(entradas, e) > 0).map((e) => {
    const [um, varios] = ROTULO_DA_ENTRADA[e] ?? ROTULO_DA_ENTRADA['outro']!;
    return plural(n(entradas, e), um, varios);
  });
  return `${plural(total, 'entrada', 'entradas')}: ${partes.join(', ')}.`;
}

/** "12 cartões ativos, 3 arquivados". Sem nada: "Nenhum cartão espelhado." */
export function frasesDosCartoes(cartoes: Record<string, number>): string {
  const partes: string[] = [];
  if (n(cartoes, 'ativo') > 0) partes.push(plural(n(cartoes, 'ativo'), 'cartão ativo', 'cartões ativos'));
  if (n(cartoes, 'arquivado') > 0) partes.push(plural(n(cartoes, 'arquivado'), 'arquivado', 'arquivados'));
  if (n(cartoes, 'criando') > 0) partes.push(plural(n(cartoes, 'criando'), 'sendo criado', 'sendo criados'));
  return partes.length > 0 ? partes.join(', ') : 'Nenhum cartão espelhado.';
}

/** A falha só preocupa se é mais recente que o último envio que deu certo: o que falhou ontem e saiu hoje é passado. */
export function falhaVigente(aviso: CanalAvisoTelegram): boolean {
  if (!aviso.ultima_falha) return false;
  const falha = parseTs(aviso.ultima_falha.em);
  const envio = parseTs(aviso.ultimo_envio_em);
  if (falha === null) return true;
  return envio === null || falha > envio;
}

export function seloDoAviso(aviso: CanalAvisoTelegram): Selo {
  if (!aviso.ligado) return 'desligado';
  return !aviso.segredo_presente || aviso.problemas.length > 0 || falhaVigente(aviso) ? 'problema' : 'ligado';
}

export function seloDaConversa(conversa: CanalConversaTelegram): Selo {
  if (!conversa.ligada) return 'desligado';
  return conversa.problemas.length > 0 ? 'problema' : 'ligado';
}

export function seloDoTrello(trello: CanalTrello): Selo {
  if (!trello.ligado) return 'desligado';
  return trello.problemas.length > 0 ? 'problema' : 'ligado';
}
