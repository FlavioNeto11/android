/**
 * Estimativas de custo das operações de persona que chamam IA paga (geração, completar, fotos), para a pessoa decidir
 * ANTES de confirmar. O valor real sai de `ai_calls` depois; aqui só se multiplica o que `GET /api/ai` diz: o papel
 * `social` é simulado ou não, e quanto custa uma imagem do gerador configurado. Sem ler o provedor, conta-se com custo
 * (nunca se finge que é grátis).
 */
import type { AiStatus } from '../../api/types';

/** A faixa numérica de `CUSTO_ESTIMADO_POR_PERSONA` (design §6.5: papel social, ~4 mil tokens de entrada e ~1,5 mil
 *  de saída), para multiplicar pelo tamanho do lote. */
export const CUSTO_POR_PERSONA_USD = { min: 0.02, max: 0.03 } as const;

/** O papel `social` (geração e "Completar com IA") roda no simulado desta máquina? `null` = não deu para ler. */
export function socialSimulado(ai: AiStatus | null): boolean {
  if (!ai) return false;
  const social = ai.roles?.find((r) => r.role === 'social') ?? null;
  return social ? social.kind === 'simulated' || social.provider === 'simulated' : ai.simulated;
}

/** A imagem é paga? Sem `ai.image`, o gerador é desconhecido: conta como pago. */
export function imagemPaga(ai: AiStatus | null): boolean {
  return !ai?.image?.simulated;
}

export function usd(v: number): string {
  return `US$ ${v.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 3 })}`;
}

export function faixaUsd(min: number, max: number): string {
  return min === max ? `≈ ${usd(min)}` : `≈ ${usd(min)}–${max.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 3 })}`;
}

export interface LinhaDeCusto {
  rotulo: string;
  texto: string;
  pago: boolean;
}

/** Geração de `n` personas pelo papel social. */
export function custoDaGeracao(ai: AiStatus | null, n: number): LinhaDeCusto {
  if (ai && socialSimulado(ai)) return { rotulo: 'Geração', texto: `${n} × simulada: sem custo`, pago: false };
  const social = ai?.roles?.find((r) => r.role === 'social') ?? null;
  return {
    rotulo: 'Geração',
    texto: `${n} × ${faixaUsd(CUSTO_POR_PERSONA_USD.min, CUSTO_POR_PERSONA_USD.max)} = `
      + `${faixaUsd(n * CUSTO_POR_PERSONA_USD.min, n * CUSTO_POR_PERSONA_USD.max)}`
      + (social ? ` (${social.model} em ${social.provider})` : ai ? '' : ' (provedor não lido: conte com custo)'),
    pago: true,
  };
}

/** `fotos` imagens do gerador configurado. `null` quando não há o que gerar (ex.: foto automática desligada). */
export function custoDasFotos(ai: AiStatus | null, fotos: number, rotulo = 'Fotos'): LinhaDeCusto | null {
  if (fotos <= 0) return null;
  const img = ai?.image ?? null;
  if (img?.simulated) return { rotulo, texto: `${fotos} × gerador simulado: sem custo`, pago: false };
  const preco = img?.price_per_image_usd;
  if (preco == null) {
    return { rotulo, texto: `${fotos} imagem(ns) pelo ${img?.provider ?? 'gerador'}, preço por imagem não informado`, pago: true };
  }
  return { rotulo, texto: `${fotos} × ${usd(preco)} = ≈ ${usd(fotos * preco)} (${img?.provider ?? 'gerador'})`, pago: true };
}

/** Quantas fotos automáticas nascem com `n` personas criadas (`ai.image.on_create` × `per_persona`). O servidor só
 *  agenda com o gerador configurado (pago sem chave não gera nada). */
export function fotosAutomaticas(ai: AiStatus | null, n: number): number {
  const img = ai?.image;
  return img?.on_create && img.per_persona > 0 && (img.simulated || img.configured) ? n * img.per_persona : 0;
}
