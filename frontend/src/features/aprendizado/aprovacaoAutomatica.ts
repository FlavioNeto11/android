import { isLivroKind, type LivroKind } from './model';

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
  return i >= 0 ? motivo.slice(i + 3) : motivo;
}

export const ROTULO_DO_GESTO: Record<GestoDaPlataforma, string> = {
  publicar: 'Publicou',
  confirmar_que_fica: 'Confirmou que fica',
};
