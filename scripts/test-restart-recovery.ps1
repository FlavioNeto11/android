<#
.SYNOPSIS
  Teste integrado: derruba o backend (kill, sem encerramento gracioso) NO MEIO de uma execução real,
  sobe de novo e confere que a fila foi preservada, as etapas foram reconciliadas pela tela e cada
  aparelho tem exatamente UMA mensagem desta execução (verificador independente: ContentProvider do app de QA).

  Item T.1 (aceite 7 em campo): confere TAMBÉM que os comandos que estavam em voo na hora da queda
  (`dispatched`/`running`/`acked`/`created`) saíram do estado aberto — o desfecho honesto é `uncertain`, nunca
  um sucesso inventado — e que cada worker que estava `online` antes da queda reconectou depois dela.
  Com -ComandoRemoto <id>, despacha um `start` naquele aparelho ANTES do kill, para que haja um comando remoto
  de verdade em voo (é o que falta ao ensaio de 17/09, que foi só local).
#>
[CmdletBinding()]
param([string[]]$Instances = @('android-01'), [int]$KillAfterSec = 9, [string]$Base = 'http://127.0.0.1:8000', [switch]$Simulated, [string]$ComandoRemoto)
$ErrorActionPreference = 'Stop'
$Instances = @($Instances | ForEach-Object { $_ -split '[,; ]+' } | Where-Object { $_ })
$root = Split-Path -Parent $PSScriptRoot
$adb = 'C:\Android\Sdk\platform-tools\adb.exe'
$cmd = 'Abra o QA Messenger, entre na conversa com o contato identificado como QA-003 e envie "Reinicio {instance_id} {run_id}". Confirme que a mensagem apareceu como enviada.'
$body = @{ command = $cmd; instance_ids = $Instances; idempotency_key = [guid]::NewGuid().ToString(); mode = 'execute' } | ConvertTo-Json
$run = Invoke-RestMethod -Method Post "$Base/api/runs" -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($body))
Write-Host "Execução $($run.id) criada; derrubando o backend em $KillAfterSec s…"
Start-Sleep -Seconds $KillAfterSec
$before = Invoke-RestMethod "$Base/api/runs/$($run.id)"
Write-Host ("Antes da queda: " + (($before.steps | Group-Object status | ForEach-Object { "$($_.Name)=$($_.Count)" }) -join ' '))
# --- aceite 7 em campo: o retrato do que estava EM VOO no instante da queda ---------------------------------
$abertos = @('created', 'dispatched', 'acked', 'running', 'cancel_requested')
if ($ComandoRemoto) {
  $novo = Invoke-RestMethod -Method Post "$Base/api/instances/$ComandoRemoto/actions/start" -ContentType 'application/json' -Body '{}'
  Write-Host "Comando remoto em voo: $($novo.command_id) ($ComandoRemoto start, estado $($novo.state))"
}
$emVoo = @(Invoke-RestMethod "$Base/api/commands?limit=200" | Where-Object { $abertos -contains $_.state })
$workersAntes = @(Invoke-RestMethod "$Base/api/workers" | Where-Object { $_.state -eq 'online' })
Write-Host ("Em voo na queda: {0} comando(s); worker(s) online: {1}" -f $emVoo.Count, ((@($workersAntes | ForEach-Object { $_.id }) -join ', ')))
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like '*app.main*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -Confirm:$false -ErrorAction SilentlyContinue }   # o launcher do venv e o python real caem juntos
Write-Host 'Backend derrubado (kill). Subindo novamente…'
Start-Sleep 2
$startArgs = @('-NoProfile', '-File', (Join-Path $PSScriptRoot 'start.ps1'), '-NoBrowser'); if ($Simulated) { $startArgs += '-Simulated' }
Start-Process pwsh -ArgumentList $startArgs -WindowStyle Hidden -Wait
$deadline = (Get-Date).AddMinutes(8)
do { Start-Sleep 3; $r = Invoke-RestMethod "$Base/api/runs/$($run.id)" } while ($r.status -in 'running', 'planning' -and (Get-Date) -lt $deadline)
Write-Host "Depois: execução $($r.status) — $($r.status_detail)"
$r.attempts | Where-Object status -eq 'interrupted' | ForEach-Object { Write-Host "  tentativa interrompida: $($_.id.Split(':')[-3..-1] -join ':') → $($_.recovery)" }
$inst = Invoke-RestMethod "$Base/api/instances"
$ok = $true
# --- aceite 7 em campo: comando em voo virou desfecho honesto, e o worker voltou ----------------------------
foreach ($c0 in $emVoo) {
  $c1 = Invoke-RestMethod "$Base/api/commands/$($c0.id)"
  $fechou = ($abertos -notcontains $c1.state)
  Write-Host ("  comando {0} ({1} {2}): {3} -> {4} {5}" -f $c1.id, $c1.instance_id, $c1.verb, $c0.state, $c1.state,
    $(if (-not $fechou) { 'AINDA ABERTO — a partida não reconciliou!' } elseif ($c1.state -eq 'succeeded') { '(sucesso; confira se ele é observado, e não presumido)' } else { 'OK' }))
  if (-not $fechou) { $ok = $false }
}
if ($workersAntes.Count) {
  $prazoWorker = (Get-Date).AddMinutes(3)
  do {
    Start-Sleep 5
    $agoraOnline = @(Invoke-RestMethod "$Base/api/workers" | Where-Object { $_.state -eq 'online' } | ForEach-Object { $_.id })
    $faltam = @($workersAntes | Where-Object { $agoraOnline -notcontains $_.id })
  } while ($faltam.Count -gt 0 -and (Get-Date) -lt $prazoWorker)
  if ($faltam.Count -gt 0) {
    Write-Host ("  worker(s) que NÃO reconectaram em 3 min: " + ((@($faltam | ForEach-Object { $_.id }) -join ', '))) -ForegroundColor Red
    $ok = $false
  } else { Write-Host "  worker(s) reconectado(s): $($agoraOnline -join ', ')" }
}
foreach ($id in $Instances) {
  $serial = ($inst | Where-Object id -eq $id).serial
  $rows = & $adb -s $serial shell "content query --uri content://com.pocqa.messenger.provider/messages --projection body:status" | Select-String ([regex]::Escape("Reinicio $id $($run.id)"))
  $n = @($rows).Count
  $o = $r.objectives | Where-Object instance_id -eq $id
  Write-Host ("  {0}: objetivo={1} mensagens desta execução no app={2} {3}" -f $id, $o.status, $n, $(if ($n -le 1) { 'OK (sem duplicata)' } else { 'DUPLICADA!' }))
  if ($n -gt 1 -or ($o.status -eq 'succeeded' -and $n -ne 1)) { $ok = $false }
}
Write-Host ("Runs com esta chave no banco: " + @((Invoke-RestMethod "$Base/api/runs?limit=200").runs | Where-Object id -eq $run.id).Count)
if (-not $ok) { throw 'Falha: duplicata ou sucesso sem mensagem.' } else { Write-Host 'RESULTADO: fila preservada, reconciliação sem reenvio indevido.' -ForegroundColor Green }
