import { describe, expect, it } from 'vitest';
import {
  type AcaoPermitida, CAMADAS, type EntradaDoLivro, type EstadoDoLivro, type RotuloDaAcao, type GrupoDeFalha, MOTIVOS, MOTIVO_DO_DESFAZER, acaoDeAprovar, acaoDeAprovarNaFila,
  acoesDoItem, acoesNaFila, refDaHabilidade,
  desfazerDoEfeito, estadoDoLivro, lerFeedbackDaExecucao, lerRelatorioDeFalhas, lerRespostaDoVoto, lerSinais, mdDoItem,
  ordenarFalhas, ordenarPendentes, porQueOSistemaNaoPublica, rotuloDaCamada, rotuloDaFalha, rotuloDoEstado,
  rotuloDoKind, textoDoEfeito, votoDoItem, capabilityComNome, nomearCapabilityNoTexto, tituloDoItem, titulosDaLista,
} from './model';

/** Pacote A6 do ADR-054: rótulos de estado e camada, a ordenação do "o que mais falha" e da fila do D1, as ações que
 *  a PESSOA pode fazer (a tabela de transições do domínio), o md do "Copiar para sessão" e a leitura tolerante das
 *  respostas de A3/A4. Prova `simulated`. */

function entrada(over: Partial<EntradaDoLivro> = {}): EntradaDoLivro {
  return {
    kind: 'receita', ref: '1', state: 'validated', native_status: 'validated', title: 'Abrir conversa', app: 'com.x',
    origin: 'execucao', side_effect: true, human_origin: false, requires_owner: true, created_at: '2026-09-28T10:00:00Z',
    state_at: '2026-09-28T10:00:00Z', last_used_at: null, uses: 3, evidence: { for: 2, against: 0 }, count: null,
    detail: null, acoes: [], por_que_nao_publica: null, ...over,
  };
}

const ACAO = (to: EstadoDoLivro, rotulo: RotuloDaAcao): AcaoPermitida => ({ to, rotulo, exige_motivo: true });

function grupo(over: Partial<GrupoDeFalha> = {}): GrupoDeFalha {
  return {
    id: 'fk-0000000001', app: 'com.instagram.android', capability: 'abrir_perfil', capability_nome: null,
    failure_kind: 'app_anr',
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

  it('por que o sistema não publica sozinho: o texto da chave que o backend mandou', () => {
    const dono = (codigo: 'efeito_externo' | 'texto_de_pessoa' | 'habilidade') => ({ codigo, espera_o_dono: true, detalhe: null });
    expect(porQueOSistemaNaoPublica(entrada({ por_que_nao_publica: dono('efeito_externo') }))).toBe('tem efeito externo');
    expect(porQueOSistemaNaoPublica(entrada({ por_que_nao_publica: dono('texto_de_pessoa') }))).toBe('tem texto de pessoa');
    expect(porQueOSistemaNaoPublica(entrada({ por_que_nao_publica: dono('habilidade') }))).toBe('habilidade: publicar é sempre de uma pessoa');
    expect(porQueOSistemaNaoPublica(entrada({ por_que_nao_publica: { codigo: 'vetado', espera_o_dono: false, detalhe: 'desligado por uma pessoa' } })))
      .toBe('desligado por uma pessoa');
    expect(porQueOSistemaNaoPublica(entrada({ por_que_nao_publica: null }))).toBeNull();
  });
});

describe('o título que a pessoa lê (validação do deploy 3, P3 e P4)', () => {
  const ENVIAR = { title: 'send_message_i1 (v1)', capability: 'SEND_MESSAGE', capability_nome: 'Enviar a mensagem' };

  it('a receita troca a chave da etapa pelo nome da capability; sem nome, ou ambígua, fica a chave', () => {
    const r = entrada(ENVIAR);
    expect(tituloDoItem(r)).toBe('Enviar a mensagem (v1)');
    expect(tituloDoItem({ ...r, capability_nome: null })).toBe('send_message_i1 (v1)');
    expect(tituloDoItem({ ...r, capability: null })).toBe('send_message_i1 (v1)');
    expect(tituloDoItem({ ...r, title: 'sem versão' })).toBe('sem versão');
    expect(tituloDoItem({ ...r, kind: 'fluxo', title: 'Enviar oi' })).toBe('Enviar oi');
  });

  it('app sem catálogo: a receita usa o título da etapa de origem, com a chave; o nome do catálogo vence (deploy 4)', () => {
    const r = entrada({ ...ENVIAR, capability: null, capability_nome: null, etapa: 'Digitar a mensagem' });
    expect(tituloDoItem(r)).toBe('Digitar a mensagem · etapa send_message_i1 (v1)');
    expect(tituloDoItem({ ...r, title: 'sem versão' })).toBe('Digitar a mensagem · etapa sem versão');
    expect(tituloDoItem({ ...r, capability: 'SEND_MESSAGE', capability_nome: 'Enviar a mensagem' })).toBe('Enviar a mensagem (v1)');
    expect(tituloDoItem({ ...r, etapa: null })).toBe('send_message_i1 (v1)');
    expect(tituloDoItem({ ...r, kind: 'licao', title: 'Em X: tocou' })).toBe('Em X: tocou');      // só a receita
  });

  it('a lição nomeia a capability uma vez, só a palavra inteira, e nomear de novo não dobra', () => {
    const texto = 'Em OPEN_PROFILE: a tentativa que comprovou tocou em "Perfil"; OPEN_PROFILE_X é outra';
    const l = entrada({ kind: 'licao', ref: 'li-1', title: texto, capability: 'OPEN_PROFILE', capability_nome: 'Abrir o perfil' });
    const nomeado = 'Em Abrir o perfil (OPEN_PROFILE): a tentativa que comprovou tocou em "Perfil"; OPEN_PROFILE_X é outra';
    expect(tituloDoItem(l)).toBe(nomeado);
    expect(nomearCapabilityNoTexto(nomeado, 'OPEN_PROFILE', 'Abrir o perfil')).toBe(nomeado);
    expect(nomearCapabilityNoTexto('Nesta etapa: role', '*', 'Etapa livre')).toBe('Nesta etapa: role');
    expect(nomearCapabilityNoTexto(texto, 'OPEN_PROFILE', null)).toBe(texto);
    expect(capabilityComNome('OPEN_FEED', 'Abrir o feed')).toBe('Abrir o feed (OPEN_FEED)');
    expect(capabilityComNome('OPEN_FEED', null)).toBe('OPEN_FEED');
  });

  it('na lista, quem empata ganha quando foi aprendido; se ainda empata, o número da receita', () => {
    const a = entrada({ ...ENVIAR, ref: '40', created_at: '2026-10-02T17:22:00Z' });
    const b = entrada({ ...ENVIAR, ref: '41', created_at: '2026-09-20T15:18:00Z' });
    const c = entrada({ ...ENVIAR, ref: '42', created_at: '2026-09-20T15:18:00Z' });
    const d = entrada({ ref: '7', title: 'Outra (v2)' });
    const t = titulosDaLista([a, b, c, d]);
    expect(t.get(d)).toBe('Outra (v2)');
    expect(t.get(a)).toMatch(/^Enviar a mensagem \(v1\) · de \S/);
    expect(t.get(a)).not.toContain('nº');
    expect(t.get(b)).toMatch(/^Enviar a mensagem \(v1\) · de .+ · nº 41$/);
    expect(t.get(c)).toMatch(/ · nº 42$/);
    expect(new Set(t.values()).size).toBe(4);
    expect(titulosDaLista([a]).get(a)).toBe('Enviar a mensagem (v1)');
  });
});

describe('ações da pessoa (as `acoes` do backend; o painel só põe o texto)', () => {
  const alvos = (e: EntradaDoLivro) => acoesDoItem(e).map((a) => a.to);

  it('sem `acoes` (habilidade, memória, receita substituída) não há botão', () => {
    expect(alvos(entrada({ kind: 'habilidade', acoes: [] }))).toEqual([]);
    expect(alvos(entrada({ kind: 'memoria', state: null, acoes: [] }))).toEqual([]);
  });

  it('cada chave vira o texto em português e a ordem do backend é mantida', () => {
    const e = entrada({ acoes: [ACAO('published', 'aprovar'), ACAO('disabled', 'rejeitar')] });
    expect(acoesDoItem(e).map((a) => [a.to, a.label, a.confirmar, a.perigo])).toEqual([
      ['published', 'Aprovar', 'Confirmar aprovação', false], ['disabled', 'Rejeitar', 'Confirmar rejeição', true]]);
    const pub = entrada({ state: 'published', acoes: [ACAO('deprecated', 'aposentar'), ACAO('disabled', 'desligar')] });
    expect(acoesDoItem(pub).map((a) => a.label)).toEqual(['Aposentar', 'Desligar']);
    expect(acoesDoItem(entrada({ state: 'disabled', acoes: [ACAO('published', 'reativar')] }))[0]?.label).toBe('Reativar');
  });

  it('chave que o painel não conhece (backend mais novo) aparece como veio, sem perigo', () => {
    const nova = { to: 'published', rotulo: 'promover', exige_motivo: true } as unknown as AcaoPermitida;
    expect(acoesDoItem(entrada({ acoes: [nova] }))).toEqual([{ to: 'published', label: 'promover', confirmar: 'Confirmar promover', perigo: false }]);
  });

  it('aprovar é o passo para cima que o backend ofereceu: validar ou aprovar', () => {
    expect(acaoDeAprovar(entrada({ state: 'candidate', acoes: [ACAO('validated', 'validar'), ACAO('disabled', 'rejeitar')] }))?.to).toBe('validated');
    expect(acaoDeAprovar(entrada({ acoes: [ACAO('published', 'aprovar'), ACAO('disabled', 'rejeitar')] }))?.to).toBe('published');
    expect(acaoDeAprovar(entrada({ state: 'published', acoes: [ACAO('deprecated', 'aposentar'), ACAO('disabled', 'desligar')] }))).toBeNull();
    expect(acaoDeAprovar(entrada({ kind: 'habilidade', acoes: [] }))).toBeNull();
  });
});

describe('habilidade na fila Para aprovar (a rota das habilidades, não a do livro)', () => {
  it('a referência é `<skill_id>@<versão>`, partida no ÚLTIMO @, com versão inteira a partir de 1 (SkillRef.parse)', () => {
    expect(refDaHabilidade('instagram.abrir-conversa@2')).toEqual({ skillId: 'instagram.abrir-conversa', version: 2 });
    expect(refDaHabilidade('a@b@13')).toEqual({ skillId: 'a@b', version: 13 });
    expect(refDaHabilidade('sem-versao')).toBeNull();
    expect(refDaHabilidade('x@')).toBeNull();
    expect(refDaHabilidade('@2')).toBeNull();
    expect(refDaHabilidade('x@0')).toBeNull();
    expect(refDaHabilidade('x@1.5')).toBeNull();
    expect(refDaHabilidade('x@-1')).toBeNull();
    expect(refDaHabilidade('x@dois')).toBeNull();
  });

  it('validada: publicar ou rejeitar (os passos da PESSOA no ciclo da habilidade); aprovar é publicar', () => {
    const hab = entrada({ kind: 'habilidade', ref: 'instagram.abrir-conversa@2', state: 'validated', side_effect: false });
    expect(acoesNaFila(hab).map((a) => [a.to, a.label])).toEqual([['published', 'Publicar'], ['disabled', 'Rejeitar']]);
    expect(acaoDeAprovarNaFila(hab)?.to).toBe('published');
  });

  it('sem referência legível ou fora de validada, a fila não oferece nada para a habilidade', () => {
    expect(acoesNaFila(entrada({ kind: 'habilidade', ref: 'sem-versao', state: 'validated' }))).toEqual([]);
    expect(acaoDeAprovarNaFila(entrada({ kind: 'habilidade', ref: 'sem-versao', state: 'validated' }))).toBeNull();
    expect(acoesNaFila(entrada({ kind: 'habilidade', ref: 'x@2', state: 'published' }))).toEqual([]);
    expect(acoesNaFila(entrada({ kind: 'habilidade', ref: 'x@2', state: 'candidate' }))).toEqual([]);
  });

  it('os outros tipos seguem as `acoes` do backend: aprovar (o próximo passo) e rejeitar', () => {
    const validado = entrada({ state: 'validated', acoes: [ACAO('published', 'aprovar'), ACAO('disabled', 'rejeitar')] });
    expect(acoesNaFila(validado).map((a) => a.to)).toEqual(['published', 'disabled']);
    expect(acoesNaFila(entrada({ kind: 'licao', ref: 'li-1', state: 'candidate', acoes: [ACAO('validated', 'validar'), ACAO('disabled', 'rejeitar')] }))
      .map((a) => a.to)).toEqual(['validated', 'disabled']);
    expect(acaoDeAprovarNaFila(validado)?.to).toBe('published');
    expect(acoesNaFila(entrada({ kind: 'memoria', state: null }))).toEqual([]);
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

  it('bloco de aprendizado no formato que o backend emite (presentation/feedback.py::_aprendido)', () => {
    // O mesmo formato de `tests/test_learning_feedback.py::test_bloco_do_aprendizado_antes_e_depois_do_voto`.
    const linha = (x: Record<string, unknown>) => ({ kind: null, ref: null, titulo: null, estado: null, papel: null,
                                                     braco: null, failure_kind: null, n: null, ...x });
    const f = lerFeedbackDaExecucao({
      run_id: 'r-1', votos: [], sinais: [],
      aprendizado: {
        receitas: [linha({ kind: 'receita', ref: '12', titulo: 'abrir (v1)', estado: 'published', papel: 'usada' })],
        fluxos: [linha({ kind: 'fluxo', ref: 'fluxo-usado', titulo: null, estado: 'disabled',
                         papel: 'usado, desligado nesta execução, evidência contra' })],
        falhas: [linha({ papel: 'classificada na leitura (retroativo)', failure_kind: 'pos_condicao_nao_comprovada', n: 1 })],
        candidatas: [linha({ kind: 'licao', ref: 'li-nova', titulo: 'espere o perfil carregar', estado: 'candidate',
                             papel: 'lição, evidência a favor' })],
        licoes: [linha({ kind: 'licao', ref: 'li-2', titulo: 'role devagar', estado: 'published',
                         papel: 'braço de controle (ator)', braco: 'holdout' })],
      },
    });
    const a = f.aprendizado ?? [];
    expect(a.map((i) => i.grupo)).toEqual(['receita', 'fluxo', 'falha', 'candidata', 'licao']);
    expect(a[0]).toEqual({ grupo: 'receita', kind: 'receita', ref: '12', texto: 'abrir (v1)', estado: 'published', papel: 'usada' });
    // Sem título (triado ou apagado), o ref é o texto; a falha ganha o rótulo pelo tipo e o × n.
    expect(a[1]?.texto).toBe('fluxo-usado');
    expect(a[2]?.texto).toBe(`${rotuloDaFalha('pos_condicao_nao_comprovada')} × 1`);
    expect(a[2]?.kind).toBeNull();
    expect(a[3]).toMatchObject({ kind: 'licao', papel: 'lição, evidência a favor' });
    // O papel do backend vence o braço (que só serve de reserva).
    expect(a[4]?.papel).toBe('braço de controle (ator)');
    // As cinco listas vazias: nada aprendido (lista vazia), não "servidor sem o bloco" (null).
    expect(lerFeedbackDaExecucao({ votos: [], sinais: [], aprendizado: { receitas: [], fluxos: [], falhas: [],
                                                                      candidatas: [], licoes: [] } }).aprendizado).toEqual([]);
    expect(lerFeedbackDaExecucao({ votos: [], sinais: [], aprendizado: null }).aprendizado).toBeNull();
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
