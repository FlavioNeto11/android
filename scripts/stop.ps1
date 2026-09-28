<#
.SYNOPSIS
  Encerra a POC de forma graciosa. Para SOMENTE o que este projeto iniciou:
  backend, o Appium que ele subiu e (com -StopEmulators) os emuladores cujo PID ele registrou.
  Sem -StopEmulators os emuladores continuam ligados, com apps abertos e sessões ativas.

.DESCRIPTION
  Com o backend já parado, encerra também o Appium DESTE projeto que tenha sobrado na porta de `appium:` do
  config (K-039): um backend derrubado à força não desliga o Appium, e o backend seguinte o readotava sem
  mascaramento comprovado. Só o `node.exe` cuja linha de comando aponta para `tools\appium` desta árvore; qualquer
  outro processo na porta fica, com aviso (`scripts\lib\appium-do-projeto.ps1`).

.PARAMETER Simular
  Diz o que seria encerrado (backend e Appium) e sai. Nada é pedido ao backend nem encerrado.

.PARAMETER Config
  O config de onde sai a porta do Appium. Padrão: config\config.yaml desta árvore.

.EXAMPLE
  pwsh -File scripts\stop.ps1 -Simular
  pwsh -File scripts\stop.ps1
#>
[CmdletBinding()]
param([switch]$StopEmulators, [switch]$Simular, [string]$Config)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'lib\farm-health.ps1')   # "responde na porta" não é "a Farm responde" (26/09/2026)
. (Join-Path $PSScriptRoot 'lib\appium-do-projeto.ps1')   # o Appium órfão que o backend novo readotava (K-039)
$base = 'http://127.0.0.1:8000'
$pidFile = Join-Path $root 'data\backend.pid'
if (-not $Config) { $Config = Join-Path $root 'config\config.yaml' }
$appiumPid = Join-Path $root 'data\appium.pid'

if ($Simular) {
  $h = Get-FarmHealth $base 2
  Write-Output ('backend: ' + $(if ($h) { "pediria o encerramento à Farm em $base (commit $($h.commit))" }
                                else { "nenhum backend da Farm responde em $base" }))
  if (Test-Path $pidFile) { Write-Output "backend.pid: $((Get-Content $pidFile | Select-Object -First 1))" }
  $a = Get-AppiumConfig $Config
  $pastaAppium = Resolve-PastaAppium $root $a.Pasta
  Write-Output "appium: porta $($a.Porta) (host $($a.Host)), pasta $pastaAppium — só depois de o backend parar"
  Stop-AppiumDoProjeto -Porta $a.Porta -PastaAppium $pastaAppium -Simular
  Write-Output 'simulacao: nada foi encerrado'
  return
}
# O segredo local prova "eu rodo NESTA máquina, com acesso ao disco dela". Antes bastava o par ser 127.0.0.1, e o
# túnel SSH reverso fez disso uma porta aberta: toda conexão vinda do worker chega com par de loopback de verdade,
# então qualquer processo daquela máquina derrubava o central. Ver backend/app/security/local_secret.py.
# Ausente é normal e não é erro: um backend ANTERIOR a esta mudança não tem o arquivo e ignora o cabeçalho.
$segredoFile = Join-Path $root 'data\shutdown.token'
$cabecalhos = @{}
if (Test-Path $segredoFile) { $cabecalhos['X-Shutdown-Token'] = (Get-Content $segredoFile -Raw).Trim() }
try {
  # O pedido leva o token de encerramento: só vai para a Farm identificada. Com ela parada, a 8000 pode ser de
  # outro serviço (o `cartorio-api-1`, 26/09/2026) — e o token não sai daqui para ele.
  if (-not (Get-FarmHealth $base 3)) { throw 'nenhum backend da Farm respondeu em /api/health' }
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
  $alive = [bool](Get-FarmHealth $base 2)
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

# O Appium que sobrou (K-039). O backend só encerra o Appium que ele sabe ter subido, e só quando sai pelo
# encerramento gracioso; depois do deploy (tarefa `farm-central` parada, depois este script), o `node` ficou na
# porta três vezes, e o backend seguinte o readotou e subiu `degraded`. Com a Farm ainda respondendo, o Appium é
# DELA e fica. Falha aqui não desfaz o encerramento do backend: vira aviso, e o health do próximo backend acusa.
try {
  if (Get-FarmHealth $base 2) {
    Write-Warning 'O backend da Farm ainda responde: o Appium é dele e fica como está.'
  } else {
    $a = Get-AppiumConfig $Config
    Stop-AppiumDoProjeto -Porta $a.Porta -PastaAppium (Resolve-PastaAppium $root $a.Pasta) -ArquivoPid $appiumPid
  }
} catch {
  Write-Warning "Conferência do Appium órfão falhou: $($_.Exception.Message)"
}
Write-Host 'Pronto.'
