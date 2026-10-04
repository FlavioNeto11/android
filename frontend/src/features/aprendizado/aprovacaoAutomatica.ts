import { metaDeSaude } from './detalhe';
import { isLivroKind, rotuloDoKind, semLacunas, type LivroKind } from './model';
import { textoDaDecisao } from './parecer';

/**
 * A aprovação automática por política no painel (30.55; rota `GET /api/aprendizado/aprovacao-automatica`, adendo
 * v1.24). Os tipos espelham `application/aprovacao_automatica.relatorio`; a leitura é tolerante (um campo ausente não
 * derruba a aba, e um backend sem a rota deixa a seção escondida).
 */

export type ModoDaAprovacao = 'off' | 'shadow' | 'on';
export type GestoDaPlataforma = 'publicar' | 'confirmar_que_fica';

export interface DecisaoDaPlataforma {
  item_ref: string;
  kind: LivroKind | null;
  ref: string;
  titulo: string | null;
  app: string | null;
  /** O estado de AGORA do item: Desligar só vale no que segue `published`. */
  estado: string | null;
  gesto: GestoDaPlataforma;
  regra: string | null;
  motivo: string;
  em: string;
}

export interface UltimaVolta {
  em: string;
  avaliados: number;
  decidiria: string[];
  decididos: string[];
}

export interface RelatorioDaAprovacao {
  modo: ModoDaAprovacao;
  ultima_volta: UltimaVolta | null;
  casos_na_sombra: number;
  decididos: DecisaoDaPlataforma[];
}

const texto = (v: unknown): string | null => (typeof v === 'string' && v ? v : null);
const inteiro = (v: unknown): number => (typeof v === 'number' && Number.isFinite(v) ? v : 0);
const textos = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : []);
const objeto = (v: unknown): Record<string, unknown> | null =>
  (v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : null);

const MODOS: readonly ModoDaAprovacao[] = ['off', 'shadow', 'on'];

function lerDecisao(v: unknown): DecisaoDaPlataforma | null {
  const o = objeto(v);
  const itemRef = texto(o?.item_ref);
  if (!o || !itemRef) return null;
  const [kindDoRef, ...resto] = itemRef.split(':');
  const kind = o.kind ?? kindDoRef;
  return {
    item_ref: itemRef,
    kind: isLivroKind(kind) ? kind : null,
    ref: texto(o.ref) ?? resto.join(':'),
    titulo: texto(o.titulo),
    app: texto(o.app),
    estado: texto(o.estado),
    gesto: o.gesto === 'publicar' ? 'publicar' : 'confirmar_que_fica',
    regra: texto(o.regra),
    motivo: texto(o.motivo) ?? '',
    em: texto(o.em) ?? '',
  };
}

export function lerRelatorioDaAprovacao(raw: unknown): RelatorioDaAprovacao {
  const o = objeto(raw) ?? {};
  const v = objeto(o.ultima_volta);
  const modo = MODOS.find((m) => m === o.modo) ?? 'off';
  return {
    modo,
    ultima_volta: v ? {
      em: texto(v.em) ?? '', avaliados: inteiro(v.avaliados), decidiria: textos(v.decidiria), decididos: textos(v.decididos),
    } : null,
    casos_na_sombra: inteiro(o.casos_na_sombra),
    decididos: (Array.isArray(o.decididos_pela_plataforma) ? o.decididos_pela_plataforma : [])
      .map(lerDecisao).filter((d): d is DecisaoDaPlataforma => d !== null),
  };
}

/** Os fatos do motivo, sem o prefixo da regra (`auto:<regra> v1 — `) nem o da confirmação: é o que a pessoa lê. */
export function fatosDoMotivo(motivo: string): string {
  const i = motivo.indexOf(' — ');
  const fatos = i >= 0 ? motivo.slice(i + 3) : motivo;
  // 30.63: os rótulos que a própria tela usa, não a palavra crua da régua ("saúde pouca_amostra", "parecer observar (lr-…)").
  // 30.66: o parecer com o rótulo das outras telas ("pedir mais evidência"), não a chave sem acento.
  return fatos
    .replace(/saúde ([a-z_]+)/g, (_m, r: string) => `saúde: ${metaDeSaude(r)?.label ?? r}`)
    .replace(/parecer ([a-z_]+) \(lr-[0-9a-f]+\)/g, (_m, d: string) => `parecer do curador: ${textoDaDecisao(d).toLowerCase()}`);
}

/** 30.66: a regra da régua em português, como em Pendências > Decidido sozinho; o id cru fica no `title`. */
const ROTULO_DA_REGRA: Record<string, string> = {
  qa_para_aprovar: 'Aprovação automática do que esperava você',
  qa_revisar: 'Confirmação automática do que estava em revisão',
};

export function rotuloDaRegra(regra: string): string {
  return ROTULO_DA_REGRA[regra] ?? `Regra automática ${regra}`;
}

/** A chave interna com a versão ("send_message_i1 (v1)"): sem o nome da capability, não diz nada ao dono. */
const RE_CHAVE_COM_VERSAO = /^([a-z][a-z0-9]*(?:_[a-z0-9]+)*) \(v(\d+)\)$/;

/**
 * 30.66: o título de uma decisão para a pessoa. O item decidido já saiu das filas, então o nome bonito do livro
 * (`doLivro`, com o nome da capability) quase nunca está à mão. Sem ele: as lacunas cruas ("{recipient_1}") viram "…" e a
 * chave interna com versão ganha o tipo e o número do item na frente ("Receita nº 180 · send message (v1)").
 */
export function tituloDaDecisao(d: Pick<DecisaoDaPlataforma, 'titulo' | 'kind' | 'ref' | 'item_ref'>,
                                doLivro?: string): string {
  if (doLivro && doLivro !== d.item_ref) return doLivro;
  const nome = d.kind ? `${rotuloDoKind(d.kind)}${d.kind === 'receita' ? ' nº' : ''} ${d.ref}` : d.item_ref;
  const t = d.titulo ? semLacunas(d.titulo) : '';
  if (!t || t === '…') return nome;
  const m = RE_CHAVE_COM_VERSAO.exec(t);
  if (m) return `${nome} · ${(m[1] ?? '').replace(/_i\d+$/, '').replace(/_/g, ' ')} (v${m[2]})`;
  return t;
}

export const ROTULO_DO_GESTO: Record<GestoDaPlataforma, string> = {
  publicar: 'Publicou',
  confirmar_que_fica: 'Confirmou que fica',
};
