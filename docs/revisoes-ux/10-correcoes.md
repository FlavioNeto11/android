# Tarefa 10: correções dos achados da revisão final (fase A)

Branch `ux/10-correcoes`, que sai de `ux/09-revisao-final` `6fb6749` (que contém `claude/ux-portal` `5da4cfc`, as
tarefas 01 a 08, e o relatório `revisao-final.md`). Data: 30/09/2026. Máquina: central (Windows Server 2025). Esforço
pedido: high. Fonte dos achados: `docs/revisoes-ux/revisao-final.md` (RF-01 a RF-29).

Um commit pequeno por achado, com teste vitest que falhava antes e passa depois quando fazia sentido. Nenhuma ação
com efeito real foi disparada: tudo foi validado por teste (backend falso) e por leitura de código.

## Resultado por achado

| Achado | Gravidade | Situação | Commit | Prova |
|---|---|---|---|---|
| RF-01 seleção e barra agindo fora do filtro | alta | **corrigido** | `35dd17f` | simulated |
| RF-02 linha da Lista tratada como "clique fora" do Foco | alta | **corrigido** | `8cf8cc8` | simulated |
| RF-03 caixa de Pendências sem as sessões que pedem pessoa | média | **corrigido** (sem endpoint novo) | `95c9703` | simulated |
| RF-04 quatro definições de "aguardando você" | média | não feito: decisão do dono | — | — |
| RF-05 contador de execuções fora da fonte única | média | **corrigido** | `8d8427e` | simulated |
| RF-06 dois formatadores de tempo relativo | média | **corrigido** | `4566393` | simulated |
| RF-07 termos em inglês e jargão visíveis | média | **corrigido**, menos `PlanTab.tsx:137` (ver abaixo) | `b84e1c1` | simulated |
| RF-08 visão e filtros com regras diferentes (Painel × Personas) | média | não feito: decisão de produto | — | — |
| RF-09 título da execução em Pendências | média | **corrigido** | `3fd3ce3` | simulated |
| RF-10 verificador de acessibilidade não alcança as telas | média | **documentado** (sem login implementado) | `5e82aa1` | — |
| RF-13 nome da barra × contador | baixa | **corrigido junto com RF-01** | `35dd17f` | simulated |
| RF-20 dica da idade em ISO cru | baixa | **corrigido** | `1892260` | simulated (typecheck) |
| RF-24 "Persona: Nome (@x) · persona Y" | baixa | **corrigido** | `1892260` | simulated |
| RF-29 comentário `<pacote>` em `lib/rotas.ts` | baixa | **corrigido** | `1892260` | — |
| RF-11, RF-12, RF-14 a RF-19, RF-21 a RF-23, RF-25 a RF-28 | baixa | não feitos (motivo por item abaixo) | — | — |

## O que mudou e por quê

### RF-01: a ação em lote vale só para o que está à vista (`35dd17f`)

Comportamento escolhido, o mais seguro dos que o briefing permitia:

- **"Selecionar todos"** (antes "Selecionar todas") seleciona só os aparelhos de tarefa visíveis no filtro e
  **substitui** a seleção. Depois do clique, nada escondido continua marcado. O botão fica desabilitado quando todos os
  visíveis já estão marcados e não há marcado escondido.
- A **barra recebe apenas selecionados ∩ visíveis** (`idsDaAcao`). `selected`, `hasAbsent` e `hasHibernated` saem
  desse mesmo conjunto, para um aparelho escondido não mudar os verbos que a barra oferece. O Shift+clique também usa
  a ordem visível.
- O marcado escondido **não é podado em silêncio**. Ele continua marcado, é contado à parte com o mesmo texto de
  Personas, "(N fora do filtro atual)", e **nunca** vai para `runBulkAction`.
- Se nenhum marcado estiver à vista, a barra mostra só o aviso ("Nenhum aparelho marcado aparece neste filtro. As
  ações valem só para o que está à vista.") e "Limpar seleção", sem nenhum botão de ação e sem `role="toolbar"`.
- O "0 de N selecionados" do cabeçalho passa a contar sobre os visíveis.

Por que não podar a seleção: ela também chega pelos contadores por estado ("3 online") e pelo `localStorage`
(`selectedInstances`). Podar faria a marcação sumir sem explicação ao trocar de filtro. Tratar a interseção como
fronteira de segurança deixa esses caminhos inertes, e o aviso diz o que ficou de fora. Os contadores por estado e a
persistência continuam iguais, de propósito.

De quebra, o `aria-label` da barra ("Ação em N aparelhos") passa a usar o mesmo conjunto do texto visível, o que
**resolve RF-13**.

Testes novos em `features/devices/DeviceGrid.test.tsx`, os dois RF-01, que falhavam antes:

- com `?estado=stopped` e `android-01` (online) marcado, a barra mostra "0 selecionados (1 fora do filtro atual)", sem
  toolbar e sem Iniciar, Parar, Reiniciar ou "Mais ações";
- "Selecionar todos" deixa exatamente `android-02`, `android-03` e `android-09`, e a toolbar se chama "Ação em 3
  aparelhos";
- com a seleção lembrada `['android-01', 'android-02']`, a toolbar é "Ação em 1 aparelho", com "(1 fora do filtro
  atual)".

### RF-02: a linha da Lista vale o mesmo que o cartão (`8cf8cc8`)

`Drawer.tsx` passa a exportar `MARCADORES_DE_APARELHO = ['data-instance-card', 'data-instance-row']` e
`seletorDoAparelho(id, dentro?)`. Uma lista só atende as três leituras:

- o "clique fora" do drawer, que ignora cartão **e** linha;
- a devolução do foco (`FocusPanel.tsx`, botão "Abrir" do cartão ou da linha);
- a rolagem até o aparelho do `?foco=` (`App.tsx`).

No teste do "clique fora" (`Drawer.test.tsx`) entraram uma linha com o "Abrir" e uma caixa de seleção. Conferi que
esse caso **falhava** com o seletor antigo e passa com o novo. Há também um teste novo para o seletor de devolução do
foco, no cartão e na linha.

### RF-05: execuções em andamento na fonte única (`8d8427e`)

`store/metricas.ts` ganha `execucoesEmAndamento(runs)` e `useExecucoesEmAndamento()`, com a regra do chip "Em
andamento" (`grupoDoStatus(s) === 'andamento'`, que inclui `planned`), e ganha também `DESTINO_EM_ANDAMENTO`. O
contador do topo usa os dois, e o clique abre `#/execucoes?status=andamento`. O texto de ajuda passou a citar
"planejadas". O teste está em `store/metricas.test.ts` e compara com `grupoDoStatus`, com um caso `planned`.

### RF-06: um só "há X" (`4566393`)

`tempoRelativo` e `duracaoHumana` foram para `lib/time.ts`, o módulo do relógio, e `lib/rotulos.ts` volta a ser só
tradução. `formatAgo` e `formatAgoCoarse` foram **removidos**, e as cerca de 20 chamadas usam `tempoRelativo`. Isso
inclui a legenda do Foco (`Screen.tsx`), Pendências, Execuções, Infraestrutura, Aplicativos, as guias da persona e a
faixa de reconexão. A `idade()` da trilha de comando também passa por ele. O cartão e o Foco mostram agora o mesmo
texto ("há 1 min", não "há 1 min 14 s").

Os testes de tempo relativo foram para `lib/time.test.ts`. A expectativa antiga de `formatAgo` mudou **de
propósito**, porque é a mudança de comportamento pedida, e não um teste enfraquecido. `formatSpan` e `formatDuration`
ficam, porque são durações e não "há X".

### RF-07: termos visíveis (`b84e1c1`)

Mudou só texto exibido. Chaves, rotas e valores de comparação ficaram: a aba continua `?aba=instancias`, e o
`localStorage` e os ids das abas não mudaram.

| Antes | Depois | Onde |
|---|---|---|
| `perfil(is)`, "vários perfis", "Nenhum perfil…" | persona(s), com `plural()` | `PolicyGroups.tsx` (Grupos de acesso), `GuiaConfiguracoes.tsx` |
| "Instâncias e contas", "Configuração → Instâncias" | "Aparelhos e contas" | `SettingsPage.tsx`, `InfraPage.tsx`, `CriarAparelho.tsx` |
| "N instância(s)" | "N aparelho(s)" | `AppsSection.tsx` |
| "as contas dos perfis" | "as contas das personas" | `AppsPage.tsx` |
| "este e N workers", "servidor worker", "Um worker é…" | "este e N remotos", "servidor remoto" | `InfraPage.tsx` |
| "frame" (último frame, Atualizar frame, sem frame, Sem frame novo…) | "imagem" | `Screen.tsx`, `DeviceCard.tsx`, `FocusPanel.tsx`, `deviceState.ts`, `streamState.ts`, `lib/status.ts`, `api/client.ts`, `settings/validation.ts` |
| "backend", "snapshot", "host", "(WebSocket)" | "servidor", "servidor central", "máquina" | `App.tsx`, `DeviceGrid.tsx`, `FocusPanel.tsx`, `DiagnosticsPage.tsx`, `CommandPanel.tsx`, `LoginPage.tsx`, `TopBar.tsx` |
| "Screenshots e textos…" | "Capturas e textos…" | `TopBar.tsx` |
| aba "Logs" | "Registros" | `InfraPage.tsx` |
| coluna "Status" | "Situação" | `FlowsRecipesSection.tsx` |
| "a partir de um prompt", "num prompt" | "a partir de uma descrição", "em poucas palavras" | `ProfilesPage.tsx`, `NovaPersona.tsx` |
| "Cards" | "Cartões" | `DeviceGrid.tsx` |
| "sem AVD" | "sem emulador" | `store/metricas.ts` |
| "Selecionar todas", "Paradas (n)", "parada(s)" | "Selecionar todos", "Parados (n)", "parado(s)"; a loja vira "aparelho-loja parado" | `DeviceGrid.tsx`, `store/metricas.ts` |
| toast do lote "instâncias aceitas / rejeitada(s)" | "aparelhos aceitos / recusado(s)" | `features/devices/actions.ts` (não estava na lista do RF-07) |

Os testes que conferiam os textos antigos foram atualizados para os novos. Nenhuma asserção saiu.

**Decisão registrada.** Os membros dos Grupos de acesso aparecem por `@conta`, mas a seção mora na tela Personas e o
grupo se aplica à persona. Por isso ficou "persona(s)". "Perfil" continua só onde é o perfil de uma conta de rede
social.

**Não feito no RF-07.** `PlanTab.tsx:137` mostra as chaves do plano cruas (`username`, `message`…). Traduzir exige um
mapa de nomes de parâmetro, que é decisão de conteúdo e não troca de texto, e fica registrado. `APK`, `AVD` em
diálogos técnicos e "token de inscrição" ficaram, porque são nomes do domínio e não estavam na lista. Também ficaram
os motivos de verbo desabilitado com "instância" em `features/devices/deviceState.ts` ("Falha na instância",
"Disponível com a instância parada…", "Exige a instância online."). Não estavam na lista do RF-07, e
`deviceState.test.ts:247` confere um deles. Fica para uma próxima varredura de textos.

### RF-09: título curto em Pendências (`3fd3ce3`)

`pendenciasDeExecucoes` usa `tituloCurto(run.command)` de `features/runs/filtroExecucoes`, e o app citado vai para
o detalhe da linha, como na lista de Execuções. O teste novo (`PendenciasPage.test.tsx`, RF-09) falhava antes e
recebia "Nas instâncias selecionadas, no QA Messenger, leia…".

### RF-03: sessões que pedem pessoa na caixa única (`95c9703`)

Os dados já estavam numa API em uso, `GET /personas`, lida pela tela Personas e por `usePersonas`. **Nenhum endpoint
foi criado.** O que mudou:

- `pendencias/store.ts` lê `api.listPersonas()` junto com as outras fontes, no mesmo `allSettled`. Uma falha só marca
  `falhou`.
- `modelo.ts` ganha a origem **"Intervenção"** (`pendenciasDeSessoes`): uma linha por persona com conta e sessão em
  `wrong_account` ou `needs_person`. É o mesmo filtro da fila "Aguardando intervenção", que também
  exige `username`. A ação "Resolver" leva a `#/personas`, onde está "Assumir controle". A caixa não assume nada.
- `PRECISA_DE_PESSOA` agora tem um lugar só (`pendencias/modelo.ts`), lido pela fila de Personas e pela caixa.
- A releitura também dispara a cada `session.needs_person` (`needsPersonEpoch`), além do minuto de sempre.
- O contador do menu continua sendo `itens.length` da mesma função: **menu = lista**, e o teste novo confere "5
  esperando você" com 5 linhas.
- `PendenciasPage` deixou de chamar `usePersonas()` à parte e lê os nomes das personas que o store já tem. Uma
  leitura, não duas.

Custo: mais um `GET /personas` por minuto, feito pelo menu enquanto há sessão e a aba está visível. É leitura.

### RF-10: verificador documentado (`5e82aa1`)

Foi mexido só o cabeçalho de `scripts/ui-verificar.mjs`. Ele agora diz duas coisas:

- o `--url` padrão (`http://127.0.0.1:8000`) serve o `dist` da **main** implantada, e não o branch. Para medir um
  branch é preciso subir o dev server dele e passar `--url`;
- o script não faz login e aborta todo POST. Sem sessão, todas as células caem na tela de entrada.

O login **não** foi implementado. As saídas estão listadas no próprio cabeçalho e no bloqueio 1 da FASE B da revisão
final: o navegador da IDE com sessão e axe injetado, ou um backend simulado local.

### Baixa gravidade aplicados (`1892260`)

- RF-20: a dica da idade em Pendências usa `formatDateTime` (dd/mm hh:mm:ss) no lugar do ISO cru.
- RF-24: `OperationalContextCard` mostra "Persona: <nome da persona> (@conta)", sem "· persona Y".
- RF-29: o comentário de `lib/rotas.ts` passa a dizer `<app_id>` e cita as intervenções em `#/pendencias`.

## Não feitos (decisão do dono, de produto ou fora do escopo seguro)

- **RF-04**: quatro definições de "aguardando você". É decisão do dono qual número é "a pendência", ou se são dois.
  Não mexi em contador nem em destino.
- **RF-08**: Cards/Lista no navegador × Cartões/Tabela no link, e filtros que se perdem ao sair pelo menu. É decisão
  de produto (regra de persistência). O rótulo "Cards" virou "Cartões" pelo RF-07, mas a regra de persistência ficou.
- **RF-11** (`aria-modal` com o fora clicável): escolha da 03, pede decisão do dono.
- **RF-12** (a grade desce cerca de 52 px ao marcar o primeiro aparelho): é mudança de layout que precisa de medida
  no navegador. Não medi.
- **RF-14** (código morto): `countByState` e `STATE_SUMMARY_LABEL` têm teste próprio, e apagar significa apagar
  teste. `viewFromHash` e `rotuloConhecidoDoComando` são exportados e usados em teste. O ramo "Novo aplicativo" de
  `AppsSection` e os tokens e classes sem uso também ficaram. Nenhum deles é troca claramente segura dentro desta
  tarefa, e ficam listados para uma limpeza à parte.
- **RF-15** (links sem `?foco=`) e **RF-18** (ícone `Hand` com três sentidos): regra de navegação e de vocabulário
  visual, decisão de produto.
- **RF-16** (gestão de app ainda em Configuração) e **RF-17** (etapas 1 → 2 → 3): decisão de produto.
- **RF-19** (chips com `role="radio"` sem setas): pede escolher um padrão (`aria-pressed` como a `BarraListagem`, ou
  radiogroup completo) e testar pelo teclado.
- **RF-21** (conta em R$ mostrada como US$): regra de câmbio, anterior à revisão, e pede decisão.
- **RF-22** (selo do Aprendizado `total` × `itens.length`): mudar a fonte do selo mexe em paginação da API.
- **RF-23** (`reconnecting` já é Crítico): decisão sobre o nível do semáforo.
- **RF-25**: não há o que corrigir (a expressão da 08 é inofensiva). É só registro.
- **RF-26** (Tab escapa da gaveta do menu) e **RF-27** (violações do axe em `region`, `heading-order` e
  `aria-allowed-role`): pedem medição de teclado e de axe, que é a FASE B.
- **RF-28** (texto de 13 px com `opacity: 0.7` na tela de entrada): mudança de contraste que precisa do axe para
  confirmar.

## Divergências do briefing

- Pela regra de posse, esta tarefa pode tocar onde o achado exigir. Os arquivos fora das posses originais estão
  listados abaixo. Não é "toque em arquivo alheio" no sentido das tarefas 01 a 08.
- O briefing pede "Selecionar todas". Mantive o comportamento pedido, mas o rótulo virou "Selecionar todos", porque o
  RF-07 aponta o feminino para aparelhos.
- `ROTULO_DO_ESTADO.stopped` foi para o masculino ("parado"), por coerência com "Parados". A loja passou a se chamar
  "aparelho-loja parado" para não ficar "loja parado".

## Provas

- **real**: nenhuma.
- **simulated**:
  - `npm run typecheck`: verde, rodado depois da última mudança de código (`1892260`).
  - `npm test`: **84 arquivos e 1000 testes, todos passando** (vitest 5.0.1, 30/09, cerca de 20:29 no horário da
    máquina). Antes, a 09 citava 991 como número dos autores, sem reexecutar. Os testes novos são os deste branch.
  - Os testes novos ou alterados que provam cada achado:
    - `features/devices/DeviceGrid.test.tsx::RF-01…` (2 testes)
    - `features/focus/Drawer.test.tsx::clicar na página fecha…`, com linha e caixa
    - `features/focus/Drawer.test.tsx::RF-02: o seletor de devolução…`
    - `store/metricas.test.ts::RF-05…`
    - `lib/time.test.ts::formata "há X"…` e `lib/time.test.ts::tempoRelativo…`
    - `features/pendencias/PendenciasPage.test.tsx::RF-09…`
    - `features/pendencias/PendenciasPage.test.tsx::RF-03…` (2 testes)
  - `app.integration.test.tsx` rodou verde depois do RF-01 e do RF-03: a seleção, a barra e o lote seguem iguais sem
    filtro.
- **not_run**: verificação visual a 1440, 1024 e 390 px. O dev server do branch (porta 5198) é outra origem e cai na
  tela de entrada. Entrar exige `POST` de sessão no central, o que as regras desta revisão vedam ("login em conta"), e
  a coordenação pediu carga mínima no host. O dev server não foi iniciado. RF-01 e RF-02 no navegador, e o axe, ficam
  para a FASE B da 09. `python scripts/docs-check.py` também não rodou. A coluna "Antes" da tabela do RF-07 cita de
  propósito "backend", "worker" e "frame", e a checagem de vocabulário pode apontá-los. Isso fica para a consolidação
  do orquestrador.

## Arquivos tocados

Código (`frontend/src/`): `App.tsx`, `api/client.ts`, `features/apps/AppsPage.tsx`, `features/command/CommandPanel.tsx`,
`features/devices/{CommandTrail,DeviceCard,DeviceGrid,OperationalContextCard}.tsx`,
`features/devices/{actions,deviceState,streamState}.ts`, `features/diagnostics/DiagnosticsPage.tsx`,
`features/focus/{Drawer,FocusPanel,Screen}.tsx`, `features/infra/{CriarAparelho,InfraPage}.tsx`,
`features/login/LoginPage.tsx`, `features/painel/BarraDeSelecao.tsx`, `features/pendencias/PendenciasPage.tsx`,
`features/pendencias/{modelo,store,usePendencias}.ts`,
`features/profiles/{GuiaAparelhos,GuiaConfiguracoes,GuiaContas,GuiaHabilidades,GuiaMemoria,GuiaVisaoGeral,NovaPersona,PolicyGroups,ProfilesPage}.tsx`,
`features/runs/RunsPage.tsx`, `features/settings/{AppsSection,FlowsRecipesSection,SettingsPage}.tsx`,
`features/settings/validation.ts`, `features/topbar/TopBar.tsx`, `lib/{rotas,rotulos,status,time}.ts`,
`store/metricas.ts`.

Testes: `app.integration.test.tsx`, `features/devices/{DeviceCard.preview,DeviceCard,DeviceGrid}.test.tsx`,
`features/devices/aparelhoPersona.test.ts`, `features/diagnostics/DiagnosticsPage.test.tsx`,
`features/focus/{Drawer,FocusPanel}.test.tsx`, `features/infra/InfraPage.test.tsx`,
`features/pendencias/PendenciasPage.test.tsx`, `features/profiles/{NovaPersonaLote,ProfilesPage}.test.tsx`,
`features/settings/SettingsPage.test.tsx`, `lib/{rotulos,time}.test.ts`, `store/metricas.test.ts`.

Outros: `scripts/ui-verificar.mjs` (só o cabeçalho) e este relatório. `CHANGELOG.md` e `docs/estado-atual.md` não
foram tocados: quem consolida é o orquestrador.
