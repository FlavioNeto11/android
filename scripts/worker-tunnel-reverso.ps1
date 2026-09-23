<#
.SYNOPSIS
  Túnel iniciado PELO WORKER: é o que permite um worker em outra rede (4G, hotspot, NAT alheio).

.DESCRIPTION
  O `scripts/worker-tunnel.ps1` roda no SERVIDOR CENTRAL e abre `ssh` PARA o worker: precisa de sshd na máquina
  do worker e de rota do central até lá. Isso funciona na LAN e não funciona atrás de um NAT que não se
  controla — que é justamente a restrição de origem do pedido ("redes que não controlamos").

  Este script é o outro lado, e roda NA MÁQUINA DO WORKER:

    -R portaNoCentral:127.0.0.1:portaDeAdbAqui   os aparelhos DAQUI aparecem no central como 127.0.0.1:<porta>
    -L portaAqui:127.0.0.1:portaDoCentral        o agente DAQUI alcança o `/api/worker/ws` do central

  Quem inicia a conexão é esta máquina, então nenhuma porta precisa ser alcançável de fora aqui. O que o central
  precisa é de um sshd com uma conta RESTRITA a encaminhamento de porta — nada de shell:

    # no central, em authorized_keys da conta do túnel:
    restrict,port-forwarding,permitopen="127.0.0.1:8010" ssh-ed25519 AAAA... worker-01

  `GatewayPorts` continua `no` no central (o padrão): as portas abertas pelo `-R` escutam só no loopback dele,
  que é exatamente como o ADB do central já fala com os aparelhos remotos hoje.

  O mapa pode vir de arquivo (`-MapaArquivo`), gerado pelo central em `data/tunnel/<worker>.map` e copiado para
  cá: adotar um aparelho novo no painel muda o arquivo, e o laço reinicia o `ssh` com as portas novas sem
  ninguém reinstalar tarefa nenhuma.

.EXAMPLE
  pwsh -File scripts\worker-tunnel-reverso.ps1 -Central central.exemplo -Mapa '15555:5555,15557:5557'
  pwsh -File scripts\worker-tunnel-reverso.ps1 -Central central.exemplo -MapaArquivo C:\farm\worker-01.map -Instalar
  pwsh -File scripts\worker-tunnel-reverso.ps1 -MostrarMapa -MapaArquivo C:\farm\worker-01.map
#>
[CmdletBinding()]
param(
  # Host do servidor central (nome ou IP alcançável a partir DESTA máquina).
  [string]$Central  = 'central.local',
  [string]$Usuario  = 'farm-tunnel',
  [string]$Chave    = "$env:USERPROFILE\.ssh\central_ed25519",
  # "portaNoCentral:portaDeAdbAqui" por aparelho. A porta de ADB local é a do console + 1 (5554 → 5555).
  # É TEXTO de propósito: `-Mapa 1,2` viraria um texto só na tarefa agendada (medido no lado do central).
  [string]$Mapa = '15555:5555',
  # Mapa vindo de arquivo, no mesmo formato. Relido a cada 5 s; mudou, o túnel sobe de novo com as portas novas.
  [string]$MapaArquivo = '',
  # "portaAqui:portaNoCentral". É como o agente desta máquina alcança o listener dedicado do central (8010).
  # NUNCA aponte para a 8000: conexão que chega por `-R`/`-L` tem par 127.0.0.1, e loopback isenta de credencial
  # no central — apontar para a API inteira daria acesso sem token a quem alcançasse esta porta aqui.
  [string]$MapaApi = '18000:8010',
  [string]$LogDir   = "$env:ProgramData\farm",
  [switch]$MostrarMapa,
  [switch]$Instalar
)
$ErrorActionPreference = 'Stop'
$tarefa = "farm-tunel-reverso-$Central"

function Resolve-Mapa {
  <# Arquivo quando existe e tem conteúdo; senão o `-Mapa`. Arquivo ilegível não derruba o túnel. #>
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

function ConvertTo-Pares($texto, $rotulo) {
  @($texto -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ } | ForEach-Object {
    $p = $_ -split ':'
    if ($p.Count -ne 2) { throw "entrada inválida em $rotulo : '$_' (esperado a:b)" }
    [pscustomobject]@{ A = [int]$p[0]; B = [int]$p[1] }
  })
}

$mapaAtual = Resolve-Mapa
if ($MostrarMapa) { Write-Output $mapaAtual; return }

if ($Instalar) {
  $eu = ([Security.Principal.WindowsIdentity]::GetCurrent()).Name
  $script = $MyInvocation.MyCommand.Path
  $argumentos = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`" " +
                "-Central $Central -Usuario $Usuario -Chave `"$Chave`" -Mapa `"$Mapa`" " +
                "-MapaArquivo `"$MapaArquivo`" -MapaApi `"$MapaApi`" -LogDir `"$LogDir`""
  $exe = (Get-Command pwsh -ErrorAction SilentlyContinue).Source
  if (-not $exe) { $exe = 'powershell.exe' }
  # Derruba o que já roda ANTES de registrar: dois laços disputando as mesmas portas locais foi o defeito
  # medido do lado do central, e aqui seria igual.
  Stop-ScheduledTask -TaskName $tarefa -ErrorAction SilentlyContinue
  Unregister-ScheduledTask -TaskName $tarefa -Confirm:$false -ErrorAction SilentlyContinue
  foreach ($p in @(Get-CimInstance Win32_Process -Filter "Name='ssh.exe'" -ErrorAction SilentlyContinue)) {
    if ($p.CommandLine -and $p.CommandLine -match [regex]::Escape($Central) -and $p.CommandLine -match '\s-N\s') {
      Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
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

New-Item -ItemType Directory -Force $LogDir | Out-Null
$log = Join-Path $LogDir "tunel-reverso-$Central.log"
function Registra($m) { "$([DateTime]::Now.ToString('yyyy-MM-dd HH:mm:ss')) $m" | Tee-Object -FilePath $log -Append | Write-Host }

$api = ConvertTo-Pares $MapaApi '-MapaApi'
$base = @('-i', $Chave, '-N',
          '-o', 'BatchMode=yes',
          '-o', 'ExitOnForwardFailure=yes',   # porta ocupada no central é falha, não túnel meio pronto
          '-o', 'ServerAliveInterval=30',
          '-o', 'ServerAliveCountMax=3',
          '-o', 'StrictHostKeyChecking=accept-new',
          '-o', "UserKnownHostsFile=$(Split-Path $Chave)\known_hosts")

$seguidas = 0
while ($true) {
  $mapaAtual = Resolve-Mapa
  $pares = ConvertTo-Pares $mapaAtual '-Mapa'
  if (-not $pares) { Registra 'mapa vazio; nova tentativa em 30s'; Start-Sleep -Seconds 30; continue }
  $encaminhamentos = @()
  # -R: a porta abre NO CENTRAL e chega no ADB DAQUI. É o que atravessa NAT: quem conecta somos nós.
  foreach ($p in $pares) { $encaminhamentos += @('-R', "$($p.A):127.0.0.1:$($p.B)") }
  # -L: a porta abre AQUI e chega no listener do agente no central.
  foreach ($a in $api) { $encaminhamentos += @('-L', "$($a.A):127.0.0.1:$($a.B)") }
  Registra ("iniciando; " + (($pares | ForEach-Object { "central:$($_.A)->adb local $($_.B)" }) -join ' ') +
            " | api: " + (($api | ForEach-Object { "local $($_.A)->central:$($_.B)" }) -join ' '))
  $t0 = Get-Date
  $saida = Join-Path $LogDir "tunel-reverso-$Central.ssh.log"
  $proc = Start-Process -FilePath ssh -ArgumentList (@($base) + $encaminhamentos + "$Usuario@$Central") `
                        -NoNewWindow -PassThru -RedirectStandardError $saida
  while (-not $proc.HasExited) {
    Start-Sleep -Seconds 5
    if ((Resolve-Mapa) -ne $mapaAtual) {
      Registra 'o mapa mudou; derrubando o ssh para subir com as portas novas'
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
  if ($viveu -lt 10) { $seguidas++ } else { $seguidas = 0 }
  $espera = [Math]::Min(60, 5 * [Math]::Max(1, $seguidas))
  Registra "ssh saiu depois de $([Math]::Round($viveu))s (falhas rapidas seguidas=$seguidas); nova tentativa em ${espera}s"
  Start-Sleep -Seconds $espera
}
