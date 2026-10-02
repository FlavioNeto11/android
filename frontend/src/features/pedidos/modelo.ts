/**
 * Vocabulário da tela Pedidos (item 28.9): rótulos, tons e textos em português de cada estado, gatilho e aviso. Um
 * só lugar, como `lib/status.ts` para as execuções: a lista, o detalhe e as Pendências leem daqui.
 *
 * O painel NÃO reescreve a tabela de estados: o que cada pedido aceita vem de `acoes_permitidas` (backend).
 */
import {
  Ban, CircleAlert, CircleCheck, CircleDashed, CircleDot, CircleHelp, CirclePause, CircleSlash, CircleX, Clock, Flag, Hand,
  Hourglass, LoaderCircle, Send,
} from 'lucide-react';
import { ApiError, hintForError } from '../../api/client';
import type {
  Autonomia, AvisoTipo, EstadoOcorrencia, EstadoPedido, MotivoDeEncerramento, OrigemOcorrencia, Sobreposicao,
  TipoDeGatilho,
} from '../../api/pedidos';
import type { StatusMeta } from '../../lib/status';
import { formatUsd as formatUsdDoUso } from '../usage/usage';

export const ESTADOS_DO_PEDIDO: readonly EstadoPedido[] = [
  'ativo', 'aguardando_pessoa', 'pausado', 'rascunho', 'concluido', 'encerrado', 'cancelado',
];

export const META_DO_PEDIDO: Record<EstadoPedido, StatusMeta> = {
  rascunho: { label: 'Rascunho', tone: 'muted', icon: CircleDashed, description: 'Ainda não ativado: não gera ocorrências.' },
  ativo: { label: 'Ativo', tone: 'success', icon: CircleDot, description: 'Gera ocorrências pela agenda.' },
  pausado: { label: 'Pausado', tone: 'warning', icon: CirclePause, description: 'Não gera ocorrências novas; o que já corre termina.' },
  aguardando_pessoa: { label: 'Aguardando você', tone: 'warning', icon: Hand, description: 'Uma aprovação, uma pergunta ou uma ocorrência incerta espera a sua decisão.' },
  concluido: { label: 'Concluído', tone: 'success', icon: CircleCheck, description: 'Os critérios de sucesso foram atingidos.' },
  encerrado: { label: 'Encerrado', tone: 'muted', icon: Flag, description: 'Chegou ao prazo, à contagem ou ao orçamento.' },
  cancelado: { label: 'Cancelado', tone: 'muted', icon: Ban, description: 'Cancelado pela pessoa.' },
};

export const META_DA_OCORRENCIA: Record<EstadoOcorrencia, StatusMeta> = {
  prevista: { label: 'Prevista', tone: 'muted', icon: Clock, description: 'Ainda não venceu.' },
  devida: { label: 'Devida', tone: 'info', icon: Hourglass, description: 'Venceu; o laço de pedidos a despacha em até um ciclo.' },
  despachada: { label: 'Despachada', tone: 'info', icon: Send, description: 'A execução foi pedida.' },
  rodando: { label: 'Rodando', tone: 'accent', icon: LoaderCircle, spin: true },
  concluida: { label: 'Concluída', tone: 'success', icon: CircleCheck },
  falhou: { label: 'Falhou', tone: 'danger', icon: CircleX },
  incerta: { label: 'Incerta', tone: 'warning', icon: CircleHelp, description: 'Não há prova de que deu certo nem de que falhou: espera a sua decisão.' },
  cancelada: { label: 'Cancelada', tone: 'muted', icon: Ban },
  pulada: { label: 'Pulada', tone: 'muted', icon: CircleSlash, description: 'Não rodou de propósito; o motivo está ao lado.' },
  perdida: { label: 'Perdida', tone: 'warning', icon: CircleAlert, description: 'Venceu e passou da janela de recuperação.' },
};

export const ROTULO_DA_AUTONOMIA: Record<Autonomia, { rotulo: string; dica: string }> = {
  observar: { rotulo: 'Observar', dica: 'Só lê e relata. É o padrão: pedido sem escolha nunca age.' },
  preparar: { rotulo: 'Preparar', dica: 'Prepara o trabalho, sem efeito fora do sistema.' },
  agir: { rotulo: 'Agir', dica: 'Pode agir; o que tem efeito fora do sistema espera a sua aprovação.' },
};

export const ROTULO_DO_GATILHO: Record<TipoDeGatilho, string> = {
  agora: 'Agora', horario: 'Em um horário', recorrencia: 'Repetir', evento: 'Quando acontecer', condicao: 'Quando valer',
  persona: 'Por persona',
};

export const ROTULO_DA_ORIGEM: Record<OrigemOcorrencia, string> = {
  agenda: 'Pela agenda', recuperacao: 'Recuperada', evento: 'Por evento', condicao: 'Por condição', persona: 'Por persona',
  manual: 'Manual', backfill: 'Retroativa',
};

export const ROTULO_DA_SOBREPOSICAO: Record<Sobreposicao, string> = {
  pular: 'Pular a nova se a anterior ainda roda', guardar_uma: 'Guardar uma na fila', permitir_todas: 'Permitir todas ao mesmo tempo',
};

export const ROTULO_DO_ENCERRAMENTO: Record<MotivoDeEncerramento, string> = {
  prazo: 'Chegou ao prazo', contagem: 'Chegou ao número de ocorrências', orcamento: 'Gastou o orçamento',
  abandonado: 'Ficou abandonado',
};

/** Os avisos informativos (a caixa de avisos) e, entre parênteses no contrato, os que vão para as Pendências. */
export const ROTULO_DO_AVISO: Record<AvisoTipo, string> = {
  pausa_automatica: 'Pausa automática', orcamento_80: 'Orçamento a 80%', orcamento_esgotado: 'Orçamento esgotado',
  ocorrencia_perdida: 'Ocorrência perdida', relatorio_pronto: 'Relatório pronto', encerramento: 'Encerramento',
  aprovacao_pendente: 'Aprovação', pergunta: 'Pergunta', ocorrencia_incerta: 'Ocorrência incerta',
};

export const ESTADO_TERMINAL: ReadonlySet<EstadoPedido> = new Set(['concluido', 'encerrado', 'cancelado']);

/** O instante canônico do contrato: UTC, segundo cheio, sufixo `Z` (`2026-10-03T11:00:00Z`). */
export function instanteCanonico(d: Date): string {
  return `${d.toISOString().slice(0, 19)}Z`;
}

export function formatUsd(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return '—';
  return formatUsdDoUso(n);   // o mesmo formato do resto do painel: vírgula decimal, "US$ 0,00"
}

/** "3h" a partir de segundos, só para o intervalo mínimo e a janela. */
export function duracaoCurta(s: number): string {
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))} min`;
  if (s < 86_400) return `${Number((s / 3600).toFixed(1))} h`;
  return `${Number((s / 86_400).toFixed(1))} dias`;
}

/** Os códigos de erro do adendo v0.45 que a tela explica com as palavras dela; o resto cai na dica geral do cliente. */
const MENSAGEM_DO_ERRO: Record<string, string> = {
  previa_desatualizada: 'Algo mudou desde a prévia (alvos, datas ou limites). Veja a prévia de novo antes de confirmar.',
  previa_nao_confirmada: 'Esta edição muda a agenda, os alvos ou os limites: veja a prévia do que muda e confirme.',
  alvos_nao_confirmados: 'Os alvos de hoje não são os que a prévia mostrou. Veja a prévia de novo.',
  versao_desatualizada: 'O pedido mudou enquanto você editava. Recarreguei a versão atual: confira e edite de novo.',
  idempotency_conflict: 'Esta chave já criou outro pedido, com conteúdo diferente. Abra a prévia de novo para gerar uma chave nova.',
  invalid_state: 'O estado atual do pedido não permite esta ação. A tela foi atualizada.',
  pendencia_aberta: 'Ainda há uma decisão sua pendente neste pedido: resolva-a antes de retomar.',
  frequencia_abaixo_do_piso: 'A repetição é mais frequente do que o piso desta autonomia.',
  recorrencia_invalida: 'A regra de repetição não é aceita.',
  fuso_desconhecido: 'Fuso horário desconhecido.',
  gatilho_nao_suportado: 'Este tipo de gatilho ainda não existe (fica para o item 28.8).',
  sobreposicao_incompativel: 'Esta autonomia não aceita esse modo de sobreposição.',
  limite_invalido: 'Os limites estão incoerentes (prazo, número de ocorrências ou orçamento).',
  credencial_no_comando: 'O objetivo parece conter uma senha: tire-a do texto (ela mora na conta da persona).',
  sem_alvo: 'Os alvos não resolvem a nenhum aparelho.',
};

/** Texto de um erro da API para a pessoa: a explicação do código quando existe, a mensagem do backend e a dica geral. */
export function mensagemDoErro(e: ApiError): string {
  // A IA não decidiu quem faz: a pergunta vem na mensagem; o que a pessoa faz é escolher quem faz.
  if (e.code === 'alvos_a_decidir') return `${e.message} Para seguir: marque aparelhos no modo Manual ou escolha uma persona.`;
  const propria = MENSAGEM_DO_ERRO[e.code];
  if (propria) return e.code === 'recorrencia_invalida' || e.code === 'frequencia_abaixo_do_piso' ? `${propria} ${e.message}` : propria;
  return e.message ? `${e.message} ${hintForError(e)}` : hintForError(e);
}

/** Um instante ISO como o campo `datetime-local` o mostra (hora do navegador); vazio sem instante válido. */
export function paraCampoLocal(iso: string | null | undefined): string {
  const t = iso ? Date.parse(iso) : NaN;
  if (!Number.isFinite(t)) return '';
  const d = new Date(t);
  const dois = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${dois(d.getMonth() + 1)}-${dois(d.getDate())}T${dois(d.getHours())}:${dois(d.getMinutes())}`;
}
