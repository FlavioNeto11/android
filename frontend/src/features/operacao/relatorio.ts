/**
 * O relatório da operação (31.162, formato do § 8 do cenário da prova de 07/10): SÓ o que o `GET /api/operacoes/{id}` (contrato
 * v1.94) traz, montado no painel e baixado em Markdown e em JSON. Por agente: os 14 estágios com a hora, o motivo de quem
 * parou, o conhecimento usado (ids), o texto gerado, a ação final (tipo, verificada sim/não/não conferida, evidência) e o
 * custo. Consolidado: a faixa de capacidade, os custos (pesquisa, agentes, total e o teto), as falhas agrupadas por motivo e os
 * textos irmãos. O agente aparece pelo RÓTULO da persona: nunca o @ da conta, o id da conta nem login ou e-mail.
 */
import { type AvisoDaOperacao, type ItemAprendido, type LeituraDoAprendizado, licoesDaOperacao } from './aprendizadoDaOperacao';
import {
  ESTAGIOS, ROTULO_DO_ESTADO, ROTULO_DO_STATUS, estagioDeParada, rotuloDaAcao, rotuloDoEstagio,
  type Alvo, type EstagioId, type Operacao,
} from './modelo';

/**
 * Os motivos e resumos que o BACKEND escreve podem citar o @ de uma conta ("conta(s) da frota já mexeram com @fulano"): no relatório
 * o @ sai (visto no central real, onda 1 de 06/10). O texto gerado pela persona não é metadado e não passa por aqui.
 */
export const semArroba = (s: string): string => s.replace(/@[A-Za-z0-9._]+/g, '@[omitido]');
const semArrobaOuNulo = (s: string | null): string | null => (s === null ? null : semArroba(s));

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
  estagios: { estagio: EstagioId; rotulo: string; em: string | null; alcancado: boolean; /** 31.197: ms desde o evento anterior (relatório do central); ausente = não veio. */ etapa_ms?: number | null }[];
  /** `null` = o relatório do central não traz o conhecimento por agente (nunca "nenhum" por omissão). */
  conhecimento_ids: string[] | null;
  texto: string | null;
  evidencia_id: number | null;
  acao_final: { tipo: string | null; verificada: Conferencia; evidencia_id: number | null } | null;
  /** O que a execução do agente gastou em IA; `null` sem execução (ou backend anterior), nunca zero inventado. */
  custo_usd: number | null;
  /** 31.197 (v1.111): da criação da operação ao último estágio, e a espera pela aprovação; `null`/ausente = não medido. */
  duracao_ms?: number | null;
  espera_do_liberar_ms?: number | null;
}

export interface FalhaPorMotivo { motivo: string; parou_em: string | null; agentes: number }
export interface GrupoDeTextos { texto: string; agentes: string[] }

/** O item aprendido no relatório: a persona vai pelo RÓTULO (nunca o id), e `null` é a operação inteira. */
export type ItemDoRelatorio = Omit<ItemAprendido, 'persona'> & { persona: string | null };
export interface AprendizadoNoRelatorio {
  disponivel: boolean;
  /** Por que não há aprendizado, quando `disponivel` é falso; nunca vira "nada aprendido". */
  motivo: string | null;
  gerado_em: string | null;
  perguntas: { chave: string; titulo: string; veio: boolean; itens: ItemDoRelatorio[] }[];
  /** 31.167: as lições de qualquer pergunta, separadas pela evidência efetiva (a mesma regra da aba Aprendizado). */
  licoes: { reforcadas: ItemDoRelatorio[]; contestadas: ItemDoRelatorio[] };
  /** 31.167: as etapas cujo conhecimento recebido não foi gravado (campo `avisos` do central), sem @ de conta. */
  avisos: AvisoDaOperacao[];
  nao_coberto: { chave: string; motivo: string }[];
}

/** 31.197 (v1.111): um dos 16 critérios do diagnóstico (com 2b, 3b e 11b) e se valeu NESTA operação; `nao_medido` nunca vira "sim" nem "não". */
export interface CriterioDoRelatorio {
  id: string;
  nome: string;
  estado: 'implementado' | 'testado_em_simulacao' | 'provado_real' | 'bloqueado' | 'nao_implementado' | null;
  nesta_operacao: 'sim' | 'nao' | 'nao_medido';
  evidencia: string | null;
}

export interface RelatorioDaOperacao {
  gerado_em: string;
  /** De onde vem o relatório: o central (`GET /api/operacoes/{id}/relatorio`, v1.111) ou a montagem do painel (reserva). */
  fonte: 'servidor' | 'painel';
  /** v1.111: a operação rodou em aparelho de verdade ou em simulação; `nao_medido` e `null` (o painel não sabe) nunca viram "real". */
  ambiente: 'real' | 'simulado' | 'nao_medido' | null;
  operacao: {
    id: string; comando: string; app_id: string | null; acao_final: string | null; status: string | null;
    criada_em: string | null; encerrada_em: string | null; assunto: string | null; fontes: string[]; fontes_da_pesquisa: string[];
  };
  /** A resposta objetiva: quantas das N identidades pedidas executam hoje e por que as outras não. `null` = o relatório do painel não tem. */
  identidades: { solicitadas: number | null; executam_hoje: number | null; nao_executam: { motivo: string; n: number }[] } | null;
  criterios: CriterioDoRelatorio[] | null;
  latencia: {
    por_estagio: { estagio: string; rotulo: string; n: number; p50_ms: number | null; p95_ms: number | null; max_ms: number | null }[];
    duracao_mediana_ms: number | null;
    mais_lento: { agente: string; duracao_ms: number } | null;
  } | null;
  capacidade: {
    solicitados: number | null; contas_existentes: number | null; sessoes_validas: number | null; contas_disponiveis: number | null;
    concluidas: number | null; bloqueadas: number | null; em_curso: number | null; motivos: { motivo: string; n: number }[];
  };
  /** `por_peca_usd` (v1.111): o total dividido pelas ações executadas e verificadas; `null` sem nenhuma. */
  custo: { pesquisa_usd: number | null; alvos_usd: number | null; total_usd: number | null; teto_usd: number | null; por_peca_usd: number | null };
  falhas_por_motivo: FalhaPorMotivo[];
  textos: { total: number; distintos: number; repetidos: GrupoDeTextos[]; lista: { agente: string; texto: string }[] };
  agentes: AgenteDoRelatorio[];
  /** As 10 perguntas do dono sobre o que a operação ensinou (adendo v1.96). */
  aprendizado: AprendizadoNoRelatorio;
  /** O que o relatório NÃO tem, para ninguém tomar a ausência por zero. */
  limites: string[];
}

/** Cada limite só aparece quando vale para ESTE relatório: a nota do custo sai quando todos os agentes têm custo (percurso real de 06/10). */
export const limitesDoRelatorio = (agentes: readonly { custo_usd: number | null }[]): string[] => [
  ...(agentes.some((a) => a.custo_usd === null) ? ['Custo por agente "não informado": o alvo ainda não tinha execução (ou o central é anterior ao custo por alvo).'] : []),
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
    motivo: semArrobaOuNulo(a.motivo),
    estagios: ESTAGIOS.map((e, i) => ({
      estagio: e.id, rotulo: e.rotulo, em: alcancados.get(e.id) ?? null, alcancado: alcancados.has(e.id) || i <= ate,
    })),
    conhecimento_ids: a.resultado?.conhecimento_ids ?? [],
    texto: a.resultado?.texto ?? null,
    evidencia_id: a.resultado?.evidencia_id ?? null,
    acao_final: acao ? { tipo: acao.tipo, verificada: conferenciaDaAcao(a), evidencia_id: acao.evidencia_id } : null,
    custo_usd: a.custo_usd,
  };
}

const SEM_LEITURA: LeituraDoAprendizado = { situacao: 'indisponivel', motivo: 'O aprendizado da operação não foi lido para este relatório.' };

export const rotulosDaOperacao = (op: Operacao): Map<string, string> =>
  new Map(op.alvos.flatMap((a) => (a.profile_id && a.persona ? [[a.profile_id, a.persona] as const] : [])));

export function aprendizadoDoRelatorio(rotulos: ReadonlyMap<string, string>, leitura: LeituraDoAprendizado): AprendizadoNoRelatorio {
  if (leitura.situacao === 'indisponivel') {
    return { disponivel: false, motivo: semArroba(leitura.motivo), gerado_em: null, perguntas: [], licoes: { reforcadas: [], contestadas: [] }, avisos: [], nao_coberto: [] };
  }
  const a = leitura.aprendizado;
  const perguntas = a.perguntas.map((p) => ({
      chave: p.chave, titulo: p.titulo, veio: p.veio,
      // Persona sem rótulo conhecido na operação vira "uma persona", nunca o id.
      itens: p.itens.map((i) => ({
        ...i, persona: i.persona === null ? null : rotulos.get(i.persona) ?? 'uma persona',
        resumo: semArrobaOuNulo(i.resumo), motivo: semArrobaOuNulo(i.motivo), fontes: i.fontes.map((f) => ({ ...f, resumo: semArrobaOuNulo(f.resumo) })),
      })),
  }));
  return {
    disponivel: true, motivo: null, gerado_em: a.gerado_em, nao_coberto: a.nao_coberto.map((n) => ({ ...n, motivo: semArroba(n.motivo) })),
    perguntas, licoes: licoesDaOperacao(perguntas),
    avisos: a.avisos.map((v) => ({ ...v, aviso: semArroba(v.aviso) })),
  };
}

/** As falhas por motivo: um agente que parou conta no motivo e no estágio onde parou. */
export function falhasDosAgentes(agentes: readonly AgenteDoRelatorio[]): FalhaPorMotivo[] {
  const falhas = new Map<string, FalhaPorMotivo>();
  for (const a of agentes) {
    if (a.estado !== ROTULO_DO_ESTADO.bloqueado && a.estado !== ROTULO_DO_ESTADO.cancelado) continue;
    const motivo = a.motivo ?? 'sem motivo informado';        // já sem @ (agenteDe)
    const chave = `${motivo}|${a.parou_em ?? ''}`;
    const atual = falhas.get(chave);
    if (atual) atual.agentes += 1;
    else falhas.set(chave, { motivo, parou_em: a.parou_em, agentes: 1 });
  }
  return [...falhas.values()].sort((x, y) => y.agentes - x.agentes || x.motivo.localeCompare(y.motivo));
}

/** Os textos gerados e os repetidos (o mesmo texto, sem diferença de caixa nem de espaço, em mais de um agente). */
export function textosDosAgentes(agentes: readonly AgenteDoRelatorio[]): RelatorioDaOperacao['textos'] {
  const comTexto = agentes.flatMap((a) => (a.texto ? [{ agente: a.agente, texto: a.texto }] : []));
  const grupos = new Map<string, GrupoDeTextos>();
  for (const t of comTexto) {
    const g = grupos.get(normaliza(t.texto));
    if (g) g.agentes.push(t.agente);
    else grupos.set(normaliza(t.texto), { texto: t.texto, agentes: [t.agente] });
  }
  return { total: comTexto.length, distintos: grupos.size, repetidos: [...grupos.values()].filter((g) => g.agentes.length > 1), lista: comTexto };
}

export function montarRelatorio(op: Operacao, agora: Date = new Date(), aprendizado: LeituraDoAprendizado = SEM_LEITURA): RelatorioDaOperacao {
  const agentes = op.alvos.map(agenteDe);
  const c = op.capacidade;
  return {
    gerado_em: agora.toISOString(), fonte: 'painel', ambiente: null,
    operacao: {
      id: op.id, comando: op.command, app_id: op.app_id, acao_final: op.acao_final, status: op.status ? ROTULO_DO_STATUS[op.status] : null,
      criada_em: op.created_at, encerrada_em: op.finished_at, assunto: op.assunto, fontes: op.fontes, fontes_da_pesquisa: [],
    },
    identidades: null, criterios: null, latencia: null,
    capacidade: { ...c, motivos: c.motivos.map((m) => ({ ...m, motivo: semArroba(m.motivo) })) },
    custo: {
      pesquisa_usd: op.custo?.pesquisa_usd ?? null, alvos_usd: op.custo?.alvos_usd ?? null, total_usd: op.custo?.total_usd ?? null, teto_usd: op.max_usd, por_peca_usd: null,
    },
    falhas_por_motivo: falhasDosAgentes(agentes),
    textos: textosDosAgentes(agentes),
    agentes,
    aprendizado: aprendizadoDoRelatorio(rotulosDaOperacao(op), aprendizado),
    limites: limitesDoRelatorio(agentes),
  };
}

const usd = (n: number | null): string => (n === null ? 'não informado' : `US$ ${n.toFixed(4)}`);
/** Milissegundos em palavras; `null` é "não medido", nunca zero. */
const duracao = (ms: number | null | undefined): string => {
  if (ms === null || ms === undefined) return 'não medido';
  if (ms < 1000) return `${ms} ms`;
  const s = Math.round(ms / 1000);
  return s < 60 ? `${s} s` : `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, '0')} s`;
};
const ROTULO_DO_CRITERIO: Record<NonNullable<CriterioDoRelatorio['estado']>, string> = {
  implementado: 'implementado', testado_em_simulacao: 'testado em simulação', provado_real: 'provado de verdade', bloqueado: 'bloqueado', nao_implementado: 'não implementado',
};
export const ROTULO_DO_AMBIENTE: Record<NonNullable<RelatorioDaOperacao['ambiente']>, string> = { real: 'real (aparelhos de verdade)', simulado: 'simulado', nao_medido: 'não medido' };
const ROTULO_NESTA: Record<CriterioDoRelatorio['nesta_operacao'], string> = { sim: 'sim', nao: 'não', nao_medido: 'não medido' };
const num = (n: number | null): string => (n === null ? 'não informado' : String(n));
/** O texto numa citação, linha a linha, para uma quebra de linha do texto não virar título do Markdown. */
const citacao = (t: string): string => t.split(/\r?\n/).map((l) => `> ${l}`).join('\n');

function itemEmMarkdown(i: ItemDoRelatorio): string {
  const marca = i.confianca === 'confirmado' ? 'confirmado' : i.confianca === 'hipotese' ? 'hipótese' : 'confiança não informada';
  const onde = [i.tipo, i.escopo, i.persona ?? 'operação inteira'].filter(Boolean).join(', ');
  const contagem = i.a_favor === null && i.contra === null ? null : `${num(i.a_favor)} a favor, ${num(i.contra)} contra`;
  const extra = [i.inferida ? 'inferida' : null, contagem, i.evidencias ? `${i.evidencias} ${i.evidencias === 1 ? 'evidência' : 'evidências'}` : null, i.motivo ? `motivo: ${i.motivo}` : null]
    .filter(Boolean).join('; ');
  const fontes = i.fontes.length ? ` Fontes: ${i.fontes.map((f) => f.resumo ?? f.ref).join(' | ')}.` : '';
  return `- [${marca}] ${i.resumo ?? i.ref} (${onde}${extra ? `; ${extra}` : ''}).${fontes}`;
}

/** As 10 perguntas do dono: cada uma com os itens ou o "nada nesta operação"; o que não está disponível diz o motivo. */
function aprendizadoEmMarkdown(a: AprendizadoNoRelatorio): string[] {
  const linhas = ['', '## O que a operação ensinou (as 10 perguntas)', ''];
  if (!a.disponivel) return [...linhas, `Não disponível: ${a.motivo ?? 'motivo não informado'}`];
  for (const p of a.perguntas) {
    linhas.push(`### ${p.titulo}`, '');
    if (!p.veio) linhas.push('Esta pergunta não veio na resposta do central.');
    else if (p.itens.length === 0) linhas.push('Nada registrado nesta operação.');
    else linhas.push(...p.itens.map(itemEmMarkdown));
    linhas.push('');
  }
  if (a.nao_coberto.length) {
    linhas.push('### O que o central não responde', '', ...a.nao_coberto.map((n) => `- ${n.chave}: ${n.motivo}`), '');
  }
  linhas.push('### Lições reforçadas', '', ...(a.licoes.reforcadas.length ? a.licoes.reforcadas.map(itemEmMarkdown) : ['Nenhuma.']), '');
  linhas.push('### Lições contestadas', '', ...(a.licoes.contestadas.length ? a.licoes.contestadas.map(itemEmMarkdown) : ['Nenhuma.']), '');
  if (a.avisos.length) {
    linhas.push('### Avisos sobre o conhecimento que o texto recebeu', '',
      ...a.avisos.map((v) => `- ${[v.run_id ? `execução ${v.run_id}` : null, v.step_id ? `etapa ${v.step_id}` : null].filter(Boolean).join(', ') || 'etapa não informada'}: ${v.aviso}`), '');
  }
  return linhas;
}

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
  if (o.fontes_da_pesquisa.length) linhas.push(`- **Fontes da pesquisa:** ${o.fontes_da_pesquisa.join(', ')}`);
  if (r.ambiente) linhas.push(`- **Ambiente:** ${ROTULO_DO_AMBIENTE[r.ambiente]}`);
  linhas.push(`- **Montado por:** ${r.fonte === 'servidor' ? 'o central (GET /api/operacoes/{id}/relatorio)' : 'o painel, do estado da operação (o central não entregou o relatório)'}`);
  if (r.identidades) {
    const i = r.identidades;
    linhas.push('', '## Identidades', '', `**${num(i.executam_hoje)} de ${num(i.solicitadas)}** identidades solicitadas executam hoje.`);
    for (const n of i.nao_executam) linhas.push(`- Não executam: ${n.motivo}: ${n.n}`);
  }
  linhas.push(
    '', '## Capacidade', '',
    '| solicitados | contas existentes | sessões válidas | disponíveis | em curso | concluídas | bloqueadas |', '|---|---|---|---|---|---|---|',
    `| ${num(c.solicitados)} | ${num(c.contas_existentes)} | ${num(c.sessoes_validas)} | ${num(c.contas_disponiveis)} | ${num(c.em_curso)} | ${num(c.concluidas)} | ${num(c.bloqueadas)} |`,
    '', '## Custo', '',
    `- Pesquisa externa: ${usd(r.custo.pesquisa_usd)}`, `- Agentes: ${usd(r.custo.alvos_usd)}`,
    `- **Total:** ${usd(r.custo.total_usd)} (teto da operação: ${usd(r.custo.teto_usd)})`,
  );
  if (r.fonte === 'servidor') linhas.push(`- Por peça (o total dividido pelas ações executadas e verificadas): ${r.custo.por_peca_usd === null ? 'sem nenhuma ação verificada' : usd(r.custo.por_peca_usd)}`);
  linhas.push('', '## Falhas por motivo', '');
  if (r.falhas_por_motivo.length === 0) linhas.push('Nenhum agente parou.');
  for (const f of r.falhas_por_motivo) linhas.push(`- ${f.motivo}${f.parou_em ? ` (parou em ${f.parou_em})` : ''}: ${f.agentes} ${f.agentes === 1 ? 'agente' : 'agentes'}`);
  linhas.push('', '## Textos gerados (irmãos da operação)', '');
  linhas.push(`${r.textos.total} ${r.textos.total === 1 ? 'texto' : 'textos'}, ${r.textos.distintos} ${r.textos.distintos === 1 ? 'distinto' : 'distintos'}.`);
  if (r.textos.repetidos.length) {
    linhas.push('', 'Repetidos (mesmo texto em mais de um agente):');
    for (const g of r.textos.repetidos) linhas.push('', citacao(g.texto), '', `Agentes: ${g.agentes.join(', ')}.`);
  }
  for (const t of r.textos.lista) linhas.push('', `**${t.agente}**`, '', citacao(t.texto));
  if (r.latencia) {
    const l = r.latencia;
    linhas.push('', '## Latência', '', `- Duração mediana por agente: ${duracao(l.duracao_mediana_ms)}`,
      `- Agente mais lento: ${l.mais_lento ? `${l.mais_lento.agente}, ${duracao(l.mais_lento.duracao_ms)}` : 'não medido'}`);
    if (l.por_estagio.length) {
      linhas.push('', '| estágio | agentes | mediana | p95 | maior |', '|---|---|---|---|---|');
      for (const e of l.por_estagio) linhas.push(`| ${e.rotulo} | ${e.n} | ${duracao(e.p50_ms)} | ${duracao(e.p95_ms)} | ${duracao(e.max_ms)} |`);
    }
  }
  if (r.criterios) {
    linhas.push('', '## Critérios do diagnóstico', '', '| critério | estado | nesta operação | evidência |', '|---|---|---|---|');
    for (const c of r.criterios) linhas.push(`| ${c.id}. ${c.nome} | ${c.estado ? ROTULO_DO_CRITERIO[c.estado] : 'não informado'} | ${ROTULO_NESTA[c.nesta_operacao]} | ${c.evidencia ?? 'nenhuma'} |`);
  }
  linhas.push(...aprendizadoEmMarkdown(r.aprendizado));
  linhas.push('', '## Agentes');
  for (const a of r.agentes) {
    linhas.push('', `### ${a.agente}`, '', `- **Estado:** ${a.estado}${a.parou_em ? `, parou em ${a.parou_em}` : ''}${a.motivo ? ` (${a.motivo})` : ''}`);
    if (a.aparelho) linhas.push(`- **Aparelho:** ${a.aparelho}`);
    linhas.push(`- **Conhecimento usado:** ${a.conhecimento_ids === null ? 'não informado pelo relatório do central' : a.conhecimento_ids.length ? a.conhecimento_ids.join(', ') : 'nenhum registrado'}`);
    if (a.duracao_ms !== undefined || a.espera_do_liberar_ms !== undefined) linhas.push(`- **Duração:** ${duracao(a.duracao_ms)} · **Espera pela aprovação:** ${duracao(a.espera_do_liberar_ms)}`);
    linhas.push(a.acao_final
      ? `- **Ação final:** ${rotuloDaAcao(a.acao_final.tipo)} · verificada: ${ROTULO_DA_CONFERENCIA[a.acao_final.verificada]} · evidência: ${a.acao_final.evidencia_id ?? 'nenhuma'}`
      : '- **Ação final:** sem ação final');
    linhas.push(`- **Evidência da tela lida:** ${a.evidencia_id ?? 'nenhuma'}`, `- **Custo de IA:** ${usd(a.custo_usd)}`);
    linhas.push('', '| estágio | hora |', '|---|---|');
    for (const e of a.estagios) linhas.push(`| ${e.rotulo} | ${e.em ?? (e.alcancado ? 'alcançado, sem hora' : 'não alcançado')}${e.etapa_ms !== undefined && e.etapa_ms !== null ? ` (+${duracao(e.etapa_ms)})` : ''} |`);
    if (a.texto) linhas.push('', 'Texto gerado:', '', citacao(a.texto));
  }
  linhas.push('', '## O que este relatório não tem', '', ...r.limites.map((l) => `- ${l}`), '');
  return linhas.join('\n');
}
