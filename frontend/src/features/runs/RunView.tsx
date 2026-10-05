import {
  Ban, CircleHelp, Clock, FileText, FlaskConical, GitBranch, Image as ImageIcon, ListChecks, ListTree, MessageSquareQuote,
  Pause, Pencil, Play, Repeat, RotateCcw, ServerCrash, Smartphone, Sparkles, X, type LucideIcon,
} from 'lucide-react';
import { useEffect, useMemo, useState, type ReactNode } from 'react';
import type { RetryFailedResponse, RunDetail, RunSummary } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Banner } from '../../components/Banner';
import { Button } from '../../components/Button';
import { Card } from '../../components/Card';
import { EmptyState } from '../../components/EmptyState';
import { Select } from '../../components/Field';
import { StackedBar } from '../../components/ProgressBar';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { StatusBadge } from '../../components/StatusBadge';
import { TabPanel, Tabs, type TabDef } from '../../components/Tabs';
import { toneClass } from '../../components/tone';
import { cx, formatPercent, plural, truncate } from '../../lib/format';
import { OBJECTIVE_STATUS, RUN_STATUS, isRunSemTrabalho, isRunTerminal, metaOf, type StatusMeta } from '../../lib/status';
import { formatDateTime, formatDuration, useNow } from '../../lib/time';
import { useAppStore } from '../../store/app';
import { loadRunDetail, reconnectNow } from '../../store/live';
import { useUiStore } from '../../store/ui';
import { DecisionsTab } from './DecisionsTab';
import { EvidenceTab } from './EvidenceTab';
import { InstancesTab } from './InstancesTab';
import { nomeDe } from '../profiles/pessoa';
import { usePersonas } from '../profiles/usePersonas';
import {
  EMPTY_COUNTS, countSegments, isBlocked, objectivesTotal, perguntaSensivelDosEventos, perguntasDosEventos,
  type PerguntaDaExecucao,
} from './model';
import { PlanTab } from './PlanTab';
import { PortaDoPlano, ValidadeDoPlano } from './PortaDoPlano';
import { ReportTab } from './ReportTab';
import { RespostaSensivel } from './RespostaSensivel';
import { ResumoDaExecucao } from './ResumoDaExecucao';
import { abaPadraoDaExecucao, efeitosRepetidos, type AbaDaExecucao } from './resumo';
import { AssistenteDoComando } from '../command/AssistenteDoComando';
import { repeatRun, responderExecucao, retryFailed, runAction } from './runActions';
import styles from './Runs.module.css';
import { RunUsageCard } from './RunUsageCard';
import { TextsTab, useRunApprovals } from './TextsTab';
import { TimelineTab } from './TimelineTab';
import { NOME_DA_IA } from '../../lib/identidade';

/** O campo de cada pergunta, em português (os de destino vêm do roteamento por persona, ADR-044). */
const CAMPO_DA_PERGUNTA: Record<string, string> = { profile_id: 'persona', instance_id: 'aparelho', persona_data: 'dado da persona' };

type TabId = AbaDaExecucao;

/** A guia padrão por situação (`resumo.ts`): Relatório se concluída, Linha do tempo se em andamento. */
const defaultTab = abaPadraoDaExecucao;

/** Nome da guia no link (`#/execucoes/<id>?aba=linha-do-tempo`): português, estável, independente do id interno. */
const ABA_NA_URL: Record<TabId, string> = {
  plano: 'plano', instancias: 'aparelhos', textos: 'textos', timeline: 'linha-do-tempo', evidencias: 'evidencias',
  decisoes: 'decisoes', relatorio: 'relatorio',
};

function abaDaUrl(v: string | undefined): TabId | null {
  const achada = (Object.keys(ABA_NA_URL) as TabId[]).find((k) => ABA_NA_URL[k] === v);
  return achada ?? null;
}

interface RunViewProps {
  /** Mostra o seletor de execuções recentes no cabeçalho (usado no Painel). */
  showPicker?: boolean;
}

export function RunView({ showPicker }: RunViewProps) {
  const hydrated = useAppStore((s) => s.hydrated);
  const connStatus = useAppStore((s) => s.conn.status);
  const runs = useAppStore((s) => s.runs);
  const detail = useAppStore((s) => s.detail);
  const selectedRunId = useUiStore((s) => s.selectedRunId);
  const selectRun = useUiStore((s) => s.selectRun);

  const summary = useMemo(() => runs.find((r) => r.id === selectedRunId) ?? null, [runs, selectedRunId]);
  const data = detail && detail.runId === selectedRunId ? detail.data : null;
  const run: RunSummary | null = data ?? summary;

  const picker = showPicker ? <RunPicker runs={runs} selectedId={selectedRunId} onSelect={selectRun} /> : null;

  if (!hydrated) {
    return (
      <Card id="execucao" aria-label="Execução">
        {connStatus === 'connecting' ? (
          <LoadingRegion label="Carregando execuções…" className={styles.header}>
            <Skeleton width={220} height={18} />
            <Skeleton width="70%" height={16} />
            <Skeleton height={10} radius={99} />
          </LoadingRegion>
        ) : (
          <EmptyState icon={ServerCrash} tone="danger" title="Execuções indisponíveis" hint="Assim que o backend responder, as execuções ativas e recentes aparecem aqui." actions={<Button variant="outline" onClick={reconnectNow}>Tentar agora</Button>}>
            Sem conexão com o backend.
          </EmptyState>
        )}
      </Card>
    );
  }

  if (!selectedRunId) {
    return (
      <Card id="execucao" aria-label="Execução">
        <EmptyState
          icon={Sparkles}
          title="Nenhuma execução selecionada"
          hint={runs.length > 0 ? 'Escolha uma execução recente abaixo ou crie uma nova pelo campo de comando.' : 'Selecione aparelhos, escreva um comando e clique em Planejar ou Executar.'}
          actions={picker}
        >
          O plano, o progresso por aparelho, as evidências e o relatório aparecem aqui.
        </EmptyState>
      </Card>
    );
  }

  if (!run) {
    const failed = detail?.runId === selectedRunId && detail.status === 'error';
    return (
      <Card id="execucao" aria-label="Execução">
        {failed ? (
          <EmptyState
            icon={ServerCrash}
            tone="danger"
            title="Não foi possível carregar esta execução"
            hint={detail?.error?.hint}
            actions={
              <>
                <Button variant="outline" icon={RotateCcw} onClick={() => void loadRunDetail(selectedRunId)}>Tentar de novo</Button>
                <Button variant="ghost" onClick={() => selectRun(null)}>Limpar seleção</Button>
              </>
            }
          >
            {detail?.error?.message}
          </EmptyState>
        ) : (
          <LoadingRegion label="Carregando a execução…" className={styles.header}>
            <Skeleton width={220} height={18} />
            <Skeleton width="70%" height={16} />
            <Skeleton height={10} radius={99} />
          </LoadingRegion>
        )}
      </Card>
    );
  }

  return <RunBody key={run.id} run={run} data={data} loading={!data && detail?.status !== 'error'} picker={picker} />;
}

function RunPicker({ runs, selectedId, onSelect }: { runs: RunSummary[]; selectedId: string | null; onSelect: (id: string | null) => void }) {
  if (runs.length === 0) return null;
  return (
    <Select
      small
      className={styles.runPicker}
      aria-label="Escolher execução"
      value={selectedId ?? ''}
      onChange={(e) => onSelect(e.target.value || null)}
    >
      <option value="">Escolher execução…</option>
      {runs.slice(0, 30).map((r) => (
        <option key={r.id} value={r.id}>
          {r.short_id} · {metaOf(RUN_STATUS, r.status).label} · {truncate(r.command, 48)}
        </option>
      ))}
    </Select>
  );
}

function Elapsed({ run }: { run: RunSummary }) {
  const now = useNow();
  if (!run.started_at) return <>ainda não iniciada</>;
  return <>{formatDuration(run.started_at, run.finished_at, now)}</>;
}

interface RunBodyProps {
  run: RunSummary;
  data: RunDetail | null;
  loading: boolean;
  picker: ReactNode;
}

function RunBody({ run, data, loading, picker }: RunBodyProps) {
  // Na tela Execuções a guia vive no link (`?aba=`); no Painel a mesma visão não mexe na URL da tela.
  const naTelaExecucoes = useUiStore((s) => s.view === 'execucoes');
  const abaUrl = useUiStore((s) => (s.view === 'execucoes' ? s.rota.query.aba : undefined));
  const trocarQuery = useUiStore((s) => s.trocarQuery);
  const [tab, setTabLocal] = useState<TabId>(() => abaDaUrl(abaUrl) ?? defaultTab(run.status));
  // Link colado ou Voltar/Avançar com outra guia: a visão acompanha.
  // Sem `?aba=` no link (Voltar até o link sem guia), vale a guia padrão da situação: a do link manda quando existe.
  // Só a mudança do link troca a guia aqui; a mudança de situação não (quem olhava a linha do tempo não é arrancado).
  useEffect(() => {
    setTabLocal(abaDaUrl(abaUrl) ?? defaultTab(run.status));
  }, [abaUrl]);
  /** Troca de guia: substitui o link (sem empilhar). A guia padrão do estado da execução não entra no link. */
  const setTab = (t: TabId) => {
    setTabLocal(t);
    if (naTelaExecucoes) trocarQuery({ aba: t === defaultTab(run.status) ? undefined : ABA_NA_URL[t] });
  };
  const [busy, setBusy] = useState<string | null>(null);
  // 29.60: as etapas com efeito repetido (29.58) sobem para o resumo, com o número de cópias e quem contou.
  const repetidos = useMemo(() => (data ? efeitosRepetidos(data.steps) : []).map((s) => ({
    chave: s.id, titulo: s.title, aparelho: s.instance_id,
    copias: s.result?.efeito_repetido?.copias ?? 0, fonte: s.result?.efeito_repetido?.fonte ?? 'acoes',
  })), [data]);
  const selectRun = useUiStore((st) => st.selectRun);
  const [retryResult, setRetryResult] = useState<RetryFailedResponse | null>(null);
  const requestCommandDraft = useUiStore((s) => s.requestCommandDraft);
  const setView = useUiStore((s) => s.setView);
  const events = useAppStore((s) => (s.detail?.runId === run.id ? s.detail.events : null));
  const eventsStatus = useAppStore((s) => (s.detail?.runId === run.id ? s.detail.eventsStatus : 'loading'));

  // Quando o planejamento termina, leva o usuário para o que importa agora.
  const [lastStatus, setLastStatus] = useState(run.status);
  useEffect(() => {
    if (run.status === lastStatus) return;
    if (lastStatus === 'planning' || (lastStatus === 'planned' && run.status === 'running')) setTab(defaultTab(run.status));
    setLastStatus(run.status);
  }, [run.status, lastStatus]);

  const status = metaOf(RUN_STATUS, run.status);
  const counts = run.counts ?? EMPTY_COUNTS;
  const total = Math.max(objectivesTotal(counts), run.instances_used, 0);
  const blockedCount = data ? data.objectives.filter(isBlocked).length : counts.waiting_user + counts.uncertain;
  // Recarrega os rascunhos quando alguém passa a esperar por você: é quando um texto novo aparece.
  const approvals = useRunApprovals(run.id, `${counts.waiting_user}:${run.status}`);
  const pendentes = approvals.itens?.length ?? 0;
  const terminal = isRunTerminal(run.status);

  const act = async (name: string, fn: () => Promise<unknown>) => {
    if (busy) return;
    setBusy(name);
    try {
      await fn();
    } finally {
      setBusy(null);
    }
  };

  const tabs: TabDef<TabId>[] = [
    { id: 'plano', label: 'Plano', icon: ListTree },
    { id: 'instancias', label: 'Por aparelho', icon: Smartphone, count: blockedCount, alert: blockedCount > 0 },
    { id: 'textos', label: 'Textos', icon: MessageSquareQuote, count: pendentes, alert: pendentes > 0 },
    { id: 'timeline', label: 'Linha do tempo', icon: Clock, count: events?.length ?? null },
    { id: 'evidencias', label: 'Evidências', icon: ImageIcon, count: data?.evidence.length ?? null },
    { id: 'decisoes', label: 'Decisões', icon: GitBranch, count: data?.decisions.length ?? null },
    { id: 'relatorio', label: 'Relatório', icon: FileText },
  ];

  const counters: { key: string; label: string; value: number; meta: StatusMeta }[] = [
    { key: 'succeeded', label: 'Sucesso', value: counts.succeeded, meta: OBJECTIVE_STATUS.succeeded },
    { key: 'failed', label: 'Falha', value: counts.failed, meta: OBJECTIVE_STATUS.failed },
    { key: 'waiting_user', label: 'Bloqueio (aguardando usuário)', value: counts.waiting_user, meta: OBJECTIVE_STATUS.waiting_user },
    { key: 'uncertain', label: 'Incerto', value: counts.uncertain, meta: OBJECTIVE_STATUS.uncertain },
    { key: 'cancelled', label: 'Cancelado', value: counts.cancelled, meta: OBJECTIVE_STATUS.cancelled },
    { key: 'running', label: 'Em andamento', value: counts.running, meta: OBJECTIVE_STATUS.running },
  ];
  // "Pendente" só aparece quando existe: evita somar fila de espera em "Em andamento".
  if (counts.pending > 0) counters.push({ key: 'pending', label: 'Pendente', value: counts.pending, meta: OBJECTIVE_STATUS.pending });

  const missing = data?.plan?.missing ?? [];
  // Sem plano (destino ambíguo, habilidade com parâmetro faltando), as perguntas vêm no evento: a mesma lista.
  const perguntas: PerguntaDaExecucao[] = missing.length > 0
    ? missing.map((m) => ({ field: m.field, question: m.question, options: [] }))
    : run.status === 'needs_input' ? perguntasDosEventos(events) : [];
  const deDestino = perguntas.some((q) => q.field === 'profile_id' || q.field === 'instance_id');
  // 31.87: dado que a persona do aparelho não tem. Não se responde aqui (a resposta não vira parâmetro do plano): o
  // caminho é cadastrar o dado na persona e criar a execução de novo, que passa pelo pré-voo outra vez.
  const daPersona = perguntas.some((q) => q.field === 'persona_data');
  // 29.52: a pergunta que pede senha ou código não tem caixa de resposta (o tipo vem do evento do backend).
  const sensivel = run.status === 'needs_input' && !deDestino && !daPersona ? perguntaSensivelDosEventos(events) : null;
  // As opções de persona chegam como ids: o nome só vem da lista de personas, lida só quando há uma pergunta assim.
  const pessoas = usePersonas(perguntas.some((q) => q.field === 'profile_id'));
  const nomeDaOpcao = (id: string) => {
    const p = pessoas?.find((x) => x.id === id);
    return p ? nomeDe(p) : id;
  };
  const idBase = `run-${run.id}`;

  return (
    <Card id="execucao" aria-label={`Execução ${run.short_id}`}>
      <div className={styles.header}>
        <div className={styles.headTop}>
          <div className={styles.headMain}>
            <div className={styles.headLine}>
              <span className={styles.eyebrow}>Execução</span>
              <span className={styles.shortId}>{run.short_id}</span>
              <StatusBadge meta={status} size="lg" srPrefix="Status" />
              {run.simulated ? <Badge tone="warning" solid icon={FlaskConical}>SIMULADO</Badge> : null}
            </div>
            <div className={styles.headMeta}>
              <span><Smartphone size={12} aria-hidden /> {plural(run.instances_requested, 'aparelho solicitado', 'aparelhos solicitados')} · {plural(run.instances_used, 'utilizado', 'utilizados')}</span>
              <span><Clock size={12} aria-hidden /> criada em {formatDateTime(run.created_at)}</span>
              <span>duração: <Elapsed run={run} /></span>
              {run.status_detail ? <span>{run.status_detail}</span> : null}
            </div>
          </div>
          <div className={styles.controls}>
            {picker}
            {run.status === 'planned' ? (
              <Button variant="primary" icon={Play} loading={busy === 'start'} onClick={() => void act('start', () => runAction(run, 'start'))}>
                Iniciar execução
              </Button>
            ) : null}
            <Button
              icon={Pause}
              loading={busy === 'pause'}
              disabledReason={run.status === 'running' ? null : 'Só é possível pausar uma execução em andamento.'}
              onClick={() => void act('pause', () => runAction(run, 'pause'))}
            >
              Pausar
            </Button>
            <Button
              icon={Play}
              loading={busy === 'resume'}
              disabledReason={run.status === 'paused' ? null : 'Só é possível continuar uma execução pausada.'}
              onClick={() => void act('resume', () => runAction(run, 'resume'))}
            >
              Continuar
            </Button>
            <Button
              icon={RotateCcw}
              loading={busy === 'retry'}
              disabledReason={counts.failed > 0 ? null : 'Nenhum objetivo com falha para tentar novamente.'}
              onClick={() => void act('retry', async () => setRetryResult(await retryFailed(run)))}
            >
              Tentar novamente os elegíveis
            </Button>
            <Button
              icon={Repeat}
              loading={busy === 'repeat'}
              disabledReason={isRunSemTrabalho(run.status) ? null : 'Espere esta execução terminar para repeti-la.'}
              onClick={() => void act('repeat', async () => {
                const nova = await repeatRun(run);
                if (nova) selectRun(nova.id);
              })}
            >
              Repetir
            </Button>
            <Button
              variant="dangerGhost"
              icon={Ban}
              loading={busy === 'cancel'}
              disabledReason={terminal ? 'A execução já terminou.' : run.status === 'cancelling' ? 'O cancelamento já está em andamento.' : null}
              onClick={() => void act('cancel', () => runAction(run, 'cancel'))}
            >
              Cancelar
            </Button>
          </div>
        </div>

        <ResumoDaExecucao
          run={run}
          repetidos={repetidos}
          perguntas={perguntas.length}
          sensivel={sensivel}
          bloqueados={blockedCount}
          textosParaAprovar={pendentes}
          terminal={terminal}
          irParaAba={setTab}
        />

        <div className={styles.progressRow}>
          <div className={styles.progressBlock}>
            <div className={styles.progressCaption}>
              <span>Progresso por objetivos</span>
              <span>{counts.succeeded} de {total} com sucesso · {formatPercent((run.progress ?? 0) * 100)}</span>
            </div>
            <StackedBar segments={countSegments(counts)} label="Distribuição dos objetivos por situação" />
          </div>
          <ul className={styles.counts} aria-label="Contadores de objetivos">
            {counters.map((c) => {
              const Icon: LucideIcon = c.meta.icon;
              return (
                <li key={c.key} className={cx(styles.count, toneClass(c.meta.tone), c.value === 0 && styles.countZero)}>
                  <Icon size={13} className={c.meta.spin && c.value > 0 ? 'spin' : undefined} aria-hidden />
                  <span className={styles.countValue}>{c.value}</span>
                  {c.label}
                </li>
              );
            })}
          </ul>
        </div>

        <div className={styles.headBanners}>
          {run.status === 'needs_input' ? (
            <Banner
              tone="warning"
              icon={CircleHelp}
              role="alert"
              title={deDestino ? 'Falta decidir quem faz e onde' : daPersona ? 'Falta um dado da persona' : `${NOME_DA_IA} precisa de mais informações para montar o plano`}
              actions={
                <Button
                  size="sm"
                  icon={Pencil}
                  onClick={() => {
                    requestCommandDraft(run.command);
                    setView('painel');
                  }}
                >
                  Editar comando
                </Button>
              }
            >
              {perguntas.length > 0 && sensivel ? (
                <RespostaSensivel tipo={sensivel} perguntas={perguntas} instanceId={run.instance_ids[0] ?? null} />
              ) : perguntas.length > 0 && !deDestino && !daPersona ? (
                // ADR-047: responder aqui mesmo. A IA junta as respostas ao comando e nasce a execução sucessora —
                // sem voltar ao Comando para reescrever o texto.
                <AssistenteDoComando
                  key={run.id}
                  titulo="Responda aqui: a IA completa o comando"
                  comando={run.command}
                  contexto={{ run_id: run.id }}
                  perguntasIniciais={perguntas.map((q) => ({ field: q.field, question: q.question, options: q.options, why: '' }))}
                  acoes={(texto, pronto) => (
                    <>
                      <Button size="sm" icon={ListChecks} variant={pronto ? 'primary' : undefined}
                              loading={busy === 'responder-plan'} disabled={busy !== null && busy !== 'responder-plan'}
                              disabledReason={texto === run.command.trim() ? 'Responda às perguntas primeiro: o texto ainda é o mesmo.' : null}
                              onClick={() => void act('responder-plan', () => responderExecucao(run, texto, 'plan'))}>
                        Planejar com as respostas
                      </Button>
                      <Button size="sm" icon={Play}
                              loading={busy === 'responder-execute'} disabled={busy !== null && busy !== 'responder-execute'}
                              disabledReason={texto === run.command.trim() ? 'Responda às perguntas primeiro: o texto ainda é o mesmo.' : null}
                              onClick={() => void act('responder-execute', () => responderExecucao(run, texto, 'execute'))}>
                        Executar
                      </Button>
                    </>
                  )}
                />
              ) : perguntas.length > 0 ? (
                <ul className={styles.questionList}>
                  {perguntas.map((m, i) => (
                    <li key={`${m.field}-${i}`} className={styles.question}>
                      <span className={styles.questionField}>{CAMPO_DA_PERGUNTA[m.field] ?? m.field}</span>
                      <span>
                        {m.question}
                        {m.options.length > 0 ? (
                          <span className={styles.questionOptions}>
                            {' '}Opções: {m.options.map((o) => (m.field === 'profile_id' ? nomeDaOpcao(o) : o)).join(', ')}.
                          </span>
                        ) : null}
                      </span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p>{loading ? 'Carregando as perguntas…' : run.status_detail || 'O backend não informou quais dados faltam.'}</p>
              )}
              <p style={{ marginTop: 6 }}>
                {deDestino
                  ? 'Escolha no Comando (modo “Por persona”, ou marcando os aparelhos) e envie de novo — esta execução não avança sozinha.'
                  : daPersona
                    ? 'Cadastre o dado na persona e crie a execução de novo.'
                  : sensivel
                    ? 'Esta execução não avança sozinha: depois disso, edite o comando e peça de novo (ou cancele).'
                    : 'Esta execução não avança sozinha: responda acima (nasce outra, com o comando completo) ou edite o comando.'}
              </p>
            </Banner>
          ) : null}
          {run.status === 'planned' ? (
            <Banner tone="info" icon={ListChecks} title="Plano pronto para revisão" role="status">
              Nada foi executado ainda. Confira as etapas na aba Plano e, abaixo, o que a porta vai fazer com cada ação;
              aprove o que pede o seu aval e inicie (ou “Iniciar execução” para decidir tudo na execução).
            </Banner>
          ) : null}
          {run.status === 'planned' ? <PortaDoPlano runId={run.id} /> : null}
          {!terminal && run.status !== 'planned' && run.status !== 'planning' ? (
            <ValidadeDoPlano runId={run.id} token={`${counts.waiting_user}:${run.status}`} />
          ) : null}
          {blockedCount > 0 && !terminal ? (
            <Banner
              tone="warning"
              icon={OBJECTIVE_STATUS.waiting_user.icon}
              role="status"
              title={`${blockedCount} objetivo(s) precisam de você`}
              actions={<Button size="sm" onClick={() => setTab('instancias')}>Ver bloqueios</Button>}
            >
              Objetivos bloqueados ou incertos não avançam (e nada é reenviado) até você decidir.
            </Banner>
          ) : null}
          {retryResult && retryResult.skipped.length > 0 ? (
            <Banner
              tone="info"
              icon={RotateCcw}
              title={`Nova tentativa: ${retryResult.retried.length} reenfileirado(s), ${retryResult.skipped.length} ignorado(s)`}
              actions={<Button size="sm" variant="ghost" icon={X} iconOnly label="Dispensar" onClick={() => setRetryResult(null)} />}
            >
              <ul className={styles.skippedList}>
                {retryResult.skipped.map((s) => {
                  const inst = data?.objectives.find((o) => o.id === s.objective_id)?.instance_id;
                  return <li key={s.objective_id}><span className="mono">{inst ?? s.objective_id}</span>: {s.reason}</li>;
                })}
              </ul>
            </Banner>
          ) : null}
        </div>

        <RunUsageCard run={run} />
      </div>

      <Tabs tabs={tabs} active={tab} onChange={setTab} idBase={idBase} label="Detalhes da execução" />
      <TabPanel idBase={idBase} id={tab} className={styles.tabBody}>
        {loading && tab !== 'timeline' && tab !== 'relatorio' ? (
          <LoadingRegion label="Carregando detalhes…" className={styles.stack}>
            <Skeleton width="40%" height={16} />
            <Skeleton height={56} radius={8} />
            <Skeleton height={56} radius={8} />
          </LoadingRegion>
        ) : tab === 'plano' ? (
          data ? <PlanTab detail={data} /> : <DetailUnavailable runId={run.id} />
        ) : tab === 'instancias' ? (
          data ? <InstancesTab detail={data} /> : <DetailUnavailable runId={run.id} />
        ) : tab === 'textos' ? (
          data ? <TextsTab detail={data} approvals={approvals} /> : <DetailUnavailable runId={run.id} />
        ) : tab === 'timeline' ? (
          <TimelineTab events={events ?? []} status={eventsStatus} instanceIds={run.instance_ids} runId={run.id} />
        ) : tab === 'evidencias' ? (
          data ? <EvidenceTab detail={data} /> : <DetailUnavailable runId={run.id} />
        ) : tab === 'decisoes' ? (
          data ? <DecisionsTab decisions={data.decisions} /> : <DetailUnavailable runId={run.id} />
        ) : (
          <ReportTab run={run} />
        )}
      </TabPanel>
    </Card>
  );
}

function DetailUnavailable({ runId }: { runId: string }) {
  return (
    <EmptyState
      icon={ServerCrash}
      tone="danger"
      compact
      title="Detalhes indisponíveis"
      hint="O resumo acima continua sendo atualizado em tempo real."
      actions={<Button variant="outline" icon={RotateCcw} onClick={() => void loadRunDetail(runId)}>Carregar de novo</Button>}
    >
      Não foi possível obter os detalhes desta execução no backend.
    </EmptyState>
  );
}
