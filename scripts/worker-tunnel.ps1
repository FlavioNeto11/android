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

.EXAMPLE
  pwsh -File scripts\worker-tunnel.ps1 -Instalar
  pwsh -File scripts\worker-tunnel.ps1                # roda em primeiro plano, para diagnóstico
#>
[CmdletBinding()]
param(
  [string]$Worker   = '192.168.1.19',
  [string]$Usuario  = 'Administrator',
  [string]$Chave    = 'C:\Users\Administrator\.ssh\worker_ed25519',
  # Uma entrada "portaLocal:portaRemota" por aparelho do worker; a remota é a de ADB (console + 1).
  # É TEXTO de propósito: `pwsh -File ... -Portas 1,2` NÃO vira array — chega como um texto só, e a tarefa
  # agendada subia encaminhando a porta "1555515557", que não existe (medido).
  [string]$Mapa = '15555:5555,15557:5557',
  # Encaminhamento REVERSO: abre uma porta NA MÁQUINA DO WORKER que chega até um serviço desta aqui. É como o
  # agente alcança a API do central sem que o central deixe de escutar só em loopback — a restrição de
  # `main.py` continua valendo, e nenhuma porta do servidor vai para a rede.
  # Formato "portaNoWorker:portaAqui". Vazio = sem encaminhamento reverso.
  [string]$MapaReverso = '',
  [string]$LogDir   = 'C:\git\android\data\logs',
  [switch]$Instalar
)
$ErrorActionPreference = 'Stop'
$tarefa = "farm-tunel-$Worker"

if ($Instalar) {
  $eu = ([Security.Principal.WindowsIdentity]::GetCurrent()).Name
  $script = $MyInvocation.MyCommand.Path
  $argumentos = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`" " +
                "-Worker $Worker -Usuario $Usuario -Chave `"$Chave`" -Mapa `"$Mapa`" " +
                "-MapaReverso `"$MapaReverso`" -LogDir `"$LogDir`""
  # pwsh, não powershell.exe: a tarefa rodava no 5.1 e morria com LastTaskResult=1 em cmdlets só do 7 (medido)
  $exe = (Get-Command pwsh -ErrorAction SilentlyContinue).Source
  if (-not $exe) { $exe = 'powershell.exe' }

  # Desregistrar NÃO mata o laço que já roda nem o `ssh` filho dele: reinstalar deixava DOIS túneis disputando
  # as mesmas portas locais (medido). Derruba o antigo primeiro, e só então registra.
  Stop-ScheduledTask -TaskName $tarefa -ErrorAction SilentlyContinue
  Unregister-ScheduledTask -TaskName $tarefa -Confirm:$false -ErrorAction SilentlyContinue
  foreach ($p in @(Get-CimInstance Win32_Process -Filter "Name='ssh.exe'" -ErrorAction SilentlyContinue)) {
    if ($p.CommandLine -and $p.CommandLine -match [regex]::Escape($Worker) -and $p.CommandLine -match '\s-N\s') {
      Write-Host "encerrando túnel anterior (pid $($p.ProcessId))"
      Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
  }
  foreach ($p in @(Get-CimInstance Win32_Process -Filter "Name='pwsh.exe' OR Name='powershell.exe'" -ErrorAction SilentlyContinue)) {
    if ($p.CommandLine -and $p.CommandLine -match 'worker-tunnel\.ps1' -and $p.ProcessId -ne $PID) {
      Write-Host "encerrando laço anterior (pid $($p.ProcessId))"
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

$pares = @($Mapa -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ } | ForEach-Object {
  $p = $_ -split ':'
  if ($p.Count -ne 2) { throw "entrada inválida no -Mapa: '$_' (esperado portaLocal:portaRemota)" }
  [pscustomobject]@{ Local = [int]$p[0]; Remota = [int]$p[1] }
})
if (-not $pares) { throw '-Mapa vazio' }
New-Item -ItemType Directory -Force $LogDir | Out-Null
$log = Join-Path $LogDir "tunel-$Worker.log"
function Registra($m) { "$([DateTime]::Now.ToString('yyyy-MM-dd HH:mm:ss')) $m" | Tee-Object -FilePath $log -Append | Write-Host }

$encaminhamentos = foreach ($p in $pares) { '-L'; "$($p.Local):127.0.0.1:$($p.Remota)" }
$reversos = @($MapaReverso -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ } | ForEach-Object {
  $p = $_ -split ':'
  if ($p.Count -ne 2) { throw "entrada inválida no -MapaReverso: '$_' (esperado portaNoWorker:portaAqui)" }
  [pscustomobject]@{ NoWorker = [int]$p[0]; Aqui = [int]$p[1] }
})
foreach ($r in $reversos) { $encaminhamentos += @('-R', "$($r.NoWorker):127.0.0.1:$($r.Aqui)") }
$base = @('-i', $Chave, '-N',
          '-o', 'BatchMode=yes',
          '-o', 'ExitOnForwardFailure=yes',   # porta ocupada é falha, não túnel meio pronto
          '-o', 'ServerAliveInterval=30',
          '-o', 'ServerAliveCountMax=3',
          '-o', 'StrictHostKeyChecking=accept-new',
          '-o', "UserKnownHostsFile=$(Split-Path $Chave)\known_hosts")

Registra ("iniciando; " + (($pares | ForEach-Object { "$($_.Local)->$Worker`:$($_.Remota)" }) -join ' ') +
          $(if ($reversos) { " | reverso: " + (($reversos | ForEach-Object { "$Worker`:$($_.NoWorker)->$($_.Aqui)" }) -join ' ') } else { "" }))
$seguidas = 0
while ($true) {
  $t0 = Get-Date
  & ssh @base @encaminhamentos "$Usuario@$Worker" 2>&1 | ForEach-Object { Registra "ssh: $_" }
  $viveu = ((Get-Date) - $t0).TotalSeconds
  # queda em menos de 10 s é falha real (chave, porta ocupada, host fora): espera mais para não martelar
  if ($viveu -lt 10) { $seguidas++ } else { $seguidas = 0 }
  $espera = [Math]::Min(60, 5 * [Math]::Max(1, $seguidas))
  Registra "ssh saiu depois de $([Math]::Round($viveu))s (falhas rapidas seguidas=$seguidas); nova tentativa em ${espera}s"
  Start-Sleep -Seconds $espera
}
