import { Bell, CheckCheck, TriangleAlert } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { toApiError } from '../../api/client';
import type { AvisoDTO } from '../../api/pedidos';
import { Badge } from '../../components/Badge';
import { Banner } from '../../components/Banner';
import { Button } from '../../components/Button';
import { EmptyState } from '../../components/EmptyState';
import { cx } from '../../lib/format';
import { hashDe } from '../../lib/rotas';
import { formatDateTime, tempoRelativo, useNow } from '../../lib/time';
import { toastError } from '../../store/toasts';
import { apiPedidos } from './api';
import { EsqueletoDaLista } from './Esqueleto';
import { ROTULO_DO_AVISO, mensagemDoErro } from './modelo';
import { LIMITE_DA_CAIXA, usePedidosStore } from './store';
import styles from './Pedidos.module.css';

const TOM = { info: 'info', warn: 'warning', error: 'danger' } as const;

/**
 * A caixa de avisos: o que os pedidos fizeram e a pessoa pode querer saber (pausa automática, orçamento, ocorrência
 * perdida, relatório pronto, encerramento). É informativa e tem contador próprio; o que depende de uma decisão fica na
 * caixa de Pendências (ADR-062), por isso o filtro `requer_pessoa=0`. Sai da lista quando marcado como lido.
 */
export function CaixaDeAvisos() {
  const agora = useNow();
  const epoch = usePedidosStore((s) => s.epoch);
  const [todos, setTodos] = useState(false);
  const [itens, setItens] = useState<AvisoDTO[] | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const [ocupado, setOcupado] = useState(false);

  const ler = useCallback(async (sinal?: AbortSignal) => {
    try {
      const r = await apiPedidos.avisos({ requer_pessoa: 0, ...(todos ? {} : { lido: 0 }), limit: LIMITE_DA_CAIXA }, sinal);
      setItens(Array.isArray(r?.items) ? r.items : []);
      setErro(null);
    } catch (e) {
      if (sinal?.aborted) return;
      // Sem dado anterior `itens` fica `null`: só o aviso com "Tentar de novo", nunca "Nenhum aviso" (que seria falso).
      setErro(mensagemDoErro(toApiError(e)));
    }
  }, [todos]);

  useEffect(() => {
    const ctrl = new AbortController();
    void ler(ctrl.signal);
    return () => ctrl.abort();
  }, [ler, epoch]);

  const marcar = async (corpo: { ids?: string[]; todos?: true }) => {
    setOcupado(true);
    try {
      await apiPedidos.lerAvisos(corpo);
      await Promise.all([ler(), usePedidosStore.getState().atualizar()]);
    } catch (e) {
      toastError('Não foi possível marcar como lido', e);
    } finally {
      setOcupado(false);
    }
  };

  const naoLidos = (itens ?? []).filter((a) => !a.lido_em);
  return (
    <>
      <div className={styles.barraDeAvisos}>
        <div className={styles.seletor} role="group" aria-label="Quais avisos mostrar">
          <button type="button" className={cx(styles.seletorItem, !todos && styles.seletorAtivo)} aria-pressed={!todos} onClick={() => setTodos(false)}>Não lidos</button>
          <button type="button" className={cx(styles.seletorItem, todos && styles.seletorAtivo)} aria-pressed={todos} onClick={() => setTodos(true)}>Todos</button>
        </div>
        <Button size="sm" icon={CheckCheck} loading={ocupado}
                disabledReason={itens === null ? (erro ? 'Os avisos não puderam ser lidos.' : 'Carregando os avisos.') : naoLidos.length === 0 ? 'Nenhum aviso não lido.' : null}
                onClick={() => void marcar({ todos: true })}>
          {naoLidos.length > 0 ? `Marcar todos como lidos (${naoLidos.length})` : 'Marcar todos como lidos'}
        </Button>
      </div>
      {erro ? (
        <Banner tone="warning" icon={TriangleAlert} compact role="status"
                actions={<Button size="sm" onClick={() => { if (itens === null) setErro(null); void ler(); }}>Tentar de novo</Button>}>
          Não foi possível ler os avisos agora. {erro}
        </Banner>
      ) : null}
      {itens === null ? (
        erro ? null : <EsqueletoDaLista label="Carregando os avisos…" />
      ) : itens.length === 0 ? (
        <EmptyState icon={Bell} title={todos ? 'Nenhum aviso' : 'Nenhum aviso novo'}
                    hint="Quando um pedido pausar sozinho, gastar o orçamento, perder uma ocorrência ou ficar pronto, o aviso aparece aqui." />
      ) : (
        <ul className={styles.lista} aria-label="Avisos">
          {itens.map((a) => (
            <li key={a.id} className={styles.linha}>
              <div className={styles.corpo}>
                <div className={styles.topo}>
                  <Badge size="sm" tone={TOM[a.nivel] ?? 'neutral'}>{ROTULO_DO_AVISO[a.tipo] ?? a.tipo}</Badge>
                  <span className={styles.dim} title={formatDateTime(a.criado_em)}>{tempoRelativo(a.criado_em, agora)}</span>
                  {a.lido_em ? <span className={styles.dim}>lido</span> : null}
                </div>
                <a className={styles.titulo} href={hashDe('pedidos', { segmentos: [a.pedido_id] })}>{a.pedido_titulo}</a>
                <span className={styles.motivo}>{a.mensagem}</span>
              </div>
              {a.lido_em ? null : (
                <Button size="sm" variant="ghost" disabled={ocupado} aria-label={`Marcar como lido: ${a.pedido_titulo}`}
                        onClick={() => void marcar({ ids: [a.id] })}>
                  Marcar como lido
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
