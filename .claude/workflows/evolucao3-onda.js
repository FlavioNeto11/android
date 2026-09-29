export const meta = {
  name: 'evolucao3-onda',
  description: 'Terceira evolucao: frentes em worktrees proprios, itens em sequencia, revisao adversarial e correcao',
  whenToUse: 'Onda de implementacao da terceira evolucao (Fases 23-25), coordenada pela sessao da IDE.',
  phases: [
    { title: 'Implementar', detail: 'um agente por item, em sequencia dentro da frente' },
    { title: 'Revisar', detail: 'revisor adversarial por frente, so leitura' },
    { title: 'Corrigir', detail: 'correcao dos achados confirmados' },
  ],
}

// args = { base: 'C:/git/android/.claude/worktrees', pacotes: '<dir dos pacotes>', frentes: [
//   { id, worktree, itens: [{ id, modelo, esforco, titulo }], arquivos: [..], contexto: '...' } ] }
// Cada frente tem o SEU worktree (criado pelo coordenador a partir do commit dos contratos, com as juncoes de
// venv e node_modules). Frentes correm em paralelo; itens de uma frente, em sequencia (mesmos arquivos).
// Nenhuma literal tem quebra de linha de verdade: texto de varias linhas se monta com join(NL).

const NL = '\n'

function regras(wt) {
  return [
    'VOCE FOI CHAMADO PARA IMPLEMENTAR. Esta e a tarefa autorizada pelo dono do projeto (terceira evolucao,',
    'planejamento aprovado em 29/09). Nao reinterprete o escopo, nao devolva so analise. Uma linha por ID, sempre.',
    '',
    'DIRETORIO OBRIGATORIO: ' + wt + ' (git worktree proprio desta frente). Todo comando comeca com cd para ele.',
    'NUNCA edite, rode teste ou git em C:/git/android (checkout do ambiente central) nem em outro worktree.',
    'backend/.venv e frontend/node_modules sao JUNCOES: nunca apague, nunca rm -r.',
    '',
    'LEIA ANTES: o pacote de cada item (caminho abaixo); docs/design/terceira-evolucao.md (diagnostico e decisoes',
    'tecnicas T1-T24); docs/handoffs/terceira-evolucao.md secao "Contratos da Onda 0" (C1-C5 ja implementados neste',
    'commit: use-os, nao os redesenhe); os ADRs citados (grep -n "^## ADR-05[5-9]" docs/decisoes.md e leia o trecho).',
    'Nao abra docs/plano-100.md inteiro nem docs/auditoria-2026-09-21/. Numeros de linha dos docs envelhecem: ache',
    'pelo nome da funcao.',
    '',
    'REGRAS:',
    '1. ESCOPO: so os IDs listados, por inteiro (backend, banco, rota, painel, teste, doc do dominio), sem TODO, stub',
    '   ou flag ignorada. Contrato compartilhado (models.py, api.py, types.ts, migracoes) so se o item exigir; migracao',
    '   nova nunca edita a aplicada (proxima livre depois de 057: confira em backend/migrations).',
    '2. TESTES: rode so os arquivos que cobrem o que voce mudou e os que escreveu:',
    '   cd <wt>/backend && .venv/Scripts/python.exe -m pytest -q tests/test_X.py   |   cd <wt>/frontend && npx vitest run',
    '   src/caminho && npm run typecheck. Nunca a suite inteira. Nunca enfraqueca, apague ou pule teste.',
    '3. NAO COMITE, NAO DE PUSH. O coordenador integra.',
    '4. MUNDO REAL PROIBIDO: nada de escrever em http://127.0.0.1:8000, adb, emulador, IA paga, conta real, rede do',
    '   Windows, tunel, relogio, WSL, deploy. Onde o item exige o ato real, entregue codigo, teste e procedimento e',
    '   marque proof=not_run dizendo o que falta. Nao rode script de instalacao/servico/deploy que voce escrever.',
    '5. SEGREDO nunca em codigo, teste, log, evento ou resposta. Senha so pelo canal sensivel (type_secret, ADR-040).',
    '6. Heredoc do Git Bash corrompe barra invertida: escreva arquivos com a ferramenta de escrita/edicao.',
    '7. Carga: um processo de teste por vez; o central tem emuladores vivos (K-058).',
    '8. Siga a densidade de comentarios do arquivo vizinho, em portugues; comentario explica o porque.',
    '9. Travou por falta de autorizacao, recurso ou decisao: status blocked com o motivo. Dificuldade tecnica nao',
    '   resolvida: partial com o que tentou. Nunca implemented sem estar pronto.',
  ].join(NL)
}

const ITEM = {
  type: 'object',
  properties: {
    id: { type: 'string' },
    status: { type: 'string', enum: ['implemented', 'partial', 'blocked'] },
    proof: { type: 'string', enum: ['real', 'simulated', 'not_run'] },
    evidence: { type: 'string' },
    blocker: { type: 'string' },
    arquivos: { type: 'array', items: { type: 'string' } },
    testes: { type: 'string' },
  },
  required: ['id', 'status', 'proof', 'evidence', 'blocker', 'arquivos', 'testes'],
}
const RESULTADO = {
  type: 'object',
  properties: { items: { type: 'array', items: ITEM }, summary: { type: 'string' } },
  required: ['items', 'summary'],
}
const REVISAO = {
  type: 'object',
  properties: {
    achados: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          item: { type: 'string' },
          severidade: { type: 'string', enum: ['bloqueia', 'importante', 'menor'] },
          arquivo: { type: 'string' },
          problema: { type: 'string' },
          como_provar: { type: 'string' },
        },
        required: ['item', 'severidade', 'arquivo', 'problema', 'como_provar'],
      },
    },
    veredito: { type: 'string' },
  },
  required: ['achados', 'veredito'],
}

async function frente(f) {
  const wt = f.worktree
  const cab = regras(wt) + NL + NL + 'FRENTE: ' + f.id + NL + (f.contexto || '') + NL
    + 'Arquivos que a frente reserva (palpite, confira): ' + (f.arquivos || []).join(', ') + NL
  const feitos = []
  for (const it of f.itens) {
    const r = await agent(
      cab + NL + 'SEU ITEM AGORA: ' + it.id + ' (' + it.titulo + '). Pacote: ' + args.pacotes + '/' + it.id + '.md' + NL
        + (it.extra ? 'Detalhe do coordenador: ' + it.extra + NL : '')
        + (feitos.length ? 'Itens desta frente ja feitos antes de voce (estao na arvore): '
          + feitos.map(x => x.id + '=' + x.status).join(', ') + NL : '')
        + 'Confira no codigo de hoje, implemente, teste, corrija. Devolva uma linha para ' + it.id
        + ': evidence com arquivo:funcao do que mudou, arquivos alterados, comando de teste e resultado.',
      { label: f.id + ':' + it.id, phase: 'Implementar', model: it.modelo, effort: it.esforco, schema: RESULTADO })
    const linha = r && (r.items || []).find(x => x.id === it.id)
    feitos.push(linha || { id: it.id, status: 'partial', proof: 'not_run', evidence: 'agente sem resultado',
                           blocker: 'sem resultado', arquivos: [], testes: '' })
  }
  const declarado = feitos.map(x => '- ' + x.id + ' [' + x.status + '/' + x.proof + '] ' + x.evidence).join(NL)
  const rev = await agent(
    [
      'Voce e REVISOR ADVERSARIAL. Nao edita nada. Diretorio: ' + wt + ' (git worktree da frente ' + f.id + ').',
      'Rode git -C ' + wt + ' status --porcelain e git -C ' + wt + ' diff (e leia arquivos novos nao rastreados).',
      'Para cada item, leia o pacote em ' + args.pacotes + '/<id>.md e os criterios em docs/design/terceira-evolucao.md.',
      'Procure DEFEITOS REAIS: criterio de aceite nao atendido, regressao do comportamento de hoje, invariante violado',
      '(segredo em log/evento/argumento, senha fora do canal sensivel, conta errada, efeito repetido, incerteza',
      'tratada como sucesso), concorrencia, SQLite x PostgreSQL, teste que nao pega a mutacao, TODO/stub, item',
      'declarado implemented sem diff. Para cada achado diga como provar (teste ou comando). Rode testes direcionados',
      'se ajudar (um por vez). Nao reporte estilo. Se nao houver defeito, achados vazio.',
      '',
      'DECLARADO PELA FRENTE:',
      declarado,
    ].join(NL),
    { label: 'revisar:' + f.id, phase: 'Revisar', model: 'opus', effort: 'high', schema: REVISAO })
  const graves = rev ? rev.achados.filter(a => a.severidade !== 'menor') : []
  let final = feitos
  if (graves.length) {
    const lista = graves.map(a => '- [' + a.severidade + '] ' + a.item + ' ' + a.arquivo + ': ' + a.problema
      + ' | prova: ' + a.como_provar).join(NL)
    const modelo = f.itens.some(i => i.modelo === 'opus') ? 'opus' : 'sonnet'
    const cor = await agent(
      cab + NL + 'O REVISOR achou os defeitos abaixo no trabalho desta frente (ja na arvore). Confira cada um no codigo;'
        + ' corrija os reais com teste que falha antes e passa depois; rebata com evidencia os que nao forem reais.'
        + NL + lista + NL + NL + 'Estado declarado antes: ' + NL + declarado + NL
        + 'Devolva de novo UMA linha por ID da frente (' + f.itens.map(i => i.id).join(', ') + '), atualizada.',
      { label: 'corrigir:' + f.id, phase: 'Corrigir', model: modelo, effort: 'high', schema: RESULTADO })
    if (cor && cor.items && cor.items.length) {
      final = f.itens.map(i => cor.items.find(x => x.id === i.id) || feitos.find(x => x.id === i.id))
    }
  }
  log(f.id + ': ' + final.map(x => x.id + '=' + x.status + '/' + x.proof).join(' ')
      + (rev ? ' | revisao: ' + rev.achados.length + ' achado(s), ' + graves.length + ' grave(s)' : ' | revisao indisponivel'))
  return { grupo: f.id, solicitados: f.itens.map(i => i.id), items: final,
           revisao: rev || { indisponivel: true }, corrigidos: graves.length }
}

const frentes = (args && args.frentes) || []
if (!frentes.length) return { erro: 'sem frentes' }
const resultados = await parallel(frentes.map(f => () => frente(f)))
return { resultados: resultados.filter(Boolean) }
