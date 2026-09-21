import type {
  Command,
  CommandAccepted,
  Approval,
  ApprovalBatchResult,
  ApprovalDecisionItem,
  AuthAttempt,
  AiStatus,
  AppConfig,
  AppConfigInput,
  BulkRequest,
  AppRelease,
  ReleaseLifecycleBody,
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
  ProfileCreateRequest,
  ProfilePatchRequest,
  ProfilePolicy,
  ProfilePolicyPatch,
  Recipe,
  RecipeStatusResult,
  RecipeStatusUpdate,
  ResolveRequest,
  RetryFailedResponse,
  RunDetail,
  RunReport,
  RunSummary,
  SessionJobAccepted,
  SocialDraft,
  SocialInteraction,
  Settings,
  Snapshot,
  UsageQuery,
  UsageReport,
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
      return 'O backend não respondeu. Confirme que o servidor está rodando em 127.0.0.1:8000 e tente de novo.';
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
    });
    if (!res.ok) {
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
  health: () => request<Health>('GET', '/health'),
  snapshot: (signal?: AbortSignal) => request<Snapshot>('GET', '/snapshot', { signal, timeoutMs: 15_000 }),
  diagnostics: (refresh: boolean) =>
    request<Diagnostics>('GET', '/diagnostics', { query: { refresh: refresh ? 1 : 0 }, timeoutMs: 180_000 }),
  metrics: () => request<Metrics>('GET', '/metrics'),

  getSettings: () => request<Settings>('GET', '/settings'),
  putSettings: (patch: Partial<Settings>) => request<Settings>('PUT', '/settings', { body: patch }),

  ai: () => request<AiStatus>('GET', '/ai'),

  /** Custo de IA de UMA execução (`{run_id}`) ou dos últimos N dias (`{days}`). */
  usage: (scope: UsageQuery, signal?: AbortSignal) => request<UsageReport>('GET', '/usage', { query: scope, signal }),

  listFlows: (signal?: AbortSignal) => request<Flow[]>('GET', '/flows', { signal }),
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
  commands: (instanceId?: string, limit = 50) =>
    request<Command[]>('GET', '/commands', { query: { ...(instanceId ? { instance_id: instanceId } : {}), limit } }),
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

  listMemory: (profileId: string, limit = 100) =>
    request<MemoryItem[]>('GET', `/instagram/profiles/${enc(profileId)}/memory`, { query: { limit } }),
  addMemory: (profileId: string, body: MemoryInput) =>
    request<MemoryItem>('POST', `/instagram/profiles/${enc(profileId)}/memory`, { body }),
  deleteMemory: (profileId: string, memoryId: string) =>
    request<void>('DELETE', `/instagram/profiles/${enc(profileId)}/memory/${enc(memoryId)}`),
  listInteractions: (profileId: string, limit = 30) =>
    request<SocialInteraction[]>('GET', `/instagram/profiles/${enc(profileId)}/interactions`, { query: { limit } }),
  listAuthAttempts: (profileId: string, limit = 20) =>
    request<AuthAttempt[]>('GET', `/instagram/profiles/${enc(profileId)}/auth-attempts`, { query: { limit } }),
  listProfileRuns: (profileId: string, limit = 20) =>
    request<RunSummary[]>('GET', `/instagram/profiles/${enc(profileId)}/runs`, { query: { limit } }),
  getPolicy: (profileId: string) =>
    request<ProfilePolicy>('GET', `/instagram/profiles/${enc(profileId)}/policy`),
  setPolicy: (profileId: string, body: ProfilePolicyPatch) =>
    request<ProfilePolicy>('PUT', `/instagram/profiles/${enc(profileId)}/policy`, { body }),
  listCapabilities: (pkg = 'com.instagram.android') =>
    request<Capability[]>('GET', '/capabilities', { query: { package: pkg } }),

  listReleases: (pkg?: string) =>
    request<AppRelease[]>('GET', '/releases', { query: { package: pkg ?? '' } }),
  importReleases: (body: { source_reference?: string; expected_package?: string } = {}) =>
    request<{ imported: unknown[] }>('POST', '/releases/import', { body, timeoutMs: 180_000 }),
  approveSignature: (releaseId: string, note?: string) =>
    request<AppRelease>('POST', `/releases/${enc(releaseId)}/approve-signature`, { body: { note: note ?? null } }),
  listAppState: (pkg?: string) =>
    request<DeviceAppState[]>('GET', '/app-state', { query: { package: pkg ?? '' } }),
  /** Canário, promoção, quarentena e rollback. Os dois primeiros respondem na hora; os que mexem no aparelho
   *  são aceitos e o resultado aparece em `/app-state`. */
  releaseLifecycle: (releaseId: string, body: ReleaseLifecycleBody) =>
    request<{ accepted: boolean; release?: AppRelease; devices?: DistributeDevice[] }>(
      'POST', `/releases/${enc(releaseId)}/lifecycle`, { body }),
  /** A loja (Play Store) como fonte do aplicativo. Instalar/atualizar NA loja é sempre um toque do usuário. */
  storeStatus: () => request<StoreStatus>('GET', '/store'),
  storeOpenListing: () => request<{ ok: boolean }>('POST', '/store/open-listing', { body: {} }),
  storeSync: () => request<{ accepted: boolean }>('POST', '/store/sync', { body: {} }),

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
  listRuns: (limit = 20) => request<RunSummary[]>('GET', '/runs', { query: { limit } }),
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
