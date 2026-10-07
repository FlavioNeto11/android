/**
 * 31.212: o histórico de operações de UMA persona, para a tela Persona. O central não tem a rota "operações desta persona": a lista
 * (`GET /api/operacoes`) traz só o resumo de cada operação, e o alvo de cada persona (estágio final, ação, verificação, custo) está no
 * detalhe. Então lê-se a lista e, das mais recentes, o detalhe, ficando só com os alvos desta persona. A conta é dita na tela (quantas
 * operações foram olhadas, de quantas) e uma operação cujo detalhe falhou aparece na contagem de falhas: nada some em silêncio.
 */
import { apiOperacoes } from '../operacao/api';
import { estagioDeParada, rotuloDoEstagio, verificacaoDoAlvo, type Alvo, type EstadoDoAlvo, type Operacao, type StatusDaOperacao, type Verificacao } from '../operacao/modelo';

/** Quantas das operações mais recentes têm o detalhe lido (uma leitura por operação). */
export const OPERACOES_LIDAS = 20;
const SIMULTANEAS = 4;

export interface LinhaDoHistorico {
  operacaoId: string;
  comando: string;
  statusDaOperacao: StatusDaOperacao | null;
  criadaEm: string | null;
  estado: EstadoDoAlvo | null;
  /** Onde o alvo chegou (concluído: o último estágio; em andamento: o estágio atual) ou parou ("Parou em …"); `—` sem dado. */
  estagioFinal: string;
  acao: string | null;
  verificacao: Verificacao;
  /** US$ de IA do alvo; `null` = não informado, nunca zero. */
  custoUsd: number | null;
  motivo: string | null;
}

/** Os alvos da persona nas operações, da mais nova à mais antiga (a ordem da lista); a persona pode ter mais de um alvo numa operação. */
export function linhasDaPersona(operacoes: readonly Operacao[], profileId: string): LinhaDoHistorico[] {
  return operacoes.flatMap((op) => op.alvos.filter((a) => a.profile_id === profileId).map((a): LinhaDoHistorico => ({
    operacaoId: op.id, comando: op.command, statusDaOperacao: op.status, criadaEm: op.created_at, estado: a.estado,
    estagioFinal: estagioFinalDoAlvo(a), acao: a.resultado?.acao_final?.tipo ?? null, verificacao: verificacaoDoAlvo(a),
    custoUsd: a.custo_usd, motivo: a.motivo,
  })));
}

function estagioFinalDoAlvo(a: Alvo): string {
  if (a.estado === 'bloqueado' || a.estado === 'cancelado') return `Parou em ${rotuloDoEstagio(estagioDeParada(a))}`;
  if (a.estado === 'pendente') return a.estagio ? rotuloDoEstagio(a.estagio) : 'Ainda não começou';
  if (a.estado === 'em_curso') return `Em ${rotuloDoEstagio(a.estagio)}`;
  return rotuloDoEstagio(a.estagio);
}

export interface SomaDoHistorico {
  alvos: number;
  concluidos: number;
  verificados: number;
  /** A soma do custo dos alvos que informam custo; `null` se nenhum informa. */
  custoUsd: number | null;
  /** Quantos alvos não informam o custo (a soma não os conta como zero). */
  semCusto: number;
}

export function somaDoHistorico(linhas: readonly LinhaDoHistorico[]): SomaDoHistorico {
  const custos = linhas.flatMap((l) => (l.custoUsd === null ? [] : [l.custoUsd]));
  return {
    alvos: linhas.length, concluidos: linhas.filter((l) => l.estado === 'concluido').length,
    verificados: linhas.filter((l) => l.verificacao === 'verificada').length,
    custoUsd: custos.length ? custos.reduce((s, x) => s + x, 0) : null, semCusto: linhas.length - custos.length,
  };
}

export interface HistoricoDaPersona {
  linhas: LinhaDoHistorico[];
  /** Quantas operações há no central e quantas tiveram o detalhe lido (as mais recentes). */
  totalDeOperacoes: number;
  lidas: number;
  /** Quantas operações lidas tiveram o detalhe com erro: ficam de fora das linhas e a tela diz. */
  falhas: number;
}

/** `null` quando o central não oferece o módulo de operações (a lista cai no exemplo): a tela diz isso e não mostra exemplo como histórico. */
export async function lerHistoricoDaPersona(profileId: string, signal?: AbortSignal): Promise<HistoricoDaPersona | null> {
  const lista = await apiOperacoes.lista(signal);
  if (lista.exemplo) return null;
  const recentes = lista.itens.slice(0, OPERACOES_LIDAS);
  const lidas: (Operacao | null)[] = new Array<Operacao | null>(recentes.length).fill(null);
  let proxima = 0;
  const trabalhar = async (): Promise<void> => {
    for (let i = proxima++; i < recentes.length; i = proxima++) {
      try { lidas[i] = await apiOperacoes.detalhe(recentes[i]!.id, signal); } catch (e) { if (signal?.aborted) throw e; }
    }
  };
  await Promise.all(Array.from({ length: Math.min(SIMULTANEAS, recentes.length) }, trabalhar));
  const boas = lidas.filter((o): o is Operacao => o !== null);
  return { linhas: linhasDaPersona(boas, profileId), totalDeOperacoes: lista.itens.length, lidas: recentes.length, falhas: recentes.length - boas.length };
}
