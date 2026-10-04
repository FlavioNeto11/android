// @vitest-environment jsdom
/**
 * Tela Canais (item 32.5): contra o formato de `GET /api/canais/estado` (`backend/tests/test_canais_estado.py` trava as
 * chaves), com respostas falsas (FakeBackend) — prova `simulated`, nunca `real`.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type { CanaisEstado } from '../../api/types';
import { FakeBackend, apiError, byRole, click, installBrowserStubs, json, text, waitFor } from '../../test/harness';
import { CanaisPage } from './CanaisPage';
import {
  falhaVigente, frasesDaFila, frasesDasEntradas, frasesDosCartoes, seloDoAviso, seloDoTrello, textoDoProblema,
} from './resumo';

const FILA_ZERO = { pendente: 0, enviando: 0, enviado: 0, falhou: 0, incerto: 0, descartado: 0 };
const ENTRADAS_ZERO = { recebida: 0, ignorada: 0, recusada: 0, limitada: 0, orquestradora: 0, pergunta: 0, executando: 0,
                        feita: 0, cancelada: 0, falhou: 0, aviso: 0 };

function desligado(): CanaisEstado {
  return {
    gerado_em: new Date().toISOString(),
    aviso_telegram: { ligado: false, segredo_presente: false, fila: { ...FILA_ZERO }, ultimo_envio_em: null, ultima_falha: null, problemas: [] },
    conversa_telegram: { ligada: false, ultima_leitura_em: null, entradas: { ...ENTRADAS_ZERO }, problemas: [] },
    trello: { ligado: false, webhook_ligado: false, cadastro_automatico: false, ultima_reconciliacao_em: null,
              cartoes: { ativo: 0, arquivado: 0, criando: 0 }, entradas: { ...ENTRADAS_ZERO }, problemas: [] },
  };
}

function ativo(): CanaisEstado {
  const minutosAtras = (m: number) => new Date(Date.now() - m * 60_000).toISOString();
  return {
    gerado_em: new Date().toISOString(),
    aviso_telegram: { ligado: true, segredo_presente: true, fila: { ...FILA_ZERO, enviado: 3, pendente: 1 },
                      ultimo_envio_em: minutosAtras(7), ultima_falha: { em: minutosAtras(2), motivo: '429' }, problemas: [] },
    conversa_telegram: { ligada: true, ultima_leitura_em: minutosAtras(1), entradas: { ...ENTRADAS_ZERO, feita: 4, recusada: 1 },
                         problemas: [] },
    trello: { ligado: true, webhook_ligado: true, cadastro_automatico: false, ultima_reconciliacao_em: minutosAtras(3),
              cartoes: { ativo: 12, arquivado: 3, criando: 0 }, entradas: { ...ENTRADAS_ZERO, aviso: 2 },
              problemas: ['trello_webhook_inativo'] },
  };
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
  await act(async () => { root.render(<CanaisPage />); });
}

describe('CanaisPage', () => {
  it('mostra os três cartões com selo, "há X min" e as contagens em português', async () => {
    backend.on('GET', /^\/api\/canais\/estado$/, () => json(ativo()));
    await abrir();
    await waitFor(() => text().includes('Aviso pelo Telegram'));
    const aviso = text(byRole('region', /Aviso pelo Telegram/));
    expect(aviso).toContain('Com problema');                            // a falha (429) é mais recente que o último envio
    expect(aviso).toContain('3 avisos enviados, 1 na fila');
    expect(aviso).toContain('há 7 min');
    expect(aviso).toContain('Telegram pediu para esperar (429), há 2 min');
    const conversa = text(byRole('region', /Conversa pelo Telegram/));
    expect(conversa).toContain('Ligado');
    expect(conversa).toContain('5 entradas: 4 atendidas, 1 recusada.');
    expect(conversa).toContain('há 1 min');
    const trello = text(byRole('region', /Trello/));
    expect(trello).toContain('Com problema');
    expect(trello).toContain('12 cartões ativos, 3 arquivados');
    expect(trello).toContain('O webhook do Trello não está cadastrado');
    expect(trello).toContain('2 avisos do webhook esperando leitura');
    expect(text()).not.toContain('trello_webhook_inativo');              // o código vira frase, não aparece cru
  });

  it('tudo desligado: selo "Desligado" nos três e a frase de cada um, sem erro nem tabela', async () => {
    backend.on('GET', /^\/api\/canais\/estado$/, () => json(desligado()));
    await abrir();
    await waitFor(() => text().includes('Aviso pelo Telegram'));
    for (const [nome, frase] of [['Aviso pelo Telegram', 'nenhum aviso sai da Central'], ['Conversa pelo Telegram', 'não lê o que você escreve'],
                                 ['Trello', 'não cria nem lê cartões']] as const) {
      const cartao = text(byRole('region', new RegExp(nome)));
      expect(cartao).toContain('Desligad');
      expect(cartao).toContain(frase);
      expect(cartao).not.toContain('Com problema');
    }
    expect(text()).not.toContain('Não foi possível');
  });

  it('erro da rota sem dado: estado de erro com "Tentar de novo", que lê de novo', async () => {
    let falha = true;
    backend.on('GET', /^\/api\/canais\/estado$/, () => (falha ? apiError(500, 'boom', 'Falha interna.') : json(desligado())));
    await abrir();
    await waitFor(() => text().includes('Não foi possível carregar o estado dos canais'));
    expect(text()).toContain('Falha interna.');
    falha = false;
    await click(byRole('button', /Tentar de novo/));
    await waitFor(() => text().includes('Aviso pelo Telegram'));
    expect(backend.callsTo('GET', /canais\/estado$/)).toHaveLength(2);
  });

  it('só lê: nenhum botão de escrita e nenhuma chamada além do GET', async () => {
    backend.on('GET', /^\/api\/canais\/estado$/, () => json(ativo()));
    await abrir();
    await waitFor(() => text().includes('Aviso pelo Telegram'));
    expect(container.querySelectorAll('button')).toHaveLength(0);
    expect(backend.calls.every((c) => c.method === 'GET')).toBe(true);
  });
});

describe('resumo', () => {
  it('frases de contagem', () => {
    expect(frasesDaFila(FILA_ZERO)).toBe('Nenhum aviso ainda.');
    expect(frasesDaFila({ ...FILA_ZERO, enviado: 1, falhou: 2 })).toBe('1 aviso enviado, 2 falharam');
    expect(frasesDasEntradas(ENTRADAS_ZERO)).toBe('Nenhuma mensagem recebida.');
    expect(frasesDasEntradas({ ...ENTRADAS_ZERO, pergunta: 1 })).toBe('1 entrada: 1 esperando a sua confirmação.');
    expect(frasesDosCartoes({ ativo: 1, arquivado: 0, criando: 0 })).toBe('1 cartão ativo');
    expect(frasesDosCartoes({})).toBe('Nenhum cartão espelhado.');
  });

  it('a falha que ficou para trás de um envio não pinta o canal de problema', () => {
    const base = ativo().aviso_telegram;
    const velha = { ...base, ultimo_envio_em: new Date().toISOString(), ultima_falha: { em: '2026-01-01T00:00:00Z', motivo: 'rede' as const } };
    expect(falhaVigente(velha)).toBe(false);
    expect(seloDoAviso(velha)).toBe('ligado');
    expect(seloDoAviso({ ...velha, segredo_presente: false })).toBe('problema');
    expect(seloDoAviso({ ...velha, ligado: false })).toBe('desligado');
    expect(seloDoTrello({ ...ativo().trello, problemas: [] })).toBe('ligado');
  });

  it('código de problema desconhecido cai no texto genérico com o código', () => {
    expect(textoDoProblema('trello_recusado')).toContain('recusou');
    expect(textoDoProblema('coisa_nova')).toBe('Problema registrado (coisa_nova).');
  });
});
