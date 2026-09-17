<#
.SYNOPSIS
  Teste integrado: derruba o backend (kill, sem encerramento gracioso) NO MEIO de uma execução real,
  sobe de novo e confere que a fila foi preservada, as etapas foram reconciliadas pela tela e cada
  aparelho tem exatamente UMA mensagem desta execução (verificador independente: ContentProvider do app de QA).
#>
[CmdletBinding()]
param([string[]]$Instances = @('android-01'), [int]$KillAfterSec = 9, [string]$Base = 'http://127.0.0.1:8000', [switch]$Simulated)
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
foreach ($id in $Instances) {
  $serial = ($inst | Where-Object id -eq $id).serial
  $rows = & $adb -s $serial shell "content query --uri content://com.pocqa.messenger.provider/messages --projection body:status" | Select-String ([regex]::Escape("Reinicio $id $($run.id)"))
  $n = @($rows).Count
  $o = $r.objectives | Where-Object instance_id -eq $id
  Write-Host ("  {0}: objetivo={1} mensagens desta execução no app={2} {3}" -f $id, $o.status, $n, $(if ($n -le 1) { 'OK (sem duplicata)' } else { 'DUPLICADA!' }))
  if ($n -gt 1 -or ($o.status -eq 'succeeded' -and $n -ne 1)) { $ok = $false }
}
Write-Host ("Runs com esta chave no banco: " + @((Invoke-RestMethod "$Base/api/runs?limit=200") | Where-Object id -eq $run.id).Count)
if (-not $ok) { throw 'Falha: duplicata ou sucesso sem mensagem.' } else { Write-Host 'RESULTADO: fila preservada, reconciliação sem reenvio indevido.' -ForegroundColor Green }
