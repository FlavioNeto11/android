# Tarefa 13: prova simulada final do portal

Branch `ux/13-prova-simulada`, que sai de `claude/ux-portal` `6f184f8`, com tudo integrado até a tarefa 12. Data:
30/09/2026, de cerca de 22:10 a 22:40 no horário local (01:10 a 01:40Z de 01/10). Máquina: central
`WIN-7S2UASNLFOP` (Windows Server 2025). Esforço pedido: high.

O objetivo foi provar, em execução, o que a `revisao-final.md` deixou `not_run`. **Toda prova aqui é `simulated`**: o
backend do próprio worktree rodou com IA simulada, sem SDK, sem emulador e sem Appium. **Nenhum código foi alterado.**
Os defeitos novos vão como RF-40 a RF-49, com passos para reproduzir.

## Veredito

A maior parte do que estava `not_run` ficou provada no simulado:

- **Drawer**: 1920, 1280 e 1024 px.
- **Lista**: os 15 aparelhos a 1440x900.
- **Executar**: o motivo aparece no próprio botão.
- **axe**: as 54 células, mais 15 visões internas a 1024 e a 390 px e a tela de entrada.
- **Lighthouse**: 4 medidas.
- **D1 a D4, RF-04 e RF-26**: inclusive "Antigas (N)", o número completo desde a primeira carga e `planned` fora do
  snapshot.

Ficaram de fora três coisas, por limite do instrumento:

- **O semáforo em "Atenção"**: o motivo e o link do servidor fora do ar estão provados, mas o nível fica mascarado.
  Sem SDK, a saúde do central é `error`, e o semáforo vai a Crítico por construção.
- **Um servidor "inscrito e nunca conectado"**: o backend não representa esse estado.
- **`ui-verificar.mjs` nas 54 células**: o script não passa da tela de entrada.

Apareceram dois achados de gravidade **Média**. O RF-40 é o mesmo aparelho "Desconhecido" na lista e "Parada" no
Foco. O RF-41 é a classificação de cada problema da saúde no semáforo. Os outros oito são Baixa.

## Instrumento (zero efeito no ambiente real)

**Backend do worktree** (`127.0.0.1:8765`):

- Comando: `backend\.venv` do checkout central, `python -m app.main`, com o diretório atual em `<worktree>\backend`.
  `app` foi importado do worktree, e o commit relatado pelo `/api/health` é `6f184f8`.
- Prioridade `BelowNormal`. PIDs 25584 e 47392.
- Ambiente: `AI_PROVIDER=simulated`, mais `POC_CONFIG` e `POC_DB_PATH` numa pasta de rascunho fora do repo (o
  scratchpad da sessão).
- O worktree não tem `.env`. Nem o `.env` nem o `config.yaml` do central foram lidos.
- A variável `ANTHROPIC_BASE_URL` existe no shell, mas não tem efeito com o provedor simulado.

**Configuração**: gerada a partir de `config/config.example.yaml` por um script no rascunho:

- `server.port: 8765`, `worker_port: 0`;
- `allowed_origins` com 8765 e 5188, para que o canal ao vivo **não** caia (na revisão final ele caía pelo vite, código
  1006);
- `paths.*` todos no rascunho, e `android.sdk_root` numa pasta inexistente;
- `instances.count: 13`, `base_console_port: 5640`, portas de sistema, mjpeg e chromedriver deslocadas (8640, 9640 e
  9940);
- `appium.autostart: false`, `appium.port: 4799` e `appium.dir` inexistente, para não readotar o Appium do central na
  4723;
- `rede.servidor.binario` inexistente, com as portas 51899 e 18099.

**Painel**: `npx vite --mode simulado --port 5188 --strictPort`, com o proxy para a 8765, prioridade `BelowNormal` e
PIDs 33588, 36748 e 42444. A navegação foi feita numa aba própria (`tab-13`) do navegador da IDE.

**Portas e processos**: as portas 8765 e 5188 estavam livres no início. Ao fim, todos os processos acima foram parados,
as duas portas ficaram livres, e a **8000 seguiu com o mesmo dono do início (PID 48356)**. Nenhum script desta tarefa
apontou para a 8000.

**Dados semeados**. Pelas rotas do backend simulado:

- Um servidor remoto **"Notebook da LAN"**: `POST /api/workers/enroll`, um `hello` pelo WebSocket com 2 aparelhos
  declarados, `POST /api/workers/notebook-lan/devices/adopt` (que virou `android-14` e `android-15`) e o canal fechado.
  O servidor ficou inscrito e fora do ar.
- **6 personas** (`POST /api/personas`).
- **31 execuções** (`POST /api/runs`):
  - 6 `needs_input`: o planejador simulado pergunta "Para qual contato…?";
  - 24 `planned` (`mode: plan`);
  - 1 `completed_with_issues` (`mode: execute` num aparelho sem AVD, sem efeito nenhum).

  Total do parque: 13 locais sem AVD mais 2 remotos, ou seja 15.

**Fora das rotas** (dito de propósito): para exercitar "Antigas (N)", duas `needs_input` tiveram `created_at`
recuado para 20/09 **por um `UPDATE` direto no SQLite do rascunho**. Nenhuma rota faz isso.

**Entrada**: o simulado **pede o nome do operador**, sem chave ("Este servidor está sendo aberto da própria máquina:
não é preciso chave de acesso"). Entrei como "Prova 13 simulada" no backend simulado. Isso **confirma o RF-10**: o
`ui-verificar.mjs` aborta todo POST, não entra e cai na entrada também aqui (ver a prova 4).

**Efeito colateral no navegador da IDE**: o cookie de sessão (`parque_sessao`) não distingue porta. Entrar no portal
em `127.0.0.1:5188` **substituiu o cookie de `127.0.0.1` no perfil do navegador da IDE**, que é o mesmo nome que o
central usa na 8000. Ao fim, saí da sessão simulada (`POST /api/logout` na 5188) e apaguei o `localStorage` da origem
5188. Se havia uma sessão do central nesse perfil, **o painel da 8000 vai pedir o nome de novo**. Basta o nome: em
loopback não há chave. Nenhum dado nem aparelho do central foi tocado. Ver o RF-47.

## Tabela das provas

| # | Prova | Resultado | Nível |
|---|---|---|---|
| 1 | Semáforo com o servidor da LAN fora do ar: motivo e link | **Atendido.** "Servidor Notebook da LAN fora do ar", com a dica "Os aparelhos dele ficam em estado desconhecido até ele voltar." e o link **"Abrir Infraestrutura"** (`#/infraestrutura`). Clicado, levou à Infraestrutura com o cartão "Notebook da LAN · offline · sem canal". O segundo motivo é "2 aparelhos em estado desconhecido", com **"Ver no Painel"** (`#/painel?estado=desconhecido`), que deu o chip "Filtro: desconhecidos (2)" e 2 linhas (android-14 e android-15). Os dois motivos aparecem marcados como **ATENÇÃO**. | simulated |
| 1b | O semáforo **no nível "Atenção"** quando só a LAN está fora | **Não executado.** Sem SDK, o `/api/health` é `error` (`sdk_missing` e depois `no_acceleration` são "duros"), e o semáforo mostrou "Ambiente crítico, 7 motivos" (depois 8). Fazer o SDK "existir" exigiria uma pasta falsa com `adb` e `emulator`, contra o instrumento pedido. A lógica do nível segue provada só por `SaudeAmbiente.test.tsx` e `metricas.test.ts`. Ver também o RF-41. | not_run |
| 1c | Servidor "inscrito e **nunca** conectado" | **Não representável sem código.** A linha de `workers` só nasce no `hello` (`registry._upsert`); um token de inscrição não usado não cria servidor. Foi provado o caso real equivalente: inscrito, conectou uma vez e caiu (`connected=false`, `state` foi a `offline`). | not_run |
| 2a | Lista a 1440x900 mostra os 15 sem rolar mais de uma tela (critério da 04) | **Atendido.** Visão Lista (`#/painel?visao=lista`): 15 `tr`, de 41 px cada. A tabela tem **643 px** de altura (cabeçalho e 15 linhas), dentro dos **802 px** visíveis do `main` (98 a 900). Ela começa em y=606, abaixo do Comando, e vê-se inteira depois de rolar **348 px**, menos de uma tela. | simulated |
| 2b | Motivo do Executar desabilitado no próprio botão (critério da 03) | **Atendido.** `aria-disabled="true"`, opacidade 0,5 e `cursor: not-allowed`. O motivo fica no nome acessível ("Executar — indisponível: …", em `sr-only`) e no **tooltip ao passar o mouse e ao focar**. Sem comando: "Escreva o comando em linguagem natural." Com comando, no modo Manual e sem seleção: "Selecione ao menos um aparelho na grade abaixo." O texto diz "grade" mesmo na visão Lista (RF-49). No modo Automático, com comando, o Executar habilita (não foi clicado). | simulated |
| 3 | Drawer de foco em 1920, 1280 e 1024 px | **Atendido nas três.** Detalhe na tabela abaixo. | simulated |
| 4 | axe-core 4.13 (tags `wcag2a/aa`, `wcag21a/aa`, `wcag22aa`, `best-practice`) | **Medido em 54 células** (9 telas x 6 larguras), mais 15 visões internas a 1024 e 390 px e a tela de entrada. **0 `color-contrast`** e **0 `serious`/`critical`**. Contagem por regra abaixo. | simulated |
| 4b | `node scripts/ui-verificar.mjs --url http://127.0.0.1:5188` | **Não roda contra o simulado.** Uma célula (`--telas painel --larguras 1440`) caiu em "waiting for locator('header')" depois de 15 s: o simulado pede o nome, e o script aborta POST. Rodar as 54 seria 54 vezes o mesmo tempo esgotado (cerca de 14 min de Chrome sem cabeça), sem informação nova. As 54 células foram medidas pelo navegador da IDE (linha 4). | not_run |
| 5 | Lighthouse 13.5.0, só acessibilidade, um de cada vez | **Painel: 100 a 1440 e 100 a 390. Personas: 99 a 1440 e 99 a 390.** A sessão foi aberta no backend simulado (`POST /api/login`) e passada por `--extra-headers`, num arquivo apagado ao fim. Os relatórios contêm o topo autenticado e não contêm "Seu nome", ou seja, não caíram na entrada. Falhas: `heading-order` (1, Personas: `h3#grupos-de-acesso`) e `label-content-name-mismatch` (1, o gatilho do semáforo; ver o RF-44). Cerca de 23 a 42 s por medida. | simulated |
| 6 | RF-04, D1 a D4 e RF-26 | **Atendidos**, menos o RF-32, que **continua aberto**. Detalhe abaixo. | simulated |

### Prova 3: drawer do Foco (android-03, visão Cartões)

O drawer foi aberto com **Enter real** no botão "Abrir android-03" e fechado com **Esc real** e com um **clique real
fora**, numa área do `#conteudo` que não é aparelho.

| Janela (medida por `innerWidth`) | `main` sem e com o drawer | Drawer (x, largura) | Grade com o drawer | Esc | Clique fora | Foco devolvido | Cortes no drawer |
|---|---|---|---|---|---|---|---|
| 1920 | 1860 = 1860 | 922, **998 px** | x, largura e altura dos 15 cartões idênticos; só rolou (todos com dy = −695 = `scrollTop`, a rolagem até o aparelho) | fechou, `?foco=` saiu da URL | fechou | "Abrir android-03" (Esc e clique fora) | 0 |
| 1280 | 1220 = 1220 | 560, **720 px** | idêntica (só a rolagem até o aparelho) | fechou | fechou | "Abrir android-03" | 0 |
| 1024 | 964 = 964 | 304, **720 px** | idêntica, dy = 0 | fechou | fechou | "Abrir android-03" | 0 |

Em todas as larguras, ao abrir, o foco entrou no painel ("Visão de foco: android-03"), sem rolagem horizontal e sem
elemento além da borda do drawer.

**Legendas**: sem aparelho ligado, o preview mostra só "AVD ausente — O AVD deste aparelho não existe…", sem corte. As
legendas "Prévia suspensa — última imagem…" só existem com imagem. Essa parte segue com a prova real da revisão final
(768, 390 e 1440) e é `not_run` no simulado.

Observação a 1920: o primeiro clique de teste caiu num ponto que, depois da rolagem automática até o aparelho, era um
cartão. Pelo desenho (RF-02), clicar num cartão não fecha o drawer. O foco foi para o `main`, e o drawer ficou aberto.
O segundo clique, no título "Aparelhos", fechou.

### Prova 4: axe por regra

**54 células.** A larguras 1920, 1440, 1280, 1024 e 768, todas as células ficaram sem rolagem horizontal, sem
transbordo, 0 fonte abaixo de 13 px, 0 contraste abaixo da meta, 0 alvo abaixo de 32 px e 0 botão sem nome. A 390 px,
igual, com os cortes listados abaixo da tabela.

| Regra (impacto) | Onde | Nós |
|---|---|---|
| `region` (moderate) | as 54 células: a região de avisos (`._toastRegion…`) fora de marco | 1 por célula |
| `heading-order` (moderate) | Personas, nas 6 larguras (`h3#grupos-de-acesso`) | 1 por célula |

Cortes com reticências sem dica, a 390 px:

- o topo, em todas as 9 telas: o selo "MODO SIMULADO" e o do modelo "claude-opus-5" (RF-46);
- Infraestrutura: mais 15 linhas "· API 34 · x86_64 · Play Services · renderizador pedido: Swi…" (RF-34, confirmado).

A 768 px, a Infraestrutura ficou sem corte: o texto do simulado é mais curto que o real.

**Visões internas a 1024 e 390 px**: as 11 guias da persona (Beatriz), o Foco de android-03 (local) e de android-14
(do servidor fora do ar), e `#/execucoes?status=pendencia`.

| Visão | Regras além de `region` | Outros |
|---|---|---|
| Persona, guia **Persona** | `aria-allowed-role` (minor, 2: `article[aria-labelledby]`) e `heading-order` (moderate, 2: `#mapa-identidade h3` e a frase do cartão de identidade) | — |
| As outras 10 guias da persona | nenhuma | — |
| Foco (`?foco=android-03` e `?foco=android-14`) | `aria-allowed-role` (minor, 1: `aside` com `role=dialog`, RF-27) | — |
| `#/execucoes?status=pendencia` | nenhuma | **1 alvo de 24 px**: "Limpar filtros" (RF-43) |

**Tela de entrada (RF-28)**, a 1440 e 390 px: **0 violações do axe** e 0 contraste abaixo da meta. Os textos com
opacidade 0,7 e 0,75 passam. O campo "Seu nome" tem `label for` e `aria-describedby` corretos. O RF-28 fica
**encerrado por medição**.

### Prova 6: reverificação de D1 a D4, RF-04 e RF-26

| Item | O que foi medido | Resultado |
|---|---|---|
| D1 completo desde a primeira carga (backend) | `GET /api/snapshot` com 31 execuções: `runs` = **26** = 20 mais recentes (todas `planned`) + **as 6 `needs_input`**, inclusive as 2 recuadas para 20/09, fora das 20 recentes. As 4 `planned` mais antigas e a `completed_with_issues` **não** vêm. | **Atendido** (também prova D2 no servidor) |
| D1 na tela, recarregada (`navigation.type = reload`) | Menu "Pendências 6 (6 esperando você)" = topo "6 aguardando você" = "Todas (6)" = "Execução (6)" = semáforo "6 pendências esperando você". **4 recentes**, mais **"Antigas (2)"** recolhida, com "execuções paradas há mais de 7 dias; contam no total". Aberta, mostra as 2 "esperando há 10 dias". A `completed_with_issues` com objetivo esperando **não** entra. | **Atendido** |
| D1 estável | Visitar Execuções e voltar: menu 6, topo 6, "Todas (6)". | **Atendido** |
| D1 nas Execuções | `?status=pendencia` → chip **"Pede atenção 7"** marcado (6 `needs_input` + 1 `completed_with_issues`), "7 de 31 execuções". | **Atendido** |
| D2 | "Em andamento **0**", "Planejadas **24**", topo "**0** execuções" com 24 `planned` no banco. `?status=planejada` → "24 de 31 execuções". | **Atendido** |
| RF-32 (reconfirmação) | Em `#/execucoes?status=pendencia`, o detalhe abriu a `cdd76e` (**Planejada**, fora do filtro), e a URL virou sozinha `#/execucoes/r-…-cdd76e?status=pendencia`. A seleção veio de `cda.selectedRun`. | **Aberto** (Baixa; agora a URL também carrega a execução fora do recorte) |
| RF-04 | Clicar no chip "6 aguardando você" do topo (um `button`) levou a `#/pendencias`. O semáforo leva a `#/pendencias` pelo "6 pendências esperando você". | **Atendido** |
| D3, Painel | `#/painel` sem preferência → Cartões. "Lista" → `#/painel?visao=lista` e `cda.painel.visao = "lista"`. Pelo menu → Painel em Lista (15 linhas). Link `#/painel?visao=cards` → Cartões, **sem** mudar a preferência ("lista"). | **Atendido** |
| D3, Personas | `?situacao=ativa`, depois "Tabela" → `#/personas?situacao=ativa&visao=tabela`, `cda.personas.visao = "tabela"`. Pelo menu → `#/personas` limpo ("6 personas"), em Tabela. **Dois Voltar** → `#/personas?situacao=ativa&visao=tabela`. | **Atendido** |
| D4 | "Ver todas as suas pendências" (`#/aprendizado`) com **37,5 px** de altura e "Ver todas as pendências" (guia Aprovações da persona) com **37,5 px**, classe `linkAlvo`. | **Atendido** |
| RF-26, a 768 px | Enter real em "Menu" → gaveta aberta, foco no item atual (`aria-current=page`). **14 Tabs reais** e 5 Shift+Tab → o foco ficou dentro. Com o foco fora (botão do modelo no topo), **um Tab real** trouxe o foco a "Fechar menu", dentro da gaveta. Com o foco fora de novo, **Esc real** fechou a gaveta (`aria-expanded=false`) e o foco foi para `#botao-menu`. | **Atendido** |
| Painel `?estado=` | `desconhecido` → 2 (android-14 e 15). `absent` → "Filtro: sem emulador (12)". `error` → 1 (android-09). `stopped` → "parados (0)". Soma 15. | **Atendido** |

## Defeitos novos

| ID | Grav. | Onde | Achado e passos para reproduzir | Prova |
|---|---|---|---|---|
| RF-40 | **Média** | `DeviceList`/`DeviceGrid` × `FocusPanel` × `InfraPage` | **O mesmo aparelho tem dois estados na mesma tela.** Com o servidor dele fora do ar, a linha (e o semáforo, a contagem e o filtro) diz **"Desconhecido"**. O Foco aberto ao lado diz **"Estado: Parada"**, com "O emulador está desligado." e "Aparelho em 'stopped'…". A Infraestrutura diz "parado". A linha ainda oferece **"Iniciar"** para um aparelho cujo servidor não responde. O Foco só traz "Servidor: desconectado" lá embaixo. Viola o critério "uma métrica, um valor" (tarefa 02). **Passos**: inscrever um servidor remoto, adotar um aparelho dele, derrubar o canal; Painel → Lista → abrir o Foco desse aparelho. Captura `p13-foco-parada-x-lista-desconhecido-1440.jpg`. | simulated |
| RF-41 | **Média** | `store/metricas.ts::nivelDoAmbiente` (o nível de cada problema da saúde) | **Um único problema "duro" pinta todos os problemas da saúde de CRÍTICO.** O nível de cada problema é o do `health.status`. Com `sdk_missing` (ou `no_acceleration`/`database_down`), o popover marca CRÍTICO também em "MODO SIMULADO ativo: nenhuma IA é consultada.", "Servidor Appium não está respondendo." e no aviso do `worker_port`. Os avisos do servidor fora do ar (ATENÇÃO) ficam enterrados no fim da lista. **Passos**: backend com `android.sdk_root` inexistente → abrir o semáforo. Captura `p13-semaforo-servidor-fora-mascarado-por-critico-1440.jpg`. É também o motivo de a prova 1b não ter saído. | simulated |
| RF-42 | Baixa | `InfraPage` (cartão do servidor), dados de `GET /api/workers` | Servidor **offline e "sem canal"**, com "**Túnel: no ar** (há 18 min)": o `transport_state` ficou `up` desde a adoção, sem medição. Além disso, os 2 aparelhos **já adotados** (android-14 e 15) continuam em "2 aparelhos anunciados por este servidor que ainda não são instâncias do parque", com o botão **"Adotar"**. O inventário declarado não muda até o agente voltar. **Passos**: os do RF-40; abrir a Infraestrutura. Captura `p13-infra-servidor-fora-tunel-no-ar-parado-1440.jpg`. | simulated |
| RF-43 | Baixa | `components/BarraListagem` ("Limpar filtros") | O botão "Limpar filtros" tem **24 px** de altura (104x24). Só aparece com filtro ativo, por isso a medição da 07, sem filtro, não o pegou. **Passos**: `#/execucoes?status=pendencia`, a 1024 ou 390 px, com o auditor de alvos. | simulated |
| RF-44 | Baixa | `features/topbar/SaudeAmbiente.tsx` (gatilho) | WCAG 2.5.3 (o rótulo visível dentro do nome): o texto visível é "Ambiente crítico" e o selo "8", e o nome é "Ambiente crítico, 8 motivos. Abrir detalhes do ambiente". O Lighthouse marca `label-content-name-mismatch` no Painel e em Personas, nas duas larguras. | simulated |
| RF-45 | Baixa | links do popover do semáforo | Seguir "Abrir Infraestrutura" pelo popover troca a tela, e o **foco vai para `<body>`**. Pelo menu, ele vai para `#conteudo`. Quem usa teclado recomeça do topo. **Passos**: abrir o semáforo e ativar "Abrir Infraestrutura"; ler `document.activeElement`. | simulated |
| RF-46 | Baixa | topo, a 390 px | O selo "MODO SIMULADO" e o do modelo ("claude-opus-5") saem com reticências **sem dica**, nas 9 telas. O do modo só existe no simulado; o do modelo existe também no real. **Passos**: 390x844, qualquer tela, medir `text-overflow` com `scrollWidth > clientWidth`. | simulated |
| RF-47 | Baixa (ambiente/ferramenta) | `security/sessions.py` (`COOKIE = "parque_sessao"`) | O cookie de sessão não distingue porta. Entrar num segundo backend em `127.0.0.1` (o simulado pela 5188) **substitui a sessão do central (8000)** no mesmo navegador, e sair de um derruba o outro. Isso aconteceu nesta tarefa, no perfil do navegador da IDE. Junto com o RF-10, faz da prova simulada um risco para a sessão de quem a roda. Sugestão: abrir o simulado por `localhost:5188`, que é outro host de cookie, ou nomear o cookie com a porta. **Passos**: com sessão no `127.0.0.1:8000`, entrar em `127.0.0.1:5188` (`--mode simulado`); voltar à 8000 → tela de entrada. | simulated (efeito observado no próprio procedimento) |
| RF-48 | Baixa (textos do servidor) | `/api/health` e DTOs de aparelho e servidor | Textos crus ou enganosos mostrados no portal: "1 aparelho(s) respondem ao ADB mas não estão utilizáveis: android-09" (não há ADB nenhum no simulado; o aparelho falhou por falta de SDK); "1 worker(s) remoto(s) inscrito(s)… (server.worker_port: 0)"; "Use AI_PROVIDER=anthropic no .env"; no Foco, "Origem: Adotado de um servidor, sem config.yaml" e "Aparelho em 'stopped'"; na Infraestrutura, "external" ao lado do estado. Somam-se ao RF-07r. | simulated |
| RF-49 | Baixa | `features/command` (motivo do Executar) | Na visão **Lista**, o motivo diz "Selecione ao menos um aparelho na **grade** abaixo." **Passos**: Painel em Lista, modo Manual, escrever um comando, focar o Executar. | simulated |

Situação dos achados anteriores tocados aqui:

- **RF-28**: encerrado (0 violações na entrada).
- **RF-26, D1 a D4 e RF-04**: confirmados no simulado.
- **RF-34**: confirmado a 390 px.
- **RF-32**: segue aberto.
- **RF-27** (`region`, `heading-order`, `aria-allowed-role`): igual, sem novidade.
- **RF-10**: confirmado também no simulado.

## O que ficou `not_run`

- O semáforo **no nível "Atenção"** com só a LAN fora (1b): mascarado pelo Crítico do SDK ausente (RF-41).
- O servidor **"inscrito e nunca conectado"** (1c): o backend não tem esse estado.
- `ui-verificar.mjs` **nas 54 células** (4b): ele não entra; as 54 foram medidas pelo navegador.
- As **legendas do preview com imagem** no drawer: sem aparelho ligado, não há imagem.
- Aparelho ligado, controle manual, comando em voo e prévia ao vivo: sem SDK, nada fica online.
- Leitor de tela e toque real.
- `python scripts/docs-check.py`.

## Verificação

- `npm run typecheck`: sem erros.
- `npm test`: **84 arquivos, 1013 testes, todos verdes** (vitest, prioridade baixa, 16,5 s, rodado depois de parar o
  backend e o vite).
- Nenhum código mudou. Os arquivos temporários de medição (`frontend/public/__rev13/`: o `axe.min.js`, o
  `ui-auditoria.js` e dois medidores) foram apagados, e `git status` só mostra este relatório e as capturas.

## Arquivos

- `docs/revisoes-ux/13-prova-simulada.md` (este relatório);
- `docs/revisoes-ux/capturas/p13-semaforo-servidor-fora-mascarado-por-critico-1440.jpg`;
- `docs/revisoes-ux/capturas/p13-infra-servidor-fora-tunel-no-ar-parado-1440.jpg`;
- `docs/revisoes-ux/capturas/p13-foco-parada-x-lista-desconhecido-1440.jpg`.

Não foram tocados `CHANGELOG.md`, `docs/estado-atual.md`, nem nenhum código.
