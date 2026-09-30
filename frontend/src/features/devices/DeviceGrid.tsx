import { CheckCheck, ChevronDown, ChevronRight, LayoutGrid, List, ServerCrash, Smartphone, X } from 'lucide-react';
import { useCallback, useMemo, useState } from 'react';
import type { Instance, Worker } from '../../api/types';
import { Button } from '../../components/Button';
import { EmptyState } from '../../components/EmptyState';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { plural } from '../../lib/format';
import { isStringArray, loadJson, saveJson } from '../../lib/storage';
import { selectInstanceList, selectTaskOrder, useAppStore } from '../../store/app';
import {
  ORDEM_DOS_ESTADOS, ROTULO_DO_ESTADO, contarSelecao, estadoContado, useContagemDeAparelhos, type EstadoContado,
} from '../../store/metricas';
import { reconnectNow } from '../../store/live';
import { useUiStore } from '../../store/ui';
import { personasPorAparelho } from '../profiles/pessoa';
import { usePersonas } from '../profiles/usePersonas';
import { BarraDeSelecao } from '../painel/BarraDeSelecao';
import { DeviceCard } from './DeviceCard';
import { DeviceList } from './DeviceList';
import { countByServer, serverHintOf } from './deviceState';
import { ehAparelhoParado } from './selos';
import styles from './Devices.module.css';

type Visao = 'cards' | 'lista';

const ehVisao = (v: unknown): v is Visao => v === 'cards' || v === 'lista';

/** Valores de `?estado=` do Painel: os estados de aparelho do resumo (`store/metricas`), inclusive `desconhecido`. */
export function estadoDoFiltro(valor: string | undefined): EstadoContado | null {
  return ORDEM_DOS_ESTADOS.find((e) => e === valor) ?? null;
}

export interface GrupoDeServidor { chave: string; nome: string; ativos: Instance[]; paradas: Instance[] }

/** Agrupa por servidor: o central primeiro, depois os demais por nome. Em cada um, o que roda e o que está parado. */
export function agruparPorServidor(
  instancias: readonly Instance[],
  workers: Readonly<Record<string, Worker>>,
): GrupoDeServidor[] {
  const mapa = new Map<string, GrupoDeServidor>();
  for (const inst of instancias) {
    const server = serverHintOf(inst, workers);
    const chave = server?.id ?? '';
    let g = mapa.get(chave);
    if (!g) {
      g = { chave, nome: server?.name ?? 'Servidor central', ativos: [], paradas: [] };
      mapa.set(chave, g);
    }
    (ehAparelhoParado(inst) ? g.paradas : g.ativos).push(inst);
  }
  return [...mapa.values()].sort((a, b) => (a.chave === '' ? -1 : b.chave === '' ? 1 : a.nome.localeCompare(b.nome, 'pt-BR')));
}

export function DeviceGrid() {
  const hydrated = useAppStore((s) => s.hydrated);
  const connStatus = useAppStore((s) => s.conn.status);
  const connError = useAppStore((s) => s.conn.lastError);
  const instancesMap = useAppStore((s) => s.instances);
  const order = useAppStore((s) => s.instanceOrder);
  const apps = useAppStore((s) => s.apps);
  const workersMap = useAppStore((s) => s.workers);
  const selectedIds = useUiStore((s) => s.selectedIds);
  const focusId = useUiStore((s) => s.focusInstanceId);
  const toggleSelected = useUiStore((s) => s.toggleSelected);
  const selectRange = useUiStore((s) => s.selectRange);
  const setSelection = useUiStore((s) => s.setSelection);
  const clearSelection = useUiStore((s) => s.clearSelection);
  const openFocus = useUiStore((s) => s.openFocus);
  const estadoQuery = useUiStore((s) => s.rota.query.estado);
  const trocarQuery = useUiStore((s) => s.trocarQuery);
  const filtro = estadoDoFiltro(estadoQuery);
  // Cards/Lista e grupos recolhidos: preferência da pessoa, lembrada no navegador (`lib/storage` já protege tudo).
  const [visao, setVisaoEstado] = useState<Visao>(() => loadJson('painel.visao', ehVisao) ?? 'cards');
  const [recolhidos, setRecolhidos] = useState<string[]>(() => loadJson('painel.paradasRecolhidas', isStringArray) ?? []);
  const setVisao = (v: Visao) => { setVisaoEstado(v); saveJson('painel.visao', v); };
  const alternarParadas = (chave: string) => setRecolhidos((atual) => {
    const novo = atual.includes(chave) ? atual.filter((c) => c !== chave) : [...atual, chave];
    saveJson('painel.paradasRecolhidas', novo);
    return novo;
  });

  const instances = useMemo(() => selectInstanceList({ instances: instancesMap, instanceOrder: order }), [instancesMap, order]);
  // Personas não vêm no snapshot nem em eventos: são poucas e mudam devagar, então basta reler a cada snapshot. Cada
  // aparelho recebe as N personas dele (N:N, v0.29), invertendo os `devices[]` de cada uma — uma leitura só.
  const pessoas = usePersonas();
  const porAparelho = useMemo(() => personasPorAparelho(pessoas ?? []), [pessoas]);
  const appNames = useMemo(() => new Map(apps.map((a) => [a.id, a.name])), [apps]);
  const selectedSet = useMemo(() => new Set(selectedIds), [selectedIds]);
  // Fonte única (`store/metricas`): o resumo conta o MESMO conjunto que o botão seleciona — antes o texto dizia
  // "10 paradas" (com a loja) e o clique selecionava 9. A loja e o aparelho de servidor fora do ar ficam à parte.
  const contagem = useContagemDeAparelhos();
  const hibernation = useAppStore((s) => s.health?.features?.hibernation === true);

  // Seleção é escolha de ALVO de comando: a loja aparece na grade, mas nunca é alvo.
  const taskOrder = useMemo(() => selectTaskOrder({ instances: instancesMap, instanceOrder: order }), [instancesMap, order]);
  // 11.5: seleção rápida por servidor, ao lado da seleção por estado — só aparece quando há mais de um servidor
  // em jogo (o caso comum é tudo local, e um botão único ali seria ruído).
  const serverBuckets = useMemo(
    () => countByServer(taskOrder.map((id) => ({ id, worker_id: instancesMap[id]?.worker_id ?? null })), workersMap),
    [taskOrder, instancesMap, workersMap]);

  // `?estado=`: o que a tarefa 02 liga em "N aparelhos em estado desconhecido". A loja nunca entra no filtro: a
  // contagem que levou até aqui (`store/metricas`) também não a conta.
  const visiveis = useMemo(
    () => (filtro ? instances.filter((i) => i.kind !== 'store' && estadoContado(i, workersMap) === filtro) : instances),
    [instances, filtro, workersMap]);
  const grupos = useMemo(() => agruparPorServidor(visiveis, workersMap), [visiveis, workersMap]);

  // RF-01 (revisão final): a ação em lote vale SÓ para o que a pessoa está vendo. O filtro esconde cartões, mas a
  // seleção sobrevive a ele (vem do navegador, dos contadores por estado ou de antes do link filtrado). Em vez de
  // podar a seleção em silêncio, o que está escondido fica marcado, é contado à parte ("N fora do filtro atual") e
  // NUNCA vai para a barra: Iniciar/Parar agem na hora, sem confirmação, e não podem alcançar quem não aparece.
  const tarefaVisivel = useMemo(() => {
    const vistos = new Set(visiveis.map((i) => i.id));
    return taskOrder.filter((id) => vistos.has(id));
  }, [taskOrder, visiveis]);
  const onRange = useCallback((id: string) => selectRange(id, tarefaVisivel), [selectRange, tarefaVisivel]);
  const idsDaAcao = useMemo(() => tarefaVisivel.filter((id) => selectedSet.has(id)), [tarefaVisivel, selectedSet]);
  const alvos = useMemo(() => {
    const daAcao = new Set(idsDaAcao);
    return instances.filter((i) => daAcao.has(i.id));
  }, [instances, idsDaAcao]);

  const { selecionados, total } = contarSelecao(selectedIds, tarefaVisivel);
  const foraDoFiltro = contarSelecao(selectedIds, taskOrder).selecionados - selecionados;
  const allSelected = total > 0 && selecionados === total && foraDoFiltro === 0;
  // A barra existe enquanto houver aparelho marcado, visível ou não: é nela que o aviso do escondido aparece.
  const barraVisivel = hydrated && (selecionados > 0 || foraDoFiltro > 0);
  const limparFiltro = () => trocarQuery({ estado: undefined });

  return (
    <section className={styles.section} aria-labelledby="devices-title">
      <div className={styles.sectionHeader}>
        <h2 id="devices-title" className={styles.sectionTitle}>Aparelhos</h2>
        {hydrated && taskOrder.length > 0 ? (
          <span className={styles.stateSummary} aria-label="Aparelhos por estado">
            {/* 11.5: cada contador seleciona os aparelhos daquele estado (ex.: "3 parados" → liga os três de uma vez),
                em vez de marcar cartão por cartão. A loja nunca entra: seleção é alvo de comando. */}
            {contagem.porEstado.map(({ estado, ids }, i) => {
              const rotulo = ROTULO_DO_ESTADO[estado][ids.length === 1 ? 0 : 1];
              return (
                <span key={estado}>
                  {i > 0 ? ' · ' : ''}
                  <button type="button" className={styles.stateQuick} onClick={() => setSelection(ids)}
                          title={`Selecionar ${plural(ids.length, 'aparelho', 'aparelhos')}: ${rotulo}`}>
                    <b>{ids.length}</b> {rotulo}
                  </button>
                </span>
              );
            })}
            {contagem.loja ? (
              <span title="A loja (Play Store) não recebe tarefa: fica fora da contagem e da seleção.">
                {contagem.porEstado.length > 0 ? ' · ' : ''}
                aparelho-loja {ROTULO_DO_ESTADO[contagem.loja.estado][0]}
              </span>
            ) : null}
          </span>
        ) : null}
        {hydrated && serverBuckets.length > 1 ? (
          <span className={styles.stateSummary} aria-label="Aparelhos por servidor">
            {serverBuckets.map((b, i) => (
              <span key={b.id ?? 'aqui'}>
                {i > 0 ? ' · ' : ''}
                <button type="button" className={styles.stateQuick} onClick={() => setSelection(b.ids)}
                        title={`Selecionar ${b.ids.length} aparelho(s) em ${b.name}`}>
                  <b>{b.ids.length}</b> {b.name}
                </button>
              </span>
            ))}
          </span>
        ) : null}
        {filtro ? (
          <span className={styles.chipFiltro}>
            Filtro: {ROTULO_DO_ESTADO[filtro][1]} ({visiveis.length})
            <Button size="sm" variant="ghost" icon={X} onClick={limparFiltro}>Limpar filtro</Button>
          </span>
        ) : null}
        <span className={styles.sectionHint}>
          <kbd>Ctrl</kbd> + clique alterna · <kbd>Shift</kbd> + clique seleciona um intervalo
        </span>
        <div className={styles.visao} role="group" aria-label="Forma de exibir os aparelhos">
          <button type="button" className={styles.visaoBotao} aria-pressed={visao === 'cards'} onClick={() => setVisao('cards')}>
            <LayoutGrid size={14} aria-hidden /> Cartões
          </button>
          <button type="button" className={styles.visaoBotao} aria-pressed={visao === 'lista'} onClick={() => setVisao('lista')}>
            <List size={14} aria-hidden /> Lista
          </button>
        </div>
        <div className={styles.sectionActions}>
          {/* Com aparelhos marcados, o contador mora na barra de seleção (logo abaixo): dizer duas vezes só confunde. */}
          {!barraVisivel ? (
            <span className={styles.selSummary} aria-live="polite">
              {hydrated ? `0 de ${total} selecionados` : ''}
            </span>
          ) : null}
          {/* Só o que o filtro mostra (e substitui a seleção): nada escondido fica marcado depois deste clique. */}
          <Button size="sm" variant="ghost" icon={CheckCheck} disabled={!hydrated || total === 0 || allSelected}
                  onClick={() => setSelection(tarefaVisivel)}>
            Selecionar todos
          </Button>
          <Button size="sm" variant="ghost" icon={X} disabled={selectedIds.length === 0} onClick={clearSelection}>
            Limpar
          </Button>
        </div>
      </div>

      {/* Presa ao topo da grade, na fila normal: nunca cobre um cartão. Com o Foco aberto mantém o lugar, sem botões. */}
      {barraVisivel ? (
        <BarraDeSelecao
          ids={idsDaAcao}
          selecionados={selecionados}
          foraDoFiltro={foraDoFiltro}
          emFoco={focusId !== null}
          hasAbsent={alvos.some((i) => i.state === 'absent')}
          hasHibernated={alvos.some((i) => i.state === 'hibernated')}
          hibernation={hibernation}
          // Numa seleção mista, o verbo só é oferecido se TODOS aceitarem: era assim que `create` chegava a um
          // aparelho de outra máquina e criava um AVD que nunca seria usado.
          selected={alvos}
        />
      ) : null}

      {!hydrated ? (
        connStatus === 'connecting' ? (
          <LoadingRegion label="Carregando aparelhos…" className={styles.grid}>
            {Array.from({ length: 10 }, (_, i) => (
              <div key={i} className={styles.card} style={{ padding: 10, gap: 10, display: 'flex', flexDirection: 'column' }}>
                <Skeleton width="55%" height={16} />
                <Skeleton height={212} radius={8} />
                <Skeleton width="80%" />
                <Skeleton width="60%" />
                <Skeleton height={6} radius={99} />
              </div>
            ))}
          </LoadingRegion>
        ) : (
          <EmptyState
            icon={ServerCrash}
            tone="danger"
            title="Sem conexão com o servidor"
            hint={<>Inicie o servidor central (em <span className="mono">127.0.0.1:8000</span>) e aguarde: a reconexão é automática.{connError ? ` Último erro: ${connError}` : ''}</>}
            actions={<Button variant="outline" onClick={reconnectNow}>Tentar agora</Button>}
          >
            Ainda não foi possível carregar a lista de aparelhos.
          </EmptyState>
        )
      ) : taskOrder.length === 0 ? (
        <EmptyState
          icon={Smartphone}
          title="Nenhum aparelho cadastrado"
          hint="O servidor deveria listar android-01 … android-10. Abra o Diagnóstico para conferir o SDK e a configuração."
        >
          O servidor respondeu sem nenhum aparelho.
        </EmptyState>
      ) : visiveis.length === 0 ? (
        <EmptyState
          icon={Smartphone}
          title="Nenhum aparelho neste estado"
          hint="O filtro vem do link que você abriu. Limpe-o para ver todos os aparelhos."
          actions={<Button variant="outline" onClick={limparFiltro}>Limpar filtro</Button>}
        >
          Nenhum aparelho está {filtro ? ROTULO_DO_ESTADO[filtro][1] : 'neste estado'} agora.
        </EmptyState>
      ) : visao === 'lista' ? (
        <DeviceList instances={visiveis} appNames={appNames} porAparelho={porAparelho} selectedSet={selectedSet}
                    focusId={focusId} onToggle={toggleSelected} onRange={onRange} onOpen={openFocus} />
      ) : (
        <div className={styles.grupos}>
          {grupos.map((g) => {
            const recolhido = recolhidos.includes(g.chave);
            const cartao = (inst: Instance, compacto: boolean) => (
              <DeviceCard
                key={inst.id}
                instance={inst}
                appName={inst.app_id ? appNames.get(inst.app_id) ?? inst.app_id : null}
                personas={porAparelho.get(inst.id) ?? null}
                selected={selectedSet.has(inst.id)}
                focused={focusId === inst.id}
                compacto={compacto}
                onToggle={toggleSelected}
                onRange={onRange}
                onOpen={openFocus}
              />
            );
            return (
              <div key={g.chave} className={styles.grupo}>
                {grupos.length > 1 ? (
                  <h3 className={styles.grupoTitulo}>
                    {g.nome}
                    <span className={styles.grupoContagem}>{plural(g.ativos.length + g.paradas.length, 'aparelho', 'aparelhos')}</span>
                  </h3>
                ) : null}
                {g.ativos.length > 0 ? <div className={styles.grid}>{g.ativos.map((i) => cartao(i, false))}</div> : null}
                {g.paradas.length > 0 ? (
                  <>
                    <button type="button" className={styles.paradasBotao} aria-expanded={!recolhido}
                            onClick={() => alternarParadas(g.chave)}>
                      {recolhido ? <ChevronRight size={14} aria-hidden /> : <ChevronDown size={14} aria-hidden />}
                      Parados ({g.paradas.length})
                    </button>
                    {recolhido ? null : <div className={styles.gridCompacto}>{g.paradas.map((i) => cartao(i, true))}</div>}
                  </>
                ) : null}
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
