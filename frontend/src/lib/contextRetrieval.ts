import type { ContextRetrievalStatus } from '../api/types';
import type { Tone } from './status';

/** Retrieval de contexto (ADR-063): textos e tons do painel. Só leitura; nada aqui liga ou desliga coisa alguma. */

const MODE_LABEL: Record<string, string> = {
  disabled: 'Desligado',
  local_only: 'Só local',
  shadow: 'Sombra (mede, não entrega)',
  hybrid: 'Híbrido (com provedor semântico)',
};

export function modeLabel(mode: string): string {
  return Object.prototype.hasOwnProperty.call(MODE_LABEL, mode) ? MODE_LABEL[mode]! : mode;
}

/** O que a política de envio decidiu, em português. Códigos novos do backend aparecem como vieram (nada some). */
const REASON_LABEL: Record<string, string> = {
  allowed: 'Envio permitido',
  private_repository: 'Repositório privado: o código não sai da máquina',
  synthetic_repository: 'Repositório sintético: envio remoto negado',
  public_repository_not_enabled: 'Repositório público, mas o envio não foi habilitado na configuração',
  repository_not_public: 'O repositório real não é público',
  repository_visibility_unverified: 'Visibilidade do repositório ainda não provada',
  repository_head_not_public: 'O commit atual não está provado público',
  repository_worktree_dirty: 'Há alteração local não commitada (ou arquivo marcado no índice)',
  no_provider: 'Sem provedor semântico',
  key_missing: 'Chave do provedor ausente',
  not_applicable: 'Não se aplica (provedor local ou desligado)',
};

export function sendReasonLabel(reason: string): string {
  return Object.prototype.hasOwnProperty.call(REASON_LABEL, reason) ? REASON_LABEL[reason]! : reason;
}

const FALLBACK_LABEL: Record<string, string> = {
  provider_unavailable: 'Provedor indisponível',
  key_missing: 'Chave ausente',
  timeout: 'Tempo esgotado',
  rate_limited: 'Limite de taxa do provedor',
  overloaded: 'Provedor sobrecarregado',
  provider_offline: 'Provedor fora do ar',
  provider_error: 'Erro do provedor',
  invalid_response: 'Resposta inválida',
  budget_exceeded: 'Orçamento estourado',
  privacy_block: 'Bloqueio de privacidade',
  secret_block: 'Bloqueio por segredo',
  empty_semantic: 'Semântico sem resultado',
  no_provider: 'Sem provedor',
};

export function fallbackLabel(reason: string): string {
  return Object.prototype.hasOwnProperty.call(FALLBACK_LABEL, reason) ? FALLBACK_LABEL[reason]! : reason;
}

export function visibilityLabel(v: string): string {
  switch (v) {
    case 'public': return 'Pública (provada)';
    case 'private': return 'Privada';
    case 'unverified': return 'Não provada';
    case 'not_applicable': return 'Não se aplica';
    default: return v;
  }
}

export interface Veredito {
  tone: Tone;
  /** Uma frase para quem só olha o selo. */
  titulo: string;
  detalhe: string;
}

/**
 * Resumo do estado: o que está ligado e se algum código pode sair. O envio externo só é "permitido" quando a política
 * diz `allowed` E o modo realmente usa um provedor remoto; em qualquer outro caso a resposta honesta é "nada sai".
 */
export function veredito(s: ContextRetrievalStatus): Veredito {
  if (!s.enabled || s.mode === 'disabled') {
    return { tone: 'neutral', titulo: 'Desligado', detalhe: 'O retrieval existe no código, mas está desligado: nada é consultado e nada sai da máquina.' };
  }
  const remoto = s.mode === 'hybrid' || s.mode === 'shadow';
  if (!remoto) {
    return { tone: 'success', titulo: 'Só local', detalhe: 'Busca léxica e BM25 na própria máquina. Nenhum código sai.' };
  }
  if (s.external_send.allowed && s.provider.available) {
    return { tone: 'warning', titulo: 'Envio externo permitido', detalhe: 'A política autoriza enviar trechos de código público ao provedor semântico.' };
  }
  return {
    tone: 'success', titulo: 'Envio externo bloqueado',
    detalhe: `${sendReasonLabel(s.external_send.reason)}. O retrieval cai no local e nenhum código sai.`,
  };
}

/** `null` ou ausente → "—"; evita mostrar "NaN" quando o resumo ainda está vazio. */
export function ms(v: number | null | undefined): string {
  return v === null || v === undefined ? '—' : `${v.toLocaleString('pt-BR', { maximumFractionDigits: 0 })} ms`;
}

export function usd(v: number): string {
  if (v === 0) return 'US$ 0,00';
  return `US$ ${v.toLocaleString('pt-BR', { minimumFractionDigits: v < 0.01 ? 4 : 2, maximumFractionDigits: v < 0.01 ? 6 : 2 })}`;
}

/** Acerto do cache em porcentagem, ou "—" quando ainda não houve consulta com cache. */
export function cacheHitRate(c: { hit: number; miss: number }): string {
  const total = c.hit + c.miss;
  return total === 0 ? '—' : `${Math.round((c.hit / total) * 100)}%`;
}

/** Contagens por razão, da maior para a menor (empate pelo nome), já com o rótulo em português. */
export function contagens(map: Record<string, number>, rotulo: (k: string) => string): Array<{ chave: string; rotulo: string; n: number }> {
  return Object.entries(map)
    .filter(([, n]) => n > 0)
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([chave, n]) => ({ chave, rotulo: rotulo(chave), n }));
}

/** As três provas que a política exige para o envio remoto, na ordem em que o backend as confere. */
export function provas(s: ContextRetrievalStatus['external_send']): Array<{ nome: string; ok: boolean | null }> {
  return [
    { nome: 'Remoto público provado', ok: s.remote_visibility_verified },
    { nome: 'Commit atual público provado', ok: s.head_public_verified },
    { nome: 'Worktree limpo', ok: s.worktree_clean },
  ];
}
