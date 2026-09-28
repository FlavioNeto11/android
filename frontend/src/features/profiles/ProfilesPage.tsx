import { AtSign, Hand, PenLine, Server, ShieldAlert, ShieldCheck, Smartphone, Trash2, UserRound, Wand2 } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, profileAvatarUrl } from '../../api/client';
import type { Instance, InstagramProfile, PersonaDTO, PolicyGroup, Worker } from '../../api/types';
import { Avatar } from '../../components/Avatar';
import { Badge } from '../../components/Badge';
import { Button } from '../../components/Button';
import { Card, CardBody, CardHeader } from '../../components/Card';
import { confirm } from '../../components/Confirm';
import { EmptyState } from '../../components/EmptyState';
import { AutoGrid, Page } from '../../components/Page';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { StatusBadge } from '../../components/StatusBadge';
import { serverHintOf } from '../devices/deviceState';
import { ServerBadge } from '../devices/ServerBadge';
import { toastError, toast } from '../../store/toasts';
import { type LoadError, LoadErrorBanner, LoadErrorState, toLoadError } from '../../lib/loadError';
import { conteudoAoTopo } from '../../lib/scroll';
import { formatAgoCoarse, useNow } from '../../lib/time';
import { ACCOUNT_SESSION_STATUS, PROFILE_STATUS, metaOf } from '../../lib/status';
import { useAppStore } from '../../store/app';
import { useControlStore } from '../../store/control';
import { useUiStore } from '../../store/ui';
import { NovaPersonaManual, NovaPersonaPorPrompt } from './NovaPersona';
import { PolicyGroupsSection } from './PolicyGroups';
import { abaDoPedido, type Aba } from './abas';
import { ProfileDetail } from './ProfileDetail';
import { handleDe, nomeDe, resumoDe } from './pessoa';
import { SESSION_PHASE_LABEL } from './sessionGate';
import styles from './Profiles.module.css';

/** Estados de sessão que só uma pessoa resolve — mesmo conjunto do backend (achado #106). */
const PRECISA_DE_PESSOA = new Set(['auth_challenge', 'wrong_account', 'needs_person']);

/** Quem tem conta de cadastro, no formato que a fila e os grupos de acesso sempre leram (`username` presente). */
function comConta(pessoas: PersonaDTO[]): InstagramProfile[] {
  return pessoas.flatMap((p) => (p.username ? [{ ...p, username: p.username }] : []));
}

/**
 * Personas (`#/perfis`, rota mantida): as PESSOAS, com e sem conta (`GET /personas`). Cada uma tem identidade,
 * voz, biografia, fotos e as contas dela em cada app; conta, senha e aparelho se ajustam DENTRO da persona.
 */
export function ProfilesPage() {
  const hydrated = useAppStore((s) => s.hydrated);
  const hydrateCount = useAppStore((s) => s.hydrateCount);
  // Achado #106: `session.needs_person` não traz o perfil inteiro (só existe por REST) — a batida basta para
  // saber que a fila "Aguardando intervenção" pode ter mudado e recarregar.
  const needsPersonEpoch = useAppStore((s) => s.needsPersonEpoch);
  const instancesMap = useAppStore((s) => s.instances);
  const liveWorkers = useAppStore((s) => s.workers);
  const personaRequest = useUiStore((s) => s.personaRequest);
  const consumePersonaRequest = useUiStore((s) => s.consumePersonaRequest);
  const [aberto, setAberto] = useState<{ id: string; aba: Aba; nonce: number } | null>(null);
  const [pessoas, setPessoas] = useState<PersonaDTO[] | null>(null);
  const [erro, setErro] = useState<LoadError | null>(null);
  const [grupos, setGrupos] = useState<PolicyGroup[]>([]);
  const [criando, setCriando] = useState<'prompt' | 'manual' | null>(null);
  const token = useRef(0);

  const load = useCallback(async () => {
    const mine = ++token.current;
    const [p, grp] = await Promise.allSettled([api.listPersonas(), api.listPolicyGroups()]);
    if (mine !== token.current) return;
    if (grp.status === 'fulfilled') setGrupos(grp.value);
    if (p.status === 'fulfilled') {
      setPessoas(p.value);
      setErro(null);
    } else {
      // O erro fica na tela, com "Tentar de novo": `[]` aqui dizia "Nenhuma persona" com a API caída, e só um
      // toast passageiro contava a verdade (P1.3).
      setErro(toLoadError(p.reason));
    }
  }, []);

  // Recarrega a cada novo snapshot (reconexão) e a cada mudança na fila "Aguardando intervenção" — perfis não
  // vêm no snapshot, e o evento dedicado só carrega o bastante para saber que algo mudou (achado #106).
  useEffect(() => {
    void load();
  }, [load, hydrateCount, needsPersonEpoch]);

  // Pedido de outra tela ("Abrir persona" no Foco): abre a pessoa (e a guia, se dita) e consome o pedido para ele
  // não reabrir sozinho quando a pessoa voltar à lista. O `nonce` remonta o detalhe se a mesma pessoa for pedida
  // de novo noutra guia.
  useEffect(() => {
    if (!personaRequest) return;
    setAberto({ id: personaRequest.id, aba: abaDoPedido(personaRequest.tab), nonce: personaRequest.nonce });
    consumePersonaRequest();
  }, [personaRequest, consumePersonaRequest]);

  // Abrir uma persona e voltar troca o conteúdo sem trocar de seção: sem voltar ao topo, a lista reaparecia rolada.
  useEffect(() => {
    conteudoAoTopo();
  }, [aberto?.id]);

  const emFoco = aberto ? (pessoas ?? []).find((p) => p.id === aberto.id) : undefined;
  if (aberto && emFoco) {
    return <ProfileDetail key={`${emFoco.id}:${aberto.nonce}`} profile={emFoco} abaInicial={aberto.aba}
                          onBack={() => setAberto(null)} onChanged={load} />;
  }

  if (pessoas === null && erro) {
    return <LoadErrorState what="as personas" error={erro} onRetry={() => void load()} />;
  }
  if (!hydrated || pessoas === null) {
    return (
      <LoadingRegion label="Carregando personas…">
        <Skeleton height={140} />
      </LoadingRegion>
    );
  }

  const contas = comConta(pessoas);
  const botoesDeCadastro = (
    <>
      <Button icon={Wand2} variant="primary" onClick={() => setCriando('prompt')}>Nova persona a partir de um prompt</Button>
      <Button icon={PenLine} onClick={() => setCriando('manual')}>Nova persona manual</Button>
    </>
  );

  async function criada(p: PersonaDTO) {
    setCriando(null);
    await load();
    setAberto({ id: p.id, aba: 'visao', nonce: Date.now() });
  }

  return (
    <Page
      title="Personas"
      lead={'Cada persona é uma pessoa: identidade, voz, biografia, fotos e as contas dela em cada app (Instagram, '
        + 'Outlook, TikTok…). A senha de cada conta é digitada dentro da persona e nunca volta: o painel só mostra '
        + 'que existe.'}
      actions={botoesDeCadastro}
    >
      {erro ? <LoadErrorBanner error={erro} onRetry={() => void load()} /> : null}

      <InterventionQueue profiles={contas} instances={instancesMap} workers={liveWorkers} />

      <PolicyGroupsSection grupos={grupos} profiles={contas} onChanged={load} />

      {pessoas.length === 0 ? (
        <EmptyState
          icon={UserRound}
          title="Nenhuma persona cadastrada"
          hint="Descreva a pessoa num prompt (a IA propõe um rascunho para você revisar) ou crie à mão. Contas e aparelho vêm depois."
          actions={botoesDeCadastro}
        >
          Nenhuma persona foi cadastrada ainda.
        </EmptyState>
      ) : (
        <AutoGrid min="320px">
          {pessoas.map((p) => (
            <PersonaCard key={p.id} pessoa={p} onChanged={load}
                         onOpen={() => setAberto({ id: p.id, aba: 'visao', nonce: Date.now() })} />
          ))}
        </AutoGrid>
      )}

      {criando === 'prompt' ? <NovaPersonaPorPrompt onClose={() => setCriando(null)} onCriada={criada} /> : null}
      {criando === 'manual' ? <NovaPersonaManual onClose={() => setCriando(null)} onCriada={criada} /> : null}
    </Page>
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
            const sess = metaOf(ACCOUNT_SESSION_STATUS, p.session.status);
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
                  disabledReason={!p.instance_id ? 'Sem aparelho vinculado a esta persona.' : null}
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

/**
 * O cartão é a PESSOA: nome, @ (se houver), idade/cidade/profissão, quantas contas, o aparelho e a situação. Senha,
 * sessão e Conectar moram na guia Contas e acesso de cada conta — o cartão antigo misturava conta, aparelho e
 * credencial num lugar só, e uma pessoa sem conta nem aparecia.
 */
function PersonaCard({ pessoa, onChanged, onOpen }: {
  pessoa: PersonaDTO;
  onChanged: () => Promise<void>;
  onOpen: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const nome = nomeDe(pessoa);
  const handle = handleDe(pessoa);
  const resumo = resumoDe(pessoa);
  const loc = pessoa.locality;
  const fase = pessoa.session_actions ? SESSION_PHASE_LABEL[pessoa.session_actions.phase] : null;

  async function remover() {
    // `confirm` devolve um OBJETO, que é sempre verdadeiro: testar o objeto faria "Voltar" apagar a persona e a
    // credencial do mesmo jeito. Quem decide é `confirmed`.
    const { confirmed } = await confirm({
      title: `Remover ${nome}?`,
      body: 'É apagar a pessoa: as contas e as senhas guardadas no cofre vão junto, com memória, fotos e histórico. '
        + 'Persona vinculada a aparelho ou com execução em andamento não sai.',
      confirmLabel: 'Remover',
      danger: true,
    });
    if (!confirmed) return;
    setBusy(true);
    try {
      await api.deletePersona(pessoa.id);
      toast({ tone: 'success', title: `${nome} removida` });
      await onChanged();
    } catch (e) {
      toastError('Não foi possível remover a persona', e);
    } finally {
      setBusy(false);
    }
  }

  async function mudarStatus(status: 'active' | 'blocked') {
    setBusy(true);
    try {
      await api.patchProfile(pessoa.id, { status });
      toast({
        tone: 'success',
        title: status === 'blocked' ? `${nome} marcada como bloqueada` : `${nome} reativada`,
        message: status === 'blocked' ? 'Nenhuma tarefa será despachada para esta persona.' : 'A persona volta a receber tarefas.',
      });
      await onChanged();
    } catch (e) {
      toastError('Não foi possível mudar a situação da persona', e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader
        title={
          <span className={styles.identidade}>
            <Avatar src={profileAvatarUrl(pessoa.id)} name={nome} size={40} />
            <span className={styles.identidadeNome}>{nome}</span>
          </span>
        }
        subtitle={handle ? `@${handle}` : 'sem conta de cadastro'}
        actions={
          <div className={styles.actions}>
            {/* Nome no rótulo: a lista tem um "Abrir" por pessoa, e o leitor de tela precisa distinguir. */}
            <Button size="sm" variant="ghost" onClick={onOpen} aria-label={`Abrir ${nome}`}>Abrir</Button>
            {/* Conta bloqueada pela plataforma: registrar aqui é o que tira a persona do despacho. Reativar é
                decisão de pessoa, depois de a conta voltar de verdade. */}
            <Button size="sm" variant="ghost" loading={busy}
                    onClick={() => void mudarStatus(pessoa.status === 'active' ? 'blocked' : 'active')}>
              {pessoa.status === 'active' ? 'Marcar bloqueada' : 'Reativar'}
            </Button>
            <Button size="sm" variant="dangerGhost" icon={Trash2} iconOnly label="Remover persona"
                    loading={busy} onClick={remover} />
          </div>
        }
      />
      <CardBody>
        {resumo.length ? <p className={styles.cardResumo}>{resumo.join(' · ')}</p> : null}
        <dl className={styles.rows}>
          <div className={styles.row}>
            <dt><AtSign size={14} aria-hidden /> Contas</dt>
            <dd>{pessoa.accounts_count ?? 0}</dd>
          </div>
          <div className={styles.row}>
            <dt><Smartphone size={14} aria-hidden /> Aparelho</dt>
            <dd>{pessoa.instance_id ?? <span className={styles.muted}>não vinculado</span>}</dd>
          </div>
          {/* Onde os DADOS vivem (E9). "Perfil armazenado num servidor não está automaticamente disponível em
              outro": sem esta linha, uma persona cujo servidor está fora aparecia igual às demais. */}
          {loc ? (
            <div className={styles.row}>
              <dt><Server size={14} aria-hidden /> Servidor</dt>
              <dd>
                {loc.worker_name ?? loc.worker_id ?? 'este servidor'}
                {loc.moved ? <> <Badge tone="warning">mudou de servidor</Badge></> : null}
                {!loc.available ? <> <Badge tone="warning">indisponível</Badge></> : null}
                {!loc.known ? <> <Badge tone="neutral">localidade não registrada</Badge></> : null}
              </dd>
            </div>
          ) : null}
          <div className={styles.row}>
            <dt>Situação</dt>
            <dd className={styles.badgeRow}>
              <StatusBadge meta={metaOf(PROFILE_STATUS, pessoa.status)} />
              {fase ? <Badge tone={fase.tone}>{fase.label}</Badge> : null}
            </dd>
          </div>
          <div className={styles.row}>
            <dt><ShieldCheck size={14} aria-hidden /> Grupo de acesso</dt>
            <dd>{pessoa.policy_group_name
              ? <Badge tone="info">{pessoa.policy_group_name}</Badge>
              : <span className={styles.muted}>nenhum — padrão do catálogo</span>}</dd>
          </div>
        </dl>
        {loc?.detail && (loc.moved || !loc.available) ? <p className={styles.detail}>{loc.detail}</p> : null}
      </CardBody>
    </Card>
  );
}
