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
# O segredo local prova "eu rodo NESTA máquina, com acesso ao disco dela". Antes bastava o par ser 127.0.0.1, e o
# túnel SSH reverso fez disso uma porta aberta: toda conexão vinda do worker chega com par de loopback de verdade,
# então qualquer processo daquela máquina derrubava o central. Ver backend/app/security/local_secret.py.
# Ausente é normal e não é erro: um backend ANTERIOR a esta mudança não tem o arquivo e ignora o cabeçalho.
$segredoFile = Join-Path $root 'data\shutdown.token'
$cabecalhos = @{}
if (Test-Path $segredoFile) { $cabecalhos['X-Shutdown-Token'] = (Get-Content $segredoFile -Raw).Trim() }
try {
  $q = if ($StopEmulators) { '?stop_emulators=true' } else { '' }
  $null = Invoke-RestMethod -Method Post "$base/api/admin/shutdown$q" -Headers $cabecalhos -TimeoutSec 10
  Write-Host ('Encerramento solicitado' + $(if ($StopEmulators) { ' (incluindo emuladores iniciados pelo projeto)' } else { ' (emuladores permanecem ligados)' }))
} catch {
  if ($_.Exception.Response -and [int]$_.Exception.Response.StatusCode -eq 403) {
    Write-Warning ('Encerramento recusado (403). O backend no ar exige data\shutdown.token e este script não o ' +
                   'encontrou ou ele está velho. O arquivo é regravado a cada subida do backend.')
  }
  Write-Host 'Backend não respondeu ou recusou; verificando processo pelo PID registrado…'
}

$deadline = (Get-Date).AddSeconds(120)
do {
  Start-Sleep -Seconds 1
  $alive = $false
  try { $null = Invoke-RestMethod "$base/api/health" -TimeoutSec 2; $alive = $true } catch {}
} while ($alive -and (Get-Date) -lt $deadline)

if (Test-Path $pidFile) {
  $id = [int](Get-Content $pidFile | Select-Object -First 1)
  # O python.exe do venv é um lançador: o interpretador de verdade é FILHO dele. "Nossos" = o PID registrado e os
  # filhos diretos dele, e só enquanto a linha de comando for a do backend (PIDs são reciclados).
  function Get-OurBackend {
    @(Get-CimInstance Win32_Process -Filter "ProcessId=$id OR ParentProcessId=$id" -ErrorAction SilentlyContinue |
      Where-Object { $_.Name -like 'python*' -and $_.CommandLine -like '*app.main*' })
  }
  # A porta fechar não basta: o processo tem de SAIR, senão o próximo start cria um segundo dono do banco.
  $exitBy = (Get-Date).AddSeconds(90)
  while ((Get-OurBackend).Count -gt 0 -and (Get-Date) -lt $exitBy) { Start-Sleep -Seconds 1 }
  $left = Get-OurBackend
  if ($left.Count -gt 0) {
    Write-Warning 'Backend não encerrou a tempo; finalizando o processo registrado (o estado já está no SQLite).'
    $left | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -Confirm:$false -ErrorAction SilentlyContinue }
  }
  Remove-Item $pidFile -Force -Confirm:$false
}
Write-Host 'Pronto.'
