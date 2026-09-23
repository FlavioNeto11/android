<#
.SYNOPSIS
  Inicia a POC: backend (processo único, 127.0.0.1:8000) que sobe o Appium local e serve o painel.
  Os emuladores são iniciados pelo painel (ou com -StartInstances N).
.PARAMETER Dev
  Também inicia o Vite (127.0.0.1:5173) com hot reload do frontend.
.PARAMETER StartInstances
  Quantidade de instâncias a iniciar após o backend ficar pronto (0 = nenhuma).
.PARAMETER Instalar
  Registra o backend como tarefa supervisionada (sobe no boot, religa se cair) em vez de iniciá-lo nesta sessão.
  Delega para scripts\install-central-service.ps1; `-Simular` mostra o que seria registrado sem tocar em nada.
  Existe porque um backend iniciado à mão numa sessão interativa morre no logoff e não volta (achado #136).
#>
[CmdletBinding()]
param([switch]$Dev, [int]$StartInstances = 0, [switch]$NoBrowser, [switch]$Simulated,
      [switch]$Instalar, [switch]$Simular)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
if ($Instalar) {
  # Repasse EXPLÍCITO, nunca por splat de array. `& script @extras` com `@('-Simular')` liga o texto ao primeiro
  # parâmetro POSICIONAL do outro script — aqui, `-Tarefa` — e o `-Simular` some. Medido da pior forma: a
  # chamada registrou (e iniciou) uma tarefa agendada chamada "-Simular" que um ensaio jamais deveria criar.
  $instalador = Join-Path $PSScriptRoot 'install-central-service.ps1'
  if ($Simular) { & $instalador -Simular } else { & $instalador }
  return
}
$py = Join-Path $root 'backend\.venv\Scripts\python.exe'
$data = Join-Path $root 'data'
New-Item -ItemType Directory -Force (Join-Path $data 'logs') | Out-Null

if (-not (Test-Path $py)) {
  Write-Host 'Criando o ambiente Python (backend\.venv)…'
  Push-Location (Join-Path $root 'backend')
  try { uv venv --python 3.13 .venv; uv pip install -r requirements.txt --python .venv\Scripts\python.exe } finally { Pop-Location }
}
if (-not (Test-Path (Join-Path $root '.env'))) { Copy-Item (Join-Path $root '.env.example') (Join-Path $root '.env'); Write-Host '.env criado — preencha ANTHROPIC_API_KEY.' }
# `config/config.yaml` é de CADA instalação e não é versionado (achado #177): na primeira partida ele nasce
# do exemplo neutro — 4 emuladores locais, sem remoto e sem loja —, como o `.env`. Nunca sobrescreve o que já existe.
$cfg = Join-Path $root 'config\config.yaml'
if (-not (Test-Path $cfg)) { Copy-Item (Join-Path $root 'config\config.example.yaml') $cfg; Write-Host 'config\config.yaml criado a partir do exemplo (4 emuladores locais).' }
if (-not $Dev -and -not (Test-Path (Join-Path $root 'frontend\dist\index.html'))) {
  Write-Host 'Compilando o frontend (frontend\dist)…'
  Push-Location (Join-Path $root 'frontend')
  try { if (-not (Test-Path node_modules)) { npm ci --no-audit --no-fund }; npm run build } finally { Pop-Location }
}

$base = 'http://127.0.0.1:8000'
$up = $false
try { $null = Invoke-RestMethod "$base/api/health" -TimeoutSec 2; $up = $true } catch {}
if ($up) { Write-Host 'Backend já está em execução.' }
else {
  if ($Simulated) { $env:AI_PROVIDER = 'simulated'; Write-Warning 'MODO SIMULADO: nenhuma IA será consultada.' }
  $p = Start-Process -FilePath $py -ArgumentList '-m', 'app.main' -WorkingDirectory (Join-Path $root 'backend') -PassThru -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $data 'logs\backend.out.log') -RedirectStandardError (Join-Path $data 'logs\backend.err.log')
  Set-Content -Path (Join-Path $data 'backend.pid') -Value $p.Id
  Write-Host "Backend iniciado (pid $($p.Id)). Aguardando ficar pronto…"
  for ($i = 0; $i -lt 90; $i++) {
    Start-Sleep -Seconds 1
    try { $h = Invoke-RestMethod "$base/api/health" -TimeoutSec 2; break } catch { if ($p.HasExited) { Get-Content (Join-Path $data 'logs\backend.err.log') -Tail 20; throw 'O backend encerrou ao iniciar.' } }
  }
  if (-not $h) { throw 'O backend não respondeu em 90 s. Veja data\logs\backend.err.log' }
  Write-Host "Saúde: $($h.status) | IA: $($h.ai.provider) configurada=$($h.ai.configured) simulada=$($h.ai.simulated) | Appium: $($h.appium.running)"
  $h.problems | ForEach-Object { Write-Warning "$($_.message) → $($_.hint)" }
}
if ($Dev) {
  Start-Process -FilePath 'npm.cmd' -ArgumentList 'run', 'dev' -WorkingDirectory (Join-Path $root 'frontend') -WindowStyle Minimized
  $url = 'http://127.0.0.1:5173'
} else { $url = $base }
if ($StartInstances -gt 0) {
  # Achado #153: gerar `android-{0:d2}` de 1..N mandava `start` a aparelho de OUTRA máquina no dia em que
  # android-09/10 viraram remotos — e prendia o parque a um teto de 10 que ele já passou. A lista sai de quem
  # existe de verdade: os emuladores DESTE host (sem a loja, sem os de worker), em ordem.
  $locais = @(Invoke-RestMethod "$base/api/instances" | Where-Object { $_.kind -eq 'emulator' } | Sort-Object id | ForEach-Object { $_.id })
  $ids = @($locais | Select-Object -First $StartInstances)
  if (-not $ids.Count) { throw 'Nenhum emulador local configurado (instances.count em config\config.yaml).' }
  $body = @{ ids = $ids; action = 'start' } | ConvertTo-Json
  $r = Invoke-RestMethod -Method Post "$base/api/instances/bulk" -ContentType 'application/json' -Body $body
  Write-Host "Iniciando: $($r.accepted -join ', ')"
}
Write-Host "Painel: $url"
if (-not $NoBrowser) { Start-Process $url }
