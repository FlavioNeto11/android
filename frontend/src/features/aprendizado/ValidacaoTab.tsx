import { BookOpen, FlaskConical, PauseCircle, Play, RefreshCw } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { Banner } from '../../components/Banner';
import { BarraListagem, type FiltroListagem } from '../../components/BarraListagem';
import { Button } from '../../components/Button';
import { EmptyState } from '../../components/EmptyState';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { StatusBadge } from '../../components/StatusBadge';
import { TruncatedText } from '../../components/TruncatedText';
import { LoadErrorBanner, LoadErrorState } from '../../lib/loadError';
import { formatDateTime, tempoRelativo } from '../../lib/time';
import { useUiStore } from '../../store/ui';
import { apiAprendizado } from './api';
import { formatUsd, hrefDoItemDaRevisao } from './metricas';
import { useCarga } from './useCarga';
import {
  ESTADOS_DA_VALIDACAO, META_DA_VALIDACAO, isEstadoDaValidacao, rotuloDoItem, type ListaDeValidacoes,
  type PedidoDeValidacao,
} from './validacao';
import styles from './Aprendizado.module.css';

const LIMITE = 50;

const O_QUE_E =
  'Quando o curador pede uma evidência que uma execução consegue produzir (rodar de novo o comando que ensinou o item, '
  + 'noutro aparelho), o sistema grava aqui um pedido e roda essa execução sozinho, num aparelho ocioso e dentro de uma '
  + 'verba pequena. O pedido não decide nada: o resultado volta ao curador, e o item segue as regras de sempre.';

const POR_QUE_PAUSA =
  'Ela gasta IA e ocupa aparelhos, por isso fica desligada até ser ligada de propósito na configuração do central '
  + '(Aprendizado › validação › modo). Pausada, nada nasce e nada roda; o que já foi pedido continua listado aqui.';

function abrirExecucao(runId: string) {
  useUiStore.getState().selectRun(runId);
  useUiStore.getState().setView('execucoes');
}

function CartaoDoPedido({ p, agora }: { p: PedidoDeValidacao; agora: number }) {
  const hrefDoItem = hrefDoItemDaRevisao({ item_ref: p.item_ref, item_kind: p.item_kind });
  const app = p.app_nome ?? p.app;
  const gasto = p.teto_usd !== null ? `${formatUsd(p.usd)} de teto ${formatUsd(p.teto_usd)}` : formatUsd(p.usd);
  return (
    <li className={styles.item} data-pedido={p.id}>
      <div className={styles.itemHead}>
        <StatusBadge meta={META_DA_VALIDACAO[p.estado]} size="sm" srPrefix="Estado" />
        <span className={styles.itemTitulo} title={p.item_ref}>
          {rotuloDoItem(p)}{app ? <> · <span title={p.app ?? undefined}>{app}</span></> : null}
        </span>
      </div>
      {p.motivo_humano ? <p className={styles.validacaoMotivo} title={p.motivo ?? undefined}>{p.motivo_humano}</p> : null}
      {p.comando ? <TruncatedText className={styles.validacaoComando}>{p.comando}</TruncatedText> : null}
      <div className={styles.itemMeta}>
        <span title={formatDateTime(p.created_at)}>Pedido {tempoRelativo(p.created_at, agora)}</span>
        {p.aparelho ? <span>Rodou no {p.aparelho}</span> : null}
        <span title="Custo medido da execução de validação e o teto do pedido">{gasto}</span>
      </div>
      <div className={styles.itemAcoes}>
        {p.run_id ? (
          <Button size="sm" variant="outline" icon={Play} onClick={() => abrirExecucao(p.run_id as string)}>Abrir execução</Button>
        ) : null}
        {hrefDoItem ? <a className={styles.linkAlvo} href={hrefDoItem}><BookOpen size={13} aria-hidden /> Abrir no Livro</a> : null}
      </div>
    </li>
  );
}

/**
 * Aprendizado › Validação (30.38 b): os pedidos de validação automática do curador, com o estado, o motivo em texto, o
 * aparelho, o custo e o caminho para a execução e para o item. Só leitura: nenhum gesto aqui muda o pedido.
 */
export function ValidacaoTab() {
  const estadoDoLink = useUiStore((s) => s.rota.query.estado);
  const trocarQuery = useUiStore((s) => s.trocarQuery);
  const estado = isEstadoDaValidacao(estadoDoLink) ? estadoDoLink : '';
  const [busca, setBusca] = useState('');
  const [mais, setMais] = useState<PedidoDeValidacao[]>([]);
  const [fim, setFim] = useState(false);
  const [carregandoMais, setCarregandoMais] = useState(false);
  const { dado, erro, carregando, carregar } = useCarga<ListaDeValidacoes>(
    (signal) => apiAprendizado.validacoes({ estado, limite: LIMITE }, signal), estado);
  // A última leitura fica para a barra e o aviso: trocar a ficha recarrega a lista sem a barra sumir e voltar.
  const [ultima, setUltima] = useState<ListaDeValidacoes | null>(null);
  useEffect(() => { if (dado) setUltima(dado); }, [dado]);
  const base = dado ?? ultima;
  const agora = Date.now();

  const recarregar = () => { setMais([]); setFim(false); void carregar(); };
  const itens = useMemo(() => [...(dado?.itens ?? []), ...mais], [dado, mais]);
  const visiveis = useMemo(() => {
    const q = busca.trim().toLowerCase();
    if (!q) return itens;
    return itens.filter((p) => [p.comando, p.app_nome, p.app, p.item_ref, p.motivo_humano, p.aparelho]
      .some((t) => t?.toLowerCase().includes(q)));
  }, [itens, busca]);
  const podeMais = !fim && (dado?.itens.length ?? 0) >= LIMITE && itens.length > 0;
  const carregarMais = async () => {
    const ultimo = itens[itens.length - 1];
    if (!ultimo) return;
    setCarregandoMais(true);
    try {
      const r = await apiAprendizado.validacoes({ estado, limite: LIMITE, antes: ultimo.created_at });
      setMais((m) => [...m, ...r.itens]);
      if (r.itens.length < LIMITE) setFim(true);
    } finally {
      setCarregandoMais(false);
    }
  };

  const pausada = base?.modo === 'off';
  const filtros: FiltroListagem[] = base ? [{
    chave: 'estado', rotulo: 'Estado', tipo: 'chips', rotuloTodos: 'Todos', contagemTodos: base.total, valor: estado,
    onChange: (v) => { setMais([]); setFim(false); trocarQuery({ estado: v || undefined }, 'replace'); },
    opcoes: ESTADOS_DA_VALIDACAO.map((e) => ({
      valor: e, rotulo: META_DA_VALIDACAO[e].label, contagem: base.contagem[e], dica: META_DA_VALIDACAO[e].description,
    })),
  }] : [];

  return (
    <section className={styles.secao} aria-label="Validação">
      {pausada ? (
        <Banner tone="info" icon={PauseCircle} compact role="status" title="A validação automática está pausada">
          {POR_QUE_PAUSA}
        </Banner>
      ) : null}
      {base && base.total > 0 ? (
        <BarraListagem
          nome="pedidos de validação"
          busca={{ valor: busca, onChange: setBusca, placeholder: 'Buscar por app, comando, item ou aparelho' }}
          filtros={filtros}
          resumo={`${visiveis.length}${podeMais ? '+' : ''} ${visiveis.length === 1 ? 'pedido' : 'pedidos'}`}
          onLimpar={estado || busca ? () => { setBusca(''); trocarQuery({ estado: undefined }, 'replace'); } : undefined}
        />
      ) : null}
      {base ? (
        <div className={styles.toolbar}>
          <div className={styles.toolbarFim}>
            <Button size="sm" variant="ghost" icon={RefreshCw} loading={carregando} onClick={recarregar}>Atualizar</Button>
          </div>
        </div>
      ) : null}
      {erro && dado ? <LoadErrorBanner error={erro} onRetry={recarregar} /> : null}
      {!dado ? (
        erro ? <LoadErrorState what="os pedidos de validação" error={erro} onRetry={recarregar} /> : (
          <LoadingRegion label="Lendo os pedidos de validação…" className={styles.secao}>
            <Skeleton height={88} radius={8} />
            <Skeleton height={88} radius={8} />
          </LoadingRegion>
        )
      ) : dado.total === 0 ? (
        <EmptyState icon={FlaskConical} title="Nenhum pedido de validação ainda"
                    hint={pausada ? `Agora ela está pausada. ${POR_QUE_PAUSA}` : undefined}>
          {O_QUE_E}
        </EmptyState>
      ) : visiveis.length === 0 ? (
        <EmptyState icon={FlaskConical} compact title={busca ? 'Nenhum pedido com essa busca' : 'Nenhum pedido neste estado'}>
          Limpe os filtros para ver todos.
        </EmptyState>
      ) : (
        <>
          <ul className={styles.lista} aria-label="Pedidos de validação">
            {visiveis.map((p) => <CartaoDoPedido key={p.id} p={p} agora={agora} />)}
          </ul>
          {podeMais ? <Button onClick={() => void carregarMais()} loading={carregandoMais}>Carregar mais</Button> : null}
        </>
      )}
    </section>
  );
}
