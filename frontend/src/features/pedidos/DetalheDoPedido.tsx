import { ArrowLeft, ListChecks, TriangleAlert } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { toApiError } from '../../api/client';
import type { OcorrenciaDTO, PedidoDetalhe } from '../../api/pedidos';
import type { RunSummary } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Banner } from '../../components/Banner';
import { Button } from '../../components/Button';
import { EmptyState } from '../../components/EmptyState';
import { Page } from '../../components/Page';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { StatusBadge } from '../../components/StatusBadge';
import { TabPanel, Tabs, type TabDef } from '../../components/Tabs';
import { prettyJson } from '../../lib/format';
import { RUN_STATUS, metaOf } from '../../lib/status';
import { hashDe } from '../../lib/rotas';
import { formatDateTime, tempoRelativo, useNow } from '../../lib/time';
import { useUiStore } from '../../store/ui';
import { apiPedidos } from './api';
import { AcoesDoPedido } from './AcoesDoPedido';
import {
  META_DA_OCORRENCIA, META_DO_PEDIDO, ROTULO_DA_AUTONOMIA, ROTULO_DA_ORIGEM, ROTULO_DA_SOBREPOSICAO, ROTULO_DO_ENCERRAMENTO,
  formatUsd, mensagemDoErro,
} from './modelo';
import { ProximasDatas } from './PreviaDoPedido';
import { usePedidosStore } from './store';
import styles from './Pedidos.module.css';

type Aba = 'resumo' | 'ocorrencias' | 'execucoes' | 'memoria';
const ABAS: readonly Aba[] = ['resumo', 'ocorrencias', 'execucoes', 'memoria'];
const ehAba = (v: unknown): v is Aba => typeof v === 'string' && (ABAS as readonly string[]).includes(v);
const ID = 'pedido';

/** O detalhe lido do backend; relê a cada evento `pedido.*` e quando uma ação muda o pedido. */
function useDetalhe(id: string) {
  const epoch = usePedidosStore((s) => s.epoch);
  const [pedido, setPedido] = useState<PedidoDetalhe | null>(null);
  const [erro, setErro] = useState<{ nao_existe: boolean; texto: string } | null>(null);
  const [rodada, setRodada] = useState(0);

  useEffect(() => { setPedido(null); setErro(null); }, [id]);
  useEffect(() => {
    const ctrl = new AbortController();
    apiPedidos.detalhe(id, ctrl.signal)
      .then((p) => { setPedido(p); setErro(null); })
      .catch((e) => {
        if (ctrl.signal.aborted) return;
        const err = toApiError(e);
        setErro({ nao_existe: err.status === 404, texto: mensagemDoErro(err) });
      });
    return () => ctrl.abort();
  }, [id, epoch, rodada]);
  return { pedido, erro, reler: useCallback(() => setRodada((n) => n + 1), []) };
}

export function DetalheDoPedido({ id }: { id: string }) {
  const { pedido, erro, reler } = useDetalhe(id);
  const abaDoLink = useUiStore((s) => s.rota.query.aba);
  const trocarQuery = useUiStore((s) => s.trocarQuery);
  const navegar = useUiStore((s) => s.navegar);
  const aba: Aba = ehAba(abaDoLink) ? abaDoLink : 'resumo';
  const voltar = <Button variant="ghost" icon={ArrowLeft} onClick={() => navegar({ tela: 'pedidos' })}>Todos os pedidos</Button>;

  if (!pedido) {
    return (
      <Page title="Pedido" actions={voltar}>
        {erro ? (
          erro.nao_existe
            ? <EmptyState icon={ListChecks} title="Este pedido não existe">O link pode estar velho. Volte à lista.</EmptyState>
            : <Banner tone="warning" icon={TriangleAlert} compact role="status">Não foi possível ler o pedido. {erro.texto}</Banner>
        ) : <LoadingRegion label="Carregando o pedido…"><Skeleton height={80} radius={8} /></LoadingRegion>}
      </Page>
    );
  }

  const tabs: TabDef<Aba>[] = [
    { id: 'resumo', label: 'Resumo' },
    { id: 'ocorrencias', label: 'Ocorrências' },
    { id: 'execucoes', label: 'Execuções' },
    { id: 'memoria', label: 'Memória e relatórios' },
  ];
  return (
    <Page title={pedido.titulo}
          lead={(
            <span className={styles.topo}>
              <StatusBadge meta={META_DO_PEDIDO[pedido.estado]} srPrefix="Estado" />
              <Badge title={ROTULO_DA_AUTONOMIA[pedido.autonomia].dica}>{ROTULO_DA_AUTONOMIA[pedido.autonomia].rotulo}</Badge>
              <span>versão {pedido.versao}</span>
            </span>
          )}
          actions={voltar}>
      <AcoesDoPedido pedido={pedido} onMudou={reler} />
      {pedido.estado === 'pausado' && pedido.pausado_motivo ? <p className={styles.nota}>Pausado: {pedido.pausado_motivo}</p> : null}
      <Tabs tabs={tabs} active={aba} onChange={(a) => trocarQuery({ aba: a === 'resumo' ? undefined : a })} idBase={ID} label="Pedido" />
      <TabPanel idBase={ID} id={aba} className={styles.tabBody}>
        {aba === 'resumo' ? <Resumo p={pedido} /> : null}
        {aba === 'ocorrencias' ? <Ocorrencias p={pedido} /> : null}
        {aba === 'execucoes' ? <Execucoes id={pedido.id} /> : null}
        {aba === 'memoria' ? <Memoria p={pedido} /> : null}
      </TabPanel>
    </Page>
  );
}

function Resumo({ p }: { p: PedidoDetalhe }) {
  const foco = useUiStore((s) => s.focusInstanceId);
  const orc = p.orcamento_total_usd !== null ? `${formatUsd(p.gasto_usd)} de ${formatUsd(p.orcamento_total_usd)}` : `${formatUsd(p.gasto_usd)} (sem orçamento total)`;
  return (
    <>
      <p className={styles.nota}>{p.objetivo}</p>
      {p.contexto ? <p className={styles.nota}>Contexto: {p.contexto}</p> : null}
      {p.criterios_sucesso?.length ? (
        <div><h3 className={styles.dim}>Critérios de sucesso</h3><ul>{p.criterios_sucesso.map((c) => <li key={c}>{c}</li>)}</ul></div>
      ) : <p className={styles.dim}>Sem critérios de sucesso: só prazo, contagem ou orçamento encerram o pedido.</p>}
      <dl className={styles.grade}>
        <div><dt>Quando</dt><dd>{p.gatilhos_resumo?.map((g) => g.descricao).join(' · ') || '—'}</dd></div>
        <div><dt>Personas</dt><dd>{p.personas?.map((x) => x.nome).join(', ') || '—'}</dd></div>
        <div><dt>Fuso</dt><dd>{p.fuso}</dd></div>
        <div><dt>Próxima</dt><dd>{p.proxima_local ?? (p.proxima_em ? formatDateTime(p.proxima_em) : '—')}</dd></div>
        <div><dt>Prazo final</dt><dd>{p.fim_em ? formatDateTime(p.fim_em) : 'sem prazo'}</dd></div>
        <div><dt>Máximo de ocorrências</dt><dd>{p.max_ocorrencias ?? 'sem limite'}</dd></div>
        <div><dt>Gasto</dt><dd>{orc}{p.orcamento_usado !== null ? ` (${Math.round(p.orcamento_usado * 100)}%)` : ''}</dd></div>
        <div><dt>Orçamento por ocorrência</dt><dd>{p.orcamento_ocorrencia_usd !== null ? formatUsd(p.orcamento_ocorrencia_usd) : 'sem teto'}</dd></div>
        <div><dt>Se uma ocorrência ainda roda</dt><dd>{ROTULO_DA_SOBREPOSICAO[p.sobreposicao]}</dd></div>
        <div><dt>Pausa após falhas seguidas</dt><dd>{p.pausa_por_falha}</dd></div>
        {p.encerrado_motivo ? <div><dt>Encerrado porque</dt><dd>{ROTULO_DO_ENCERRAMENTO[p.encerrado_motivo]}</dd></div> : null}
        <div><dt>Criado por</dt><dd>{p.criado_por ?? '—'} em {formatDateTime(p.criado_em)}</dd></div>
      </dl>
      <ProximasDatas datas={p.proximas ?? []} />
      {p.estado === 'aguardando_pessoa' ? (
        <div>
          <h3 className={styles.dim}>O que espera você</h3>
          {p.pendencias?.length ? (
            <ul aria-label="Decisões que esperam você">
              {p.pendencias.map((x) => (
                <li key={`${x.tipo}-${x.ref}`}>
                  {{ aprovacao: 'Aprovação', pergunta: 'Pergunta do planejador', ocorrencia_incerta: 'Ocorrência incerta' }[x.tipo]}
                  {x.run_id ? <> · <a href={hashDe('execucoes', { segmentos: [x.run_id], query: { foco: foco ?? undefined } })}>abrir a execução</a></> : null}
                  <span className={styles.dim}> desde {formatDateTime(x.desde)}</span>
                </li>
              ))}
            </ul>
          ) : <p className={styles.nota}>Nenhuma decisão aberta: já dá para retomar.</p>}
        </div>
      ) : null}
    </>
  );
}

function LinhaDaOcorrencia({ o, agora }: { o: OcorrenciaDTO; agora: number }) {
  const foco = useUiStore((s) => s.focusInstanceId);
  return (
    <li className={styles.linha}>
      <div className={styles.corpo}>
        <div className={styles.topo}>
          <StatusBadge meta={META_DA_OCORRENCIA[o.estado]} size="sm" srPrefix="Ocorrência" />
          <span title={o.previsto_para}>{formatDateTime(o.previsto_para)}</span>
          <Badge size="sm">{ROTULO_DA_ORIGEM[o.origem]}</Badge>
          {o.tentativa > 1 ? <span className={styles.dim}>{o.tentativa}ª tentativa</span> : null}
          {o.terminada_em ? <span className={styles.dim}>terminou {tempoRelativo(o.terminada_em, agora)}</span> : null}
        </div>
        {/* Nada some em silêncio: a pulada, a perdida e a que falhou dizem por quê. */}
        {o.motivo ? <span className={styles.motivo}>Motivo: {o.motivo}</span> : null}
        {o.resumo ? <span className={styles.motivo}>{o.resumo}</span> : null}
        <div className={styles.meta}>
          <span>Custo: {formatUsd(o.custo_usd)}</span>
          {o.run_id && o.run_disponivel ? (
            <a href={hashDe('execucoes', { segmentos: [o.run_id], query: { foco: foco ?? undefined } })}>
              ver a execução{o.run ? ` ${o.run.short_id}` : ''}
            </a>
          ) : o.run_id ? <span className={styles.dim}>A execução foi purgada; o resumo e o custo ficaram aqui.</span> : null}
        </div>
      </div>
    </li>
  );
}

function Ocorrencias({ p }: { p: PedidoDetalhe }) {
  const agora = useNow();
  const [extra, setExtra] = useState<OcorrenciaDTO[]>([]);
  const [fim, setFim] = useState(false);
  const [carregando, setCarregando] = useState(false);
  const [erro, setErro] = useState<string | null>(null);
  const recentes = p.ocorrencias_recentes ?? [];
  const lista = [...recentes, ...extra.filter((e) => !recentes.some((r) => r.id === e.id))];
  const maisAntiga = lista[lista.length - 1]?.previsto_para;

  const maisAntigas = async () => {
    if (!maisAntiga) return;
    setCarregando(true);
    try {
      const r = await apiPedidos.ocorrencias(p.id, { antes_de: maisAntiga, limit: 50 });
      setExtra((e) => [...e, ...(r.items ?? [])]);
      if (!r.proximo) setFim(true);
      setErro(null);
    } catch (e) {
      setErro(mensagemDoErro(toApiError(e)));
    } finally {
      setCarregando(false);
    }
  };

  if (lista.length === 0) return <EmptyState icon={ListChecks} title="Nenhuma ocorrência ainda">Quando uma data vencer, a ocorrência aparece aqui.</EmptyState>;
  return (
    <>
      <ul className={styles.lista} aria-label="Ocorrências">{lista.map((o) => <LinhaDaOcorrencia key={o.id} o={o} agora={agora} />)}</ul>
      {erro ? <Banner tone="warning" icon={TriangleAlert} compact role="status">{erro}</Banner> : null}
      {!fim ? <Button onClick={() => void maisAntigas()} loading={carregando}>Carregar mais antigas</Button> : null}
    </>
  );
}

function Execucoes({ id }: { id: string }) {
  const epoch = usePedidosStore((s) => s.epoch);
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const foco = useUiStore((s) => s.focusInstanceId);
  useEffect(() => {
    const ctrl = new AbortController();
    apiPedidos.execucoes(id, 30, ctrl.signal)
      .then((r) => { setRuns(Array.isArray(r) ? r : []); setErro(null); })
      .catch((e) => { if (!ctrl.signal.aborted) { setErro(mensagemDoErro(toApiError(e))); setRuns((a) => a ?? []); } });
    return () => ctrl.abort();
  }, [id, epoch]);
  if (runs === null) return <LoadingRegion label="Carregando as execuções…"><Skeleton height={56} radius={8} /></LoadingRegion>;
  return (
    <>
      {erro ? <Banner tone="warning" icon={TriangleAlert} compact role="status">Não foi possível ler as execuções. {erro}</Banner> : null}
      {runs.length === 0 ? <EmptyState icon={ListChecks} title="Nenhuma execução ligada">As execuções das ocorrências aparecem aqui.</EmptyState> : (
        <ul className={styles.lista} aria-label="Execuções do pedido">
          {runs.map((r) => (
            <li key={r.id} className={styles.linha}>
              <div className={styles.corpo}>
                <div className={styles.topo}>
                  <StatusBadge meta={metaOf(RUN_STATUS, r.status)} size="sm" srPrefix="Execução" />
                  <span className={styles.dim} title={formatDateTime(r.created_at)}>{formatDateTime(r.created_at)}</span>
                </div>
                <a className={styles.titulo} href={hashDe('execucoes', { segmentos: [r.id], query: { foco: foco ?? undefined } })}>{r.short_id}</a>
              </div>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

/** Memória, relatórios e observações vêm do 28.7: `null` = ainda não existe (não é "vazio"); `[]` = existe e está vazio. */
function Memoria({ p }: { p: PedidoDetalhe }) {
  const bloco = (titulo: string, v: unknown) => (
    <section key={titulo} aria-label={titulo}>
      <h3 className={styles.dim}>{titulo}</h3>
      {v === null || v === undefined ? <p className={styles.nota}>Ainda não disponível.</p>
        : Array.isArray(v) && v.length === 0 ? <p className={styles.nota}>Vazio: nada registrado ainda.</p>
        : <pre className={styles.nota} style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{prettyJson(v)}</pre>}
    </section>
  );
  return <>{bloco('Memória', p.memoria)}{bloco('Relatórios recentes', p.relatorios_recentes)}{bloco('Observações recentes', p.observacoes_recentes)}</>;
}
