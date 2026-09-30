# Tarefa 06: Aplicativos x Configuração e caixa única de pendências (relatório)

Branch `ux/06-apps-pendencias` (sai de `claude/ux-portal` `16a45ce`, com as tarefas 01, 02, 03 e 05). Data: 30/09/2026.
Máquina: central (Windows Server 2025). Modelo: Sonnet 5.5, esforço medium.

## O que mudou

1. **Um lugar para cadastrar app novo: Aplicativos > Loja.** O "Novo aplicativo" e o "Cadastrar aplicativo" (estado vazio) de
   Configuração > Aplicativos saíram. A seção agora tem o link "Gerenciar em Aplicativos" (`#/aplicativos`), e o estado
   vazio de "Por app" diz onde cadastrar ("na aba Loja, com o botão Novo aplicativo"). Medido no navegador: um único
   botão "Novo aplicativo" em todo o portal (na Loja).
2. **Configuração > Aplicativos continua editando o que só existe lá**: as dicas de navegação e os seletores conhecidos
   (o botão passou a se chamar "Editar dicas e seletores") e o "Excluir" com a mesma confirmação. Ver "Divergências".
3. **"Proxy (legado)" deixou de ser aba.** Virou um trecho recolhido no fim da aba Rede, "Proxy global (antigo)", com
   selo "descontinuado" e o texto de quando ainda serve (só para tirar um proxy antigo aplicado). O link antigo
   `#/aplicativos?aba=proxy` abre a Rede com o trecho já aberto. Abas agora: Loja, Por app, Versões e instalação, Rede.
4. **Vocabulário da loja com explicação acessível** (`features/apps/glossario.tsx`):
   - `Termo`: sublinhado pontilhado, focável (`tabIndex=0`), abre a definição por foco do teclado e por hover, fecha com
     Esc (reusa o `Tooltip` do sistema, com `role="tooltip"` e `aria-describedby`). Usado nos selos do cabeçalho do app
     na loja (promovida, nenhuma versão promovida, IA com catálogo de ações ou caminho livre).
   - `LegendaDaLoja`: recolhível ("O que significam os selos da loja"), nas abas Loja e Versões, com os dez termos
     (promovida, sem versão promovida, pendente, fora do catálogo, atualização para N, em prova/canário, nunca provada,
     substituída, em quarentena, catálogo de ações).
   - Selos dentro de botões (cartões da Loja, que não podem ter gatilho focável aninhado) ganharam `title` com a
     definição, e a legenda cobre o teclado.
   - Estado vazio com ação: "Sem versão promovida: ainda não há o que distribuir", com o botão "Ir para as versões"
     (rola até a lista de versões do app, onde ficam Provar e Promover).
5. **Caixa única de Pendências** (`#/pendencias`, nova tela e novo item "Pendências" no menu, com contador):
   - Três origens, **uma linha por decisão**: item da fila "Para aprovar" do Aprendizado; aprovação pendente de persona
     (`GET /approvals?status=pending` sem filtro de persona); execução com objetivos aguardando você ou incertos (uma linha
     por execução, o número de objetivos vai no texto).
   - Cada linha: origem (selo), idade ("esperando há 2 d", data completa no `title`), título, detalhe e botão primário
     (Revisar, Decidir, Abrir execução) que **só leva** à tela onde se decide (`#/aprendizado?aba=aprovar`,
     `#/personas/<id>/aprovacoes`, `#/execucoes/<id>`). A caixa não aprova nem recusa nada: decidir sem a evidência ao lado
     é o erro que a aprovação existe para evitar, e isso mantém a regra "nunca disparar aprovar" trivial. Filtro por origem
     em chips, guardado no link (`?origem=`).
   - **O contador do menu é o tamanho da lista**: a mesma função (`features/pendencias/modelo.ts::montarPendencias`)
     alimenta a página e o número do menu (`usePendencias().total`).
   - Mais antigas primeiro. Leitura do servidor a cada minuto, só com sessão e aba visível (mesmo padrão do Aprendizado), e
     relida depois de cada decisão no Aprendizado e na aba Aprovações da persona. Falha de leitura não zera a lista e
     aparece como aviso "a lista pode estar incompleta".
   - As telas de origem apontam para ela: Aprendizado > Para aprovar ("Ver todas as suas pendências") e a aba Aprovações
     da persona ("Ver todas as pendências").
6. **Aprendizado em linguagem simples.** O lead da página caiu para uma frase. "Para aprovar" tem uma frase e "Saiba
   mais" (recolhido). O aviso laranja de "Revisar" virou uma linha com "Saiba mais". "Rebaixar" virou "Desligar" (a
   transição é `published → disabled`; "voltar a pedir aprovação" seria impreciso: o que acontece é que a automação volta
   a pedir a IA nesses passos, e isso está no "Saiba mais"). Botões: "Desligar", "Confirmar desligamento", "Desligar
   selecionados".
7. **Guias na URL.** Configuração (`?aba=aplicativos|instancias|ia|fluxos|limites`, sem `aba` = Aplicativos, então o link
   antigo `#/configuracao` abre a mesma guia) e Aprendizado (`?aba=aprovar|aprendido|falhas|sinais`, sem `aba` = Para
   aprovar) leem e gravam a guia com `rota.query.aba` e `trocarQuery({ aba }, 'replace')`. A chave de `localStorage`
   (`settingsSection`, `aprendizado.aba`) deixou de ser lida; os dois chamadores que a escreviam para abrir Configuração
   numa guia (`ItemDoLivro.abrirHabilidades`, `infra/CriarAparelho.irParaLimites`) agora navegam para o link.

## Decisões e divergências do briefing (segui o código)

- **Editar em Configuração foi mantido.** O briefing sugere Configuração somente leitura. Mas dicas de navegação e
  seletores só são editáveis ali (Aplicativos > Por app mostra uso e custo, não edita cadastro). Remover seria tirar
  funcionalidade, contra o item 6 do briefing. Fica registrado como pendência: mover o editor para a página do app em
  Aplicativos e deixar Configuração só com o link.
- **"Promover versão" direto do estado vazio não existe**: pelo código, promover exige antes provar a versão (canário).
  O estado vazio leva à lista de versões, onde ficam Provar e Promover. Não criei um segundo caminho de promoção.
- **O "aguardando você" do topo continua contando objetivos** (definição da tarefa 02, que não mexi), e a caixa conta
  execuções. Os dois leem o mesmo seletor (`execucoesAguardando`, que extraí de `objetivosAguardando`), e a página
  explica a diferença em "Como o número é contado". Não troquei o chip do topo para não reabrir a definição da tarefa 02.
- **Aprovações de persona**: usa o limite padrão da API (50). Com mais de 50 pendentes a caixa mostraria 50.
- **Legado "Revisar" do Aprendizado não entra na caixa**: são itens antigos já ativos (não esperam decisão para
  funcionar) e a contagem "Para aprovar" do backend também não os conta. Continuam na aba Para aprovar.
- **`?aba=aplicativos` em Configuração** é o nome no link; o id interno continua `apps`.

## Toques em arquivo alheio

- `lib/rotas.ts` (tarefa 01): `'pendencias'` em `TELAS` e documentação dos parâmetros `aba`.
- `App.tsx` (01): título e ramo de renderização da nova tela.
- `features/topbar/MenuLateral.tsx` (01): item "Pendências" com contador; a releitura do Aprendizado passou a ser a da
  caixa (uma chamada alimenta o selo do Aprendizado e o da Pendências).
- `store/metricas.ts` (02): `execucoesAguardando` (nova) e `objetivosAguardando` derivada dela, com o mesmo resultado.
- `features/loja/LojaPage.tsx`, `features/loja/AppNaLoja.tsx` (fora do mapa de posse): `title`/`Termo` nos selos,
  aviso "Sem versão promovida" com botão e `id` na lista de versões.
- `features/profiles/GuiaAprovacoes.tsx` (05): link "Ver todas as pendências" e releitura da caixa após decidir.
- `features/infra/CriarAparelho.tsx` (02): "Ir para Limites" navega com `?aba=limites`.
- Testes: `features/topbar/TopBar.test.tsx` e `app.integration.test.tsx` (nove seções no menu; o trecho que criava app por
  Configuração agora edita um app existente e confere que não há "Novo aplicativo").

## Arquivos

- Novos: `features/pendencias/{modelo.ts,store.ts,usePendencias.ts,PendenciasPage.tsx,Pendencias.module.css,PendenciasPage.test.tsx}`,
  `features/apps/glossario.tsx`.
- Alterados (posse): `features/apps/{AppsPage.tsx,Apps.module.css,AppsPage.test.tsx}`,
  `features/settings/{SettingsPage.tsx,AppsSection.tsx,Settings.module.css,SettingsPage.test.tsx}`,
  `features/aprendizado/{AprendizadoPage.tsx,ParaAprovarTab.tsx,ItemDoLivro.tsx,SinaisTab.tsx,AprendizadoPage.test.tsx}`.

## Provas

**simulated** (vitest, backend falso):
- `npm run typecheck` verde. Testes novos e alterados: `features/pendencias/PendenciasPage.test.tsx` (5: linhas por
  origem e contador do menu igual ao tamanho da lista; ação que só navega, sem POST, e filtro na URL; caixa vazia e leitura
  que falha; montagem pura com aprovação decidida fora, execução com 3 objetivos em uma linha, fontes nulas),
  `features/apps/AppsPage.test.tsx` (4: abas sem Proxy, legenda com os termos, `?aba=proxy` abre a Rede com o trecho
  aberto, `Termo` abre por foco e fecha com Esc), `features/settings/SettingsPage.test.tsx` (Gerenciar em Aplicativos e
  sem "Novo aplicativo"; a guia vai e volta pelo link), `AprendizadoPage.test.tsx` (Desligar; Configuração pelo link),
  `TopBar.test.tsx` e `app.integration.test.tsx` (nove seções).
- `npm test` inteiro: ver a linha "Suíte" ao fim deste relatório.

**real** (30/09/2026, máquina central, vite na 5196 com `VITE_API_TARGET=http://127.0.0.1:8000`, somente leitura, sem
clicar em nada com efeito; o painel do navegador estava com o canal ao vivo caído, código 1006, como nas tarefas 01 e 02):
- 1440 px: `#/pendencias` com 4 linhas (as 4 execuções com objetivo aguardando), selo do menu "4 (4 esperando você)" e
  chip "Todas (4)"; Aprendizado (0) e Persona (0) no backend de hoje; `scrollWidth` igual à janela.
- `#/configuracao` abre Aplicativos com o link "Gerenciar em Aplicativos" e sem "Novo aplicativo";
  `#/configuracao?aba=fluxos` abre Fluxos e receitas; `#/aprendizado?aba=falhas` abre "O que mais falha";
  `#/aplicativos?aba=proxy` abre a Rede com o trecho "Proxy global (antigo) descontinuado" aberto; abas de Aplicativos
  sem "Proxy (legado)". Um único botão "Novo aplicativo" em todo o portal, na Loja.
- 1024 px e 390 px: `scrollWidth` igual à janela em Pendências, Aplicativos, Configuração e Aprendizado (as faixas de abas
  rolam dentro do próprio contêiner, como antes). Em 1024, botão da linha de pendência dentro da janela (direita em 973).
- Com o login do navegador da IDE, a caixa leu só a origem Execução com dados; as leituras `/aprendizado/pendentes` e
  `/approvals` não puderam ser conferidas com itens reais.

**not_run**: aprovar, recusar, desligar, promover ou cadastrar de verdade (proibido); caixa com itens de Aprendizado e de
Persona contra o backend vivo (não havia nenhum pendente); tooltip por foco no navegador real (provado só em jsdom);
leitor de tela; 768 e 1920 px.

## O que ficou de fora e pendências

- Mover o editor de dicas e seletores para a página do app em Aplicativos e deixar Configuração > Aplicativos só com o link.
- A aba Execuções poderia apontar para a caixa ("Com pendência" nos filtros da tarefa 05): não mexi em `features/runs`.
- O chip "aguardando você" do topo e a linha "objetivos aguardando você" do semáforo de saúde continuam em objetivos.
- Termos da loja em `features/releases/ReleasesPage.tsx` (rótulos de canal e status) ficaram só com a legenda, sem
  `Termo`/`title` por selo (não é da posse desta tarefa).
- Tarefa 08 (varredura de textos): "D1" e "desvio consciente do ADR-054" ainda aparecem em textos do Aprendizado.
- `localStorage` antigo (`cda.settingsSection`, `cda.aprendizado.aba`) fica órfão; some sozinho com o tempo.

**Suíte** (simulated, 30/09, ~20:39Z, commit desta entrega): `npm run typecheck` verde e `npm test` inteiro com 82 arquivos e
958 testes, todos verdes.
