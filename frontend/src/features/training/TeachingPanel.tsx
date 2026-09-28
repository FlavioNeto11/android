/**
 * Ensino v2 (fase F) dentro da revisão do treino — só com `health.features.skills`. A gravação vira uma CANDIDATA de
 * habilidade versionada (documento `automation/v1alpha1`): o painel mostra o comando, os parâmetros inferidos com
 * tipo e exemplo, as etapas e os riscos, e as PERGUNTAS que o generalizador fez em vez de inventar. Respondidas, a
 * pessoa pede outra candidata; validada, ela vira RASCUNHO de versão. Publicar a habilidade é outra decisão, fora
 * daqui. O "Salvar como fluxo" de sempre continua ao lado, sem ponte entre os dois.
 */
import {
  Ban, CircleCheck, CircleDot, CircleHelp, CircleX, Disc, FileCheck2, FileText, History, LoaderCircle,
  MessageCircleQuestion, RefreshCw, ScanSearch, ServerCrash, ShieldAlert, Sparkles, Trash2, TriangleAlert,
} from 'lucide-react';
import { useEffect, useState } from 'react';
import { api, hintForError, toApiError } from '../../api/client';
import type { SkillCandidate, TeachingQuestion, TeachingSessionView, TeachingStatus, TeachingTurn } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Banner } from '../../components/Banner';
import { Button } from '../../components/Button';
import { confirm } from '../../components/Confirm';
import { EmptyState } from '../../components/EmptyState';
import { TextInput } from '../../components/Field';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { StatusBadge } from '../../components/StatusBadge';
import { metaOf, type StatusMeta } from '../../lib/status';
import { toast, toastError } from '../../store/toasts';
import styles from './Training.module.css';

/**
 * Estados do ensino (`teaching.py::TRANSITIONS`). `published` quer dizer "virou rascunho", nada foi publicado: leva o
 * mesmo tom e ícone do rascunho na lista de Configuração (`FlowsRecipesSection.tsx::SKILL_STATE.draft`).
 */
const TEACHING_STATUS: Record<TeachingStatus, StatusMeta> = {
  open: { label: 'aberto', tone: 'neutral', icon: CircleDot },
  demonstrating: { label: 'gravando', tone: 'info', icon: Disc },
  proposing: { label: 'gerando', tone: 'info', icon: LoaderCircle, spin: true },
  asking: { label: 'com perguntas', tone: 'warning', icon: CircleHelp },
  validating: { label: 'a validar', tone: 'info', icon: ScanSearch },
  ready: { label: 'pronto', tone: 'success', icon: CircleCheck },
  published: { label: 'virou rascunho', tone: 'neutral', icon: FileText },
  discarded: { label: 'descartado', tone: 'muted', icon: Ban },
};

const CANDIDATE_STATUS: Record<SkillCandidate['status'], StatusMeta> = {
  proposed: { label: 'proposta', tone: 'info', icon: CircleDot },
  rejected: { label: 'recusada', tone: 'danger', icon: CircleX },
  accepted: { label: 'aceita', tone: 'success', icon: CircleCheck },
  superseded: { label: 'substituída', tone: 'muted', icon: History },
};

/** Uma ação em voo por vez, mas cada botão só gira pela SUA ação; os outros explicam por que esperam. */
type Acao = 'criar' | 'gerar' | 'salvar' | 'descartar' | `responder:${number}`;

/** Carga inicial: enquanto não se sabe se a gravação já tem ensino, "Gerar" não aparece (criaria um segundo). */
type Carga = { estado: 'carregando' } | { estado: 'pronto' } | { estado: 'erro'; message: string; hint: string };

const TERMINAL: ReadonlySet<TeachingStatus> = new Set<TeachingStatus>(['published', 'discarded']);

function chave(): string {
  return `ensino-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

function titulo(no: SkillCandidate['document']['spec']['nodes'][number]): string {
  return no.goal?.title ?? no.capability ?? no.id;
}

/** O texto da pergunta pode vir nulo (pergunta só com chave): a pessoa precisa ver algo para responder. */
function textoDa(q: TeachingQuestion): string {
  return q.text?.trim() || (q.key ? `Pergunta sem texto (${q.key})` : 'Pergunta sem texto');
}

/** Lista dentro de um Banner: a primeira linha é a frase, as demais (erros do compilador) viram itens. */
function Linhas({ texto }: { texto: string }) {
  const [primeira, ...resto] = texto.split('\n').map((l) => l.trim()).filter(Boolean);
  return (
    <>
      {primeira}
      {resto.length ? <ul className={styles.bannerList}>{resto.map((l, i) => <li key={i} className="mono">{l}</li>)}</ul> : null}
    </>
  );
}

export function TeachingPanel({ trainingSessionId, intent, appId }: { trainingSessionId: string; intent: string; appId: string | null }) {
  const [ensino, setEnsino] = useState<TeachingSessionView | null>(null);
  const [carga, setCarga] = useState<Carga>({ estado: 'carregando' });
  const [tentativa, setTentativa] = useState(0);
  const [ocupado, setOcupado] = useState<Acao | null>(null);
  const [respostas, setRespostas] = useState<Record<number, string>>({});

  useEffect(() => {
    let vivo = true;
    setCarga({ estado: 'carregando' });
    void api.teachingOfRecording(trainingSessionId)
      .then(async (lista) => (lista[0] ? api.getTeaching(lista[0].id) : null))
      .then((v) => {
        if (!vivo) return;
        setEnsino(v);
        setCarga({ estado: 'pronto' });
      })
      .catch((e) => {
        if (!vivo) return;
        const err = toApiError(e);
        setCarga({ estado: 'erro', message: err.message, hint: hintForError(err) });
      });
    return () => { vivo = false; };
  }, [trainingSessionId, tentativa]);

  async function executar(acao: Acao, fazer: () => Promise<TeachingSessionView>, erro: string) {
    if (ocupado) return;
    setOcupado(acao);
    try {
      setEnsino(await fazer());
    } catch (e) {
      toastError(erro, e);
    } finally {
      setOcupado(null);
    }
  }

  const criar = () => executar('criar', async () => {
    const novo = await api.startTeaching({ instruction: intent, app_id: appId });
    await api.attachRecording(novo.id, trainingSessionId);
    return api.proposeCandidate(novo.id, chave());
  }, 'Não foi possível gerar a candidata');

  const responder = (id: string, perguntaId: number) => executar(`responder:${perguntaId}`, async () => {
    const v = await api.answerQuestion(id, perguntaId, (respostas[perguntaId] ?? '').trim());
    setRespostas((r) => ({ ...r, [perguntaId]: '' }));
    return v;
  }, 'Não foi possível registrar a resposta');

  const salvarRascunho = (v: TeachingSessionView, candidata: SkillCandidate) => executar('salvar', async () => {
    const validado = v.status === 'validating' ? await api.validateCandidate(candidata.id) : v;
    // Não compilou: a candidata volta recusada e a nota do compilador aparece no Banner abaixo.
    if (validado.status !== 'ready') return validado;
    const salvo = await api.publishCandidate(candidata.id);
    toast({ tone: 'success', title: 'Rascunho de habilidade criado', message: `${salvo.result_version_id} — publicar é outra decisão.` });
    return salvo;
  }, 'Não foi possível salvar o rascunho');

  async function descartar(v: TeachingSessionView) {
    const { confirmed } = await confirm({
      title: 'Descartar este ensino?',
      danger: true,
      confirmLabel: 'Descartar ensino',
      cancelLabel: 'Cancelar',
      body: 'A candidata e as respostas são descartadas, e descartado é terminal: esta gravação fica ligada ao ensino '
        + 'descartado e não gera outra candidata por aqui. O treinamento gravado e o "Salvar como fluxo" continuam valendo.',
    });
    if (!confirmed) return;
    await executar('descartar', async () => {
      const d = await api.discardTeaching(v.id);
      toast({ tone: 'info', title: 'Ensino descartado' });
      return d;
    }, 'Não foi possível descartar o ensino');
  }

  const candidata = ensino?.current_candidate ?? null;
  const nota: TeachingTurn | undefined = ensino
    ? [...ensino.turns].reverse().find((t) => t.kind === 'note' && t.candidate_id === candidata?.id && t.body)
    : undefined;
  const esperando = (acao: Acao) => (ocupado && ocupado !== acao ? 'Aguarde a ação em andamento.' : null);

  let corpo;
  if (carga.estado === 'carregando') {
    corpo = (
      <LoadingRegion label="Procurando o ensino desta gravação…">
        <Skeleton height={36} radius={8} />
      </LoadingRegion>
    );
  } else if (carga.estado === 'erro') {
    corpo = (
      <EmptyState icon={ServerCrash} tone="danger" compact title="Não foi possível saber se esta gravação já tem ensino"
                  hint={carga.hint}
                  actions={<Button variant="outline" icon={RefreshCw} onClick={() => setTentativa((t) => t + 1)}>Tentar de novo</Button>}>
        {carga.message}
      </EmptyState>
    );
  } else if (!ensino) {
    corpo = (
      <div className={styles.ask}>
        <p>Gera uma candidata de habilidade versionada a partir desta gravação: comando, parâmetros com tipo e exemplo,
          etapas e riscos. O que só você sabe vira pergunta. Uma chamada do modelo do planejador.</p>
        <Button variant="primary" icon={Sparkles} loading={ocupado === 'criar'} onClick={() => void criar()}>Gerar candidata de habilidade</Button>
      </div>
    );
  } else {
    corpo = (
      <>
        <p className={styles.muted}>
          Ensino <StatusBadge size="sm" meta={metaOf(TEACHING_STATUS, ensino.status)} srPrefix="Estado do ensino" />
          {candidata ? <> · candidata {candidata.seq} <StatusBadge size="sm" meta={metaOf(CANDIDATE_STATUS, candidata.status)} srPrefix="Estado da candidata" /></> : null}
        </p>
        {candidata ? (
          <>
            <code className={styles.command}>{candidata.document.spec.invocation.command_template}</code>
            {candidata.annotations.parameters.length ? (
              <p className={styles.params}>
                {candidata.annotations.parameters.map((p) => (
                  <Badge key={p.name} size="sm" tone="accent">{`{${p.name}}`} · {p.type}{p.examples.length ? ` = ${p.examples.join(', ')}` : ''}</Badge>
                ))}
              </p>
            ) : null}
            <ol className={styles.steps} aria-label="Etapas da candidata">
              {candidata.document.spec.nodes.map((n) => (
                <li key={n.id} className={styles.step}>
                  <div className={styles.stepHead}>
                    <span>{titulo(n)}</span>
                    {n.capability ? <Badge size="sm" tone="neutral">{n.capability}</Badge> : null}
                    {n.side_effect || candidata.annotations.effects.some((e) => e.node === n.id)
                      ? <Badge size="sm" tone="warning">efeito externo</Badge> : null}
                  </div>
                </li>
              ))}
            </ol>
            {candidata.annotations.risks.length ? (
              <Banner tone="warning" icon={TriangleAlert} compact title="Riscos">
                <ul className={styles.bannerList} aria-label="Riscos">{candidata.annotations.risks.map((r) => <li key={r}>{r}</li>)}</ul>
              </Banner>
            ) : null}
          </>
        ) : null}
        {nota?.body && candidata?.status === 'rejected' ? (
          <Banner tone="danger" icon={ShieldAlert} compact role="alert" title={`Candidata ${candidata.seq} recusada`}>
            <Linhas texto={nota.body} />
          </Banner>
        ) : null}
        {ensino.errors.length ? (
          <Banner tone="danger" icon={ShieldAlert} compact role="alert" title="A candidata não compila">
            <ul className={styles.bannerList} aria-label="Erros de compilação">{ensino.errors.map((e) => <li key={e} className="mono">{e}</li>)}</ul>
          </Banner>
        ) : null}
        {ensino.open_questions.map((q) => {
          const texto = textoDa(q);
          const acao: Acao = `responder:${q.id}`;
          return (
            <div key={q.id} className={styles.step}>
              <p><MessageCircleQuestion size={14} aria-hidden /> {texto}</p>
              <div className={styles.stepHead}>
                <TextInput aria-label={`Resposta à pergunta: ${texto}`} value={respostas[q.id] ?? ''}
                           onChange={(e) => setRespostas((r) => ({ ...r, [q.id]: e.target.value }))} />
                <Button size="sm" variant="primary" loading={ocupado === acao}
                        disabledReason={esperando(acao) ?? ((respostas[q.id] ?? '').trim() ? null : 'Escreva a resposta (sem senha nem código).')}
                        onClick={() => void responder(ensino.id, q.id)}>Responder</Button>
              </div>
            </div>
          );
        })}
        <div className={styles.actions}>
          {(ensino.status === 'asking' && ensino.open_questions.length === 0) || ensino.status === 'open' ? (
            <Button size="sm" variant="outline" icon={Sparkles} loading={ocupado === 'gerar'} disabledReason={esperando('gerar')}
                    onClick={() => void executar('gerar', () => api.proposeCandidate(ensino.id, chave()), 'Não foi possível gerar a candidata')}>
              {ensino.status === 'asking' ? 'Gerar de novo com as respostas' : 'Pedir outra candidata'}
            </Button>
          ) : null}
          {candidata && (ensino.status === 'validating' || ensino.status === 'ready') ? (
            <Button size="sm" variant="primary" icon={FileCheck2} loading={ocupado === 'salvar'} disabledReason={esperando('salvar')}
                    onClick={() => void salvarRascunho(ensino, candidata)}>
              Salvar como rascunho
            </Button>
          ) : null}
          {!TERMINAL.has(ensino.status) ? (
            <Button size="sm" variant="dangerGhost" icon={Trash2} loading={ocupado === 'descartar'} disabledReason={esperando('descartar')}
                    onClick={() => void descartar(ensino)}>
              Descartar
            </Button>
          ) : null}
        </div>
        {ensino.status === 'published' ? (
          <p>Rascunho <strong>{ensino.result_version_id}</strong> criado. Publicar a habilidade é outra decisão: submeter, validar e publicar a versão em Configuração → Fluxos e receitas → Habilidades.</p>
        ) : null}
        {ensino.status === 'discarded' ? (
          <p className={styles.muted}>Ensino descartado. A gravação continua na revisão e pode virar fluxo pelo "Salvar como fluxo".</p>
        ) : null}
      </>
    );
  }

  return (
    <fieldset className={styles.scope} aria-label="Habilidade versionada">
      <legend>Habilidade versionada (ensino v2)</legend>
      {corpo}
    </fieldset>
  );
}
