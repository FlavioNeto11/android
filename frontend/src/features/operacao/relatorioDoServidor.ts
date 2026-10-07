/**
 * 31.197: o relatório que o CENTRAL monta (`GET /api/operacoes/{id}/relatorio`, adendo v1.111, Jev 31.195), lido para o mesmo formato do
 * relatório do painel (`RelatorioDaOperacao`): quem baixa e quem lê a tela veem um relatório só, venha ele de onde vier. O formato abaixo
 * é o do RASCUNHO do Jev (o adendo ainda não foi publicado). Regra de "não medido": número, texto ou id vem `null` e nunca vira zero; o
 * estado de 3 vias (`sim`/`nao`/`nao_medido`) fica como veio. O @ de conta já sai do central ("@[omitido]"); por garantia passa de novo
 * por `semArroba`, menos o texto gerado pela persona, que não é metadado.
 */
import { lerAprendizado } from './aprendizadoDaOperacao';
import { ESTAGIOS, ROTULO_DO_ESTADO, ROTULO_DO_STATUS, lerEstagio, rotuloDoEstagio, type EstadoDoAlvo, type StatusDaOperacao } from './modelo';
import {
  LIMITES, aprendizadoDoRelatorio, falhasDosAgentes, semArroba, textosDosAgentes,
  type AgenteDoRelatorio, type CriterioDoRelatorio, type Conferencia, type RelatorioDaOperacao,
} from './relatorio';

const registro = (v: unknown): Record<string, unknown> | null => (v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : null);
const lista = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);
const texto = (v: unknown): string | null => (typeof v === 'string' && v.trim() ? v : null);
const limpo = (v: unknown): string | null => { const t = texto(v); return t === null ? null : semArroba(t); };
const inteiro = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) && v >= 0 ? Math.trunc(v) : null);
const usd = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) && v >= 0 ? v : null);

const CONFERENCIAS: readonly Conferencia[] = ['sim', 'nao', 'nao_conferida', 'sem_acao'];
const ESTADOS_DO_CRITERIO: readonly NonNullable<CriterioDoRelatorio['estado']>[] = ['implementado', 'testado_em_simulacao', 'provado_real', 'bloqueado', 'nao_implementado'];
const AMBIENTES = ['real', 'simulado', 'nao_medido'] as const;
const NESTA: readonly CriterioDoRelatorio['nesta_operacao'][] = ['sim', 'nao', 'nao_medido'];

/** A etapa em palavras: o estágio conhecido vira o rótulo; o que o painel não conhece fica como o central disse. */
const rotuloDaParada = (v: unknown): string | null => {
  const t = texto(v);
  if (t === null) return null;
  const e = lerEstagio(t);
  return e ? rotuloDoEstagio(e) : semArroba(t);
};

function lerAgente(v: unknown, posicao: number): AgenteDoRelatorio | null {
  const o = registro(v);
  if (!o) return null;
  const doCentral = new Map(lista(o.estagios).flatMap((e) => {
    const r = registro(e);
    const id = r ? lerEstagio(r.estagio) : null;
    return r && id ? [[id, { em: texto(r.em), etapa_ms: inteiro(r.etapa_ms) }] as const] : [];
  }));
  const ultimo = lerEstagio(o.estagio);
  // Sem a lista de estágios, o último alcançado e os anteriores contam, sem hora (como no relatório do painel).
  const ate = doCentral.size === 0 && ultimo ? ESTAGIOS.findIndex((e) => e.id === ultimo) : -1;
  const acao = registro(o.acao_final);
  const estado = typeof o.estado === 'string' && o.estado in ROTULO_DO_ESTADO ? ROTULO_DO_ESTADO[o.estado as EstadoDoAlvo] : 'não informado';
  const verificada = CONFERENCIAS.find((c) => c === acao?.verificada) ?? 'nao_conferida';
  return {
    agente: limpo(o.persona) ?? `Persona ${posicao + 1}`,
    aparelho: texto(o.aparelho),
    estado,
    parou_em: rotuloDaParada(o.parou_em),
    motivo: limpo(o.motivo),
    estagios: ESTAGIOS.map((e, i) => {
      const x = doCentral.get(e.id);
      return { estagio: e.id, rotulo: e.rotulo, em: x?.em ?? null, alcancado: x !== undefined || i <= ate, ...(x ? { etapa_ms: x.etapa_ms } : {}) };
    }),
    // O relatório do central não traz o conhecimento nem a evidência da tela por agente: "não informado", nunca "nenhum".
    conhecimento_ids: null,
    texto: texto(o.texto),
    evidencia_id: null,
    acao_final: acao ? { tipo: texto(acao.tipo), verificada, evidencia_id: inteiro(acao.evidencia_id) } : null,
    custo_usd: usd(o.custo_usd),
    duracao_ms: inteiro(o.duracao_ms),
    espera_do_liberar_ms: inteiro(o.espera_do_liberar_ms),
  };
}

function lerCriterio(v: unknown): CriterioDoRelatorio | null {
  const o = registro(v);
  const id = o ? (typeof o.id === 'number' ? String(o.id) : texto(o.id)) : null;
  const nome = o ? texto(o.nome) : null;
  if (!o || !id || !nome) return null;
  return {
    id, nome: semArroba(nome), estado: ESTADOS_DO_CRITERIO.find((e) => e === o.estado) ?? null,
    nesta_operacao: NESTA.find((n) => n === o.nesta_operacao) ?? 'nao_medido', evidencia: limpo(o.evidencia),
  };
}

const motivos = (v: unknown): { motivo: string; n: number }[] => lista(v).flatMap((m) => {
  const r = registro(m);
  const motivo = r ? limpo(r.motivo) : null;
  const n = r ? inteiro(r.n) : null;
  return motivo && n !== null ? [{ motivo, n }] : [];
});

/** `null` quando a resposta não é o relatório (sem a operação nem a lista de agentes): erro de leitura, nunca relatório vazio. */
export function relatorioDoServidor(v: unknown): RelatorioDaOperacao | null {
  const o = registro(v);
  const op = o ? registro(o.operacao) : null;
  const id = op ? texto(op.id) : null;
  if (!o || !op || !id || !Array.isArray(o.agentes)) return null;
  const agentes = o.agentes.map(lerAgente).filter((a): a is AgenteDoRelatorio => a !== null);
  const rotulos = new Map(o.agentes.flatMap((a) => { const r = registro(a); const p = r ? texto(r.profile_id) : null; const n = r ? limpo(r.persona) : null; return p && n ? [[p, n] as const] : []; }));
  const cap = registro(o.capacidade) ?? {};
  const ident = registro(o.identidades);
  const lat = registro(o.latencia);
  const maisLento = lat ? registro(lat.mais_lento) : null;
  const custo = registro(o.custo) ?? {};
  const aprendizadoBruto = registro(o.aprendizado);
  const aprendizado = aprendizadoBruto?.disponivel === false
    ? { situacao: 'indisponivel' as const, motivo: texto(aprendizadoBruto.motivo) ?? 'O central não disse por que o aprendizado não está disponível.' }
    : (() => { const l = lerAprendizado(o.aprendizado); return l ? { situacao: 'lido' as const, aprendizado: l } : { situacao: 'indisponivel' as const, motivo: 'O aprendizado do relatório veio em formato inesperado.' }; })();
  const status = typeof op.status === 'string' && op.status in ROTULO_DO_STATUS ? ROTULO_DO_STATUS[op.status as StatusDaOperacao] : limpo(op.status);
  return {
    gerado_em: texto(o.gerado_em) ?? '', fonte: 'servidor',
    ambiente: AMBIENTES.find((a) => a === (o.ambiente ?? op.ambiente)) ?? null,
    operacao: {
      id, comando: limpo(op.comando) ?? '', app_id: texto(op.app_id), acao_final: texto(op.acao_final), status,
      criada_em: texto(op.criada_em), encerrada_em: texto(op.encerrada_em), assunto: limpo(op.assunto),
      fontes: lista(op.fontes).filter((f): f is string => typeof f === 'string' && f.trim() !== ''),
      fontes_da_pesquisa: lista(op.fontes_da_pesquisa).filter((f): f is string => typeof f === 'string' && f.trim() !== ''),
    },
    identidades: ident ? { solicitadas: inteiro(ident.solicitadas), executam_hoje: inteiro(ident.executam_hoje), nao_executam: motivos(ident.nao_executam) } : null,
    capacidade: {
      solicitados: inteiro(cap.solicitados), contas_existentes: inteiro(cap.contas_existentes), sessoes_validas: inteiro(cap.sessoes_validas),
      contas_disponiveis: inteiro(cap.contas_disponiveis), concluidas: inteiro(cap.concluidas), bloqueadas: inteiro(cap.bloqueadas), em_curso: inteiro(cap.em_curso),
      motivos: motivos(cap.motivos),
    },
    custo: { pesquisa_usd: usd(custo.pesquisa_usd), alvos_usd: usd(custo.alvos_usd), total_usd: usd(custo.total_usd), teto_usd: usd(custo.teto_usd), por_peca_usd: usd(custo.por_peca_usd) },
    falhas_por_motivo: Array.isArray(o.falhas_por_motivo)
      ? o.falhas_por_motivo.flatMap((f) => {
        const r = registro(f);
        const motivo = r ? limpo(r.motivo) : null;
        const n = r ? inteiro(r.agentes) : null;
        return r && motivo && n !== null ? [{ motivo, parou_em: rotuloDaParada(r.parou_em), agentes: n }] : [];
      })
      : falhasDosAgentes(agentes),
    textos: textosDosAgentes(agentes),
    agentes,
    criterios: Array.isArray(o.criterios) ? o.criterios.map(lerCriterio).filter((c): c is CriterioDoRelatorio => c !== null) : null,
    aprendizado: aprendizadoDoRelatorio(rotulos, aprendizado),
    latencia: lat ? {
      por_estagio: Object.entries(registro(lat.por_estagio) ?? {}).flatMap(([k, x]) => {
        const e = lerEstagio(k);
        const r = registro(x);
        const n = r ? inteiro(r.n) : null;
        return e && r && n !== null ? [{ estagio: e, rotulo: rotuloDoEstagio(e), n, p50_ms: inteiro(r.p50_ms), p95_ms: inteiro(r.p95_ms), max_ms: inteiro(r.max_ms) }] : [];
      }).sort((a, b) => ESTAGIOS.findIndex((e) => e.id === a.estagio) - ESTAGIOS.findIndex((e) => e.id === b.estagio)),
      duracao_mediana_ms: inteiro(lat.duracao_mediana_ms),
      mais_lento: maisLento && inteiro(maisLento.duracao_ms) !== null
        ? { agente: rotulos.get(texto(maisLento.profile_id) ?? '') ?? 'uma persona', duracao_ms: inteiro(maisLento.duracao_ms)! } : null,
    } : null,
    limites: LIMITES,
  };
}
