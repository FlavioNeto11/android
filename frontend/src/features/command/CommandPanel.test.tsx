// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { useAppStore } from '../../store/app';
import { initialDataState } from '../../store/reducer';
import { useUiStore } from '../../store/ui';
import type { ResolveTargetsRequest, ResolveTargetsResponse } from '../../api/types';
import { makeBinding, makePersona, makeRun, makeSnapshot } from '../../test/fixtures';
import {
  FakeBackend, allByRole, botaoPronto, byRole, click, flush, installBrowserStubs, json, setValue, text, waitFor,
} from '../../test/harness';
import { CommandPanel, SENHA_NO_COMANDO } from './CommandPanel';

/**
 * ADR-040 (evolução 2, onda E1): a execução NÃO carrega credencial. O campo "Senha para a automação" saiu, o corpo de
 * `POST /runs` não leva `credentials` nem `consent_credentials` (o backend responde 422 a quem ainda os mande), e a
 * senha escrita no texto continua barrada — agora apontando para a conta da persona. Prova `simulated`.
 */
let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

beforeAll(() => installBrowserStubs());

beforeEach(async () => {
  backend = new FakeBackend();
  backend
    .on('POST', /^\/api\/flows\/match$/, () => json(null))
    .on('POST', /^\/api\/runs$/, () => json(makeRun()));
  backend.install();
  window.localStorage.clear();
  // Estas suítes cobrem os modos MANUAIS; o Automático (ADR-050, o padrão) tem os seus testes em SugestaoDeAlvos.
  window.localStorage.setItem('cda.commandTargetV2', JSON.stringify('selecao'));
  useAppStore.setState({ ...initialDataState });
  useAppStore.getState().hydrate(makeSnapshot());
  useUiStore.setState({ selectedIds: ['android-01'] });
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(<CommandPanel />));
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe('Comando sem credencial (ADR-040)', () => {
  it('não há campo de senha no comando', () => {
    expect(text(container)).not.toContain('Senha para a automação');
    expect(container.querySelector('input[type="password"]')).toBeNull();
  });

  it('o corpo de POST /runs não leva credentials nem consent_credentials', async () => {
    await setValue(byRole('textbox', 'Comando em linguagem natural') as HTMLTextAreaElement, 'Abra o app e envie a mensagem');
    await click(byRole('button', /^Executar/));
    await waitFor(() => expect(backend.callsTo('POST', /^\/api\/runs$/)).toHaveLength(1));
    const corpo = backend.callsTo('POST', /^\/api\/runs$/)[0]!.body as Record<string, unknown>;
    expect(corpo).toMatchObject({ command: 'Abra o app e envie a mensagem', instance_ids: ['android-01'], mode: 'execute' });
    expect(corpo).not.toHaveProperty('credentials');
    expect(corpo).not.toHaveProperty('consent_credentials');
  });

  it('senha no texto continua barrada, e o motivo manda guardá-la na conta da persona', async () => {
    await setValue(byRole('textbox', 'Comando em linguagem natural') as HTMLTextAreaElement, 'Entre no portal com senha: hunter2');
    expect(text(container)).toContain(SENHA_NO_COMANDO);
    expect(SENHA_NO_COMANDO).toContain('Contas e acesso');
    await click(byRole('button', /^Executar/));
    expect(backend.callsTo('POST', /^\/api\/runs$/)).toHaveLength(0);
    // Nem o rascunho com a senha vai ao navegador: depois do atraso do rascunho (400 ms), o gravado é vazio.
    await flush(500);
    expect(window.localStorage.getItem('cda.commandDraft')).toBe('""');
  });
});

// ---------------------------------------------------------------- "Por persona" (ADR-044, adendo v0.29)
/**
 * O modo por persona: escolhe-se a pessoa e a política; o painel mostra ANTES quem faz e onde (`POST
 * /runs/targets/resolve`), com a origem de cada alvo e as perguntas, e o envio ecoa em `targets` o que a prévia
 * mostrou. No modo por aparelho, o 409 `alvos_nao_confirmados` abre a mesma prévia para confirmar. Prova `simulated`.
 */
const MARINA = makePersona('ig-1', 'Marina Bastos', {
  username: 'marina.fotografa',
  devices: [makeBinding('android-01', { is_primary: true }), makeBinding('android-03')],
});
const ANDRE_LIMA = makePersona('ig-2', 'Ravenna Lima', { devices: [makeBinding('android-02', { is_primary: true })] });
const ANDRE_SOUZA = makePersona('ig-3', 'Ravenna Souza', { devices: [makeBinding('android-04', { is_primary: true })] });

function previa(over: Partial<ResolveTargetsResponse> = {}): ResolveTargetsResponse {
  return { targets: [], questions: [], command_sem_destinos: 'abra o app', warnings: [], ...over };
}

const campo = () => byRole('textbox', 'Comando em linguagem natural') as HTMLTextAreaElement;
const resolucoes = () => backend.callsTo('POST', /^\/api\/runs\/targets\/resolve$/);
const envios = () => backend.callsTo('POST', /^\/api\/runs$/);

async function modoPorPersona(): Promise<void> {
  backend.on('GET', /^\/api\/personas$/, () => json([MARINA, ANDRE_LIMA, ANDRE_SOUZA]));
  await click(byRole('button', /Por persona/));
  await waitFor(() => expect(text(container)).toContain('Ravenna Souza'));
}

describe('Comando "Por persona"', () => {
  it('manda targets e device_policy ecoados da prévia, com os aparelhos que a prévia mostrou', async () => {
    backend.on('POST', /^\/api\/runs\/targets\/resolve$/, () => json(previa({
      targets: [
        { instance_id: 'android-01', profile_id: 'ig-1', app_id: 'instagram', origem: 'vinculo' },
        { instance_id: 'android-03', profile_id: 'ig-1', app_id: 'instagram', origem: 'vinculo' },
      ],
      warnings: ['A mesma persona age em mais de um aparelho (ig-1): o que a tarefa faz acontece uma vez em CADA um.'],
    })));
    await modoPorPersona();
    await click(byRole('button', /Marina Bastos/));
    await click(byRole('button', /Todos os aparelhos dela/));
    await setValue(campo(), 'abra o app e curta a última foto');
    const alvos = await waitFor(() => {
      const p = byRole('region', 'Prévia dos alvos');
      expect(text(p)).toContain('android-03');
      return p;
    });
    expect(text(alvos)).toContain('origem: vínculo');
    expect(text(alvos)).toContain('uma vez em CADA um');
    expect(text(alvos)).toContain('mais de um aparelho (Marina Bastos)');   // o aviso cita pelo nome, não pelo id
    // A prévia foi pedida com a seleção de agora: a persona e a política.
    const pedido = resolucoes().at(-1)!.body as ResolveTargetsRequest;
    expect(pedido).toMatchObject({ command: 'abra o app e curta a última foto', profile_ids: ['ig-1'], device_policy: 'all' });

    await click(byRole('button', /^Executar/));
    await waitFor(() => expect(envios()).toHaveLength(1));
    const corpo = envios()[0]!.body as Record<string, unknown>;
    expect(corpo).toMatchObject({
      command: 'abra o app e curta a última foto', instance_ids: [], device_policy: 'all', mode: 'execute',
      targets: [{ profile_id: 'ig-1', instance_ids: ['android-01', 'android-03'], app_id: 'instagram' }],
    });
    expect(corpo).not.toHaveProperty('profile_ids');
  });

  it('item 24.6: a prévia mostra o CONJUNTO de apps do alvo (contrato C5), não um só — pelo nome do catálogo', async () => {
    backend.on('POST', /^\/api\/runs\/targets\/resolve$/, () => json(previa({
      // 'qa' e 'notes' são os apps de makeSnapshot() (hidratados no beforeEach): resolve pelo nome, não pelo id.
      targets: [{ instance_id: 'android-01', profile_id: 'ig-1', app_id: 'qa', app_ids: ['qa', 'notes'], origem: 'vinculo' }],
    })));
    await modoPorPersona();
    await click(byRole('button', /Marina Bastos/));
    await click(byRole('button', /Todos os aparelhos dela/));
    await setValue(campo(), 'leia a última nota e comente no QA Messenger');
    const alvos = await waitFor(() => {
      const p = byRole('region', 'Prévia dos alvos');
      expect(text(p)).toContain('android-01');
      return p;
    });
    expect(text(alvos)).toContain('QA Messenger + Notas');
  });

  it('a prévia mostra a origem, o comando sem destinos e a pergunta; a opção clicada vira a seleção', async () => {
    backend.on('POST', /^\/api\/runs\/targets\/resolve$/, (c) => {
      const corpo = c.body as ResolveTargetsRequest;
      if ((corpo.profile_ids ?? []).join() === 'ig-3') {
        return json(previa({ targets: [{ instance_id: 'android-04', profile_id: 'ig-3', app_id: null, origem: 'texto' }],
                             warnings: ['Há destino tirado do texto do comando: confira antes de executar.'] }));
      }
      return json(previa({ questions: [{ code: 'persona_ambigua', question: '“o Ravenna” pode ser Ravenna Lima, Ravenna Souza: qual delas?',
                                         field: 'profile_id', options: ['ig-2', 'ig-3'], instance_id: null, profile_id: null }] }));
    });
    await modoPorPersona();
    await click(byRole('button', /Ravenna Lima/));
    await click(byRole('button', /Ravenna Souza/));
    await setValue(campo(), 'peça para o Ravenna abra o app');
    const perguntas = await waitFor(() => byRole('group', 'Perguntas da prévia'));
    expect(text(perguntas)).toContain('pode ser Ravenna Lima, Ravenna Souza');
    // Pergunta pendente: nada executa.
    expect(byRole('button', /^Executar/).getAttribute('aria-disabled')).toBe('true');
    expect(text(container)).toContain('Responda às perguntas da prévia antes de executar.');

    await click(byRole('button', /Ravenna Souza$/, perguntas));
    await waitFor(() => expect((resolucoes().at(-1)!.body as ResolveTargetsRequest).profile_ids).toEqual(['ig-3']));
    const alvos = await waitFor(() => {
      const p = byRole('region', 'Prévia dos alvos');
      expect(text(p)).toContain('android-04');
      return p;
    });
    expect(text(alvos)).toContain('origem: texto');
    expect(text(alvos)).toContain('a IA recebe');
    expect(text(alvos)).toContain('abra o app');
    expect(text(alvos)).toContain('Há destino tirado do texto');
    // Só a persona escolhida fica marcada.
    expect(byRole('button', /Ravenna Lima/, byRole('group', 'Personas')).getAttribute('aria-pressed')).toBe('false');
    expect(byRole('button', /Ravenna Souza/, byRole('group', 'Personas')).getAttribute('aria-pressed')).toBe('true');
  });

  it('recusa da prévia (sem interseção) fica na tela com o que fazer e bloqueia Executar', async () => {
    backend.on('POST', /^\/api\/runs\/targets\/resolve$/, () => json({ detail: {
      code: 'sem_intersecao', message: 'Nenhum dos aparelhos escolhidos (android-03) é de Ravenna Lima (vinculada a android-02).',
    } }, 409));
    await modoPorPersona();
    await click(byRole('button', /Ravenna Lima/));
    await setValue(campo(), 'abra o app');
    await waitFor(() => expect(text(container)).toContain('Nenhum aparelho em comum'));
    expect(text(container)).toContain('Tire o filtro de aparelhos');
    expect(text(container)).toContain('vinculada a android-02');
    await click(byRole('button', /^Executar/));
    expect(envios()).toHaveLength(0);
  });

  it('409 alvos_nao_confirmados no modo por aparelho abre a prévia e o eco confirma', async () => {
    await act(async () => useUiStore.setState({ selectedIds: ['android-01', 'android-03'] }));
    backend.on('GET', /^\/api\/personas$/, () => json([MARINA]));
    let n = 0;
    backend.on('POST', /^\/api\/runs$/, () => (++n === 1
      ? json({ detail: { code: 'alvos_nao_confirmados', message: 'O comando cita destinos que ainda não foram confirmados: android-03 (ig-1).',
                         targets: [{ instance_id: 'android-03', profile_id: 'ig-1', app_id: null, origem: 'texto' }],
                         command_sem_destinos: 'abra o app' } }, 409)
      : json(makeRun())));
    backend.on('POST', /^\/api\/runs\/targets\/resolve$/, () => json(previa({
      targets: [{ instance_id: 'android-03', profile_id: 'ig-1', app_id: null, origem: 'texto' }],
      warnings: ['Há destino tirado do texto do comando: confira antes de executar.'],
    })));
    await setValue(campo(), 'no android-03 abra o app');
    await click(byRole('button', /^Executar/));
    const caixa = await waitFor(() => byRole('alert', 'Confirmar os destinos do comando'));
    await waitFor(() => expect(text(caixa)).toContain('Marina Bastos'));
    expect(text(caixa)).toContain('android-03');
    expect(text(caixa)).toContain('origem: texto');
    // A prévia inteira foi pedida com a seleção do modo por aparelho.
    expect(resolucoes()[0]!.body).toEqual({ command: 'no android-03 abra o app', instance_ids: ['android-01', 'android-03'] });

    await click(await botaoPronto(/^Confirmar e executar/, caixa));
    await waitFor(() => expect(envios()).toHaveLength(2));
    const eco = envios()[1]!.body as Record<string, unknown>;
    expect(eco).toMatchObject({ instance_ids: [], targets: [{ profile_id: 'ig-1', instance_ids: ['android-03'], app_id: null }] });
    expect(eco).not.toHaveProperty('device_policy');
    await waitFor(() => expect(allByRole('alert', 'Confirmar os destinos do comando')).toHaveLength(0));
  });
});

// ---------------------------------------------------------------- "Distribuir entre servidores" (item 24.6, R9)
/**
 * O Comando deixa de escolher "um app": sem app escolhido, a prévia e o envio vão pelo COMANDO (o backend lê os apps
 * que ele usa, um ou vários), e o painel não preenche mais o app mais comum do parque. Escolher um app só restringe.
 * Prova `simulated` (backend falso).
 */
describe('Comando "Distribuir entre servidores" (item 24.6)', () => {
  const distribuicoes = () => backend.callsTo('POST', /^\/api\/runs\/distribution$/);
  const PREVIA = { requested: 2, missing: 0, reasons: [], per_server: { central: 2 },
                   picks: [{ instance_id: 'android-01', server_id: 'c', server_name: 'central', needs_start: false },
                           { instance_id: 'android-02', server_id: 'c', server_name: 'central', needs_start: false }] };

  async function modoDistribuir(): Promise<void> {
    backend.on('POST', /^\/api\/runs\/distribution$/, () => json(PREVIA));
    await click(byRole('button', /Distribuir entre servidores/));
  }

  it('sem app escolhido, a prévia e o envio vão pelo comando — sem app_id, sem pedir "escolha o app"', async () => {
    await modoDistribuir();
    // Sem comando e sem app, nada a prever: o painel diz que a distribuição sai do comando.
    expect(text(container)).toContain('a distribuição sai dos apps que ele usa');
    expect(text(container)).not.toContain('Escolha o app dos aparelhos');
    expect((byRole('combobox', 'do app') as HTMLSelectElement).value).toBe('');
    await setValue(campo(), 'leia a última nota e mande no QA Messenger');
    await waitFor(() => expect(distribuicoes().length).toBeGreaterThan(0));
    const corpoDaPrevia = distribuicoes().at(-1)!.body as Record<string, unknown>;
    expect(corpoDaPrevia.command).toBe('leia a última nota e mande no QA Messenger');
    // 29.26: o texto vai no corpo; a URL leva só o caminho (query string vira linha de log de acesso).
    expect(distribuicoes().every((c) => c.query.toString() === '')).toBe(true);
    expect(corpoDaPrevia).not.toHaveProperty('app_id');
    await waitFor(() => expect(text(container)).toContain('em central'));

    await click(byRole('button', /^Executar/));
    await waitFor(() => expect(envios()).toHaveLength(1));
    const corpo = envios()[0]!.body as Record<string, unknown>;
    expect(corpo).toMatchObject({ command: 'leia a última nota e mande no QA Messenger', instance_ids: [],
                                  distribute: { count: 2 } });
    expect(corpo.distribute).not.toHaveProperty('app_id');
  });

  it('escolher um app restringe a ele: a prévia e o envio levam o app_id', async () => {
    await modoDistribuir();
    await setValue(byRole('combobox', 'do app') as HTMLSelectElement, 'notes');
    await setValue(campo(), 'leia a última nota');
    await waitFor(() => expect(distribuicoes().some((c) => (c.body as Record<string, unknown>).app_id === 'notes')).toBe(true));
    expect(distribuicoes().every((c) => !('command' in (c.body as Record<string, unknown>)))).toBe(true);
    await waitFor(() => expect(text(container)).toContain('em central'));
    await click(byRole('button', /^Executar/));
    await waitFor(() => expect(envios()).toHaveLength(1));
    expect((envios()[0]!.body as Record<string, unknown>).distribute).toEqual({ count: 2, app_id: 'notes' });
  });

  it('texto com senha não vai à prévia da distribuição', async () => {
    await modoDistribuir();
    await setValue(campo(), 'abra o QA Messenger com senha: hunter2');
    await flush(600);
    expect(distribuicoes()).toHaveLength(0);
    expect(text(container)).toContain(SENHA_NO_COMANDO);
  });
});

// Tarefa 03 (revisão de UX): o link "escolher manualmente" virou um controle segmentado de duas posições, e
// Refinar → Planejar → Executar são três etapas em ordem, com o motivo de cada bloqueio NO botão.
describe('Comando — controle segmentado e etapas', () => {
  const pressionado = (nome: RegExp) => byRole('button', nome).getAttribute('aria-pressed');

  it('Automático | Manual: duas posições, sempre uma marcada; os modos manuais só aparecem no Manual', async () => {
    // o beforeEach deixou o modo manual "aparelhos marcados"
    expect(pressionado(/^Manual/)).toBe('true');
    expect(pressionado(/^Automático/)).toBe('false');
    expect(pressionado(/^Aparelhos marcados/)).toBe('true');

    await click(byRole('button', /^Automático/));
    expect(pressionado(/^Automático/)).toBe('true');
    expect(pressionado(/^Manual/)).toBe('false');
    expect(allByRole('button', /^Aparelhos marcados/)).toHaveLength(0);
    expect(text(container)).toContain('a IA escolhe quem faz');

    // voltar ao Manual reabre o último jeito usado
    await click(byRole('button', /^Manual/));
    expect(pressionado(/^Manual/)).toBe('true');
    expect(pressionado(/^Aparelhos marcados/)).toBe('true');
    await click(byRole('button', /^Por persona/));
    await click(byRole('button', /^Automático/));
    await click(byRole('button', /^Manual/));
    expect(pressionado(/^Por persona/)).toBe('true');
  });

  it('refinar, planejar e executar formam um grupo de etapas, nessa ordem', async () => {
    const etapas = byRole('group', /^Etapas do comando/);
    const nomes = allByRole('button', /./, etapas).map((b) => (b.textContent ?? '').split(' — ')[0]);
    expect(nomes).toEqual(['Refinar com IA', 'Planejar', 'Executar']);
  });

  it('Executar indisponível: o motivo está no próprio botão, uma vez só, e não num aviso solto', async () => {
    useUiStore.setState({ selectedIds: [] });
    await flush(20);
    const motivo = 'Selecione ao menos um aparelho na grade abaixo.';
    const executar = byRole('button', /^Executar/);
    expect(executar.getAttribute('aria-disabled')).toBe('true');
    expect(executar.textContent).toContain(motivo);
    // o Planejar explica pelo mesmo motivo, cada um no seu botão; nenhum aviso fora deles
    expect(text(container).split(motivo).length - 1).toBe(2);
    expect(container.querySelector('[class*="reason"]')).toBeNull();
    // e ao focar, o tooltip mostra o motivo
    await act(async () => executar.focus());
    await waitFor(() => expect(allByRole('tooltip', motivo).length).toBeGreaterThan(0));
  });
});

// ---------------------------------------------------------------- 31.89 (adendo v1.72): "isto parece com…"
describe('Comando: fluxos parecidos quando nenhum casa (31.89)', () => {
  const campo = () => byRole('textbox', 'Comando em linguagem natural') as HTMLTextAreaElement;
  const parecidos = () => document.querySelector('[aria-label="Fluxos parecidos com o comando"]');
  const SUGESTOES = [
    { ref: 'f-0a1b2c3d4e5f', template: 'mande {mensagem} para {contato}', score: 0.97 },
    { ref: 'f-aaaaaaaaaaaa', template: 'envie {mensagem} a {contato}', score: 0.93 },
  ];

  it('com o match nulo, mostra os moldes; clicar só reescreve o comando e não executa nada', async () => {
    backend.on('POST', /^\/api\/flows\/similar$/, () => json({ matches: false, suggestions: SUGESTOES }));
    await setValue(campo(), 'mande oi para a Ana');
    await waitFor(() => expect(parecidos()).not.toBeNull());
    expect(text(parecidos() as HTMLElement)).toContain('Isto parece com');
    expect(allByRole('button', /mande \{mensagem\} para \{contato\}/)).toHaveLength(1);
    expect(allByRole('button', /envie \{mensagem\} a \{contato\}/)).toHaveLength(1);
    expect(backend.callsTo('POST', /^\/api\/flows\/similar$/)[0]!.body).toEqual({ command: 'mande oi para a Ana' });

    await click(byRole('button', /envie \{mensagem\} a \{contato\}/));
    expect(campo().value).toBe('envie {mensagem} a {contato}');
    expect(backend.callsTo('POST', /^\/api\/runs/)).toHaveLength(0);          // só pergunta: nada é executado
  });

  it('mostra no máximo 3 moldes, mesmo que a API mande mais', async () => {
    const quatro = [...SUGESTOES, { ref: 'f-bbbbbbbbbbbb', template: 'diga {mensagem} a {contato}', score: 0.92 },
                    { ref: 'f-cccccccccccc', template: 'fale {mensagem} com {contato}', score: 0.91 }];
    backend.on('POST', /^\/api\/flows\/similar$/, () => json({ matches: false, suggestions: quatro }));
    await setValue(campo(), 'mande oi para a Ana');
    await waitFor(() => expect(parecidos()).not.toBeNull());
    expect(parecidos()!.querySelectorAll('button')).toHaveLength(3);
  });

  it('com um fluxo que já casa (match não nulo) a rota nem é chamada, e com matches true não sobra sugestão', async () => {
    backend.on('POST', /^\/api\/flows\/match$/, () => json({ flow_id: 'f-oi', name: 'Enviar oi', command_template: 'enviar oi', package: null,
                                                         target_version: null, steps_total: 3, steps_with_recipe: 1, ai_cost: 'parcial', estimated_usd: 0.12 }));
    backend.on('POST', /^\/api\/flows\/similar$/, () => json({ matches: false, suggestions: SUGESTOES }));
    await setValue(campo(), 'enviar oi');
    await waitFor(() => expect(text(container)).toContain('estimativa: US$ 0.12 por aparelho'));
    expect(backend.callsTo('POST', /^\/api\/flows\/similar$/)).toHaveLength(0);
    expect(parecidos()).toBeNull();

    backend.on('POST', /^\/api\/flows\/match$/, () => json(null));
    backend.on('POST', /^\/api\/flows\/similar$/, () => json({ matches: true, suggestions: [] }));
    await setValue(campo(), 'enviar oi agora');
    await waitFor(() => expect(backend.callsTo('POST', /^\/api\/flows\/similar$/)).toHaveLength(1));
    await flush(30);
    expect(parecidos()).toBeNull();
  });

  it('a rota que falha (backend antigo) não mostra nada nem atrapalha o comando', async () => {
    await setValue(campo(), 'mande oi para a Ana');                               // /flows/similar não simulada: 404
    await waitFor(() => expect(backend.callsTo('POST', /^\/api\/flows\/similar$/)).toHaveLength(1));
    await flush(30);
    expect(parecidos()).toBeNull();
    await click(await botaoPronto(/^Executar/));
    await waitFor(() => expect(backend.callsTo('POST', /^\/api\/runs$/)).toHaveLength(1));
  });
});

