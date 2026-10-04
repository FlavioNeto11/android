# Produto

> Fonte principal para "o que este sistema é e o que ele promete". Para arquitetura interna, ver
> [docs/arquitetura.md](arquitetura.md); para domínios, [docs/dominios/](dominios/); para o estado de execução do
> plano de trabalho, [docs/estado-atual.md](estado-atual.md) e [docs/roadmap.md](roadmap.md).

## 1. Objetivo

Um painel controla um **parque de emuladores Android** (locais e em outras máquinas, via *worker*) e executa
tarefas em aplicativos a partir de um **objetivo em português**. A IA lê a tela, decide uma ação por vez, age pela
interface (Appium/UiAutomator2) e **comprova** o resultado — nunca aceita a resposta do driver como prova, só a
pós-condição observada na tela seguinte.

Quatro compromissos guiam o design, e aparecem espalhados pelo código com o mesmo nome:

1. **Ver os aparelhos e mandar objetivo em português.** Um comando descreve o que fazer, não como; a IA monta o
   plano, decide etapa por etapa e relata **por instância**.
2. **Falha ou incerteza nunca conta como sucesso.** Um comando que termina sem se saber o efeito vira `uncertain`
   — nunca é reenviado sozinho; alguém confirma, repete ou abandona (`backend/app/commands/`,
   `backend/app/taskqueue/executor.py`). Um resultado do driver (Appium) nunca fecha uma etapa: só a pós-condição
   observada na tela fecha.
3. **Custo sob controle.** Receitas (aprender uma vez, repetir por seletor), fluxos (plano congelado reaproveitado
   sem novo planejamento), modelo por função e rodízio de instâncias existem para que IA e RAM não cresçam
   linearmente com o número de contas. Ver [docs/ia.md](ia.md) §7.
4. **Operar contas com persona e aprovação.** Uma conta de Instagram tem persona (voz, limites, memória) e
   política por ação (sozinho / com aprovação / só manual) — nunca ação livre sem esses dois.

## 2. Conceitos

| Termo | O que é |
|---|---|
| **Aparelho / instância** | Um emulador Android com AVD próprio (`android-NN`), userdata, apps e sessões preservados entre reinícios. |
| **Servidor / worker** | Uma máquina que hospeda aparelhos. O central é um worker de si mesmo (`LocalWorker`); um worker remoto se inscreve pelo mesmo contrato e recebe trabalho por um canal WebSocket dedicado, atrás de túnel SSH. |
| **Comando** | Um verbo do ciclo de vida do aparelho (`start`, `stop`, `hibernate`, `wake`, `restart`, `reset`, `open_app`…) endereçado a uma instância. Responde `202` com `command_id` e caminha por estados até `succeeded`/`failed`/`rejected`/`cancelled`/`uncertain`. |
| **Execução / objetivo / etapa** | Uma *execução* (`run`) atende um comando em linguagem natural para um conjunto de instâncias; cada instância ganha um *objetivo* (`objective`); um objetivo se decompõe em *etapas* (`step`) com dependência e pós-condição observável. |
| **Receita** | Uma etapa aprendida uma vez pela IA vira uma sequência de seletores repetível sem nova chamada de modelo (`ai.recipes: replay`). |
| **Fluxo** | Uma execução 100% comprovada vira um plano congelado; o mesmo comando com outros parâmetros reaproveita o plano sem chamar o planejador (`ai.flows: true`). |
| **Perfil / persona** | Um perfil (`instagram_profiles`) é a identidade que opera um app: conta, voz (persona), memória, política por ação e limites por hora. |
| **Política / aprovação** | Cada ação do catálogo tem uma política: sozinho, com aprovação (fica em `pending_approvals` até alguém decidir) ou só manual. |
| **Release / canário** | Uma versão de APK importada nasce `validated`; só vira instalável em massa (`promoted`) depois de provar um canário (instalar, abrir, continuar de pé) num aparelho só. |
| **Loja** | Um emulador extra com imagem Play Store, logado na conta Google do dono. É a única fonte de APK do Instagram: o backend copia o conjunto (base + splits) desse aparelho por `adb` e distribui ao parque. Nunca recebe tarefa nem entra no rodízio. |
| **Habilidade versionada** | Um processo com versão, estado e prova (`skill_versions`), atrás de `skills.enabled` (padrão desligado). Nasce do ensino v2 como **rascunho**, ou da conversão de um fluxo; publicá-la é outra decisão, de uma pessoa. Ver [dominios/skills.md](dominios/skills.md) e [teaching.md](teaching.md). |

## 3. Fluxos do usuário

- **Primeiro comando.** Dois modos no Comando:
  - **por aparelho**: selecionar aparelhos, escrever o objetivo, *Planejar* (inspeciona sem agir) ou *Executar*;
  - **por persona** (segunda evolução, [ADR-044](decisoes.md#adr-044--roteamento-das-execuções-por-persona-alvos-resolvidos-destinos-no-texto-e-prévia-obrigatória)):
    escolher uma ou mais personas e a política ("um aparelho dela", "o principal", "todos"); o sistema escolhe o
    aparelho.

  Nos dois, o texto pode dizer o destino ("peça para o André…", "no aparelho android-02 e android-03", "como @user").
  Antes de enviar, a **prévia** mostra persona → aparelho e a origem de cada escolha (interface, texto, vínculo,
  balanceamento), o comando sem os destinos e as perguntas; destino tirado do texto só executa depois de confirmado.
  Se faltar dado essencial ou houver ambiguidade, a execução fica `needs_input` com as perguntas — a IA nunca
  inventa. **Não há campo de senha no Comando** (ADR-040): a senha fica na conta da persona.
- **Menu lateral e cabeçalho (revisão de UX, 30/09).** As onze seções (Painel, Personas, Aplicativos, Execuções, Pedidos, Aprendizado,
  Infraestrutura, Configuração, Diagnóstico, Canais e Pendências) ficam num menu lateral recolhível (ícone e rótulo; só ícone quando
  recolhido; **abre expandido a partir de 1280 px** e recolhido abaixo, e a escolha da pessoa fica guardada no navegador e vence
  o padrão; abaixo de 1024 px vira gaveta, aberta pelo botão "Menu", que traz o selo de pendências, com o Tab preso dentro e Esc
  para fechar). **No celular (abaixo de 768 px) o cabeçalho é uma linha só de 56 px**: Menu, marca, semáforo de saúde e o botão
  "Resumo", que abre o painel com as métricas, os custos e a sessão; de 768 a 1023 px, CPU, RAM e custos viram o chip "Recursos".
  Quando uma origem de pendências falha ao carregar, o total aparece como "4+" (ou "?"), nunca como um número menor sem aviso). O item
  atual tem `aria-current`; o selo de Pendências é o total da caixa. O topo guarda a marca, o chip do modelo de IA, o aviso
  de envio externo, a conexão em tempo real e o operador. Embaixo, uma régua do parque em três grupos (Saúde, Capacidade e
  Custos): o semáforo do ambiente (OK, Atenção ou Crítico, com os motivos e links), aparelhos online/cadastrados e vagas
  por servidor, execuções em andamento, "aguardando você" (o total da caixa de Pendências), CPU e RAM, e os saldos de IA
  sempre em US$ (valor estimado traz ícone e dica). Os números saem todos de `frontend/src/store/metricas.ts`.
- **Modo Automático** ([ADR-050](decisoes.md#adr-050--modo-automático-a-ia-escolhe-quem-faz-o-código-escolhe-onde-crença-é-coerência-não-alvo-de-persuasão)).
  É o padrão do Comando: a pessoa escreve o pedido e o sistema decide quem faz e onde. A IA escolhe quais e quantas
  personas combinam com o pedido (perfil, voz, crenças como coerência, disponibilidade); o aparelho e o servidor
  saem da sessão pronta, do vínculo e da carga. Antes de criar, "Quem faz e onde" mostra cada persona com o motivo,
  o aparelho e o servidor, as descartadas e as que faltam dados (com link para completar na persona). Pedido de
  propaganda ou de voto não é roteado (ADR-048). Os modos manuais ficam no lado "Manual" do controle
  segmentado **Automático | Manual** (sempre há um dos dois marcado). Refinar com IA, Planejar e Executar são etapas
  em sequência, numeradas; quando uma está indisponível, o motivo aparece no próprio botão (ao passar o mouse ou focar).
- **Seleção em massa e Foco (revisão de UX, 30/09).** Com aparelhos marcados, uma barra fica presa ao topo da grade
  (nunca sobre os cartões): "N selecionados", Iniciar, Parar, Reiniciar e "Mais ações" (Hibernar, Instalar app, Abrir
  app e, numa "Zona de perigo" à parte, Resetar dados, sempre com a confirmação). O Foco é um drawer lateral que
  **sobrepõe** a página sem reorganizar a grade; fecha com Esc, com o clique na página ou em Fechar, e devolve o teclado
  ao cartão (o menu lateral e os cartões seguem clicáveis: o Foco acompanha a troca de tela ou de aparelho). Com ele aberto, a barra em massa esconde os botões (as ações do aparelho estão no drawer).
- **Menu, links e visões (revisão de UX, 30/09; decisão D3).** A URL é a fonte da verdade: busca, filtros, o objeto
  aberto e a guia ficam no link (`frontend/src/lib/rotas.ts`), e colar o link ou usar o Voltar mostra o mesmo recorte.
  O item do menu leva à tela **limpa**, sem os filtros da última visita. A forma de ver a lista é a exceção, com uma
  regra só no Painel ("Cartões | Lista") e em Personas ("Cartões | Tabela"): `?visao=` no link manda; sem ele, vale a
  última visão escolhida neste navegador; escolher grava nos dois (no link, sem empilhar entrada no histórico, e no
  navegador). Prova: `simulated` (`DeviceGrid.test.tsx`, `ProfilesPage.test.tsx`, `filtroPersonas.test.ts`).
- **Pendências e o que pede atenção (revisão de UX, 30/09; decisões D1 e D2).** "Pendência" é o que depende de uma
  pessoa, e tem dona: a caixa de Pendências (`#/pendencias`), com aprovações do Aprendizado e das personas, contas que
  pedem intervenção e execuções paradas pedindo informação (`needs_input`), todas, por mais antigas. As paradas há mais
  de 7 dias ficam numa seção recolhida "Antigas (N)", mas contam. O selo do menu, o "aguardando você" do topo e o
  aviso na saúde do ambiente são o mesmo total e levam à caixa. Em Execuções, o filtro que junta "terminou com
  problema" e "parou pedindo informação" se chama **Pede atenção**, e um plano pronto que ninguém mandou executar
  (`planned`) tem o chip **Planejadas**: não conta como execução em andamento.
- **Pedidos persistentes e a tela Pedidos (Fase 28, item 28.9) — tela IMPLEMENTADA no painel, prova `simulated`** (código em
  `frontend/src/features/pedidos/`, testes `PedidosPage.test.tsx`, `NovoPedido.test.tsx` e `pendencias/pedidosNaCaixa.test.ts`, com o
  backend falso; contra o backend real do 28.9: `not_run`; as divergências e o que falta estão no fim deste item; fontes:
  [desenho](design/pedidos-persistentes.md) §11, §6.2 e §7.9, e o adendo v0.45 do [contrato](api-contract.md), que traz as
  rotas, os corpos e os códigos de erro). Um pedido é o objetivo que dura: ele gera ocorrências e cada ocorrência vira uma
  execução comum. É a décima seção do menu, logo depois de Execuções.
  - **Criar pelo Comando.** Ao lado de Planejar e Executar, o Comando ganha o seletor **Quando** (agora, em, repetir, quando
    acontecer, acompanhar) e os campos do pedido: título, critérios de sucesso, autonomia (observar, preparar ou agir; o
    padrão é observar), fuso, prazo, limite de ocorrências e orçamento. O assistente do ADR-047 refina o objetivo, e a
    escolha de personas é a de sempre (Automático ou Manual). A **prévia é obrigatória** e não gasta nada: mostra os alvos
    e a origem de cada escolha (ADR-044), as próximas 5 datas no fuso escolhido (com a marca de hora que não existe ou que
    se repete no horário de verão), a autonomia com o que exige aprovação e o que fica recusado, o custo estimado por mês
    (ou "sem base de custo", nunca um número inventado) e os bloqueios. Só **Confirmar**, com o selo da prévia, cria o pedido
    ativo; sem confirmar, ele fica rascunho.
  - **Aparência da tela (passe de design, 02/10).** Datas sempre como "sex, 02/10 às 19:00" (hora do fuso do pedido; o fuso só aparece se difere do navegador, uma vez). Lista: cartão com chips, agenda legível e metadados discretos; vazio com **Novo pedido**, que abre o Comando com o painel "Repetir ou acompanhar" aberto. Painel de criação em seções (Quando, O que conta como feito, Limites, Avançado) com o resumo "Quem faz" e a prévia em blocos (quando, quem e onde, autonomia, custo). Detalhe: título curto e Resumo em cartões. Sem alvo decidido no Automático, a tela mostra a pergunta do backend e manda marcar aparelhos no Manual ou escolher uma persona.
  - **Lista (`#/pedidos`).** Uma linha por pedido: estado, título, tipo de gatilho, persona(s), próxima execução (hora
    local e fuso), última ocorrência (resultado e link para a execução), gasto contra o orçamento e avisos não lidos.
    Busca, estado, autonomia, persona e tipo ficam no link, como nas outras telas (ADR-062, item 4); o item do menu leva
    à tela limpa, e os chips de estado contam todos os pedidos, não só os carregados.
  - **Detalhe (`#/pedidos/<id>?aba=…`).** Resumo (objetivo, critérios, autonomia, limites, versão); linha do tempo das
    ocorrências, com o **motivo de cada pulada ou perdida** e o link para a execução (a mesma tela de Execuções), ou o
    aviso de que a execução foi purgada, com custo e resumo preservados; próximas datas; pendências do pedido; custo por
    ocorrência. Memória, relatórios e observações aparecem quando o item 28.7 existir; antes disso a guia diz "ainda não
    disponível", não "vazio".
  - **Ações**, todas lidas de `acoes_permitidas` (o painel não reescreve a tabela de estados): editar (nova versão, com a
    prévia do que muda nas próximas datas e quantas ocorrências são refeitas), ativar, pausar (com motivo), retomar
    ("daqui para frente", o padrão, ou "recuperar dentro da janela"), executar agora, backfill (só em observar, com prévia da
    quantidade e do custo) e cancelar, que **pede confirmação e diz se há execução em curso**; cancelar é pedir, e o
    desfecho da execução em curso é o real. Cada clique manda a própria chave de idempotência.
  - **Avisos e Pendências não se misturam (ADR-062).** A **caixa de avisos** no painel é informativa, com contador próprio
    na barra, e traz pausa automática, orçamento a 80% ou esgotado, ocorrência perdida, relatório pronto e encerramento. O
    pedido que **espera uma pessoa** (aprovação, pergunta do planejador ou ocorrência incerta) entra na caixa de Pendências
    como uma origem nova, e o aviso nunca usa a palavra "pendência". O canal de aviso **fora do painel** (e-mail, mensagem) é
    o item 28.11, decisão do dono; aqui existe só o ponto de extensão, o evento `pedido.aviso`.
  - **O que o painel faz hoje, e onde diverge do texto acima** (02/10/2026):
    - Criar: o Comando ganhou o botão **Repetir ou acompanhar…**, que abre a criação do pedido com o texto e os alvos do
      Comando (aparelhos marcados, personas, distribuição ou o Automático, que pergunta à sugestão como o Executar). Não há
      um seletor "Quando" ao lado de Planejar e Executar: o "Quando" fica dentro do pedido (agora, em um horário, repetir,
      acompanhar). "Quando acontecer" (evento, condição) não é oferecido: depende do 28.8 e o backend o recusa. "Acompanhar" é
      repetir de hora em hora na autonomia observar. A prévia é obrigatória: "Confirmar e criar" só anda com a prévia em dia
      e sem bloqueio, e mexer em qualquer campo a apaga. A `idempotency_key` é uma por conteúdo e selo: repetir a mesma
      tentativa reaproveita a chave.
    - Lista e detalhe: como descritos, com os filtros no link com os nomes da query da API. Os chips de estado são de
      escolha única na tela (o link aceita vários estados separados por vírgula).
    - Ações: lidas de `acoes_permitidas`. Há botão para **editar** (prévia com `dry_run`: o que muda e as datas antes e
      depois; aplicar leva a `versao` e o selo; só os campos que mudaram vão; a agenda dos gatilhos NÃO se edita nesta tela),
      **ativar** (o selo vem da prévia em `dry_run`), **pausar** (com motivo), **retomar** (modo "daqui para frente" ou
      "recuperar" só para pausado; de aguardando você, sem modo) e **cancelar** (primeiro pergunta, sem `confirmar`, e a
      confirmação diz quantas ocorrências futuras e quantas execuções em curso; depois manda `confirmar: true`). Repetir uma
      ação que já valia mostra "nada mudou" (`sem_mudanca`). **Não há botão de executar agora nem de backfill**: ficam para
      uma próxima entrega. A "chave de idempotência por clique" do texto acima vale só para a criação (as ações de estado
      repetem pelo estado, como o contrato define).
    - Avisos: a caixa é a guia **Avisos** da própria tela Pedidos (`#/pedidos?aba=avisos`), com o filtro `requer_pessoa=0`; o
      contador de **avisos não lidos** é o selo do item Pedidos do menu (não há chip na barra do topo), e um aviso
      informativo que chega ao vivo vira um toast.
    - Pendências: o pedido `aguardando_pessoa` é uma origem nova da caixa (uma linha por pedido, com a aprovação e a execução
      parada dele agrupadas embaixo, sem contar de novo; emenda à ADR-062 confirmada pelo dono). O painel monta o item no
      cliente com `GET /api/pedidos?estado=aguardando_pessoa`; o campo `pedidos` do snapshot, previsto no contrato, não é
      lido.
    - Memória, relatórios e observações: `null` diz "ainda não disponível", `[]` diz "vazio".
    - Em aberto: o resultado de nenhuma destas telas foi visto contra o backend real nem num navegador a 1366 e a 375 px
      (`not_run`); a execução (tela Execuções) ainda não mostra de que pedido nasceu.
- **Assistente do comando** ([ADR-047](decisoes.md#adr-047--assistente-do-comando-refinar-com-a-ia-e-responder-à-execução-sem-reescrever-o-texto)).
  "Refinar com IA" reescreve o texto em blocos (Objetivo, App ou site, Passos, Dados, Concluído quando), pergunta só
  o que falta (com opções) e incorpora cada resposta na rodada seguinte; o texto refinado é editável, cada rodada
  pode ser desfeita, e "Usar este comando" o põe no campo. Numa execução em `needs_input`, as perguntas do
  planejador viram campos ali mesmo: responder refina o comando e "Planejar com as respostas" / "Executar" criam a
  execução sucessora com os mesmos alvos (a antiga fica cancelada, apontando para a nova). Perguntas de destino
  continuam no Comando (escolher persona ou aparelhos).
- **Controle manual.** Pedir o controle faz a IA ceder no próximo ponto seguro; toques são mapeados para o frame
  exibido; devolver o controle faz a IA reobservar a tela antes de continuar.
- **Distribuir app.** Uma release promovida é entregue por rodízio: quem está ligado instala já, o resto recebe
  antes da próxima tarefa daquele app (ou `-Agora`, que liga o parque inteiro para instalar).
- **Personas** (segunda evolução; [persona](dominios/persona.md)). A persona é a pessoa, com ou sem conta.
  - **Criar:** "Nova persona a partir de um prompt" (rascunho gerado por IA, editável, chamada paga com o custo
    mostrado) ou "Nova persona manual".
    Com "Quantidade" de 1 a 10, gera em lote: criar direto ou revisar antes, com o custo (geração e imagem)
    mostrado antes de confirmar e o progresso de cada pessoa.
  - **Operações em lote:** caixa de seleção em cada pessoa e "Selecionar todas"; a barra de ações gera mais fotos,
    completa com IA (instrução opcional), muda o grupo de acesso, bloqueia/reativa e apaga (com "apagar N"
    digitado), três de cada vez, com um resumo por pessoa.
  - **Tela da persona (UX2-12, 01/10):** um cabeçalho único (foto, nome, @, estado, aparelho e "Abrir no aparelho"); "Não verificada"
    é um botão que leva à guia Contas e acesso e "Conectado" diz há quanto tempo foi confirmada. A navegação tem **5 seções**
    (Visão geral, Perfil, Contas e aparelhos, Atividade, Avançado; em tela estreita, uma lista suspensa) e, dentro da seção,
    as guias abaixo. A URL guarda a **guia** (`#/personas/lucas-almeida/memoria`), nunca a seção, então os links antigos
    valem; a persona aparece pelo nome (homônimos ganham um sufixo curto do id) ou pelo id antigo. O filtro por app só
    aparece em Memória e Interações, com "Mostrando: …"; religião e política ficam em "Atributos de personalidade", fechado.
  - **Guias:** Visão geral (atributos, fotos, contas, aparelhos); Persona — o **mapa da pessoa** (28/09): retrato no topo
    (quem ela é num relance, cada fato leva à sua seção, quantas seções estão preenchidas, "Completar com IA" ao
    lado), índice fixo com o estado de cada seção (completa, parcial, vazia) e a marca do que vai ao modelo, e as
    seções Identidade, Origem e casa, Trabalho, Vida, Gostos, Crenças e Voz abrindo em leitura visual (etiquetas,
    linha do tempo, gosta × não gosta); "Editar {seção}" abre só aquela. Em janela estreita o índice vira faixa de
    atalhos; **Contas e acesso** (uma linha por conta, com identificador de login, senha com
    consentimento obrigatório, sessão por aparelho, Conectar/Verificar/Sair); Imagens (galeria, principal, gerar mais,
    upload); **Aparelhos** (os N aparelhos da persona, o principal, vincular e desvincular; um aparelho tem uma conta
    por app).
  - A senha vai direto ao cofre cifrado (DPAPI no Windows) e nunca volta — nem em resposta, nem em log, nem em
    evidência, nem em prompt.
- **Aparelhos.** O painel lateral (Foco) mostra o aparelho em seções: identidade, estado e saúde, servidor, tarefa,
  **personas neste aparelho**, contas, apps e ações em grupos, com a **Zona de perigo** separada no fim. A
  Infraestrutura mostra Servidor → Aparelho → Persona(s), cria aparelho no servidor central e aposenta aparelho
  criado pela plataforma ([ADR-045](decisoes.md#adr-045--provisionamento-de-aparelho-pela-plataforma-local-agora-remoto-depois)).
- **Aprovar ação.** Uma ação de política "com aprovação" fica pendente até alguém decidir (aprovar/rejeitar) pela
  aba Aprovações do perfil.
- **Aprendizado** ([ADR-054](decisoes.md#adr-054--aprendizado-contínuo-livro-de-aprendizado-com-ciclo-de-vida-publicação-sozinha-só-sem-efeito-externo-d1-feedback-implícito-com-botão-opcional-d2-lições-medidas-e-backlog-do-que-mais-falha);
  [domínio](dominios/aprendizado.md); `frontend/src/features/aprendizado/`). O que o sistema aprendeu com as execuções e
  com quem monitora. Sem efeito externo e com repetição, ele publica sozinho; com efeito ou texto de pessoa, espera o
  dono; rebaixar é sempre automático. A seção tem seis abas:
  - **Aplicativos** (a inicial, 30.15): um cartão por app (existência declarado, loja ou só aprendido; o declarado, o aprendido
    por tipo e estado, o absorvido e como o aprendido é usado, inclusive "medido, não usado"), os baldes "App não resolvido" e
    "Fora do eixo" e, por app (`?aba=apps&app=<pacote>`), o detalhe Declarado, Aprendido e Absorvido, com link de volta nos dois
    sentidos. O filtro de app do Aprendido vem dessa lista. Cada app mostra a contagem por rótulo de saúde (contada da lista do Livro,
    nunca recalculada), a fila **Atenção** (degradando, provavelmente obsoleto ou sem evidência, só leitura, com o motivo e o link do
    item; global e por app) e, no detalhe, "O que falha" com os grupos do backlog do app agrupados por capability.
  - **Para aprovar:** a fila do D1 (receita com commit, fluxo com efeito, texto de pessoa, habilidade validada), com a
    evidência ao lado, motivo obrigatório e aprovação em lote ("Selecionar todos", "Aprovar selecionados"). A
    habilidade se decide ali pela rota das habilidades. Embaixo, **Revisar**: receitas e fluxos ativos com efeito,
    anteriores ao D1, que só se rebaixam;
  - **Aprendido:** o catálogo unificado (receitas, fluxos, habilidades, memória em contagem, telas, lições, vozes,
    preferências), por tipo e estado, com desligar, aposentar e reativar. Cada linha mostra o selo de saúde; o detalhe (30.16) traz
    identidade, conteúdo legível (só nomes de parâmetro), saúde com motivos, versão do app, evidência, histórico e relações com link;
  - **O que mais falha:** grupos por app, ação, tipo de falha e tela, com US$, minutos e intervenções separados, a
    camada, "onde alterar" e o estado no backlog. O falso positivo do verificador fica no topo;
  - **Sinais:** os votos e os gestos que viram evidência;
  - **Métricas** (30.33): o aprendizado medido na janela (7, 14 ou 30 dias) e por app. Há um cartão por bloco: o livro
    agora, o movimento, o tempo até subir, o depois de publicado, a saúde, a economia de IA, a comparação receita × só
    IA (rotulada como comparação, não prova), o curador e o orçamento dele (global, na janela do curador, com o aviso a
    80%). Embaixo vêm os pareceres do curador, com filtro pela sugestão e "Carregar mais". Ausente aparece como "sem
    amostra", nunca como zero.

  Na aba "Por aparelho", a etapa que falhou ou ficou incerta e veio de uma habilidade tem **"Corrigir esta etapa"**
  (no detalhe; a linha recolhida mostra a marca "corrigível"): a pessoa diz o que devia ter acontecido, e a correção
  entra no ensino daquela habilidade e versão (item 22.7, 29/09).

  Na execução, cada objetivo (aba "Por aparelho") e a execução inteira (aba "Relatório") têm o botão **"Deu certo /
  Deu errado"**: sem modal e sem pergunta, com o motivo em linha. "Deu errado" por navegação desliga o fluxo e as
  receitas envolvidos, e "Reativar" aparece quando o que foi desligado estava publicado. Nota com cara de senha é
  recusada. "Aprendizado desta execução", no Relatório, mostra o que a execução ensinou ou usou do livro (receitas,
  fluxos, falhas, candidatas e lições, com quem decidiu), os votos e os sinais. Se a leitura falhar, diz que não foi
  possível ler, em vez de "nada aprendido". O cartão "Custo de IA desta execução" mostra "Normal medido para este
  plano" (mediana–p90 de chamadas, US$ e tempo, e a janela efetiva). Na lista de fluxos, os status "Em prova" e
  "Esperando o dono" aparecem com nome (29/09). Prova: `simulated` (vitest de `features/aprendizado/`, `features/runs/`
  e `lib/status.test.ts`); conferência visual no navegador: `not_run`.
- **Relatório da execução: "Resultado por instância" em cartões (29/09, pedido do dono: "muito ruim de ler").** A
  tabela de ~15 colunas, que espremia etapas e efeitos em colunas estreitas e mostrava chaves em inglês ("Proven",
  "Blocked reason"), virou um cartão por aparelho
  (`frontend/src/features/runs/ResultadoPorInstancia.tsx`, leitura em `resultadoDaInstancia.ts`):
  - **cabeçalho:** a situação, o selo de prova, a entrega e onde rodou (servidor · serial; "não registrado" quando não
    houve fotografia). "Comprovado" só aparece com `proven: true`; sucesso com etapa confirmada à mão mostra "Com
    etapa confirmada à mão", nunca "Comprovado";
  - **corpo:** o detalhe, o motivo (só quando difere do detalhe), "O que falta" (`needs`) e as etapas em três grupos
    que não se fundem: comprovadas (com a prova; "em qualquer versão do plano" quando houve replano), confirmadas à mão
    e "em aberto no plano final (vN)". A mesma etapa pode estar comprovada numa versão antiga e em aberto na final;
  - **efeitos externos** com o horário, e o rodapé com versões do plano, chamadas de IA e tokens. A borda do cartão
    tem a cor da situação (aviso para sucesso sem prova completa);
  - com mais de 3 aparelhos, os comprovados começam recolhidos ("Ver etapas e efeitos (…)") e o resto fica à vista;
  - campo desconhecido aparece em chave/valor, e linha que não é objeto cai para a árvore genérica.
- **Canais (04/10, item 32.5; só leitura).** `#/canais`, no menu junto de Diagnóstico: três cartões (Aviso pelo Telegram, Conversa pelo
  Telegram, Trello) com selo ligado, desligado ou com problema, "há X min" e as contagens em frases ("3 avisos enviados, 1 na fila"); o
  motivo da última falha de envio vem traduzido e os códigos de problema viram frases. Relê a cada 30 s e não tem botão de
  escrita (ligar, desligar e reenviar ficam na configuração). Nunca mostra conteúdo de aviso, mensagem ou cartão
  (`GET /api/canais/estado`, adendo v1.11 do contrato). Código em `frontend/src/features/canais/`.
- **Diagnóstico: "Outros dados" legível (29/09, mesmo pedido).** Era uma árvore aninhada sem fim, em que o teste de
  escala repetia a lista de aparelhos em cada leva. Agora cada chave tem um bloco com título e explicação
  (`frontend/src/features/diagnostics/OutrosDados.tsx`, leitura em `outros.ts`):
  - **Onde foi medido;**
  - **SDK do Android:** as imagens uma por linha e a imagem própria por aparelho em tabela;
  - **Teste de escala:** uma tabela por leva (pedidos, no ar, tempo da leva, CPU, memória livre e usada, fora do ar,
    IA, quando) e, recolhida, a tabela aparelho × leva com o boot e a RAM do emulador;
  - **Imagens de sistema medidas:** RAM pedida, RAM que o Android vê, memória do emulador e primeiro boot;
  - **Levantamento do host (script):** chave/valor em português, discos e processos em tabela, a saída do
    `-accel-check` em bloco de código.

  A chave desconhecida continua visível, uma linha por item ou pela árvore genérica.
- **Diagnóstico › Custo de IA: o modelo forte e a conferência (03/10, item 31.16; RA-10).** As chaves do adendo v0.75
  que só a API tinha viram tela (`frontend/src/features/usage/UsageView.tsx`, leitura em `usage.ts`; detalhe no adendo
  v0.90 de `api-contract.md`):
  - a seção "Modelo forte e conferência" diz por que as chamadas subiram ao modelo forte (por motivo, maior custo
    primeiro), o rejulgamento do verificador (julgadas, discordância e custo) e a cascata do bloqueio (quantas subiram e
    quantas desbloquearam a tela);
  - a discordância por app e o motivo da imagem ficam em recolhidos;
  - o aviso "Etapas sem registro de quem decidiu" só aparece quando há alguma (o esperado é nenhuma);
  - com servidor anterior ao v0.75, ou período sem nada disso, nada aparece.
- **Treinar habilidade.** Assumir o controle no Foco e realizar a tarefa; cada entrada é gravada com o elemento
  tocado; a IA generaliza a gravação em comando + etapas + receitas, com escopo por perfis/grupos (item 13.1–13.3
  do plano — ver §5). Desde a fase J, salvar recusa (409 `duplicate_command`) um comando que uma habilidade
  versionada publicada já tem.
- **Ensinar habilidade versionada** (ensino v2, fase F; só com `health.features.skills`, que é o `skills.enabled`).
  O detalhe está em [teaching.md](teaching.md).
  - **Na revisão do treino** (`frontend/src/features/training/TrainingReview.tsx`), o quadro "Habilidade versionada
    (ensino v2)" (`TeachingPanel.tsx`) aparece ao lado do "Salvar como fluxo" de sempre, sem ponte entre os dois.
    Desde a fase L (usabilidade), com o flag ligado o ensino v2 é o único botão primário do diálogo; o rodapé que
    salva o fluxo é secundário. O quadro só oferece "Gerar candidata" depois de saber que a gravação ainda não tem
    ensino (carga com indicador e erro com "Tentar de novo"), e tem "Descartar" com confirmação
    (`POST /api/teaching-sessions/{id}/discard`; a gravação fica presa ao ensino descartado, por isso o quadro não
    volta a oferecer "Gerar").
    - "Gerar candidata de habilidade" cria o ensino, liga a gravação e pede a candidata: uma chamada do modelo do
      planejador na IA real, nenhuma no simulado.
    - O quadro mostra o comando, os parâmetros inferidos com tipo e exemplo, as etapas (com a capability e o
      "efeito externo"), os riscos, os erros de compilação e o motivo de uma candidata recusada.
    - As perguntas do generalizador ganham um campo de resposta ("sem senha nem código"): resposta com credencial
      volta recusada. "Gerar de novo com as respostas" pede a candidata seguinte.
    - "Salvar como rascunho" valida (estática) e salva a candidata como versão em `draft`. Nada é publicado.
    - Ao reabrir a revisão, o quadro reencontra o ensino daquela gravação.
  - **Em Configuração → Fluxos e receitas** (`frontend/src/features/settings/FlowsRecipesSection.tsx`), a lista
    "Habilidades" agrupa as versões por habilidade (fase L): a publicada e a última em destaque, as anteriores
    recolhidas. Cada versão mostra o estado com ícone (rascunho, candidata, validada, publicada, substituída,
    desabilitada), "conteúdo alterado" quando o hash não bate (com a explicação no `title`), o comando-modelo, a
    referência, o nome do app e desde quando. A seção lembra se estava aberta, como as vizinhas.
    - **Transições de versão** (fase J): cada versão ganha os botões que o domínio permite no estado dela
      (`FlowsRecipesSection.tsx::ACOES`), cada um com confirmação e um campo de motivo, pela rota de sempre
      (`POST /api/skills/{id}/versions/{n}/status`,
      [contrato](api-contract.md#adendo-v023-27092026--ensino-v2-e-habilidades-no-http)):
      - rascunho → "Submeter" (o conteúdo congela);
      - candidata → "Validar": sem motivo, pelas observações registradas; com motivo, é a validação manual do dono
        (P4, `manual: true`);
      - validada → "Publicar"; publicada → "Recolher"; substituída → "Publicar de novo" (rollback);
      - "Desabilitar" em candidata, validada, publicada e substituída (terminal). Desabilitada não tem ação.
    - **A recusa do domínio aparece na linha da versão**, não só num aviso passageiro: o `code`, a `message` e as
      listas `errors` e `pending` (por exemplo, 409 `validation_pending` com as pendências).
    - O texto do `TeachingPanel`, depois do rascunho, diz os passos que existem: submeter, validar e publicar a versão
      nesta lista (antes mandava publicar aqui, onde não havia ação).
  - **Converter um fluxo em habilidade** (fase J), na lista "Fluxos" da mesma seção:
    - fluxo ativo sem habilidade publicada que o adotou ganha "Converter em habilidade" (também depois de desfazer:
      é a readoção pela mesma habilidade): confirmação com motivo opcional →
      `POST /api/flows/{id}/adopt`; nascem a v1 publicada (o plano do fluxo) e a v2 em rascunho, e o fluxo fica
      desligado. A recusa da conversão (422, com os erros da ida e volta) aparece na linha do fluxo;
    - fluxo com habilidade publicada mostra "habilidade `<ref>`", o botão "Desfazer conversão"
      (`POST /api/flows/{id}/release`, motivo opcional) e o interruptor de ativo **travado** (religar por ali o backend
      recusa com 409 `flow_adopted`);
    - "Excluir" fica bloqueado enquanto houver habilidade que adotou o fluxo, com o motivo no botão (o backend exige
      o mesmo: 409 `flow_adopted`).
    - Prova `simulated`: `FlowsRecipesSection.test.tsx` ("desligado: um fluxo ativo não ganha…", "ligado: converter
      o fluxo ativo…", "ligado: fluxo adotado mostra a habilidade…", "ligado: a recusa da conversão aparece…",
      "ligado: cada versão tem as transições do seu estado…", "ligado: a recusa da transição mostra…").
      Conferência visual no navegador: `not_run`.
  - **Desligado, nada muda.** Sem o campo ou com `false`, o painel é o de antes: nenhuma chamada nova e nada novo na
    tela (`TrainingReview.tsx::ensinoV2`, `FlowsRecipesSection.tsx::skillsOn`). Prova `simulated`:
    `TrainingReview.test.tsx` ("com features.skills desligado (padrão), a revisão é a de sempre…") e
    `FlowsRecipesSection.test.tsx` ("desligado (padrão): … sem habilidades nem chamada a /api/skills"), mais os
    casos ligados. Conferência visual no navegador: `not_run`.
  - A correção de uma etapa que falhou e a execução comprovada como exemplo existem só na API, não no painel.

## 4. Limitações e exclusões por decisão

De [docs/plano-100.md §6](plano-100.md) (o que não dá para provar com o hardware de hoje) e
[§7](plano-100.md) (o que fica fora, por decisão — não por lacuna técnica):

- **Desafio, CAPTCHA e 2FA são sempre manuais.** O sistema nunca tenta resolvê-los; vira `AUTH_CHALLENGE` e espera
  uma pessoa. O plano melhora a fila de intervenção em volta (item 6.4), não automatiza o desafio.
- **Sem evasão de detecção** de emulador ou de antibot. Multi-conta em emulador pode ser bloqueada pela
  plataforma; o projeto não contorna isso.
- **APK só da Play Store**, com a conta Google do dono (via emulador-loja), ou de um arquivo que o dono forneça —
  nunca de espelho de terceiros; `apks/` fica fora do Git.
- **Senha nunca em resposta, log, evento, evidência, captura, prompt, memória, fixture ou Git.**
- Dois cenários ficam sem prova possível no hardware atual: worker fora da LAN (falta máquina fora da rede local)
  e worker Linux (falta máquina com KVM); o mecanismo para o primeiro existe (item 4.5 do plano), a prova, não.

## 5. Critérios de sucesso — os 9 aceites

O pedido de execução distribuída definiu 9 aceites. Há dois registros datados, e eles **não foram fundidos**:
[docs/plano-100.md §2](plano-100.md) (21/09/2026, auditoria inicial) e
[docs/relatorio-validacao.md §13](relatorio-validacao.md) (23/09/2026, tabela viva — é o texto que manda em caso
de conflito, por ser mais recente e ter ids). Um resumo de §13, com a ressalva do próprio arquivo: **toda prova
real do lado distribuído é de 19–21/09, anterior às fases 0–10 do plano**; o trabalho de código das fases (itens
0.1–13.3, ver `.claude/plano-100/estado.json`) rodou em 23–24/09 mas **não tornou a exercitar o parque real** —
cada blocker relevante nesse arquivo diz textualmente que o ensaio ao vivo "não foi executado" por proibição da
chamada que fez a implementação. Ou seja: em 24/09 o código dos 9 aceites está majoritariamente implementado, mas
a validação em infraestrutura real segue no mesmo ponto de 23/09.

| # | Aceite | Estado em 23/09 (§13) | O que muda com o código de 23-24/09 |
|---|---|---|---|
| 1 | Mesma operação em local e remoto | Local: `hibernate`/`wake`/`start`/`stop`/`open_app` provados. Remoto: só `stop` chegou a `succeeded`; `start` remoto ficou `uncertain` (`c-20260921172322-6f7fdc`, aberto desde 21/09); `hibernate`/`wake`/`restart`/`reset`/`create` nunca foram despachados a um worker | Item 1.2/1.7 implementados (`proof: real`/`simulated`, ver estado.json); a reconciliação do `uncertain` acima e um novo ensaio remoto **não** foram refeitos |
| 2 | Workflow completo remoto acompanhado pelo painel | Só leitura, 19/09, sem efeito externo, sem imagem, sem verificação por modelo | Item 1.8 implementado (simulado); ensaio real com efeito externo continua pendente |
| 3 | Controle manual de tela remoto | Só por log de eventos, sem `command_id`; sem roteiro pelo painel registrado | Sem item dedicado; segue não feito |
| 4 | Instalar/abrir app ≠ Instagram em remoto | QA Messenger nos 6 remotos (19/09); pelo catálogo de releases, nunca; conjunto completo do Instagram, só em 6.6 (23/09, prova real citada abaixo) | Item 6.6: `implemented`/`real` — conjunto real do Instagram instalado em 6 remotos, 23/09 |
| 5 | Distribuição entre dois workers | Impossível: só um worker inscrito | Item 2.1 (`LocalWorker`) implementado, simulado; ainda falta uma **segunda máquina física** — decisão e hardware do dono |
| 6 | Queda e reconexão com reconciliação | Túnel reconecta em ~5s; matar o agente durante um comando, não testado ao vivo | Item 1.4 implementado, proof `not_run`: "falta o ensaio REAL... derrubar o túnel durante um start/reset" |
| 7 | Reinício de API/scheduler sem perder tarefa | Provado só localmente (17/09), anterior às tabelas `commands`/`workers` de hoje | T.1 e item 0.1: backup/deploy reais em produção (24/09), mas reinício com comando **remoto em voo** continua não exercitado |
| 8 | Cancelamento/duplicada/incerto sem repetir efeito | Idempotência real comprovada; cancelamento remoto, não | Item 1.6 implementado, simulado ("não exercitado no mundo real: cancelar um start de verdade... os testes usam agente falso") |
| 9 | Bloqueio de execução concorrente | Só etapa×etapa num processo | Item 1.3 implementado, mas o "ensaio AO VIVO... não foi feito: é fechamento de fase" |

Leitura recomendada para retomar o trabalho: comece por
[docs/relatorio-validacao.md §13.1](relatorio-validacao.md) — lista, aceite por aceite, o roteiro já escrito e
pronto para rodar (`scripts/aceites-remotos.ps1`, `scripts/test-restart-recovery.ps1`), pendente só de autorização
para tocar o parque real.

## 6. Matriz de funcionalidades

Fonte: `.claude/plano-100/estado.json` (88 itens; campos `status`/`proof`/`evidence`/`blocker`, gerado em 24/09)
e `docs/relatorio-validacao.md`. **Regra de leitura do `proof`:** `real` só conta como ambiente real quando a
evidência cita data+máquina+id **e** o campo `blocker` não diz que o ensaio ao vivo ainda falta — vários itens
marcados `real` têm blocker afirmando o oposto (ex.: 5.6/5.7 "não há broker/MinIO nesta máquina"; 1.3/3.4/3.5/4.5
"ensaio real pendente"); esses entram como **"real parcial"** abaixo. `simulated` = só teste automatizado.
`not_run`/`pending` = não executado. `tests`/`unit` (10.5, 11.10, 12.1, 12.2, 13.1–13.3) = escrito à mão pelo
próprio executor do item, sem conferência externa — tratado aqui como **automatizada**, não como prova
independente.

| Funcionalidade | Implementação | Validação | Origem |
|---|---|---|---|
| Ciclo de vida local honesto (start/restart/reset esperam o boot) | `backend/app/api.py` (`PRAZO_POR_VERBO`, `VERBOS_QUE_ESPERAM_O_BOOT`) | Real parcial — código medido, ensaio dedicado de aceite não refeito | plano-100 1.2 |
| Um aparelho, uma operação (409 `device_busy`, trava por aparelho) | `backend/app/api.py` (`VERBOS_EXCLUSIVOS`, `_precheck`) | Real parcial — camadas central+agente testadas; ensaio AO VIVO (aceite 9) não feito | plano-100 1.3 |
| Queda de conexão não cancela o trabalho | `backend/app/worker/agent.py` (`finally` não cancela; diário de resultados) | Não executado — falta derrubar o túnel real durante um `start`/`reset` | plano-100 1.4 |
| `uncertain` com saída (reconciliação + decisão humana) | `backend/app/commands/reconciler.py` | Simulada | plano-100 1.5 |
| Cancelamento de ponta a ponta | `POST /commands/{id}/cancel` (`backend/app/api.py`) | Simulada — não exercitado com worker real | plano-100 1.6 |
| Remoto produz os mesmos efeitos que o local (`desired_state`, `hibernated`+snapshot, `wake`) | `backend/app/devices/manager.py` (`aplicar_desfecho_remoto`) | Simulada | plano-100 1.7 |
| Painel acompanha por evento (sem teto de 210s) | `frontend/src/features/devices/actions.ts` | Simulada | plano-100 1.8 |
| `LocalWorker` — central vira worker de si mesmo | `backend/app/workers/local.py` | Simulada — `worker/executor.py` não virou núcleo comum, por decisão declarada | plano-100 2.1 |
| Capacidades declaradas por worker (imagem, API, ABI, GMS…) | migração 019, `backend/app/devices/compatibilidade.py` | Simulada — `appium: local` real não ensaiado | plano-100 2.2 |
| Saúde do convidado além de `boot_completed` | `backend/app/devices/adb.py` (`framework_alive`) | Real — mas limpeza de dado herdado em produção não executada | plano-100 3.1 |
| Estado de app/sessão com identidade física (serial) | `backend/app/devices/manager.py` (`_esquecer_o_que_o_disco_tinha`) | Real | plano-100 3.2 |
| Sessão com validade (desafio/login atualiza o perfil) | `backend/app/taskqueue/executor.py` (`_sessao_desmentida`) | Real | plano-100 3.3 |
| Saúde ao vivo (`health.updated`) | `backend/app/state.py` (`_health_loop`) | Real parcial — readoção do Appium órfão em produção não confirmada ao vivo | plano-100 3.4 |
| Túnel como componente monitorado | migração 021, `backend/app/state.py` (`_probe_*`) | Real parcial — queda real do `ssh.exe` de produção não exercitada nesta chamada | plano-100 3.5 |
| Execução registra onde rodou (`worker_id`, serial, backend) | migração 022 | Real | plano-100 4.1 |
| Scheduler ciente de worker (vagas/slots por máquina) | `backend/app/taskqueue/scheduler.py` (`_rotate`) | Simulada — ensaio de carga real (6 contas/3 vagas) não feito, por tocar produção | plano-100 4.2 |
| Pré-voo na criação da execução | `RunService.pre_voo` (`backend/app/taskqueue/service.py`) | Real | plano-100 4.3 |
| Localidade de perfil (worker+aparelho físico) | migração 023 | Real | plano-100 4.4 |
| Servidor/aparelho novo sem editar YAML (conferência de inventário) | `backend/app/devices/manager.py` (`conferir_inventario`) | Real parcial — ensaio com worker fora da LAN não feito (falta máquina) | plano-100 4.5 |
| Hospedeiro por backend (`hosted_by`), `ROLE=api\|scheduler\|all` | migração 027 | Real | plano-100 5.1 |
| Limite de IA global por lease no banco (`ai_slots`) | `backend/app/taskqueue/ai_slots.py` | Real | plano-100 5.2 |
| Guarda de relógio entre backends | `backend/app/db.py` (`desvio_do_relogio`) | Real | plano-100 5.3 |
| PostgreSQL de produção (reconexão, `/health`, migração 018/028) | `backend/app/db.py` | Real parcial — perna PostgreSQL da suíte não rodou nesta chamada (falta `TEST_DATABASE_URL`) | plano-100 5.4 |
| Cofre entre backends (`key_id`) | `backend/app/security/secret_store.py` | Real | plano-100 5.5 |
| Outbox + transporte NATS (atrás de bandeira) | migração 029, `backend/app/commands/outbox.py` | Real parcial — sem broker NATS nesta máquina, não exercitado ao vivo | plano-100 5.6 |
| Storage de evidências (disco/S3) | `backend/app/storage.py` | Real parcial — sem MinIO nesta máquina, não exercitado ao vivo | plano-100 5.7 |
| Catálogo de apps (fora do `if package == instagram`) | `backend/app/modules/applications/infrastructure/registry.py` (manifesto de app, fase K1; `planning/catalog/__init__.py` é shim) | Simulada | plano-100 6.1; [apps](dominios/apps-e-loja.md#manifesto-de-app-fase-k1) |
| Comandos de app (install/canary/rollback/distribute/verify) | `backend/app/api.py` (`APP_COMMAND_VERBS`) | Simulada | plano-100 6.2 |
| Catálogo visual (ícone, versão, upload) | `backend/app/releases/inspector.py`, `catalog.py` | Simulada — `install_apk` pelo painel restrito, pendências declaradas | plano-100 6.3 |
| Fila "aguardando intervenção" | `backend/app/devices/manager.py` (hook `on_control_released`) | Real | plano-100 6.4 |
| VM da loja pode ser remota | `backend/app/config.py` | Simulada | plano-100 6.5 |
| Conjunto real do Instagram instalado em remoto | `install-multiple` via túnel | **Real** — 23/09, 21:02–21:05, android-09/10/12/13/14/15 | plano-100 6.6 |
| Hub de IA — provedor por função, roteamento | `backend/app/planning/routing.py` | Real | plano-100 7.1 (`implemented`/`real`: ator no Ollama local medido em produção em 24/09, `ad48634`; vLLM nunca exercitado) |
| Fallback explícito por função + preço cadastrado | migração 032 | Simulada — decisão do dono sobre o padrão global não tomada | plano-100 7.2 |
| Interface distingue aguardando/vaga/resposta/recusa | `backend/app/taskqueue/executor.py` | Real | plano-100 7.3 |
| Bateria de avaliação (linha de base vs. atual) | `scripts/eval-run.ps1`, `scripts/eval-rejudge.ps1` | Real parcial — falta rejulgar as 56 capturas com o modelo caro | plano-100 7.4 (`partial`) |
| Cache de prompt sempre ligado + escalonamento por risco | `backend/app/planning/anthropic_provider.py` | Real — medido: Instagram 2/3→3/3, US$0,20→0,08/caso | plano-100 7.5 |
| Dieta de contexto do ator (−tokens por decisão) | `backend/app/planning/prompts.py`, `hierarchy.py` | Real | plano-100 7.6 |
| Estimativa de custo antes de rodar | `GET /api/flows/cobertura` | Real | plano-100 7.7 |
| Modelo local (Ollama) preparado, piso de conteúdo | `backend/app/planning/openai_provider.py` | Real parcial — Ollama real não exercitado no teste do item (não instalado no momento do item); **ligado em produção depois, 24/09** (ver `docs/ia.md` §10) | plano-100 7.8 |
| Personas completas + prova antes/depois | `scripts/personas_completar.py` | Não executado — preencher as 8 personas é decisão e gasto do dono | plano-100 8.1 |
| Memória de DM com resumo real | `backend/app/social/` | Não executado — aceite de nível 2 (conversa real entre duas contas) | plano-100 8.2 |
| Sinais e limites (classificador, teto diário) | `backend/app/social/` | Real parcial — `REPLY_COMMENT` em aparelho real não confirmado | plano-100 8.3 (`partial`) |
| Instagram operando em worker remoto | — | **Bloqueado** — autorização do dono pendente (instalar app real em aparelho remoto) | plano-100 8.4 (`blocked`) |
| Login/sessão do painel com identidade | `backend/app/security/sessions.py` | Real | plano-100 9.1 |
| TLS na porta de rede | `backend/app/config.py` (`tls_cert`/`tls_behind_proxy`) | Real | plano-100 9.2 |
| Credencial de worker revogável/rotacionável | `backend/app/workers/registry.py` (`rotate_credential`) | Real | plano-100 9.3 |
| Túnel com usuário restrito | `scripts/worker-ssh-restrito.ps1` | Não executado — feito parcialmente em 23/09 (digital de host conferida), resto pendente | plano-100 9.4 |
| Redação de dado sensível (URL, `Authorization`, chaves) | `backend/app/security/redaction.py` | Real parcial — job de CI de dependências nunca rodou de fato neste repositório | plano-100 9.5 |
| Tudo volta sozinho (tarefas supervisionadas) | `scripts/install-central-service.ps1`, `scripts/worker-agent.ps1` | Real | plano-100 10.1 |
| Crescimento sob controle (retenção, rotação de log) | — | Real parcial — envio de log do agente sob demanda não implementado | plano-100 10.2 |
| Capacidade (alvo, alerta, teste de escala) | `scripts/scale-test.ps1` | Real | plano-100 10.3 |
| Worker Linux | `backend/app/worker/` | Não executado — falta máquina Linux com KVM | plano-100 10.4 |
| Limites por servidor + distribuição entre servidores | migração 039, `frontend/src/features/settings/ServersLimits.tsx` | **Automatizada** (`proof: unit`) — 15 testes; sem ensaio de carga real registrado aqui | plano-100 10.5 |
| Usabilidade do painel (11.1–11.9: rolagem, tabelas, perfil, diagnóstico, instâncias) | `frontend/src/features/*` | Real | plano-100 11.1–11.9 |
| Grupos de acesso (política herdável por grupo) | migração 036, `backend/app/social/policy.py` | **Automatizada** (`proof: tests`) | plano-100 11.10 |
| Contas por app (`profile_accounts`, `app_id` por etapa) | migração 037 | **Automatizada** (`proof: tests`) | plano-100 12.1 |
| Telas de Perfil/Aplicativos por app | `frontend/src/features/profiles/` | **Automatizada** (`proof: tests`) | plano-100 12.2 |
| Apps novos operando de verdade (Outlook, TikTok…) | — | **Não iniciado** — decisão do dono sobre qual app vem primeiro | plano-100 12.3 (`pending`) |
| Modo treinamento — gravação | migração 038 | **Automatizada** (`proof: tests`) | plano-100 13.1 |
| Modo treinamento — generalização assistida (`generalize`) | `backend/app/planning/training.py` | **Automatizada** (`proof: tests`) | plano-100 13.2 |
| Modo treinamento — telas | `frontend/src/features/` | **Automatizada** (`proof: tests`) | plano-100 13.3 |

Itens de `.claude/plano-100/estado.json` não listados linha a linha acima (fase 0 completa, T.1–T.3) estão
resumidos no §5; para o detalhe por item, ler o próprio `estado.json` ou
[docs/execucao-plano-100-runner.md](execucao-plano-100-runner.md) (arquivo gerado, não editar — tem `arquivo:linha`
por item).

## 7. Lacunas para o backlog

Extraídas desta varredura, para o coordenador priorizar (não implementadas aqui):

- Nenhum dos 9 aceites de execução distribuída tem ensaio **ao vivo** completo desde 23/09 — a fase de código
  (23–24/09) não voltou a tocar o parque real. Roteiro pronto em `docs/relatorio-validacao.md §13.1`.
- Item 12.3 (apps novos operando de verdade) está `pending`: decisão do dono sobre qual app vem primeiro nunca
  foi tomada.
- Item 8.4 (Instagram num worker remoto) está `blocked`: falta autorização para instalar o app real (~238 MB) em
  aparelho remoto.
- Itens com proof `tests`/`unit` escrito pelo próprio executor (10.5, 11.10, 12.1, 12.2, 13.1–13.3) não têm
  conferência independente registrada — candidatos a auditoria cruzada antes de contar como fechados.
- Item 9.4 (túnel com usuário restrito): só a digital de host foi conferida em 23/09; o restante do endurecimento
  do túnel segue pendente.
- Item 8.1 (personas completas): 8 dos 15 campos de voz seguem vazios nas personas reais (achado #107 do
  plano-100), decisão e trabalho do dono.
