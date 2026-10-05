// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type { ItemDaPorta, PreviaDaPorta } from '../../api/types';
import { FakeBackend, apiError, byRole, click, installBrowserStubs, json, setValue, text, waitFor } from '../../test/harness';
import { PortaDoPlano, ValidadeDoPlano, frasesDoRenovar, tamanhoDoTexto, temChaveSolta, temVariavel } from './PortaDoPlano';

let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

const CHAVE = 'a'.repeat(64);
/** 30.68: a chave da prévia do texto EDITADO (outra que a da prévia do plano). */
const CHAVE_EDITADA = 'b'.repeat(64);

function item(over: Partial<ItemDaPorta>): ItemDaPorta {
  return {
    objective_id: 'run-p:android-01', step_id: 'run-p:android-01:v1:dm', aparelho: 'android-01', titulo: 'Mandar DM',
    persona_rotulo: '@lucas', profile_id: 'p-1', app: 'ig', acao: 'SEND_MESSAGE', alvo: 'ana', selo: 'aprovacao',
    motivo: 'primeira mensagem para quem nunca escreveu', dica: '', retry_at: null, texto: 'oi, tudo bem?',
    texto_na_execucao: false, tem_imagem: false, imagem_sha256: null, chave: CHAVE, dependentes: [], falhou: false,
    ...over,
  };
}

function travado(): boolean {
  const botao = byRole('button', /Aprovar 1 e iniciar/);
  return botao.getAttribute('aria-disabled') === 'true' || (botao as HTMLButtonElement).disabled;
}

async function sair(el: HTMLElement): Promise<void> {
  await act(async () => { el.dispatchEvent(new FocusEvent('focusout', { bubbles: true })); });
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
  // 30.68: por padrão, o texto editado segue pedindo o aval, com a chave do texto editado.
  backend.on('POST', /\/runs\/run-p\/porta\/item$/, (c) => {
    const corpo = c.body as { step_id: string; texto: string };
    return json({ step_id: corpo.step_id, texto: corpo.texto.trim(),
                  item: item({ step_id: corpo.step_id, texto: corpo.texto.trim(), chave: CHAVE_EDITADA }) });
  });
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
    expect(t).toContain('4 ações em 1 aparelho: 1 liberada, 1 pede seu aval, 1 não será feita, 1 decide na execução');
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
    await waitFor(() => expect(travado()).toBe(false));
    await click(byRole('button', /Aprovar 1 e iniciar/));
    await waitFor(() => expect(backend.callsTo('POST', /aprovar-plano$/)).toHaveLength(1));
    const corpo = backend.callsTo('POST', /aprovar-plano$/)[0]?.body as { aprovar: unknown[]; tirar: string[] };
    expect(corpo.aprovar).toEqual([{ step_id: 'run-p:android-01:v1:dm', chave: CHAVE_EDITADA, texto: 'oi! tudo certo?' }]);
    expect(corpo.tirar).toEqual(['run-p:android-01:v1:like']);
  });

  it('texto em branco trava o Aprovar com o motivo', async () => {
    await montar();
    await setValue(byRole('textbox', /Texto de SEND_MESSAGE/) as HTMLTextAreaElement, '   ');
    expect(travado()).toBe(true);
    expect(backend.callsTo('POST', /aprovar-plano$/)).toHaveLength(0);
    expect(backend.callsTo('POST', /porta\/item$/)).toHaveLength(0);     // a trava local não pergunta à porta
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

  it('nota da Ferramentas: o marcador com acento é variável, como no servidor; o tamanho conta ponto de código', () => {
    expect(temVariavel('oi {ação}')).toBe(true);
    expect(temVariavel('{número}')).toBe(true);
    expect(temVariavel('{ nome }')).toBe(false);
    expect(tamanhoDoTexto('😀'.repeat(2200))).toBe(2200);
    expect(temChaveSolta('oi { nome }')).toBe(true);
    expect(temChaveSolta('oi {nome}')).toBe(false);
  });

  it('a chave solta no texto avisa sem travar o Aprovar', async () => {
    await montar();
    await setValue(byRole('textbox', /Texto de SEND_MESSAGE/) as HTMLTextAreaElement, 'oi { nome }');
    expect(text(container)).toContain('ele sai exatamente assim');
    await waitFor(() => expect(travado()).toBe(false));
  });

  it('o emoji perto do limite conta como o servidor conta', async () => {
    await montar();
    await setValue(byRole('textbox', /Texto de SEND_MESSAGE/) as HTMLTextAreaElement, '😀'.repeat(2200));
    await waitFor(() => expect(travado()).toBe(false));
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
    await waitFor(() => expect(travado()).toBe(false));
    await click(byRole('button', /Aprovar 1 e iniciar/));
    await waitFor(() => expect(text(container)).toContain('com o texto editado: já comentado'));
    expect((byRole('textbox', /Texto de SEND_MESSAGE/) as HTMLTextAreaElement).value).toBe('já comentado antes');
  });
});

describe('29.30 no cartão do plano', () => {
  it('a publicação mostra a imagem que vai ao feed', async () => {
    backend.on('GET', /\/runs\/run-p\/porta$/, () => json(previa([
      item({ step_id: 'run-p:android-01:v1:pub', acao: 'CREATE_POST', alvo: null, tem_imagem: true, image_id: 'img-1',
             imagem_sha256: 'c'.repeat(64), rotulo_ia: true }),
    ])));
    await montar();
    const img = container.querySelector('img');
    expect(img?.getAttribute('alt')).toBe('Imagem que CREATE_POST publica');
    expect(img?.getAttribute('src')).toContain('img-1');
    expect(text(container)).toContain('com rótulo de IA');       // 29.79
  });

  it('29.81: sem rótulo por foto real (o dono disse) é neutro; sem rótulo porque ninguém disse é aviso, com o caminho', async () => {
    backend.on('GET', /\/runs\/run-p\/porta$/, () => json(previa([
      item({ step_id: 'run-p:android-01:v1:real', acao: 'CREATE_POST', alvo: null, tem_imagem: true, image_id: 'img-r',
             imagem_sha256: 'c'.repeat(64), rotulo_ia: false, rotulo_ia_motivo: 'foto_real' }),
      item({ step_id: 'run-p:android-01:v1:nada', acao: 'CREATE_POST', alvo: null, tem_imagem: true, image_id: 'img-n',
             imagem_sha256: 'd'.repeat(64), rotulo_ia: false, rotulo_ia_motivo: 'nao_informado' }),
    ])));
    await montar();
    const itens = Array.from(container.querySelectorAll('li')).filter((li) => li.querySelector('img'));
    expect(itens).toHaveLength(2);
    expect(text(itens[0]!)).toContain('sem rótulo de IA (foto real, informado por você)');
    expect(itens[0]!.querySelector('a[href*="imagens"]')).toBeNull();          // o dono já disse: nada a fazer
    expect(text(itens[1]!)).toContain('sem rótulo de IA: ninguém informou se a foto é de IA');
    const caminho = itens[1]!.querySelector('a[href*="imagens"]');
    expect(caminho?.getAttribute('href')).toBe('#/personas/p-1/imagens');
    expect(text(container)).not.toContain('com rótulo de IA');
  });
});

describe('30.68: a prévia do texto editado antes do sim', () => {
  it('trava o Aprovar até a prévia do texto voltar, mostra o motivo novo e manda a chave do texto editado', async () => {
    let soltar: (() => void) | null = null;
    backend.on('POST', /\/runs\/run-p\/porta\/item$/, (c) => new Promise<Response>((ok) => {
      const corpo = c.body as { step_id: string; texto: string };
      soltar = () => ok(json({ step_id: corpo.step_id, texto: corpo.texto,
        item: item({ texto: corpo.texto, chave: CHAVE_EDITADA, motivo: 'esta conta já mandou ESTA mensagem para @ana' }) }));
    }));
    backend.on('POST', /\/runs\/run-p\/aprovar-plano$/, () => json({
      run: { id: 'run-p', status: 'running' }, aprovacoes: ['apr-1'], tiradas: [], validade_ate: '2026-10-05T21:00:00.000Z',
    }));
    await montar();
    const campo = byRole('textbox', /Texto de SEND_MESSAGE/) as HTMLTextAreaElement;
    await setValue(campo, '  oi de novo  ');
    expect(travado()).toBe(true);                                    // antes mesmo de a espera da digitação acabar
    await sair(campo);                                               // sair do campo confere na hora
    await waitFor(() => expect(backend.callsTo('POST', /porta\/item$/)).toHaveLength(1));
    expect(backend.callsTo('POST', /porta\/item$/)[0]?.body).toEqual({ step_id: 'run-p:android-01:v1:dm', texto: 'oi de novo' });
    expect(text(container)).toContain('Conferindo este texto na porta');
    expect(travado()).toBe(true);
    await act(async () => soltar?.());
    await waitFor(() => expect(text(container)).toContain('Com este texto: pede seu aval — esta conta já mandou ESTA mensagem para @ana'));
    expect(travado()).toBe(false);
    // A espera da digitação também dispara, mas o mesmo texto não se pede duas vezes.
    await new Promise((r) => setTimeout(r, 600));
    expect(backend.callsTo('POST', /porta\/item$/)).toHaveLength(1);
    await click(byRole('button', /Aprovar 1 e iniciar/));
    await waitFor(() => expect(backend.callsTo('POST', /aprovar-plano$/)).toHaveLength(1));
    const corpo = backend.callsTo('POST', /aprovar-plano$/)[0]?.body as { aprovar: unknown[] };
    expect(corpo.aprovar).toEqual([{ step_id: 'run-p:android-01:v1:dm', chave: CHAVE_EDITADA, texto: 'oi de novo' }]);
  });

  it('o item que deixa de ser 🔒 com o texto editado diz por quê e não deixa aprovar', async () => {
    backend.on('POST', /\/runs\/run-p\/porta\/item$/, (c) => json({ step_id: 'run-p:android-01:v1:dm',
      texto: (c.body as { texto: string }).texto,
      item: item({ selo: 'recusado', chave: null, motivo: 'esta conta já comentou este texto' }) }));
    await montar();
    await setValue(byRole('textbox', /Texto de SEND_MESSAGE/) as HTMLTextAreaElement, 'outro texto');
    await waitFor(() => expect(text(container)).toContain('Com este texto, a ação não pede mais o seu aval aqui (não será feita): esta conta já comentou este texto'));
    expect(travado()).toBe(true);
  });

  it('a resposta do texto antigo não vale para o texto novo', async () => {
    const pendentes: { texto: string; ok: (r: Response) => void }[] = [];
    backend.on('POST', /\/runs\/run-p\/porta\/item$/, (c) => new Promise<Response>((ok) => {
      pendentes.push({ texto: (c.body as { texto: string }).texto, ok });
    }));
    await montar();
    const campo = byRole('textbox', /Texto de SEND_MESSAGE/) as HTMLTextAreaElement;
    await setValue(campo, 'primeiro');
    await sair(campo);
    await setValue(campo, 'segundo');
    await sair(campo);
    await waitFor(() => expect(pendentes).toHaveLength(2));
    const [velho, novo] = pendentes;
    await act(async () => novo?.ok(json({ step_id: 'run-p:android-01:v1:dm', texto: 'segundo',
      item: item({ texto: 'segundo', chave: CHAVE_EDITADA }) })));
    await act(async () => velho?.ok(json({ step_id: 'run-p:android-01:v1:dm', texto: 'primeiro',
      item: item({ selo: 'recusado', chave: null, motivo: 'velho' }) })));
    await waitFor(() => expect(travado()).toBe(false));
    expect(text(container)).not.toContain('velho');
  });

  it('erro ao conferir trava com o motivo e sair do campo tenta de novo', async () => {
    let vez = 0;
    backend.on('POST', /\/runs\/run-p\/porta\/item$/, (c) => {
      vez += 1;
      return vez === 1 ? apiError(500, 'internal', 'caiu')
        : json({ step_id: 'run-p:android-01:v1:dm', texto: (c.body as { texto: string }).texto,
                 item: item({ chave: CHAVE_EDITADA }) });
    });
    await montar();
    const campo = byRole('textbox', /Texto de SEND_MESSAGE/) as HTMLTextAreaElement;
    await setValue(campo, 'oi outra vez');
    await sair(campo);
    await waitFor(() => expect(text(container)).toContain('Não deu para conferir este texto'));
    expect(travado()).toBe(true);
    await sair(campo);
    await waitFor(() => expect(travado()).toBe(false));
  });
});
