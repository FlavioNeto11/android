import { Hand, KeyRound, PlugZap, Plus, ScanEye, Server, ShieldAlert, ShieldCheck, Smartphone, Trash2, UserRound } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, profileAvatarUrl } from '../../api/client';
import type { Instance, InstagramProfile, Persona, PolicyGroup, ProfileCreateRequest, Worker } from '../../api/types';
import { Avatar } from '../../components/Avatar';
import { Badge } from '../../components/Badge';
import { Button } from '../../components/Button';
import { Card, CardBody, CardHeader } from '../../components/Card';
import { confirm } from '../../components/Confirm';
import { Dialog } from '../../components/Dialog';
import { EmptyState } from '../../components/EmptyState';
import { Field, Select, TextInput } from '../../components/Field';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { StatusBadge } from '../../components/StatusBadge';
import { serverHintOf } from '../devices/deviceState';
import { ServerBadge } from '../devices/ServerBadge';
import { toastError, toast } from '../../store/toasts';
import { conteudoAoTopo } from '../../lib/scroll';
import { formatAgoCoarse, useNow } from '../../lib/time';
import { PROFILE_STATUS, SESSION_STATUS, metaOf } from '../../lib/status';
import { selectTaskOrder, useAppStore } from '../../store/app';
import { useControlStore } from '../../store/control';
import { useUiStore } from '../../store/ui';
import { PolicyGroupsSection } from './PolicyGroups';
import { ProfileDetail } from './ProfileDetail';
import styles from './Profiles.module.css';

/** Estados de sessão que só uma pessoa resolve — mesmo conjunto do backend (achado #106). */
const PRECISA_DE_PESSOA = new Set(['auth_challenge', 'wrong_account']);

const VAZIO: ProfileCreateRequest = {
  username: '', first_name: '', last_name: '', birth_date: '', email: '',
  instance_id: '', persona_id: '', policy_group_id: '', password: '',
};

/** A senha é write-only: ela sai deste formulário para o backend e nunca volta em resposta alguma. */
export function ProfilesPage() {
  const hydrated = useAppStore((s) => s.hydrated);
  const [aberto, setAberto] = useState<string | null>(null);
  const hydrateCount = useAppStore((s) => s.hydrateCount);
  // Achado #106: `session.needs_person` não traz o perfil inteiro (só existe por REST) — a batida basta para
  // saber que a fila "Aguardando intervenção" pode ter mudado e recarregar.
  const needsPersonEpoch = useAppStore((s) => s.needsPersonEpoch);
  const instancesMap = useAppStore((s) => s.instances);
  const liveWorkers = useAppStore((s) => s.workers);
  const fullOrder = useAppStore((s) => s.instanceOrder);
  // Perfil só se vincula a aparelho de TAREFA: a loja (Play Store) não recebe perfil — o backend recusaria.
  const instances = useMemo(() => selectTaskOrder({ instances: instancesMap, instanceOrder: fullOrder }), [instancesMap, fullOrder]);
  const [profiles, setProfiles] = useState<InstagramProfile[] | null>(null);
  const [personas, setPersonas] = useState<Persona[]>([]);
  const [grupos, setGrupos] = useState<PolicyGroup[]>([]);
  const [workers, setWorkers] = useState<Worker[]>([]);
  const [editing, setEditing] = useState(false);
  const token = useRef(0);

  /** Aparelho → nome do servidor que o hospeda. É o agrupamento do select de criação. */
  const servidores = useMemo(() => {
    const nomes = new Map(workers.map((w) => [w.id, w.local ? `${w.name} (este servidor)` : w.name]));
    const mapa: Record<string, string> = {};
    for (const iid of instances) {
      const wid = instancesMap[iid]?.worker_id ?? null;
      mapa[iid] = (wid ? nomes.get(wid) : undefined) ?? (wid ?? 'este servidor');
    }
    return mapa;
  }, [workers, instances, instancesMap]);

  const load = useCallback(async () => {
    const mine = ++token.current;
    const [p, per, wk, grp] = await Promise.allSettled([api.listProfiles(), api.listPersonas(), api.workers(),
                                                        api.listPolicyGroups()]);
    if (mine !== token.current) return;
    if (grp.status === 'fulfilled') setGrupos(grp.value);
    if (p.status === 'fulfilled') setProfiles(p.value);
    else {
      setProfiles([]);
      toastError('Não foi possível carregar os perfis', p.reason);
    }
    if (per.status === 'fulfilled') setPersonas(per.value);
    // Os servidores são só rótulo aqui: sem eles o select de criação dizia "android-12" sem dizer em que
    // máquina aquele aparelho está — e é a máquina que decide onde os dados do perfil vão viver.
    if (wk.status === 'fulfilled') setWorkers(wk.value);
  }, []);

  // Recarrega a cada novo snapshot (reconexão) e a cada mudança na fila "Aguardando intervenção" — perfis não
  // vêm no snapshot, e o evento dedicado só carrega o bastante para saber que algo mudou (achado #106).
  useEffect(() => {
    void load();
  }, [load, hydrateCount, needsPersonEpoch]);

  // Abrir um perfil e voltar troca o conteúdo sem trocar de seção: sem voltar ao topo, a lista reaparecia rolada.
  useEffect(() => {
    conteudoAoTopo();
  }, [aberto]);

  const emFoco = aberto ? (profiles ?? []).find((p) => p.id === aberto) : undefined;
  if (emFoco) {
    return <ProfileDetail profile={emFoco} onBack={() => setAberto(null)} onChanged={load} />;
  }

  if (!hydrated || profiles === null) {
    return (
      <LoadingRegion label="Carregando perfis…">
        <Skeleton height={140} />
      </LoadingRegion>
    );
  }

  return (
    <div className={styles.page}>
      <div className={styles.header}>
        <div>
          <h2 className={styles.title}>Perfis do Instagram</h2>
          <p className={styles.lead}>
            Cada perfil tem credencial própria, guardada cifrada, e um aparelho vinculado. A senha é digitada aqui e
            nunca volta: o painel só mostra que existe.
          </p>
        </div>
        <Button icon={Plus} onClick={() => setEditing(true)}>Novo perfil</Button>
      </div>

      <InterventionQueue profiles={profiles} instances={instancesMap} workers={liveWorkers} />

      <PolicyGroupsSection grupos={grupos} profiles={profiles} onChanged={load} />

      {profiles.length === 0 ? (
        <EmptyState
          icon={UserRound}
          title="Nenhum perfil cadastrado"
          hint="Cadastre um perfil com o usuário do Instagram, a senha e o aparelho que vai usá-lo."
          actions={<Button icon={Plus} onClick={() => setEditing(true)}>Novo perfil</Button>}
        >
          Nenhum perfil foi cadastrado ainda.
        </EmptyState>
      ) : (
        <div className={styles.grid}>
          {profiles.map((p) => (
            <ProfileCard key={p.id} profile={p} onChanged={load} onOpen={() => setAberto(p.id)} />
          ))}
        </div>
      )}

      {editing ? (
        <ProfileEditor
          personas={personas}
          grupos={grupos}
          instances={instances}
          servidores={servidores}
          usados={profiles.map((p) => p.instance_id).filter(Boolean) as string[]}
          onClose={() => setEditing(false)}
          onSaved={async () => {
            setEditing(false);
            await load();
          }}
        />
      ) : null}
    </div>
  );
}

/**
 * Fila "Aguardando intervenção" (achado #106): perfil + aparelho + motivo + idade, com um botão que assume o
 * controle e abre a tela certa do aparelho — local ou remoto, pelo mesmo painel de Foco de sempre. Sem isto, a
 * pessoa precisava descobrir sozinha qual perfil estava preso, achar o aparelho e lembrar de assumir o controle.
 */
function InterventionQueue({ profiles, instances, workers }: {
  profiles: InstagramProfile[];
  instances: Record<string, Instance>;
  workers: Readonly<Record<string, Worker>>;
}) {
  const now = useNow();
  const take = useControlStore((s) => s.take);
  const controlBusy = useControlStore((s) => s.busy);
  const openFocus = useUiStore((s) => s.openFocus);

  const itens = useMemo(
    () => profiles
      .filter((p) => PRECISA_DE_PESSOA.has(p.session.status))
      // Mais velho primeiro: quem está esperando há mais tempo aparece no topo.
      .sort((a, b) => (a.session.verified_at ?? '').localeCompare(b.session.verified_at ?? '')),
    [profiles],
  );

  if (itens.length === 0) return null;

  async function assumirEAbrir(instanceId: string) {
    await take(instanceId);
    openFocus(instanceId);
  }

  return (
    <Card>
      <CardHeader
        title={
          <span className={styles.filaTitulo}>
            <ShieldAlert size={18} aria-hidden /> Aguardando intervenção
            <Badge tone="warning">{itens.length}</Badge>
          </span>
        }
        subtitle="Login, desafio de segurança ou conta errada — só uma pessoa resolve. Assuma o controle e resolva na tela do aparelho; devolver o controle relê a tela sozinho."
      />
      <CardBody>
        <ul className={styles.filaLista}>
          {itens.map((p) => {
            const inst = p.instance_id ? instances[p.instance_id] : undefined;
            const server = inst ? serverHintOf(inst, workers) : null;
            const sess = metaOf(SESSION_STATUS, p.session.status);
            return (
              <li key={p.id} className={styles.filaItem}>
                <Avatar src={profileAvatarUrl(p.id)} name={p.display_name || p.username} size={32} />
                <div className={styles.filaInfo}>
                  <p className={styles.filaPerfil}>
                    <span className={styles.filaUsuario}>@{p.username}</span>
                    <StatusBadge meta={sess} />
                  </p>
                  <p className={styles.filaDetalhe}>
                    <Smartphone size={13} aria-hidden />
                    {p.instance_id ?? <span className={styles.muted}>sem aparelho vinculado</span>}
                    {server ? <ServerBadge server={server} size="sm" estatico /> : null}
                    <span className={styles.muted}>· {formatAgoCoarse(p.session.verified_at, now)}</span>
                  </p>
                  {p.session.detail ? <p className={styles.filaMotivo}>{p.session.detail}</p> : null}
                </div>
                <Button
                  size="sm"
                  variant="primary"
                  icon={Hand}
                  loading={!!p.instance_id && !!controlBusy[p.instance_id]}
                  disabledReason={!p.instance_id ? 'Sem aparelho vinculado a este perfil.' : null}
                  onClick={() => p.instance_id && void assumirEAbrir(p.instance_id)}
                >
                  Assumir controle
                </Button>
              </li>
            );
          })}
        </ul>
      </CardBody>
    </Card>
  );
}

function ProfileCard({ profile, onChanged, onOpen }: {
  profile: InstagramProfile;
  onChanged: () => Promise<void>;
  onOpen: () => void;
}) {
  const sess = metaOf(SESSION_STATUS, profile.session.status);
  const [busy, setBusy] = useState(false);
  const [conectando, setConectando] = useState(false);
  const pronto = profile.session.status === 'session_ready';

  // 202: o trabalho roda no aparelho. Recarrega algumas vezes até o estado parar de mudar.
  async function acompanhar() {
    for (const espera of [2000, 3000, 5000, 8000, 12000]) {
      await new Promise((r) => setTimeout(r, espera));
      await onChanged();
    }
  }

  async function conectar(verificar = false) {
    setConectando(true);
    try {
      await (verificar ? api.verifyProfile(profile.id) : api.connectProfile(profile.id));
      toast({
        tone: 'info',
        title: verificar ? `Verificando @${profile.username}…` : `Conectando @${profile.username}…`,
        message: 'O aparelho está sendo usado agora; o estado da sessão aparece aqui em instantes.',
      });
      await acompanhar();
    } catch (e) {
      toastError(verificar ? 'Não foi possível verificar a conta' : 'Não foi possível conectar', e);
    } finally {
      setConectando(false);
    }
  }

  async function remover() {
    // `confirm` devolve um OBJETO, que é sempre verdadeiro: testar o objeto faria "Voltar" apagar o perfil e a
    // credencial do mesmo jeito. Quem decide é `confirmed`.
    const { confirmed } = await confirm({
      title: `Remover @${profile.username}?`,
      body: 'A credencial guardada no cofre também é apagada. Persona, memória e histórico deste perfil vão junto.',
      confirmLabel: 'Remover',
      danger: true,
    });
    if (!confirmed) return;
    setBusy(true);
    try {
      await api.deleteProfile(profile.id);
      toast({ tone: 'success', title: `@${profile.username} removido` });
      await onChanged();
    } catch (e) {
      toastError('Não foi possível remover o perfil', e);
    } finally {
      setBusy(false);
    }
  }

  async function mudarStatus(status: 'active' | 'blocked') {
    setBusy(true);
    try {
      await api.patchProfile(profile.id, { status });
      toast({
        tone: 'success',
        title: status === 'blocked' ? `@${profile.username} marcado como bloqueado` : `@${profile.username} reativado`,
        message: status === 'blocked' ? 'Nenhuma tarefa será despachada para este perfil.' : 'O perfil volta a receber tarefas.',
      });
      await onChanged();
    } catch (e) {
      toastError('Não foi possível mudar a situação do perfil', e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader
        title={
          <span className={styles.identidade}>
            <Avatar src={profileAvatarUrl(profile.id)} name={profile.display_name || profile.username} size={40} />
            <span className={styles.identidadeNome}>@{profile.username}</span>
          </span>
        }
        subtitle={profile.display_name ?? undefined}
        actions={
          <div className={styles.actions}>
            <Button size="sm" variant="ghost" onClick={onOpen}>Abrir</Button>
            {/* Conta bloqueada pela plataforma: registrar aqui é o que tira o perfil do despacho. Reativar é
                decisão de pessoa, depois de a conta voltar de verdade. */}
            <Button size="sm" variant="ghost" loading={busy}
                    onClick={() => void mudarStatus(profile.status === 'active' ? 'blocked' : 'active')}>
              {profile.status === 'active' ? 'Marcar bloqueada' : 'Reativar'}
            </Button>
            <Button size="sm" variant="dangerGhost" icon={Trash2} iconOnly label="Remover perfil"
                    loading={busy} onClick={remover} />
          </div>
        }
      />
      <CardBody>
        <dl className={styles.rows}>
          <div className={styles.row}>
            <dt><Smartphone size={14} aria-hidden /> Aparelho</dt>
            <dd>{profile.instance_id ?? <span className={styles.muted}>não vinculado</span>}</dd>
          </div>
          {/* Onde os DADOS vivem (E9). "Perfil armazenado num servidor não está automaticamente disponível em
              outro": sem esta linha, um perfil cujo servidor está fora aparecia igual aos demais. */}
          {profile.locality ? (
            <div className={styles.row}>
              <dt><Server size={14} aria-hidden /> Servidor</dt>
              <dd>
                {profile.locality.worker_name ?? profile.locality.worker_id ?? 'este servidor'}
                {profile.locality.moved ? <> <Badge tone="warning">mudou de servidor</Badge></> : null}
                {!profile.locality.available ? <> <Badge tone="warning">indisponível</Badge></> : null}
                {!profile.locality.known ? <> <Badge tone="neutral">localidade não registrada</Badge></> : null}
              </dd>
            </div>
          ) : null}
          <div className={styles.row}>
            <dt><KeyRound size={14} aria-hidden /> Senha</dt>
            <dd>
              {profile.credential.configured ? (
                <span className={styles.mask} title="A senha nunca é devolvida pela API">••••••••••••</span>
              ) : (
                <span className={styles.muted}>não configurada</span>
              )}
            </dd>
          </div>
          <div className={styles.row}>
            <dt>Sessão</dt>
            <dd><StatusBadge meta={sess} /></dd>
          </div>
          {profile.session.observed_username ? (
            <div className={styles.row}>
              <dt>Conta observada</dt>
              <dd>@{profile.session.observed_username}</dd>
            </div>
          ) : null}
          {profile.persona_name ? (
            <div className={styles.row}>
              <dt>Persona</dt>
              <dd><Badge>{profile.persona_name}</Badge></dd>
            </div>
          ) : null}
          <div className={styles.row}>
            <dt><ShieldCheck size={14} aria-hidden /> Grupo de acesso</dt>
            <dd>{profile.policy_group_name
              ? <Badge tone="info">{profile.policy_group_name}</Badge>
              : <span className={styles.muted}>nenhum — padrão do catálogo</span>}</dd>
          </div>
          {profile.status !== 'active' ? (
            <div className={styles.row}>
              <dt>Situação</dt>
              <dd><StatusBadge meta={metaOf(PROFILE_STATUS, profile.status)} /></dd>
            </div>
          ) : null}
        </dl>
        {profile.locality?.detail && (profile.locality.moved || !profile.locality.available) ? (
          <p className={styles.detail}>{profile.locality.detail}</p>
        ) : null}
        {profile.session.detail ? <p className={styles.detail}>{profile.session.detail}</p> : null}
        <div className={styles.actions}>
          <Button
            size="sm"
            variant={pronto ? 'secondary' : 'primary'}
            icon={PlugZap}
            loading={conectando}
            disabledReason={!profile.credential.configured
              ? 'Abra o perfil e guarde a senha na aba Autenticação antes de conectar.'
              : !profile.instance_id
                ? 'Vincule um aparelho a este perfil antes de conectar.'
                : null}
            onClick={() => void conectar(false)}
          >
            {pronto ? 'Reconectar' : 'Conectar'}
          </Button>
          <Button size="sm" variant="ghost" icon={ScanEye} loading={conectando}
                  disabledReason={profile.instance_id ? null : 'Vincule um aparelho a este perfil.'}
                  onClick={() => void conectar(true)}>
            Verificar conta
          </Button>
        </div>
      </CardBody>
    </Card>
  );
}

function ProfileEditor({ personas, grupos, instances, servidores, usados, onClose, onSaved }: {
  personas: Persona[];
  grupos: PolicyGroup[];
  instances: string[];
  /** Aparelho → servidor que o hospeda. Escolher aparelho é escolher ONDE os dados do perfil vão viver. */
  servidores: Record<string, string>;
  usados: string[];
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const [draft, setDraft] = useState<ProfileCreateRequest>(VAZIO);
  const [erros, setErros] = useState<Record<string, string>>({});
  const [salvando, setSalvando] = useState(false);

  function set<K extends keyof ProfileCreateRequest>(k: K, v: ProfileCreateRequest[K]) {
    setDraft((d) => ({ ...d, [k]: v }));
  }

  function validar(): boolean {
    const e: Record<string, string> = {};
    const user = (draft.username ?? '').trim().replace(/^@/, '');
    if (!/^[A-Za-z0-9._]{1,30}$/.test(user)) e.username = 'Use letras, números, ponto ou sublinhado (até 30).';
    if (!draft.password) e.password = 'Informe a senha; ela vai cifrada para o cofre e nunca volta.';
    if (!draft.instance_id) e.instance_id = 'Escolha o aparelho que vai usar este perfil.';
    setErros(e);
    return Object.keys(e).length === 0;
  }

  async function salvarEConectar() {
    if (!validar()) return;
    setSalvando(true);
    try {
      const limpo: ProfileCreateRequest = {
        ...draft,
        username: (draft.username ?? '').trim().replace(/^@/, ''),
        first_name: draft.first_name || null,
        last_name: draft.last_name || null,
        birth_date: draft.birth_date || null,
        email: draft.email || null,
        persona_id: draft.persona_id || null,
        policy_group_id: draft.policy_group_id || null,
      };
      const criado = await api.createProfile(limpo);
      try {
        await api.connectProfile(criado.id);
        toast({
          tone: 'success',
          title: `@${criado.username} cadastrado`,
          message: 'Credencial guardada cifrada. Conectando ao Instagram no aparelho escolhido…',
        });
      } catch (e) {
        // O perfil foi criado; só a conexão falhou (aparelho desligado, por exemplo).
        toastError(`@${criado.username} foi cadastrado, mas a conexão não começou`, e);
      }
      await onSaved();
    } catch (e) {
      toastError('Não foi possível cadastrar o perfil', e);
    } finally {
      setSalvando(false);
    }
  }

  const livres = instances.filter((i) => !usados.includes(i));
  // Agrupado por servidor: o select dizia "android-12" sem dizer em que máquina aquele aparelho está, e é a
  // máquina que decide onde os dados do perfil vão viver (E9).
  const porServidor = useMemo(() => {
    const grupos = new Map<string, string[]>();
    for (const i of livres) {
      const onde = servidores[i] ?? 'este servidor';
      grupos.set(onde, [...(grupos.get(onde) ?? []), i]);
    }
    return [...grupos.entries()];
  }, [livres, servidores]);

  return (
    <Dialog
      open
      onClose={onClose}
      title="Novo perfil Instagram"
      icon={UserRound}
      size="md"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>Cancelar</Button>
          <Button variant="primary" loading={salvando} onClick={salvarEConectar}>Salvar e conectar</Button>
        </>
      }
    >
      <div className={styles.form}>
        <Field label="Usuário do Instagram" error={erros.username} hint="Sem o @; é ele que será verificado na tela.">
          {({ id, describedBy, invalid }) => (
            <TextInput id={id} aria-describedby={describedBy} invalid={invalid} value={draft.username ?? ''}
                       placeholder="mariana.costa91182" onChange={(e) => set('username', e.target.value)} />
          )}
        </Field>
        <div className={styles.pair}>
          <Field label="Nome">
            {({ id }) => (
              <TextInput id={id} value={draft.first_name ?? ''} onChange={(e) => set('first_name', e.target.value)} />
            )}
          </Field>
          <Field label="Sobrenome">
            {({ id }) => (
              <TextInput id={id} value={draft.last_name ?? ''} onChange={(e) => set('last_name', e.target.value)} />
            )}
          </Field>
        </div>
        <div className={styles.pair}>
          <Field label="Nascimento" unit="opcional">
            {({ id }) => (
              <TextInput id={id} value={draft.birth_date ?? ''} placeholder="1991-08-22"
                         onChange={(e) => set('birth_date', e.target.value)} />
            )}
          </Field>
          <Field label="E-mail" unit="opcional">
            {({ id }) => (
              <TextInput id={id} value={draft.email ?? ''} onChange={(e) => set('email', e.target.value)} />
            )}
          </Field>
        </div>
        <Field label="Senha" error={erros.password}
               hint="Vai cifrada para o cofre e nunca é devolvida. Nem o painel nem a IA veem o valor.">
          {({ id, describedBy, invalid }) => (
            <TextInput id={id} type="password" autoComplete="new-password" aria-describedby={describedBy}
                       invalid={invalid} value={draft.password ?? ''}
                       onChange={(e) => set('password', e.target.value)} />
          )}
        </Field>
        <div className={styles.pair}>
          <Field label="Aparelho" error={erros.instance_id}
                 hint={'Um perfil por aparelho, e um aparelho por perfil. Os dados deste perfil passam a viver '
                       + 'no servidor do aparelho escolhido: em outro servidor será preciso entrar na conta de novo.'}>
            {({ id, describedBy, invalid }) => (
              <Select id={id} aria-describedby={describedBy} invalid={invalid} value={draft.instance_id ?? ''}
                      onChange={(e) => set('instance_id', e.target.value)}>
                <option value="">Escolha…</option>
                {porServidor.map(([servidor, ids]) => (
                  <optgroup key={servidor} label={servidor}>
                    {ids.map((i) => <option key={i} value={i}>{i}</option>)}
                  </optgroup>
                ))}
              </Select>
            )}
          </Field>
          <Field label="Persona" unit="opcional">
            {({ id }) => (
              <Select id={id} value={draft.persona_id ?? ''} onChange={(e) => set('persona_id', e.target.value)}>
                <option value="">Nenhuma</option>
                {personas.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </Select>
            )}
          </Field>
          <Field label="Grupo de acesso" unit="opcional">
            {({ id }) => (
              <Select id={id} value={draft.policy_group_id ?? ''} onChange={(e) => set('policy_group_id', e.target.value)}>
                <option value="">Sem grupo — padrão do catálogo</option>
                {grupos.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
              </Select>
            )}
          </Field>
        </div>
      </div>
    </Dialog>
  );
}
