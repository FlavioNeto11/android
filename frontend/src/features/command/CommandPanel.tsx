import { CheckCheck, Info, ListChecks, Play, Smartphone, X } from 'lucide-react';
import { useEffect, useId, useRef, useState } from 'react';
import { api } from '../../api/client';
import type { RunMode } from '../../api/types';
import { Button } from '../../components/Button';
import { Card } from '../../components/Card';
import { TextArea } from '../../components/Field';
import ui from '../../components/ui.module.css';
import { cx, plural } from '../../lib/format';
import { IdempotencyKeeper } from '../../lib/idempotency';
import { instanceShort } from '../../lib/ids';
import { isString, loadJson, saveJson } from '../../lib/storage';
import { aiAvailable, useAppStore } from '../../store/app';
import { toast, toastError } from '../../store/toasts';
import { useUiStore } from '../../store/ui';
import styles from './CommandPanel.module.css';

export const COMMAND_PLACEHOLDER =
  'Nas instâncias selecionadas, abra o QA Messenger, entre na conversa com QA-001 e envie “Teste POC {instance_id} {run_id}”. Confirme que apareceu como enviada.';

const EXAMPLES: { label: string; text: string }[] = [
  {
    label: 'Abrir uma tela',
    text: 'Nas instâncias selecionadas, abra o QA Messenger e vá até a tela de Configurações. Confirme que o título “Configurações” está visível.',
  },
  {
    label: 'Preencher formulário de perfil',
    text: 'Nas instâncias selecionadas, abra o QA Messenger, vá em Perfil, preencha o nome com “Tester {instance_id}” e a bio com “Conta de teste da POC” e salve. Confirme que os dados aparecem salvos.',
  },
  { label: 'Enviar mensagem de teste', text: COMMAND_PLACEHOLDER },
];

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
  const order = useAppStore((s) => s.instanceOrder);
  const upsertRun = useAppStore((s) => s.upsertRun);
  const selectedIds = useUiStore((s) => s.selectedIds);
  const setSelection = useUiStore((s) => s.setSelection);
  const clearSelection = useUiStore((s) => s.clearSelection);
  const selectRun = useUiStore((s) => s.selectRun);
  const draftRequest = useUiStore((s) => s.commandDraftRequest);

  const [command, setCommand] = useState(() => loadJson('commandDraft', isString) ?? '');
  const [inFlight, setInFlight] = useState<RunMode | null>(null);
  const [cooldown, setCooldown] = useState(false);
  const textRef = useRef<HTMLTextAreaElement>(null);
  const reasonId = useId();
  const fieldId = useId();

  useEffect(() => {
    const t = setTimeout(() => saveJson('commandDraft', command), 400);
    return () => clearTimeout(t);
  }, [command]);

  useEffect(() => {
    if (!draftRequest) return;
    setCommand(draftRequest.text);
    textRef.current?.focus();
    scrollToElement(textRef.current, 'center');
  }, [draftRequest]);

  const trimmed = command.trim();
  const total = order.length;

  const reason: string | null =
    !hydrated ? 'Aguardando a conexão com o backend.'
    : !aiOk ? 'IA não configurada: defina a chave no arquivo .env do backend (o restante do painel continua funcionando).'
    : selectedIds.length === 0 ? 'Selecione ao menos uma instância na grade abaixo.'
    : trimmed.length === 0 ? 'Escreva o comando em linguagem natural.'
    : null;

  const submit = async (mode: RunMode) => {
    if (reason || inFlight || cooldown) return;
    const intent = { command: trimmed, instanceIds: selectedIds, mode };
    // Mesma intenção → mesma chave (cliques repetidos e novas tentativas). Só troca após resposta 2xx.
    const idempotencyKey = keeper.keyFor(intent);
    setInFlight(mode);
    try {
      const run = await api.createRun({ command: trimmed, instance_ids: [...selectedIds], idempotency_key: idempotencyKey, mode });
      keeper.confirm(intent);
      upsertRun(run);
      selectRun(run.id);
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
          <p className={styles.subtitle}>Descreva a tarefa em português. A IA monta o plano e executa em cada instância selecionada.</p>
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

        <div className={styles.footer}>
          <div className={styles.selection}>
            <span className={cx(styles.selCount, hydrated && selectedIds.length === 0 && styles.selCountEmpty)} aria-live="polite">
              <Smartphone size={14} aria-hidden />
              {hydrated ? `${selectedIds.length} de ${total} selecionadas` : 'Carregando instâncias…'}
            </span>
            {selectedIds.length > 0 && selectedIds.length <= 6 ? (
              <span className={styles.selIds}>{[...selectedIds].sort().map(instanceShort).join(' · ')}</span>
            ) : null}
            <Button size="sm" variant="ghost" icon={CheckCheck} disabled={!hydrated || (total > 0 && selectedIds.length === total)} onClick={() => setSelection(order)}>
              Selecionar todas
            </Button>
            <Button size="sm" variant="ghost" icon={X} disabled={selectedIds.length === 0} onClick={clearSelection}>
              Limpar
            </Button>
          </div>

          <div className={styles.actions}>
            {reason ? (
              <span id={reasonId} className={styles.reason}>
                <Info size={13} aria-hidden /> {reason}
              </span>
            ) : (
              <span className={styles.shortcut}>
                <kbd>Ctrl</kbd> + <kbd>Enter</kbd> executa em {plural(selectedIds.length, 'instância', 'instâncias')}
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
        {ai?.simulated ? (
          <p className={styles.subtitle}>Modo simulado ativo: o plano e os resultados são fictícios e nenhuma IA externa é chamada.</p>
        ) : null}
      </div>
    </Card>
  );
}
