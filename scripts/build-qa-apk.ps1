<#
.SYNOPSIS
    Compila o app de QA (qa-app) e publica o APK debug em qa-app\dist\qa-messenger.apk.

.DESCRIPTION
    - Define ANDROID_HOME / ANDROID_SDK_ROOT apenas para este processo.
    - Cria qa-app\local.properties (sdk.dir=...) se ainda nao existir.
    - Executa "gradlew.bat assembleDebug --no-daemon" (Gradle wrapper fixado no projeto).
    - Copia o APK para qa-app\dist\qa-messenger.apk e imprime caminho, tamanho e SHA-256.

    Requisitos: JDK 17+ (testado com JDK 21) em JAVA_HOME ou no PATH; Android SDK com platforms;android-34.
    O AGP baixa sozinho o build-tools de que precisa (licencas do SDK ja aceitas).

.PARAMETER SdkRoot
    Raiz do Android SDK. Padrao: C:\Android\Sdk

.PARAMETER Clean
    Executa "clean assembleDebug". Recomendado antes de versionar o APK: o empacotamento incremental do
    AGP deixa espacos vazios no .apk quando entradas sao removidas, e o arquivo fica maior que o necessario.

.EXAMPLE
    pwsh -File C:\git\android\scripts\build-qa-apk.ps1
    pwsh -File C:\git\android\scripts\build-qa-apk.ps1 -Clean
    pwsh -File C:\git\android\scripts\build-qa-apk.ps1 -SdkRoot D:\Android\Sdk
#>
[CmdletBinding()]
param(
    [string]$SdkRoot = 'C:\Android\Sdk',
    [switch]$Clean
)

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$appDir   = Join-Path $repoRoot 'qa-app'
$gradlew  = Join-Path $appDir 'gradlew.bat'
$distDir  = Join-Path $appDir 'dist'
$apkOut   = Join-Path $appDir 'app\build\outputs\apk\debug\app-debug.apk'
$apkDist  = Join-Path $distDir 'qa-messenger.apk'

if (-not (Test-Path -LiteralPath $gradlew)) { throw "gradlew.bat nao encontrado em $appDir" }
if (-not (Test-Path -LiteralPath (Join-Path $SdkRoot 'platforms'))) { throw "Android SDK nao encontrado em $SdkRoot (use -SdkRoot)" }
if (-not $env:JAVA_HOME -and -not (Get-Command java -ErrorAction SilentlyContinue)) {
    throw 'JDK nao encontrado: defina JAVA_HOME (JDK 17+; o projeto foi validado com JDK 21).'
}

# SDK somente para este processo (nao altera variaveis de usuario/maquina).
$env:ANDROID_HOME     = $SdkRoot
$env:ANDROID_SDK_ROOT = $SdkRoot

# local.properties (ignorado pelo git). Formato .properties: "\" e ":" escapados.
$localProps = Join-Path $appDir 'local.properties'
if (-not (Test-Path -LiteralPath $localProps)) {
    $escaped = $SdkRoot.Replace('\', '\\').Replace(':', '\:')
    Set-Content -LiteralPath $localProps -Value "sdk.dir=$escaped" -Encoding ascii
    Write-Host "local.properties criado: sdk.dir=$escaped"
}

$gradleArgs = @('assembleDebug', '--no-daemon')
if ($Clean) { $gradleArgs = @('clean') + $gradleArgs }

Write-Host "==> gradlew.bat $($gradleArgs -join ' ')  (em $appDir)"
Push-Location $appDir
try {
    & $gradlew @gradleArgs
    if ($LASTEXITCODE -ne 0) { throw "Build falhou (gradlew saiu com codigo $LASTEXITCODE)" }
}
finally {
    Pop-Location
}

if (-not (Test-Path -LiteralPath $apkOut)) { throw "APK nao encontrado em $apkOut" }
New-Item -ItemType Directory -Force -Path $distDir | Out-Null
Copy-Item -LiteralPath $apkOut -Destination $apkDist -Force

$file = Get-Item -LiteralPath $apkDist
$hash = (Get-FileHash -LiteralPath $apkDist -Algorithm SHA256).Hash.ToLowerInvariant()
Write-Host ''
Write-Host "APK     : $($file.FullName)"
Write-Host ("Tamanho : {0:N0} bytes ({1:N1} KiB)" -f $file.Length, ($file.Length / 1KB))
Write-Host "SHA-256 : $hash"
