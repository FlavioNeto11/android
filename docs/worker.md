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
| Appium | ✅ por padrão | opcional (`appium: local`) |
| Credencial de conta (senha de perfil) | ✅ **só aqui** | ❌ nunca |

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

### 6. No central — amarrar as instâncias ao worker

Cada aparelho do worker serve uma instância do parque. No `config/config.yaml`, a instância continua sendo
`external` (o ADB chega por túnel), e o vínculo com a máquina fica em `instances.worker_id`, na tabela.

## Como o canal se comporta

- **O worker liga para o central**, nunca o contrário: atravessa NAT sem abrir porta na casa de ninguém.
- **Ausência de batida** é o que marca o worker indisponível, não o socket fechado — socket cai por rede
  piscando, e isso não significa que o worker parou de trabalhar. Batida a cada 10 s, ausente após 3 perdidas.
- **Reconexão automática** com espera crescente até 60 s. Recusa explicada (credencial errada, não inscrito) faz
  o agente **parar** em vez de martelar: insistir não resolveria.
- **Cerca (fencing):** todo despacho leva um número monotônico e o agente o devolve no resultado. Resultado com
  cerca velha é recusado — um worker que voltou do limbo não sobrescreve o presente.
- **Queda no meio de um comando** deixa o comando `uncertain`, nunca falho: o agente pode ter agido.

## Como o worker alcança o central

Duas formas, e a diferença é quanta superfície nova cada uma abre.

**a) Túnel SSH reverso (padrão).** O central segue atendendo só em `127.0.0.1`, e o worker chega nele por um `-R`
do próprio túnel que ele já mantém — o único caminho que funciona quando o worker está atrás de NAT que você não
controla.

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
