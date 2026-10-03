import { Moon } from 'lucide-react';
import { describe, expect, it } from 'vitest';
import type { InstanceState } from '../api/types';
import type { ConnectivityInfo, ReadinessInfo, StreamStatus } from '../api/types';
import {
  ACCOUNT_SESSION_STATUS, APP_INSTALL_STATE, CONNECTIVITY_STATE, DRIFT_KIND, DRIVEN_BY, FLOW_STATUS, INSTANCE_STATE,
  READINESS_PHASE, STREAM_STATUS, UNKNOWN_STATUS, aiWaitMeta, drivenByMeta, isAiBlocked, metaOf, pendingWaitMeta,
  slotWaitDetail,
} from './status';

describe('INSTANCE_STATE', () => {
  it('cobre todos os estados do contrato v0.2, inclusive hibernated', () => {
    const states: InstanceState[] = ['absent', 'stopped', 'hibernated', 'booting', 'online', 'stopping', 'error'];
    expect(Object.keys(INSTANCE_STATE).sort()).toEqual([...states].sort());
    for (const s of states) {
      expect(INSTANCE_STATE[s].label).not.toBe('');
      expect(INSTANCE_STATE[s].icon).toBeDefined();
    }
  });

  it('hibernated: rótulo "Hibernado", ícone de lua, cor própria e explicação (texto + cor + forma)', () => {
    const meta = metaOf(INSTANCE_STATE, 'hibernated');
    expect(meta.label).toBe('Hibernado');
    expect(meta.icon).toBe(Moon);
    expect(meta.tone).toBe('info');
    expect(meta.spin).toBeUndefined(); // não é um estado de transição
    expect(meta.description).toMatch(/acorda em segundos/);
    expect(meta.description).toMatch(/não ocupa RAM/);
    // não se confunde com "parada" nem com o fallback de valor desconhecido
    expect(meta.label).not.toBe(INSTANCE_STATE.stopped.label);
    expect(meta.icon).not.toBe(INSTANCE_STATE.stopped.icon);
  });
});

describe('drivenByMeta — selo por etapa', () => {
  it('traduz cada valor de driven_by', () => {
    expect(drivenByMeta('recipe')?.label).toBe('Receita');
    expect(drivenByMeta('recipe+ai')?.label).toBe('Receita + IA');
    expect(drivenByMeta('ai')?.label).toBe('IA');
    expect(drivenByMeta('sem_ator')?.label).toBe('Sem o ator');
  });

  it('só a receita pura diz "sem chamada de modelo"; cada selo tem ícone e tom distintos', () => {
    expect(DRIVEN_BY.recipe.description).toMatch(/nenhuma chamada de modelo/);
    expect(DRIVEN_BY['recipe+ai'].description).toMatch(/IA assumiu/);
    expect(DRIVEN_BY.sem_ator.description).toMatch(/sem o ator decidir/);
    const icons = new Set([DRIVEN_BY.recipe.icon, DRIVEN_BY['recipe+ai'].icon, DRIVEN_BY.ai.icon, DRIVEN_BY.sem_ator.icon]);
    const tones = new Set([DRIVEN_BY.recipe.tone, DRIVEN_BY['recipe+ai'].tone, DRIVEN_BY.ai.tone, DRIVEN_BY.sem_ator.tone]);
    expect(icons.size).toBe(4);
    expect(tones.size).toBe(4);
  });

  it('não mostra selo enquanto driven_by é nulo/ausente e tolera valor fora do contrato', () => {
    expect(drivenByMeta(null)).toBeNull();
    expect(drivenByMeta(undefined)).toBeNull();
    expect(drivenByMeta('')).toBeNull();
    expect(drivenByMeta('macro')?.label).toBe('macro');
  });
});

describe('slotWaitDetail — rodízio', () => {
  it('devolve o texto só para objetivo pendente cujo detalhe começa com "aguardando vaga"', () => {
    expect(slotWaitDetail({ status: 'pending', status_detail: 'aguardando vaga (3/3 ligados)' })).toBe('aguardando vaga (3/3 ligados)');
    expect(slotWaitDetail({ status: 'pending', status_detail: '  Aguardando vaga — nenhum aparelho ligado pode ser desligado agora (3/3 ligados)' }))
      .toBe('Aguardando vaga — nenhum aparelho ligado pode ser desligado agora (3/3 ligados)');
  });

  it('ignora outros estados e outros detalhes', () => {
    expect(slotWaitDetail({ status: 'running', status_detail: 'aguardando vaga (3/3 ligados)' })).toBeNull();
    expect(slotWaitDetail({ status: 'waiting_user', status_detail: 'aguardando vaga' })).toBeNull();
    expect(slotWaitDetail({ status: 'pending', status_detail: 'na fila; aguardando vaga' })).toBeNull();
    expect(slotWaitDetail({ status: 'pending', status_detail: null })).toBeNull();
    expect(slotWaitDetail(null)).toBeNull();
  });

  it('item 7.3: com wait_reason gravado, a fonte da verdade é o campo estruturado — não o texto', () => {
    expect(slotWaitDetail({ status: 'pending', status_detail: 'aguardando vaga (3/3 ligados)', wait_reason: 'device_slot' }))
      .toBe('aguardando vaga (3/3 ligados)');
    // motivo tipado presente mas NÃO é device_slot (ex.: aparelho ligando por outro caminho): não é "vaga"
    expect(slotWaitDetail({ status: 'pending', status_detail: 'aguardando vaga (3/3 ligados)', wait_reason: 'profile_limit' }))
      .toBeNull();
  });
});

describe('aiWaitMeta / isAiBlocked — item 7.3 (achados #93, #68)', () => {
  it('distingue vaga de IA de resposta do modelo, só em objetivo em andamento', () => {
    expect(aiWaitMeta({ status: 'running', wait_reason: 'ai_capacity' })?.label).toBe('Aguardando vaga de IA');
    expect(aiWaitMeta({ status: 'running', wait_reason: 'model_response' })?.label).toBe('Aguardando resposta do modelo');
    expect(aiWaitMeta({ status: 'running', wait_reason: 'device_slot' })).toBeNull();
    expect(aiWaitMeta({ status: 'pending', wait_reason: 'ai_capacity' })).toBeNull();
    expect(aiWaitMeta({ status: 'running', wait_reason: null })).toBeNull();
    expect(aiWaitMeta(null)).toBeNull();
  });

  it('pathfinder (v0.20): objetivo ainda não iniciado esperando outro aparelho aprender o caminho', () => {
    expect(pendingWaitMeta({ status: 'pending', wait_reason: 'pathfinder' })?.label)
      .toBe('Aguardando outro aparelho aprender o caminho');
    expect(pendingWaitMeta({ status: 'pending', wait_reason: 'pathfinder' })?.description).toContain('sem gastar IA');
    // só vale para quem ainda não começou (o agendador só segura objetivo `pending`)
    expect(pendingWaitMeta({ status: 'running', wait_reason: 'pathfinder' })).toBeNull();
    expect(pendingWaitMeta({ status: 'pending', wait_reason: 'device_slot' })).toBeNull();
    expect(pendingWaitMeta({ status: 'pending', wait_reason: null })).toBeNull();
    expect(pendingWaitMeta(null)).toBeNull();
    // não se confunde com a espera por vaga de aparelho nem com a espera de IA
    expect(slotWaitDetail({ status: 'pending', status_detail: 'aguardando o caminho', wait_reason: 'pathfinder' })).toBeNull();
    expect(aiWaitMeta({ status: 'pending', wait_reason: 'pathfinder' })).toBeNull();
  });

  it('blocked_kind="ai" é o único que conta como bloqueio DA IA', () => {
    expect(isAiBlocked({ blocked_kind: 'ai' })).toBe(true);
    expect(isAiBlocked({ blocked_kind: 'policy' })).toBe(false);
    expect(isAiBlocked({ blocked_kind: null })).toBe(false);
    expect(isAiBlocked(null)).toBe(false);
  });
});

// Auditoria UX 27/09, P2.9: os enums abaixo chegavam crus à tela (`ready`, `capture_error`, `downgrade_refused`…).
describe('mapas novos de StatusMeta — todo enum tem rótulo em português, tom e ícone', () => {
  it('READINESS_PHASE cobre a escada inteira e só `ready` é sucesso', () => {
    const fases: ReadinessInfo['phase'][] = ['not_running', 'process_running', 'adb_device', 'boot_completed', 'android_responsive', 'ready'];
    expect(Object.keys(READINESS_PHASE).sort()).toEqual([...fases].sort());
    for (const f of fases) {
      expect(READINESS_PHASE[f].label).not.toBe(f);
      expect(READINESS_PHASE[f].icon).toBeDefined();
    }
    expect(READINESS_PHASE.ready.tone).toBe('success');
    expect(fases.filter((f) => READINESS_PHASE[f].tone === 'success')).toEqual(['ready']);
  });

  it('STREAM_STATUS cobre os oito estados da tela e `stale` não é tratado como offline', () => {
    const estados: StreamStatus[] = ['live', 'stale', 'capture_error', 'no_frame', 'device_offline', 'device_hibernated', 'worker_offline', 'paused'];
    expect(Object.keys(STREAM_STATUS).sort()).toEqual([...estados].sort());
    expect(STREAM_STATUS.stale.tone).toBe('warning');
    expect(STREAM_STATUS.stale.label).not.toMatch(/offline|desligado/i);
    expect(STREAM_STATUS.capture_error.tone).toBe('danger');
    expect(STREAM_STATUS.worker_offline.tone).toBe('danger');
  });

  it('CONNECTIVITY_STATE cobre os quatro estados da internet do aparelho', () => {
    const estados: ConnectivityInfo['state'][] = ['unknown', 'healthy', 'degraded', 'unavailable'];
    expect(Object.keys(CONNECTIVITY_STATE).sort()).toEqual([...estados].sort());
    expect(CONNECTIVITY_STATE.healthy.tone).toBe('success');
    expect(CONNECTIVITY_STATE.unavailable.tone).toBe('danger');
  });

  it('APP_INSTALL_STATE cobre o InstallState do backend e usa os rótulos da tela de versões', () => {
    const estados = ['missing', 'installing', 'installed', 'verifying', 'ready', 'install_failed', 'verify_failed', 'incompatible', 'version_drift'];
    expect(Object.keys(APP_INSTALL_STATE).sort()).toEqual([...estados].sort());
    expect(APP_INSTALL_STATE.ready!.label).toBe('Instalado e conferido');
    expect(APP_INSTALL_STATE.missing!.label).toBe('Ainda não chegou');
    expect(APP_INSTALL_STATE.install_failed!.tone).toBe('danger');
    expect(APP_INSTALL_STATE.version_drift!.tone).toBe('warning');
  });

  it('DRIFT_KIND, FLOW_STATUS e ACCOUNT_SESSION_STATUS traduzem os valores que o painel mostra', () => {
    expect(metaOf(DRIFT_KIND, 'downgrade_refused').label).toBe('Downgrade recusado');
    // Os quatro status que um fluxo pode ter (D1, ADR-054): o candidato não aparece mais cru em Aplicativos nem na guia
    // Habilidades.
    expect(Object.keys(FLOW_STATUS).sort()).toEqual(['active', 'candidate', 'disabled', 'validated']);
    expect(metaOf(FLOW_STATUS, 'candidate').label).toBe('Em prova');
    expect(metaOf(FLOW_STATUS, 'validated').label).toBe('Esperando o dono');
    expect(FLOW_STATUS.active.label).toBe('Ativo');
    expect(FLOW_STATUS.disabled.label).toBe('Desligado');
    // Os valores do Instagram continuam iguais aos de SESSION_STATUS; os manuais ganham rótulo próprio.
    expect(metaOf(ACCOUNT_SESSION_STATUS, 'session_ready').label).toBe('Conectado');
    expect(metaOf(ACCOUNT_SESSION_STATUS, 'logged_out').label).toBe('Fora da conta');
    expect(metaOf(ACCOUNT_SESSION_STATUS, 'needs_person').label).toBe('Precisa de uma pessoa');
  });

  it('valor fora do contrato nunca quebra: cai no próprio valor, com o tom apagado do desconhecido', () => {
    for (const mapa of [READINESS_PHASE, STREAM_STATUS, CONNECTIVITY_STATE, APP_INSTALL_STATE, DRIFT_KIND, FLOW_STATUS, ACCOUNT_SESSION_STATUS]) {
      const meta = metaOf(mapa as Record<string, typeof UNKNOWN_STATUS>, 'valor_novo_do_backend');
      expect(meta.label).toBe('valor_novo_do_backend');
      expect(meta.tone).toBe(UNKNOWN_STATUS.tone);
      expect(meta.icon).toBe(UNKNOWN_STATUS.icon);
    }
    expect(metaOf(STREAM_STATUS, null)).toBe(UNKNOWN_STATUS);
  });
});
