import { CalendarClock, CheckCheck, ChevronRight, Hand, ListChecks, Play, Shuffle, Smartphone, Sparkles, TriangleAlert, Users, Wand2, X } from 'lucide-react';
import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import { ApiError, api, toApiError } from '../../api/client';
import type { PedidoCorpo } from '../../api/pedidos';
import type {
  CreateRunRequest, DevicePolicy, FlowCoverage, PreflightRefusal, ResolvedTarget, ResolveTargetsRequest,
  ResolveTargetsResponse, RunMode, RunTargetsSuggestion, TargetQuestion,
} from '../../api/types';
import { Button } from '../../components/Button';
import { Card } from '../../components/Card';
import { TextArea } from '../../components/Field';
import ui from '../../components/ui.module.css';
import { balanceAlerts, balanceBrief, balanceStateLabel, balanceTone, balanceUsage } from '../../lib/aiBalance';
import { cx, plural, truncate } from '../../lib/format';
import { IdempotencyKeeper } from '../../lib/idempotency';
import { instanceShort } from '../../lib/ids';
import { isString, isStringArray, loadJson, saveJson } from '../../lib/storage';
import { aiAvailable, selectTaskOrder, useAppStore } from '../../store/app';
import { toast, toastError } from '../../store/toasts';
import { useUiStore } from '../../store/ui';
import { NovoPedido } from '../pedidos/NovoPedido';
import { handleDe, idsDosAparelhos, nomeDe } from '../profiles/pessoa';
import { usePersonas } from '../profiles/usePersonas';
import { AssistenteDoComando } from './AssistenteDoComando';
import { RECUSAS_DE_ALVO, ecoDosAlvos, recusaDosAlvos, responder, type Eco, type RecusaDeAlvo } from './alvos';
import styles from './CommandPanel.module.css';
import { DistributeTarget, parseCount, useDistributionPreview } from './DistributeTarget';
import { historicoSeguro, pareceCredencial, pushHistory } from './history';
import { PersonaTarget, usePreviaDosAlvos } from './PersonaTarget';
import { PreviaDosAlvos, type NomeDaPersona } from './PreviaDosAlvos';
import { SugestaoDeAlvos } from './SugestaoDeAlvos';

/** ADR-040: não existe mais campo de senha no comando — a credencial é da conta da persona. */
export const SENHA_NO_COMANDO =
  'O comando contém uma senha: tire-a do texto e guarde-a na conta da persona (Personas → a pessoa → guia '
  + '“Contas e acesso”), com o consentimento. A automação a digita só no app e no site daquela conta.';

export const COMMAND_PLACEHOLDER =
  'Nos aparelhos selecionados, abra o QA Messenger, entre na conversa com QA-001 e envie “Teste POC {instance_id} {run_id}”. Confirme que apareceu como enviada.';

const EXAMPLES: { label: string; text: string }[] = [
  {
    label: 'Abrir uma tela',
    text: 'Nos aparelhos selecionados, abra o QA Messenger e vá até a tela de Configurações. Confirme que o título “Configurações” está visível.',
  },
  {
    label: 'Preencher formulário de perfil',
    text: 'Nos aparelhos selecionados, abra o QA Messenger, vá em Perfil, preencha o nome com “Tester {instance_id}” e a bio com “Conta de teste da POC” e salve. Confirme que os dados aparecem salvos.',
  },
  { label: 'Enviar mensagem de teste', text: COMMAND_PLACEHOLDER },
];

/** A recusa do pré-voo, quando é isso que o backend devolveu (409 `preflight`), senão `null`. */
function preflightOf(err: { code: string; detail: Record<string, unknown> | null }): PreflightRefusal | null {
  if (err.code !== 'preflight' || !err.detail) return null;
  const devices = err.detail.devices;
  const ready = err.detail.ready;
  if (!Array.isArray(devices)) return null;
  return {
    devices: devices as PreflightRefusal['devices'],
    ready: Array.isArray(ready) ? (ready as string[]) : [],
  };
}

/** Onde executar: o sistema decide (ADR-050: pelo pedido, pelo perfil das personas, pela fila e pela carga), ou, à
 *  mão, os aparelhos marcados na grade, as personas (o sistema escolhe o aparelho delas) ou N aparelhos distribuídos
 *  pela carga dos servidores. */
type ModoDoAlvo = 'auto' | 'selecao' | 'persona' | 'distribuir';

/** Chave nova de propósito: com o Automático, todo mundo começa nele uma vez, mesmo quem tinha outro modo salvo. */
const CHAVE_DO_MODO = 'commandTargetV2';

function modoSalvo(): ModoDoAlvo {
  const v = loadJson(CHAVE_DO_MODO, isString);
  return v === 'distribuir' || v === 'persona' || v === 'selecao' ? v : 'auto';
}

/** A sugestão do modo Automático na tela: de qual texto e para qual botão (planejar/executar). */
interface Sugestao {
  mode: RunMode;
  dados: RunTargetsSuggestion | null;
  carregando: boolean;
  erro: string | null;
}

function politicaSalva(): DevicePolicy {
  const v = loadJson('commandPolicy', isString);
  return v === 'primary' || v === 'all' ? v : 'one';
}

/**
 * A confirmação que o 409 `alvos_nao_confirmados` abre: o texto do comando citou destinos ("no android-03", "peça
 * para o André") e a execução não nasce sem que a pessoa os veja. Começa com os alvos do próprio erro e troca pela
 * prévia inteira assim que ela chega (o erro só traz os de origem `texto`, e o eco precisa de todos).
 */
interface Confirmacao {
  mode: RunMode;
  previa: ResolveTargetsResponse;
  recusa: RecusaDeAlvo | null;
  carregando: boolean;
}

function previaDoErro(err: ApiError, comando: string): ResolveTargetsResponse {
  const d = err.detail ?? {};
  return {
    targets: Array.isArray(d.targets) ? (d.targets as ResolvedTarget[]) : [],
    questions: [],
    command_sem_destinos: typeof d.command_sem_destinos === 'string' ? d.command_sem_destinos : comando,
    warnings: [],
  };
}

/** Rolagem "melhor esforço": nunca pode derrubar o fluxo que a chamou. */
function scrollToElement(el: Element | null, block: ScrollLogicalPosition): void {
  try {
    if (el && typeof el.scrollIntoView === 'function') el.scrollIntoView({ block, behavior: 'smooth' });
  } catch {
    /* ambiente sem suporte a scrollIntoView */
  }
}

/** Fora do componente: a chave de uma intenção pendente sobrevive à troca de tela. */
const keeper = new IdempotencyKeeper();

/** Depois de um sucesso, segura os botões por um instante para que um clique duplo tardio não crie outra execução. */
const COOLDOWN_MS = 2000;

export function CommandPanel() {
  const hydrated = useAppStore((s) => s.hydrated);
  const aiOk = useAppStore(aiAvailable);
  const ai = useAppStore((s) => s.health?.ai ?? null);
  const instancesMap = useAppStore((s) => s.instances);
  const fullOrder = useAppStore((s) => s.instanceOrder);
  // Alvo de comando: todos menos a loja (o backend recusaria, e 11 alvos estourariam o teto de 10).
  const order = useMemo(() => selectTaskOrder({ instances: instancesMap, instanceOrder: fullOrder }), [instancesMap, fullOrder]);
  const upsertRun = useAppStore((s) => s.upsertRun);
  const selectedIds = useUiStore((s) => s.selectedIds);
  const setSelection = useUiStore((s) => s.setSelection);
  const clearSelection = useUiStore((s) => s.clearSelection);
  const selectRun = useUiStore((s) => s.selectRun);
  const draftRequest = useUiStore((s) => s.commandDraftRequest);
  const novoPedidoRequest = useUiStore((s) => s.novoPedidoRequest);
  const novoPedidoAtendido = useUiStore((s) => s.novoPedidoAtendido);
  const apps = useAppStore((s) => s.apps);

  // Alvo do comando: os aparelhos marcados na grade, as personas (ADR-044: o sistema escolhe o aparelho delas, com
  // prévia obrigatória), ou "distribuir entre servidores" (o backend escolhe N aparelhos do app pela carga de cada
  // máquina — Limites → Por servidor).
  const [target, setTarget] = useState<ModoDoAlvo>(modoSalvo);
  const [distCount, setDistCount] = useState(() => loadJson('commandDistCount', isString) ?? '2');
  const [distApp, setDistApp] = useState(() => loadJson('commandDistApp', isString) ?? '');
  const distribuir = target === 'distribuir';
  const porPersona = target === 'persona';
  const automatico = target === 'auto';
  // O controle "Automático | Manual" tem DUAS posições; dentro do Manual, três jeitos de escolher. Voltar ao Manual
  // reabre o último jeito usado, não o primeiro da lista.
  const [manualPreferido, setManualPreferido] = useState<Exclude<ModoDoAlvo, 'auto'>>(target === 'auto' ? 'selecao' : target);
  const mudarModo = (m: ModoDoAlvo) => {
    setTarget(m);
    if (m !== 'auto') setManualPreferido(m);
    saveJson(CHAVE_DO_MODO, m);
  };
  const count = parseCount(distCount);
  // Item 24.6 (R9): o Comando não escolhe mais "um app" pela pessoa. Antes, sem app escolhido, valia o app mais comum
  // do parque, e o comando entre apps era repartido pelos aparelhos de um app que ninguém pediu. Agora, sem escolha,
  // a distribuição é pelos apps que o COMANDO usa (o backend lê como na criação); escolher um app só restringe.
  const appId = distApp && apps.some((a) => a.id === distApp) ? distApp : '';

  const [command, setCommand] = useState(() => {
    const rascunho = loadJson('commandDraft', isString) ?? '';
    return pareceCredencial(rascunho) ? '' : rascunho;
  });
  // Item 11.5: os últimos comandos usados, para reaproveitar sem redigitar — "Repetir" já cobre a MESMA
  // execução; isto cobre o próximo comando parecido.
  const [history, setHistory] = useState<string[]>(() => {
    // ADR-025: entrada com senha gravada antes desta regra (a execução 22d65f deixou uma) sai na primeira leitura.
    const salvo = loadJson('commandHistory', isStringArray) ?? [];
    const seguro = historicoSeguro(salvo);
    if (seguro.length !== salvo.length) saveJson('commandHistory', seguro);
    return seguro;
  });
  const [inFlight, setInFlight] = useState<RunMode | null>(null);
  // Recusa do pré-voo ainda na tela: fica até a pessoa seguir só com os aptos, resolver o motivo, ou fechar.
  const [preflight, setPreflight] = useState<(PreflightRefusal & { mode: RunMode; eco?: Eco }) | null>(null);
  const [cooldown, setCooldown] = useState(false);
  // Recusa da resolução de alvos no envio (sem interseção, sem vínculo, aparelho repetido…): fica na tela, com o que
  // fazer, até a pessoa mudar o pedido ou fechar.
  const [recusaDoEnvio, setRecusaDoEnvio] = useState<RecusaDeAlvo | null>(null);
  const [confirmacao, setConfirmacao] = useState<Confirmacao | null>(null);
  // "Por persona": quem faz, quantos aparelhos de cada uma e, se quiser, em quais.
  const [personaIds, setPersonaIds] = useState<string[]>(() => loadJson('commandPersonas', isStringArray) ?? []);
  const [politica, setPolitica] = useState<DevicePolicy>(politicaSalva);
  const [estreitar, setEstreitar] = useState<string[]>([]);
  const pessoas = usePersonas(porPersona || confirmacao !== null);
  // Item 7.7: quanto vai custar repetir o fluxo que este comando casa — só um palpite de leitura, nunca bloqueia.
  const [estimate, setEstimate] = useState<FlowCoverage | null>(null);
  // ADR-050: a sugestão de quem faz e onde, no modo Automático. Nada é criado até confirmar.
  const [sugestao, setSugestao] = useState<Sugestao | null>(null);
  // ADR-047: o assistente aberto (a chave remonta a conversa a cada "Refinar com IA").
  const [assistente, setAssistente] = useState<number | null>(null);
  // Item 28.9: "Repetir ou acompanhar…" abre a criação de um pedido persistente (prévia obrigatória, sem custo).
  const [pedidoAberto, setPedidoAberto] = useState(false);
  const textRef = useRef<HTMLTextAreaElement>(null);
  const fieldId = useId();

  useEffect(() => {
    // Rascunho com senha não vai ao localStorage: o painel recusa enviá-lo, e guardá-lo seria a senha em claro no disco.
    const t = setTimeout(() => saveJson('commandDraft', pareceCredencial(command) ? '' : command), 400);
    return () => clearTimeout(t);
  }, [command]);

  useEffect(() => {
    const trimmed = command.trim();
    if (trimmed.length === 0) {
      setEstimate(null);
      return;
    }
    const ctrl = new AbortController();
    const t = setTimeout(() => {
      api.flowsMatch(trimmed, ctrl.signal).then(setEstimate).catch(() => setEstimate(null));
    }, 400);
    return () => {
      clearTimeout(t);
      ctrl.abort();
    };
  }, [command]);

  useEffect(() => {
    if (!draftRequest) return;
    setCommand(draftRequest.text);
    textRef.current?.focus();
    scrollToElement(textRef.current, 'center');
  }, [draftRequest]);

  // "Novo pedido" da tela Pedidos: abre o painel "Repetir ou acompanhar" e leva o foco ao texto do comando.
  useEffect(() => {
    if (novoPedidoRequest === null) return;
    setPedidoAberto(true);
    novoPedidoAtendido();
    textRef.current?.focus();
    scrollToElement(textRef.current, 'center');
  }, [novoPedidoRequest, novoPedidoAtendido]);

  const trimmed = command.trim();
  const total = order.length;
  // Texto com senha não vai à prévia da distribuição (nem a rota nenhuma): vai vazio, e a prévia espera.
  const { preview, loading: previewLoading } = useDistributionPreview(
    distribuir, count, appId, pareceCredencial(trimmed) ? '' : trimmed);

  // Persona apagada desde a última visita não fica "selecionada" invisível: some da seleção quando a lista chega.
  const selecionadas = useMemo(
    () => (pessoas ? personaIds.filter((id) => pessoas.some((p) => p.id === id)) : personaIds), [pessoas, personaIds]);
  const aparelhosDelas = useMemo(() => {
    const ids = new Set<string>();
    for (const p of pessoas ?? []) if (selecionadas.includes(p.id)) for (const d of idsDosAparelhos(p)) ids.add(d);
    return ids;
  }, [pessoas, selecionadas]);
  // Estreitar só vale com aparelho DELAS: um que sobrou de outra seleção seria sempre `sem_intersecao`.
  const estreitarValido = useMemo(() => estreitar.filter((id) => aparelhosDelas.has(id)), [estreitar, aparelhosDelas]);
  const mudarPessoas = (ids: string[]) => {
    setPersonaIds(ids);
    saveJson('commandPersonas', ids);
  };
  const nomeDaPersona: NomeDaPersona = useCallback((id) => {
    const p = pessoas?.find((x) => x.id === id);
    return p ? { nome: nomeDe(p), handle: handleDe(p), temFoto: p.has_avatar } : { nome: id, handle: null };
  }, [pessoas]);

  // A prévia OBRIGATÓRIA (§7.6) do modo por persona. Texto com senha não vai a rota nenhuma, nem à prévia.
  const pedidoDaPrevia: ResolveTargetsRequest | null = porPersona && selecionadas.length > 0 && trimmed.length >= 3
    && !pareceCredencial(trimmed)
    ? { command: trimmed, profile_ids: selecionadas, instance_ids: estreitarValido, device_policy: politica }
    : null;
  const previaPersona = usePreviaDosAlvos(pedidoDaPrevia);
  const previaEmDia = previaPersona.chave !== null && previaPersona.chave === previaPersona.pedidoChave;
  const ecoPersona = porPersona && previaEmDia && previaPersona.previa ? ecoDosAlvos(previaPersona.previa.targets) : null;

  // Mudou o texto, a seleção ou o modo: a confirmação e a recusa na tela eram de OUTRO pedido.
  const selecaoChave = selectedIds.join(',');
  useEffect(() => {
    setConfirmacao(null);
    setRecusaDoEnvio(null);
    setSugestao(null);
  }, [trimmed, selecaoChave, target, personaIds, politica, estreitar]);

  const alvoInvalido: string | null = automatico ? null
    : distribuir
    ? (count === null ? 'Informe quantos aparelhos (de 1 a 64).'
      : preview && preview.picks.length === 0 ? `Nenhum aparelho disponível para distribuir${preview.reasons[0] ? `: ${preview.reasons[0]}` : ''}.`
      : null)
    : porPersona ? (selecionadas.length === 0 ? 'Escolha ao menos uma persona.' : null)
    : selectedIds.length === 0 ? 'Selecione ao menos um aparelho na grade abaixo.' : null;

  // Por persona só executa o que a prévia mostrou: prévia do pedido ATUAL, sem recusa e sem pergunta pendente.
  const previaImpede: string | null = !porPersona ? null
    : trimmed.length < 3 ? 'Escreva o comando (ao menos 3 letras) para ver quem faz e onde.'
    : !previaEmDia ? 'Aguardando a prévia dos alvos…'
    : previaPersona.recusa ? `${previaPersona.recusa.titulo}: ${previaPersona.recusa.passo}`
    : (previaPersona.previa?.questions.length ?? 0) > 0 ? 'Responda às perguntas da prévia antes de executar.'
    : ecoPersona?.erro ?? null;

  const reason: string | null =
    !hydrated ? 'Aguardando a conexão com o servidor.'
    : !aiOk ? 'IA não configurada: defina a chave no arquivo .env do servidor (o restante do painel continua funcionando).'
    : alvoInvalido ? alvoInvalido
    : trimmed.length === 0 ? 'Escreva o comando em linguagem natural.'
    // ADR-040: a execução não carrega credencial. A senha mora na conta da persona, com consentimento por conta, e a
    // automação a digita só no app e no site daquela conta.
    : pareceCredencial(trimmed) ? SENHA_NO_COMANDO
    : previaImpede;
  const nAlvos = previaPersona.previa?.targets.length ?? 0;
  const alvoTexto = automatico
    ? 'quem o sistema escolher'
    : distribuir
    ? plural(count ?? 0, 'aparelho distribuído', 'aparelhos distribuídos')
    : porPersona ? `${plural(nAlvos, 'aparelho', 'aparelhos')} de ${plural(selecionadas.length, 'persona', 'personas')}`
    : plural(selectedIds.length, 'aparelho', 'aparelhos');

  /** O que vai à prévia com a seleção de agora, no modo de agora. */
  const pedidoAtual = (): ResolveTargetsRequest => (porPersona
    ? { command: trimmed, profile_ids: selecionadas, instance_ids: estreitarValido, device_policy: politica }
    : { command: trimmed, instance_ids: [...selectedIds] });

  // 409 `alvos_nao_confirmados` (nos dois modos): o texto citou destinos. Mostra a prévia inteira e pede confirmação
  // em vez de um toast — é o eco dela que faz a execução nascer.
  const abrirConfirmacao = async (mode: RunMode, err: ApiError) => {
    setConfirmacao({ mode, previa: previaDoErro(err, trimmed), recusa: null, carregando: true });
    try {
      const previa = await api.resolveRunTargets(pedidoAtual());
      setConfirmacao((c) => (c ? { ...c, previa, carregando: false } : c));
    } catch (e) {
      // Sem a prévia inteira fica a do erro (os alvos de origem `texto`), com a recusa quando for de alvo.
      const falha = toApiError(e);
      const recusa = RECUSAS_DE_ALVO.has(falha.code) ? recusaDosAlvos(falha) : null;
      setConfirmacao((c) => (c ? { ...c, recusa, carregando: false } : c));
    }
  };

  /** Automático: pergunta ao backend quem faz e onde (pode custar uma chamada de IA) e mostra antes de criar. */
  const pedirSugestao = async (mode: RunMode) => {
    setSugestao({ mode, dados: null, carregando: true, erro: null });
    try {
      const dados = await api.suggestRunTargets({ command: trimmed });
      setSugestao((s) => (s ? { ...s, dados, carregando: false } : s));
    } catch (e) {
      const err = toApiError(e);
      setSugestao((s) => (s ? { ...s, carregando: false, erro: err.message || 'Não foi possível sugerir agora.' } : s));
    }
  };

  const submit = async (mode: RunMode, onlyReady = false, ecoConfirmado?: Eco) => {
    if (reason || inFlight || cooldown) return;
    if (automatico && !ecoConfirmado) {
      void pedirSugestao(mode);
      return;
    }
    // No modo por persona, o que vai é o ECO da prévia (os alvos que a pessoa viu, fixados); na confirmação de
    // destinos do texto, o eco da confirmação.
    const eco = ecoConfirmado ?? (porPersona ? ecoPersona?.eco ?? undefined : undefined);
    if (porPersona && !eco) return;
    // A distribuição e o eco entram na intenção: mudar app, quantidade ou alvos é outro pedido, com outra chave.
    const intent = {
      command: trimmed, mode,
      instanceIds: distribuir ? [`distribuir:${appId || 'comando'}:${count}`]
        : eco ? [`eco:${JSON.stringify(eco)}`, ...(porPersona ? [`politica:${politica}`] : [])]
        : selectedIds,
    };
    // Mesma intenção → mesma chave (cliques repetidos e novas tentativas). Só troca após resposta 2xx.
    const idempotencyKey = keeper.keyFor(intent);
    setInFlight(mode);
    setRecusaDoEnvio(null);
    try {
      let corpo: CreateRunRequest;
      if (distribuir && count !== null) {
        // Faltando aparelho, a prévia já disse quantos e por quê: executar segue com os disponíveis.
        corpo = { command: trimmed, instance_ids: [], idempotency_key: idempotencyKey, mode,
                  distribute: appId ? { count, app_id: appId } : { count },
                  only_ready: onlyReady || (preview !== null && preview.missing > 0) || undefined };
      } else if (eco) {
        corpo = { command: trimmed, instance_ids: eco.instance_ids, idempotency_key: idempotencyKey, mode,
                  only_ready: onlyReady || undefined,
                  ...(eco.targets.length > 0 ? { targets: eco.targets } : {}),
                  ...(porPersona ? { device_policy: politica } : {}) };
      } else {
        corpo = { command: trimmed, instance_ids: [...selectedIds], idempotency_key: idempotencyKey, mode,
                  only_ready: onlyReady || undefined };
      }
      const run = await api.createRun(corpo);
      setPreflight(null);
      setConfirmacao(null);
      setSugestao(null);
      keeper.confirm(intent);
      upsertRun(run);
      selectRun(run.id);
      setHistory((h) => {
        const next = pushHistory(h, trimmed);
        saveJson('commandHistory', next);
        return next;
      });
      if (run.deduplicated) {
        toast({ tone: 'info', title: 'Execução já existente — nenhuma duplicata criada', message: `Mostrando a execução ${run.short_id}.` });
      } else {
        toast({
          tone: 'success',
          title: mode === 'plan' ? `Planejamento ${run.short_id} criado` : `Execução ${run.short_id} criada`,
          message: mode === 'plan' ? 'Revise o plano abaixo e inicie quando estiver de acordo.' : 'Acompanhe o progresso na área de execução.',
        });
      }
      setCooldown(true);
      setTimeout(() => setCooldown(false), COOLDOWN_MS);
      scrollToElement(document.getElementById('execucao'), 'start');
    } catch (e) {
      // Pré-voo: a plataforma explica a limitação ANTES de agendar, por aparelho, e oferece a saída — em vez de
      // aceitar a tarefa, gastar o planejador e bloquear no meio (#51).
      const err = toApiError(e);
      const recusa = preflightOf(err);
      if (recusa) {
        // O eco vai junto: "Seguir só com os aptos" repete o MESMO pedido, só com `only_ready`.
        setPreflight({ ...recusa, mode, eco });
        setInFlight(null);
        return;
      }
      setPreflight(null);
      if (err.code === 'alvos_nao_confirmados') {
        setInFlight(null);
        void abrirConfirmacao(mode, err);
        return;
      }
      if (RECUSAS_DE_ALVO.has(err.code)) {
        setRecusaDoEnvio(recusaDosAlvos(err));
        return;
      }
      toastError(mode === 'plan' ? 'Não foi possível planejar' : 'Não foi possível criar a execução', e, {
        hint: 'Nada foi duplicado: tentar de novo reutiliza a mesma chave de idempotência.',
      });
    } finally {
      setInFlight(null);
    }
  };

  // Refinar não precisa de alvo escolhido nem de prévia em dia: só de texto, IA e nenhuma senha no meio.
  const refinarImpede: string | null =
    !hydrated ? 'Aguardando a conexão com o servidor.'
    : !aiOk ? 'IA não configurada.'
    : trimmed.length < 3 ? 'Escreva o objetivo do seu jeito primeiro; a IA organiza e pergunta o que faltar.'
    : pareceCredencial(trimmed) ? SENHA_NO_COMANDO
    : null;
  const contextoDoAssistente = porPersona ? { profile_ids: selecionadas }
    : distribuir ? {} : { instance_ids: [...selectedIds] };

  // O pedido persistente não gasta IA para ver a prévia (só o Automático pergunta à sugestão, como o Executar já faz).
  const pedidoImpede: string | null =
    !hydrated ? 'Aguardando a conexão com o servidor.'
    : trimmed.length < 3 ? 'Escreva o comando (ao menos 3 letras).'
    : pareceCredencial(trimmed) ? SENHA_NO_COMANDO
    : distribuir ? 'O pedido persistente não distribui por contas (o backend recusa `alvos.distribute`): escolha as personas ou os aparelhos.'
    : alvoInvalido;
  /** Em uma frase, quem fará o pedido do jeito que o Comando está agora (a prévia mostra o resultado de verdade). */
  const quemFazDoPedido: string = distribuir ? 'distribuir por contas (não vale em pedido)'
    : porPersona ? (selecionadas.length > 0 ? `persona ${selecionadas.map((id) => nomeDaPersona(id).nome).join(', ')}` : 'nenhuma persona escolhida')
    : automatico ? 'a IA escolhe (confira na prévia)'
    : selectedIds.length > 0 ? `${selectedIds.join(', ')}` : 'nenhum aparelho marcado';
  /** Quem faz e onde, do jeito que o Comando está agora, no formato dos alvos do pedido. */
  const alvosDoPedido = async (): Promise<PedidoCorpo['alvos']> => {
    if (distribuir) throw new Error('Distribuir por contas não vale em pedido persistente.');   // `pedidoImpede` já barra
    if (porPersona) return { profile_ids: selecionadas, instance_ids: estreitarValido, device_policy: politica };
    if (!automatico) return { instance_ids: [...selectedIds] };
    // Automático: a sugestão é a mesma do Executar (pode custar uma chamada de IA do papel `plan`); só vai o que ela
    // resolveu sem pergunta nem alerta de conduta, fixado em `targets` para o backend não re-escolher.
    const dados = await api.suggestRunTargets({ command: trimmed });
    const eco = ecoDosAlvos(dados.targets);
    // A pergunta que o backend fez é o que a pessoa precisa responder: vai inteira, não resumida.
    const pergunta = dados.questions[0]?.question ?? dados.perguntas[0];
    const impede = dados.alerta_conduta ? 'Pedido não roteado pela regra de conduta das personas.'
      : pergunta ? `A IA não sabe quem deve fazer isto: «${pergunta}»`
      : dados.questions.length > 0 || dados.perguntas.length > 0 ? 'Há uma pergunta sobre quem faz.'
      : eco.erro;
    if (impede || !eco.eco) throw new ApiError(422, 'alvos_a_decidir', impede ?? 'Nenhum aparelho sugerido.');
    return { instance_ids: eco.eco.instance_ids, targets: eco.eco.targets };
  };

  const blocked = reason ?? (cooldown ? 'Execução criada agora há pouco — aguarde um instante para enviar de novo.' : null);

  /**
   * Resposta a uma pergunta da prévia: a opção clicada vira SELEÇÃO (regra em `responder`). Pergunta sobre persona
   * no modo por aparelho leva ao modo por persona — o único que diz "esta persona" —, com os aparelhos marcados
   * como filtro.
   */
  const responderPergunta = (q: TargetQuestion, opcao: string) => {
    const r = responder(q, opcao, porPersona ? selecionadas : [], porPersona ? estreitarValido : selectedIds);
    const semDestinos = (confirmacao?.previa ?? previaPersona.previa)?.command_sem_destinos;
    if (r.semDestinos && semDestinos) setCommand(semDestinos);
    if (q.field === 'instance_id') {
      if (r.aparelhos) (porPersona ? setEstreitar : setSelection)(r.aparelhos);
    } else {
      if (!porPersona) mudarModo('persona');
      if (r.personas) mudarPessoas(r.personas);
      setEstreitar(r.aparelhos ?? (porPersona ? estreitarValido : [...selectedIds]));
    }
    setConfirmacao(null);
  };
  const ecoDaConfirmacao = confirmacao ? ecoDosAlvos(confirmacao.previa.targets) : null;
  const dadosDaSugestao = sugestao?.dados ?? null;
  const ecoDaSugestao = dadosDaSugestao && dadosDaSugestao.targets.length > 0 ? ecoDosAlvos(dadosDaSugestao.targets) : null;
  const sugestaoImpede: string | null = !dadosDaSugestao ? (sugestao?.erro ? 'Tente de novo ou escolha manualmente.' : null)
    : dadosDaSugestao.alerta_conduta ? 'Pedido não roteado pela regra de conduta das personas.'
    : dadosDaSugestao.questions.length > 0 || dadosDaSugestao.perguntas.length > 0
      ? 'Há uma pergunta sobre quem faz: ajuste o comando ou escolha manualmente.'
    : dadosDaSugestao.targets.length === 0 ? 'Nenhum aparelho sugerido.'
    : ecoDaSugestao?.erro ?? null;
  /** "Escolher manualmente" a partir da sugestão: as personas sugeridas já vêm marcadas no modo por persona. */
  const escolherManualmente = () => {
    const ids = dadosDaSugestao?.escolhidas.map((e) => e.profile_id) ?? [];
    if (ids.length > 0) {
      mudarPessoas(ids);
      mudarModo('persona');
    } else {
      mudarModo(manualPreferido);
    }
    setSugestao(null);
  };
  const confirmacaoImpede: string | null = !confirmacao ? null
    : confirmacao.carregando ? 'Carregando a prévia dos alvos…'
    : confirmacao.recusa ? confirmacao.recusa.passo
    : confirmacao.previa.questions.length > 0 ? 'Responda às perguntas primeiro.'
    : ecoDaConfirmacao?.erro ?? null;

  return (
    <Card aria-labelledby="command-title">
      <div className={styles.panel}>
        <div className={styles.titleRow}>
          <h2 id="command-title" className={styles.title}>Comando</h2>
          <p className={styles.subtitle}>Descreva a tarefa em português, do seu jeito.</p>
        </div>

        <label htmlFor={fieldId} className="sr-only">Comando em linguagem natural</label>
        <TextArea
          id={fieldId}
          ref={textRef}
          className={styles.textarea}
          rows={3}
          value={command}
          placeholder={COMMAND_PLACEHOLDER}
          onChange={(e) => setCommand(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
              e.preventDefault();
              void submit('execute');
            }
          }}
        />

        {assistente !== null ? (
          <AssistenteDoComando
            key={assistente}
            comando={trimmed}
            contexto={contextoDoAssistente}
            autoIniciar
            onFechar={() => setAssistente(null)}
            acoes={(texto, pronto) => (
              <Button size="sm" variant={pronto ? 'primary' : undefined} icon={CheckCheck}
                      disabledReason={texto.length < 3 ? 'Nada para usar ainda.' : null}
                      onClick={() => {
                        setCommand(texto);
                        setAssistente(null);
                        textRef.current?.focus();
                      }}>
                Usar este comando
              </Button>
            )}
          />
        ) : null}

        <div className={styles.examples}>
          <span className={styles.examplesLabel}>Exemplos:</span>
          {EXAMPLES.map((ex) => (
            <button
              key={ex.label}
              type="button"
              className={ui.chip}
              onClick={() => {
                setCommand(ex.text);
                textRef.current?.focus();
              }}
            >
              {ex.label}
            </button>
          ))}
        </div>

        {history.length > 0 ? (
          <div className={styles.examples}>
            <span className={styles.examplesLabel}>Recentes:</span>
            {history.map((cmd) => (
              <button
                key={cmd}
                type="button"
                className={ui.chip}
                title={cmd}
                onClick={() => {
                  setCommand(cmd);
                  textRef.current?.focus();
                }}
              >
                {truncate(cmd, 40)}
              </button>
            ))}
          </div>
        ) : null}

        <div className={styles.targetMode} role="group" aria-label="Onde executar">
          {/* Duas posições, uma sempre marcada: no Automático a IA escolhe quem faz; no Manual a pessoa escolhe. */}
          <div className={styles.segmentado} role="group" aria-label="Quem escolhe os aparelhos">
            <button type="button" className={cx(styles.segmento, automatico && styles.segmentoAtivo)}
                    aria-pressed={automatico} onClick={() => mudarModo('auto')}>
              <Wand2 size={13} aria-hidden /> Automático
            </button>
            <button type="button" className={cx(styles.segmento, !automatico && styles.segmentoAtivo)}
                    aria-pressed={!automatico} onClick={() => { if (automatico) mudarModo(manualPreferido); }}>
              <Hand size={13} aria-hidden /> Manual
            </button>
          </div>
          {!automatico ? (
            <>
              <button type="button" className={ui.chip} aria-pressed={target === 'selecao'} onClick={() => mudarModo('selecao')}>
                <Smartphone size={13} aria-hidden /> Aparelhos marcados
              </button>
              <button type="button" className={ui.chip} aria-pressed={porPersona} onClick={() => mudarModo('persona')}>
                <Users size={13} aria-hidden /> Por persona
              </button>
              <button type="button" className={ui.chip} aria-pressed={distribuir} onClick={() => mudarModo('distribuir')}>
                <Shuffle size={13} aria-hidden /> Distribuir entre servidores
              </button>
            </>
          ) : null}
          {automatico ? (
            <span className={styles.autoDica}>
              a IA escolhe quem faz pelo pedido e pelo perfil das personas; aparelho e servidor saem da fila e da carga
            </span>
          ) : null}
        </div>
        {porPersona ? (
          <>
            <PersonaTarget
              pessoas={pessoas}
              selecionadas={selecionadas}
              politica={politica}
              estreitar={estreitarValido}
              onPessoas={mudarPessoas}
              onPolitica={(p) => { setPolitica(p); saveJson('commandPolicy', p); }}
              onEstreitar={setEstreitar}
            />
            {pedidoDaPrevia || previaPersona.previa || previaPersona.recusa ? (
              <PreviaDosAlvos previa={previaPersona.previa} recusa={previaPersona.recusa}
                              carregando={previaPersona.carregando || !previaEmDia} comando={trimmed}
                              nomeDe={nomeDaPersona} apps={apps} onResponder={responderPergunta} onSemDestinos={setCommand} />
            ) : null}
          </>
        ) : null}
        {distribuir ? (
          <DistributeTarget
            apps={apps.map((a) => ({ id: a.id, name: a.name }))}
            appId={appId}
            temComando={trimmed.length > 0 && !pareceCredencial(trimmed)}
            countText={distCount}
            preview={preview}
            loading={previewLoading}
            onApp={(id) => { setDistApp(id); saveJson('commandDistApp', id); }}
            onCount={(t) => { setDistCount(t); saveJson('commandDistCount', t); }}
          />
        ) : null}

        <div className={styles.footer}>
          <div className={styles.selection} style={target !== 'selecao' ? { display: 'none' } : undefined}>
            <span className={cx(styles.selCount, hydrated && selectedIds.length === 0 && styles.selCountEmpty)} aria-live="polite">
              <Smartphone size={14} aria-hidden />
              {hydrated ? `${selectedIds.length} de ${total} selecionados` : 'Carregando aparelhos…'}
            </span>
            {selectedIds.length > 0 && selectedIds.length <= 6 ? (
              <span className={styles.selIds}>{[...selectedIds].sort().map(instanceShort).join(' · ')}</span>
            ) : null}
            <Button size="sm" variant="ghost" icon={CheckCheck} disabled={!hydrated || (total > 0 && selectedIds.length === total)} onClick={() => setSelection(order)}>
              Selecionar todos
            </Button>
            <Button size="sm" variant="ghost" icon={X} disabled={selectedIds.length === 0} onClick={clearSelection}>
              Limpar
            </Button>
          </div>

          <div className={styles.actions}>
            {estimate && estimate.estimated_usd !== null ? (
              <span className={styles.shortcut} title={`Fluxo conhecido: ${estimate.name}`}>
                estimativa: US$ {estimate.estimated_usd.toFixed(2)} por aparelho ·{' '}
                {plural(Math.max(0, estimate.steps_total - estimate.steps_with_recipe), 'etapa sem IA', 'etapas sem IA')}
              </span>
            ) : null}
            {/* O motivo de um botão indisponível mora NO botão (tooltip ao passar o mouse ou focar, e dentro do nome
                acessível): um aviso solto ao lado obrigava a ligar o texto ao botão de cabeça. */}
            {reason ? null : (
              <span className={styles.shortcut}>
                <kbd>Ctrl</kbd> + <kbd>Enter</kbd> executa em {alvoTexto}
              </span>
            )}
            {/* Três etapas em sequência, não três escolhas: refinar (opcional) → planejar → executar. */}
            <div className={styles.etapas} role="group" aria-label="Etapas do comando: refinar, planejar e executar">
              <span className={styles.etapa}>
                <span className={styles.etapaNum} aria-hidden>1</span>
                <Button
                  icon={Sparkles}
                  disabledReason={refinarImpede}
                  aria-expanded={assistente !== null}
                  onClick={() => setAssistente(Date.now())}
                >
                  Refinar com IA
                </Button>
                <ChevronRight size={14} className={styles.etapaSeta} aria-hidden />
              </span>
              <span className={styles.etapa}>
                <span className={styles.etapaNum} aria-hidden>2</span>
                <Button
                  icon={ListChecks}
                  loading={inFlight === 'plan'}
                  disabled={inFlight === 'execute'}
                  disabledReason={blocked}
                  onClick={() => void submit('plan')}
                >
                  Planejar
                </Button>
                <ChevronRight size={14} className={styles.etapaSeta} aria-hidden />
              </span>
              <span className={styles.etapa}>
                <span className={styles.etapaNum} aria-hidden>3</span>
                <Button
                  variant="primary"
                  icon={Play}
                  loading={inFlight === 'execute'}
                  disabled={inFlight === 'plan'}
                  disabledReason={blocked}
                  onClick={() => void submit('execute')}
                >
                  Executar
                </Button>
              </span>
            </div>
          </div>
        </div>
        <p className={styles.etapasDica}>
          <strong>Refinar</strong> (opcional) organiza o texto e pergunta o que faltar · <strong>Planejar</strong> só
          mostra o plano, sem mexer nos aparelhos · <strong>Executar</strong> faz o trabalho em cada aparelho.
        </p>
        <div className={styles.actions}>
          {/* Só o que NUNCA vai funcionar tira o botão (senha no texto, distribuir por contas); comando curto, não: o painel abre e a prévia espera o texto. */}
          <Button size="sm" variant="outline" icon={CalendarClock} aria-expanded={pedidoAberto}
                  disabledReason={pareceCredencial(trimmed) ? SENHA_NO_COMANDO : distribuir ? pedidoImpede : null}
                  onClick={() => setPedidoAberto((a) => !a)}>
            Repetir ou acompanhar…
          </Button>
          <span className={styles.shortcut}>vira um pedido com agenda, com prévia antes de criar</span>
        </div>
        {pedidoAberto ? (
          <NovoPedido comando={trimmed} resolverAlvos={alvosDoPedido} impede={pedidoImpede} quemFaz={quemFazDoPedido}
                      onFechar={() => setPedidoAberto(false)} />
        ) : null}
        {sugestao ? (
          <SugestaoDeAlvos
            sugestao={sugestao.dados}
            carregando={sugestao.carregando}
            erro={sugestao.erro}
            mode={sugestao.mode}
            enviando={inFlight !== null}
            impede={sugestaoImpede}
            onConfirmar={() => { if (ecoDaSugestao?.eco) void submit(sugestao.mode, false, ecoDaSugestao.eco); }}
            onManual={escolherManualmente}
            onFechar={() => setSugestao(null)}
          />
        ) : null}
        {preflight ? (
          <div className={styles.preflight} role="alert">
            <p className={styles.preflightTitle}>
              <TriangleAlert size={14} aria-hidden /> Estes aparelhos não podem executar isto agora
            </p>
            <ul className={styles.preflightList}>
              {preflight.devices.map((d) => (
                <li key={d.instance_id}>
                  <strong>{instanceShort(d.instance_id)}</strong>: {d.motivo} <span className={styles.preflightAcao}>{d.acao}</span>
                </li>
              ))}
            </ul>
            <div className={styles.preflightActions}>
              {preflight.ready.length > 0 ? (
                <Button size="sm" variant="primary" loading={inFlight !== null}
                        onClick={() => void submit(preflight.mode, true, preflight.eco)}>
                  Seguir só com {plural(preflight.ready.length, 'aparelho apto', 'aparelhos aptos')}
                </Button>
              ) : null}
              <Button size="sm" variant="ghost" onClick={() => setPreflight(null)}>Fechar</Button>
            </div>
          </div>
        ) : null}
        {confirmacao ? (
          <div className={styles.preflight} role="alert" aria-label="Confirmar os destinos do comando">
            <p className={styles.preflightTitle}>
              <TriangleAlert size={14} aria-hidden /> O comando cita destinos: confira quem faz e onde antes de
              {confirmacao.mode === 'plan' ? ' planejar' : ' executar'}
            </p>
            <PreviaDosAlvos previa={confirmacao.previa} recusa={confirmacao.recusa} carregando={confirmacao.carregando}
                            comando={trimmed} nomeDe={nomeDaPersona} apps={apps} onResponder={responderPergunta}
                            onSemDestinos={setCommand} />
            <div className={styles.preflightActions}>
              <Button size="sm" variant="primary" loading={inFlight !== null} disabledReason={confirmacaoImpede}
                      onClick={() => { if (ecoDaConfirmacao?.eco) void submit(confirmacao.mode, false, ecoDaConfirmacao.eco); }}>
                {confirmacao.mode === 'plan' ? 'Confirmar e planejar' : 'Confirmar e executar'}
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setConfirmacao(null)}>Fechar</Button>
            </div>
          </div>
        ) : null}
        {recusaDoEnvio ? (
          <div className={styles.preflight} role="alert">
            <p className={styles.preflightTitle}>
              <TriangleAlert size={14} aria-hidden /> {recusaDoEnvio.titulo}
            </p>
            <p>{recusaDoEnvio.passo}</p>
            <p className={styles.preflightAcao}>{recusaDoEnvio.mensagem}</p>
            <div className={styles.preflightActions}>
              <Button size="sm" variant="ghost" onClick={() => setRecusaDoEnvio(null)}>Fechar</Button>
            </div>
          </div>
        ) : null}
        {balanceAlerts(ai?.balances).map((b) => (
          // Saldo da conta que vai pagar esta execução (ADR-051): avisar ANTES de enviar, não quando o provedor recusar.
          <p key={b.account} className={cx(styles.subtitle, styles.saldo)} data-tone={balanceTone(b)} role="status">
            <TriangleAlert size={13} aria-hidden /> {balanceStateLabel(b.state)}: {balanceBrief(b)} (paga {balanceUsage(b)}).{' '}
            {b.state === 'blocked' || b.state === 'exhausted'
              ? 'A IA desta conta está barrada até registrar o saldo novo em Configuração › IA.'
              : 'Recarregue no console do provedor antes de uma execução longa.'}
          </p>
        ))}
        {ai?.simulated ? (
          <p className={styles.subtitle}>Modo simulado ativo: o plano e os resultados são fictícios e nenhuma IA externa é chamada.</p>
        ) : null}
      </div>
    </Card>
  );
}
