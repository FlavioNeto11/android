# Tarefa 09: revisão final, regressão e testes de responsividade (relatório final)

Branch `ux/09-revisao-final`. Revisor independente (Opus 5.5, esforço high): não participei das tarefas 01 a 08 nem da 10.
Máquina: central (Windows Server 2025), 30/09/2026.

- **FASE A** (estática, cerca de 21:05Z a 21:25Z, commit `5da4cfc`): leitura dos diffs das tarefas 01 a 08, com 29 achados
  (RF-01 a RF-29). O texto completo da FASE A está no commit `6fb6749` deste branch, no mesmo arquivo.
- **Tarefa 10** (`ux/10-correcoes`, integrada em `claude/ux-portal` `58d67f5`): corrigiu RF-01, 02, 03, 05, 06, 07, 09,
  13, 20, 24 e 29, e documentou RF-10 (`10-correcoes.md`).
- **FASE B** (em execução, 30/09, cerca de 23:34Z a 23:56Z, commit `58d67f5`): portal do branch no vite da porta 5199
  (`VITE_API_TARGET=http://127.0.0.1:8000`, prioridade baixa), numa aba do navegador da IDE que **já tinha sessão**.
  **Não entrei com conta**; o cookie existente no perfil do navegador valeu para a porta 5199. Foi um trabalho pesado
  por vez: um vite e uma aba, com o host em 2 a 48 % de CPU e 31 GB livres.

## Veredito

**Pronto com ressalvas.**

- Nenhuma regressão de gravidade Alta ficou aberta. RF-01 e RF-02, as duas Altas da FASE A, estão corrigidas e
  provadas no navegador.
- As 54 células (9 telas x 6 larguras) não têm rolagem horizontal, menu inalcançável, transbordo nem sobreposição no
  topo.
- Os números da fonte única batem entre topo, Painel, Infraestrutura, Diagnóstico, semáforo e API na mesma leitura.
- O axe não acusa nenhuma falha de contraste nas 54 células. As violações restantes são de estrutura (moderate/minor).

**Ressalvas que o dono precisa ver antes da `main`:**

- RF-05 **reaberto**: o contador "execuções" do topo ainda muda conforme a tela visitada e diverge de "Em andamento".
  Em 23:40Z o topo dizia 0 ou 1, e o chip dizia 3.
- RF-26: o Tab escapa da gaveta do menu, abaixo de 1024 px, e dali o Esc deixa de fechá-la.
- RF-04 e RF-08: decisões de produto pendentes (definição de "pendência"; filtros que se perdem ao sair pelo menu).
- O semáforo com servidor fora do ar **não foi provado em execução**. Pelo vite, o canal ao vivo cai (código 1006) e o
  semáforo mostra "Crítico" por construção. A lógica está provada só por teste (`simulated`).

## Provas

**real** (30/09, máquina central, commit `58d67f5`, vite 5199 com backend vivo em leitura, aba `tab-10` do navegador da
IDE). Foi só navegação, seleção, abrir e fechar o Foco, filtros e teclado. **Nenhuma** ação com efeito foi disparada:
Iniciar, Parar, Reiniciar, Resetar, Instalar, Executar, Assumir controle, aprovar e login ficaram intocados.

- Varredura automática por célula com um script próprio, injetado junto com o axe-core 4.13 (tags `wcag2a`, `wcag2aa`,
  `wcag21a`, `wcag21aa`, `wcag22aa`, `best-practice`) e o `scripts/ui-auditoria.js`. Para cada célula ela mede:
  `scrollWidth` contra a largura, as 9 seções do menu alcançáveis (visíveis, ou botão da gaveta visível), elementos do
  `main` que passam da borda fora de rolador, sobreposição de controles no topo, texto cortado com reticências sem
  dica, fonte abaixo de 13 px, contraste abaixo de AA, alvos abaixo de 32 px e botão sem nome.
  - Os arquivos `axe.min.js`, `ui-auditoria.js` e o medidor ficaram em `frontend/public/__rev09/` só durante a
    medição, foram apagados depois, e nada foi comitado.
  - Mais 26 visões internas a 1440 px: guias de Aplicativos, Execuções, Aprendizado e Configuração, o detalhe de app,
    o Foco de dois aparelhos e as 11 guias de uma persona.
- Fluxos medidos um a um, descritos abaixo, com cliques e teclas reais do painel (`computer`) onde isso importava:
  caixa de seleção, Tab, Shift+Tab, Enter e Esc.
- `node scripts/ui-verificar.mjs --url http://127.0.0.1:5199 --telas painel --larguras 1440`: **caiu na tela de
  entrada** ("waiting for locator('header')"). Isso confirma o RF-10: sem sessão, o script não alcança as telas.

**simulated**:

- `npm run typecheck`: verde.
- `npm test`: **84 arquivos, 1000 testes, todos verdes** (vitest, 30/09 ~23:55Z, prioridade baixa, 17,6 s). Entre eles
  estão `features/topbar/SaudeAmbiente.test.tsx` e `store/metricas.test.ts`, que provam o semáforo com o servidor da LAN
  fora do ar ("Atenção" com o motivo e o link).

**not_run**:

- Lighthouse. Ele sobe um Chrome próprio sem a sessão e cairia na tela de entrada, e entrar é proibido.
- axe na tela de entrada (RF-28): exigiria sair da sessão.
- Semáforo com o servidor realmente fora do ar (a lógica fica só por teste).
- Drawer em 1920, 1280 e 1024 px (medido em 1440, 768 e 390).
- Leitor de tela e toque real.
- O build da `main` servido na 8000. Só abri a 8000 uma vez, para saber se um defeito é anterior (RF-35).
- `python scripts/docs-check.py`.

## Tabela tela x largura (9 x 6)

Legenda: **OK** = sem rolagem horizontal, menu alcançável, sem transbordo, sem sobreposição no topo, sem corte sem dica,
0 contraste abaixo de AA e 0 alvo abaixo de 32 px. Em todas as células há duas violações do axe que não quebram a
célula: `region` (moderate), da região de avisos (toast) fora de marco, e a faixa "Desconectado do servidor", do canal
caído pelo vite. Abaixo de 1024 px o menu é a gaveta, pelo botão "Menu".

| Tela | 1920 | 1440 | 1280 | 1024 | 768 | 390 |
|---|---|---|---|---|---|---|
| Painel | OK | OK | OK | OK | OK | OK (a barra de seleção rola de lado, RF-37) |
| Personas | OK · axe `heading-order` | OK · `heading-order` | OK · `heading-order` | OK · `heading-order` | OK · `heading-order` | OK · `heading-order` |
| Aplicativos | problema, Baixa (pacote `com.microsoft.office.outlook` cortado sem dica) | OK | problema, Baixa (idem) | problema, Baixa (idem) | OK | OK |
| Execuções | OK | OK | OK | OK | OK | OK |
| Pendências | OK | OK | OK | OK | OK | OK |
| Aprendizado | problema, Baixa (alvo de 29 px, RF-31) | idem | idem | idem | idem | OK |
| Infraestrutura | OK | OK | OK | OK | problema, Baixa (linha de hardware cortada sem dica, RF-34) | idem |
| Configuração | OK | OK | OK | OK | OK | OK |
| Diagnóstico | OK · 3 rótulos de gráfico a 12 px (exceção documentada) · axe `aria-allowed-role` (minor) | idem | idem | idem | idem | idem |

Visões internas a 1440 px: 26 medidas. Todas estão sem rolagem horizontal e sem transbordo, com 0 contraste abaixo de
AA. Os problemas foram estes:

- `#/aplicativos?aba=versoes`: selo "copiado da loja (Play Store)" cortado sem dica, e axe `page-has-heading-one`.
- `#/aplicativos/chrome`: comando cru "No QA Messenger, leia o nome do primeiro…" cortado sem dica (RF-33), e axe
  `heading-order` e `page-has-heading-one`.
- Foco (`?foco=`): axe `aria-allowed-role`, porque o `aside` tem `role="dialog"` (RF-27).
- Persona: `heading-order` nas guias Visão geral, Persona, Contas e Interações; `aria-allowed-role` em Persona e
  Aparelhos; alvo de 17 px em Aprovações (RF-31).

Capturas, só dos problemas (`docs/revisoes-ux/capturas/`):

- `rf05-topo-1-execucao-x-em-andamento-3-1440.jpg`: o topo diz "1 execução", o chip diz "Em andamento 3", e o detalhe
  aberto é de uma execução fora do filtro (RF-05, RF-32).
- `rf30-lista-drawer-cobre-abrir-1440.jpg`: na visão Lista, a coluna do "Abrir" fica sob o drawer (RF-30).

## Fluxos (real, salvo quando dito)

| Fluxo | Resultado |
|---|---|
| Persona por URL direta | `#/perfis/<id>/memoria?q=x` virou `#/personas/<id>/memoria?q=x`, com a persona certa na guia Memória e "Personas" como item atual. Voltar levou à entrada anterior do histórico (o Painel), como se espera de um link colado. |
| Abrir persona pela lista e Voltar | A partir de `#/personas?situacao=bloqueada&visao=tabela` (5 linhas, "5 de 14 personas"): "Abrir Beatriz Rocha" empilhou `#/personas/<id>`; a guia Memória **substituiu** (`/memoria`); um Voltar retornou à lista **com os filtros**. |
| Filtro persistente após recarregar | Personas `?q=%40lucas&situacao=ativa&visao=tabela`: depois de `location.reload()` (tipo `reload`), a busca "@lucas", o chip "Ativas", a Tabela e 1 linha voltaram. Execuções `?periodo=30d&q=nasa&status=concluida`: a busca, o período e o chip voltaram, com "15 de 247". |
| Filtro ao sair e voltar pelo menu | **Perdido** (RF-08 confirmado): menu Painel → menu Personas deu `#/personas`, sem filtro. Com dois Voltar, o link filtrado reaparece. |
| Seleção x filtro (RF-01) | **Corrigido.** Com `#/painel?estado=stopped` (4 visíveis), marcar os 6 online deu "0 selecionados (6 fora do filtro atual)", o aviso "Nenhum aparelho marcado aparece neste filtro…", só o botão "Limpar seleção" e nenhuma `toolbar`. "Selecionar todos" **substituiu** pela seleção dos 4 visíveis, com a barra "4 selecionados" e `aria-label` "Ação em 4 aparelhos" (RF-13 corrigido). Nenhuma ação foi clicada. |
| Barra de seleção | Fica presa ao topo da grade: nenhum cartão sob ela a 390 px. Ao marcar o primeiro aparelho, a grade **desce 58 px** (RF-12 confirmado). A 390 px a linha rola (637 px de conteúdo em 364 visíveis), e "Mais ações" e "Limpar seleção" ficam fora da vista (RF-37). |
| Drawer do Foco | Aberto pelo "Abrir android-03" com **Enter real**: o foco entra no painel. Tab real no último controle ("Hierarquia") foi ao primeiro ("Fechar"); Shift+Tab voltou ao último; Esc real fechou (`#/painel`) e devolveu o foco a "Abrir android-03". Os retângulos dos 15 cartões e a largura do `main` ficaram iguais com o drawer aberto e fechado (1440: `main` 1228 px; 768: `main` 768 px, drawer 720 px). A 390 px é tela cheia (390x844), com "Voltar" e sem corte nem transbordo. As legendas do preview não têm corte. |
| Drawer na visão Lista (RF-02) | **Corrigido.** Com o Foco de android-01 aberto, o clique real na caixa de android-02 marcou a caixa e o drawer continuou aberto. "Abrir android-02" (clique por script) trocou para `?foco=android-02`. Novo: a coluna "Abrir" fica **sob** o drawer em qualquer largura (RF-30). |
| Caixa de Pendências | Menu "Pendências 4 (4 esperando você)" = 4 linhas = chip "Todas (4)" (Aprendizado 0, Persona 0, Execução 4, **Intervenção 0**: a origem nova do RF-03 aparece). Os títulos são curtos, sem "Nas instâncias selecionadas, no QA Messenger," (RF-09 corrigido). A dica da idade é "28/09, 12:12:00" (RF-20 corrigido). |
| Contadores que "esperam você" (RF-04) | Na mesma leitura: topo "4 aguardando você", semáforo "4 objetivos aguardando você nas execuções", Pendências 4, Execuções "Com pendência 115". Hoje os três primeiros coincidem só porque cada execução tem um objetivo. Os destinos seguem diferentes. É decisão do dono. |
| Execuções ativas (RF-05) | **Reaberto.** O topo mostrou "0 execuções" ao abrir o portal e "1 execução" depois de visitar Execuções (o histórico carregado entra no store). O chip "Em andamento" mostrou **3**. A API tem 3 execuções `planned` (19/09, 22/09 e 27/09), que o snapshot não conta como ativas. |
| Tempo relativo (RF-06) | **Corrigido.** A legenda do preview do Foco diz "Prévia suspensa — última imagem há 1 min", e o rodapé "última imagem há 1 min". O cartão e a trilha do Foco dizem "há 3 dias" para o mesmo comando. Não aparece mais "há 1 min 14 s". |
| Semáforo (RF-23 e prova do 02) | Pelo vite, "Ambiente crítico, 2 motivos": "painel sem conexão com o servidor central" (canal caído, código 1006) e "Servidor Notebook da LAN degradado — relógio desalinhado: +6.1 s". Em "Também": 5 personas bloqueadas e 4 objetivos. O nível mostrado é o do canal caído, não o do ambiente. Com o servidor fora do ar: só `simulated` (testes acima). |
| Gaveta do menu, teclado (RF-26) | A 768 px, Enter no "Menu" abre a gaveta com o foco no item atual. Depois de 9 Tabs, o foco saiu da gaveta para "Reconectar agora", atrás do fundo escurecido. Esc com o foco fora da gaveta **não** a fecha. Escolher uma seção pela gaveta leva o foco ao `<main>` (sem anel) e fecha a gaveta: isso funciona. |
| Chips de Pendências (RF-19) | `role="radio"`, todos com `tabindex=0`; seta para a direita não move o foco. Confirmado. |
| Teclado em Painel e Personas | 115 e 106 controles tabuláveis, nenhum `tabindex` positivo, todos com anel de foco (medido por `focus()` em cada um, além dos Tabs reais acima). |

## Tabela de métricas da tarefa 02 (real, 30/09 ~23:43Z, mesma leitura)

| Métrica | API (`/api/snapshot`, `/api/workers`, `/api/personas`) | Topo | Painel | Infraestrutura | Diagnóstico | Semáforo, Personas e Pendências |
|---|---|---|---|---|---|---|
| Aparelhos de tarefa | 15 instâncias, 1 é a loja → 14 | "6/**14**" | "0 de 14 selecionados" | — | "6 de **14** online" | — |
| Online | 6 | 6 | "6 online" | vagas 3 (central) + 3 (notebook) = 6 | 6 | — |
| Parados / hibernados | 4 / 4, loja parada | — | "4 hibernados · 4 parados · aparelho-loja parado" | — | — | — |
| Desconhecidos | 0 (notebook conectado, `degraded`) | — | nenhum grupo | — | — | sem motivo de desconhecidos |
| Vagas | central `max_slots` 4, notebook 6 | — | — | "3 de 4" e "3 de 6", sem alerta | "cabem até 15 neste servidor (estimado)" | — |
| Personas bloqueadas | 5 de 14 | — | — | — | — | "5 personas bloqueadas pela plataforma" = `#/personas?situacao=bloqueada` com 5 linhas |
| Aguardando você | 4 objetivos (snapshot) | 4 | — | — | — | semáforo 4; Pendências 4 linhas |
| Execuções ativas | 3 `planned` no histórico, 0 ativas no snapshot | **0 → 1** | — | — | — | Execuções "Em andamento **3**" (RF-05) |

Tudo bate, exceto as execuções ativas (RF-05).

## Regressões e achados

Escala: **Alta** = perda de função ou ação sobre o que não se vê; **Média** = critério não atendido, contradição visível
ou acessibilidade de teclado quebrada; **Baixa** = texto, polimento, código morto, estrutura para o axe.

### Situação dos achados da FASE A (depois da tarefa 10 e da FASE B)

| ID | Situação agora | Prova |
|---|---|---|
| RF-01 ação em lote fora do filtro (Alta) | **corrigido** | real (fluxo acima) |
| RF-02 drawer na Lista (Alta) | **corrigido**; efeito colateral vira RF-30 | real (clique na caixa) + `Drawer.test.tsx` |
| RF-03 intervenções fora de Pendências | **corrigido** (origem "Intervenção" visível; 0 casos vivos) | real parcial + `PendenciasPage.test.tsx` |
| RF-04 quatro definições de "pendência" | aberto, **decisão do dono** (Média) | real (contadores acima) |
| RF-05 execuções ativas fora da fonte única | **reaberto** (Média): regra unificada, base ainda variável | real + captura |
| RF-06 dois formatadores de tempo | **corrigido** | real |
| RF-07 jargão e inglês visíveis | **parcial** (agora Baixa): restam os textos listados em RF-07r | real (varredura de texto) |
| RF-08 persistência de visão e filtros | aberto, **decisão do dono** (Média) | real |
| RF-09 título cru em Pendências | **corrigido** | real |
| RF-10 verificador sem sessão | documentado; limitação confirmada (Baixa, ferramenta) | real (execução do script) |
| RF-11 `aria-modal` com o fora clicável | aberto, decisão do dono (Baixa) | not_run (leitor de tela) |
| RF-12 grade desce ao marcar | **confirmado**, 58 px (Baixa) | real |
| RF-13 nome da barra × contador | **corrigido** | real |
| RF-14 código morto | aberto (Baixa) | estático |
| RF-15 links que fecham o Foco | aberto (Baixa) | estático |
| RF-16 gestão de app em Configuração | aberto, decisão (Baixa) | estático |
| RF-17 etapas 1 → 2 → 3 | aberto, decisão (Baixa) | estático |
| RF-18 ícone `Hand` com três sentidos | aberto (Baixa) | estático |
| RF-19 chips `role="radio"` sem setas | **confirmado** (Baixa) | real |
| RF-20 dica em ISO cru | **corrigido** | real |
| RF-21 R$ mostrado como US$ sem câmbio | aberto (Baixa); com as contas de hoje (Anthropic e OpenAI em US$) não se manifesta | real (topo e Diagnóstico em US$) |
| RF-22 selo Aprendizado × chip | aberto (Baixa); hoje 0 = 0 | real |
| RF-23 `reconnecting` já é Crítico | aberto, decisão (Baixa) | real (o semáforo fica Crítico pelo canal) |
| RF-24 "Persona: … · persona Y" | corrigido | estático (`10-correcoes.md`) |
| RF-25 expressão da 08 em `PlanTab` | encerrado (sem ação) | — |
| RF-26 Tab escapa da gaveta | **confirmado**, e o Esc para de funcionar: sobe para **Média** | real |
| RF-27 violações do axe | **confirmado**: `region` (54 células), `heading-order` (Personas, guias da persona, detalhe de app), `aria-allowed-role` (figure do Diagnóstico, `aside role=dialog` do Foco, article e listitem na persona), `page-has-heading-one` (Aplicativos: versões e detalhe) (Baixa) | real (axe) |
| RF-28 texto a 70 % na entrada | not_run (exigiria sair da sessão) | — |
| RF-29 comentário `<pacote>` | corrigido | estático |

### Novos na FASE B

| ID | Grav. | Onde | Achado e passos para reproduzir | Prova |
|---|---|---|---|---|
| RF-07r | Baixa | `RunView` (Painel e Execuções), `InfraPage`, `SettingsPage` (lead), `FocusPanel`/`FocusInfoSections`, textos vindos do servidor | Restos visíveis do RF-07: "Status: Precisa de informações" e "Nenhuma instância precisou revisar o plano" (detalhe da execução); "processo: running/stopped", "worker.yaml" e "GPU do host" (Infraestrutura); "associação de instâncias e contas, status da IA" (lead de Configuração); "para os perfis que você escolher" (Modo treinamento no Foco); "readotado após reinício do backend" e "hibernado (snapshot salvo)" (Foco; parecem vir do servidor); selo "Parada" ao lado do resumo "parados". Passos: abrir cada tela e buscar os termos. | real |
| RF-30 | Baixa | `DeviceList` × `Drawer` | Na visão Lista, a coluna de ações (o "Abrir") fica à direita e o drawer a cobre em qualquer largura. Para trocar de aparelho pela Lista é preciso fechar o Foco; nos Cartões, os da esquerda ficam à vista. Passos: Painel → Lista → abrir android-01 → o "Abrir" das outras linhas fica sob o painel. | real + captura |
| RF-31 | Baixa | `ParaAprovarTab.tsx` ("Ver todas as suas pendências", 29 px), `GuiaAprovacoes.tsx` ("Ver todas as pendências", 17 px) | Os links que a 06 acrescentou ficam abaixo de 32 px de alvo: é regressão do critério da 07, que mediu antes da integração da 06. Passos: `#/aprendizado` e `#/personas/<id>/aprovacoes`, auditor de alvos. | real |
| RF-32 | Baixa | `RunsPage` | Com um filtro ativo, o detalhe continua mostrando a execução selecionada antes, mesmo fora do recorte. Em `?status=andamento` e em `?status=concluida&q=nasa` o detalhe era `f55c04`, "Precisa de informações". Passos: abrir Execuções, filtrar por uma situação que não inclua a aberta. | real + captura |
| RF-33 | Baixa | detalhe do app (`#/aplicativos/chrome`) | As execuções recentes do app mostram o comando cru ("No QA Messenger, leia o nome do primeiro…"), cortado sem dica. Não usa o `tituloCurto` da 05 (o RF-09 corrigiu só Pendências). | real |
| RF-34 | Baixa | `InfraPage` (lista de aparelhos) | A 768 e 390 px, a linha "processo: running · API 34 · x86_64 · Play Services · renderizador…" é cortada com reticências sem dica. | real |
| RF-35 | Baixa, **pré-existente** | `components/Tabs` (faixas de guias) | Toda faixa de guias tem `scrollHeight` 41 > `clientHeight` 40 com `overflow: auto`, e aparece uma barra de rolagem vertical minúscula à direita (Aprendizado, Configuração, Aplicativos, Infraestrutura). O mesmo acontece no build da `main` (8000), então não foi introduzido pela revisão. | real |
| RF-37 | Baixa | `BarraDeSelecao` a 390 px | A barra rola de lado, e "Mais ações" e "Limpar seleção" nascem fora da vista (637 px de conteúdo em 364), sem pista de que há mais. Foi escolha da 03; sai do RF-12 visto no celular. | real |
| RF-38 | Baixa | cartão do aparelho (evidência) | A evidência escrita pela IA aparece com referências de elemento cruas: "…o título 'Inbox' (e6), a lista de e-mails visível (e18, e19, e20, e21…)". O texto vem do servidor. | real |

(RF-36 foi absorvido pelo RF-07r.)

### Contagem de achados abertos depois da FASE B

- **Alta: 0.**
- **Média: 4**: RF-04 (decisão), RF-05 (reaberto), RF-08 (decisão), RF-26.
- **Baixa: 24**:
  - da FASE A: RF-07r, 10, 11, 12, 14, 15, 16, 17, 18, 19, 21, 22, 23, 27, 28;
  - novos: RF-30, 31, 32, 33, 34, 35 (pré-existente), 37, 38.
- **Corrigidos e verificados**: RF-01, 02, 03, 06, 09, 13 e 20 (real); RF-24 e 29 (estático). **Encerrado**: RF-25.

## Critérios de aceite por tarefa (depois da FASE B)

| Tarefa | Critério | Situação |
|---|---|---|
| 01 | As telas alcançáveis pelo menu de 390 a 1920 px | **atendido** (real: 9/9 visíveis de 1024 a 1920 px, gaveta a 768 e 390) |
| 01 | Abrir persona, aparelho ou execução muda a URL; colar abre | **atendido** (real) |
| 01 | Voltar fecha o detalhe e volta à lista | **atendido** (real, com os filtros preservados) |
| 01 | `#/perfis` → `#/personas` | **atendido** (real) |
| 01 | Nenhuma regressão de rota | atendido com ressalvas: RF-08 (filtro some pelo menu), RF-15 |
| 02 | Uma métrica, um valor em todas as telas | **parcial**: aparelhos, online, vagas, bloqueadas e aguardando batem (real); execuções ativas não (RF-05) |
| 02 | "Ambiente" em Atenção com motivo quando a LAN está fora do ar | `simulated` (testes); real `not_run` (canal caído pelo vite; notebook no ar) |
| 02 | Contadores levam à lista filtrada | parcial: bloqueadas e desconhecidos sim; "execuções" sim (`?status=andamento`); "aguardando você" não (RF-04) |
| 02 | Testes dos cálculos | **atendido** (`metricas.test.ts`, suíte verde) |
| 03 | A barra não cobre cartão em 390, 768, 1024 e 1440 px | **atendido** (real a 390 e 1440; nas demais o layout é igual, fluxo normal) |
| 03 | Nenhuma ação duas vezes | **atendido** (código + teste; com o Foco aberto, a barra fica sem botões) |
| 03 | Abrir o drawer não altera a grade | **atendido** (real: retângulos idênticos a 1440 e 768) |
| 03 | Legendas do preview sem corte | **atendido** (real a 768, 390 e 1440) |
| 03 | Executar desabilitado mostra o motivo | atendido (código e teste); not_run no navegador (não havia Executar desabilitado com motivo à vista) |
| 04 | Nenhum identificador técnico cru por padrão | **parcial** (RF-07r, RF-38, chaves de `PlanTab`) |
| 04 | Parado com no máximo metade da altura | atendido (cartões compactos à vista; não remedi em px) |
| 04 | A Lista mostra os 15 numa tela a 1440x900 | not_run (não remedi) |
| 04 | Texto novo em pt-BR | atendido após o RF-07 ("Cartões", "sem emulador") |
| 05 | Achar persona pelo @ | **atendido** (real: "@lucas" filtra na hora) |
| 05 | Filtros combinados persistem ao recarregar | **atendido** (real: Personas e Execuções); RF-08 fora do recarregamento |
| 05 | Cartões de altura uniforme, sem corte sem dica | **atendido** (real: 0 cortes sem dica em Personas nas 6 larguras) |
| 05 | Cartões/tabela não perde seleção | atendido (código e teste); not_run no navegador |
| 05 | Testes | **atendido** (suíte verde) |
| 06 | Um só "Novo aplicativo" | **atendido** |
| 06 | Todos os pendentes na caixa; contador = lista | **atendido** (real: 4 = 4; a origem Intervenção existe) |
| 06 | Termos da loja explicados | atendido (legenda à vista); o tooltip por foco só em jsdom |
| 06 | Links antigos | **atendido** |
| 07 | Nenhum texto abaixo de 13 px (exceto gráfico) | **atendido** (real: 0 nas 54 células e nas 26 visões internas; 3 rótulos de gráfico) |
| 07 | axe sem falha de contraste AA | **atendido** (real: 0 `color-contrast` nas 54 células) |
| 07 | Alvos abaixo de 32 px = 0 na tela principal | **atendido** no Painel (0 nas 6 larguras); regressão fora dela em RF-31 |
| 07 | Teclado completo em Painel e Personas | **atendido** (real: 115 e 106 controles, anel em todos, sem `tabindex` positivo) |
| 08 | Tabela de achados / só strings | parcial (a varredura da 08 deixou restos, completados pela 10 e ainda com RF-07r) |

## Pendências e recomendação

**Antes de ir para a `main`** (correções pequenas, uma por commit, como a 10):

1. **RF-05**: contar as execuções em andamento sobre uma base fixa, a mesma janela do snapshot, como já se faz com
   "aguardando você". Outra saída é o backend informar o total. A opção mais honesta é o topo dizer o que conta
   ("ativas agora") e a Execuções mostrar à parte as `planned` antigas, que parecem execuções paradas desde 19/09. Vale
   o dono olhar essas 3.
2. **RF-26**: prender o Tab na gaveta aberta (como o drawer faz) ou fechá-la quando o foco sai. O Esc deve valer com o
   foco em qualquer lugar enquanto ela estiver aberta.
3. **RF-31**: alvo de 32 px nos dois links "Ver todas… pendências" (padding e margem negativa, como a 07 fez nos
   `linkBtn`).

**Decisões do dono** (sem código até decidir): RF-04 (qual número é "a pendência" e para onde o contador leva), RF-08
(filtros e visão na URL ou no navegador, e se o menu leva a última busca), RF-11, RF-16, RF-17 e RF-23.

**Limpeza depois**: RF-07r (termos restantes; parte vem do servidor), RF-14 (código morto), RF-27 (estrutura de
títulos e papéis para o axe), RF-30, RF-32, RF-33, RF-34, RF-35 (pré-existente), RF-37 e RF-38.

**Da avaliação original, fora do escopo das tarefas 01 a 08**:

- detalhe da persona com 11 guias;
- detalhe da execução com 7 guias, sem "Relatório" como padrão;
- hierarquia da Infraestrutura;
- Ctrl+K e notificações persistentes;
- campos sensíveis da persona;
- a decisão sobre o escopo mobile. A 390 px o cabeçalho ocupa cerca de 300 px, um terço da tela, antes do conteúdo, e
  o campo de comando segue com 3 linhas.

**Para provar o que ficou `not_run`**: um backend simulado do próprio worktree na 8765 (`vite --mode simulado`), onde
entrar é inofensivo. Ele permitiria o Lighthouse, o axe da tela de entrada e o semáforo com um servidor inscrito e
nunca conectado. É outro processo no host, e fica a critério da coordenação.

## Arquivos

- `docs/revisoes-ux/revisao-final.md` (este relatório);
- `docs/revisoes-ux/capturas/rf05-topo-1-execucao-x-em-andamento-3-1440.jpg`;
- `docs/revisoes-ux/capturas/rf30-lista-drawer-cobre-abrir-1440.jpg`.

Nenhum código foi alterado. Os arquivos temporários de medição (`frontend/public/__rev09/`) foram apagados. O vite da
5199 foi parado e a aba do navegador fechada. As preferências que a medição gravou no navegador (`cda.painel.visao`,
`cda.selectedInstances`) foram apagadas, e o perfil voltou às chaves de antes.
