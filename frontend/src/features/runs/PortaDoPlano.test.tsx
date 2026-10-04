// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type { ItemDaPorta, PreviaDaPorta } from '../../api/types';
import { FakeBackend, apiError, byRole, click, installBrowserStubs, json, setValue, text, waitFor } from '../../test/harness';
import { PortaDoPlano, ValidadeDoPlano, frasesDoRenovar, temVariavel } from './PortaDoPlano';

let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

const CHAVE = 'a'.repeat(64);

function item(over: Partial<ItemDaPorta>): ItemDaPorta {
  return {
    objective_id: 'run-p:android-01', step_id: 'run-p:android-01:v1:dm', aparelho: 'android-01', titulo: 'Mandar DM',
    persona_rotulo: '@lucas', profile_id: 'p-1', app: 'ig', acao: 'SEND_MESSAGE', alvo: 'ana', selo: 'aprovacao',
    motivo: 'primeira mensagem para quem nunca escreveu', dica: '', retry_at: null, texto: 'oi, tudo bem?',
    texto_na_execucao: false, tem_imagem: false, imagem_sha256: null, chave: CHAVE, dependentes: [], falhou: false,
    ...over,
  };
}

function previa(itens: ItemDaPorta[], over: Partial<PreviaDaPorta> = {}): PreviaDaPorta {
  return {
    run_id: 'run-p', hash_do_plano: 'h', validade_ate: '2026-10-05T21:00:00.000Z', custo_rascunhos_usd: 0,
    estimativa: true, parcial: false, total: false, itens,
    na_execucao: { textos_da_tela: 0, itens_for_each: 0, sempre: ['desafio', '2FA', 'CAPTCHA'] }, ...over,
  };
}

const PLANO = previa([
  item({}),
  item({ step_id: 'run-p:android-01:v1:like', acao: 'LIKE_POST', selo: 'permitido', texto: null, chave: null, motivo: '',
         dependentes: ['run-p:android-01:v1:depois'] }),
  item({ step_id: 'run-p:android-01:v1:resp', acao: 'REPLY_COMMENT', selo: 'na_execucao', chave: null,
         motivo: 'o item ainda não está fechado' }),
  item({ step_id: 'run-p:android-01:v1:seguir', acao: 'FOLLOW', selo: 'recusado', texto: null, chave: null,
         motivo: 'uma conta por alvo' }),
]);

async function montar(): Promise<void> {
  await act(async () => root.render(<PortaDoPlano runId="run-p" />));
  await waitFor(() => expect(text(container)).toContain('pede seu aval'));
}

beforeAll(() => installBrowserStubs());
beforeEach(() => {
  backend = new FakeBackend();
  backend.install();
  backend.on('GET', /\/runs\/run-p\/porta$/, () => json(PLANO));
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe('PortaDoPlano (30.61)', () => {
  it('resume os selos, mostra o recusado em cinza e o que fica para a execução', async () => {
    await montar();
    const t = text(container);
    expect(t).toContain('4 ação(ões) em 1 aparelho(s): 1 liberada(s), 1 pede(m) seu aval, 1 não será(ão) feita(s), 1 decide(m) na execução');
    expect(t).toContain('não será feita');
    expect(t).toContain('Ainda vão pedir você na execução');
    expect(t).toContain('Sempre com você: desafio, 2FA, CAPTCHA');
    expect(byRole('button', /Aprovar 1 e iniciar/)).toBeTruthy();
  });

  it('aprova com a chave vista, manda o texto editado e as tiradas', async () => {
    backend.on('POST', /\/runs\/run-p\/aprovar-plano$/, () => json({
      run: { id: 'run-p', status: 'running' }, aprovacoes: ['apr-1'], tiradas: [], validade_ate: '2026-10-05T21:00:00.000Z',
    }));
    await montar();
    await setValue(byRole('textbox', /Texto de SEND_MESSAGE para @ana/) as HTMLTextAreaElement, 'oi! tudo certo?');
    await click(byRole('button', /Não fazer esta — LIKE_POST/));
    expect(text(container)).toContain('não será feita');
    await click(byRole('button', /Aprovar 1 e iniciar/));
    await waitFor(() => expect(backend.callsTo('POST', /aprovar-plano$/)).toHaveLength(1));
    const corpo = backend.callsTo('POST', /aprovar-plano$/)[0]?.body as { aprovar: unknown[]; tirar: string[] };
    expect(corpo.aprovar).toEqual([{ step_id: 'run-p:android-01:v1:dm', chave: CHAVE, texto: 'oi! tudo certo?' }]);
    expect(corpo.tirar).toEqual(['run-p:android-01:v1:like']);
  });

  it('texto em branco trava o Aprovar com o motivo', async () => {
    await montar();
    await setValue(byRole('textbox', /Texto de SEND_MESSAGE/) as HTMLTextAreaElement, '   ');
    const botao = byRole('button', /Aprovar 1 e iniciar/);
    expect(botao.getAttribute('aria-disabled') === 'true' || (botao as HTMLButtonElement).disabled).toBe(true);
    expect(backend.callsTo('POST', /aprovar-plano$/)).toHaveLength(0);
  });

  it('plano_mudou mostra o que mudou e troca pela prévia nova, sem gravar', async () => {
    const nova = previa([item({ selo: 'na_execucao', chave: null, motivo: 'o texto mudou' })]);
    backend.on('POST', /\/runs\/run-p\/aprovar-plano$/, () => json({ detail: {
      code: 'plano_mudou', message: '1 item mudou', mudaram: [{ step_id: 'run-p:android-01:v1:dm', selo: 'na_execucao',
        motivo: 'o texto mudou' }], previa: nova,
    } }, 409));
    await montar();
    await click(byRole('button', /Aprovar 1 e iniciar/));
    await waitFor(() => expect(text(container)).toContain('O plano mudou desde a prévia: 1 item(ns)'));
    expect(text(container)).toContain('Mandar DM: o texto mudou');
    expect(byRole('button', /Iniciar e decidir na execução/)).toBeTruthy();
  });

  it('sem prévia, explica e oferece calcular de novo', async () => {
    backend.on('GET', /\/runs\/run-p\/porta$/, () => apiError(500, 'internal', 'falhou'));
    await act(async () => root.render(<PortaDoPlano runId="run-p" />));
    await waitFor(() => expect(text(container)).toContain('A prévia da porta não pôde ser calculada'));
    expect(text(container)).toContain('a porta decide cada item na execução');
    expect(byRole('button', /Calcular de novo/)).toBeTruthy();
  });
});

describe('ValidadeDoPlano (30.61, Renovar)', () => {
  it('o caso misto diz quantos renovaram e quantos voltam para rever', () => {
    expect(frasesDoRenovar(2, 0)).toBe('2 sim(ns) do plano renovado(s).');
    expect(frasesDoRenovar(1, 2)).toBe('1 renovado(s), 2 vencido(s) voltam para você rever.');
  });

  it('mostra a validade dos sins do plano e renova pela rota', async () => {
    backend.on('GET', /\/approvals/, () => json([
      { id: 'apr-1', origem: 'plano', interaction_id: null, expires_at: '2026-10-05T21:00:00.000Z', status: 'approved' },
      { id: 'apr-2', origem: 'execucao', interaction_id: null, expires_at: null, status: 'approved' },
    ]));
    backend.on('POST', /\/runs\/run-r\/porta\/renovar$/, () => json({ run_id: 'run-r', renovadas: 1, vencidas: 0,
      validade_ate: '2026-10-06T21:00:00.000Z' }));
    await act(async () => root.render(<ValidadeDoPlano runId="run-r" />));
    await waitFor(() => expect(text(container)).toContain('1 sim(ns) dado(s) na prévia valem até'));
    await click(byRole('button', /Renovar/));
    await waitFor(() => expect(backend.callsTo('POST', /porta\/renovar$/)).toHaveLength(1));
  });

  it('sem sim do plano em aberto, não aparece', async () => {
    backend.on('GET', /\/approvals/, () => json([]));
    await act(async () => root.render(<ValidadeDoPlano runId="run-r" />));
    await waitFor(() => expect(backend.callsTo('GET', /\/approvals/)).toHaveLength(1));
    expect(text(container)).toBe('');
  });
});

describe('Revisão do painel (F1, F2, B2)', () => {
  it('F1: depois do plano_mudou, a tirada que sumiu da prévia nova não volta a ir (sem laço de 409)', async () => {
    const nova = previa([item({})]);
    let chamadas = 0;
    backend.on('POST', /\/runs\/run-p\/aprovar-plano$/, () => {
      chamadas += 1;
      return chamadas === 1
        ? json({ detail: { code: 'plano_mudou', message: 'mudou', previa: nova,
          mudaram: [{ step_id: 'run-p:android-01:v1:like', selo: null, motivo: 'a etapa não está mais no plano' }] } }, 409)
        : json({ run: { id: 'run-p', status: 'running' }, aprovacoes: ['apr-1'], tiradas: [], validade_ate: '2026-10-05T21:00:00.000Z' });
    });
    await montar();
    await click(byRole('button', /Não fazer esta — LIKE_POST/));
    await click(byRole('button', /Aprovar 1 e iniciar/));
    await waitFor(() => expect(text(container)).toContain('O plano mudou desde a prévia'));
    await click(byRole('button', /Aprovar 1 e iniciar/));
    await waitFor(() => expect(backend.callsTo('POST', /aprovar-plano$/)).toHaveLength(2));
    const segundo = backend.callsTo('POST', /aprovar-plano$/)[1]?.body as { tirar: string[] };
    expect(segundo.tirar).toEqual([]);
  });

  it('F2: o sim vencido que a faxina não marcou não aparece como válido', async () => {
    backend.on('GET', /\/approvals/, () => json([
      { id: 'apr-1', origem: 'plano', interaction_id: null, expires_at: '2020-01-01T00:00:00.000Z', status: 'approved' },
    ]));
    await act(async () => root.render(<ValidadeDoPlano runId="run-r" />));
    await waitFor(() => expect(text(container)).toContain('1 sim(ns) dado(s) na prévia venceu(ram) e volta(m) para você'));
    expect(text(container)).not.toContain('valem até');
  });

  it('B2: só o marcador de modelo é variável', () => {
    expect(temVariavel('oi {item}')).toBe(true);
    expect(temVariavel('{{saida:nome}}')).toBe(true);
    expect(temVariavel('oi :-{ tchau')).toBe(false);
  });
});

describe('Revisão do painel (N2)', () => {
  it('o 409 pelo texto editado mantém a edição no campo para corrigir', async () => {
    const nova = previa([item({})]);
    backend.on('POST', /\/runs\/run-p\/aprovar-plano$/, () => json({ detail: {
      code: 'plano_mudou', message: 'mudou', previa: nova,
      mudaram: [{ step_id: 'run-p:android-01:v1:dm', selo: 'recusado', motivo: 'com o texto editado: já comentado' }],
    } }, 409));
    await montar();
    await setValue(byRole('textbox', /Texto de SEND_MESSAGE/) as HTMLTextAreaElement, 'já comentado antes');
    await click(byRole('button', /Aprovar 1 e iniciar/));
    await waitFor(() => expect(text(container)).toContain('com o texto editado: já comentado'));
    expect((byRole('textbox', /Texto de SEND_MESSAGE/) as HTMLTextAreaElement).value).toBe('já comentado antes');
  });
});
