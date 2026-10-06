/**
 * O relatório da operação (31.162, formato do § 8 do cenário da prova de 07/10): SÓ o que o `GET /api/operacoes/{id}` (contrato
 * v1.94) traz, montado no painel e baixado em Markdown e em JSON. Por agente: os 14 estágios com a hora, o motivo de quem
 * parou, o conhecimento usado (ids), o texto gerado, a ação final (tipo, verificada sim/não/não conferida, evidência) e o
 * custo. Consolidado: a faixa de capacidade, os custos (pesquisa, agentes, total e o teto), as falhas agrupadas por motivo e os
 * textos irmãos. O agente aparece pelo RÓTULO da persona: nunca o @ da conta, o id da conta nem login ou e-mail.
 */
import {
  ESTAGIOS, ROTULO_DO_ESTADO, ROTULO_DO_STATUS, estagioDeParada, rotuloDaAcao, rotuloDoEstagio,
  type Alvo, type EstagioId, type Operacao,
} from './modelo';

export type Conferencia = 'sim' | 'nao' | 'nao_conferida' | 'sem_acao';

/** `true` é sim; `false` é não; a ação que existe mas não trouxe o resultado da conferência é "não conferida". */
export function conferenciaDaAcao(a: Pick<Alvo, 'resultado'>): Conferencia {
  const acao = a.resultado?.acao_final;
  if (!acao) return 'sem_acao';
  return acao.verificada === true ? 'sim' : acao.verificada === false ? 'nao' : 'nao_conferida';
}

const ROTULO_DA_CONFERENCIA: Record<Conferencia, string> = { sim: 'sim', nao: 'não', nao_conferida: 'não conferida', sem_acao: 'sem ação final' };

export interface AgenteDoRelatorio {
  agente: string;
  aparelho: string | null;
  estado: string;
  parou_em: string | null;
  motivo: string | null;
  estagios: { estagio: EstagioId; rotulo: string; em: string | null; alcancado: boolean }[];
  conhecimento_ids: string[];
  texto: string | null;
  evidencia_id: number | null;
  acao_final: { tipo: string | null; verificada: Conferencia; evidencia_id: number | null } | null;
  /** O contrato v1.94 só traz o custo da operação inteira; por agente não há número, e o relatório não inventa. */
  custo_usd: null;
}

export interface FalhaPorMotivo { motivo: string; parou_em: string | null; agentes: number }
export interface GrupoDeTextos { texto: string; agentes: string[] }

export interface RelatorioDaOperacao {
  gerado_em: string;
  operacao: {
    id: string; comando: string; app_id: string | null; acao_final: string | null; status: string | null;
    criada_em: string | null; encerrada_em: string | null; assunto: string | null; fontes: string[];
  };
  capacidade: {
    solicitados: number | null; contas_existentes: number | null; sessoes_validas: number | null; contas_disponiveis: number | null;
    concluidas: number | null; bloqueadas: number | null; em_curso: number | null; motivos: { motivo: string; n: number }[];
  };
  custo: { pesquisa_usd: number | null; alvos_usd: number | null; total_usd: number | null; teto_usd: number | null };
  falhas_por_motivo: FalhaPorMotivo[];
  textos: { total: number; distintos: number; repetidos: GrupoDeTextos[]; lista: { agente: string; texto: string }[] };
  agentes: AgenteDoRelatorio[];
  /** O que o relatório NÃO tem, para ninguém tomar a ausência por zero. */
  limites: string[];
}

const LIMITES = [
  'Custo por agente: o contrato da operação traz só o custo da operação inteira (pesquisa, agentes e total).',
  'O relatório vem do estado da operação no momento em que foi gerado; uma operação em curso muda depois.',
];

const normaliza = (t: string): string => t.replace(/\s+/g, ' ').trim().toLowerCase();

function agenteDe(a: Alvo, posicao: number): AgenteDoRelatorio {
  const alcancados = new Map(a.estagios.map((e) => [e.estagio, e.em]));
  // Sem a lista `estagios` (backend antigo), o último estágio alcançado e os anteriores contam, sem hora.
  const ate = a.estagios.length === 0 && a.estagio ? ESTAGIOS.findIndex((e) => e.id === a.estagio) : -1;
  const acao = a.resultado?.acao_final ?? null;
  const parou = a.estado === 'bloqueado' || a.estado === 'cancelado' ? estagioDeParada(a) : null;
  return {
    agente: a.persona ?? `Persona ${posicao + 1}`,
    aparelho: a.instance_id,
    estado: a.estado ? ROTULO_DO_ESTADO[a.estado] : 'não informado',
    parou_em: parou ? rotuloDoEstagio(parou) : null,
    motivo: a.motivo,
    estagios: ESTAGIOS.map((e, i) => ({
      estagio: e.id, rotulo: e.rotulo, em: alcancados.get(e.id) ?? null, alcancado: alcancados.has(e.id) || i <= ate,
    })),
    conhecimento_ids: a.resultado?.conhecimento_ids ?? [],
    texto: a.resultado?.texto ?? null,
    evidencia_id: a.resultado?.evidencia_id ?? null,
    acao_final: acao ? { tipo: acao.tipo, verificada: conferenciaDaAcao(a), evidencia_id: acao.evidencia_id } : null,
    custo_usd: null,
  };
}

export function montarRelatorio(op: Operacao, agora: Date = new Date()): RelatorioDaOperacao {
  const agentes = op.alvos.map(agenteDe);
  const falhas = new Map<string, FalhaPorMotivo>();
  for (const a of agentes) {
    if (a.estado !== ROTULO_DO_ESTADO.bloqueado && a.estado !== ROTULO_DO_ESTADO.cancelado) continue;
    const motivo = a.motivo ?? 'sem motivo informado';
    const chave = `${motivo}|${a.parou_em ?? ''}`;
    const atual = falhas.get(chave);
    if (atual) atual.agentes += 1;
    else falhas.set(chave, { motivo, parou_em: a.parou_em, agentes: 1 });
  }
  const comTexto = agentes.flatMap((a) => (a.texto ? [{ agente: a.agente, texto: a.texto }] : []));
  const grupos = new Map<string, GrupoDeTextos>();
  for (const t of comTexto) {
    const g = grupos.get(normaliza(t.texto));
    if (g) g.agentes.push(t.agente);
    else grupos.set(normaliza(t.texto), { texto: t.texto, agentes: [t.agente] });
  }
  const c = op.capacidade;
  return {
    gerado_em: agora.toISOString(),
    operacao: {
      id: op.id, comando: op.command, app_id: op.app_id, acao_final: op.acao_final, status: op.status ? ROTULO_DO_STATUS[op.status] : null,
      criada_em: op.created_at, encerrada_em: op.finished_at, assunto: op.assunto, fontes: op.fontes,
    },
    capacidade: { ...c },
    custo: {
      pesquisa_usd: op.custo?.pesquisa_usd ?? null, alvos_usd: op.custo?.alvos_usd ?? null, total_usd: op.custo?.total_usd ?? null, teto_usd: op.max_usd,
    },
    falhas_por_motivo: [...falhas.values()].sort((x, y) => y.agentes - x.agentes || x.motivo.localeCompare(y.motivo)),
    textos: { total: comTexto.length, distintos: grupos.size, repetidos: [...grupos.values()].filter((g) => g.agentes.length > 1), lista: comTexto },
    agentes,
    limites: LIMITES,
  };
}

const usd = (n: number | null): string => (n === null ? 'não informado' : `US$ ${n.toFixed(4)}`);
const num = (n: number | null): string => (n === null ? 'não informado' : String(n));
/** O texto numa citação, linha a linha, para uma quebra de linha do texto não virar título do Markdown. */
const citacao = (t: string): string => t.split(/\r?\n/).map((l) => `> ${l}`).join('\n');

export function relatorioEmMarkdown(r: RelatorioDaOperacao): string {
  const o = r.operacao;
  const c = r.capacidade;
  const linhas: string[] = [
    `# Relatório da operação ${o.id}`,
    '',
    `Gerado em ${r.gerado_em}. Estado da operação: ${o.status ?? 'não informado'}.`,
    '',
    `- **Comando:** ${o.comando}`,
    `- **App:** ${o.app_id ?? 'não informado'}`,
    `- **Ação final:** ${rotuloDaAcao(o.acao_final)}`,
    `- **Criada em:** ${o.criada_em ?? 'não informado'} · **Encerrada em:** ${o.encerrada_em ?? 'em aberto'}`,
  ];
  if (o.assunto) linhas.push(`- **Assunto:** ${o.assunto}`);
  if (o.fontes.length) linhas.push(`- **Fontes indicadas:** ${o.fontes.join(', ')}`);
  linhas.push(
    '', '## Capacidade', '',
    '| solicitados | contas existentes | sessões válidas | disponíveis | em curso | concluídas | bloqueadas |', '|---|---|---|---|---|---|---|',
    `| ${num(c.solicitados)} | ${num(c.contas_existentes)} | ${num(c.sessoes_validas)} | ${num(c.contas_disponiveis)} | ${num(c.em_curso)} | ${num(c.concluidas)} | ${num(c.bloqueadas)} |`,
    '', '## Custo', '',
    `- Pesquisa externa: ${usd(r.custo.pesquisa_usd)}`, `- Agentes: ${usd(r.custo.alvos_usd)}`,
    `- **Total:** ${usd(r.custo.total_usd)} (teto da operação: ${usd(r.custo.teto_usd)})`,
    '', '## Falhas por motivo', '',
  );
  if (r.falhas_por_motivo.length === 0) linhas.push('Nenhum agente parou.');
  for (const f of r.falhas_por_motivo) linhas.push(`- ${f.motivo}${f.parou_em ? ` (parou em ${f.parou_em})` : ''}: ${f.agentes} ${f.agentes === 1 ? 'agente' : 'agentes'}`);
  linhas.push('', '## Textos gerados (irmãos da operação)', '');
  linhas.push(`${r.textos.total} ${r.textos.total === 1 ? 'texto' : 'textos'}, ${r.textos.distintos} ${r.textos.distintos === 1 ? 'distinto' : 'distintos'}.`);
  if (r.textos.repetidos.length) {
    linhas.push('', 'Repetidos (mesmo texto em mais de um agente):');
    for (const g of r.textos.repetidos) linhas.push('', citacao(g.texto), '', `Agentes: ${g.agentes.join(', ')}.`);
  }
  for (const t of r.textos.lista) linhas.push('', `**${t.agente}**`, '', citacao(t.texto));
  linhas.push('', '## Agentes');
  for (const a of r.agentes) {
    linhas.push('', `### ${a.agente}`, '', `- **Estado:** ${a.estado}${a.parou_em ? `, parou em ${a.parou_em}` : ''}${a.motivo ? ` (${a.motivo})` : ''}`);
    if (a.aparelho) linhas.push(`- **Aparelho:** ${a.aparelho}`);
    linhas.push(`- **Conhecimento usado:** ${a.conhecimento_ids.length ? a.conhecimento_ids.join(', ') : 'nenhum registrado'}`);
    linhas.push(a.acao_final
      ? `- **Ação final:** ${rotuloDaAcao(a.acao_final.tipo)} · verificada: ${ROTULO_DA_CONFERENCIA[a.acao_final.verificada]} · evidência: ${a.acao_final.evidencia_id ?? 'nenhuma'}`
      : '- **Ação final:** sem ação final');
    linhas.push(`- **Evidência da tela lida:** ${a.evidencia_id ?? 'nenhuma'}`, `- **Custo:** ${usd(a.custo_usd)} (o contrato traz só o total da operação)`);
    linhas.push('', '| estágio | hora |', '|---|---|');
    for (const e of a.estagios) linhas.push(`| ${e.rotulo} | ${e.em ?? (e.alcancado ? 'alcançado, sem hora' : 'não alcançado')} |`);
    if (a.texto) linhas.push('', 'Texto gerado:', '', citacao(a.texto));
  }
  linhas.push('', '## O que este relatório não tem', '', ...r.limites.map((l) => `- ${l}`), '');
  return linhas.join('\n');
}
