<#
.SYNOPSIS
  Mantém de pé o túnel SSH que traz os aparelhos de uma máquina worker para o servidor central.

.DESCRIPTION
  O emulador do Android escuta a porta de ADB SOMENTE em 127.0.0.1 (medido em 19/09/2026 nas duas máquinas),
  então não existe `adb connect ip-da-lan:5555`: o túnel não é escolha de segurança, é requisito de
  funcionamento. Cada aparelho remoto vira uma porta local aqui, e o `adb connect 127.0.0.1:<porta>` do
  servidor central passa a enxergá-lo como aparelho de rede comum.

  Reconecta sozinho: o `ssh` cai quando o Wi-Fi oscila, e o laço o levanta de novo. O `ServerAliveInterval`
  faz o cliente perceber queda em ~90 s em vez de ficar pendurado numa conexão morta.

  Registre como tarefa agendada (-Instalar) para sobreviver a logout e reinício. Processo iniciado dentro de
  uma sessão SSH ou de um console morre com ela — foi assim que o primeiro emulador do worker caiu.

  O acesso é de ENCAMINHAMENTO, não de shell: a conta usada no worker é uma conta de serviço sem privilégio
  (`farm-tunel`), e a chave é autorizada lá com `restrict,port-forwarding,permitopen=...,permitlisten=...`. Quem
  prepara o worker é `scripts/worker-ssh-restrito.ps1`, rodado NA MÁQUINA DO WORKER, uma vez. Ver docs/worker.md.

.EXAMPLE
  pwsh -File scripts\worker-tunnel.ps1 -Worker 192.168.1.19 -RegistrarChaveDeHost   # uma vez, conferindo a digital
  pwsh -File scripts\worker-tunnel.ps1 -Instalar
  pwsh -File scripts\worker-tunnel.ps1                # roda em primeiro plano, para diagnóstico
#>
[CmdletBinding()]
param(
  [string]$Worker   = '192.168.1.19',
  # Conta de SERVIÇO no worker, sem privilégio, criada por `scripts/worker-ssh-restrito.ps1` e autorizada com uma
  # chave restrita a encaminhamento (`restrict,port-forwarding,permitopen=...,permitlisten=...,command="exit"`).
  # Era `Administrator`: um arquivo de chave sem senha no central valia shell de administrador em CADA worker, e
  # num parque de N máquinas isso é movimento lateral central → todos os hosts. O túnel só precisa encaminhar
  # portas; não precisa de shell nenhum, muito menos com privilégio.
  [string]$Usuario  = 'farm-tunel',
  [string]$Chave    = 'C:\Users\Administrator\.ssh\worker_ed25519',
  # Uma entrada "portaLocal:portaRemota" por aparelho do worker; a remota é a de ADB (console + 1).
  # É TEXTO de propósito: `pwsh -File ... -Portas 1,2` NÃO vira array — chega como um texto só, e a tarefa
  # agendada subia encaminhando a porta "1555515557", que não existe (medido).
  [string]$Mapa = '15555:5555,15557:5557',
  # Mapa vindo de ARQUIVO, gerado pelo central (`data/tunnel/<worker>.map`, uma linha `local:remota,...`). Com
  # ele, acrescentar um aparelho deixa de exigir `-Instalar` de novo: o laco rele o arquivo a cada 5 s e, quando
  # o conteudo muda, derruba o `ssh` e sobe outro ja com as portas novas. Vazio, ou arquivo ausente/vazio, faz o
  # script cair no `-Mapa` de sempre - a tarefa agendada que ja existe continua funcionando sem ser tocada.
  [string]$MapaArquivo = '',
  # Imprime o mapa resolvido e sai. Serve para conferir o arquivo gerado sem abrir tunel nenhum.
  [switch]$MostrarMapa,
  # Encaminhamento REVERSO: abre uma porta NA MÁQUINA DO WORKER que chega até um serviço desta aqui. É como o
  # agente alcança o central sem que o central deixe de escutar só em loopback.
  #
  # A porta DAQUI tem de ser a do listener dedicado (`server.worker_port`, 8010), NUNCA a 8000. Por quê: toda
  # conexão que chega pelo `-R` tem par 127.0.0.1 de verdade, e loopback isenta de credencial. Apontando para a
  # 8000, qualquer processo/usuário local da máquina do worker alcançava a API INTEIRA do central sem token
  # (medido: /api/workers, /api/instagram/profiles e /api/commands respondiam 200) e chegava a POST
  # /api/admin/shutdown e PUT de credencial de perfil. Na 8010 só existe /api/worker/ws, que autentica na
  # primeira mensagem; o resto responde 404.
  # Formato "portaNoWorker:portaAqui". Vazio = sem encaminhamento reverso.
  [string]$MapaReverso = '18000:8010',
  [string]$LogDir   = 'C:\git\android\data\logs',
  # Registra a chave de HOST do worker no known_hosts e imprime a impressão digital para conferência fora de
  # banda. Existe porque `accept-new` aceita às cegas a primeira chave de cada worker novo (TOFU): quem estiver
  # no caminho da LAN no momento da primeira conexão vira o worker, para sempre, sem aviso.
  [switch]$RegistrarChaveDeHost,
  [switch]$Instalar,
  # OBSOLETO e agora é ERRO. Aceitava registrar a tarefa apontando para o pwsh do pacote da MICROSOFT STORE, cujo
  # caminho (`...\Microsoft.PowerShell_7.6.6.0_x64__8wekyb3d8bbwe\pwsh.exe`) carrega a versão do MSIX e some na
  # próxima atualização da Store: foi assim que a tarefa `farm-tunel-192.168.1.11` deixou o túnel (e o android-09)
  # fora no boot seguinte. O parâmetro segue declarado só para quem ainda o passa receber uma falha clara, em vez
  # de um "parâmetro não encontrado" sem explicação. Sem PowerShell 7 em `Program Files`, o instalador usa o
  # Windows PowerShell 5.1 do sistema, que é estável.
  [switch]$AceitarStore,
  # Imprime o que o `-Instalar` faria — executável escolhido, argumentos da tarefa, laços que derrubaria e
  # colisão de portas — sem tocar no Agendador nem matar processo nenhum. É o que torna o `-Instalar` testável.
  [switch]$Simular,
  # Túneis JÁ registrados a considerar na conferência de colisão de portas, no formato
  # `nome=15555:5555,15557:5557;outro=15559:5555`. Vazio = lê as tarefas `farm-tunel-*` do Agendador desta
  # máquina, que é o uso normal.
  [string]$MapaDeOutrosTuneis = ''
)
$ErrorActionPreference = 'Stop'
if ($AceitarStore) {
  throw ("-AceitarStore foi removido: registrar a tarefa no pwsh do pacote da Microsoft Store deixa o tunel fora " +
         "no boot seguinte a uma atualizacao da Store (o caminho carrega a versao do pacote). Rode -Instalar sem " +
         "o parametro: o instalador usa o PowerShell 7 do MSI ou, na falta dele, o Windows PowerShell 5.1 do sistema.")
}
$tarefa = "farm-tunel-$Worker"
$KnownHosts = Join-Path (Split-Path $Chave) 'known_hosts'

function Resolve-Mapa {
  <# O mapa que vale AGORA: o arquivo gerado pelo central quando existe e tem conteúdo, senão o `-Mapa` dos
     argumentos. Arquivo ausente, vazio ou ilegível NÃO derruba o túnel — cair para o mapa dos argumentos mantém
     de pé exatamente o que já funcionava antes de este parâmetro existir. #>
  if ($MapaArquivo) {
    try {
      if (Test-Path -LiteralPath $MapaArquivo) {
        $bruto = (Get-Content -Raw -LiteralPath $MapaArquivo -ErrorAction Stop).Trim()
        if ($bruto) { return $bruto }
      }
    } catch { }
  }
  return $Mapa
}

function ConvertTo-Pares($texto) {
  @($texto -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ } | ForEach-Object {
    $p = $_ -split ':'
    if ($p.Count -ne 2) { throw "entrada inválida no mapa: '$_' (esperado portaLocal:portaRemota)" }
    [pscustomobject]@{ Local = [int]$p[0]; Remota = [int]$p[1] }
  })
}


function Test-ChaveDeHostConhecida {
  <# Verdadeiro quando o known_hosts já tem a chave do worker. `ssh-keygen -F` é o caminho certo: ele entende
     entrada com hash (HashKnownHosts) e `[host]:porta`, que uma busca por texto não entende. Sem ssh-keygen,
     cai para a busca textual em vez de travar o túnel. #>
  if (-not (Test-Path -LiteralPath $KnownHosts)) { return $false }
  $keygen = (Get-Command ssh-keygen -ErrorAction SilentlyContinue)
  if ($keygen) {
    & $keygen.Source -F $Worker -f $KnownHosts *> $null
    return ($LASTEXITCODE -eq 0)
  }
  return [bool](Select-String -LiteralPath $KnownHosts -SimpleMatch $Worker -Quiet)
}

if ($RegistrarChaveDeHost) {
  New-Item -ItemType Directory -Force (Split-Path $Chave) | Out-Null
  $linhas = @(& ssh-keyscan -T 10 $Worker 2>$null | Where-Object { $_ -and -not $_.StartsWith('#') })
  if (-not $linhas) { throw "ssh-keyscan não obteve chave de host de $Worker (o sshd está no ar? a rede alcança?)" }
  $antes = @()
  if (Test-Path -LiteralPath $KnownHosts) { $antes = @(Get-Content -LiteralPath $KnownHosts) }
  $novas = @($linhas | Where-Object { $antes -notcontains $_ })
  if ($novas) { Add-Content -LiteralPath $KnownHosts -Value $novas }
  Write-Host "known_hosts: $($novas.Count) linha(s) nova(s) em $KnownHosts"
  Write-Host 'CONFIRME estas impressões digitais NO PRÓPRIO WORKER, no console dele, antes de confiar no túnel:'
  Write-Host '  Get-ChildItem C:\ProgramData\ssh\ssh_host_*_key.pub | ForEach-Object { ssh-keygen -lf $_.FullName }'
  $tmp = New-TemporaryFile
  try {
    Set-Content -LiteralPath $tmp -Value $linhas
    & ssh-keygen -lf $tmp | ForEach-Object { Write-Host "  $_" }
  } finally { Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue }
  return
}

function Resolve-Interpretador {
  <# O executável que a TAREFA vai guardar. Tem de ser um caminho que não muda: a ação registrada guarda TEXTO,
     e o Agendador não procura o programa de novo.

     Achado #138, medido neste central: `(Get-Command pwsh).Source` devolvia
     `C:\Program Files\WindowsApps\Microsoft.PowerShell_7.6.6.0_x64__8wekyb3d8bbwe\pwsh.exe`. Esse caminho carrega
     a VERSÃO do pacote MSIX e some na próxima atualização da Microsoft Store. No boot seguinte a ação aponta
     para um executável inexistente, o túnel não sobe, os seis aparelhos remotos E o canal reverso do agente caem
     juntos, e o painel só mostra "worker offline". `RestartCount` não socorre: não há processo a reiniciar.

     Reincidência (A8, 02/10/2026): a tarefa `farm-tunel-192.168.1.11` foi registrada com `-AceitarStore` justamente
     nesse caminho. Por isso NENHUM caminho dentro de `\WindowsApps\` é aceito mais, com ou sem flag: a escolha é,
     em ordem, (1) o MSI estável `C:\Program Files\PowerShell\7\pwsh.exe`; (2) qualquer outro pwsh que não seja do
     WindowsApps; (3) o Windows PowerShell 5.1 do sistema, que mora em `System32` e não muda com atualização
     nenhuma. O 5.1 serve porque este script não usa nada exclusivo do 7 (conferido: `??`, `&&`, `?.`,
     `-AsHashtable`, `Join-String` etc. não aparecem, e o arquivo carrega BOM para o 5.1 ler os acentos como UTF-8).
     Sem nenhum dos três, falha com a instrução de instalar o MSI.

     Os parâmetros existem para o teste injetar caminhos falsos; no uso normal os padrões valem. Devolve
     `@{ Caminho; Tipo }` com Tipo = pwsh7 | pwsh | powershell51. #>
  param(
    [string]$Estavel = (Join-Path ${env:ProgramFiles} 'PowerShell\7\pwsh.exe'),
    [string[]]$OutrosPwsh = @((Get-Command pwsh -All -ErrorAction SilentlyContinue).Source),
    [string]$WindowsPowerShell = (Join-Path ${env:SystemRoot} 'System32\WindowsPowerShell\v1.0\powershell.exe')
  )
  $serve = { param($c) $c -and ($c -notmatch '\\WindowsApps\\') -and (Test-Path -LiteralPath $c -PathType Leaf) }
  if (& $serve $Estavel) { return [pscustomobject]@{ Caminho = $Estavel; Tipo = 'pwsh7' } }
  foreach ($c in @($OutrosPwsh)) {
    if (& $serve $c) { return [pscustomobject]@{ Caminho = $c; Tipo = 'pwsh' } }
  }
  if (& $serve $WindowsPowerShell) { return [pscustomobject]@{ Caminho = $WindowsPowerShell; Tipo = 'powershell51' } }
  $store = @($OutrosPwsh | Where-Object { $_ }) -join ', '
  throw ("nenhum interpretador estavel para a tarefa agendada: nao ha PowerShell 7 em " +
         "'$Estavel', nao ha outro pwsh fora do WindowsApps (achados: '$store'; o pacote da Microsoft Store " +
         "e recusado porque o caminho dele carrega a versao e some na proxima atualizacao) e o Windows PowerShell " +
         "5.1 nao esta em '$WindowsPowerShell'. Instale o PowerShell 7 por MSI: " +
         "winget install --id Microsoft.PowerShell --source winget")
}

function Get-OutrosTuneis {
  <# Os túneis JÁ registrados nesta máquina, exceto o deste worker: `@{ Nome; Portas }`.

     Existe por causa do aceite de dois workers. As portas locais vêm do `-Mapa` digitado à mão, sem alocação
     central; duas máquinas com o mapa padrão pedem as MESMAS 15555/15557, `ExitOnForwardFailure=yes` faz o
     segundo túnel morrer na largada, e o erro aparece como "worker offline" e não como "porta ocupada".

     `-MapaDeOutrosTuneis` substitui a leitura do Agendador (formato `nome=15555:5555,15557:5557;outro=...`):
     é como o teste exercita a colisão sem registrar tarefa nenhuma nesta máquina. #>
  $saida = @()
  if ($MapaDeOutrosTuneis) {
    foreach ($item in ($MapaDeOutrosTuneis -split ';' | Where-Object { $_.Trim() })) {
      $nome, $mapa = $item -split '=', 2
      if (-not $mapa) { continue }
      $saida += [pscustomobject]@{ Nome = $nome.Trim(); Portas = @((ConvertTo-Pares $mapa).Local) }
    }
    return $saida
  }
  foreach ($t in @(Get-ScheduledTask -TaskName 'farm-tunel-*' -ErrorAction SilentlyContinue)) {
    if ($t.TaskName -eq $tarefa) { continue }        # o daqui vai ser substituído; ele não colide consigo mesmo
    foreach ($a in @($t.Actions)) {
      if ($a.Arguments -match '-Mapa\s+"([^"]*)"') {
        $saida += [pscustomobject]@{ Nome = $t.TaskName; Portas = @((ConvertTo-Pares $Matches[1]).Local) }
      }
    }
  }
  return $saida
}

if ($Instalar) {
  $eu = ([Security.Principal.WindowsIdentity]::GetCurrent()).Name
  $script = $MyInvocation.MyCommand.Path
  $argumentos = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`" " +
                "-Worker $Worker -Usuario $Usuario -Chave `"$Chave`" -Mapa `"$Mapa`" " +
                "-MapaArquivo `"$MapaArquivo`" " +
                "-MapaReverso `"$MapaReverso`" -LogDir `"$LogDir`""
  # Prefere o pwsh 7 (MSI), mas este script roda também no Windows PowerShell 5.1 (conferido: sem recurso só do 7;
  # o 5.1 que morria com LastTaskResult=1 era OUTRO script, de cmdlets só do 7). Nunca o pacote da Store.
  $inter = Resolve-Interpretador
  $exe = $inter.Caminho
  if ($exe -match '\\WindowsApps\\') {
    throw "recusado: o executavel '$exe' esta em WindowsApps e some quando a Microsoft Store atualiza o pacote"
  }

  # Colisao de porta local com OUTRO tunel: o `ssh` novo morreria na largada por ExitOnForwardFailure.
  $minhas = @((ConvertTo-Pares (Resolve-Mapa)).Local)
  $colisoes = @()
  foreach ($outro in (Get-OutrosTuneis)) {
    $comuns = @($minhas | Where-Object { $outro.Portas -contains $_ })
    if ($comuns) { $colisoes += "$($outro.Nome) ja usa a(s) porta(s) local(is) $($comuns -join ', ')" }
  }
  if ($colisoes) {
    throw ("colisao de portas locais com tunel ja registrado: " + ($colisoes -join ' | ') +
           ". Escolha outras portas em -Mapa (ou gere o mapa pelo central, em data\tunnel\<worker>.map).")
  }

  # Desregistrar NÃO mata o laço que já roda nem o `ssh` filho dele: reinstalar deixava DOIS túneis disputando
  # as mesmas portas locais (medido). Derruba o antigo primeiro, e só então registra.
  #
  # Achado #181: o laço era filtrado só por `worker-tunnel.ps1`, sem o worker. Instalar o túnel do SEGUNDO
  # servidor matava o laço de reconexão do PRIMEIRO — o `ssh` dele ficava vivo até a próxima oscilação de rede e
  # então ninguém o levantava. E o filtro é ANCORADO (`-Worker <valor>` seguido de espaço ou fim): com `-match`
  # de texto solto, instalar o túnel de `192.168.1.1` derrubaria também o de `192.168.1.19`.
  $meu = "-Worker\s+$([regex]::Escape($Worker))(\s|$)"
  $meuSsh = "@$([regex]::Escape($Worker))(\s|$)"
  $alvos = @()
  foreach ($p in @(Get-CimInstance Win32_Process -Filter "Name='ssh.exe'" -ErrorAction SilentlyContinue)) {
    if ($p.CommandLine -and $p.CommandLine -match $meuSsh -and $p.CommandLine -match '\s-N\s') {
      $alvos += [pscustomobject]@{ Tipo = 'tunel'; Pid = $p.ProcessId }
    }
  }
  foreach ($p in @(Get-CimInstance Win32_Process -Filter "Name='pwsh.exe' OR Name='powershell.exe'" -ErrorAction SilentlyContinue)) {
    if ($p.CommandLine -and $p.CommandLine -match 'worker-tunnel\.ps1' -and $p.CommandLine -match $meu -and
        $p.ProcessId -ne $PID) {
      $alvos += [pscustomobject]@{ Tipo = 'laco'; Pid = $p.ProcessId }
    }
  }

  if ($Simular) {
    Write-Output "tarefa: $tarefa"
    Write-Output "executavel: $exe"
    Write-Output "interpretador: $($inter.Tipo)"
    Write-Output "argumentos: $argumentos"
    Write-Output "portas locais: $($minhas -join ', ')"
    Write-Output 'colisao: nenhuma'
    foreach ($a in $alvos) { Write-Output "encerraria $($a.Tipo) (pid $($a.Pid))" }
    Write-Output 'simulacao: nada foi registrado nem encerrado'
    return
  }

  Stop-ScheduledTask -TaskName $tarefa -ErrorAction SilentlyContinue
  Unregister-ScheduledTask -TaskName $tarefa -Confirm:$false -ErrorAction SilentlyContinue
  foreach ($a in $alvos) {
    Write-Host "encerrando $($a.Tipo) anterior (pid $($a.Pid))"
    Stop-Process -Id $a.Pid -Force -ErrorAction SilentlyContinue
  }
  Start-Sleep -Seconds 2
  $acao = New-ScheduledTaskAction -Execute $exe -Argument $argumentos
  $gatilho = New-ScheduledTaskTrigger -AtStartup
  $principal = New-ScheduledTaskPrincipal -UserId $eu -LogonType S4U -RunLevel Highest
  $ajustes = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
               -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew `
               -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
  Register-ScheduledTask -TaskName $tarefa -Action $acao -Trigger $gatilho -Principal $principal -Settings $ajustes | Out-Null
  Start-ScheduledTask -TaskName $tarefa
  Write-Host "tarefa '$tarefa' registrada (sobe no boot) e iniciada."
  return
}

$mapaAtual = Resolve-Mapa
if ($MostrarMapa) { Write-Output $mapaAtual; return }
$pares = ConvertTo-Pares $mapaAtual
if (-not $pares) { throw 'mapa vazio: nem -MapaArquivo nem -Mapa trouxeram portas' }
New-Item -ItemType Directory -Force $LogDir | Out-Null
$log = Join-Path $LogDir "tunel-$Worker.log"
function Registra($m) { "$([DateTime]::Now.ToString('yyyy-MM-dd HH:mm:ss')) $m" | Tee-Object -FilePath $log -Append | Write-Host }

$reversos = @($MapaReverso -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ } | ForEach-Object {
  $p = $_ -split ':'
  if ($p.Count -ne 2) { throw "entrada inválida no -MapaReverso: '$_' (esperado portaNoWorker:portaAqui)" }
  [pscustomobject]@{ NoWorker = [int]$p[0]; Aqui = [int]$p[1] }
})
function Get-Encaminhamentos($pares) {
  $lista = @()
  foreach ($p in $pares) { $lista += @('-L', "$($p.Local):127.0.0.1:$($p.Remota)") }
  foreach ($r in $reversos) { $lista += @('-R', "$($r.NoWorker):127.0.0.1:$($r.Aqui)") }
  return $lista
}
$base = @('-i', $Chave, '-N',
          '-o', 'BatchMode=yes',
          '-o', 'ExitOnForwardFailure=yes',   # porta ocupada é falha, não túnel meio pronto
          '-o', 'ServerAliveInterval=30',
          '-o', 'ServerAliveCountMax=3',
          # `yes`, não `accept-new`: a chave do worker é registrada uma vez, por gente, com a impressão digital
          # conferida no console dele (`-RegistrarChaveDeHost`). Com `accept-new`, a PRIMEIRA conexão a um worker
          # novo aceita qualquer chave que responda naquele IP — e é nessa primeira conexão que a credencial
          # permanente do agente passa pelo `-R`.
          '-o', 'StrictHostKeyChecking=yes',
          '-o', "UserKnownHostsFile=$KnownHosts")

if (-not (Test-ChaveDeHostConhecida)) {
  throw ("a chave de host de $Worker não está em $KnownHosts, e o túnel não aceita chave desconhecida. " +
         "Rode uma vez, conferindo a impressão digital no console do worker: " +
         "pwsh -File scripts\worker-tunnel.ps1 -Worker $Worker -Chave `"$Chave`" -RegistrarChaveDeHost")
}

Registra ("iniciando" + $(if ($MapaArquivo) { " (mapa de $MapaArquivo)" } else { "" }) + "; " +
          (($pares | ForEach-Object { "$($_.Local)->$Worker`:$($_.Remota)" }) -join ' ') +
          $(if ($reversos) { " | reverso: " + (($reversos | ForEach-Object { "$Worker`:$($_.NoWorker)->$($_.Aqui)" }) -join ' ') } else { "" }))
$seguidas = 0
while ($true) {
  # Relê o mapa a cada volta: aparelho adotado no painel entra no túnel sem ninguém reinstalar a tarefa.
  $mapaAtual = Resolve-Mapa
  $pares = ConvertTo-Pares $mapaAtual
  if (-not $pares) { Registra 'mapa vazio; nova tentativa em 30s'; Start-Sleep -Seconds 30; continue }
  $encaminhamentos = Get-Encaminhamentos $pares
  $t0 = Get-Date
  $saida = Join-Path $LogDir "tunel-$Worker.ssh.log"
  # `Start-Process` em vez de `&`: com o `ssh` num processo próprio dá para VIGIAR o arquivo de mapa enquanto o
  # túnel está de pé. Sem isso, a única forma de trocar as portas era matar a tarefa agendada e reinstalá-la.
  $proc = Start-Process -FilePath ssh -ArgumentList (@($base) + $encaminhamentos + "$Usuario@$Worker") `
                        -NoNewWindow -PassThru -RedirectStandardError $saida
  while (-not $proc.HasExited) {
    Start-Sleep -Seconds 5
    if ((Resolve-Mapa) -ne $mapaAtual) {
      Registra 'o mapa do túnel mudou; derrubando o ssh para subir com as portas novas'
      Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
      break
    }
  }
  $proc.WaitForExit()
  if (Test-Path -LiteralPath $saida) {
    foreach ($linha in @(Get-Content -LiteralPath $saida -ErrorAction SilentlyContinue)) {
      if ($linha) { Registra "ssh: $linha" }
    }
    Remove-Item -LiteralPath $saida -Force -ErrorAction SilentlyContinue
  }
  $viveu = ((Get-Date) - $t0).TotalSeconds
  # queda em menos de 10 s é falha real (chave, porta ocupada, host fora): espera mais para não martelar
  if ($viveu -lt 10) { $seguidas++ } else { $seguidas = 0 }
  $espera = [Math]::Min(60, 5 * [Math]::Max(1, $seguidas))
  Registra "ssh saiu depois de $([Math]::Round($viveu))s (falhas rapidas seguidas=$seguidas); nova tentativa em ${espera}s"
  Start-Sleep -Seconds $espera
}
