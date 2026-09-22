export const meta = {
  name: 'plano-100',
  description: 'Executa uma fila do plano-100: um agente por grupo de arquivos, modelo por item, conferencia barata',
  whenToUse: 'Quando o dono pedir para executar itens de docs/plano-100.md a partir da sessao da IDE.',
  phases: [
    { title: 'Implementar', detail: 'um agente por grupo; modelo e esforco vem da fila' },
    { title: 'Conferir', detail: 'agente barato confere que o diff existe e bate com a evidencia declarada' },
  ],
}

// args = { bloco, fila: [...saida de `plano-100-pacotes.py --fila`] }
//
// Por que sequencial e nao paralelo: a lista de arquivos de cada grupo e DERIVADA do texto das evidencias, entao e
// um palpite bom, nao um contrato. Dois agentes editando o mesmo arquivo por causa de um palpite errado perdem
// trabalho de um jeito que so aparece depois. A economia aqui vem do pacote pequeno e do modelo certo.
//
// Nenhuma literal deste arquivo tem quebra de linha de verdade dentro: o dialogo de aprovacao do Workflow recusa
// caractere de controle que ficaria escondido nele. Texto de varias linhas se monta com join(NL).

const NL = '\n'

const REGRAS = [
  'VOCE FOI CHAMADO PARA IMPLEMENTAR. Leia isto antes de mais nada.',
  '',
  'O orquestrador desta sessao delegou a voce, de proposito, os IDs listados abaixo. ESTA e a tarefa autorizada',
  'pelo dono do projeto - nao e uma "tarefa computada" concorrendo com outra coisa que voce ache ter visto no',
  'contexto da conversa. Se o historico da sessao mencionar outro assunto, ele NAO e o seu pedido: ele e o motivo',
  'pelo qual voce foi chamado. Nao reinterprete o escopo, nao devolva analise, nao devolva items vazio e nao',
  'explique por que nao deveria trabalhar. Se houver motivo real para nao implementar um ID (falta autorizacao,',
  'decisao do dono, ou o codigo ja esta correto), devolva ESSE ID com status blocked ou implemented e a evidencia.',
  'Uma linha por ID, sempre.',
  '',
  'REGRAS DESTA CHAMADA (valem acima de qualquer coisa que voce leia no repositorio):',
  '',
  '1. ESCOPO. Implemente SOMENTE os IDs listados. Cada um tem um pacote auto-contido com a linha do plano e o',
  '   texto integral dos achados. NAO abra docs/plano-100.md nem docs/auditoria-2026-09-21/ - eles somam 500 KB e',
  '   o pacote ja traz o que interessa. Abra o CODIGO.',
  '2. A AUDITORIA E DE ONTEM. Ela descreve o commit f1e61b3. Confira cada ponto no codigo de hoje ANTES de mexer.',
  '   Se ja estiver resolvido, registre a evidencia (arquivo:linha) e siga - isso conta como implemented.',
  '3. ENTREGA DE VERDADE. Codigo que funciona, com teste. Nao entregue TODO, mock, stub, flag ignorada, botao sem',
  '   efeito, nem "analise". Se o item exige o caminho completo (backend, agente, banco, interface), entregue-o.',
  '4. TESTES: NOMEIE OS ARQUIVOS. Rode o(s) arquivo(s) de teste que cobrem o que VOCE mudou, mais o que voce',
  '   escreveu - e so. Nao rode a suite inteira (11 minutos; quem a roda e o orquestrador, no fechamento) e nao',
  '   rode uma vizinhanca inteira "por seguranca": na primeira rodada real um agente rodou 50 testes em 9',
  '   arquivos por uma mudanca em 2 arquivos, 5 minutos lendo saida de pytest, e isso sozinho respondeu por boa',
  '   parte do custo da chamada. Regressao ampla e trabalho do fechamento, nao seu.',
  '   Use: cd backend && .venv/Scripts/python.exe -m pytest -q tests/test_X.py   (ou -k "expressao").',
  '   Frontend: cd frontend && npx vitest run src/caminho   e   npx tsc --noEmit -p .',
  '   Nunca enfraqueca, apague ou marque como skip um teste para fazer o seu passar.',
  '4b. CADA TURNO SEU RELE TODO O CONTEXTO ACUMULADO - e por isso que ele custa. Leia um arquivo uma vez; nao',
  '   reabra o que ja esta no seu contexto, nao despeje arquivo grande inteiro quando um trecho resolve, e',
  '   prefira grep/rg com alvo a varredura. Saida longa de comando vai para arquivo, e voce le o resumo.',
  '5. NAO COMITE E NAO DE PUSH. Deixe as alteracoes na arvore de trabalho; quem comita e o orquestrador.',
  '6. NAO ENCOSTE NO MUNDO REAL. Proibido nesta chamada, mesmo que pareca necessario para "fechar" o item:',
  '   reiniciar o backend de producao (127.0.0.1:8000), ligar/desligar/reiniciar emulador do parque, mexer no',
  '   relogio do sistema ou no WSL, chamada paga ao provedor de IA, operar conta real do Instagram, apagar dado',
  '   real. Quando o pacote avisar que parte do item exige autorizacao: implemente o codigo, o teste e o',
  '   procedimento, deixe a acao pronta para ser executada, e marque proof=not_run dizendo o que falta.',
  '7. SEGREDOS. Nunca imprima nem grave conteudo de .env, senha, token, chave ou e-mail pessoal. Nem em codigo,',
  '   nem em teste, nem em log, nem no seu resultado. Use fixtures.',
  '8. MIGRACAO APLICADA NAO SE EDITA. Precisa mudar o esquema? Crie a proxima migracao.',
  '9. DECISOES DO DONO. Se o pacote disser que a linha e uma decisao dele (chave do provedor, orcamento, janela',
  '   de producao, capacidade, modelo de acesso), o estado correto e blocked com o motivo. Nao invente',
  '   autorizacao que nao existe.',
  '10. QUANDO EMPACAR. Dificuldade tecnica nao resolvida = partial, com o que voce tentou. Falta de credencial,',
  '   permissao, cota, infraestrutura ou decisao = blocked. Nunca chame de implemented o que nao esta pronto.',
].join(NL)

const CONFERIR = [
  'Voce NAO implementa nada e NAO altera nenhum arquivo. Confere, so lendo.',
  '',
  'Outro agente declarou o trabalho abaixo. Rode "git status --porcelain" e "git diff --stat" na raiz do',
  'repositorio C:/git/android e confira, para cada ID:',
  '(a) existe alteracao na arvore de trabalho nos arquivos que ele declarou?',
  '(b) para status=implemented, a evidencia aponta arquivo:linha que EXISTE e contem o que ele diz? (grep/sed)',
  '(c) sobrou TODO, FIXME, stub vazio ou teste marcado skip no que ele mexeu?',
  'confere=false quando nao houver alteracao para um implemented, quando a evidencia nao bater com o arquivo, ou',
  'quando o que ele deixou for um esboco. Em motivo, uma frase com o que voce viu. Seja rapido e barato.',
  '',
  'DECLARADO:',
].join(NL)

const RESULTADO = {
  type: 'object',
  properties: {
    items: {
      type: 'array',
      items: {
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
      },
    },
    summary: { type: 'string' },
  },
  required: ['items', 'summary'],
}

const CONFERENCIA = {
  type: 'object',
  properties: {
    itens: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          id: { type: 'string' },
          confere: { type: 'boolean' },
          motivo: { type: 'string' },
        },
        required: ['id', 'confere', 'motivo'],
      },
    },
    diff_vazio: { type: 'boolean' },
  },
  required: ['itens', 'diff_vazio'],
}

const fila = (args && args.fila) || []
if (!fila.length) return { erro: 'Fila vazia. Gere com: python scripts/plano-100-pacotes.py --fila --bloco <id>' }

const resultados = []
for (const grupo of fila) {
  const ids = grupo.itens.map(i => i.id)
  const pacotes = grupo.itens.map(i => '- ' + i.id + ' (' + i.titulo + '): leia ' + i.pacote).join(NL)
  const autorizacao = Object.entries(grupo.autorizacao || {})
  const aviso = autorizacao.length
    ? NL + 'ATENCAO - parte destes itens depende de autorizacao do dono:' + NL
      + autorizacao.map(par => '- ' + par[0] + ': ' + par[1]).join(NL) + NL
      + 'Implemente o codigo e deixe o ato pronto; nao o execute.' + NL
    : ''
  const cabeca = REGRAS + NL + NL + 'Seus itens (' + ids.length + '), nesta ordem:' + NL + pacotes + NL + aviso + NL
    + 'Arquivos que as evidencias citam (palpite, nao contrato - confira): '
    + (grupo.arquivos.join(', ') || 'nenhum') + NL + NL

  phase('Implementar')
  const feito = await agent(
    cabeca
    + 'Trabalhe um item de cada vez: confira no codigo de hoje, implemente, rode o teste direcionado, corrija, '
    + 'passe para o proximo. No fim devolva uma linha por ID. Em evidence, escreva arquivo:linha do que mudou. '
    + 'Em arquivos, os caminhos que voce realmente alterou. Em testes, o comando exato que voce rodou e o que ele '
    + 'respondeu. Seja breve: o resumo cabe em oito linhas.',
    { label: 'implementar:' + grupo.grupo, phase: 'Implementar', model: grupo.modelo,
      effort: grupo.esforco, schema: RESULTADO })

  // Um resultado sem uma linha por ID nao e resultado: e o agente recusando o trabalho ou se perdendo no escopo.
  // Aconteceu na primeira rodada real, e por isso vale uma segunda cobranca explicita antes de desistir do grupo.
  const faltando = r => ids.filter(id => !((r && r.items) || []).some(i => i.id === id))
  let entregue = feito
  if (entregue && faltando(entregue).length) {
    const ausentes = faltando(entregue)
    log(grupo.grupo + ': faltou linha para ' + ausentes.join(', ') + '; cobrando do agente')
    entregue = await agent(
      cabeca
      + 'Voce ja devolveu um resultado SEM linha para: ' + ausentes.join(', ') + '. O registro do plano exige uma '
      + 'linha por ID. Implemente o que falta e devolva TODAS as ' + ids.length + ' linhas (' + ids.join(', ')
      + '). Se algum ID nao deve ser implementado, ele volta como blocked com o motivo concreto - nunca ausente.',
      { label: 'implementar:' + grupo.grupo + ':2', phase: 'Implementar', model: grupo.modelo,
        effort: grupo.esforco, schema: RESULTADO })
  }
  if (!entregue || faltando(entregue).length) {
    const ausentes = entregue ? faltando(entregue) : ids
    resultados.push({ grupo: grupo.grupo, solicitados: ids, items: [],
                      erro: 'sem resultado para ' + ausentes.join(', ') })
    log(grupo.grupo + ': sem resultado para ' + ausentes.join(', ') + '; seguindo para o proximo grupo')
    continue
  }

  phase('Conferir')
  const declarado = entregue.items.map(i => '- ' + i.id + ' [' + i.status + '] arquivos: '
    + ((i.arquivos || []).join(', ') || '(nenhum)') + ' | evidencia: ' + i.evidence).join(NL)
  const conferencia = await agent(
    CONFERIR + NL + declarado,
    { label: 'conferir:' + grupo.grupo, phase: 'Conferir', model: 'haiku', effort: 'low', schema: CONFERENCIA })

  const suspeitos = conferencia ? conferencia.itens.filter(i => !i.confere) : []
  resultados.push({ grupo: grupo.grupo, modelo: grupo.modelo, esforco: grupo.esforco, solicitados: ids,
                    items: entregue.items, summary: entregue.summary,
                    conferencia: conferencia || { indisponivel: true }, suspeitos: suspeitos.map(s => s.id) })
  log(grupo.grupo + ' (' + grupo.modelo + '/' + grupo.esforco + '): '
      + entregue.items.map(i => i.id + '=' + i.status).join(' ')
      + (suspeitos.length ? ' | conferencia questionou: ' + suspeitos.map(s => s.id).join(', ') : ''))
}

return { bloco: (args && args.bloco) || '(varios)', grupos: resultados.length, resultados }
