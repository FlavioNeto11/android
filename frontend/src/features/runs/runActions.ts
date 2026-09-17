import { createElement } from 'react';
import { api } from '../../api/client';
import type { Objective, Resolution, RetryFailedResponse, RunSummary } from '../../api/types';
import { confirm } from '../../components/Confirm';
import { useAppStore } from '../../store/app';
import { refreshSelectedRun } from '../../store/live';
import { toast, toastError } from '../../store/toasts';

type SimpleRunAction = 'start' | 'pause' | 'resume' | 'cancel';

const LABEL: Record<SimpleRunAction, { ok: string; fail: string }> = {
  start: { ok: 'Execução iniciada', fail: 'Não foi possível iniciar a execução' },
  pause: { ok: 'Pausa solicitada', fail: 'Não foi possível pausar' },
  resume: { ok: 'Execução retomada', fail: 'Não foi possível continuar' },
  cancel: { ok: 'Cancelamento solicitado', fail: 'Não foi possível cancelar' },
};

export async function runAction(run: Pick<RunSummary, 'id' | 'short_id'>, action: SimpleRunAction): Promise<boolean> {
  if (action === 'cancel') {
    const { confirmed } = await confirm({
      title: `Cancelar a execução ${run.short_id}?`,
      danger: true,
      confirmLabel: 'Cancelar execução',
      cancelLabel: 'Manter em andamento',
      body: createElement(
        'p',
        null,
        'As etapas em andamento são interrompidas assim que for seguro. Ações com efeito externo que já aconteceram (ex.: mensagens enviadas) ',
        createElement('strong', null, 'não são desfeitas'),
        '.',
      ),
    });
    if (!confirmed) return false;
  }
  try {
    const fn = action === 'start' ? api.startRun : action === 'pause' ? api.pauseRun : action === 'resume' ? api.resumeRun : api.cancelRun;
    const updated = await fn(run.id);
    useAppStore.getState().upsertRun(updated);
    toast({ tone: 'success', title: `${LABEL[action].ok} — ${run.short_id}`, key: `run-${run.id}` });
    return true;
  } catch (e) {
    toastError(LABEL[action].fail, e);
    return false;
  }
}

export async function retryFailed(run: Pick<RunSummary, 'id' | 'short_id'>): Promise<RetryFailedResponse | null> {
  try {
    const res = await api.retryFailed(run.id);
    const retried = Array.isArray(res?.retried) ? res.retried : [];
    const skipped = Array.isArray(res?.skipped) ? res.skipped : [];
    toast({
      tone: skipped.length > 0 ? 'warning' : 'success',
      title: `Nova tentativa: ${retried.length} objetivo(s) reenfileirado(s)${skipped.length > 0 ? `, ${skipped.length} ignorado(s)` : ''}`,
      message: skipped.length > 0 ? 'Os motivos dos ignorados ficam listados no cabeçalho da execução.' : null,
    });
    refreshSelectedRun();
    return { retried, skipped };
  } catch (e) {
    toastError('Não foi possível tentar novamente', e);
    return null;
  }
}

const RESOLUTION_COPY: Record<Resolution, { title: string; confirmLabel: string; danger: boolean; ok: string }> = {
  confirm_done: { title: 'Marcar como concluído?', confirmLabel: 'Sim, está concluído', danger: false, ok: 'Objetivo marcado como concluído' },
  retry: { title: 'Tentar este objetivo novamente?', confirmLabel: 'Tentar novamente', danger: false, ok: 'Objetivo reenfileirado' },
  abandon: { title: 'Abandonar este objetivo?', confirmLabel: 'Abandonar objetivo', danger: true, ok: 'Objetivo abandonado' },
};

export async function resolveObjective(objective: Objective, resolution: Resolution): Promise<boolean> {
  const copy = RESOLUTION_COPY[resolution];
  const uncertain = objective.status === 'uncertain';
  const hasEffects = (objective.effects?.length ?? 0) > 0;

  const paragraphs: string[] = [];
  if (resolution === 'confirm_done') {
    paragraphs.push(`Você confirma que verificou ${objective.instance_id} e que o objetivo foi atingido. O objetivo passa a contar como sucesso.`);
  } else if (resolution === 'retry') {
    paragraphs.push(`A IA volta a trabalhar neste objetivo em ${objective.instance_id}, a partir da tela atual.`);
    if (uncertain || hasEffects) {
      paragraphs.push('Atenção: há ação com efeito externo cujo resultado não foi confirmado. Tentar novamente PODE repeti-la (ex.: enviar a mesma mensagem duas vezes). Confira no aparelho antes.');
    }
  } else {
    paragraphs.push(`O objetivo em ${objective.instance_id} é encerrado sem sucesso. As demais instâncias continuam normalmente.`);
  }
  if (uncertain) paragraphs.push('Enquanto você não decidir, nada é reenviado automaticamente.');

  const { confirmed, note } = await confirm({
    title: copy.title,
    danger: copy.danger,
    confirmLabel: copy.confirmLabel,
    body: createElement('div', null, ...paragraphs.map((p, i) => createElement('p', { key: i, style: i > 0 ? { marginTop: 8 } : undefined }, p))),
    note: { label: 'Observação para o registro', placeholder: 'Ex.: conferi na tela e a mensagem aparece como enviada' },
  });
  if (!confirmed) return false;

  try {
    const updated = await api.resolveObjective(objective.run_id, objective.id, note ? { resolution, note } : { resolution });
    useAppStore.getState().upsertObjective(updated);
    toast({ tone: 'success', title: `${copy.ok} — ${objective.instance_id}` });
    return true;
  } catch (e) {
    toastError('Não foi possível resolver o objetivo', e);
    return false;
  }
}
