// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type { PersonaDTO, PolicyGroup } from '../../api/types';
import { metaDaSessao, rotuloDaFila } from '../../lib/status';
import { pendenciasDeSessoes } from '../pendencias/modelo';
import { useAppStore } from '../../store/app';
import { initialDataState } from '../../store/reducer';
import { FakeBackend, installBrowserStubs, json, text, waitFor } from '../../test/harness';
import { estadoDaSessao } from './estadoSessao';
import { bloqueiosDaPersona, lerMotivo, linhasDoMotivo, ListaDeContasRetiradas, MotivoDoBloqueio, unirBloqueios } from './MotivoDoBloqueio';
import { PolicyGroupsSection } from './PolicyGroups';

/**
 * 31.326 (adendo v1.142 do 31.322): a tela humana é bloqueio DEFINITIVO. O painel diz "bloqueada" (não "aguardando
 * intervenção"), mostra o motivo guardado e não conta a persona de teste no grupo de acesso. Prova `simulated`.
 */

const TELA = 'conta_travada: Confirm you are human';
const sessao = (detail: string | null, status = 'needs_person') => ({
  status, instance_id: 'android-03', observed_username: null, verified_at: null, detail, stale: false,
}) as PersonaDTO['session'];

describe('rótulo da fila', () => {
  it('a tela humana e a retirada por bloqueio são "bloqueada"; o resto espera uma pessoa', () => {
    expect(rotuloDaFila(TELA)).toBe('bloqueada');
    expect(rotuloDaFila('conta retirada por bloqueio')).toBe('bloqueada');
    expect(rotuloDaFila('o app pediu o código enviado por e-mail')).toBe('aguardando');
    expect(rotuloDaFila(null)).toBe('aguardando');
    // o servidor manda o rótulo no evento: ele vale sobre o texto
    expect(rotuloDaFila('qualquer', 'bloqueada')).toBe('bloqueada');
    expect(rotuloDaFila(TELA, 'aguardando')).toBe('aguardando');
    expect(rotuloDaFila(TELA, 'outra coisa')).toBe('bloqueada');
  });

  it('o selo da sessão vira "Conta bloqueada"; sem a tela humana segue "Precisa de uma pessoa"', () => {
    expect(metaDaSessao(sessao(TELA)).label).toBe('Conta bloqueada');
    expect(metaDaSessao(sessao('pediu o código')).label).toBe('Precisa de uma pessoa');
    expect(metaDaSessao(sessao(null, 'session_ready')).label).not.toBe('Conta bloqueada');
  });

  it('o cabeçalho da persona não oferece "Resolver" para o bloqueio definitivo', () => {
    const b = estadoDaSessao(sessao(TELA));
    expect(b.meta.label).toBe('Conta bloqueada');
    expect(b.acao?.rotulo).toBe('Ver conta');
    expect(estadoDaSessao(sessao('pediu o código')).acao?.rotulo).toBe('Resolver');
  });

  it('a caixa de pendências diz bloqueio definitivo, e não "só uma pessoa resolve"', () => {
    const p = { id: 'ig-1', name: 'A', display_name: 'A', username: 'a.b', instance_id: 'android-03', session: sessao(TELA) } as PersonaDTO;
    const q = { ...p, id: 'ig-2', username: 'c.d', session: sessao('pediu o código') } as PersonaDTO;
    const [bloq, espera] = pendenciasDeSessoes([p, q]);
    expect(bloq!.detalhe).toContain('bloqueio definitivo');
    expect(bloq!.detalhe).not.toContain('só uma pessoa resolve');
    expect(bloq!.acao).toBe('Ver');
    expect(espera!.detalhe).toContain('só uma pessoa resolve');
    expect(espera!.acao).toBe('Resolver');
  });
});

describe('motivo do bloqueio', () => {
  const completo = {
    egresso_esperado: '203.0.113.5', egresso_medido: '198.51.100.7', egresso_divergente: true,
    ips_distintos_desde_criacao: 3, minutos_ate_o_primeiro_login: 4.5, trecho_da_tela: 'Confirm you are human',
  };

  it('lê só o que tem o tipo certo e nunca inventa o que falta', () => {
    expect(lerMotivo(null)).toBeNull();
    expect(lerMotivo('x')).toBeNull();
    expect(lerMotivo({ egresso_esperado: 5, ips_distintos_desde_criacao: 'três' })).toEqual({
      egresso_esperado: null, egresso_medido: null, egresso_divergente: null, ips_distintos_desde_criacao: null,
      minutos_ate_o_primeiro_login: null, trecho_da_tela: null,
    });
    expect(linhasDoMotivo(lerMotivo({}))).toEqual([]);
    expect(linhasDoMotivo(null)).toEqual([]);
  });

  it('descreve o padrão: saída divergente, rotação, tempo até o login e o trecho da tela', () => {
    const l = linhasDoMotivo(lerMotivo(completo));
    expect(l[0]).toBe('Saída (IP): esperada 203.0.113.5, medida 198.51.100.7 — divergente');
    expect(l[1]).toBe('3 IPs de saída distintos desde a criação');
    expect(l[2]).toBe('4,5 min entre a criação da conta e o primeiro login');
    expect(l[3]).toContain('Confirm you');
    expect(linhasDoMotivo(lerMotivo({ egresso_esperado: '1.1.1.1', egresso_divergente: null }))[0])
      .toBe('Saída (IP): esperada 1.1.1.1, medida não medida');
  });

  it('só os eventos desta persona, do mais novo ao mais antigo', () => {
    const ev = (ts: string, pid: string) => ({ kind: 'profile.account_retired', ts, data: { profile_id: pid, app_id: 'com.instagram.android', motivo_do_bloqueio: completo } });
    const r = bloqueiosDaPersona([ev('2026-10-10T10:00:00Z', 'ig-1'), ev('2026-10-10T11:00:00Z', 'ig-2'), ev('2026-10-10T12:00:00Z', 'ig-1'),
                                  { kind: 'log', ts: 'x', data: { profile_id: 'ig-1' } }], 'ig-1');
    expect(r.map((b) => b.quando)).toEqual(['2026-10-10T12:00:00Z', '2026-10-10T10:00:00Z']);
  });
});

describe('telas', () => {
  let root: Root;
  let container: HTMLElement;

  beforeAll(() => installBrowserStubs());
  beforeEach(() => {
    new FakeBackend().install();
    container = document.createElement('div');
    document.body.append(container);
    root = createRoot(container);
    useAppStore.setState({ ...initialDataState, hydrated: true, hydrateCount: 1 });
  });
  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
  });

  const evento = (id: number, data: Record<string, unknown>) => ({
    id, ts: new Date().toISOString(), kind: 'profile.account_retired', level: 'warn' as const, run_id: null, instance_id: null,
    objective_id: null, step_id: null, attempt_id: null, message: 'retirada', data,
  });

  it('a ficha mostra o cartão "Conta bloqueada" com o motivo; sem evento, não mostra nada', async () => {
    await act(async () => root.render(<MotivoDoBloqueio profileId="ig-1" />));
    expect(container.textContent).toBe('');
    await act(async () => {
      useAppStore.setState({
        recentEvents: [evento(1, {
          profile_id: 'ig-1', app_id: 'com.instagram.android',
          motivo_do_bloqueio: { egresso_esperado: '203.0.113.5', egresso_medido: null, egresso_divergente: null, ips_distintos_desde_criacao: 1, minutos_ate_o_primeiro_login: null, trecho_da_tela: null },
        })],
      });
    });
    await waitFor(() => text(container).includes('Conta bloqueada'));
    expect(text(container)).toContain('esperada 203.0.113.5, medida não medida');
    expect(text(container)).toContain('1 IP de saída desde a criação (sem rotação)');
    expect(text(container)).toContain('O bloqueio é definitivo');
  });

  it('bloqueio sem motivo registrado diz isso', async () => {
    useAppStore.setState({ recentEvents: [evento(2, { profile_id: 'ig-1', motivo_do_bloqueio: null })] });
    await act(async () => root.render(<MotivoDoBloqueio profileId="ig-1" />));
    expect(text(container)).toContain('Sem motivo registrado para este bloqueio.');
  });

  it('o grupo de acesso não conta a persona de teste: mostra "+N de teste" à parte', async () => {
    const grupo = {
      id: 'g1', name: 'Liberado', description: '', capabilities: {}, loosened: [],
      members: [{ id: 'ig-1', username: 'a.b' }, { id: 'ig-2', username: 'c.d' }, { id: 'ig-t', username: null, name: 'TESTE X' }],
    } as unknown as PolicyGroup;
    const profiles = [{ id: 'ig-1' }, { id: 'ig-2' }, { id: 'ig-t', teste: true }];
    const be = new FakeBackend();
    be.install();
    be.on('GET', /app-catalog/, () => json([]));
    await act(async () => root.render(<PolicyGroupsSection grupos={[grupo]} profiles={profiles as never} onChanged={async () => {}} />));
    await waitFor(() => text(container).includes('Liberado'));
    const chips = container.querySelector('[aria-label="Personas no grupo Liberado"]') as HTMLElement;
    expect(text(chips)).toContain('2 personas');
    expect(text(chips)).toContain('+1 de teste');
    expect(text(chips)).not.toContain('TESTE X');
  });
  it('achado do percurso real do deploy 73: a persona de teste SEM conta (sem @) também sai da conta do grupo', async () => {
    const grupo = {
      id: 'g1', name: 'Liberado', description: '', capabilities: {}, loosened: [],
      members: [{ id: 'ig-1', username: 'a.b' }, { id: 'ig-t', username: null, name: 'TESTE X' }],
    } as unknown as PolicyGroup;
    const be = new FakeBackend();
    be.install();
    be.on('GET', /app-catalog/, () => json([]));
    await act(async () => root.render(<PolicyGroupsSection grupos={[grupo]} profiles={[{ id: 'ig-1' }] as never}
                                                           todas={[{ id: 'ig-1' }, { id: 'ig-t', teste: true }] as never} onChanged={async () => {}} />));
    await waitFor(() => text(container).includes('Liberado'));
    const chips = container.querySelector('[aria-label="Personas no grupo Liberado"]') as HTMLElement;
    expect(text(chips)).toContain('1 persona');
    expect(text(chips)).toContain('+1 de teste');
    expect(text(chips)).not.toContain('TESTE X');
  });
});

describe('contas_retiradas (v1.142)', () => {
  let root: Root;
  let container: HTMLElement;
  beforeAll(() => installBrowserStubs());
  beforeEach(() => {
    new FakeBackend().install();
    container = document.createElement('div');
    document.body.append(container);
    root = createRoot(container);
    useAppStore.setState({ ...initialDataState, hydrated: true, hydrateCount: 1 });
  });
  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
  });

  const motivo = { egresso_esperado: '203.0.113.5', egresso_medido: '198.51.100.7', egresso_divergente: true,
                   ips_distintos_desde_criacao: 2, minutos_ate_o_primeiro_login: 7, trecho_da_tela: null };
  const retirada = { app_id: 'com.instagram.android', retirada_em: '2026-10-10T12:00:00Z', motivo_do_bloqueio: motivo };

  it('une a lápide com o evento ao vivo sem duplicar o mesmo bloqueio; ausência do campo vale lista vazia', () => {
    const vivo = { quando: '2026-10-10T12:00:20Z', app: 'com.instagram.android', motivo: lerMotivo(motivo) };
    expect(unirBloqueios(undefined, [])).toEqual([]);
    expect(unirBloqueios([retirada], [vivo])).toHaveLength(1);
    const outro = { ...vivo, quando: '2026-10-10T15:00:00Z' };
    expect(unirBloqueios([retirada], [outro]).map((b) => b.quando)).toEqual(['2026-10-10T15:00:00Z', '2026-10-10T12:00:00Z']);
    expect(unirBloqueios(undefined, [vivo])).toHaveLength(1);
  });

  it('a ficha mostra o motivo da lápide sem evento nenhum (depois de recarregar)', async () => {
    await act(async () => root.render(<MotivoDoBloqueio profileId="ig-1" retiradas={[retirada]} />));
    expect(text(container)).toContain('Conta bloqueada');
    expect(text(container)).toContain('esperada 203.0.113.5, medida 198.51.100.7 — divergente');
    expect(text(container)).toContain('2 IPs de saída distintos desde a criação');
  });

  it('a lista de contas retiradas: uma linha por conta, com o motivo; some sem retiradas; antiga sem motivo diz isso', async () => {
    const abertas: string[] = [];
    await act(async () => root.render(<ListaDeContasRetiradas personas={[{ id: 'ig-1', name: 'Ana' }]} />));
    expect(container.textContent).toBe('');
    await act(async () => root.render(<ListaDeContasRetiradas abrir={(id) => abertas.push(id)} personas={[
      { id: 'ig-1', name: 'Ana', contas_retiradas: [retirada] },
      { id: 'ig-2', name: 'Bia', contas_retiradas: [{ app_id: 'com.instagram.android', retirada_em: '2026-09-01T10:00:00Z', motivo_do_bloqueio: null }] },
    ]} />));
    expect(text(container)).toContain('Contas retiradas por bloqueio');
    expect(text(container)).toContain('Ana');
    expect(text(container)).toContain('esperada 203.0.113.5');
    expect(text(container)).toContain('Sem motivo registrado para este bloqueio.');
    const itens = container.querySelectorAll('ul[aria-label="Contas retiradas por bloqueio"] li');
    expect(itens).toHaveLength(2);
    expect(itens[0]!.textContent).toContain('Ana');
    (container.querySelector('button') as HTMLButtonElement).click();
    expect(abertas).toEqual(['ig-1']);
  });
});
