/**
 * 31.215: o resumo do dia da página inicial, calculado do que as telas já leem (lista de operações, personas, snapshot, pendências,
 * custo e saúde): nenhuma rota nova. Puro e tolerante: o que a fonte não diz fica `null` ("não informado"), nunca zero.
 */
import type { AvisoDTO } from '../../api/pedidos';
import type { Instance, PersonaDTO, SessionInfo } from '../../api/types';
import { estadoContado, type EstadoContado } from '../../store/metricas';
import { precisaDePessoa } from '../pendencias/modelo';
import type { Operacao, ResumoDaOperacao } from '../operacao/modelo';

/** O app sem conta real: o QA Messenger tem contas fictícias; o resto (Instagram, Outlook…) é conta de verdade. */
export const APP_SEM_CONTA_REAL = 'qa-messenger';

const soma = (xs: readonly (number | null)[]): number | null => {
  const v = xs.filter((x): x is number => x !== null);
  return v.length ? v.reduce((s, x) => s + x, 0) : null;
};

// ---- operações -------------------------------------------------------------------------------------------------------------------------

export interface OperacoesDoDia {
  /** Em andamento agora (qualquer dia). */
  emCurso: number;
  /** Criadas no dia local de `agoraMs`. */
  doDia: ResumoDaOperacao[];
  /** A capacidade somada das operações do dia; `null` quando nenhuma informa o número. */
  solicitados: number | null;
  concluidas: number | null;
  bloqueadas: number | null;
}

const mesmoDia = (iso: string | null, agoraMs: number): boolean => {
  const t = iso ? Date.parse(iso) : NaN;
  return Number.isFinite(t) && new Date(t).toDateString() === new Date(agoraMs).toDateString();
};

export function operacoesDoDia(itens: readonly ResumoDaOperacao[], agoraMs: number): OperacoesDoDia {
  const doDia = itens.filter((o) => mesmoDia(o.created_at, agoraMs));
  return {
    emCurso: itens.filter((o) => o.status === 'em_curso').length, doDia,
    solicitados: soma(doDia.map((o) => o.capacidade.solicitados)), concluidas: soma(doDia.map((o) => o.capacidade.concluidas)),
    bloqueadas: soma(doDia.map((o) => o.capacidade.bloqueadas)),
  };
}

export interface CustoDasOperacoes {
  /** A soma dos custos que as operações lidas informam; `null` quando nenhuma informa. */
  totalUsd: number | null;
  /** Quantas das lidas não informam o custo (ficam fora da soma, nunca como zero). */
  semCusto: number;
}

export function custoDasOperacoes(lidas: readonly Pick<Operacao, 'custo'>[]): CustoDasOperacoes {
  const custos = lidas.map((o) => o.custo?.total_usd ?? null);
  return { totalUsd: soma(custos), semCusto: custos.filter((c) => c === null).length };
}

// ---- aparelhos de conta real -----------------------------------------------------------------------------------------------------------

export interface AparelhoDeContaReal {
  instanceId: string;
  appId: string;
  /** O estado do aparelho no parque; `null` = o painel ainda não o conhece. */
  estado: EstadoContado | null;
  sessao: SessionInfo | null;
}

/**
 * Os aparelhos em que alguma persona tem conta num app de verdade (vínculo com app que não é o QA), um por aparelho e app, na ordem do id.
 * Só ids, estado e sessão: nenhum nome de persona nem @ entra no resumo.
 */
export function aparelhosDeContaReal(personas: readonly Pick<PersonaDTO, 'devices'>[], instancias: Readonly<Record<string, Instance>>,
                                     workers?: Parameters<typeof estadoContado>[1]): AparelhoDeContaReal[] {
  const vistos = new Map<string, AparelhoDeContaReal>();
  for (const p of personas) {
    for (const d of p.devices ?? []) {
      if (!d.app_id || d.app_id === APP_SEM_CONTA_REAL) continue;
      const chave = `${d.instance_id}|${d.app_id}`;
      const inst = instancias[d.instance_id];
      const atual = vistos.get(chave);
      // Duas personas no mesmo aparelho e app: a sessão que pede pessoa (a pior) é a que o resumo mostra.
      if (atual && !(d.session && precisaDePessoa(d.session) && !(atual.sessao && precisaDePessoa(atual.sessao)))) continue;
      vistos.set(chave, { instanceId: d.instance_id, appId: d.app_id, estado: inst ? estadoContado(inst, workers) : null, sessao: d.session });
    }
  }
  return [...vistos.values()].sort((a, b) => a.instanceId.localeCompare(b.instanceId, 'pt-BR', { numeric: true }) || a.appId.localeCompare(b.appId));
}

// ---- perguntas e deploy ----------------------------------------------------------------------------------------------------------------------

/**
 * As perguntas abertas ao dono: os avisos `pergunta` que esperam uma pessoa e ainda não foram lidos (`GET /api/pedidos/avisos?requer_pessoa=1`,
 * a fonte da caixa de Pendências). `null` = a leitura não existe (rota ausente, erro): o bloco some, não mostra zero.
 */
export function perguntasAbertas(avisos: readonly Pick<AvisoDTO, 'tipo' | 'lido_em'>[] | null): number | null {
  return avisos === null ? null : avisos.filter((a) => a.tipo === 'pergunta' && a.lido_em === null).length;
}

export interface UltimoDeploy {
  versao: string | null;
  /** Os 8 primeiros caracteres do commit no ar; `null` quando a instalação veio por cópia, sem `.git`. */
  commit: string | null;
  migracao: string | null;
}

/** O que a saúde diz do que está no ar. A saúde não traz a HORA do deploy: o resumo diz isso em vez de inventá-la. */
export function ultimoDeploy(health: { version?: string | null; commit?: string | null; migration?: string | null } | null | undefined): UltimoDeploy | null {
  if (!health) return null;
  const limpo = (v: string | null | undefined): string | null => (typeof v === 'string' && v.trim() ? v.trim() : null);
  return { versao: limpo(health.version), commit: limpo(health.commit)?.slice(0, 8) ?? null, migracao: limpo(health.migration) };
}
