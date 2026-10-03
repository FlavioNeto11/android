import { Check, FlaskConical, Hand, ListChecks, RefreshCw, SearchX, Smartphone, X } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import appStyles from '../../App.module.css';
import { api } from '../../api/client';
import type { RunSummary } from '../../api/types';
import { BarraListagem } from '../../components/BarraListagem';
import { Button } from '../../components/Button';
import { Card, CardHeader } from '../../components/Card';
import { EmptyState } from '../../components/EmptyState';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { StatusBadge } from '../../components/StatusBadge';
import { TruncatedText } from '../../components/TruncatedText';
import { conteudoAoTopo } from '../../lib/scroll';
import { RUN_STATUS, metaOf } from '../../lib/status';
import { tempoRelativo, formatDateTime, useNow } from '../../lib/time';
import { useAppStore } from '../../store/app';
import { toastError } from '../../store/toasts';
import { useUiStore } from '../../store/ui';
import {
  contagemPorGrupo, filtrarExecucoes, filtroAtivo, filtroLocalAtivo, GRUPOS_STATUS, lerFiltroExecucoes, LIMPAR_FILTROS,
  DICA_GRUPO, PERIODOS, ROTULO_GRUPO, ROTULO_PERIODO, tituloCurto, unirExecucoes,
} from './filtroExecucoes';
import styles from './Runs.module.css';
import { RunView } from './RunView';
import { SeloDeOrigem, origemDaExecucao } from './SeloDeProva';

/** Página do servidor na navegação normal. */
const HISTORY_LIMIT = 50;
/** Página do servidor quando a busca precisa do histórico inteiro (o teto da API é 200). */
const HISTORY_LIMIT_BUSCA = 200;
/** Quantas execuções a lista desenha por vez: o histórico passa de 240, e desenhar tudo pesava a coluna. */
const PAGINA_LISTA = 50;

export function RunsPage() {
  const hydrated = useAppStore((s) => s.hydrated);
  const hydrateCount = useAppStore((s) => s.hydrateCount);
  const runs = useAppStore((s) => s.runs);
  const mergeRuns = useAppStore((s) => s.mergeRuns);
  const instances = useAppStore((s) => s.instances);
  // Só para dar NOME ao servidor no filtro: a execução guarda o worker_id, e id cru não diz qual máquina é.
  const workers = useAppStore((s) => s.workers);
  const selectedRunId = useUiStore((s) => s.selectedRunId);
  const selectRun = useUiStore((s) => s.selectRun);
  const abrirExecucao = useUiStore((s) => s.abrirExecucao);
  const setView = useUiStore((s) => s.setView);
  const rota = useUiStore((s) => s.rota);
  const trocarQuery = useUiStore((s) => s.trocarQuery);
  const agora = useNow();
  const [loading, setLoading] = useState(false);
  // Busca e filtros vêm do link (`#/execucoes/<id>?q=…&status=pendencia&periodo=7d&aparelho=…&servidor=…`):
  // recarregar mostra o mesmo recorte. Gravar substitui a entrada (digitar não empilha uma por tecla).
  const filtro = lerFiltroExecucoes(rota.tela === 'execucoes' ? rota.query : {});
  // Filtro por ONDE rodou (aparelho, servidor) a API aplica. Com ele ligado a lista deixa de ser "o que o store
  // tem" e passa a ser a resposta do servidor: o store recebe execuções ao vivo pelo WebSocket, e misturá-las com
  // um recorte mentiria sobre o filtro.
  const filtrandoNoServidor = !!(filtro.aparelho || filtro.servidor);
  // Busca, status e período a TELA aplica: para achar uma execução antiga, ela precisa do histórico inteiro.
  const filtrandoAqui = filtroLocalAtivo(filtro);
  // As páginas que a tela buscou, guardadas aqui: o store só segura as 100 mais recentes (ver `unirExecucoes`).
  const [paginadas, setPaginadas] = useState<RunSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [carregadas, setCarregadas] = useState(0);
  const [mostrar, setMostrar] = useState(PAGINA_LISTA);
  // Uma falha ao completar o histórico para a busca não vira laço de pedidos: fica parada até a pessoa atualizar.
  const falhouCompletar = useRef(false);

  // Servidores conhecidos pelo parque: a lista sai dos aparelhos, sem uma chamada só para isso.
  const servidores = useMemo(
    () => Array.from(new Set(Object.values(instances).map((i) => i.worker_id).filter((w): w is string => !!w))).sort(),
    [instances],
  );

  // O snapshot traz só as ativas + 20 recentes; aqui buscamos um histórico um pouco maior — e, do segundo
  // "Mostrar mais" em diante, as páginas seguintes. Sem isto as execuções antigas não tinham caminho nenhum.
  const loadHistory = useCallback(async (opts: { mais?: boolean } = {}) => {
    setLoading(true);
    try {
      const offset = opts.mais ? carregadas : 0;
      const limite = filtrandoAqui ? HISTORY_LIMIT_BUSCA : HISTORY_LIMIT;
      const page = await api.listRuns(limite, offset, filtro.aparelho || undefined, filtro.servidor || undefined);
      const lista = Array.isArray(page?.runs) ? page.runs : [];
      mergeRuns(lista);                               // o detalhe de qualquer execução da página fica disponível
      setTotal(typeof page?.total === 'number' ? page.total : lista.length);
      setCarregadas(offset + lista.length);
      setPaginadas((prev) => (opts.mais ? [...prev, ...lista] : lista));
      if (!opts.mais) falhouCompletar.current = false;
    } catch (e) {
      falhouCompletar.current = true;
      toastError('Não foi possível atualizar a lista de execuções', e, { key: 'runs-history' });
    } finally {
      setLoading(false);
    }
  }, [mergeRuns, carregadas, filtro.aparelho, filtro.servidor, filtrandoAqui]);

  useEffect(() => {
    if (hydrateCount > 0) void loadHistory();
    // Trocar o filtro do servidor recomeça a paginação do zero, de propósito.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hydrateCount, filtro.aparelho, filtro.servidor]);

  const temMais = carregadas < total;

  // Com busca, status ou período ligados, traz o resto do histórico (246 execuções = duas páginas de 200).
  useEffect(() => {
    if (filtrandoAqui && temMais && !loading && carregadas > 0 && !falhouCompletar.current) void loadHistory({ mais: true });
  }, [filtrandoAqui, temMais, loading, carregadas, loadHistory]);

  const base = useMemo(() => (filtrandoNoServidor ? paginadas : unirExecucoes(runs, paginadas)),
    [filtrandoNoServidor, paginadas, runs]);
  const lista = useMemo(() => filtrarExecucoes(base, filtro, agora),
    // `filtro` é recriado a cada render; as chaves dele bastam.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [base, filtro.q, filtro.status, filtro.periodo, agora]);
  const visiveis = lista.slice(0, mostrar);
  // As contagens dos chips só aparecem com o histórico inteiro na mão: contar só a página carregada mentiria.
  const contagem = !temMais ? contagemPorGrupo(base, filtro, agora) : null;
  const completando = filtrandoAqui && temMais;

  // Trocar filtro ou busca volta ao começo da lista.
  const chaveFiltro = `${filtro.q}|${filtro.status}|${filtro.periodo}|${filtro.aparelho}|${filtro.servidor}`;
  useEffect(() => { setMostrar(PAGINA_LISTA); }, [chaveFiltro]);

  // Abre a mais recente quando nada está selecionado (substitui o link, não empilha: não foi a pessoa que escolheu).
  useEffect(() => {
    if (!selectedRunId && visiveis.length > 0 && visiveis[0]) selectRun(visiveis[0].id);
  }, [selectedRunId, visiveis, selectRun]);

  // Execução nova, leitura do começo: sem isto, escolher outra com a página rolada abria no meio do relatório.
  useEffect(() => {
    conteudoAoTopo();
  }, [selectedRunId]);

  const restantesNaTela = lista.length - visiveis.length;
  // Sem filtro local, o que falta pode ainda estar no servidor.
  const restantes = filtrandoAqui ? restantesNaTela : Math.max(total, base.length) - visiveis.length;
  function mostrarMais() {
    if (restantesNaTela <= 0 && temMais && !filtrandoAqui) void loadHistory({ mais: true });
    setMostrar((m) => m + PAGINA_LISTA);
  }
  const mudar = (parcial: Record<string, string | undefined>) => trocarQuery(parcial, 'replace');
  const algumFiltro = filtroAtivo(filtro);
  const totalConhecido = Math.max(total, base.length);

  return (
    <div className={appStyles.page}>
      <div className={appStyles.pageHeader}>
        <div>
          <h1 className={appStyles.pageTitle}>Execuções</h1>
          <p className={appStyles.pageLead}>Histórico recente e detalhes completos de cada execução: plano, progresso por aparelho, linha do tempo, evidências e relatório.</p>
        </div>
      </div>

      <div className={styles.runsLayout}>
        <Card className={styles.runListCard} aria-label="Lista de execuções">
          <CardHeader
            title="Recentes"
            subtitle={hydrated
              ? completando ? 'Procurando no histórico inteiro…'
                : `${lista.length} de ${totalConhecido} ${totalConhecido === 1 ? 'execução' : 'execuções'}`
              : undefined}
            actions={<Button size="sm" variant="ghost" icon={RefreshCw} iconOnly label="Atualizar lista" loading={loading} onClick={() => void loadHistory()} />}
          />
          {/* Achado #176: de uma execução não se descobria onde ela rodou, e antigas não tinham como ser achadas.
              Tarefa UX 05: busca no objetivo, situação e período, na mesma barra das outras listas. */}
          <div className={styles.runBarra}>
            <BarraListagem
              nome="execuções"
              compacta
              busca={{ valor: filtro.q, onChange: (q) => mudar({ q: q || undefined }), placeholder: 'Buscar no objetivo ou código' }}
              filtros={[
                { chave: 'status', rotulo: 'Situação da execução', tipo: 'chips', rotuloTodos: 'Todas',
                  contagemTodos: contagem?.todas, valor: filtro.status ?? '', onChange: (v) => mudar({ status: v || undefined }),
                  opcoes: GRUPOS_STATUS.map((g) => ({ valor: g, rotulo: ROTULO_GRUPO[g], contagem: contagem?.[g], dica: DICA_GRUPO[g] })) },
                { chave: 'periodo', rotulo: 'Filtrar por período', tipo: 'lista', rotuloTodos: 'Qualquer data',
                  valor: filtro.periodo ?? '', onChange: (v) => mudar({ periodo: v || undefined }),
                  opcoes: PERIODOS.map((p) => ({ valor: p, rotulo: ROTULO_PERIODO[p] })) },
                { chave: 'aparelho', rotulo: 'Filtrar por aparelho', tipo: 'lista', rotuloTodos: 'Todos os aparelhos',
                  valor: filtro.aparelho, onChange: (v) => mudar({ aparelho: v || undefined }),
                  opcoes: Object.keys(instances).sort().map((id) => ({ valor: id, rotulo: id })) },
                { chave: 'servidor', rotulo: 'Filtrar por servidor', tipo: 'lista', rotuloTodos: 'Todos os servidores',
                  valor: filtro.servidor, onChange: (v) => mudar({ servidor: v || undefined }),
                  opcoes: servidores.map((w) => ({ valor: w, rotulo: workers[w]?.name ?? w })) },
              ]}
              onLimpar={algumFiltro ? () => mudar(LIMPAR_FILTROS) : undefined}
            />
          </div>
          {!hydrated ? (
            <LoadingRegion label="Carregando execuções…" className={styles.runList}>
              {Array.from({ length: 5 }, (_, i) => <Skeleton key={i} height={74} radius={8} />)}
            </LoadingRegion>
          ) : lista.length === 0 ? (
            completando ? (
              <LoadingRegion label="Procurando no histórico inteiro…" className={styles.runList}>
                {Array.from({ length: 3 }, (_, i) => <Skeleton key={i} height={60} radius={8} />)}
              </LoadingRegion>
            ) : (
              <EmptyState
                icon={algumFiltro ? SearchX : ListChecks}
                compact
                title={algumFiltro ? 'Nenhuma execução com esse filtro' : 'Nenhuma execução ainda'}
                hint={algumFiltro ? 'Nenhuma execução registrada casa com a busca e os filtros escolhidos.'
                                  : 'Crie a primeira pelo campo de comando do Painel.'}
                actions={algumFiltro
                  ? <Button variant="outline" onClick={() => mudar(LIMPAR_FILTROS)}>Limpar filtros</Button>
                  : <Button variant="outline" onClick={() => setView('painel')}>Ir para o Painel</Button>}
              />
            )
          ) : (
            <ul className={styles.runList}>
              {visiveis.map((r) => (
                <li key={r.id}>
                  <RunItem run={r} current={r.id === selectedRunId} onSelect={() => abrirExecucao(r.id)} />
                </li>
              ))}
              {restantes > 0 ? (
                <li className={styles.runListMais}>
                  <Button size="sm" variant="outline" block loading={loading} onClick={mostrarMais}>
                    Mostrar mais ({restantes} {restantes === 1 ? 'restante' : 'restantes'})
                  </Button>
                </li>
              ) : null}
            </ul>
          )}
        </Card>

        <RunView />
      </div>
    </div>
  );
}

function Age({ ts }: { ts: string }) {
  const now = useNow();
  return <>{tempoRelativo(ts, now)}</>;
}

function RunItem({ run, current, onSelect }: { run: RunSummary; current: boolean; onSelect: () => void }) {
  const c = run.counts;
  const blocked = (c?.waiting_user ?? 0) + (c?.uncertain ?? 0);
  // Título curto (tarefa UX 05): o que a execução faz, sem a abertura repetida ("No QA Messenger, …"); o app vai à
  // parte e o objetivo inteiro fica no `title` e no detalhe.
  const { titulo, app } = tituloCurto(run.command);
  const origem = origemDaExecucao(run);
  return (
    <button type="button" className={styles.runItem} aria-current={current ? 'true' : undefined} onClick={onSelect}
            title={run.command}>
      <span className={styles.runItemCmd}>{titulo}</span>
      <span className={styles.runItemTop}>
        <StatusBadge meta={metaOf(RUN_STATUS, run.status)} size="sm" className={styles.runItemBadge} />
        <span className={styles.runItemAge} title={formatDateTime(run.created_at)}><Age ts={run.created_at} /></span>
      </span>
      <span className={styles.runItemMeta}>
        {origem ? <SeloDeOrigem origem={origem} /> : null}
        {app ? <TruncatedText className={styles.runItemApp}>{app}</TruncatedText> : null}
        <span className={styles.shortId}>{run.short_id}</span>
        <span><Smartphone size={11} aria-hidden /> {run.instances_used}/{run.instances_requested}</span>
        {c ? (
          <>
            <span className={c.succeeded > 0 ? styles.miniOk : undefined}><Check size={11} aria-hidden /> {c.succeeded}<span className="sr-only"> com sucesso</span></span>
            <span className={c.failed > 0 ? styles.miniBad : undefined}><X size={11} aria-hidden /> {c.failed}<span className="sr-only"> com falha</span></span>
            <span className={blocked > 0 ? styles.miniWarn : undefined}><Hand size={11} aria-hidden /> {blocked}<span className="sr-only"> bloqueados ou incertos</span></span>
          </>
        ) : null}
        {run.simulated ? <span className={styles.miniWarn}><FlaskConical size={11} aria-hidden /> simulado</span> : null}
      </span>
    </button>
  );
}
