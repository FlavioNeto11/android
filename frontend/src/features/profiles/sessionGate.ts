import type { InstagramProfile, SessionPhase } from '../../api/types';

/**
 * Por que Conectar / Verificar conta / Sair está indisponível AGORA — ou `null` quando está liberado.
 *
 * A decisão é do backend (`session_actions`, de `backend/app/social/sessao_gate.py`): a tela só mostra o motivo.
 * Antes, cada botão olhava uma flag solta (senha guardada? aparelho vinculado?) e "Conectar" aparecia num aparelho
 * sem o Instagram. O caminho de reserva abaixo só existe para backend antigo, que não manda `session_actions`.
 */
export function sessionGateReason(p: Pick<InstagramProfile, 'session_actions' | 'instance_id' | 'credential'>,
                                  acao: 'connect' | 'verify' | 'logout'): string | null {
  const gate = p.session_actions?.[acao];
  if (gate) return gate.allowed ? null : (gate.reason ?? p.session_actions?.detail ?? 'Indisponível agora.');
  if (acao === 'connect' && !p.credential.configured) {
    return 'Abra o perfil e guarde a senha na aba Autenticação antes de conectar.';
  }
  if (!p.instance_id) return 'Vincule um aparelho a este perfil.';
  return null;
}

/** A fase da cadeia aparelho → app → credencial → sessão, em palavras para o cartão. */
export const SESSION_PHASE_LABEL: Record<SessionPhase, { label: string; tone: 'neutral' | 'info' | 'success' | 'warning' | 'danger' }> = {
  no_device: { label: 'sem aparelho', tone: 'neutral' },
  app_unknown: { label: 'app não verificado no aparelho', tone: 'warning' },
  app_missing: { label: 'app não instalado', tone: 'danger' },
  app_installing: { label: 'instalando o app', tone: 'info' },
  no_credential: { label: 'sem senha guardada', tone: 'warning' },
  authenticating: { label: 'autenticando…', tone: 'info' },
  logged_out: { label: 'deslogado', tone: 'warning' },
  authenticated: { label: 'conectada', tone: 'success' },
  challenge: { label: 'desafio: ação da pessoa', tone: 'danger' },
  wrong_account: { label: 'outra conta aberta', tone: 'danger' },
  unknown: { label: 'sessão não observada', tone: 'neutral' },
};
