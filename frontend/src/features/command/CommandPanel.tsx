import { CheckCheck, Info, ListChecks, Play, Shuffle, Smartphone, TriangleAlert, X } from 'lucide-react';
import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { api, toApiError } from '../../api/client';
import type { FlowCoverage, PreflightRefusal, RunMode } from '../../api/types';
import { Button } from '../../components/Button';
import { Card } from '../../components/Card';
import { confirm } from '../../components/Confirm';
import { Field, TextArea, TextInput } from '../../components/Field';
import ui from '../../components/ui.module.css';
import { cx, plural, truncate } from '../../lib/format';
import { IdempotencyKeeper } from '../../lib/idempotency';
import { instanceShort } from '../../lib/ids';
import { isString, isStringArray, loadJson, saveJson } from '../../lib/storage';
import { aiAvailable, selectTaskOrder, useAppStore } from '../../store/app';
import { toast, toastError } from '../../store/toasts';
import { useUiStore } from '../../store/ui';
import styles from './CommandPanel.module.css';
import { DistributeTarget, parseCount, useDistributionPreview } from './DistributeTarget';
import { historicoSeguro, pareceCredencial, pushHistory } from './history';

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
  const apps = useAppStore((s) => s.apps);

  // Alvo do comando: os aparelhos marcados na grade, ou "distribuir entre servidores" (o backend escolhe N
  // aparelhos do app pela carga de cada máquina — Limites → Por servidor).
  const [target, setTarget] = useState<'selecao' | 'distribuir'>(
    () => (loadJson('commandTarget', isString) === 'distribuir' ? 'distribuir' : 'selecao'));
  const [distCount, setDistCount] = useState(() => loadJson('commandDistCount', isString) ?? '2');
  const [distApp, setDistApp] = useState(() => loadJson('commandDistApp', isString) ?? '');
  const distribuir = target === 'distribuir';
  const count = parseCount(distCount);
  // Sem app escolhido ainda: o app mais comum entre os aparelhos do parque.
  const appId = useMemo(() => {
    if (distApp && apps.some((a) => a.id === distApp)) return distApp;
    const cont = new Map<string, number>();
    for (const id of order) {
      const a = instancesMap[id]?.app_id;
      if (a) cont.set(a, (cont.get(a) ?? 0) + 1);
    }
    return [...cont.entries()].sort((x, y) => y[1] - x[1])[0]?.[0] ?? apps[0]?.id ?? '';
  }, [distApp, apps, order, instancesMap]);
  const { preview, loading: previewLoading } = useDistributionPreview(distribuir, count, appId);

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
  // ADR-025: a senha da execução fica SÓ em memória — nunca em rascunho, histórico ou localStorage — e é limpa
  // assim que a execução é criada.
  const [senha, setSenha] = useState('');
  // Recusa do pré-voo ainda na tela: fica até a pessoa seguir só com os aptos, resolver o motivo, ou fechar.
  const [preflight, setPreflight] = useState<(PreflightRefusal & { mode: RunMode }) | null>(null);
  const [cooldown, setCooldown] = useState(false);
  // Item 7.7: quanto vai custar repetir o fluxo que este comando casa — só um palpite de leitura, nunca bloqueia.
  const [estimate, setEstimate] = useState<FlowCoverage | null>(null);
  const textRef = useRef<HTMLTextAreaElement>(null);
  const reasonId = useId();
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

  const trimmed = command.trim();
  const total = order.length;

  const alvoInvalido: string | null = distribuir
    ? (!appId ? 'Escolha o app dos aparelhos a distribuir.'
      : count === null ? 'Informe quantos aparelhos (de 1 a 64).'
      : preview && preview.picks.length === 0 ? `Nenhum aparelho disponível para distribuir${preview.reasons[0] ? `: ${preview.reasons[0]}` : ''}.`
      : null)
    : selectedIds.length === 0 ? 'Selecione ao menos um aparelho na grade abaixo.' : null;

  const reason: string | null =
    !hydrated ? 'Aguardando a conexão com o backend.'
    : !aiOk ? 'IA não configurada: defina a chave no arquivo .env do backend (o restante do painel continua funcionando).'
    : alvoInvalido ? alvoInvalido
    : trimmed.length === 0 ? 'Escreva o comando em linguagem natural.'
    : pareceCredencial(trimmed) ? 'O comando contém uma senha: tire-a do texto e informe-a no campo "Senha para a automação".'
    : null;
  const alvoTexto = distribuir
    ? plural(count ?? 0, 'aparelho distribuído', 'aparelhos distribuídos')
    : plural(selectedIds.length, 'aparelho', 'aparelhos');

  const submit = async (mode: RunMode, onlyReady = false, consent = false) => {
    if (reason || inFlight || cooldown) return;
    // A distribuição entra na intenção: mudar app ou quantidade é outro pedido, com outra chave.
    const intent = { command: trimmed, instanceIds: distribuir ? [`distribuir:${appId}:${count}`] : selectedIds, mode };
    // Mesma intenção → mesma chave (cliques repetidos e novas tentativas). Só troca após resposta 2xx.
    const idempotencyKey = keeper.keyFor(intent);
    setInFlight(mode);
    const credenciais = senha ? { credentials: { senha }, consent_credentials: consent || undefined } : {};
    try {
      const run = distribuir && count !== null
        // Faltando aparelho, a prévia já disse quantos e por quê: executar segue com os disponíveis.
        ? await api.createRun({ command: trimmed, instance_ids: [], idempotency_key: idempotencyKey, mode,
                                distribute: { count, app_id: appId },
                                only_ready: onlyReady || (preview !== null && preview.missing > 0) || undefined,
                                ...credenciais })
        : await api.createRun({ command: trimmed, instance_ids: [...selectedIds], idempotency_key: idempotencyKey,
                                mode, only_ready: onlyReady || undefined, ...credenciais });
      setPreflight(null);
      setSenha('');
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
      if (err.code === 'consentimento_de_credencial' && !consent) {
        // O backend descreve o que vai acontecer com cada dado; a pessoa decide aqui, e só um "sim" reenvia.
        setInFlight(null);
        const { confirmed } = await confirm({
          title: 'Autorizar a automação a digitar a credencial?',
          body: err.message,
          confirmLabel: 'Autorizo digitar',
          danger: true,
        });
        if (confirmed) await submit(mode, onlyReady, true);
        return;
      }
      const recusa = preflightOf(err);
      if (recusa) {
        setPreflight({ ...recusa, mode });
        setInFlight(null);
        return;
      }
      setPreflight(null);
      toastError(mode === 'plan' ? 'Não foi possível planejar' : 'Não foi possível criar a execução', e, {
        hint: 'Nada foi duplicado: tentar de novo reutiliza a mesma chave de idempotência.',
      });
    } finally {
      setInFlight(null);
    }
  };

  const blocked = reason ?? (cooldown ? 'Execução criada agora há pouco — aguarde um instante para enviar de novo.' : null);

  return (
    <Card aria-labelledby="command-title">
      <div className={styles.panel}>
        <div className={styles.titleRow}>
          <h2 id="command-title" className={styles.title}>Comando</h2>
          <p className={styles.subtitle}>Descreva a tarefa em português. A IA monta o plano e executa em cada aparelho selecionado.</p>
        </div>

        <label htmlFor={fieldId} className="sr-only">Comando em linguagem natural</label>
        <TextArea
          id={fieldId}
          ref={textRef}
          className={styles.textarea}
          rows={3}
          value={command}
          placeholder={COMMAND_PLACEHOLDER}
          aria-describedby={reason ? reasonId : undefined}
          onChange={(e) => setCommand(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
              e.preventDefault();
              void submit('execute');
            }
          }}
        />

        <Field
          label="Senha para a automação"
          unit="opcional"
          hint="Se a tarefa precisa entrar numa conta, informe a senha aqui — não no comando. Ela fica cifrada, é digitada sem passar pela IA e é apagada quando a execução termina. Você confirma antes de executar."
        >
          {({ id, describedBy }) => (
            <TextInput
              id={id}
              type="password"
              autoComplete="off"
              value={senha}
              aria-describedby={describedBy}
              onChange={(e) => setSenha(e.target.value)}
            />
          )}
        </Field>

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
          <button type="button" className={ui.chip} aria-pressed={!distribuir}
                  onClick={() => { setTarget('selecao'); saveJson('commandTarget', 'selecao'); }}>
            <Smartphone size={13} aria-hidden /> Aparelhos marcados
          </button>
          <button type="button" className={ui.chip} aria-pressed={distribuir}
                  onClick={() => { setTarget('distribuir'); saveJson('commandTarget', 'distribuir'); }}>
            <Shuffle size={13} aria-hidden /> Distribuir entre servidores
          </button>
        </div>
        {distribuir ? (
          <DistributeTarget
            apps={apps.map((a) => ({ id: a.id, name: a.name }))}
            appId={appId}
            countText={distCount}
            preview={preview}
            loading={previewLoading}
            onApp={(id) => { setDistApp(id); saveJson('commandDistApp', id); }}
            onCount={(t) => { setDistCount(t); saveJson('commandDistCount', t); }}
          />
        ) : null}

        <div className={styles.footer}>
          <div className={styles.selection} style={distribuir ? { display: 'none' } : undefined}>
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
            {reason ? (
              <span id={reasonId} className={styles.reason}>
                <Info size={13} aria-hidden /> {reason}
              </span>
            ) : (
              <span className={styles.shortcut}>
                <kbd>Ctrl</kbd> + <kbd>Enter</kbd> executa em {alvoTexto}
              </span>
            )}
            <Button
              icon={ListChecks}
              loading={inFlight === 'plan'}
              disabled={inFlight === 'execute'}
              disabledReason={blocked}
              onClick={() => void submit('plan')}
            >
              Planejar
            </Button>
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
          </div>
        </div>
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
                        onClick={() => void submit(preflight.mode, true)}>
                  Seguir só com {plural(preflight.ready.length, 'aparelho apto', 'aparelhos aptos')}
                </Button>
              ) : null}
              <Button size="sm" variant="ghost" onClick={() => setPreflight(null)}>Fechar</Button>
            </div>
          </div>
        ) : null}
        {ai?.simulated ? (
          <p className={styles.subtitle}>Modo simulado ativo: o plano e os resultados são fictícios e nenhuma IA externa é chamada.</p>
        ) : null}
      </div>
    </Card>
  );
}
