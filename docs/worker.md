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
do próprio túnel que ele já mantém. **Zero porta nova em qualquer lugar** — é o caminho de menor exposição, e o
único que funciona quando o worker está atrás de NAT que você não controla.

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
- Token de inscrição: uso único, 1 hora, só o hash guardado. Credencial permanente: só o hash no central, e um
  arquivo de permissão restrita no worker.
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
| Perdi a credencial do worker | Apague `worker-credential.json`, remova o worker no painel e inscreva de novo |
| Token venceu ou já foi usado | Gere outro; ele é de uso único de propósito |
| O agente diz `protocol_too_new` | O agente é mais novo que o servidor: atualize o **central** |
| Worker aparece offline mas está ligado | Veja `last_seen_at` na Infraestrutura; sem batida há >30 s, o problema é rede ou o processo do agente |
| Manutenção | Painel → Infraestrutura → manutenção. Suspende **novas** atribuições e não derruba o que já está em voo |
