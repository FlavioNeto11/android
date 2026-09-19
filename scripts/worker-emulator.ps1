# Sobe o emulador do worker por TAREFA AGENDADA.
#
# Por que: um processo iniciado dentro de uma sessao SSH pertence ao job da sessao e o Windows o mata quando a
# sessao fecha - medido: o emulador morreu em segundos, ja com o WHPX operacional. A tarefa agendada roda sob o
# servico Agendador, fora do job da sessao, e sobrevive.
[CmdletBinding()]
param(
  [string]$Name     = 'worker-01',
  [int]$ConsolePort = 5554,
  [string]$SdkRoot  = 'C:\Android\Sdk',
  [string]$AvdHome  = 'C:\farm\avd',
  # Guarda de capacidade DESTA máquina. O servidor central não protege o worker: `slots_used()` exclui
  # aparelho externo de propósito, então quem impede a máquina de afundar é este script, aqui.
  [int]$RamPorAparelhoMb = 1800,   # medido no worker: ~650 MB de working set + páginas do QEMU; 1800 dá folga
  [int]$RamLivreMinimaMb = 4096,   # o que tem de sobrar para o SO depois de subir este aparelho
  [int]$NucleosPorAparelho = 2,    # igual a android.cores do config central
  [switch]$Forcar                  # ignora a guarda (use sabendo o que faz)
)
$ErrorActionPreference = 'Stop'
$tarefa = "farm-emulador-$Name"
$launcher = "C:\farm\run-emulator-$Name.ps1"

# ---------------------------------------------------------------- guarda de capacidade
# O nome do processo é 'qemu-system-x86_64-headless' quando o emulador sobe com -no-window; procurar por
# 'qemu-system-x86_64' exato não acha nada e a contagem daria zero em silêncio (foi o que aconteceu).
$rodando = @(Get-Process -Name 'qemu-system-x86_64*' -ErrorAction SilentlyContinue)
$ja = @($rodando | Where-Object { $_.Id -ne $PID }).Count
$os = Get-CimInstance Win32_OperatingSystem
$livreMb = [int]($os.FreePhysicalMemory / 1KB)
$cpus = (Get-CimInstance Win32_ComputerSystem).NumberOfLogicalProcessors
$sobraria = $livreMb - $RamPorAparelhoMb
$nucleosDepois = ($ja + 1) * $NucleosPorAparelho

Write-Host ("[capacidade] {0} aparelho(s) no ar | RAM livre {1} MB | {2} CPUs | depois deste: {3} MB livres, {4} núcleos pedidos" -f `
            $ja, $livreMb, $cpus, $sobraria, $nucleosDepois)

if ($sobraria -lt $RamLivreMinimaMb) {
  $msg = "RAM insuficiente: sobrariam $sobraria MB e o mínimo é $RamLivreMinimaMb MB. Use -Forcar para ignorar."
  if ($Forcar) { Write-Warning $msg } else { throw $msg }
}
if ($nucleosDepois -gt $cpus) {
  # CPU sobressubscrita degrada TODOS os aparelhos ao mesmo tempo, inclusive os que já estavam bem. Avisa e segue:
  # emulador ocioso quase não consome, então o limite real é empírico, não aritmético.
  Write-Warning ("CPU sobressubscrita: {0} núcleos pedidos para {1} CPUs. Observe a latência antes de subir mais." -f $nucleosDepois, $cpus)
}

# 1. lancador: a tarefa nao carrega variaveis de ambiente, entao elas sao definidas aqui
@"
`$env:ANDROID_HOME     = '$SdkRoot'
`$env:ANDROID_SDK_ROOT = '$SdkRoot'
`$env:ANDROID_AVD_HOME = '$AvdHome'
& '$SdkRoot\emulator\emulator.exe' -avd '$Name' -port $ConsolePort -no-window -no-audio -no-boot-anim ``
  -no-snapshot-load -no-snapshot-save -gpu swiftshader_indirect -accel on -no-metrics -lowram ``
  *> 'C:\farm\emulator-$Name.log'
"@ | Set-Content -Path $launcher -Encoding ascii

# 2. registra (idempotente) e dispara
Unregister-ScheduledTask -TaskName $tarefa -Confirm:$false -ErrorAction SilentlyContinue
$acao = New-ScheduledTaskAction -Execute 'powershell.exe' `
          -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$launcher`""
# numa sessao SSH $env:USERDOMAIN pode vir vazio; o token real nunca mente
$eu = ([Security.Principal.WindowsIdentity]::GetCurrent()).Name
Write-Host "identidade da tarefa: $eu"
$principal = New-ScheduledTaskPrincipal -UserId $eu -LogonType S4U -RunLevel Highest
$ajustes = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
             -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $tarefa -Action $acao -Principal $principal -Settings $ajustes | Out-Null
Start-ScheduledTask -TaskName $tarefa

Write-Host "[$(Get-Date -Format HH:mm:ss)] tarefa '$tarefa' disparada; aguardando as portas $ConsolePort/$($ConsolePort+1)"
$limite = (Get-Date).AddSeconds(90)
while ((Get-Date) -lt $limite) {
  $l = netstat -ano -p tcp | Select-String 'LISTENING' | Select-String ":$($ConsolePort+1)\s"
  if ($l) { Write-Host "[$(Get-Date -Format HH:mm:ss)] no ar:"; ($l | Out-String).Trim(); break }
  Start-Sleep -Seconds 3
}
if (-not $l) { Write-Host "NAO subiu em 90s; ultimas linhas do log:"; Get-Content "C:\farm\emulator-$Name.log" -Tail 15 }
(Get-ScheduledTask -TaskName $tarefa | Get-ScheduledTaskInfo | Select-Object LastTaskResult,NumberOfMissedRuns | Out-String).Trim()
