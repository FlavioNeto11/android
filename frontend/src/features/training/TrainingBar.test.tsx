// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, expect, it } from 'vitest';
import { ConfirmHost } from '../../components/Confirm';
import { useAppStore } from '../../store/app';
import { initialDataState } from '../../store/reducer';
import { makeInstance } from '../../test/fixtures';
import { useToastStore } from '../../store/toasts';
import { FakeBackend, allByRole, apiError, botaoPronto, byRole, click, installBrowserStubs, json, setValue, text, waitFor } from '../../test/harness';
import type { PersonaOnDevice } from '../../api/types';
import { TrainingBar, personasDoEnsino } from './TrainingBar';
import { useTrainingStore } from './trainingStore';

// Fase L (P2.6): descartar a gravação em andamento é irreversível e passa a pedir confirmação, como toda ação
// destrutiva do painel; enquanto uma ação corre, a outra explica por que espera em vez de só apagar.

let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

const GRAVANDO = {
  id: 'trn-9', instance_id: 'android-01', profile_id: null, app_id: null, intent: 'Responder a DM', status: 'recording',
  operator: null, proposal: null, flow_id: null, created_at: '', finished_at: null, updated_at: '',
  inputs: [{ session_id: 'trn-9', seq: 1, ts: '', type: 'tap', x: 1, y: 1, x2: null, y2: null, key_name: null, text: null,
             has_text: false, text_len: null, package: 'com.pocqa.messenger', app_id: null, target: null, screen_title: null,
             screen_lines: [], sensitive: false }],
};

beforeAll(() => installBrowserStubs());
beforeEach(() => {
  backend = new FakeBackend();
  backend.install();
  backend.on('GET', /\/training$/, () => json([GRAVANDO]));
  backend.on('GET', /\/training\/trn-9$/, () => json(GRAVANDO));
  backend.on('POST', /\/training\/trn-9\/stop$/, () => json({ ...GRAVANDO, status: 'recorded' }));
  backend.on('POST', /\/training\/trn-9\/discard$/, () => json({ ...GRAVANDO, status: 'discarded' }));
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  useAppStore.setState({ ...initialDataState });
  useTrainingStore.setState({ gravando: {}, recusadas: {} });
});

it('"Descartar" a gravação pede confirmação: cancelar não chama a rota; confirmar descarta', async () => {
  await act(async () => root.render(<><TrainingBar instance={makeInstance(1, { state: 'online', control: 'user' })} leaseId="lease-1" mine /><ConfirmHost /></>));
  await waitFor(() => expect(text()).toContain('Gravando: Responder a DM'));
  // 29.146: plural certo no contador (antes "1 entrada(s)").
  expect(text()).toContain('1 entrada');
  expect(text()).not.toContain('entrada(s)');

  await click(byRole('button', /^Descartar$/));
  await waitFor(() => expect(text()).toContain('Descartar a gravação?'));
  expect(text()).toContain('1 entrada gravada');
  await click(byRole('button', /^Cancelar$/, byRole('dialog', /Descartar a gravação/)));
  await waitFor(() => expect(allByRole('dialog', /Descartar a gravação/)).toHaveLength(0));
  expect(backend.callsTo('POST', /discard$/)).toHaveLength(0);
  expect(text()).toContain('Gravando: Responder a DM');

  backend.on('GET', /\/training$/, () => json([{ ...GRAVANDO, status: 'discarded' }]));
  await click(byRole('button', /^Descartar$/));
  await waitFor(() => expect(text()).toContain('Descartar a gravação?'));
  await click(byRole('button', /^Descartar gravação$/, byRole('dialog', /Descartar a gravação/)));
  await waitFor(() => expect(backend.callsTo('POST', /\/training\/trn-9\/discard$/)).toHaveLength(1));
  await waitFor(() => expect(text()).not.toContain('Gravando: Responder a DM'));
});

// 31.80: depois de um reinício do backend a sessão `recording` fica órfã; sem o controle na mão a barra não pode
// dizer "Gravando", só avisar e deixar concluir ou descartar.
it('sessão "recording" com o controle fora das suas mãos: avisa, sem "Gravando", com Concluir e Descartar', async () => {
  await act(async () => root.render(<TrainingBar instance={makeInstance(1, { state: 'online', control: 'ai' })} leaseId={null} mine={false} />));
  await waitFor(() => expect(text()).toContain('Há uma gravação aberta neste aparelho que não está mais gravando'));
  expect(text()).not.toContain('Gravando:');
  expect(byRole('button', /^Concluir e revisar$/)).toBeTruthy();
  expect(byRole('button', /^Descartar$/)).toBeTruthy();
});

// 31.86: o aparelho que entrou em erro logo depois de gravar não pode deixar as gravações inalcançáveis.
it('modo "só revisão" (aparelho fora do ar): lista "Para revisar" e a nota, sem o formulário de iniciar', async () => {
  backend.on('GET', /\/training$/, () => json([{ ...GRAVANDO, id: 'trn-3', status: 'recorded' }]));
  await act(async () => root.render(<TrainingBar instance={makeInstance(1, { state: 'error', control: 'ai' })} leaseId={null} mine={false} somenteRevisao />));
  await waitFor(() => expect(text()).toContain('Para revisar:'));
  expect(text()).toContain('Aparelho fora do ar: dá para revisar e salvar o fluxo');
  expect(allByRole('button', /Iniciar treinamento/)).toHaveLength(0);
  expect(document.querySelector('input[placeholder^="Ex.:"]')).toBeNull();
  expect(byRole('button', /Responder a DM/)).toBeTruthy();
});

it('contador de entradas recusadas: aparece só enquanto grava e some quando a gravação termina', async () => {
  await act(async () => root.render(<TrainingBar instance={makeInstance(1, { state: 'online', control: 'user' })} leaseId="lease-1" mine />));
  await waitFor(() => expect(text()).toContain('Gravando: Responder a DM'));
  expect(text()).not.toContain('recusada');
  await act(async () => { useTrainingStore.getState().registrarRecusa('android-01'); useTrainingStore.getState().registrarRecusa('android-01'); });
  expect(text()).toContain('2 entradas recusadas: refaça');
  backend.on('GET', /\/training$/, () => json([{ ...GRAVANDO, status: 'discarded' }]));
  await act(async () => root.render(<TrainingBar instance={makeInstance(1, { state: 'online', control: 'user' })} leaseId="lease-1" mine={false} />));
  await waitFor(() => expect(useTrainingStore.getState().recusadas['android-01']).toBeUndefined());
  expect(text()).not.toContain('recusada');
});

// 31.80 + A1: a gravação órfã (ninguém com o controle) se distingue da viva de quem tem o controle em outra aba.
it('órfã: com o controle em "none" também avisa e oferece Concluir e Descartar', async () => {
  await act(async () => root.render(<TrainingBar instance={makeInstance(1, { state: 'online', control: 'none' })} leaseId={null} mine={false} />));
  await waitFor(() => expect(text()).toContain('não está mais gravando'));
  expect(allByRole('button', /^Concluir e revisar$/)).toHaveLength(1);
});

it('gravação viva de quem tem o controle em outra aba: aviso certo, sem Concluir nem Descartar', async () => {
  await act(async () => root.render(<TrainingBar instance={makeInstance(1, { state: 'online', control: 'user' })} leaseId={null} mine={false} />));
  await waitFor(() => expect(text()).toContain('Há uma gravação em andamento neste aparelho por quem está com o controle.'));
  // B1 (#428): a quem recarregou a página, o aviso diz a saída.
  expect(text()).toMatch(/Se a gravação é sua.*clique em Retomar controle/s);
  expect(text()).not.toContain('não está mais gravando');
  expect(text()).not.toContain('Gravando:');
  expect(allByRole('button', /^Concluir e revisar$/)).toHaveLength(0);
  expect(allByRole('button', /^Descartar$/)).toHaveLength(0);
});

it('Concluir e Descartar mandam o lease_id que a aba tem; sem lease, o corpo não vai', async () => {
  await act(async () => root.render(<><TrainingBar instance={makeInstance(1, { state: 'online', control: 'user' })} leaseId="lease-1" mine /><ConfirmHost /></>));
  await waitFor(() => expect(text()).toContain('Gravando: Responder a DM'));
  await click(byRole('button', /^Concluir e revisar$/));
  await waitFor(() => expect(backend.callsTo('POST', /\/training\/trn-9\/stop$/)).toHaveLength(1));
  expect(backend.callsTo('POST', /\/stop$/)[0]!.body).toEqual({ lease_id: 'lease-1' });

  await act(async () => root.unmount());
  root = createRoot(container);
  await act(async () => root.render(<><TrainingBar instance={makeInstance(1, { state: 'online', control: 'ai' })} leaseId={null} mine={false} /><ConfirmHost /></>));
  await waitFor(() => expect(text()).toContain('não está mais gravando'));
  await click(byRole('button', /^Descartar$/));
  await click(byRole('button', /^Descartar gravação$/, byRole('dialog', /Descartar a gravação/)));
  await waitFor(() => expect(backend.callsTo('POST', /\/training\/trn-9\/discard$/)).toHaveLength(1));
  expect(backend.callsTo('POST', /\/discard$/)[0]!.body).toBeUndefined();
});

it('A6: a região viva do contador nasce vazia com a gravação e o texto entra depois, no mesmo nó', async () => {
  await act(async () => root.render(<TrainingBar instance={makeInstance(1, { state: 'online', control: 'user' })} leaseId="lease-1" mine />));
  await waitFor(() => expect(text()).toContain('Gravando: Responder a DM'));
  const viva = document.querySelector('section[aria-label="Modo treinamento"] [aria-live="polite"]') as HTMLElement;
  expect(viva).toBeTruthy();
  expect(viva.textContent).toBe('');
  await act(async () => { useTrainingStore.getState().registrarRecusa('android-01'); });
  expect(viva.isConnected).toBe(true);
  expect(viva.textContent).toBe('1 entrada recusada: refaça');
});

it('29.142: "Para revisar" corta o nome com reticências, leva o nome inteiro no rótulo e diz o estado de cada gravação', async () => {
  const longo = 'Atualizar o cadastro do perfil no QA Messenger com o nome e a cidade';
  backend.on('GET', /\/training$/, () => json([
    { ...GRAVANDO, id: 'trn-a', intent: longo, status: 'recorded' },
    { ...GRAVANDO, id: 'trn-b', intent: longo, status: 'proposed' },
  ]));
  await act(async () => root.render(<TrainingBar instance={makeInstance(1, { state: 'online', control: 'none' })} leaseId={null} mine={false} />));
  await waitFor(() => expect(text()).toContain('Para revisar:'));
  const gravada = byRole('button', /, só gravada$/);
  const pronta = byRole('button', /, proposta pronta$/);
  expect(gravada.getAttribute('aria-label')).toBe(`Revisar “${longo}”, só gravada`);
  expect(gravada.textContent).toBe('Atualizar o cadastro do perfil no QA… · só gravada');
  expect(pronta.textContent).toBe('Atualizar o cadastro do perfil no QA… · proposta pronta');
});

// 31.90-B (v1.58): a sessão salva sai de "Para revisar", mas a etapa sem receita ainda pode ganhá-la daqui.
it('sessão salva aparece em "Salvas" com "Refazer receitas", que só chama /recipes no clique', async () => {
  const SALVA = { ...GRAVANDO, id: 'trn-7', intent: 'Abrir o perfil', status: 'saved', flow_id: 'abrir-perfil' };
  backend.on('GET', /\/training$/, () => json([SALVA]));
  backend.on('POST', /\/training\/trn-7\/recipes$/, () => json({
    session: SALVA, flow_id: 'abrir-perfil', created: 0,
    steps: [{ key: 'abrir', title: 'Abrir', recipe: false, reason: 'já havia receita ativa para esta etapa' }],
  }));
  await act(async () => root.render(<TrainingBar instance={makeInstance(1, { state: 'online', control: 'none' })} leaseId={null} mine={false} />));
  await waitFor(() => expect(text()).toContain('Salvas (1)'));
  expect(text()).not.toContain('Para revisar');
  // A lista fica num Disclosure recolhido (<details>), que mantém o conteúdo no DOM.
  expect(byRole('button', /^Refazer receitas de “Abrir o perfil”$/).closest('details')).not.toBeNull();
  await click(byRole('button', /^Refazer receitas de “Abrir o perfil”$/));
  await waitFor(() => expect(text()).toContain('Nenhuma receita nova.'));
  expect(text()).toContain('Abrir (já havia receita ativa para esta etapa)');
  expect(backend.callsTo('POST', /\/training\/trn-7\/recipes$/)).toHaveLength(1);
});

// 31.92 (v1.64): o controle mudou de mãos e o lease desta aba ficou velho; o backend recusa com 409 control_required
// e não muda nada. A barra diz que a gravação continua (com a mensagem do backend) e se relê.
it('Concluir recusado por control_required: a gravação continua na barra, com o motivo, e a lista se relê', async () => {
  useToastStore.setState({ toasts: [] });
  backend.on('POST', /\/training\/trn-9\/stop$/, () => apiError(409, 'control_required', 'Só quem está com o controle do aparelho encerra esta gravação.'));
  await act(async () => root.render(<><TrainingBar instance={makeInstance(1, { state: 'online', control: 'user' })} leaseId="lease-velho" mine /><ConfirmHost /></>));
  await waitFor(() => expect(text()).toContain('Gravando: Responder a DM'));
  const leituras = backend.callsTo('GET', /\/training$/).length;
  await click(byRole('button', /^Concluir e revisar$/));
  await waitFor(() => expect(useToastStore.getState().toasts.some((t) => t.title === 'A gravação continua')).toBe(true));
  const aviso = useToastStore.getState().toasts.find((t) => t.title === 'A gravação continua');
  expect(aviso?.message).toContain('Só quem está com o controle do aparelho encerra esta gravação.');
  expect(useToastStore.getState().toasts.some((t) => t.title === 'Não foi possível encerrar o treinamento')).toBe(false);
  await waitFor(() => expect(backend.callsTo('GET', /\/training$/).length).toBeGreaterThan(leituras));
  expect(text()).toContain('Gravando: Responder a DM');
  expect(allByRole('dialog', /Treinamento:/)).toHaveLength(0);          // não abre a revisão
});

it('Descartar recusado por control_required: a gravação continua, sem perder nada, e a lista se relê', async () => {
  useToastStore.setState({ toasts: [] });
  backend.on('POST', /\/training\/trn-9\/discard$/, () => apiError(409, 'control_required', 'Só quem está com o controle do aparelho descarta esta gravação.'));
  await act(async () => root.render(<><TrainingBar instance={makeInstance(1, { state: 'online', control: 'user' })} leaseId="lease-velho" mine /><ConfirmHost /></>));
  await waitFor(() => expect(text()).toContain('Gravando: Responder a DM'));
  const leituras = backend.callsTo('GET', /\/training$/).length;
  await click(byRole('button', /^Descartar$/));
  await click(byRole('button', /^Descartar gravação$/, byRole('dialog', /Descartar a gravação/)));
  await waitFor(() => expect(useToastStore.getState().toasts.find((t) => t.title === 'A gravação continua')?.message)
    .toContain('Só quem está com o controle do aparelho descarta esta gravação.'));
  await waitFor(() => expect(backend.callsTo('GET', /\/training$/).length).toBeGreaterThan(leituras));
  expect(backend.callsTo('POST', /\/discard$/)[0]!.body).toEqual({ lease_id: 'lease-velho' });
  expect(text()).toContain('Gravando: Responder a DM');
});

it('com mais de 5 sessões salvas, a lista diz que mostra só as 5 mais novas', async () => {
  const salva = (n: number) => ({ ...GRAVANDO, id: `trn-s${n}`, intent: `Ensino ${n}`, status: 'saved', flow_id: `fluxo-${n}` });
  backend.on('GET', /\/training$/, () => json([1, 2, 3, 4, 5, 6].map(salva)));
  await act(async () => root.render(<TrainingBar instance={makeInstance(1, { state: 'online', control: 'none' })} leaseId={null} mine={false} />));
  await waitFor(() => expect(text()).toContain('Salvas (as 5 mais novas de 6)'));
  expect(allByRole('button', /^Refazer receitas de/)).toHaveLength(5);
  expect(text()).not.toContain('Ensino 6');
});

// ---------------------------------------------------------------- 31.90-C: de quem é o ensino
const persona = (profile_id: string, name: string, app_id: string | null = null): PersonaOnDevice => ({
  profile_id, username: null, display_name: name, name, status: 'active', app_id, is_primary: false, bound_at: null, session: null,
});

/** O aparelho sem gravação viva, com as personas dadas, e a rota de iniciar que guarda o corpo. */
async function abrirParaIniciar(personas: PersonaOnDevice[] | 'falha') {
  const corpos: unknown[] = [];
  backend.on('GET', /\/training$/, () => json([]));
  backend.on('GET', /\/instances\/android-01\/personas$/, () => (personas === 'falha' ? json({ detail: 'x' }, 500) : json(personas)));
  backend.on('POST', /\/instances\/android-01\/training$/, (c) => { corpos.push(c.body); return json(GRAVANDO); });
  await act(async () => root.render(<TrainingBar instance={makeInstance(1, { state: 'online', control: 'user' })} leaseId="lease-1" mine />));
  await setValue(await waitFor(() => byRole('textbox', /O que você vai ensinar/)) as HTMLInputElement, 'Responder a DM');
  return corpos;
}

it('31.90-C: com duas personas o painel pede de quem é o ensino, trava o início sem escolha e manda profile_id', async () => {
  const corpos = await abrirParaIniciar([persona('p-a', 'Ana Exemplo'), persona('p-b', 'Beto Exemplo')]);
  const escolha = await waitFor(() => byRole('combobox', /De quem é o ensino/)) as HTMLSelectElement;
  expect([...escolha.options].map((o) => o.value)).toEqual(['', 'p-a', 'p-b']);
  expect(byRole('button', /Iniciar treinamento/).getAttribute('aria-disabled')).toBe('true');
  await click(byRole('button', /Iniciar treinamento/));
  expect(corpos).toHaveLength(0);                                       // sem escolha, nada vai ao backend (que recusaria com 409)

  await setValue(escolha, 'p-b');
  expect(byRole('button', /Iniciar treinamento/).getAttribute('aria-disabled')).not.toBe('true');
  await click(byRole('button', /Iniciar treinamento/));
  await waitFor(() => expect(corpos).toHaveLength(1));
  expect(corpos[0]).toEqual({ intent: 'Responder a DM', lease_id: 'lease-1', app_id: null, profile_id: 'p-b' });
});

it('31.90-C: com uma só persona não há seletor, o texto diz de quem é e o corpo não leva profile_id', async () => {
  const corpos = await abrirParaIniciar([persona('p-a', 'Ana Exemplo')]);
  await waitFor(() => expect(text()).toContain('O ensino fica com Ana Exemplo'));
  expect(allByRole('combobox', /De quem é o ensino/)).toHaveLength(0);
  await click(byRole('button', /Iniciar treinamento/));
  await waitFor(() => expect(corpos).toHaveLength(1));
  expect(corpos[0]).toEqual({ intent: 'Responder a DM', lease_id: 'lease-1', app_id: null });
});

it('31.90-C: sem persona vinculada o painel avisa que o fluxo não vale em aparelho nenhum, e o início segue', async () => {
  const corpos = await abrirParaIniciar([]);
  await waitFor(() => expect(text()).toContain('Nenhuma persona está vinculada a este aparelho'));
  expect(text()).toContain('não vale em aparelho nenhum');
  await click(byRole('button', /Iniciar treinamento/));
  await waitFor(() => expect(corpos).toHaveLength(1));
});

it('31.90-C: a leitura das personas que falha não trava o início: sem seletor e sem aviso, o backend decide', async () => {
  const corpos = await abrirParaIniciar('falha');
  await waitFor(() => expect(byRole('button', /Iniciar treinamento/).getAttribute('aria-disabled')).not.toBe('true'));
  await click(byRole('button', /Iniciar treinamento/));
  await waitFor(() => expect(corpos).toHaveLength(1));
  expect(allByRole('combobox', /De quem é o ensino/)).toHaveLength(0);
  expect(text()).not.toContain('Nenhuma persona está vinculada');
});

it('31.90-C: enquanto a leitura das personas corre o Iniciar fica travado com o motivo; ao chegar, o seletor aparece', async () => {
  let soltar: (r: Response) => void = () => {};
  let pedida = false;                                  // com o fetch atrasado, o pedido só chega ao handler depois
  const corpos: unknown[] = [];
  backend.on('GET', /\/training$/, () => json([]));
  backend.on('GET', /\/instances\/android-01\/personas$/, () => new Promise<Response>((ok) => { soltar = ok; pedida = true; }));
  backend.on('POST', /\/instances\/android-01\/training$/, (c) => { corpos.push(c.body); return json(GRAVANDO); });
  await act(async () => root.render(<TrainingBar instance={makeInstance(1, { state: 'online', control: 'user' })} leaseId="lease-1" mine />));
  await setValue(await waitFor(() => byRole('textbox', /O que você vai ensinar/)) as HTMLInputElement, 'Responder a DM');
  const iniciar = () => byRole('button', /Iniciar treinamento/);
  expect(iniciar().getAttribute('aria-disabled')).toBe('true');
  expect(iniciar().textContent).toContain('Lendo as personas deste aparelho.');
  await click(iniciar());
  expect(corpos).toHaveLength(0);                                       // sem a lista não se manda o início sem dono

  await waitFor(() => expect(pedida).toBe(true));
  await act(async () => soltar(json([persona('p-a', 'Ana Exemplo'), persona('p-b', 'Beto Exemplo')])));
  await waitFor(() => expect(byRole('combobox', /De quem é o ensino/)).toBeTruthy());
  expect(iniciar().textContent).toContain('Escolha de qual persona é o ensino.');   // o motivo muda quando a lista chega
  expect(corpos).toHaveLength(0);
});

it('31.90-C: personasDoEnsino conta uma por pessoa e respeita o app escolhido, como o backend', () => {
  const lista = [persona('p-a', 'A', 'com.x'), persona('p-a', 'A', 'com.y'), persona('p-b', 'B', 'com.y'), persona('p-c', 'C', null)];
  expect(personasDoEnsino(lista, '').map((p) => p.profile_id)).toEqual(['p-a', 'p-b', 'p-c']);   // sem app: todas, uma vez cada
  expect(personasDoEnsino(lista, 'com.y').map((p) => p.profile_id)).toEqual(['p-a', 'p-b', 'p-c']);
  expect(personasDoEnsino(lista, 'com.x').map((p) => p.profile_id)).toEqual(['p-a', 'p-c']);      // o vínculo sem app serve a qualquer um
  expect(personasDoEnsino(lista, 'com.z').map((p) => p.profile_id)).toEqual(['p-c']);
});

// ---------------------------------------------------------------- 31.111 F5 (adendo v1.75)
const ORIGEM = { run_id: 'run-0001', step_id: 'run-0001:android-01:v1:open_app', step_key: 'open_app', attempt_id: null, motivo: 'A tela esperada não apareceu.' };

it('31.111: a gravação aberta a partir de uma falha mostra a origem na barra; a comum, não', async () => {
  const el = makeInstance(1, { state: 'online', control: 'user' });
  await act(async () => root.render(<TrainingBar instance={el} leaseId="lease-1" mine />));
  await waitFor(() => expect(text()).toContain('Gravando: Responder a DM'));
  expect(document.querySelector('section[aria-label="Origem do treino"]')).toBeNull();
  expect(text()).not.toContain('corrige uma falha');
  await act(async () => root.unmount());
  root = createRoot(container);

  const comOrigem = { ...GRAVANDO, origin: { ...ORIGEM, context: { disponivel: false } } };
  backend.on('GET', /\/training$/, () => json([comOrigem]));
  backend.on('GET', /\/training\/trn-9$/, () => json(comOrigem));
  await act(async () => root.render(<TrainingBar instance={el} leaseId="lease-1" mine />));
  await waitFor(() => expect(text()).toContain('Gravando: Responder a DM'));
  await waitFor(() => expect(document.querySelector('section[aria-label="Origem do treino"]')).not.toBeNull());
  const origem = document.querySelector<HTMLElement>('section[aria-label="Origem do treino"]')!;
  expect(origem.textContent).toContain('corrige uma falha');
  expect(origem.textContent).toContain('Motivo: A tela esperada não apareceu.');
});

it('31.111: "Para revisar" leva o selo na sessão que nasceu de uma falha e só nela', async () => {
  backend.on('GET', /\/training$/, () => json([
    { ...GRAVANDO, id: 'trn-a', intent: 'Corrigir a etapa', status: 'recorded', origin: ORIGEM },
    { ...GRAVANDO, id: 'trn-b', intent: 'Outro ensino', status: 'recorded' },
  ]));
  await act(async () => root.render(<TrainingBar instance={makeInstance(1, { state: 'online', control: 'none' })} leaseId={null} mine={false} />));
  await waitFor(() => expect(text()).toContain('Para revisar:'));
  const botoes = allByRole('button', /Revisar “/);
  expect(botoes).toHaveLength(2);
  const marcados = botoes.filter((b) => (b.getAttribute('aria-label') ?? '').endsWith(', corrige uma falha') && b.textContent!.includes('corrige uma falha'));
  expect(marcados).toHaveLength(1);
  expect(marcados[0]!.textContent).toContain('Corrigir a etapa');
});

// 31.90-D (adendo v1.70): "Desfazer a última" tira só a última entrada da gravação viva. Manda o lease_id e o seq que a
// tela mostra como último; o número desfeito é reaproveitado pela próxima entrada, então nada se guarda por seq.
const entrada = (seq: number) => ({ ...GRAVANDO.inputs[0]!, seq });
const COM_DUAS = { ...GRAVANDO, inputs: [entrada(1), entrada(2)] };

it('31.90-D: "Desfazer a última" manda lease_id e o seq da última entrada, mostra a sessão sem ela e avisa', async () => {
  useToastStore.setState({ toasts: [] });
  backend.on('GET', /\/training$/, () => json([COM_DUAS]));
  backend.on('GET', /\/training\/trn-9$/, () => json(COM_DUAS));
  backend.on('POST', /\/training\/trn-9\/undo$/, () => json({ ...GRAVANDO, inputs: [entrada(1)], undone: { seq: 2, type: 'tap' } }));
  await act(async () => root.render(<TrainingBar instance={makeInstance(1, { state: 'online', control: 'user' })} leaseId="lease-1" mine />));
  await waitFor(() => expect(text()).toContain('2 entradas'));
  expect(text()).toContain('#2');
  await click(await botaoPronto(/^Desfazer a última$/));
  await waitFor(() => expect(backend.callsTo('POST', /\/training\/trn-9\/undo$/)).toHaveLength(1));
  expect(backend.callsTo('POST', /\/undo$/)[0]!.body).toEqual({ lease_id: 'lease-1', seq: 2 });
  await waitFor(() => expect(text()).toContain('1 entrada'));
  expect(text()).not.toContain('#2');
  expect(useToastStore.getState().toasts.some((t) => t.title === 'Entrada desfeita' && (t.message ?? '').includes('#2'))).toBe(true);
  // O que a tela mostra agora como última é a #1: o próximo pedido leva seq 1, não o 2 de antes.
  await click(await botaoPronto(/^Desfazer a última$/));
  await waitFor(() => expect(backend.callsTo('POST', /\/undo$/)).toHaveLength(2));
  expect(backend.callsTo('POST', /\/undo$/)[1]!.body).toEqual({ lease_id: 'lease-1', seq: 1 });
});

it('31.90-D: entrada_mudou (outra chegou antes): mostra a mensagem, relê a gravação e a última passa a ser a nova', async () => {
  useToastStore.setState({ toasts: [] });
  const tres = { ...GRAVANDO, inputs: [entrada(1), entrada(2), entrada(3)] };
  backend.on('GET', /\/training$/, () => json([COM_DUAS]));
  backend.on('GET', /\/training\/trn-9$/, () => json(COM_DUAS));
  backend.on('POST', /\/undo$/, () => apiError(409, 'entrada_mudou', 'A última entrada agora é a 3, não a 2; confira antes de desfazer.'));
  await act(async () => root.render(<TrainingBar instance={makeInstance(1, { state: 'online', control: 'user' })} leaseId="lease-1" mine />));
  await waitFor(() => expect(text()).toContain('2 entradas'));
  backend.on('GET', /\/training$/, () => json([tres]));
  backend.on('GET', /\/training\/trn-9$/, () => json(tres));
  await click(await botaoPronto(/^Desfazer a última$/));
  await waitFor(() => expect(useToastStore.getState().toasts.some((t) => t.title === 'A gravação continua como estava')).toBe(true));
  expect(useToastStore.getState().toasts.find((t) => t.title === 'A gravação continua como estava')?.message)
    .toContain('A última entrada agora é a 3, não a 2');
  await waitFor(() => expect(text()).toContain('3 entradas'));
  expect(text()).toContain('Gravando: Responder a DM');
  // Nada foi apagado e o próximo pedido leva o seq que a tela mostra agora.
  backend.on('POST', /\/undo$/, () => json({ ...GRAVANDO, inputs: [entrada(1), entrada(2)], undone: { seq: 3, type: 'tap' } }));
  await click(await botaoPronto(/^Desfazer a última$/));
  await waitFor(() => expect(backend.callsTo('POST', /\/undo$/)).toHaveLength(2));
  expect(backend.callsTo('POST', /\/undo$/)[1]!.body).toEqual({ lease_id: 'lease-1', seq: 3 });
});

it('31.90-D: control_required: mostra o motivo, mantém a barra gravando e relê a lista', async () => {
  useToastStore.setState({ toasts: [] });
  backend.on('POST', /\/undo$/, () => apiError(409, 'control_required', 'Só quem está com o controle do aparelho desfaz a última entrada.'));
  await act(async () => root.render(<TrainingBar instance={makeInstance(1, { state: 'online', control: 'user' })} leaseId="lease-velho" mine />));
  await waitFor(() => expect(text()).toContain('Gravando: Responder a DM'));
  const leituras = backend.callsTo('GET', /\/training$/).length;
  await click(await botaoPronto(/^Desfazer a última$/));
  await waitFor(() => expect(useToastStore.getState().toasts.some((t) => t.title === 'A gravação continua como estava')).toBe(true));
  expect(useToastStore.getState().toasts.find((t) => t.title === 'A gravação continua como estava')?.message)
    .toContain('Só quem está com o controle do aparelho desfaz a última entrada.');
  await waitFor(() => expect(backend.callsTo('GET', /\/training$/).length).toBeGreaterThan(leituras));
  expect(text()).toContain('Gravando: Responder a DM');
  expect(text()).toContain('1 entrada');
});

it('31.90-D: quem só olha (outra aba) e a gravação órfã não têm o botão de desfazer', async () => {
  await act(async () => root.render(<TrainingBar instance={makeInstance(1, { state: 'online', control: 'user' })} leaseId={null} mine={false} />));
  await waitFor(() => expect(text()).toContain('Há uma gravação em andamento neste aparelho por quem está com o controle'));
  expect(allByRole('button', /Desfazer a última/)).toHaveLength(0);
  await act(async () => root.unmount());
  root = createRoot(container);
  await act(async () => root.render(<TrainingBar instance={makeInstance(1, { state: 'online', control: 'none' })} leaseId={null} mine={false} />));
  await waitFor(() => expect(text()).toContain('não está mais gravando'));
  expect(allByRole('button', /Desfazer a última/)).toHaveLength(0);
});

it('31.90-D: sem entrada ou sem lease o botão explica por que não dá e não chama a rota', async () => {
  const vazia = { ...GRAVANDO, inputs: [] };
  backend.on('GET', /\/training$/, () => json([vazia]));
  backend.on('GET', /\/training\/trn-9$/, () => json(vazia));
  await act(async () => root.render(<TrainingBar instance={makeInstance(1, { state: 'online', control: 'user' })} leaseId="lease-1" mine />));
  await waitFor(() => expect(text()).toContain('Gravando: Responder a DM'));
  const vazio = byRole('button', /Desfazer a última/);
  expect(vazio.getAttribute('aria-disabled')).toBe('true');
  expect(vazio.textContent).toContain('Ainda não há entrada para desfazer.');
  await click(vazio);
  await act(async () => root.unmount());
  root = createRoot(container);
  await act(async () => root.render(<TrainingBar instance={makeInstance(1, { state: 'online', control: 'user' })} leaseId={null} mine />));
  await waitFor(() => expect(text()).toContain('Gravando: Responder a DM'));
  const semLease = byRole('button', /Desfazer a última/);
  expect(semLease.getAttribute('aria-disabled')).toBe('true');
  expect(semLease.textContent).toContain('Retome o controle para desfazer.');
  await click(semLease);
  expect(backend.callsTo('POST', /\/undo$/)).toHaveLength(0);
});

it('31.90-D: enquanto o desfazer corre, Concluir e Descartar explicam por que esperam', async () => {
  let soltar: (r: Response) => void = () => {};
  // Com atraso no fetch o pedido é registrado antes de o handler correr: só se solta a resposta depois de ela ser pedida.
  let pedida = false;
  backend.on('POST', /\/undo$/, () => new Promise<Response>((resolve) => { pedida = true; soltar = resolve; }));
  await act(async () => root.render(<TrainingBar instance={makeInstance(1, { state: 'online', control: 'user' })} leaseId="lease-1" mine />));
  await waitFor(() => expect(text()).toContain('Gravando: Responder a DM'));
  await click(await botaoPronto(/^Desfazer a última$/));
  await waitFor(() => expect(backend.callsTo('POST', /\/undo$/)).toHaveLength(1));
  await waitFor(() => expect(pedida).toBe(true));
  await waitFor(() => expect(byRole('button', /^Concluir e revisar/).textContent).toContain('Desfazendo a última entrada…'));
  expect(byRole('button', /^Descartar/).textContent).toContain('Desfazendo a última entrada…');
  await act(async () => soltar(json({ ...GRAVANDO, inputs: [], undone: { seq: 1, type: 'tap' } })));
  await waitFor(() => expect(text()).not.toContain('Desfazendo a última entrada…'));
});
