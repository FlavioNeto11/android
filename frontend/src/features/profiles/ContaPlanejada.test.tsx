// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type { AcaoDeContaItem, CommandRefinement, ProfileAccount, ProvisioningInfo } from '../../api/types';
import { ConfirmHost } from '../../components/Confirm';
import { useAppStore } from '../../store/app';
import { initialDataState } from '../../store/reducer';
import { useUiStore } from '../../store/ui';
import { makePersona, makeSnapshot } from '../../test/fixtures';
import { FakeBackend, apiError, byRole, click, installBrowserStubs, json, setValue, text, waitFor } from '../../test/harness';
import { AssistenteDoComando } from '../command/AssistenteDoComando';
import { AbaContasEAcesso } from './GuiaContas';

/**
 * Conta planejada (ADR-087, 31.283): preparar a conta de uma persona num app ANTES de ela existir no serviço, ver o
 * ciclo e agir pelo assistente do comando. Prova `simulated` (backend falso, contrato v1.132): nada aqui toca o
 * Outlook nem conta real.
 */
let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

// Montada na hora: nenhuma string de senha fica escrita no arquivo.
const SENHA = ['Teste', 'x9', 'Zq'].join('-');
const PESSOA = makePersona('persona-1', 'Luciana Bastos');

beforeAll(() => installBrowserStubs());

beforeEach(() => {
  backend = new FakeBackend();
  backend.install();
  window.localStorage.clear();
  useAppStore.setState({ ...initialDataState });
  useAppStore.getState().hydrate(makeSnapshot());
  useUiStore.setState({ selectedIds: ['android-01'] });
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

const SEM_SENHA = { configured: false, login_identifier: null, status: null, failed_attempts: 0, blocked_until: null,
                    updated_at: null, last_used_at: null, consent_at: null, consent_by: null };

function provisionamento(over: Partial<ProvisioningInfo> = {}): ProvisioningInfo {
  return { state: 'planejada', desired_handle: 'luciana.bastos@exemplo.com', detail: null, resume_state: null,
           confirmed_at: null, evidence: null, actions: ['cancelar', 'falhar'], authenticated: false, ...over };
}

function conta(over: Partial<ProfileAccount> = {}): ProfileAccount {
  return {
    id: 'acc-9', profile_id: 'persona-1', app_id: 'notes', app_name: 'Notas', package: 'com.poc.notes', handle: '',
    host: null, login_identifier: null, status: 'active', session_status: 'unknown', session_detail: null,
    session_verified_at: null, session_actions: null, automated_login: false, credential_configured: false,
    credential: SEM_SENHA, consent_at: null, notes: '', created_at: '2026-10-10T10:00:00Z', updated_at: '2026-10-10T10:00:00Z',
    provisioning: provisionamento(), ...over,
  };
}

const CONFIRMADA = conta({
  id: 'acc-1', app_id: 'qa', app_name: 'QA Messenger', handle: 'luciana', credential_configured: true,
  credential: { ...SEM_SENHA, configured: true }, provisioning: undefined,
});

async function abrirGuia(contas: ProfileAccount[]) {
  backend.on('GET', /\/accounts$/, () => json(contas));
  backend.on('GET', /app-catalog/, () => json([]));
  await act(async () => {
    root.render(
      <>
        <ConfirmHost />
        <AbaContasEAcesso profile={PESSOA} contas={contas} recarregar={async () => {}} onChanged={async () => {}} />
      </>,
    );
  });
}

describe('Guia Contas e acesso — conta planejada', () => {
  it('conta planejada não oferece Conectar, Verificar nem Sair, e diz desejado x confirmado', async () => {
    await abrirGuia([conta()]);
    const t = text(container);
    expect(t).toContain('Planejada, sem senha');
    expect(t).toContain('Esta conta ainda não existe no serviço');
    expect(t).toContain('ainda não confirmado pelo serviço');
    expect((byRole('textbox', /Endereço desejado de Notas/) as HTMLInputElement).value).toBe('luciana.bastos@exemplo.com');
    expect(() => byRole('button', /Conectar/)).toThrow();
    expect(() => byRole('button', /Verificar conta/)).toThrow();
    expect(() => byRole('button', /Sair da conta/)).toThrow();
  });

  it('conta de central anterior ao v1.132 (sem provisioning) segue como confirmada, com os botões de sempre', async () => {
    await abrirGuia([{ ...CONFIRMADA, automated_login: true }]);
    expect(text(container)).not.toContain('ainda não existe no serviço');
    expect(byRole('button', /Conectar/)).toBeTruthy();
  });

  it('"Preparar conta nova": planeja, grava a senha gerada (sem senha no corpo) e a esvazia da tela', async () => {
    backend
      .on('GET', /handle-suggestions/, () => json({ suggestions: [{ handle: 'luciana.bastos', source: 'persona_nome' }] }))
      .on('POST', /\/accounts\/planned$/, () => json(conta(), 201))
      .on('POST', /\/credential\/prepare$/, () => json(conta({
        credential_configured: true, credential: { ...SEM_SENHA, configured: true },
        provisioning: provisionamento({ state: 'credencial_preparada', actions: ['iniciar_cadastro', 'cancelar', 'falhar'] }),
      })));
    await abrirGuia([]);
    await click(byRole('button', /Preparar conta nova/));
    await setValue(byRole('combobox', /Aplicativo ou serviço/) as HTMLSelectElement, 'notes');
    await waitFor(() => text(container).includes('luciana.bastos'));
    await click(byRole('button', /^luciana\.bastos$/));
    expect(byRole('button', /Preparar conta/).getAttribute('aria-disabled')).toBe('true');   // falta a autorização
    await click(byRole('checkbox', /Autorizo a automação a digitar esta senha/));
    await click(byRole('button', /Preparar conta$/));
    await waitFor(() => backend.callsTo('POST', /\/credential\/prepare$/).length === 1);
    expect(backend.callsTo('POST', /\/accounts\/planned$/)[0]?.body).toEqual({
      app_id: 'notes', host: null, desired_handle: 'luciana.bastos' });
    expect(backend.callsTo('POST', /\/credential\/prepare$/)[0]?.body).toEqual({ modo: 'gerar', consent: true });
    expect(JSON.stringify(backend.calls.map((c) => c.body))).not.toMatch(/password/);
  });

  it('digitar a senha: ela vai só ao prepare, nunca fica na tela e o botão exige o consentimento', async () => {
    backend
      .on('GET', /handle-suggestions/, () => json({ suggestions: [] }))
      .on('POST', /\/accounts\/planned$/, () => json(conta(), 201))
      .on('POST', /\/credential\/prepare$/, () => json(conta()));
    await abrirGuia([]);
    await click(byRole('button', /Preparar conta nova/));
    await setValue(byRole('combobox', /Aplicativo ou serviço/) as HTMLSelectElement, 'notes');
    await click(byRole('radio', /Digitar a senha/));
    const campo = container.querySelector('input[type="password"]') as HTMLInputElement;
    expect(campo.getAttribute('autocomplete')).toBe('new-password');
    await setValue(campo, SENHA);
    await click(byRole('checkbox', /Autorizo a automação a digitar esta senha/));
    await click(byRole('button', /Preparar conta$/));
    await waitFor(() => backend.callsTo('POST', /\/credential\/prepare$/).length === 1);
    expect(backend.callsTo('POST', /\/credential\/prepare$/)[0]?.body).toEqual({ modo: 'digitar', consent: true, password: SENHA });
    expect(backend.callsTo('POST', /\/accounts\/planned$/)[0]?.body).not.toHaveProperty('password');
    expect(text(container)).not.toContain(SENHA);
    expect(JSON.stringify(Object.fromEntries(Object.entries(window.localStorage)))).not.toContain(SENHA);
  });

  it('preparar a senha numa conta planejada: o envio que falha esvazia a senha e desmarca a autorização', async () => {
    backend.on('POST', /\/credential\/prepare$/, () => apiError(409, 'estado_nao_permite_credencial', 'Agora não.'));
    await abrirGuia([conta()]);
    await click(byRole('radio', /Digitar a senha/));
    const campo = container.querySelector('input[type="password"]') as HTMLInputElement;
    await setValue(campo, SENHA);
    await click(byRole('checkbox', /Autorizo a automação a digitar esta senha/));
    await click(byRole('button', /Preparar a senha/));
    await waitFor(() => backend.callsTo('POST', /\/credential\/prepare$/).length === 1);
    expect(backend.callsTo('POST', /\/credential\/prepare$/)[0]?.body).toEqual({ modo: 'digitar', consent: true, password: SENHA });
    await waitFor(() => (container.querySelector('input[type="password"]') as HTMLInputElement).value === '');
    expect((byRole('checkbox', /Autorizo a automação a digitar esta senha/) as HTMLInputElement).checked).toBe(false);
  });

  it('"Preparar conta" numa conta que já tem senha preparada pede confirmação antes de trocar, e recusar não grava', async () => {
    const preparada = conta({ credential_configured: true, credential: { ...SEM_SENHA, configured: true },
                              provisioning: provisionamento({ state: 'credencial_preparada', actions: ['iniciar_cadastro', 'cancelar'] }) });
    backend
      .on('GET', /handle-suggestions/, () => json({ suggestions: [] }))
      .on('POST', /\/accounts\/planned$/, () => json(preparada))
      .on('POST', /\/credential\/prepare$/, () => json(preparada));
    await abrirGuia([]);
    await click(byRole('button', /Preparar conta nova/));
    await setValue(byRole('combobox', /Aplicativo ou serviço/) as HTMLSelectElement, 'notes');
    await click(byRole('checkbox', /Autorizo a automação a digitar esta senha/));
    await click(byRole('button', /Preparar conta$/));
    await waitFor(() => text(document).includes('Trocar a senha já guardada'));
    await click(byRole('button', /^Voltar$/));
    await waitFor(() => !text(document).includes('Trocar a senha já guardada'));
    expect(backend.callsTo('POST', /\/credential\/prepare$/)).toHaveLength(0);
  });

  it('reaproveitar a senha é opcional e nunca o padrão: o modo nasce em "gerar" e a origem é outra conta da pessoa', async () => {
    backend
      .on('GET', /handle-suggestions/, () => json({ suggestions: [] }))
      .on('POST', /\/accounts\/planned$/, () => json(conta(), 201))
      .on('POST', /\/credential\/prepare$/, () => json(conta()));
    await abrirGuia([CONFIRMADA]);
    await click(byRole('button', /Preparar conta nova/));
    expect((byRole('radio', /Gerar uma senha forte/) as HTMLInputElement).checked).toBe(true);
    await setValue(byRole('combobox', /Aplicativo ou serviço/) as HTMLSelectElement, 'notes');
    await click(byRole('radio', /Reaproveitar de outra conta/));
    await setValue(byRole('combobox', /Reaproveitar a senha de/) as HTMLSelectElement, 'acc-1');
    await click(byRole('checkbox', /Autorizo a automação a digitar esta senha/));
    await click(byRole('button', /Preparar conta$/));
    await waitFor(() => backend.callsTo('POST', /\/credential\/prepare$/).length === 1);
    expect(backend.callsTo('POST', /\/credential\/prepare$/)[0]?.body).toEqual({ modo: 'reutilizar', consent: true, clonar_de: 'acc-1' });
  });

  it('confirmada a troca, o prepare leva substituir: true', async () => {
    const preparada = conta({ credential_configured: true, credential: { ...SEM_SENHA, configured: true },
                              provisioning: provisionamento({ state: 'credencial_preparada', actions: ['iniciar_cadastro', 'cancelar'] }) });
    backend
      .on('GET', /handle-suggestions/, () => json({ suggestions: [] }))
      .on('POST', /\/accounts\/planned$/, () => json(preparada))
      .on('POST', /\/credential\/prepare$/, () => json(preparada));
    await abrirGuia([]);
    await click(byRole('button', /Preparar conta nova/));
    await setValue(byRole('combobox', /Aplicativo ou serviço/) as HTMLSelectElement, 'notes');
    await click(byRole('checkbox', /Autorizo a automação a digitar esta senha/));
    await click(byRole('button', /Preparar conta$/));
    await waitFor(() => text(document).includes('Trocar a senha já guardada'));
    await click(byRole('button', /^Trocar a senha$/));
    await waitFor(() => backend.callsTo('POST', /\/credential\/prepare$/).length === 1);
    expect(backend.callsTo('POST', /\/credential\/prepare$/)[0]?.body).toEqual({ modo: 'gerar', consent: true, substituir: true });
  });

  it('cada evento leva o estado visto (estado_esperado) e a tela relê; confirmar exige a evidência declarada', async () => {
    const preparada = conta({
      credential_configured: true, credential: { ...SEM_SENHA, configured: true },
      provisioning: provisionamento({ state: 'aguardando_verificacao', actions: ['confirmar', 'falhar', 'cancelar'] }),
    });
    backend.on('POST', /\/provisioning$/, () => json(preparada));
    await abrirGuia([preparada]);
    await click(byRole('button', /Confirmar a conta/));
    const confirmar = byRole('button', /Confirmar com esta evidência/);
    expect(confirmar.getAttribute('aria-disabled')).toBe('true');
    await setValue(byRole('textbox', /Endereço que o serviço confirmou/) as HTMLInputElement, 'luciana.bastos@exemplo.com');
    await click(byRole('button', /Confirmar com esta evidência/));
    await waitFor(() => backend.callsTo('POST', /\/provisioning$/).length === 1);
    expect(backend.callsTo('POST', /\/provisioning$/)[0]?.body).toEqual({
      evento: 'confirmar', estado_esperado: 'aguardando_verificacao',
      evidencia: { tipo: 'declarada', handle_confirmado: 'luciana.bastos@exemplo.com' } });
  });

  it('falha: mostra o motivo e oferece retomar de onde parou', async () => {
    const falha = conta({
      provisioning: provisionamento({ state: 'falha', resume_state: 'aguardando_cadastro_externo', detail: 'O serviço pediu telefone.',
                                      actions: ['retomar', 'cancelar'] }),
    });
    backend.on('POST', /\/provisioning$/, () => json(falha));
    await abrirGuia([falha]);
    expect(text(container)).toContain('O serviço pediu telefone.');
    await click(byRole('button', /Retomar de onde parou/));
    await waitFor(() => backend.callsTo('POST', /\/provisioning$/).length === 1);
    expect(backend.callsTo('POST', /\/provisioning$/)[0]?.body).toEqual({ evento: 'retomar', estado_esperado: 'falha' });
  });

  it('cancelar pede confirmação e aceita a resposta {removida}', async () => {
    backend.on('POST', /\/provisioning$/, () => json({ removida: true, credencial_removida: false }));
    await abrirGuia([conta()]);
    await click(byRole('button', /Cancelar a conta$/));
    await click(byRole('button', /^Cancelar a conta$/, document.querySelector('[role="dialog"]') ?? document));
    await waitFor(() => backend.callsTo('POST', /\/provisioning$/).length === 1);
    expect(backend.callsTo('POST', /\/provisioning$/)[0]?.body).toEqual({ evento: 'cancelar', estado_esperado: 'planejada' });
  });
});

describe('Assistente do comando — ações de conta (v1.132)', () => {
  const ITEM: AcaoDeContaItem = {
    persona_id: 'persona-1', persona_nome: 'Luciana Bastos', app_id: 'notes', app_nome: 'Notas', estado: 'sem_conta',
    acoes: ['preparar_credencial', 'abrir_contas_e_acesso', 'usar_credencial_existente', 'continuar'],
    reutilizavel_de: [{ account_id: 'acc-1', app_id: 'qa', app_nome: 'QA Messenger' }],
  };
  const COM_ACAO: CommandRefinement = {
    command: 'Objetivo: criar a conta de Notas', summary: 'Falta preparar a conta.', questions: [], ready: false, notes: [],
    acoes_de_conta: [ITEM],
  };
  const PRONTO: CommandRefinement = { ...COM_ACAO, summary: 'Pronto.', ready: true, acoes_de_conta: [] };

  async function abrirAssistente(rodadas: CommandRefinement[]) {
    let n = 0;
    backend.on('POST', /^\/api\/commands\/refine$/, () => json(rodadas[Math.min(n++, rodadas.length - 1)]));
    await act(async () => root.render(
      <AssistenteDoComando comando="crie a conta de Notas da Luciana" contexto={{ profile_ids: ['persona-1'] }}
                           autoIniciar acoes={(_, pronto) => <span>{pronto ? 'pode planejar' : 'nao pode planejar'}</span>} />,
    ));
    await waitFor(() => text(container).includes('Contas a preparar') || backend.callsTo('POST', /refine$/).length > 0);
  }

  it('mostra o cartão da conta em vez de uma pergunta de senha, conta como pendência e não deixa planejar', async () => {
    await abrirAssistente([COM_ACAO]);
    await waitFor(() => text(container).includes('Luciana Bastos'));
    const t = text(container);
    expect(t).toContain('Esta persona ainda não possui uma conta Notas preparada');
    expect(t).toContain('1 pendência');
    expect(t).toContain('nao pode planejar');
    expect(byRole('link', /Abrir Contas e acesso/).getAttribute('href')).toBe('#/personas/persona-1/contas');
  });

  it('preparar a credencial no cartão grava no cofre e reavalia o comando; a senha não entra no refinamento', async () => {
    backend
      .on('GET', /\/accounts$/, () => json([]))
      .on('GET', /handle-suggestions/, () => json({ suggestions: [] }))
      .on('POST', /\/accounts\/planned$/, () => json(conta(), 201))
      .on('POST', /\/credential\/prepare$/, () => json(conta()));
    await abrirAssistente([COM_ACAO, PRONTO]);
    await waitFor(() => text(container).includes('Luciana Bastos'));
    await click(byRole('button', /Preparar credencial para esta persona/));
    await waitFor(() => !!container.querySelector('input[type="radio"]'));
    expect((byRole('combobox', /Aplicativo ou serviço/) as HTMLSelectElement).disabled).toBe(true);   // o app vem do cartão
    await click(byRole('radio', /Digitar a senha/));
    await setValue(container.querySelector('input[type="password"]') as HTMLInputElement, SENHA);
    await click(byRole('checkbox', /Autorizo a automação a digitar esta senha/));
    await click(byRole('button', /Preparar conta$/));
    await waitFor(() => backend.callsTo('POST', /refine$/).length === 2);
    await waitFor(() => text(container).includes('pode planejar'));
    for (const c of backend.callsTo('POST', /refine$/)) expect(JSON.stringify(c.body)).not.toContain(SENHA);
    expect(backend.callsTo('POST', /\/accounts\/planned$/)[0]?.body).toEqual({ app_id: 'notes', host: null, desired_handle: null });
    expect(text(container)).not.toContain(SENHA);
  });

  it('com host no item, o site vai ao planned; sem host, o painel não inventa um', async () => {
    const comSite: AcaoDeContaItem = { ...ITEM, host: 'portal.exemplo.com.br' };
    backend
      .on('GET', /\/accounts$/, () => json([]))
      .on('GET', /handle-suggestions/, () => json({ suggestions: [] }))
      .on('POST', /\/accounts\/planned$/, () => json(conta(), 201))
      .on('POST', /\/credential\/prepare$/, () => json(conta()));
    await abrirAssistente([{ ...COM_ACAO, acoes_de_conta: [comSite] }, PRONTO]);
    await waitFor(() => text(container).includes('portal.exemplo.com.br'));
    await click(byRole('button', /Preparar credencial para esta persona/));
    await waitFor(() => !!container.querySelector('input[type="radio"]'));
    await click(byRole('checkbox', /Autorizo a automação a digitar esta senha/));
    await click(byRole('button', /Preparar conta$/));
    await waitFor(() => backend.callsTo('POST', /\/accounts\/planned$/).length === 1);
    expect(backend.callsTo('POST', /\/accounts\/planned$/)[0]?.body).toMatchObject({ app_id: 'notes' });
  });

  it('"usar credencial existente" sem outra conta com senha fica bloqueado e diz por quê', async () => {
    await abrirAssistente([{ ...COM_ACAO, acoes_de_conta: [{ ...ITEM, reutilizavel_de: [] }] }]);
    await waitFor(() => text(container).includes('Luciana Bastos'));
    const botao = byRole('button', /Usar credencial existente/);
    expect(botao.getAttribute('aria-disabled')).toBe('true');
    expect(botao.textContent).toContain('Nenhuma outra conta desta pessoa tem senha guardada');
  });
});
