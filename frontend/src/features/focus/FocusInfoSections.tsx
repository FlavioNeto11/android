/**
 * As seções de LEITURA da coluna lateral do Foco: quem é o aparelho, como ele está, onde roda, o que faz agora,
 * quem mora nele (personas e contas) e com que apps.
 *
 * O que muda por evento (estado, prontidão, tela, internet, automação, versão do app) vem do store, que o
 * WebSocket mantém vivo; o que só a rota `operational-context` sabe (personas, contas, versão promovida) vem dela.
 * Nenhuma camada é deduzida de outra — a mesma regra do cartão de contexto operacional.
 */
import {
  ExternalLink, Link2, PackageCheck, RefreshCw, ServerCrash, Star, TriangleAlert, Wifi, WifiOff, Wrench,
} from 'lucide-react';
import { useEffect, useState, type ReactNode } from 'react';
import { api, profileAvatarUrl } from '../../api/client';
import type {
  Command, Instance, OperationalContext, PersonaOnDevice, ProfileAccount, SessionInfo, Worker,
} from '../../api/types';
import { Avatar } from '../../components/Avatar';
import { Badge } from '../../components/Badge';
import { Banner } from '../../components/Banner';
import { Button } from '../../components/Button';
import { KvList, KvRow } from '../../components/JsonTree';
import { StatusBadge } from '../../components/StatusBadge';
import { formatMb, formatPercent } from '../../lib/format';
import type { LoadError } from '../../lib/loadError';
import {
  ACCOUNT_SESSION_STATUS, APP_INSTALL_STATE, AUTOMATION_STATE, CONNECTIVITY_STATE, INSTANCE_STATE, OBJECTIVE_STATUS,
  READINESS_PHASE, SESSION_STATUS, STEP_STATUS, STREAM_STATUS, metaOf, type StatusMeta, type Tone,
} from '../../lib/status';
import { selectSlotWait, useAppStore } from '../../store/app';
import { chaveDoApp } from '../../store/reducer';
import { toast, toastError } from '../../store/toasts';
import { useUiStore } from '../../store/ui';
import { COMMAND_STATE, rotuloDoVerbo } from '../devices/CommandTrail';
import type { ServerHint } from '../devices/deviceState';
import { PRESENCA, Quando } from '../devices/OperationalContextCard';
import { SESSION_PHASE_LABEL } from '../profiles/sessionGate';
import { VincularForm } from '../profiles/VincularForm';
import styles from './Focus.module.css';
import { FocusSection } from './FocusSection';

/** O que `useOperationalContext` devolve: as seções que dependem do contexto recebem a leitura inteira. */
export interface ContextoLido {
  ctx: OperationalContext | null;
  erro: LoadError | null;
  carregando: boolean;
  carregar: () => Promise<void>;
}

// ---------------------------------------------------------------- Identidade

const TIPO: Record<Instance['kind'], string> = {
  emulator: 'Emulador',
  external: 'Aparelho de outra máquina',
  store: 'Aparelho-loja (Play Store)',
};

const NATUREZA: Record<string, string> = { emulator: 'emulador', physical: 'aparelho físico', container: 'contêiner' };

const ORIGEM: Record<string, string> = {
  config: 'Configuração desta instalação (config.yaml)',
  dynamic: 'Adotado de um servidor, sem config.yaml',
};

export function IdentitySection({ instance, server, appName }: {
  instance: Instance; server: ServerHint | null; appName: string | null;
}) {
  const natureza = instance.device_kind ? NATUREZA[instance.device_kind] ?? instance.device_kind : null;
  return (
    <FocusSection title="Identidade">
      <KvList>
        <KvRow label="Aparelho"><span className="mono">{instance.id}</span></KvRow>
        <KvRow label="Servidor">{server?.name ?? 'Este servidor (central)'}</KvRow>
        <KvRow label="Origem">{instance.origin ? ORIGEM[instance.origin] ?? instance.origin : '—'}</KvRow>
        {/* O AVD que o worker reporta ganha do palpite local: num aparelho remoto o nome daqui é de outra máquina. */}
        <KvRow label="AVD"><span className="mono">{server?.avd_name ?? instance.avd_name}</span></KvRow>
        <KvRow label="Tipo">{TIPO[instance.kind] ?? instance.kind}{natureza ? ` · ${natureza}` : ''}</KvRow>
        <KvRow label="App padrão">{appName ?? 'nenhum associado'}</KvRow>
      </KvList>
    </FocusSection>
  );
}

// ---------------------------------------------------------------- Estado e saúde

/** Pior tom primeiro. Neutro fica abaixo de "sucesso": internet não verificada não rebaixa um aparelho saudável. */
const GRAVIDADE: Record<Tone, number> = { danger: 4, warning: 3, info: 2, accent: 2, success: 1, neutral: 0, muted: 0 };

/**
 * O semáforo da seção: o pior sinal entre as camadas. Serve para decidir se vale abrir a seção, não substitui as
 * linhas — cada uma continua dizendo o seu estado.
 */
export function healthLight(instance: Instance, estado: StatusMeta = metaOf(INSTANCE_STATE, instance.state)): { label: string; tone: Tone } {
  const tons: Tone[] = [
    estado.tone,
    instance.readiness ? metaOf(READINESS_PHASE, instance.readiness.phase).tone : 'neutral',
    instance.stream ? metaOf(STREAM_STATUS, instance.stream.status).tone : 'neutral',
    instance.connectivity ? metaOf(CONNECTIVITY_STATE, instance.connectivity.state).tone : 'neutral',
    metaOf(AUTOMATION_STATE, instance.automation?.state).tone,
    instance.attention ? 'warning' : 'neutral',
    instance.inventory_state === 'divergent' ? 'danger' : 'neutral',
  ];
  const pior = tons.reduce((a, b) => (GRAVIDADE[b] > GRAVIDADE[a] ? b : a), 'neutral' as Tone);
  if (pior === 'danger') return { label: 'Com falha', tone: 'danger' };
  if (pior === 'warning') return { label: 'Pede atenção', tone: 'warning' };
  if (pior === 'info' || pior === 'accent') return { label: 'Em transição', tone: 'info' };
  if (pior === 'success') return { label: 'Saudável', tone: 'success' };
  return { label: estado.label, tone: 'neutral' };
}

function Linha({ meta, detalhe, children }: { meta: StatusMeta; detalhe?: string | null; children?: ReactNode }) {
  return (
    <span className={styles.kvLine}>
      <StatusBadge meta={meta} size="sm" />
      {detalhe ? <span className={styles.kvDetail}>{detalhe}</span> : null}
      {children}
    </span>
  );
}

/**
 * `selo` é o estado que o painel mostra (`devices/selos::seloDoAparelho`): com o servidor sem resposta ele é
 * "Desconhecido" (RF-40), e o `state_detail` do backend, que descreve o estado guardado, fica de fora.
 */
export function HealthSection({ instance, selo, desconhecido = false }: {
  instance: Instance; selo?: StatusMeta; desconhecido?: boolean;
}) {
  const { readiness, stream, connectivity, automation, resources } = instance;
  const estado = selo ?? metaOf(INSTANCE_STATE, instance.state);
  return (
    <FocusSection title="Estado e saúde" badge={healthLight(instance, estado)}>
      <KvList>
        <KvRow label="Aparelho"><Linha meta={estado} detalhe={desconhecido ? null : instance.state_detail} /></KvRow>
        <KvRow label="Prontidão">
          {readiness ? <Linha meta={metaOf(READINESS_PHASE, readiness.phase)} detalhe={readiness.detail} /> : '—'}
        </KvRow>
        <KvRow label="Tela">{stream ? <Linha meta={metaOf(STREAM_STATUS, stream.status)} detalhe={stream.detail} /> : '—'}</KvRow>
        <KvRow label="Internet">
          {connectivity ? (
            <Linha meta={metaOf(CONNECTIVITY_STATE, connectivity.state)} detalhe={connectivity.detail}>
              <span className={styles.kvDetail}>verificada <Quando ts={connectivity.checked_at} /></span>
            </Linha>
          ) : '—'}
        </KvRow>
        <KvRow label="Automação"><Linha meta={metaOf(AUTOMATION_STATE, automation?.state)} detalhe={automation?.detail} /></KvRow>
        <KvRow label="Atenção">{instance.attention ?? 'nada pendente'}</KvRow>
        <KvRow label="Inventário">
          {instance.inventory_state === 'divergent' ? (
            <span className={styles.kvLine}>
              <Badge size="sm" tone="danger">Divergente</Badge>
              <span className={styles.kvDetail}>
                {instance.inventory_detail ?? 'as fontes discordam sobre qual aparelho está por trás deste id'}
                {' '}— verbos destrutivos ficam recusados até resolver.
              </span>
            </span>
          ) : instance.inventory_state ?? 'sem divergência'}
        </KvRow>
        <KvRow label="Recursos">
          {resources ? `${formatMb(resources.rss_mb)} · CPU ${formatPercent(resources.cpu_percent)}` : '—'}
        </KvRow>
      </KvList>
    </FocusSection>
  );
}

// ---------------------------------------------------------------- Servidor

const ESTADO_WORKER: Record<Worker['state'], StatusMeta> = {
  online: { label: 'Online', tone: 'success', icon: Wifi },
  degraded: { label: 'Degradado', tone: 'warning', icon: TriangleAlert },
  maintenance: { label: 'Em manutenção', tone: 'info', icon: Wrench },
  offline: { label: 'Offline', tone: 'danger', icon: WifiOff },
};

export function ServerSection({ instance, server }: { instance: Instance; server: ServerHint | null }) {
  const worker = useAppStore((s) => (instance.worker_id ? s.workers[instance.worker_id] : undefined));
  const setView = useUiStore((s) => s.setView);
  const conectado = server ? server.connected : worker ? worker.connected : true;
  const badge = !server ? { label: 'central', tone: 'neutral' as Tone }
    : !server.enrolled ? { label: 'não inscrito', tone: 'danger' as Tone }
    : conectado ? { label: 'conectado', tone: 'success' as Tone } : { label: 'desconectado', tone: 'danger' as Tone };
  return (
    <FocusSection title="Servidor" badge={badge}>
      {server && !server.enrolled ? (
        <Banner tone="danger" icon={ServerCrash} compact title={`${server.name} não está inscrito`}>
          O aparelho aponta para um servidor que não existe mais: não há quem o ligue, desligue ou leia.
        </Banner>
      ) : null}
      <KvList>
        <KvRow label="Nome">
          {worker?.name ?? server?.name ?? 'Este servidor'}{!server ? ' (central)' : ''}
        </KvRow>
        {worker || server ? (
          <KvRow label="Conexão">
            <Badge size="sm" tone={conectado ? 'success' : 'danger'}>{conectado ? 'conectado' : 'sem canal agora'}</Badge>
          </KvRow>
        ) : null}
        {worker ? (
          <KvRow label="Estado"><Linha meta={metaOf(ESTADO_WORKER, worker.state)} detalhe={worker.state_detail} /></KvRow>
        ) : null}
        {server?.transport ? (
          <KvRow label="Túnel">{server.transport === 'up' ? 'no ar' : 'fora — o ADB daqui não alcança a outra máquina'}</KvRow>
        ) : null}
        {/* Só para aparelho de outra máquina: do central, "processo no servidor" repetiria o estado do aparelho. */}
        {server ? (
          <KvRow label="Processo no servidor">
            {server.process ?? 'não reportado'}{server.detail ? ` — ${server.detail}` : ''}
          </KvRow>
        ) : null}
      </KvList>
      <div className={styles.sectionActions}>
        <Button size="sm" variant="ghost" icon={ExternalLink} onClick={() => setView('infraestrutura')}>
          Ver em Infraestrutura
        </Button>
      </div>
    </FocusSection>
  );
}

// ---------------------------------------------------------------- Automação e tarefa

export function TaskSection({ instance, openCmd }: { instance: Instance; openCmd: Command | undefined }) {
  const { current } = instance;
  const slotWait = useAppStore((s) => selectSlotWait(s, instance.id));
  const esperaVaga = !!slotWait && instance.state !== 'online' && instance.state !== 'booting';
  const badge = current?.run_id ? { label: 'em execução', tone: 'accent' as Tone }
    : openCmd ? { label: 'comando em voo', tone: 'info' as Tone }
    : esperaVaga ? { label: 'aguardando vaga', tone: 'warning' as Tone } : null;
  return (
    <FocusSection title="Automação e tarefa" badge={badge}>
      <KvList>
        <KvRow label="Execução">{current?.run_id ? <span className="mono">{current.run_id}</span> : 'nenhuma'}</KvRow>
        {current?.objective_status ? (
          <KvRow label="Objetivo"><StatusBadge meta={metaOf(OBJECTIVE_STATUS, current.objective_status)} size="sm" /></KvRow>
        ) : null}
        {current?.step_title ? (
          <KvRow label="Etapa">
            <span className={styles.kvLine}>
              <span>{current.step_title}</span>
              {current.step_status ? <StatusBadge meta={metaOf(STEP_STATUS, current.step_status)} size="sm" /> : null}
              <span className={styles.kvDetail}>{current.steps_done}/{current.steps_total} etapas</span>
            </span>
          </KvRow>
        ) : current?.run_id ? <KvRow label="Etapa">aguardando a próxima etapa…</KvRow> : null}
        {esperaVaga ? <KvRow label="Vaga">{slotWait}</KvRow> : null}
        <KvRow label="Comando em voo">
          {openCmd ? (
            <span className={styles.kvLine}>
              <StatusBadge meta={metaOf(COMMAND_STATE, openCmd.state)} size="sm" />
              <span>{rotuloDoVerbo(openCmd.verb)}</span>
            </span>
          ) : 'nenhum'}
        </KvRow>
      </KvList>
    </FocusSection>
  );
}

// ---------------------------------------------------------------- Personas e contas

/** O @ só existe quando há usuário: persona sem conta (onda D) chega com `username` vazio ou nulo. */
function arroba(nome: string | null | undefined): string | null {
  const limpo = (nome ?? '').trim().replace(/^@/, '');
  return limpo ? `@${limpo}` : null;
}

type PerfilNoContexto = OperationalContext['profiles'][number];

function nomeDaPersona(p: PerfilNoContexto): string {
  return p.display_name || p.persona_name || p.username || p.profile_id;
}

function SemContexto({ contexto }: { contexto: ContextoLido }) {
  return <p className={styles.groupHint}>{contexto.carregando ? 'Lendo o contexto do aparelho…' : 'Sem leitura do contexto do aparelho.'}</p>;
}

/** Uma persona na lista do aparelho, venha da rota N:N ou do contexto operacional (backend anterior). */
interface PersonaAqui {
  chave: string;
  profileId: string;
  nome: string;
  sub: string;
  session: SessionInfo | null;
  fase: { label: string; tone: Tone } | null;
  /** Este aparelho é o PRINCIPAL da persona (só a rota N:N sabe). */
  principal: boolean;
  /** Há foto (`has_avatar`); sem ela, iniciais e nenhuma requisição (29.26). */
  temFoto?: boolean;
}

/**
 * As N personas deste aparelho (`GET /instances/{id}/personas`, v0.29): uma linha por vínculo, com o app dele e a
 * sessão da conta AQUI. `null` enquanto lê — e também quando a rota falha: aí a seção cai no contexto operacional,
 * que é o que um backend anterior ao N:N sabe dizer.
 */
function usePersonasDoAparelho(instanceId: string, chave: unknown): { lista: PersonaOnDevice[] | null; reler: () => void } {
  const [lista, setLista] = useState<PersonaOnDevice[] | null>(null);
  const [versao, setVersao] = useState(0);
  useEffect(() => {
    let vivo = true;
    api.instancePersonas(instanceId)
      .then((r) => { if (vivo) setLista(r); })
      .catch(() => { if (vivo) setLista(null); });
    return () => { vivo = false; };
  }, [instanceId, chave, versao]);
  return { lista, reler: () => setVersao((v) => v + 1) };
}

export function PersonasSection({ contexto, instanceId }: { contexto: ContextoLido; instanceId: string }) {
  const openPersona = useUiStore((s) => s.openPersona);
  const apps = useAppStore((s) => s.apps);
  const { ctx, erro, carregando, carregar } = contexto;
  // Relê junto com o contexto (ele relê quando o estado do aparelho muda): as duas leituras contam a mesma história.
  const { lista, reler } = usePersonasDoAparelho(instanceId, ctx);
  const [vinculando, setVinculando] = useState(false);
  const perfis = ctx?.profiles ?? [];
  const faseDe = (id: string) => {
    const acoes = perfis.find((p) => p.profile_id === id)?.session_actions;
    return acoes ? SESSION_PHASE_LABEL[acoes.phase] : null;
  };
  const itens: PersonaAqui[] | null = lista
    ? lista.map((p) => {
      const app = p.app_id ? apps.find((a) => a.id === p.app_id)?.name ?? p.app_id : null;
      const nome = p.name || p.display_name || arroba(p.username) || p.profile_id;
      return {
        chave: `${p.profile_id}:${p.app_id ?? ''}`, profileId: p.profile_id, nome, principal: p.is_primary, temFoto: p.has_avatar,
        sub: [arroba(p.username), app ? `conta do ${app}` : 'sem app (os apps sem conta gerenciada)'].filter(Boolean).join(' · '),
        session: p.session, fase: faseDe(p.profile_id),
      };
    })
    : ctx ? perfis.map((p) => {
      const nome = nomeDaPersona(p);
      return {
        chave: p.profile_id, profileId: p.profile_id, nome, principal: false, temFoto: p.has_avatar, session: p.session,
        fase: p.session_actions ? SESSION_PHASE_LABEL[p.session_actions.phase] : null,
        sub: [arroba(p.username), p.persona_name && p.persona_name !== nome ? `persona ${p.persona_name}` : null]
          .filter(Boolean).join(' · ') || 'sem conta vinculada',
      };
    })
    : null;
  return (
    <FocusSection title="Personas neste aparelho" badge={itens ? { label: String(itens.length), tone: 'neutral' } : null}>
      {erro ? (
        // O "tentar de novo" fica aqui porque é a primeira seção que depende do contexto; as de baixo só avisam.
        <Banner tone="danger" icon={ServerCrash} compact role="alert" title="Não foi possível ler o contexto">
          {erro.message} {erro.hint}{' '}
          <Button size="sm" variant="ghost" icon={RefreshCw} loading={carregando} onClick={() => void carregar()}>Reler</Button>
        </Banner>
      ) : null}
      {!itens ? <SemContexto contexto={contexto} />
        : itens.length === 0 ? <p className={styles.groupHint}>Nenhuma persona vinculada a este aparelho.</p>
        : (
          <ul className={styles.personaList}>
            {itens.map((p) => (
              <li key={p.chave} className={styles.personaItem}>
                <Avatar src={profileAvatarUrl(p.profileId, p.temFoto)} name={p.nome} size={36} />
                <div className={styles.personaMain}>
                  <p className={styles.personaName}>
                    {p.nome}{' '}
                    {p.principal ? <Badge size="sm" tone="info" icon={Star} title="Este é o aparelho principal desta persona">Principal</Badge> : null}
                  </p>
                  <p className={styles.personaSub}>{p.sub}</p>
                  <span className={styles.kvLine}>
                    {p.session ? <StatusBadge meta={metaOf(SESSION_STATUS, p.session.status)} size="sm" srPrefix="Sessão aqui" />
                      : <span className={styles.kvDetail}>sem conta que sirva a este vínculo</span>}
                    {p.fase ? <Badge size="sm" tone={p.fase.tone}>{p.fase.label}</Badge> : null}
                    {p.session?.stale ? <Badge size="sm" tone="warning">precisa reler</Badge> : null}
                    {p.session ? <span className={styles.kvDetail}>verificada <Quando ts={p.session.verified_at} /></span> : null}
                  </span>
                  {p.session?.detail ? <p className={styles.personaSub}>{p.session.detail}</p> : null}
                </div>
                <Button size="sm" variant="outline" onClick={() => openPersona(p.profileId)}>Abrir persona</Button>
              </li>
            ))}
          </ul>
        )}
      {/* A outra direção do vínculo: daqui escolhe-se a PERSONA (a mesma rota da guia Aparelhos da persona). */}
      {vinculando ? (
        <VincularForm instanceId={instanceId}
                      onVinculado={async () => { setVinculando(false); reler(); await carregar(); }}
                      onCancelar={() => setVinculando(false)} />
      ) : (
        <div className={styles.sectionActions}>
          <Button size="sm" variant="ghost" icon={Link2} onClick={() => setVinculando(true)}>Vincular persona</Button>
        </div>
      )}
    </FocusSection>
  );
}

function sessaoDaConta(a: ProfileAccount): StatusMeta {
  return a.session ? metaOf(SESSION_STATUS, a.session.status) : metaOf(ACCOUNT_SESSION_STATUS, a.session_status);
}

function identificadorDaConta(a: ProfileAccount): string {
  const handle = arroba(a.handle);
  if (handle && a.host) return `${handle} em ${a.host}`;
  return handle ?? a.host ?? 'sem identificador';
}

export function AccountsSection({ contexto, instance }: { contexto: ContextoLido; instance: Instance }) {
  const { ctx } = contexto;
  const perfis = ctx?.profiles ?? [];
  const total = perfis.reduce((n, p) => n + (p.accounts?.length ?? 0), 0);
  return (
    <FocusSection title="Contas" badge={ctx ? { label: String(total), tone: 'neutral' } : null}>
      {!ctx ? <SemContexto contexto={contexto} />
        : perfis.length === 0 ? <p className={styles.groupHint}>Sem persona vinculada, não há conta neste aparelho.</p>
        : perfis.map((p) => (
          <div key={p.profile_id} className={styles.accountGroup}>
            {perfis.length > 1 ? <p className={styles.accountOwner}>{nomeDaPersona(p)}</p> : null}
            {!p.accounts ? (
              // Backend anterior ao v0.28: só a conta do Instagram do perfil, sem a lista.
              <p className={styles.groupHint}>
                {arroba(p.username) ?? 'Conta da persona'} · senha {p.credential_configured ? 'guardada' : 'não guardada'}
                {' '}— o servidor não informou as contas desta persona.
              </p>
            ) : p.accounts.length === 0 ? (
              <p className={styles.groupHint}>{nomeDaPersona(p)} ainda não tem conta cadastrada.</p>
            ) : (
              <ul className={styles.accountList}>
                {p.accounts.map((a) => {
                  // A senha nunca chega ao painel: só se ela está guardada e se há consentimento para digitá-la.
                  const consentiu = !!(a.credential?.consent_at ?? a.consent_at);
                  const guardada = a.credential?.configured ?? a.credential_configured;
                  return (
                    <li key={a.id} className={styles.accountItem}>
                      <p className={styles.accountHead}>
                        <strong>{a.app_name ?? a.app_id}</strong>
                        <span className={styles.kvDetail}>{identificadorDaConta(a)}</span>
                      </p>
                      <span className={styles.kvLine}>
                        <StatusBadge meta={sessaoDaConta(a)} size="sm" srPrefix="Sessão neste aparelho" />
                        {a.session?.stale ? <Badge size="sm" tone="warning">precisa reler</Badge> : null}
                        {a.status === 'disabled' ? <Badge size="sm" tone="muted">desativada</Badge> : null}
                      </span>
                      <p className={styles.personaSub}>
                        Senha {guardada ? 'guardada' : 'não guardada'} · consentimento: {consentiu ? 'sim' : 'não'}
                      </p>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        ))}
      {/* O rótulo configurado e a última evidência observada no app eram a antiga linha "App · Conta · observado"
          das ações rápidas: continuam aqui, junto das contas que eles tentam descrever. */}
      <p className={styles.groupHint}>
        Rótulo configurado no aparelho: {instance.account_label ?? '—'}
        {instance.account_evidence ? ` · observado: ${instance.account_evidence}` : ''}
      </p>
    </FocusSection>
  );
}

// ---------------------------------------------------------------- Apps

/** Relê do aparelho o que está instalado (`POST /app/verify`, o mesmo das telas de Loja e Releases). */
export async function verificarApp(instanceId: string, pkg: string, nome: string): Promise<void> {
  try {
    await api.verifyApp(instanceId, pkg);
    toast({ tone: 'info', title: `Relendo ${nome} em ${instanceId}`, key: `verify-${instanceId}-${pkg}`,
            hint: 'Pedido aceito não é versão confirmada: o resultado aparece em Apps quando o aparelho responder.' });
  } catch (e) {
    toastError('Não foi possível reler o aparelho', e);
  }
}

export function AppsSection({ contexto, instance, verifyReason }: {
  contexto: ContextoLido; instance: Instance; verifyReason: string | null;
}) {
  const appState = useAppStore((s) => s.appState);
  const { ctx } = contexto;
  const apps = ctx?.apps ?? [];
  let divergentes = 0;
  const linhas = apps.map((a) => {
    // O estado ao vivo (evento `app.state`) ganha da foto do contexto, que só muda quando alguém relê.
    const vivo = appState[chaveDoApp(instance.id, a.package)];
    const versao = vivo?.observed_version_name ?? a.installed_version_name;
    const codigo = vivo?.observed_version_code ?? a.installed_version_code;
    const diverge = !!versao && !!a.promoted_version_name
      && (codigo != null && a.promoted_version_code != null ? codigo !== a.promoted_version_code : versao !== a.promoted_version_name);
    if (diverge) divergentes += 1;
    return { a, vivo, versao, diverge };
  });
  const badge = !ctx ? null : divergentes ? { label: `${divergentes} divergente(s)`, tone: 'warning' as Tone }
    : { label: String(apps.length), tone: 'neutral' as Tone };
  return (
    <FocusSection title="Apps" badge={badge}>
      {!ctx ? <SemContexto contexto={contexto} />
        : apps.length === 0 ? <p className={styles.groupHint}>Nenhum app do catálogo foi visto neste aparelho.</p>
        : (
          <ul className={styles.appList}>
            {linhas.map(({ a, vivo, versao, diverge }) => (
              <li key={a.app_id} className={styles.appItem}>
                <div className={styles.appMain}>
                  <p className={styles.accountHead}>
                    <strong>{a.name}</strong>
                    <StatusBadge meta={vivo ? metaOf(APP_INSTALL_STATE, vivo.state) : PRESENCA[a.presence]} size="sm" />
                    {diverge ? <Badge size="sm" tone="warning">diferente da promovida</Badge> : null}
                  </p>
                  <p className={styles.personaSub}>
                    instalada {versao ?? '—'} · promovida {a.promoted_version_name ?? 'nenhuma'}
                    {' '}· verificado <Quando ts={vivo?.verified_at ?? a.verified_at} />
                  </p>
                  {vivo?.detail ?? a.detail ? <p className={styles.personaSub}>{vivo?.detail ?? a.detail}</p> : null}
                </div>
                <Button size="sm" variant="ghost" icon={PackageCheck} disabledReason={verifyReason}
                        onClick={() => void verificarApp(instance.id, a.package, a.name)}>
                  Verificar
                </Button>
              </li>
            ))}
          </ul>
        )}
    </FocusSection>
  );
}
