# Parque distribuído: usar a RAM de outras máquinas — projeto e o que ainda falta medir

**Data:** 19/09/2026 · **Estado:** **Etapa 0 EXECUTADA e aprovada na LAN** (ver §"Resultado da Etapa 0"). As
etapas 1 a 4 seguem como proposta.

## Por que

O servidor atual sustenta 4 emuladores simultâneos (`max_online_devices: 4`). Com o perfil enxuto hoje em uso
(`-lowram` + `ram_mb: 1536`) são ≈2,4 GB em repouso e ≈2,9 GB com app e automação rodando —
`est_instance_ram_mb: 3000` no `config.yaml`. O rodízio contorna o limite atendendo N contas sobre K vagas, mas o
teto é de RAM desta máquina. O objetivo é somar a RAM e a CPU de outros notebooks — um na mesma rede e outros em
redes que não controlamos — **mantendo um único servidor central** (Appium, IA, banco e painel ficam aqui).

## Decisões do usuário (restrições de entrada)

1. Workers **mistos** (Windows e Linux). O servidor enxerga só "dispositivos ADB disponíveis", agnóstico de como
   foram criados.
2. Redes remotas por **túnel reverso iniciado pelo worker**. Nada de abrir porta em roteador alheio.
3. Worker fornece **apenas dispositivos + ADB**. Appium continua no centro.
4. Precisa de **instalador/agente** que registre a máquina, publique capacidade e suba/derrube dispositivos.

## O que o código já tem (pontos de encaixe reais)

| Capacidade | Onde | Consequência |
|---|---|---|
| Instância pode ser um aparelho externo, serial = `host:porta` | `devices/manager.py:114` | É exatamente a fronteira que a arquitetura pede |
| Adoção faz `adb connect host:porta` e só então marca online | `devices/adb.py:238`, `manager.py:341` | O caminho remoto já é o caminho testado |
| Appium recebe esse serial como `appium:udid` | `automation/appium_driver.py:35` | O Appium central já é apontado por endereço de rede |
| Monitor reconfere a cada 30 s e recupera sozinho | `manager.py:369` | Queda de Wi-Fi já é tratada como estado, não como erro |
| **`slots_used()` exclui aparelhos externos** | `manager.py:717` | Aparelho de outra máquina **não** disputa a RAM daqui |

Falta: registro dinâmico (hoje `external` é mapa estático no `config.yaml`), agente no worker, contabilidade de RAM
**por worker** e o túnel.

## Arquitetura escolhida

### Plano de controle e de dados

O servidor central faz **`adb connect worker:porta` através do túnel**, e cada dispositivo remoto entra no
**servidor adb único** do centro, endereçado por `-s host:porta`. Isso mantém a decisão 3.

**Mas o gancho `external` só serve pela metade, e é importante dizer isso.** O docstring do `_adopt_external`
(`manager.py:341`) é explícito: ele existe para aparelho *que o projeto não controla* — só confere se o ADB o vê.
O dispositivo de um worker é o oposto: o centro precisa **ligar, desligar, hibernar e rodiziar** através do agente.
Ou seja, nasce um **terceiro tipo de instância — "remota gerenciada"**: ciclo de vida pelo agente, ADB pelo túnel.

- O que se reaproveita de verdade: o endereçamento `host:porta`, o `adb connect`, o `appium:udid` e — decisivo — a
  exclusão do `slots_used()`.
- O que **não** se reaproveita: o ciclo de vida. Se a instância remota for tratada como `external` pura, o rodízio
  (`_rotate`) nunca se aplica às vagas remotas e elas ficam presas ligadas.

A alternativa oficial — `appium:remoteAdbHost`/`adb -H` — foi **descartada**: ela exige um servidor adb rodando no
worker e muda o modelo de portas. É outra topologia, não uma variação.

> Fonte: man page do adb no AOSP — `connect HOST[:PORT] … [default PORT=5555]`, `-s SERIAL`, `-H`/`-P` como smart
> socket separado. As duas topologias existem e são distintas.

### Segurança: o túnel é o único limite

Não há autenticação a recuperar se o túnel falhar:

- Em **emulador**, a autenticação por chave do ADB não protege — o launcher oficial do Google sobe o emulador com
  `-skip-adb-auth`.
- O servidor adb exposto com `-a` (`-L tcp:0.0.0.0:5037`) é **controle total sem autenticação nenhuma**.
- O próprio STF trafega em claro entre servidor e máquina dos aparelhos.

Por padrão o adb escuta **só em loopback**; expor é opt-in deliberado. O túnel reverso entrega a porta do worker
como loopback no servidor central — nada vai para interface pública. **A decisão 2 não é preferência, é o que
sustenta a segurança da arquitetura inteira.**

### Como cada worker cria dispositivos

| Worker | Rota | Observação |
|---|---|---|
| Linux | Imagem de contêiner oficial do Google, **ou** AVD + KVM | O contêiner publica **uma porta TCP de ADB (5555)** e o cliente anexa com `adb connect` — confirma a fronteira escolhida |
| Windows | **Só** AVD nativo + WHPX, fora de contêiner | A rota de contêiner do Google é *Linux Only*; Docker Desktop não entrega KVM |

O escape por WSL2 existe apenas no Windows 11 e paga virtualização aninhada — o próprio Google reconhece a
penalidade. **Consequência da decisão 1:** os dois tipos de worker convivem, mas só os Linux ganham a rota barata.

### Agente do worker (registro, saúde, capacidade)

Padrão copiado do Selenium Grid e do STF, ambos com registro **iniciado pelo worker** — compatível com túnel
reverso:

- O agente conhece o endereço do servidor central; o servidor **não** lista workers em configuração.
- No registro, o worker **declara**: endereço que serve, endpoint de saúde, e as vagas que aceita com suas
  etiquetas (imagem, API level, backend de virtualização).
- **Heartbeat** reconstrói o inventário sozinho: no STF, batida a cada 10 s e o reaper marca ausente após 30 s
  (três perdidas), restaurando quando voltam. São *defaults configuráveis*, não números sagrados.
- Inventário de workers/dispositivos pode viver em memória; **estado de lease, rodízio e snapshot não pode**.

**Aviso de terreno novo:** no STF o provider **nunca cria** dispositivo — ele adota o que já está plugado. Nosso
agente precisa *criar* AVDs. Essa parte não tem precedente pronto para copiar; é o que o instalador viabiliza.

### Instalador e enrolamento (a peça que o usuário pediu)

O que o instalador deixa na máquina:

1. **Dependências**: `cmdline-tools` do Android SDK, `emulator`, e ao menos uma imagem de sistema. São vários GB —
   decidir entre baixar na instalação (mais lento, sempre atual) ou empacotar. Recomendo baixar na instalação,
   ecoando a regra que já vale para APK: **o instalador não baixa binário de terceiro**, usa o canal oficial do SDK.
2. **Agente + cliente de túnel**, instalados como **serviço** (serviço do Windows / unidade systemd), para subir com
   a máquina e reconectar sozinho.
3. **Credencial do worker.**

O enrolamento responde ao "ao configurado aqui" do pedido: o painel central gera um **token de inscrição** de uso
único e prazo curto; quem instala cola esse token; o agente o troca **uma vez** por uma credencial permanente de
worker. O servidor central nunca lista workers em arquivo, e o token não vira segredo de longa duração.

**Atenção:** a chave privada de ADB do servidor tem de chegar a cada worker para o pareamento funcionar. Ela é
credencial real e entra no mesmo regime dos outros segredos do projeto — nunca em repositório, log ou evidência.

### O que NÃO copiar dos precedentes

- **Plano de dados do STF é INBOUND**: cada provider *binda* uma faixa (15000–25000 na referência) e o servidor
  disca para o IP dele. Atrás de NAT alheio isso exigiria tunelar a faixa inteira, com faixas disjuntas por worker.
  Nosso desenho mantém **tudo outbound a partir do worker**.
- **Selenium Grid relay é precedente CONTRA a decisão 3**, não a favor: ele pressupõe um Appium rodando em cada
  worker. Quem cita relay como apoio a "Appium central dirigindo ADB remoto" está aplicando mal a fonte.
- **Nenhum precedente faz admissão por RAM livre.** O `maxSessionCount` do Grid é inteiro estático derivado do
  número de CPUs no boot. A proteção contra o worker aceitar mais dispositivos do que a RAM suporta **tem que ser
  escrita por nós** — é, aliás, o problema central deste projeto, e o único sem referência pronta.

## O que a pesquisa NÃO respondeu (e decide o projeto)

Três das sete perguntas ficaram sem evidência verificada. São as que definem custo e desempenho:

1. **Ganho de capacidade em Linux (C).** Não sobrou nenhuma medição de RAM por dispositivo em Linux+KVM, redroid,
   Waydroid ou Genymotion para comparar com os 3,5–3,7 GB já medidos aqui.

   **Para worker Windows a lacuna não existe** — é a mesma imagem android-34/WHPX com o mesmo perfil `-lowram`
   já medido aqui. O ganho se calcula direto:

   > vagas ≈ (RAM do worker − ~4 GB para SO, agente e túnel) ÷ 3,0 GB

   Worker de **16 GB ≈ +4 aparelhos**; de **32 GB ≈ +9**. Esse é o número que responde à pergunta original, e não
   depende de nenhuma pesquisa pendente.
2. **Escolha do túnel (D).** Nenhuma claim sobreviveu comparando SSH `-R`/autossh, frp, chisel, rathole,
   Cloudflare Tunnel, ngrok e WireGuard/Tailscale. A decisão 2 está justificada; a **ferramenta**, não.
3. **Banda e latência (F).** Único sinal, e é negativo: o próprio STF **desaconselha** dispositivos ADB remotos
   por lentidão. Ninguém mediu tráfego por dispositivo, nem Wi-Fi doméstico, nem WAN.

E o teste empírico literal da pergunta A — **o Appium central dirige um dispositivo que existe só via
`adb connect`?**, incluindo instalação do UiAutomator2 server, `adb forward` do `systemPort`, screenshots e page
source — **não foi respondido por nenhuma fonte**.

## Plano por etapas — medir antes de construir

**Etapa 0 — a prova de viabilidade (horas, na LAN).** O túnel é obrigatório (ver a medição acima), então o
emulador do worker sobe e o **servidor central** abre o túnel para ele:

```
ssh -L 15555:127.0.0.1:5555 usuario@worker
```

**Sentido escolhido: `-L`, do central para o worker** — o contrário do que a arquitetura final usará. Na LAN não
há NAT, e assim o servidor central opera o worker por SSH (instala SDK, cria AVD, sobe emulador) sem ninguém
precisar manter terminal aberto do outro lado. Para worker em **outra rede**, aí sim inverte para `-R` iniciado
pelo worker, e o servidor de SSH passa a ser o central — é a Etapa 2.

Daqui: `adb connect 127.0.0.1:15555`, registrar como aparelho externo e **rodar uma execução real**. Critério de
aprovação concreto: o Appium central abre sessão, o `adb -s 127.0.0.1:15555 forward tcp:<systemPort>` funciona, o
UiAutomator2 server instala, e screenshot e page source voltam. Medir o tráfego durante a execução responde F.

Alvo do teste: instância **sem perfil de Instagram vinculado**, com o APK interno do QA Messenger. Prova o
caminho técnico inteiro sem nenhum efeito externo real e sem passar por aprovação.

Ou isso acontece, ou a decisão 3 cai aqui — antes de existir agente, instalador ou túnel definitivo. **Nada mais
deve ser construído antes desta etapa.**

**Etapa 1 — quanto se ganha.** Medir RAM por dispositivo no worker Linux (AVD+KVM e contêiner do Google) e no
worker Windows (AVD+WHPX), com o mesmo método do `probe-image.ps1`. Se o ganho por máquina não justificar, o
projeto muda de forma.

**Etapa 2 — túnel.** Escolher por experimento entre as opções, com os critérios que a pesquisa não cobriu:
estabilidade em conexão longa, NAT traversal, instalação como serviço nos dois SOs, latência acrescentada.

**Etapa 3 — agente e registro.** Registro iniciado pelo worker, heartbeat, etiquetas de vaga, e a peça sem
precedente: **admissão por RAM livre**, recalculada a cada batida — o equivalente por worker da guarda de RAM que
já existe aqui.

**Etapa 4 — instância dinâmica.** Tirar `external` do `config.yaml` estático e permitir que um dispositivo anunciado
por um worker vire instância em tempo de execução, com `slots_used()` passando a contar **por worker**.

## Resultado da Etapa 0 (19/09/2026) — a arquitetura está provada

Executada de ponta a ponta entre este servidor e um segundo Windows Server 2025 na mesma rede Wi-Fi
(192.168.1.19, 12 CPUs lógicas, 63,7 GB de RAM, 2,7 TB livres).

**A pergunta que nenhuma fonte respondeu está respondida: sim.** O Appium central abriu sessão
UiAutomator2 completa contra um aparelho que existe só via `adb connect` através de um túnel SSH:

```
POST /session {"appium:udid":"127.0.0.1:15555","appium:systemPort":8208, ...}
[AndroidUiautomator2Driver] Using device: 127.0.0.1:15555
New AndroidUiautomator2Driver session created successfully
```

O painel lista `android-09` como `online`, serial `127.0.0.1:15555`, "aparelho externo via ADB".

### Medições (fecham a lacuna F)

| Medida | Resultado |
|---|---|
| `adb shell getprop`, 20 chamadas — **remoto** | **114 ms**/chamada |
| idem — **local**, para comparação | 168 ms/chamada (locais estavam carregados) |
| `screencap` + `pull` (PNG real de 673 KB) | 210–277 ms → **2,4–3,2 MB/s** |
| `push` de 20 MB | 1,6 s → 12,5 MB/s (dado compressível; use 3 MB/s para APK real) |
| `adb forward tcp:… tcp:6790` | funciona; abre a porta **no servidor central** |
| Boot a frio do AVD recém-criado | ~3 min (primeiro boot; os seguintes são menores) |

**O aviso do STF sobre lentidão de ADB remoto não se aplica nesta escala.** Na LAN o aparelho remoto
respondeu mais rápido que os locais ocupados. O número continua desconhecido para WAN.

### O que quebrou no caminho, e por quê (para o instalador não repetir)

1. **O emulador morre junto com a sessão SSH.** Iniciado com `Start-Process` dentro do SSH, ele pertence ao
   *job* da sessão e o Windows o mata no logout — morreu em segundos, já com o WHPX operacional. **Solução:
   tarefa agendada** (`Register-ScheduledTask`, `-LogonType S4U`), que roda sob o serviço Agendador, fora do
   job. Confirmado: sobreviveu ao fim da sessão. O agente definitivo deve ser serviço pelo mesmo motivo.
2. **`ssh-keygen -N '""'` no PowerShell grava a senha literal `""`.** O sintoma engana: o log diz *"Server
   accepts key"* e em seguida *"Permission denied"*. Use `-N ""`.
3. **Conta administrativa ignora `~/.ssh/authorized_keys`** — lê `C:\ProgramData\ssh\administrators_authorized_keys`,
   que precisa de ACL exatamente SYSTEM + Administrators, senão a recusa é silenciosa.
4. **Sem `DefaultShell` em `HKLM:\SOFTWARE\OpenSSH`**, todo comando remoto cai no `cmd.exe`.
5. **`install-prereqs.ps1` aborta por stderr.** O `sdkmanager` escreve um aviso de depreciação em stderr e,
   com `ErrorActionPreference = Stop` no PowerShell 5.1, vira erro fatal — *depois* de instalar tudo. O
   script precisa tolerar stderr de comando nativo.
6. **`$env:USERDOMAIN` vem vazio na sessão SSH**, e `Register-ScheduledTask` falha com "No mapping between
   account names and security IDs". Derive de `[Security.Principal.WindowsIdentity]::GetCurrent().Name`.

### Execução real, com IA, nos aparelhos remotos

Não ficou na sessão: rodou objetivo de verdade. Comando *"Abra o QA Messenger e me diga qual conta está no topo
e quantas conversas aparecem"*, nos **dois** aparelhos do worker (`r-20260919144626-852576`):

```
2 de 2 com sucesso comprovado          (76 s)
android-09  succeeded  IA=2 chamadas  16.425 tokens   conta lida: qa-user-09, 8 conversas
android-10  succeeded  IA=0 chamadas        0 tokens   conta lida: qa-user-10, 8 conversas
```

**O android-10 gastou zero de IA**: a receita aprendida no android-09 foi reproduzida por seletores. O
reaproveitamento que já existia no parque **atravessa a fronteira de máquina** sem nenhuma adaptação.

Antes disso, a guarda de senha bloqueou corretamente o primeiro ensaio (*"O app pede autenticação (campo de
senha na tela)"*) e o fez **sem gastar nenhuma chamada de IA** — o aparelho remoto não é um caminho que escape
das proteções.

### Túnel durável

`scripts/worker-tunnel.ps1 -Instalar` registra tarefa agendada que sobe no boot e mantém o `ssh -L`.
Verificado: matando o processo `ssh`, o túnel voltou sozinho em **5 s** com PID novo. `ServerAliveInterval=30`
detecta queda em ~90 s; falhas rápidas seguidas aumentam a espera até 60 s para não martelar.

Dois defeitos meus no caminho, ambos clássicos de Windows:

- **`pwsh -File ... -Portas 1,2` não vira array.** A tarefa subiu encaminhando a porta "1555515557". A
  correção é passar `-Mapa "15555:5555,15557:5557"` como texto e fazer o parse dentro. É a mesma armadilha já
  registrada para `-ImageTags` no `install-prereqs.ps1`.
- **A tarefa agendada executava `powershell.exe` (5.1)** e morria com `LastTaskResult=1` num `Join-String`,
  que só existe no 7. Agora ela procura `pwsh` e só cai para o 5.1 se não houver.

### Seis aparelhos remotos (19/09/2026) — e o gargalo não é onde eu apostei

`r-20260919161223-916157`: **6 de 6 com sucesso comprovado, 142 s, ZERO chamadas de IA.** Seis aparelhos que
existem só na outra máquina, cada um lendo a própria conta.

O custo marginal de somar aparelho é quase nulo: a receita aprendida uma vez é reproduzida por seletores em
todos os outros. Na rodada anterior foram 6 aparelhos com **1** chamada de IA no total.

**Eu apostei errado e a medição corrigiu.** Quatro instalações falharam com timeout (`adb shell excedeu 30s`) e
a hipótese óbvia era o túnel — uma única conexão SSH para os seis aparelhos. Medição:

| | |
|---|---|
| 1 aparelho sozinho | 311 ms/chamada |
| **6 em paralelo** | **75 ms/chamada efetiva** (60 chamadas em 4,5 s) |

O túnel escala; **o convidado é que fica lento**. Com 45 GB livres e CPU em 42%, o limite é a disputa de CPU
durante operação pesada simultânea. Confirmado instalando no aparelho que falhou, agora sozinho: **34 s**.

**Conclusão de capacidade, honesta:** o worker (12 CPUs) comporta **6 aparelhos existindo**, mas só
**3 a 4 trabalhando pesado ao mesmo tempo**. É o mesmo modelo de rodízio que o parque já usa — N contas sobre
K vagas —, agora por máquina.

### Quatro defeitos que só aparecem ao escalar

1. **Boot simultâneo trava o convidado.** Subi 4 emuladores de uma vez e os quatro ficaram presos em ANR do
   SystemUI/`system`, sem se recuperar. Reiniciados **um a um**, subiram limpos em 104–192 s. O parque já tem
   `boot_parallelism: 2` exatamente por isso; o worker precisa da mesma regra, e hoje não tem.
2. **Falha de entrega é pegajosa.** *"A entrega da versão 1.0.0 (1) falhou neste aparelho e não é repetida
   sozinha"* — e o aparelho fica em `waiting_user` para sempre até alguém mandar distribuir de novo. Somado ao
   timeout sob disputa, é a principal fricção operacional ao subir muitos aparelhos.
3. **`adb install -r` é invisível para a camada de releases.** Instalei por fora para contornar o timeout; o
   app estava lá e funcionando, e o painel continuou dizendo `verifying`/falhou. Confirma o achado de auditoria
   já registrado (reinstalação com o mesmo versionCode não chega à camada de release).
4. **`hide_error_dialogs` não cobre o diálogo que já está na tela** — daí `dismiss_system_dialog()`. Mas ele
   também não resolve ANR real: tocar em "aguardar" apenas adia, e o diálogo volta enquanto o processo estiver
   travado. Dispensar diálogo é paliativo; a cura é não sobrecarregar o boot.

### Dois tetos herdados do parque de 10 vagas

- **`limits.max_active_devices` é `Field(10, ge=1, le=10)`** (`backend/app/config.py:108`): trava **no código**,
  não na configuração. Com 4 locais + 6 remotos batemos exatamente nele. Crescer exige mexer ali.
- **O app de QA aceitava só `qa-user-01..10`** e recusava **em silêncio** — o provisionamento imprimia
  `account=` vazio e seguia como se tivesse dado certo. Corrigido para 99 (`qa-app/.../Contract.java`).

### Estado atual e o que ainda não foi feito

- Rodando hoje: **6 emuladores no worker**, túnel durável com 6 encaminhamentos, `android-09`, `10` e `12…15`
  em `instances.external`. `instances.count: 15`.
- Scripts do worker versionados: `scripts/worker-avd.ps1` (cria o AVD com o perfil do parque),
  `scripts/worker-emulator.ps1` (sobe por tarefa agendada), `scripts/worker-tunnel.ps1` (túnel durável).
  São o embrião do instalador, **não** o instalador: não há registro, heartbeat nem enrolamento.
- **Falta escalonar o boot no worker.** Enquanto não houver o equivalente a `boot_parallelism`, subir vários
  emuladores de uma vez vai travá-los em ANR. Hoje isso é disciplina minha, não regra do código.
- **A guarda de capacidade vive só no worker** (`scripts/worker-emulator.ps1`: RAM e núcleos). O servidor
  central continua sem saber quanta RAM a outra máquina tem — `slots_used()` exclui externo de propósito.
  Enquanto o central não **ligar** aparelho remoto, não há admissão a proteger daqui; quando ligar (Etapa 3/4),
  aí a contabilidade por worker passa a ser necessária de verdade.
- **Nada disso foi testado em outra rede.** Continua valendo só para a LAN.

## Correções de leituras comuns (não propagar)

- Heartbeats 10 s/30 s do STF são **defaults configuráveis**, não valores fixos.
- O nginx do STF roteia por **prefixo de PATH**, não por header `Host`.
- `ADBKEY` **não** é controle de acesso do Google para ADB em rede: é acoplamento obrigatório de credencial, com a
  chave privada do servidor indo para cada worker.
- "Docker Desktop não suporta KVM" é **postura de suporte** do Google (com falha de campo corroborando), não
  impossibilidade provada.
- ~~A crença de que o emulador só escuta 5554/5555 em `127.0.0.1` não foi confirmada.~~ **Resolvido por medição
  em 19/09/2026, e não por pesquisa:** `netstat -ano -p tcp` com três instâncias no ar mostrou
  `127.0.0.1:5554/5555`, `:5556/5557`, `:5558/5559` e o servidor adb em `127.0.0.1:5037` — **todos só em
  loopback**. Consequência direta: `adb connect ip-da-lan:5555` **não funciona**, nem na mesma rede. O túnel
  deixa de ser escolha de segurança e passa a ser requisito de funcionamento.

## Fontes principais

- Man page do adb (AOSP master) — topologias `connect`/`-H`/`-P`, `-s`, `-L` default loopback.
- `developer.android.com/tools/adb` e `.../studio/run/emulator-commandline`.
- `google/android-emulator-container-scripts` — porta ADB única por contêiner, *Linux Only*, `-skip-adb-auth`.
- DeviceFarmer/STF — provider/worker, heartbeat/reaper, faixa de portas inbound, aviso sobre lentidão de ADB remoto.
- Documentação do Selenium Grid 4 — registro iniciado pelo node, `stereotype`, `maxSessionCount`, relay.
