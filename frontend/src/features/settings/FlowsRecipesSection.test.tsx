// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, expect, it } from 'vitest';
import { ConfirmHost } from '../../components/Confirm';
import { useAppStore } from '../../store/app';
import { initialDataState } from '../../store/reducer';
import { APPS, RECIPES, makeSnapshot } from '../../test/fixtures';
import { FakeBackend, allByRole, byRole, click, installBrowserStubs, json, setValue, text, waitFor } from '../../test/harness';
import { FlowsRecipesSection } from './FlowsRecipesSection';

// Fase F: a lista de habilidades versionadas só aparece com `health.features.skills`. Desligado (o padrão), a seção
// é a de sempre — nem a lista nem a chamada a `/api/skills`.

let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

function comHabilidades(ligado: boolean | undefined): void {
  const health = makeSnapshot().health;
  useAppStore.setState({ ...initialDataState, health: { ...health, features: { ...health.features, skills: ligado } } });
}

beforeAll(() => installBrowserStubs());
beforeEach(() => {
  backend = new FakeBackend();
  backend.install();
  backend.on('GET', /\/flows$/, () => json([]));
  backend.on('GET', /\/flows\/cobertura$/, () => json([]));
  backend.on('GET', /\/recipes$/, () => json([]));
  backend.on('GET', /\/skills$/, () => json([
    { ref: 'qa-messenger.mandar_mensagem@1', skill_id: 'qa-messenger.mandar_mensagem', version: 1,
      name: 'Mandar mensagem no QA', app_id: 'qa-messenger', state: 'draft',
      command_template: 'Mandar mensagem — contato: {contato}', schema_version: 1, content_hash: 'h', intact: true,
      state_at: '2026-09-27T12:00:00.000Z' },
  ]));
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  useAppStore.setState({ ...initialDataState });
  window.localStorage.clear();            // o aberto/fechado das seções não vaza entre testes
});

it('desligado (padrão): fluxos e receitas como sempre, sem habilidades nem chamada a /api/skills', async () => {
  comHabilidades(undefined);
  await act(async () => root.render(<FlowsRecipesSection />));
  await waitFor(() => expect(text()).toContain('Nenhum fluxo salvo ainda'));
  expect(text()).not.toContain('Habilidades');
  expect(backend.callsTo('GET', /\/skills$/)).toHaveLength(0);
});

it('ligado: lista as habilidades com o estado de cada versão', async () => {
  comHabilidades(true);
  await act(async () => root.render(<FlowsRecipesSection />));
  await waitFor(() => expect(text()).toContain('Mandar mensagem no QA'));
  expect(text()).toContain('Habilidades (1)');
  expect(text()).toContain('rascunho');
  expect(text()).toContain('qa-messenger.mandar_mensagem@1');
  expect(backend.callsTo('GET', /\/skills$/)).toHaveLength(1);
});

// ---- fase J: converter fluxo e desfazer; transições de versão (atrás de `features.skills`) ---------------------

function fluxo(status: 'active' | 'disabled' = 'active') {
  return { id: 'abrir-conversa', name: 'Abrir a conversa', command_template: 'abra a conversa com {username} no instagram',
    app_id: 'instagram', source_run_id: 'r-1', status, uses: 3, created_at: '2026-09-27T10:00:00Z', last_used_at: null };
}

function versao(version: number, state: string, extra: Record<string, unknown> = {}) {
  return { ref: `instagram.abrir-conversa@${version}`, skill_id: 'instagram.abrir-conversa', version,
    name: 'Abrir a conversa', app_id: 'instagram', state, command_template: 'abra a conversa com {username} no instagram',
    schema_version: version === 1 ? 0 : 1, content_hash: 'h', intact: true, state_at: '2026-09-27T12:00:00.000Z',
    legacy_flow_id: 'abrir-conversa', ...extra };
}

async function renderComDialogo(): Promise<void> {
  await act(async () => root.render(<><FlowsRecipesSection /><ConfirmHost /></>));
}

it('desligado: um fluxo ativo não ganha "converter em habilidade" nem ação de versão', async () => {
  comHabilidades(false);
  backend.on('GET', /\/flows$/, () => json([fluxo()]));
  await renderComDialogo();
  await waitFor(() => expect(text()).toContain('Abrir a conversa'));
  expect(allByRole('button', /Converter o fluxo/)).toHaveLength(0);
  expect(allByRole('button', /Desfazer a conversão/)).toHaveLength(0);
  expect(backend.callsTo('GET', /\/skills$/)).toHaveLength(0);
});

it('29.42: o fluxo entre apps mostra "QA Messenger → Chrome" na ordem do plano; sem required_apps, o app do fluxo; sem nenhum, nada', async () => {
  comHabilidades(false);
  useAppStore.setState({ apps: [{ ...APPS[0]!, id: 'qa-messenger', name: 'QA Messenger', package: 'com.pocqa.messenger' },
                                { ...APPS[0]!, id: 'chrome', name: 'Chrome', package: 'com.android.chrome' }] });
  backend.on('GET', /\/flows$/, () => json([{ ...fluxo(), required_apps: ['qa-messenger', 'chrome'] },
    // UX do deploy 8: o fluxo aprendido de plano antigo chega com required_apps vazio e o app em app_id.
    { ...fluxo(), id: 'do-qa', name: 'Do QA', app_id: 'qa-messenger', required_apps: [] },
    { ...fluxo(), id: 'outro', name: 'Outro', app_id: null, required_apps: [] }]));
  await renderComDialogo();
  await waitFor(() => expect(text()).toContain('QA Messenger → Chrome'));
  expect(Array.from(document.querySelectorAll('[aria-label="Apps do fluxo"]')).map((s) => s.textContent))
    .toEqual(['QA Messenger → Chrome', 'QA Messenger']);
});

it('ligado: converter o fluxo ativo pede confirmação, chama a rota e recarrega as duas listas', async () => {
  comHabilidades(true);
  backend.on('GET', /\/flows$/, () => json([fluxo()]));
  backend.on('GET', /\/skills$/, () => json([]));
  backend.on('POST', /\/flows\/abrir-conversa\/adopt$/, () => json({
    flow_id: 'abrir-conversa', skill_id: 'instagram.abrir-conversa', published: versao(1, 'published'),
    draft: versao(2, 'draft'), warnings: [{ code: 'W_NO_VALIDATION_CASE', message: 'sem caso', path: '/spec/validation',
      severity: 'warning', origin: 'document' }] }, 201));
  await renderComDialogo();
  await waitFor(() => expect(allByRole('button', /Converter o fluxo Abrir a conversa em habilidade/)).toHaveLength(1));

  await click(byRole('button', /Converter o fluxo Abrir a conversa em habilidade/));
  await waitFor(() => expect(text()).toContain('A versão 1 da habilidade é o plano deste fluxo'));
  await setValue(byRole('textbox', /Motivo/, byRole('dialog', /Converter/)) as HTMLTextAreaElement, 'migrar');
  const listasAntes = backend.callsTo('GET', /\/flows$/).length;
  const habilidadesAntes = backend.callsTo('GET', /\/skills$/).length;
  await click(byRole('button', /^Converter$/, byRole('dialog', /Converter/)));
  await waitFor(() => expect(backend.callsTo('POST', /\/adopt$/)).toHaveLength(1));
  expect(backend.callsTo('POST', /\/adopt$/)[0]?.body).toEqual({ reason: 'migrar' });
  await waitFor(() => expect(backend.callsTo('GET', /\/flows$/).length).toBeGreaterThan(listasAntes));
  await waitFor(() => expect(backend.callsTo('GET', /\/skills$/).length).toBeGreaterThan(habilidadesAntes));
});

it('ligado: fluxo adotado mostra a habilidade, trava o interruptor e desfaz pela rota', async () => {
  comHabilidades(true);
  backend.on('GET', /\/flows$/, () => json([fluxo('disabled')]));
  backend.on('GET', /\/skills$/, () => json([versao(1, 'published'), versao(2, 'draft')]));
  backend.on('POST', /\/flows\/abrir-conversa\/release$/, () => json({ flow_id: 'abrir-conversa',
    skill_id: 'instagram.abrir-conversa', flow_status: 'active', discarded_drafts: ['instagram.abrir-conversa@2'],
    versions: [versao(1, 'disabled')] }));
  await renderComDialogo();
  await waitFor(() => expect(text()).toContain('habilidade instagram.abrir-conversa@1'));
  expect(allByRole('button', /Converter o fluxo/)).toHaveLength(0);
  const interruptor = byRole('switch', /Abrir a conversa/);
  expect(interruptor.hasAttribute('disabled') || interruptor.getAttribute('aria-disabled') === 'true').toBe(true);
  expect(text()).toContain('convertida do fluxo abrir-conversa');

  await click(byRole('button', /Desfazer a conversão do fluxo Abrir a conversa/));
  await waitFor(() => expect(text()).toContain('volta a valer exatamente como era'));
  await click(byRole('button', /^Desfazer$/, byRole('dialog', /Desfazer/)));
  await waitFor(() => expect(backend.callsTo('POST', /\/release$/)).toHaveLength(1));
});

it('ligado: a recusa da conversão aparece na linha do fluxo, com o código e os erros da ida e volta', async () => {
  comHabilidades(true);
  backend.on('GET', /\/flows$/, () => json([fluxo()]));
  backend.on('GET', /\/skills$/, () => json([]));
  backend.on('POST', /\/adopt$/, () => json({ detail: { code: 'invalid_document',
    message: 'O fluxo abrir-conversa não se converte sem mudar o plano.',
    errors: ['E_ROUNDTRIP /steps/1/postcondition/value: abrir_conversa: postcondition/value muda'] } }, 422));
  await renderComDialogo();
  await waitFor(() => expect(allByRole('button', /Converter o fluxo/)).toHaveLength(1));
  await click(byRole('button', /Converter o fluxo/));
  await waitFor(() => expect(text()).toContain('A versão 1 da habilidade'));
  await click(byRole('button', /^Converter$/, byRole('dialog', /Converter/)));
  await waitFor(() => expect(text()).toContain('invalid_document'));
  expect(text()).toContain('não se converte sem mudar o plano');
  expect(text()).toContain('E_ROUNDTRIP /steps/1/postcondition/value');
});

it('ligado: cada versão tem as transições do seu estado; validar com motivo é a validação manual', async () => {
  comHabilidades(true);
  backend.on('GET', /\/flows$/, () => json([]));
  backend.on('GET', /\/skills$/, () => json([versao(2, 'candidate'), versao(3, 'draft', { legacy_flow_id: null })]));
  backend.on('POST', /\/skills\/instagram\.abrir-conversa\/versions\/2\/status$/, () => json({
    ...versao(2, 'validated'), parent_version: 1, state_detail: 'validação manual', history: [] }));
  await renderComDialogo();
  await waitFor(() => expect(text()).toContain('instagram.abrir-conversa@3'));
  expect(allByRole('button', /^Submeter instagram\.abrir-conversa@3$/)).toHaveLength(1);
  expect(allByRole('button', /^Validar instagram\.abrir-conversa@2$/)).toHaveLength(1);
  expect(allByRole('button', /^Desabilitar instagram\.abrir-conversa@2$/)).toHaveLength(1);
  expect(allByRole('button', /^Publicar /)).toHaveLength(0);

  await click(byRole('button', /^Validar instagram\.abrir-conversa@2$/));
  await waitFor(() => expect(text()).toContain('validação manual do dono'));
  await setValue(byRole('textbox', /Motivo/, byRole('dialog', /Validar/)) as HTMLTextAreaElement, 'conversão comprovada');
  await click(byRole('button', /^Validar$/, byRole('dialog', /Validar/)));
  await waitFor(() => expect(backend.callsTo('POST', /\/status$/)).toHaveLength(1));
  expect(backend.callsTo('POST', /\/status$/)[0]?.body).toEqual({ to: 'validated', reason: 'conversão comprovada', manual: true });
});

it('ligado: a recusa da transição mostra o código e as pendências do domínio na linha da versão', async () => {
  comHabilidades(true);
  backend.on('GET', /\/flows$/, () => json([]));
  backend.on('GET', /\/skills$/, () => json([versao(2, 'candidate')]));
  backend.on('POST', /\/status$/, () => json({ detail: { code: 'validation_pending',
    message: 'instagram.abrir-conversa@2 ainda não tem a prova que `validated` exige.',
    pending: ['caso x: sem observação'] } }, 409));
  await renderComDialogo();
  await waitFor(() => expect(allByRole('button', /^Validar /)).toHaveLength(1));
  await click(byRole('button', /^Validar /));
  await waitFor(() => expect(text()).toContain('validação manual do dono'));
  await click(byRole('button', /^Validar$/, byRole('dialog', /Validar/)));
  await waitFor(() => expect(text()).toContain('validation_pending'));
  expect(backend.callsTo('POST', /\/status$/)[0]?.body).toEqual({ to: 'validated', reason: '', manual: false });
  expect(text()).toContain('caso x: sem observação');
});

// ---- fase L (usabilidade): P1.2 lista agrupada por habilidade; P2.7 padrões do bloco vizinho; P2.8 coluna fixa ----

it('ligado: agrupa as versões por habilidade — publicada e última em destaque, as anteriores recolhidas; app pelo nome; hash explicado', async () => {
  comHabilidades(true);
  useAppStore.setState({ apps: [{ ...APPS[0]!, id: 'instagram', name: 'Instagram', package: 'com.instagram.android' }] });
  backend.on('GET', /\/skills$/, () => json([
    versao(1, 'deprecated', { intact: false }), versao(3, 'draft', { legacy_flow_id: null }), versao(2, 'published'),
    { ...versao(1, 'candidate', { legacy_flow_id: null }), ref: 'qa.outra@1', skill_id: 'qa.outra', name: 'Outra', app_id: 'qa-sem-cadastro' },
  ]));
  await renderComDialogo();
  await waitFor(() => expect(text()).toContain('instagram.abrir-conversa@3'));
  expect(text()).toContain('Habilidades (2)');                      // duas habilidades, quatro versões
  expect(text()).toContain('3 versões');
  expect(text()).toContain('app: Instagram');                       // o nome cadastrado, como na lista de fluxos
  expect(text()).toContain('app: qa-sem-cadastro');                 // sem cadastro, fica o id
  expect(text()).toContain('última');

  // Cada versão mantém as ações do seu estado, inclusive a anterior (recolhida, mas no DOM).
  expect(allByRole('button', /^Recolher instagram\.abrir-conversa@2$/)).toHaveLength(1);
  expect(allByRole('button', /^Submeter instagram\.abrir-conversa@3$/)).toHaveLength(1);
  expect(allByRole('button', /^Publicar de novo instagram\.abrir-conversa@1$/)).toHaveLength(1);
  expect(allByRole('button', /^Validar qa\.outra@1$/)).toHaveLength(1);

  // A anterior fica num Disclosure recolhido; a publicada e a última, fora dele.
  const anterior = byRole('button', /^Publicar de novo instagram\.abrir-conversa@1$/).closest('details') as HTMLDetailsElement | null;
  expect(anterior).not.toBeNull();
  expect(anterior!.open).toBe(false);
  expect(text(anterior!)).toContain('1 versão anterior');
  expect(byRole('button', /^Recolher instagram\.abrir-conversa@2$/).closest('details')?.id).toBe('settings-habilidades');

  // Estado com ícone (StatusBadge) e "conteúdo alterado" explicando o hash.
  const publicada = Array.from(document.querySelectorAll('span')).find((el) => el.textContent === 'Estado: publicada');
  expect(publicada?.querySelector('svg')).not.toBeNull();
  const alterado = document.querySelector('[title*="hash"]');
  expect(alterado?.textContent).toContain('conteúdo alterado');
  expect(alterado?.getAttribute('title')).toContain('fora do ciclo de vida');
});

it('ligado: a seção Habilidades lembra aberta/fechada entre visitas, como as vizinhas', async () => {
  comHabilidades(true);
  await renderComDialogo();
  await waitFor(() => expect(text()).toContain('Mandar mensagem no QA'));
  const secao = document.getElementById('settings-habilidades') as HTMLDetailsElement;
  expect(secao.open).toBe(true);
  await act(async () => {
    secao.open = false;
    secao.dispatchEvent(new Event('toggle'));
  });
  expect(window.localStorage.getItem('cda.settings.section.habilidades')).toBe('false');

  await act(async () => root.unmount());
  container.remove();
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
  await renderComDialogo();
  await waitFor(() => { if (!document.getElementById('settings-habilidades')) throw new Error('seção ainda não apareceu'); });
  expect((document.getElementById('settings-habilidades') as HTMLDetailsElement).open).toBe(false);
});

it('receitas: a candidata aparece como Candidata, com a prova em sombra e sem botão de ativar', async () => {
  comHabilidades(undefined);
  const ativa = RECIPES[0];
  const candidata = { ...ativa, id: 21, step_key: 'open_conversation', version: 1, status: 'candidate' as const,
    replay_ok: 0, replay_fail: 0, shadow_agree: 1, shadow_total: 1, last_used_at: null };
  const nova = { ...candidata, id: 22, step_key: 'compose_message', shadow_agree: 0, shadow_total: 0 };
  backend.on('GET', /\/recipes$/, () => json([candidata, nova, ativa]));
  await act(async () => root.render(<FlowsRecipesSection />));
  await waitFor(() => expect(allByRole('button', /Excluir a receita/)).toHaveLength(3));
  const linha = byRole('button', /Excluir a receita open_conversation v1/).closest('tr') as HTMLElement;
  expect(text(linha)).toContain('Candidata');
  expect(text(linha)).toContain('1/1 (100%)');
  expect(text(byRole('button', /Excluir a receita compose_message v1/).closest('tr') as HTMLElement))
    .toContain('em prova, sem comparação ainda');
  expect(byRole('button', /Pôr em quarentena a receita open_conversation v1/)).toBeDefined();
  expect(allByRole('button', /Reativar a receita open_conversation/)).toHaveLength(0);
  expect(text(byRole('button', /Excluir a receita send v2/).closest('tr') as HTMLElement)).toContain('Ativa');
});

it('receitas: a coluna dos botões é fixa à direita da tabela (cabeçalho e células)', async () => {
  comHabilidades(undefined);
  backend.on('GET', /\/recipes$/, () => json(RECIPES));
  await act(async () => root.render(<FlowsRecipesSection />));
  await waitFor(() => expect(allByRole('button', /Excluir a receita/)).toHaveLength(RECIPES.length));
  const tabela = byRole('button', /Excluir a receita send v2/).closest('table') as HTMLTableElement;
  const ultimoTh = tabela.querySelector('thead th:last-child') as HTMLElement;
  expect(ultimoTh.className).toMatch(/stickyCol/);
  for (const linha of Array.from(tabela.querySelectorAll('tbody tr'))) {
    expect((linha.lastElementChild as HTMLElement).className).toMatch(/stickyCol/);
    expect(text(linha.lastElementChild as HTMLElement)).toMatch(/Excluir/);
  }
});

it('candidato não se passa por desligado, e o desligado mostra quem desligou e por quê (deploys 11–12)', async () => {
  comHabilidades(false);
  backend.on('GET', /\/flows$/, () => json([
    { ...fluxo(), id: 'mandar-oi', name: 'Mandar para o {contact_position}º contato', status: 'candidate' },
    { ...fluxo('disabled'), id: 'abrir-qa', name: 'Abrir o QA' },
  ]));
  backend.on('GET', /\/aprendizado\/fluxo\/abrir-qa$/, () => json({
    item: {}, evidencias: [], exposicoes: [],
    trilha: [{ id: 1, from: null, to: 'published', reason: 'nasceu', decided_by: 'sistema',
               decided_at: '2026-10-02T10:00:00Z', run_id: null },
             { id: 2, from: 'published', to: 'disabled', reason: 'validar pela IA antes de valer',
               decided_by: 'orquestradora', decided_at: '2026-10-03T08:45:07Z', run_id: null }],
  }));
  await renderComDialogo();
  await waitFor(() => expect(text()).toContain('validar pela IA antes de valer'));
  expect(text()).toContain('Desligado por orquestradora em');
  expect(text()).toContain('candidato');
  expect(text()).toContain('Não vale ainda');
  // O nome sem a lacuna em claro; o comando-modelo destaca o parâmetro sem as chaves (o cru fica na dica).
  expect(text()).toContain('Mandar para o …º contato');
  expect(text()).not.toContain('{contact_position}');
  expect(text()).not.toContain('{username}');
  expect(container.querySelector('mark[title="{username}"]')?.textContent).toBe('username');
  // Só o desligado lê a trilha.
  expect(backend.callsTo('GET', /\/aprendizado\/fluxo\//)).toHaveLength(1);
});
