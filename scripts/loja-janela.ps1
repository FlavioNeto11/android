<#
.SYNOPSIS
  PLANO B: sobe o aparelho-loja COM JANELA, à mão, para você entrar na conta Google na Play Store.

.DESCRIPTION
  O caminho normal é o painel: a loja tem `window: true` nos overrides e o backend já a sobe com janela. Use este
  script só se a janela NÃO aparecer quando o backend inicia o emulador (por exemplo, backend rodando numa sessão
  sem área de trabalho).

  O que ele faz: inicia o emulador do AVD da loja, na porta de console dela, sem `-no-window`. Nada mais.
  A conta Google é digitada por você, direto na janela — nenhuma tecla passa pelo backend, pelo Appium ou pelo adb.

  ORDEM QUE IMPORTA: o backend só adota um emulador que já está no ar quando ELE sobe. Então:
    1. pare o backend            ->  pwsh -File scripts\stop.ps1
    2. rode este script          ->  a janela do emulador abre
    3. suba o backend            ->  pwsh -File scripts\start.ps1
  Com o emulador manual no ar, NÃO use "Iniciar" no cartão da loja: abriria um segundo processo na mesma porta.
  Para desligar: feche a janela do emulador (ou "Desligar a loja" no painel).

.PARAMETER Instancia
  Id da loja, como em `instances.store` no config.yaml. Padrão: android-11.

.PARAMETER SdkRoot
  Raiz do Android SDK. Padrão: C:\Android\Sdk.
#>
[CmdletBinding()]
param(
  [string]$Instancia = 'android-11',
  [string]$SdkRoot = 'C:\Android\Sdk'
)
$ErrorActionPreference = 'Stop'

$raiz = Split-Path -Parent $PSScriptRoot
$avdHome = Join-Path $raiz 'data\avd'
$emulador = Join-Path $SdkRoot 'emulator\emulator.exe'
if (-not (Test-Path $emulador)) { throw "emulator.exe não encontrado em $emulador" }
if (-not (Test-Path (Join-Path $avdHome "$Instancia.avd"))) {
  throw "O AVD '$Instancia' ainda não existe em $avdHome. Crie-o pelo painel (cartão da loja -> Criar AVD) e rode de novo."
}
if ($Instancia -notmatch '(\d+)$') { throw "Não consegui tirar o índice de '$Instancia'." }
# Mesma conta do backend: porta de console = 5554 + 2*(índice-1).
$porta = 5554 + 2 * ([int]$Matches[1] - 1)

try { $null = Invoke-RestMethod 'http://127.0.0.1:8000/api/health' -TimeoutSec 2; $backendNoAr = $true } catch { $backendNoAr = $false }
if ($backendNoAr) {
  Write-Warning 'O backend está no ar. Ele só adota emulador que já existe quando sobe: pare-o (scripts\stop.ps1), rode este script e suba-o de novo.'
}

$env:ANDROID_AVD_HOME = $avdHome
$env:ANDROID_SDK_ROOT = $SdkRoot
Write-Host "Subindo $Instancia com janela (console $porta)…"
Start-Process -FilePath $emulador -ArgumentList @('-avd', $Instancia, '-port', "$porta", '-no-audio', '-no-boot-anim',
  '-no-snapshot', '-gpu', 'swiftshader_indirect', '-accel', 'on', '-no-metrics')
Write-Host 'Quando o Android terminar de iniciar, abra a Play Store NA JANELA e entre na sua conta Google.'
Write-Host 'Minimize a janela se quiser — fechar a janela desliga o emulador.'
