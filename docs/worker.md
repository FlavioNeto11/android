# Cadastrar um servidor novo no parque

Um **worker** é uma máquina que hospeda aparelhos. O servidor central continua único: painel, IA, banco e
catálogo de aplicativos ficam nele. O que muda com um worker é que o **ciclo de vida** dos aparelhos daquela
máquina passa a existir de verdade — antes o túnel carregava só ADB, e criar AVD, ligar o emulador, `emu kill`,
snapshot e guarda de RAM simplesmente não tinham contraparte do outro lado.

## O que roda onde

| | Central | Worker |
|---|---|---|
| Painel, banco, IA, catálogo de APK | ✅ | – |
| Criar AVD, ligar, desligar, reiniciar, hibernar, acordar, resetar | ✅ (aparelhos locais) | ✅ (os dele) |
| Instalar APK, abrir app, teclas, tela, hierarquia | ✅ **sempre** | – |
| Ler o log do emulador (`emulator_log`) | ✅ (aparelhos locais) | ✅ (os dele) |
| Appium | ✅ por padrão | opcional (`appium: local`) |
| Credencial de conta (senha de perfil) | ✅ **só aqui** | ❌ nunca |

O central **também é um worker** (`LocalWorker`): ele aparece na tabela `workers` com o `OWNER_ID`, os aparelhos
desta máquina têm `worker_id`, e o ciclo de vida sai pelo mesmo despacho — mesma cerca, mesmo prazo, mesmos
estados de comando. Não há um caminho para os aparelhos daqui e outro para os de lá.

### O log do emulador

Quando um `start`/`wake`/`restart`/`reset` termina `failed` ou `uncertain`, a **cauda do log do emulador**
(últimos 8 KB, com redação do que parecer segredo) volta no desfecho do comando e aparece no painel, em
*Log do emulador (fim)*. Vale para as duas máquinas: antes, num boot remoto que não subia, o operador recebia
uma frase e o log ficava na outra máquina, fora de alcance.

### Capacidades declaradas

O agente declara, a partir do `config.ini` de cada AVD, a **imagem do sistema, o nível de API, a ABI e se a
imagem tem Google Play Services**. O central guarda isso na instância e usa no pré-voo: distribuir uma versão ou
criar uma execução recusa, **com a frase**, o aparelho que não roda aquele pacote (API velha demais, ABI que ele
não executa, fluxo que precisa de GMS numa imagem AOSP) — em vez de deixar aparecer no meio, como
`INSTALL_FAILED_NO_MATCHING_ABIS`. Capacidade que o agente não declara fica *desconhecida*, e o desconhecido
nunca vira recusa.

### `appium: local`

Declarar `appium: local` com `appium_url` faz os aparelhos deste worker serem dirigidos pelo **Appium da máquina
dele**, com o `udid` local (`emulator-55xx`) — e não mais pelo Appium do central através do túnel. O central
confere o `/status` daquele Appium na conexão: se não responder, o worker é aceito do mesmo jeito, continua
sendo dirigido daqui, e o motivo aparece na Infraestrutura. Quando o worker desconecta, os aparelhos voltam ao
Appium do central.

O recorte dos verbos é deliberado: o agente faz exatamente o que o túnel **não** consegue carregar. Instalar APK
continua saindo do central pelo túnel porque aquele caminho está provado (APK de 20 MB em 1,6 s, `adb forward`,
screenshot de 673 KB em ~250 ms) e porque mandar o catálogo de APK para cada máquina trocaria um problema
resolvido por um novo.

## Passo a passo

### 1. No servidor central — gerar o token de inscrição

Painel → **Infraestrutura** → *Inscrever servidor*. Ou:

```bash
curl -s -X POST http://127.0.0.1:8000/api/workers/enroll -H "Content-Type: application/json" -d "{\"label\":\"notebook da sala\"}"
```

O token aparece **uma vez**, vale 1 hora e é de **uso único** — só o hash fica guardado. Ele existe para a janela
da instalação, não para viver num arquivo.

### 2. Na máquina do worker — dependências

Precisa de: Python 3.13, Android SDK com `emulator` e `platform-tools`, e aceleração (WHPX no Windows, KVM no
Linux). O instalador do projeto resolve o SDK:

```bash
pwsh -File scripts\install-prereqs.ps1 -SkipAppium
```

Confira a aceleração antes de seguir — sem ela o emulador sobe lentíssimo ou não sobe:

```bash
C:\Android\Sdk\emulator\emulator.exe -accel-check
```

### 3. Configurar

Copie `config/worker.example.yaml` para `C:\farm\worker.yaml` e ajuste `server`, `worker_id`, `name`,
`max_slots` e a lista de `devices`. **A credencial não vai neste arquivo**: o agente a grava sozinho em
`worker-credential.json` depois da inscrição.

**`android.hibernation`** (padrão `false`) diz se ESTA máquina salva snapshot. Quem responde isso é ela, não o
`config.yaml` do central: o agente declara o valor no `Hello` e o painel só oferece "Hibernar" para os aparelhos
dela quando é `true`. Com `false`, o emulador sobe com `-no-snapshot` e `wake` é **recusado** com o motivo —
antes ele fazia um boot a frio de minutos e respondia `from_snapshot: true`, que era sucesso com dado falso.
Cada snapshot ocupa ~1,5 GB em disco por aparelho.

O agente também avisa quando um `start` está **na fila de boot** (`boot_parallelism`, um por vez por padrão): a
espera na fila vira progresso no painel em vez de ser contada como tempo de boot, e a guarda de RAM é reavaliada
na vez de cada boot — avaliada antes da fila, N `start` simultâneos passavam todos pela mesma leitura de
memória livre e só o primeiro tinha a RAM que a conta prometia.

### 4. Primeira partida, com o token

```bash
python -m app.worker --config C:\farm\worker.yaml --enroll <token>
```

O agente se declara (SO, versão, vagas, verbos, aparelhos, CPU/RAM/disco), recebe a credencial permanente, grava
e passa a bater o coração. A partir daí, **sem** `--enroll`:

```bash
python -m app.worker --config C:\farm\worker.yaml
```

### 5. Deixar como serviço

Processo iniciado dentro de uma sessão SSH pertence ao job dela e o Windows o mata no logout — medido em 19/09,
com o emulador morrendo em segundos já com o WHPX operacional. No Windows, tarefa agendada com
`-LogonType S4U` e `-RunLevel Highest`, disparada no boot (o padrão de `scripts/worker-emulator.ps1`). No Linux,
unidade systemd com `Restart=always`.

### 6. No central — o aparelho do worker vira instância

**Caminho curto (desde o item 4.5): adotar no painel, sem editar YAML e sem reiniciar nada.** O agente já declara
o inventário dele no `hello` e na batida (`devices[].serial`, `.avd_name`, `.adb_port`). Na **Infraestrutura**, o
cartão do worker lista os aparelhos anunciados que ainda não são instância do parque, com o botão **Adotar**. Um
clique e o central:

1. cria a instância (id novo no prefixo configurado, ou o que você informar), com `origin: dynamic`;
2. **aloca a porta local do túnel** — ele é quem conhece as portas em uso, inclusive as do `config.yaml`;
3. grava `instances.external_serial`, `tunnel_port` e `remote_adb_port`: o mapa do túnel passa a ter UM lugar;
4. reescreve `data/tunnel/<worker_id>.map`, que o script do túnel relê sozinho (nada de `-Instalar` de novo);
5. põe o aparelho no painel na mesma chamada — **sem reiniciar o backend**.

Pela API, o mesmo:

```bash
curl http://127.0.0.1:8000/api/workers/devices/unbound
curl -X POST http://127.0.0.1:8000/api/workers/<worker-id>/devices/adopt -d '{"serial":"emulator-5554"}'
```

A resposta traz a instância criada e o `tunnel_map` novo. Para o túnel enxergar as portas novas, ele precisa
estar rodando com `-MapaArquivo` (ver "O túnel como componente"); com o `-Mapa` fixo dos argumentos, a porta
nova só entra depois de reinstalar a tarefa.

**Caminho antigo (ainda válido).** Declarar a instância em `config/config.yaml` (`instances.count` +
`instances.external`) e amarrá-la à máquina por `instances.worker_id`. O ADB continua chegando por túnel.

No painel, esse vínculo é a coluna **Servidor** em **Configuração → Instâncias**: escolha o worker e clique em
**Salvar**. Voltar a escolha para **Este servidor (central)** desamarra o aparelho (é o `worker_id: null` de
`PUT /api/instances/{id}`) — é o que fazer quando o aviso "aparelhos amarrados a um servidor que não está
inscrito", na Infraestrutura, apontar para um worker que não existe mais.

## Como o canal se comporta

- **O worker liga para o central**, nunca o contrário: atravessa NAT sem abrir porta na casa de ninguém.
- **Ausência de batida** é o que marca o worker indisponível, não o socket fechado — socket cai por rede
  piscando, e isso não significa que o worker parou de trabalhar. Batida a cada 10 s, ausente após 3 perdidas.
- **Reconexão automática** com espera crescente até 60 s. Recusa explicada (credencial errada, não inscrito) faz
  o agente **parar** em vez de martelar: insistir não resolveria.
- **Cerca (fencing):** todo despacho leva um número monotônico e o agente o devolve no resultado. Resultado com
  cerca velha — ou **sem** cerca — é recusado. E quem recusa não é só o central: o **agente** guarda a maior cerca
  já executada por aparelho (em disco, no diário) e recusa despacho de cerca menor sem tocar no aparelho. É essa
  metade que faz a cerca proteger o RECURSO, e não apenas quem a emitiu.
- **Um aparelho, uma operação.** Enquanto houver comando de ciclo de vida aberto num aparelho, o próximo é
  recusado com `409 device_busy` no pré-voo do central; o agente tem a mesma trava por `instance_id` e recusa o
  segundo despacho sem executar nada.
- **Marcas de tempo verdadeiras.** `dispatched` é gravado quando o comando SAI pelo socket (com o `worker_id`),
  `acked` quando o agente confirma o recebimento, e `running` no primeiro `progress` do agente. Numa queda isso é
  o que separa "o worker nunca confirmou o recebimento" de "confirmou e o efeito é desconhecido".
- **Queda no meio de um comando** deixa o comando `uncertain`, nunca falho: o agente pode ter agido. E o agente
  **não cancela** o verbo em andamento quando o canal cai — ele termina, guarda o desfecho num diário local
  (`<work_dir>/diario-do-agente.json`) e o reenvia na reconexão; o central procura o comando no banco e fecha o
  `uncertain` com o desfecho verdadeiro, respondendo `result_ack` para o diário poder ser limpo. O `hello` da
  reconexão também lista em `inflight` o que ainda está sendo executado.
- **Cancelamento** (`POST /api/commands/{id}/cancel`) viaja pelo mesmo canal: o central manda
  `{"type":"cancel", "command_id": ...}` e o agente cancela a tarefa daquele comando. O que ele responde depende
  de onde o verbo estava: **`cancelled`** só enquanto o aparelho continua intacto (esperando vaga na fila de
  boot, por exemplo) e **`uncertain`** depois de o efeito começar — AVD sendo criado, emulador iniciado,
  snapshot sendo salvo. `asyncio.to_thread` não é interrompível no meio, então dizer "cancelado" ali seria
  afirmar que nada aconteceu sobre um aparelho que já ligou. Cancel de comando que o agente não conhece é
  ignorado, sem efeito.

## Como o worker alcança o central

Duas formas, e a diferença é quanta superfície nova cada uma abre.

**a) Túnel SSH reverso (padrão).** O central segue atendendo só em `127.0.0.1`, e o worker chega nele por um `-R`
do próprio túnel.

> **Quem abre o túnel, na prática.** Por muito tempo esta página disse que esse era "o único caminho que
> funciona quando o worker está atrás de NAT que você não controla". Era falso do jeito que mais engana: o
> `ssh` de `scripts/worker-tunnel.ps1` parte **do central para o worker** (`-L` das portas de ADB e `-R` da API
> na MESMA conexão iniciada daqui), então ele exige sshd na máquina do worker e rota do central até lá. Na LAN
> funciona; atrás do NAT de outra pessoa, não existe.
>
> O lado que de fato atravessa NAT é `scripts/worker-tunnel-reverso.ps1` (e o irmão `.sh`), que roda **na
> máquina do worker** e abre `-R <porta-no-central>:127.0.0.1:<adb-daqui>` para um sshd do central, mais um
> `-L 18000:127.0.0.1:8010` para o agente alcançar `/api/worker/ws`. Quem inicia a conexão é o worker, então
> nenhuma porta precisa ser alcançável na rede dele. O que o central precisa é de um sshd com conta **restrita a
> encaminhamento**, em `authorized_keys`:
>
> ```
> restrict,port-forwarding,permitopen="127.0.0.1:8010" ssh-ed25519 AAAA... worker-01
> ```
>
> `GatewayPorts` fica em `no` (o padrão): as portas abertas pelo `-R` escutam só no loopback do central, que é
> exatamente como o ADB de lá já fala com os aparelhos remotos. **Ainda não foi exercitado com um worker fora da
> LAN** — o script existe, aceita o mesmo arquivo de mapa e reconecta sozinho, mas o ensaio em 4G/hotspot
> (latência de ADB e execução de ponta a ponta) depende de um sshd no central e de uma máquina noutra rede, e
> continua pendente.

> **Correção de um erro que esta página afirmava.** Aqui estava escrito "**zero porta nova em qualquer lugar** — é
> o caminho de menor exposição". Era falso, e da pior maneira: o `-R` apontava para a porta do backend (`8000`), e
> **toda conexão que chega por um túnel reverso tem par `127.0.0.1` de verdade**. Como loopback isenta de
> credencial, qualquer processo da máquina do worker — job de CI, usuário local não-administrador — alcançava a API
> inteira do central sem token. Medido: `GET http://127.0.0.1:18000/api/workers`, `/api/instagram/profiles`,
> `/api/commands` e `/api/health` respondiam 200; `POST /api/admin/shutdown`, `PUT` de credencial de perfil e
> `POST /api/workers/enroll` estavam ao alcance. Comprometer um worker equivalia a comprometer o central.
>
> Conferir o endereço do par **não** resolve: o par É `127.0.0.1`. O que resolve é **uma porta a mais**, e é por
> isso que a afirmação acima foi retirada em vez de remendada.

O `-R` aponta para `server.worker_port` (`127.0.0.1:8010`), um listener **dedicado** que serve só
`/api/worker/ws`. Nele não existe rota REST nenhuma: o que chega pelo túnel e não é o WebSocket do worker recebe
404. O agente não precisa de mais nada — ele fala pelo canal autenticado, nunca pela API REST.

```powershell
# na máquina que mantém o túnel
pwsh -File scripts\worker-tunnel.ps1 -Instalar -MapaReverso '18000:8010'
```

Do lado do worker, o endereço do central continua sendo `127.0.0.1:18000`: só o alvo do outro lado mudou. Um túnel
antigo, apontando para `:8000`, continua funcionando enquanto o backend velho estiver no ar e **para de alcançar
qualquer coisa** depois da subida do backend novo — é a tranca, não um efeito colateral.

**b) Porta de rede no central, com autenticação.** Para isso o central precisa das duas coisas juntas, e ele
**recusa subir** se faltar qualquer uma:

```yaml
# config/config.yaml
server:
  host: 0.0.0.0
  public_hosts: [central.parque.local, 192.168.1.10]
```

```bash
# .env — gere com: python -c "import secrets; print(secrets.token_urlsafe(32))"
API_TOKEN=...
```

Por que as duas: o `API_TOKEN` responde "quem é você" e o `public_hosts` responde "por qual nome você me
chamou". A segunda pergunta não é redundante — é a defesa contra *DNS rebinding*, em que um nome controlado pelo
atacante resolve para `127.0.0.1` e um navegador da vítima passa a falar com o seu backend. Nome que não está na
lista é recusado com `forbidden_host` **mesmo com a credencial certa**.

Chamada vinda do loopback continua **sem** precisar de token, de propósito: quem já está na máquina tem o banco e o
adb na mão, então exigir segredo ali não protegeria nada e quebraria o frontend servido localmente.

## Segurança

- ADB e Appium ficam na rede privada (túnel, ou a rede do próprio worker). Nada é exposto.
- Token de inscrição: uso único, 1 hora, só o hash guardado. Credencial permanente: só o hash no central, e no
  worker um arquivo com ACL própria — `icacls /inheritance:r` mais `/grant:r` para SYSTEM, Administrators e o
  usuário que roda o agente. **Isto era falso no Windows até agora:** o código só fazia `chmod` fora do Windows e
  confiava na herança da pasta de trabalho, que dava `BUILTIN\Users:(I)(RX)` — qualquer usuário local lia a
  credencial permanente do worker. Um `worker-credential.json` gravado por uma versão anterior **continua com a
  ACL velha**: apague-o e reinscreva o worker, ou rode `icacls C:\farm\worker-credential.json /inheritance:r
  /grant:r *S-1-5-18:F /grant:r *S-1-5-32-544:F`.
- Isenção de loopback exige as **duas** coisas: o endereço do par (que o cliente não escolhe) e o cabeçalho `Host`.
  Antes só o `Host` decidia, e no modo (b) um `curl -H 'Host: localhost'` de qualquer máquina da rede atravessava o
  portão sem token. Os nomes `test`/`testserver` também valiam em produção; saíram.
- `POST /api/admin/shutdown` exige, além do par local, o segredo de `data/shutdown.token` (regravado a cada subida
  do backend, ACL restrita, lido pelo `scripts/stop.ps1`). Par de loopback deixou de significar "esta máquina" no
  dia em que o túnel reverso existiu.
- **O worker nunca recebe senha de perfil.** O canal de entrada sensível continua central.
- A autenticação do worker é na **primeira mensagem** do WebSocket, nunca em query string — query string acaba em
  log de proxy.
- Segredo não entra em log: a redação é por **formato** (`Authorization: Bearer …`, `API_TOKEN=…`, `senha=…`), não
  por lista de valores — manter os valores para comparar criaria mais uma cópia do segredo em memória.
- **Limite honesto:** não existe tela de login. Com `API_TOKEN` configurado, um frontend servido para fora da
  máquina precisaria carregar o token, o que o exporia no navegador. Hoje o uso previsto da porta de rede é
  **worker↔central e chamadas de máquina**; o painel continua sendo aberto no central. Sessão de usuário é
  trabalho separado e não está feito.

## Recuperação

| Situação | O que fazer |
|---|---|
| Perdi a credencial do worker | Apague `worker-credential.json`, clique **Remover** no cartão do worker (Infraestrutura) e inscreva de novo com um token novo |
| Suspeito que a credencial vazou (máquina comprometida) | Clique **Rotacionar credencial** no cartão do worker: a credencial antiga para de servir na hora e o painel mostra a nova, uma única vez — grave-a em `worker-credential.json` e reinicie o agente. Não precisa remover nem reinscrever |
| Token venceu ou já foi usado | Gere outro; ele é de uso único de propósito |
| O agente diz `protocol_too_new` | O agente é mais novo que o servidor: atualize o **central** |
| Worker aparece offline mas está ligado | Veja `last_seen_at` na Infraestrutura; sem batida há >30 s, o problema é rede ou o processo do agente |
| Manutenção | Painel → Infraestrutura → manutenção. Suspende **novas** atribuições (comando de painel e tarefa de IA) e não derruba o que já está em voo |
| Remover um worker | `DELETE /api/workers/{id}` (ou o botão **Remover** no painel). Recusa com 409 se o worker está conectado ou tem comando em voo — desconecte-o primeiro, ou confirme de novo no painel para remover com `force`. Os aparelhos amarrados a ele ficam sem dono (não são apagados); amarre-os a outro worker ou reinscreva este com o mesmo id |

## O túnel como componente (achado #179)

Até aqui a queda do túnel SSH aparecia só como sintomas espalhados: seis aparelhos "sem conexão ADB", o worker
"sem batida" 30 s depois, comandos `uncertain` — e nada dizia "o túnel para o worker X está fora desde HH:MM".
`AppState._probe_transport()` sonda, a cada volta do laço de workers (~10 s), a porta LOCAL que
`scripts/worker-tunnel.ps1 -L` encaminha para cada aparelho `external` vinculado a um worker (`rt.worker_id`):
`ssh -L porta:127.0.0.1:remota` mantém a porta local escutando mesmo com o lado remoto fora do ar, então conexão
**recusada** ali é o túnel caído, e conexão **aceita** é túnel de pé (o aparelho do lado de lá pode estar
desligado — isso é outra causa, detectada como sempre pela batida/ADB).

O resultado fica em `workers.transport_state` (`up`/`down`), `transport_detail` e `transport_since`, exposto no
`WorkerDTO` e mostrado na Infraestrutura como a linha "Túnel" do cartão de cada worker. `GET /api/health` ganha
o problema `tunnel_down` (degraded) quando algum túnel está fora. O motivo de "aparelho sem ADB"
(`DeviceManager._transport_hint`) passa a citar o túnel quando ele é a causa, em vez da mesma frase genérica de
sempre. A sonda só lê (conexão TCP local); ela nunca reinicia o `ssh` nem mexe na tarefa agendada —
`farm-tunel-<worker>` reconecta sozinha, como antes.

### O mapa do túnel num lugar só (item 4.5)

O mapa de portas vivia nos **argumentos da tarefa agendada** (`-Mapa '15555:5555,…'`): acrescentar um aparelho
exigia `-Instalar` de novo, o que desregistra e registra a tarefa. Agora o central grava
`data/tunnel/<worker_id>.map` (uma linha, `portaLocal:portaRemota,…`) a partir de `instances.tunnel_port` e
`instances.remote_adb_port`, e o script aceita `-MapaArquivo`:

```bash
pwsh -File scripts\worker-tunnel.ps1 -Instalar -Worker 192.168.1.19 `
     -MapaArquivo C:\git\android\data\tunnel\<worker-id>.map
```

O laço relê o arquivo a cada 5 s; quando o conteúdo muda, derruba o `ssh` e sobe outro já com as portas novas.
Arquivo ausente, vazio ou ilegível **não** derruba o túnel: ele cai para o `-Mapa` dos argumentos, que é o que a
tarefa existente já usa. Para conferir o mapa resolvido sem abrir túnel nenhum:

```bash
pwsh -File scripts\worker-tunnel.ps1 -MostrarMapa -MapaArquivo C:\git\android\data\tunnel\<worker-id>.map
```

### Inventário conferido, e não presumido (achado #47)

O inventário aparelho↔máquina vive em três lugares: o mapa de portas (agora na instância), `instances.worker_id`
e o `worker.yaml` da outra ponta. A cada `hello` e a cada batida, o central **confronta** os três
(`DeviceManager.conferir_inventario`) e acusa:

- instância amarrada a W que W **não declara** hospedar;
- aparelho que W declara como instância de **outra** máquina;
- porta de ADB declarada diferente da que o túnel encaminha.

A divergência vira `inventory_state: "divergent"` no `InstanceDTO`, com o motivo, aparece no cartão do servidor
na Infraestrutura e faz o central **recusar verbo destrutivo** (`reset`, `stop`, `restart`, `hibernate`,
`install_apk`, `create`) naquele aparelho até o vínculo ser resolvido — era exatamente o dano latente: `reset`
agindo num AVD com a tela em outro. Verbos de leitura e de tela continuam liberados, porque são eles que
permitem diagnosticar.

## Ensaio do aceite 6 — derrubar o túnel no meio de um comando

Procedimento pronto para ser executado no parque. Ele exige o emulador real e o túnel real, então **não é
coberto pela suíte**: o que a suíte cobre é a lógica dos dois lados (`backend/tests/test_queda_de_conexao.py`,
com o laço de sessão do agente dirigido por um WebSocket de mentira que o teste derruba no meio do verbo).
Registre aqui o que for medido, separando o que foi real do que foi simulado.

1. No central, confira que o worker está conectado (Infraestrutura) e que o aparelho aceita `start`.
2. Peça **Iniciar** no aparelho remoto e anote o `command_id` da resposta.
3. Durante o boot (ele leva 100–480 s), derrube o túnel na máquina do worker:
   `Stop-Process -Name ssh` — ou desligue o Wi-Fi por ~30 s.
4. Confira no central: o comando vai para `uncertain` com o motivo da queda, e **não** para `failed`.
   `GET /api/commands/<command_id>`.
5. Deixe o túnel voltar (a tarefa agendada reconecta sozinha; o agente tenta a cada 2 s até 60 s).
6. Confira, sem tocar em mais nada:
   - no worker, `<work_dir>/diario-do-agente.json` some o registro daquele comando assim que o central confirma;
   - no central, o comando fecha sozinho em `succeeded` (ou `failed`), com o motivo dizendo que o desfecho chegou
     depois da reconexão;
   - o emulador está de fato no ar — o verbo terminou apesar da queda, em vez de ser cancelado no meio.
7. Repita com `reset`, que é o caso que mais doía: stop seguido de start com wipe, sem ponto seguro no meio.

Se o passo 6 mostrar o comando ainda `uncertain` depois de o agente reconectar, o desfecho não saiu do diário:
veja o log do agente por `reenviando o desfecho de <command_id>` e o do central por `resultado tardio`.

### As outras duas quedas do aceite 6

Mesmo formato: pronto para executar, **não** coberto pela suíte, e o que for medido volta para cá separando real
de simulado.

**Matar o agente no meio do verbo.** Peça `start` no aparelho remoto, anote o `command_id` e, durante o boot,
`Stop-Process -Name python` na máquina do worker (o processo do agente). O emulador **continua subindo** — quem
o iniciou foi o sistema operacional, não o agente. Ao subir de novo, o agente relê o diário e reenvia o que
estava pendente; o comando fecha com o desfecho real, ou permanece `uncertain` com o `hello` da reconexão
anunciando `inflight` (o painel mostra "o worker reconectou e ainda está executando este comando").

**Reiniciar o central com um comando em voo.** Peça `reset` no aparelho remoto e, durante o boot, reinicie o
backend do central. Na partida, a reconciliação carimba `uncertain` no que estava `dispatched`/`acked`/`running`
e `failed` no que nunca saiu (`created`) — nada é repetido sozinho. Quando o agente reconectar e mandar o
desfecho guardado, o comando fecha com ele; a cerca (`fence`) é o que garante que um resultado vindo do limbo não
sobrescreva um comando mais novo do mesmo aparelho.

**O que ainda falta medir no parque:** um `start` e um `reset` remotos **completos e bem-sucedidos**. Até hoje há
três `stop` remotos `succeeded` (android-13/14/15) e nenhum `start` fechado — o único registrado terminou
`uncertain` por boot acima de 480 s (`c-20260921172322-6f7fdc`). Registre aqui o `command_id`, o tempo de boot e
se foi a frio ou de snapshot.

### O que a suíte já cobre do worker

Não substitui o ensaio de campo, mas nenhuma destas regras depende mais de memória de quem estava na sala:

| Arquivo | O que trava |
|---|---|
| `backend/tests/test_worker_agent.py` | agente contra um `websockets.serve` de verdade em porta efêmera: inscrição devolve a credencial uma vez e a reconexão usa ela; recusa explicada para o agente; ACK → iniciado → desfecho, nessa ordem; `VerbUncertain` → `uncertain`; `cancel` antes de tocar no aparelho → `cancelled` e depois do efeito começar → `uncertain`; reentrega devolve o mesmo desfecho sem executar de novo |
| `backend/tests/test_worker_executor.py` | `start` só volta depois de o Android responder; prazo estourado → `VerbUncertain`; boots um a um com `boot_parallelism=1`; guarda de RAM recusa sem subir nada; hibernar sem snapshot → `VerbFailed`; e a prova de que `adb.state()`/`estado()` saem da thread do laço de eventos |
| `backend/tests/test_canal_do_worker.py` | o canal `/api/worker/ws` de ponta a ponta: `hello` malformado → `bad_hello`, batida e ACK viram estado no banco, desconexão devolve o aparelho ao que o transporte alcança |

