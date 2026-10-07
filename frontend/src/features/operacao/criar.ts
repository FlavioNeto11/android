/**
 * 31.176: criar a operação pela tela. A regra pura do formulário (sem React): como cada alvo escolhido se resolve com o que EXISTE
 * (a conta da persona no app, a sessão dela, o aparelho), a prévia da capacidade que a pessoa vê ANTES de enviar e o corpo do
 * `POST /api/operacoes` (adendo v1.94/v1.95). A tela nunca cria conta nem persona: só escolhe entre as que já existem.
 */
import { ApiError } from '../../api/client';
import type { ProfileAccount } from '../../api/types';
import { fonteComoLink, type Operacao } from './modelo';

export const MAX_ALVOS = 64;
export const MAX_TETO_USD = 100;
export const ASSUNTO_MIN = 3;
export const ASSUNTO_MAX = 500;

export const MAX_FONTES = 10;
export const PARAMETRO_MAX = 300;

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
  return { profileId, conta, instanceId, situacao: 'apto', motivo: `Vai rodar em ${instanceId}: a conta tem sessão pronta.`, aviso };
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
  /** Uma URL por linha (opcional). */
  fontes: string;
  /** Parâmetros fixos (v1.95): o perfil alvo e o trecho da legenda, quando o comando não os diz. */
  username: string;
  legenda: string;
}

/** As fontes do campo: uma por linha, sem linha vazia nem repetida. */
export const fontesDoCampo = (texto: string): string[] => [...new Set(texto.split(/\r?\n/).map((l) => l.trim()).filter(Boolean))];

const normalizado = (v: string): string => v.replace(/@/g, '').replace(/\s+/g, '').toLowerCase();

/** O que a pessoa preencheu como parâmetros fixos, sem espaço sobrando e sem o campo vazio. */
export function parametrosDoFormulario(f: Pick<FormularioDaOperacao, 'username' | 'legenda'>): Record<string, string> {
  const u = f.username.trim();
  const l = f.legenda.trim();
  return { ...(u ? { username: u } : {}), ...(l ? { caption_contains: l } : {}) };
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
  const fontes = fontesDoCampo(f.fontes);
  if (fontes.length > MAX_FONTES) return `Até ${MAX_FONTES} fontes.`;
  const ruim = fontes.find((x) => !fonteComoLink(x));
  if (ruim) return 'Cada fonte é uma URL https:// sem usuário nem parâmetros (?…).';
  const par = parametrosDoFormulario(f);
  for (const [nome, valor] of Object.entries(par)) {
    if (valor.length > PARAMETRO_MAX || /[{}]/.test(valor)) return `${nome === 'username' ? 'O perfil alvo' : 'O trecho da legenda'} vai até ${PARAMETRO_MAX} caracteres e não leva { nem }.`;
  }
  if (par.username && par.caption_contains && normalizado(par.username) === normalizado(par.caption_contains)) return 'O perfil alvo e o trecho da legenda não podem ser o mesmo valor.';
  return null;
}

/**
 * "Repetir como nova": o que a gaveta de uma operação leva ao formulário. Fica na memória da aba (não vai na URL: o objetivo e as
 * fontes são longos) e vale para UMA abertura do formulário.
 */
export interface Rascunho {
  command: string;
  appId: string;
  acaoFinal: AcaoFinal;
  assunto: string;
  maxUsd: string;
  fontes: string;
  username: string;
  legenda: string;
  profileIds: string[];
  /** Parâmetros fixos além de `username` e `caption_contains`, que o formulário não oferece e não copia. */
  parametrosNaoCopiados: string[];
}

let guardado: Rascunho | null = null;
export const guardarRascunho = (r: Rascunho): void => { guardado = r; };
export const lerRascunho = (): Rascunho | null => guardado;
export const limparRascunho = (): void => { guardado = null; };

/** Conta e aparelho NÃO se copiam: a operação nova resolve o que existe agora (sessões e vínculos mudam entre uma operação e outra). */
export function rascunhoDaOperacao(op: Operacao): Rascunho {
  const par = op.parametros ?? {};
  return {
    command: op.command, appId: op.app_id ?? '', acaoFinal: op.acao_final === 'executar' ? 'executar' : 'preparar', assunto: op.assunto ?? '',
    maxUsd: op.max_usd === null ? '' : String(op.max_usd).replace('.', ','), fontes: op.fontes.join('\n'),
    username: par.username ?? '', legenda: par.caption_contains ?? '',
    profileIds: [...new Set(op.alvos.map((a) => a.profile_id).filter((x): x is string => !!x))],
    parametrosNaoCopiados: Object.keys(par).filter((k) => k !== 'username' && k !== 'caption_contains'),
  };
}

export interface CorpoDaOperacao {
  command: string;
  app_id: string;
  alvos: { profile_id: string; account_id?: string; instance_id?: string }[];
  acao_final: AcaoFinal;
  max_usd: number;
  assunto?: string;
  fontes?: string[];
  parametros?: Record<string, string>;
}

/** O corpo sem a `idempotency_key` (a tela a guarda por corpo, para a nova tentativa reaproveitar a mesma). */
export function montarCorpo(f: FormularioDaOperacao, alvos: readonly AlvoResolvido[]): CorpoDaOperacao {
  const assunto = f.assunto.trim();
  const fontes = fontesDoCampo(f.fontes);
  const par = parametrosDoFormulario(f);
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
    ...(fontes.length ? { fontes } : {}),
    ...(Object.keys(par).length ? { parametros: par } : {}),
  };
}

/** Os dois parâmetros fixos que o formulário oferece; qualquer outra chave que o servidor recuse não tem campo e vira aviso geral. */
export type CampoDeParametro = 'username' | 'caption_contains';

/** 31.224: um parâmetro que o servidor recusou, com o motivo dele (nunca um "erro genérico"). `campo` = onde mostrar; null = sem campo na tela. */
export interface RecusaDeParametro { chave: string; campo: CampoDeParametro | null; motivo: string }

const texto = (v: unknown): string | null => (typeof v === 'string' && v.trim() ? v.trim() : null);
const registro = (v: unknown): Record<string, unknown> | null => (v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : null);

function recusa(chave: string | null, motivo: string | null): RecusaDeParametro | null {
  if (!chave || !motivo) return null;
  const nome = chave.replace(/^.*\./, '').trim();
  return { chave: nome, campo: nome === 'username' || nome === 'caption_contains' ? nome : null, motivo };
}

/**
 * Lê a recusa de parâmetros do `POST /api/operacoes` (422 do 31.224: username com arroba ou espaço, chave desconhecida). Tolerante ao
 * formato: `detail.campo|field|parametro` com `detail.motivo|message`, uma lista `detail.erros|parametros` de `{campo, motivo}`, ou o 422
 * de validação do FastAPI (`parametros.username: mensagem`). Devolve `[]` quando o erro não é dessa classe ou não diz qual parâmetro:
 * quem chama então mostra o aviso geral de sempre.
 */
export function recusasDeParametros(e: unknown): RecusaDeParametro[] {
  if (!(e instanceof ApiError) || (e.status !== 422 && e.status !== 400)) return [];
  const d = e.detail;
  if (d) {
    for (const nome of ['erros', 'parametros', 'errors']) {
      const lista = d[nome];
      if (Array.isArray(lista)) {
        const itens = lista.flatMap((i) => {
          const o = registro(i);
          const r = o ? recusa(texto(o.campo) ?? texto(o.field) ?? texto(o.parametro) ?? texto(o.chave), texto(o.motivo) ?? texto(o.message)) : null;
          return r ? [r] : [];
        });
        if (itens.length > 0) return itens;
      }
    }
    const unica = recusa(texto(d.campo) ?? texto(d.field) ?? texto(d.parametro) ?? texto(d.chave), texto(d.motivo) ?? texto(d.message) ?? texto(e.message));
    if (unica) return [unica];
  }
  if (e.code === 'validation') {
    return e.message.split('; ').flatMap((parte) => {
      const m = /^(?:.*\.)?parametros\.([^:\s]+):\s*(.+)$/.exec(parte);
      const r = m ? recusa(m[1]!, m[2]!) : null;
      return r ? [r] : [];
    });
  }
  return [];
}
