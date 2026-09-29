import { describe, expect, it } from 'vitest';
import {
  CAMADAS, type EntradaDoLivro, type GrupoDeFalha, MOTIVOS, MOTIVO_DO_DESFAZER, acaoDeAprovar, acoesDoItem,
  desfazerDoEfeito, estadoDoLivro, lerFeedbackDaExecucao, lerRelatorioDeFalhas, lerRespostaDoVoto, lerSinais, mdDoItem,
  ordenarFalhas, ordenarPendentes, porQueOSistemaNaoPublica, rotuloDaCamada, rotuloDaFalha, rotuloDoEstado,
  rotuloDoKind, textoDoEfeito, votoDoItem,
} from './model';

/** Pacote A6 do ADR-054: rótulos de estado e camada, a ordenação do "o que mais falha" e da fila do D1, as ações que
 *  a PESSOA pode fazer (a tabela de transições do domínio), o md do "Copiar para sessão" e a leitura tolerante das
 *  respostas de A3/A4. Prova `simulated`. */

function entrada(over: Partial<EntradaDoLivro> = {}): EntradaDoLivro {
  return {
    kind: 'receita', ref: '1', state: 'validated', native_status: 'validated', title: 'Abrir conversa', app: 'com.x',
    origin: 'execucao', side_effect: true, human_origin: false, requires_owner: true, created_at: '2026-09-28T10:00:00Z',
    state_at: '2026-09-28T10:00:00Z', last_used_at: null, uses: 3, evidence: { for: 2, against: 0 }, count: null,
    detail: null, ...over,
  };
}

function grupo(over: Partial<GrupoDeFalha> = {}): GrupoDeFalha {
  return {
    id: 'fk-0000000001', app: 'com.instagram.android', capability: 'abrir_perfil', failure_kind: 'app_anr',
    failure_screen: null, titulo: 'App sem resposta', camada: 'aparelho', onde_alterar: ['backend/app/devices/manager.py'],
    doc: 'docs/dominios/parque.md', prova: 'a mesma etapa no mesmo aparelho', ocorrencias: 5, taxa: 0.2, execucoes: 3,
    aparelhos: 2, usd_perdido: 0.1, min_perdidos: 4, intervencoes: 1, custo_total: 0.35, tendencia: null,
    exemplos: [{ run_id: 'r-1', attempt_id: 'a-1', erro: 'ANR' }], retroativo: false, estado_backlog: 'open',
    falso_positivo: false, md: null, ...over,
  };
}

describe('rótulos', () => {
  it('estado do livro em português, sem estado dito como tal', () => {
    expect(rotuloDoEstado('candidate')).toBe('Candidato');
    expect(rotuloDoEstado('validated')).toBe('Validado');
    expect(rotuloDoEstado('published')).toBe('Publicado');
    expect(rotuloDoEstado('deprecated')).toBe('Aposentado');
    expect(rotuloDoEstado('disabled')).toBe('Desligado');
    expect(rotuloDoEstado(null)).toBe('Sem estado');
  });

  it('camada: todas as do domínio têm rótulo, e a desconhecida aparece como veio', () => {
    for (const c of CAMADAS) expect(rotuloDaCamada(c)).not.toBe(c);
    expect(rotuloDaCamada('verificacao')).toBe('Verificação');
    expect(rotuloDaCamada('ia_ator')).toBe('IA (ator)');
    expect(rotuloDaCamada('camada_nova')).toBe('camada_nova');
    expect(rotuloDaCamada(null)).toBe('—');
  });

  it('tipo de falha e tipo do livro', () => {
    expect(rotuloDaFalha('app_anr')).toBe('App sem resposta (ANR)');
    expect(rotuloDaFalha('prazo_da_etapa')).toBe('Prazo da etapa esgotado');
    expect(rotuloDaFalha('tipo_novo')).toBe('tipo_novo');
    expect(rotuloDoKind('licao')).toBe('Lição');
    expect(rotuloDoKind('memoria')).toBe('Memória da persona');
  });

  it('por que o sistema não publica sozinho (D1)', () => {
    expect(porQueOSistemaNaoPublica(entrada({ side_effect: true }))).toBe('tem efeito externo');
    expect(porQueOSistemaNaoPublica(entrada({ side_effect: false, human_origin: true }))).toBe('tem texto de pessoa');
    expect(porQueOSistemaNaoPublica(entrada({ kind: 'habilidade', side_effect: false }))).toBe('habilidade: publicar é sempre de uma pessoa');
    expect(porQueOSistemaNaoPublica(entrada({ side_effect: false, human_origin: false }))).toBeNull();
  });
});

describe('ações da pessoa (a tabela de transições do domínio)', () => {
  const alvos = (e: EntradaDoLivro) => acoesDoItem(e).map((a) => a.to);

  it('validado: aprovar publica, rejeitar desliga', () => {
    expect(alvos(entrada({ state: 'validated' }))).toEqual(['published', 'disabled']);
    expect(acoesDoItem(entrada({ state: 'validated' }))[0]?.label).toBe('Aprovar');
  });

  it('candidato: validar à mão ou rejeitar', () => {
    expect(alvos(entrada({ kind: 'licao', state: 'candidate' }))).toEqual(['validated', 'disabled']);
  });

  it('publicado: receita aposenta ou desliga; fluxo não tem aposentadoria', () => {
    expect(alvos(entrada({ state: 'published' }))).toEqual(['deprecated', 'disabled']);
    expect(alvos(entrada({ kind: 'fluxo', state: 'published' }))).toEqual(['disabled']);
  });

  it('reativar é de pessoa; receita substituída não volta', () => {
    expect(alvos(entrada({ kind: 'fluxo', state: 'disabled' }))).toEqual(['published']);
    expect(alvos(entrada({ kind: 'tela', state: 'deprecated' }))).toEqual(['published']);
    expect(alvos(entrada({ kind: 'receita', state: 'deprecated' }))).toEqual([]);
  });

  it('habilidade e memória não se decidem pelo livro', () => {
    expect(alvos(entrada({ kind: 'habilidade', state: 'validated' }))).toEqual([]);
    expect(alvos(entrada({ kind: 'memoria', state: null }))).toEqual([]);
  });

  it('aprovar é o próximo passo para cima: candidato → validado, validado → publicado', () => {
    expect(acaoDeAprovar(entrada({ state: 'candidate' }))?.to).toBe('validated');
    expect(acaoDeAprovar(entrada({ state: 'validated' }))?.to).toBe('published');
    expect(acaoDeAprovar(entrada({ state: 'published' }))).toBeNull();
    expect(acaoDeAprovar(entrada({ kind: 'habilidade', state: 'validated' }))).toBeNull();
  });
});

describe('ordenação', () => {
  it('o falso positivo do verificador fica sempre no topo; depois o custo total, depois as ocorrências', () => {
    const caro = grupo({ id: 'fk-caro', custo_total: 3 });
    const barato = grupo({ id: 'fk-barato', custo_total: 0.1, ocorrencias: 50 });
    const empateMais = grupo({ id: 'fk-empate-mais', custo_total: 1, ocorrencias: 9 });
    const empateMenos = grupo({ id: 'fk-empate-menos', custo_total: 1, ocorrencias: 4 });
    const fp = grupo({ id: 'fk-fp', custo_total: 0, falso_positivo: true });
    expect(ordenarFalhas([barato, empateMenos, caro, empateMais, fp]).map((g) => g.id))
      .toEqual(['fk-fp', 'fk-caro', 'fk-empate-mais', 'fk-empate-menos', 'fk-barato']);
  });

  it('sem o custo total em todos, vale a ordem do backend (que já ordena pelo custo) — o falso positivo sobe mesmo assim', () => {
    const a = grupo({ id: 'fk-a', custo_total: null, usd_perdido: 0.1 });
    const b = grupo({ id: 'fk-b', custo_total: null, usd_perdido: 5 });
    const fp = grupo({ id: 'fk-fp', custo_total: null, falso_positivo: true });
    expect(ordenarFalhas([a, b, fp]).map((g) => g.id)).toEqual(['fk-fp', 'fk-a', 'fk-b']);
  });

  it('a fila do D1 põe o validado (que espera só o dono) antes do candidato, e o mais recente primeiro', () => {
    const velho = entrada({ ref: 'v', state: 'validated', state_at: '2026-09-20T00:00:00Z' });
    const novo = entrada({ ref: 'n', state: 'validated', state_at: '2026-09-28T00:00:00Z' });
    const cand = entrada({ ref: 'c', kind: 'licao', state: 'candidate', state_at: '2026-09-29T00:00:00Z' });
    expect(ordenarPendentes([cand, velho, novo]).map((e) => e.ref)).toEqual(['n', 'v', 'c']);
  });
});

describe('Copiar para sessão', () => {
  it('o md do item traz a chave estável, o tipo, a camada, onde alterar, a prova e os exemplos', () => {
    const md = mdDoItem(grupo({ retroativo: true }));
    expect(md).toContain('fk-0000000001');
    expect(md).toContain('App sem resposta (ANR)');
    expect(md).toContain('Aparelho');
    expect(md).toContain('backend/app/devices/manager.py');
    expect(md).toContain('a mesma etapa no mesmo aparelho');
    expect(md).toContain('r-1');
    expect(md).toContain('retroativo');
  });

  it('quando o backend manda o md do item, ele é usado como veio', () => {
    expect(mdDoItem(grupo({ md: '## fk-x\npronto' }))).toBe('## fk-x\npronto');
  });
});

describe('leitura tolerante (contrato de A3/A4 ainda em implementação)', () => {
  it('relatório de falhas: grupos com aliases, números ausentes viram null, lixo vira lista vazia', () => {
    const r = lerRelatorioDeFalhas({
      dias: 14, outro_pct: 18.5,
      grupos: [{ id: 'fk-1', app_package: 'com.x', capability: '*', failure_kind: 'app_anr', ocorrencias: 3,
                 usd_perdido: 0.5, onde_alterar: { arquivos: ['a.py'], doc: 'docs/x.md', prova: 'p' },
                 exemplos: [{ run_id: 'r-1', attempt_id: 'a-1', erro: 'x' }, { nada: 1 }], retroativo: true }, 'lixo'],
    });
    expect(r.dias).toBe(14);
    expect(r.outro_pct).toBe(18.5);
    expect(r.grupos).toHaveLength(1);
    const g = r.grupos[0];
    expect(g?.app).toBe('com.x');
    expect(g?.onde_alterar).toEqual(['a.py']);
    expect(g?.doc).toBe('docs/x.md');
    expect(g?.prova).toBe('p');
    expect(g?.camada).toBe('aparelho');            // derivada do tipo quando o backend não disser
    expect(g?.custo_total).toBeNull();
    expect(g?.exemplos).toEqual([{ run_id: 'r-1', attempt_id: 'a-1', erro: 'x' }]);
    expect(lerRelatorioDeFalhas(null).grupos).toEqual([]);
    expect(lerRelatorioDeFalhas({ itens: [{ key: 'fk-2', failure_kind: 'verificacao_falso_positivo' }] }).grupos[0]?.falso_positivo).toBe(true);
  });

  it('relatório de falhas no formato do domínio (A3): linha com o grupo embrulhado, chave aninhada, tendência 7×7, outro e verificação à parte', () => {
    const r = lerRelatorioDeFalhas({
      janela: { dias: 30, desde: '2026-08-30', ate: '2026-09-29' },
      outro: { ocorrencias: 3, total: 20 },
      itens: [{
        estado: 'open', registrado: true, licoes_ativas: 0,
        grupo: { chave: { app: 'com.instagram.android', capability: 'abrir_perfil', tipo: 'app_anr', tela: '' },
                 ocorrencias: 4, retroativas: 2, elegiveis: 16, etapas: 4, execucoes: 3, aparelhos: 2, usd_perdido: 0.3,
                 min_perdidos: 9, intervencoes: 1, tendencia: { ultimos_7d: 3, anteriores_7d: 1 },
                 exemplos: [{ run_id: 'r-1', attempt_id: 'a-1', quando: '2026-09-28T10:00:00Z', erro: 'ANR' }] },
      }],
      verificacao: [{ id: 'fk-fp', chave: { app: 'com.whatsapp', capability: 'enviar', tipo: 'verificacao_falso_positivo', tela: '' },
                      ocorrencias: 1 }],
    });
    expect(r.dias).toBe(30);
    expect(r.outro_pct).toBe(15);
    expect(r.grupos.map((g) => g.id)).toEqual(['com.instagram.android|abrir_perfil|app_anr|', 'fk-fp']);
    const g = r.grupos[0];
    expect(g?.app).toBe('com.instagram.android');
    expect(g?.failure_kind).toBe('app_anr');
    expect(g?.taxa).toBe(0.25);                    // 4 de 16 elegíveis
    expect(g?.retroativo).toBe(true);
    expect(g?.estado_backlog).toBe('open');
    expect(g?.tendencia).toEqual({ atual: 3, anterior: 1 });
    expect(r.grupos[1]?.falso_positivo).toBe(true);
  });

  it('sinais: lista crua ou embrulhada', () => {
    const s = { id: 1, kind: 'tomou_controle', polarity: 'negative', source_ref: 'takeover:a-1', created_by: 'sistema',
                created_at: '2026-09-28T10:00:00Z', app_package: 'com.x', capability: 'abrir_perfil', simulated: 0 };
    expect(lerSinais([s])).toHaveLength(1);
    expect(lerSinais({ sinais: [s, 3] })).toHaveLength(1);
    expect(lerSinais({ itens: [s] })[0]?.simulated).toBe(false);
    expect(lerSinais('x')).toEqual([]);
  });

  it('feedback da execução: votos por item, sinais e o bloco de aprendizado opcional', () => {
    const f = lerFeedbackDaExecucao({
      votos: [{ objective_id: 'obj-1', verdict: 'errado', reason: 'alvo_errado', created_by: 'ana' },
              { objective_id: null, verdict: 'certo', created_by: 'ana' }, { verdict: 'talvez' }],
      sinais: [],
    });
    expect(f.votos).toHaveLength(2);
    expect(votoDoItem(f, 'obj-1')?.reason).toBe('alvo_errado');
    expect(votoDoItem(f, null)?.verdict).toBe('certo');
    expect(votoDoItem(f, 'obj-2')).toBeNull();
    // um voto por pessoa: com quem pergunta, o voto de outra pessoa não marca o botão
    expect(votoDoItem(f, 'obj-1', 'ana')?.verdict).toBe('errado');
    expect(votoDoItem(f, 'obj-1', 'bruno')).toBeNull();
    expect(f.aprendizado).toBeNull();
    expect(lerFeedbackDaExecucao(undefined).votos).toEqual([]);
  });

  it('resposta do voto: efeitos e resumo', () => {
    const r = lerRespostaDoVoto({ signal: { id: 9 }, resumo: 'fluxo desligado',
                                  efeitos: [{ kind: 'fluxo', ref: 'f-1', de: 'active', para: 'disabled', desfazer: true }, 1] });
    expect(r.resumo).toBe('fluxo desligado');
    expect(r.efeitos).toHaveLength(1);
  });
});

describe('desfazer o efeito do voto', () => {
  it('volta ao estado anterior no vocabulário do livro (o status nativo é traduzido)', () => {
    expect(desfazerDoEfeito({ kind: 'fluxo', ref: 'f-1', de: 'active', para: 'disabled', desfazer: true }))
      .toEqual({ kind: 'fluxo', ref: 'f-1', to: 'published', reason: MOTIVO_DO_DESFAZER });
    expect(desfazerDoEfeito({ kind: 'receita', ref: '7', de: 'published', para: 'disabled', desfazer: {} }))
      .toEqual({ kind: 'receita', ref: '7', to: 'published', reason: MOTIVO_DO_DESFAZER });
    expect(desfazerDoEfeito({ kind: 'receita', ref: '7', de: 'active', para: 'quarantined', desfazer: { to: 'published' } }))
      .toEqual({ kind: 'receita', ref: '7', to: 'published', reason: MOTIVO_DO_DESFAZER });
  });

  it('a chamada pronta do backend (A4: {method, href, body}) é seguida, com o motivo dela', () => {
    expect(desfazerDoEfeito({ kind: 'fluxo', ref: 'f-1', de: 'published', para: 'disabled', aplicado: true, erro: null,
                              desfazer: { method: 'POST', href: '/api/aprendizado/fluxo/f-1/status',
                                          body: { to: 'published', reason: 'reativado depois do voto' } } }))
      .toEqual({ kind: 'fluxo', ref: 'f-1', to: 'published', reason: 'reativado depois do voto' });
  });

  it('sem desfazer, efeito não aplicado, tipo fora do livro ou estado desconhecido: nada a oferecer', () => {
    expect(desfazerDoEfeito({ kind: 'fluxo', ref: 'f-1', de: 'active', para: 'disabled', desfazer: null })).toBeNull();
    expect(desfazerDoEfeito({ kind: 'fluxo', ref: 'f-1', de: 'active', para: 'disabled', aplicado: false,
                              erro: 'conflito', desfazer: true })).toBeNull();
    expect(desfazerDoEfeito({ kind: 'backlog', ref: 'fk-1', de: 'open', para: 'open', desfazer: true })).toBeNull();
    expect(desfazerDoEfeito({ kind: 'fluxo', ref: 'f-1', de: 'estranho', para: 'disabled', desfazer: true })).toBeNull();
  });

  it('o texto do efeito diz o novo estado e, quando não aplicou, o porquê', () => {
    expect(textoDoEfeito({ kind: 'fluxo', ref: 'f-1', de: 'active', para: 'disabled', desfazer: true })).toBe('fluxo f-1: desligado');
    expect(textoDoEfeito({ kind: 'receita', ref: '7', de: 'active', para: 'quarantined', aplicado: false, erro: 'mudou antes',
                           desfazer: null })).toBe('receita 7: desligado (não aplicado: mudou antes)');
  });

  it('estadoDoLivro traduz os status nativos de receita e fluxo', () => {
    expect(estadoDoLivro('receita', 'quarantined')).toBe('disabled');
    expect(estadoDoLivro('receita', 'superseded')).toBe('deprecated');
    expect(estadoDoLivro('fluxo', 'active')).toBe('published');
    expect(estadoDoLivro('tela', 'validated')).toBe('validated');
    expect(estadoDoLivro('tela', 'draft')).toBeNull();
  });
});

describe('motivos do "Deu errado"', () => {
  it('o vocabulário fechado do domínio, com os de navegação marcados (são os que rebaixam)', () => {
    expect(MOTIVOS.map((m) => m.id)).toEqual(['fez_outra_coisa', 'alvo_errado', 'nao_terminou', 'texto_ruim',
                                              'demorou_ou_gastou', 'pediu_ajuda_a_toa', 'outro']);
    expect(MOTIVOS.filter((m) => m.navegacao).map((m) => m.id)).toEqual(['fez_outra_coisa', 'alvo_errado', 'nao_terminou']);
  });
});
