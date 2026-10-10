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
import type { NetworkDeviceRow, NetworkProfileListed, NetworkServerStatus } from '../../api/types';
import { ConfirmHost } from '../../components/Confirm';
import { useAppStore } from '../../store/app';
import { useToastStore } from '../../store/toasts';
import { FakeBackend, apiError, atrasoMaximoDoFetchMs, botaoPronto, byRole, click, flush, installBrowserStubs, json, setValue, text, waitFor } from '../../test/harness';
import { RedePage, nomeDoPacote } from './RedePage';

function perfil(over: Partial<NetworkProfileListed> = {}): NetworkProfileListed {
  return { id: 'vpn-1', name: 'WireGuard escritório', kind: 'vpn', protocol: 'wireguard', endpoint_host: '10.0.0.9',
            endpoint_port: 51820, has_secret: true, params: {}, created_at: '', created_by: null, in_use: [], ...over };
}

/** Uma linha de `GET /network/devices`, no molde de `rede.listar_aparelhos`. */
function linha(over: Partial<NetworkDeviceRow> = {}): NetworkDeviceRow {
  return {
    instance_id: 'android-01', worker_id: null, external: false, device_state: 'online',
    network: null, effective_state: null, legacy_proxy: null, restriction: null, real_account: null,
    pending: null, last_measurement: null, egress_shared_with: [], ...over,
  };
}

function rede(over: Partial<NonNullable<NetworkDeviceRow['network']>> = {}) {
  return { instance_id: 'android-01', vpn_profile_id: null, proxy_profile_id: null, policy: 'livre' as const,
           desired_rev: 1, applied_rev: 1, state: 'pendente' as const, detail: null, error: null,
           egress_ipv4: null, egress_ipv6: null, verified_at: null, updated_at: '', updated_by: null,
           leak_rev: null, leak_client: null, leak_result: null, leak_at: null, leak_detail: null, leak_pending: false,
           ...over };
}

/** `GET /network/server` no molde de `ServidorDeRede.status()` (25.4) com o `remote_access` do 25.7. */
function servidor(over: Partial<NetworkServerStatus> = {}): NetworkServerStatus {
  return {
    binary_present: true, running: true, pid: 4242, started_at: '', signature: 'abc', in_sync: true,
    server_address: '10.66.0.1', subnet: '10.66.0.0/24', wireguard_udp_port: 51820, proxy: null,
    server_public_key: 'pub', proxy_users: [], detail: null,
    peers: [{ instance_id: 'android-09', address: '10.66.0.2', public_key: 'p9', last_connection: null, remote: true }],
    remote_access: { lan_endpoint: '192.168.1.81', wireguard_udp_port: 51820, remote_peers: ['android-09'], firewall: null },
    ...over,
  };
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
  backend.on('GET', /\/network\/server$/, () => json(servidor()));
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

it('aparelho lido sem proxy diz "sem proxy (legado conferido)", e só o nunca conferido fica com o traço (29.15)', async () => {
  backend.on('GET', /\/network\/devices/, () => json({
    devices: [
      // O android-01 de 30/09: o proxy global foi lido de volta e não há nenhum. Antes aparecia como "—".
      linha({ instance_id: 'android-01', network: null,
              legacy_proxy: { proxy_id: null, name: null, value: null, state: 'applied', observed_value: null,
                              verified_at: '2026-09-30T10:00:00Z', detail: 'configuração lida de volta do aparelho',
                              effective_state: null } }),
      linha({ instance_id: 'android-07', network: null, legacy_proxy: null }),
    ],
  }));
  await render(<RedePage />);
  await waitFor(() => text().includes('android-07'));
  const tabela = Array.from(container.querySelectorAll('table')).find((t) => t.textContent?.includes('IP de saída'))!;
  const linhas = Array.from(tabela.querySelectorAll('tbody tr'));
  const proxyDe = (id: string) => linhas.find((tr) => tr.textContent?.includes(id))!.querySelectorAll('td')[2]!;
  expect(proxyDe('android-01').textContent).toBe('sem proxy (legado conferido)');
  expect(proxyDe('android-01').querySelector('[title="configuração lida de volta do aparelho"]')).not.toBeNull();
  expect(proxyDe('android-07').textContent).toBe('—');
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
  await click(await botaoPronto(/^Criar$/));
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
  await click(await botaoPronto(/^Aplicar/));
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
  await click(await botaoPronto(/^Aplicar/));
  await waitFor(() => backend.callsTo('POST', /\/network\/assign/).length === 2);
  const final = backend.callsTo('POST', /\/network\/assign/)[1]!.body as Record<string, unknown>;
  expect(final.policy).toBeUndefined();
  expect('policy' in final).toBe(false);
});

it('aparelho com conta real pede confirmação POR APARELHO antes de aplicar (ADR-056 §7): o backend, não uma heurística', async () => {
  backend.on('GET', /\/network\/devices/, () => json({
    devices: [linha({ instance_id: 'android-01', real_account: '@luciana', network: rede({ policy: 'livre' }), effective_state: 'pendente' })],
  }));
  backend.on('POST', /\/network\/assign/, (call) => {
    const b = call.body as { dry_run?: boolean; instance_ids?: string[]; confirm_real_account?: string[] };
    const confirmado = (b.confirm_real_account ?? []).includes('android-01');
    return json({ accepted: !b.dry_run, dry_run: !!b.dry_run,
      devices: (b.instance_ids ?? []).map((id) => (
        confirmado || b.dry_run === false
          ? { id, outcome: 'assigned', reason: 'ok', from: { vpn_profile_id: null, proxy_profile_id: null, policy: 'livre' },
              to: { vpn_profile_id: 'vpn-1', proxy_profile_id: null, policy: 'livre' }, reapply: true,
              warnings: confirmado ? ['conta real vinculada, confirmada pela pessoa neste pedido: @luciana'] : [] }
          : { id, outcome: 'refused', code: 'real_account_confirm_required',
              reason: `${id} tem conta real vinculada: @luciana. Mudar a saída pede confirmação`,
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
  await click(await botaoPronto(/^Aplicar/));
  const dialogo = await waitFor(() => byRole('dialog', /Mudar a saída de android-01/));
  expect(text(dialogo)).toContain('@luciana');
  // Ainda não mandou o pedido de verdade: a confirmação é obrigatória antes.
  expect(backend.callsTo('POST', /\/network\/assign/).length).toBe(1);
  await click(byRole('button', /Mudar mesmo assim/, dialogo));
  await waitFor(() => backend.callsTo('POST', /\/network\/assign/).length === 2);
  const final = backend.callsTo('POST', /\/network\/assign/)[1]!.body as Record<string, unknown>;
  expect(final.confirm_real_account).toEqual(['android-01']);
});

it('recusar a confirmação de conta real aborta o lote inteiro: nenhum segundo POST', async () => {
  backend.on('GET', /\/network\/devices/, () => json({
    devices: [linha({ instance_id: 'android-01', real_account: '@luciana', network: rede({ policy: 'livre' }), effective_state: 'pendente' })],
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
  await click(await botaoPronto(/^Aplicar/));
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
  await click(await botaoPronto(/^Aplicar/));
  await waitFor(() => backend.callsTo('POST', /\/network\/assign/).length === 3);
  const [, previa, final] = backend.callsTo('POST', /\/network\/assign/).map((c) => c.body as Record<string, unknown>);
  expect(final).toEqual({ ...previa, dry_run: false });
  expect((final!.instance_ids as string[]).slice().sort()).toEqual(['android-01', 'android-02']);
});

it('a prova de vazamento da linha aparece por revisão e cliente, e só "provado" não é alerta (29.2)', async () => {
  const base = { vpn_profile_id: 'vpn-1', policy: 'exigida_com_bloqueio' as const, state: 'trafego_verificado' as const,
                 desired_rev: 2, applied_rev: 2, leak_at: '2026-09-30T06:52:38Z',
                 leak_client: '1.14.2 (739) /data/app/~~AbC==/io.nekohasekai.sfa-XyZ==' };
  backend.on('GET', /\/network\/devices/, () => json({
    devices: [
      linha({ instance_id: 'android-01', network: rede({ ...base, leak_rev: 2, leak_result: true }) }),
      linha({ instance_id: 'android-02', network: rede({ ...base, instance_id: 'android-02', leak_rev: 1, leak_result: true }) }),
      linha({ instance_id: 'android-03', network: rede({ ...base, instance_id: 'android-03', leak_rev: 2, leak_result: null,
                                                         leak_detail: 'vazamento não medido: o ensaio foi interrompido' }) }),
      linha({ instance_id: 'android-05', network: rede({ ...base, instance_id: 'android-05', leak_rev: 2, leak_result: false }) }),
      linha({ instance_id: 'android-06', network: rede({ ...base, instance_id: 'android-06', leak_rev: 2, leak_pending: true }) }),
      linha({ instance_id: 'android-07', network: rede({ ...base, instance_id: 'android-07', policy: 'exigida' }) }),
    ],
  }));
  await render(<RedePage />);
  await waitFor(() => text().includes('bloqueio fora da VPN'));
  expect(text()).toContain('rev 2, cliente 1.14.2 (739)');                    // a pasta de instalação fica no title
  expect(text()).not.toContain('/data/app/');
  expect(text()).toContain('a prova é da rev 1, e a pedida é a 2');           // prova de outra revisão não vale
  expect(text()).toContain('não concluiu');
  expect(text()).toContain('VAZOU');
  expect(text()).toContain('teste em curso');
  // Sem bloqueio pedido, a linha nem aparece: cinco aparelhos com a frase, não seis.
  expect(text().split('bloqueio fora da VPN:').length - 1).toBe(5);
  expect(container.querySelector('[title="vazamento não medido: o ensaio foi interrompido"]')).not.toBeNull();
});

it('a última medição da sonda mostra por app, DNS, UDP, vazamento e a saída repetida vinda do backend (25.5)', async () => {
  backend.on('GET', /\/network\/devices/, () => json({
    devices: [
      linha({
        instance_id: 'android-01', effective_state: 'parcial', egress_shared_with: ['android-05'],
        network: rede({ vpn_profile_id: 'vpn-1', policy: 'exigida_com_bloqueio', state: 'parcial', egress_ipv4: '198.51.100.7', verified_at: '2026-09-29T18:23:05Z' }),
        last_measurement: {
          id: 3, instance_id: 'android-01', measured_at: '2026-09-29T18:23:05Z', method: 'sonda nc http/1.0 + netstats por uid (uid 2000)',
          egress_ipv4: '198.51.100.7', egress_ipv6: null, dns_resolver: '172.19.0.2', udp_ok: false,
          per_app: { 'com.instagram.android': 'ok', 'com.microsoft.office.outlook': 'sem_trafego', 'com.android.shell': 'ok',
                     'com.whatsapp': 'nao_medido' },
          leak_blocked: null, detail: 'IPv4 198.51.100.7 (api.ipify.org) | vazamento não medido: servidor externo',
        },
      }),
    ],
  }));
  await render(<RedePage />);
  await waitFor(() => text().includes('198.51.100.7'));
  expect(text()).toContain('mesma saída que android-05');
  expect(text()).toContain('DNS 172.19.0.2');
  expect(text()).toContain('UDP falhou');
  expect(text()).toContain('vazamento não medido');                          // null nunca vira "bloqueado"
  expect(text()).toContain('bloqueio fora da VPN: sem prova');                // com bloqueio pedido e sem prova na linha
  expect(text()).toContain('com.microsoft.office.outlook: sem tráfego na janela');      // 29.44: parado não segura
  expect(text()).toContain('com.whatsapp: não medido (não instalado ou não lido)');
  expect(text()).toContain('com.instagram.android: pelo túnel');
  expect(container.querySelectorAll('[title="Outro aparelho mediu o mesmo IP agora."]').length).toBe(1);
});

// Polimento do deploy 10: a lista por app saía com o pacote cru, e a ressalva ao lado já dizia "Outlook".
it('a lista por app diz o nome do app do registro, com o pacote na dica; sem nome, o pacote', async () => {
  useAppStore.setState({ apps: [
    { id: 'outlook', name: 'Outlook', package: 'com.microsoft.office.outlook' },
    { id: 'instagram', name: 'Instagram', package: 'com.instagram.android' },
  ] as never });
  try {
    backend.on('GET', /\/network\/devices/, () => json({
      devices: [linha({
        instance_id: 'android-01',
        last_measurement: {
          id: 4, instance_id: 'android-01', measured_at: '2026-10-03T14:57:41Z', method: 'sonda', egress_ipv4: '198.51.100.7',
          egress_ipv6: null, dns_resolver: '172.19.0.2', udp_ok: true, leak_blocked: true, detail: null,
          per_app: { 'com.instagram.android': 'ok', 'com.microsoft.office.outlook': 'sem_trafego', 'com.android.shell': 'ok',
                     'com.whatsapp': 'nao_medido' },
        },
      })],
    }));
    await render(<RedePage />);
    await waitFor(() => text().includes('Outlook: sem tráfego na janela'));
    expect(text()).toContain('Instagram: pelo túnel');
    expect(text()).toContain('shell do Android (a sonda): pelo túnel');
    expect(text()).toContain('com.whatsapp: não medido');                   // fora do registro: o pacote, como veio
    expect(text()).not.toContain('com.microsoft.office.outlook:');
    const outlook = [...container.querySelectorAll('[title]')].find((e) => e.textContent?.startsWith('Outlook:'));
    expect(outlook?.getAttribute('title')).toContain('com.microsoft.office.outlook.');
    expect(nomeDoPacote('com.exemplo', [])).toBe('com.exemplo');
  } finally {
    useAppStore.setState({ apps: [] });
  }
});

it('UDP aparece por perna, com destaque na que falhou (29.5); sem os campos novos, fica como antes', async () => {
  const medicao = (over: Partial<NonNullable<NetworkDeviceRow['last_measurement']>>) => ({
    id: 6, instance_id: 'android-03', measured_at: '2026-09-30T14:02:00Z', method: 'sonda nc http/1.0 + netstats por uid (uid 2000)',
    egress_ipv4: '198.51.100.7', egress_ipv6: null, dns_resolver: '172.19.0.2', udp_ok: false,
    per_app: { 'com.android.shell': 'ok' }, leak_blocked: null,
    detail: 'UDP DNS 83 B (1ª de 3, 2,0 s), NTP 0 B (0 de 3, 6,1 s)', ...over,
  });
  backend.on('GET', /\/network\/devices/, () => json({
    devices: [
      // A medição #6 do android-03 (30/09): o DNS respondeu e o NTP não.
      linha({ instance_id: 'android-03', last_measurement: medicao({ udp_dns_ok: true, udp_ntp_ok: false }) }),
      linha({ instance_id: 'android-05', last_measurement: medicao({ instance_id: 'android-05', udp_ok: true, udp_dns_ok: true, udp_ntp_ok: true }) }),
      // Backend de antes do 29.5 (sem os campos), e medição cujo `detail` não diz as pernas (campos nulos).
      linha({ instance_id: 'android-06', last_measurement: medicao({ instance_id: 'android-06' }) }),
      linha({ instance_id: 'android-07', last_measurement: medicao({ instance_id: 'android-07', udp_ok: null, udp_dns_ok: null, udp_ntp_ok: null }) }),
    ],
  }));
  await render(<RedePage />);
  await waitFor(() => text().includes('android-07'));
  const tabela = Array.from(container.querySelectorAll('table')).find((t) => t.textContent?.includes('IP de saída'))!;
  const celula = (id: string) => Array.from(tabela.querySelectorAll('tbody tr'))
    .find((tr) => tr.textContent?.includes(id))!.querySelectorAll('td')[5]!;
  const udp = (id: string) => Array.from(celula(id).querySelectorAll('div')).find((d) => d.textContent?.startsWith('UDP: '));
  expect(udp('android-03')!.textContent).toBe('UDP: DNS ok · NTP falhou');
  // O destaque de quem falhou vem com a explicação (as classes de CSS module não existem no jsdom: o `title` é o
  // que o teste enxerga do destaque).
  expect(udp('android-03')!.getAttribute('title')).toContain('Uma perna de UDP ficou sem resposta');
  expect(udp('android-05')!.textContent).toBe('UDP: DNS ok · NTP ok');
  expect(udp('android-05')!.getAttribute('title')).toBeNull();
  // Por perna, o "UDP falhou" genérico não aparece junto.
  expect(celula('android-03').textContent).not.toContain('UDP falhou');
  // Sem os campos novos (ou com os dois nulos), o resumo de antes: um UDP só, do `udp_ok`.
  expect(udp('android-06')).toBeUndefined();
  expect(celula('android-06').textContent).toContain('DNS 172.19.0.2 · UDP falhou · vazamento não medido');
  expect(udp('android-07')).toBeUndefined();
  expect(celula('android-07').textContent).toContain('UDP não medido');
});

it('a saída esperada aparece junto do IP medido, com destaque quando a medida é outra (29.6)', async () => {
  const esperada = { ipv4: '198.51.100.7', ipv6: null, profile_id: 'vpn-1', profile_name: 'Dedicada-01' };
  backend.on('GET', /\/network\/profiles/, () => json({
    profiles: [perfil({ name: 'Dedicada-01', params: { egress_esperado: '198.51.100.7' } }), perfil({ id: 'vpn-2', name: 'Central' })],
  }));
  backend.on('GET', /\/network\/devices/, () => json({
    devices: [
      linha({ instance_id: 'android-01', effective_state: 'parcial', egress_expected: esperada, egress_matches: false,
              network: rede({ vpn_profile_id: 'vpn-1', state: 'parcial', egress_ipv4: '198.51.100.99' }) }),
      linha({ instance_id: 'android-02', effective_state: 'trafego_verificado', egress_expected: esperada, egress_matches: true,
              network: rede({ instance_id: 'android-02', vpn_profile_id: 'vpn-1', state: 'trafego_verificado', egress_ipv4: '198.51.100.7' }) }),
      // Pedido e ainda não medido nesta revisão: a esperada aparece, sem veredito.
      linha({ instance_id: 'android-03', effective_state: 'pendente', egress_expected: esperada, egress_matches: null,
              network: rede({ instance_id: 'android-03', vpn_profile_id: 'vpn-1' }) }),
      // Perfil sem saída esperada (e backend de antes do 29.6, sem os campos): nada aparece.
      linha({ instance_id: 'android-05', network: rede({ instance_id: 'android-05', vpn_profile_id: 'vpn-2', egress_ipv4: '198.51.100.50' }) }),
    ],
  }));
  await render(<RedePage />);
  await waitFor(() => text().includes('android-05'));
  const tabela = Array.from(container.querySelectorAll('table')).find((t) => t.textContent?.includes('IP de saída'))!;
  const celula = (id: string) => Array.from(tabela.querySelectorAll('tbody tr'))
    .find((tr) => tr.textContent?.includes(id))!.querySelectorAll('td')[5]!;
  expect(celula('android-01').textContent).toContain('198.51.100.99');
  expect(celula('android-01').textContent).toContain('esperada 198.51.100.7 (perfil Dedicada-01) — a saída medida é outra');
  expect(celula('android-01').querySelector('[title^="A saída medida não é a que o perfil Dedicada-01 declara"]')).not.toBeNull();
  expect(celula('android-02').textContent).toContain('esperada 198.51.100.7 (perfil Dedicada-01) — confere');
  expect(celula('android-02').querySelector('[title^="A saída medida não é"]')).toBeNull();
  expect(celula('android-03').textContent).toContain('esperada 198.51.100.7 (perfil Dedicada-01)');
  expect(celula('android-03').textContent).not.toContain('confere');
  expect(celula('android-03').textContent).not.toContain('é outra');
  expect(celula('android-05').textContent).not.toContain('esperada');
  // O cartão de perfis diz qual saída cada perfil declara.
  expect(text()).toContain('saída esperada 198.51.100.7');
});

it('o cadastro manda a saída esperada em params só quando preenchida (29.6)', async () => {
  backend.on('POST', /\/network\/profiles$/, (call) => {
    const b = call.body as Record<string, unknown>;
    return json({ ...perfil({ id: 'vpn-9', name: b.name as string, params: (b.params ?? {}) as Record<string, unknown> }) });
  });
  await render(<RedePage />);
  await waitFor(() => text().includes('WireGuard escritório'));
  await setValue(byRole('textbox', /^Nome do perfil$/) as HTMLInputElement, 'Dedicada-01');
  await setValue(byRole('textbox', /^Host$/) as HTMLInputElement, 'vpn.provedor.example');
  await click(await botaoPronto(/^Criar$/));
  await waitFor(() => backend.callsTo('POST', /\/network\/profiles$/).length === 1);
  expect('params' in (backend.callsTo('POST', /\/network\/profiles$/)[0]!.body as object)).toBe(false);
  // Os campos ficam desabilitados enquanto o POST anterior não volta (29.115).
  await waitFor(() => !byRole('textbox', /^Nome do perfil$/).matches(':disabled'));
  await setValue(byRole('textbox', /^Nome do perfil$/) as HTMLInputElement, 'Dedicada-02');
  await setValue(byRole('textbox', /^Host$/) as HTMLInputElement, 'vpn.provedor.example');
  await setValue(byRole('textbox', /Saída esperada/) as HTMLInputElement, ' 198.51.100.8 ');
  await click(await botaoPronto(/^Criar$/));
  await waitFor(() => backend.callsTo('POST', /\/network\/profiles$/).length === 2);
  expect(backend.callsTo('POST', /\/network\/profiles$/)[1]!.body).toMatchObject({
    name: 'Dedicada-02', params: { egress_esperado: '198.51.100.8' },
  });
});

it('a prévia mostra os avisos de saída dedicada (compartilhada, ou trocada por compartilhada) sem travar o Aplicar (29.6)', async () => {
  backend.on('POST', /\/network\/assign/, (call) => {
    const b = call.body as { dry_run?: boolean; instance_ids?: string[] };
    return json({ accepted: !b.dry_run, dry_run: !!b.dry_run,
      devices: (b.instance_ids ?? []).map((id) => ({
        id, outcome: b.dry_run ? 'would_assign' : 'assigned', reason: 'configuração nova',
        from: { vpn_profile_id: 'vpn-2', proxy_profile_id: null, policy: 'livre' },
        to: { vpn_profile_id: 'vpn-1', proxy_profile_id: null, policy: 'livre' }, reapply: true, warnings: [],
        egress_warnings: id === 'android-02'
          ? [{ code: 'saida_dedicada_compartilhada', profile_id: 'vpn-1', shared_with: ['android-01'],
               message: 'o perfil Dedicada-01 declara a saída 198.51.100.7 e, com este pedido, fica também em android-01: a saída dedicada passa a ser compartilhada' }]
          : [{ code: 'saida_dedicada_trocada_por_compartilhada', profile_id: 'vpn-2',
               message: 'android-01 deixa a saída dedicada 198.51.100.9 do perfil Dedicada-02 e passa para o perfil Central, que não declara saída esperada' }],
      })),
    });
  });
  await render(<RedePage />);
  await waitFor(() => text().includes('android-02'));
  await click(byRole('checkbox', /Selecionar android-01/));
  await click(byRole('checkbox', /Selecionar android-02/));
  await setValue(byRole('combobox', /Perfil de VPN/) as HTMLSelectElement, 'vpn-1');
  await click(byRole('button', /Ver prévia/));
  await waitFor(() => text().includes('aplicaria agora'));
  const previa = container.querySelector('[aria-label="Prévia da atribuição de rede"]') as HTMLElement;
  expect(text(previa)).toContain('fica também em android-01: a saída dedicada passa a ser compartilhada');
  expect(text(previa)).toContain('android-01 deixa a saída dedicada 198.51.100.9 do perfil Dedicada-02');
  expect(previa.querySelectorAll('[data-aviso-de-saida]').length).toBe(2);
  // É aviso: o Aplicar segue liberado, e o pedido é o mesmo da prévia.
  await click(await botaoPronto(/^Aplicar/));
  await waitFor(() => backend.callsTo('POST', /\/network\/assign/).length === 2);
  expect((backend.callsTo('POST', /\/network\/assign/)[1]!.body as { dry_run: boolean }).dry_run).toBe(false);
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

it('servidor do central: conferir o firewall só LÊ e mostra o comando do dono (25.7)', async () => {
  const comando = "New-NetFirewallRule -DisplayName 'Central de Aparelhos - rede por aparelho (WireGuard UDP 51820)' "
    + "-Direction Inbound -Action Allow -Protocol UDP -LocalPort 51820 -Program 'C:\\sing-box.exe' "
    + '-RemoteAddress LocalSubnet -Profile Public';
  backend.on('POST', /\/network\/server\/firewall-check$/, () => json({
    lan_endpoint: '192.168.1.81', wireguard_udp_port: 51820, remote_peers: ['android-09'],
    firewall: { state: 'sem_regra', detail: 'firewall ligado no perfil Public com entrada padrão Block',
                endpoint: '192.168.1.81', profile: 'Public', interface: 'Wi-Fi', endpoint_is_local: true,
                allowing_rules: [], blocking_rules: [], commands: [comando], checked_at: '2026-09-29T22:12:42Z' },
  }));
  await render(<RedePage />);
  await waitFor(() => text().includes('Endereço na LAN: 192.168.1.81'));
  expect(text()).toContain('aparelhos de outra máquina: android-09');
  expect(text()).toContain('firewall ainda não lido');                         // o GET não roda PowerShell
  await click(byRole('button', /Conferir firewall/));
  await waitFor(() => text().includes('firewall sem regra'));
  expect(backend.callsTo('POST', /\/network\/server\/firewall-check$/)).toHaveLength(1);
  expect(text()).toContain(comando);
  expect(text()).toContain('a plataforma não mexe no firewall');
});

it('servidor do central sem endereço da LAN avisa que o remoto é recusado', async () => {
  backend.on('GET', /\/network\/server$/, () => json(servidor({
    remote_access: { lan_endpoint: null, wireguard_udp_port: 51820, remote_peers: [], firewall: null },
  })));
  await render(<RedePage />);
  await waitFor(() => text().includes('não configurado (rede.servidor.endpoint_lan)'));
  expect(text()).toContain('aparelhos de outra máquina: nenhum');
});

it('mostra quem ainda sai pela casa, o que está sem medida e a saída do central (29.20)', async () => {
  const home = (over: Partial<NonNullable<NetworkDeviceRow['egress_home']>>) => ({
    ipv4: false, ipv6: false, ipv6_outside_profile: false, leaves_by_home: false, reason: 'a saída medida não é a do central', ...over,
  });
  backend.on('GET', /\/network\/profiles/, () => json({ profiles: [perfil()] }));
  backend.on('GET', /\/network\/devices/, () => json({
    central_egress: { ipv4: '177.10.20.30', ipv6: null, measured_at: '2026-10-02T12:00:00+00:00', reason: 'IPv6: sem rota' },
    devices: [
      linha({ instance_id: 'android-01', egress_home: home({ ipv4: true, leaves_by_home: true, reason: 'o aparelho sai pelo IPv4 do central' }),
              network: rede({ vpn_profile_id: 'vpn-1', state: 'trafego_verificado', egress_ipv4: '177.10.20.30' }) }),
      linha({ instance_id: 'android-02', egress_home: home({ ipv6_outside_profile: true, leaves_by_home: true }),
              network: rede({ instance_id: 'android-02', vpn_profile_id: 'vpn-1', state: 'trafego_verificado', egress_ipv4: '198.51.100.9' }) }),
      linha({ instance_id: 'android-03', egress_home: home({}), network: rede({ instance_id: 'android-03', vpn_profile_id: 'vpn-1', egress_ipv4: '198.51.100.10' }) }),
      linha({ instance_id: 'android-05', egress_home: home({ ipv4: null, ipv6: null, ipv6_outside_profile: null, leaves_by_home: null, reason: 'sem rede pedida' }) }),
    ],
  }));
  await render(<RedePage />);
  await waitFor(() => text().includes('android-05'));
  expect(text()).toContain('2 aparelhos ainda saem pela casa (android-01, android-02) · 1 sem medida.');
  expect(text()).toContain('Saída medida do central: 177.10.20.30.');
  const tabela = Array.from(container.querySelectorAll('table')).find((t) => t.textContent?.includes('IP de saída'))!;
  const celula = (id: string) => Array.from(tabela.querySelectorAll('tbody tr'))
    .find((tr) => tr.textContent?.includes(id))!.querySelectorAll('td')[5]!;
  expect(celula('android-01').textContent).toContain('sai pela casa: IPv4 igual ao do central');
  expect(celula('android-02').textContent).toContain('sai pela casa: IPv6 fora do perfil');
  expect(celula('android-03').textContent).toContain('não sai pela casa');
  expect(celula('android-05').textContent).toContain('sai pela casa: sem medida');
});

it('sem egress_home (backend de antes do 29.20) o resumo da casa não aparece', async () => {
  backend.on('GET', /\/network\/profiles/, () => json({ profiles: [perfil()] }));
  backend.on('GET', /\/network\/devices/, () => json({ devices: [linha({ instance_id: 'android-01' })] }));
  await render(<RedePage />);
  await waitFor(() => text().includes('android-01'));
  expect(text()).not.toContain('Saída pela casa');
});

it('aparelho sem rede pedida aparece como "sai pela casa (presumido)" e, medido, mostra o IP da sonda (29.20)', async () => {
  const base = { ipv4: null, ipv6: null, ipv6_outside_profile: null };
  backend.on('GET', /\/network\/profiles/, () => json({ profiles: [perfil()] }));
  backend.on('GET', /\/network\/devices/, () => json({
    central_egress: { ipv4: '177.10.20.30', ipv6: null, measured_at: '2026-10-02T12:00:00+00:00', reason: 'IPv6: sem rota' },
    devices: [
      linha({ instance_id: 'android-01', egress_home: { ...base, leaves_by_home: true, basis: 'presumed', measured: null,
                                                        reason: 'presumido: sem rede pedida' } }),
      linha({ instance_id: 'android-02', egress_home: { ...base, ipv4: true, leaves_by_home: true, basis: 'measured',
                                                        measured: { ipv4: '177.10.20.30', ipv6: null, measured_at: '2026-10-02T12:01:00+00:00', source: 'probe_no_network' },
                                                        reason: 'medido: igual ao central' } }),
      linha({ instance_id: 'android-03', egress_home: { ...base, ipv4: false, leaves_by_home: false, basis: 'measured',
                                                        measured: { ipv4: '198.51.100.9', ipv6: null, measured_at: '2026-10-02T12:01:00+00:00', source: 'probe_no_network' },
                                                        reason: 'medido: diferente do central' } }),
    ],
  }));
  await render(<RedePage />);
  await waitFor(() => text().includes('android-03'));
  expect(text()).toContain('2 aparelhos ainda saem pela casa (android-01, android-02) · 1 presumido (sem rede pedida) · 0 sem medida.');
  const tabela = Array.from(container.querySelectorAll('table')).find((t) => t.textContent?.includes('IP de saída'))!;
  const celula = (id: string) => Array.from(tabela.querySelectorAll('tbody tr'))
    .find((tr) => tr.textContent?.includes(id))!.querySelectorAll('td')[5]!;
  expect(celula('android-01').textContent).toContain('sai pela casa (presumido: sem rede pedida)');
  expect(celula('android-01').textContent).toContain('não medido');
  expect(celula('android-02').textContent).toContain('177.10.20.30');
  expect(celula('android-02').textContent).toContain('sai pela casa: IPv4 igual ao do central');
  expect(celula('android-02').textContent).not.toContain('presumido');
  expect(celula('android-03').textContent).toContain('198.51.100.9');
  expect(celula('android-03').textContent).toContain('não sai pela casa');
});

it('com mais de 3 saindo pela casa, o resumo cita 3 e conta o resto; a lista inteira fica na dica (deploy 10/11)', async () => {
  // No central eram 14 ids na mesma linha: a tabela logo abaixo já diz aparelho por aparelho.
  const ids = ['android-01', 'android-02', 'android-03', 'android-05', 'android-07'];
  backend.on('GET', /\/network\/devices/, () => json({
    central_egress: { ipv4: '177.10.20.30', ipv6: null, measured_at: '2026-10-02T12:00:00+00:00', reason: '' },
    devices: ids.map((id) => linha({ instance_id: id, egress_home: {
      ipv4: true, ipv6: null, ipv6_outside_profile: null, leaves_by_home: true, basis: 'measured', measured: null,
      reason: 'medido: igual ao central' } })),
  }));
  await render(<RedePage />);
  await waitFor(() => text().includes('android-07'));
  expect(text()).toContain('5 aparelhos ainda saem pela casa (android-01, android-02, android-03 e mais 2) · 0 sem medida.');
  const resto = Array.from(container.querySelectorAll('[title]')).find((el) => el.textContent === 'e mais 2')!;
  expect(resto.getAttribute('title')).toBe(ids.join(', '));
});

it('a carga da Rede lê perfis e aparelhos uma vez só (deploy 10: eram 2× por carga)', async () => {
  await render(<RedePage />);
  await waitFor(() => text().includes('android-02'));
  await flush();
  await flush();
  expect(backend.callsTo('GET', /\/network\/profiles/)).toHaveLength(1);
  expect(backend.callsTo('GET', /\/network\/devices/)).toHaveLength(1);
});

// 29.115: a resposta do POST esvazia o cadastro. Com os campos livres durante o envio, o que a pessoa digitava para o
// próximo perfil sumia.
it('enquanto cria o perfil, o cadastro fica desabilitado; com a resposta, volta vazio e livre', async () => {
  let soltar: (() => void) | null = null;
  backend.on('POST', /\/network\/profiles$/, (call) => new Promise<Response>((r) => {
    const b = call.body as Record<string, unknown>;
    soltar = () => r(json(perfil({ id: 'vpn-9', name: b.name as string })));
  }));
  await render(<RedePage />);
  await waitFor(() => text().includes('WireGuard escritório'));
  const nome = () => byRole('textbox', /^Nome do perfil$/) as HTMLInputElement;
  await setValue(nome(), 'Dedicada-01');
  await setValue(byRole('textbox', /^Host$/) as HTMLInputElement, 'vpn.provedor.example');
  await click(await botaoPronto(/^Criar$/));
  await waitFor(() => soltar !== null);
  for (const rotulo of [/^Nome do perfil$/, /^Host$/, /^Porta$/, /^Segredo/, /Saída esperada/]) {
    expect(byRole('textbox', rotulo).matches(':disabled'), String(rotulo)).toBe(true);
  }
  await expect(setValue(nome(), 'Dedicada-02')).rejects.toThrow('está desabilitado');

  await act(async () => { soltar!(); });
  await waitFor(() => !nome().matches(':disabled'));
  expect(nome().value).toBe('');
});

// 29.130: a releitura da lista depois de criar não trava o cadastro (ela ficava dentro do try do criar).
it('criado o perfil, o cadastro volta livre já com a releitura da lista em voo', async () => {
  let soltarLista: (() => void) | null = null;
  backend.on('POST', /\/network\/profiles$/, (call) => json(perfil({ id: 'vpn-9', name: (call.body as { name: string }).name })));
  await render(<RedePage />);
  await waitFor(() => text().includes('WireGuard escritório'));
  const lista = backend.callsTo('GET', /\/network\/profiles$/).length;
  backend.on('GET', /\/network\/profiles$/, () => new Promise<Response>((r) => {
    soltarLista = () => r(json({ profiles: [] }));
  }));
  const nome = () => byRole('textbox', /^Nome do perfil$/) as HTMLInputElement;
  await setValue(nome(), 'Dedicada-01');
  await setValue(byRole('textbox', /^Host$/) as HTMLInputElement, 'vpn.provedor.example');
  await click(await botaoPronto(/^Criar$/));
  await waitFor(() => soltarLista !== null);
  expect(backend.callsTo('GET', /\/network\/profiles$/).length).toBe(lista + 1);
  await waitFor(() => !nome().matches(':disabled'));
  await setValue(nome(), 'Dedicada-02');                                  // a pessoa já começa o próximo
  await act(async () => { soltarLista!(); });
  expect(nome().value).toBe('Dedicada-02');
});

// 29.130 (S1 da leitura): com o cadastro livre, dois perfis criados em seguida disparam duas releituras; a mais velha
// que responde por último não apaga o 2º da tela.
it('duas releituras fora de ordem: vale a mais nova, e o 2º perfil criado não some da lista', async () => {
  let n = 0;
  backend.on('POST', /\/network\/profiles$/, (call) => json(perfil({ id: `vpn-${++n}`, name: (call.body as { name: string }).name })));
  await render(<RedePage />);
  await waitFor(() => text().includes('WireGuard escritório'));
  const soltar: (() => void)[] = [];
  const respostas = [
    { profiles: [perfil({ id: 'vpn-1', name: 'Dedicada-01' })] },
    { profiles: [perfil({ id: 'vpn-1', name: 'Dedicada-01' }), perfil({ id: 'vpn-2', name: 'Dedicada-02' })] },
  ];
  backend.on('GET', /\/network\/profiles$/, () => {
    const corpo = respostas[soltar.length]!;
    return new Promise<Response>((r) => { soltar.push(() => r(json(corpo))); });
  });
  const nome = () => byRole('textbox', /^Nome do perfil$/) as HTMLInputElement;
  for (const n2 of ['Dedicada-01', 'Dedicada-02']) {
    await waitFor(() => !nome().matches(':disabled'));
    await setValue(nome(), n2);
    await setValue(byRole('textbox', /^Host$/) as HTMLInputElement, 'vpn.provedor.example');
    await click(await botaoPronto(/^Criar$/));
    await waitFor(() => soltar.length === (n2 === 'Dedicada-01' ? 1 : 2));
  }
  await act(async () => { soltar[1]!(); });                              // a mais nova chega primeiro
  await waitFor(() => text().includes('Dedicada-02'));
  await act(async () => { soltar[0]!(); });                              // a velha chega depois e não escreve
  // Afirmar a AUSÊNCIA de mudança só prova algo depois de a resposta velha passar por todo o caminho (fetch falso,
  // json, Promise.all com os aparelhos), inclusive com o atraso do modo ATRASO_DO_FETCH_MS.
  await flush(atrasoMaximoDoFetchMs() + 30);
  await flush();
  expect(text()).toContain('Dedicada-02');
});

// ---- 31.301: editar a saída esperada do perfil (PUT /api/network/profiles/{id}, adendo v1.134) -----------------------------------

const COM_SAIDA = perfil({ id: 'igfarm-1', name: 'igfarm-acc-1', kind: 'proxy', protocol: 'socks5', has_secret: true,
                           params: { egress_esperado: '203.0.113.10' }, in_use: ['android-05'] });

async function abrirEditor(perfilAtual: NetworkProfileListed = COM_SAIDA): Promise<void> {
  backend.on('GET', /\/network\/profiles$/, () => json({ profiles: [perfilAtual] }));
  await render(<RedePage />);
  await waitFor(() => text().includes(perfilAtual.name));
  await click(byRole('button', new RegExp(`Editar a saída esperada de ${perfilAtual.name}`)));
}

it('31.301: troca o IPv4 e manda só o que mudou, com antes e depois à vista', async () => {
  backend.on('PUT', /\/network\/profiles\/igfarm-1$/, () => json(COM_SAIDA));
  await abrirEditor();
  expect(byRole('button', /Salvar saída esperada/).getAttribute('aria-disabled')).toBe('true');   // nada mudou
  await setValue(byRole('textbox', /Saída esperada IPv4/) as HTMLInputElement, '198.51.100.7');
  expect(text()).toContain('IPv4: 203.0.113.10 → 198.51.100.7');
  await click(await botaoPronto(/Salvar saída esperada/));
  await waitFor(() => backend.callsTo('PUT', /\/network\/profiles\/igfarm-1$/).length === 1);
  expect(backend.callsTo('PUT', /\/network\/profiles\/igfarm-1$/)[0]?.body).toEqual({ egress_esperado: '198.51.100.7' });
  await waitFor(() => useToastStore.getState().toasts.some((t) => (t.message ?? '').includes('Nada foi reavaliado')));
});

it('31.301: esvaziar o campo tira a saída daquela família (null); campo vazio sem valor antes não manda nada', async () => {
  backend.on('PUT', /\/network\/profiles\/igfarm-1$/, () => json(COM_SAIDA));
  await abrirEditor();
  await setValue(byRole('textbox', /Saída esperada IPv4/) as HTMLInputElement, '');
  expect(text()).toContain('IPv4: 203.0.113.10 → sem saída esperada');
  await click(await botaoPronto(/Salvar saída esperada/));
  await waitFor(() => backend.callsTo('PUT', /\/network\/profiles\/igfarm-1$/).length === 1);
  expect(backend.callsTo('PUT', /\/network\/profiles\/igfarm-1$/)[0]?.body).toEqual({ egress_esperado: null });
});

it('31.301: 409 egress_esperado_protegido explica o porquê, pede o motivo e repete o PUT com ele', async () => {
  let chamadas = 0;
  backend.on('PUT', /\/network\/profiles\/igfarm-1$/, (c) => {
    chamadas += 1;
    if ((c.body as { motivo?: string }).motivo) return json(COM_SAIDA);
    return new Response(JSON.stringify({ detail: {
      code: 'egress_esperado_protegido', message: "O perfil 'igfarm-acc-1' é o da conta acc-1 do igfarm, em uso por android-05: trocar apaga a prova.",
      profile_id: 'igfarm-1', account_id: 'acc-1', in_use: ['android-05'], motivo_obrigatorio: true,
      antes: { egress_esperado: '203.0.113.10', egress_esperado_ipv6: null }, depois: { egress_esperado: '198.51.100.7', egress_esperado_ipv6: null },
    } }), { status: 409, headers: { 'Content-Type': 'application/json' } });
  });
  await abrirEditor();
  await setValue(byRole('textbox', /Saída esperada IPv4/) as HTMLInputElement, '198.51.100.7');
  await click(await botaoPronto(/Salvar saída esperada/));
  await waitFor(() => text().includes('trocar apaga a prova'));
  expect(text()).toContain('Conta do igfarm: acc-1');
  expect(text()).toContain('Em uso por: android-05');
  const trocar = byRole('button', /Trocar mesmo assim/);
  expect(trocar.getAttribute('aria-disabled')).toBe('true');          // sem motivo não há como repetir
  await setValue(byRole('textbox', /Motivo da troca/) as HTMLInputElement, 'o proxy foi recriado com outro IP de saída');
  await click(await botaoPronto(/Trocar mesmo assim/));
  await waitFor(() => chamadas === 2);
  expect(backend.callsTo('PUT', /\/network\/profiles\/igfarm-1$/)[1]?.body)
    .toEqual({ egress_esperado: '198.51.100.7', motivo: 'o proxy foi recriado com outro IP de saída' });
});

it('31.301: 422 invalid_egress aparece no campo, sem toast de erro genérico, e nada é gravado', async () => {
  backend.on('PUT', /\/network\/profiles\/igfarm-1$/, () => apiError(422, 'invalid_egress', 'egress_esperado deve ser um IPv4 público.'));
  await abrirEditor();
  await setValue(byRole('textbox', /Saída esperada IPv4/) as HTMLInputElement, '10.0.0.1');
  await click(await botaoPronto(/Salvar saída esperada/));
  await waitFor(() => text().includes('deve ser um IPv4 público'));
  expect(useToastStore.getState().toasts.filter((t) => t.tone === 'danger')).toHaveLength(0);
});

it('31.301: com várias trocas, a lista começa pela mais nova e mostra só as três últimas', async () => {
  const troca = (id: number, hora: string, de: string, para: string) => ({
    id, ts: `2026-10-10T${hora}Z`, kind: 'network.updated', level: 'info' as const, run_id: null, instance_id: null, objective_id: null,
    step_id: null, attempt_id: null, message: 'Rede: saída esperada mudou',
    data: { acao: 'perfil_atualizado', profile_id: 'igfarm-1', antes: { egress_esperado: de, egress_esperado_ipv6: null },
            depois: { egress_esperado: para, egress_esperado_ipv6: null }, motivo: null, quem: 'Flavio' },
  });
  useAppStore.setState({ recentEvents: [
    troca(1, '10:00:00', '1.1.1.1', '2.2.2.2'), troca(2, '11:00:00', '2.2.2.2', '3.3.3.3'),
    troca(3, '12:00:00', '3.3.3.3', '4.4.4.4'), troca(4, '13:00:00', '4.4.4.4', '5.5.5.5'),
  ] });
  await abrirEditor();
  const itens = Array.from(container.querySelectorAll('ul[aria-label^="Trocas da saída esperada de igfarm-acc-1"] li')).map((l) => text(l as HTMLElement));
  expect(itens).toHaveLength(3);
  expect(itens[0]).toContain('4.4.4.4 → 5.5.5.5');
  expect(itens[2]).toContain('2.2.2.2 → 3.3.3.3');
  expect(itens.join(' ')).not.toContain('1.1.1.1 → 2.2.2.2');   // a mais antiga saiu do corte
});

it('31.301: a troca que chegou pelo evento network.updated aparece na linha do perfil, com antes, depois, motivo e quem', async () => {
  useAppStore.setState({ recentEvents: [{
    id: 501, ts: '2026-10-10T13:40:00Z', kind: 'network.updated', level: 'info', run_id: null, instance_id: null, objective_id: null,
    step_id: null, attempt_id: null, message: 'Rede: saída esperada do perfil igfarm-acc-1 mudou',
    data: { acao: 'perfil_atualizado', profile_id: 'igfarm-1', antes: { egress_esperado: '203.0.113.10', egress_esperado_ipv6: null },
            depois: { egress_esperado: '198.51.100.7', egress_esperado_ipv6: null }, motivo: 'proxy recriado', quem: 'Flavio' },
  }] });
  await abrirEditor();
  const lista = container.querySelector('ul[aria-label^="Trocas da saída esperada de igfarm-acc-1"]') as HTMLElement;
  expect(text(lista)).toContain('203.0.113.10 → 198.51.100.7');
  expect(text(lista)).toContain('motivo: proxy recriado');
  expect(text(lista)).toContain('por Flavio');
});

// ---- 31.303: saída medida × esperada com a divergência destacada e o atalho ao editor ------------------------------------------------

const ESPERADA_303 = { ipv4: '198.51.100.7', ipv6: null, profile_id: 'vpn-1', profile_name: 'Dedicada-01' };

async function aparelhosDivergentes(): Promise<void> {
  backend.on('GET', /\/network\/profiles$/, () => json({
    profiles: [perfil({ id: 'vpn-1', name: 'Dedicada-01', params: { egress_esperado: '198.51.100.7' } })],
  }));
  backend.on('GET', /\/network\/devices/, () => json({
    devices: [
      linha({ instance_id: 'android-01', effective_state: 'parcial', egress_expected: ESPERADA_303, egress_matches: false,
              network: rede({ vpn_profile_id: 'vpn-1', state: 'parcial', egress_ipv4: '198.51.100.99' }) }),
      linha({ instance_id: 'android-02', effective_state: 'trafego_verificado', egress_expected: ESPERADA_303, egress_matches: true,
              network: rede({ instance_id: 'android-02', vpn_profile_id: 'vpn-1', state: 'trafego_verificado', egress_ipv4: '198.51.100.7' }) }),
      linha({ instance_id: 'android-03', effective_state: 'pendente', egress_expected: ESPERADA_303, egress_matches: null,
              network: rede({ instance_id: 'android-03', vpn_profile_id: 'vpn-1' }) }),
    ],
  }));
  await render(<RedePage />);
  await waitFor(() => text().includes('android-03'));
}

it('31.303: o aparelho divergente mostra medida × esperada por família; os outros dois não mostram nada a mais', async () => {
  await aparelhosDivergentes();
  const listas = Array.from(container.querySelectorAll('ul[aria-label="Medida × esperada"]'));
  expect(listas).toHaveLength(1);
  expect(text(listas[0] as HTMLElement)).toContain('IPv4: medida 198.51.100.99 × esperada 198.51.100.7');
  expect(text(listas[0] as HTMLElement)).toContain('diverge');
  const linhaDe = (id: string) => Array.from(container.querySelectorAll('tbody tr')).find((tr) => tr.textContent?.includes(id))!;
  expect(linhaDe('android-02').textContent).not.toContain('Corrigir a esperada');
  expect(linhaDe('android-03').textContent).not.toContain('Corrigir a esperada');
  expect(text(listas[0]!.parentElement as HTMLElement)).toContain('sessão fixa no proxy');
});

it('31.303: o resumo acima da tabela conta só o que o backend marcou como divergente e lista os aparelhos', async () => {
  await aparelhosDivergentes();
  expect(text()).toContain('1 aparelho sai por um IP diferente do esperado');
  expect(text()).toContain('android-01. Com política exigida');
});

it('31.303: sem divergência (ou backend antigo, sem os campos) o resumo e o detalhe não aparecem', async () => {
  backend.on('GET', /\/network\/devices/, () => json({
    devices: [
      linha({ instance_id: 'android-02', egress_expected: ESPERADA_303, egress_matches: true,
              network: rede({ instance_id: 'android-02', vpn_profile_id: 'vpn-1', egress_ipv4: '198.51.100.7' }) }),
      linha({ instance_id: 'android-05', network: rede({ instance_id: 'android-05', vpn_profile_id: 'vpn-1', egress_ipv4: '198.51.100.50' }) }),
    ],
  }));
  await render(<RedePage />);
  await waitFor(() => text().includes('android-05'));
  expect(text()).not.toContain('por um IP diferente do esperado');
  expect(container.querySelector('ul[aria-label="Medida × esperada"]')).toBeNull();
});

it('31.303: "Corrigir a esperada" abre o editor do perfil que declara a saída, e abre de novo depois de fechado', async () => {
  await aparelhosDivergentes();
  expect(container.querySelector('[aria-expanded="true"]')).toBeNull();
  await click(byRole('button', /^Corrigir a saída esperada do perfil Dedicada-01$/));
  await waitFor(() => !!container.querySelector('input[placeholder="203.0.113.10"]'));
  expect((byRole('textbox', /Saída esperada IPv4/) as HTMLInputElement).value).toBe('198.51.100.7');
  await click(byRole('button', /Editar a saída esperada de Dedicada-01$/));   // a pessoa fecha o editor
  expect(container.querySelector('input[placeholder="203.0.113.10"]')).toBeNull();
  await click(byRole('button', /Editar a saída esperada do perfil Dedicada-01 \(resumo\)/));   // o atalho do resumo reabre
  await waitFor(() => !!container.querySelector('input[placeholder="203.0.113.10"]'));
});
