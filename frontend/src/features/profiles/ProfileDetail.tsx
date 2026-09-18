import {
  ArrowLeft, BrainCircuit, CheckCircle2, ClipboardCheck, KeyRound, ListChecks, MessageSquare, PlugZap,
  ScanEye, Settings2, Smartphone, Sparkles, Trash2, UserRound,
} from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../../api/client';
import type {
  Approval, AuthAttempt, Capability, InstagramProfile, MemoryItem, Persona, PolicyName, ProfilePolicy, RunSummary,
  SocialDraft, SocialInteraction,
} from '../../api/types';
import { Badge } from '../../components/Badge';
import { Button } from '../../components/Button';
import { Card, CardBody, CardHeader } from '../../components/Card';
import { confirm } from '../../components/Confirm';
import { EmptyState } from '../../components/EmptyState';
import { Field, Select, TextArea, TextInput } from '../../components/Field';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { StatusBadge } from '../../components/StatusBadge';
import { TabPanel, Tabs, type TabDef } from '../../components/Tabs';
import { SESSION_STATUS, metaOf } from '../../lib/status';
import { toast, toastError } from '../../store/toasts';
import styles from './Profiles.module.css';

type Aba = 'visao' | 'persona' | 'device' | 'auth' | 'memoria' | 'interacoes' | 'aprovacoes' | 'execucoes' | 'config';

const POLICY_LABEL: Record<PolicyName, string> = {
  autonomous: 'Sozinho',
  approval_required: 'Com aprovação',
  manual_only: 'Só manual',
  disabled: 'Desligado',
};

const LIMIT_LABEL: Record<string, string> = {
  likes_per_hour: 'Curtidas por hora',
  comments_per_hour: 'Comentários por hora',
  follows_per_hour: 'Seguir/deixar de seguir por hora',
  dms_per_hour: 'Mensagens por hora',
  actions_per_run: 'Ações com efeito por execução',
  cooldown_between_external_actions_s: 'Intervalo entre ações (segundos)',
};

/** Tela de um perfil: as nove abas do §29. Cada aba carrega o que precisa quando é aberta, e só então. */
export function ProfileDetail({ profile, onBack, onChanged }: {
  profile: InstagramProfile;
  onBack: () => void;
  onChanged: () => Promise<void>;
}) {
  const [aba, setAba] = useState<Aba>('visao');
  const [pendentes, setPendentes] = useState<number | null>(null);

  useEffect(() => {
    let vivo = true;
    void api.listApprovals('pending', profile.id)
      .then((a) => vivo && setPendentes(a.length))
      .catch(() => vivo && setPendentes(null));
    return () => {
      vivo = false;
    };
  }, [profile.id, aba]);

  const abas: TabDef<Aba>[] = [
    { id: 'visao', label: 'Visão geral', icon: UserRound },
    { id: 'persona', label: 'Persona', icon: Sparkles },
    { id: 'device', label: 'Aparelho', icon: Smartphone },
    { id: 'auth', label: 'Autenticação', icon: KeyRound },
    { id: 'memoria', label: 'Memória', icon: BrainCircuit },
    { id: 'interacoes', label: 'Interações', icon: MessageSquare },
    { id: 'aprovacoes', label: 'Aprovações', icon: ClipboardCheck, count: pendentes, alert: !!pendentes },
    { id: 'execucoes', label: 'Execuções', icon: ListChecks },
    { id: 'config', label: 'Configurações', icon: Settings2 },
  ];

  return (
    <div className={styles.page}>
      <div className={styles.header}>
        <div>
          <Button size="sm" variant="ghost" icon={ArrowLeft} onClick={onBack}>Perfis</Button>
          <h2 className={styles.title}>@{profile.username}</h2>
          <p className={styles.lead}>
            {profile.display_name || 'Sem nome de exibição'} · {profile.instance_id || 'sem aparelho vinculado'}
          </p>
        </div>
        <StatusBadge meta={metaOf(SESSION_STATUS, profile.session.status)} />
      </div>

      <Tabs tabs={abas} active={aba} onChange={setAba} idBase={`perfil-${profile.id}`} label="Abas do perfil" />
      <TabPanel idBase={`perfil-${profile.id}`} id={aba}>
        {aba === 'visao' ? <VisaoGeral profile={profile} /> : null}
        {aba === 'persona' ? <AbaPersona profile={profile} onChanged={onChanged} /> : null}
        {aba === 'device' ? <AbaAparelho profile={profile} /> : null}
        {aba === 'auth' ? <AbaAutenticacao profile={profile} /> : null}
        {aba === 'memoria' ? <AbaMemoria profile={profile} /> : null}
        {aba === 'interacoes' ? <AbaInteracoes profile={profile} /> : null}
        {aba === 'aprovacoes' ? <AbaAprovacoes profile={profile} /> : null}
        {aba === 'execucoes' ? <AbaExecucoes profile={profile} /> : null}
        {aba === 'config' ? <AbaConfiguracoes profile={profile} /> : null}
      </TabPanel>
    </div>
  );
}

/** Carrega uma lista quando a aba abre. Erro vira estado vazio com aviso — nunca tela quebrada. */
function useLista<T>(carregar: () => Promise<T[]>, deps: unknown[]): [T[] | null, () => Promise<void>] {
  const [dados, setDados] = useState<T[] | null>(null);
  const token = useRef(0);
  const recarregar = useCallback(async () => {
    const meu = ++token.current;
    try {
      const r = await carregar();
      if (meu === token.current) setDados(r);
    } catch (e) {
      if (meu === token.current) setDados([]);
      toastError('Não foi possível carregar', e);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  useEffect(() => {
    void recarregar();
  }, [recarregar]);
  return [dados, recarregar];
}

function Linha({ rotulo, children }: { rotulo: string; children: React.ReactNode }) {
  return (
    <div className={styles.row}>
      <dt>{rotulo}</dt>
      <dd>{children}</dd>
    </div>
  );
}

function Carregando({ children }: { children?: React.ReactNode }) {
  return (
    <LoadingRegion label="Carregando…">
      <Skeleton height={80} />
      {children}
    </LoadingRegion>
  );
}

// ---------------------------------------------------------------- visão geral
function VisaoGeral({ profile }: { profile: InstagramProfile }) {
  const cred = profile.credential;
  return (
    <div className={styles.grid}>
      <Card>
        <CardHeader title="Identidade" />
        <CardBody>
          <dl className={styles.rows}>
            <Linha rotulo="Usuário">@{profile.username}</Linha>
            <Linha rotulo="Nome">{[profile.first_name, profile.last_name].filter(Boolean).join(' ') || '—'}</Linha>
            <Linha rotulo="E-mail">{profile.email || '—'}</Linha>
            <Linha rotulo="Nascimento">{profile.birth_date || '—'}</Linha>
            <Linha rotulo="Persona">{profile.persona_name ? <Badge>{profile.persona_name}</Badge> : '—'}</Linha>
          </dl>
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="Sessão e credencial" />
        <CardBody>
          <dl className={styles.rows}>
            <Linha rotulo="Sessão"><StatusBadge meta={metaOf(SESSION_STATUS, profile.session.status)} /></Linha>
            <Linha rotulo="Conta observada">
              {profile.session.observed_username ? `@${profile.session.observed_username}` : '—'}
            </Linha>
            <Linha rotulo="Verificada em">{profile.session.verified_at || '—'}</Linha>
            <Linha rotulo="Senha">{cred.configured ? 'guardada cifrada (nunca exibida)' : 'não configurada'}</Linha>
            <Linha rotulo="Tentativas falhas">{cred.failed_attempts}</Linha>
            <Linha rotulo="Bloqueada até">{cred.blocked_until || '—'}</Linha>
          </dl>
          {profile.session.detail ? <p className={styles.detail}>{profile.session.detail}</p> : null}
        </CardBody>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------- persona
function AbaPersona({ profile, onChanged }: { profile: InstagramProfile; onChanged: () => Promise<void> }) {
  const [persona, setPersona] = useState<Persona | null>(null);
  const [carregando, setCarregando] = useState(true);
  const [rascunho, setRascunho] = useState<SocialDraft | null>(null);
  const [recebido, setRecebido] = useState('oi! tudo bem?');
  const [testando, setTestando] = useState(false);
  const [salvando, setSalvando] = useState(false);

  useEffect(() => {
    let vivo = true;
    setCarregando(true);
    (profile.persona_id ? api.listPersonas() : Promise.resolve([]))
      .then((todas) => {
        if (!vivo) return;
        setPersona(todas.find((p) => p.id === profile.persona_id) ?? null);
      })
      .catch((e) => toastError('Não foi possível carregar a persona', e))
      .finally(() => vivo && setCarregando(false));
    return () => {
      vivo = false;
    };
  }, [profile.persona_id]);

  async function criar() {
    setSalvando(true);
    try {
      const nova = await api.createPersona({ name: `Persona de @${profile.username}` });
      await api.patchProfile(profile.id, { persona_id: nova.id });
      setPersona(nova);
      await onChanged();
      toast({ tone: 'success', title: 'Persona criada', message: 'Descreva o tom e o jeito de escrever dela.' });
    } catch (e) {
      toastError('Não foi possível criar a persona', e);
    } finally {
      setSalvando(false);
    }
  }

  async function salvar(campos: Partial<Persona>) {
    if (!persona) return;
    setSalvando(true);
    try {
      const atualizada = await api.updatePersona(persona.id, {
        name: campos.name ?? persona.name,
        summary: campos.summary ?? persona.summary,
        persona_prompt: campos.persona_prompt ?? persona.persona_prompt,
        traits: campos.traits ?? persona.traits,
      });
      setPersona(atualizada);
      await onChanged();
      toast({ tone: 'success', title: 'Persona atualizada' });
    } catch (e) {
      toastError('Não foi possível salvar a persona', e);
    } finally {
      setSalvando(false);
    }
  }

  async function testar() {
    if (!persona) return;
    setTestando(true);
    try {
      setRascunho(await api.previewPersona(persona.id, { incoming: recebido, profile_id: profile.id }));
    } catch (e) {
      toastError('Não foi possível testar a persona', e);
    } finally {
      setTestando(false);
    }
  }

  if (carregando) return <Carregando />;
  if (!persona) {
    return (
      <EmptyState
        icon={Sparkles}
        title="Sem persona"
        hint="A persona define tom, tamanho, emojis e o jeito de escrever deste perfil. Cada perfil tem a sua."
        actions={<Button icon={Sparkles} loading={salvando} onClick={() => void criar()}>Criar persona</Button>}
      >
        Este perfil ainda responde de forma neutra.
      </EmptyState>
    );
  }

  const t = persona.traits ?? {};
  return (
    <div className={styles.grid}>
      <Card>
        <CardHeader title="Como este perfil escreve" />
        <CardBody className={styles.form}>
          <Field label="Nome da persona">
            {({ id }) => (
              <TextInput id={id} defaultValue={persona.name}
                         onBlur={(e) => void salvar({ name: e.target.value })} />
            )}
          </Field>
          <Field label="Resumo">
            {({ id }) => (
              <TextInput id={id} defaultValue={persona.summary ?? ''}
                         onBlur={(e) => void salvar({ summary: e.target.value })} />
            )}
          </Field>
          <Field label="Tom" hint="Ex.: animado, direto, acolhedor.">
            {({ id }) => (
              <TextInput id={id} defaultValue={t.tone ?? ''}
                         onBlur={(e) => void salvar({ traits: { ...t, tone: e.target.value } })} />
            )}
          </Field>
          <Field label="Formalidade">
            {({ id }) => (
              <Select id={id} defaultValue={t.formality ?? ''}
                      onChange={(e) => void salvar({ traits: { ...t, formality: (e.target.value || null) as never } })}>
                <option value="">—</option>
                <option value="informal">Informal</option>
                <option value="neutro">Neutro</option>
                <option value="formal">Formal</option>
              </Select>
            )}
          </Field>
          <Field label="Tamanho típico">
            {({ id }) => (
              <Select id={id} defaultValue={t.typical_length ?? ''}
                      onChange={(e) => void salvar({ traits: { ...t, typical_length: (e.target.value || null) as never } })}>
                <option value="">—</option>
                <option value="curta">Curta</option>
                <option value="media">Média</option>
                <option value="longa">Longa</option>
              </Select>
            )}
          </Field>
          <Field label="Emojis">
            {({ id }) => (
              <Select id={id} defaultValue={t.emojis ?? ''}
                      onChange={(e) => void salvar({ traits: { ...t, emojis: (e.target.value || null) as never } })}>
                <option value="">—</option>
                <option value="nunca">Nunca</option>
                <option value="raro">Raro</option>
                <option value="moderado">Moderado</option>
                <option value="muito">Muito</option>
              </Select>
            )}
          </Field>
          <Field label="Instruções da persona" hint="Vai direto ao modelo, junto com a memória do perfil.">
            {({ id }) => (
              <TextArea id={id} rows={4} defaultValue={persona.persona_prompt ?? ''}
                        onBlur={(e) => void salvar({ persona_prompt: e.target.value })} />
            )}
          </Field>
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Testar persona" subtitle="Mostra como ela responderia. Nada é publicado." />
        <CardBody className={styles.form}>
          <Field label="Mensagem recebida">
            {({ id }) => <TextArea id={id} rows={3} value={recebido} onChange={(e) => setRecebido(e.target.value)} />}
          </Field>
          <Button icon={Sparkles} loading={testando}
                  disabledReason={recebido.trim() ? null : 'Escreva a mensagem que a persona receberia.'}
                  onClick={() => void testar()}>
            Testar persona
          </Button>
          {rascunho ? (
            <div className={styles.draft}>
              {rascunho.refused ? (
                <p className={styles.detail}>Recusou responder: {rascunho.refusal_reason}</p>
              ) : (
                <>
                  <p className={styles.draftText}>{rascunho.content}</p>
                  <p className={styles.detail}>{rascunho.rationale}</p>
                </>
              )}
            </div>
          ) : null}
        </CardBody>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------- aparelho
function AbaAparelho({ profile }: { profile: InstagramProfile }) {
  const [apps, recarregar] = useLista(() => api.listAppState(), [profile.instance_id]);
  const doAparelho = (apps ?? []).filter((a) => a.instance_id === profile.instance_id);

  if (!profile.instance_id) {
    return (
      <EmptyState icon={Smartphone} title="Sem aparelho" hint="Vincule um aparelho para este perfil poder operar.">
        Este perfil não está vinculado a nenhum aparelho.
      </EmptyState>
    );
  }
  if (apps === null) return <Carregando />;
  return (
    <Card>
      <CardHeader title={`Aparelho ${profile.instance_id}`}
                  subtitle="O que está instalado, lido do próprio aparelho — nunca presumido."
                  actions={<Button size="sm" variant="ghost" onClick={() => void recarregar()}>Atualizar</Button>} />
      <CardBody>
        {doAparelho.length === 0 ? (
          <p className={styles.detail}>Nenhum aplicativo catalogado neste aparelho ainda.</p>
        ) : (
          <dl className={styles.rows}>
            {doAparelho.map((a) => (
              <Linha key={a.package_name} rotulo={a.package_name}>
                <Badge tone={a.state === 'ready' ? 'success' : 'neutral'}>{a.state}</Badge>{' '}
                {a.observed_version_name ? `${a.observed_version_name} (${a.observed_version_code ?? '?'})` : '—'}
                {a.drift_kind ? <> · <Badge tone="warning">{a.drift_kind}</Badge></> : null}
              </Linha>
            ))}
          </dl>
        )}
        <p className={styles.detail}>
          Sessão: {profile.session.status} {profile.session.detail ? `— ${profile.session.detail}` : ''}
        </p>
      </CardBody>
    </Card>
  );
}

// ---------------------------------------------------------------- autenticação
function AbaAutenticacao({ profile }: { profile: InstagramProfile }) {
  const [tentativas, recarregar] = useLista<AuthAttempt>(() => api.listAuthAttempts(profile.id), [profile.id]);
  const [busy, setBusy] = useState(false);

  async function acao(qual: 'connect' | 'verify' | 'logout') {
    if (qual === 'logout' && !(await confirm({
      title: 'Sair da conta neste aparelho?',
      body: 'Os dados do app são apagados; a sessão precisará ser refeita com a senha.',
      confirmLabel: 'Sair da conta', danger: true,
    })).confirmed) return;
    setBusy(true);
    try {
      await (qual === 'connect' ? api.connectProfile(profile.id)
        : qual === 'verify' ? api.verifyProfile(profile.id) : api.logoutProfile(profile.id));
      toast({ tone: 'info', title: 'Pedido aceito', message: 'O aparelho está sendo usado agora.' });
      setTimeout(() => void recarregar(), 4000);
    } catch (e) {
      toastError('Não foi possível executar', e);
    } finally {
      setBusy(false);
    }
  }

  if (tentativas === null) return <Carregando />;
  return (
    <Card>
      <CardHeader
        title="Autenticação"
        subtitle="A senha só passa pelo canal seguro; ela não aparece aqui, nem no log, nem em evidência."
        actions={
          <div className={styles.actions}>
            <Button size="sm" icon={PlugZap} loading={busy}
                    disabledReason={profile.credential.configured
                      ? null : 'Cadastre a senha deste perfil antes de conectar.'}
                    onClick={() => void acao('connect')}>Conectar</Button>
            <Button size="sm" variant="ghost" icon={ScanEye} loading={busy}
                    onClick={() => void acao('verify')}>Verificar conta</Button>
            <Button size="sm" variant="ghost" icon={KeyRound} loading={busy}
                    onClick={() => void acao('logout')}>Sair da conta</Button>
          </div>
        } />
      <CardBody>
        {tentativas.length === 0 ? (
          <p className={styles.detail}>Nenhuma tentativa de autenticação registrada.</p>
        ) : (
          <ul className={styles.list}>
            {tentativas.map((t) => (
              <li key={t.id}>
                <Badge tone={t.outcome === 'session_ready' ? 'success' : t.outcome ? 'warning' : 'neutral'}>
                  {t.outcome ?? 'em andamento'}
                </Badge>{' '}
                {t.started_at} · {t.instance_id} {t.detail ? <span className={styles.detail}>— {t.detail}</span> : null}
              </li>
            ))}
          </ul>
        )}
      </CardBody>
    </Card>
  );
}

// ---------------------------------------------------------------- memória
function AbaMemoria({ profile }: { profile: InstagramProfile }) {
  const [itens, recarregar] = useLista<MemoryItem>(() => api.listMemory(profile.id), [profile.id]);
  const [assunto, setAssunto] = useState('');
  const [conteudo, setConteudo] = useState('');
  const [salvando, setSalvando] = useState(false);

  async function adicionar() {
    setSalvando(true);
    try {
      await api.addMemory(profile.id, { subject: assunto.trim(), content: conteudo.trim() });
      setAssunto('');
      setConteudo('');
      await recarregar();
      toast({ tone: 'success', title: 'Fato guardado' });
    } catch (e) {
      toastError('Não foi possível guardar', e);
    } finally {
      setSalvando(false);
    }
  }

  async function esquecer(item: MemoryItem) {
    if (!(await confirm({ title: 'Esquecer este fato?', body: item.content, confirmLabel: 'Esquecer',
                          danger: true })).confirmed) return;
    try {
      await api.deleteMemory(profile.id, item.id);
      await recarregar();
    } catch (e) {
      toastError('Não foi possível esquecer', e);
    }
  }

  if (itens === null) return <Carregando />;
  return (
    <div className={styles.grid}>
      <Card>
        <CardHeader title="O que este perfil sabe"
                    subtitle="Só o que veio de interação confirmada ou foi ensinado aqui. Senha e código nunca entram." />
        <CardBody>
          {itens.length === 0 ? (
            <p className={styles.detail}>Nenhuma lembrança ainda.</p>
          ) : (
            <ul className={styles.list}>
              {itens.map((m) => (
                <li key={m.id}>
                  <Badge>{m.subject}</Badge> {m.content}{' '}
                  <span className={styles.detail}>
                    (importância {m.importance.toFixed(1)} · visto {m.occurrences}x · {m.source})
                  </span>
                  <Button size="sm" variant="ghost" icon={Trash2} onClick={() => void esquecer(m)}>Esquecer</Button>
                </li>
              ))}
            </ul>
          )}
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="Ensinar um fato" />
        <CardBody className={styles.form}>
          <Field label="Sobre quem/o quê" hint="Ex.: @ana, ou um tema.">
            {({ id }) => (
              <TextInput id={id} value={assunto} onChange={(e) => setAssunto(e.target.value)} placeholder="@ana" />
            )}
          </Field>
          <Field label="Fato">
            {({ id }) => (
              <TextArea id={id} rows={3} value={conteudo} onChange={(e) => setConteudo(e.target.value)}
                        placeholder="Corre maratonas aos domingos" />
            )}
          </Field>
          <Button loading={salvando}
                  disabledReason={assunto.trim() && conteudo.trim() ? null : 'Preencha o assunto e o fato.'}
                  onClick={() => void adicionar()}>Guardar</Button>
        </CardBody>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------- interações
function AbaInteracoes({ profile }: { profile: InstagramProfile }) {
  const [itens] = useLista<SocialInteraction>(() => api.listInteractions(profile.id), [profile.id]);
  if (itens === null) return <Carregando />;
  if (itens.length === 0) {
    return (
      <EmptyState icon={MessageSquare} title="Sem interações" hint="Aqui fica o que este perfil fez e recebeu.">
        Nada registrado ainda.
      </EmptyState>
    );
  }
  return (
    <Card>
      <CardHeader title="Histórico social" />
      <CardBody>
        <ul className={styles.list}>
          {itens.map((i) => (
            <li key={i.id}>
              <Badge tone={i.status === 'confirmed' ? 'success' : i.status === 'pending' ? 'warning' : 'neutral'}>
                {i.status}
              </Badge>{' '}
              {i.occurred_at} · {i.type} · {i.counterparty ?? '—'}
              {i.outgoing_content || i.incoming_content ? (
                <span className={styles.detail}> — {i.outgoing_content || i.incoming_content}</span>
              ) : null}
            </li>
          ))}
        </ul>
      </CardBody>
    </Card>
  );
}

// ---------------------------------------------------------------- aprovações
function AbaAprovacoes({ profile }: { profile: InstagramProfile }) {
  const [itens, recarregar] = useLista<Approval>(() => api.listApprovals('pending', profile.id), [profile.id]);
  const [editando, setEditando] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);

  async function decidir(a: Approval, verbo: 'approve' | 'edit' | 'reject') {
    setBusy(a.id);
    try {
      await api.decideApproval(a.id, verbo, verbo === 'edit' ? { content: editando[a.id] ?? '' } : {});
      await recarregar();
      toast({
        tone: verbo === 'reject' ? 'info' : 'success',
        title: verbo === 'approve' ? 'Aprovado' : verbo === 'edit' ? 'Editado e aprovado' : 'Rejeitado',
        message: verbo === 'reject' ? 'Nada será enviado neste alvo.' : 'O item volta para a fila.',
      });
    } catch (e) {
      toastError('Não foi possível decidir', e);
    } finally {
      setBusy(null);
    }
  }

  if (itens === null) return <Carregando />;
  if (itens.length === 0) {
    return (
      <EmptyState icon={CheckCircle2} title="Nada para aprovar"
                  hint="Ações com aprovação exigida aparecem aqui antes de acontecer.">
        Nenhuma pendência.
      </EmptyState>
    );
  }
  return (
    <div className={styles.grid}>
      {itens.map((a) => (
        <Card key={a.id}>
          <CardHeader title={a.summary} subtitle={`${a.capability} · ${a.target ?? 'sem alvo'}`} />
          <CardBody className={styles.form}>
            <Field label="Conteúdo que será enviado">
              {({ id }) => (
                <TextArea id={id} rows={3} defaultValue={a.content ?? ''}
                          onChange={(e) => setEditando((s) => ({ ...s, [a.id]: e.target.value }))} />
              )}
            </Field>
            <div className={styles.actions}>
              <Button size="sm" loading={busy === a.id} onClick={() => void decidir(a, 'approve')}>Aprovar</Button>
              <Button size="sm" variant="secondary" loading={busy === a.id}
                      disabledReason={(editando[a.id] ?? '').trim()
                        ? null : 'Altere o texto para poder aprovar a edição.'}
                      onClick={() => void decidir(a, 'edit')}>Editar</Button>
              <Button size="sm" variant="ghost" loading={busy === a.id}
                      onClick={() => void decidir(a, 'reject')}>Rejeitar</Button>
            </div>
          </CardBody>
        </Card>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- execuções
function AbaExecucoes({ profile }: { profile: InstagramProfile }) {
  const [runs] = useLista<RunSummary>(() => api.listProfileRuns(profile.id), [profile.id]);
  if (runs === null) return <Carregando />;
  if (runs.length === 0) {
    return (
      <EmptyState icon={ListChecks} title="Sem execuções" hint="Comandos executados por este perfil aparecem aqui.">
        Nenhuma execução ainda.
      </EmptyState>
    );
  }
  return (
    <Card>
      <CardHeader title="Execuções deste perfil" />
      <CardBody>
        <ul className={styles.list}>
          {runs.map((r) => (
            <li key={r.id}>
              <Badge>{r.status}</Badge> {r.created_at} — {r.command}
            </li>
          ))}
        </ul>
      </CardBody>
    </Card>
  );
}

// ---------------------------------------------------------------- configurações
function AbaConfiguracoes({ profile }: { profile: InstagramProfile }) {
  const [politica, setPolitica] = useState<ProfilePolicy | null>(null);
  const [acoes, setAcoes] = useState<Capability[]>([]);
  const [salvando, setSalvando] = useState(false);

  useEffect(() => {
    let vivo = true;
    Promise.all([api.getPolicy(profile.id), api.listCapabilities()])
      .then(([p, c]) => {
        if (!vivo) return;
        setPolitica(p);
        setAcoes(c);
      })
      .catch((e) => toastError('Não foi possível carregar as políticas', e));
    return () => {
      vivo = false;
    };
  }, [profile.id]);

  async function mudarPolitica(key: string, valor: PolicyName) {
    setSalvando(true);
    try {
      setPolitica(await api.setPolicy(profile.id, { capabilities: { [key]: valor } }));
    } catch (e) {
      toastError('Não foi possível salvar a política', e);
    } finally {
      setSalvando(false);
    }
  }

  async function mudarLimite(key: string, valor: number) {
    setSalvando(true);
    try {
      setPolitica(await api.setPolicy(profile.id, { limits: { [key]: valor } }));
    } catch (e) {
      toastError('Não foi possível salvar o limite', e);
    } finally {
      setSalvando(false);
    }
  }

  if (!politica) return <Carregando />;
  return (
    <div className={styles.grid}>
      <Card>
        <CardHeader title="O que este perfil pode fazer sozinho"
                    subtitle="O padrão vem do catálogo; aqui você pode endurecer." />
        <CardBody>
          <ul className={styles.list}>
            {acoes.map((c) => {
              const atual = politica.capabilities[c.key] ?? c.default_policy;
              return (
                <li key={c.key}>
                  <span>{c.title}{c.side_effect ? <> <Badge tone="warning">efeito externo</Badge></> : null}</span>
                  <Select value={atual} disabled={salvando}
                          onChange={(e) => void mudarPolitica(c.key, e.target.value as PolicyName)}>
                    {(Object.keys(POLICY_LABEL) as PolicyName[]).map((p) => (
                      <option key={p} value={p}>{POLICY_LABEL[p]}</option>
                    ))}
                  </Select>
                  {atual !== c.default_policy ? (
                    <span className={styles.detail}>padrão: {POLICY_LABEL[c.default_policy]}</span>
                  ) : null}
                </li>
              );
            })}
          </ul>
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="Limites"
                    subtitle="Existem para o sistema não agir como robô e derrubar a própria conta." />
        <CardBody className={styles.form}>
          {Object.entries(politica.limits).map(([k, v]) => (
            <Field key={k} label={LIMIT_LABEL[k] ?? k}>
              {({ id }) => (
                <TextInput id={id} type="number" min={0} defaultValue={v} disabled={salvando}
                           onBlur={(e) => {
                             const n = Number(e.target.value);
                             if (Number.isFinite(n) && n >= 0 && n !== v) void mudarLimite(k, n);
                           }} />
              )}
            </Field>
          ))}
        </CardBody>
      </Card>
    </div>
  );
}
