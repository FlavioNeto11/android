import { CalendarClock, TriangleAlert } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { toApiError } from '../../api/client';
import type { ListaDePedidos as Lista, PedidoView } from '../../api/pedidos';
import { BarraListagem, type FiltroListagem } from '../../components/BarraListagem';
import { Banner } from '../../components/Banner';
import { Button } from '../../components/Button';
import { EmptyState } from '../../components/EmptyState';
import { StatusBadge } from '../../components/StatusBadge';
import { Badge } from '../../components/Badge';
import { hashDe } from '../../lib/rotas';
import { tempoRelativo, useNow } from '../../lib/time';
import { PARAM_FOCO, useUiStore } from '../../store/ui';
import { nomeDe } from '../profiles/pessoa';
import { usePersonas } from '../profiles/usePersonas';
import { apiPedidos } from './api';
import { EsqueletoDaLista } from './Esqueleto';
import { agendaLegivel, dataCompacta, fusoParaMostrar, horaEscrita, horaNoFuso, proximaDoPedido, quemFazDoPedido } from './formato';
import { AvisoDoLacoDesligado } from './LacoDesligado';
import {
  AUTONOMIAS, ORDENS, ROTULO_DA_ORDEM, TIPOS_DE_GATILHO, chaveDoFiltro, filtroDoLink, temFiltro, type FiltroDoLink,
} from './filtro';
import {
  ESTADOS_DO_PEDIDO, META_DA_OCORRENCIA, META_DO_PEDIDO, ROTULO_DA_AUTONOMIA, ROTULO_DO_GATILHO, formatUsd, mensagemDoErro,
} from './modelo';
import { usePedidosStore } from './store';
import styles from './Pedidos.module.css';

const PAGINA = 50;

/** A lista de pedidos do filtro do link: relê quando o filtro muda e a cada evento `pedido.*`; "Carregar mais" segue o cursor. */
function usePedidosDoFiltro(filtro: FiltroDoLink) {
  const epoch = usePedidosStore((s) => s.epoch);
  const [itens, setItens] = useState<PedidoView[] | null>(null);
  const [totais, setTotais] = useState<Lista['total_por_estado']>({});
  const [laco, setLaco] = useState<Lista['laco'] | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const [maisCarregando, setMaisCarregando] = useState(false);
  const chave = chaveDoFiltro(filtro);
  const filtroRef = useRef(filtro);
  filtroRef.current = filtro;
  const anterior = useRef<string | null>(null);

  useEffect(() => {
    const ctrl = new AbortController();
    // Outro filtro: a lista de antes não é a resposta; mesma chave (evento ao vivo): mantém o que está na tela.
    if (anterior.current !== chave) setItens(null);
    anterior.current = chave;
    apiPedidos.listar({ ...filtroRef.current, limit: PAGINA }, ctrl.signal)
      .then((r) => {
        setItens(Array.isArray(r?.items) ? r.items : []);
        setTotais(r?.total_por_estado ?? {});
        setLaco(r?.laco ?? null);
        setCursor(r?.proximo_cursor ?? null);
        setErro(null);
      })
      .catch((e) => {
        if (ctrl.signal.aborted) return;
        setErro(mensagemDoErro(toApiError(e)));
        setItens((atual) => atual ?? []);
      });
    return () => ctrl.abort();
  }, [chave, epoch]);

  const maisUm = useCallback(async () => {
    if (!cursor) return;
    setMaisCarregando(true);
    try {
      const r = await apiPedidos.listar({ ...filtroRef.current, limit: PAGINA, cursor });
      setItens((atual) => [...(atual ?? []), ...(r.items ?? [])]);
      setCursor(r.proximo_cursor ?? null);
    } catch (e) {
      setErro(mensagemDoErro(toApiError(e)));
    } finally {
      setMaisCarregando(false);
    }
  }, [cursor]);

  return { itens, totais, laco, cursor, erro, maisCarregando, maisUm };
}

/** A lista (`#/pedidos`): uma linha por pedido, com os filtros no link (ADR-062, item 4). */
export function ListaDePedidos() {
  const query = useUiStore((s) => s.rota.query);
  const trocarQuery = useUiStore((s) => s.trocarQuery);
  const filtro = useMemo(() => filtroDoLink(query), [query]);
  const { itens, totais, laco, cursor, erro, maisCarregando, maisUm } = usePedidosDoFiltro(filtro);
  const pessoas = usePersonas();
  const agora = useNow();

  // A busca digitada só vai ao link depois de uma pausa: cada tecla seria uma leitura.
  const [busca, setBusca] = useState(filtro.q ?? '');
  useEffect(() => { setBusca(filtro.q ?? ''); }, [filtro.q]);
  useEffect(() => {
    if (busca.trim() === (filtro.q ?? '')) return undefined;
    const t = setTimeout(() => trocarQuery({ q: busca.trim() || undefined }), 350);
    return () => clearTimeout(t);
  }, [busca, filtro.q, trocarQuery]);

  const totalGeral = Object.values(totais).reduce<number>((a, n) => a + (n ?? 0), 0);
  const filtros: FiltroListagem[] = [
    {
      chave: 'estado', rotulo: 'Estado do pedido', tipo: 'chips', rotuloTodos: 'Todos', contagemTodos: totalGeral,
      valor: filtro.estado ?? '', onChange: (v) => trocarQuery({ estado: v || undefined }),
      opcoes: ESTADOS_DO_PEDIDO.map((e) => ({ valor: e, rotulo: META_DO_PEDIDO[e].label, contagem: totais[e] ?? 0, dica: META_DO_PEDIDO[e].description })),
    },
    {
      chave: 'atencao', rotulo: 'Atenção', tipo: 'chips', rotuloTodos: 'Qualquer', valor: filtro.pede_atencao ?? '',
      onChange: (v) => trocarQuery({ pede_atencao: v || undefined }),
      opcoes: [{ valor: '1', rotulo: 'Pede atenção', dica: 'Pausados, aguardando você ou com aviso não lido.' }],
    },
    {
      chave: 'autonomia', rotulo: 'Autonomia', tipo: 'lista', rotuloTodos: 'Toda autonomia', valor: filtro.autonomia ?? '',
      onChange: (v) => trocarQuery({ autonomia: v || undefined }),
      opcoes: AUTONOMIAS.map((a) => ({ valor: a, rotulo: ROTULO_DA_AUTONOMIA[a].rotulo })),
    },
    {
      chave: 'tipo', rotulo: 'Tipo de gatilho', tipo: 'lista', rotuloTodos: 'Todo gatilho', valor: filtro.tipo ?? '',
      onChange: (v) => trocarQuery({ tipo: v || undefined }),
      opcoes: TIPOS_DE_GATILHO.map((t) => ({ valor: t, rotulo: ROTULO_DO_GATILHO[t] })),
    },
    {
      chave: 'persona', rotulo: 'Persona', tipo: 'lista', rotuloTodos: 'Toda persona', valor: filtro.profile_id ?? '',
      onChange: (v) => trocarQuery({ profile_id: v || undefined }),
      opcoes: (pessoas ?? []).map((p) => ({ valor: p.id, rotulo: nomeDe(p) })),
    },
  ];

  return (
    <>
      {/* Vale para a instalação inteira: vem antes dos filtros, não entre a contagem e a lista. */}
      {laco?.ligado === false ? <AvisoDoLacoDesligado /> : null}
      <BarraListagem
        nome="pedidos"
        busca={{ valor: busca, onChange: setBusca, placeholder: 'Buscar por título ou objetivo' }}
        filtros={filtros}
        ordem={{ valor: filtro.ordem, onChange: (v) => trocarQuery({ ordem: v === 'atualizado' ? undefined : v }),
                 opcoes: ORDENS.map((o) => ({ valor: o, rotulo: ROTULO_DA_ORDEM[o] })) }}
        resumo={itens ? `${itens.length}${cursor ? '+' : ''} ${itens.length === 1 ? 'pedido' : 'pedidos'}` : undefined}
        onLimpar={temFiltro(filtro) ? () => trocarQuery({ q: undefined, estado: undefined, autonomia: undefined, tipo: undefined,
                                                         profile_id: undefined, pede_atencao: undefined }) : undefined}
      />
      {erro ? (
        <Banner tone="warning" icon={TriangleAlert} compact role="status"
                actions={<Button size="sm" onClick={() => usePedidosStore.getState().bater()}>Tentar de novo</Button>}>
          Não foi possível ler os pedidos agora. {erro}
        </Banner>
      ) : null}
      {itens === null ? (
        <EsqueletoDaLista label="Carregando os pedidos…" />
      ) : itens.length === 0 ? (
        temFiltro(filtro) ? (
          <EmptyState icon={CalendarClock} title="Nenhum pedido neste filtro">Limpe os filtros para ver todos.</EmptyState>
        ) : (
          <EmptyState icon={CalendarClock} title="Nenhum pedido ainda"
                      hint="Use “Novo pedido”: escreva o objetivo no Comando e escolha “Repetir ou acompanhar…”." />
        )
      ) : (
        <>
          <ul className={styles.lista} aria-label="Pedidos">
            {itens.map((p) => <LinhaDoPedido key={p.id} p={p} agora={agora} />)}
          </ul>
          {cursor ? <Button onClick={() => void maisUm()} loading={maisCarregando}>Carregar mais</Button> : null}
        </>
      )}
    </>
  );
}

function LinhaDoPedido({ p, agora }: { p: PedidoView; agora: number }) {
  const foco = useUiStore((s) => s.focusInstanceId);
  const href = hashDe('pedidos', { segmentos: [p.id], query: { [PARAM_FOCO]: foco ?? undefined } });
  const ult = p.ultima_ocorrencia;
  const { personas, aparelhos } = quemFazDoPedido(p);
  const quem = personas.length > 0 ? personas.join(', ') : aparelhos.join(', ');
  // A hora fica no texto da agenda ("Todo dia às 19:00"); o backend só a escreve quando a regra a traz, então completa-se
  // com a hora da próxima data (escrita no fuso do pedido). O fuso vai uma vez, e só se difere do navegador.
  // Sem a data do laço (desligado ou ainda não gerou), vale a PREVISTA pela agenda, que a API calcula (28.12).
  const proxima = proximaDoPedido(p, p.proxima_prevista ? [p.proxima_prevista] : null);
  const hora = horaEscrita(p.proxima_local) ?? horaEscrita(p.proxima_prevista?.local) ?? horaNoFuso(proxima?.iso, p.fuso);
  const agenda = p.gatilhos_resumo?.length ? p.gatilhos_resumo.map((g) => agendaLegivel(g.descricao, p.fuso, hora)).join(' · ') : null;
  const fusoMostrado = fusoParaMostrar(p.fuso);
  const usado = p.orcamento_usado !== null && p.orcamento_usado !== undefined ? ` (${Math.round(p.orcamento_usado * 100)}% do orçamento)` : '';
  return (
    <li className={styles.linha}>
      <div className={styles.corpo}>
        <div className={styles.topo}>
          <StatusBadge meta={META_DO_PEDIDO[p.estado]} size="sm" srPrefix="Estado" />
          <Badge size="sm" title={ROTULO_DA_AUTONOMIA[p.autonomia].dica}>{ROTULO_DA_AUTONOMIA[p.autonomia].rotulo}</Badge>
          {p.avisos_nao_lidos > 0 ? <Badge size="sm" tone="info">{p.avisos_nao_lidos} {p.avisos_nao_lidos === 1 ? 'aviso novo' : 'avisos novos'}</Badge> : null}
        </div>
        <a className={styles.titulo} href={href}>{p.titulo}</a>
        {agenda || proxima || p.estado === 'ativo' ? (
        <p className={styles.agenda}>
          <CalendarClock size={13} aria-hidden />
          {agenda ? <span>{agenda}</span> : null}
          {proxima ? (
            <span>
              {agenda ? '· ' : ''}{proxima.calculada ? 'prevista' : 'próxima'} <strong title={proxima.iso}>{dataCompacta(proxima.iso, p.fuso)}</strong>
              {proxima.calculada ? <span className={styles.dim} title="Calculada pela agenda: o laço ainda não gerou esta ocorrência."> (pela agenda)</span> : null}
            </span>
          ) : p.estado === 'ativo' ? <span className={styles.dim}>{agenda ? '· ' : ''}sem data prevista</span> : null}
          {fusoMostrado ? <span className={styles.dim}>(fuso {fusoMostrado})</span> : null}
        </p>
        ) : null}
        {p.estado === 'pausado' && p.pausado_motivo ? <span className={styles.motivo}>Pausado: {p.pausado_motivo}</span> : null}
        <div className={styles.metaDiscreta}>
          {quem ? <span>{quem}</span> : null}
          <span>Gasto {formatUsd(p.gasto_usd)}{usado}</span>
          {ult ? (
            <>
              <span>Última: <StatusBadge meta={META_DA_OCORRENCIA[ult.estado]} size="sm" plain />
                {ult.terminada_em ? ` ${tempoRelativo(ult.terminada_em, agora)}` : ''}</span>
              {ult.motivo ? <span>{ult.motivo}</span> : null}
              {ult.run_id ? <a href={hashDe('execucoes', { segmentos: [ult.run_id] })}>ver a execução</a> : null}
            </>
          ) : <span>Nenhuma ocorrência ainda</span>}
        </div>
      </div>
    </li>
  );
}
