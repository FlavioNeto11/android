import { Ban, CirclePlay, Pause, Pencil, Play } from 'lucide-react';
import { useState } from 'react';
import { toApiError, type ApiError } from '../../api/client';
import type { AcaoDePedido, PedidoDetalhe } from '../../api/pedidos';
import { Button } from '../../components/Button';
import { confirm } from '../../components/Confirm';
import { Dialog } from '../../components/Dialog';
import { toast } from '../../store/toasts';
import { apiPedidos } from './api';
import { EdicaoDoPedido } from './EdicaoDoPedido';
import { mensagemDoErro } from './modelo';
import { ProximasDatas } from './PreviaDoPedido';
import { usePedidosStore } from './store';
import styles from './Pedidos.module.css';

const quantos = (v: unknown): number => (Array.isArray(v) ? v.length : typeof v === 'number' ? v : 0);

/**
 * As ações do pedido, todas lidas de `acoes_permitidas` (o painel não reescreve a tabela de estados). Cada uma trata o
 * `200 sem_mudanca` (repetir é seguro: a tela diz que já estava assim) e o `409 invalid_state` (a tela relê e diz o que
 * vale agora). As destrutivas pedem confirmação. `executar` e `backfill` ainda não têm botão (fora do 28.9 desta tela).
 */
export function AcoesDoPedido({ pedido, onMudou, somente, exceto }: {
  pedido: PedidoDetalhe; onMudou: () => void;
  /** Mostra só estas ações (o aviso de "aguardando você" leva o Retomar para perto do motivo). */
  somente?: readonly AcaoDePedido[];
  /** Esconde estas (a barra principal não repete o que o aviso já oferece). */
  exceto?: readonly AcaoDePedido[];
}) {
  const [ocupado, setOcupado] = useState<AcaoDePedido | null>(null);
  const [editando, setEditando] = useState(false);
  const [retomando, setRetomando] = useState(false);
  const permitidas = new Set((pedido.acoes_permitidas ?? []).filter((a) => (!somente || somente.includes(a)) && !exceto?.includes(a)));

  const depois = () => {
    usePedidosStore.getState().bater();
    onMudou();
  };

  /** Erro de uma ação: o estado velho (`invalid_state`) relê a tela; os demais viram o toast com a explicação. */
  const falhou = (titulo: string, e: unknown) => {
    const err: ApiError = toApiError(e);
    if (err.code === 'invalid_state') {
      const validas = Array.isArray(err.detail?.acoes_permitidas) ? (err.detail?.acoes_permitidas as string[]).join(', ') : '';
      toast({ tone: 'warning', title: `${titulo}: o estado do pedido mudou`, message: validas ? `Agora vale: ${validas}.` : mensagemDoErro(err) });
      depois();
      return;
    }
    if (err.code === 'pendencia_aberta') {
      const n = quantos(err.detail?.pendencias);
      toast({ tone: 'warning', title: titulo, message: `${mensagemDoErro(err)}${n ? ` (${n} aberta${n > 1 ? 's' : ''}).` : ''}` });
      return;
    }
    toast({ tone: 'danger', title: titulo, message: mensagemDoErro(err) });
  };

  const rodar = async (acao: AcaoDePedido, titulo: string, fn: () => Promise<void>) => {
    setOcupado(acao);
    try {
      await fn();
    } catch (e) {
      falhou(titulo, e);
    } finally {
      setOcupado(null);
    }
  };

  const semMudanca = (texto: string) => toast({ tone: 'info', title: 'Nada mudou', message: texto });

  const pausar = () => rodar('pausar', 'Não foi possível pausar', async () => {
    const r = await confirm({
      title: 'Pausar o pedido?', confirmLabel: 'Pausar', icon: Pause,
      body: 'Ele para de gerar ocorrências novas. Uma execução que já corre não é cancelada. Você retoma quando quiser.',
      note: { label: 'Motivo', placeholder: 'Pausado pela pessoa' },
    });
    if (!r.confirmed) return;
    const res = await apiPedidos.pausar(pedido.id, r.note || undefined);
    if (res.sem_mudanca) semMudanca('O pedido já estava pausado.');
    else toast({ tone: 'success', title: 'Pedido pausado' });
    depois();
  });

  const retomar = (modo?: 'daqui' | 'recuperar') => rodar('retomar', 'Não foi possível retomar', async () => {
    setRetomando(false);
    const res = await apiPedidos.retomar(pedido.id, modo);
    if (res.sem_mudanca) semMudanca('O pedido já estava ativo.');
    else {
      toast({
        tone: 'success', title: 'Pedido retomado',
        message: modo ? `${res.puladas ?? 0} puladas, ${res.recuperadas ?? 0} recuperadas.` : undefined,
      });
    }
    depois();
  });

  /** Cancelar é pedir, não desfazer: a primeira chamada, sem `confirmar`, só devolve o que seria afetado (409). */
  const cancelar = () => rodar('cancelar', 'Não foi possível cancelar', async () => {
    let afetadas = { execucoes: quantos(pedido.execucoes_em_curso), futuras: 0 };
    try {
      const direto = await apiPedidos.cancelar(pedido.id, { confirmar: false });
      // Resposta 200 sem pedir confirmação: o backend decidiu que não havia o que confirmar.
      if (direto.sem_mudanca) semMudanca('O pedido já estava cancelado.');
      else toast({ tone: 'success', title: 'Pedido cancelado' });
      depois();
      return;
    } catch (e) {
      const err = toApiError(e);
      if (err.code !== 'confirmacao_necessaria') throw e;
      afetadas = { execucoes: quantos(err.detail?.execucoes_em_curso), futuras: quantos(err.detail?.ocorrencias_futuras) };
    }
    const r = await confirm({
      title: 'Cancelar o pedido?', confirmLabel: 'Cancelar o pedido', cancelLabel: 'Voltar', danger: true,
      body: (
        <>
          <p>O pedido deixa de gerar ocorrências e não pode ser reaberto.</p>
          <p>{afetadas.futuras} {afetadas.futuras === 1 ? 'ocorrência futura será cancelada' : 'ocorrências futuras serão canceladas'}.</p>
          <p>
            {afetadas.execucoes > 0
              ? `${afetadas.execucoes} ${afetadas.execucoes === 1 ? 'execução em curso recebe' : 'execuções em curso recebem'} o pedido de cancelamento: cancelar é pedir, e o desfecho de cada uma é o real.`
              : 'Nenhuma execução em curso.'}
          </p>
        </>
      ),
      note: { label: 'Motivo' },
    });
    if (!r.confirmed) return;
    const res = await apiPedidos.cancelar(pedido.id, { confirmar: true, ...(r.note ? { motivo: r.note } : {}) });
    if (res.sem_mudanca) semMudanca('O pedido já estava cancelado.');
    else {
      toast({
        tone: 'success', title: 'Pedido cancelado',
        message: `${res.ocorrencias_canceladas ?? 0} ocorrências canceladas; ${quantos(res.execucoes_em_curso)} execuções em curso com cancelamento pedido.`,
      });
    }
    depois();
  });

  /** Ativar usa o selo da edição vazia em `dry_run` ("o selo do estado resultante") e mostra as datas antes de ativar. */
  const ativar = () => rodar('ativar', 'Não foi possível ativar', async () => {
    const previa = await apiPedidos.editar(pedido.id, { versao: pedido.versao, dry_run: true });
    if (!previa.confirmacao) {
      toast({ tone: 'warning', title: 'Não foi possível ativar', message: 'A prévia não devolveu o selo de confirmação.' });
      return;
    }
    const r = await confirm({
      title: 'Ativar o pedido?', confirmLabel: 'Ativar', icon: CirclePlay,
      body: <ProximasDatas datas={previa.proximas_depois} titulo="O pedido passa a rodar nestas datas" fuso={pedido.fuso} />,
    });
    if (!r.confirmed) return;
    await apiPedidos.ativar(pedido.id, previa.confirmacao);
    toast({ tone: 'success', title: 'Pedido ativado' });
    depois();
  });

  const retomavel = permitidas.has('retomar');
  const pausado = pedido.estado === 'pausado';
  return (
    <div className={styles.acoes} role="group" aria-label="Ações do pedido">
      {permitidas.has('ativar') ? <Button variant="primary" icon={Play} loading={ocupado === 'ativar'} onClick={() => void ativar()}>Ativar</Button> : null}
      {permitidas.has('pausar') ? <Button icon={Pause} loading={ocupado === 'pausar'} onClick={() => void pausar()}>Pausar</Button> : null}
      {retomavel ? (
        <Button variant="primary" icon={Play} loading={ocupado === 'retomar'}
                onClick={() => (pausado ? setRetomando(true) : void retomar())}>
          Retomar
        </Button>
      ) : null}
      {permitidas.has('editar') ? <Button icon={Pencil} onClick={() => setEditando(true)}>Editar</Button> : null}
      {permitidas.has('cancelar') ? <Button variant="dangerGhost" icon={Ban} loading={ocupado === 'cancelar'} onClick={() => void cancelar()}>Cancelar pedido</Button> : null}
      {permitidas.size === 0 && !somente ? <span className={styles.dim}>Este pedido não aceita ações.</span> : null}

      {retomando ? (
        <Dialog open onClose={() => setRetomando(false)} title="Retomar o pedido" icon={Play}
                footer={(
                  <>
                    <Button variant="ghost" onClick={() => setRetomando(false)}>Voltar</Button>
                    <Button onClick={() => void retomar('recuperar')}>Recuperar o que cabe na janela</Button>
                    <Button variant="primary" onClick={() => void retomar('daqui')}>Daqui para frente</Button>
                  </>
                )}>
          <p>O que venceu durante a pausa pode ser deixado para trás (“daqui para frente”, o padrão: vira ocorrência pulada, com o motivo) ou recuperado, se ainda estiver dentro da janela de recuperação; o resto vira pulada.</p>
        </Dialog>
      ) : null}
      {editando ? <EdicaoDoPedido pedido={pedido} onFechar={() => setEditando(false)} onMudou={depois} /> : null}
    </div>
  );
}
