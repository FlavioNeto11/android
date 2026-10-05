# Rodada 2, tarefa 15: revalidação final depois das correções 10 a 14

Branch `ux2/15-revalidacao-final`, base `claude/ux-portal-2` em `7d4bcc5` (as tarefas 10 a 14 integradas, na ordem 14,
10, 12, 11, 13). Data: 01/10/2026, máquina central de desenvolvimento (Windows Server 2025). Revisor independente
(Opus, esforço high), sem participação nas tarefas 10 a 14. Este relatório **não corrige nada**: cada achado traz os
passos para reproduzir e fica para uma tarefa pequena separada.

**Divergência de caminho**: o briefing pede `docs/revalidacao-final.md`; o orquestrador indicou
`docs/revisoes-ux/rodada-2/revalidacao-final.md`, que é o que vale (o mapa de posse também diz isso).

## Veredito

**Pronto com ressalvas.**

- Nenhum achado de gravidade **Alta**. Nenhuma regressão de gravidade Média introduzida pelas tarefas 10 a 14.
- Os critérios específicos da rodada passam no navegador contra o backend simulado: cabeçalho de **56 px** a 390 px,
  persona com identidade única e **5 seções**, slug legível com ids antigos compatíveis, foco devolvido ao cartão e à
  linha da Lista, menu expandido por padrão a partir de 1280 px, resumo no topo da execução e **o mesmo número de
  pendências** no topo, no botão Menu, no menu lateral, na caixa e no Aprendizado › Para aprovar.
- As 54 células (9 telas x 6 larguras) têm rolagem horizontal 0, nenhum elemento vazando, menu alcançável, 0 alvo
  abaixo de 32 px pelo auditor, 0 falha de contraste AA e 0 elipse sem dica.

**Ressalvas antes da `main`:**

1. **Duas Médias pré-existentes a 390 px** (não são regressões das 10 a 14, mas aparecem justamente onde a rodada
   prometeu o celular): a caixa "Responda aqui" de uma execução que pede informação fica espremida em 127 px com os
   botões cortados (M1), e a barra de seleção com um aparelho de servidor fora do ar deixa as 6 ações fora da vista
   (M2, o RF-37 agravado).
2. **Sete Baixas atribuíveis às tarefas 10 a 14**: quatro visíveis na tela (B1 a B4) e três de código ou ferramenta
   (B5 a B7); B8 a B11 são pré-existentes. As visíveis: nome acessível do menu lateral que não começa pelo texto
   visível (B1, tarefa 13; o Lighthouse acusa em todas as telas a 1440 px), selos de 12 px novos sem a documentação
   que o token exige (B2, tarefas 10 e 12), a seção "Atividade" cujo selo conta Aprovações mas abre Interações (B3,
   tarefa 12) e "Plano pronto. Nada foi executado ainda · 0 de 1 objetivo com sucesso" (B4, tarefa 14).

## Instrumento e provas

### real (01/10/2026, central de desenvolvimento, commit `7d4bcc5`)

- **Backend simulado do worktree** na **8715**: `C:\git\android\backend\.venv\Scripts\python.exe -m app.main` com o
  diretório atual em `<worktree>\backend`, `AI_PROVIDER=simulated`, `POC_CONFIG` e `POC_DB_PATH` no rascunho da sessão
  (`sim15/`), prioridade baixa. Config gerado de `config/config.example.yaml`: `server.port: 8715`, `worker_port: 0`,
  `allowed_origins` com 8715 e `localhost:5115`, `paths.*` no rascunho, `android.sdk_root` inexistente,
  `instances.count: 12`, `base_console_port: 5640` (e portas de sistema, mjpeg e chromedriver deslocadas),
  `appium.autostart: false` na 4799 com pasta inexistente, `rede.servidor.binario` inexistente. **`GET /api/health` →
  `commit 7d4bcc5c…`, migração `063`**: era o código deste branch. `.env` e `config.yaml` do central não foram lidos.
- **Vite** na **5115** com `VITE_API_TARGET=http://127.0.0.1:8715`, aberto só por `http://localhost:5115` (nunca
  `127.0.0.1`, RF-47). Abas próprias do painel da IDE (`tab-7`, `tab-8`), sempre com `tabId`, fechadas ao fim com a
  emulação de tamanho desfeita. A aba `seed` (de outra tarefa) não foi tocada.
- **Servidor estático** na 8745 (rascunho) para injetar `axe.min.js` (axe-core **4.13**, de `C:\temp\ui-verificar`),
  `scripts/ui-auditoria.js`, `scripts/ui-truncamento.js` e um medidor próprio do rascunho (rolagem, vazamento fora de
  rolador, menu, altura do `<header>`, alvos estritos `< 32` e axe com `wcag2a/aa`, `wcag21a/aa`, `wcag22aa`,
  `best-practice`).
- **Lighthouse 13.5.0** (só acessibilidade), Chrome de `C:\Program Files\Google\Chrome\Application\chrome.exe` sem
  cabeça, um por vez, cookie da sessão simulada por `--extra-headers` num arquivo apagado no mesmo passo (valor nunca
  impresso). Os relatórios contêm o topo autenticado e não a tela de entrada.
- **Teclas reais** pelo painel (`Enter`, `Tab`, `Esc`) nos fluxos de drawer, gaveta e popover do semáforo.
- Ao fim: backend, vite e servidor estático parados (8715, 5115 e 8745 livres); a **8000 nunca foi tocada** (dono
  PID 22060 no fim). Nenhuma ação com efeito foi disparada: Executar, Planejar, Iniciar, Parar, Resetar, Instalar,
  Assumir controle, aprovar e recusar ficaram intocados. Só houve navegação, filtros, guias e seções, abrir e fechar o
  drawer, abrir popovers e marcar caixas de seleção (marcação local).

**Dados semeados, pelas rotas do simulado**: login "Revisao 15"; 2 saldos de IA (`POST /api/ai/balances/{conta}`);
6 personas (`POST /api/personas`), duas com o **mesmo nome** "Lucas Almeida", uma com acento ("Ana Beatriz Nandu Ávila")
e uma de 45 caracteres; 4 contas no QA Messenger e 5 vínculos com aparelho; **um servidor remoto que conectou e caiu**
(`POST /api/workers/enroll`, `hello` pelo `/api/worker/ws` com 2 aparelhos, `POST …/devices/adopt` dos dois, canal
fechado: `android-13` e `android-14` ficam "desconhecido"); 5 execuções (`POST /api/runs`, modo plano): 2 `needs_input`
("Envie uma mensagem…" sem destinatário), 2 `planned` e uma com pedido de 219 caracteres.

**Fora das rotas** (dito de propósito, por `UPDATE`/`INSERT` no SQLite do rascunho, como nas tarefas 11, 13 e 14): a
execução longa virou `completed` com 2 min 03 s e objetivo `succeeded`; 1 aprovação `pending` (persona Ana); 1 item
de aprendizado `candidate` com texto de pessoa (fila "Para aprovar"); 4 medições `boot` para o gráfico do Diagnóstico;
API, ABI, Play e imagem nos 12 aparelhos locais.

### simulated

- `npm run typecheck`: limpo. `npm test` inteiro, uma vez, no fim, com tudo parado: **93 arquivos, 1120 testes, todos
  verdes** (17 s).

### not_run

- Semáforo no **nível "Atenção"** com só o servidor fora: sem SDK o ambiente é sempre "crítico" (RF-41). Provados por
  real só o motivo "Servidor Notebook da LAN fora do ar" com "Abrir Infraestrutura" e "2 aparelhos em estado
  desconhecido" com "Ver no Painel", ambos marcados ATENÇÃO.
- Grupo **Custos** no topo e no "Resumo": com IA simulada nenhuma conta paga função, e os 2 saldos semeados não
  aparecem (a tarefa 10 semeou saldos baixos). Não é defeito; a composição do Resumo com Custos fica sem prova real.
- Execução `running`, origem "Intervenção", sessão "Conectado · confirmada há…" no cabeçalho da
  persona e selo "Comprovado" (`proven: true`): o simulado não alcança.
- Lighthouse nas larguras 1920, 1280, 1024 e 768 (rodado a 1440 nas 9 telas e a 390 em Painel e Personas).
- Popover "Recursos" do tablet (768 px) nunca aberto; a gaveta do menu exercitada com teclado só a 768 px (a 390 só
  a presença do botão "Menu").
- Leitor de tela, toque real (só emulação de largura), tema claro (o produto é só escuro).
- RF-12, RF-38 e RF-43 não foram remedidos; `scripts/ui-verificar.mjs` (não faz login, RF-10); `python
  scripts/docs-check.py`.

## Tabela tela x largura (9 x 6)

Medido por script em cada célula, depois de recarregar a página na largura (menu sem preferência gravada). **OK** =
rolagem horizontal 0, nenhum elemento vazando, menu alcançável (sidebar de 9 itens ou botão "Menu" da gaveta), 0 alvo
abaixo de 32 px pelo auditor (tolerância de 0,5 px) e também pela medida estrita, 0 contraste abaixo de AA, 0 elipse
sem dica. Em todas as células há a violação `region` (moderate) da região de avisos, a mesma desde a rodada 1 (RF-27).

| Tela | 1920 | 1440 | 1280 | 1024 | 768 | 390 |
|---|---|---|---|---|---|---|
| Painel | OK | OK | OK | OK | Baixa: selo de 12 px (B2) | **Média**: M1 com execução `needs_input` aberta, M2 com aparelho desconhecido marcado; B2 |
| Personas | OK · axe `heading-order` (RF-27) | idem | idem | idem | idem + B2 | idem + B2 |
| Aplicativos | OK | OK | OK | OK | B2 | B2 |
| Execuções | OK | OK | OK | OK | B2 | **Média**: M1 na execução `needs_input`; B2 |
| Pendências | OK | OK | OK | OK | B2 | B2 |
| Aprendizado | OK | OK | OK | OK | B2 | B2 |
| Infraestrutura | OK | OK | OK | OK | B2 | B2 |
| Configuração | OK | OK | OK | OK | B2 | B2 |
| Diagnóstico | OK | OK | OK | OK | B2 | B2 |

Notas da tabela:

- **Menu**: 212 px expandido a 1920, 1440 e 1280; 60 px recolhido a 1024; gaveta pelo botão "Menu" a 768 e 390.
- **Cabeçalho**: 98 px de 1024 a 1920, **106 px a 768** e **56 px a 390** (critério <= 56 px atendido), com 4
  pendências e o semáforo "Ambiente crítico 7" inteiro, sem reticências.
- **B2** só aparece com pendências > 0 (o selo do botão "Menu", 12 px, existe abaixo de 1024 px).
- **Lighthouse** (acessibilidade): Painel **100** a 1440 e 390; Personas **99** a 1440 e 390 (`heading-order`);
  Aplicativos, Execuções, Pendências, Aprendizado, Infraestrutura, Configuração e Diagnóstico **100** a 1440. Em
  todas a 1440 falha a auditoria `label-content-name-mismatch` (peso zero, por isso a nota fica 100): os itens
  "Pendências" e "Aprendizado" do menu lateral (B1) e, na Infraestrutura, os botões "Abrir android-NN na visão de
  foco" (já registrado pela tarefa 13).
- **Visões internas**: as 11 guias da persona a 1440 e 6 delas a 390 (Ana e um dos homônimos), os detalhes de
  execução concluída, `needs_input` e planejada a 390, o Foco aberto e o detalhe do app QA Messenger a 1440, e o
  painel "Resumo" aberto a 390: rolagem 0, vazamento 0, contraste 0, elipse 0, salvo o que está nos achados. axe nas guias da persona:
  `aria-allowed-role` e `heading-order` em Persona, `heading-order` em Contas e acesso, `aria-allowed-role` em
  Aparelhos (RF-27); no detalhe do app, `heading-order` e `page-has-heading-one` (RF-27); no Foco,
  `aria-allowed-role` no `aside role=dialog` (RF-27). Painel do "Resumo" aberto a 390: 0 violações além de `region`.
- **Diagnóstico**: o gráfico usa a largura do contêiner (324 px a 390) e os rótulos saem com 13 px reais; a largura
  ficou estável depois de 1,5 s (sem laço de redimensionamento).

## Critérios específicos e fluxos (real)

| Critério ou fluxo | Resultado |
|---|---|
| Cabeçalho <= 56 px a 390 px | **Atendido**: 56 px em todas as 9 telas. "Resumo" abre um painel de 351 x 513 px com Capacidade, IA e conexão e Sessão; nome "Resumo, 4 aguardando você. Abrir capacidade…" (começa pelo texto visível). "MODO SIMULADO" deixou de ser cortado (RF-46 fechado). |
| Persona: identidade única e 5 seções | **Atendido**: 1 `h1`, o nome 1 vez e o @ 1 vez no `main` da Visão geral; 5 botões de seção (Visão geral, Perfil, Contas e aparelhos, Atividade, Avançado) com `aria-current`, e a lista suspensa com as mesmas 5 a 390 px. Ressalva B3. |
| Slug legível e ids antigos | **Atendido**: `#/personas/ig-10x…/memoria` → `#/personas/ana-beatriz-nandu-avila/memoria` (Perfil › Memória); `#/perfis/ig-GdL…/contas?foco=android-04` → `#/personas/helena-prado/contas?foco=android-04`; homônimos → `lucas-almeida-iwug` e `lucas-almeida-pwfl/aprovacoes`; `#/personas/lucas-almeida` → aviso "Mais de uma persona com esse nome"; `nao-existe` → "Persona não encontrada". Numa aba nova, abrir por id antigo somou **1** entrada ao histórico (a da navegação; a troca pelo slug é substituição) e Voltar levou ao Painel. |
| Lista com filtro → persona → guia → Voltar | **Atendido**: `#/personas?q=lucas&visao=tabela` (2 de 6) → "Abrir Lucas Almeida" → seção Contas (substitui) → Voltar = `#/personas?q=lucas&visao=tabela`, 2 linhas, busca "lucas". |
| Filtro persistente | **Atendido**: `#/execucoes?status=pendencia` recarregado mantém o chip "Pede atenção 2". |
| Foco volta ao origem ao fechar o drawer | **Atendido**: Cartões, `Enter` em "Abrir android-02" e `Esc` → foco em "Abrir android-02". Lista, `Enter` em "Abrir android-03", `Tab` até "Fechar", `Enter` → foco no botão da linha. Recarregado em `#/infraestrutura?foco=android-13`, `Esc` → `main#conteudo`. |
| Menu expandido por padrão >= 1280 px | **Atendido**: 212 px a 1920, 1440 e 1280; recolhido (60 px) a 1024, sem preferência gravada. |
| Resumo da execução | **Atendido**: concluída abre em **Relatório** com "Concluída com sucesso em 2 min 03 s · 1 de 1 objetivo com sucesso" e "Ver pedido completo"; `needs_input` abre em **Plano** com "Precisa de você: Responder às 2 perguntas da IA"; planejada em **Plano** (ressalva B4). `?aba=plano` manda; tirar a guia volta ao Relatório; Voltar de `?aba=linha-do-tempo` volta ao padrão. |
| Contador de pendências coerente | **Atendido**: topo "4 aguardando você" = botão "Menu, 4 aguardando você" = menu "Pendências, 4 esperando você" = caixa "Todas (4)" (Aprendizado 1, Persona 1, Execução 2, Intervenção 0) = popover do semáforo "4 pendências esperando você"; Aprendizado › "Para aprovar 1" = "Aprendizado (1)" da caixa = menu "Aprendizado, 1 para aprovar". Ressalva B8 (uma origem que falha). |
| Seleção e barra | **Atendido a 1440**: marcar android-01 e android-13 (desconhecido) → "2 selecionados (1 ignorado: servidor sem resposta)", barra "Ação em 1 aparelho". A 390, M2. |
| Gaveta do menu (RF-26) a 768 | **Atendido**: `Enter` no "Menu" abre; 14 `Tab` e o foco segue na gaveta; `Esc` fecha e devolve o foco a `#botao-menu`. |
| Semáforo com servidor degradado | **Atendido em parte**: motivos "Servidor Notebook da LAN fora do ar" (link "Abrir Infraestrutura", que com `Enter` real leva o foco a `main#conteudo`, RF-45 fechado) e "2 aparelhos em estado desconhecido" ("Ver no Painel"). O Foco do android-13 diz "Desconhecido" e deixa Iniciar, Assumir controle, Instalar app e afins indisponíveis com o motivo (RF-40 fechado). O nível "Atenção": not_run (RF-41). |

## Achados

Escala: **Alta** = perda de função ou ação sobre o que não se vê; **Média** = critério não atendido, contradição
visível ou teclado quebrado; **Baixa** = texto, polimento, código, estrutura para o axe.

### Médias (ambas pré-existentes; nenhuma regressão das 10 a 14)

| ID | Onde | Achado e passos para reproduzir | Prova |
|---|---|---|---|
| **M1** | `features/runs/RunView.tsx` (Banner da execução `needs_input`, com `AssistenteDoComando` e a ação "Editar comando" ao lado), no detalhe de Execuções e no Painel | A 390 px o texto do Banner fica com **127 px** porque a ação "Editar comando" divide a linha. Dentro dele, "Responder e refinar" (116 px de texto em 99) e "Planejar com as respostas" (134 em 99) vazam do botão, os campos mostram "Sua respo", e o selo "2 perguntas" sai "2 pergun…" sem dica. A 768 px a caixa tem 471 px e nada corta. O código do Banner não foi tocado pelas 10 a 14 (pré-existente; a matriz da rodada 1 não o pegou porque o texto vaza dentro do botão, sem reticências). **Passos**: semear uma execução "Envie uma mensagem de bom dia" (modo plano); 390x844; `#/execucoes/<id>` ou `#/painel` com ela selecionada; rolar até "Responda aqui". Captura `capturas/ux2-15-execucao-responda-espremido-390.jpg`. | real |
| **M2** (RF-37 agravado) | `features/painel/BarraDeSelecao.tsx` a 390 px | Com um aparelho de servidor fora do ar entre os marcados, o rótulo "2 selecionados (1 ignorado: servidor sem resposta)" ocupa a linha (x 27 a 382) e as **6 ações** (Criar AVD, Iniciar, Parar, Reiniciar, Mais ações, Limpar seleção) começam em x=394, fora da tela; a barra rola de lado (891 px de conteúdo em 364) sem pista. Na rodada 1 eram só 2 ações fora. **Passos**: 390x844, `#/painel?visao=lista`, marcar android-01 e android-13. Captura `capturas/ux2-15-barra-selecao-acoes-fora-390.jpg`. | real |

### Baixas

| ID | Origem | Achado e passos para reproduzir | Prova |
|---|---|---|---|
| **B1** | regressão da **13** (`MenuLateral.tsx`) | O `aria-label` novo ("Pendências, 4 esperando você") troca o nome que antes vinha do conteúdo e não contém o texto visível contíguo "Pendências 4": o Lighthouse acusa `label-content-name-mismatch` nos itens Pendências e Aprendizado **nas 9 telas a 1440 px** (WCAG 2.5.3). Saída simples: o rótulo começar pelo que se vê ("Pendências 4, esperando você"). **Passos**: Lighthouse de acessibilidade em qualquer tela a 1440 com pendências > 0. | real |
| **B2** | regressões da **10** e da **12** | Dois selos novos em `--fs-2xs` (12 px): `TopBar.module.css .menuSelo` (botão "Menu", abaixo de 1024 px, nas 9 telas) e `Profiles.module.css .navSecaoContagem` (selo da seção Atividade da persona, nas 11 guias a 1440). "Contador em selo redondo" é a exceção que o token admite, mas o próprio `tokens.css` exige que cada uso seja documentado em `07-tipografia-a11y.md`, e nenhum dos dois está. O auditor conta como fonte < 13 px. Contraste do selo do Menu: dentro da meta. | real |
| **B3** | **12** (`abas.ts`, `ProfileDetail.tsx`) | O selo da seção **Atividade** conta as **Aprovações** pendentes, mas clicar abre **Interações** (a primeira guia da seção); as Aprovações são a 3ª guia. O nome acessível do botão é "Atividade 1": o `title` "Aprovações esperando você" está num `span` interno e não entra no nome. **Passos**: `#/personas/ana-beatriz-nandu-avila` com 1 aprovação pendente → "Atividade" → `…/interacoes`. | real |
| **B4** | **14** (`resumo.ts`) | Execução planejada: "Plano pronto. Nada foi executado ainda · **0 de 1 objetivo com sucesso**". A segunda parte lê como fracasso de algo que não rodou. **Passos**: `#/execucoes/<planejada>`. | real |
| **B5** | **11 x 12** (`scripts/ui-truncamento.js`) | `regiao()` trata **qualquer** `<header>` como o cabeçalho do portal. O `PersonaHeader` é um `<header>` e os blocos de conteúdo também têm `header` (ex.: o selo "2 perguntas" de M1 saiu como `cabecalho`): ficam fora de `totalConteudo`, o número que o aceite da 11 zera. Ponto cego da ferramenta, não da tela. Saída: `body > … header` do topo por id ou `role=banner`. | real (o caso de M1) |
| **B6** | **14 x 11** | `ResumoDaExecucao.tsx` mede o corte do pedido por conta própria (`resize` da janela) em vez de `useTransborda` de `components/TruncatedText.tsx`, a regra que a 11 criou: duas implementações. O expansor e a legenda usam `outline` no foco e não o anel do portal (`--focus-ring`, `box-shadow`). O script de truncamento cobre o pedido (há `aria-expanded` + `aria-controls` no `dd`), então não vira caso. | estático |
| **B7** | **10** e **10 x 13** | Polimento: `.alvoToque.alvoToque.alvoToque` (especificidade forçada) em `TopBar.module.css`; linha sem recuo em `SaudeAmbiente.tsx` (`<span className={styles.rotulo}>`); o mesmo número dito de dois jeitos, "aguardando você" (topo, botão Menu, Resumo) e "esperando você" (menu lateral, Pendências, semáforo). | estático + real (nomes lidos) |
| **B8** | pré-existente, agora também no botão Menu (10) | Quando uma origem falha (reproduzido com o item de aprendizado ilegível: `GET /api/aprendizado/pendentes` 500), a caixa avisa "Não foi possível ler todas as origens agora. A lista pode estar incompleta", mas o topo, o botão Menu e o menu lateral mostram **3** sem ressalva e o chip diz "Aprendizado (0)" como se fosse zero certo. Falha não deveria parecer número. **Passos**: derrubar uma das leituras da caixa e recarregar. | real |
| **B9** | pré-existente (lista de Personas), relevante para a 12 | Pelo padrão `Abrir ${nome}` do botão da linha (lido "Abrir Lucas Almeida" na tabela filtrada com os 2 homônimos), homônimos ficam com o mesmo nome acessível; quem usa leitor de tela não distingue os dois. O slug já distingue (`…-iwug`, `…-pwfl`); o nome pode levar o @ ou o aparelho. | real |
| **B10** | pré-existente (`GuiaPersona`) | Os 7 botões de bloco da guia Persona ("Identidade IA (parcial)", "Trabalho IA (vazia)"…) têm **31,5 px** de altura: passam no auditor (tolerância de 0,5 px) e falham na medida estrita; é a mesma receita de meio pixel que a 13 corrigiu em outras classes. | real |
| **B11** | pré-existente (`ParaAprovarTab`) | O cartão do item mostra a referência crua `licao:li-rev15-1` (soma-se ao RF-07r). | real |

## Achados da rodada 1 (RF-xx): situação agora

| ID | Situação | Prova |
|---|---|---|
| RF-26 Tab escapa da gaveta | **fechado** | real (768, teclas reais) |
| RF-31 links de 32 px | **fechado** (0 alvo estrito no Aprendizado nas 6 larguras) | real |
| RF-33 comando cru no detalhe do app | **fechado** (título curto e `title` com o integral) | real |
| RF-34 linha de hardware cortada | **fechado** (12 aparelhos, 0 elipse a 768 e 390) | real |
| RF-40 "Desconhecido" x "Parada" | **fechado** (Foco do android-13 diz Desconhecido, ações indisponíveis com motivo) | real |
| RF-44 nome do semáforo | **fechado** (o Lighthouse não acusa mais o semáforo; ver B1 para o menu) | real |
| RF-45 foco após link do popover | **fechado** (`main#conteudo`) | real |
| RF-46 selos cortados no topo a 390 | **fechado** (vão para o Resumo; 0 elipse no topo) | real |
| RF-22 selo Aprendizado x chip | coincidem hoje (1 = 1) | real |
| RF-07r jargão restante | **aberto**: "para os perfis que você escolher" (`TrainingBar.tsx:148`), "associação de instâncias e contas, status da IA" (lead de Configuração), "processo: …" e "worker.yaml" (Infraestrutura), mais B11 | estático + real |
| RF-19 chips `role=radio` sem setas | **aberto** (5 chips de Pendências, todos `tabindex=0`) | real |
| RF-27 estrutura para o axe | **aberto**: `region` (toast) em todas as células; `heading-order` em Personas (`h3#grupos-de-acesso`) e nas guias Persona e Contas e acesso; `aria-allowed-role` nas guias Persona e Aparelhos e no `aside role=dialog`; `heading-order` e `page-has-heading-one` no detalhe do app | real |
| RF-30 drawer cobre a coluna "Abrir" da Lista | **aberto** (14 de 14 botões sob o drawer a 1440) | real |
| RF-32 detalhe fora do filtro | **aberto** (chip "Concluídas" com o detalhe de uma `needs_input`) | real |
| RF-35 faixa de guias 41/40 com barra de rolagem | **aberto** (Aprendizado, Configuração, Aplicativos) | real |
| RF-37 barra de seleção a 390 | **aberto e agravado** (M2) | real |
| RF-41 nível de cada problema da saúde | **aberto** ("MODO SIMULADO ativo" e "Appium" marcados CRÍTICO) | real |
| RF-42 servidor offline com "Túnel: no ar" e "Adotar" de aparelhos já adotados | **aberto** | real |
| RF-48 textos crus do servidor | **aberto**: "1 worker(s) remoto(s)… (server.worker_port: 0)", "Use AI_PROVIDER=anthropic no .env", "Origem: Adotado de um servidor, sem config.yaml", "external", "stopped · adb 15554" | real |
| RF-49 "grade" na visão Lista | **aberto** (`CommandPanel.tsx:277`) | estático |
| RF-10, 11, 14, 15, 16, 17, 18, 21, 23, 47 | sem mudança nesta rodada (não tocados pelas 10 a 14; decisões do dono ou ferramenta) | estático |
| RF-12, 38, 43 | não remedidos | not_run |

## Revisão estática dos diffs (FASE 1)

Lidos os merges `652cb40` (14), `c495b2d` (10), `bf1b046` (12), `c286dd1` (11) e `7d4bcc5` (13).

- **Rotas**: nenhuma regressão. `#/perfis/…` redireciona e vira slug; `?foco=` atravessa a canonização; `?aba=` de
  Execuções manda sobre a guia padrão e o Voltar volta ao padrão (`RunBody` tem `key={run.id}`, então trocar de
  execução recalcula a guia); `?status=` e `?visao=` intactos. `store/ui.ts` só ganhou um `export`; `openPersona` e
  `pendencias/modelo.ts` continuam gerando links com id, que a tela canoniza por substituição.
- **Fonte única**: o botão Menu e o Resumo leem `usePendencias().total`; Counters, Custos e IA do Resumo reusam os
  mesmos componentes da régua; a regra de contagem continua só em `montarPendencias` (a 14 só a documentou). Sem
  duplicação de número. A duplicação encontrada é a de B6 (medida de corte).
- **Contradições entre tarefas** pedidas pelo orquestrador:
  - selo do Menu (10) x `aria-label` do menu (13): o mesmo número, mas dito de dois jeitos (B7), e o `aria-label` da
    13 quebrou o rótulo no nome (B1);
  - `useFaixa` (10) x sidebar (13): sem conflito. O cabeçalho troca de faixa em 768 e 1024; o menu, em 1024 (gaveta)
    e 1280 (recolhido por padrão). Medido nas 6 larguras;
  - resumo da execução (14) x `TruncatedText` (11): ver B6;
  - abas da persona (12) x filtro de app: o filtro só aparece em Memória e Interações e diz o que filtra;
    "Adicionar conta" continua na guia Contas e acesso, então a entrada não se perdeu.
- **Código morto**: a 11 apagou o `.command` que a 14 deixou sem uso. Nada novo encontrado.
- **Textos**: nada em inglês nas cadeias novas; o glossário (Persona, Aparelho, Execução) é respeitado.

## Divergências do briefing

- Caminho do relatório (acima).
- As medições de linha de base do briefing são do build implantado em `83af733` com dados reais; aqui o dado é o do
  simulado (12 aparelhos locais + 2 remotos, 6 personas, 5 execuções). Com ele renderizaram os contadores de estado
  do Painel (`stateQuick`), os botões de persona e de tarefa das linhas da Infraestrutura (`personaDoAparelho`,
  `.tarefa`), e a medida deu **0** alvo abaixo de 32 px no Painel e na Infraestrutura (auditor e medida estrita), não
  5 e 6: a correção da 13 cobre a causa comum nessas classes; a contagem 5/6 do build antigo com dado real não foi
  reproduzida.
- "Rodar axe/Lighthouse nas 9 telas": axe nas 54 células; Lighthouse nas 9 a 1440 e em 2 a 390 (as demais larguras
  `not_run`).

## Pendências restantes (para tarefas pequenas, uma por commit)

1. **M1**: o Banner da execução `needs_input` empilhar a ação "Editar comando" abaixo do texto em tela estreita (ou
   a caixa ocupar a largura toda), e os botões quebrarem linha em vez de vazar.
2. **M2 / RF-37**: a barra de seleção a 390 px quebrar em duas linhas (rótulo em cima, ações embaixo) ou encurtar o
   rótulo ("1 ignorado" com o motivo no `title`/legenda).
3. **B1**: `aria-label` dos itens do menu começando pelo texto visível.
4. **B3**: a seção Atividade abrir Aprovações quando há pendência (ou o selo ir para a guia), e o nome do botão dizer
   o que o número conta.
5. **B4**: não mostrar "0 de N objetivos com sucesso" para `planned`/`needs_input`.
6. **B2**: documentar os dois selos de 12 px em `07-tipografia-a11y.md` (e atualizar o comentário de `--fs-2xs`, que
   ainda cita o gráfico) ou subi-los a 13 px.
7. **B5**: `regiao()` do `ui-truncamento.js` olhar só o cabeçalho do portal.
8. B6 a B11 e os RF abertos (lista acima), na limpeza.

## Arquivos

- `docs/revisoes-ux/rodada-2/revalidacao-final.md` (este relatório);
- `docs/revisoes-ux/rodada-2/capturas/ux2-15-execucao-responda-espremido-390.jpg` (M1);
- `docs/revisoes-ux/rodada-2/capturas/ux2-15-barra-selecao-acoes-fora-390.jpg` (M2).

Nenhum arquivo de código foi alterado. Os scripts de semeio, config, banco e medidor ficaram no rascunho da sessão,
fora do repositório, e foram apagados ao fim.
