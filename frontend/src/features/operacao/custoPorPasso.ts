/**
 * 31.228 (parte 2): o modelo que decidiu e o custo de CADA passo de um agente, e as somas por modelo e por estágio (adendo v1.124, Jev,
 * 31.229): `GET /api/operacoes/{id}` traz `alvos[].custo_por_passo` e, na operação, `custo_por_modelo` e `custo_por_estagio`. Serve para
 * medir o 31.223 (o modelo de ação decide a navegação; o forte só o commit). Leitura tolerante: campo torto vira "não informado", nunca
 * zero; um passo inválido invalida o objeto inteiro (a tela cai no custo por função e modelo, que não depende dele).
 */
import { formatUsd } from '../usage/usage';

export interface SomaPorModelo { modelo: string; chamadas: number | null; custoUsd: number | null }
export interface SomaPorEstagio { estagio: string; chamadas: number | null; custoUsd: number | null }

/** A última ação com efeito não rejeitada e o decide que a escolheu; `fonte: 'recipe'` = a receita executou, sem modelo. */
export interface CommitDoPasso { fonte: 'ai' | 'recipe' | null; modelo: string | null; tier: number | null; escalate: string | null }

export interface PassoDoAlvo {
  stepId: string;
  seq: number | null;
  chave: string;
  capacidade: string | null;
  /** A etapa declara efeito externo (publica, comenta, envia). */
  efeito: boolean;
  estagio: string | null;
  /** O id cru do modelo do último decide ok da etapa; `null` = só receita. */
  modelo: string | null;
  /** `null` = a etapa não chegou ao commit (ou não tem efeito). */
  commit: CommitDoPasso | null;
  chamadas: number | null;
  custoUsd: number | null;
  porModelo: SomaPorModelo[];
}

export interface CustoPorPasso {
  passos: PassoDoAlvo[];
  /** Planejamento e chamadas sem etapa. */
  semPasso: { chamadas: number | null; custoUsd: number | null; porModelo: SomaPorModelo[] } | null;
  porModelo: SomaPorModelo[];
  porEstagio: SomaPorEstagio[];
}

const registro = (v: unknown): Record<string, unknown> | null => (v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : null);
const texto = (v: unknown): string | null => (typeof v === 'string' && v.trim() ? v.trim() : null);
const inteiro = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) && v >= 0 ? Math.trunc(v) : null);
const usd = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) && v >= 0 ? v : null);

/** `[{modelo, chamadas, custo_usd}]`; `null` quando não é uma lista (um item sem modelo é descartado: não há o que mostrar dele). */
export function lerSomaPorModelo(v: unknown): SomaPorModelo[] | null {
  if (!Array.isArray(v)) return null;
  return v.flatMap((i): SomaPorModelo[] => {
    const o = registro(i);
    const modelo = o ? texto(o.modelo) : null;
    return o && modelo ? [{ modelo, chamadas: inteiro(o.chamadas), custoUsd: usd(o.custo_usd) }] : [];
  });
}

/** `{estagio: {chamadas, custo_usd}}`, na ordem em que o servidor mandou; `null` quando não é um mapa. */
export function lerSomaPorEstagio(v: unknown): SomaPorEstagio[] | null {
  const o = registro(v);
  if (!o) return null;
  return Object.entries(o).flatMap(([estagio, bruto]): SomaPorEstagio[] => {
    const r = registro(bruto);
    return r && estagio.trim() ? [{ estagio, chamadas: inteiro(r.chamadas), custoUsd: usd(r.custo_usd) }] : [];
  });
}

function lerCommit(v: unknown): CommitDoPasso | null {
  const o = registro(v);
  if (!o) return null;
  const fonte = o.fonte === 'ai' || o.fonte === 'recipe' ? o.fonte : null;
  return { fonte, modelo: texto(o.modelo), tier: typeof o.tier === 'number' && Number.isFinite(o.tier) ? o.tier : null, escalate: texto(o.escalate) };
}

function lerPasso(v: unknown): PassoDoAlvo | null {
  const o = registro(v);
  const stepId = o ? texto(o.step_id) : null;
  const chave = o ? texto(o.key) : null;
  if (!o || !stepId || !chave) return null;
  return {
    stepId, seq: inteiro(o.seq), chave, capacidade: texto(o.capability), efeito: o.efeito === true, estagio: texto(o.estagio), modelo: texto(o.modelo),
    commit: lerCommit(o.commit), chamadas: inteiro(o.chamadas), custoUsd: usd(o.custo_usd), porModelo: lerSomaPorModelo(o.por_modelo) ?? [],
  };
}

/** O `custo_por_passo` de um alvo. `null` quando ausente, com formato inesperado ou com um passo inválido. */
export function lerCustoPorPasso(v: unknown): CustoPorPasso | null {
  const o = registro(v);
  if (!o || !Array.isArray(o.passos)) return null;
  const passos = o.passos.map(lerPasso);
  if (passos.some((p) => p === null)) return null;
  const sem = registro(o.sem_passo);
  return {
    passos: passos as PassoDoAlvo[],
    semPasso: sem ? { chamadas: inteiro(sem.chamadas), custoUsd: usd(sem.custo_usd), porModelo: lerSomaPorModelo(sem.por_modelo) ?? [] } : null,
    porModelo: lerSomaPorModelo(o.por_modelo) ?? [],
    porEstagio: lerSomaPorEstagio(o.por_estagio) ?? [],
  };
}

const FAMILIAS: Record<string, string> = { opus: 'Opus', sonnet: 'Sonnet', haiku: 'Haiku', fable: 'Fable' };

/** "claude-sonnet-5-5" → "Sonnet 5.5"; um id que não segue o padrão aparece como veio (o rótulo é da tela, o id cru vai no `title`). */
export function rotuloDoModelo(id: string): string {
  const m = /^claude-(opus|sonnet|haiku|fable)-(\d+)(?:-(\d{1,2}))?(?:-\d{8})?$/.exec(id);
  if (!m) return id;
  return `${FAMILIAS[m[1]!]} ${m[2]}${m[3] ? `.${m[3]}` : ''}`;
}

/** Quem executou o commit do passo, em palavras; `null` quando o passo não tem commit a dizer. */
export function commitEmPalavras(p: Pick<PassoDoAlvo, 'efeito' | 'commit'>): string | null {
  if (!p.commit) return p.efeito ? 'não chegou ao commit' : null;
  if (p.commit.fonte === 'recipe') return 'por receita, sem modelo';
  const quem = p.commit.modelo ? rotuloDoModelo(p.commit.modelo) : 'modelo não informado';
  const forte = p.commit.escalate === 'efeito' || p.commit.tier === 1 ? ', escalonado pelo efeito (forte)' : '';
  return `${quem}${forte}`;
}

export const usdOuNaoInformado = (v: number | null): string => (v === null ? 'não informado' : formatUsd(v));
