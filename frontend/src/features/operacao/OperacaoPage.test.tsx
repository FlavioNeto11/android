// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { FakeBackend, allByRole, byRole, installBrowserStubs, setValue, text } from '../../test/harness';
import {
  agregadoPorApp, contarPorEstado, ESTAGIOS, estagioDeParada, estagiosAlcancados, lerAlvo, lerOperacao, rotuloDaSessao,
} from './modelo';
import { OPERACAO_DE_EXEMPLO } from './operacaoDeExemplo';
import { OperacaoPage } from './OperacaoPage';

/**
 * Prova 07/10 (FULL INSTAGRAM): a tela Operação lê um exemplo fixo no formato do contrato suposto (adendo v1.94). Prova `simulated`:
 * nenhuma rota real; os nomes dos campos são a suposição do Portal e o leitor é tolerante.
 */

let root: Root;
let container: HTMLElement;

beforeAll(() => installBrowserStubs());
beforeEach(() => {
  new FakeBackend().install();
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

const montar = () => act(async () => root.render(<OperacaoPage />));
const linhas = () => Array.from(container.querySelectorAll('tbody tr')) as HTMLElement[];

describe('o leitor tolerante', () => {
  it('lerOperacao: sem id não é operação; campo ausente vira "não informado", nunca erro', () => {
    expect(lerOperacao(null)).toBeNull();
    expect(lerOperacao({ objetivo: 'x' })).toBeNull();
    const op = lerOperacao({ id: 'op-1', alvos: [{}, 7, null, { id: 'a', estado: 'inventado', estagio: 'inventado', verificada: 'talvez' }] })!;
    expect(op.alvos).toHaveLength(2);                                  // 7 e null caem; {} fica com id derivado
    expect(op.alvos[0]!.id).toBe('alvo-1');
    expect(op.alvos[1]).toMatchObject({ id: 'a', estado: null, estagio: null, verificada: null });
    expect(op.capacidade).toEqual({ solicitados: null, contas_existentes: null, sessoes_validas: null, disponiveis: null, concluidas: null, bloqueadas: null, motivos: [] });
  });

  it('lerAlvo: número negativo, texto vazio e bloqueio sem motivo não valem', () => {
    const a = lerAlvo({ id: 'x', persona: '  ', conhecimento_n: -1, evidencia_id: 3.7, bloqueio: { estagio: 'conta' } }, 0)!;
    expect(a).toMatchObject({ persona: null, conhecimento_n: null, evidencia_id: 3, bloqueio: null });
  });

  it('estágios: o dono manda a ordem (14), o alcançado conta do primeiro e a parada é o bloqueio ou o seguinte', () => {
    expect(ESTAGIOS.map((e) => e.id)).toEqual([
      'persona', 'conta', 'sessao', 'aparelho', 'instagram_aberto', 'target_localizado', 'post_localizado', 'conteudo_lido',
      'conhecimento_recuperado', 'resposta_gerada', 'interface_de_comentario', 'acao_preparada', 'acao_executada', 'resultado_verificado',
    ]);
    expect(estagiosAlcancados({ estagio: null })).toBe(0);
    expect(estagiosAlcancados({ estagio: 'aparelho' })).toBe(4);
    expect(estagiosAlcancados({ estagio: 'resultado_verificado' })).toBe(14);
    expect(estagioDeParada({ estagio: 'aparelho', bloqueio: null })).toBe('instagram_aberto');
    expect(estagioDeParada({ estagio: 'aparelho', bloqueio: { estagio: 'sessao', motivo: 'm' } })).toBe('sessao');
    expect(estagioDeParada({ estagio: 'resultado_verificado', bloqueio: null })).toBeNull();
  });

  it('o agregado por app separa concluído de verificado, e a sessão desconhecida fica como veio', () => {
    const op = lerOperacao(OPERACAO_DE_EXEMPLO, true)!;
    expect(agregadoPorApp(op.alvos)).toEqual([{ app: 'Instagram', alvos: 30, concluidos: 3, verificados: 2, bloqueados: 27, falhos: 0 }]);
    expect(contarPorEstado(op.alvos)).toMatchObject({ concluido: 3, bloqueado: 27, em_andamento: 0 });
    expect(rotuloDaSessao('vencida')).toBe('Vencida');
    expect(rotuloDaSessao('estranha')).toBe('estranha');
    expect(rotuloDaSessao(null)).toBe('Não informada');
  });
});

describe('a tela', () => {
  it('avisa que é exemplo e mostra a faixa de capacidade com o déficit e os motivos, sem esconder', async () => {
    await montar();
    expect(text(byRole('status', /Dados de exemplo/, container))).toContain('adendo v1.94');
    const faixa = container.querySelector('section[aria-labelledby="operacao-capacidade"]') as HTMLElement;
    const valores = Array.from(faixa.querySelectorAll('div > dd')).map((d) => `${d.textContent} ${d.parentElement!.querySelector('dt')!.textContent}`);
    expect(valores).toEqual(['30 solicitados', '5 contas existentes', '4 sessões válidas', '4 disponíveis', '3 concluídas', '27 bloqueadas']);
    expect(text(faixa)).toContain('25 A persona não tem conta do Instagram vinculada.');
    expect(text(faixa)).toContain('Motivos dos bloqueios');
  });

  it('uma linha por agente (30), com persona, conta, sessão, aparelho, estado e o motivo do bloqueio; sem conta fica "sem conta"', async () => {
    await montar();
    expect(linhas()).toHaveLength(30);
    const um = linhas()[0]!;
    expect(text(um)).toContain('Persona 01');
    expect(text(um)).toContain('@exemplo_01');
    expect(text(um)).toContain('Conectada');
    expect(text(um)).toContain('android-04');
    expect(text(um)).toContain('Concluído');
    expect(text(um)).toContain('Verificada');
    const quatro = linhas()[3]!;
    expect(text(quatro)).toContain('Parou em Interface de comentário alcançada: A tela de comentário do post não abriu');
    const sem = linhas()[10]!;
    expect(text(sem)).toContain('sem conta');
    expect(text(sem)).toContain('Parou em Conta: A persona não tem conta do Instagram vinculada.');
    expect(text(sem)).toContain('sem aparelho');
  });

  it('a ação concluída sem verificação aparece como "Não conferida", nunca como verificada', async () => {
    await montar();
    const tres = linhas()[2]!;
    expect(text(tres)).toContain('Concluído');
    expect(text(tres)).toContain('Não conferida');
    expect(text(tres)).not.toMatch(/(^|[^o] )Verificada/);
    expect(allByRole('img', /^Concluiu até/, tres)).toHaveLength(1);
  });

  it('o pipeline fala em palavras: quantos estágios e onde parou', async () => {
    await montar();
    const um = linhas()[0]!;
    expect(byRole('img', /Concluiu até “Resultado verificado” \(14 de 14\)/, um)).toBeTruthy();
    const dois = linhas()[3]!;
    expect(byRole('img', /10 de 14 estágios; parou em “Interface de comentário alcançada”/, dois)).toBeTruthy();
  });

  it('o filtro por estado e por estágio de parada reduz a lista', async () => {
    await montar();
    await setValue(byRole('combobox', /^Estado/, container) as HTMLSelectElement, 'concluido');
    expect(linhas()).toHaveLength(3);
    await setValue(byRole('combobox', /^Estado/, container) as HTMLSelectElement, '');
    await setValue(byRole('combobox', /^Parou em/, container) as HTMLSelectElement, 'sessao');
    expect(linhas()).toHaveLength(1);
    expect(text(linhas()[0]!)).toContain('Persona 05');
    await setValue(byRole('combobox', /^Parou em/, container) as HTMLSelectElement, 'target_localizado');
    expect(container.textContent).toContain('Nenhum agente com este filtro');
  });

  it('mostra o agregado por app com concluídos e verificados separados', async () => {
    await montar();
    expect(text(container.querySelector('section[aria-labelledby="operacao-por-app"]') as HTMLElement))
      .toContain('Instagram: 30 alvos, 3 concluídos, 2 verificados, 27 bloqueados, 0 falhos');
  });
});

describe('o que a tela nunca mostra', () => {
  it('sem credencial, e-mail, código cru nem valor quebrado, no texto e nos rótulos de leitura de tela', async () => {
    await montar();
    const t = `${text(container)}\n${Array.from(container.querySelectorAll('[aria-label],[title]')).map((e) => `${e.getAttribute('aria-label') ?? ''} ${e.getAttribute('title') ?? ''}`).join('\n')}`;
    expect(t).not.toMatch(/senha|password|token|credencial|login_identifier|[\w.+-]+@[\w-]+\.[a-z]{2,}/i);
    expect(t).not.toMatch(/\b(undefined|null|NaN)\b|\[object Object\]/);
    expect(t).not.toMatch(/\b(na_fila|em_andamento|nao_conferida|sem_conta|acao_preparada|interface_de_comentario|resultado_verificado)\b/);
  });
});
