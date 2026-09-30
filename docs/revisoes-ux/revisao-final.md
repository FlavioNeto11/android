# Tarefa 09: revisão final, regressão e testes de responsividade (relatório parcial, FASE A)

Branch `ux/09-revisao-final`, que sai de `claude/ux-portal` `5da4cfc` (tarefas 01 a 08 integradas, mais
`scripts/ui-verificar.mjs` e `scripts/ui-auditoria.js`). Base de comparação: `origin/main` `beb8056`. Data: 30/09/2026,
cerca de 21:05Z a 21:40Z. Máquina: central (Windows Server 2025). Modelo: Opus 5.5, esforço high. Revisor independente:
não participei das tarefas 01 a 08.

**Este relatório é parcial.** A FASE A é só leitura de código e de relatórios: a coordenação do ambiente pediu pausa de
carga (21:30Z a ~23:00Z) e há um aparelho com conta real sendo reiniciado. Nada foi executado: nem vitest, nem
typecheck, nem vite, nem navegador. A FASE B (portal rodando, 9 telas x 6 larguras, fluxos, axe, teclado) vem depois,
quando o orquestrador reativar esta tarefa.

## Escopo

- Os diffs de `beb8056..5da4cfc` em `frontend/`, `scripts/ui-auditoria.js`, `scripts/ui-verificar.mjs` e
  `docs/produto.md`: 110 arquivos, +7.735/−1.291. As tarefas ficam delimitadas pelos merges `--first-parent`: 02
  `351d5b2`, 01 `c2c26cb`, 03 `65a1a0e`, 05 `16a45ce`, 04 `e0b4636`, 06 `98d247e`, 08 `1e93294`, o script `e12c72e`
  e 07 `5da4cfc`.
- Os relatórios `01-rotas-menu.md` a `08-revisao-textos.md`, conferidos contra o código em vez de aceitos.
- Os critérios de aceite de cada briefing (`briefings/01` a `08`), um a um.
- A avaliação original (`00-avaliacao-original.txt`), para o que nenhum briefing cobriu.

## Método

1. Li os arquivos centrais inteiros (`lib/rotas.ts`, `store/ui.ts`, `store/metricas.ts`, `App.tsx`, `MenuLateral`,
   `TopBar`, `SaudeAmbiente`, `Drawer`, `BarraDeSelecao`, `DeviceGrid`, `DeviceList`, `features/pendencias/*`,
   `BarraListagem`, `ProfilesPage`, `lib/rotulos.ts`). Dos demais, li o diff por tarefa: `RunsPage`/`RunView`,
   `AppsPage`, `SettingsPage`, `AppsSection`, `Aprendizado*`, `Infra*`, `Diagnostics*`, `CommandPanel`,
   `FocusPanel`/`Focus.module.css` e os CSS de tokens e base.
2. Fiz buscas dirigidas por texto: rotas antigas (`#/perfis`, `personaRequest`, `settingsSection`), fontes de número
   fora de `store/metricas.ts`, formatadores de tempo, seletores `data-instance-*`, `aria-modal`, `aria-current`,
   `aria-label`, `role="radio"`, termos fora do glossário, tokens e classes sem uso, e `<button>` sem classe (por causa
   da regra global da 07).
3. Cruzei o que cada tarefa diz ter feito com o código. Nos pontos de costura entre tarefas (03×04, 02×05×06, 04×05)
   procurei contradições.
4. **Toda alegação de `npm test`, `typecheck` ou medida em navegador dos relatórios 01 a 08 fica `not_run` nesta
   fase.** Não reexecutei nenhuma. Os números citados (873 → 991 testes, 733 → 3 fontes, 394 → 0 alvos) são dos
   autores, não meus.
5. Não corrigi nada. Cada achado é para uma correção pequena aberta depois, em separado.

**Escala de gravidade.** **Alta**: perda de função, ou risco de ação com efeito sobre algo que a pessoa não está
vendo. **Média**: critério de aceite não atendido, contradição visível entre tarefas ou bloqueio de prova. **Baixa**:
texto, código morto, polimento, divergência documentada.

**Divergências registradas.**

- O briefing 09 pede o relatório em `docs/revisao-final.md`. Sigo o orquestrador: `docs/revisoes-ux/revisao-final.md`.
- As regras pedem o trailer `Co-Authored-By: Claude Sonnet 5.5`. Os relatórios 01 e 02 usaram `Claude Opus 5.5` e
  registraram isso. Aqui uso exatamente a linha pedida pelo orquestrador.
- O relatório 08 diz "Modelo: Haiku 4.5", mas o plano previa Haiku com esforço medium. É só registro, sem efeito.

## Achados estáticos

Na coluna "FASE B?", "sim" quer dizer que o achado precisa ser confirmado ou medido com o portal rodando. O "como
reproduzir" traz o caminho no código e, quando cabe, os passos no navegador (só leitura).

### Alta

| ID | Onde (arquivo:linha) | Achado e como reproduzir | Origem | FASE B? |
|---|---|---|---|---|
| RF-01 | `features/devices/DeviceGrid.tsx:179` (Selecionar todas usa `taskOrder` inteiro), `:189-200` (a barra recebe `selectedIds` cru), `store/ui.ts:170` (seleção persistida em `localStorage` `selectedInstances`), `features/painel/BarraDeSelecao.tsx:83,136` (`runBulkAction(ids, …)`) | **Ação em lote sobre aparelhos que a pessoa não está vendo.** O filtro `?estado=` (04) esconde cartões, mas a seleção não é filtrada. Além disso, a seleção sobrevive à recarga, e não há o aviso "(N fora do filtro atual)" que a 05 pôs em Personas. Passos: abrir `#/painel?estado=stopped` (7 visíveis de 14) e clicar "Selecionar todas". A barra diz "14 selecionados", e Parar, Reiniciar ou Resetar dados valeriam para os 14. **Não clicar em ação.** Variante: marcar aparelhos, recarregar com um link filtrado e ver a barra contar marcados invisíveis. | integração 01×03×04 | sim (contar, sem acionar) |
| RF-02 | `features/focus/Drawer.tsx:37-38,69-70` (a exceção do "clique fora" é só `[data-instance-card]`), `features/devices/DeviceList.tsx:60` (a linha da Lista usa `data-instance-row`), `features/focus/FocusPanel.tsx:55` (seletor de devolução do foco só para cartão) | **Na visão Lista, com o Foco aberto, as linhas contam como "fora" do drawer.** Clicar em "Abrir android-02" ou na caixa de seleção de outra linha fecha o drawer e o clique é engolido: não troca de aparelho nem marca. Nos Cards, o mesmo gesto troca de aparelho, como a 03 prometeu. Ao fechar, o `restoreSelector` não acha nada na Lista (só vale o elemento de origem). Passos: Painel → Lista → abrir android-01 → clicar o "Abrir" de outra linha. | integração 03×04 | sim |

### Média

| ID | Onde | Achado e como reproduzir | Origem | FASE B? |
|---|---|---|---|---|
| RF-03 | `features/profiles/ProfilesPage.tsx:293-368` (fila "Aguardando intervenção": login, desafio, conta errada, `needs_person`), `features/pendencias/modelo.ts:5-8,22,102-109` (só três origens) | **A caixa "única" de Pendências deixa de fora as sessões que só uma pessoa resolve.** O lead da página diz "tudo o que espera uma decisão sua" (`PendenciasPage.tsx:54`), mas quem tem desafio de login só aparece em Personas. **Critério 06 "todos os itens pendentes aparecem na caixa única": não atendido.** | 06 (×05) | sim, se houver sessão `needs_person` viva (não provocar) |
| RF-04 | `features/topbar/TopBar.tsx:174-186` ("aguardando você" = objetivos → `abrirExecucao(primeira)`), `features/topbar/SaudeAmbiente.tsx:99-101` ("objetivos aguardando você" → `#/execucoes`), `features/topbar/MenuLateral.tsx:101-103` (Pendências = linhas, legenda "esperando você"), `features/runs/filtroExecucoes.ts:31-35` ("Com pendência" = `completed_with_issues` + `needs_input`) | **"O que espera você" tem quatro definições, três números e três destinos.** O chip do topo conta objetivos e abre só a execução mais recente. O semáforo abre Execuções sem filtro. O menu conta linhas da caixa. O chip "Com pendência" da 05 conta outro conjunto (115 contra 4 no ambiente medido pela 05/06). Existem dois destinos já filtrados (`#/pendencias?origem=execucao` e `#/execucoes?status=pendencia`), e nenhum contador os usa. **Critério 02 "contadores clicáveis levam à lista filtrada correspondente": não atendido na integração.** O glossário fixa "Pendência" como termo único. | integração 02×05×06 | sim (comparar os números na mesma hora) |
| RF-05 | `features/topbar/TopBar.tsx:140,166-171` (`isRunActive`: planning, running, paused, cancelling) × `features/runs/filtroExecucoes.ts:31-38` ("Em andamento" inclui `planned`) | **O número de execuções ativas é calculado fora da fonte única**, com uma regra diferente da do chip "Em andamento" de Execuções. Com uma execução `planned`, o topo diz N e o chip diz N+1. Clicar no contador do topo abre Execuções sem `?status=andamento`. | 02×05 | sim |
| RF-06 | `lib/time.ts:81-113` (`formatAgo`/`formatAgoCoarse`: "há 1 min 14 s", "há 6 d"; cerca de 22 usos, entre eles `features/focus/Screen.tsx:63,419,424,436`, `PendenciasPage.tsx:109`, `RunsPage.tsx:232`, `InfraPage`, `AppsPage` e as guias da persona) × `lib/rotulos.ts:97-116` (`tempoRelativo`: "há 1 min", "há 6 dias"; só `DeviceCard.tsx:93` e `CommandTrail`) | **Dois formatadores de tempo.** O mesmo quadro aparece como "há 1 min" no cartão e "há 1 min 14 s" na legenda do Foco, e esse é o exemplo literal da avaliação original ("Prévia suspensa — último frame há 1 min 1…"). **Critério 04 item 2 (tempo relativo humano): parcial**, e a lógica está duplicada. | 04 | sim (ver as duas legendas lado a lado) |
| RF-07 | ver a lista abaixo | **A varredura de textos da 08 está incompleta**, e a frase do relatório "Varredura completa: nenhum outro texto visível não-conforme" é falsa. O que continua visível: `perfil(is)` em `features/profiles/PolicyGroups.tsx:73,93,125,212,258` (seção "Grupos de acesso" da própria tela Personas) e `GuiaConfiguracoes.tsx:150`; "Instâncias e contas" em `SettingsPage.tsx:20` (e `?aba=instancias`) e "instância(s)" em `AppsSection.tsx:91`; "as contas dos perfis" em `AppsPage.tsx:44`; "worker(s)" em `InfraPage.tsx:121,146-147`; "frame" em `Screen.tsx:403,419,424,436,438` ("Atualizar frame") e `DeviceCard.tsx:93,179,187`; "backend" e "snapshot" em `App.tsx:51`, `DeviceGrid.tsx:220-233`, `FocusPanel.tsx:160-161`, `DiagnosticsPage.tsx:209,222,286`, `CommandPanel.tsx:288-289,428`, `LoginPage.tsx:66,81`; "Screenshots" em `TopBar.tsx:43`; "WebSocket" e "host" em `TopBar.tsx:188,368`; "Logs" em `InfraPage.tsx:717`; "Status" em `FlowsRecipesSection.tsx:736`; "prompt" em `ProfilesPage.tsx:146` e `NovaPersona.tsx:224`; "Cards" em `DeviceGrid.tsx:166`; "sem AVD" em `store/metricas.ts:47` (rótulo **novo**, da 02); "Selecionar todas" e "Paradas" no feminino para aparelhos em `DeviceGrid.tsx:180,279`. Também ficou o `PlanTab.tsx:137` (chaves cruas), que a própria 08 listou. | 08 (e 02 no "sem AVD") | não (texto) |
| RF-08 | `features/devices/DeviceGrid.tsx:73-75,164-171` (visão Cards/Lista em `localStorage` `cda.painel.visao`) × `features/profiles/filtroPersonas.ts` (`visao=tabela` na URL) e `components/BarraListagem.tsx:84-94` ("Cartões / Tabela"); `features/topbar/MenuLateral.tsx:108` (o link do menu não leva a query da tela) | **O mesmo controle segue duas regras.** Rótulos: Painel "Cards / Lista", Personas "Cartões / Tabela". Persistência: Painel no navegador, Personas no link. Os filtros de Personas e Execuções moram só na URL: quem sai pelo menu e volta pelo menu perde busca, filtro e visão (recarregar preserva, trocar de tela não). O Painel também não usa a `BarraListagem` (pendência registrada pela 05). | 04×05 (×01) | sim (sair e voltar pelo menu) |
| RF-09 | `features/pendencias/modelo.ts:86` (`run.command.split('\n')` cru) × `features/runs/filtroExecucoes.ts` (`tituloCurto`) | **O título da execução em Pendências é a primeira linha crua do comando**, com o prefixo repetido que a 05 removeu em Execuções ("No QA Messenger, leia o nome do…"). É lógica duplicada, e a mesma execução aparece com dois títulos. | 05×06 | sim |
| RF-10 | `scripts/ui-verificar.mjs:46` (`--url` padrão `http://127.0.0.1:8000`), `:104-109` (aborta todo POST, então não há login), `:117` (espera `<header>`) | **O verificador de acessibilidade não alcança as telas autenticadas.** Um Chrome sem cabeça cai na tela de entrada (a 07 relatou isso), onde não há `<header>`. Resultado: erro ou axe da tela de entrada nas 54 células, sem prova das 9 telas. O padrão 8000 serve o `dist` da `main`, **não este branch**. Sem passar `--url` do dev server e sem uma sessão, o script não prova nada. | script `e12c72e` | sim (bloqueio: ver a FASE B) |

### Baixa

| ID | Onde | Achado | Origem | FASE B? |
|---|---|---|---|---|
| RF-11 | `features/focus/Drawer.tsx:127-133` | O drawer declara `aria-modal="true"`, mas menu, topo e cartões seguem clicáveis pelo ponteiro. É contradição semântica documentada pela 03 como escolha. Com leitor de tela (VoiceOver trata o fora como inerte), o comportamento fica diferente do ponteiro. Pede decisão do dono. | 03 | sim (leitor de tela, se houver) |
| RF-12 | `features/devices/DeviceGrid.tsx:174-178,189` | A barra de seleção entra no fluxo ao marcar o primeiro aparelho: a grade desce cerca de 52 px sob o cursor e o cabeçalho perde "0 de 14 selecionados". É deslocamento de layout na seleção (o critério da 03 só falava de abrir o drawer). | 03 | sim (medir) |
| RF-13 | `features/painel/BarraDeSelecao.tsx:66` × `:70` | O nome da barra ("Ação em N aparelhos") usa `ids.length` cru, e o texto visível usa `selecionados` (`contarSelecao`). Divergem num id que sumiu entre dois snapshots (`live.ts:85` poda a cada snapshot). | 03 | não |
| RF-14 | `features/devices/deviceState.ts:342-360` (`countByState`, `STATE_SUMMARY_LABEL`, usados só em teste); `styles/base.css:258-292` (`.t-titulo-pagina` … `.t-legenda`, sem uso); `styles/tokens.css:120` (`--hit-touch`, sem uso); `features/focus/Focus.module.css:31,739-743` (comentários ainda citam `--focus-panel-w`, e o de 739 descreve a regra antiga de "painel com no mínimo 600 px"); `store/ui.ts:25` (`viewFromHash`) e `lib/rotulos.ts:73` (`rotuloConhecidoDoComando`) exportados sem uso fora de teste; `features/settings/AppsSection.tsx:151` (título "Novo aplicativo", ramo que nenhum botão alcança mais) | Código morto. `countByState` é uma **segunda função de contagem** ainda exportada, contra a fonte única da 02. | 02, 03, 06, 07 | não |
| RF-15 | `features/topbar/TopBar.tsx:66` (marca → `hashForView('painel')`), `features/topbar/SaudeAmbiente.tsx:66,96,100,109-110` | Links sem `foco`: clicar na marca ou num link do semáforo fecha o Foco, enquanto o menu e a caixa de Pendências levam o `?foco=` junto. É inconsistência de navegação. | 01×02 | não |
| RF-16 | `features/settings/AppsSection.tsx:94-100` | Configuração > Aplicativos ainda edita dicas e seletores e **exclui** app. O cadastro está num lugar só (um botão "Novo aplicativo", conferido por busca), mas a gestão não. Divergência documentada pela 06. | 06 | não |
| RF-17 | `features/command/CommandPanel.tsx` (grupo `etapas`, cerca de 660-700) | As etapas numeradas 1 → 2 → 3 sugerem sequência obrigatória, mas Executar não exige Planejar. A ambiguidade "etapas ou alternativas" da avaliação só mudou de lado. | 03 | não |
| RF-18 | `TopBar.tsx:183`, `CommandPanel.tsx:574`, `features/focus/FocusActions.tsx:168`, `ProfilesPage.tsx:354` | O ícone `Hand` tem três sentidos: "aguardando você", "Manual" (quem escolhe os aparelhos) e "Controle manual"/"Assumir controle". "Manual" do Comando e "controle manual" do Foco são coisas diferentes com a mesma palavra. | 02, 03 | não |
| RF-19 | `features/pendencias/PendenciasPage.tsx:62-73` | Os chips de origem usam `role="radio"` sem navegação por setas nem tabindex móvel. A `BarraListagem` usa `aria-pressed` para o mesmo controle: dois padrões. | 06×05 | sim (teclado) |
| RF-20 | `features/pendencias/PendenciasPage.tsx:108` | O tooltip da idade mostra o ISO cru (`2026-09-30T…Z`), fora do `dd/mm/aaaa` da 08. | 06 | não |
| RF-21 | `lib/aiBalance.ts:88` (`estimated_balance / (units_per_usd \|\| 1)`) | Sem câmbio informado, a conta em R$ aparece como "US$" com o mesmo número. A regra é anterior, mas a 07 passou a expô-la no topo. Configuração › IA continua na moeda original (documentado pela 07). | 07 | sim (conta em R$) |
| RF-22 | `features/pendencias/store.ts:38-40` × `modelo.ts:49-61` | O selo "Aprendizado" do menu usa `fila.total` do servidor, e o chip "Aprendizado (n)" da caixa usa `itens.length`. Divergem se a API paginar. O mesmo item também conta em dois selos do menu (Aprendizado e Pendências). | 06 | sim |
| RF-23 | `store/metricas.ts:241-244` | `reconnecting` já vira **Ambiente crítico**: uma reconexão normal pisca o semáforo em vermelho. | 02 | sim |
| RF-24 | `features/devices/OperationalContextCard.tsx:125-127` | Depois da troca da 08, a linha diz "Persona: Nome (@x) · persona Y" (redundante). | 08 | não |
| RF-25 | `features/runs/PlanTab.tsx:123` | A 08 mudou uma expressão (`appLabel(apps, plan.app_id) ?? …`), não só texto. É inofensivo, mas fere o critério "diff contém só strings". | 08 | não |
| RF-26 | `features/topbar/MenuLateral.tsx:78-142` | A gaveta (abaixo de 1024 px) não prende o Tab: ele sai para o conteúdo escurecido atrás. | 01 | sim (teclado) |
| RF-27 | `components/Toasts.tsx:83` (região fora de marco), `features/profiles/PolicyGroups.tsx:91` (`h3` fora de ordem), `figure` com papel no Diagnóstico; `DeviceGrid.tsx:119,143` (`aria-label` em `<span>` sem papel) | As violações do axe que a 07 mediu e passou para a 08 ou a 09 (`region`, `heading-order`, `aria-allowed-role`) **não foram tratadas**. O `aria-label` em `span` pode aparecer como `aria-prohibited-attr`. | 07 → 08/09 | sim (axe) |
| RF-28 | `features/login/LoginPage.tsx:80` | Texto de 13 px com `opacity: 0.7` na tela de entrada. A 07 não auditou essa tela, que fica fora da sessão. | 07 | sim (axe na tela de entrada) |
| RF-29 | `lib/rotas.ts:8` | O comentário diz `#/aplicativos/<pacote>`, mas o segmento é o `app_id` (a 01 registrou isso). É documentação. | 01 | não |

**Conferido e sem achado (estático).**

- `#/perfis[...]` → `#/personas[...]` por `replaceState` (`store/ui.ts:349`, `lib/rotas.ts:47`), inclusive o valor
  `'perfis'` salvo no navegador (`ui.ts:120-125`).
- `personaRequest`, `consumePersonaRequest`, `settingsSection`, `aprendizado.aba`, `HealthPill` e `BulkBar` não restam
  no código.
- `#/configuracao` sem `aba` abre Aplicativos (`SettingsPage.tsx:32-34`). `#/aplicativos?aba=proxy` abre a Rede com o
  trecho antigo aberto (`AppsPage.tsx:67-69`).
- `aria-current="page"` só no item do menu da tela atual (`MenuLateral.tsx:110`).
- Regra global de botão da 07: todo `<button>` tem classe de componente (conferido um a um: `Button`, `Popover`,
  `Switch`, `Tabs`, `CommandPanel` e os chips da Loja e do Diagnóstico). A exceção é o de envio oculto de
  `CriarAparelho.tsx:160`. O visual continua para a FASE B.
- `noUnusedLocals`/`noUnusedParameters` estão ligados no `tsconfig`: import morto seria barrado pelo typecheck, que
  nesta fase não rodei.
- `situacao=bloqueada` (05) usa `status === 'blocked'` (`filtroPersonas.ts:103,133`), o mesmo critério de
  `personasBloqueadas` (02).
- As vagas por servidor (`ocupacaoDoServidor`) têm a mesma regra do backend: o `max_slots` do worker local é
  sobrescrito por `max_online_devices` (`backend/app/state.py:741`). A Infraestrutura e o semáforo repartem os
  aparelhos do central igual (`InfraPage.tsx:97` e `metricas.ts:154`).
- Nenhum `font-size` em px abaixo de 13 nos CSS. O único token de 12 px (`--fs-2xs`) é usado só no gráfico do
  Diagnóstico.

## Critérios de aceite por tarefa

Legenda: **atendido** (no código, conferido estaticamente), **parcial**, **não atendido**, **não verificável sem rodar**
(fica para a FASE B). Nada aqui é prova de execução: `not_run` para tudo o que dependa de rodar.

| Tarefa | Critério | Situação | Base |
|---|---|---|---|
| 01 | As 8 telas (hoje 9, com Pendências) alcançáveis pelo menu de 390 a 1920 px | atendido no código; visual não verificável sem rodar | lista vertical e gaveta sem medir largura (`MenuLateral.tsx`, CSS 159-247) |
| 01 | Abrir persona, aparelho ou execução muda a URL; colar abre o mesmo objeto | atendido no código | `ProfilesPage.tsx:100-102`, `ui.ts:282-291`, `ui.ts:272-280` |
| 01 | Voltar fecha o detalhe e volta à lista | atendido no código; não verificável sem rodar | `voltarPara`/`podeVoltarPara` (`ui.ts:52-58,189-205`) |
| 01 | `#/perfis` redireciona para `#/personas` | atendido | `ui.ts:349` |
| 01 | Nenhuma regressão nas rotas existentes | parcial | RF-08 (filtros somem ao trocar de tela pelo menu), RF-15 (links que fecham o Foco) |
| 02 | Uma métrica tem o mesmo valor em todas as telas (LAN fora do ar) | parcial | RF-04, RF-05; aparelhos, vagas e bloqueadas vêm da fonte única |
| 02 | "Ambiente" em Atenção com motivo quando o notebook está fora do ar | atendido no código; prova real `not_run` (a 02 só provou `simulated`) | `metricas.ts:263-266` |
| 02 | Contadores clicáveis levam à lista filtrada | parcial / não atendido na integração | bloqueadas e desconhecidos sim; "aguardando você" e "execuções" não (RF-04, RF-05); "online" não é clicável |
| 02 | Testes dos cálculos (servidor fora do ar, acima da capacidade, seleção parcial) | existem (`metricas.test.ts`, 20); execução `not_run` | — |
| 03 | A barra não cobre card em 390, 768, 1024 e 1440 px | atendido no código (sticky na fila, não flutuante); ao rolar, os cartões passam por baixo; não verificável sem rodar | `BarraDeSelecao.module.css:3-7` |
| 03 | Nenhuma ação aparece duas vezes (barra + drawer) | atendido | `BarraDeSelecao.tsx:72-110` (`emFoco`) |
| 03 | Abrir o drawer não altera o layout da grade | atendido no código; não verificável sem rodar | `Focus.module.css` (`.panel` absoluto), `App.module.css:22-24` |
| 03 | Legendas do preview sem truncamento | atendido no código (`nowrap` saiu); não verificável sem rodar | `Focus.module.css` 286-352 |
| 03 | Executar desabilitado mostra o motivo no mouse ou no foco | atendido no código | `disabledReason` no próprio botão |
| 03 | (implícito) drawer funcional em todas as visões | **não atendido na visão Lista** | RF-02 |
| 04 | Nenhum identificador cru por padrão em cards, execuções e logs | parcial | chaves do plano (`PlanTab.tsx:137`), "frame", "sem AVD" (RF-07) |
| 04 | Aparelho parado com no máximo metade da altura | não verificável sem rodar (a 04 mediu 79 a 125 px contra 426 a 478) | — |
| 04 | A Lista mostra os 15 sem rolar mais de uma tela a 1440x900 | não verificável sem rodar | — |
| 04 | Texto novo em pt-BR | parcial | "Cards" (RF-07, RF-08) |
| 05 | Achar persona pelo @ em menos de 3 s | atendido no código (busca por nome e @ sem acento); tempo não verificável sem rodar | `filtroPersonas.ts` |
| 05 | Filtros combinados funcionam e persistem ao recarregar | atendido no código (URL); ressalva: somem ao sair e voltar pelo menu (RF-08) | — |
| 05 | Cartões de persona com altura uniforme, sem corte sem tooltip | não verificável sem rodar | — |
| 05 | Alternar cartões/tabela não perde seleção | atendido no código (a seleção é estado da página) | `ProfilesPage.tsx:70` |
| 05 | Testes da lógica de filtro e ordem | existem (`filtroPersonas.test.ts`, `filtroExecucoes.test.ts`); execução `not_run` | — |
| 06 | Existe só um botão "Novo aplicativo" | atendido | busca: só `LojaPage.tsx:75`; `AppsSection.tsx:151` é título de diálogo inalcançável (RF-14) |
| 06 | Todos os pendentes na caixa única; o contador do menu bate com a lista | contador = lista: atendido (mesma função); "todos": **não atendido** | RF-03 |
| 06 | Nenhum termo de loja sem explicação acessível | parcial / não verificável sem rodar | a legenda cobre; `ReleasesPage` sem `Termo` por selo (a 06 registrou) |
| 06 | Links antigos (`#/configuracao`, aba Aplicativos) continuam | atendido | `SettingsPage.tsx:32-34` |
| 07 | Nenhum texto de interface abaixo de 13 px (exceto gráfico) | atendido nos tokens e CSS; RF-28 na tela de entrada; visual não verificável sem rodar | busca por `font-size` em px |
| 07 | axe sem falha de contraste AA nas 8 telas | não verificável sem rodar (a 07 mediu só a 1440 px, sem Pendências) | RF-27, RF-28 |
| 07 | Alvos abaixo de 32 px caem a zero na tela principal | não verificável sem rodar | `--hit-min` aplicado |
| 07 | Navegação completa por teclado em Painel e Personas | não verificável sem rodar | — |
| 08 | Tabela de achados entregue | parcial: 6 linhas, em `docs/revisoes-ux/08-revisao-textos.md` (não em `docs/revisao-textos.md`), varredura incompleta | RF-07 |
| 08 | Nenhuma alteração de lógica; diff só com strings | quase: uma expressão em `PlanTab.tsx:123` | RF-25 |

## Pendências da avaliação original que nenhum briefing cobriu

Não são regressões: ficaram fora do escopo das tarefas 01 a 08.

- Detalhe da persona com 11 guias numa faixa (foto, nome e @ repetidos três vezes). A 01 só pôs a guia na URL.
- Detalhe da execução com 7 guias, sem resumo acima e sem "Relatório" como padrão. A diferença entre "1 de 1 com
  sucesso comprovado" e "1 Sucesso" continua sem explicação.
- Infraestrutura sem a hierarquia servidor > aparelho > persona, e com as guias abaixo da dobra.
- Busca global, paleta de comandos (Ctrl+K) e notificações persistentes.
- Exibição consciente de campos sensíveis (religião, política) na persona.
- Item 8 do backlog: decisão documentada sobre o escopo mobile (monitoramento ou uso completo).
- Estados vazios com a próxima ação sugerida: foram tratados só nas listas da 05 e em Pendências.
- A barra de listagem na grade de Aparelhos (a 05 registrou) e o editor de dicas e seletores dentro da página do app
  (a 06 registrou).
- API: busca de execuções no servidor (`GET /runs?q=&status=&since=`), `last_activity_at` vazio nas personas e um
  endpoint de contagem estável de "objetivos esperando pessoa" (registrados pela 02 e pela 05).

## O que a FASE B precisa provar

### Bloqueios que o orquestrador precisa decidir antes

1. **Sessão para as telas autenticadas** (RF-10). O `ui-verificar.mjs` não faz login e aborta todo POST. Opções:
   - (a) usar o painel do navegador da IDE, que já tem sessão, e injetar o axe à mão, como a 07 fez;
   - (b) subir um backend simulado do próprio worktree em `127.0.0.1:8765` (SQLite de rascunho,
     `AI_PROVIDER=simulated`, `base_console_port: 5640`, sem SDK) e rodar `npx vite --mode simulado --port 5199`.
     Há precedente em `docs/auditoria-ux-2026-09-27/evo2-aceite.md`. Ali o login e os fluxos de escrita são
     inofensivos, e um worker inscrito e nunca conectado simula "servidor fora do ar". Ressalvas: a 8765 pode estar
     ocupada por outra sessão (`docs/conhecimento/aprendizados.md:1014`), e é mais um processo no host.
   - (c) dar ao script um `storageState` com cookie de sessão. Isso é mudança de código que não cabe à 09.

   Em qualquer caso, `--url` precisa ser a do dev server deste branch, não a 8000.
2. **O canal ao vivo pelo vite.** Nas tarefas 01, 02, 05 e 06, o WebSocket pela porta do vite caiu com 1006. Com isso,
   o semáforo mostra **Crítico** por definição (`metricas.ts:241`) e a faixa "Desconectado do backend" ocupa o topo do
   conteúdo. A FASE B vai separar: lógica do semáforo por `vitest` (`SaudeAmbiente.test.tsx`, `metricas.test.ts`,
   `simulated`), e visual e números no navegador, registrando que o nível mostrado é o do canal caído. Com o backend
   simulado da opção (b), o canal sobe e o semáforo pode ser provado de verdade.
3. **Carga.** Uma aba, um vite, nada em paralelo. O axe em 54 células roda uma célula por vez. O `npm test` inteiro
   roda uma vez, no fim, em prioridade baixa.

### Provas pedidas

1. **RF-01**: em `#/painel?estado=stopped`, "Selecionar todas" e ler o que a barra conta. Também recarregar com a
   seleção salva e um filtro. **Sem acionar nenhuma ação.**
2. **RF-02**: visão Lista com o Foco aberto; clicar "Abrir" e a caixa de outra linha. Comparar com os Cards.
3. **Tela x largura**: 9 telas (Painel, Personas, Aplicativos, Execuções, Pendências, Aprendizado, Infraestrutura,
   Configuração, Diagnóstico) x 1920, 1440, 1280, 1024, 768 e 390 px. Em cada uma: menu alcançável, `scrollWidth` =
   largura, sem sobreposição, barra sem cobrir conteúdo, drawer funcional, texto sem corte indevido. Captura só do que
   tiver problema.
4. **Fluxos**:
   - persona por URL direta (`#/personas/<id>/memoria`) e Voltar;
   - `#/perfis/...` redirecionado;
   - filtro de Personas e de Execuções depois de recarregar **e depois de sair e voltar pelo menu** (RF-08);
   - seleção e barra, incluindo o salto da grade ao marcar o primeiro aparelho (RF-12);
   - drawer do Foco (Esc, clique fora, Tab preso, foco devolvido) em Cards **e** Lista;
   - caixa de Pendências: contador do menu = linhas; comparar com "aguardando você", com o semáforo e com "Com
     pendência" de Execuções no mesmo instante (RF-04, RF-09, RF-22);
   - contador "execuções" do topo × chip "Em andamento" (RF-05);
   - semáforo com servidor fora do ar: real só pela opção (b); senão `simulated` pelos testes.
5. **Tabela de métricas da 02**: online, total, paradas, desconhecidos, vagas por servidor, bloqueadas e aguardando
   você no topo, no Painel, na Infraestrutura, no Diagnóstico e no semáforo, lidos na mesma hora.
6. **Tempo relativo**: o mesmo aparelho no cartão e na legenda do Foco (RF-06).
7. **axe** nas 54 células (mais a tela de entrada, RF-28), com atenção a `region`, `heading-order`,
   `aria-allowed-role`, `aria-prohibited-attr` (RF-27) e contraste. Lighthouse de acessibilidade em pelo menos
   Painel e Personas a 1440 e 390 px (a 07 não rodou nenhum).
8. **Teclado**: Painel e Personas completos; gaveta do menu (RF-26); chips de Pendências (RF-19); anel de foco no
   `<main>` depois de navegar pela gaveta.
9. **Visual da regra global `button { border: 0; background: transparent }`** nas telas novas da 06 (Pendências,
   Aprendizado com "Saiba mais", Rede com o Proxy antigo) e nas guias da persona.
10. **Saldos no topo a 390 px** e conta em R$ convertida (RF-21).
11. **Suíte**: `npm run typecheck` e `npm test` uma vez, para registrar o número (`simulated`), já que nesta fase
    nenhuma alegação de teste foi reexecutada.

## Veredito parcial (FASE A)

**Pronto com ressalvas, condicionado à FASE B.** A base de rotas (01), a fonte única de números (02), o drawer
sobreposto (03), a tradução dos cartões (04), a busca e os filtros (05), a caixa de Pendências (06) e os tokens de
tipografia e alvo (07) estão no código e são coerentes no geral. Duas costuras entre tarefas, porém, não podem ir
para a `main` sem correção pequena antes:

- RF-01: ação em lote sobre aparelhos escondidos pelo filtro;
- RF-02: drawer quebrado na visão Lista.

Há também quatro critérios de aceite não atendidos ou parciais que o dono deveria ver:

- RF-03: caixa "única" sem as sessões que pedem pessoa;
- RF-04 e RF-05: contadores de "o que espera você" e de execuções com definições diferentes;
- RF-07: varredura de textos incompleta.

O veredito muda para "não pronto" se a FASE B mostrar rolagem horizontal, sobreposição ou menu inalcançável em
alguma das 54 células, ou contraste AA falhando. Muda para "pronto com ressalvas" sem condição se RF-01 e RF-02
forem corrigidos e o resto se confirmar.

## Provas

- **real**: nenhuma nesta fase.
- **simulated**: nenhuma reexecutada nesta fase.
- **not_run**: tudo o que depende de rodar. Isso inclui as suítes e os typechecks alegados pelos relatórios 01 a 08,
  todas as medidas em navegador, o axe e o Lighthouse.
- **estático** (o que esta fase fez): leitura de código e busca dirigida no commit `5da4cfc`, com as referências
  `arquivo:linha` acima.

## Arquivos

- Criado: `docs/revisoes-ux/revisao-final.md` (este). Nenhum arquivo de código foi tocado. `CHANGELOG.md` e
  `docs/estado-atual.md` não foram alterados: o orquestrador consolida.
