import { isRecord } from '../../lib/format';

/**
 * Uma linha de `per_instance` do relatório (`RunService.report`), lida para o cartão da instância.
 *
 * O contrato só fixa as chaves de topo do relatório; o conteúdo de cada linha é livre. Tudo aqui é defensivo: campo
 * ausente vira `null`/lista vazia, e o que não é conhecido volta em `extras` para o cartão mostrar em vez de sumir.
 *
 * As três listas de etapas NÃO se fundem: `proven_steps` e `manually_confirmed_steps` cobrem todas as versões do plano,
 * e `open_steps` só a versão final. A mesma etapa pode estar comprovada numa versão antiga e em aberto na final (02ee9e);
 * juntar as listas esconderia a falha.
 */
export interface EtapaComprovada {
  titulo: string;
  /** A prova gravada ("pós-condição comprovada pela árvore local…"); `null` quando a linha não tinha o separador. */
  prova: string | null;
}

export interface EfeitoExterno {
  /** Instante ISO do efeito; `null` quando a linha não começa com um. */
  quando: string | null;
  /** Título da etapa entre aspas simples, quando dá para separar. */
  etapa: string | null;
  texto: string;
}

export interface ResultadoDaInstancia {
  instanceId: string | null;
  status: string | null;
  /** O que aconteceu, numa frase: `detail`, ou o `summary`/`status_detail` de formatos mais antigos. */
  texto: string | null;
  /** `blocked_reason` só quando diz algo além do `texto` (o escalonador costuma gravar os dois iguais). */
  motivo: string | null;
  /** `needs`: o que a pessoa precisa fazer. */
  falta: string | null;
  servidor: string | null;
  serial: string | null;
  /** `proven` cru: só `true` vira "Comprovado". */
  comprovado: boolean | null;
  entrega: string | null;
  /** `null` = o relatório não trouxe a lista (formato antigo); `[]` = trouxe vazia. */
  comprovadas: EtapaComprovada[] | null;
  aMao: string[] | null;
  emAberto: string[] | null;
  efeitos: EfeitoExterno[] | null;
  versaoDoPlano: number | null;
  chamadasDeIa: number | null;
  tokensDeIa: number | null;
  /** Chaves que o cartão não conhece, na ordem em que vieram. */
  extras: [string, unknown][];
}

const TEXTO_KEYS = ['detail', 'summary', 'status_detail'] as const;

const CONHECIDAS = new Set([
  'instance_id', 'status', ...TEXTO_KEYS, 'worker_id', 'device_serial', 'proven', 'delivery_level', 'blocked_reason',
  'needs', 'effects', 'proven_steps', 'manually_confirmed_steps', 'open_steps', 'plan_versions', 'ai_calls', 'ai_tokens',
]);

function texto(v: unknown): string | null {
  return typeof v === 'string' && v.trim() ? v : null;
}

function numero(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}

/** Lista de textos; qualquer item que não seja texto vira o JSON dele (nada some). */
function textos(v: unknown): string[] | null {
  if (!Array.isArray(v)) return null;
  return v.filter((x) => x !== null && x !== undefined && x !== '').map((x) => (typeof x === 'string' ? x : JSON.stringify(x)));
}

/** `"{título}: {prova}"` (o formato de `proven_steps`); sem o separador, a linha inteira é o título. */
export function separarProva(linha: string): EtapaComprovada {
  const i = linha.indexOf(': ');
  if (i <= 0) return { titulo: linha, prova: null };
  return { titulo: linha.slice(0, i), prova: linha.slice(i + 2) || null };
}

const EFEITO = /^(\d{4}-\d{2}-\d{2}T[\d:.]+(?:Z|[+-]\d{2}:?\d{2})?)\s+[—-]\s+([\s\S]*)$/;
const ETAPA_DO_EFEITO = /^'([^']+)':\s*([\s\S]*)$/;

/** `"{ISO} — '{etapa}': {texto}"` (o formato de `effects`); o que não casar fica inteiro em `texto`. */
export function separarEfeito(linha: string): EfeitoExterno {
  const m = EFEITO.exec(linha);
  const quando = m?.[1] ?? null;
  const resto = m?.[2] ?? linha;
  const e = ETAPA_DO_EFEITO.exec(resto);
  return e ? { quando, etapa: e[1] ?? null, texto: e[2] ?? '' } : { quando, etapa: null, texto: resto };
}

export function lerResultado(row: Record<string, unknown>): ResultadoDaInstancia {
  const candidatos = TEXTO_KEYS.map((k) => [k, texto(row[k])] as const).filter(([, v]) => v !== null);
  const principal = candidatos[0]?.[1] ?? null;
  const motivo = texto(row.blocked_reason);
  const efeitos = textos(row.effects);
  const comprovadas = textos(row.proven_steps);
  const extras: [string, unknown][] = [];
  for (const [k, v] of Object.entries(row)) {
    if (!CONHECIDAS.has(k)) extras.push([k, v]);
  }
  // Um segundo texto (ex.: `summary` além de `detail`) que diga outra coisa também aparece.
  for (const [k, v] of candidatos.slice(1)) {
    if (v !== principal) extras.push([k, v]);
  }
  return {
    instanceId: texto(row.instance_id),
    status: texto(row.status),
    texto: principal,
    motivo: motivo && motivo.trim() !== principal?.trim() ? motivo : null,
    falta: texto(row.needs),
    servidor: texto(row.worker_id),
    serial: texto(row.device_serial),
    comprovado: typeof row.proven === 'boolean' ? row.proven : null,
    entrega: texto(row.delivery_level),
    comprovadas: comprovadas ? comprovadas.map(separarProva) : null,
    aMao: textos(row.manually_confirmed_steps),
    emAberto: textos(row.open_steps),
    efeitos: efeitos ? efeitos.map(separarEfeito) : null,
    versaoDoPlano: numero(row.plan_versions),
    chamadasDeIa: numero(row.ai_calls),
    tokensDeIa: numero(row.ai_tokens),
    extras,
  };
}

export type SeloDaProva = 'comprovado' | 'a_mao' | 'sem_prova' | null;

/**
 * O selo ao lado da situação. "Comprovado" só com `proven: true` (sucesso sem nenhuma etapa confirmada à mão, como o
 * backend calcula). Sucesso sem `proven` é o "SUCESSO com etapa confirmada manualmente" do Markdown: nunca vira
 * comprovado. Sem o campo (formato antigo), nenhum selo: não se inventa prova.
 */
export function seloDaProva(r: ResultadoDaInstancia): SeloDaProva {
  if (r.comprovado === true) return 'comprovado';
  if (r.status !== 'succeeded' || r.comprovado === null) return null;
  return r.aMao && r.aMao.length > 0 ? 'a_mao' : 'sem_prova';
}

/** Linhas do relatório que são objetos; `null` se alguma não for (aí a tela cai para a árvore genérica). */
export function lerResultados(rows: unknown[]): ResultadoDaInstancia[] | null {
  if (!rows.every(isRecord)) return null;
  return rows.map((r) => lerResultado(r as Record<string, unknown>));
}
