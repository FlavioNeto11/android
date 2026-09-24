import {
  ArrowLeft, AtSign, BrainCircuit, CheckCircle2, ChevronRight, ClipboardCheck, KeyRound, ListChecks, MessageSquare,
  PlugZap, ScanEye, Settings2, Smartphone, Sparkles, Trash2, TriangleAlert, Undo2, UserRound,
} from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { api, profileAvatarUrl } from '../../api/client';
import type {
  Approval, AuthAttempt, Capability, InstagramProfile, MemoryItem, OfflinePolicy, Persona, PolicyGroup, PolicyName,
  PolicyOrigin, ProfilePolicyPatch,
  ProfileCapabilities, ProfilePolicy, RunSummary, SocialDraft, SocialInteraction,
} from '../../api/types';
import { Avatar } from '../../components/Avatar';
import { Badge } from '../../components/Badge';
import { Banner } from '../../components/Banner';
import { Button } from '../../components/Button';
import { Card, CardBody, CardHeader } from '../../components/Card';
import { confirm } from '../../components/Confirm';
import { EmptyState } from '../../components/EmptyState';
import { Field, Select, TextArea, TextInput } from '../../components/Field';
import { ProgressBar } from '../../components/ProgressBar';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { StatusBadge } from '../../components/StatusBadge';
import { TabPanel, Tabs, type TabDef } from '../../components/Tabs';
import { clamp01, cx, isRecord } from '../../lib/format';
import { PROFILE_STATUS, SESSION_STATUS, metaOf } from '../../lib/status';
import { formatAgo, useNow } from '../../lib/time';
import { useAppStore } from '../../store/app';
import { onLiveEvent } from '../../store/live';
import { toast, toastError } from '../../store/toasts';
import {
  CompletenessGauge, EMOJI_OPTIONS, ExampleBubbles, FORMALITY_OPTIONS, LENGTH_OPTIONS, PairColumns,
  PhraseColumns, ROTULO_DE_VOZ, Ruler, StatFigure, TagList,
} from './PersonaVisual';
import { baldeDoLimite, contarUsoDeHoje } from './PolicyVisual';
import { LimitsEditor, type Origem, PolicyActionsEditor } from './PolicyEditor';
import appStyles from '../../App.module.css';
import styles from './Profiles.module.css';
import { InteractionTimeline, TimelineFilter } from './Timeline';
import { AbaContas, AppSwitcher, useContas } from './ProfileAccounts';

type Aba = 'visao' | 'contas' | 'persona' | 'device' | 'auth' | 'memoria' | 'interacoes' | 'habilidades' | 'aprovacoes' | 'execucoes' | 'config';

/** Tela de um perfil: as nove abas do §29. Cada aba carrega o que precisa quando é aberta, e só então. */
export function ProfileDetail({ profile, onBack, onChanged }: {
  profile: InstagramProfile;
  onBack: () => void;
  onChanged: () => Promise<void>;
}) {
  const [aba, setAba] = useState<Aba>('visao');
  // Item 12.2: o perfil tem contas em vários apps; o filtro de app vale para Memória e Interações.
  const [contas, recarregarContas] = useContas(profile.id);
  const [appFiltro, setAppFiltro] = useState<string | null>(null);
  const [novaConta, setNovaConta] = useState(0);
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
    { id: 'contas', label: 'Contas', icon: AtSign, count: contas?.length ?? null },
    { id: 'persona', label: 'Persona', icon: Sparkles },
    { id: 'device', label: 'Aparelho', icon: Smartphone },
    { id: 'auth', label: 'Autenticação', icon: KeyRound },
    { id: 'memoria', label: 'Memória', icon: BrainCircuit },
    { id: 'interacoes', label: 'Interações', icon: MessageSquare },
    { id: 'habilidades', label: 'Habilidades', icon: Sparkles },
    { id: 'aprovacoes', label: 'Aprovações', icon: ClipboardCheck, count: pendentes, alert: !!pendentes },
    { id: 'execucoes', label: 'Execuções', icon: ListChecks },
    { id: 'config', label: 'Configurações', icon: Settings2 },
  ];

  return (
    <div className={`${appStyles.page} ${styles.page}`}>
      <div className={styles.header}>
        <div>
          <Button size="sm" variant="ghost" icon={ArrowLeft} onClick={onBack}>Perfis</Button>
          <div className={styles.identidade}>
            <Avatar src={profileAvatarUrl(profile.id)} name={profile.display_name || profile.username} size={56} />
            <div>
              <h2 className={styles.title}>@{profile.username}</h2>
              <p className={styles.lead}>
                {profile.display_name || 'Sem nome de exibição'} · {profile.instance_id || 'sem aparelho vinculado'}
              </p>
            </div>
          </div>
        </div>
        <StatusBadge meta={metaOf(SESSION_STATUS, profile.session.status)} />
      </div>

      {contas && contas.length ? (
        <AppSwitcher contas={contas} valor={appFiltro} onChange={setAppFiltro}
                     onAdicionar={() => { setAba('contas'); setNovaConta((n) => n + 1); }} />
      ) : null}
      <Tabs tabs={abas} active={aba} onChange={setAba} idBase={`perfil-${profile.id}`} label="Abas do perfil" />
      <TabPanel idBase={`perfil-${profile.id}`} id={aba}>
        {aba === 'visao' ? <VisaoGeral profile={profile} /> : null}
        {aba === 'contas' ? (
          <AbaContas key={novaConta} profile={profile} contas={contas} recarregar={recarregarContas}
                     abrirFormulario={novaConta > 0} />
        ) : null}
        {aba === 'persona' ? <AbaPersona profile={profile} onChanged={onChanged} /> : null}
        {aba === 'device' ? <AbaAparelho profile={profile} onChanged={onChanged} /> : null}
        {aba === 'auth' ? <AbaAutenticacao profile={profile} onChanged={onChanged} /> : null}
        {aba === 'memoria' ? <AbaMemoria profile={profile} appId={appFiltro} /> : null}
        {aba === 'interacoes' ? <AbaInteracoes profile={profile} appId={appFiltro} /> : null}
        {aba === 'habilidades' ? <AbaHabilidades profile={profile} /> : null}
        {aba === 'aprovacoes' ? <AbaAprovacoes profile={profile} /> : null}
        {aba === 'execucoes' ? <AbaExecucoes profile={profile} /> : null}
        {aba === 'config' ? <AbaConfiguracoes profile={profile} onChanged={onChanged} /> : null}
      </TabPanel>
    </div>
  );
}

/** Carrega uma lista quando a aba abre. Erro vira estado vazio com aviso — nunca tela quebrada. */
/** Sobe quando chega evento persistido DESTE perfil ou do aparelho dele — no máximo uma vez a cada 1,5 s. As abas
 *  põem a versão nas dependências e recarregam sozinhas enquanto o perfil age (antes carregavam só ao abrir). */
function useVersaoAoVivo(profile: InstagramProfile): number {
  const [versao, setVersao] = useState(0);
  const instancia = profile.instance_id;
  const grupo = profile.policy_group_id ?? null;
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | null = null;
    const desligar = onLiveEvent((ev) => {
      if (ev.id === null) return;                       // quadro/métrica efêmera: não muda dado de perfil
      const doPerfil = isRecord(ev.data) && (ev.data.profile_id === profile.id
        || (!!grupo && ev.data.group_id === grupo));        // editar o grupo muda o que vale para este perfil
      if (!doPerfil && !(instancia && ev.instance_id === instancia)) return;
      if (timer) return;
      timer = setTimeout(() => {
        timer = null;
        setVersao((v) => v + 1);
      }, 1500);
    });
    return () => {
      desligar();
      if (timer) clearTimeout(timer);
    };
  }, [profile.id, instancia, grupo]);
  return versao;
}

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
/** Cartão de identidade: a pessoa em vez do formulário. Persona, réguas de voz, interesses e números vêm
 *  de três fontes carregadas juntas (persona, interações, capacidades) — cada uma cai em vazio, não em erro. */
function VisaoGeral({ profile }: { profile: InstagramProfile }) {
  const cred = profile.credential;
  const [persona, setPersona] = useState<Persona | null>(null);
  const [interacoes, setInteracoes] = useState<SocialInteraction[] | null>(null);
  const [capacidades, setCapacidades] = useState<ProfileCapabilities | null>(null);
  const now = useNow();
  const versao = useVersaoAoVivo(profile);

  useEffect(() => {
    let vivo = true;
    (profile.persona_id ? api.listPersonas() : Promise.resolve([]))
      .then((todas) => { if (vivo) setPersona(todas.find((p) => p.id === profile.persona_id) ?? null); })
      .catch(() => { if (vivo) setPersona(null); });
    api.listInteractions(profile.id)
      .then((r) => { if (vivo) setInteracoes(r); })
      .catch(() => { if (vivo) setInteracoes([]); });
    api.profileCapabilities(profile.id)
      .then((r) => { if (vivo) setCapacidades(r); })
      .catch(() => {
        if (vivo) setCapacidades({ profile_id: profile.id, flows: [], steps_driven_by: {}, recipe_share: null, interactions: {} });
      });
    return () => { vivo = false; };
  }, [profile.id, profile.persona_id, versao]);

  const t = persona?.traits ?? {};
  const totalInteracoes = capacidades ? Object.values(capacidades.interactions).reduce((a, b) => a + b, 0) : null;
  const ultimoContato = interacoes && interacoes.length > 0 ? interacoes[0]?.occurred_at ?? null : null;
  const semIaPct = capacidades?.recipe_share == null ? null : Math.round(capacidades.recipe_share * 100);

  return (
    <div className={styles.stack}>
      <Card>
        <CardHeader title="Identidade" />
        <CardBody className={styles.identityCard}>
          <div className={styles.identidade}>
            <Avatar src={profileAvatarUrl(profile.id)} name={profile.display_name || profile.username} size={72} />
            <div>
              <h3 className={styles.title}>@{profile.username}</h3>
              <p className={styles.lead}>
                {[profile.first_name, profile.last_name].filter(Boolean).join(' ') || profile.display_name || 'Sem nome de exibição'}
                {profile.email ? ` · ${profile.email}` : ''}
              </p>
            </div>
          </div>
          <div className={styles.identityBadges}>
            <StatusBadge meta={metaOf(SESSION_STATUS, profile.session.status)} />
            <StatusBadge meta={metaOf(PROFILE_STATUS, profile.status)} />
            {profile.persona_name ? <Badge icon={Sparkles}>{profile.persona_name}</Badge> : <Badge tone="muted">sem persona</Badge>}
            <Badge icon={Smartphone} tone={profile.instance_id ? 'neutral' : 'muted'}>
              {profile.instance_id ?? 'sem aparelho vinculado'}
            </Badge>
          </div>
          {persona ? (
            <div className={styles.pair}>
              <Ruler compact label="Formalidade" options={FORMALITY_OPTIONS} value={t.formality} />
              <Ruler compact label="Tamanho" options={LENGTH_OPTIONS} value={t.typical_length} />
            </div>
          ) : null}
          {persona ? <Ruler compact label="Emoji" options={EMOJI_OPTIONS} value={t.emojis} /> : null}
          {persona && (t.interests ?? []).length > 0 ? <TagList items={t.interests ?? []} /> : null}
          <div className={styles.statRow}>
            <StatFigure value={totalInteracoes ?? '—'} label="interações confirmadas" />
            <StatFigure value={semIaPct === null ? '—' : `${semIaPct}%`} label="roda sem IA" />
            <StatFigure value={ultimoContato ? formatAgo(ultimoContato, now) : 'nunca'} label="último contato" />
          </div>
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Interações recentes" subtitle="As 8 mais recentes — a lista completa fica na aba Interações." />
        <CardBody>
          {interacoes === null ? <Skeleton height={80} /> : interacoes.length === 0 ? (
            <p className={styles.detail}>Nenhuma interação registrada ainda.</p>
          ) : (
            <InteractionTimeline itens={interacoes} limite={8} />
          )}
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Sessão e credencial" />
        <CardBody>
          <dl className={styles.rows}>
            <Linha rotulo="Conta observada">
              {profile.session.observed_username ? `@${profile.session.observed_username}` : '—'}
            </Linha>
            <Linha rotulo="Verificada em">
              {profile.session.verified_at || 'nunca'}
              {profile.session.stale ? (
                <>
                  {' '}
                  <Badge tone="warning">
                    dado velho — o aparelho é relido antes da próxima tarefa
                  </Badge>
                </>
              ) : null}
            </Linha>
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
/** Lista editada como texto: uma por linha. Guardar vazio é apagar a lista, não gravar [''] . */
function linhasParaLista(valor: string): string[] {
  return valor.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
}

function AbaPersona({ profile, onChanged }: { profile: InstagramProfile; onChanged: () => Promise<void> }) {
  const [persona, setPersona] = useState<Persona | null>(null);
  const [carregando, setCarregando] = useState(true);
  const [rascunho, setRascunho] = useState<SocialDraft | null>(null);
  const [recebido, setRecebido] = useState('oi! tudo bem?');
  const [intencao, setIntencao] = useState('');
  const [tipo, setTipo] = useState<'dm_reply' | 'dm_initiate' | 'post_comment'>('dm_reply');
  const [testando, setTestando] = useState(false);
  const [salvando, setSalvando] = useState(false);
  const [editando, setEditando] = useState(false);

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
      setRascunho(await api.previewPersona(persona.id, {
        kind: tipo,
        // Puxar conversa/comentar NÃO tem mensagem recebida: mandar o campo mesmo assim faria a prévia conferir
        // um prompt que a execução nunca monta.
        incoming: tipo === 'dm_reply' ? recebido : '',
        brief: tipo === 'dm_reply' ? '' : intencao,
        profile_id: profile.id,
      }));
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
  const faltando = persona.voice_gaps ?? [];
  return (
    <div className={styles.personaLayout}>
      <Card>
        <CardHeader title="Quem é" subtitle={persona.name}
                    actions={
                      <Button size="sm" variant="ghost" icon={Settings2} onClick={() => setEditando((v) => !v)}>
                        {editando ? 'Fechar edição' : 'Editar'}
                      </Button>
                    } />
        <CardBody className={styles.identityCard}>
          {faltando.length > 0 ? (
            <Banner tone="warning" icon={TriangleAlert} role="status"
                    title={`Faltam ${faltando.length} campo(s) de voz nesta persona`}>
              Sem eles o modelo só tem tom, formalidade, tamanho e emoji para diferenciar esta conta das outras —
              e contas diferentes acabam escrevendo parecido. Faltam:{' '}
              {faltando.map((c) => ROTULO_DE_VOZ[c] ?? c).join(', ')}.
            </Banner>
          ) : null}
          {persona.summary ? <p className={styles.lead}>{persona.summary}</p> : null}
          {t.personality ? <p className={styles.personality}>“{t.personality}”</p> : null}
          <div className={styles.pair}>
            <Ruler label="Formalidade" options={FORMALITY_OPTIONS} value={t.formality} />
            <Ruler label="Tamanho típico" options={LENGTH_OPTIONS} value={t.typical_length} />
          </div>
          <Ruler label="Emojis" options={EMOJI_OPTIONS} value={t.emojis} />
          <TagList items={t.interests ?? []} empty="Sem interesses registrados." />
          <PhraseColumns common={t.common_phrases ?? []} forbidden={t.forbidden_phrases ?? []} />
          <div>
            <h4>Exemplos</h4>
            <ExampleBubbles examples={t.examples ?? []} />
          </div>
          <PairColumns leftLabel="Com quem já conhece" left={t.with_known} rightLabel="Com desconhecidos" right={t.with_strangers} />
          <PairColumns leftLabel="Em mensagem direta" left={t.dm_style} rightLabel="Em comentário" right={t.comment_style} />
          {(t.appearance || t.visual_style) ? (
            <PairColumns leftLabel="Aparência" left={t.appearance} rightLabel="Estilo visual" right={t.visual_style} />
          ) : null}
          <CompletenessGauge total={Object.keys(ROTULO_DE_VOZ).length} missing={faltando} />

          {editando ? (
            <div className={cx(styles.form, styles.editablePanel)}>
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
          <Field label="Personalidade" hint="Quem é esta pessoa em uma frase.">
            {({ id }) => (
              <TextArea id={id} rows={2} defaultValue={t.personality ?? ''}
                        onBlur={(e) => void salvar({ traits: { ...t, personality: e.target.value || null } })} />
            )}
          </Field>
          <Field label="Gírias" hint="Como ela fala no dia a dia. Ex.: usa “mano”, “top”.">
            {({ id }) => (
              <TextInput id={id} defaultValue={t.slang ?? ''}
                         onBlur={(e) => void salvar({ traits: { ...t, slang: e.target.value || null } })} />
            )}
          </Field>
          <Field label="Humor" hint="Ex.: irônica, brincalhona, séria.">
            {({ id }) => (
              <TextInput id={id} defaultValue={t.humor ?? ''}
                         onBlur={(e) => void salvar({ traits: { ...t, humor: e.target.value || null } })} />
            )}
          </Field>
          <Field label="Interesses" hint="Um por linha.">
            {({ id }) => (
              <TextArea id={id} rows={2} defaultValue={(t.interests ?? []).join('\n')}
                        onBlur={(e) => void salvar({ traits: { ...t, interests: linhasParaLista(e.target.value) } })} />
            )}
          </Field>
          <Field label="Estilo em mensagem direta" hint="Como ela escreve numa DM: abertura, tamanho, jeito.">
            {({ id }) => (
              <TextArea id={id} rows={2} defaultValue={t.dm_style ?? ''}
                        onBlur={(e) => void salvar({ traits: { ...t, dm_style: e.target.value || null } })} />
            )}
          </Field>
          <Field label="Estilo em comentário" hint="Como ela comenta uma publicação.">
            {({ id }) => (
              <TextArea id={id} rows={2} defaultValue={t.comment_style ?? ''}
                        onBlur={(e) => void salvar({ traits: { ...t, comment_style: e.target.value || null } })} />
            )}
          </Field>
          <Field label="Com quem já conhece">
            {({ id }) => (
              <TextArea id={id} rows={2} defaultValue={t.with_known ?? ''}
                        onBlur={(e) => void salvar({ traits: { ...t, with_known: e.target.value || null } })} />
            )}
          </Field>
          <Field label="Com desconhecidos">
            {({ id }) => (
              <TextArea id={id} rows={2} defaultValue={t.with_strangers ?? ''}
                        onBlur={(e) => void salvar({ traits: { ...t, with_strangers: e.target.value || null } })} />
            )}
          </Field>
          <Field label="Expressões comuns" hint="Uma por linha. São as que ela usa de verdade.">
            {({ id }) => (
              <TextArea id={id} rows={3} defaultValue={(t.common_phrases ?? []).join('\n')}
                        onBlur={(e) => void salvar({
                          traits: { ...t, common_phrases: linhasParaLista(e.target.value) } })} />
            )}
          </Field>
          <Field label="Expressões proibidas" hint="Uma por linha. O que esta persona NUNCA escreveria.">
            {({ id }) => (
              <TextArea id={id} rows={3} defaultValue={(t.forbidden_phrases ?? []).join('\n')}
                        onBlur={(e) => void salvar({
                          traits: { ...t, forbidden_phrases: linhasParaLista(e.target.value) } })} />
            )}
          </Field>
          <Field label="Exemplos" hint="Uma mensagem por linha, escrita como ela escreveria.">
            {({ id }) => (
              <TextArea id={id} rows={4} defaultValue={(t.examples ?? []).join('\n')}
                        onBlur={(e) => void salvar({ traits: { ...t, examples: linhasParaLista(e.target.value) } })} />
            )}
          </Field>
          <Field label="Instruções da persona" hint="Vai direto ao modelo, junto com a memória do perfil.">
            {({ id }) => (
              <TextArea id={id} rows={4} defaultValue={persona.persona_prompt ?? ''}
                        onBlur={(e) => void salvar({ persona_prompt: e.target.value })} />
            )}
          </Field>
          <Field label="Aparência" hint="Identidade visual: fica guardada, mas NÃO vai ao modelo que escreve.">
            {({ id }) => (
              <TextArea id={id} rows={3} defaultValue={t.appearance ?? ''}
                        onBlur={(e) => void salvar({ traits: { ...t, appearance: e.target.value || null } })} />
            )}
          </Field>
          <Field label="Estilo visual" hint="Roupas e acessórios típicos. Também não vai ao modelo.">
            {({ id }) => (
              <TextArea id={id} rows={2} defaultValue={t.visual_style ?? ''}
                        onBlur={(e) => void salvar({ traits: { ...t, visual_style: e.target.value || null } })} />
            )}
          </Field>
          <Field label="Cenário da foto de perfil" hint="Como a foto é composta. Também não vai ao modelo.">
            {({ id }) => (
              <TextArea id={id} rows={2} defaultValue={t.photo_scenario ?? ''}
                        onBlur={(e) => void salvar({ traits: { ...t, photo_scenario: e.target.value || null } })} />
            )}
          </Field>
            </div>
          ) : null}
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Testar persona" subtitle="Mostra como ela escreveria. Nada é publicado." />
        <CardBody>
          {/* Painel lateral/recolhível: aberto por padrão, mas pode ser fechado sem perder o cartão de identidade
              acima. `<details>` mantém o conteúdo acessível a teclado e leitor de tela sem JS extra. */}
          <details open>
            <summary className={styles.disclosure}>
              <ChevronRight size={14} aria-hidden />
              Prévia de escrita
            </summary>
            <div className={styles.form} style={{ marginTop: 'var(--sp-3)' }}>
              <Field label="O que testar">
                {({ id }) => (
                  <Select id={id} value={tipo} onChange={(e) => setTipo(e.target.value as typeof tipo)}>
                    <option value="dm_reply">Responder uma mensagem</option>
                    <option value="dm_initiate">Puxar conversa (mensagem direta)</option>
                    <option value="post_comment">Comentar uma publicação</option>
                  </Select>
                )}
              </Field>
              {tipo === 'dm_reply' ? (
                <Field label="Mensagem recebida">
                  {({ id }) => <TextArea id={id} rows={3} value={recebido} onChange={(e) => setRecebido(e.target.value)} />}
                </Field>
              ) : (
                <Field label="Intenção" hint="A mesma que o comando daria. Ex.: cumprimentar, dizer boa tarde.">
                  {({ id }) => <TextArea id={id} rows={3} value={intencao} onChange={(e) => setIntencao(e.target.value)} />}
                </Field>
              )}
              <Button icon={Sparkles} loading={testando}
                      disabledReason={(tipo === 'dm_reply' ? recebido : intencao).trim() ? null
                        : tipo === 'dm_reply' ? 'Escreva a mensagem que a persona receberia.'
                          : 'Escreva a intenção deste texto.'}
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
            </div>
          </details>
        </CardBody>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------- aparelho
function AbaAparelho({ profile, onChanged }: { profile: InstagramProfile; onChanged: () => Promise<void> }) {
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
    <>
    <Localidade profile={profile} onChanged={onChanged} />
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
    </>
  );
}

/**
 * Onde os dados deste perfil VIVEM (E9, item 4.4) e o que fazer quando aquele servidor não responde.
 *
 * A sessão do Instagram mora na partição de dados do aparelho, no disco de UMA máquina: o pedido do dono diz que
 * "perfil armazenado num servidor NÃO está automaticamente disponível em outro". Até aqui a tela mostrava só o
 * `instance_id`, e um perfil cujo servidor tinha mudado aparecia igual aos demais.
 */
function Localidade({ profile, onChanged }: { profile: InstagramProfile; onChanged: () => Promise<void> }) {
  const loc = profile.locality;
  const [salvando, setSalvando] = useState(false);
  if (!loc) return null;

  async function mudarPolitica(valor: OfflinePolicy) {
    setSalvando(true);
    try {
      await api.patchProfile(profile.id, { offline_policy: valor });
      // Recarrega ANTES do toast: o select é controlado por `profile.offline_policy`, e sem isto ele voltaria
      // visualmente ao valor antigo depois de um PATCH que funcionou — um controle que parece não ter efeito.
      await onChanged();
      toast({
        tone: 'info',
        title: 'Política de localidade atualizada',
        message: valor === 'wait'
          ? 'Este perfil espera o servidor onde os dados vivem voltar.'
          : 'Este perfil pode entrar na conta de novo em outro servidor, quando for usado lá.',
      });
    } catch (e) {
      toastError('Não foi possível mudar a política', e);
    } finally {
      setSalvando(false);
    }
  }

  return (
    <Card>
      <CardHeader title="Onde este perfil vive"
                  subtitle="Os dados da sessão ficam no disco de uma máquina; mudar de servidor exige entrar na conta de novo." />
      <CardBody>
        <dl className={styles.rows}>
          <Linha rotulo="Servidor">
            {loc.worker_name ?? loc.worker_id ?? 'este servidor'}
            {loc.moved ? <> <Badge tone="warning">mudou de servidor</Badge></> : null}
            {!loc.available ? <> <Badge tone="warning">indisponível</Badge></> : null}
            {!loc.known ? <> <Badge>localidade não registrada</Badge></> : null}
          </Linha>
          {loc.physical_id ? <Linha rotulo="Aparelho físico">{loc.physical_id}</Linha> : null}
          <Linha rotulo="Se o servidor estiver fora">
            <Select value={profile.offline_policy} disabled={salvando}
                    aria-label="O que fazer quando o servidor deste perfil não responde"
                    onChange={(e) => void mudarPolitica(e.target.value as OfflinePolicy)}>
              <option value="wait">Esperar aquele servidor voltar</option>
              <option value="reauth_elsewhere">Permitir entrar na conta de novo em outro servidor</option>
            </Select>
          </Linha>
        </dl>
        {loc.detail ? <p className={styles.detail}>{loc.detail}</p> : null}
      </CardBody>
    </Card>
  );
}

// ---------------------------------------------------------------- autenticação
function AbaAutenticacao({ profile, onChanged }: { profile: InstagramProfile; onChanged: () => Promise<void> }) {
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
    <div className={styles.stack}>
      <CartaoSenha profile={profile} onChanged={onChanged} />
      <Card>
        <CardHeader
          title="Autenticação"
          subtitle="A senha só passa pelo canal seguro; ela não aparece aqui, nem no log, nem em evidência."
          actions={
            <div className={styles.actions}>
              <Button size="sm" icon={PlugZap} loading={busy}
                      disabledReason={profile.credential.configured
                        ? null : 'Guarde a senha deste perfil (acima) antes de conectar.'}
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
    </div>
  );
}

/** Guarda ou troca a senha do perfil. Só escrita: o campo nasce vazio e esvazia depois de cada envio, dê certo ou
 *  não — a senha passa por aqui a caminho do cofre e não volta, nem para este formulário. */
function CartaoSenha({ profile, onChanged }: { profile: InstagramProfile; onChanged: () => Promise<void> }) {
  const [senha, setSenha] = useState('');
  const [salvando, setSalvando] = useState(false);
  const cred = profile.credential;

  async function salvar() {
    // O botão bloqueia o clique, mas Enter no campo envia o formulário mesmo assim: a guarda fica aqui também.
    if (!senha || salvando) return;
    setSalvando(true);
    try {
      await api.setCredential(profile.id, { password: senha });
      toast({ tone: 'success', title: cred.configured ? 'Senha trocada' : 'Senha guardada',
              message: 'Cifrada no cofre. Ela não volta mais para o portal.' });
      await onChanged();
    } catch (e) {
      toastError('Não foi possível guardar a senha', e);
    } finally {
      setSenha('');
      setSalvando(false);
    }
  }

  return (
    <Card>
      <CardHeader
        title="Senha do Instagram"
        subtitle={cred.configured
          ? 'Guardada cifrada e nunca exibida. Para trocar, digite a nova.'
          : 'Ainda não cadastrada: sem ela este perfil não conecta.'} />
      <CardBody>
        {cred.status === 'invalid' ? (
          <p className={styles.detail}>
            O Instagram recusou a senha guardada. A autenticação automática fica parada até ela ser trocada aqui.
          </p>
        ) : null}
        <form className={styles.form} onSubmit={(e) => { e.preventDefault(); void salvar(); }}>
          <Field label={cred.configured ? 'Nova senha' : 'Senha'}
                 hint="Vai cifrada para o cofre e nunca é devolvida. Nem o painel nem a IA veem o valor.">
            {({ id, describedBy }) => (
              <TextInput id={id} type="password" autoComplete="new-password" aria-describedby={describedBy}
                         value={senha} onChange={(e) => setSenha(e.target.value)} />
            )}
          </Field>
          <div>
            <Button size="sm" type="submit" variant="primary" icon={KeyRound} loading={salvando}
                    disabledReason={senha ? null : 'Digite a senha primeiro.'}>
              {cred.configured ? 'Trocar senha' : 'Guardar senha'}
            </Button>
          </div>
        </form>
      </CardBody>
    </Card>
  );
}

// ---------------------------------------------------------------- memória
/** Agrupa por assunto (a pessoa/tema) e ordena por importância × recência — os fatos que mais importam e
 *  os mais frescos primeiro, tanto dentro de cada cartão quanto na ordem dos cartões. */
function agruparMemoriaPorAssunto(itens: MemoryItem[]): { assunto: string; itens: MemoryItem[] }[] {
  const porAssunto = new Map<string, MemoryItem[]>();
  for (const m of itens) {
    const lista = porAssunto.get(m.subject) ?? [];
    lista.push(m);
    porAssunto.set(m.subject, lista);
  }
  const recencia = (m: MemoryItem) => Date.parse(m.last_used_at ?? m.updated_at ?? m.created_at) || 0;
  const grupos = [...porAssunto.entries()].map(([assunto, lista]) => ({
    assunto,
    itens: [...lista].sort((a, b) => b.importance - a.importance || recencia(b) - recencia(a)),
  }));
  grupos.sort((a, b) => {
    const [topoA] = a.itens;
    const [topoB] = b.itens;
    return (topoB?.importance ?? 0) - (topoA?.importance ?? 0)
      || (topoB ? recencia(topoB) : 0) - (topoA ? recencia(topoA) : 0);
  });
  return grupos;
}

/** De onde veio cada fato: a tela vista não tem o mesmo peso do que a pessoa disse ao perfil. */
const ORIGEM_DA_MEMORIA: Record<string, { label: string; tone: 'info' | 'success' | 'neutral' }> = {
  observation: { label: 'visto na tela', tone: 'info' },
  interaction: { label: 'dito pela pessoa', tone: 'success' },
  operator: { label: 'ensinado', tone: 'neutral' },
  system: { label: 'sistema', tone: 'neutral' },
};

function AbaMemoria({ profile, appId = null }: { profile: InstagramProfile; appId?: string | null }) {
  const versao = useVersaoAoVivo(profile);
  const [itens, recarregar] = useLista<MemoryItem>(() => api.listMemory(profile.id, 100, appId), [profile.id, versao, appId]);
  const appsDoStore = useAppStore((st) => st.apps);
  const nomeDoApp = new Map(appsDoStore.map((x) => [x.id, x.name]));
  const [assunto, setAssunto] = useState('');
  const [conteudo, setConteudo] = useState('');
  const [salvando, setSalvando] = useState(false);
  const [ensinando, setEnsinando] = useState(false);
  const now = useNow();

  async function adicionar() {
    setSalvando(true);
    try {
      await api.addMemory(profile.id, { subject: assunto.trim(), content: conteudo.trim(), app_id: appId });
      setAssunto('');
      setConteudo('');
      setEnsinando(false);
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
  const grupos = agruparMemoriaPorAssunto(itens);
  return (
    <Card>
      <CardHeader title="O que este perfil sabe"
                  subtitle="O que ele viu na tela, o que as pessoas disseram a ele e o que foi ensinado aqui. Senha e código nunca entram."
                  actions={
                    <Button size="sm" icon={BrainCircuit} onClick={() => setEnsinando((v) => !v)}>
                      {ensinando ? 'Fechar' : 'Ensinar um fato'}
                    </Button>
                  } />
      <CardBody>
        {ensinando ? (
          <div className={styles.form} style={{ marginBottom: 'var(--sp-4)' }}>
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
            <div>
              <Button loading={salvando}
                      disabledReason={assunto.trim() && conteudo.trim() ? null : 'Preencha o assunto e o fato.'}
                      onClick={() => void adicionar()}>Guardar</Button>
            </div>
          </div>
        ) : null}
        {itens.length === 0 ? (
          <p className={styles.detail}>Nenhuma lembrança ainda.</p>
        ) : (
          <div className={styles.memoryGroups}>
            {grupos.map((g) => (
              <div key={g.assunto} className={styles.memoryCard}>
                <div className={styles.memoryCardHead}>
                  <Avatar name={g.assunto} size={32} />
                  <strong>{g.assunto}</strong>
                </div>
                <ul className={styles.memoryFacts}>
                  {g.itens.map((m) => (
                    <li key={m.id} className={styles.memoryFact}>
                      <div className={styles.memoryFactHead}>
                        <span className={styles.importanceBar} title={`Importância ${m.importance.toFixed(1)}`}>
                          <span className={styles.importanceBarFill} style={{ width: `${Math.round(clamp01(m.importance) * 100)}%` }} />
                        </span>
                        <Badge size="sm" tone={m.app_id ? 'accent' : 'muted'}>
                          {m.app_id ? (nomeDoApp.get(m.app_id) ?? m.app_id) : 'geral'}
                        </Badge>
                        <Badge size="sm" tone={ORIGEM_DA_MEMORIA[m.source]?.tone ?? 'neutral'}>
                          {ORIGEM_DA_MEMORIA[m.source]?.label ?? m.source}
                        </Badge>
                        <Badge size="sm" tone={m.confidence >= 0.7 ? 'success' : m.confidence >= 0.4 ? 'warning' : 'muted'}>
                          confiança {Math.round(m.confidence * 100)}%
                        </Badge>
                        <span className={styles.muted}>
                          visto {m.occurrences}x · usado {m.last_used_at ? formatAgo(m.last_used_at, now) : 'nunca'}
                        </span>
                        <Button size="sm" variant="ghost" icon={Trash2} onClick={() => void esquecer(m)}>Esquecer</Button>
                      </div>
                      <p style={{ margin: 0 }}>{m.content}</p>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        )}
      </CardBody>
    </Card>
  );
}

// ---------------------------------------------------------------- interações
function AbaInteracoes({ profile, appId = null }: { profile: InstagramProfile; appId?: string | null }) {
  const versao = useVersaoAoVivo(profile);
  const [itens] = useLista<SocialInteraction>(() => api.listInteractions(profile.id, 30, appId), [profile.id, versao, appId]);
  const [filtro, setFiltro] = useState<string | null>(null);
  if (itens === null) return <Carregando />;
  if (itens.length === 0) {
    return (
      <EmptyState icon={MessageSquare} title="Sem interações" hint="Aqui fica o que este perfil fez e recebeu.">
        Nada registrado ainda.
      </EmptyState>
    );
  }
  const tipos = [...new Set(itens.map((i) => i.type))];
  const filtrados = filtro ? itens.filter((i) => i.type === filtro) : itens;
  return (
    <Card>
      <CardHeader title="Histórico social" subtitle="O que este perfil fez e recebeu, do mais recente ao mais antigo." />
      <CardBody>
        <TimelineFilter tipos={tipos} ativo={filtro} onChange={setFiltro} />
        {filtrados.length === 0 ? (
          <p className={styles.detail}>Nada deste tipo ainda.</p>
        ) : (
          <InteractionTimeline itens={filtrados} />
        )}
      </CardBody>
    </Card>
  );
}

// ---------------------------------------------------------------- habilidades: o que a persona já fez e o que roda sem IA
const CUSTO_IA: Record<string, { label: string; tone: 'success' | 'warning' | 'danger' | 'neutral' }> = {
  zero: { label: 'roda sem IA', tone: 'success' },
  parcial: { label: 'parte por receita', tone: 'warning' },
  total: { label: 'a IA faz tudo', tone: 'danger' },
  desconhecido: { label: 'cobertura desconhecida', tone: 'neutral' },
};

function AbaHabilidades({ profile }: { profile: InstagramProfile }) {
  const [dados, setDados] = useState<ProfileCapabilities | null>(null);
  const now = useNow();
  const versao = useVersaoAoVivo(profile);
  useEffect(() => {
    let vivo = true;
    api.profileCapabilities(profile.id)
      .then((d) => { if (vivo) setDados(d); })
      .catch((e) => { toastError('Não foi possível carregar', e); if (vivo) setDados({ profile_id: profile.id, flows: [], steps_driven_by: {}, recipe_share: null, interactions: {} }); });
    return () => { vivo = false; };
  }, [profile.id, versao]);
  if (dados === null) return <Carregando />;
  const totalEtapas = Object.values(dados.steps_driven_by).reduce((a, b) => a + b, 0);
  const interacoes = Object.entries(dados.interactions);
  if (dados.flows.length === 0 && totalEtapas === 0 && interacoes.length === 0) {
    return (
      <EmptyState icon={Sparkles} title="Nada mapeado ainda" hint="Cada execução concluída vira um fluxo; cada etapa que a IA resolveu vira receita. Aqui aparece o que este perfil já sabe fazer.">
        Este perfil ainda não concluiu nenhuma execução.
      </EmptyState>
    );
  }
  const maxInteracao = Math.max(1, ...interacoes.map(([, n]) => n));
  const pct = dados.recipe_share === null ? null : Math.round(dados.recipe_share * 100);
  return (
    <div className={styles.grid}>
      <Card>
        <CardHeader title="Caminhos que este perfil já percorreu"
                    subtitle="Cada bolinha é uma etapa: verde tem receita própria; a cor de quem falta muda com o quanto o fluxo ainda depende da IA." />
        <CardBody>
          {dados.flows.length === 0 ? <p className={styles.muted}>Nenhum fluxo concluído.</p> : (
            <div>
              {dados.flows.map((f) => {
                const custo = CUSTO_IA[f.ai_cost] ?? { label: 'cobertura desconhecida', tone: 'neutral' as const };
                const restoTone: 'success' | 'warning' | 'danger' = f.ai_cost === 'total' ? 'danger' : f.ai_cost === 'zero' ? 'success' : 'warning';
                return (
                  <div key={f.flow_id} className={styles.skillTrail}>
                    <div className={styles.skillTrailHead}>
                      <strong>{f.name}</strong>
                      <Badge tone={custo.tone}>{custo.label}</Badge>
                      <span className={styles.muted}>
                        {f.target_version ? `versão ${f.target_version} · ` : ''}{f.times ?? 0}× · último: {f.last_at ? formatAgo(f.last_at, now) : '—'}
                      </span>
                    </div>
                    <div className={styles.skillDots} role="img"
                         aria-label={`${f.steps_with_recipe} de ${f.steps_total} etapas com receita`}>
                      {Array.from({ length: f.steps_total }, (_, i) => (
                        <span key={i} className={styles.skillDot} data-tone={i < f.steps_with_recipe ? 'success' : restoTone} />
                      ))}
                    </div>
                    <div className={styles.detail}>{f.command_template}</div>
                  </div>
                );
              })}
            </div>
          )}
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="Fração que roda sem IA" />
        <CardBody style={{ display: 'flex', gap: 'var(--sp-4)', alignItems: 'center' }}>
          <div className={styles.ring} style={{ background: `conic-gradient(var(--accent) ${pct ?? 0}%, var(--surface-4) 0)` }}>
            <div className={styles.ringInner}>{pct === null ? '—' : `${pct}%`}</div>
          </div>
          <dl className={styles.rows} style={{ flex: 1 }}>
            <Linha rotulo="Por receita (sem IA)">{dados.steps_driven_by.recipe ?? 0}</Linha>
            <Linha rotulo="Receita + IA">{dados.steps_driven_by['recipe+ai'] ?? 0}</Linha>
            <Linha rotulo="Só IA">{dados.steps_driven_by.ai ?? 0}</Linha>
          </dl>
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="Interações confirmadas, por tipo" />
        <CardBody className={styles.form}>
          {interacoes.length === 0 ? <p className={styles.muted}>Nenhuma.</p> : (
            interacoes.map(([tipo, n]) => (
              <ProgressBar key={tipo} value={n / maxInteracao} label={tipo} text={`${tipo}: ${n}`} tone="accent" />
            ))
          )}
        </CardBody>
      </Card>
    </div>
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
  const versao = useVersaoAoVivo(profile);
  const [runs] = useLista<RunSummary>(() => api.listProfileRuns(profile.id), [profile.id, versao]);
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
function AbaConfiguracoes({ profile, onChanged }: { profile: InstagramProfile; onChanged: () => Promise<void> }) {
  const [politica, setPolitica] = useState<ProfilePolicy | null>(null);
  const [acoes, setAcoes] = useState<Capability[]>([]);
  const [interacoes, setInteracoes] = useState<SocialInteraction[]>([]);
  const [grupos, setGrupos] = useState<PolicyGroup[]>([]);
  const [salvando, setSalvando] = useState(false);
  // Mudou a política deste perfil (aqui, em outra aba ou pelo grupo): recarrega sozinha, como as demais abas.
  const versao = useVersaoAoVivo(profile);

  useEffect(() => {
    let vivo = true;
    // O pacote das capacidades vem do REGISTRO de aplicativos (qual app provê a conta deste perfil), e não de
    // um padrão no cliente: com `listCapabilities()` sem argumento, qualquer chamador recebia o catálogo do
    // Instagram como se fosse o do app dele.
    api.listAppCatalog()
      .then(async (apps) => {
        const alvo = apps.find((a) => a.session_provider !== null);
        const [p, c, i, g] = await Promise.all([
          api.getPolicy(profile.id),
          alvo ? api.listCapabilities(alvo.package) : Promise.resolve([] as Capability[]),
          // O medidor precisa do dia inteiro, não só das últimas dezenas — 200 é folga sobre qualquer teto
          // razoável de "por hora" somado ao longo de um dia. Sem interações não há medidor, não tela quebrada.
          api.listInteractions(profile.id, 200).catch(() => [] as SocialInteraction[]),
          api.listPolicyGroups().catch(() => [] as PolicyGroup[]),
        ]);
        if (!vivo) return;
        setPolitica(p);
        setAcoes(c);
        setInteracoes(i);
        setGrupos(g);
      })
      .catch((e) => toastError('Não foi possível carregar as políticas', e));
    return () => {
      vivo = false;
    };
  }, [profile.id, versao]);

  async function salvar(corpo: ProfilePolicyPatch, erro: string) {
    setSalvando(true);
    try {
      setPolitica(await api.setPolicy(profile.id, corpo));
    } catch (e) {
      toastError(erro, e);
    } finally {
      setSalvando(false);
    }
  }

  async function trocarGrupo(groupId: string) {
    setSalvando(true);
    try {
      await api.patchProfile(profile.id, { policy_group_id: groupId || null });
      setPolitica(await api.getPolicy(profile.id));
      await onChanged();
    } catch (e) {
      toastError('Não foi possível trocar o grupo de acesso', e);
    } finally {
      setSalvando(false);
    }
  }

  if (!politica) return <Carregando />;

  const grupoNome = politica.group_name ?? null;
  const doGrupo = politica.group ?? {};
  const efetivo = (c: Capability): PolicyName => politica.capabilities[c.key] ?? c.default_policy;
  const origemDe = (c: Capability): PolicyOrigin =>
    politica.origin?.[c.key] ?? (efetivo(c) !== c.default_policy ? 'own' : 'default');
  const origem = (c: Capability): Origem => {
    const o = origemDe(c);
    if (o === 'own') {
      const sobrepoe = grupoNome && doGrupo[c.key] && doGrupo[c.key] !== efetivo(c);
      return { propria: true, rotulo: sobrepoe ? 'próprio · sobrepõe o grupo' : 'próprio', tone: 'accent' };
    }
    if (o === 'group') return { propria: false, rotulo: `do grupo ${grupoNome ?? ''}`.trim(), tone: 'info' };
    return { propria: false, rotulo: 'padrão', tone: 'muted' };
  };
  const proprias = acoes.filter((c) => origemDe(c) === 'own').map((c) => c.key);
  const limitesProprios = Object.keys(politica.own_limits ?? {});
  const usoDeHoje = contarUsoDeHoje(interacoes);

  return (
    <div className={styles.configStack}>
      <Card>
        <CardHeader title="Grupo de acesso"
                    subtitle="O perfil herda as políticas e os limites do grupo. O que você mudar aqui é deste perfil e sobrepõe o grupo." />
        <CardBody>
          <div className={styles.groupPicker}>
            <Field label="Grupo">
              {({ id }) => (
                <Select id={id} value={politica.group_id ?? ''} disabled={salvando}
                        onChange={(e) => void trocarGrupo(e.target.value)}>
                  <option value="">Sem grupo — só o padrão do catálogo</option>
                  {grupos.map((g) => (
                    <option key={g.id} value={g.id}>{g.name} · {g.members.length} perfil(is)</option>
                  ))}
                </Select>
              )}
            </Field>
            <p className={styles.detail}>
              {proprias.length + limitesProprios.length === 0
                ? 'Nenhuma escolha própria: tudo vem do grupo ou do padrão.'
                : `${proprias.length} ação(ões) e ${limitesProprios.length} limite(s) escolhidos neste perfil${grupoNome ? ' — sobrepõem o grupo' : ''}.`}
            </p>
            {proprias.length + limitesProprios.length ? (
              <Button size="sm" variant="ghost" icon={Undo2} disabled={salvando}
                      onClick={() => void salvar({
                        capabilities: Object.fromEntries(proprias.map((k) => [k, null])),
                        limits: Object.fromEntries(limitesProprios.map((k) => [k, null])),
                      }, 'Não foi possível devolver ao grupo')}>
                {grupoNome ? 'Herdar tudo do grupo' : 'Voltar tudo ao padrão'}
              </Button>
            ) : null}
          </div>
        </CardBody>
      </Card>
      <div className={styles.personaLayout}>
        <Card>
          <CardHeader title="O que este perfil pode fazer"
                      subtitle="Cada ação mostra de onde vem o valor: próprio, do grupo ou padrão. “Herdar” apaga a escolha deste perfil." />
          <CardBody>
            <PolicyActionsEditor
              acoes={acoes} efetivo={efetivo} origem={origem} loosened={politica.loosened ?? []} salvando={salvando}
              herdaria={(c) => doGrupo[c.key] ?? c.default_policy}
              nomeDaHeranca={(c) => (doGrupo[c.key] ? `do grupo ${grupoNome ?? ''}` : 'do padrão do catálogo')}
              onChange={(keys, valor) => void salvar(
                { capabilities: Object.fromEntries(keys.map((k) => [k, valor])) },
                keys.length > 1 ? 'Não foi possível salvar as políticas da categoria' : 'Não foi possível salvar a política')} />
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Limites"
                      subtitle="Existem para o sistema não agir como robô e derrubar a própria conta." />
          <CardBody className={styles.limitGrid}>
            <LimitsEditor
              limites={politica.limits} salvando={salvando}
              origem={(k) => {
                const o = politica.limits_origin?.[k] ?? 'default';
                if (o === 'own') return { propria: true, rotulo: 'próprio', tone: 'accent' };
                if (o === 'group') return { propria: false, rotulo: `do grupo ${grupoNome ?? ''}`.trim(), tone: 'info' };
                return { propria: false, rotulo: 'padrão', tone: 'muted' };
              }}
              herdaria={(k) => politica.group_limits?.[k]}
              uso={(k) => {
                const balde = baldeDoLimite(k);
                return balde ? usoDeHoje[balde] ?? 0 : null;
              }}
              onChange={(k, valor) => void salvar({ limits: { [k]: valor } }, 'Não foi possível salvar o limite')} />
          </CardBody>
        </Card>
      </div>
    </div>
  );
}
