# Changelog

Mudanças sustentadas pelo Git (`git log`) e, quando houver, pela evidência registrada. **Não há versões nem releases**:
o código declara `VERSION = "0.1.0"` (`backend/app/version.py`) desde o início, não há tags, e a implantação é
contínua a partir da `main` (commit direto, por escolha do dono — ver [ADR em decisoes.md](docs/decisoes.md)).
Por isso as entradas são por **data**, com o commit que as sustenta.

Três estados diferentes, que não se confundem:

- **integrado** — está na `main` (`origin/main`);
- **implantado** — é o commit que a produção responde em `GET /api/health` (`commit`, `migration`);
- **validado** — tem prova registrada em [`docs/relatorio-validacao.md`](docs/relatorio-validacao.md) ou no livro-razão
  do plano-100 ([`docs/execucao-plano-100-runner.md`](docs/execucao-plano-100-runner.md), coluna Prova).

Implantado em 25/09/2026 ~14:19 UTC (conferido no `/api/health` do central): `8169fd3`, migração `039_limites_por_servidor`,
`cryptography` 50.0.0 no venv; agente do worker `worker-lan-01` em `0.1.0+c0c982d` (o central o marca
`agent_outdated`, esperado `0.1.0+e6b00db`).

Ao fechar uma tarefa, acrescente a linha no dia dela (skill `fechar-tarefa`). Mudança só de documentação entra em
"Documentação e processo".

## 2026-09-26 — loja de aplicativos e proxy do aparelho (não implantado)

Branch `claude/loja-de-apps`. Prova `simulated` (`backend/tests/test_loja_de_apps.py`,
`frontend/src/features/loja/LojaPage.test.tsx`, painel no navegador contra o harness com aparelhos falsos). Nada foi
instalado nem configurado em aparelho real: `not_run`. Os testes rodaram só em SQLite; PostgreSQL `not_run` (o
contêiner de teste da porta 55433 não estava no ar, e subir o Docker mexe no WSL).

### Código
- Aba **Loja** no menu Aplicativos. A vitrine (`GET /api/app-store`) mostra o ícone, a versão promovida, os
  aparelhos por versão e a "atualização para N". Há cadastro de app com categoria (migração 041), envio de
  APK/XAPK e a Play Store da loja por app.
- `distribute` ganhou `instance_ids`, `count` e `dry_run`. O painel distribui para todos, N ou os escolhidos, com
  prévia obrigatória, e "Atualizar para X" marca quem está atrasado. A volta de versão pode ser em lote.
- Versão de pacote não cadastrado cadastra o app sozinha. App que não é o principal do aparelho instala quando ele
  liga.
- Proxy do aparelho: aba **Proxy**, `/api/proxies*`, comando `device.proxy`, conferido por releitura de
  `settings global http_proxy`.


## 2026-09-26 — a automação entra com a credencial que a pessoa fornece (não implantado)

Branch `claude/credenciais-na-automacao`. Decisão do dono (ADR-025). Prova `simulated`
(`backend/tests/test_credenciais_da_execucao.py`).

### Código
- `POST /api/runs`: campo `credentials` (cofre, apagado no fim da execução) e consentimento explícito
  (`consentimento_de_credencial`); comando com senha no texto é recusado antes de gravar (`credencial_no_comando`) e
  `runs.command` passa pela redação. Origem: execução `22d65f`, cuja senha ficou em claro no banco e foi ao planejador.
- Ferramentas `type_secret` (canal sensível, só campo de senha, só no app da etapa e no site pedido) e `open_url` (só
  endereço do comando); tela de senha não para a execução que tem credencial; desafio e CAPTCHA continuam com a
  pessoa.
- Revisão local (code-review xhigh): credencial mantida em `completed_with_issues` e varrida após 24 h parada; 422 sem
  eco de valor sensível; cofre antes da execução (nada órfão); `usuário:senha@` em URL recusado; texto citado não
  tira o comando do catálogo; painel não guarda nem envia comando com senha e limpa o histórico antigo.
- Segunda rodada: só o texto do comando autoriza endereço (`open_url`) e site da senha — parâmetro do plano não;
  `)` que faz parte da URL fica; "página" não tira comando do Instagram do catálogo; execução cuja credencial não
  se ligou vai a `failed` em vez de ficar em `planning`.
- O planejador não fica preso ao catálogo do app do aparelho quando o comando pede site ou outro app; Chrome no
  `config.example.yaml`. Prompts: regra de conduta (sem desinformação, sem ofensa explícita).
- Painel: campo "Senha para a automação" (só em memória) e confirmação antes de criar a execução.

### Operação
- 26/09 16:40 UTC: senha da execução `r-20260926161438-22d65f` mascarada em `runs.command` no banco de produção.

## 2026-09-26 — prontidão por subsistema (implantado: `5b81c1a`, conferido em `/api/health`)

Branch `claude/prontidao-por-subsistema`. Prova `simulated` (`backend/tests/test_prontidao_subsistemas.py`).

### Código
- Pronto = servicemanager + system_server + display respondendo (`devices/prontidao.py`), a mesma definição no
  worker (`start`/`wake`) e no central (entrada no ar). Preparo que estoura o prazo não fecha mais `succeeded` com
  o `service check` sozinho (a lacuna do wake de 25/09).
- Contrato temporal: estouro de prazo no preparo ou no acerto do relógio (worker e central, boot, wake, readoção e
  adoção externa) deixa a tentativa não pronta — efeito incerto no aparelho; a chamada zumbi do executor é drenada
  com teto antes de devolver. Erro rápido depois da prontidão exige rodada nova (`AdbError` pode ser `device
  offline`); erro benigno segue sem bloquear. Limitação conhecida: efeito tardio de um timeout no mesmo guest
  (`input tap` do diálogo, `cmd alarm set-time`) não é isolado entre tentativas — tarefa separada.


## 2026-09-26 — identidade do backend em /api/health (não implantado)

Branch `claude/supervisor-identidade`. Prova `simulated` (`backend/tests/test_identidade_do_backend.py`).

### Código
- `Health.service = "android-farm-central"`; o supervisor só trata como "backend vivo" o health que identifica a
  Farm (com reconhecimento legado estrito do esquema antigo). O 404 do `cartorio-api-1` na 8000 não segura mais a
  subida. `deploy`/`start`/`stop`/`restore`/`loja-janela` usam a mesma regra (`scripts/lib/farm-health.ps1`); o
  `stop.ps1` não envia mais o token de encerramento a quem não for a Farm.

## 2026-09-25 — prontidão real e sondas com trilha própria (não implantado)

Branch `claude/prontidao-e-sondas`. Prova `simulated` (`backend/tests/test_prontidao.py`); o wake remoto que a
motivou não foi reproduzido (evidência preservada no worker).

### Código
- `online` e `start`/`wake` do worker exigem o framework respondendo (ANDROID_RESPONSIVE), não só adb +
  `boot_completed`; `InstanceDTO.readiness` mostra o degrau; framework mudo = `booting` com motivo, depois degradado.
- Sondas de saúde, pressão e internet numa trilha própria por aparelho: a captura travada não as cala mais.

## 2026-09-25 — validação runtime do android-06 (fase local, implantado `9acba15`)

Branch `claude/awesome-lamport-s602ai` (PR #4), implantada no central. Prova `real`: ver
[`docs/handoffs/android-device-persona-runtime.md`](docs/handoffs/android-device-persona-runtime.md).

### Código
- Internet do aparelho separada de `online` (`devices/conectividade.py`, `InstanceDTO.connectivity`): sonda adb só
  leitura (rota, DNS, TCP 443, `VALIDATED`, 2ª tentativa), `unknown` a cada entrada no ar, aviso "sem internet: …",
  `409 device_no_internet` no Conectar; linha "Internet" no contexto operacional.
- `android.dns_servers` por máquina → `-dns-server` (o emulador só usava o 1º DNS IPv4 do host, que estava morto).
- Popover em posição fixa presa à tela (o menu "Instalar app" cortava a versão).
- "Verificar app" carimba `verified_at` também quando o app está ausente; hibernar remoto diz a causa real.
- Despacho: `AppCapabilities.requires_internet`; tarefa de app que precisa de rede espera aparelho sem internet
  confirmada (tarefa local segue).
- Boot remoto em andamento é `booting`, não `error` "system_server caiu".
- Login real do André no android-06: `session_ready` pelo @ lido na tela; observação pós-envio 25 → 45 s.

## 2026-09-25 — aparelho × persona × app × sessão (fase cloud)

Branch `claude/awesome-lamport-s602ai`. Prova `simulated`; a validação real está em
[`docs/handoffs/android-device-persona-runtime.md`](docs/handoffs/android-device-persona-runtime.md).

### Código
- Portão único de sessão (`social/sessao_gate.py`): Conectar/Verificar conta/Sair só com o app observado no aparelho;
  o perfil traz `app_on_device` e `session_actions`, e a rota recusa com `409 app_not_installed`/`app_not_verified`.
- "Instalar app" diz app e versão promovida antes do clique; sem versão promovida, recusa antes do 202;
  `install_target` na resposta. Instalar e abrir escolhem o app cada um.
- `InstanceDTO.stream` separa `stale` de `device_offline`, `worker_offline` e `capture_error`; a captura conta
  falhas, publica a primeira e recua até 30 s.
- Diálogo "Unable to log in" registrado como `login_error_dialog` (incerto, sem repetir sozinho).
- `GET /api/instances/{id}/operational-context` e `GET /api/instagram/profiles/{id}/operational-context`, com cartão
  no Foco e no perfil.

## 2026-09-25 — CI verde, deploy com dependências, documentação e continuidade

Implantado no central às ~01:20 UTC (`scripts/deploy.ps1 -PularFrontend`, backup `data/backups/20260924-221919`).

### Código
- 7.9: o aviso de IA diz de qual função é a frase "os dados NÃO saem desta máquina" quando o ator é local e o resto
  é externo (`planning/routing.py`).
- 10.6: teto de `boot_parallelism` igual (10) no painel, no `config.yaml` e na mensagem `limits`; comentário do
  protocolo corrigido (os limites vão na primeira batida, não junto do `welcome`).
- T.4: CI de volta ao verde — chave de teste do cofre fora do Windows, testes de PowerShell só no Windows, inspetor
  de APK lê o formato `V2 Signer:` do `apksigner` novo (defeito real), o mock de frame do Foco não depende do `Blob` do jsdom (falhava no Node 22 do CI), a saúde
  dos testes não depende de SDK/KVM do host,
  `cryptography` 50.0.0 (a 46.0.7 ainda tinha avisos; o uso do projeto é só `AESGCM`/`InvalidTag`). CI verde no run
  36078946300 (`9e12baf`).
- Deploy: `scripts/deploy.ps1` instala as dependências do backend entre parar e subir (`e6b00db`); antes, versão nova
  no `requirements.txt` nunca chegava à produção.

### Decisões delegadas (custo-benefício), implantadas em `8169fd3`
- 7.10 (ADR-024): verificador Haiku mantido, com rejulgamento escalado quando recusa com nível de entrega suficiente
  e guarda contra "sim" sobre tela sem elementos — em vez de trocar o modelo do verificador (2× o custo).
- 7.11 e ADR-023: `/api/health` acusa IA em fallback; o ator volta a ser declarado no Sonnet 5 (config de produção).

### Validação
- Bateria de avaliação autorizada (ADR-018), ~US$ 2,57: rejulgamento 41/56, linha de base 16/17 (`base-25-09`),
  HTTP 500 do verificador em 0,7 %. Item 7.4 registrado como `partial`/`real`. Ator no fallback (Ollama fora do ar).

### Documentação e processo
- Base de documentação e continuidade: `CLAUDE.md`, índice [`docs/README.md`](docs/README.md), produto, arquitetura,
  domínios, IA, operação, decisões (ADR), knowledge lake, roadmap, este changelog e o handoff
  [`docs/estado-atual.md`](docs/estado-atual.md); skills `retomar`, `preparar-tarefa`, `fechar-tarefa`; regras por
  caminho em `.claude/rules/`; `scripts/docs-check.py`.
- plano-100: o item 10.5 entrou no mapa de blocos (`check`/`relatorio` estavam quebrados desde `c0c982d`); relatório
  de execução regenerado (82 de 88); `scripts/tests/test_claude_plan_100.py` reescrito para o livro-razão atual.
- Decisão 2 do plano-100 tomada pelo dono: sem revogação da chave (ADR-017); 0.10 registrado por `aplicar` (83 de 88).

## 2026-09-24 — custo de IA, painel, perfis multi-app, treinamento, limites por servidor
- Custo de IA: cache de prompt sempre, preço do Opus 5.5, escalonamento por risco, provas locais (`ac18099`); dieta
  do contexto do ator e estimativa antes de rodar, 7.6/7.7 (`42f8e93`); modelo local preparado, 7.8 (`b3addfe`,
  `ac6bf69`); ator de produção ligado no Ollama local, 7.1 (`ad48634`).
- Painel: layout responsivo, 11.1–11.4 (`d305899`); seleção por estado e "Repetir", 11.5 (`6ada25a`); perfil como
  pessoa e Diagnóstico prático, 11.6/11.7 (`cb2d437`); configurações do perfil e instâncias, 11.8/11.9 (`5a60aa3`);
  rolagem do foco e teste de escala com 14 aparelhos, 10.3 (`d8a57f7`).
- Perfis: memória semeada do histórico social; decisões 3 e 4 aplicadas, 0.10/6.5 (`728e2ad`); o que o perfil vê vira
  memória (`2fa2280`, `327f7b9`); grupos de acesso, 11.10 (`a4237da`, `95ee764`); contas em vários apps e menu
  Aplicativos por app, 12.1/12.2 (`407cfce`, `6530d4d`).
- Modo treinamento, 13.1–13.3 (`bfffb0d`, `bf29d48`).
- Parque: limites por servidor e distribuição entre servidores, 10.5 (`c0c982d`); distribuir app com conta só para
  aparelho com perfil ativo (`944f158`, `f443a90`).
- plano-100: 0.1, 0.7, 6.6 e 10.1 fechados com operação real de 23–24/09 (`cc3e960`).

## 2026-09-23 — plano-100, fases 5–10 e T; operação
- Fases 5 (dois backends, fila, storage, PostgreSQL) `a45b95f`; 6b (catálogo visual, fila de intervenção, loja)
  `00330b9`; 7 (hub de IA, teto em dólar) `f94f918`; 8 (persona governa o texto) `f5015a6`; 9 (sessão nominal, TLS
  fora do loopback) `57f8a3c`; 10 (supervisão, retenção) `2564d56`; T (os nove aceites em tabela, relógio
  injetável) `4cee0a9`.
- Operação: `/docs` fechado no listener do túnel e deploy reconstrói o painel (`e03d967`); partida para quando a
  configuração some (`09c040f`); cache do `index.html` (`4204ed8`); correções de agente, supervisor e hibernação
  (`a0b211c`, `8caeb2e`); chave de host do worker conferida pelo dono (`98b4669`).
- Execução: reparo automático, personas completas (`c76ccea`); receitas sem o alvo por extenso (`aa8a43c`); fim do
  laço tocar→voltar, receita vazia e ordem no replano (`fd5a26f`).

## 2026-09-22 — plano-100, fases 0–4 e 6a; executor na sessão da IDE
- O runner externo (`claude -p`) é aposentado; a sessão orquestra com pacote e modelo por item (`21e11e9`), depois
  de `cca458d`/`352ee3e`.
- Fase 0 (backup, desvio do token, túnel com credencial, DM longa, CI) `c727284`, `db07776`; fase 1 (comando
  honesto nos dois caminhos) `7ec4cf2`; fase 2 (o central vira worker; capacidades declaradas) `1a206e0`; fase 3
  (saúde verdadeira) `f30c452`; fase 4 (escalonamento por recursos e localidade) `abe0aa2`; fase 6a (Instagram fora
  do núcleo, instalar como comando) `5e206c2`.

## 2026-09-21 — comandos, worker de verdade, PostgreSQL, auditoria
- Comandos como entidade (`074d27a`), capacidades explicadas antes de agendar (`a6307c4`), estado desejado
  (`785b9b7`), ciclo de vida no worker provado na máquina dele (`89f5508`), tela de infraestrutura (`bda8a0d`).
- SQLite e PostgreSQL provados (`c7feab4`); posse de etapa e autenticação entre backends (`1162e07`, `f1e61b3`).
- Auditoria de 181 achados e o plano-100 (`53507d2`); primeiro executor do plano (`59bc422`); PR #2 integrado.

## 2026-09-19 — persona e aprovações medidas; parque remoto (Etapa 0)
- Persona escreve na própria voz, lê a tela e quem escreveu; aprovação aprova o texto que será digitado; revisão
  adversarial registrada (`6d9e270`, `c566fdf`, `580dc42`, `285e1df`, `858a0ea`).
- Appium central dirige aparelho de outra máquina (`139b06d`); seis aparelhos remotos, 6 de 6 (`1931de3`, `c29212d`).

## 2026-09-18 — releases, loja Play Store e Instagram real
- Canário, promoção e rollback (`2aa3ef5`); aparelho-loja e "buscar da loja" (`4820e97`…`10ae9d5`).
- Login real do Instagram e leitura da conta (`d7075cd`, `f90433c` e seguintes); persona por perfil (`34c9b52`);
  aprovações agrupadas (`6f7d7fe`); suíte isolada dos emuladores reais (`36c8784`, `df9cb05`).

## 2026-09-17 — POC
- Painel React + backend FastAPI, Appium local, app de QA; custo de IA e rodízio N sobre K (PR #1, `5f3f7ea`);
  coleta e iteração sobre listas (`a127ae9`); canal de entrada sensível (`ec8cb07`); releases, perfis, Instagram,
  persona, memória, capacidades e portal (`c4c490b`…`21f276d`).
