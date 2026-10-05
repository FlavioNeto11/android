# Tarefa 05: busca, filtros, ordenação e visão em tabela (relatório)

Branch `ux/05-busca-tabela` (sai de `claude/ux-portal` c2c26cb, com as tarefas 01 e 02). Data: 30/09/2026. Máquina:
central (Windows Server 2025). Modelo: Opus 5.5, esforço high.

## O que mudou

1. **Barra de listagem reutilizável** (`components/BarraListagem.tsx` + `.module.css`): busca (Esc limpa), filtros
   em chips (poucas opções, com contagem) ou em lista, ordenação, alternância Cartões/Tabela, resumo "N de M" e
   "Limpar filtros" só quando algum filtro esconde itens. É controlada: quem usa guarda o estado (na URL) e a barra
   só mostra e avisa. Tem `role="search"`, rótulos acessíveis e o modo `compacta` para colunas estreitas. Abaixo de
   560 px os botões de visão ficam só com o ícone (o nome acessível continua) e os chips quebram linha.
2. **Personas** (`features/profiles/ProfilesPage.tsx`, `ListaDePersonas.tsx`, `filtroPersonas.ts`):
   - busca por nome, nome de exibição e @, sem acento e sem caixa, com ou sem o "@";
   - situação em chips (Todas, Ativas, Precisam de atenção, Bloqueadas pela plataforma, Pausadas, Sem conta de
     cadastro), com a contagem que o chip mostraria com os outros filtros aplicados;
   - aparelho vinculado (com/sem), grupo de acesso (inclui "Sem grupo") e aplicativo (os apps dos vínculos, a lista
     só aparece quando algum vínculo tem app);
   - ordem por nome (pt-BR), situação (o que pede alguém primeiro) ou última atividade (a do trabalho, senão a
     última verificação, senão a última edição; mais recente primeiro);
   - visão em tabela com as colunas persona, conta (@), contas, aparelho, situação, grupo e ações;
   - "Selecionar todas" vale para o que o filtro mostra. Selecionadas que o filtro escondeu continuam no lote, e a
     linha diz "(N fora do filtro atual)" para ninguém agir sem ver;
   - estado vazio que diz o filtro ("Nenhuma persona bloqueada.") com "Limpar filtros".
3. **Cartão de persona de layout fixo**: as mesmas linhas em todo cartão (resumo, contas, aparelho, servidor,
   situação, grupo, linha de aviso), uma linha de texto cada, cortada com reticências e com o texto inteiro no
   `title`; ações presas ao pé. Medido: 14 cartões com a mesma altura (346 px) em 1440, 1024 e 390 px, nenhum
   transbordando. O avatar continua o `components/Avatar` (foto ou iniciais no mesmo círculo), que já era
   consistente.
4. **Estado composto**: situação e fase da sessão viram um selo só, explicado no tooltip (rótulo + porquê):
   "Ativa · app não instalado", "Ativa · sem conta", "Ativa · conectada", "Bloqueada pela plataforma" etc. Quando
   há o que resolver, o cartão ganha uma ação ("Instalar app", "Adicionar conta", "Guardar senha", "Vincular
   aparelho", "Ver conta", "Resolver") que **só navega** para a guia da persona onde se resolve (`#/personas/<id>/aparelhos` ou
   `/contas`); nada é instalado, conectado nem vinculado a partir da lista. No máximo dois selos por linha
   (situação; servidor com "mudou de servidor"/"indisponível"). "localidade não registrada" deixou de ser um selo
   sobreposto ao nome do servidor: vai no texto, com tooltip.
5. **Ações**: "Abrir" é o botão primário. "Marcar bloqueada"/"Reativar" e "Remover persona" foram para o menu "⋯"
   (`Popover` já existente: Esc fecha e devolve o foco). O vermelho de Remover mora só dentro do menu, e Remover
   continua com a confirmação de antes (`confirmed`, não o objeto).
6. **Execuções** (`features/runs/RunsPage.tsx`, `filtroExecucoes.ts`):
   - busca no texto do objetivo ou no código curto;
   - situação em chips: Em andamento (planejando, planejada, em execução, pausada, cancelando), Concluídas, Com
     pendência (concluída com pendências + precisa de informações), Falharam, Canceladas;
   - período (últimas 24 h, 7 dias, 30 dias), aparelho e servidor (estes dois continuam filtrados pela API);
   - com busca, situação ou período ligados, a tela traz o **histórico inteiro** em páginas de 200 (o teto da API):
     246 execuções = duas chamadas. As contagens dos chips só aparecem com o histórico inteiro na mão;
   - a lista desenha 50 por vez, com "Mostrar mais (N restantes)", que pede a próxima página ao servidor quando
     a tela já mostrou tudo o que tinha;
   - **título curto** por item: a primeira linha com conteúdo do objetivo, sem as aberturas repetidas
     ("Nas instâncias selecionadas,", "Objetivo:") e com o app citado ("No QA Messenger,", "Abra o Instagram e")
     separado numa etiqueta; primeira frase, até 72 caracteres, corte sem partir palavra. O objetivo inteiro fica
     no `title` do item e no detalhe. Depois vêm situação e idade (com a data completa no `title`), e na linha de
     baixo app, código e contadores.
7. **Estado na URL**, sempre por `trocarQuery(..., 'replace')`: digitar, trocar filtro, ordem ou visão substitui a
   entrada do histórico, sem empilhar (medido: `history.length` não cresce ao digitar). Recarregar reabre o mesmo
   recorte (medido com `location.reload()`).

## Parâmetros de URL (registrados no cabeçalho de `lib/rotas.ts`)

| Tela | Parâmetro | Valores | Padrão (fora do link) |
|---|---|---|---|
| Personas | `q` | texto (nome ou @) | vazio |
| Personas | `situacao` | `ativa` (inclui quem precisa de atenção), `atencao`, `bloqueada` (status `blocked`), `pausada`, `sem-conta` | todas |
| Personas | `vinculo` | `com`, `sem` | com e sem |
| Personas | `grupo` | id do grupo de acesso, ou `nenhum` | todos |
| Personas | `app` | id do app de um vínculo (`instagram`, `outlook`…) | todos |
| Personas | `ordem` | `situacao`, `atividade` | nome |
| Personas | `visao` | `tabela` | cartões |
| Execuções | `q` | texto (objetivo ou código) | vazio |
| Execuções | `status` | `andamento`, `concluida`, `pendencia`, `falha`, `cancelada` | todas |
| Execuções | `periodo` | `24h`, `7d`, `30d` | qualquer data |
| Execuções | `aparelho` | id do aparelho | todos |
| Execuções | `servidor` | id do servidor (worker) | todos |

`situacao=bloqueada` usa o mesmo critério do contador "N personas bloqueadas pela plataforma" da tarefa 02
(`status = blocked`): medido contra o backend vivo, o link abre as mesmas 5 pessoas que o contador conta. Valor
desconhecido num link vira "sem filtro" (um link velho não esvazia a lista sem dizer por quê). Em Execuções os
filtros convivem com `aba` e com o id da execução aberta (`#/execucoes/<id>?status=pendencia`), porque
`abrirExecucao`/`selectRun` já preservam a query da tela.

## Decisões e divergências

- **`vinculo`, não `aparelho`, em Personas.** Um nome de parâmetro tem um dono só (`rotas.ts`). Em Execuções
  `aparelho` é o id de um aparelho; em Personas o filtro é sim/não, então virou `vinculo=com|sem`.
- **Situação `atencao`** não estava no briefing (ativa, bloqueada, sem conta). Entrou porque "Ativa" com "app não
  instalado" é justamente o caso que o briefing quer explicado, e precisa de um recorte para ser achado. "Ativas"
  inclui quem precisa de atenção (continua ativa); "Sem conta de cadastro" é à parte. Uma persona bloqueada sem
  conta conta como bloqueada.
- **"Última atividade"**: `last_activity_at` está nulo nas 14 personas do backend vivo (30/09). A ordem cai para a
  última verificação e depois para a última edição; fica registrado que hoje ela ordena, na prática, por edição.
- **Filtro por app** usa os apps dos vínculos com aparelho (`devices[].app_id`): o `GET /personas` não traz a lista
  de contas por app, só `accounts_count`. Persona com conta num app mas sem vínculo não aparece no filtro desse app.
- **Paginação, não virtualização.** 50 itens por vez com "Mostrar mais" resolve o peso com 246 registros sem
  biblioteca nova. A busca filtra o histórico inteiro, não só o que está desenhado.
- **Bug achado e corrigido no caminho:** o store guarda só as 100 execuções mais recentes (`MAX_RUNS` em
  `store/reducer.ts`), e a lista lia do store. "Carregar mais" passava da centésima e as antigas sumiam de novo; a
  busca nunca acharia uma execução além dela. A tela agora guarda as páginas que buscou e as une com as ao vivo do
  store (`unirExecucoes`, a versão ao vivo ganha). Não mexi no store (não é da minha posse). O detalhe de uma
  execução além da centésima abre: o `RunView` usa o detalhe carregado por id (`store/live.ts` recarrega a cada troca
  de `selectedRunId`) antes do resumo do store (prova real abaixo).
- **Título "Recentes"** da coluna ficou como estava: trocar para "Histórico" obrigaria mexer em
  `app.integration.test.tsx` (tarefa 01). O subtítulo diz "N de 246 execuções".
- **Execuções sem visão em tabela.** O briefing pede tabela para Personas; em Execuções a coluna é mestre-detalhe
  de 340 px, onde a tabela não cabe. A barra é a mesma, sem o botão de visão.
- **Aparelhos (15) sem barra.** A grade de aparelhos mora em `features/devices` e `features/painel` (tarefas 03 e
  04), fora da minha posse. `BarraListagem` está pronta para ser usada lá.
- **"Ver conta", não "Conectar"**, para a sessão deslogada: o botão só leva à guia Contas e acesso, e "Conectar"
  prometeria um efeito que ele não tem. No cartão, a linha "Grupo de acesso" virou "Grupo" (quebrava em duas linhas
  a 1440 px), como na tabela.
- **Lint**: o repositório não tem ESLint configurado (nem script `lint` no `package.json`); os comentários
  `eslint-disable` de `RunsPage.tsx` já existiam. Valem o typecheck e a suíte.
- **Co-Authored-By**: usei `Claude Sonnet 5.5`, como o orquestrador pediu, embora o trabalho seja do Opus 5.5.

## Toques em arquivo alheio

- `frontend/src/lib/rotas.ts` (tarefa 01): só o comentário do cabeçalho, com os parâmetros e códigos acima.
  Nenhuma assinatura mudou.

## Arquivos alterados

- Novos: `frontend/src/components/BarraListagem.tsx`, `BarraListagem.module.css`,
  `frontend/src/features/profiles/ListaDePersonas.tsx`, `filtroPersonas.ts`, `filtroPersonas.test.ts`,
  `frontend/src/features/runs/filtroExecucoes.ts`, `filtroExecucoes.test.ts`.
- Alterados: `frontend/src/features/profiles/ProfilesPage.tsx` (o cartão antigo saiu para `ListaDePersonas.tsx`),
  `Profiles.module.css`, `ProfilesPage.test.tsx`; `frontend/src/features/runs/RunsPage.tsx`, `Runs.module.css`,
  `RunsPage.test.tsx`; `frontend/src/lib/rotas.ts` (comentário).

## Provas

**simulated** (vitest, backend falso):
- `npm run typecheck` verde. `npm test` inteiro: **79 arquivos, 929 testes, todos verdes** (30/09, 20:20Z, e de novo às 20:25Z depois dos últimos ajustes de rótulo).
- `src/features/profiles/filtroPersonas.test.ts` (8): situação num código só; estado composto "Ativa · app não
  instalado" com ação que só aponta a guia; `situacao=bloqueada` filtra as bloqueadas; valor desconhecido não
  esvazia; busca pelo @ com e sem "@" e sem acento; filtros combinados (situação, vínculo, grupo, app, busca);
  ordem por nome, situação e atividade sem mudar a lista original; contagens dos chips; URL curta (padrão fora).
- `src/features/runs/filtroExecucoes.test.ts` (10): título curto (app separado, aberturas repetidas, sem app, corte
  sem partir palavra, nunca vazio, começos distintos); grupos de status; leitura da URL; busca por objetivo e
  código combinada com status e período; contagens; união do histórico paginado com o ao vivo.
- `src/features/profiles/ProfilesPage.test.tsx::busca, filtros e visão em tabela` (7): o link `situacao=bloqueada`
  abre filtrado e "Limpar filtros" volta a `#/personas`; buscar `@bruno` grava `q` sem crescer o histórico e o vazio
  diz o filtro; filtros combinados vêm do link ao montar; cartões → tabela mantém a seleção e a tabela tem as
  colunas pedidas; "Selecionar todas" só nas visíveis e aviso de seleção fora do filtro; estado composto e a ação
  "Instalar app" só navega (nenhum pedido que não seja GET); "Marcar bloqueada" só no menu "⋯". Os dois testes de
  "remover persona" passaram a abrir o menu antes; o de confirmar continua apagando pela rota certa.
- `src/features/runs/RunsPage.test.tsx` (+4): 50 por vez com título curto e "Mostrar mais" paginando; buscar
  `nasa` traz o histórico em páginas de 200 e acha a 201ª execução (além do `MAX_RUNS`), com `q` no link e sem
  crescer o histórico; `status=falha&periodo=24h` vindos do link filtram certo e "Limpar filtros" limpa; vazio com
  "Limpar filtros".

**real** (30/09, entre 20:15Z e 20:20Z, máquina central, código do commit `8e1b399` mais os ajustes de `bd03042`;
vite na 5195 com `VITE_API_TARGET=http://127.0.0.1:8000`, painel do Browser da IDE, aba própria; só leitura):
- 1440×900: `#/personas?situacao=bloqueada` abriu 5 personas (Beatriz, Felipe, Juliana, Mariana, Thiago), as mesmas
  que o contador da tarefa 02 conta. Sem filtro, 14 cartões com a mesma altura, nenhum selo cortado depois do
  ajuste (antes do ajuste "Bloqueada pela platafo…" cortava; medido e corrigido), `scrollWidth` 1440. Marquei Bruno,
  troquei para Tabela: `#/personas?visao=tabela`, 14 linhas, Bruno continuou marcado. Digitei `@lucas` na busca:
  sobrou só Tadeu Quintela na hora, `#/personas?q=%40tadeu&visao=tabela`, `history.length` igual ao de antes, e o
  aviso "(1 fora do filtro atual)". `location.reload()` (tipo `reload`) reabriu com a busca `@lucas` e a tabela.
- Menu "⋯" de Sueli Barreto (bloqueada): itens "Reativar" e "Remover persona". "Remover persona" abriu o diálogo
  "Remover Sueli Barreto?" com "Voltar" e "Remover"; cliquei **Voltar**, o diálogo fechou e Sueli continuou na
  lista. Nada foi confirmado, marcado nem reativado.
- Execuções a 1440: `#/execucoes?status=pendencia` trouxe o histórico inteiro (subtítulo "115 de 246 execuções";
  chips Todas 246, Em andamento 3, Concluídas 103, Com pendência 115, Falharam 7, Canceladas 18), 50 itens
  desenhados e "Mostrar mais (65 restantes)". Títulos curtos começando pelo verbo ("Leia o nome do primeiro
  contato…", "Com uma das personas ativas, curtir…", "Envie a mensagem para o contato"). Os três selects da barra
  cabem sem corte.
- 1024×768: Personas com 14 cartões iguais (346 px de altura, 371 px de largura), `scrollWidth` 1024. Execuções
  empilhadas com `q=nasa`: 27 de 246, a lista com 289 px de altura (antes do ajuste de 45vh para 65vh sobrava cerca de
  um item e meio à vista), `scrollWidth` 1024.
- 390×844: Personas com a barra em coluna, botões de visão só com ícone, 14 cartões iguais (346 px), nenhum
  transbordando, `scrollWidth` 390; tabela rola dentro da própria caixa, não a página. Execuções com
  `status=falha`: 7 de 246, o chip ligado "Falharam" visível (antes do ajuste os chips rolavam de lado e ele
  ficava fora da tela), `scrollWidth` 390.
- Observado e alheio: pela porta do vite o canal ao vivo cai com 1006 (faixa "Desconectado do backend"), já
  registrado na tarefa 01. A 390 px o saldo das contas de IA se sobrepõe no topo (tarefa 01/02).

- Execução além da centésima (30/09, cerca de 20:26Z, 1440 px): `#/execucoes?q=762d0d` achou a 221ª execução do
  histórico ("1 de 246 execuções", título "Entre na conversa com o contato de teste identificado como QA-001 e…"),
  e o clique abriu o detalhe completo dela (`#/execucoes/r-20260917175222-762d0d?q=762d0d`, "Concluída", 3 de 3
  aparelhos com sucesso, guias com os eventos e evidências).

**not_run**: 768 e 1920 px. Leitor de tela real. A ação "Instalar app" no navegador (a lógica foi provada no teste;
não cliquei para não abrir nada além do necessário).

## O que ficou de fora e pendências

- Barra de listagem na grade de Aparelhos (Painel): posse das tarefas 03/04.
- `MAX_RUNS = 100` no store continua; a tela de Execuções contorna guardando as próprias páginas. Se outra tela
  precisar do histórico além de 100, o limite do store é o lugar (tarefa de quem cuida de `store/`).
- A API não filtra por texto, situação nem período: com o histórico crescendo muito além de algumas centenas, a
  busca deveria ir ao servidor (`GET /runs?q=&status=&since=`). Hoje, com 246, são duas chamadas.
- "Última atividade" depende de `last_activity_at`, que o backend não preenche para nenhuma persona hoje.
