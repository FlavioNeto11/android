<#
.SYNOPSIS
  Sonda somente leitura de android-12: investiga a carga que o achado #1 aponta como causa da falha em abrir
  sessão de automação (Appium Settings não sobe em 5 s, load 9-18).

.DESCRIPTION
  Item 0.7 / achado #1, parte "investigar a carga de android-12". Não reinicia nada, não altera nada — só lê
  pelo túnel ADB já em pé (scripts\worker-tunnel.ps1). Serial e porta seguem a sonda registrada no pacote
  (.claude/plano-100/pacotes/0.7.md): 127.0.0.1:15559 seria android-12, mas CONFIRA no /api/instances antes —
  o mapa de portas do túnel é decisão operacional e pode ter mudado desde 21/09.

.EXAMPLE
  pwsh -File scripts\sondar-android-12.ps1
#>
[CmdletBinding()]
param(
  [string]$Serial = '127.0.0.1:15559',
  [string]$Adb = 'C:\Android\Sdk\platform-tools\adb.exe',
  [string]$Out = 'data\logs\sonda-android-12.txt'
)
$ErrorActionPreference = 'Stop'
if (-not (Test-Path $Adb)) { throw "adb não encontrado em $Adb" }

$root = Split-Path -Parent $PSScriptRoot
$outPath = Join-Path $root $Out
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $outPath) | Out-Null

function Rodar($rotulo, [string[]]$argumentos) {
  Write-Host "== $rotulo =="
  $texto = & $Adb -s $Serial @argumentos 2>&1 | Out-String
  Write-Host $texto.Trim()
  "== $rotulo ==`n$($texto.Trim())`n" | Add-Content -Path $outPath -Encoding utf8
}

"Sonda de $Serial em $(Get-Date -Format o)" | Set-Content -Path $outPath -Encoding utf8

Rodar 'estado do serviço convidado'     @('shell', 'service check settings; service check activity; service check package')
Rodar 'boot e uptime'                   @('shell', 'getprop sys.boot_completed; uptime')
Rodar 'carga (top, 1 amostra)'          @('shell', 'top -n 1 -m 10 -o pid,%cpu,%mem,cmdline 2>/dev/null || top -n 1')
Rodar 'Appium Settings instalado?'      @('shell', 'pm list packages | grep -i appium')
Rodar 'Appium Settings em execução?'    @('shell', 'ps -A | grep -i appium')
Rodar 'logcat recente com "appium"'     @('shell', 'logcat -d -t 300 | grep -i appium')

Write-Host "`nSaída completa em $outPath"
