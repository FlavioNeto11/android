<#
.SYNOPSIS
  Encerra a POC de forma graciosa. Para SOMENTE o que este projeto iniciou:
  backend, o Appium que ele subiu e (com -StopEmulators) os emuladores cujo PID ele registrou.
  Sem -StopEmulators os emuladores continuam ligados, com apps abertos e sessões ativas.
#>
[CmdletBinding()]
param([switch]$StopEmulators)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$base = 'http://127.0.0.1:8000'
$pidFile = Join-Path $root 'data\backend.pid'
try {
  $q = if ($StopEmulators) { '?stop_emulators=true' } else { '' }
  $null = Invoke-RestMethod -Method Post "$base/api/admin/shutdown$q" -TimeoutSec 10
  Write-Host ('Encerramento solicitado' + $(if ($StopEmulators) { ' (incluindo emuladores iniciados pelo projeto)' } else { ' (emuladores permanecem ligados)' }))
} catch { Write-Host 'Backend não respondeu; verificando processo pelo PID registrado…' }

$deadline = (Get-Date).AddSeconds(120)
do {
  Start-Sleep -Seconds 1
  $alive = $false
  try { $null = Invoke-RestMethod "$base/api/health" -TimeoutSec 2; $alive = $true } catch {}
} while ($alive -and (Get-Date) -lt $deadline)

if (Test-Path $pidFile) {
  $id = [int](Get-Content $pidFile | Select-Object -First 1)
  $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$id" -ErrorAction SilentlyContinue
  # só encerra se o PID ainda for o NOSSO backend (PIDs são reciclados)
  if ($proc -and $proc.CommandLine -like '*app.main*') {
    if ($alive) { Write-Warning 'Backend não encerrou a tempo; finalizando o processo registrado.' ; Stop-Process -Id $id -Force -Confirm:$false }
  }
  Remove-Item $pidFile -Force -Confirm:$false
}
Write-Host 'Pronto.'
