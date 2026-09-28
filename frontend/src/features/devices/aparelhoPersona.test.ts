/**
 * Aparelho → App → Perfil → Sessão no painel: a tela mostra a decisão do backend e não deduz uma camada da outra.
 * Prova `simulated` (jsdom/node, sem aparelho).
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import type { InstagramProfile, Instance, SessionActions } from '../../api/types';
import { sessionGateReason } from '../profiles/sessionGate';
import { ACTION_META } from './actions';
import { installItemLabel } from './InstallAppMenu';
import { streamLabel } from './streamState';

const nega = (reason: string) => ({ allowed: false, reason });
const ok = { allowed: true, reason: null };

function perfil(acoes: SessionActions | null, extra: Partial<InstagramProfile> = {}) {
  return { session_actions: acoes, instance_id: 'android-06',
           credential: { configured: true } as InstagramProfile['credential'], ...extra };
}

describe('portões de sessão vêm do backend', () => {
  it('app ausente: Conectar e Verificar conta indisponíveis, com o motivo', () => {
    const p = perfil({ phase: 'app_missing', detail: 'Instagram não está instalado', connect: nega('Instagram não está instalado'),
                       verify: nega('Instagram não está instalado'), logout: nega('x'), inspect_app: ok });
    expect(sessionGateReason(p, 'connect')).toContain('não está instalado');
    expect(sessionGateReason(p, 'verify')).toContain('não está instalado');
  });

  it('senha guardada não libera Conectar se o backend diz que o app não foi verificado', () => {
    const p = perfil({ phase: 'app_unknown', detail: 'não verificado', connect: nega('não verificado'),
                       verify: nega('não verificado'), logout: nega('não verificado'), inspect_app: ok });
    expect(sessionGateReason(p, 'connect')).not.toBeNull();
  });

  it('app instalado + senha + deslogado: Conectar liberado', () => {
    const p = perfil({ phase: 'logged_out', detail: '', connect: ok, verify: ok, logout: ok, inspect_app: ok });
    expect(sessionGateReason(p, 'connect')).toBeNull();
  });

  it('backend antigo (sem session_actions) cai na regra de antes', () => {
    expect(sessionGateReason(perfil(null, { instance_id: null }), 'verify')).toContain('Vincule');
  });
});

describe('selo da tela: stale não é offline', () => {
  const inst = (stream: Instance['stream']) => ({ state: 'online' as const, stream });
  const base = { detail: '', last_frame_at: null, frame_age_s: 60, last_capture_error: null, last_capture_error_at: null,
                 consecutive_capture_failures: 0 };
  it('sem frame novo com aparelho online não fala em offline', () => {
    const l = streamLabel(inst({ ...base, status: 'stale' }), true);
    expect(l?.title).toBe('Sem frame novo');
    expect(l?.hint).toContain('não é');
  });
  it('erro de captura e servidor fora têm selo próprio', () => {
    expect(streamLabel(inst({ ...base, status: 'capture_error', consecutive_capture_failures: 3,
                              last_capture_error: 'DriverError' }), true)?.title).toBe('Captura falhando');
    expect(streamLabel(inst({ ...base, status: 'worker_offline' }), true)?.title).toBe('Servidor desconectado');
  });
  it('frame em dia: nenhum selo', () => {
    expect(streamLabel(inst({ ...base, status: 'live' }), false)).toBeNull();
  });
  it('IA no controle e frame velho: o motivo é a IA sem olhar a tela, não a captura (r-20260928195344-02ee9e)', () => {
    // `stream` só se renova em `instance.updated`: o cliente pode achar o frame velho com o `live` guardado
    for (const status of ['live', 'stale'] as const) {
      const l = streamLabel({ ...inst({ ...base, status }), control: 'ai' }, true);
      expect(l?.title).toBe('IA sem olhar a tela');
      expect(l?.hint).not.toContain('captura está atrasada');
    }
    // falha publicada da tela continua com selo próprio, com ou sem a IA
    expect(streamLabel({ ...inst({ ...base, status: 'capture_error', consecutive_capture_failures: 2 }), control: 'ai' },
                       true)?.title).toBe('Captura falhando');
    expect(streamLabel({ ...inst({ ...base, status: 'worker_offline' }), control: 'ai' }, true)?.title)
      .toBe('Servidor desconectado');
    expect(streamLabel({ ...inst({ ...base, status: 'stale' }), control: 'none' }, true)?.title).toBe('Sem frame novo');
  });
});

describe('instalar diz o quê antes do clique', () => {
  it('rótulo com app e versão promovida', () => {
    expect(installItemLabel({ name: 'Instagram', promoted_version_name: '412.0.0.35.87' }))
      .toBe('Instalar Instagram 412.0.0.35.87 (promovida)');
    expect(installItemLabel({ name: 'Instagram', promoted_version_name: null })).toContain('nenhuma versão promovida');
  });
  it('instalar e abrir são verbos e rótulos diferentes', () => {
    expect(ACTION_META.install_apk.label).not.toBe(ACTION_META.open_app.label);
  });
  it('o botão não trunca: rótulo curto e o grid de ações permite quebra de linha', () => {
    expect(ACTION_META.install_apk.label.length).toBeLessThanOrEqual(14);
    const css = readFileSync(resolve(__dirname, '../focus/Focus.module.css'), 'utf8');
    expect(css).toMatch(/\.quick > button \{[^}]*white-space: normal/);
  });
});
