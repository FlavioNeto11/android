// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type { AiStatus } from '../../api/types';
import { useAppStore } from '../../store/app';
import { initialDataState } from '../../store/reducer';
import { makeSnapshot } from '../../test/fixtures';
import { FakeBackend, installBrowserStubs, json, text, waitFor } from '../../test/harness';
import { AiSection } from './AiSection';

let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

const BASE: AiStatus = {
  provider: 'anthropic', model: 'claude-opus-5', configured: true, simulated: false, sends_data_externally: true,
  notice: 'Provedor externo.', effort: 'medium',
};

async function renderSection(status: AiStatus): Promise<HTMLElement> {
  backend.on('GET', /^\/api\/ai$/, () => json(status));
  await act(async () => {
    root.render(<AiSection />);
  });
  await waitFor(() => expect(backend.callsTo('GET', /^\/api\/ai$/)).toHaveLength(1));
  // O pedido registrado não é a resposta: espera a seção sair do "Consultando" (29.104).
  await waitFor(() => !text(container).includes('Consultando o status da IA'));
  return container;
}

beforeAll(() => installBrowserStubs());

beforeEach(() => {
  backend = new FakeBackend();
  backend.install();
  useAppStore.setState({ ...initialDataState, settings: makeSnapshot().settings });
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe('AiSection — disjuntor de conta de IA (achado #90)', () => {
  it('conta configurada e sem disjuntor: Situação "Pronta para uso"', async () => {
    const el = await renderSection(BASE);
    expect(text(el)).toContain('Pronta para uso');
    expect(text(el)).not.toContain('disjuntor');
  });

  it('disjuntor acionado: Situação some de "Pronta para uso" e o motivo aparece, sem o dicionário cru do provedor', async () => {
    const el = await renderSection({
      ...BASE, account_blocked: true,
      account_blocked_reason: 'Sem crédito no provedor de IA — recarregue e retome.',
    });
    expect(text(el)).toContain('Bloqueada (disjuntor)');
    expect(text(el)).not.toContain('Pronta para uso');
    expect(text(el)).toContain('Disjuntor de conta de IA acionado');
    expect(text(el)).toContain('Sem crédito no provedor de IA — recarregue e retome.');
    expect(text(el)).not.toContain('invalid_request_error');
  });
});

describe('AiSection — hub de IA (itens 7.1 e 7.2)', () => {
  const PAPEL = {
    role: 'decide', provider: 'local', kind: 'openai', model: 'qwen-vl', endpoint: '127.0.0.1:8001',
    sends_data_externally: false, configured: true, priced: false, vision: true, tools: true,
    refusal_fallback: false, fallback_provider: null, timeout_s: 45, concurrency: 8, effort: 'low',
  } as const;
  const PLANEJADOR = {
    role: 'plan', provider: 'anthropic', kind: 'anthropic', model: 'claude-opus-5', endpoint: 'api.anthropic.com',
    sends_data_externally: true, configured: true, priced: true, vision: true, tools: true,
    refusal_fallback: true, fallback_provider: null, timeout_s: 120, concurrency: 4, effort: 'medium',
  } as const;

  it('mostra provedor, endpoint e "os dados saem?" POR função', async () => {
    const el = await renderSection({ ...BASE, roles: [PAPEL, PLANEJADOR] });
    expect(text(el)).toContain('Por função');
    expect(text(el)).toContain('127.0.0.1:8001');
    expect(text(el)).toContain('api.anthropic.com');
    // O modelo local não tem preço cadastrado: a tela diz isso em vez de deixar somar zero escondido.
    expect(text(el)).toContain('modelo sem preço cadastrado');
  });

  it('função sem fallback declarado diz que o erro sobe — e a que tem diz para onde cai', async () => {
    const el = await renderSection({
      ...BASE, roles: [PAPEL, { ...PLANEJADOR, fallback_provider: 'anthropic' }],
    });
    expect(text(el)).toContain('o erro sobe (sem fallback pago)');
    expect(text(el)).toContain('cai para');
  });

  it('a aba IA passa a dizer que o fallback pago de recusa está ligado, e qual é o alvo', async () => {
    const semHub = await renderSection(BASE);
    expect(text(semHub)).not.toContain('Fallback pago de recusa');
    await act(async () => root.unmount());
    root = createRoot(container);
    backend = new FakeBackend();
    backend.install();
    const el = await renderSection({
      ...BASE, refusal_fallback: true,
      refusal_fallback_target: 'definido pelo provedor (documentado: claude-opus-4-8)',
      spend_today_usd: 8.88, spend_limit_day_usd: 25,
    });
    expect(text(el)).toContain('Fallback pago de recusa está ligado');
    expect(text(el)).toContain('claude-opus-4-8');
    expect(text(el)).toContain('US$ 8.88 de US$ 25.00');
  });
});

describe('AiSection — I2 da validação do deploy 7 (v0.87)', () => {
  const PLANEJADOR = {
    role: 'plan', provider: 'anthropic', kind: 'anthropic', model: 'claude-opus-5-5', endpoint: 'api.anthropic.com',
    sends_data_externally: true, configured: true, priced: true, vision: true, tools: true,
    refusal_fallback: false, fallback_provider: null, timeout_s: 120, concurrency: 4, effort: 'low', thinking: 'adaptive',
  } as const;

  it('Situação em português: esforço, esquema do plano, perfis e leitura visual', async () => {
    const el = await renderSection({
      ...BASE, effort: 'low', esquema_do_plano: 'curto', leitura_visual: false, roles: [PLANEJADOR],
      profiles: [{ name: 'planejador-sonnet', note: '', canary_fraction: null, screenshot_max_side: null, rich_tree_min_elements: null,
        roles: [{ role: 'plan', provider: 'anthropic', model: 'claude-sonnet-5-5', effort: 'low', sends_data_externally: true }] }],
    });
    expect(text(el)).toContain('Esforço de raciocínio');
    expect(text(el)).toContain('baixo');
    expect(text(el)).not.toMatch(/Esforço de raciocínio\s*low/);
    expect(text(el)).toContain('Esquema do plano');
    expect(text(el)).toContain('curto (o backend preenche');
    expect(text(el)).toContain('Leitura visual');
    expect(text(el)).toContain('desligada');
  });

  it('a tabela Por função ganha Esforço e Raciocínio; o cartão Perfis de IA diz o que muda', async () => {
    const el = await renderSection({
      ...BASE, roles: [PLANEJADOR],
      profiles: [{ name: 'planejador-sonnet', note: 'plano no Sonnet', canary_fraction: 0.1, screenshot_max_side: null,
        rich_tree_min_elements: null,
        roles: [{ role: 'plan', provider: 'anthropic', model: 'claude-sonnet-5-5', effort: 'low', sends_data_externally: true }] }],
    });
    const cabecalhos = Array.from(el.querySelectorAll('th[scope="col"]')).map((th) => th.textContent);
    expect(cabecalhos).toEqual(expect.arrayContaining(['Esforço', 'Raciocínio', 'Perfil', 'O que muda', 'Canário']));
    expect(text(el)).toContain('adaptativo');
    expect(text(el)).toContain('Perfis de IA');
    expect(text(el)).toContain('Planejar: claude-sonnet-5-5 (anthropic, esforço baixo)');
    expect(text(el)).toContain('10 % das execuções sem perfil');
    expect(text(el)).toContain('plano no Sonnet');
  });

  it('sem perfis: a Situação diz "nenhum" e o cartão não aparece; backend anterior não ganha linha inventada', async () => {
    const semPerfis = await renderSection({ ...BASE, profiles: [] });
    expect(text(semPerfis)).toContain('nenhum (todas as execuções usam o padrão)');
    expect(text(semPerfis)).not.toContain('O que muda');
  });

  it('backend anterior ao v0.87: nem esquema, nem perfis, nem leitura visual inventados', async () => {
    const antigo = await renderSection(BASE);
    expect(text(antigo)).not.toContain('Esquema do plano');
    expect(text(antigo)).not.toContain('Perfis de IA');
    expect(text(antigo)).not.toContain('Leitura visual');
  });
});

describe('AiSection — polimento do deploy 10: os consumidores da decisão fechada em palavras', () => {
  const BLOCO = {
    provider: 'typesafe', name: 'Jev (TypeSafe System One)', consumers: { curador: 'shadow', intencao: 'shadow' },
    classes: ['C0', 'C1', 'C2', 'C3'], send_approved: true, key: 'configurada', decider: 'jev', sending: true,
    retention_days: 180,
  };

  it('lista "curador: em sombra · intenção: em sombra" e diz se o envio está ativo', async () => {
    const tela = await renderSection({ ...BASE, decisao_fechada: BLOCO });
    await waitFor(() => expect(text(tela)).toContain('Decisão fechada (Jev)'));
    expect(text(tela)).toContain('curador: em sombra · intenção: em sombra');
    expect(text(tela)).toContain('envio ativo');
    expect(text(tela)).not.toContain('intencao');
  });

  it('sem envio diz "nada sai agora"; sem o bloco (ou backend anterior) não inventa a linha', async () => {
    const tela = await renderSection({ ...BASE, decisao_fechada: { ...BLOCO, consumers: { curador: 'on' }, sending: false } });
    await waitFor(() => expect(text(tela)).toContain('curador: ligado'));
    expect(text(tela)).toContain('nada sai agora');
    await act(async () => root.unmount());
    root = createRoot(container);
    backend.calls = [];
    const sem = await renderSection({ ...BASE, decisao_fechada: null });
    expect(text(sem)).not.toContain('Decisão fechada (Jev)');
  });
});

describe('AiSection: 31.223, a política do modelo forte (só leitura)', () => {
  it('com os campos do central: mostra em palavras o que o forte decide e que muda no config.yaml; nenhum campo para editar', async () => {
    const c = await renderSection({ ...BASE, strong_model_only_on_commit: true, strong_model_for_side_effect: 'by_risk' });
    const t = text(c);
    expect(t).toContain('Modelo forte na etapa com efeito');
    expect(t).toContain('pelo risco da etapa');
    expect(t).toContain('Modelo forte só no commit');
    expect(t).toContain('ligado: o forte decide só o commit, o resto da etapa é do modelo de ação');
    expect(t).toContain('muda no config.yaml e vale na subida da farm-central');
    expect(t).not.toMatch(/strong_model|by_risk/);                                              // nenhum nome interno cru
    expect(c.querySelectorAll('input, select, textarea')).toHaveLength(0);                      // só leitura
  });

  it('desligado: a etapa inteira no forte, o modo de antes', async () => {
    const c = await renderSection({ ...BASE, strong_model_only_on_commit: false, strong_model_for_side_effect: true });
    expect(text(c)).toContain('desligado: a etapa inteira no forte');
    expect(text(c)).toContain('sempre que a etapa tem efeito');
  });

  it('central anterior (sem os campos): a tela não afirma ligado nem desligado', async () => {
    const c = await renderSection(BASE);
    expect(text(c)).not.toContain('Modelo forte');
  });
});
