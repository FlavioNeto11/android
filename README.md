# Central de Aparelhos — POC

Painel para controlar um **parque de emuladores Android independentes** — os desta máquina e os de outras, por
um agente (*worker*) — e executar tarefas em aplicativos por **comandos em linguagem natural**. A IA interpreta
o objetivo, monta um plano, observa a tela de cada aparelho, age pela interface (Appium/UiAutomator2), verifica
o resultado e relata **por instância** o que foi comprovado.

> Estado do que foi realmente testado nesta máquina: [docs/relatorio-validacao.md](docs/relatorio-validacao.md)
> — a **§13** separa, aceite por aceite, o que foi provado em *infraestrutura real* (com id de comando ou de
> execução, data e máquina) do que só passou em *simulação*. Execução distribuída:
> [docs/worker.md](docs/worker.md), [docs/parque-distribuido.md](docs/parque-distribuido.md) e
> [docs/banco.md](docs/banco.md); contrato da API em [docs/api-contract.md](docs/api-contract.md).

```
frontend (React+TS+Vite)  ──HTTP/WS──►  backend (FastAPI)
                                         ├─ devices/     AVDs, emulador, ADB, frames, lease IA×usuário
                                         ├─ automation/  Appium por aparelho (udid + systemPort exclusivos), ferramentas tipadas
                                         ├─ planning/    provedor de IA (Anthropic) + modo simulado identificado
                                         ├─ taskqueue/   fila persistente, máquina de estados, scheduler, executor, recuperação
                                         ├─ commands/    o comando como entidade: estado, cerca, diário de saída, incerto com saída
                                         ├─ workers/     inscrição, batida, capacidades e despacho para agentes de OUTRAS máquinas
                                         └─ events       eventos persistidos + WebSocket com retomada
                                              ▲
                                              │ WebSocket dedicado (túnel SSH)
                                    agente (backend/app/worker) em outra máquina: emuladores dela, mesmo contrato
SQLite (WAL) em data/poc.sqlite3 — ou PostgreSQL por `DATABASE_URL` · evidências em data/evidence · logs em data/logs · AVDs em data/avd
```

## 1. Pré-requisitos (Windows)

| Item | Versão usada | Observação |
|---|---|---|
| Windows 10/11/Server x64 com virtualização | Server 2025 | Com Hyper-V ativo o acelerador é o **WHPX** (recurso *Windows Hypervisor Platform*) |
| Python | 3.13 | o venv é criado com **`uv`** (`winget install astral-sh.uv` ou `pip install uv`) — `start.ps1` depende dele; dependências fixadas em `backend/requirements.txt` |
| Node.js | 24.x | Appium 3.7.0 + driver UiAutomator2 8.7.0 fixados em `tools/appium` |
| JDK | 21 | exigido pelo Android SDK/Appium e pelo build do APK de QA |
| Android SDK | emulator 37.1.11, platform-tools 37.0.1, imagem `android-34;google_apis;x86_64` | instalado em `C:\Android\Sdk` |

```powershell
# 1) diagnóstico do host (não altera nada) — rode como Administrador para ler os recursos do Windows
pwsh -File scripts\diagnose.ps1

# 2) Android SDK + Appium (idempotente). ATENÇÃO: aceita as licenças do Android SDK em seu nome.
pwsh -File scripts\install-prereqs.ps1

# 3) ambiente Python
cd backend; uv venv --python 3.13 .venv; uv pip install -r requirements.txt --python .venv\Scripts\python.exe; cd ..

# 4) frontend
cd frontend; npm ci; npm run build; cd ..
```

Se `emulator -accel-check` não disser *"WHPX … is installed and usable"*, habilite o recurso e reinicie:
`Enable-WindowsOptionalFeature -Online -FeatureName HypervisorPlatform` (ver a
[documentação de aceleração](https://developer.android.com/studio/run/emulator-acceleration)).

## 2. Configurar

* **`config/config.yaml`** — tudo que não é segredo: quantidade de instâncias, RAM/CPU/resolução/imagem (com
  `overrides` por instância), portas base, paralelismo de boot, limites de execução, capturas, timeouts,
  orçamento de IA, retenção, apps iniciais e o rótulo de conta de cada instância.
  **Ele não é versionado** (é o retrato da SUA instalação): o repositório traz `config/config.example.yaml`,
  neutro — 4 emuladores locais, sem aparelho remoto e sem loja. `start.ps1` copia o exemplo na primeira partida
  (e nunca por cima do que já existe); sem nenhum dos dois, o backend lê o exemplo. Ligue um bloco de cada vez
  (`external`, `store`, `overrides`) e confira `/api/health` depois de cada um. Como ele **não** está no Git,
  quem guarda a sua cópia é `scripts\backup.ps1`, que leva o `config/` inteiro junto com o banco.
* **`.env`** (copie de `.env.example`) — `ANTHROPIC_API_KEY`, `AI_PROVIDER`, `AI_MODEL`. A chave nunca vai para
  o frontend, para o banco ou para os logs. **Com o provedor real, screenshots e textos das telas são enviados à
  API da Anthropic** (o painel avisa); telas com campo de senha nunca são enviadas nem gravadas.
* Sem chave: gerenciamento, screenshots e controle manual funcionam; *Planejar/Executar* informam a pendência.
* `AI_PROVIDER=simulated` liga o **modo simulado de desenvolvimento** (regras fixas para o app de QA). Ele é
  marcado em destaque no painel, nas execuções e nos relatórios e **não** vale como validação do uso de IA.

Isolamento das instâncias: `android-01 … android-NN` (`instances.count`), cada uma com seu AVD em `data/avd/<id>.avd` (userdata, apps e
sessões próprios, preservados entre reinícios), console `5554+2i` → serial `emulator-<porta>`, e portas Appium
exclusivas (`systemPort 8200+i`, `mjpegServerPort 9200+i`, `chromedriverPort 9515+i`). *Reset* (com confirmação)
apaga os dados só daquela instância (`-wipe-data`).

## 3. Iniciar e parar

```powershell
pwsh -File scripts\start.ps1                 # backend + Appium + painel em http://127.0.0.1:8000
pwsh -File scripts\start.ps1 -StartInstances 2   # já pede o boot de 2 instâncias
pwsh -File scripts\start.ps1 -Dev            # + Vite com hot reload em http://127.0.0.1:5173
pwsh -File scripts\start.ps1 -Simulated      # MODO SIMULADO de desenvolvimento (sem IA; só conhece o app de QA)
pwsh -File scripts\stop.ps1                  # para backend e Appium; emuladores SEGUEM ligados (apps e sessões ativos)
pwsh -File scripts\stop.ps1 -StopEmulators   # também encerra os emuladores que o projeto iniciou
```

O backend sobe como **um processo por máquina** (sem `--reload`, sem múltiplos processos do uvicorn). Ele é o dono
do scheduler dos aparelhos que hospeda — e só deles: com dois backends no mesmo PostgreSQL, cada etapa tem um dono
registrado e um backend não mexe na etapa viva do outro (posse de etapa, migração 016; ver
[docs/banco.md](docs/banco.md)).

**Rede.** De fábrica, tudo escuta apenas em `127.0.0.1` e só aceita as origens de `server.allowed_origins`. Isso
deixou de ser incondicional: `server.host` pode sair do loopback, e quando sai o backend **recusa subir** sem as
três coisas juntas — `API_TOKEN` no `.env`, `server.public_hosts` e TLS (certificado próprio ou
`tls_behind_proxy`). O canal do agente tem listener próprio (`server.worker_port`, sempre em loopback, alvo do
`-R` do túnel SSH) que serve só `/api/worker/ws`. Detalhes em [docs/worker.md](docs/worker.md).

O encerramento só mexe em processos cujo PID o projeto registrou (e confere a linha de comando antes).

## 4. Preparar os aparelhos

1. No painel, selecione as instâncias e use **Iniciar** (o AVD é criado na primeira vez). O boot é feito em lotes
   (`boot_parallelism`) e o backend **recusa** novos boots quando não cabem na memória livre do host
   (`android.est_instance_ram_mb` por instância + boots em andamento + folga `android.min_free_ram_mb_after_boot`),
   explicando o motivo no cartão.
2. **Configuração → Aplicativos**: cadastre nome, package, activity (opcional), APK local (dentro de `qa-app/dist`
   ou `apks/`), dicas de navegação e seletores conhecidos — ou escolha um app já instalado em uma instância.
3. **Configuração → Instâncias e contas**: associe app e rótulo de conta a cada instância. O rótulo é só
   configuração; a conta realmente conectada é confirmada por evidência no app durante a execução.
4. **Instalar APK** (ação coletiva) e faça o **login manual**: abra o aparelho, *Assumir controle*, toque/digite,
   *Devolver à IA*. A sessão fica no AVD. Se o app pedir autenticação no meio de uma execução, só aquele aparelho
   fica *Aguardando usuário*.

App de QA incluído (`qa-app/`, contas fictícias `qa-user-01…10`, PIN `1234`):

```powershell
pwsh -File scripts\build-qa-apk.ps1          # recompila qa-app\dist\qa-messenger.apk (AGP 9.4.0 / Gradle 9.6.0)
pwsh -File scripts\provision-qa.ps1          # instala e conecta a conta de cada instância online (atalho só do app de QA)
```

Compatibilidade: as imagens são **x86_64**; APKs só-ARM falham com `INSTALL_FAILED_NO_MATCHING_ABIS` (o erro é
traduzido no painel). Apps que exigem Google Play Services precisam de imagem `google_apis` (padrão) ou
`google_apis_playstore`. Apps que bloqueiam emuladores ou não expõem hierarquia acessível são reportados como
`app_incompatible`, com as evidências.

## 5. Primeiro comando

No painel: selecione instâncias, escreva o objetivo e use **Planejar** (inspeciona o plano) ou **Executar**.

* Abrir uma tela: `Abra o QA Messenger e vá até a tela de Perfil.`
* Preencher um formulário: `No QA Messenger, abra o Perfil, preencha nome "Ana QA", e-mail "ana@qa.test" e cidade "Recife" e salve. Confirme que apareceu "Perfil salvo".`
* Enviar mensagem de teste: `Nas instâncias selecionadas, abra o QA Messenger, entre na conversa com o contato de teste identificado como QA-001 e envie “Teste POC {instance_id} {run_id}”. Confirme que a mensagem apareceu como enviada em cada conta e apresente o resultado individual.`
* Outro app (cadastre-o antes em Configuração → Aplicativos; basta nome + package): `Abra o app Configurações do Android, role até "About emulated device", entre nessa tela e confirme que o campo "Android version" está visível.`

Referência de custo medida com `claude-opus-5`: ≈12 chamadas e ≈80 mil tokens de entrada por aparelho por comando de
6–7 etapas, 70–130 s de ponta a ponta com 3 aparelhos em paralelo. Os limites ficam em `limits.*` (`config.yaml`) e
na tela Configuração. Se o comando for ambíguo ou pedir algo que a tela não tem, a IA **para e pergunta** (execução
*Precisa de informações* / item *Bloqueado*) em vez de inventar.

Pela linha de comando (mesma API do painel):

```powershell
pwsh -File scripts\demo-run.ps1 -Instances android-01,android-02
# verificador independente do ambiente de QA (persistência e ausência de duplicatas):
C:\Android\Sdk\platform-tools\adb.exe -s emulator-5554 shell "content query --uri content://com.pocqa.messenger.provider/messages"
```

## 6. Como a execução funciona

1. **Interpretar + planejar** (uma chamada ao modelo, saída estruturada): objetivo, app, parâmetros, critérios de
   sucesso e etapas-objetivo com dependências e pós-condição observável. Plano, objetivos por aparelho e etapas são
   **persistidos antes** de qualquer ação. Se faltar dado essencial, a execução fica *needs_input* com as perguntas.
2. **Por aparelho** (um worker por dispositivo, em paralelo): *observar* (screenshot + hierarquia) → *decidir* (o
   modelo com visão escolhe UMA ferramenta tipada) → *validar* (schema + guardas) → *agir* (Appium) → *observar* →
   *verificar a pós-condição*. O retorno do driver nunca conclui uma etapa; só a pós-condição observada conclui.
3. **Efeito externo** (enviar, salvar…): etapa própria, **uma única ação "commit"** em todas as tentativas, precedida
   da conferência de destinatário/conteúdo na tela. Timeout/queda depois do toque → **reconciliação pela tela**;
   persistindo a dúvida → `uncertain` (nunca reenvia sozinho; o usuário confirma, repete ou abandona).
4. **Níveis de entrega** observados: `appeared` < `sent` < `delivered` < `read`.
5. **Controle manual**: pedir o controle faz a IA ceder no próximo ponto seguro; cliques são mapeados para o frame
   exibido e rejeitados se o frame for antigo/incompatível; ao devolver, a IA reobserva a tela.
6. **Persistência**: reiniciar o backend marca ações pendentes como `unknown`, devolve as etapas para `ready` e
   reconcilia pela tela; reabrir o painel busca snapshot + eventos posteriores, sem reenfileirar nada. A
   `idempotency_key` impede execuções duplicadas por clique duplo ou repetição HTTP.

Ferramentas aceitas: `observe_screen, find_element, tap, long_press, drag, scroll, type_text, press_back,
press_home, open_app, wait_for, verify_state, step_done, step_blocked`. O pedido de *strict* (gramática imposta
pelo provedor) é **declarado por modelo** em `ai.models.<modelo>.strict_tools`, e sai de fábrica **desligado**
para os modelos Claude: com o conjunto de ferramentas de hoje a API responde "Schema is too complex" mesmo para o
subconjunto de 6 que causam efeito — medido 17 vezes em 3 dias de log real. Ligar `strict_tools: true` num modelo
que aceite volta a enviar `tap, long_press, drag, type_text, step_done, step_blocked` como estritas. Em qualquer
caso, **toda** chamada é revalidada por Pydantic antes de executar — a gramática do provedor é aceleração, nunca
garantia. Texto do modelo nunca vira código ou shell, e o conteúdo lido nas telas é tratado como dado (não altera
o objetivo).

## 7. Operação econômica: rodízio, modelo por função e receitas

Duas ideias cortam o custo sem mudar o que você vê no painel:

**RAM não cresce com o nº de contas.** Com `limits.auto_start_devices: true`, o scheduler liga o aparelho quando há
tarefa para ele e, sem vaga (`max_online_devices`), **hiberna** um aparelho ocioso (snapshot: ≈1,5 s para salvar,
≈16 s de mediana até ficar pronto para automação ao acordar, contra 73–105 s de boot a frio). Nunca desliga aparelho em uso, em foco no painel, com você no
controle ou com item bloqueado/incerto de execução aberta. O objetivo de um aparelho desligado fica "aguardando vaga"
em vez de bloquear. `android.extra_emulator_args: ["-lowram"]` faz o emulador respeitar `ram_mb` (≈2,4–2,9 GB reais
por instância em vez de ≈3,7 GB). A guarda de RAM do host continua valendo por cima de tudo.

**"Para todos os contatos" (lista lida da tela).** O plano usa uma etapa de coleta + um bloco `for_each`: o executor
lê a lista inteira sozinho (`collect_list`), copia o bloco para cada item e a receita aprendida no 1º item serve aos
demais. Falha em um item não trava os outros nem vira sucesso; "Tentar novamente" refaz só o que falhou. Teto de itens:
`for_each_max_items` (Configuração → Limites por objetivo).

**IA não cresce com o nº de execuções.**
* *Receitas* (`ai.recipes: replay`): a IA descobre como cumprir cada etapa **uma vez**; depois a etapa é repetida por
  seletores (resource-id/texto), sem chamada de modelo, com os parâmetros de cada conta. Tudo o que protege a execução
  continua igual (guardas do efeito externo, registro da intenção, verificação da pós-condição, "nunca reenviar").
  Seletor que não casa exatamente um elemento → **divergência**: a IA assume só aquela etapa. Versão nova do app →
  reaprende. 3 falhas seguidas → quarentena. Nada sensível é aprendido (senha, PIN, texto que não veio de parâmetro).
  `shadow` = aprende e compara com a IA sem agir.
* *Fluxos* (`ai.flows: true`): execução 100 % comprovada vira um plano congelado; o mesmo comando com outros valores
  reaproveita o plano **sem chamar o planejador**.
* *Modelo por função* (`.env`): `AI_MODEL_ACTOR` / `AI_MODEL_VERIFIER` mais baratos para as ~90 % de chamadas de tela;
  `AI_MODEL_PLANNER` forte só no plano; `AI_MODEL_ESCALATION` assume quando o barato tropeça, em nova tentativa e em
  etapa com efeito externo. Parâmetros que um modelo não aceita são descobertos e desligados sozinhos.
* *Menos tokens por chamada*: imagem só quando precisa (`image_policy: auto`; o modelo pode pedi-la), imagem menor,
  hierarquia priorizada, verificação por visão só rejulga quando a tela muda, e depois do toque de efeito externo a
  etapa vai direto à verificação.

Medir antes de adotar — cada alavanca tem o valor antigo anotado em `config.yaml`:

```powershell
pwsh -File scripts\eval-run.ps1 -Label minha-config -Yes    # bateria congelada: sucesso comprovado × US$ por caso
pwsh -File scripts\usage-report.ps1                          # US$ por função/modelo (o painel mostra o mesmo por execução)
pwsh -File scripts\rotation-test.ps1 -Accounts 10 -Slots 4   # 10 contas sobre 4 vagas: pico de RAM, tempo do lote
```

Limites honestos: emulador não tem SIM (SMS real e verificação de número pedem aparelho físico ou API); onde existir
API oficial (WhatsApp Business/Cloud API, Graph API, gateways de SMS) ela é mais barata e estável que automação de
tela; multi-conta em emulador pode ser bloqueada pelas plataformas — este projeto **não** implementa evasão de detecção.

## 7.1 Instagram: aplicativo, perfil, persona e aprovação

O alvo real é o Instagram (`com.instagram.android`); o QA Messenger continua como app de teste. O caminho é este:

```powershell
# 1) coloque o(s) APK(s) em apks\inbox (nada é baixado pelo sistema) e importe pelo portal ou pela linha de comando
#    Do seu aparelho, onde você instalou o app pela loja, o conjunto completo (base + splits) sai com:
pwsh -File scripts\pull-instagram.ps1 -Serial <serial do adb devices>
pwsh -File scripts\instagram.ps1 importar
pwsh -File scripts\instagram.ps1 releases          # pacote, versão, splits e assinatura vêm do arquivo

# 2) cadastre o perfil no portal (aba Perfis → Novo perfil): usuário, senha e aparelho.
#    A senha vai direto para o cofre cifrado e nunca volta — nem em resposta, nem em log, nem em evidência.
pwsh -File scripts\instagram.ps1 perfis
pwsh -File scripts\instagram.ps1 conectar -Perfil @mariana.costa91182

# 3) o que o perfil sabe, o que ele fez e o que espera decisão
pwsh -File scripts\instagram.ps1 memoria     -Perfil @mariana.costa91182
pwsh -File scripts\instagram.ps1 interacoes  -Perfil @mariana.costa91182
pwsh -File scripts\instagram.ps1 aprovacoes
pwsh -File scripts\instagram.ps1 aprovar     -Id apr-... -Nota "pode mandar"
```

No portal, **Perfis → Abrir** tem nove abas: visão geral, persona, aparelho, autenticação, memória, interações,
aprovações, execuções e configurações (política por ação e limites por hora). **Aplicativos** mostra o catálogo de
releases e o que está instalado em cada aparelho, lido do próprio aparelho.

### De onde vem o aplicativo: a loja (Play Store) como fonte oficial

O sistema não baixa APK de lugar nenhum. A fonte é **um emulador extra, com imagem Play Store, logado na sua conta
Google**: você instala o Instagram ali pela loja oficial, o backend copia o conjunto (base + splits) desse aparelho
por adb e o distribui ao resto do parque. Esse aparelho é a *loja*: o projeto o liga e desliga, mas **nunca lhe
despacha tarefa**, nunca o despeja no rodízio e não abre sessão de automação nele.

```powershell
# 1) uma vez: a imagem com Play Store (repositório oficial do SDK; o script aceita as licenças do SDK em seu nome)
pwsh -File scripts\install-prereqs.ps1 -ImageTags google_apis,google_apis_playstore -SkipAppium

# 2) config\config.yaml: instances.count: 11, instances.store: android-11 e os overrides da loja (há um exemplo lá)

# 3) no painel: cartão da loja -> Criar AVD -> Iniciar. A loja sobe COM JANELA.
#    Na JANELA do emulador: entre na conta Google e instale o Instagram pela Play Store.
#    Nenhuma tecla passa pelo painel — ele recusa texto na loja de propósito.
pwsh -File scripts\instagram.ps1 abrir-loja        # abre a página do app na loja; o toque em Instalar é seu

# 4) copiar da loja para o catálogo, provar num aparelho, promover e distribuir
pwsh -File scripts\instagram.ps1 buscar
pwsh -File scripts\instagram.ps1 releases          # a primeira versão entra `validated`: aprove a assinatura no portal
pwsh -File scripts\instagram.ps1 canario    -Id rel-... -Aparelho android-01
pwsh -File scripts\instagram.ps1 promover   -Id rel-...
pwsh -File scripts\instagram.ps1 distribuir -Id rel-...            # quem está ligado instala já; o resto, ao pegar tarefa
pwsh -File scripts\instagram.ps1 distribuir -Id rel-... -Agora     # o rodízio liga os desligados e instala em todos
```

**Distribuir exige versão promovida** — canário primeiro. Só `limits.max_online_devices` aparelhos ficam ligados
por vez nesta máquina (4 no parque de hoje; cada worker tem o próprio teto, `max_slots`), então a entrega
padrão grava a versão *desejada* e cada aparelho a recebe **antes da próxima tarefa daquele app**; `-Agora` faz o
rodízio percorrer o parque sem esperar tarefa. Uma entrega que falha **não se repete sozinha**: fica nomeada no
estado do aparelho e espera você pedir de novo. Quando a Play Store atualizar o app na loja, `loja` avisa que há
versão nova e o ciclo se repete: buscar → canário → promover → distribuir.

Se a janela da loja não aparecer (backend rodando sem área de trabalho), há o plano B: `scripts\loja-janela.ps1`.
Logo depois do primeiro login a Play Store pode abrir em branco enquanto os serviços Google se preparam: feche-a e
abra de novo pelo launcher. O canário também não é instantâneo: a primeira abertura do Instagram num aparelho levou
25 s até a primeira tela, e a prova de abertura espera até 90 s antes de desistir.

### Subir uma versão nova sem apostar no parque inteiro

Uma versão importada nasce **nunca provada**. Ela só vira a versão recomendada depois de instalar num aparelho só,
abrir e continuar de pé — código de retorno do ADB não conta como prova.

```powershell
pwsh -File scripts\instagram.ps1 canario    -Id rel-... -Aparelho android-01   # prova num aparelho só
pwsh -File scripts\instagram.ps1 promover   -Id rel-...                        # exige a prova registrada
pwsh -File scripts\instagram.ps1 quarentena -Id rel-... -Nota "travou no feed"  # bloqueia a instalação
pwsh -File scripts\instagram.ps1 rollback   -Id rel-... -Aparelho android-01   # volta preservando os dados
```

Se o canário falhar, a versão vai para a **quarentena** sozinha e deixa de ser instalável; voltar atrás é pedir um
canário novo de propósito. Cada aparelho pode ficar numa versão diferente — nada é instalado sem alguém pedir.

**Rollback tem um limite real do Android:** voltar preservando os dados (`adb install -r -d`) pode ser recusado
conforme o build. Quando é recusado, nada é apagado e o aparelho fica como estava; a única saída é reinstalar com
`-ApagandoOsDados`, que **apaga os dados do aplicativo e a sessão** — o login terá de ser refeito. O sistema nunca
escolhe esse caminho sozinho.

**Qualquer** instalação — primeira, reinstalação, atualização, downgrade ou rollback — deixa a sessão daquele
aparelho como *não verificada*. Isso significa **observar antes de pedir a senha**: se o Instagram tiver preservado
o login, a verificação termina sozinha sem digitar nada. Memória, histórico, persona e vínculo são do perfil e não
são tocados por instalação nenhuma.

Três coisas que o sistema **não** faz, de propósito: não contorna CAPTCHA, 2FA nem desafio de segurança (isso vira
`AUTH_CHALLENGE` e espera uma pessoa); não baixa APK de lugar nenhum; e não repete efeito externo por timeout —
ele observa a tela e reconcilia. Automatizar conta de Instagram contraria os termos da plataforma e pode levar a
bloqueio: os limites por perfil existem para reduzir risco, não para contorná-los.

## 7.2 Execução distribuída: o parque em mais de uma máquina

Os aparelhos não precisam estar todos aqui. Uma segunda máquina roda um **agente** (`backend/app/worker`), se
inscreve no central e passa a receber trabalho pelo **mesmo contrato** do parque local — inclusive o central, que
é um worker de si mesmo (`LocalWorker`). O roteiro completo, com instalador, serviço e recuperação, está em
[docs/worker.md](docs/worker.md); a arquitetura e o que foi medido, em
[docs/parque-distribuido.md](docs/parque-distribuido.md); o banco e a posse de etapa, em
[docs/banco.md](docs/banco.md).

* **Aba Infraestrutura** do painel: os servidores inscritos, a batida (*heartbeat*) de cada um, vagas usadas,
  verbos que ele declara saber fazer, aparelhos anunciados que ainda não são instância (com "adotar"),
  manutenção liga/desliga, rotação de credencial e o estado do túnel.
* **Ação vira comando, não promessa.** `POST /api/instances/{id}/actions/{verbo}` responde `202` com
  `command_id` e estado — e **aceito não é sucesso**: o comando caminha por `dispatched → acked → running` até
  `succeeded`, `failed`, `rejected`, `cancelled` ou **`uncertain`** (terminou sem que se saiba o efeito: nada é
  repetido sozinho, e alguém decide). Acompanhe por `GET /api/commands/{id}` ou pelo evento `command.updated`.
* **Túnel.** O ADB remoto e o canal do agente passam por SSH (`scripts/worker-tunnel.ps1`). O listener do canal
  (`server.worker_port`) é dedicado e serve só `/api/worker/ws`.
* **Variáveis do `.env`** (todas opcionais; veja `.env.example`): `DATABASE_URL` troca o SQLite por PostgreSQL —
  é o que permite mais de um backend no mesmo banco; `OWNER_ID` identifica este servidor na posse de etapa
  (padrão: o hostname); `API_TOKEN` exige credencial nas rotas — **obrigatório** para sair do loopback, junto
  com `server.public_hosts` e TLS.
* **O que ainda não foi provado em infraestrutura real** está na tabela dos nove aceites,
  [docs/relatorio-validacao.md §13](docs/relatorio-validacao.md) — inclusive o roteiro pronto para fechar cada
  linha (`scripts/aceites-remotos.ps1`, `scripts/test-restart-recovery.ps1`).

## 8. Testes

```powershell
cd backend; .venv\Scripts\python.exe -m pytest -q      # isolamento, exclusividade, transições, dedup, recuperação pós-efeito
cd frontend; npm test                                   # store, reducer de eventos (command.updated/worker.updated), telas com backend e WebSocket falsos, mapeamento de coordenadas
cd frontend; npx tsc --noEmit -p .                      # tipos
cd backend; .venv\Scripts\python.exe -m pytest -q ..\scripts\tests   # a lógica pura dos scripts (sem tocar no parque)
pwsh -File scripts\scale-test.ps1                        # mede 1→2→5→10→14 (parque inteiro, sem a loja) e grava data\scale-test-results.json
pwsh -File scripts\probe-image.ps1 -Image 'system-images;android-34;aosp_atd;x86_64'   # custo real de uma imagem
```

## 9. Estrutura

```
backend/app/{devices,automation,planning,taskqueue,commands,workers,worker,social,releases,security,integrations}
backend/migrations   backend/tests
frontend/src/features/{topbar,command,devices,focus,runs,infra,profiles,releases,settings,usage,diagnostics,login,painel}
qa-app/   tools/appium/   scripts/   config/config.example.yaml   docs/   apks/inbox/
```

Para trocar o provedor de IA: implemente `AIProvider` (`backend/app/planning/provider.py` — `plan`, `decide`,
`verify`) e registre em `build_provider`.
