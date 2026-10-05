# Rodada 2, tarefa 14: painel de execução e regra de contagem das pendências

Branch `ux2/14-execucao-pendencias` (sai de `claude/ux-portal-2`). Data: 01/10/2026, máquina central
`WIN-7S2UASNLFOP`. Esforço pedido: medium. Nenhuma ação com efeito real: só leitura no navegador e testes.

## O que mudou

1. **Resumo no topo do detalhe da execução** (`features/runs/ResumoDaExecucao.tsx`, regras puras em `resumo.ts`).
   Três linhas, no lugar do parágrafo do comando que ficava cortado em 3 linhas:
   - **Pedido**: o objetivo completo. Quando é longo (mais de 100 caracteres, quebra de linha, ou o corte real medido pelo
     navegador), aparece duas linhas com o botão "Ver pedido completo" / "Mostrar menos" (`aria-expanded`,
     `aria-controls`). O texto inteiro está sempre no DOM.
   - **Resultado**: "Concluída com sucesso em 2 min 03 s" (e as outras situações: com problemas, falhou, cancelada, em
     execução há…, pausada, plano pronto, parou pedindo informação), mais "1 de 1 objetivo com sucesso". A duração usa o
     formato do resto da tela (`2 min 03 s`, não `2 min 3 s`). Sem início registrado, não inventa duração.
   - **Precisa de você**: só aparece quando há algo (perguntas da IA, objetivos bloqueados em execução em curso,
     textos para aprovar); as duas últimas linhas são atalhos para a guia que resolve.
2. **"Sucesso comprovado" explicado** por uma legenda que abre por foco de teclado, toque e mouse (botão com
   `Tooltip`): "cada etapa foi confirmada por evidência na tela do aparelho, não só pela resposta do agente. Se alguma
   etapa foi confirmada à mão ou ficou sem prova, o Relatório avisa." O mesmo texto serve ao selo "Comprovado" do
   Relatório (antes só `title`, que só abria com o mouse).
3. **Guia padrão por situação** (`abaPadraoDaExecucao`): concluída abre no **Relatório**; em execução, pausada ou
   cancelando, na **Linha do tempo**; planejando, esperando informação ou plano pronto, no **Plano**; terminou com
   problemas, falhou ou foi cancelada, em **Por aparelho** (é onde estão o que falhou e a correção da etapa). A guia do
   link `?aba=` manda sobre o padrão; voltar para o link sem guia volta ao padrão da situação. Mudar de situação com a
   tela aberta não troca a guia (quem olhava a linha do tempo não é arrancado), salvo a regra que já existia
   (planejamento termina, plano começa a rodar).
4. **Contagem de pendências**: a regra já estava implementada pela rodada 1 (ADR-062, `usePendencias().total`). Esta
   tarefa só a documentou num comentário no seletor único (`pendencias/modelo.ts::montarPendencias`) e acrescentou os
   testes que faltavam (abaixo). Nada de lógica mudou.
5. **Aprendizado**: o aviso laranja de "Revisar" passou a uma frase curta ("Itens antigos com efeito externo, ainda
   ativos."), com o resto em "Saiba mais"; o aviso de falhas sem tipo da aba "O que mais falha" perdeu o caminho de
   arquivo e o `("outro")` ("N% das falhas ainda sem tipo definido ... tarefa para o desenvolvimento"). "Rebaixar" já era
   "Desligar" desde a rodada 1; sobra só em nomes internos de código.

## Arquivos

Novos: `features/runs/ResumoDaExecucao.tsx`, `ResumoDaExecucao.module.css`, `resumo.ts`, `resumo.test.ts`,
`ResumoDaExecucao.test.tsx`, `features/pendencias/contagem.test.tsx`, este relatório.

Alterados: `features/runs/RunView.tsx` (resumo no lugar do comando; guia padrão; efeito da guia do link),
`features/pendencias/modelo.ts` (só comentário), `features/aprendizado/ParaAprovarTab.tsx` e `FalhasTab.tsx` (só texto).

**Toque em arquivo alheio**: `features/runs/ResultadoPorInstancia.tsx` (envolveu o selo "Comprovado" num `Tooltip`
focável e importou a legenda). `Runs.module.css` não foi tocado; a regra `.command` ficou sem uso (tarefa 11 mexe nesse
arquivo, não quis conflitar; pode apagar depois).

## Divergências do briefing

- Item 3 (contagem): o código já fazia o que o briefing pede; só faltavam o comentário e os testes. A "Pendências
  zerada" da avaliação era o central antes do deploy do snapshot novo (ADR-062); no backend simulado desta tarefa o
  número não é zero (ver prova 3).
- Item 4: "Para aprovar" e Pendências já liam a mesma fila (`GET /aprendizado/pendentes`). O que o Aprendizado mostra a
  mais é o "Revisar" (legado ativo), que **não é pendência de propósito** (continua valendo até a pessoa decidir,
  ADR-054). Registrado em comentário e em teste.
- "1 de 1 com sucesso comprovado": a tela nunca disse "comprovado" na legenda de progresso; o selo "Comprovado" é por
  aparelho, no Relatório, e só vale com `proven: true`. Não escrevi "comprovado" no resumo (a contagem de objetivos com
  sucesso não prova que todas as etapas foram confirmadas); a legenda explica a diferença.
- Guia padrão para execução terminada mal: o briefing só fala em concluída/em andamento. `completed_with_issues`,
  `failed` e `cancelled` continuam em "Por aparelho".

## Provas

**simulated** (vitest, 1052 testes passam, 88 arquivos; `npm run typecheck` limpo):
- `features/pendencias/contagem.test.tsx`: regra de contagem pura (vazio, nulo, uma origem por vez com o que NÃO conta,
  as quatro juntas somando 4); selo do menu, chip do topo e lista da caixa dão 4 com uma pendência de cada origem, e
  0/"Nada esperando você" com tudo vazio; fila "Para aprovar" do Aprendizado = pendências de origem Aprendizado (mesmo
  conjunto de chaves, incluindo habilidade e lição), com o legado "Revisar" fora.
- `features/runs/resumo.test.ts`: guia padrão, frase de resultado, "N de M", pedido longo, "o que precisa de você".
- `features/runs/ResumoDaExecucao.test.tsx`: resumo com duração e pedido completo, legenda abrindo por foco,
  expandir/recolher o pedido, "Precisa de você" só quando há algo (e o atalho abre "Por aparelho"), guia padrão por
  situação, `?aba=` mandando sobre o padrão, voltar ao link sem guia.

**real** (01/10/2026, central `WIN-7S2UASNLFOP`, commit de base `085e34e`, backend simulado do worktree na 8714, vite na
5114, aba própria do painel da IDE, aberto por `localhost`): `AI_PROVIDER=simulated`, config e banco no rascunho,
`base_console_port: 5640`, `appium.autostart: false`, SDK inexistente. Dados semeados pelas rotas (login, 3 execuções,
1 persona) e, **fora das rotas, por `UPDATE`/`INSERT` direto no SQLite do rascunho**: uma aprovação pendente e a
situação "concluída" de uma execução (com início e fim de 2 min 03 s). Por isso, nesse dado, "Concluída com sucesso" e
"0 de 1 objetivo com sucesso" aparecem juntos: é artefato do semeio, não da tela.
1. Resumo: concluída com pedido longo mostrou "Concluída com sucesso em 2 min 03 s", "Ver pedido completo" (alvo
   129 x 38 px), legenda "sucesso comprovado" abrindo ao focar (texto confirmado no DOM); execução `needs_input` mostrou
   "Precisa de você: Responder às 2 perguntas da IA…". Guia padrão: a concluída abriu em "Relatório".
2. Larguras 1440, 1024, 768 e 390 px, nas telas Execuções (concluída e `needs_input`), Pendências, Aprendizado e
   Aprendizado > O que mais falha: sem rolagem horizontal, nenhum alvo abaixo de 32 px no `main`.
3. Pendências no simulado: com 1 execução `needs_input` e 1 aprovação, o menu mostrou "Pendências 2 (2 esperando você)",
   o topo "2 aguardando você" e a caixa "Todas (2)" com Persona (1) e Execução (1): o mesmo número nos três.
4. axe-core 4.13 (wcag2a/aa, 21a/aa, 22aa, best-practice) nessas telas e larguras: única violação `region (moderate)`
   na região de avisos (`._toastRegion…`), a mesma da linha de base da rodada 1 (`13-prova-simulada.md`, prova 4); 0
   `color-contrast`, 0 `serious`/`critical`.

**not_run**:
- Execuções em andamento (`running`) vistas na tela: o backend simulado normaliza o status que forcei no banco; a guia
  Linha do tempo para `running` está provada só no teste.
- Origens Aprendizado e Intervenção no backend simulado (não semeei item de aprendizado nem sessão em intervenção); só os
  testes cobrem as quatro origens.
- Leitura do central real (porta 8000): não consultado.
- O **Painel** (`#/painel`) com a execução selecionada: o mesmo `RunView`, dentro de um cartão mais estreito; não foi
  aberto nos quatro tamanhos. A quebra do resumo a 560 px vale para a largura da janela, não do cartão.
- O selo "Comprovado" do Relatório com `Tooltip` focável (`ResultadoPorInstancia.tsx`): o dado semeado não tem
  `proven: true`, então não renderizou no simulado e não tem teste próprio; o axe não o viu.
- Lighthouse e `ui-verificar.mjs` (este não faz login).

## Mudança de comportamento a registrar

A regra que já existia ao fim do planejamento (`planned` virar `running` troca a guia pela padrão) agora leva à Linha do
tempo, e não mais a "Por aparelho". Decorre da nova regra de guia padrão; a suíte passa.

## O que ficou de fora

- Apagar a regra `.command` agora sem uso em `Runs.module.css` (tarefa 11).
- O texto "Plano pronto para inspeção" do `status_detail` na linha de metadados repete parte do resultado quando a
  execução está planejada; deixei como está.
