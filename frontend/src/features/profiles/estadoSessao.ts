import { ACCOUNT_SESSION_STATUS, metaOf, type StatusMeta } from '../../lib/status';
import type { Aba } from './abas';

/** O que o selo de estado do cabeçalho da persona mostra e, quando há o que fazer, para onde leva. */
export interface EstadoDaSessao {
  meta: StatusMeta;
  /** O verbo do passo seguinte e a guia onde ele mora; `null` quando está tudo certo e não há o que fazer. */
  acao: { rotulo: string; guia: Aba } | null;
  /** Quando a conta foi confirmada na tela (só para "Conectado", e só se o dado existir). */
  confirmadaEm: string | null;
}

/**
 * Sessão da conta principal → selo acionável. Não executa nada: "Verificar conta" e "Resolver" só LEVEM à guia
 * Contas e acesso, onde moram os botões de verdade (com o aparelho escolhido e a confirmação de cada um).
 */
export function estadoDaSessao(session: { status?: string | null; verified_at?: string | null } | null | undefined): EstadoDaSessao {
  const status = session?.status ?? 'unknown';
  const meta = metaOf(ACCOUNT_SESSION_STATUS, status);
  switch (status) {
    case 'session_ready':
      return { meta, acao: null, confirmadaEm: session?.verified_at ?? null };
    case 'unknown':
      return { meta, acao: { rotulo: 'Verificar conta', guia: 'contas' }, confirmadaEm: null };
    case 'auth_challenge':
    case 'needs_person':
    case 'wrong_account':
      return { meta, acao: { rotulo: 'Resolver', guia: 'contas' }, confirmadaEm: null };
    default:
      // auth_required, logged_out e qualquer estado novo: ver a conta é sempre o passo seguro.
      return { meta, acao: { rotulo: 'Ver conta', guia: 'contas' }, confirmadaEm: null };
  }
}
