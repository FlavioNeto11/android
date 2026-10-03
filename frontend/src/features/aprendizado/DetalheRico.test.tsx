// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { FakeBackend, apiError, click, installBrowserStubs, json, openDetails, text, waitFor } from '../../test/harness';
import { useAppStore } from '../../store/app';
import { useUiStore } from '../../store/ui';
import { AprendizadoPage } from './AprendizadoPage';
import { DetalheRico, hrefDoItem, passoDaTransicao } from './DetalheRico';
import { alvoLegivel, destinoDaRelacao, metaDeSaude, rotuloDaExecucao, textoDaVariante, textoDoMotivo, valorDaDimensao } from './detalhe';
import { itemDoLink } from './AprendidoTab';
import type {
  ConteudoDaReceita, ConteudoDoItem, DetalheDoLivro, DimensaoDeSaude, EntradaDoLivro, EvidenciaDoLivro, SaudeDoItem,
  VersaoDoItem,
} from './model';

/**
 * 30.16: o detalhe rico do item do Livro (seções do §11.2) contra os contratos dos adendos v0.50 (conteúdo), v0.51
 * (versão), v0.52 (saúde) e v0.53 (relações). Prova `simulated`: nenhum backend real foi chamado.
 */

function entrada(over: Partial<EntradaDoLivro> = {}): EntradaDoLivro {
  return {
    kind: 'receita', ref: '12', state: 'published', native_status: 'active', title: 'Enviar oi para o contato',
    app: 'com.whatsapp', origin: 'execucao', side_effect: true, human_origin: false, requires_owner: true,
    created_at: '2026-09-28T10:00:00Z', state_at: '2026-09-28T10:00:00Z', last_used_at: '2026-10-01T10:00:00Z', uses: 10,
    evidence: { for: 3, against: 1 }, count: null, detail: null, acoes: [], por_que_nao_publica: null, saude: null, ...over,
  };
}

const RECEITA: ConteudoDaReceita = {
  tipo: 'receita',
  identidade: { app: 'com.whatsapp', app_version: '2.24.1', assinatura: null, variante: null, step_key: 'enviar', step_hash: 'abc', versao: 3, estado: 'active' },
  acoes: [
    { indice: 0, ferramenta: 'tap', commit: false, alvo: [{ tipo: 'rid+text', rid: 'com.whatsapp:id/contato', texto: 'Marina' }], parametros: [], segredo: false },
    { indice: 1, ferramenta: 'type_text', commit: false, alvo: [{ tipo: 'rid', rid: 'com.whatsapp:id/entry' }], parametros: ['mensagem'], segredo: false,
      digita: { limpa_antes: true, enter: false, so_parametro: true } },
    { indice: 2, ferramenta: 'tap', commit: true, alvo: [{ tipo: 'desc', desc: 'Enviar' }], parametros: [], segredo: false },
    { indice: 3, ferramenta: 'type_secret', commit: false, alvo: [], parametros: [], segredo: true },
  ],
  efeito: { externo: true, acoes_commit: [2] },
  capability: { nomes: ['enviar_mensagem', 'responder'], ambigua: true, fonte: 'mesmo_step_hash' },
  origem: { tipo: 'execucao', step_id: 's-1', run_id: 'r-20261001-abc' },
  uso: { replay_ok: 8, replay_fail: 2, consecutive_fail: 1, last_used_at: '2026-10-01T10:00:00Z' },
  sombra: { shadow_agree: 4, shadow_total: 5 },
  substitui: { id: 9, versao: 2, estado: 'superseded' },
  substituida_por: null,
};

const SAUDE: SaudeDoItem = {
  rotulo: 'degradando',
  motivos: [{ codigo: 'eficacia_abaixo_do_minimo', dimensao: 'eficacia', valor: 0.6, limite: 0.8, detalhe: '10 usos' }],
  dimensoes: [
    { nome: 'uso', estado: 'medida', valor: 10, amostra: 10, fonte: 'recipes.replay_ok+replay_fail' },
    { nome: 'eficacia', estado: 'medida', valor: 0.6, amostra: 10, fonte: 'recipes.replay_ok+replay_fail' },
    { nome: 'versao', estado: 'desconhecida', valor: null, amostra: null, fonte: 'versão do app (§7)' },
    { nome: 'intervencao_humana', estado: 'desconhecida', valor: null, amostra: null, fonte: 'learning_signals' },
  ],
};

const VERSAO: VersaoDoItem = {
  estado: 'nao_testado', app: 'com.whatsapp', app_version: '2.24.1',
  vivas: [{ versao: '2.24.1', aparelhos: 2 }, { versao: '2.25.0', aparelhos: 1 }],
  nao_testada_em: ['2.25.0'],
  por_versao: [
    { versao: '2.24.1', viva: true, aparelhos: 2, estado: 'comprovado', receita_ref: '12' },
    { versao: '2.25.0', viva: true, aparelhos: 1, estado: 'nao_testado', receita_ref: null },
  ],
};

function detalhe(over: Omit<Partial<DetalheDoLivro>, 'item'> & { item?: Partial<EntradaDoLivro> } = {}): DetalheDoLivro {
  const { item, ...resto } = over;
  return {
    item: entrada({ saude: SAUDE, ...item }), evidencias: [], trilha: [], exposicoes: [], conteudo: RECEITA, versao: VERSAO,
    relacoes: [], ...resto,
  };
}

let root: Root;
let container: HTMLDivElement;

beforeEach(() => {
  installBrowserStubs();
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

async function mostrar(d: DetalheDoLivro): Promise<string> {
  await act(async () => {
    root.render(<DetalheRico detalhe={d} />);
  });
  return text(container);
}

const secoes = () => Array.from(container.querySelectorAll('h4')).map((h) => h.textContent);

describe('detalhe rico: receita', () => {
  it('mostra as ações com ferramenta, alvo e só o NOME dos parâmetros, e marca a ação do commit', async () => {
    const t = await mostrar(detalhe());
    const passos = container.querySelector('[aria-label="Passos da receita"]') as HTMLElement;
    expect(passos.querySelectorAll(':scope > li')).toHaveLength(4);
    expect(text(passos)).toContain('Digita um texto');
    expect(text(passos)).toContain('{mensagem}');            // o nome, entre chaves
    expect(text(passos)).toContain('com.whatsapp:id/contato');
    expect(text(passos)).toContain('Marina');                 // o texto do seletor é da tela, não de parâmetro
    // UX do deploy 8: o alvo em palavras na linha; os seletores crus ficam recolhidos em "como o encontra".
    const primeiro = passos.querySelector(':scope > li') as HTMLElement;
    expect(text(primeiro)).toContain('Alvo: “Marina”');
    expect(primeiro.querySelector('details:not([open])')?.textContent).toContain('com.whatsapp:id/contato');
    expect(text(passos.querySelectorAll(':scope > li')[1] as HTMLElement)).toContain('Alvo: o elemento entry');
    // O selo "faz o efeito" só na terceira ação, e o resumo diz qual é.
    const selos = Array.from(passos.querySelectorAll(':scope > li')).map((li) => /faz o efeito/.test(li.textContent ?? ''));
    expect(selos).toEqual([false, false, true, false]);
    expect(t).toContain('quem o faz é o passo 3');
    // Dado sigiloso: só a frase, nenhum nome.
    expect(text(passos)).toContain('Digita um dado sigiloso');
  });

  it('marca a capability ambígua, linka a execução de origem e a versão que ela substitui', async () => {
    const t = await mostrar(detalhe());
    expect(t).toContain('enviar_mensagem, responder');
    expect(t).toContain('ambígua');
    const run = container.querySelector('a[href="#/execucoes/r-20261001-abc"]');
    expect(run?.textContent).toBe('execução r-20261001-abc');      // fora do formato do id: o id, com a palavra
    expect(run?.getAttribute('title')).toBe('r-20261001-abc');
    const subst = container.querySelector(`a[href="${hrefDoItem('receita', '9')}"]`);
    expect(subst?.textContent).toContain('versão 2');
    expect(t).toContain('concordou com a IA em 4 de 5');     // sombra
    expect(t).toContain('deu certo 8 · falhou 2 · falhas seguidas 1');
  });
});

describe('detalhe rico: saúde', () => {
  it('traz o rótulo, o motivo com fato e limiar e as dimensões, com "sem dado" no lugar do zero', async () => {
    const t = await mostrar(detalhe());
    expect(t).toContain('Degradando');
    expect(t).toContain('6 de 10 deram certo (60%), abaixo de 80%');                // 30.44: a conta, não só a taxa
    const linhas = Array.from(container.querySelectorAll('table'))
      .find((tb) => /O que foi medido/.test(tb.textContent ?? ''))!.querySelectorAll('tbody tr');
    const porNome = Object.fromEntries(Array.from(linhas).map((tr) => [tr.querySelector('th')?.textContent, tr.querySelector('td')?.textContent]));
    expect(porNome['Eficácia']).toBe('60% (em 10)');
    // Com a seção "Versão do app" logo abaixo, a linha "Versão do app: sem dado" da saúde sai (ela a contradiria).
    expect(porNome['Versão do app']).toBeUndefined();
    expect(porNome['Intervenção humana']).toBe('sem dado');
    expect(Object.values(porNome)).not.toContain('0');
  });

  it('não cria a seção quando o item não tem saúde (memória, backend antigo)', async () => {
    await mostrar(detalhe({ item: { saude: null } }));
    expect(secoes()).not.toContain('Saúde');
  });

  it('textoDoMotivo e valorDaDimensao: desconhecido nunca vira número', () => {
    expect(textoDoMotivo({ codigo: 'nunca_usado', dimensao: 'uso', valor: 20, limite: 14, detalhe: 'dias publicado' }))
      .toBe('Nunca usado desde que foi publicado, há 20 dias (prazo: 14 dias)');
    // o mesmo fato no fluxo (backend anterior) sai com o mesmo texto
    expect(textoDoMotivo({ codigo: 'fluxo_nunca_casado', dimensao: 'uso', valor: 20, limite: 14, detalhe: null }))
      .toBe('Nunca usado desde que foi publicado, há 20 dias (prazo: 14 dias)');
    expect(textoDoMotivo({ codigo: 'eficacia_desconhecida', dimensao: 'eficacia', valor: null, limite: null, detalhe: null }))
      .toMatch(/^Sem dado/);
    expect(textoDoMotivo({ codigo: 'codigo_novo', dimensao: null, valor: 3, limite: null, detalhe: null })).toBe('codigo_novo: 3');
    const d: DimensaoDeSaude = { nome: 'eficacia', estado: 'desconhecida', valor: null, amostra: null, fonte: 'x' };
    expect(valorDaDimensao(d)).toBe('sem dado');
    expect(valorDaDimensao({ ...d, nome: 'frescor', estado: 'medida', valor: 0 })).toContain('0 dias');
  });

  it('30.44: o motivo de eficácia diz "3 de 5 deram certo (60%), abaixo de 80%"; sem a dimensão, o texto antigo', () => {
    const motivo = { codigo: 'eficacia_abaixo_do_minimo', dimensao: 'eficacia', valor: 0.6, limite: 0.8, detalhe: '5 usos' };
    const dimensoes: DimensaoDeSaude[] = [{ nome: 'eficacia', estado: 'medida', valor: 0.6, amostra: 5, fonte: 'x' }];
    expect(textoDoMotivo(motivo, { dimensoes })).toBe('3 de 5 deram certo (60%), abaixo de 80%');
    // a amostra é a da dimensão (acertos + falhas), não os usos do contador
    expect(textoDoMotivo({ ...motivo, valor: 0.75 }, { dimensoes: [{ ...dimensoes[0]!, valor: 0.75, amostra: 8 }] }))
      .toBe('6 de 8 deram certo (75%), abaixo de 80%');
    // sem dimensão, dimensão sem dado ou taxa que não fecha em inteiros: o texto de antes, nunca um número inventado
    const antigo = 'Acerta 60% das vezes, abaixo do mínimo de 80% (5 usos)';
    expect(textoDoMotivo(motivo)).toBe(antigo);
    expect(textoDoMotivo(motivo, { dimensoes: [{ ...dimensoes[0]!, estado: 'desconhecida', valor: null, amostra: null }] })).toBe(antigo);
    expect(textoDoMotivo({ ...motivo, valor: 0.61 }, { dimensoes })).toContain('Acerta 61%');
  });

  it('obsoleto_provavel (30.14): rótulo traduzido e o sinal do catálogo em palavras', () => {
    expect(metaDeSaude('obsoleto_provavel')?.label).toBe('Provavelmente obsoleto');
    expect(textoDoMotivo({ codigo: 'efeito_sem_respaldo_no_catalogo', dimensao: null, valor: '*', limite: null, detalhe: 'catalogo.yaml' }))
      .toMatch(/catálogo atual do app não permite/);
  });
});

describe('detalhe rico: versão', () => {
  it('mostra "não testado", as versões vivas e o quadro por versão com a receita linkada', async () => {
    const t = await mostrar(detalhe());
    expect(secoes()).toContain('Versão do app');
    expect(t).toContain('Não testado');
    expect(t).toContain('2.24.1 (2 aparelhos) · 2.25.0 (1 aparelho)');
    expect(t).toContain('Não testada em2.25.0');
    const quadro = Array.from(container.querySelectorAll('table')).find((tb) => /Quadro por versão/.test(tb.textContent ?? ''))!;
    const linhas = Array.from(quadro.querySelectorAll('tbody tr')).map((tr) => Array.from(tr.children).map((c) => c.textContent));
    expect(linhas).toEqual([['2.24.1', '2 aparelhos', 'Comprovado', 'receita 12'], ['2.25.0', '1 aparelho', 'Não testado', 'nenhuma']]);
    expect(quadro.querySelector(`a[href="${hrefDoItem('receita', '12')}"]`)).not.toBeNull();
  });

  it('item independente da versão (fluxo, lição) não ganha a seção', async () => {
    await mostrar(detalhe({ versao: { estado: 'independente', app: null, app_version: null, vivas: [], nao_testada_em: [], por_versao: [] } }));
    expect(secoes()).not.toContain('Versão do app');
  });
});

describe('detalhe rico: relações e seções ausentes', () => {
  it('lista as relações com link real para o item no Livro; a absorvida por regra declarada vira texto', async () => {
    const t = await mostrar(detalhe({
      relacoes: [
        { tipo: 'substitui', kind: 'receita', ref: '9', rotulo: 'v2 (superseded)', fonte: 'recipes' },
        { tipo: 'contradiz', kind: 'tela', ref: 'li-77', rotulo: null, fonte: 'learning_items' },
        { tipo: 'absorvida', kind: 'regra_declarada', ref: 'chat_aberto', rotulo: null, fonte: 'state_detail' },
      ],
    }));
    expect(secoes()).toContain('Relações');
    const lista = container.querySelector('[aria-label="Itens relacionados"]') as HTMLElement;
    const links = Array.from(lista.querySelectorAll('a')).map((a) => [a.getAttribute('href'), a.textContent]);
    expect(links).toEqual([
      [hrefDoItem('receita', '9'), 'Receita v2 (superseded)'],
      [hrefDoItem('tela', 'li-77'), 'Tela aprendida li-77'],
    ]);
    expect(hrefDoItem('receita', '9')).toBe('#/aprendizado?aba=aprendido&item=receita%3A9');
    expect(t).toContain('Absorvida por a regra declarada "chat_aberto"');
    expect(destinoDaRelacao({ kind: 'commit', ref: 'abc123' })).toBeNull();
  });

  it('sem conteúdo, saúde, versão e relações: só Identidade, Evidência e Histórico (e Validações, na receita)', async () => {
    await mostrar({ item: entrada({ kind: 'licao', ref: 'li-1', saude: null }), evidencias: [], trilha: [], exposicoes: [] });
    expect(secoes()).toEqual(['Identidade', 'Evidência registrada', 'Histórico']);
    // A receita ganha a seção Validações (30.43), mesmo sem conteúdo; a lição não.
    await mostrar(detalhe({ conteudo: null, versao: undefined, item: { saude: null }, relacoes: [] }));
    expect(secoes()).toEqual(['Identidade', 'Evidência registrada', 'Validações', 'Histórico']);
  });

  it('histórico e evidência com link para a execução; ids dos títulos não se repetem entre dois detalhes', async () => {
    await mostrar(detalhe({
      trilha: [{ id: 1, from: 'candidate', to: 'published', reason: 'conferi o alvo', decided_by: 'dono', decided_at: '2026-10-01T10:00:00Z', run_id: null }],
      evidencias: [{ stance: 'against', origin_ref: 'o', run_id: 'r-9', instance_id: 'android-01', app_version: '2.25.0',
                     simulated: true, detail: 'alvo ausente', observed_at: '2026-10-01T11:00:00Z' }],
    }));
    expect(text(container.querySelector('[aria-label="Trilha"]')!)).toContain('conferi o alvo');
    expect(container.querySelector('a[href="#/execucoes/r-9"]')).not.toBeNull();
    expect(text(container)).toContain('simulada');
    const ids = Array.from(container.querySelectorAll('h4')).map((h) => h.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it('30.44: o título da etapa vai ao lado da chave; sem título, o texto fica como veio', async () => {
    const ev = (run: string, detail: string, etapa_titulo: string | null) => ({
      stance: 'for' as const, origin_ref: `reproducao:${run}`, run_id: run, instance_id: 'android-09', app_version: null,
      simulated: false, detail, observed_at: '2026-10-03T16:00:00Z', etapa_titulo });
    await mostrar(detalhe({
      evidencias: [ev('r-1', 'etapa 5 (send_message): reproduzida', 'Enviar a mensagem'),
                   ev('r-2', 'etapa 2 (open_inbox): reproduzida', null)],
    }));
    const linhas = Array.from(container.querySelectorAll('[aria-label="Evidências"] li')).map((li) => text(li));
    expect(linhas.find((l) => l.includes('r-1'))).toContain('etapa 5 (send_message) — Enviar a mensagem: reproduzida');
    const sem = linhas.find((l) => l.includes('r-2'))!;
    expect(sem).toContain('etapa 2 (open_inbox): reproduzida');
    expect(sem).not.toContain('—');
  });

  it('30.36: a divergência de forma aparece à parte, e o contra que ela reclassificou sai da conta', async () => {
    const ev = (stance: EvidenciaDoLivro['stance'], run: string, minuto: string) => ({
      stance, origin_ref: `run:${run}`, run_id: run, instance_id: 'android-09', app_version: null, simulated: false,
      detail: null, observed_at: `2026-10-03T16:${minuto}:00Z` });
    await mostrar(detalhe({
      evidencias: [ev('for', 'r-1', '00'), ev('against', 'r-2', '07'), ev('forma', 'r-2', '30'), ev('against', 'r-3', '40')],
    }));
    const secao = text(container.querySelector('[aria-label="Evidências"]')!.parentElement!);
    expect(secao).toContain('1 a favor · 1 contra · 0 em conflito · 1 de forma (só a redação do plano mudou; não contam)');
    const linhas = Array.from(container.querySelectorAll('[aria-label="Evidências"] li')).map((li) => text(li));
    expect(linhas.filter((l) => l.startsWith('contra (reclassificada como forma; não conta)'))).toHaveLength(1);
    expect(linhas.filter((l) => l.startsWith('divergência de forma'))).toHaveLength(1);
  });

  it('30.42: a prova inválida mostra o motivo, e o a favor que ela reclassificou sai da conta', async () => {
    const ev = (stance: EvidenciaDoLivro['stance'], run: string, minuto: string, detail: string | null = null) => ({
      stance, origin_ref: `run:${run}`, run_id: run, instance_id: 'android-09', app_version: null, simulated: false,
      detail, observed_at: `2026-10-03T16:${minuto}:00Z` });
    await mostrar(detalhe({
      evidencias: [ev('for', 'r-1', '00'), ev('for', 'r-2', '07', '[686ac998656d] prova: 3/3 etapas comprovadas'),
                   ev('invalida', 'r-2', '30', '[686ac998656d] invalida:efeito_repetido — o efeito saiu 2 vezes (reclassificada)'),
                   ev('invalida', 'r-3', '40', '[686ac998656d] invalida:ponto_de_partida — a etapa de abertura (open_app) não chegou')],
    }));
    const secao = text(container.querySelector('[aria-label="Evidências"]')!.parentElement!);
    expect(secao).toContain('1 a favor · 0 contra · 0 em conflito · 2 inválidas (a prova não valeu; não contam)');
    const linhas = Array.from(container.querySelectorAll('[aria-label="Evidências"] li')).map((li) => text(li));
    expect(linhas.filter((l) => l.startsWith('a favor (reclassificada como inválida; não conta)'))).toHaveLength(1);
    expect(linhas.filter((l) => l.startsWith('inválida (efeito repetido; não conta)'))).toHaveLength(1);
    expect(linhas.filter((l) => l.startsWith('inválida (ponto de partida; não conta)'))).toHaveLength(1);
    expect(linhas.join(' ')).not.toContain('invalida:');                  // o formato nunca aparece cru
  });

  it('a linha de forma mostra a pós-condição pelo rótulo, sem a marca do conteúdo (polimento do deploy 13)', async () => {
    await mostrar(detalhe({
      evidencias: [{ stance: 'forma', origin_ref: 'run:r-2', run_id: 'r-2', instance_id: 'android-09', app_version: null,
                     simulated: false, observed_at: '2026-10-03T16:30:00Z',
                     detail: '[686ac998656d] etapa 2: pós-condição reescrita (app_foreground × element_present) (+1)' }],
    }));
    const linha = text(container.querySelector('[aria-label="Evidências"] li')!);
    expect(linha).toContain('etapa 2: pós-condição reescrita (nesta execução: app em primeiro plano; no fluxo: elemento presente) (e mais 1)');
    expect(linha).not.toContain('686ac998656d');
    expect(linha).not.toContain('app_foreground');
  });
});

describe('evidência inválida e reaprendido (30.23)', () => {
  const RUN = 'r-20261002204347-8c3f6e';
  const botao = (el: ParentNode, rotulo: string) =>
    Array.from(el.querySelectorAll('button')).find((b) => text(b).trim() === rotulo) as HTMLElement | undefined;

  it('a trilha mostra o tipo com o link da execução, e a marca que reclassifica diz isso', async () => {
    await mostrar(detalhe({
      item: { state: 'disabled', native_status: 'quarantined' },
      trilha: [
        { id: 46, from: null, to: 'candidate', reason: 'aprendida da IA; em prova (sombra)', decided_by: 'sistema',
          decided_at: '2026-10-02T20:46:54Z', run_id: RUN, tipo: null, run_invalidada: null },
        { id: 60, from: 'disabled', to: 'disabled', reason: `evidencia_invalida:${RUN}`, decided_by: 'Ana Ribeiro',
          decided_at: '2026-10-03T10:00:00Z', run_id: null, tipo: 'evidencia_invalida', run_invalidada: RUN },
      ],
      evidencias: [{ stance: 'for', origin_ref: `run:${RUN}`, run_id: RUN, instance_id: 'android-01', app_version: null,
                     simulated: false, detail: 'a execução que o gerou', observed_at: '2026-10-02T20:47:00Z', invalidada: true }],
    }));
    const marca = container.querySelector('[aria-label="Trilha"] [data-tipo="evidencia_invalida"]') as HTMLElement;
    expect(text(marca)).toContain('Desligado (motivo reclassificado) por Ana Ribeiro');
    expect(text(marca)).toContain('evidência inválida');
    expect(text(marca)).toContain('terminou como sucesso sem comprovar o que fez');
    expect(text(marca)).not.toContain('evidencia_invalida:');                    // o formato nunca aparece cru
    expect(marca.querySelector(`a[href="#/execucoes/${RUN}"]`)).not.toBeNull();
    expect(text(container.querySelector('[aria-label="Evidências"]')!)).toContain('execução invalidada não conta como prova');
    expect(passoDaTransicao({ from: 'candidate', to: 'disabled' })).toBe('Candidato → Desligado');
    expect(passoDaTransicao({ from: null, to: 'candidate' })).toBe('Candidato');
    expect(passoDaTransicao({ from: 'published', to: 'published', tipo: 'confirmacao' })).toBe('Confirmado que fica');
  });

  it('a receita reaprendida diz o que reaprende, com link, e por que espera o dono', async () => {
    const t = await mostrar(detalhe({
      item: { ref: '120', state: 'validated', side_effect: false, requires_owner: true,
              reaprendido: { run_invalidada: RUN, item: { kind: 'receita', ref: '109' } },
              por_que_nao_publica: { codigo: 'reaprendido', espera_o_dono: true, detalhe: RUN } },
      relacoes: [{ tipo: 'reaprende', kind: 'receita', ref: '109', rotulo: `109 (evidência inválida da execução ${RUN})`,
                   fonte: 'learning_transitions' }],
    }));
    expect(secoes()).toContain('Reaprendido depois de uma evidência inválida');
    const aviso = container.querySelector('[data-reaprendido]') as HTMLElement;
    expect(text(aviso)).toContain('Reaprende o item Receita 109, aprendido da execução');
    expect(aviso.querySelector(`a[href="${hrefDoItem('receita', '109')}"]`)).not.toBeNull();
    expect(text(aviso)).toContain('a aprovação é sua');
    expect(t).toContain(`Reaprende Receita 109 (evidência inválida da execução ${RUN})`);
    expect(t).toContain(`O sistema não publica sozinho: foi reaprendido depois de uma evidência inválida (a execução ${RUN}`);
  });

  it('o fluxo reaprendido na mesma linha não aponta para si mesmo', async () => {
    await mostrar(detalhe({
      conteudo: null, versao: undefined,
      item: { kind: 'fluxo', ref: 'ler-a-caixa', state: 'candidate', saude: null,
              reaprendido: { run_invalidada: RUN, item: { kind: 'fluxo', ref: 'ler-a-caixa' } } },
    }));
    const aviso = container.querySelector('[data-reaprendido]') as HTMLElement;
    expect(text(aviso)).toContain('Esta linha tinha sido aprendida da execução');
    expect(Array.from(aviso.querySelectorAll('a')).map((a) => a.getAttribute('href'))).toEqual([`#/execucoes/${RUN}`]);
  });

  it('marcar evidência inválida: confirma no lugar, manda só a execução e avisa quem relê', async () => {
    const backend = new FakeBackend();
    backend.install();
    backend.on('POST', /^\/api\/aprendizado\/receita\/109\/evidencia-invalida$/, () => json(detalhe()));
    let mudou = 0;
    await act(async () => {
      root.render(<DetalheRico detalhe={detalhe({ item: { ref: '109', state: 'candidate', native_status: 'candidate' },
                                                  invalidar_evidencia: { run_id: RUN } })}
                               onMudou={() => { mudou += 1; }} />);
    });
    await click(botao(container, 'Marcar evidência inválida')!);
    const conf = container.querySelector('[data-evidencia-invalida]') as HTMLElement;
    expect(text(conf)).toContain('O item é desligado agora.');
    expect(text(conf)).toContain('renasce como candidato e espera a sua aprovação');
    expect(conf.querySelector(`a[href="#/execucoes/${RUN}"]`)).not.toBeNull();
    await click(botao(conf, 'Confirmar evidência inválida')!);
    await waitFor(() => expect(mudou).toBe(1));
    expect(backend.callsTo('POST', /evidencia-invalida$/).map((c) => c.body)).toEqual([{ run_id: RUN }]);
    expect(container.querySelector('[data-evidencia-invalida]')).toBeNull();
  });

  it('a recusa do backend aparece na confirmação; já desligado, o texto diz que só muda o motivo', async () => {
    const backend = new FakeBackend();
    backend.install();
    backend.on('POST', /evidencia-invalida$/, () => apiError(409, 'state_conflict', 'A receita mudou de status; releia.'));
    await act(async () => {
      root.render(<DetalheRico detalhe={detalhe({ item: { ref: '109', state: 'disabled', native_status: 'quarantined' },
                                                  invalidar_evidencia: { run_id: RUN } })} />);
    });
    await click(botao(container, 'Marcar evidência inválida')!);
    const conf = container.querySelector('[data-evidencia-invalida]') as HTMLElement;
    expect(text(conf)).toContain('O item já está desligado: a marca só registra este motivo na trilha.');
    await click(botao(conf, 'Confirmar evidência inválida')!);
    await waitFor(() => expect(text(conf)).toContain('A receita mudou de status; releia.'));
    await mostrar(detalhe({ item: { ref: '109', state: 'disabled' }, invalidar_evidencia: null }));
    expect(botao(container, 'Marcar evidência inválida')).toBeUndefined();
  });
});

describe('detalhe rico: outros tipos de conteúdo', () => {
  it('lição: o texto exato; tela: ids; fluxo: etapas', async () => {
    const licao: ConteudoDoItem = { tipo: 'licao', texto: 'Role a lista antes de procurar', modelo: null, acao: 'scroll',
                                    alvo: { tipo: 'parametro', valor: 'contato' }, escopo: { app: 'com.whatsapp', capability: 'abrir_conversa', step_hash: null, role: null }, tokens: 12 };
    let t = await mostrar(detalhe({ conteudo: licao, item: { kind: 'licao' } }));
    expect(container.querySelector('blockquote')?.textContent).toBe('Role a lista antes de procurar');
    expect(t).toContain('o parâmetro contato');

    t = await mostrar(detalhe({ conteudo: { tipo: 'tela', tela: 'chat_aberto', casa: true, autenticada: true, ids_todos: ['a:id/x', 'a:id/y'], razao: null }, item: { kind: 'tela' } }));
    expect(t).toContain('a:id/x, a:id/y');

    t = await mostrar(detalhe({ conteudo: { tipo: 'fluxo', nome: 'Abrir o perfil', comando_modelo: 'abra o perfil de {nome}', origem: { tipo: 'treino', fonte: 't', source_run_id: null },
                                            etapas: [{ indice: 0, chave: 'abrir', capability: 'abrir_perfil', alvo: null, efeito: false, pos_condicao: { tipo: 'text_present', descricao: 'perfil aberto' }, parametros: ['nome'], segredo: false }],
                                            efeito: { externo: false, etapas_com_efeito: [] } }, item: { kind: 'fluxo' } }));
    expect(t).toContain('Confere: perfil aberto');
    expect(t).toContain('{nome}');
    expect(t).not.toContain('→');                                     // sem `apps` no conteúdo (backend antigo), nada novo na tela

    // 29.42: o fluxo entre apps diz os dois, na ordem do plano, pelo nome do app (sem cadastro, o id).
    useAppStore.setState({ apps: [{ id: 'qa-messenger', name: 'QA Messenger' }] as never });
    t = await mostrar(detalhe({ conteudo: { tipo: 'fluxo', nome: 'Mandar e abrir', comando_modelo: 'mande {x}', origem: { tipo: 'execucao', fonte: null, source_run_id: null },
                                            apps: ['qa-messenger', 'chrome'], etapas: [], efeito: { externo: false, etapas_com_efeito: [] } }, item: { kind: 'fluxo' } }));
    expect(t).toContain('QA Messenger → chrome');
    useAppStore.setState({ apps: [] });
    // UX do deploy 8: a origem diz a execução pela data, com o id no title (e sem repetir "execução").
    t = await mostrar(detalhe({ conteudo: { tipo: 'fluxo', nome: 'Abrir', comando_modelo: 'abra', origem: { tipo: 'execucao', fonte: null, source_run_id: 'r-20260924114815-c14258' },
                                            apps: [], etapas: [], efeito: { externo: false, etapas_com_efeito: [] } }, item: { kind: 'fluxo' } }));
    expect(t).toMatch(/Aprendido na execução de \S/);
    expect(t).not.toContain('execução · execução');
    expect(container.querySelector('a[title="r-20260924114815-c14258"]')).not.toBeNull();
  });
});

describe('detalhe rico: a capability com o nome do catálogo (validação do deploy 3, P3)', () => {
  it('Identidade e lição: "Abrir o perfil (OPEN_PROFILE)"; o texto da lição nomeia a capability só na tela', async () => {
    const licao: ConteudoDoItem = { tipo: 'licao', texto: 'Em OPEN_PROFILE: a tentativa que comprovou tocou em "Perfil"', modelo: null,
                                    acao: null, alvo: null, escopo: { app: 'com.instagram.android', capability: 'OPEN_PROFILE', step_hash: null, role: null }, tokens: 9 };
    const t = await mostrar(detalhe({ conteudo: licao, item: { kind: 'licao', capability: 'OPEN_PROFILE', capability_nome: 'Abrir o perfil' } }));
    expect(container.querySelector('blockquote')?.textContent).toBe('Em Abrir o perfil (OPEN_PROFILE): a tentativa que comprovou tocou em "Perfil"');
    expect(t.split('Abrir o perfil (OPEN_PROFILE)').length - 1).toBe(3);  // a citação, a Identidade e o escopo
  });

  it('receita de capability única ganha o nome; a ambígua e a de outra capability seguem com o código', async () => {
    const unica = { ...RECEITA, capability: { nomes: ['SEND_MESSAGE'], ambigua: false, fonte: 'origem' as const } };
    let t = await mostrar(detalhe({ conteudo: unica, item: { capability: 'SEND_MESSAGE', capability_nome: 'Enviar a mensagem' } }));
    expect(t.split('Enviar a mensagem (SEND_MESSAGE)').length - 1).toBe(2);  // a Identidade e o conteúdo
    t = await mostrar(detalhe({ item: { capability: null, capability_nome: null } }));
    expect(t).toContain('enviar_mensagem, responder');
    expect(t).not.toContain('(enviar_mensagem');
  });
});

describe('itemDoLink', () => {
  it('lê kind:ref (o ref pode ter @) e recusa o que não é do Livro', () => {
    expect(itemDoLink('habilidade:instagram.abrir@2')).toEqual({ kind: 'habilidade', ref: 'instagram.abrir@2' });
    expect(itemDoLink('commit:abc')).toBeNull();
    expect(itemDoLink('receita')).toBeNull();
    expect(itemDoLink(undefined)).toBeNull();
  });
});

describe('no catálogo Aprendido', () => {
  let backend: FakeBackend;
  const NO_LIVRO = entrada({ saude: SAUDE });

  beforeEach(() => {
    backend = new FakeBackend();
    backend.install();
    backend.on('GET', /^\/api\/aprendizado\/apps$/, () => json({ apps: [], nao_resolvido: null, sem_eixo: null }));
    backend.on('GET', /^\/api\/aprendizado$/, () => json({ itens: [NO_LIVRO], total: 1, contagem: { receita: { published: 1 } } }));
    backend.on('GET', /^\/api\/aprendizado\/pendentes$/, () => json({ itens: [], total: 0 }));
    backend.on('GET', /^\/api\/aprendizado\/receita\/12$/, () => json(detalhe()));
    backend.on('GET', /^\/api\/aprendizado\/receita\/9$/, () => json(detalhe({ item: { ref: '9', title: 'Receita antiga', state: 'deprecated', saude: null }, conteudo: null, versao: undefined })));
  });

  it('a lista continua com uma linha por item e o rótulo de saúde como selo; o detalhe abre sob demanda', async () => {
    useUiStore.getState().navegar({ tela: 'aprendizado', query: { aba: 'aprendido' } }, 'replace');
    await act(async () => {
      root.render(<AprendizadoPage />);
    });
    await waitFor(() => expect(text(container)).toContain('Enviar oi para o contato'));
    const linha = container.querySelector('[data-item="receita:12"]') as HTMLElement;
    expect(text(linha)).toContain('Degradando');
    expect(linha.querySelector('h4')).toBeNull();                              // nada do detalhe antes de abrir
    expect(backend.callsTo('GET', /^\/api\/aprendizado\/receita\/12$/)).toHaveLength(0);
    await openDetails(/Detalhes, evidência e trilha/, linha);
    await waitFor(() => expect(linha.querySelector('h4')).not.toBeNull());
    expect(secoes()).toEqual(['Identidade', 'Conteúdo', 'Saúde', 'Versão do app', 'Evidência registrada', 'Validações', 'Histórico']);
  });

  it('?item=kind:ref abre o item do link no topo, já com o detalhe', async () => {
    useUiStore.getState().navegar({ tela: 'aprendizado', query: { aba: 'aprendido', item: 'receita:9' } }, 'replace');
    await act(async () => {
      root.render(<AprendizadoPage />);
    });
    await waitFor(() => expect(container.querySelector('[aria-label="Item aberto pelo link"] [data-item="receita:9"]')).not.toBeNull());
    await waitFor(() => expect(container.querySelector('[aria-label="Item aberto pelo link"] h4')).not.toBeNull());
    expect(text(container.querySelector('[aria-label="Item aberto pelo link"]')!)).toContain('Receita antiga');
  });
});

describe('os códigos do detalhe em palavras (UX do deploy 8)', () => {
  it('a execução pela data do id, a variante da tela e o alvo da ação', () => {
    const agora = Date.parse('2026-12-01T12:00:00Z');
    expect(rotuloDaExecucao('r-20260924114815-c14258', agora)).toMatch(/^execução de \d{2}\/\d{2} \d{2}:\d{2}$/);
    expect(rotuloDaExecucao('r-20261001-abc')).toBe('execução r-20261001-abc');
    expect(textoDaVariante('en-US/xhdpi')).toBe('idioma en-US, tela xhdpi');
    expect(textoDaVariante('outra')).toBe('outra');
    expect(textoDaVariante(null)).toBeNull();
    expect(alvoLegivel([{ tipo: 'rid+text', rid: 'com.x:id/send', texto: 'Enviar' }])).toBe('“Enviar”');
    expect(alvoLegivel([{ tipo: 'rid', rid: 'com.x:id/send' }, { tipo: 'desc', desc: 'Enviar' }])).toBe('“Enviar”');
    expect(alvoLegivel([{ tipo: 'rid', rid: 'com.x:id/send' }])).toBe('o elemento send');
    expect(alvoLegivel([])).toBeNull();
  });
});

describe('30.43: de onde o item nasceu e o histórico de validações', () => {
  let backend: FakeBackend;
  let respostaDaLista: () => Response;

  const pedido = (over: Record<string, unknown>) => ({
    id: 'lv-x', estado: 'feita', motivo: null, motivo_humano: null, item_ref: 'receita:12', item_kind: 'receita', app: null, app_nome: null,
    grupo: null, run_id: null, run_origem: null, aparelho: null, usd: 0, teto_usd: null, created_at: '2026-10-03T10:00:00Z',
    feito_em: null, expira_em: null, comando: 'Abra', ...over,
  });
  const lista = (itens: unknown[]) => () => json({ itens, contagem: {}, total: itens.length, modo: 'on' });

  beforeEach(() => {
    backend = new FakeBackend();
    backend.install();
    respostaDaLista = lista([]);
    backend.on('GET', /^\/api\/aprendizado\/validacoes$/, () => respostaDaLista());
  });

  const secaoDeValidacoes = () => container.querySelector('section[aria-labelledby$="-validacoes"]') as HTMLElement;

  it('"Nasceu na prova de um fluxo" e "numa re-execução da validação do QA", com o link da execução; nada quando nulo', async () => {
    let t = await mostrar(detalhe({ item: { nasceu_em: 'prova_fluxo', nasceu_de: 'r-20261003-abc' } }));
    expect(t).toContain('Nasceu na prova de um fluxo');
    const marca = container.querySelector('[data-nasceu-em="prova_fluxo"]') as HTMLElement;
    expect(marca.querySelector('a')?.getAttribute('href')).toBe('#/execucoes/r-20261003-abc');
    t = await mostrar(detalhe({ item: { nasceu_em: 'validacao_qa', nasceu_de: null } }));
    expect(t).toContain('Nasceu numa re-execução da validação do QA');
    expect(container.querySelector('[data-nasceu-em] a')).toBeNull();     // sem execução de origem, sem link
    t = await mostrar(detalhe({ item: { nasceu_em: null, nasceu_de: 'r-1' } }));
    expect(t).not.toContain('Nasceu');
  });

  it('busca os pedidos do item (item=kind:ref, limite 20) e escreve os dois sentidos do mesmo motivo', async () => {
    respostaDaLista = lista([
      // Rodou (tem execução) e foi reclassificada depois: o caso do lv-2dd29.
      pedido({ id: 'lv-2dd29', estado: 'recusada', motivo: 'sem_caminho', motivo_humano: 'o plano não tinha caminho até o item',
               run_id: 'r-2dd29', aparelho: 'android-02', usd: 0.0512, feito_em: '2026-10-03T11:05:00Z' }),
      // Nunca rodou: recusado ao despachar, sem gasto.
      pedido({ id: 'lv-nr', estado: 'recusada', motivo: 'sem_caminho', motivo_humano: 'o plano não tinha caminho até o item', run_id: null }),
      pedido({ id: 'lv-ok', estado: 'feita', run_id: 'r-ok', aparelho: 'android-05', usd: 0.03 }),
      pedido({ id: 'lv-p', estado: 'pendente' }),
    ]);
    await mostrar(detalhe({ item: { ref: '12' } }));
    await waitFor(() => expect(secaoDeValidacoes().querySelector('ul')).not.toBeNull());
    const chamada = backend.callsTo('GET', /^\/api\/aprendizado\/validacoes$/).at(-1);
    expect(chamada?.query.get('item')).toBe('receita:12');
    expect(chamada?.query.get('limite')).toBe('20');
    const rodou = text(secaoDeValidacoes().querySelector('[data-pedido="lv-2dd29"]') as HTMLElement);
    expect(rodou).toContain('Rodou; depois: o plano não tinha caminho até o item');
    expect(rodou).toContain('aparelho android-02');
    expect(rodou).toContain('US$ 0,0512');
    expect(rodou).toContain('Recusada');
    expect((secaoDeValidacoes().querySelector('[data-pedido="lv-2dd29"] a') as HTMLAnchorElement).getAttribute('href')).toBe('#/execucoes/r-2dd29');
    const naoRodou = text(secaoDeValidacoes().querySelector('[data-pedido="lv-nr"]') as HTMLElement);
    expect(naoRodou).toContain('Não rodou: o plano não tinha caminho até o item');
    expect(naoRodou).toContain('sem gasto');
    expect(naoRodou).not.toContain('Rodou');
    expect(secaoDeValidacoes().querySelector('[data-pedido="lv-nr"] a')).toBeNull();      // sem execução, sem link
    // Sem motivo, o rótulo do estado (no selo) basta.
    expect(text(secaoDeValidacoes().querySelector('[data-pedido="lv-ok"]') as HTMLElement)).toContain('Feita');
    expect(text(secaoDeValidacoes().querySelector('[data-pedido="lv-p"]') as HTMLElement)).toContain('Pendente');
  });

  it('lista vazia: uma frase curta; erro de rede: aviso discreto e o resto do detalhe segue de pé', async () => {
    await mostrar(detalhe());
    await waitFor(() => expect(text(secaoDeValidacoes())).toContain('Nenhum pedido de validação para este item.'));
    respostaDaLista = () => apiError(500, 'boom', 'falhou');
    await mostrar(detalhe({ item: { ref: '13' } }));
    await waitFor(() => expect(secaoDeValidacoes().querySelector('[data-validacoes-erro]')).not.toBeNull());
    expect(secoes()).toContain('Evidência registrada');
    expect(secoes()).toContain('Histórico');
  });

  it('lição e outros tipos não pedem validações', async () => {
    await mostrar({ item: entrada({ kind: 'licao', ref: 'li-1', saude: null }), evidencias: [], trilha: [], exposicoes: [] });
    expect(secoes()).not.toContain('Validações');
    expect(backend.callsTo('GET', /^\/api\/aprendizado\/validacoes$/)).toHaveLength(0);
  });
});
