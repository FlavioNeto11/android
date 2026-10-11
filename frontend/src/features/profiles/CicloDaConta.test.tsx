// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type { CicloDaConta as Ciclo } from '../../api/types';
import { FakeBackend, apiError, byRole, click, installBrowserStubs, json, text, waitFor } from '../../test/harness';
import { CicloDaConta, CorpoDoCiclo, desfechoEmPalavras, minutosEmPalavras, origemEmPalavras, referenciaEmPalavras } from './CicloDaConta';

/**
 * 31.346 (adendos v1.145 e v1.149): a ficha mostra o ciclo da conta do Instagram, só leitura. Prova `simulated`.
 */

function ciclo(over: Partial<Ciclo> = {}): Ciclo {
  return {
    account_id: 'acc-1', igfarm_account_id: 'igf-9', origem: 'igfarm', referencia: 'criacao', criada_em: '2026-10-10T10:00:00Z',
    registrada_em: '2026-10-10T10:01:00Z', estado: 'ativa', retirada_em: null, minutos_ate_o_primeiro_contato: 3.2,
    ultimo_desfecho: 'session_ready',
    contatos: [{ iniciado_em: '2026-10-10T10:03:12Z', minutos_desde_a_criacao: 3.2, desfecho: 'session_ready', etapa: null, detalhe: null }],
    ...over,
  };
}

describe('regras puras', () => {
  it('minutos em palavras, sem inventar o que não se mediu', () => {
    expect(minutosEmPalavras(null)).toBe('não medido');
    expect(minutosEmPalavras(undefined)).toBe('não medido');
    expect(minutosEmPalavras(Number.NaN)).toBe('não medido');
    expect(minutosEmPalavras(0.4)).toBe('menos de 1 min');
    expect(minutosEmPalavras(42.4)).toBe('42 min');
    expect(minutosEmPalavras(185)).toBe('3 h 05 min');
    expect(minutosEmPalavras(1440 + 4 * 60)).toBe('1 d 4 h');
  });

  it('desfechos conhecidos em palavras; o desconhecido aparece com o código do servidor', () => {
    expect(desfechoEmPalavras('conta_nao_encontrada').texto).toBe('Conta não encontrada no app');
    expect(desfechoEmPalavras('confirmada').tom).toBe('success');
    expect(desfechoEmPalavras('parada').texto).toBe('Cadastro parado');
    expect(desfechoEmPalavras('desfecho_novo_do_motor')).toEqual({ texto: 'desfecho_novo_do_motor', tom: 'neutral' });
    expect(desfechoEmPalavras(null).texto).toBe('sem desfecho');
  });

  it('origem e referência: igfarm/criação por padrão (central anterior), app/planejamento quando vêm', () => {
    expect(origemEmPalavras({})).toBe('Criada pelo igfarm');
    expect(origemEmPalavras({ origem: 'app' })).toBe('Criada no app (cadastro guiado)');
    expect(referenciaEmPalavras({})).toBe('minutos desde a criação');
    expect(referenciaEmPalavras({ referencia: 'planejamento' })).toContain('desde o planejamento');
  });
});

describe('o ciclo na tela', () => {
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

  const abrir = async () => {
    await act(async () => root.render(<CicloDaConta accountId="acc-1" />));
    await click(container.querySelector('summary') as HTMLElement);
  };

  it('recolhido não chama a API; ao abrir mostra "carregando" e depois o ciclo da conta do igfarm', async () => {
    let solta: (() => void) | null = null;
    backend.on('GET', /\/instagram\/contas\/acc-1\/ciclo$/, () => new Promise((r) => { solta = () => r(json(ciclo())); }));
    await act(async () => root.render(<CicloDaConta accountId="acc-1" />));
    expect(backend.callsTo('GET', /\/ciclo$/)).toHaveLength(0);
    expect(text(container)).toContain('Ciclo da conta');
    await click(container.querySelector('summary') as HTMLElement);
    await waitFor(() => container.querySelector('[aria-busy="true"], [role="status"]') !== null || text(container).includes('Carregando'));
    await act(async () => { solta?.(); });
    await waitFor(() => text(container).includes('Criada pelo igfarm'));
    expect(text(container)).toContain('Primeiro contato com o app: 3 min');
    expect(text(container)).toContain('minutos desde a criação');
    expect(text(container)).toContain('Entrou (sessão pronta)');
    expect(text(container)).toContain('ativa');
    expect(container.querySelectorAll('ul[aria-label="Contatos da conta com o app"] li')).toHaveLength(1);
  });

  it('a conta do app ainda não confirmada conta os minutos do planejamento e mostra o cadastro parado', async () => {
    backend.on('GET', /\/ciclo$/, () => json(ciclo({
      origem: 'app', referencia: 'planejamento', igfarm_account_id: null, criada_em: null, registrada_em: '2026-10-11T09:00:00Z',
      minutos_ate_o_primeiro_contato: 190,
      contatos: [{ iniciado_em: '2026-10-11T12:10:00Z', minutos_desde_a_criacao: 190, desfecho: 'parada', etapa: 'cadastro', detalhe: 'codigo_nao_chegou' }],
    })));
    await abrir();
    await waitFor(() => text(container).includes('Criada no app (cadastro guiado)'));
    expect(text(container)).toContain('planejada em');
    expect(text(container)).toContain('3 h 10 min');
    expect(text(container)).toContain('desde o planejamento');
    expect(text(container)).toContain('Cadastro parado');
    expect(text(container)).toContain('codigo_nao_chegou');
  });

  it('conta retirada mostra a retirada com a hora; conta_nao_encontrada aparece como contato', async () => {
    backend.on('GET', /\/ciclo$/, () => json(ciclo({
      estado: 'retirada', retirada_em: '2026-10-10T19:02:54Z', minutos_ate_o_primeiro_contato: 1,
      contatos: [
        { iniciado_em: '2026-10-10T10:01:00Z', minutos_desde_a_criacao: 1, desfecho: 'conta_nao_encontrada', etapa: null, detalhe: 'usuario <e-mail omitido>' },
        { iniciado_em: '2026-10-10T10:20:00Z', minutos_desde_a_criacao: 20, desfecho: 'auth_challenge', etapa: null, detalhe: null },
      ],
    })));
    await abrir();
    await waitFor(() => text(container).includes('retirada'));
    expect(text(container)).toContain('a conta saiu da plataforma');
    expect(text(container)).toContain('Conta não encontrada no app');
    expect(text(container)).toContain('Pediu um desafio de segurança');
    expect(text(container)).toContain('usuario <e-mail omitido>');
    expect(container.querySelectorAll('ul[aria-label="Contatos da conta com o app"] li')).toHaveLength(2);
  });

  it('sem contatos, diz que ainda não houve; sem primeiro contato, não inventa minutos', async () => {
    backend.on('GET', /\/ciclo$/, () => json(ciclo({ contatos: [], minutos_ate_o_primeiro_contato: null, ultimo_desfecho: null })));
    await abrir();
    await waitFor(() => text(container).includes('Nenhum contato com o app registrado ainda.'));
    expect(text(container)).toContain('Primeiro contato com o app: ainda não houve');
  });

  it('erro da leitura (404 de uma conta sem rastro) mostra o erro com "Tentar de novo", que lê de novo', async () => {
    let n = 0;
    backend.on('GET', /\/ciclo$/, () => { n += 1; return n === 1 ? apiError(404, 'not_found', 'Conta sem rastro.') : json(ciclo()); });
    await abrir();
    await waitFor(() => /Tentar de novo/i.test(container.textContent ?? ''));
    expect(text(container)).not.toContain('Criada pelo igfarm');
    await click(byRole('button', /Tentar de novo/i));
    await waitFor(() => text(container).includes('Criada pelo igfarm'));
    expect(backend.callsTo('GET', /\/ciclo$/)).toHaveLength(2);
  });

  it('o corpo só tem ids, horas, minutos e desfechos: sem @, e-mail ou senha na tela', async () => {
    await act(async () => root.render(<CorpoDoCiclo ciclo={ciclo()} />));
    expect(text(container)).not.toMatch(/@|senha|password/i);
  });
});
