// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { ApiError } from '../../api/client';
import type { ProfileAccount } from '../../api/types';
import { ConfirmHost } from '../../components/Confirm';
import { useUiStore } from '../../store/ui';
import { APPS, makeInstance } from '../../test/fixtures';
import { FakeBackend, apiError, byRole, click, installBrowserStubs, json, setValue, text, waitFor } from '../../test/harness';
import {
  erroDoFormulario, fontesDoCampo, lerRascunho, lerTeto, limparRascunho, montarCorpo, previaDaCapacidade, rascunhoDaOperacao, recusaDeParametros, resolverAlvo, type FormularioDaOperacao,
} from './criar';
import { lerOperacao } from './modelo';
import { OperacaoPage } from './OperacaoPage';

/**
 * 31.176: criar a operação pela tela. Prova `simulated` (servidor falso, formato do adendo v1.94/v1.95): a regra pura de resolução do
 * alvo, a prévia da capacidade, o corpo enviado (instance_id explícito) e a confirmação com o teto em destaque.
 */

const conta = (id: string, over: Partial<ProfileAccount> & { instancia?: string | null; sessao?: string } = {}): ProfileAccount => {
  const { instancia = 'android-02', sessao = 'session_ready', ...resto } = over;
  return {
    id, profile_id: 'p', app_id: 'instagram', app_name: 'Instagram', package: null, handle: `@${id}`, status: 'active', session_status: sessao,
    session_detail: null, session_verified_at: null, session: { status: sessao, instance_id: instancia, observed_username: null, verified_at: null, detail: null, stale: false },
    automated_login: true, credential_configured: true, ...resto,
  } as unknown as ProfileAccount;
};
const APARELHOS = new Set(['android-02', 'android-03']);
const VAZIA = { conta: '', aparelho: '' };

describe('resolverAlvo: só o que existe', () => {
  it('conta ativa com sessão pronta e aparelho conhecido = apto, com o aparelho explícito', () => {
    const r = resolverAlvo('p1', [conta('a')], 'instagram', VAZIA, APARELHOS);
    expect(r).toMatchObject({ situacao: 'apto', instanceId: 'android-02', aviso: null });
    expect(r.conta?.id).toBe('a');
  });
  it('sem conta ativa no app (outra conta, conta desligada) = sem_conta', () => {
    expect(resolverAlvo('p1', [], 'instagram', VAZIA, APARELHOS).situacao).toBe('sem_conta');
    expect(resolverAlvo('p1', [conta('a', { app_id: 'outlook' })], 'instagram', VAZIA, APARELHOS).situacao).toBe('sem_conta');
    expect(resolverAlvo('p1', [conta('a', { status: 'disabled' })], 'instagram', VAZIA, APARELHOS).situacao).toBe('sem_conta');
  });
  it('conta sem sessão pronta = sem_sessao, mesmo com aparelho escolhido', () => {
    const r = resolverAlvo('p1', [conta('a', { sessao: 'auth_required' })], 'instagram', { conta: '', aparelho: 'android-03' }, APARELHOS);
    expect(r.situacao).toBe('sem_sessao');
    expect(r.instanceId).toBeNull();
  });
  it('aparelho que não existe (nem o da sessão) = sem_aparelho', () => {
    expect(resolverAlvo('p1', [conta('a', { instancia: null })], 'instagram', VAZIA, APARELHOS).situacao).toBe('sem_aparelho');
    expect(resolverAlvo('p1', [conta('a', { instancia: 'android-99' })], 'instagram', VAZIA, APARELHOS).situacao).toBe('sem_aparelho');
    expect(resolverAlvo('p1', [conta('a')], 'instagram', { conta: '', aparelho: 'android-77' }, APARELHOS).situacao).toBe('sem_aparelho');
  });
  it('com duas contas, a de sessão pronta vem primeiro; a escolhida à mão vale; aparelho diferente da sessão só avisa', () => {
    const contas = [conta('a', { sessao: 'auth_required' }), conta('b')];
    expect(resolverAlvo('p1', contas, 'instagram', VAZIA, APARELHOS).conta?.id).toBe('b');
    expect(resolverAlvo('p1', contas, 'instagram', { conta: 'a', aparelho: '' }, APARELHOS).situacao).toBe('sem_sessao');
    const r = resolverAlvo('p1', [conta('b')], 'instagram', { conta: '', aparelho: 'android-03' }, APARELHOS);
    expect(r).toMatchObject({ situacao: 'apto', instanceId: 'android-03' });
    expect(r.aviso).toContain('android-02');
  });
});

describe('prévia, validação e corpo', () => {
  const ok = resolverAlvo('p1', [conta('a')], 'instagram', VAZIA, APARELHOS);
  const semConta = resolverAlvo('p2', [], 'instagram', VAZIA, APARELHOS);
  const semSessao = resolverAlvo('p3', [conta('c', { sessao: 'unknown' })], 'instagram', VAZIA, APARELHOS);

  it('a prévia separa quem vai rodar de quem nasce parado', () => {
    expect(previaDaCapacidade([ok, semConta, semSessao])).toEqual({ solicitados: 3, comConta: 2, comSessao: 1, aptos: 1, paradosNaCriacao: 2 });
    expect(previaDaCapacidade([])).toMatchObject({ solicitados: 0, aptos: 0, paradosNaCriacao: 0 });
  });
  it('lerTeto aceita vírgula e ponto, até 4 casas; texto e negativo não são número', () => {
    expect(lerTeto('2,5')).toBe(2.5);
    expect(lerTeto(' 3.1234 ')).toBe(3.1234);
    for (const t of ['', 'abc', '-1', '1,2,3', '1e3', '2,55555']) expect(lerTeto(t)).toBeNull();
  });
  const base: FormularioDaOperacao = { command: 'comentar', appId: 'instagram', acaoFinal: 'preparar', assunto: '', maxUsd: '2', selecionados: ['p1'], fontes: '', username: '', legenda: '' };
  it('erroDoFormulario: a ordem em que a pessoa preenche, e os limites do contrato', () => {
    expect(erroDoFormulario(base)).toBeNull();
    expect(erroDoFormulario({ ...base, command: '  ' })).toMatch(/objetivo/);
    expect(erroDoFormulario({ ...base, appId: '' })).toMatch(/app/);
    expect(erroDoFormulario({ ...base, selecionados: [] })).toMatch(/persona/);
    expect(erroDoFormulario({ ...base, selecionados: Array.from({ length: 65 }, (_, i) => `p${i}`) })).toMatch(/até 64/);
    expect(erroDoFormulario({ ...base, maxUsd: '' })).toMatch(/teto/);
    expect(erroDoFormulario({ ...base, maxUsd: '0' })).toMatch(/maior que zero/);
    expect(erroDoFormulario({ ...base, maxUsd: '100,5' })).toMatch(/até US\$ 100/);
    expect(erroDoFormulario({ ...base, assunto: 'ab' })).toMatch(/assunto/);
    expect(erroDoFormulario({ ...base, assunto: 'x'.repeat(501) })).toMatch(/assunto/);
    expect(erroDoFormulario({ ...base, assunto: 'abc' })).toBeNull();
  });
  it('fontes e parâmetros fixos (v1.95): só https sem usuário nem query, até 10, valores sem chaves e diferentes entre si', () => {
    expect(fontesDoCampo(' https://a.com/x \n\n https://a.com/x\r\nhttps://b.com ')).toEqual(['https://a.com/x', 'https://b.com']);
    expect(erroDoFormulario({ ...base, fontes: 'https://a.com/x\nhttps://b.com' })).toBeNull();
    for (const ruim of ['http://a.com', 'https://u:p@a.com', 'https://a.com/?q=1', 'não é url']) expect(erroDoFormulario({ ...base, fontes: ruim })).toMatch(/URL https/);
    expect(erroDoFormulario({ ...base, fontes: Array.from({ length: 11 }, (_, i) => `https://a.com/${i}`).join('\n') })).toMatch(/Até 10/);
    expect(erroDoFormulario({ ...base, username: 'perfil', legenda: 'trecho' })).toBeNull();
    expect(erroDoFormulario({ ...base, legenda: 'tem {chave}' })).toMatch(/não leva/);
    expect(erroDoFormulario({ ...base, username: 'x'.repeat(301) })).toMatch(/até 300/);
    expect(erroDoFormulario({ ...base, username: '@Perfil Um', legenda: 'perfilum' })).toMatch(/mesmo valor/);
  });
  it('montarCorpo leva fontes e parametros só quando preenchidos', () => {
    const c = montarCorpo({ ...base, fontes: 'https://a.com/x', username: ' perfil ', legenda: '' }, [ok]);
    expect(c.fontes).toEqual(['https://a.com/x']);
    expect(c.parametros).toEqual({ username: 'perfil' });
    const vazio = montarCorpo(base, [ok]);
    expect('fontes' in vazio).toBe(false);
    expect('parametros' in vazio).toBe(false);
  });
  it('lerOperacao lê os parametros (só texto) e rascunhoDaOperacao copia o pedido sem conta nem aparelho', () => {
    const op = lerOperacao({
      id: 'op-9', command: 'comentar', app_id: 'instagram', acao_final: 'executar', max_usd: 2.5, assunto: 'tema', fontes: ['https://a.com'],
      parametros: { username: 'perfil', caption_contains: 'trecho', outro_campo: 'v', vazio: '', numero: 3 },
      alvos: [{ profile_id: 'p1', account_id: 'a', instance_id: 'android-02' }, { profile_id: 'p1' }, { profile_id: 'p2' }, { run_id: 'r' }],
    })!;
    expect(op.parametros).toEqual({ username: 'perfil', caption_contains: 'trecho', outro_campo: 'v' });
    expect(lerOperacao({ id: 'op-8', parametros: {} })!.parametros).toBeNull();
    expect(rascunhoDaOperacao(op)).toEqual({
      command: 'comentar', appId: 'instagram', acaoFinal: 'executar', assunto: 'tema', maxUsd: '2,5', fontes: 'https://a.com', username: 'perfil', legenda: 'trecho',
      profileIds: ['p1', 'p2'], parametrosNaoCopiados: ['outro_campo'],
    });
  });
  it('montarCorpo: instance_id e account_id explícitos só onde existem; assunto vazio não vai', () => {
    expect(montarCorpo({ ...base, command: '  comentar  ', maxUsd: '2,5' }, [ok, semConta])).toEqual({
      command: 'comentar', app_id: 'instagram', acao_final: 'preparar', max_usd: 2.5,
      alvos: [{ profile_id: 'p1', account_id: 'a', instance_id: 'android-02' }, { profile_id: 'p2' }],
    });
    expect(montarCorpo({ ...base, assunto: ' tema ' }, [ok]).assunto).toBe('tema');
  });
});

let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

beforeAll(() => installBrowserStubs());
beforeEach(() => {
  backend = new FakeBackend();
  backend.install();
  backend.on('GET', /^\/api\/apps$/, () => json([{ ...APPS[0]!, id: 'instagram', name: 'Instagram' }, APPS[1]]));
  backend.on('GET', /^\/api\/instances$/, () => json([makeInstance(2), makeInstance(3)]));
  backend.on('GET', /^\/api\/instagram\/profiles$/, () => json([
    { id: 'p1', persona_name: 'Ana', display_name: null }, { id: 'p2', persona_name: 'Bia', display_name: null }, { id: 'p3', persona_name: 'Caio', display_name: null },
  ]));
  backend.on('GET', /^\/api\/instagram\/profiles\/p1\/accounts$/, () => json([conta('ana')]));
  backend.on('GET', /^\/api\/instagram\/profiles\/p2\/accounts$/, () => json([conta('bia', { sessao: 'auth_required' })]));
  backend.on('GET', /^\/api\/instagram\/profiles\/p3\/accounts$/, () => json([]));
  useUiStore.setState({ rota: { ...useUiStore.getState().rota, tela: 'operacoes', segmentos: ['nova'], query: {} } });
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  useUiStore.setState({ rota: { ...useUiStore.getState().rota, segmentos: [] } });
});

const abrir = async () => {
  await act(async () => root.render(<><OperacaoPage /><ConfirmHost /></>));
  await waitFor(() => expect(container.querySelector('form')).not.toBeNull());
};
const campo = <E extends HTMLElement>(rotulo: RegExp): E => {
  const l = Array.from(container.querySelectorAll('label')).find((x) => rotulo.test(x.textContent ?? ''));
  if (!l) throw new Error(`sem campo ${String(rotulo)}`);
  return document.getElementById(l.htmlFor) as E;
};
const caixa = (id: string) => container.querySelector(`li[data-persona="${id}"] input[type="checkbox"]`) as HTMLInputElement;
const preencher = async () => {
  await setValue(campo<HTMLTextAreaElement>(/^Objetivo/), 'Comentar na última publicação');
  await setValue(campo<HTMLInputElement>(/^Teto de custo/), '2,5');
  for (const p of ['p1', 'p2', 'p3']) await click(caixa(p));
  await waitFor(() => expect(container.querySelectorAll('li[data-persona] [data-situacao]')).toHaveLength(3));
};
const botao = () => byRole('button', /^Criar a operação/, container);

describe('a tela "Nova operação"', () => {
  it('só envia com tudo preenchido; a prévia mostra quem roda e quem nasce parado, com a regra de cada um', async () => {
    await abrir();
    expect(text(container)).toContain('Nova operação');
    expect((campo<HTMLSelectElement>(/^App/)).value).toBe('instagram');
    expect(botao().getAttribute('aria-disabled')).toBe('true');
    await preencher();
    const previa = container.querySelector('[data-previa]')!;
    expect(text(previa)).toMatch(/3Escolhidas/);
    expect(text(previa)).toMatch(/2Com conta/);
    expect(text(previa)).toMatch(/1Com aparelho \(vão rodar\)/);
    expect(text(previa)).toMatch(/2Nascem paradas/);
    expect(container.querySelector('li[data-persona="p1"] [data-situacao]')?.getAttribute('data-situacao')).toBe('apto');
    expect(container.querySelector('li[data-persona="p2"] [data-situacao]')?.getAttribute('data-situacao')).toBe('sem_sessao');
    expect(container.querySelector('li[data-persona="p3"] [data-situacao]')?.getAttribute('data-situacao')).toBe('sem_conta');
    expect(text(container.querySelector('li[data-persona="p1"]')!)).toContain('Vai rodar em android-02');
    expect(botao().getAttribute('aria-disabled')).not.toBe('true');
  });

  it('confirma com o teto em destaque e envia o corpo com instance_id explícito e a chave; depois abre a operação criada', async () => {
    backend.on('POST', /^\/api\/operacoes$/, () => json({ id: 'op-nova', command: 'x', alvos: [] }, 201));
    await abrir();
    await preencher();
    await click(botao());
    const d = await waitFor(() => byRole('dialog', /Criar a operação\?/));
    expect(text(d.querySelector('[data-teto-em-destaque]')!)).toContain('Teto de custo: US$ 2,50');
    expect(text(d)).toContain('3 personas: 1 vai rodar, 2 nascem parados');
    expect(text(d)).toContain('Só preparar');
    expect(backend.callsTo('POST', /operacoes$/)).toHaveLength(0);                          // nada sai antes da confirmação
    await click(byRole('button', /^Criar a operação$/, d));
    await waitFor(() => expect(backend.callsTo('POST', /operacoes$/)).toHaveLength(1));
    const corpo = backend.callsTo('POST', /operacoes$/)[0]!.body as Record<string, unknown>;
    expect(corpo).toMatchObject({
      command: 'Comentar na última publicação', app_id: 'instagram', acao_final: 'preparar', max_usd: 2.5,
      alvos: [{ profile_id: 'p1', account_id: 'ana', instance_id: 'android-02' }, { profile_id: 'p2', account_id: 'bia' }, { profile_id: 'p3' }],
    });
    expect(typeof corpo.idempotency_key).toBe('string');
    await waitFor(() => expect(useUiStore.getState().rota.segmentos).toEqual(['op-nova']));
  });

  it('"Voltar" na confirmação não cria nada', async () => {
    await abrir();
    await preencher();
    await click(botao());
    const d = await waitFor(() => byRole('dialog', /Criar a operação\?/));
    await click(byRole('button', /^Voltar$/, d));
    expect(backend.callsTo('POST', /operacoes$/)).toHaveLength(0);
    expect(useUiStore.getState().rota.segmentos).toEqual(['nova']);
  });

  it('recusa do servidor aparece na tela e a nova tentativa do mesmo corpo reaproveita a chave', async () => {
    let n = 0;
    backend.on('POST', /^\/api\/operacoes$/, () => (++n === 1 ? apiError(409, 'credencial_no_comando', 'O objetivo parece conter uma credencial.') : json({ id: 'op-2', alvos: [] }, 201)));
    await abrir();
    await preencher();
    for (let i = 0; i < 2; i++) {
      await click(botao());
      const d = await waitFor(() => byRole('dialog', /Criar a operação\?/));
      await click(byRole('button', /^Criar a operação$/, d));
      await waitFor(() => expect(backend.callsTo('POST', /operacoes$/)).toHaveLength(i + 1));
      if (i === 0) await waitFor(() => expect(text(container)).toContain('parece conter uma credencial'));
    }
    const [a, b] = backend.callsTo('POST', /operacoes$/).map((c) => (c.body as { idempotency_key: string }).idempotency_key);
    expect(a).toBe(b);
  });

  it('escolher o aparelho e a conta à mão vai no corpo; só aparecem os aparelhos que existem', async () => {
    backend.on('POST', /^\/api\/operacoes$/, () => json({ id: 'op-3', alvos: [] }, 201));
    await abrir();
    await setValue(campo<HTMLTextAreaElement>(/^Objetivo/), 'Comentar');
    await setValue(campo<HTMLInputElement>(/^Teto de custo/), '1');
    await click(caixa('p1'));
    const li = await waitFor(() => { const x = container.querySelector('li[data-persona="p1"] select'); if (!x) throw new Error('sem selects'); return container.querySelector('li[data-persona="p1"]')!; });
    const aparelho = Array.from(li.querySelectorAll('select')).find((s) => s.id && /Aparelho/.test(li.querySelector(`label[for="${s.id}"]`)?.textContent ?? ''))!;
    expect(Array.from(aparelho.options).map((o) => o.value)).toEqual(['', 'android-02', 'android-03']);
    await setValue(aparelho, 'android-03');
    expect(text(li)).toContain('A sessão desta conta está em android-02, não em android-03');
    await click(botao());
    const d = await waitFor(() => byRole('dialog', /Criar a operação\?/));
    await click(byRole('button', /^Criar a operação$/, d));
    await waitFor(() => expect(backend.callsTo('POST', /operacoes$/)).toHaveLength(1));
    expect((backend.callsTo('POST', /operacoes$/)[0]!.body as { alvos: unknown[] }).alvos).toEqual([{ profile_id: 'p1', account_id: 'ana', instance_id: 'android-03' }]);
  });

  it('trocar o app esquece a conta escolhida à mão (a conta de um app não serve a outro)', async () => {
    await abrir();
    await click(caixa('p1'));
    await waitFor(() => expect(container.querySelector('li[data-persona="p1"] [data-situacao="apto"]')).not.toBeNull());
    await setValue(campo<HTMLSelectElement>(/^App/), 'notes');
    await waitFor(() => expect(container.querySelector('li[data-persona="p1"] [data-situacao="sem_conta"]')).not.toBeNull());
  });

  it('personas que não carregam: erro com "tentar de novo", nunca formulário vazio', async () => {
    backend.on('GET', /^\/api\/instagram\/profiles$/, () => apiError(500, 'falha', 'sem lista'));
    await act(async () => root.render(<><OperacaoPage /><ConfirmHost /></>));
    await waitFor(() => expect(text(container)).toContain('Tentar de novo'));
    expect(container.querySelector('form')).toBeNull();
  });
});

describe('fontes, parâmetros fixos e "Repetir como nova"', () => {
  it('fonte inválida bloqueia o envio; fontes e parâmetros preenchidos vão no corpo', async () => {
    backend.on('POST', /^\/api\/operacoes$/, () => json({ id: 'op-4', alvos: [] }, 201));
    await abrir();
    await preencher();
    await setValue(campo<HTMLTextAreaElement>(/^Fontes públicas/), 'http://inseguro.com');
    expect(botao().getAttribute('aria-disabled')).toBe('true');
    await setValue(campo<HTMLTextAreaElement>(/^Fontes públicas/), 'https://a.com/lancamento\nhttps://b.com/nota');
    await setValue(campo<HTMLInputElement>(/^Perfil alvo/), 'perfil_alvo');
    await setValue(campo<HTMLInputElement>(/^Trecho da legenda/), 'lançamento');
    expect(text(container)).toContain('Fixa o alvo quando o comando não diz.');
    await click(botao());
    const d = await waitFor(() => byRole('dialog', /Criar a operação\?/));
    await click(byRole('button', /^Criar a operação$/, d));
    await waitFor(() => expect(backend.callsTo('POST', /operacoes$/)).toHaveLength(1));
    expect(backend.callsTo('POST', /operacoes$/)[0]!.body).toMatchObject({
      fontes: ['https://a.com/lancamento', 'https://b.com/nota'], parametros: { username: 'perfil_alvo', caption_contains: 'lançamento' },
    });
  });

  it('"Repetir como nova" na gaveta abre o formulário preenchido; persona que sumiu fica de fora e a conta se resolve de novo', async () => {
    limparRascunho();
    backend.on('GET', /^\/api\/operacoes\/op-1$/, () => json({
      id: 'op-1', command: 'Comentar na publicação', app_id: 'instagram', acao_final: 'preparar', max_usd: 1.5, assunto: 'tema do lote',
      fontes: ['https://a.com/x'], parametros: { username: 'perfil_alvo', caption_contains: 'trecho', extra: 'v' },
      alvos: [{ profile_id: 'p1', account_id: 'ana', instance_id: 'android-03' }, { profile_id: 'p2' }, { profile_id: 'sumiu' }],
    }));
    useUiStore.setState({ rota: { ...useUiStore.getState().rota, tela: 'operacoes', segmentos: ['op-1'] } });
    await act(async () => root.render(<><OperacaoPage /><ConfirmHost /></>));
    await click(await waitFor(() => byRole('button', /^Repetir como nova$/, container)));
    expect(useUiStore.getState().rota.segmentos).toEqual(['nova']);
    await waitFor(() => expect(container.querySelector('form')).not.toBeNull());
    expect(campo<HTMLTextAreaElement>(/^Objetivo/).value).toBe('Comentar na publicação');
    expect(campo<HTMLInputElement>(/^Teto de custo/).value).toBe('1,5');
    expect(campo<HTMLInputElement>(/^Assunto/).value).toBe('tema do lote');
    expect(campo<HTMLTextAreaElement>(/^Fontes públicas/).value).toBe('https://a.com/x');
    expect(campo<HTMLInputElement>(/^Perfil alvo/).value).toBe('perfil_alvo');
    expect(caixa('p1').checked).toBe(true);
    expect(caixa('p2').checked).toBe(true);
    expect(caixa('p3').checked).toBe(false);
    const tudo = text(container);
    expect(tudo).toContain('Copiado de uma operação anterior');
    expect(tudo).toContain('1 persona não existe mais e ficou de fora');
    expect(tudo).toContain('não foram copiados: extra');
    // conta e aparelho são resolvidos de novo: o aparelho do alvo antigo (android-03) não foi copiado
    await waitFor(() => expect(text(container.querySelector('li[data-persona="p1"]')!)).toContain('Vai rodar em android-02'));
    expect(lerRascunho()).toBeNull();                                                       // vale para uma abertura só
  });
});

describe('a lista leva à criação', () => {
  it('"Nova operação" abre o formulário; com a rota ausente (exemplo) o botão fica desabilitado com o motivo', async () => {
    useUiStore.setState({ rota: { ...useUiStore.getState().rota, tela: 'operacoes', segmentos: [] } });
    backend.on('GET', /^\/api\/operacoes$/, () => json({ items: [] }));
    await act(async () => root.render(<><OperacaoPage /><ConfirmHost /></>));
    await click(await waitFor(() => byRole('button', /^Nova operação$/, container)));
    expect(useUiStore.getState().rota.segmentos).toEqual(['nova']);

    useUiStore.setState({ rota: { ...useUiStore.getState().rota, tela: 'operacoes', segmentos: [] } });
    backend.on('GET', /^\/api\/operacoes$/, () => apiError(404, 'nao_encontrado', 'não existe'));
    await act(async () => { root.unmount(); root = createRoot(container); root.render(<><OperacaoPage /><ConfirmHost /></>); });
    const b = await waitFor(() => byRole('button', /^Nova operação — indisponível: O central ainda não oferece o módulo de operações/, container));
    expect(b.getAttribute('aria-disabled')).toBe('true');
  });
});

describe('31.224/31.225: o motivo da recusa dos parâmetros vai no campo certo', () => {
  const recusa422 = (motivo: string, message: string, extra: Record<string, unknown> = {}) =>
    json({ detail: { code: 'pedido_invalido', message, motivo, ...extra } }, 422);
  const enviar = async () => {
    await click(botao());
    const d = await waitFor(() => byRole('dialog', /Criar a operação\?/));
    await click(byRole('button', /^Criar a operação$/, d));
  };
  const erroDe = (rotulo: RegExp) => {
    const l = Array.from(container.querySelectorAll('label')).find((x) => rotulo.test(x.textContent ?? ''))!;
    return l.closest('div[class]')?.parentElement?.querySelector('[role="alert"]') ?? null;
  };

  it('recusaDeParametros acha o campo pela posicao (de 1, na ordem enviada) ou por campo; o resto é genérico', () => {
    const e = (status: number, code: string, detail: Record<string, unknown>) => new ApiError(status, code, String(detail.message ?? 'x'), detail);
    const ENVIADAS = ['username', 'caption_contains'];
    expect(recusaDeParametros(e(422, 'pedido_invalido', { message: 'Sem arroba.', motivo: 'username_com_arroba', campo: 'username', posicao: 1 }), ENVIADAS))
      .toEqual({ chave: 'username', campo: 'username', motivo: 'Sem arroba.' });
    // o nome da chave desconhecida nunca volta: a posição aponta a do nosso corpo, e os aceitos entram na frase
    expect(recusaDeParametros(e(422, 'pedido_invalido', { message: 'Parâmetro desconhecido.', motivo: 'parametro_desconhecido', posicao: 2, aceitos: ['username', 'post_id'] }), ENVIADAS))
      .toEqual({ chave: 'caption_contains', campo: 'caption_contains', motivo: 'Parâmetro desconhecido. Este app aceita: username, post_id.' });
    // sem campo e sem posição que case: aviso sem campo
    expect(recusaDeParametros(e(422, 'pedido_invalido', { message: 'Parâmetro desconhecido.', motivo: 'parametro_desconhecido', posicao: 9 }), ENVIADAS))
      .toEqual({ chave: null, campo: null, motivo: 'Parâmetro desconhecido.' });
    expect(recusaDeParametros(e(422, 'pedido_invalido', { message: 'm', motivo: 'username_com_espaco', posicao: 0 }), ENVIADAS)).toMatchObject({ chave: null, campo: null });
    // 422 genérico (só code e message), motivo de outra classe, outro status e erro qualquer: nada a mostrar por campo
    expect(recusaDeParametros(e(422, 'validation_error', { message: 'Valor longo demais.' }), ENVIADAS)).toBeNull();
    expect(recusaDeParametros(e(422, 'pedido_invalido', { message: 'm', motivo: 'outro' }), ENVIADAS)).toBeNull();
    expect(recusaDeParametros(e(409, 'pedido_invalido', { message: 'm', motivo: 'username_com_arroba' }), ENVIADAS)).toBeNull();
    expect(recusaDeParametros(new Error('x'), ENVIADAS)).toBeNull();
  });

  it('31.227: o motivo é por parâmetro declarado (_longo, _com_arroba de outro nome); campo que o formulário não oferece vira aviso sem campo', async () => {
    const e = (detail: Record<string, unknown>) => new ApiError(422, 'pedido_invalido', String(detail.message), detail);
    expect(recusaDeParametros(e({ message: 'O perfil alvo vai até 30 caracteres.', motivo: 'username_longo', campo: 'username', posicao: 1, max: 30 }), ['username']))
      .toEqual({ chave: 'username', campo: 'username', motivo: 'O perfil alvo vai até 30 caracteres.' });
    expect(recusaDeParametros(e({ message: 'O autor do post não leva @.', motivo: 'post_author_com_arroba', campo: 'post_author', posicao: 2 }), ['username', 'caption_contains']))
      .toEqual({ chave: 'post_author', campo: null, motivo: 'O autor do post não leva @.' });
    expect(recusaDeParametros(e({ message: 'm', motivo: 'Username_com_arroba', posicao: 1 }), ['username'])).toBeNull();   // fora do formato do motivo
    backend.on('POST', /^\/api\/operacoes$/, () => recusa422('username_longo', 'O perfil alvo vai até 30 caracteres.', { campo: 'username', posicao: 1, max: 30 }));
    await abrir();
    await preencher();
    await setValue(campo<HTMLInputElement>(/^Perfil alvo/), 'nasa');
    await enviar();
    await waitFor(() => expect(text(erroDe(/^Perfil alvo/)!)).toContain('vai até 30 caracteres'));
  });

  it('o 422 do username (posicao 1) aparece no campo Perfil alvo e não no aviso geral; mexer no campo limpa só ele', async () => {
    backend.on('POST', /^\/api\/operacoes$/, () => recusa422('username_com_arroba', 'O perfil alvo não leva @.', { campo: 'username', posicao: 1 }));
    await abrir();
    await preencher();
    await setValue(campo<HTMLInputElement>(/^Perfil alvo/), 'nasa');
    await enviar();
    await waitFor(() => expect(text(erroDe(/^Perfil alvo/)!)).toContain('O perfil alvo não leva @.'));
    expect(campo<HTMLInputElement>(/^Perfil alvo/).getAttribute('aria-invalid')).toBe('true');
    expect(erroDe(/^Trecho da legenda/)).toBeNull();
    expect(text(container)).not.toContain('Revise os campos');
    expect((backend.callsTo('POST', /operacoes$/)[0]!.body as { parametros: Record<string, string> }).parametros).toEqual({ username: 'nasa' });
    await setValue(campo<HTMLInputElement>(/^Perfil alvo/), 'nasa2');
    await waitFor(() => expect(erroDe(/^Perfil alvo/)).toBeNull());
  });

  it('parametro_desconhecido na posição 2 cai no Trecho da legenda, com os parâmetros aceitos; a nova recusa troca a antiga', async () => {
    let n = 0;
    backend.on('POST', /^\/api\/operacoes$/, () => (++n === 1
      ? recusa422('parametro_desconhecido', 'Este app não conhece o parâmetro.', { posicao: 2, aceitos: ['username'] })
      : recusa422('username_com_espaco', 'O perfil alvo não leva espaço.', { campo: 'username', posicao: 1 })));
    await abrir();
    await preencher();
    await setValue(campo<HTMLInputElement>(/^Perfil alvo/), 'nasa');
    await setValue(campo<HTMLInputElement>(/^Trecho da legenda/), 'foto');
    await enviar();
    await waitFor(() => expect(text(erroDe(/^Trecho da legenda/)!)).toContain('Este app aceita: username.'));
    await enviar();
    await waitFor(() => expect(text(erroDe(/^Perfil alvo/)!)).toContain('não leva espaço'));
    expect(erroDe(/^Trecho da legenda/)).toBeNull();
  });

  it('um 422 genérico (sem motivo) mostra a frase do servidor no topo do formulário, sem marcar campo', async () => {
    backend.on('POST', /^\/api\/operacoes$/, () => json({ detail: { code: 'validation_error', message: 'O valor do parâmetro é longo demais.' } }, 422));
    await abrir();
    await preencher();
    await enviar();
    await waitFor(() => expect(text(container)).toContain('O valor do parâmetro é longo demais.'));
    expect(erroDe(/^Perfil alvo/)).toBeNull();
    expect(campo<HTMLInputElement>(/^Perfil alvo/).getAttribute('aria-invalid')).not.toBe('true');
  });
});
