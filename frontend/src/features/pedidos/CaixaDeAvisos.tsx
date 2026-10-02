import { Bell, CheckCheck, TriangleAlert } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { toApiError } from '../../api/client';
import type { AvisoDTO } from '../../api/pedidos';
import { Badge } from '../../components/Badge';
import { Banner } from '../../components/Banner';
import { Button } from '../../components/Button';
import { EmptyState } from '../../components/EmptyState';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { hashDe } from '../../lib/rotas';
import { formatDateTime, tempoRelativo, useNow } from '../../lib/time';
import { toastError } from '../../store/toasts';
import { apiPedidos } from './api';
import { ROTULO_DO_AVISO, mensagemDoErro } from './modelo';
import { usePedidosStore } from './store';
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
      const r = await apiPedidos.avisos({ requer_pessoa: 0, ...(todos ? {} : { lido: 0 }), limit: 100 }, sinal);
      setItens(Array.isArray(r?.items) ? r.items : []);
      setErro(null);
    } catch (e) {
      if (sinal?.aborted) return;
      setErro(mensagemDoErro(toApiError(e)));
      setItens((atual) => atual ?? []);
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
      <div className={styles.acoes}>
        <Button size="sm" variant={todos ? 'outline' : 'primary'} aria-pressed={!todos} onClick={() => setTodos(false)}>Não lidos</Button>
        <Button size="sm" variant={todos ? 'primary' : 'outline'} aria-pressed={todos} onClick={() => setTodos(true)}>Todos</Button>
        <Button size="sm" icon={CheckCheck} loading={ocupado} disabledReason={naoLidos.length === 0 ? 'Nenhum aviso não lido.' : null}
                onClick={() => void marcar({ todos: true })}>
          Marcar todos como lidos
        </Button>
      </div>
      {erro ? <Banner tone="warning" icon={TriangleAlert} compact role="status">Não foi possível ler os avisos agora. {erro}</Banner> : null}
      {itens === null ? (
        <LoadingRegion label="Carregando os avisos…"><Skeleton height={56} radius={8} /></LoadingRegion>
      ) : itens.length === 0 ? (
        <EmptyState icon={Bell} title={todos ? 'Nenhum aviso' : 'Nenhum aviso novo'}
                    hint="Quando um pedido pausar sozinho, gastar o orçamento, perder uma ocorrência ou ficar pronto, o aviso aparece aqui.">
          Nada para ler.
        </EmptyState>
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
