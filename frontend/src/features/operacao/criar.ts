/**
 * 31.176: criar a operação pela tela. A regra pura do formulário (sem React): como cada alvo escolhido se resolve com o que EXISTE
 * (a conta da persona no app, a sessão dela, o aparelho), a prévia da capacidade que a pessoa vê ANTES de enviar e o corpo do
 * `POST /api/operacoes` (adendo v1.94/v1.95). A tela nunca cria conta nem persona: só escolhe entre as que já existem.
 */
import type { ProfileAccount } from '../../api/types';

export const MAX_ALVOS = 64;
export const MAX_TETO_USD = 100;
export const ASSUNTO_MIN = 3;
export const ASSUNTO_MAX = 500;

export type AcaoFinal = 'preparar' | 'executar';

/** O que a pessoa mudou no alvo; vazio = o padrão do servidor (a conta ativa da persona, o aparelho onde a sessão está). */
export interface EscolhaDoAlvo { conta: string; aparelho: string }

/**
 * `apto`: o servidor cria a execução. As demais situações são onde o alvo NASCE PARADO (a operação não troca de app nem
 * inventa vínculo): diz-se antes de enviar, não depois.
 */
export type SituacaoDoAlvo = 'apto' | 'sem_conta' | 'sem_sessao' | 'sem_aparelho';

export interface AlvoResolvido {
  profileId: string;
  conta: ProfileAccount | null;
  /** O aparelho que vai explícito no corpo; `null` quando nenhum existe para esta conta. */
  instanceId: string | null;
  situacao: SituacaoDoAlvo;
  /** A frase para a pessoa: por que está apto, ou onde pára. */
  motivo: string;
  /** Algo a conferir que não impede o envio. */
  aviso: string | null;
}

const sessaoDaConta = (c: ProfileAccount): string => c.session?.status ?? c.session_status;

/** As contas da persona que o alvo pode usar neste app: as ativas, as com sessão pronta primeiro (a ordem do servidor se mantém). */
export function contasDoApp(contas: readonly ProfileAccount[], appId: string): ProfileAccount[] {
  const doApp = contas.filter((c) => c.app_id === appId && c.status === 'active');
  return [...doApp.filter((c) => sessaoDaConta(c) === 'session_ready'), ...doApp.filter((c) => sessaoDaConta(c) !== 'session_ready')];
}

export function resolverAlvo(
  profileId: string, contas: readonly ProfileAccount[], appId: string, escolha: EscolhaDoAlvo, aparelhos: ReadonlySet<string>,
): AlvoResolvido {
  const base = { profileId, conta: null, instanceId: null, aviso: null } as const;
  const candidatas = contasDoApp(contas, appId);
  const conta = escolha.conta ? candidatas.find((c) => c.id === escolha.conta) : candidatas[0];
  if (!conta) return { ...base, situacao: 'sem_conta', motivo: 'A persona não tem conta ativa neste app: o alvo nasce parado em "conta".' };
  if (sessaoDaConta(conta) !== 'session_ready') {
    return { ...base, conta, situacao: 'sem_sessao', motivo: 'A conta não tem sessão pronta: o alvo nasce parado em "sessão".' };
  }
  const daSessao = conta.session?.instance_id ?? null;
  const instanceId = escolha.aparelho || daSessao;
  if (!instanceId || !aparelhos.has(instanceId)) {
    return { ...base, conta, situacao: 'sem_aparelho', motivo: 'Não há aparelho conhecido para esta conta: o alvo nasce parado em "sessão".' };
  }
  const aviso = escolha.aparelho && daSessao && escolha.aparelho !== daSessao
    ? `A sessão desta conta está em ${daSessao}, não em ${escolha.aparelho}: confira antes de enviar.` : null;
  return { profileId, conta, instanceId, situacao: 'apto', motivo: 'Tem conta, sessão pronta e aparelho.', aviso };
}

export interface PreviaDaCapacidade {
  solicitados: number;
  comConta: number;
  comSessao: number;
  /** Os que o servidor cria execução (conta + sessão + aparelho). */
  aptos: number;
  paradosNaCriacao: number;
}

export function previaDaCapacidade(alvos: readonly AlvoResolvido[]): PreviaDaCapacidade {
  const comConta = alvos.filter((a) => a.conta).length;
  const comSessao = alvos.filter((a) => a.situacao === 'apto' || a.situacao === 'sem_aparelho').length;
  const aptos = alvos.filter((a) => a.situacao === 'apto').length;
  return { solicitados: alvos.length, comConta, comSessao, aptos, paradosNaCriacao: alvos.length - aptos };
}

export interface FormularioDaOperacao {
  command: string;
  appId: string;
  acaoFinal: AcaoFinal;
  assunto: string;
  /** Texto do campo (aceita vírgula decimal). */
  maxUsd: string;
  selecionados: readonly string[];
}

/** O teto em US$ lido do campo: aceita "2,5" e "2.5"; qualquer outra coisa não é número. */
export function lerTeto(texto: string): number | null {
  const t = texto.trim().replace(',', '.');
  if (!/^\d+(\.\d{1,4})?$/.test(t)) return null;
  const n = Number(t);
  return Number.isFinite(n) ? n : null;
}

/** O que impede de enviar, na ordem em que a pessoa preenche; `null` = pode enviar. */
export function erroDoFormulario(f: FormularioDaOperacao): string | null {
  if (!f.command.trim()) return 'Diga o objetivo da operação.';
  if (!f.appId) return 'Escolha o app da operação.';
  if (f.selecionados.length === 0) return 'Escolha ao menos uma persona.';
  if (f.selecionados.length > MAX_ALVOS) return `Uma operação aceita até ${MAX_ALVOS} personas.`;
  const teto = lerTeto(f.maxUsd);
  if (teto === null || teto <= 0) return 'Informe o teto de custo em US$ (maior que zero).';
  if (teto > MAX_TETO_USD) return `O teto de custo vai até US$ ${MAX_TETO_USD}.`;
  const assunto = f.assunto.trim();
  if (assunto && (assunto.length < ASSUNTO_MIN || assunto.length > ASSUNTO_MAX)) return `O assunto tem de ${ASSUNTO_MIN} a ${ASSUNTO_MAX} caracteres, ou fica vazio.`;
  return null;
}

export interface CorpoDaOperacao {
  command: string;
  app_id: string;
  alvos: { profile_id: string; account_id?: string; instance_id?: string }[];
  acao_final: AcaoFinal;
  max_usd: number;
  assunto?: string;
}

/** O corpo sem a `idempotency_key` (a tela a guarda por corpo, para a nova tentativa reaproveitar a mesma). */
export function montarCorpo(f: FormularioDaOperacao, alvos: readonly AlvoResolvido[]): CorpoDaOperacao {
  const assunto = f.assunto.trim();
  return {
    command: f.command.trim(),
    app_id: f.appId,
    alvos: alvos.map((a) => ({
      profile_id: a.profileId,
      ...(a.conta ? { account_id: a.conta.id } : {}),
      ...(a.instanceId ? { instance_id: a.instanceId } : {}),
    })),
    acao_final: f.acaoFinal,
    max_usd: lerTeto(f.maxUsd) ?? 0,
    ...(assunto ? { assunto } : {}),
  };
}
