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

Precisa de: **Python 3.12 ou mais novo** (o worker real roda 3.12.10), Android SDK com `emulator` e
`platform-tools`, e aceleração (WHPX no Windows, KVM no Linux). O instalador do projeto resolve o SDK:

```bash
pwsh -File scripts\install-prereqs.ps1 -SkipAppium
```

Acerte o relógio ANTES de inscrever — o agente mede o desvio em relação ao central uma vez por conexão, e
acima de 5 s o worker fica `degradado` ("relógio desalinhado") até reconectar:

```bash
pwsh -File scripts\hora-certa.ps1        # como Administrador; no central também
```

Medido em 23/09: o worker estava 97 s atrás do central (e o central, 8 s do NTP). O `w32tm /resync` pode
responder "no time data was available" logo depois de configurar a fonte — nesse caso o script acerta uma vez
pelo desvio medido (`Set-Date -Adjust`) e o w32time mantém daí em diante.

Confira a aceleração antes de seguir — sem ela o emulador sobe lentíssimo ou não sobe:

```bash
C:\Android\Sdk\emulator\emulator.exe -accel-check     # Windows
ls -l /dev/kvm && id -nG | grep -qw kvm && echo 'kvm ok'   # Linux: existe, e a conta do agente vê o grupo?
```

No Linux, o agente declara o resultado dessa conferência no `hello` (`accel`), e a Infraestrutura mostra
**KVM sem permissão** ou **sem KVM** no cartão do worker. Sem KVM, um boot de 2 min vira dezenas de minutos, e
antes disso o sintoma era só um comando estourando prazo do outro lado da rede.

### 3. Instalar o agente (é o instalador que leva o código até lá)

O agente **não** chega por `git clone`: ele é um pacote copiado, com só os módulos que importa de verdade
(`app.worker`, `app.workers`, `app.devices`, `app.security`, `app.config`, `app.util`, `app.version`) e seis
dependências, não as sessenta do backend. Quem faz isso é o instalador — rode-o **na máquina do worker**,
apontando `-Origem`/`--origem` para a árvore do projeto (clonada lá, ou num compartilhamento de rede):

```powershell
# Windows
pwsh -File scripts\worker-install.ps1 -Simular                 # o plano, sem tocar em nada
pwsh -File scripts\worker-install.ps1 -Inscrever <token>       # primeira instalação
pwsh -File scripts\worker-install.ps1                          # atualizar o agente depois
```

```bash
# Linux
sudo bash scripts/worker-install.sh --dry-run
sudo bash scripts/worker-install.sh --enroll <token>
sudo bash scripts/worker-install.sh                            # atualização
```

O instalador grava `app/BUILD_VERSION` com a versão derivada do commit da árvore de origem (`0.1.0+<sha7>`). É
o que faz o central conseguir dizer **agente defasado** no cartão do worker: antes, a cópia em `C:\farm\agent`
não era checkout e as duas pontas diziam `0.1.0` para sempre, dessem elas o mesmo código ou não.

Atualizar é rodar o instalador de novo: ele para o serviço, troca os arquivos e o religa. `worker.yaml` e
`worker-credential.json` não são tocados. No Linux, `KillMode=process` faz os **emuladores continuarem de pé**
enquanto o agente reinicia; no Windows, o mesmo vale porque o emulador nasce em grupo de processos próprio.

### 4. Configurar

**RAM do emulador vem do perfil da imagem** (`backend/app/devices/perfis.py`, medido): `google_apis` → 2048 MB +
`-lowram`; `google_apis_playstore` → 4096 MB; AOSP → 1536 MB + `-lowram`. Deixe `android.ram_mb` e
`extra_emulator_args` FORA do `worker.yaml` para valer o perfil; um número explícito é decisão do dono e vale como
está. O agente reaplica `hw.ramSize` em todo `start` — a mudança pega no próximo start de cada aparelho, sem
recriar AVD. Medido em 23/09/2026: com 1536 MB o convidado `google_apis` entrava em thrash pós-boot (load 22,
87 MB livres) e cada `adb shell` levava 20–40 s; com 2048 MB, não.


O instalador semeia `worker.yaml` a partir de `config/worker.example.yaml` (`C:\farm\worker.yaml` no Windows,
`/etc/farm/worker.yaml` no Linux). Ajuste `server`, `worker_id`, `name`, `max_slots` e a lista de `devices`
**antes de inscrever** — o exemplo não descreve a sua máquina. `sdk_root` e `work_dir` têm padrão por sistema
(`C:\Android\Sdk` / `C:\farm` no Windows; `~/Android/Sdk` / `~/farm` fora dele), então em geral não precisam ser
escritos. **A credencial não vai neste arquivo**: o agente a grava sozinho em `worker-credential.json` depois da
inscrição, com permissão restrita.

**`android.hibernation`** (padrão `false`) diz se ESTA máquina salva snapshot. Quem responde isso é ela, não o
`config.yaml` do central: o agente declara o valor no `Hello` e o painel só oferece "Hibernar" para os aparelhos
dela quando é `true`. Com `false`, o emulador sobe com `-no-snapshot` e `wake` é **recusado** com o motivo —
antes ele fazia um boot a frio de minutos e respondia `from_snapshot: true`, que era sucesso com dado falso.
Cada snapshot ocupa ~1,5 GB em disco por aparelho.

O agente também avisa quando um `start` está **na fila de boot** (`boot_parallelism`, um por vez por padrão): a
espera na fila vira progresso no painel em vez de ser contada como tempo de boot, e a guarda de RAM é reavaliada
na vez de cada boot — avaliada antes da fila, N `start` simultâneos passavam todos pela mesma leitura de
memória livre e só o primeiro tinha a RAM que a conta prometia.

### 5. Serviço: o agente sobe no boot e volta sozinho

Processo iniciado dentro de uma sessão SSH pertence ao job dela e o Windows o mata no logout — medido em 19/09,
com o emulador morrendo em segundos já com o WHPX operacional. O instalador do passo 3 já registra o serviço; o
script que faz isso é `scripts/worker-agent.ps1` (Windows) e a unidade `config/farm-worker.service` (Linux), e
os dois podem ser usados sozinhos:

```powershell
pwsh -File scripts\worker-agent.ps1 -Simular -Instalar    # o que seria registrado
pwsh -File scripts\worker-agent.ps1 -Instalar             # tarefa AtStartup, S4U, RestartCount=999
pwsh -File scripts\worker-agent.ps1 -Remover
```

```bash
systemctl status farm-worker        # Restart=always, KillMode=process, SupplementaryGroups=kvm
journalctl -u farm-worker -f
```

Três coisas que a tarefa registrada tem e a escrita à mão não tinha: **gatilho de boot**, **reinício em falha** e
**nenhum `--enroll` na linha de comando** — o token é de uso único e já foi gasto na inscrição; mantê-lo ali era
segredo gasto exposto em texto. O log do agente vai para `<work_dir>/logs/agente.log` com rotação (5 × 5 MB).

Os emuladores **não** ganham tarefa de boot própria: quem os religa é o central, pelo estado desejado. Quando o
agente reconecta e declara um aparelho `stopped`/`absent` cujo `desired_state` é `online`, o central abre um
`start` rastreável para ele (aparece no histórico do aparelho, com o motivo). Aparelho que alguém parou de
propósito — `desired_state=stopped` — continua parado. Duas fontes ligando o mesmo aparelho brigariam pela porta
do console, e é por isso que `scripts/worker-emulator.ps1` (a tarefa por aparelho, do tempo em que não havia
agente) segue **sem** gatilho de boot.

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

**b) Porta de rede no central, com autenticação e TLS.** Para isso o central precisa das **três** coisas
juntas, e ele **recusa subir** se faltar qualquer uma:

```yaml
# config/config.yaml
server:
  host: 0.0.0.0
  public_hosts: [central.parque.local, 192.168.1.10]
  # A ORIGEM que o navegador vê na barra de endereços — esquema, nome e porta. Sem ela, todo POST do painel
  # remoto (o login inclusive) leva 403 `forbidden_origin`, porque a conferência de CSRF não reconhece a
  # origem. Atrás de um proxy em 443, é a origem do PROXY, sem `:8000`.
  allowed_origins: [https://central.parque.local:8000]
  # TLS: uma das duas linhas abaixo, nunca nenhuma.
  tls_cert: C:/parque/tls/central.crt       # servido pelo próprio processo (https:// e wss://)
  tls_key:  C:/parque/tls/central.key
  # tls_behind_proxy: true                   # …OU um Caddy/nginx/IIS termina o TLS na frente
```

```bash
# .env — gere com: python -c "import secrets; print(secrets.token_urlsafe(32))"
API_TOKEN=...
```

Por que as três: o `API_TOKEN` responde "quem é você", o `public_hosts` responde "por qual nome você me
chamou" e o TLS responde "quem mais está lendo isto". A segunda pergunta não é redundante — é a defesa contra
*DNS rebinding*, em que um nome controlado pelo atacante resolve para `127.0.0.1` e um navegador da vítima passa
a falar com o seu backend. Nome que não está na lista é recusado com `forbidden_host` **mesmo com a credencial
certa**.

A terceira também não é: sem TLS, **nesta opção** vão em claro pela rede o `API_TOKEN` (a cada requisição), a
credencial permanente do worker (a cada conexão), o cookie de sessão do painel e todo screenshot ou evidência.
Rede Wi-Fi com chave compartilhada é rede legível por quem tem a chave — e é justamente a rede em que o notebook
do parque vive. (Na opção (a) isso não se aplica: o tráfego vai dentro do SSH.)

> **Certificado próprio e o canal do túnel não convivem.** `tls_cert` vale para o processo inteiro, e o listener
> de `server.worker_port` é outro socket do MESMO uvicorn: ele passaria a exigir `wss://` do agente, com um
> certificado emitido para o nome público e não para `127.0.0.1`. O central recusa subir nessa combinação e diz
> o que fazer — `tls_behind_proxy` com um proxy na frente, ou `worker_port: 0` quando nenhum worker chega por
> túnel.

**Do lado do worker**, em `worker.yaml`:

```yaml
server: https://central.parque.local:8000
# ca_file: C:/farm/parque-ca.pem          # só quando o certificado é de uma CA sua (parque doméstico)
```

O agente converte o esquema sozinho (`https` → `wss`) e **recusa** `http://` para qualquer endereço que não
seja `127.0.0.1` — é a conexão que carrega a credencial permanente dele. `ca_file` acrescenta a sua autoridade
à verificação; não existe opção de desligar a verificação, porque desligá-la seria aceitar exatamente o
certificado que um ataque apresentaria.

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
- **O log do agente também é filtrado** (achado #128). O `RedactingFilter` estava instalado só no backend, e o
  agente roda em OUTRA máquina, com `logging.basicConfig` próprio: o processo mais perto do aparelho era o único
  sem a defesa secundária. Agora `app/worker/__main__.instalar_redacao_de_log()` o põe nos **handlers** — e não no
  logger raiz, porque filtro de logger só vale para o que é emitido naquele logger, e tudo do agente sai em
  `poc.worker.*`, que apenas propaga. Um `addFilter` na raiz não redigiria uma linha sequer, e falharia calado.
  A redação não traz dependência nenhuma (só `re`): as seis dependências do agente continuam seis.
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
- **Sessão de usuário (item 9.1).** O painel tem tela de login: `POST /api/login` troca o nome de quem está
  operando (mais o `API_TOKEN`, quando a chamada vem de fora do loopback) por um cookie `HttpOnly`,
  `SameSite=Strict`, `Path=/api`, com `Secure` sempre que houver TLS declarado. O cookie é o que permite abrir o
  painel de OUTRA estação: `new WebSocket(...)` e `<img src=...>` não conseguem mandar `Authorization`, e era
  por isso que frame, evidência e avatar davam 401 fora do central. O token continua valendo em `Authorization`
  para chamadas de máquina.
- **A auditoria passou a ter nome.** `commands.requested_by` e `pending_approvals.decided_by` gravam o operador
  da sessão, e o nome do corpo da requisição **não** vence o da sessão. `panel` continua aparecendo quando
  ninguém se identificou — inclusive no loopback, onde o login é só o nome (sem token), porque ali quem chama
  já tem o banco e o adb na mão. O que **não** existe é conta por pessoa com senha própria: quem tem o
  `API_TOKEN` entra com o nome que quiser. Separar acesso por pessoa é decisão de quem cuida do parque, e está
  fora deste item.

## Recuperação

| Situação | O que fazer |
|---|---|
| Perdi a credencial do worker | Apague `worker-credential.json`, clique **Remover** no cartão do worker (Infraestrutura) e inscreva de novo com um token novo |
| Suspeito que a credencial vazou (máquina comprometida) | Clique **Rotacionar credencial** no cartão do worker: a credencial antiga para de servir na hora e o painel mostra a nova, uma única vez — grave-a em `worker-credential.json` e reinicie o agente. Não precisa remover nem reinscrever |
| Token venceu ou já foi usado | Gere outro; ele é de uso único de propósito |
| O agente diz `protocol_too_new` | O agente é mais novo que o servidor: atualize o **central** |
| O agente diz `protocol_too_old` | O agente é velho demais para este servidor: rode o instalador do passo 3 de novo na máquina dele |
| O cartão do worker mostra **agente defasado** | O código de lá não é o daqui. Passe o mouse na etiqueta para ver a versão que o central espera e rode o instalador na máquina do worker |
| O cartão mostra **sem KVM** ou **KVM sem permissão** | `/dev/kvm` não existe (habilite a virtualização) ou a conta do serviço não está no grupo `kvm` (`usermod -aG kvm farm` e reinicie a unidade). Sem isso um boot de 2 min vira dezenas |
| Worker aparece offline mas está ligado | Veja `last_seen_at` na Infraestrutura; sem batida há >30 s, o problema é rede ou o processo do agente |
| Manutenção | Painel → Infraestrutura → manutenção. Suspende **novas** atribuições (comando de painel e tarefa de IA) e não derruba o que já está em voo |
| Remover um worker | `DELETE /api/workers/{id}` (ou o botão **Remover** no painel). Recusa com 409 se o worker está conectado ou tem comando em voo — desconecte-o primeiro, ou confirme de novo no painel para remover com `force`. Os aparelhos amarrados a ele ficam sem dono (não são apagados); amarre-os a outro worker ou reinscreva este com o mesmo id |

## Quando a máquina reinicia (aceite 7)

Depois de um reboot — do central ou do worker — **nada precisa ser iniciado à mão**. Era o contrário: o túnel
voltava sozinho (tarefa com gatilho de boot) e o backend não, porque existia só porque alguém o iniciara numa
sessão do console; logoff, Windows Update ou crash deixavam API, agendador e Appium fora até alguém voltar à
máquina, com o agente do worker reconectando no vazio.

**Ordem de subida no central**, e o que cada peça faz:

| Quando | O que sobe | Registrado por |
|---|---|---|
| Boot | `farm-tunel-<worker>` — o túnel SSH (ADB dos aparelhos remotos + `-R` do agente) | `scripts/worker-tunnel.ps1 -Instalar` |
| Boot | `farm-central` — o **supervisor**, que sobe `python -m app.main` | `scripts/start.ps1 -Instalar` (= `scripts/install-central-service.ps1`) |
| Com o backend | Appium local, agendador, e os aparelhos de `auto_start_devices` | o próprio backend |

As duas tarefas são independentes e o backend **não** espera o túnel: sem ele, o worker aparece offline até o
túnel subir, e isso se resolve sozinho. Ensaio antes de registrar de verdade:

```powershell
pwsh -File scripts\start.ps1 -Instalar -Simular
```

O supervisor cobre o que a tarefa sozinha não cobre: processo **vivo e travado**. Ele pergunta a
`/api/health` a cada 15 s e religa o backend depois de **três** respostas ausentes seguidas — ausentes, não
ruins: `degraded` é resposta, e reiniciar por causa dela trocaria um problema visível por um laço de reinício
(e apagaria o Appium que o próprio backend acabou de subir). Log em `data\logs\supervisor.log`.

**No worker**, o serviço do passo 5 traz o agente de volta, e o central religa os aparelhos pelo estado
desejado na reconexão (passo 5, último parágrafo).

**Um detalhe que já derrubou o túnel:** a tarefa guarda o caminho do executável como TEXTO, e o `pwsh` do pacote
da Microsoft Store mora em `C:\Program Files\WindowsApps\Microsoft.PowerShell_<versão>_x64__.../pwsh.exe` — some
na próxima atualização da Store, e no boot seguinte a ação aponta para nada. Por isso `-Instalar` **recusa** o
pwsh da Store e manda instalar o MSI (`winget install --id Microsoft.PowerShell --source winget`); e por isso a
tarefa do central aponta para `backend\.venv\Scripts\python.exe`, que é caminho desta árvore. `-Instalar`
também recusa quando uma porta local do `-Mapa` já é usada por outro `farm-tunel-*` (com dois workers, o mapa
padrão colide) e, ao reinstalar, derruba **só** o laço daquele worker — antes matava o de todos.

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

### O túnel entra com conta de serviço, não com Administrator (item 9.4 / achado #123)

O desenho anterior era desproporcional ao que o túnel precisa. A chave `worker_ed25519` **não tem passphrase** —
e não pode ter, porque a tarefa agendada sobe sozinha no boot, sem ninguém para digitar nada. Ela era autorizada
no worker em `C:\ProgramData\ssh\administrators_authorized_keys`, **sem nenhuma opção**. Medido: com aquele
arquivo dava para rodar `whoami`, `icacls` e `Get-Content` como Administrator na máquina do worker. Um arquivo no
central equivalia a administrador do sistema operacional em cada worker do parque — movimento lateral do central
para todos os hosts, com um `cat` de distância.

O túnel precisa de duas coisas, e só: `-L` até a porta de ADB de cada emulador (em `127.0.0.1` do worker) e `-R`
para abrir a porta que o agente usa para chegar ao central. Nada disso é shell, pty, agente de chaves ou
privilégio. O `scripts/worker-ssh-restrito.ps1` prepara o worker de acordo:

```powershell
# NO WORKER, PowerShell como administrador. A chave pública sai do central:
#   ssh-keygen -y -f C:\Users\Administrator\.ssh\worker_ed25519
pwsh -File scripts\worker-ssh-restrito.ps1 -ChavePublica "ssh-ed25519 AAAA... central" `
     -PortasAdb "5555,5557,5559,5561,5563,5565" -Simular      # confere o que seria gravado
pwsh -File scripts\worker-ssh-restrito.ps1 -ChavePublica "ssh-ed25519 AAAA... central" `
     -PortasAdb "5555,5557,5559,5561,5563,5565"
```

Ele cria a conta `farm-tunel` (só no grupo `Users`, senha aleatória que ninguém anota — a autenticação é por
chave), grava a chave em `C:\ProgramData\ssh\authorized_keys\farm-tunel` com ACL restrita e acrescenta ao fim do
`sshd_config` um bloco `Match User farm-tunel` com `PermitTTY no`, `AllowAgentForwarding no`, `X11Forwarding no`
e `ForceCommand exit`. A linha da chave fica assim:

```
restrict,port-forwarding,permitopen="127.0.0.1:5555",…,permitlisten="127.0.0.1:18000",command="exit" ssh-ed25519 AAAA…
```

Três detalhes que custam caro se passarem despercebidos:

- **`restrict` não impede executar comando.** Ele tira pty, agente, X11 e user-rc; quem fecha a porta do shell é
  o `command="exit"` (e o `ForceCommand` do lado do `sshd`).
- **`port-forwarding` vem DEPOIS do `restrict`**, senão o próprio túnel deixa de subir.
- **`permitopen` é por porta, e porta de ADB é uma por aparelho.** Sem `-PortasAdb` o padrão é
  `permitopen="127.0.0.1:*"`, que continua sendo só o loopback **do worker** (nunca a LAN dele) e não obriga a
  reeditar o `sshd_config` cada vez que o parque ganha um aparelho.
- **A porta reversa é autorizada SEM endereço** (`permitlisten="18000"` e `PermitListen 18000`), e isto não é
  descuido. O cliente pede `-R 18000:127.0.0.1:8010` sem endereço de bind, e o que viaja no fio nesse caso é o
  nome `localhost` — que o `sshd_config(5)` trata como **diferente** de `127.0.0.1`. O sshd exige que tanto o
  `PermitListen` do administrador quanto o `permitlisten` da chave aceitem o pedido; escrito só na forma
  `127.0.0.1:18000`, o encaminhamento seria recusado e, com `ExitOnForwardFailure=yes`, o túnel simplesmente não
  subiria — depois de o acesso antigo já ter sido removido. Quem garante que a escuta é só loopback continua
  sendo o `GatewayPorts no` padrão.
- **O arquivo de chaves fica em `__PROGRAMDATA__/ssh/authorized_keys/%u`**, não em `~/.ssh`: o perfil de uma
  conta que nunca fez logon interativo pode não existir ainda.

Com o worker preparado, o `-Usuario` padrão do `worker-tunnel.ps1` já é `farm-tunel`. Confirme o túnel novo de
pé **antes** de passar `-RemoverChaveDeAdministrador` (que tira a linha antiga do
`administrators_authorized_keys`): apagar a única forma de entrar numa máquina remota antes de a nova funcionar é
como se perde um worker.

### Chave de host registrada por gente, não por `accept-new`

O script usava `StrictHostKeyChecking=accept-new`: a primeira conexão a um worker novo aceitava **qualquer** chave
que respondesse naquele IP, e é nessa primeira conexão que a credencial permanente do agente atravessa o `-R`.
Agora é `StrictHostKeyChecking=yes`, e o registro é um passo explícito:

```powershell
pwsh -File scripts\worker-tunnel.ps1 -Worker 192.168.1.19 -RegistrarChaveDeHost
```

Ele faz `ssh-keyscan`, grava no `known_hosts` ao lado da chave e **imprime as impressões digitais**. Compare-as
no console do próprio worker antes de confiar no túnel:

```powershell
Get-ChildItem C:\ProgramData\ssh\ssh_host_*_key.pub | ForEach-Object { ssh-keygen -lf $_.FullName }
```

Sem entrada no `known_hosts`, o túnel agora **falha na partida com a mensagem que diz o que fazer**, em vez de
confiar em quem responder primeiro.

**Conferido em 23/09/2026 para 192.168.1.19**: o dono rodou o comando acima no console do notebook
(`WIN-EDHUOCQJ6JJ`) e as três impressões bateram com o `known_hosts` do central — ED25519 `SHA256:wtLix8ag…`,
RSA `SHA256:XUkP3SZ6…`, ECDSA `SHA256:iaU1b9Rd…`. O que o túnel confia hoje é a máquina certa. Refaça a
conferência se o sshd do worker for reinstalado (as chaves de host são regeradas).

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

## Loja num worker remoto (decisão 4, opção a — 24/09/2026)

A VM-loja (imagem `google_apis_playstore`) pode rodar num worker: o `worker.yaml` aceita `system_image`, `ram_mb` e
`window` por aparelho (ver `config/worker.example.yaml`). O que **não** passa pela plataforma é a conta Google:

1. Abra uma sessão de área de trabalho remota (RDP) até a máquina do worker — fora do painel.
2. Na janela do emulador da loja (`window: true`), entre na conta Google e toque em Instalar na Play Store.
3. De volta ao painel, "Buscar da loja" copia o APK que a loja já instalou, por ADB, pelo túnel.

Nenhuma tecla da conta Google trafega pelo painel, pelo túnel ou pelo banco: é o mesmo regime da loja local. A
opção (b) — liberar texto na loja por um canal com o regime do cofre — fica para quando uma loja remota virar
necessidade real.
