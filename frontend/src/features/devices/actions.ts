import {
  AppWindow, Eraser, House, Moon, PackagePlus, Play, Plus, RotateCcw, Square, SquareStack, Sunrise, Undo2, type LucideIcon,
} from 'lucide-react';
import { createElement } from 'react';
import { create } from 'zustand';
import { api } from '../../api/client';
import type { Command, CommandState, InstanceAction, InstanceActionParams } from '../../api/types';
import { confirm } from '../../components/Confirm';
import { plural } from '../../lib/format';
import { useAppStore } from '../../store/app';
import { toast, toastError } from '../../store/toasts';

/**
 * `done` é o que se diz ao ENVIAR (sempre "solicitado"); `confirmed` é o que se diz quando o aparelho confirmou.
 * A distinção existe porque a interface mostrava toast verde no `202` e chamava de sucesso o que só tinha sido
 * aceito — em aparelho de outra máquina, "Resetar dados" era recusado no fundo e ficava indistinguível de êxito.
 */
export const ACTION_META: Record<InstanceAction, { label: string; done: string; confirmed: string; icon: LucideIcon }> = {
  create: { label: 'Criar AVD', done: 'Criação do AVD solicitada', confirmed: 'AVD criado', icon: Plus },
  start: { label: 'Iniciar', done: 'Inicialização solicitada', confirmed: 'Iniciada', icon: Play },
  stop: { label: 'Parar', done: 'Parada solicitada', confirmed: 'Parada', icon: Square },
  restart: { label: 'Reiniciar', done: 'Reinício solicitado', confirmed: 'Reiniciada', icon: RotateCcw },
  reset: { label: 'Resetar dados', done: 'Reset de dados solicitado', confirmed: 'Dados resetados', icon: Eraser },
  // O verbo continua `install_apk` (é o que o backend conhece), mas o que ele faz mudou: resolve a versão
  // PROMOVIDA do pacote e instala pela camada de releases, com hash, assinatura aprovada e estado observado.
  // O rótulo antigo prometia "instale este arquivo que eu configurei", que é justamente o caminho que sumiu.
  // O rótulo curto é o do botão (o antigo "Instalar versão promovida" cortava no grid de duas colunas e não dizia
  // QUAL app). O que será instalado aparece no menu, antes do clique: "Instalar Instagram 412.0 (promovida)".
  install_apk: { label: 'Instalar app', done: 'Instalação solicitada',
                 confirmed: 'App instalado e verificado', icon: PackagePlus },
  open_app: { label: 'Abrir app', done: 'Abertura do app solicitada', confirmed: 'App aberto', icon: AppWindow },
  home: { label: 'Início', done: 'Tecla Início enviada', confirmed: 'Tecla Início confirmada', icon: House },
  back: { label: 'Voltar', done: 'Tecla Voltar enviada', confirmed: 'Tecla Voltar confirmada', icon: Undo2 },
  recents: { label: 'Recentes', done: 'Tecla Recentes enviada', confirmed: 'Tecla Recentes confirmada', icon: SquareStack },
  hibernate: { label: 'Hibernar', done: 'Hibernação solicitada', confirmed: 'Hibernada', icon: Moon },
  wake: { label: 'Acordar', done: 'Despertar solicitado', confirmed: 'Acordada', icon: Sunrise },
};

/**
 * Estados em que o comando AINDA pode agir no aparelho. Enquanto um deles estiver de pé, o aparelho está
 * ocupado — e não apenas durante a requisição HTTP, que era a única proteção que existia: em aparelho remoto o
 * `202` volta em milissegundos e o boot leva até 480 s, então o botão voltava a ficar clicável no meio da ação.
 */
const EM_VOO: readonly CommandState[] = ['created', 'dispatched', 'acked', 'running', 'cancel_requested'];

/** Verbos de ciclo de vida: são os que disputam o aparelho. Teclas (`home`, `back`) não mexem no ciclo. */
const CICLO_DE_VIDA: readonly InstanceAction[] = [
  'create', 'start', 'stop', 'restart', 'reset', 'hibernate', 'wake', 'install_apk', 'open_app',
];

/** Versão pura, para o seletor do componente: devolve o próprio comando quando ele ainda pode agir. */
export function comandoAbertoDe(cmd: Command | undefined): Command | undefined {
  return cmd && EM_VOO.includes(cmd.state) ? cmd : undefined;
}

/** O comando ainda em voo naquele aparelho, se houver. É a mesma pergunta que o pré-voo do backend faz. */
export function comandoEmVoo(id: string): Command | undefined {
  return comandoAbertoDe(useAppStore.getState().lastCommand[id]);
}

/** Por que este verbo está bloqueado agora, ou `undefined` quando não está. Vai para o tooltip do botão. */
export function motivoDoComando(cmd: Command | undefined, id: string, action: InstanceAction): string | undefined {
  if (!CICLO_DE_VIDA.includes(action)) return undefined;
  const aberto = comandoAbertoDe(cmd);
  if (!aberto) return undefined;
  const rotulo = ACTION_META[aberto.verb as InstanceAction]?.label ?? aberto.verb;
  return `${id} está ocupado: “${rotulo}” em andamento. Espere o desfecho antes de pedir outra coisa.`;
}

export function motivoBloqueado(id: string, action: InstanceAction): string | undefined {
  return motivoDoComando(useAppStore.getState().lastCommand[id], id, action);
}

/**
 * Pede o cancelamento de um comando ainda aberto — a ponta que faltava de `cancel_requested`.
 *
 * O toast NUNCA diz "cancelado": pedir não é ter conseguido, e é o backend que responde o que foi possível fazer
 * (interromper o boot aqui, avisar o worker, ou apenas registrar o pedido). O desfecho de verdade chega depois,
 * pelo mesmo acompanhamento por evento de qualquer outro comando.
 */
export async function cancelarComando(cmd: Command): Promise<boolean> {
  const rotulo = ACTION_META[cmd.verb as InstanceAction]?.label ?? cmd.verb;
  try {
    // Sem nota: o "de onde" (`origin`) o backend compõe no motivo — no texto, o id do aparelho passava pela triagem
    // de credencial, e um id fora do padrão recusava o pedido.
    const r = await api.cancelCommand(cmd.id, { origin: 'panel' });
    toast({
      tone: r.delivered ? 'info' : 'warning', key: `cancel-${cmd.id}`,
      title: `${rotulo} em ${cmd.instance_id}: cancelamento pedido`,
      hint: r.detail,
    });
    return true;
  } catch (e) {
    toastError('Não foi possível pedir o cancelamento', e);
    return false;
  }
}

/** Requisições de ação em voo, para desabilitar botões e evitar cliques duplos. */
interface BusyStore {
  busy: Record<string, InstanceAction | undefined>;
  bulkBusy: InstanceAction | null;
}

export const useBusyStore = create<BusyStore>(() => ({ busy: {}, bulkBusy: null }));

function setBusy(id: string, action: InstanceAction | undefined): void {
  useBusyStore.setState((s) => ({ busy: { ...s.busy, [id]: action } }));
}

async function confirmReset(ids: readonly string[]): Promise<boolean> {
  const list = ids.join(', ');
  const mapa = useAppStore.getState().instances;
  const temLoja = ids.some((i) => mapa[i]?.kind === 'store');
  const { confirmed } = await confirm({
    title: ids.length === 1 ? `Resetar dados de ${ids[0]}?` : `Resetar dados de ${ids.length} instâncias?`,
    danger: true,
    confirmLabel: 'Apagar dados e resetar',
    cancelLabel: 'Cancelar',
    body: createElement(
      'div',
      null,
      createElement('p', null, 'Isto ', createElement('strong', null, 'apaga todos os dados'), ' do emulador: apps instalados, contas conectadas e configurações. Não é possível desfazer.'),
      createElement('p', { style: { marginTop: 8 } }, createElement('strong', null, 'Afetadas: '), list),
      // A loja guarda o login da conta Google na Play Store: resetá-la obriga a entrar de novo, à mão, na janela dela.
      temLoja ? createElement('p', { style: { marginTop: 8 } },
        createElement('strong', null, 'Atenção: '),
        'este é o aparelho-loja. O reset apaga também o login da conta Google na Play Store, que terá de ser refeito '
        + 'à mão, na janela do emulador.') : null,
    ),
  });
  return confirmed;
}

const TERMINAL: readonly CommandState[] = ['succeeded', 'failed', 'uncertain', 'rejected', 'cancelled'];

/**
 * Acompanhamento por EVENTO, sem teto.
 *
 * O que havia antes: uma sondagem presa à aba, com teto de 210 s — menor que o prazo que o próprio backend
 * concede ao comando (540 s no `start`, 600 s no `restart`/`reset`). Em boot a frio remoto, o caso real
 * registrado em 21/09, o usuário recebia "ainda em andamento" e nunca ficava sabendo se terminou em sucesso,
 * falha ou incerto; o comando daquele dia fechou 273 s DEPOIS de o painel desistir. E um único erro de rede em
 * qualquer GET do laço (`catch { return }`) apagava o acompanhamento em silêncio.
 *
 * Agora quem conta o desfecho é o evento `command.updated`, que já chega por WebSocket e já é guardado no
 * store: não importa quanto o comando demore nem qual aba pediu. A sondagem continua existindo, mas como
 * RECONCILIAÇÃO — para o caso de o WebSocket estar caído —, com erro tratado como "tenta de novo", nunca como
 * "desisti". Ela começa rápida (verbo curto responde em segundos) e afrouxa depois, porque um boot leva
 * minutos e não há por que perguntar a cada 1,5 s durante dez minutos.
 */
const INTERVALO_MS = 1_500;
const INTERVALO_LONGO_MS = 15_000;
const APERTADO_POR_MS = 30_000;

interface Acompanhado { id: string; action: InstanceAction; desde: number }

const acompanhados = new Map<string, Acompanhado>();
let cancelarAssinatura: (() => void) | null = null;
let timer: ReturnType<typeof setTimeout> | null = null;

function relatar(cmd: Command, alvo: Acompanhado): void {
  const { id, action } = alvo;
  const meta = ACTION_META[action];
  if (cmd.state === 'succeeded') {
    toast({ tone: 'success', title: `${meta.confirmed} — ${id}`, key: `cmd-${id}` });
  } else if (cmd.state === 'uncertain') {
    toast({
      tone: 'warning', key: `cmd-${id}`,
      title: `${meta.label} em ${id}: resultado desconhecido`,
      details: cmd.reason ? [cmd.reason] : undefined,
      hint: 'Nada será repetido automaticamente. O comando fica na lista do aparelho, com "Verificar agora" e '
        + '"Marcar como…" — confira o aparelho antes de tentar de novo.',
    });
  } else {
    // `cancelled` não é má notícia: alguém PEDIU para parar, e ele só é gravado quando o efeito não aconteceu.
    // Em vermelho, o desfecho de um cancelamento pedido no painel parecia falha do aparelho.
    const cancelado = cmd.state === 'cancelled';
    const rotulo = cmd.state === 'rejected' ? 'recusado' : cancelado ? 'cancelado' : 'falhou';
    toast({
      tone: cancelado ? 'info' : 'danger', key: `cmd-${id}`,
      title: `${meta.label} em ${id}: ${rotulo}`,
      details: cmd.reason ? [cmd.reason] : undefined,
      ...(cmd.state === 'rejected' || cancelado ? { hint: 'Nada foi executado no aparelho.' } : {}),
    });
  }
}

/** Avalia um comando recém-conhecido; devolve `true` quando ele fechou e saiu do acompanhamento. */
function avaliar(commandId: string, cmd: Command): boolean {
  const alvo = acompanhados.get(commandId);
  if (!alvo || cmd.id !== commandId || !TERMINAL.includes(cmd.state)) return false;
  acompanhados.delete(commandId);
  relatar(cmd, alvo);
  if (acompanhados.size === 0) pararDeAcompanhar();
  return true;
}

function pararDeAcompanhar(): void {
  cancelarAssinatura?.();
  cancelarAssinatura = null;
  if (timer !== null) clearTimeout(timer);
  timer = null;
}

/** Reconciliação: pergunta o estado de cada comando acompanhado. Erro aqui NÃO encerra o acompanhamento. */
async function reconciliar(): Promise<void> {
  for (const [commandId] of [...acompanhados]) {
    try {
      avaliar(commandId, await api.command(commandId));
    } catch {
      // A rede piscou. O comando continua vivo no backend e o evento ainda pode chegar: seguimos acompanhando.
    }
  }
  if (acompanhados.size === 0) return;
  const maisAntigo = Math.min(...[...acompanhados.values()].map((a) => a.desde));
  const proximo = Date.now() - maisAntigo > APERTADO_POR_MS ? INTERVALO_LONGO_MS : INTERVALO_MS;
  if (timer !== null) clearTimeout(timer);
  timer = setTimeout(() => void reconciliar(), proximo);
}

/** Acompanha o comando até o desfecho e conta a verdade — inclusive "não sei". Sem prazo para desistir. */
function acompanharDesfecho(commandId: string, id: string, action: InstanceAction): void {
  acompanhados.set(commandId, { id, action, desde: Date.now() });
  if (cancelarAssinatura === null) {
    // `command.updated` já chega ao reducer e vira `lastCommand`: é dele que sai o desfecho, venha quando vier.
    cancelarAssinatura = useAppStore.subscribe((estado, anterior) => {
      if (estado.lastCommand === anterior.lastCommand) return;
      for (const [cid, alvo] of [...acompanhados]) {
        const cmd = estado.lastCommand[alvo.id];
        if (cmd) avaliar(cid, cmd);
      }
    });
  }
  void reconciliar();
}

/** Ação em UMA instância. `reset` sempre passa por confirmação e envia `{confirm:true}`. */
export async function runInstanceAction(id: string, action: InstanceAction, params?: InstanceActionParams): Promise<boolean> {
  if (useBusyStore.getState().busy[id]) return false;
  // Um aparelho, uma operação — a mesma regra que o backend impõe com 409 `device_busy`. Aqui ela evita a
  // requisição inútil e diz o motivo, em vez de o clique virar um comando recusado no histórico.
  const bloqueio = motivoBloqueado(id, action);
  if (bloqueio) {
    toast({ tone: 'warning', title: `${ACTION_META[action].label} em ${id}: aparelho ocupado`,
            details: [bloqueio], key: `busy-${id}` });
    return false;
  }
  let finalParams: InstanceActionParams = { ...params };
  if (action === 'reset') {
    if (!(await confirmReset([id]))) return false;
    finalParams.confirm = true;
  }
  // Chave por clique: se a rede duplicar a requisição, o backend devolve o MESMO comando em vez de agir duas vezes.
  finalParams.idempotency_key ??= `${id}:${action}:${crypto.randomUUID()}`;
  setBusy(id, action);
  try {
    const aceito = await api.instanceAction(id, action, finalParams);
    // Tom neutro: a única coisa verdadeira neste instante é que o pedido foi aceito.
    const alvo = aceito.install_target;
    toast({ tone: 'info', title: `${ACTION_META[action].done} — ${id}`, key: `act-${id}`,
            // Pedido aceito não é app instalado: o desfecho só vem depois de o aparelho ser relido.
            hint: alvo ? `${alvo.app_name} ${alvo.version_name} (versão promovida), pelo catálogo via ADB. `
              + 'Só vira “instalado” depois de o aparelho confirmar a versão.' : undefined });
    acompanharDesfecho(aceito.command_id, id, action);
    return true;
  } catch (e) {
    toastError(`Não foi possível executar “${ACTION_META[action].label}” em ${id}`, e);
    return false;
  } finally {
    setBusy(id, undefined);
  }
}

/** Ação em lote: o backend devolve quem aceitou e quem rejeitou (com motivo) — mostramos os dois. */
export async function runBulkAction(ids: readonly string[], action: InstanceAction, params?: InstanceActionParams): Promise<void> {
  if (ids.length === 0 || useBusyStore.getState().bulkBusy) return;
  let finalParams: InstanceActionParams = { ...params };
  if (action === 'reset') {
    if (!(await confirmReset(ids))) return;
    finalParams.confirm = true;
  }
  // O lote também leva chave de idempotência. Faltava: o backend só montava chave por aparelho quando o lote
  // trazia uma, então reenviar um lote (rede duplicando, clique repetido) criava comandos novos em todos eles.
  finalParams.idempotency_key ??= `bulk:${action}:${crypto.randomUUID()}`;
  useBusyStore.setState({ bulkBusy: action });
  try {
    const res = await api.bulk({ ids: [...ids], action, params: finalParams });
    const accepted = Array.isArray(res?.accepted) ? res.accepted : [];
    const rejected = Array.isArray(res?.rejected) ? res.rejected : [];
    const label = ACTION_META[action].label;
    // Cada aparelho tem seu comando: o desfecho de um não fala pelo do outro.
    for (const c of res?.commands ?? []) {
      if (!c.deduplicated && accepted.includes(c.id)) acompanharDesfecho(c.command_id, c.id, action);
    }
    if (rejected.length === 0) {
      // `info`, não `success`: neste instante só se sabe que foram aceitas.
      toast({ tone: 'info', title: `${label}: ${plural(accepted.length, 'aparelho aceito', 'aparelhos aceitos')}`,
              hint: 'O desfecho de cada aparelho aparece à medida que for confirmado.' });
    } else {
      toast({
        tone: accepted.length > 0 ? 'warning' : 'danger',
        title: `${label}: ${accepted.length} aceito(s), ${rejected.length} recusado(s)`,
        details: rejected.map((r) => `${r.id}: ${r.reason}`),
        hint: 'Resolva o motivo indicado e repita a ação apenas nos aparelhos recusados.',
      });
    }
  } catch (e) {
    toastError(`Não foi possível executar “${ACTION_META[action].label}” em lote`, e);
  } finally {
    useBusyStore.setState({ bulkBusy: null });
  }
}
