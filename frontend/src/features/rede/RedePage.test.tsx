// @vitest-environment jsdom
/**
 * Painel Rede (25.8, ADR-056): contra o contrato REAL do backend da frente D1 (`backend/app/devices/rede.py`),
 * com respostas falsas (FakeBackend) — nunca `real`. As fixtures abaixo reproduzem os envelopes exatos de
 * `listar_perfis`, `listar_aparelhos`, `atribuir` e `_resposta_de_pedido`: a versão anterior deste teste inventava
 * um formato (listas soltas, `device`/`measurement` no 202) que o backend real nunca devolve — achado do revisor.
 */
import { act, type ReactElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, expect, it } from 'vitest';
import type { NetworkDeviceRow, NetworkProfileListed } from '../../api/types';
import { ConfirmHost } from '../../components/Confirm';
import { useToastStore } from '../../store/toasts';
import { FakeBackend, apiError, byRole, click, flush, installBrowserStubs, json, setValue, text, waitFor } from '../../test/harness';
import { RedePage } from './RedePage';

function perfil(over: Partial<NetworkProfileListed> = {}): NetworkProfileListed {
  return { id: 'vpn-1', name: 'WireGuard escritório', kind: 'vpn', protocol: 'wireguard', endpoint_host: '10.0.0.9',
            endpoint_port: 51820, has_secret: true, params: {}, created_at: '', created_by: null, in_use: [], ...over };
}

/** Uma linha de `GET /network/devices`, no molde de `rede.listar_aparelhos`. */
function linha(over: Partial<NetworkDeviceRow> = {}): NetworkDeviceRow {
  return {
    instance_id: 'android-01', worker_id: null, external: false, device_state: 'online',
    network: null, effective_state: null, legacy_proxy: null, restriction: null, real_account: null,
    pending: null, last_measurement: null, ...over,
  };
}

function rede(over: Partial<NonNullable<NetworkDeviceRow['network']>> = {}) {
  return { instance_id: 'android-01', vpn_profile_id: null, proxy_profile_id: null, policy: 'livre' as const,
           desired_rev: 1, applied_rev: 1, state: 'pendente' as const, detail: null, error: null,
           egress_ipv4: null, egress_ipv6: null, verified_at: null, updated_at: '', updated_by: null, ...over };
}

let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

beforeAll(() => installBrowserStubs());

beforeEach(() => {
  useToastStore.setState({ toasts: [] });
  backend = new FakeBackend();
  backend.install();
  backend.on('GET', /\/network\/profiles/, () => json({ profiles: [perfil()] }));
  backend.on('GET', /\/network\/devices/, () => json({
    devices: [
      linha({ instance_id: 'android-01', network: rede({ vpn_profile_id: 'vpn-1', policy: 'exigida', state: 'conectado' }), effective_state: 'conectado' }),
      linha({ instance_id: 'android-02' }),
    ],
  }));
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

async function render(el: ReactElement): Promise<void> {
  await act(async () => { root.render(<>{el}<ConfirmHost /></>); });
}

it('mostra o perfil, o aparelho e o selo "Conectado" (que não prova tráfego)', async () => {
  await render(<RedePage />);
  await waitFor(() => text().includes('WireGuard escritório'));
  expect(text()).toContain('android-01');
  expect(text()).toContain('Conectado');
  expect(text()).not.toContain('Tráfego verificado');
});

it('proxy legado sem linha nova aparece como "Configurado", nunca "Tráfego verificado"', async () => {
  backend.on('GET', /\/network\/devices/, () => json({
    devices: [linha({
      instance_id: 'android-01', network: null, effective_state: 'configurado',
      legacy_proxy: { proxy_id: 'px-1', name: 'Escritório', value: '10.0.0.5:3128', state: 'applied',
                      observed_value: '10.0.0.5:3128', verified_at: null, detail: null, effective_state: 'configurado' },
    })],
  }));
  await render(<RedePage />);
  await waitFor(() => text().includes('android-01'));
  expect(text()).toContain('10.0.0.5:3128 (legado)');
  expect(text()).toContain('Configurado');
  expect(text()).not.toContain('Tráfego verificado');
});

it('cria o perfil e manda o segredo uma vez só, no corpo do pedido', async () => {
  backend.on('POST', /\/network\/profiles$/, (call) => {
    const b = call.body as Record<string, unknown>;
    return json({ ...perfil({ id: 'vpn-2', name: b.name as string }), has_secret: !!b.secret });
  });
  await render(<RedePage />);
  await waitFor(() => text().includes('WireGuard escritório'));
  await setValue(byRole('textbox', /^Nome do perfil$/) as HTMLInputElement, 'VPN nova');
  await setValue(byRole('textbox', /^Host$/) as HTMLInputElement, '10.0.0.20');
  await setValue(byRole('textbox', /Segredo/) as HTMLInputElement, 'chave-privada-de-teste');
  await click(byRole('button', /^Criar$/));
  await waitFor(() => backend.callsTo('POST', /\/network\/profiles$/).length === 1);
  const b = backend.callsTo('POST', /\/network\/profiles$/)[0]!.body as Record<string, unknown>;
  expect(b).toMatchObject({ name: 'VPN nova', kind: 'vpn', protocol: 'wireguard', endpoint_host: '10.0.0.20', secret: 'chave-privada-de-teste' });
});

it('atribuir em lote exige prévia (dry_run) antes de aplicar, e manda os mesmos aparelhos', async () => {
  backend.on('POST', /\/network\/assign/, (call) => {
    const b = call.body as { dry_run?: boolean; instance_ids?: string[] };
    return json({ accepted: !b.dry_run, dry_run: !!b.dry_run,
      devices: (b.instance_ids ?? []).map((id) => ({
        id, outcome: b.dry_run ? 'would_assign' : 'assigned', reason: 'configuração nova',
        from: { vpn_profile_id: null, proxy_profile_id: null, policy: 'livre' },
        to: { vpn_profile_id: 'vpn-1', proxy_profile_id: null, policy: 'livre' }, reapply: true, warnings: [],
      })),
    });
  });
  await render(<RedePage />);
  await waitFor(() => text().includes('android-02'));
  // A tabela de atribuição repete os ids; pega a linha de baixo (a de seleção em lote).
  await click(byRole('checkbox', /Selecionar android-02/));
  await setValue(byRole('combobox', /Perfil de VPN/) as HTMLSelectElement, 'vpn-1');
  expect(byRole('button', /^Aplicar/).getAttribute('aria-disabled')).toBe('true');
  await click(byRole('button', /Ver prévia/));
  await waitFor(() => backend.callsTo('POST', /\/network\/assign/).length === 1);
  expect(backend.callsTo('POST', /\/network\/assign/)[0]!.body).toEqual({
    instance_ids: ['android-02'], vpn_profile_id: 'vpn-1', confirm_real_account: [], dry_run: true,
  });
  await waitFor(() => text().includes('aplicaria agora'));
  await click(byRole('button', /^Aplicar/));
  await waitFor(() => backend.callsTo('POST', /\/network\/assign/).length === 2);
  const final = backend.callsTo('POST', /\/network\/assign/)[1]!.body as Record<string, unknown>;
  expect(final).toEqual({ instance_ids: ['android-02'], vpn_profile_id: 'vpn-1', confirm_real_account: [], dry_run: false });
});

it('política fica de fora do pedido quando ninguém mexe nela: não rebaixa um aparelho \'exigida\' para \'livre\' (achado do revisor 25.8)', async () => {
  backend.on('GET', /\/network\/devices/, () => json({
    devices: [
      linha({ instance_id: 'android-01' }),
      linha({ instance_id: 'android-02', network: rede({ instance_id: 'android-02', vpn_profile_id: null, policy: 'exigida' }), effective_state: 'pendente' }),
    ],
  }));
  backend.on('POST', /\/network\/assign/, (call) => {
    const b = call.body as { dry_run?: boolean; instance_ids?: string[] };
    return json({ accepted: !b.dry_run, dry_run: !!b.dry_run,
      devices: (b.instance_ids ?? []).map((id) => ({
        id, outcome: b.dry_run ? 'would_assign' : 'assigned', reason: 'configuração nova',
        from: { vpn_profile_id: null, proxy_profile_id: null, policy: 'exigida' },
        to: { vpn_profile_id: 'vpn-1', proxy_profile_id: null, policy: 'exigida' }, reapply: true, warnings: [],
      })),
    });
  });
  await render(<RedePage />);
  await waitFor(() => text().includes('android-02'));
  await click(byRole('checkbox', /Selecionar android-02/));
  await setValue(byRole('combobox', /Perfil de VPN/) as HTMLSelectElement, 'vpn-1');
  // A Política nunca é tocada: continua "não mudar".
  await click(byRole('button', /Ver prévia/));
  await waitFor(() => backend.callsTo('POST', /\/network\/assign/).length === 1);
  await click(byRole('button', /^Aplicar/));
  await waitFor(() => backend.callsTo('POST', /\/network\/assign/).length === 2);
  const final = backend.callsTo('POST', /\/network\/assign/)[1]!.body as Record<string, unknown>;
  expect(final.policy).toBeUndefined();
  expect('policy' in final).toBe(false);
});

it('aparelho com conta real pede confirmação POR APARELHO antes de aplicar (ADR-056 §7): o backend, não uma heurística', async () => {
  backend.on('GET', /\/network\/devices/, () => json({
    devices: [linha({ instance_id: 'android-01', real_account: '@mariana', network: rede({ policy: 'livre' }), effective_state: 'pendente' })],
  }));
  backend.on('POST', /\/network\/assign/, (call) => {
    const b = call.body as { dry_run?: boolean; instance_ids?: string[]; confirm_real_account?: string[] };
    const confirmado = (b.confirm_real_account ?? []).includes('android-01');
    return json({ accepted: !b.dry_run, dry_run: !!b.dry_run,
      devices: (b.instance_ids ?? []).map((id) => (
        confirmado || b.dry_run === false
          ? { id, outcome: 'assigned', reason: 'ok', from: { vpn_profile_id: null, proxy_profile_id: null, policy: 'livre' },
              to: { vpn_profile_id: 'vpn-1', proxy_profile_id: null, policy: 'livre' }, reapply: true,
              warnings: confirmado ? ['conta real vinculada (@mariana), confirmada pela pessoa neste pedido'] : [] }
          : { id, outcome: 'refused', code: 'real_account_confirm_required',
              reason: `${id} tem conta real vinculada (@mariana): mudar a saída pede confirmação`,
              from: { vpn_profile_id: null, proxy_profile_id: null, policy: 'livre' },
              to: { vpn_profile_id: 'vpn-1', proxy_profile_id: null, policy: 'livre' }, reapply: true, warnings: [] }
      )),
    });
  });
  await render(<RedePage />);
  await waitFor(() => text().includes('android-01'));
  await click(byRole('checkbox', /Selecionar android-01/));
  await setValue(byRole('combobox', /Perfil de VPN/) as HTMLSelectElement, 'vpn-1');
  await click(byRole('button', /Ver prévia/));
  await waitFor(() => backend.callsTo('POST', /\/network\/assign/).length === 1);
  await click(byRole('button', /^Aplicar/));
  const dialogo = await waitFor(() => byRole('dialog', /Mudar a saída de android-01/));
  expect(text(dialogo)).toContain('@mariana');
  // Ainda não mandou o pedido de verdade: a confirmação é obrigatória antes.
  expect(backend.callsTo('POST', /\/network\/assign/).length).toBe(1);
  await click(byRole('button', /Mudar mesmo assim/, dialogo));
  await waitFor(() => backend.callsTo('POST', /\/network\/assign/).length === 2);
  const final = backend.callsTo('POST', /\/network\/assign/)[1]!.body as Record<string, unknown>;
  expect(final.confirm_real_account).toEqual(['android-01']);
});

it('recusar a confirmação de conta real aborta o lote inteiro: nenhum segundo POST', async () => {
  backend.on('GET', /\/network\/devices/, () => json({
    devices: [linha({ instance_id: 'android-01', real_account: '@mariana', network: rede({ policy: 'livre' }), effective_state: 'pendente' })],
  }));
  backend.on('POST', /\/network\/assign/, (call) => {
    const b = call.body as { dry_run?: boolean; instance_ids?: string[] };
    return json({ accepted: !b.dry_run, dry_run: !!b.dry_run,
      devices: (b.instance_ids ?? []).map((id) => ({
        id, outcome: 'refused', code: 'real_account_confirm_required', reason: 'pede confirmação',
        from: { vpn_profile_id: null, proxy_profile_id: null, policy: 'livre' },
        to: { vpn_profile_id: 'vpn-1', proxy_profile_id: null, policy: 'livre' }, reapply: true, warnings: [],
      })),
    });
  });
  await render(<RedePage />);
  await waitFor(() => text().includes('android-01'));
  await click(byRole('checkbox', /Selecionar android-01/));
  await setValue(byRole('combobox', /Perfil de VPN/) as HTMLSelectElement, 'vpn-1');
  await click(byRole('button', /Ver prévia/));
  await waitFor(() => backend.callsTo('POST', /\/network\/assign/).length === 1);
  await click(byRole('button', /^Aplicar/));
  const dialogo = await waitFor(() => byRole('dialog', /Mudar a saída de android-01/));
  await click(byRole('button', /^Cancelar$/, dialogo));
  await flush(20);
  expect(backend.callsTo('POST', /\/network\/assign/).length).toBe(1);
});

it('testar REGISTRA o pedido (202); não finge que mediu, e não muda o selo sozinho', async () => {
  backend.on('POST', /\/network\/devices\/android-01\/verify/, () => json({
    accepted: true, instance_id: 'android-01', action: 'verify', pending: 'verificar', desired_rev: 1, applied_rev: 1,
    state: 'conectado', executed: false,
    reason: 'Verificação registrada. A medição de dentro do aparelho é do item 25.5 e ainda não roda: nada foi executado no aparelho, e o estado não mudou.',
  }));
  await render(<RedePage />);
  await waitFor(() => text().includes('android-01'));
  await click(byRole('button', /^Testar$/));
  await waitFor(() => useToastStore.getState().toasts.length > 0);
  const t = useToastStore.getState().toasts.at(-1)!;
  expect(t.title).toContain('verificação pedida');
  expect(t.message).toContain('nada foi executado no aparelho');
  // O selo continua "Conectado" (o que a lista de aparelhos já mostrava): a chamada não inventou tráfego verificado.
  expect(text()).not.toContain('Tráfego verificado');
});

it('a API de verificar falhando mostra erro, não sucesso silencioso', async () => {
  backend.on('POST', /\/network\/devices\/android-01\/verify/, () => apiError(503, 'unavailable', 'sonda indisponível'));
  await render(<RedePage />);
  await waitFor(() => text().includes('android-01'));
  await click(byRole('button', /^Testar$/));
  await waitFor(() => useToastStore.getState().toasts.length > 0);
  const t = useToastStore.getState().toasts.at(-1)!;
  expect(t.tone).toBe('danger');
  expect([t.title, t.message, t.hint].join(' ')).toContain('sonda indisponível');
});

it('dois aparelhos com o mesmo IP medido mostram o aviso (ADR-056 §1)', async () => {
  backend.on('GET', /\/network\/devices/, () => json({
    devices: [
      linha({ instance_id: 'android-01', network: rede({ vpn_profile_id: 'vpn-1', state: 'trafego_verificado', egress_ipv4: '198.51.100.1', verified_at: '2026-09-29T10:00:00Z' }), effective_state: 'trafego_verificado' }),
      linha({ instance_id: 'android-02', network: rede({ instance_id: 'android-02', vpn_profile_id: 'vpn-1', state: 'trafego_verificado', egress_ipv4: '198.51.100.1', verified_at: '2026-09-29T10:05:00Z' }), effective_state: 'trafego_verificado' }),
    ],
  }));
  await render(<RedePage />);
  await waitFor(() => text().includes('198.51.100.1'));
  expect(container.querySelectorAll('[title="Outro aparelho mediu o mesmo IP agora."]').length).toBe(2);
});

it('o mesmo IPv6 medido em dois aparelhos também dá o aviso, e o IPv6 aparece na coluna', async () => {
  backend.on('GET', /\/network\/devices/, () => json({
    devices: [
      linha({ instance_id: 'android-01', network: rede({ vpn_profile_id: 'vpn-1', state: 'trafego_verificado', egress_ipv6: '2001:db8::1' }), effective_state: 'trafego_verificado' }),
      linha({ instance_id: 'android-02', network: rede({ instance_id: 'android-02', vpn_profile_id: 'vpn-1', state: 'trafego_verificado', egress_ipv6: '2001:db8::1' }), effective_state: 'trafego_verificado' }),
    ],
  }));
  await render(<RedePage />);
  await waitFor(() => text().includes('2001:db8::1'));
  expect(container.querySelectorAll('[title="Outro aparelho mediu o mesmo IP agora."]').length).toBe(2);
});

it('prévia em voo quando a seleção muda: a resposta velha não liga "Aplicar", e aplicar manda a seleção da prévia', async () => {
  // Revisão do 25.8: a resposta da seleção antiga chegava depois da limpeza, religava "Aplicar", e ele mandava a
  // seleção ATUAL, que ninguém viu na prévia.
  const respostas: (() => void)[] = [];
  const envelope = (b: { dry_run?: boolean; instance_ids?: string[] }) => json({
    accepted: !b.dry_run, dry_run: !!b.dry_run,
    devices: (b.instance_ids ?? []).map((id) => ({
      id, outcome: b.dry_run ? 'would_assign' : 'assigned', reason: 'configuração nova',
      from: { vpn_profile_id: null, proxy_profile_id: null, policy: 'livre' },
      to: { vpn_profile_id: 'vpn-1', proxy_profile_id: null, policy: 'livre' }, reapply: true, warnings: [],
    })),
  });
  backend.on('POST', /\/network\/assign/, (call) => {
    const b = call.body as { dry_run?: boolean; instance_ids?: string[] };
    if (respostas.length === 0 && b.dry_run) return new Promise<Response>((r) => respostas.push(() => r(envelope(b))));
    return envelope(b);
  });
  await render(<RedePage />);
  await waitFor(() => text().includes('android-02'));
  await click(byRole('checkbox', /Selecionar android-02/));
  await setValue(byRole('combobox', /Perfil de VPN/) as HTMLSelectElement, 'vpn-1');
  await click(byRole('button', /Ver prévia/));
  await waitFor(() => respostas.length === 1);
  await click(byRole('checkbox', /Selecionar android-01/));           // muda a seleção com a prévia em voo
  await act(async () => { respostas[0]!(); });
  await flush();
  expect(text()).not.toContain('aplicaria agora');
  expect(byRole('button', /^Aplicar/).getAttribute('aria-disabled')).toBe('true');

  // Nova prévia, agora da seleção que está na tela; aplicar manda exatamente ela.
  await click(byRole('button', /Ver prévia/));
  await waitFor(() => text().includes('aplicaria agora'));
  await click(byRole('button', /^Aplicar/));
  await waitFor(() => backend.callsTo('POST', /\/network\/assign/).length === 3);
  const [, previa, final] = backend.callsTo('POST', /\/network\/assign/).map((c) => c.body as Record<string, unknown>);
  expect(final).toEqual({ ...previa, dry_run: false });
  expect((final!.instance_ids as string[]).slice().sort()).toEqual(['android-01', 'android-02']);
});

it('aparelho em quarentena mostra o motivo na coluna de erro/pendência', async () => {
  backend.on('GET', /\/network\/devices/, () => json({
    devices: [linha({ instance_id: 'android-01', restriction: 'conta bloqueada: nada toca nela' })],
  }));
  await render(<RedePage />);
  await waitFor(() => text().includes('conta bloqueada'));
});

it('a API caída mostra o erro com "Tentar de novo", não a tabela vazia', async () => {
  backend.on('GET', /\/network\/devices/, () => apiError(503, 'unavailable', 'banco indisponível'));
  await render(<RedePage />);
  await waitFor(() => text().includes('Não foi possível carregar a rede dos aparelhos'));
  expect(text()).toContain('banco indisponível');

  backend.on('GET', /\/network\/devices/, () => json({ devices: [] }));
  await click(byRole('button', /Tentar de novo/));
  await waitFor(() => !text().includes('Não foi possível carregar'));
});
