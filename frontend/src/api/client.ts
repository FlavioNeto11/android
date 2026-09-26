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
  CreateRunRequest,
  CredentialUpdateRequest,
  Diagnostics,
  EventRecord,
  Evidence,
  Flow,
  FlowCoverage,
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
  Persona,
  PersonaInput,
  PersonaPreviewRequest,
  ProfileCapabilities,
  ProfileCreateRequest,
  ProfilePatchRequest,
  ProfilePolicy,
  PolicyGroup,
  ProfileAccount,
  ProfileAccountCreateRequest,
  ProfileAccountPatchRequest,
  AppOverview,
  TrainingProposal,
  TrainingSaveResult,
  TrainingSession,
  AppDetail,
  PolicyGroupCreateRequest,
  PolicyGroupPatchRequest,
  ProfilePolicyPatch,
  Recipe,
  RecipeStatusResult,
  RecipeStatusUpdate,
  ResolveRequest,
  RetryFailedResponse,
  RunDetail,
  RunPage,
  RunReport,
  RunSummary,
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
      return 'Tentativas demais em pouco tempo. Espere um minuto antes de tentar de novo.';
    case 'forbidden_host':
      return 'Este backend não aceita ser chamado por este endereço. Ele precisa constar em server.public_hosts '
        + 'no config.yaml do central — é a defesa que impede um nome de fora se passar por ele.';
    case 'forbidden_origin':
      return 'A origem desta página não está em server.allowed_origins no config.yaml do central.';
    case 'timeout':
      return 'O backend está lento ou travado. Veja o Diagnóstico e tente novamente em instantes.';
    case 'stale_frame':
    case 'frame_mismatch':
      return 'A tela mudou — aguarde o novo frame e tente de novo.';
    case 'not_controller':
      return 'Você não está com o controle desta instância. Use “Assumir controle” antes de interagir.';
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

function parseErrorBody(status: number, body: unknown): ApiError {
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
      throw parseErrorBody(res.status, parsed);
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
async function requestBinary<T>(path: string, file: Blob, query: Record<string, string>): Promise<T> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15 * 60_000);
  try {
    const res = await fetch(buildUrl(path, query), {
      method: 'POST',
      headers: { 'Content-Type': 'application/octet-stream', Accept: 'application/json' },
      body: file,
      signal: controller.signal,
      cache: 'no-store',
      credentials: 'same-origin',
    });
    if (!res.ok) {
      if (res.status === 401) for (const fn of unauthorizedListeners) fn();
      let parsed: unknown = null;
      try { parsed = await res.json(); } catch { parsed = null; }
      throw parseErrorBody(res.status, parsed);
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
/** URL da foto do perfil. Responde 404 quando não há foto — o `Avatar` cai nas iniciais nesse caso. */
export function profileAvatarUrl(profileId: string): string {
  return `${API_BASE}/instagram/profiles/${enc(profileId)}/avatar`;
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

  getSettings: () => request<Settings>('GET', '/settings'),
  putSettings: (patch: Partial<Settings>) => request<Settings>('PUT', '/settings', { body: patch }),
  getServerLimits: () => request<ServerLimits[]>('GET', '/servers/limits'),
  putServerLimits: (workerId: string, patch: ServerLimitsPatch) =>
    request<ServerLimits>('PUT', `/servers/${enc(workerId)}/limits`, { body: patch }),
  previewDistribution: (count: number, appId: string, signal?: AbortSignal) =>
    request<DistributionPreview>('GET', '/runs/distribution', { query: { count, app_id: appId }, signal }),

  ai: () => request<AiStatus>('GET', '/ai'),

  /** Custo de IA de UMA execução (`{run_id}`) ou dos últimos N dias (`{days}`). */
  usage: (scope: UsageQuery, signal?: AbortSignal) => request<UsageReport>('GET', '/usage', { query: scope, signal }),

  listFlows: (signal?: AbortSignal) => request<Flow[]>('GET', '/flows', { signal }),
  flowsCoverage: (signal?: AbortSignal) => request<FlowCoverage[]>('GET', '/flows/cobertura', { signal }),
  /** Item 7.7: o comando digitado casa com um fluxo conhecido? `null` sem casamento — nada a estimar. */
  flowsMatch: (command: string, signal?: AbortSignal) =>
    request<FlowCoverage | null>('GET', '/flows/match', { query: { command }, signal }),
  profileCapabilities: (profileId: string) =>
    request<ProfileCapabilities>('GET', `/instagram/profiles/${enc(profileId)}/capacidades`),
  updateFlow: (id: string, body: FlowStatusUpdate) => request<Flow>('PUT', `/flows/${enc(id)}`, { body }),
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
  updateInstance: (id: string, patch: InstanceUpdate) =>
    request<Instance>('PUT', `/instances/${enc(id)}`, { body: patch }),
  packages: (id: string) => request<PackagesResponse>('GET', `/instances/${enc(id)}/packages`, { timeoutMs: 60_000 }),
  instanceAction: (id: string, action: InstanceAction, params?: InstanceActionParams) =>
    request<CommandAccepted>('POST', `/instances/${enc(id)}/actions/${enc(action)}`, { body: params ?? {} }),
  bulk: (req: BulkRequest) => request<BulkResult>('POST', '/instances/bulk', { body: req }),
  command: (id: string) => request<Command>('GET', `/commands/${enc(id)}`),
  workers: () => request<Worker[]>('GET', '/workers'),
  worker: (id: string) => request<Worker>('GET', `/workers/${enc(id)}`),
  enrollWorker: (label?: string) =>
    request<WorkerEnrollment>('POST', '/workers/enroll', { body: label ? { label } : {} }),
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

  takeControl: (id: string) => request<ControlTakeResponse>('POST', `/instances/${enc(id)}/control/take`),
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
  profileContext: (id: string) =>
    request<OperationalContext>('GET', `/instagram/profiles/${enc(id)}/operational-context`),
  connectProfile: (id: string) =>
    request<SessionJobAccepted>('POST', `/instagram/profiles/${enc(id)}/connect`, { timeoutMs: 60_000 }),
  verifyProfile: (id: string) =>
    request<SessionJobAccepted>('POST', `/instagram/profiles/${enc(id)}/verify`, { timeoutMs: 60_000 }),
  logoutProfile: (id: string) =>
    request<SessionJobAccepted>('POST', `/instagram/profiles/${enc(id)}/logout`, { timeoutMs: 60_000 }),
  listPersonas: () => request<Persona[]>('GET', '/personas'),
  createPersona: (body: PersonaInput) => request<Persona>('POST', '/personas', { body }),
  updatePersona: (id: string, body: Partial<PersonaInput>) =>
    request<Persona>('PATCH', `/personas/${enc(id)}`, { body }),
  deletePersona: (id: string) => request<void>('DELETE', `/personas/${enc(id)}`),
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
  startTraining: (instanceId: string, body: { intent: string; lease_id: string; app_id?: string | null }) =>
    request<TrainingSession>('POST', `/instances/${enc(instanceId)}/training`, { body }),
  listTraining: (instanceId?: string) =>
    request<TrainingSession[]>('GET', '/training', { query: { instance_id: instanceId } }),
  getTraining: (id: string) => request<TrainingSession>('GET', `/training/${enc(id)}`),
  stopTraining: (id: string) => request<TrainingSession>('POST', `/training/${enc(id)}/stop`),
  discardTraining: (id: string) => request<TrainingSession>('POST', `/training/${enc(id)}/discard`),
  proposeTraining: (id: string) => request<TrainingSession>('POST', `/training/${enc(id)}/propose`),
  saveTraining: (id: string, body: { proposal?: TrainingProposal | null; profile_ids?: string[]; group_ids?: string[] }) =>
    request<TrainingSaveResult>('POST', `/training/${enc(id)}/save`, { body }),
  appsOverview: (days = 7) => request<AppOverview[]>('GET', '/apps-overview', { query: { days } }),
  appOverview: (appId: string, days = 30) =>
    request<AppDetail>('GET', `/apps/${enc(appId)}/overview`, { query: { days } }),
  listAuthAttempts: (profileId: string, limit = 20) =>
    request<AuthAttempt[]>('GET', `/instagram/profiles/${enc(profileId)}/auth-attempts`, { query: { limit } }),
  listProfileRuns: (profileId: string, limit = 20) =>
    request<RunSummary[]>('GET', `/instagram/profiles/${enc(profileId)}/runs`, { query: { limit } }),
  getPolicy: (profileId: string) =>
    request<ProfilePolicy>('GET', `/instagram/profiles/${enc(profileId)}/policy`),
  setPolicy: (profileId: string, body: ProfilePolicyPatch) =>
    request<ProfilePolicy>('PUT', `/instagram/profiles/${enc(profileId)}/policy`, { body }),
  listPolicyGroups: () => request<PolicyGroup[]>('GET', '/instagram/policy-groups'),
  policyDefaults: () => request<{ limits: Record<string, number> }>('GET', '/instagram/policy-defaults'),
  createPolicyGroup: (body: PolicyGroupCreateRequest) =>
    request<PolicyGroup>('POST', '/instagram/policy-groups', { body }),
  updatePolicyGroup: (id: string, body: PolicyGroupPatchRequest) =>
    request<PolicyGroup>('PUT', `/instagram/policy-groups/${enc(id)}`, { body }),
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
  /** `proxy_id: null` tira o proxy. `instance_ids` ausente = o parque inteiro. `dry_run` = prévia. */
  applyProxy: (body: { proxy_id: string | null; instance_ids?: string[]; dry_run?: boolean }) =>
    request<{ accepted: boolean; dry_run: boolean; devices: DistributeDevice[] }>('POST', '/proxies/apply', { body }),

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
  /** Página do histórico. `instanceId`/`workerId` filtram por ONDE a execução rodou (fotografia do objetivo). */
  listRuns: (limit = 20, offset = 0, instanceId?: string, workerId?: string) =>
    request<RunPage>('GET', '/runs', {
      query: { limit, offset, instance_id: instanceId ?? '', worker_id: workerId ?? '' },
    }),
  getRun: (id: string, signal?: AbortSignal) => request<RunDetail>('GET', `/runs/${enc(id)}`, { signal }),
  runEvents: (id: string, after = 0, limit = 500, signal?: AbortSignal) =>
    request<EventRecord[]>('GET', `/runs/${enc(id)}/events`, { query: { after, limit }, signal }),
  runReport: (id: string) => request<RunReport>('GET', `/runs/${enc(id)}/report`),
  startRun: (id: string) => request<RunSummary>('POST', `/runs/${enc(id)}/start`),
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

/** URL do WebSocket derivada de `location` (nunca fixa a porta 8000: o proxy/servidor resolve). */
export function wsUrl(lastEventId: number): string {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${proto}//${window.location.host}${API_BASE}/ws?last_event_id=${enc(String(lastEventId))}`;
}
