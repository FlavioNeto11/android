// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { FakeBackend, byRole, click, installBrowserStubs, json, setValue, text, waitFor } from '../../test/harness';
import { useUiStore } from '../../store/ui';
import { AprendizadoPage } from './AprendizadoPage';
import { assuntoDoItem, assuntosDasLicoes, buscarNoLivro, canonizarAssunto, filtrarPorAssunto, semAcento } from './assunto';
import { lerFiltroDoEndereco, queryDoFiltro } from './filtroNoEndereco';
import type { EntradaDoLivro } from './model';

/**
 * 31.209: o Livro com busca e filtro por assunto das lições, e o assunto visível na lição (adendo v1.113, Aprendizado 31.200, migração 128).
 * Prova `simulated`: servidor falso no formato combinado (o código do Aprendizado ainda não está implantado). O filtro `?assunto=` é do
 * servidor (igualdade exata, ele canoniza); a lista de assuntos e a busca por trecho são do painel.
 */

function entrada(over: Partial<EntradaDoLivro>): EntradaDoLivro {
  return {
    kind: 'licao', ref: 'li-1', state: 'candidate', native_status: null, title: 'Lição', app: null,
    origin: 'execucao', side_effect: false, human_origin: false, requires_owner: false, created_at: '2026-10-06T10:00:00Z',
    state_at: '2026-10-06T10:00:00Z', last_used_at: null, uses: 0, evidence: { for: 0, against: 0 }, count: null,
    detail: null, acoes: [], por_que_nao_publica: null, ...over,
  };
}
const L1 = entrada({ ref: 'li-1', title: 'Comente citando o preço', assunto: 'festival de inverno' });
const L2 = entrada({ ref: 'li-2', title: 'Fale da programação', assunto: 'festival de inverno' });
const L3 = entrada({ ref: 'li-3', title: 'Agradeça pela coleção nova', assunto: 'lancamento da colecao' });
const L4 = entrada({ ref: 'li-4', title: 'Role a lista antes de procurar', assunto: null });
const FLUXO = entrada({ kind: 'fluxo', ref: 'f-1', title: 'Abrir o perfil', state: 'published', native_status: 'active' });

describe('o assunto (puro)', () => {
  it('semAcento e canonizarAssunto: a forma que o servidor compara', () => {
    expect(semAcento('  Lançamento   da COLEÇÃO ')).toBe('lancamento da colecao');
    expect(canonizarAssunto('Festival de Inverno!')).toBe('festival de inverno');
    expect(canonizarAssunto('!!!')).toBe('');
    expect(canonizarAssunto('a'.repeat(200))).toHaveLength(120);
  });
  it('assuntoDoItem: texto ou null; ausente (backend anterior) também é null', () => {
    expect(assuntoDoItem(L1)).toBe('festival de inverno');
    expect(assuntoDoItem(L4)).toBeNull();
    expect(assuntoDoItem(entrada({}))).toBeNull();
    expect(assuntoDoItem(entrada({ assunto: '   ' }))).toBeNull();
  });
  it('assuntosDasLicoes: distintos, com a conta, o mais comum primeiro; sem assunto não entra', () => {
    expect(assuntosDasLicoes([L1, L2, L3, L4, FLUXO])).toEqual([{ assunto: 'festival de inverno', n: 2 }, { assunto: 'lancamento da colecao', n: 1 }]);
    expect(assuntosDasLicoes([])).toEqual([]);
  });
  it('filtrarPorAssunto: igualdade exata depois de canonizar; sem filtro, tudo; filtro sem forma canônica não acha nada', () => {
    expect(filtrarPorAssunto([L1, L2, L3, L4], 'Festival de Inverno!').map((e) => e.ref)).toEqual(['li-1', 'li-2']);
    expect(filtrarPorAssunto([L1, L2, L3, L4], 'festival').map((e) => e.ref)).toEqual([]);          // não é busca por trecho
    expect(filtrarPorAssunto([L1, L4], undefined)).toHaveLength(2);
    expect(filtrarPorAssunto([L1, L4], '!!!')).toEqual([]);
  });
  it('buscarNoLivro: todas as palavras, sem caixa nem acento, no título, assunto, etapa e nome do app', () => {
    const itens = [L1, L2, L3, L4, entrada({ kind: 'receita', ref: '9', title: 'abc', etapa: 'Digitar a mensagem', app_nome: 'Instagram' })];
    expect(buscarNoLivro(itens, 'COLECAO', undefined).map((e) => e.ref)).toEqual(['li-3']);          // assunto e título (sem acento)
    expect(buscarNoLivro(itens, 'festival preço', undefined).map((e) => e.ref)).toEqual(['li-1']);   // as duas palavras, no mesmo item
    expect(buscarNoLivro(itens, 'digitar instagram', undefined).map((e) => e.ref)).toEqual(['9']);
    expect(buscarNoLivro(itens, '   ', undefined)).toHaveLength(5);
    expect(buscarNoLivro(itens, 'nada disso', undefined)).toEqual([]);
  });
});

describe('o endereço', () => {
  it('lê e escreve o assunto; acima de 200 caracteres vira "sem filtro"; sem valor sai do endereço', () => {
    expect(lerFiltroDoEndereco({ assunto: ' festival de inverno ' }).assunto).toBe('festival de inverno');
    expect(lerFiltroDoEndereco({ assunto: 'x'.repeat(201) }).assunto).toBeUndefined();
    expect(lerFiltroDoEndereco({ assunto: '' }).assunto).toBeUndefined();
    expect(queryDoFiltro({ assunto: 'festival de inverno' })).toEqual({ assunto: 'festival de inverno' });
    expect(queryDoFiltro({ assunto: undefined })).toEqual({ assunto: undefined });
  });
});

describe('a tela', () => {
  let root: Root;
  let container: HTMLElement;
  let backend: FakeBackend;
  const consultas = () => backend.callsTo('GET', /^\/api\/aprendizado$/);
  const ultima = () => consultas().at(-1)?.query.toString() ?? '';
  const query = () => useUiStore.getState().rota.query;
  const refs = () => Array.from(container.querySelectorAll('[data-item]')).map((i) => i.getAttribute('data-item'));

  beforeEach(() => {
    installBrowserStubs();
    window.localStorage.clear();
    backend = new FakeBackend();
    backend.install();
    for (const rota of ['pendentes', 'revisar', 'intencao']) backend.on('GET', new RegExp(`^/api/aprendizado/${rota}$`), () => json({ itens: [], total: 0 }));
    backend.on('GET', /^\/api\/aprendizado\/apps$/, () => json({ apps: [], sem_eixo: null, nao_resolvido: null }));
    // O servidor falso faz o que o verdadeiro faz: filtra por assunto exato quando o parâmetro vem.
    backend.on('GET', /^\/api\/aprendizado$/, (c) => {
      const a = c.query.get('assunto');
      const itens = [L1, L2, L3, L4, FLUXO].filter((e) => !a || (e.assunto ?? '') === canonizarAssunto(a));
      return json({ itens, total: itens.length, contagem: {} });
    });
  });
  afterEach(async () => {
    if (root) await act(async () => root.unmount());
    container?.remove();
    useUiStore.setState({ rota: { ...useUiStore.getState().rota, query: {} } });
  });

  async function montar(q: Record<string, string> = {}): Promise<void> {
    useUiStore.setState({ rota: { ...useUiStore.getState().rota, tela: 'aprendizado', segmentos: [], query: { aba: 'aprendido', ...q } } });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => root.render(<AprendizadoPage />));
    await waitFor(() => expect(container.querySelector('[data-item]')).not.toBeNull());
  }

  it('o assunto aparece na lição; só quem tem assunto o mostra', async () => {
    await montar();
    expect(text(container.querySelector('[data-item="licao:li-1"] [data-assunto]')!)).toBe('Assunto: festival de inverno');
    expect(container.querySelector('[data-item="licao:li-4"] [data-assunto]')).toBeNull();
    expect(container.querySelector('[data-item="fluxo:f-1"] [data-assunto]')).toBeNull();
  });

  it('o campo Assunto lista os assuntos vistos; escolher manda ?assunto= ao servidor, escreve o endereço e filtra; "Todos" volta', async () => {
    await montar();
    const campo = byRole('combobox', /^Assunto/, container) as HTMLSelectElement;
    expect(Array.from(campo.options).map((o) => o.value)).toEqual(['', 'festival de inverno', 'lancamento da colecao']);
    await setValue(campo, 'lancamento da colecao');
    await waitFor(() => expect(query().assunto).toBe('lancamento da colecao'));
    await waitFor(() => expect(ultima()).toContain('assunto=lancamento+da+colecao'));
    await waitFor(() => expect(refs()).toEqual(['licao:li-3']));
    // os outros assuntos continuam na escolha, mesmo com a lista já reduzida
    expect(Array.from((byRole('combobox', /^Assunto/, container) as HTMLSelectElement).options).map((o) => o.value)).toContain('festival de inverno');
    await setValue(byRole('combobox', /^Assunto/, container) as HTMLSelectElement, '');
    await waitFor(() => expect(query().assunto).toBeUndefined());
    await waitFor(() => expect(refs()).toHaveLength(5));
    expect(ultima()).not.toContain('assunto=');
  });

  it('clicar no assunto da lição filtra por ele', async () => {
    await montar();
    await click(byRole('button', /^festival de inverno$/, container.querySelector('[data-item="licao:li-1"]') as HTMLElement));
    await waitFor(() => expect(query().assunto).toBe('festival de inverno'));
    await waitFor(() => expect(refs()).toEqual(['licao:li-1', 'licao:li-2']));
  });

  it('um link com assunto já chega filtrado e o campo mostra o assunto do link', async () => {
    await montar({ assunto: 'Festival de Inverno!' });
    expect(consultas()[0]!.query.get('assunto')).toBe('Festival de Inverno!');                  // quem canoniza é o servidor
    await waitFor(() => expect((byRole('combobox', /^Assunto/, container) as HTMLSelectElement).value).toBe('Festival de Inverno!'));
    expect(refs()).toEqual(['licao:li-1', 'licao:li-2']);
  });

  it('a busca por trecho é do painel: filtra a lista carregada sem consultar de novo, diz quantos achou e vai para o endereço', async () => {
    await montar();
    const antes = consultas().length;
    await setValue(byRole('textbox', /^Buscar no livro/, container) as HTMLInputElement, 'COLEÇÃO');
    await waitFor(() => expect(refs()).toEqual(['licao:li-3']));
    expect(query().busca).toBe('COLEÇÃO');
    expect(consultas()).toHaveLength(antes);
    expect(text(container.querySelector('[data-busca]')!)).toContain('1 de 5 itens com “COLEÇÃO”');
    await setValue(byRole('textbox', /^Buscar no livro/, container) as HTMLInputElement, 'zzz');
    await waitFor(() => expect(text(container)).toContain('Nenhum item com esta busca'));
    await setValue(byRole('textbox', /^Buscar no livro/, container) as HTMLInputElement, '');
    await waitFor(() => expect(refs()).toHaveLength(5));
    expect(query().busca).toBeUndefined();
  });

  it('a busca avisa quando olha só parte do livro (total maior que o carregado)', async () => {
    backend.on('GET', /^\/api\/aprendizado$/, () => json({ itens: [L1, L3], total: 40, contagem: {} }));
    await montar({ busca: 'festival' });
    expect(text(container.querySelector('[data-busca]')!)).toContain('A busca olha os 2 itens carregados, de 40');
  });

  it('sem nenhuma lição com assunto, o campo Assunto nem aparece', async () => {
    backend.on('GET', /^\/api\/aprendizado$/, () => json({ itens: [L4, FLUXO], total: 2, contagem: {} }));
    await montar();
    expect(container.querySelector('[data-item]')).not.toBeNull();
    expect(() => byRole('combobox', /^Assunto/, container)).toThrow();
  });
});
