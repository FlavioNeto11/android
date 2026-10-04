// @vitest-environment jsdom
/**
 * Aba Anexos da tela Canais (item 28.24, fatia 4): contra o formato de `GET /api/canais/anexos` e de
 * `POST /api/canais/anexos/{id}/trello` (`backend/tests/test_canais_anexos_lista.py` e `test_canais_anexos_trello.py` travam
 * as chaves e as regras), com respostas falsas (FakeBackend) — prova `simulated`, nunca `real`.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type { CanalAnexo } from '../../api/types';
import { FakeBackend, apiError, byRole, click, installBrowserStubs, json, setValue, text, waitFor } from '../../test/harness';
import { AnexosTab } from './AnexosTab';
import { desdeDoPeriodo, tamanhoLegivel, validarCartao } from './anexos';

const CARTAO = 'c'.repeat(24);

function anexo(parcial: Partial<CanalAnexo> & { id: number }): CanalAnexo {
  return {
    canal: 'telegram', direcao: 'entrada', mime: 'image/png', bytes: 123 * 1024, estado: 'guardado', motivo_recusa: null,
    criado_em: new Date(Date.now() - 3 * 3_600_000).toISOString(), apagado_em: null, do_dono: true, tem_conteudo: true,
    pode_ir_ao_cartao: true, ...parcial,
  };
}

const IMAGEM = anexo({ id: 3 });
const PDF_ENVIADO = anexo({ id: 2, direcao: 'saida', mime: 'application/pdf', bytes: 2 * 1024 * 1024, do_dono: false, pode_ir_ao_cartao: false });
const RECUSADO = anexo({ id: 1, mime: null, bytes: 0, estado: 'recusado', motivo_recusa: 'Voz não é aceita.', tem_conteudo: false,
                         pode_ir_ao_cartao: false });

function pagina(items: CanalAnexo[], total = items.length, offset = 0) {
  return { items, total, limit: 12, offset };
}

let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

beforeAll(() => installBrowserStubs());

beforeEach(() => {
  backend = new FakeBackend();
  backend.install();
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

async function abrir(): Promise<void> {
  await act(async () => { root.render(<AnexosTab />); });
}

const LISTA = /^\/api\/canais\/anexos$/;
const ultimaConsulta = () => backend.callsTo('GET', LISTA).at(-1)?.query;

describe('AnexosTab', () => {
  it('lista com miniatura, ícone do PDF, canal, sentido, data e tamanho, sem remetente nem caminho', async () => {
    backend.on('GET', LISTA, () => json(pagina([IMAGEM, PDF_ENVIADO, RECUSADO])));
    await abrir();
    await waitFor(() => text().includes('Mostrando 3 de 3'));
    const itens = Array.from(container.querySelectorAll('li'));
    expect(itens).toHaveLength(3);
    const [imagem, pdf, recusado] = itens.map((li) => text(li));
    expect(imagem).toContain('Telegram');
    expect(imagem).toContain('Recebido de você');
    expect(imagem).toContain('123 KB');
    expect(imagem).toContain('há 3 h');
    expect(container.querySelector('li img')?.getAttribute('src')).toBe('/api/canais/anexos/3/conteudo');
    expect(pdf).toContain('Enviado pela Central');
    expect(pdf).toContain('PDF · 2 MB');
    expect(itens[1]?.querySelector('img')).toBeNull();                                  // PDF: ícone, não imagem
    expect(recusado).toContain('Recusado');
    expect(recusado).toContain('Voz não é aceita.');
    expect(itens[2]?.querySelector('button')).toBeNull();                               // sem arquivo: sem prévia nem ação
    expect(text()).not.toMatch(/sha256|data\/anexos|remetente/i);
    expect(backend.calls.every((c) => c.method === 'GET')).toBe(true);
    expect(ultimaConsulta()?.get('limit')).toBe('12');
  });

  it('sem nenhum anexo: estado vazio que diz o que fazer, sem filtros pendentes', async () => {
    backend.on('GET', LISTA, () => json(pagina([])));
    await abrir();
    await waitFor(() => text().includes('Nenhum anexo ainda'));
    expect(text()).toContain('Mande uma foto ou um PDF ao bot do Telegram');
    expect(text()).not.toContain('Limpar filtros');
  });

  it('filtros viram parâmetros da rota; sem resultado, o vazio oferece "Limpar filtros" e volta ao todo', async () => {
    backend.on('GET', LISTA, (c) => json(c.query.get('canal') === 'trello' ? pagina([]) : pagina([IMAGEM])));
    await abrir();
    await waitFor(() => text().includes('Mostrando 1 de 1'));
    await setValue(byRole('combobox', 'Canal') as HTMLSelectElement, 'trello');
    await waitFor(() => text().includes('Nenhum anexo com esses filtros'));
    expect(ultimaConsulta()?.get('canal')).toBe('trello');
    await click(byRole('button', /Limpar filtros/));
    await waitFor(() => text().includes('Mostrando 1 de 1'));
    expect(ultimaConsulta()?.get('canal')).toBeNull();

    await setValue(byRole('combobox', 'Sentido') as HTMLSelectElement, 'saida');
    await waitFor(() => ultimaConsulta()?.get('direcao') === 'saida');
    await setValue(byRole('combobox', 'Período') as HTMLSelectElement, '7d');
    await waitFor(() => ultimaConsulta()?.get('desde') != null);
    const desde = Date.parse(ultimaConsulta()?.get('desde') ?? '');
    expect(Date.now() - desde).toBeGreaterThan(6.9 * 86_400_000);
    await click(container.querySelector('input[type="checkbox"]') as HTMLInputElement);
    await waitFor(() => ultimaConsulta()?.get('do_dono') === 'true');
  });

  it('"Carregar mais" pede a página seguinte sem repetir o que já está na tela', async () => {
    backend.on('GET', LISTA, (c) => json(c.query.get('offset') === '1' ? pagina([IMAGEM, PDF_ENVIADO], 3, 1) : pagina([IMAGEM], 3)));
    await abrir();
    await waitFor(() => text().includes('Mostrando 1 de 3'));
    await click(byRole('button', /Carregar mais/));
    await waitFor(() => text().includes('Mostrando 2 de 3'));
    expect(container.querySelectorAll('li')).toHaveLength(2);                           // o repetido (id 3) não duplica
    expect(ultimaConsulta()?.get('offset')).toBe('1');
    expect(byRole('button', /Carregar mais/)).toBeTruthy();                             // ainda falta uma
  });

  it('prévia ao clicar: imagem grande no próprio item; PDF com link de download; fecha de novo', async () => {
    backend.on('GET', LISTA, () => json(pagina([IMAGEM, PDF_ENVIADO])));
    await abrir();
    await waitFor(() => text().includes('Mostrando 2 de 2'));
    const [li3, li2] = Array.from(container.querySelectorAll('li')) as HTMLElement[];
    expect(li3?.querySelector('img[alt^="Prévia"]')).toBeNull();
    await click(byRole('button', /Ver prévia$/, li3));
    const previa = li3?.querySelector('img[alt="Prévia: Imagem 3"]');
    expect(previa?.getAttribute('src')).toBe('/api/canais/anexos/3/conteudo');
    expect(li3?.getAttribute('data-expandido')).toBe('true');
    await click(byRole('button', /Fechar prévia$/, li3));
    expect(li3?.querySelector('img[alt^="Prévia"]')).toBeNull();

    await click(byRole('button', /Ver prévia$/, li2));
    const baixar = byRole('link', /Baixar o PDF/, li2);
    expect(baixar.getAttribute('href')).toBe('/api/canais/anexos/2/conteudo');
    expect(baixar.getAttribute('download')).toBe('anexo-2.pdf');
  });

  it('imagem que não carrega vira aviso, não ícone quebrado', async () => {
    backend.on('GET', LISTA, () => json(pagina([IMAGEM])));
    await abrir();
    await waitFor(() => text().includes('Mostrando 1 de 1'));
    await click(byRole('button', /Ver prévia$/));
    await act(async () => { container.querySelector('img[alt^="Prévia"]')?.dispatchEvent(new Event('error')); });
    expect(text()).toContain('Não consegui carregar a imagem');
  });

  it('só a mensagem do dono tem "Anexar ao cartão"; convidado, saída e recusado não', async () => {
    const convidado = anexo({ id: 4, do_dono: false, tem_conteudo: false, pode_ir_ao_cartao: false });
    backend.on('GET', LISTA, () => json(pagina([convidado, IMAGEM, PDF_ENVIADO, RECUSADO])));
    await abrir();
    await waitFor(() => text().includes('Mostrando 4 de 4'));
    expect(text(Array.from(container.querySelectorAll('li'))[0] as HTMLElement)).toContain('Recebido de convidado');
    expect(container.querySelectorAll('button')).not.toHaveLength(0);
    const botoes = Array.from(container.querySelectorAll('li button')).filter((b) => /Anexar ao cartão/.test(b.textContent ?? ''));
    expect(botoes).toHaveLength(1);
    expect(botoes[0]?.closest('li')?.getAttribute('aria-label')).toBe('Imagem 3');
  });

  it('anexar ao cartão: campo em linha, só confirma com id válido e manda confirmar:true', async () => {
    backend.on('GET', LISTA, () => json(pagina([IMAGEM])));
    backend.on('POST', /^\/api\/canais\/anexos\/3\/trello$/, () => json({ anexo_id: 3, card: CARTAO, trello_anexo: 'a'.repeat(24) }));
    await abrir();
    await waitFor(() => text().includes('Mostrando 1 de 1'));
    await click(byRole('button', /Anexar ao cartão/));
    expect(document.querySelector('[role="dialog"]')).toBeNull();                       // nada de modal
    const campo = byRole('textbox', /Cartão do Trello/) as HTMLInputElement;
    const confirmar = () => byRole('button', /Confirmar e anexar ao cartão/);          // o botão é refeito quando o motivo some
    expect(text()).toContain('sistema fora da Central');

    await click(confirmar());                                                             // vazio: nada sai
    await setValue(campo, 'abc');
    await click(confirmar());                                                             // curto demais: nada sai
    expect(text()).toContain('Cole o link do cartão');
    expect(backend.callsTo('POST', /trello$/)).toHaveLength(0);

    await setValue(campo, `  ${CARTAO.toUpperCase()} `);
    await click(confirmar());
    await waitFor(() => text().includes('Anexado ao cartão do Trello.'));
    const [chamada] = backend.callsTo('POST', /trello$/);
    expect(chamada?.body).toEqual({ card: CARTAO, confirmar: true });
    expect(container.querySelector('form[class*="anexar"]')).toBeNull();                // o campo some
    expect(Array.from(container.querySelectorAll('button')).some((b) => /Anexar ao cartão/.test(b.textContent ?? ''))).toBe(false);
  });

  it('erro da rota fica no campo, em português, e o anexo NÃO aparece como anexado', async () => {
    backend.on('GET', LISTA, () => json(pagina([IMAGEM])));
    backend.on('POST', /trello$/, () => apiError(409, 'cartao_fora_dos_quadros', 'Esse cartão não é de um quadro que a Central espelha.'));
    await abrir();
    await waitFor(() => text().includes('Mostrando 1 de 1'));
    await click(byRole('button', /Anexar ao cartão/));
    await setValue(byRole('textbox', /Cartão do Trello/) as HTMLInputElement, CARTAO);
    await click(byRole('button', /Confirmar e anexar ao cartão/));
    await waitFor(() => text().includes('Esse cartão não é de um quadro que a Central espelha.'));
    expect(byRole('alert', /Esse cartão/)).toBeTruthy();
    expect(text()).not.toContain('Anexado ao cartão do Trello.');
    expect(byRole('textbox', /Cartão do Trello/)).toBeTruthy();                         // o campo segue para corrigir
    await click(byRole('button', /^Cancelar$/));
    expect(Array.from(container.querySelectorAll('button')).some((b) => /Anexar ao cartão/.test(b.textContent ?? ''))).toBe(true);
  });

  it('erro de carga sem dado: estado de erro com "Tentar de novo", que lê de novo', async () => {
    let falha = true;
    backend.on('GET', LISTA, () => (falha ? apiError(500, 'boom', 'Falha interna.') : json(pagina([IMAGEM]))));
    await abrir();
    await waitFor(() => text().includes('Não foi possível carregar os anexos'));
    expect(text()).toContain('Falha interna.');
    falha = false;
    await click(byRole('button', /Tentar de novo/));
    await waitFor(() => text().includes('Mostrando 1 de 1'));
    expect(text()).not.toContain('Nenhum anexo ainda');
  });
});

describe('anexos.ts', () => {
  it('tamanho legível em português', () => {
    expect(tamanhoLegivel(812)).toBe('812 B');
    expect(tamanhoLegivel(125_952)).toBe('123 KB');
    expect(tamanhoLegivel(1_572_864)).toBe('1,5 MB');
    expect(tamanhoLegivel(2 * 1024 * 1024)).toBe('2 MB');
  });

  it('o cartão vem pelo link, pelo código curto ou pelo id; o resto nem sai', () => {
    expect(validarCartao(`  ${CARTAO.toUpperCase()} `)).toEqual({ ok: true, card: CARTAO });
    expect(validarCartao('https://trello.com/c/AbCdEf12/123-titulo-do-cartao')).toEqual({ ok: true, card: 'AbCdEf12' });
    expect(validarCartao(' https://trello.com/c/AbCdEf12 ')).toEqual({ ok: true, card: 'AbCdEf12' });
    expect(validarCartao('AbCdEf12')).toEqual({ ok: true, card: 'AbCdEf12' });             // o código curto mantém a caixa
    expect(validarCartao('')).toMatchObject({ ok: false });
    expect(validarCartao('g'.repeat(24))).toMatchObject({ ok: false });
    expect(validarCartao('https://example.com/c/AbCdEf12')).toMatchObject({ ok: false });
    expect(validarCartao('../x')).toMatchObject({ ok: false });
  });

  it('o período vira o limite inferior em ISO; "todo o período" não manda nada', () => {
    const agora = Date.parse('2026-10-04T12:00:00Z');
    expect(desdeDoPeriodo('24h', agora)).toBe('2026-10-03T12:00:00.000Z');
    expect(desdeDoPeriodo('tudo', agora)).toBeUndefined();
  });
});
