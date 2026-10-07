import type { PersonaDTO } from '../../api/types';
import type { Aba } from './abas';
import { handleDe, idsDosAparelhos } from './pessoa';

/**
 * A capacidade da persona para trabalhar numa operação (31.255), lida só do que `GET /personas` já traz: conta no app, aparelho, cofre
 * (senha guardada e consentimento da conta, ADR-040), sessão e grupo. Nunca lê nem mostra o identificador de login; o valor da senha
 * não existe aqui. Um dado que o central não manda (`consent_at` ausente) fica "não informado", nunca vira "sem consentimento".
 */

export type SessaoDaPersona = 'pronta' | 'vencida' | 'desconhecida' | 'precisa_de_pessoa' | 'deslogada' | 'sem_sessao';
export type CofreDaPersona = 'sem_conta' | 'sem_senha' | 'sem_consentimento' | 'pronto' | 'sem_informacao' | 'bloqueado';
export type BloqueioDaPersona = 'inativa' | 'sem_conta' | 'sem_senha' | 'sem_consentimento' | 'sem_aparelho' | 'sessao';

export const ROTULO_DA_SESSAO: Record<SessaoDaPersona, string> = {
  pronta: 'Pronta', vencida: 'Vencida', desconhecida: 'Desconhecida', precisa_de_pessoa: 'Pede uma pessoa', deslogada: 'Deslogada', sem_sessao: 'Sem conta',
};
export const ROTULO_DO_COFRE: Record<CofreDaPersona, string> = {
  sem_conta: 'Sem conta', sem_senha: 'Sem senha guardada', sem_consentimento: 'Sem consentimento', pronto: 'Senha e consentimento',
  sem_informacao: 'Senha guardada (consentimento não informado)', bloqueado: 'Bloqueado por tentativas',
};
export const ROTULO_DO_BLOQUEIO: Record<BloqueioDaPersona, string> = {
  inativa: 'Persona bloqueada ou pausada', sem_conta: 'Sem conta', sem_senha: 'Falta guardar a senha', sem_consentimento: 'Falta o consentimento',
  sem_aparelho: 'Sem aparelho', sessao: 'Sessão não pronta',
};

export interface CapacidadeDaPersona {
  conta: boolean;
  aparelho: boolean;
  cofre: CofreDaPersona;
  sessao: SessaoDaPersona;
  grupo: string | null;
  /** O último uso conhecido: o da senha pela automação; senão a última atividade. */
  ultimoUso: string | null;
  /** O que impede de trabalhar agora (o primeiro da ordem); `null` = pronta. */
  bloqueio: BloqueioDaPersona | null;
  /** Para onde ir resolver conta, senha, consentimento ou aparelho (só navega; daqui não se grava nada). */
  passo: { rotulo: string; guia: Aba } | null;
}

function sessaoDe(p: PersonaDTO): SessaoDaPersona {
  if (!handleDe(p)) return 'sem_sessao';
  const s = p.session;
  if (s?.unknown_at_cap) return 'precisa_de_pessoa';
  switch (s?.status ?? 'unknown') {
    case 'session_ready': return s?.stale ? 'vencida' : 'pronta';
    case 'auth_challenge': case 'needs_person': case 'wrong_account': return 'precisa_de_pessoa';
    case 'auth_required': return 'deslogada';
    default: return 'desconhecida';
  }
}

function cofreDe(p: PersonaDTO): CofreDaPersona {
  if (!handleDe(p)) return 'sem_conta';
  const c = p.credential;
  if (!c?.configured) return 'sem_senha';
  if (c.blocked_until && Date.parse(c.blocked_until) > Date.now()) return 'bloqueado';
  if (c.consent_at === null) return 'sem_consentimento';
  return c.consent_at === undefined ? 'sem_informacao' : 'pronto';
}

export function capacidadeDe(p: PersonaDTO): CapacidadeDaPersona {
  const conta = !!handleDe(p);
  const aparelho = idsDosAparelhos(p).length > 0;
  const cofre = cofreDe(p);
  const sessao = sessaoDe(p);
  let bloqueio: BloqueioDaPersona | null = null;
  let passo: CapacidadeDaPersona['passo'] = null;
  if (p.status !== 'active') bloqueio = 'inativa';
  else if (!conta) { bloqueio = 'sem_conta'; passo = { rotulo: 'Adicionar conta', guia: 'contas' }; }
  else if (cofre === 'sem_senha') { bloqueio = 'sem_senha'; passo = { rotulo: 'Guardar senha', guia: 'contas' }; }
  else if (cofre === 'sem_consentimento') { bloqueio = 'sem_consentimento'; passo = { rotulo: 'Dar consentimento', guia: 'contas' }; }
  else if (!aparelho) { bloqueio = 'sem_aparelho'; passo = { rotulo: 'Vincular aparelho', guia: 'aparelhos' }; }
  // Sessão não pronta: sem atalho aqui (a sessão e o "Conectar" moram só na guia Contas e acesso; o cartão não oferece essas ações).
  else if (sessao !== 'pronta') bloqueio = 'sessao';
  return {
    conta, aparelho, cofre, sessao, grupo: p.policy_group_name ?? null,
    ultimoUso: p.credential?.last_used_at ?? p.last_activity_at ?? null, bloqueio, passo,
  };
}

/** Os recortes do filtro "Capacidade": cada um responde "quem falta resolver o quê". */
export const CAPACIDADES = ['prontas', 'sem-conta', 'sem-senha', 'sem-consentimento', 'sem-aparelho', 'sessao'] as const;
export type RecorteDeCapacidade = (typeof CAPACIDADES)[number];
export const ROTULO_DO_RECORTE: Record<RecorteDeCapacidade, string> = {
  prontas: 'Prontas para operar', 'sem-conta': 'Sem conta', 'sem-senha': 'Sem senha guardada', 'sem-consentimento': 'Sem consentimento',
  'sem-aparelho': 'Sem aparelho', sessao: 'Sessão não pronta',
};
const BLOQUEIO_DO_RECORTE: Record<Exclude<RecorteDeCapacidade, 'prontas'>, BloqueioDaPersona> = {
  'sem-conta': 'sem_conta', 'sem-senha': 'sem_senha', 'sem-consentimento': 'sem_consentimento', 'sem-aparelho': 'sem_aparelho', sessao: 'sessao',
};

export function casaCapacidade(p: PersonaDTO, recorte: RecorteDeCapacidade): boolean {
  const b = capacidadeDe(p).bloqueio;
  return recorte === 'prontas' ? b === null : b === BLOQUEIO_DO_RECORTE[recorte];
}
