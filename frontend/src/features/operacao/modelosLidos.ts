/**
 * 31.228: qual modelo decidiu o que, e quanto custou, num agente (alvo) da operação. Lido de `GET /api/usage?run_id=` (as chamadas da
 * execução do alvo, por função × modelo × escalonamento): é o que o central já traz hoje; o que decidiu CADA passo e a soma por estágio
 * dependem de um contrato novo (chamada por etapa) e ficam fora até ele existir. Serve para medir o 31.223 (o modelo barato decide a
 * navegação, o forte só o que tem efeito): a divisão das chamadas de "Decidir" entre os modelos.
 */
import type { UsageGroup, UsageReport } from '../../api/types';
import { aiRoleLabel } from '../../lib/aiLabels';

export interface LinhaDeModelo {
  chave: string;
  funcao: string;
  rotulo: string;
  modelo: string;
  /** Chamada escalonada para o modelo mais forte (`tier: 1`). */
  escalonada: boolean;
  chamadas: number;
  erros: number;
  /** `null` = modelo sem preço: nunca vira zero. */
  usd: number | null;
}

export interface SomaDaFuncao { funcao: string; rotulo: string; chamadas: number; usd: number | null; parcial: boolean }

export interface ModelosDoAlvo {
  linhas: LinhaDeModelo[];
  somas: SomaDaFuncao[];
  chamadas: number;
  /** Soma só do que tem preço; `null` quando nada tem. */
  usd: number | null;
  parcial: boolean;
  /** As chamadas de "Decidir" por modelo, com a parte de cada um (0 a 1): é o que o 31.223 muda. */
  decisoes: { modelo: string; chamadas: number; parte: number }[];
}

const ORDEM: Record<string, number> = { plan: 0, decide: 1, verify: 2 };
const ordem = (f: string) => ORDEM[f] ?? 99;
const numero = (v: unknown): number => (typeof v === 'number' && Number.isFinite(v) && v >= 0 ? v : 0);

function somar(itens: readonly { usd: number | null }[]): { usd: number | null; parcial: boolean } {
  const comPreco = itens.filter((i) => i.usd !== null);
  if (comPreco.length === 0) return { usd: null, parcial: false };
  return { usd: comPreco.reduce((s, i) => s + (i.usd ?? 0), 0), parcial: comPreco.length < itens.length };
}

export function modelosDoAlvo(report: Pick<UsageReport, 'groups'>): ModelosDoAlvo {
  const grupos: UsageGroup[] = (Array.isArray(report.groups) ? report.groups : []).filter((g) => g && typeof g.role === 'string' && typeof g.model === 'string');
  const linhas: LinhaDeModelo[] = grupos
    .map((g) => ({
      chave: `${g.role}|${g.model}|${g.tier}`, funcao: g.role, rotulo: aiRoleLabel(g.role), modelo: g.model, escalonada: g.tier === 1,
      chamadas: numero(g.calls), erros: numero(g.errors), usd: typeof g.usd === 'number' && Number.isFinite(g.usd) ? g.usd : null,
    }))
    .sort((a, b) => ordem(a.funcao) - ordem(b.funcao) || a.modelo.localeCompare(b.modelo) || Number(a.escalonada) - Number(b.escalonada));
  const funcoes = Array.from(new Set(linhas.map((l) => l.funcao)));
  const somas: SomaDaFuncao[] = funcoes.map((f) => {
    const doGrupo = linhas.filter((l) => l.funcao === f);
    return { funcao: f, rotulo: aiRoleLabel(f), chamadas: doGrupo.reduce((s, l) => s + l.chamadas, 0), ...somar(doGrupo) };
  });
  const decidir = linhas.filter((l) => l.funcao === 'decide');
  const totalDecidir = decidir.reduce((s, l) => s + l.chamadas, 0);
  const porModelo = new Map<string, number>();
  for (const l of decidir) porModelo.set(l.modelo, (porModelo.get(l.modelo) ?? 0) + l.chamadas);
  const total = somar(linhas);
  return {
    linhas, somas, chamadas: linhas.reduce((s, l) => s + l.chamadas, 0), usd: total.usd, parcial: total.parcial,
    decisoes: Array.from(porModelo, ([modelo, chamadas]) => ({ modelo, chamadas, parte: totalDecidir > 0 ? chamadas / totalDecidir : 0 }))
      .sort((a, b) => b.chamadas - a.chamadas || a.modelo.localeCompare(b.modelo)),
  };
}
