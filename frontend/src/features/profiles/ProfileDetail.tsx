import {
  ArrowLeft, AtSign, BrainCircuit, ChevronRight, ClipboardCheck, KeyRound, ListChecks, MessageSquare,
  PlugZap, ScanEye, Settings2, Smartphone, Sparkles, TriangleAlert, UserRound,
} from 'lucide-react';
import { useEffect, useState } from 'react';
import { api, profileAvatarUrl } from '../../api/client';
import type {
  AuthAttempt, InstagramProfile, Persona, ProfileCapabilities, SocialDraft, SocialInteraction,
} from '../../api/types';
import { Avatar } from '../../components/Avatar';
import { Badge } from '../../components/Badge';
import { Banner } from '../../components/Banner';
import { Button } from '../../components/Button';
import { Card, CardBody, CardHeader } from '../../components/Card';
import { confirm } from '../../components/Confirm';
import { EmptyState } from '../../components/EmptyState';
import { Field, Select, TextArea, TextInput } from '../../components/Field';
import { Skeleton } from '../../components/Skeleton';
import { StatusBadge } from '../../components/StatusBadge';
import { TabPanel, Tabs, type TabDef } from '../../components/Tabs';
import { cx } from '../../lib/format';
import { type LoadError, LoadErrorState, toLoadError } from '../../lib/loadError';
import { PROFILE_STATUS, SESSION_STATUS, metaOf } from '../../lib/status';
import { formatAgo, useNow } from '../../lib/time';
import { toast, toastError } from '../../store/toasts';
import {
  CompletenessGauge, EMOJI_OPTIONS, ExampleBubbles, FORMALITY_OPTIONS, LENGTH_OPTIONS, PairColumns,
  PhraseColumns, ROTULO_DE_VOZ, Ruler, StatFigure, TagList,
} from './PersonaVisual';
import appStyles from '../../App.module.css';
import styles from './Profiles.module.css';
import { InteractionTimeline } from './Timeline';
import { AbaContas, AppSwitcher, useContas } from './ProfileAccounts';
import { SESSION_PHASE_LABEL, sessionGateReason } from './sessionGate';
import { OperationalContextCard } from '../devices/OperationalContextCard';
import { Carregando, Linha, useLista, useVersaoAoVivo } from './detalheComum';
import { AbaAparelho } from './GuiaAparelhos';
import { AbaAprovacoes } from './GuiaAprovacoes';
import { AbaConfiguracoes } from './GuiaConfiguracoes';
import { AbaExecucoes } from './GuiaExecucoes';
import { AbaHabilidades } from './GuiaHabilidades';
import { AbaInteracoes } from './GuiaInteracoes';
import { AbaMemoria } from './GuiaMemoria';

type Aba = 'visao' | 'contas' | 'persona' | 'device' | 'auth' | 'memoria' | 'interacoes' | 'habilidades' | 'aprovacoes' | 'execucoes' | 'config';

/** Tela de um perfil: as nove abas do §29. Cada aba carrega o que precisa quando é aberta, e só então. */
export function ProfileDetail({ profile, onBack, onChanged }: {
  profile: InstagramProfile;
  onBack: () => void;
  onChanged: () => Promise<void>;
}) {
  const [aba, setAba] = useState<Aba>('visao');
  // Item 12.2: o perfil tem contas em vários apps; o filtro de app vale para Memória e Interações.
  const [contas, recarregarContas, erroContas] = useContas(profile.id);
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
          <AbaContas key={novaConta} profile={profile} contas={contas} erro={erroContas} recarregar={recarregarContas}
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
  const [erro, setErro] = useState<LoadError | null>(null);
  // "Tentar de novo" só refaz a leitura: incrementar aqui é o jeito de reexecutar o efeito sem duplicar a lógica.
  const [tentativa, setTentativa] = useState(0);
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
    setErro(null);
    (profile.persona_id ? api.listPersonas() : Promise.resolve([]))
      .then((todas) => {
        if (!vivo) return;
        setPersona(todas.find((p) => p.id === profile.persona_id) ?? null);
      })
      // O erro fica na aba, com "Tentar de novo": antes ia para um toast e o esqueleto ficava para sempre.
      .catch((e) => vivo && setErro(toLoadError(e)))
      .finally(() => vivo && setCarregando(false));
    return () => {
      vivo = false;
    };
  }, [profile.persona_id, tentativa]);

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
  if (erro) return <LoadErrorState what="a persona" error={erro} onRetry={() => setTentativa((t) => t + 1)} />;
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
      {/* Do perfil ao aparelho sem trocar de tela: servidor, tela, apps e sessão, da mesma fonte que o Foco. */}
      {profile.instance_id ? (
        <Card><CardBody>
          <OperationalContextCard profileId={profile.id}
                                  refreshKey={`${profile.session.status}:${profile.session_actions?.phase ?? ''}`} />
        </CardBody></Card>
      ) : null}
      <Card>
        <CardHeader
          title="Autenticação"
          subtitle="A senha só passa pelo canal seguro; ela não aparece aqui, nem no log, nem em evidência."
          actions={
            <div className={styles.actions}>
              <Button size="sm" icon={PlugZap} loading={busy}
                      disabledReason={sessionGateReason(profile, 'connect')}
                      onClick={() => void acao('connect')}>Conectar</Button>
              <Button size="sm" variant="ghost" icon={ScanEye} loading={busy}
                      disabledReason={sessionGateReason(profile, 'verify')}
                      onClick={() => void acao('verify')}>Verificar conta</Button>
              <Button size="sm" variant="ghost" icon={KeyRound} loading={busy}
                      disabledReason={sessionGateReason(profile, 'logout')}
                      onClick={() => void acao('logout')}>Sair da conta</Button>
            </div>
          } />
        <CardBody>
          {profile.session_actions ? (
            <p className={styles.detail}>
              <Badge tone={SESSION_PHASE_LABEL[profile.session_actions.phase].tone}>
                {SESSION_PHASE_LABEL[profile.session_actions.phase].label}
              </Badge>{' '}{profile.session_actions.detail}
            </p>
          ) : null}
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

