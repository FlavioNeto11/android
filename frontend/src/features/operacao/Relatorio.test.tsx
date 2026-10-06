// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { useUiStore } from '../../store/ui';
import { FakeBackend, byRole, click, installBrowserStubs, json, text, waitFor } from '../../test/harness';
import { ESTAGIOS, lerOperacao } from './modelo';
import { OperacaoPage } from './OperacaoPage';
import { nomeDoArquivo } from './RelatorioDaOperacao';
import { conferenciaDaAcao, montarRelatorio, relatorioEmMarkdown } from './relatorio';

/**
 * 31.162: o relatório da operação (formato do § 8 do cenário da prova de 07/10) montado só do `GET /api/operacoes/{id}` (adendo
 * v1.94). Prova `simulated`: servidor falso, operação inventada (personas e contas fictícias).
 */

const hora = (i: number) => `2026-10-07T18:${String(10 + i).padStart(2, '0')}:00Z`;
const ate = (n: number) => ESTAGIOS.slice(0, n).map((e, i) => ({ estagio: e.id, em: hora(i) }));
const base = (n: number) => ({
  profile_id: `p${n}`, persona_nome: `Persona 0${n}`, app_id: 'com.instagram.android', account_id: `acc-0${n}`, conta: `@exemplo_0${n}`,
  instance_id: `android-0${n}`, run_id: `r${n}`,
});
const concluido = (n: number, texto: string, verificada: boolean | null, estagios: number) => ({
  ...base(n), estagio: estagios === 14 ? 'resultado_verificado' : 'acao_executada', estagios: ate(estagios), estado: 'concluido', motivo: null, parou_em: null,
  // o alvo 3 só traz o custo dentro do resultado (a reserva); os outros, no próprio alvo
  ...(n === 3 ? {} : { custo_usd: 0.1 * n }),
  resultado: { texto, conhecimento_ids: [`fluxo:f${n}`, 'licao:voz'], evidencia_id: 100 + n, acao_final: { tipo: 'CREATE_COMMENT', verificada, evidencia_id: verificada ? 200 + n : null },
               ...(n === 3 ? { custo_usd: 0.05 } : {}) },
});
const semConta = (n: number) => ({
  ...base(n), account_id: null, conta: null, instance_id: null, run_id: null, estagio: 'persona', estagios: ate(1), estado: 'bloqueado', motivo: 'sem conta', parou_em: 'conta', resultado: null,
});
const BRUTA = {
  id: 'op/1', command: 'Comentar no post da loja', app_id: 'com.instagram.android', acao_final: 'preparar', status: 'concluida_com_bloqueios',
  created_at: '2026-10-07T18:00:00Z', finished_at: '2026-10-07T19:00:00Z', max_usd: 4.5, assunto: 'A embalagem nova.', fontes: ['https://exemplo.com.br/a'],
  custo: { pesquisa_usd: 0.07, alvos_usd: 0.9, total_usd: 0.97 },
  capacidade: { solicitados: 5, contas_existentes: 3, sessoes_validas: 3, contas_disponiveis: 3, concluidas: 3, bloqueadas: 2, em_curso: 0, motivos: { 'sem conta': 2 } },
  alvos: [
    concluido(1, 'Texto igual.', true, 14), concluido(2, 'texto  IGUAL.', false, 13), concluido(3, 'Outro texto\n# parece título', null, 13), semConta(4), semConta(5),
  ],
};
const OP = lerOperacao(BRUTA)!;

describe('montarRelatorio', () => {
  const r = montarRelatorio(OP, new Date('2026-10-07T20:00:00Z'));

  it('traz por agente os 14 estágios com a hora, o motivo de quem parou, o conhecimento, o texto e a ação final', () => {
    expect(r.agentes).toHaveLength(5);
    const [a1, a2, , a4] = r.agentes;
    expect(a1!.estagios).toHaveLength(14);
    expect(a1!.estagios.every((e) => e.alcancado && e.em !== null)).toBe(true);
    expect(a1!.estagios[0]).toEqual({ estagio: 'persona', rotulo: 'Persona', em: hora(0), alcancado: true });
    expect(a2!.estagios.filter((e) => e.alcancado)).toHaveLength(13);
    expect(a2!.estagios[13]).toMatchObject({ estagio: 'resultado_verificado', em: null, alcancado: false });
    expect(a1!.conhecimento_ids).toEqual(['fluxo:f1', 'licao:voz']);
    expect(a1!.texto).toBe('Texto igual.');
    expect(a1!.acao_final).toEqual({ tipo: 'CREATE_COMMENT', verificada: 'sim', evidencia_id: 201 });
    expect(a1!.evidencia_id).toBe(101);
    expect(a4).toMatchObject({ estado: 'Bloqueado', motivo: 'sem conta', parou_em: 'Conta', texto: null, acao_final: null, custo_usd: null });
    expect(a4!.estagios.filter((e) => e.alcancado)).toHaveLength(1);
  });

  it('a conferência da ação é sim, não ou não conferida, e sem ação quando não houve', () => {
    expect(r.agentes.map((a) => a.acao_final?.verificada ?? 'sem_acao')).toEqual(['sim', 'nao', 'nao_conferida', 'sem_acao', 'sem_acao']);
    expect(conferenciaDaAcao({ resultado: null })).toBe('sem_acao');
  });

  it('o consolidado: capacidade, custos com o teto, falhas agrupadas por motivo e textos irmãos', () => {
    expect(r.capacidade).toMatchObject({ solicitados: 5, concluidas: 3, bloqueadas: 2, motivos: [{ motivo: 'sem conta', n: 2 }] });
    expect(r.custo).toEqual({ pesquisa_usd: 0.07, alvos_usd: 0.9, total_usd: 0.97, teto_usd: 4.5 });
    expect(r.falhas_por_motivo).toEqual([{ motivo: 'sem conta', parou_em: 'Conta', agentes: 2 }]);
    expect(r.textos.total).toBe(3);
    expect(r.textos.distintos).toBe(2);                                  // "Texto igual." e "texto  IGUAL." são o mesmo texto
    expect(r.textos.repetidos).toEqual([{ texto: 'Texto igual.', agentes: ['Persona 01', 'Persona 02'] }]);
  });

  it('o agente é o rótulo da persona: nenhuma conta (@), id de conta nem login aparece no JSON nem no Markdown', () => {
    const tudo = `${JSON.stringify(r)}\n${relatorioEmMarkdown(r)}`;
    expect(tudo).not.toMatch(/@exemplo|acc-0|account_id|login|e-?mail/i);
    expect(tudo).toContain('Persona 01');
  });

  it('o custo por agente vem do próprio alvo, do resultado só como reserva, e é nulo (não zero) sem execução', () => {
    expect(r.agentes.map((a) => a.custo_usd)).toEqual([0.1, 0.2, 0.05, null, null]);
    expect(r.limites.join(' ')).toContain('Custo por agente "não informado"');
    expect(OP.alvos.map((a) => a.custo_usd)).toEqual([0.1, 0.2, 0.05, null, null]);
    // o do alvo vale mais que o do resultado quando os dois vêm
    expect(lerOperacao({ id: 'o', alvos: [{ custo_usd: 0.3, resultado: { texto: 'x', custo_usd: 0.9 } }] })!.alvos[0]!.custo_usd).toBe(0.3);
    expect(lerOperacao({ id: 'o', alvos: [{ custo_usd: -1, resultado: { texto: 'x', custo_usd: 'caro' } }] })!.alvos[0]!.custo_usd).toBeNull();
  });

  it('sem custo nem teto no backend, os campos ficam nulos, nunca zero', () => {
    const sem = montarRelatorio(lerOperacao({ ...BRUTA, custo: undefined, max_usd: undefined })!);
    expect(sem.custo).toEqual({ pesquisa_usd: null, alvos_usd: null, total_usd: null, teto_usd: null });
    expect(relatorioEmMarkdown(sem)).toContain('Total:** não informado');
  });

  it('sem a lista de estágios (backend antigo), o último estágio e os anteriores contam, sem hora', () => {
    const antigo = montarRelatorio(lerOperacao({ id: 'o', alvos: [{ profile_id: 'p', persona_nome: 'Persona 09', estagio: 'conta', estado: 'bloqueado', motivo: 'sem sessão' }] })!);
    expect(antigo.agentes[0]!.estagios.filter((e) => e.alcancado).map((e) => e.estagio)).toEqual(['persona', 'conta']);
    expect(antigo.agentes[0]!.estagios.every((e) => e.em === null)).toBe(true);
  });
});

describe('relatorioEmMarkdown', () => {
  const md = relatorioEmMarkdown(montarRelatorio(OP, new Date('2026-10-07T20:00:00Z')));

  it('tem as seções do consolidado e uma por agente, com a tabela dos 14 estágios', () => {
    for (const secao of ['# Relatório da operação op/1', '## Capacidade', '## Custo', '## Falhas por motivo', '## Textos gerados', '## Agentes', '### Persona 01', '## O que este relatório não tem']) {
      expect(md).toContain(secao);
    }
    expect(md).toContain('Pesquisa externa: US$ 0.0700');
    expect(md).toContain('**Total:** US$ 0.9700 (teto da operação: US$ 4.5000)');
    expect(md).toContain('- sem conta (parou em Conta): 2 agentes');
    expect(md).toContain('verificada: não conferida');
    expect(md).toContain('- **Custo de IA:** US$ 0.1000');
    expect(md).toContain('- **Custo de IA:** não informado');
    expect(md).toContain(`| ${ESTAGIOS[13]!.rotulo} | ${hora(13)} |`);
    expect(md).toContain(`| ${ESTAGIOS[13]!.rotulo} | não alcançado |`);
  });

  it('o texto vai em citação, linha a linha: uma linha do texto que comece com # não vira título', () => {
    expect(md).toContain('> Outro texto\n> # parece título');
    expect(md.split('\n').filter((l) => l.startsWith('# '))).toEqual(['# Relatório da operação op/1']);
  });
});

describe('o botão Relatório', () => {
  let root: Root;
  let container: HTMLElement;
  let backend: FakeBackend;
  let blobs: Blob[];
  let baixados: string[];

  beforeAll(() => installBrowserStubs());
  beforeEach(() => {
    backend = new FakeBackend();
    backend.install();
    blobs = [];
    baixados = [];
    URL.createObjectURL = (b: Blob | MediaSource) => { blobs.push(b as Blob); return `blob:r-${blobs.length}`; };
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function click(this: HTMLAnchorElement) { baixados.push(this.download); });
    container = document.createElement('div');
    document.body.append(container);
    root = createRoot(container);
  });
  afterEach(async () => {
    vi.restoreAllMocks();
    await act(async () => root.unmount());
    container.remove();
    useUiStore.setState({ rota: { ...useUiStore.getState().rota, segmentos: [] } });
  });

  const ir = async (id: string) => {
    useUiStore.setState({ rota: { ...useUiStore.getState().rota, tela: 'operacoes', segmentos: [id], query: {} } });
    await act(async () => root.render(<OperacaoPage />));
  };
  const lerBlob = (b: Blob) => new Promise<string>((ok) => { const f = new FileReader(); f.onload = () => ok(String(f.result)); f.readAsText(b); });

  it('abre o diálogo e baixa o Markdown e o JSON, com o mesmo conteúdo do relatório', async () => {
    backend.on('GET', /^\/api\/operacoes\/op-1$/, () => json({ ...BRUTA, id: 'op-1' }));
    await ir('op-1');
    await click(await waitFor(() => byRole('button', /^Relatório$/, container)));
    const d = await waitFor(() => byRole('dialog', /Relatório da operação/));
    expect(text(d)).toContain('5 agentes');
    await click(byRole('button', /^Baixar Markdown$/, d));
    await click(byRole('button', /^Baixar JSON$/, d));
    expect(baixados).toEqual(['operacao-op-1.md', 'operacao-op-1.json']);
    const md = await lerBlob(blobs[0]!);
    const js = JSON.parse(await lerBlob(blobs[1]!)) as ReturnType<typeof montarRelatorio>;
    expect(md).toContain('# Relatório da operação op-1');
    expect(js.operacao.id).toBe('op-1');
    expect(js.agentes).toHaveLength(5);
    expect(js.falhas_por_motivo[0]).toMatchObject({ motivo: 'sem conta', agentes: 2 });
    expect(blobs[1]!.type).toContain('application/json');
  });

  it('o nome do arquivo não leva barra nem caractere estranho do id', () => {
    expect(nomeDoArquivo('op/1 ..\\x', 'md')).toBe('operacao-op_1_x.md');
  });

  it('no exemplo fixo o botão fica desligado, com o motivo (não se relata o que é inventado)', async () => {
    await ir('op-exemplo');
    await waitFor(() => expect(container.querySelectorAll('tbody tr[data-alvo]').length).toBeGreaterThan(0));
    expect(byRole('button', /^Relatório — indisponível: É um exemplo/, container).getAttribute('aria-disabled')).toBe('true');
  });
});
