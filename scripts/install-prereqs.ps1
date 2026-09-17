<#
.SYNOPSIS
  Instala os pré-requisitos da POC no Windows: Android SDK (cmdline-tools, platform-tools,
  emulator, build-tools, platform, system image) e Appium + driver UiAutomator2 (local ao projeto).

.NOTES
  - Idempotente: pode ser executado novamente; só baixa o que falta.
  - ACEITA AS LICENÇAS DO ANDROID SDK em nome do usuário (sdkmanager --licenses).
    Leia os termos em https://developer.android.com/studio/terms antes de executar.
  - Não altera variáveis de ambiente globais nem configurações do sistema. O backend
    recebe o caminho do SDK por config/.env e o repassa aos processos filhos.
#>
[CmdletBinding()]
param(
  [string]$SdkRoot = 'C:\Android\Sdk',
  [string]$CmdlineToolsUrl = 'https://dl.google.com/android/repository/commandlinetools-win-15859902_latest.zip',
  [string]$CmdlineToolsSha256 = '90ae805d20434428bffcb699c290860f19bb5f66a67e6b330067e3de801fb04a',
  [string]$ApiLevel = '34',
  [string[]]$ImageTags = @('google_apis'),
  [string]$BuildTools = '35.0.0',
  [switch]$SkipAppium
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$projectRoot = Split-Path -Parent $PSScriptRoot

function Step($m) { Write-Host "[$(Get-Date -Format HH:mm:ss)] $m" }

# --- 1. cmdline-tools -------------------------------------------------------
$sdkmanager = Join-Path $SdkRoot 'cmdline-tools\latest\bin\sdkmanager.bat'
if (-not (Test-Path $sdkmanager)) {
  Step "Baixando cmdline-tools de $CmdlineToolsUrl"
  New-Item -ItemType Directory -Force $SdkRoot | Out-Null
  $zip = Join-Path $env:TEMP 'commandlinetools-win.zip'
  Invoke-WebRequest -Uri $CmdlineToolsUrl -OutFile $zip -UseBasicParsing
  $hash = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
  if ($hash -ne $CmdlineToolsSha256.ToLower()) { throw "SHA-256 inesperado para cmdline-tools: $hash" }
  Step "SHA-256 conferido. Extraindo..."
  $tmp = Join-Path $env:TEMP "cmdline-tools-extract-$PID"
  Expand-Archive -Path $zip -DestinationPath $tmp -Force
  New-Item -ItemType Directory -Force (Join-Path $SdkRoot 'cmdline-tools') | Out-Null
  Move-Item (Join-Path $tmp 'cmdline-tools') (Join-Path $SdkRoot 'cmdline-tools\latest')
  Remove-Item $tmp -Recurse -Force -Confirm:$false
  Remove-Item $zip -Force -Confirm:$false
} else { Step "cmdline-tools já presente." }

# --- 2. licenças + pacotes ---------------------------------------------------
Step "Aceitando licenças do Android SDK (sdkmanager --licenses)"
(1..40 | ForEach-Object { 'y' }) | & $sdkmanager --sdk_root=$SdkRoot --licenses | Select-Object -Last 2

$packages = @('platform-tools', 'emulator', "build-tools;$BuildTools", "platforms;android-$ApiLevel")
foreach ($t in $ImageTags) { $packages += "system-images;android-$ApiLevel;$t;x86_64" }
Step "Instalando pacotes: $($packages -join ', ')"
(1..40 | ForEach-Object { 'y' }) | & $sdkmanager --sdk_root=$SdkRoot @packages | Where-Object { $_ -notmatch '^\[=* *\]' } | Select-Object -Last 5

Step "Pacotes instalados:"
& $sdkmanager --sdk_root=$SdkRoot --list_installed 2>$null | Select-String -Pattern 'platform-tools|emulator|build-tools|platforms;|system-images|cmdline'

# --- 3. aceleração -----------------------------------------------------------
Step "Verificando aceleração (emulator -accel-check)"
& (Join-Path $SdkRoot 'emulator\emulator.exe') -accel-check

# --- 4. Appium local ao projeto ---------------------------------------------
if (-not $SkipAppium) {
  $appiumDir = Join-Path $projectRoot 'tools\appium'
  Step "Instalando Appium + UiAutomator2 (versões fixadas em $appiumDir\package.json)"
  Push-Location $appiumDir
  try { npm ci --no-audit --no-fund 2>&1 | Select-Object -Last 3 } finally { Pop-Location }
}
Step "Concluído."
