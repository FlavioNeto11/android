import type {
  AppCatalogEntry,
  Command,
  CommandAccepted,
  CommandCancelRequest,
  CommandState,
  CommandCancelled,
  CommandResolution,
  CommandVerified,
  OperationalContext,
  Worker,
  WorkerDeviceProposal,
  ComandoRemoto, ComandoRemotoInterruptor, ComandoRemotoPedido,
  WorkerEnrollment,
  Approval,
  ApprovalBatchResult,
  ApprovalDecisionItem,
  AuthAttempt,
  AiStatus,
  AppConfig,
  AppConfigInput,
  BulkRequest,
  AppRelease,
  AppStoreEntry,
  NetworkAssignRequest,
  NetworkAssignResult,
  NetworkDeviceList,
  NetworkProfile,
  NetworkProfileCreateRequest,
  NetworkProfileList,
  NetworkProfileSaidaRequest,
  NetworkRemoteAccess,
  NetworkRequestAccepted,
  NetworkServerStatus,
  ProxyList,
  ProxyProfile,
  ReleaseLifecycleBody,
  ReleaseTargets,
  DistributeDevice,
  StoreStatus,
  BulkResult,
  Capability,
  DeviceAppState,
  ControlReleaseResponse,
  ControlTakeResponse,
  CanaisEstado,
  CanalAnexoAoCartao,
  CanalAnexoLeitura,
  CanalAnexosPagina,
  CommandRefinement,
  CreateRunRequest,
  CredentialCloneRequest,
  CredentialUpdateRequest,
  Diagnostics,
  EventRecord,
  EnsinoSugerido,
  Evidence,
  Flow,
  FlowCoverage,
  FlowSimilar,
  FlowStatusUpdate,
  FrameHeaders,
  Health,
  HierarchyResponse,
  InputOk,
  InstagramProfile,
  Instance,
  InstanceAction,
  InstanceActionParams,
  InstanceUpdate,
  ManualInput,
  MemoryInput,
  MemoryItem,
  Metrics,
  Objective,
  PackagesResponse,
  PersonaCreateRequest,
  PersonaDTO,
  PersonaDeviceBindRequest,
  PersonaOnDevice,
  PersonaGenerateRequest,
  PersonaImage,
  PersonaImagesAccepted,
  PersonaInput,
  CicloDaConta,
  PersonaPatchRequest,
  PersonaPreviewRequest,
  InstanceProvisionAccepted,
  InstanceProvisionRequest,
  InstanceRetired,
  ProfileCapabilities,
  ProfileCreateRequest,
  ProfilePatchRequest,
  ProfilePolicy,
  PolicyGroup,
  ProfileAccount,
  ProfileAccountCreateRequest,
  PlannedAccountRequest,
  HandleSuggestion,
  CredentialPrepareRequest,
  ProvisioningTransitionRequest,
  ProvisioningCancelled,
  ProfileAccountPatchRequest,
  AppOverview,
  TrainingAnswer,
  TrainingPreview,
  TrainingProposal,
  TrainingRecipesResult,
  TrainingSaveResult,
  TrainingFromRunBody,
  TrainingSession,
  TrainingUndoResult,
  SkillSummary,
  SkillState,
  SkillVersionDetail,
  FlowConversion,
  FlowConversionUndone,
  AppDetail,
  PolicyGroupCreateRequest,
  PolicyGroupPatchRequest,
  ProfilePolicyPatch,
  Recipe,
  RecipeStatusResult,
  RecipeStatusUpdate,
  ResolveRequest,
  ResolveTargetsRequest,
  ResolveTargetsResponse,
  RetryFailedResponse,
  RunDetail,
  RunPage,
  RunReport,
  RunSummary,
  PreviaDaPorta,
  PreviaDoItem,
  AprovarPlanoItem,
  AprovarPlanoResultado,
  RenovarPlanoResultado,
  SessionJobAccepted,
  SocialDraft,
  SocialInteraction,
  PanelSession,
  Settings,
  Snapshot,
  UsageQuery,
  UsageReport,
  ServerLimits,
  ServerLimitsPatch,
  DistributionPreview,
  RefineCommandRequest,
  RunSuccessorRequest,
  AiBalanceAccount,
  AiBalanceReadingIn,
  AiBalanceRechargeIn,
  AiBalanceRuleIn,
  AiBalancesReport,
  ContextRetrievalStatus,
  PortalContatosBusca,
  PortalExclusaoResultado,
  PortalPedidoPor,
  RunTargetsSuggestRequest,
  RunTargetsSuggestion,
} from './types';

/** Todas as URLs são relativas a `/api`: funcionam atrás do proxy do Vite e servidas pelo backend. */
export const API_BASE = '/api';

export class ApiError extends Error {
  /** Status HTTP; 0 = falha de rede (backend inacessível) ou tempo esgotado. */
  readonly status: number;
  /** `detail.code` do backend, ou um código local (`network`, `timeout`, `http_<status>`). */
  readonly code: string;
  /** Objeto `detail` bruto devolvido pelo backend, quando existir. */
  readonly detail: Record<string, unknown> | null;

  constructor(status: number, code: string, message: string, detail: Record<string, unknown> | null = null) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.detail = detail;
  }
}

/** Converte qualquer erro em ApiError para que a UI trate um formato só. */
export function toApiError(e: unknown): ApiError {
  if (e instanceof ApiError) return e;
  if (e instanceof DOMException && e.name === 'AbortError') {
    return new ApiError(0, 'timeout', 'A requisição demorou demais e foi interrompida.');
  }
  if (e instanceof Error) return new ApiError(0, 'network', e.message || 'Falha de rede.');
  return new ApiError(0, 'unknown', 'Erro desconhecido.');
}

/** Dica de próximo passo (pt-BR) para cada classe de erro. */
export function hintForError(e: ApiError): string {
  switch (e.code) {
    case 'network':
      // Sem endereço fixo: o painel também é aberto por nome de rede, e mandar alguém conferir "127.0.0.1:8000"
      // ali é mandar conferir a máquina errada.
      return 'O backend não respondeu neste endereço. Confirme que o servidor está no ar e tente de novo.';
    case 'unauthorized':
      return 'Sua sessão terminou (ou nunca começou neste navegador). Entre de novo para continuar.';
    case 'invalid_credentials':
      return 'Nome ou chave de acesso não conferem. A chave é o API_TOKEN do backend, com quem cuida do parque.';
    case 'too_many_attempts':
      return 'A trava protege a chave de acesso contra chutes e vale só para quem errou; o acesso pela própria máquina '
        + 'não é trancado.';
    case 'rate_limited':
      return 'O endereço público limita as tentativas de cada pessoa por alguns segundos.';
    case 'forbidden_host':
      return 'Este backend não aceita ser chamado por este endereço. Ele precisa constar em server.public_hosts '
        + 'no config.yaml do central — é a defesa que impede um nome de fora se passar por ele.';
    case 'forbidden_origin':
      return 'A origem desta página não está em server.allowed_origins no config.yaml do central.';
    case 'timeout':
      return 'O backend está lento ou travado. Veja o Diagnóstico e tente novamente em instantes.';
    case 'stale_frame':
    case 'frame_mismatch':
      return 'A tela mudou — aguarde a nova imagem e tente de novo.';
    // 29.105: o quadro não se renova (tela protegida contra captura); as teclas de navegação não dependem dele.
    case 'capture_failing':
      return 'A captura da tela está falhando e a imagem não se renova. Voltar, Início e Recentes seguem funcionando.';
    case 'not_controller':
      return 'Você não está com o controle desta instância. Use “Assumir controle” antes de interagir.';
    // ADR-040: a senha mora na conta da persona, nunca no comando nem na execução.
    case 'credencial_no_comando':
      return 'Tire a senha do texto e guarde-a na conta da persona (Persona → Contas e acesso), com o consentimento.';
    // 29.52: a resposta a uma pergunta de senha ou código (ou com cara de uma) não vira comando.
    case 'credencial_na_resposta':
      return 'Essa pergunta pede uma credencial: informe pelo caminho adequado e peça de novo.';
    case 'consentimento_de_credencial':
      return 'Marque que a pessoa autoriza a automação a digitar esta senha, só no app e no site desta conta.';
    case 'no_credential':
      return 'Esta conta ainda não tem senha guardada. Guarde-a em Persona → Contas e acesso.';
    // v0.29 (ADR-043/044): vínculo N:N e roteamento por persona.
    case 'alvos_nao_confirmados':
      return 'O comando cita destinos (“no aparelho Y”, “peça para o André”). Confira a prévia dos alvos e confirme.';
    case 'sem_intersecao':
      return 'Nenhum dos aparelhos escolhidos é desta persona: tire o filtro de aparelhos ou vincule a persona a eles.';
    case 'sem_vinculo':
      return 'A persona não está vinculada a este aparelho. Vincule em Persona → Aparelhos, ou escolha um aparelho dela.';
    case 'no_binding':
      return 'A persona não está vinculada a nenhum aparelho. Vincule um em Persona → Aparelhos.';
    case 'aparelho_repetido_na_execucao':
      return 'Uma execução usa cada aparelho uma vez só: faça duas execuções, ou escolha outro aparelho para uma delas.';
    case 'sem_alvo':
      return 'Diga onde ou por quem: escolha aparelhos ou personas, ou cite no comando (“no android-03”).';
    case 'conta_do_app_ja_no_aparelho':
      return 'Um aparelho tem uma conta por app: escolha outro aparelho, ou desvincule quem já usa este app lá.';
    case 'persona_in_use':
      return 'A persona tem execução em andamento ali: espere terminar ou cancele antes.';
    case 'not_bound':
      return 'Este vínculo não existe mais. Atualize a tela.';
    default:
      break;
  }
  if (e.status === 400 || e.status === 422) return 'Revise os campos informados e tente novamente.';
  if (e.status === 404) return 'O item não existe mais no backend. Atualize a página para recarregar os dados.';
  if (e.status === 409) return 'O estado atual não permite esta ação. Aguarde a atualização do painel e tente de novo.';
  if (e.status === 503) return 'Uma dependência está indisponível (SDK, Appium ou IA). Abra o Diagnóstico para ver o que falta.';
  if (e.status >= 500) return 'Erro interno do backend. Consulte o log do servidor e o Diagnóstico.';
  return 'Tente novamente. Se persistir, consulte o Diagnóstico.';
}

function parseErrorBody(status: number, body: unknown, retryAfter: string | null = null): ApiError {
  // Formato do contrato: {"detail": {"code": string, "message": string, ...}}
  if (body && typeof body === 'object' && 'detail' in body) {
    const detail = (body as { detail: unknown }).detail;
    if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
      const d = detail as Record<string, unknown>;
      const code = typeof d.code === 'string' ? d.code : `http_${status}`;
      const message = typeof d.message === 'string' ? d.message : `Erro HTTP ${status}`;
      return new ApiError(status, code, message, d);
    }
    // FastAPI devolve `detail` como string (HTTPException simples) ou lista (erro de validação 422).
    if (typeof detail === 'string') return new ApiError(status, `http_${status}`, detail);
    if (Array.isArray(detail)) {
      const msgs = detail
        .map((item) => {
          if (item && typeof item === 'object') {
            const it = item as { loc?: unknown; msg?: unknown };
            const loc = Array.isArray(it.loc) ? it.loc.filter((p) => p !== 'body').join('.') : '';
            const msg = typeof it.msg === 'string' ? it.msg : '';
            return loc ? `${loc}: ${msg}` : msg;
          }
          return String(item);
        })
        .filter(Boolean);
      return new ApiError(status, 'validation', msgs.join('; ') || 'Dados inválidos.');
    }
  }
  // A tabela REST do contrato escreve alguns erros como `409 {code: ...}` (sem o envelope `detail`);
  // aceitamos essa forma também para que `stale_frame`/`not_controller` sejam reconhecidos nos dois casos.
  if (body && typeof body === 'object' && typeof (body as { code?: unknown }).code === 'string') {
    const b = body as Record<string, unknown>;
    return new ApiError(status, b.code as string, typeof b.message === 'string' ? b.message : `Erro HTTP ${status}`, b);
  }
  // 29.56: o limite de taxa da borda (Cloudflare) responde 429 em HTML, sem o nosso envelope. Sem este caso o login
  // de fora mostrava "Erro HTTP 429"; a regra da borda é por IP e libera em 10 s, que é o padrão quando falta
  // o `Retry-After`.
  if (status === 429) {
    const segundos = Math.max(1, Number.parseInt(retryAfter ?? '', 10) || 10);
    return new ApiError(429, 'rate_limited', `Muitas tentativas em pouco tempo. Espere ${segundos} segundos e tente de novo.`,
      { retry_after_s: segundos });
  }
  return new ApiError(status, `http_${status}`, `Erro HTTP ${status}`);
}

interface RequestOptions {
  body?: unknown;
  query?: Record<string, string | number | undefined>;
  timeoutMs?: number;
  signal?: AbortSignal;
}

function buildUrl(path: string, query?: RequestOptions['query']): string {
  let url = `${API_BASE}${path}`;
  if (query) {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(query)) if (v !== undefined) qs.set(k, String(v));
    const s = qs.toString();
    if (s) url += `?${s}`;
  }
  return url;
}

/** Quem quer saber que o backend acabou de responder 401 (o gate de login). */
type UnauthorizedListener = () => void;
const unauthorizedListeners = new Set<UnauthorizedListener>();

/**
 * Avisa quando QUALQUER chamada tomar 401. O store de sessão assina isto: sem um ponto único, cada tela teria
 * de tratar "a sessão caiu" por conta própria — e as que esquecessem ficariam mostrando "Tente novamente"
 * para sempre, que é exatamente o que o painel fazia antes de existir login.
 */
export function onUnauthorized(fn: UnauthorizedListener): () => void {
  unauthorizedListeners.add(fn);
  return () => unauthorizedListeners.delete(fn);
}

async function rawRequest(method: string, path: string, opts: RequestOptions = {}): Promise<Response> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), opts.timeoutMs ?? 30_000);
  const onOuterAbort = () => controller.abort();
  opts.signal?.addEventListener('abort', onOuterAbort);
  try {
    const res = await fetch(buildUrl(path, opts.query), {
      method,
      headers: opts.body !== undefined
        ? { 'Content-Type': 'application/json', Accept: 'application/json' }
        : { Accept: 'application/json' },
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      signal: controller.signal,
      cache: 'no-store',
      // O cookie de sessão é o que autentica REST, <img> e WebSocket. Explícito e não pelo padrão do
      // navegador: o padrão já foi outro, e um dia volta a ser.
      credentials: 'same-origin',
    });
    if (!res.ok) {
      if (res.status === 401) for (const fn of unauthorizedListeners) fn();
      let parsed: unknown = null;
      try {
        parsed = await res.json();
      } catch {
        parsed = null;
      }
      throw parseErrorBody(res.status, parsed, res.headers.get('Retry-After'));
    }
    return res;
  } catch (e) {
    throw toApiError(e);
  } finally {
    clearTimeout(timeout);
    opts.signal?.removeEventListener('abort', onOuterAbort);
  }
}

async function request<T>(method: string, path: string, opts: RequestOptions = {}): Promise<T> {
  const res = await rawRequest(method, path, opts);
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  if (!text) return undefined as T;
  try {
    return JSON.parse(text) as T;
  } catch {
    throw new ApiError(res.status, 'bad_json', 'O backend devolveu uma resposta que não é JSON válido.');
  }
}

/** Resposta de `POST /releases/upload`. `imported` só vem no arquivo final do conjunto. */
export interface ReleaseUploadResult {
  stored: string;
  size_bytes: number;
  set_id: string;
  imported: { ok: boolean; label: string; reason: string | null; package: string | null;
    version_name: string | null; status: string | null } | null;
}

/** Envia um arquivo como corpo cru. Não usa `request` porque ali o corpo é sempre JSON — e serializar um APK de
 *  240 MB em base64 dentro de um JSON seria o pior jeito possível de subir um arquivo. O prazo é largo pelo
 *  mesmo motivo: o conjunto do Instagram leva minutos por uma rede doméstica. */
async function requestBinary<T>(path: string, file: Blob, query: Record<string, string>,
                                contentType = 'application/octet-stream'): Promise<T> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15 * 60_000);
  try {
    const res = await fetch(buildUrl(path, query), {
      method: 'POST',
      headers: { 'Content-Type': contentType, Accept: 'application/json' },
      body: file,
      signal: controller.signal,
      cache: 'no-store',
      credentials: 'same-origin',
    });
    if (!res.ok) {
      if (res.status === 401) for (const fn of unauthorizedListeners) fn();
      let parsed: unknown = null;
      try { parsed = await res.json(); } catch { parsed = null; }
      throw parseErrorBody(res.status, parsed, res.headers.get('Retry-After'));
    }
    return (await res.json()) as T;
  } catch (e) {
    throw toApiError(e);
  } finally {
    clearTimeout(timeout);
  }
}

const enc = encodeURIComponent;

export type FrameMode = 'thumb' | 'full';

/**
 * URL da imagem do frame. O parâmetro `f` NÃO faz parte do contrato: é apenas um "cache key" para que o
 * navegador só busque de novo quando `frame.id` mudar (o backend ignora parâmetros desconhecidos).
 */
/** URL da foto do perfil, ou `undefined` quando o perfil não tem foto (`has_avatar` do DTO): o `Avatar` mostra as
 *  iniciais SEM requisição. Antes a rota era chamada sempre e respondia 404 (um erro no console por persona sem foto,
 *  29.26). `temFoto` ausente (fixture antiga, DTO sem o campo) conta como sem foto: na dúvida, nenhuma requisição. */
export function profileAvatarUrl(profileId: string, temFoto: boolean | undefined): string | undefined {
  return temFoto ? `${API_BASE}/instagram/profiles/${enc(profileId)}/avatar` : undefined;
}

/** 28.24 F4: o arquivo de um anexo dos canais (imagem ou PDF; a rota é do mesmo login do painel e não guarda cache). */
export function canalAnexoConteudoUrl(id: number): string {
  return `${API_BASE}/canais/anexos/${id}/conteudo`;
}

/** 29.30: URL de uma imagem da persona (a que a publicação aprovada vai levar). A rota só serve a imagem pronta. */
export function personaImageUrl(personaId: string, imageId: string): string {
  return `${API_BASE}/personas/${enc(personaId)}/images/${enc(imageId)}`;
}

/** URL do ícone do aplicativo, extraído do próprio APK. Só vale pedir quando `release.has_icon`: sem ícone
 *  servível (o caso do ícone adaptativo em XML) a rota responde 404 de propósito. */
export function releaseIconUrl(releaseId: string): string {
  return `${API_BASE}/releases/${enc(releaseId)}/icon`;
}

export function frameUrl(instanceId: string, mode: FrameMode, frameId?: string | null): string {
  const qs = new URLSearchParams({ mode });
  if (frameId) qs.set('f', frameId);
  return `${API_BASE}/instances/${enc(instanceId)}/frame?${qs.toString()}`;
}

/**
 * URL para abrir uma evidência. O contrato diz apenas "url relativa"; interpretação conservadora:
 * só confiamos em `evidence.url` quando ela já aponta para `/api/...`; em qualquer outro caso usamos a
 * rota documentada `GET /api/evidence/{id}`.
 */
export function evidenceUrl(ev: Pick<Evidence, 'id' | 'url'>): string {
  if (ev.url && ev.url.startsWith(`${API_BASE}/`)) return ev.url;
  return `${API_BASE}/evidence/${enc(String(ev.id))}`;
}

function readFrameHeaders(h: Headers): FrameHeaders {
  const num = (v: string | null) => {
    if (v === null) return null;
    const n = Number(v);
    return Number.isFinite(n) && n > 0 ? n : null;
  };
  const o = h.get('X-Frame-Orientation');
  return {
    id: h.get('X-Frame-Id'),
    ts: h.get('X-Frame-Ts'),
    width: num(h.get('X-Frame-Width')),
    height: num(h.get('X-Frame-Height')),
    orientation: o === 'portrait' || o === 'landscape' ? o : null,
  };
}

export const api = {
  /** Quem está logado neste navegador, e se esta origem exige a chave de acesso para logar. */
  session: () => request<PanelSession>('GET', '/session'),
  /** Troca nome (+ chave, quando exigida) pelo cookie de sessão. O cookie é `HttpOnly`: o JS não o lê. */
  login: (operator: string, token?: string) =>
    request<PanelSession>('POST', '/login', { body: token ? { operator, token } : { operator } }),
  logout: () => request<{ ended: boolean }>('POST', '/logout'),

  health: () => request<Health>('GET', '/health'),
  snapshot: (signal?: AbortSignal) => request<Snapshot>('GET', '/snapshot', { signal, timeoutMs: 15_000 }),
  diagnostics: (refresh: boolean) =>
    request<Diagnostics>('GET', '/diagnostics', { query: { refresh: refresh ? 1 : 0 }, timeoutMs: 180_000 }),
  metrics: () => request<Metrics>('GET', '/metrics'),
  /** Estado dos canais externos (Telegram e Trello), só leitura (32.5). */
  canaisEstado: (signal?: AbortSignal) => request<CanaisEstado>('GET', '/canais/estado', { signal }),
  /** 28.24 F4: os anexos dos canais, do mais novo ao mais velho. `desde`/`ate` são ISO; `do_dono` só vale `'true'`. */
  canaisAnexos: (query: { canal?: string; direcao?: string; do_dono?: string; desde?: string; limit?: number; offset?: number },
                 signal?: AbortSignal) => request<CanalAnexosPagina>('GET', '/canais/anexos', { query, signal }),
  /** Anexa ao cartão do Trello o arquivo que o dono mandou (`confirmar` é exigido pela rota: é efeito num sistema externo). */
  canaisAnexarAoCartao: (id: number, card: string) =>
    request<CanalAnexoAoCartao>('POST', `/canais/anexos/${id}/trello`, { body: { card, confirmar: true } }),
  /** F5: a IA descreve a imagem. Chamada paga (com teto por imagem): só sai do botão de confirmar. */
  canaisLerAnexo: (id: number) =>
    request<CanalAnexoLeitura>('POST', `/canais/anexos/${id}/ler`, { body: { confirmar: true }, timeoutMs: 90_000 }),
  /** F5: o arquivo do anexo como Blob, para a aba guardá-lo em memória (a rota responde `no-store`). */
  canaisAnexoArquivo: async (id: number): Promise<Blob> =>
    (await rawRequest('GET', `/canais/anexos/${id}/conteudo`)).blob(),

  getSettings: () => request<Settings>('GET', '/settings'),
  putSettings: (patch: Partial<Settings>) => request<Settings>('PUT', '/settings', { body: patch }),
  getServerLimits: () => request<ServerLimits[]>('GET', '/servers/limits'),
  putServerLimits: (workerId: string, patch: ServerLimitsPatch) =>
    request<ServerLimits>('PUT', `/servers/${enc(workerId)}/limits`, { body: patch }),
  /** Item 24.6: pelo app escolhido (`appId`) ou, sem ele, pelos apps que o `command` usa. POST com o texto no corpo
   *  (29.26): o comando pode ter e-mail e query string vira linha de log de acesso. */
  previewDistribution: (count: number, alvo: { appId?: string; command?: string }, signal?: AbortSignal) =>
    request<DistributionPreview>('POST', '/runs/distribution', {
      body: { count, app_id: alvo.appId || undefined, command: alvo.appId ? undefined : alvo.command }, signal }),

  ai: () => request<AiStatus>('GET', '/ai'),
  /** Estado do retrieval de contexto (ADR-063): só leitura, sem rede e sem gasto. */
  contextRetrievalStatus: (signal?: AbortSignal) => request<ContextRetrievalStatus>('GET', '/context-retrieval/status', { signal }),
  /** 29.83: contatos do site por telefone, para a exclusão a pedido do titular. POST (e não GET) para o telefone não
   *  ir na URL, que vira linha de log de acesso. Devolve só o final do número, nunca nome nem mensagem. */
  portalBuscarContatos: (telefone: string) =>
    request<PortalContatosBusca>('POST', '/portal/contatos/busca', { body: { telefone } }),
  /** 29.83: apaga os contatos escolhidos (e as mensagens do bot de menos de 48 h). Irreversível. */
  portalExcluirContatos: (ids: number[], pedido_por: PortalPedidoPor) =>
    request<PortalExclusaoResultado>('POST', '/portal/contatos/excluir', { body: { ids, pedido_por } }),
  /** Saldo estimado das contas de IA (ADR-051) e os dois ajustes: nova leitura do console e limites. */
  aiBalances: (refresh = false, signal?: AbortSignal) =>
    request<AiBalancesReport>('GET', '/ai/balances', { query: refresh ? { refresh: 1 } : undefined, signal, timeoutMs: 45_000 }),
  aiBalanceReading: (account: AiBalanceAccount, body: AiBalanceReadingIn) =>
    request<AiBalancesReport>('POST', `/ai/balances/${enc(account)}`, { body }),
  aiBalanceRecharge: (account: AiBalanceAccount, body: AiBalanceRechargeIn) =>
    request<AiBalancesReport>('POST', `/ai/balances/${enc(account)}/recharge`, { body, timeoutMs: 45_000 }),
  aiBalanceRule: (account: AiBalanceAccount, body: AiBalanceRuleIn) =>
    request<AiBalancesReport>('PUT', `/ai/balances/${enc(account)}`, { body }),

  /** Custo de IA de UMA execução (`{run_id}`) ou dos últimos N dias (`{days}`). */
  usage: (scope: UsageQuery, signal?: AbortSignal) => request<UsageReport>('GET', '/usage', { query: scope, signal }),

  listFlows: (signal?: AbortSignal) => request<Flow[]>('GET', '/flows', { signal }),
  flowsCoverage: (signal?: AbortSignal) => request<FlowCoverage[]>('GET', '/flows/cobertura', { signal }),
  /** Item 7.7: o comando digitado casa com um fluxo conhecido? `null` sem casamento — nada a estimar. POST com o
   *  texto no corpo (29.25): o rascunho pode ter e-mail e query string vira linha de log de acesso. */
  flowsMatch: (command: string, signal?: AbortSignal) =>
    request<FlowCoverage | null>('POST', '/flows/match', { body: { command }, signal }),
  /** Adendo v1.72 (31.89): "isto parece com…" quando `flowsMatch` volta `null`. Sem execução e sem IA. */
  flowsSimilar: (command: string, signal?: AbortSignal) =>
    request<FlowSimilar>('POST', '/flows/similar', { body: { command }, signal }),
  profileCapabilities: (profileId: string) =>
    request<ProfileCapabilities>('GET', `/instagram/profiles/${enc(profileId)}/capacidades`),
  updateFlow: (id: string, body: FlowStatusUpdate) => request<Flow>('PUT', `/flows/${enc(id)}`, { body }),
  /** Adendo v1.71 (31.88 F2): a quem o fluxo vale, depois de salvo. Vazio nos dois = todos. Gesto de pessoa; 400 `unknown_profile` / `unknown_group`. */
  setFlowScope: (id: string, body: { profile_ids: string[]; group_ids: string[] }) =>
    request<{ flow_id: string; profile_ids: string[]; group_ids: string[] }>('PUT', `/flows/${enc(id)}/scope`, { body }),
  deleteFlow: (id: string) => request<void>('DELETE', `/flows/${enc(id)}`),

  listRecipes: (signal?: AbortSignal) => request<Recipe[]>('GET', '/recipes', { signal }),
  updateRecipe: (id: number, body: RecipeStatusUpdate) =>
    request<RecipeStatusResult>('PUT', `/recipes/${enc(String(id))}`, { body }),
  deleteRecipe: (id: number) => request<void>('DELETE', `/recipes/${enc(String(id))}`),

  listApps: () => request<AppConfig[]>('GET', '/apps'),
  createApp: (input: AppConfigInput) => request<AppConfig>('POST', '/apps', { body: input }),
  updateApp: (id: string, patch: Partial<AppConfigInput>) =>
    request<AppConfig>('PUT', `/apps/${enc(id)}`, { body: patch }),
  deleteApp: (id: string) => request<void>('DELETE', `/apps/${enc(id)}`),

  listInstances: () => request<Instance[]>('GET', '/instances'),
  /** v0.26: aparelho NOVO neste servidor (202 com o comando `create`). Teto, disco e worker remoto dão 409. */
  provisionInstance: (body: InstanceProvisionRequest) =>
    request<InstanceProvisionAccepted>('POST', '/instances', { body, timeoutMs: 60_000 }),
  /** v0.26: aposenta uma instância DINÂMICA (a do `config.yaml` sai editando o arquivo). */
  retireInstance: (id: string) => request<InstanceRetired>('DELETE', `/instances/${enc(id)}`, { timeoutMs: 120_000 }),
  updateInstance: (id: string, patch: InstanceUpdate) =>
    request<Instance>('PUT', `/instances/${enc(id)}`, { body: patch }),
  packages: (id: string) => request<PackagesResponse>('GET', `/instances/${enc(id)}/packages`, { timeoutMs: 60_000 }),
  /** v0.29: as N personas deste aparelho, cada uma com o app do vínculo e a sessão AQUI. */
  instancePersonas: (id: string) => request<PersonaOnDevice[]>('GET', `/instances/${enc(id)}/personas`),
  instanceAction: (id: string, action: InstanceAction, params?: InstanceActionParams) =>
    request<CommandAccepted>('POST', `/instances/${enc(id)}/actions/${enc(action)}`, { body: params ?? {} }),
  bulk: (req: BulkRequest) => request<BulkResult>('POST', '/instances/bulk', { body: req }),
  command: (id: string) => request<Command>('GET', `/commands/${enc(id)}`),
  workers: () => request<Worker[]>('GET', '/workers'),
  worker: (id: string) => request<Worker>('GET', `/workers/${enc(id)}`),
  enrollWorker: (label?: string) =>
    request<WorkerEnrollment>('POST', '/workers/enroll', { body: label ? { label } : {} }),
  /** Comando remoto (29.154): só com sessão nomeada de operador; 404 no host público. */
  comandoRemoto: (workerId: string) =>
    request<ComandoRemotoInterruptor>('GET', `/workers/${enc(workerId)}/comando-remoto`),
  comandoRemotoLigar: (workerId: string, ligado: boolean) =>
    request<ComandoRemotoInterruptor>('PUT', `/workers/${enc(workerId)}/comando-remoto`, { body: { ligado } }),
  comandosRemotos: (workerId: string, limite = 50) =>
    request<{ items: ComandoRemoto[] }>('GET', `/workers/${enc(workerId)}/comandos`, { query: { limite } }),
  comandoRemotoLer: (workerId: string, id: string) =>
    request<ComandoRemoto>('GET', `/workers/${enc(workerId)}/comandos/${enc(id)}`),
  comandoRemotoPedir: (workerId: string, pedido: ComandoRemotoPedido) =>
    request<{ id: string; worker_id: string; state: string; created_at: string }>(
      'POST', `/workers/${enc(workerId)}/comandos`, { body: pedido }),
  comandoRemotoCancelar: (workerId: string, id: string) =>
    request<ComandoRemoto>('POST', `/workers/${enc(workerId)}/comandos/${enc(id)}/cancelar`),
  workerMaintenance: (id: string, on: boolean) =>
    request<Worker>('POST', `/workers/${enc(id)}/maintenance`, { body: { on } }),
  removeWorker: (id: string, force = false) =>
    request<{ ok: boolean; worker_id: string }>('DELETE', `/workers/${enc(id)}`, { body: { force } }),
  rotateWorkerCredential: (id: string) =>
    request<{ credential: string }>('POST', `/workers/${enc(id)}/rotate-credential`),
  /** Aparelhos que os workers anunciam e que ainda não são instância deste parque. */
  unboundWorkerDevices: () => request<WorkerDeviceProposal[]>('GET', '/workers/devices/unbound'),
  /** Transforma um aparelho anunciado em instância: porta de túnel alocada pelo central, sem editar YAML. */
  adoptWorkerDevice: (workerId: string, serial: string, instanceId?: string) =>
    request<{ instance: Instance; tunnel_map: string; tunnel_map_file: string | null }>(
      'POST', `/workers/${enc(workerId)}/devices/adopt`,
      { body: instanceId ? { serial, instance_id: instanceId } : { serial } }),
  commands: (instanceId?: string, limit = 50, unsettled = false) =>
    request<Command[]>('GET', '/commands', {
      query: { ...(instanceId ? { instance_id: instanceId } : {}), ...(unsettled ? { unsettled: 'true' } : {}), limit },
    }),
  /** Pergunta ao estado real se aquele comando incerto deu certo. "Continua incerto" é resposta, não erro. */
  verifyCommand: (id: string) => request<CommandVerified>('POST', `/commands/${enc(id)}/verify`),
  /** A decisão de uma pessoa sobre um comando incerto — a saída que nenhuma sonda substitui. */
  resolveCommand: (id: string, body: CommandResolution) =>
    request<Command>('POST', `/commands/${enc(id)}/resolve`, { body }),
  /**
   * Pede o cancelamento de um comando ainda aberto. Pedir não é ter cancelado: o desfecho continua vindo de quem
   * executa, e `detail` diz o que foi possível fazer (interromper o boot, avisar o worker, ou só registrar).
   */
  cancelCommand: (id: string, body: CommandCancelRequest = {}) =>
    request<CommandCancelled>('POST', `/commands/${enc(id)}/cancel`, { body }),
  hierarchy: (id: string) => request<HierarchyResponse>('GET', `/instances/${enc(id)}/hierarchy`, { timeoutMs: 60_000 }),

  /** Baixa o JPEG e devolve também os cabeçalhos `X-Frame-*` (o frame realmente entregue). */
  async fetchFrame(id: string, mode: FrameMode, signal?: AbortSignal): Promise<{ blob: Blob; headers: FrameHeaders }> {
    const res = await rawRequest('GET', `/instances/${enc(id)}/frame`, { query: { mode }, signal, timeoutMs: 15_000 });
    try {
      const blob = await res.blob();
      return { blob, headers: readFrameHeaders(res.headers) };
    } catch (e) {
      throw toApiError(e);
    }
  },

  /** 29.143 (adendo v1.67): `tomar` é a tomada explícita do controle de OUTRA pessoa; sem ele, o corpo não vai. */
  takeControl: (id: string, tomar = false) =>
    request<ControlTakeResponse>('POST', `/instances/${enc(id)}/control/take`, tomar ? { body: { tomar: true } } : {}),
  releaseControl: (id: string, leaseId: string) =>
    request<ControlReleaseResponse>('POST', `/instances/${enc(id)}/control/release`, { body: { lease_id: leaseId } }),
  sendInput: (id: string, input: ManualInput) =>
    request<InputOk>('POST', `/instances/${enc(id)}/input`, { body: input }),

  listProfiles: () => request<InstagramProfile[]>('GET', '/instagram/profiles'),
  getProfile: (id: string) => request<InstagramProfile>('GET', `/instagram/profiles/${enc(id)}`),
  createProfile: (body: ProfileCreateRequest) => request<InstagramProfile>('POST', '/instagram/profiles', { body }),
  patchProfile: (id: string, body: ProfilePatchRequest) =>
    request<InstagramProfile>('PATCH', `/instagram/profiles/${enc(id)}`, { body }),
  deleteProfile: (id: string) => request<void>('DELETE', `/instagram/profiles/${enc(id)}`),
  /** Write-only: manda a senha, recebe o perfil sem ela. */
  setCredential: (id: string, body: CredentialUpdateRequest) =>
    request<InstagramProfile>('PUT', `/instagram/profiles/${enc(id)}/credential`, { body }),
  deleteCredential: (id: string) => request<InstagramProfile>('DELETE', `/instagram/profiles/${enc(id)}/credential`),
  /** 202: abre o Instagram, reaproveita a sessão ou autentica, e verifica a conta. O resultado vem no perfil. */
  /** Só leitura: servidor → aparelho → tela → apps → perfil → sessão, cada camada com a sua fonte. */
  instanceContext: (id: string) =>
    request<OperationalContext>('GET', `/instances/${enc(id)}/operational-context`),
  /** v0.29: `instanceId` escolhe outro aparelho VINCULADO (senão 409 `sem_vinculo`); sem ele, o principal. */
  profileContext: (id: string, instanceId?: string | null) =>
    request<OperationalContext>('GET', `/instagram/profiles/${enc(id)}/operational-context`,
      { query: { instance_id: instanceId ?? undefined } }),
  connectProfile: (id: string, instanceId?: string | null) =>
    request<SessionJobAccepted>('POST', `/instagram/profiles/${enc(id)}/connect`,
      { query: { instance_id: instanceId ?? undefined }, timeoutMs: 60_000 }),
  verifyProfile: (id: string, instanceId?: string | null) =>
    request<SessionJobAccepted>('POST', `/instagram/profiles/${enc(id)}/verify`,
      { query: { instance_id: instanceId ?? undefined }, timeoutMs: 60_000 }),
  logoutProfile: (id: string, instanceId?: string | null) =>
    request<SessionJobAccepted>('POST', `/instagram/profiles/${enc(id)}/logout`,
      { query: { instance_id: instanceId ?? undefined }, timeoutMs: 60_000 }),
  /** v0.27: TODAS as pessoas, com ou sem conta (`username` nulo = ainda sem conta de cadastro). */
  listPersonas: () => request<PersonaDTO[]>('GET', '/personas'),
  getPersona: (id: string) => request<PersonaDTO>('GET', `/personas/${enc(id)}`),
  /** v0.29 (N:N): soma um aparelho à persona, sem tirar ninguém de lá. Duas contas do mesmo app no mesmo aparelho
   *  → 409 `conta_do_app_ja_no_aparelho` (D2-a). */
  bindPersonaDevice: (id: string, body: PersonaDeviceBindRequest) =>
    request<PersonaDTO>('POST', `/personas/${enc(id)}/devices`, { body }),
  /** Com `appId`, só o vínculo daquele app. O principal que sai é trocado pelo vínculo mais antigo. */
  unbindPersonaDevice: (id: string, instanceId: string, appId?: string | null) =>
    request<PersonaDTO>('DELETE', `/personas/${enc(id)}/devices/${enc(instanceId)}`,
      { query: { app_id: appId ?? undefined } }),
  setPrimaryPersonaDevice: (id: string, instanceId: string) =>
    request<PersonaDTO>('PUT', `/personas/${enc(id)}/devices/${enc(instanceId)}/primary`),
  createPersona: (body: PersonaInput | PersonaCreateRequest) => request<PersonaDTO>('POST', '/personas', { body }),
  /** Por seção: `traits`/`visual`/`biography` são mesclados no servidor; o que não vier fica como está. */
  updatePersona: (id: string, body: Partial<PersonaInput> | PersonaPatchRequest) =>
    request<PersonaDTO>('PATCH', `/personas/${enc(id)}`, { body }),
  deletePersona: (id: string) => request<void>('DELETE', `/personas/${enc(id)}`),
  /** Chamada PAGA (papel social): devolve um rascunho no formato de `POST /personas`, sem gravar nada. */
  generatePersona: (body: PersonaGenerateRequest) =>
    request<PersonaCreateRequest>('POST', '/personas/generate', { body, timeoutMs: 120_000 }),
  /** Chamada PAGA quando há lacuna: completa só o que está vazio. */
  /** Completa SÓ o vazio (chamada paga). `instructions` = o que o dono quer para o que falta (adendo v0.32). */
  enrichPersona: (id: string, instructions?: string) =>
    request<PersonaDTO>('POST', `/personas/${enc(id)}/enrich`, {
      body: instructions ? { instructions } : undefined, timeoutMs: 120_000,
    }),
  listPersonaImages: (id: string) => request<PersonaImage[]>('GET', `/personas/${enc(id)}/images`),
  /** 202: gera em segundo plano; cada imagem chega por `persona.image.updated`. */
  generatePersonaImages: (id: string, count: number) =>
    request<PersonaImagesAccepted>('POST', `/personas/${enc(id)}/images`, { body: { count } }),
  /** Upload: o corpo é a própria imagem (`image/jpeg` ou `image/png`), não JSON. 29.81: `feitaPorIa` é a resposta do
   *  dono ("feita por IA?"); `null` = não informado (o parâmetro não vai). */
  uploadPersonaImage: (id: string, file: File, feitaPorIa: boolean | null = null) =>
    requestBinary<PersonaImage>(`/personas/${enc(id)}/images`, file,
      feitaPorIa === null ? {} : { feita_por_ia: String(feitaPorIa) },
      file.type === 'image/png' ? 'image/png' : 'image/jpeg'),
  /** 29.81: corrige se a foto enviada foi feita por IA (`null` volta a "não informado"). As publicações ainda por
   *  fazer com ela passam a pedir (ou dispensar) o rótulo, e o sim dado antes deixa de cobri-las. */
  setPersonaImageFeitaPorIa: (id: string, imageId: string, feitaPorIa: boolean | null) =>
    request<PersonaImage>('PUT', `/personas/${enc(id)}/images/${enc(imageId)}/feita-por-ia`,
      { body: { feita_por_ia: feitaPorIa } }),
  setPrimaryPersonaImage: (id: string, imageId: string) =>
    request<PersonaDTO>('PUT', `/personas/${enc(id)}/images/${enc(imageId)}/primary`),
  deletePersonaImage: (id: string, imageId: string) =>
    request<void>('DELETE', `/personas/${enc(id)}/images/${enc(imageId)}`),
  /** Testar persona: devolve como ela responderia. Não toca no aparelho e não publica nada. */
  previewPersona: (id: string, body: PersonaPreviewRequest) =>
    request<SocialDraft>('POST', `/personas/${enc(id)}/preview`, { body, timeoutMs: 60_000 }),

  listMemory: (profileId: string, limit = 100, appId?: string | null) =>
    request<MemoryItem[]>('GET', `/instagram/profiles/${enc(profileId)}/memory`, { query: { limit, app_id: appId ?? undefined } }),
  addMemory: (profileId: string, body: MemoryInput) =>
    request<MemoryItem>('POST', `/instagram/profiles/${enc(profileId)}/memory`, { body }),
  deleteMemory: (profileId: string, memoryId: string) =>
    request<void>('DELETE', `/instagram/profiles/${enc(profileId)}/memory/${enc(memoryId)}`),
  listInteractions: (profileId: string, limit = 30, appId?: string | null) =>
    request<SocialInteraction[]>('GET', `/instagram/profiles/${enc(profileId)}/interactions`, { query: { limit, app_id: appId ?? undefined } }),
  /** 31.346 (v1.145/v1.149): só leitura; `{id}` é o `account_id` da conta (ou o do igfarm). */
  getCicloDaConta: (accountId: string) =>
    request<CicloDaConta>('GET', `/instagram/contas/${enc(accountId)}/ciclo`),
  listAccounts: (profileId: string) =>
    request<ProfileAccount[]>('GET', `/instagram/profiles/${enc(profileId)}/accounts`),
  addAccount: (profileId: string, body: ProfileAccountCreateRequest) =>
    request<ProfileAccount>('POST', `/instagram/profiles/${enc(profileId)}/accounts`, { body }),
  patchAccount: (profileId: string, accountId: string, body: ProfileAccountPatchRequest) =>
    request<ProfileAccount>('PATCH', `/instagram/profiles/${enc(profileId)}/accounts/${enc(accountId)}`, { body }),
  deleteAccount: (profileId: string, accountId: string) =>
    request<void>('DELETE', `/instagram/profiles/${enc(profileId)}/accounts/${enc(accountId)}`),
  setAccountCredential: (profileId: string, accountId: string, body: CredentialUpdateRequest) =>
    request<ProfileAccount>('PUT', `/instagram/profiles/${enc(profileId)}/accounts/${enc(accountId)}/credential`, { body }),
  planAccount: (profileId: string, body: PlannedAccountRequest) =>
    request<ProfileAccount>('POST', `/instagram/profiles/${enc(profileId)}/accounts/planned`, { body }),
  handleSuggestions: (profileId: string, appId: string) =>
    request<{ suggestions: HandleSuggestion[] }>('GET', `/instagram/profiles/${enc(profileId)}/accounts/handle-suggestions`,
                                                 { query: { app_id: appId } }),
  prepareAccountCredential: (profileId: string, accountId: string, body: CredentialPrepareRequest) =>
    request<ProfileAccount>('POST', `/instagram/profiles/${enc(profileId)}/accounts/${enc(accountId)}/credential/prepare`, { body }),
  provisionAccount: (profileId: string, accountId: string, body: ProvisioningTransitionRequest) =>
    request<ProfileAccount | ProvisioningCancelled>('POST',
      `/instagram/profiles/${enc(profileId)}/accounts/${enc(accountId)}/provisioning`, { body }),
  deleteAccountCredential: (profileId: string, accountId: string) =>
    request<ProfileAccount>('DELETE', `/instagram/profiles/${enc(profileId)}/accounts/${enc(accountId)}/credential`),
  /** 23.9: o cofre clona a senha de outra conta DESTA persona para esta; o consentimento desta não muda. */
  cloneAccountCredential: (profileId: string, accountId: string, body: CredentialCloneRequest) =>
    request<ProfileAccount>('POST', `/instagram/profiles/${enc(profileId)}/accounts/${enc(accountId)}/credential/clone`, { body }),
  /** Marca o consentimento sem redigitar a senha (v0.28). Sem senha guardada, 409 `no_credential`. */
  consentAccountCredential: (profileId: string, accountId: string) =>
    request<ProfileAccount>('POST', `/instagram/profiles/${enc(profileId)}/accounts/${enc(accountId)}/credential/consent`),
  /** Sessão POR CONTA (v0.28): 202; o resultado aparece na conta. */
  /** v0.29: `instanceId` escolhe outro aparelho vinculado; sem ele, o principal. */
  accountSession: (profileId: string, accountId: string, verb: 'connect' | 'verify' | 'logout', instanceId?: string | null) =>
    request<SessionJobAccepted>('POST', `/instagram/profiles/${enc(profileId)}/accounts/${enc(accountId)}/session/${verb}`,
      { query: { instance_id: instanceId ?? undefined }, timeoutMs: 60_000 }),
  accountAuthAttempts: (profileId: string, accountId: string, limit = 20) =>
    request<AuthAttempt[]>('GET', `/instagram/profiles/${enc(profileId)}/accounts/${enc(accountId)}/auth-attempts`,
      { query: { limit } }),
  startTraining: (instanceId: string, body: { intent: string; lease_id: string; app_id?: string | null; profile_id?: string | null }) =>
    request<TrainingSession>('POST', `/instances/${enc(instanceId)}/training`, { body }),
  listTraining: (instanceId?: string) =>
    request<TrainingSession[]>('GET', '/training', { query: { instance_id: instanceId } }),
  getTraining: (id: string) => request<TrainingSession>('GET', `/training/${enc(id)}`),
  // O `lease_id` vai quando a aba tem um (31.92: o backend passa a exigi-lo para encerrar gravação viva); sem lease, o corpo não vai.
  stopTraining: (id: string, leaseId?: string | null) =>
    request<TrainingSession>('POST', `/training/${enc(id)}/stop`, leaseId ? { body: { lease_id: leaseId } } : {}),
  discardTraining: (id: string, leaseId?: string | null) =>
    request<TrainingSession>('POST', `/training/${enc(id)}/discard`, leaseId ? { body: { lease_id: leaseId } } : {}),
  /** Adendo v1.75: abre o treino já ligado à etapa que falhou (`failed`/`uncertain`). Só quem está com o controle abre. */
  startTrainingFromRun: (body: TrainingFromRunBody) => request<TrainingSession>('POST', '/training/from-run', { body }),
  /** Adendo v1.80: a intenção, a pergunta e o rótulo da causa que o diagnóstico sugere para a etapa. Só leitura, sem IA e sem o controle do aparelho. */
  ensinoSugerido: (runId: string, stepId: string) =>
    request<EnsinoSugerido | null>('GET', `/runs/${enc(runId)}/steps/${enc(stepId)}/ensino-sugerido`),
  /** Adendo v1.70: tira a ÚLTIMA entrada da gravação viva. `seq` é a que a pessoa viu como última: se outra chegou, 409 `entrada_mudou`. */
  undoTraining: (id: string, leaseId: string, seq: number) =>
    request<TrainingUndoResult>('POST', `/training/${enc(id)}/undo`, { body: { lease_id: leaseId, seq } }),
  /** Adendo v1.63: com `answers`, a IA propõe de novo levando as respostas da pessoa; sem elas, o corpo não vai (igual a antes). */
  proposeTraining: (id: string, answers?: TrainingAnswer[]) =>
    request<TrainingSession>('POST', `/training/${enc(id)}/propose`, answers?.length ? { body: { answers } } : {}),
  saveTraining: (id: string, body: { proposal?: TrainingProposal | null; profile_ids?: string[]; group_ids?: string[]; scope_on_proof?: 'todos' | 'quem_ensinou' }) =>
    request<TrainingSaveResult>('POST', `/training/${enc(id)}/save`, { body }),
  /** Adendo v1.58: a mesma conferência e destilação do salvar, sem gravar nada. */
  previewTraining: (id: string, body: { proposal?: TrainingProposal | null; profile_ids?: string[]; group_ids?: string[]; scope_on_proof?: 'todos' | 'quem_ensinou' }) =>
    request<TrainingPreview>('POST', `/training/${enc(id)}/preview`, { body }),
  /** Adendo v1.58: refaz as receitas das etapas que ficaram sem receita numa sessão já salva. */
  redoTrainingRecipes: (id: string) => request<TrainingRecipesResult>('POST', `/training/${enc(id)}/recipes`),
  // Ensino v2 e habilidades (fase F): só existem com `health.features.skills`; desligado, o backend responde 404
  // `skills_disabled`.
  listSkills: (signal?: AbortSignal) => request<SkillSummary[]>('GET', '/skills', { signal }),
  /** Transição de uma versão (§10.3). A recusa do domínio vem como `code`/`message` (e `pending`/`errors`). */
  transitionSkill: (skillId: string, version: number, body: { to: SkillState; reason?: string; manual?: boolean }) =>
    request<SkillVersionDetail>('POST', `/skills/${enc(skillId)}/versions/${version}/status`, { body }),
  // Fase J: converter um fluxo em habilidade (adoção + rascunho descompilado, numa transação) e desfazer.
  adoptFlow: (flowId: string, body: { skill_id?: string; reason?: string } = {}) =>
    request<FlowConversion>('POST', `/flows/${enc(flowId)}/adopt`, { body }),
  releaseFlow: (flowId: string, body: { reason?: string } = {}) =>
    request<FlowConversionUndone>('POST', `/flows/${enc(flowId)}/release`, { body }),
  // 31.91 T1 (ADR-078): as rotas do ensino v2 (`/teaching-sessions`, `/skill-candidates`) saíram do painel; o ensino é o Modo treinamento.
  appsOverview: (days = 7) => request<AppOverview[]>('GET', '/apps-overview', { query: { days } }),
  appOverview: (appId: string, days = 30) =>
    request<AppDetail>('GET', `/apps/${enc(appId)}/overview`, { query: { days } }),
  listAuthAttempts: (profileId: string, limit = 20) =>
    request<AuthAttempt[]>('GET', `/instagram/profiles/${enc(profileId)}/auth-attempts`, { query: { limit } }),
  listProfileRuns: (profileId: string, limit = 20) =>
    request<RunSummary[]>('GET', `/instagram/profiles/${enc(profileId)}/runs`, { query: { limit } }),
  /** `pkg` (23.10): sem ele, o app âncora, como sempre; com ele, o catálogo do app escolhido no painel. */
  getPolicy: (profileId: string, pkg?: string | null) =>
    request<ProfilePolicy>('GET', `/instagram/profiles/${enc(profileId)}/policy`, { query: { package: pkg ?? undefined } }),
  setPolicy: (profileId: string, body: ProfilePolicyPatch, pkg?: string | null) =>
    request<ProfilePolicy>('PUT', `/instagram/profiles/${enc(profileId)}/policy`,
      { body, query: { package: pkg ?? undefined } }),
  listPolicyGroups: (pkg?: string | null) =>
    request<PolicyGroup[]>('GET', '/instagram/policy-groups', { query: { package: pkg ?? undefined } }),
  createPolicyGroup: (body: PolicyGroupCreateRequest, pkg?: string | null) =>
    request<PolicyGroup>('POST', '/instagram/policy-groups', { body, query: { package: pkg ?? undefined } }),
  /** Um grupo visto por UM app (23.10): `capabilities`/`loosened` são o recorte do catálogo de `pkg` (sem ele, o
   *  âncora) — a mesma chave em dois catálogos são duas escolhas do grupo. */
  getPolicyGroup: (id: string, pkg?: string | null) =>
    request<PolicyGroup>('GET', `/instagram/policy-groups/${enc(id)}`, { query: { package: pkg ?? undefined } }),
  updatePolicyGroup: (id: string, body: PolicyGroupPatchRequest, pkg?: string | null) =>
    request<PolicyGroup>('PUT', `/instagram/policy-groups/${enc(id)}`, { body, query: { package: pkg ?? undefined } }),
  deletePolicyGroup: (id: string) => request<void>('DELETE', `/instagram/policy-groups/${enc(id)}`),
  /** O pacote é obrigatório: com um padrão aqui, quem esquecia de dizer o app recebia o catálogo do Instagram
   *  como se fosse o dele. */
  listCapabilities: (pkg: string) =>
    request<Capability[]>('GET', '/capabilities', { query: { package: pkg } }),
  /** Os aplicativos que o registro do backend conhece. É daqui que sai o seletor de app da loja. */
  listAppCatalog: () => request<AppCatalogEntry[]>('GET', '/app-catalog'),

  listReleases: (pkg?: string) =>
    request<AppRelease[]>('GET', '/releases', { query: { package: pkg ?? '' } }),
  importReleases: (body: { source_reference?: string; expected_package?: string } = {}) =>
    request<{ imported: unknown[] }>('POST', '/releases/import', { body, timeoutMs: 180_000 }),
  approveSignature: (releaseId: string, note?: string) =>
    request<AppRelease>('POST', `/releases/${enc(releaseId)}/approve-signature`, { body: { note: note ?? null } }),
  /** Para onde ESTA versão pode ir, com o motivo de cada aparelho que não pode. A incompatibilidade é decidida
   *  no backend pela mesma função que recusa a instalação — repeti-la aqui ficaria desatualizada na primeira
   *  mudança de regra, e a tela prometeria o que o backend recusa. */
  releaseTargets: (releaseId: string) =>
    request<ReleaseTargets>('GET', `/releases/${enc(releaseId)}/targets`),
  /** Instala uma versão NUM aparelho. 202 com `command_id`: leva minutos, e o desfecho aparece em `/app-state`
   *  e no comando. É o caminho de "Instalar em…", que escolhe destinos em vez de empurrar para todos. */
  installApp: (instanceId: string, releaseId: string) =>
    request<CommandAccepted & { instance_id: string; release_id: string }>(
      'POST', `/instances/${enc(instanceId)}/app/install`, { body: { release_id: releaseId } }),
  /** Envia UM arquivo do conjunto. O corpo é o arquivo cru; `final` manda importar a pasta inteira. Existe para
   *  quem abre o painel de fora do servidor: até aqui a única entrada de APK era a pasta local da máquina. */
  uploadRelease: (file: File, setId: string, final: boolean, sourceReference?: string) =>
    requestBinary<ReleaseUploadResult>(
      `/releases/upload`, file,
      { filename: file.name, set_id: setId, final: String(final),
        ...(sourceReference ? { source_reference: sourceReference } : {}) }),
  listAppState: (pkg?: string) =>
    request<DeviceAppState[]>('GET', '/app-state', { query: { package: pkg ?? '' } }),
  /** Relê do APARELHO o que está instalado. Aceito (202): o resultado aparece em `/app-state`, com `verified_at`
   *  novo. Até existir este botão, reobservar exigia um curl na rota — e a tela mostrava dado de dias atrás
   *  com a mesma cara de recém-lido. */
  verifyApp: (instanceId: string, pkg: string) =>
    request<CommandAccepted & { instance_id: string; package: string }>(
      'POST', `/instances/${enc(instanceId)}/app/verify`, { body: { package: pkg } }),
  /** Canário, promoção, quarentena e rollback. Os dois primeiros respondem na hora; os que mexem no aparelho
   *  são aceitos e o resultado aparece em `/app-state`. */
  releaseLifecycle: (releaseId: string, body: ReleaseLifecycleBody) =>
    request<{ accepted: boolean; dry_run?: boolean; release?: AppRelease; devices?: DistributeDevice[];
      /** Só em `promote` (ADR-026): a versão que o parque passa a perseguir — promover uma MENOR não muda o alvo. */
      target_release_id?: string | null;
      /** Só em `promote`: a promoção valeu, mas a convergência imediata falhou; a varredura entrega depois. */
      convergence_error?: string;
      /** Canário, rollback e entrega por aparelho devolvem um comando acompanhável; promote/quarantine, não:
       *  são decisões de banco que já respondem na hora. */
      command_id?: string; state?: CommandState }>(
      'POST', `/releases/${enc(releaseId)}/lifecycle`, { body }),
  /** A loja (Play Store) como fonte do aplicativo. Instalar/atualizar NA loja é sempre um toque do usuário. */
  storeStatus: (pkg: string) => request<StoreStatus>('GET', '/store', { query: { package: pkg } }),
  storeOpenListing: (pkg: string) =>
    request<{ ok: boolean }>('POST', '/store/open-listing', { body: { package: pkg } }),
  /** 202 com `command_id`: o resultado aparece pelo comando, não por recarregar a página na hora certa. */
  storeSync: (pkg: string) =>
    request<CommandAccepted>('POST', '/store/sync', { body: { package: pkg } }),
  /** A vitrine: por app, ícone, versão promovida, aparelhos por versão e quem está atrasado. */
  appStore: () => request<AppStoreEntry[]>('GET', '/app-store'),
  /** Proxy do aparelho, distribuído como as versões: perfis nomeados e o estado de cada aparelho. */
  listProxies: () => request<ProxyList>('GET', '/proxies'),
  createProxy: (body: { name: string; host: string; port: number }) =>
    request<ProxyProfile>('POST', '/proxies', { body }),
  deleteProxy: (id: string) => request<void>('DELETE', `/proxies/${enc(id)}`),
  /** `proxy_id: null` tira o proxy. Alvo: `instance_ids` OU `all: true` (nunca inferido). `dry_run` = prévia. */
  applyProxy: (body: { proxy_id: string | null; instance_ids?: string[]; all?: boolean; dry_run?: boolean }) =>
    request<{ accepted: boolean; dry_run: boolean; devices: DistributeDevice[] }>('POST', '/proxies/apply', { body }),

  // ---- Rede por aparelho (ADR-056, C3, rotas 25.2/25.8) -------------------------------------------------------
  // Envelopes exatamente como `backend/app/devices/rede.py` (D1) devolve — não um desenho livre do painel.
  /** Perfis de VPN e de proxy, sem segredo (só `has_secret`), cada um com os aparelhos que o pedem hoje. */
  listNetworkProfiles: () => request<NetworkProfileList>('GET', '/network/profiles'),
  /** O segredo (chave, senha, certificado) vai só nesta chamada, uma vez; nunca volta em nenhuma leitura. */
  createNetworkProfile: (body: NetworkProfileCreateRequest) =>
    request<NetworkProfile>('POST', '/network/profiles', { body }),
  updateNetworkProfileSaida: (id: string, body: NetworkProfileSaidaRequest) =>
    request<NetworkProfile>('PUT', `/network/profiles/${enc(id)}`, { body }),
  deleteNetworkProfile: (id: string) => request<void>('DELETE', `/network/profiles/${enc(id)}`),
  /** Uma linha por aparelho do parque (a loja já vem de fora): desejado × observado novo, o legado da 041, conta
   *  real vinculada e o que falta — tudo resolvido pelo backend, nunca recombinado aqui. */
  listNetworkDevices: () => request<NetworkDeviceList>('GET', '/network/devices'),
  /** `dry_run` = prévia obrigatória antes de qualquer atribuição em lote (nunca se pula a prévia). */
  assignNetwork: (body: NetworkAssignRequest) =>
    request<NetworkAssignResult>('POST', '/network/assign', { body }),
  /** Registra o PEDIDO de medir de novo (202): não mede nada aqui — a sonda (25.5) é quem executa. */
  verifyNetworkDevice: (instanceId: string) =>
    request<NetworkRequestAccepted>('POST', `/network/devices/${enc(instanceId)}/verify`),
  /** Registra o PEDIDO de reaplicar a configuração desejada (202): a aplicação (25.4) é quem executa. */
  reapplyNetworkDevice: (instanceId: string) =>
    request<NetworkRequestAccepted>('POST', `/network/devices/${enc(instanceId)}/reapply`),
  /** O servidor sing-box do central e o `remote_access` (25.7): endpoint da LAN, aparelhos remotos e a última
   *  leitura do firewall (cache; o GET não roda PowerShell). */
  getNetworkServer: () => request<NetworkServerStatus>('GET', '/network/server'),
  /** Relê o firewall do central JÁ (só leitura) e devolve o `remote_access` com o comando do dono. */
  checkNetworkServerFirewall: () => request<NetworkRemoteAccess>('POST', '/network/server/firewall-check'),

  listApprovals: (status: string | null = 'pending', profileId?: string, runId?: string) =>
    request<Approval[]>('GET', '/approvals', {
      query: { status: status ?? '', profile_id: profileId ?? '', run_id: runId ?? '' },
    }),
  decideApproval: (id: string, verb: 'approve' | 'edit' | 'reject', body: { content?: string; note?: string } = {}) =>
    request<Approval>('POST', `/approvals/${enc(id)}/decide`, { body: { verb, ...body } }),
  /** Decide vários de uma vez: cada item com o seu verbo. Um recusado não impede os outros — veja `refused`. */
  decideApprovals: (decisions: ApprovalDecisionItem[]) =>
    request<ApprovalBatchResult>('POST', '/approvals/decide', { body: { decisions } }),

  createRun: (req: CreateRunRequest) => request<RunSummary>('POST', '/runs', { body: req, timeoutMs: 120_000 }),
  /** v0.29 (ADR-044): para quem e onde a execução aconteceria, com a origem de cada alvo e as perguntas. Não grava
   *  nada e não chama o planejador — é a prévia que o Comando mostra antes de Executar. */
  resolveRunTargets: (body: ResolveTargetsRequest, signal?: AbortSignal) =>
    request<ResolveTargetsResponse>('POST', '/runs/targets/resolve', { body, signal }),
  /** ADR-047: o assistente reescreve o comando em blocos e diz o que ainda falta. Uma chamada de IA (papel `plan`);
   *  não cria execução. Credencial no texto ou numa resposta → 409 `credencial_no_comando`; resposta a uma pergunta
   *  de senha ou código, ou com cara de credencial → 409 `credencial_na_resposta` (29.52), sem chamar a IA. */
  refineCommand: (body: RefineCommandRequest, signal?: AbortSignal) =>
    request<CommandRefinement>('POST', '/commands/refine', { body, signal, timeoutMs: 120_000 }),
  /** ADR-047: responde a uma execução em `needs_input` — nasce a sucessora com o comando novo e os mesmos alvos, e a
   *  antiga é cancelada apontando para ela. Pergunta de senha ou código aberta, ou resposta com cara de credencial →
   *  409 `credencial_na_resposta` (29.52), sem execução nova e sem cancelar a antiga. */
  runSuccessor: (runId: string, body: RunSuccessorRequest) =>
    request<RunSummary>('POST', `/runs/${enc(runId)}/successor`, { body, timeoutMs: 120_000 }),
  /** ADR-050: quem faz e onde, pelo pedido (modo Automático). Não cria execução; pode custar uma chamada de IA
   *  (papel `plan`) quando a escolha depende do perfil das personas. */
  suggestRunTargets: (body: RunTargetsSuggestRequest, signal?: AbortSignal) =>
    request<RunTargetsSuggestion>('POST', '/runs/targets/suggest', { body, signal, timeoutMs: 120_000 }),
  /** Página do histórico. `instanceId`/`workerId` filtram por ONDE a execução rodou (fotografia do objetivo). */
  listRuns: (limit = 20, offset = 0, instanceId?: string, workerId?: string) =>
    request<RunPage>('GET', '/runs', {
      query: { limit, offset, instance_id: instanceId ?? '', worker_id: workerId ?? '' },
    }),
  getRun: (id: string, signal?: AbortSignal) => request<RunDetail>('GET', `/runs/${enc(id)}`, { signal }),
  runEvents: (id: string, after = 0, limit = 500, signal?: AbortSignal) =>
    request<EventRecord[]>('GET', `/runs/${enc(id)}/events`, { query: { after, limit }, signal }),
  runReport: (id: string) => request<RunReport>('GET', `/runs/${enc(id)}/report`),
  /** Item 18.3: o normal medido de cada etapa do plano, com a janela efetiva. Não chama IA; 409 `no_plan` = ainda
   *  sem plano. Lida por `features/runs/projecao.ts::lerProjecao` (tolerante). */
  runProjection: (id: string, signal?: AbortSignal) => request<unknown>('GET', `/runs/${enc(id)}/projection`, { signal }),
  startRun: (id: string) => request<RunSummary>('POST', `/runs/${enc(id)}/start`),
  /** 30.61: a prévia da porta numa execução `planned` (só leitura; não chama IA). 409 `invalid_state` fora de `planned`. */
  portaDoPlano: (id: string, signal?: AbortSignal) => request<PreviaDaPorta>('GET', `/runs/${enc(id)}/porta`, { signal }),
  /** 30.61: "Aprovar N e iniciar". 409 `plano_mudou` traz `mudaram` e a `previa` nova no `detail`; nada é gravado. */
  aprovarPlano: (id: string, aprovar: AprovarPlanoItem[], tirar: string[], vistaEm?: string) =>
    request<AprovarPlanoResultado>('POST', `/runs/${enc(id)}/aprovar-plano`, {
      body: { aprovar, tirar, vista_em: vistaEm ?? null },
    }),
  /** 30.61 "Renovar": só o sim ainda válido; o vencido volta para rever (409 `sim_vencido` quando nada renovou). */
  renovarPorta: (id: string) => request<RenovarPlanoResultado>('POST', `/runs/${enc(id)}/porta/renovar`),
  /** 30.68: o selo, o motivo e a chave do item com o texto editado no cartão. Só leitura (não grava, não chama IA). */
  previaDoItem: (id: string, stepId: string, texto: string) =>
    request<PreviaDoItem>('POST', `/runs/${enc(id)}/porta/item`, { body: { step_id: stepId, texto } }),
  pauseRun: (id: string) => request<RunSummary>('POST', `/runs/${enc(id)}/pause`),
  resumeRun: (id: string) => request<RunSummary>('POST', `/runs/${enc(id)}/resume`),
  cancelRun: (id: string) => request<RunSummary>('POST', `/runs/${enc(id)}/cancel`),
  retryFailed: (id: string) => request<RetryFailedResponse>('POST', `/runs/${enc(id)}/retry_failed`),
  resolveObjective: (runId: string, objectiveId: string, body: ResolveRequest) =>
    request<Objective>('POST', `/runs/${enc(runId)}/objectives/${enc(objectiveId)}/resolve`, { body }),

  /** Conteúdo textual de uma evidência (`GET /api/evidence/{id}` devolve text/plain para tipos não-imagem). */
  async evidenceText(ev: Pick<Evidence, 'id'>, signal?: AbortSignal): Promise<string> {
    const res = await rawRequest('GET', `/evidence/${enc(String(ev.id))}`, { signal });
    try {
      return await res.text();
    } catch (e) {
      throw toApiError(e);
    }
  },
};

/** O mesmo `request` (erros no formato único, cookie de sessão, prazo) para as features que têm o próprio `api.ts`
 *  (o aprendizado, ADR-054): as rotas delas não entram no objeto `api`, mexido por muitas frentes ao mesmo tempo. */
export { request as apiRequest };

/** URL do WebSocket derivada de `location` (nunca fixa a porta 8000: o proxy/servidor resolve). */
export function wsUrl(lastEventId: number): string {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${proto}//${window.location.host}${API_BASE}/ws?last_event_id=${enc(String(lastEventId))}`;
}

// ---------------------------------------------------------------- personas em lote (v0.34)
// Bloco próprio no fim do arquivo: o lote tem só estas duas rotas, e o objeto `api` é mexido por outras frentes.
import type { PersonaBatch, PersonaBatchAccepted, PersonaBatchRequest } from './types';

export const apiLote = {
  /** 202: gera em segundo plano (concorrência 2); cada item é uma chamada PAGA pelo papel `social`. */
  generatePersonaBatch: (body: PersonaBatchRequest) =>
    request<PersonaBatchAccepted>('POST', '/personas/generate/batch', { body }),
  /** Estado do lote; 404 quando o servidor reiniciou (o lote vive na memória dele). */
  getPersonaBatch: (id: string, signal?: AbortSignal) =>
    request<PersonaBatch>('GET', `/personas/generate/batch/${enc(id)}`, { signal }),
};
