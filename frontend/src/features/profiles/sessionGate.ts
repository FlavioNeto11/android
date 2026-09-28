import type { InstagramProfile, ProfileAccount, SessionPhase } from '../../api/types';

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
    return 'Abra a persona e guarde a senha na guia Contas e acesso antes de conectar.';
  }
  if (!p.instance_id) return 'Vincule um aparelho a esta persona.';
  return null;
}

/**
 * O mesmo motivo, POR CONTA (v0.28): cada conta traz o seu `session_actions`. O caminho de reserva (backend sem o
 * campo) repete a regra da rota: sem senha ou sem consentimento o login automático não começa.
 */
export function accountGateReason(c: Pick<ProfileAccount, 'session_actions' | 'credential' | 'credential_configured'>,
                                  acao: 'connect' | 'verify' | 'logout'): string | null {
  const gate = c.session_actions?.[acao];
  if (gate) return gate.allowed ? null : (gate.reason ?? c.session_actions?.detail ?? 'Indisponível agora.');
  if (acao === 'connect') {
    if (!c.credential_configured) return 'Guarde a senha desta conta antes de conectar.';
    if (c.credential && !c.credential.consent_at) return 'Autorize a automação a digitar a senha desta conta.';
  }
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
