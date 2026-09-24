# Executar o plano-100 a partir da sessão da IDE

O executor mudou em 22/09. Antes, `python scripts/claude-plan-100.py` abria `claude -p` em subprocesso, um por
bloco. **Isso não funciona no aplicativo Claude Code**, e não por configuração:

- o próprio executor se recusava a rodar dentro do agente (`CLAUDECODE=1`), para não abrir sessão aninhada — e é
  de dentro do agente que se quer rodar;
- **não existe executável `claude` nesta máquina**: o aplicativo não publica um CLI no PATH (conferido no Bash e
  no PowerShell, e não há instalação global do npm);
- e, mesmo que existisse, `--resume` numa conversa só para 67 itens ficaria **caro em vez de barato**: pela
  metade do plano, toda chamada carregaria a conversa inteira das anteriores.

Agora quem orquestra é a sessão da IDE, e o trabalho vai para subagentes — um por grupo de arquivos, com o modelo
e o esforço que o item merece.

## Estado do mecanismo (24/09/2026)

- O plano tem **88 IDs** (67 originais + 10.5, 11.x, 12.x, 13.x). O item 10.5 entrou no plano em `c0c982d` sem entrar
  no mapa de blocos, e isso quebrava `check` e `relatorio` ("O mapa de blocos não casa com o plano"); foi posto no
  bloco `10-capacidade`. **Regra:** todo ID novo em `plano-100.md` entra num bloco de `.claude/plano-100.json`, e
  depois `python scripts/plano-100-pacotes.py` (sem `--fila`, que não regenera um índice existente).
- Oito registros do `estado.json` foram escritos **à mão**, fora do `aplicar` e sem rodada: 11.10, 12.1, 12.2,
  13.1–13.3 com prova `tests`, 10.5 com `unit` e 12.3 com estado `pending` (commits `a4237da`, `407cfce`, `bfffb0d`,
  `c0c982d`). Esses valores estão fora do vocabulário que `aplicar` e o workflow aceitam (`real`/`simulated`/`not_run`;
  `implemented`/`partial`/`blocked`). Ficaram como foram escritos, porque é histórico; `tests`/`unit` equivalem a
  validação automatizada (não real). `scripts/docs-check.py` avisa sobre eles.
- Trabalho feito fora da esteira deve ser registrado por um `resultado.json` e `aplicar`, não editando o estado.
- Retomada e fechamento de tarefa: skills `retomar`, `preparar-tarefa` e `fechar-tarefa`; protocolo em
  [`../CLAUDE.md`](../CLAUDE.md).

## Como rodar

Peça na sessão: *"execute o bloco 0-contratos do plano-100"*. Nos bastidores são quatro passos, e você pode fazê-los
à mão:

```bash
python scripts/plano-100-pacotes.py --fila --bloco 0-contratos
```

O retorno vai como `args` para o workflow `.claude/workflows/plano-100.js`, o resultado é salvo em JSON, e então:

```bash
python scripts/claude-plan-100.py aplicar resultado.json
```

`python scripts/claude-plan-100.py check` mostra o que falta, por modelo, e
`python scripts/plano-100-custo.py` diz quanto já foi gasto. Nenhum dos três scripts chama IA.

## As três economias

Um plano de 67 itens não fica caro pela orquestração; fica caro por **contexto repetido** e por **modelo grande em
trabalho pequeno**. As três medidas atacam exatamente isso.

**1. Pacote auto-contido por item.** `scripts/plano-100-pacotes.py` monta, sem IA, um arquivo por item com a linha
do plano, os achados citados **na íntegra** e os arquivos que as evidências mencionam.

| | Sem pacote | Com pacote |
|---|---|---|
| Para se orientar num item | `plano-100.md` (34 KB) + caçar dentro do apêndice (467 KB) | um arquivo, 7 KB em média |
| Nos 67 itens | ~33 MB relidos, ~8 milhões de tokens | 535 KB, ~137 mil tokens |

O pacote manda explicitamente **não abrir** o plano nem o apêndice. Custo de gerar: zero tokens.

**2. Modelo por item, não por plano.** O mapa antigo fixava Opus 5 nos 67 itens e só variava o esforço. Opus
atualizando uma tabela de documentação é desperdício; Sonnet mexendo em concorrência, protocolo ou migração é
risco. A distribuição de hoje: **32 Opus** (concorrência, protocolo, segurança, esquema de banco, e tudo de porte
G), **33 Sonnet**, **1 Haiku**, **1 decisão sua** (não vai para agente nenhum).

**3. Um agente por grupo de arquivos.** Itens que tocam o mesmo arquivo caem no mesmo agente: o arquivo é lido uma
vez, não uma vez por item. Itens de arquivos disjuntos ficam em agentes separados. Os 67 itens viram **38 agentes**,
no máximo 3 itens cada — um agente com oito itens perde o fio, e quando erra leva junto o que já tinha feito.

Agrupar também é o que permitiria paralelizar com segurança. **Não paralelizamos**: a lista de arquivos é derivada
do texto das evidências, então é um palpite bom, não um contrato, e dois agentes editando o mesmo arquivo por causa
de um palpite errado perdem trabalho de um jeito que só aparece depois.

## O que custa de verdade, medido

`python scripts/plano-100-custo.py` lê os transcritos locais e diz quanto cada agente custou. Nada de estimativa:
são os contadores de uso do próprio transcrito, com os preços de `ai.prices` do `config.yaml`.

A primeira execução real (bloco `0-contratos`, 3 itens entregues) custou **US$ 17,87**. O que ela ensina:

| | tokens | US$ |
|---|---|---|
| Item 0.6 (médio: backend + frontend + 2 arquivos de teste novos) | 32,8 M | **8,29** |
| Itens 0.4 + 0.9 juntos (um agente) | 32,7 M | **7,92** |
| Os três conferentes Haiku somados | 4,4 M | **1,24** |
| Agente que recusou a tarefa (defeito já corrigido) | 0,5 M | 0,41 |

**97% dos tokens são cache lido.** Isso é o ponto que muda como se economiza aqui: o que encarece não é o que o
agente escreve — a saída inteira das duas rodadas deu 135 mil tokens —, é **quantas vezes ele relê o que já tem**.
Cada turno relê o contexto acumulado. Logo: comando com saída longa, arquivo reaberto e bateria de teste ampla
custam caro de um jeito que não aparece na sensação de "foi rápido". O agente do item 0.6 rodou 50 testes em 9
arquivos para uma mudança em 2 — daí a regra 4 nomear os arquivos hoje.

Projeção para os 64 itens restantes, de amostra pequena (n=3) e por isso uma faixa, não um número: **US$ 400 a
700**. Os 32 itens de Opus pesam ~2,5× por token, e a regra nova de testes ainda não foi medida.

Para comparação, e porque muda o que "caro" significa aqui: **a auditoria que produziu o plano custou
US$ 1.178,50** — 21 agentes lendo o código a fundo, 1,55 bilhão de tokens. `--tudo` mostra essa conta. Executar o
plano inteiro deve custar menos da metade de tê-lo descoberto. Um agente que *lê para entender* percorre muito mais
contexto que um que *implementa um item já especificado* — e é exatamente por isso que o pacote por item existe.

## O que impede uma entrega de mentira

Cada grupo passa por um conferente **Haiku**, que não edita nada: ele roda `git diff`, confere se a evidência aponta
para arquivo e linha que existem e dizem o que o agente afirmou, e procura TODO, stub e teste marcado como skip.
Questionou, aparece como **questionada** no relatório. É barato e pega a falha mais cara: `implemented` sem diff.

Depois, `aplicar` recusa o registro de `implemented` sem evidência, `blocked` sem motivo e prova sem evidência.

## Os limites que valem para todo agente

Vão no prompt de cada chamada, porque subagente não lê a documentação do repositório:

- Implementa só os IDs recebidos; não abre o plano nem o apêndice.
- A auditoria descreve o commit `f1e61b3` — confere no código de hoje antes de mexer.
- Roda **teste direcionado**, nunca a suíte inteira (11 minutos); nunca enfraquece ou apaga teste.
- **Não comita e não dá push**: quem comita é a sessão, depois de olhar o diff.
- **Não encosta no mundo real**: nada de reiniciar produção, mexer no parque, no relógio ou no WSL, chamada paga,
  conta real do Instagram. Onde o item exige isso, entrega o código e o procedimento e marca a prova como
  `not_run`, dizendo qual autorização falta. Sete itens têm esse aviso no pacote: 0.1, 0.7, 6.6, 7.4, 8.4, 10.3, T.1.
- Segredo não entra em código, teste, log nem resultado.
- Migração já aplicada não se edita; cria-se a próxima.
- Decisão reservada a você (item 0.10) é `blocked`, nunca "implementada".

## Duas armadilhas que a primeira rodada real revelou

Ambas custaram tempo e estão consertadas; ficam escritas para não voltarem.

**O agente pode recusar a tarefa delegada.** Na primeira execução o subagente leu o pedido da conversa no contexto
dele, concluiu que aquele pedido prevalecia sobre a tarefa que o workflow lhe deu, devolveu `items: []` com uma
explicação — e não alterou uma linha de código. O conserto tem duas partes: o prompt agora **afirma** que a
delegação é a tarefa autorizada e que uma linha por ID é obrigatória; e workflow e livro-razão passaram a **recusar**
resultado que não cubra os IDs pedidos. Antes, um item recusado sumia do registro em silêncio, que é a pior forma
de perder trabalho.

**Nenhuma literal do `plano-100.js` pode ter quebra de linha de verdade dentro.** O diálogo de aprovação do
Workflow recusa o script com "control characters that would be hidden in the approval dialog". Texto de várias
linhas se monta com `[...].join(NL)`, como em `REGRAS`. Vale também para quem editar o arquivo por script: o shell
come as contrabarras, e `\n` escrito num script vira quebra de linha de verdade sem avisar.

## Ordem sugerida

A do plano: `0-protecao`, `0-ajustes`, `0-contratos`, depois a fase 1 (`1-comandos`) e seguindo. A fase 0 é barata e
protege dado; a fase 1 é o coração do pedido original.
