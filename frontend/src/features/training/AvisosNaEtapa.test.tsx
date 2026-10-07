// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { ConfirmHost } from '../../components/Confirm';
import { useAppStore } from '../../store/app';
import { initialDataState } from '../../store/reducer';
import { FakeBackend, botaoPronto, click, installBrowserStubs, json, text, waitFor } from '../../test/harness';
import { avisosPorEtapa } from './avisosDaEtapa';
import { TrainingReview } from './TrainingReview';

/**
 * 31.198: os avisos da prévia e do salvar que falam de uma etapa (31.182: a etapa que mira a conta da própria persona) aparecem junto
 * dela, sem bloquear; o que não aponta para etapa nenhuma fica na lista geral. Prova `simulated`: servidor falso no formato do 31.182.
 */

const CONTA = 'A etapa “abrir_perfil” mira a conta da própria persona ({conta_instagram_usuario}): a receita dela abre a conta de quem roda, em cada aparelho. Para um alvo da operação, ensine com um perfil que não seja o da persona.';
const GERAL = 'O comando se parece com o do fluxo antigo.';

describe('avisosPorEtapa', () => {
  const etapas = [{ key: 'abrir_perfil' }, { key: 'enviar' }];
  it('o aviso com a chave da etapa vai para ela; com a posição, também; o resto fica solto', () => {
    const r = avisosPorEtapa([CONTA, 'Confira a etapa 2: falta a conferência.', GERAL], etapas);
    expect(r.porEtapa.get('abrir_perfil')).toEqual([CONTA]);
    expect(r.porEtapa.get('enviar')).toEqual(['Confira a etapa 2: falta a conferência.']);
    expect(r.soltos).toEqual([GERAL]);
  });
  it('etapa que não existe na proposta (chave ou posição) não some: fica solto', () => {
    const r = avisosPorEtapa(['A etapa “inexistente” mira a conta da própria persona.', 'Confira a etapa 9.'], etapas);
    expect(r.porEtapa.size).toBe(0);
    expect(r.soltos).toHaveLength(2);
  });
  it('a etapa citada com aspas retas casa; uma palavra solta "etapa" sem número não', () => {
    expect(avisosPorEtapa(['A etapa "enviar" não confere o efeito.'], etapas).porEtapa.get('enviar')).toHaveLength(1);
    expect(avisosPorEtapa(['Há uma etapa sem conferência.'], etapas).soltos).toHaveLength(1);
  });
});

let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

const entrada = (seq: number) => ({
  session_id: 'trn-1', seq, ts: '', type: 'tap', x: 10, y: 10, x2: null, y2: null, key_name: null, text: null, has_text: false, text_len: null,
  package: 'com.pocqa.messenger', app_id: null, target: null, screen_title: null, screen_lines: [], sensitive: false,
});
const etapa = (chave: string, titulo: string, inputs: number[]) => ({
  key: chave, title: titulo, goal: titulo, inputs, side_effect: false, capability: null, bindings: [], app_id: null,
  postcondition: { kind: 'text_visible', value: 'Pronto', description: 'pronto' },
});
const PROPOSTA = {
  summary: 'Abrir e enviar', command_template: 'abra o perfil e envie', app_id: 'qa-messenger', parameters: [], discarded: [], questions: [],
  steps: [etapa('abrir_perfil', 'Abrir o perfil', [1]), etapa('enviar', 'Enviar a mensagem', [2])],
};
const SESSAO = {
  id: 'trn-1', instance_id: 'android-01', profile_id: 'ig-1', app_id: 'qa-messenger', intent: 'Enviar', status: 'proposed', operator: null,
  proposal: PROPOSTA, flow_id: null, created_at: '', finished_at: '', updated_at: '', inputs: [entrada(1), entrada(2)],
};

beforeAll(() => installBrowserStubs());
beforeEach(() => {
  backend = new FakeBackend();
  backend.install();
  backend.on('GET', /\/training\/trn-1$/, () => json(SESSAO));
  backend.on('GET', /\/instagram\/profiles$/, () => json([{ id: 'ig-1', username: 'aluno.um' }]));
  backend.on('GET', /policy-groups$/, () => json([]));
  backend.on('GET', /app-catalog/, () => json([]));
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  useAppStore.setState({ ...initialDataState });
});

const abrir = async () => {
  await act(async () => root.render(<><TrainingReview sessionId="trn-1" onClose={() => {}} /><ConfirmHost /></>));
  await waitFor(() => expect(text()).toContain('Vale para'));
};
const doAviso = (chave: string) => [...document.querySelectorAll<HTMLElement>(`[data-aviso-da-etapa="${chave}"]`)];

describe('a tela do ensino', () => {
  it('o aviso da prévia que cita a etapa aparece junto dela; o geral fica na lista de avisos da prévia; nada bloqueia', async () => {
    backend.on('POST', /\/training\/trn-1\/preview$/, () => json({
      steps: [{ key: 'abrir_perfil', title: 'Abrir o perfil', recipe: true, reason: 'receita será gravada ao salvar' }], warnings: [CONTA, GERAL],
    }));
    await abrir();
    await waitFor(() => expect(doAviso('abrir_perfil')).toHaveLength(1));
    expect(text(doAviso('abrir_perfil')[0]!)).toContain('mira a conta da própria persona ({conta_instagram_usuario})');
    expect(doAviso('enviar')).toHaveLength(0);
    // o aviso fica dentro da etapa dele (o item da lista de etapas), não na lista geral
    expect(doAviso('abrir_perfil')[0]!.closest('li')!.textContent).toContain('Confere');
    const geral = document.querySelector('ul[aria-label="Avisos da prévia"]')!;
    expect(text(geral)).toContain(GERAL);
    expect(text(geral)).not.toContain('mira a conta da própria persona');
    // só aviso: o Salvar continua liberado
    expect((await botaoPronto(/^Salvar como fluxo/)).getAttribute('aria-disabled')).not.toBe('true');
  });

  it('o aviso do salvar vai junto da etapa no relatório do salvar, e o que não aponta para etapa fica em "Avisos do salvar"', async () => {
    backend.on('POST', /\/training\/trn-1\/preview$/, () => json({ steps: [], warnings: [] }));
    backend.on('POST', /\/training\/trn-1\/save$/, () => json({
      session: { ...SESSAO, status: 'saved' }, flow_id: 'abrir-e-enviar', warnings: [CONTA, GERAL],
      steps: [{ key: 'abrir_perfil', title: 'Abrir o perfil', recipe: true, reason: 'receita gravada' }, { key: 'enviar', title: 'Enviar a mensagem', recipe: false, reason: 'sem receita' }],
    }));
    await abrir();
    await click(await botaoPronto(/^Salvar como fluxo/));
    await waitFor(() => expect(text()).toContain('Fluxo abrir-e-enviar salvo'));
    expect(doAviso('abrir_perfil')).toHaveLength(1);
    expect(doAviso('abrir_perfil')[0]!.closest('li')!.textContent).toContain('Abrir o perfil — receita gravada');
    expect(doAviso('enviar')).toHaveLength(0);
    const geral = document.querySelector('ul[aria-label="Avisos do salvar"]')!;
    expect(text(geral)).toContain(GERAL);
    expect(text(geral)).not.toContain('mira a conta da própria persona');
  });

  it('sem aviso nenhum, nada aparece', async () => {
    backend.on('POST', /\/training\/trn-1\/preview$/, () => json({ steps: [], warnings: [] }));
    await abrir();
    expect(doAviso('abrir_perfil')).toHaveLength(0);
    expect(document.querySelector('ul[aria-label="Avisos da prévia"]')).toBeNull();
  });
});
