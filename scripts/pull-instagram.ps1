<#
.SYNOPSIS
  Copia para `apks\inbox` o app JÁ INSTALADO num aparelho seu — celular por USB ou emulador com Play Store.

.DESCRIPTION
  Este script NÃO baixa nada de lugar nenhum. Ele copia, do aparelho onde VOCÊ instalou o app pela loja, os
  arquivos que o Android guarda em disco: o `base.apk` e os splits de ABI, densidade e idioma. É o caminho
  previsto no plano — "o usuário fornece os arquivos" —, e é o único que produz um conjunto de splits REAL.
  Provado assim em 18/09 com o Instagram 447.0.0.55.81: `base` + `config.xhdpi` num aparelho xhdpi, esperado =
  observado (docs/relatorio-validacao.md §10.3). Continua faltando o caso em que a filtragem DESCARTA alguma
  coisa: aquele conjunto não tinha split de ABI nem de idioma.

  Depois de copiar, importe pelo portal (Aplicativos → Importar da pasta) ou por:
      pwsh -File scripts\instagram.ps1 importar

  Pacote, versão, splits, ABIs e assinatura vêm da inspeção do próprio arquivo — o nome não decide nada.

.PARAMETER Serial
  Serial do aparelho, como aparece em `adb devices`. Pode omitir se só houver um conectado.

.PARAMETER Pacote
  Qual app copiar. Padrão: com.instagram.android.

.EXAMPLE
  pwsh -File scripts\pull-instagram.ps1
.EXAMPLE
  pwsh -File scripts\pull-instagram.ps1 -Serial 1a2b3c4d -Pacote com.instagram.android
#>
[CmdletBinding()]
param(
  [string]$Serial,
  [string]$Pacote = 'com.instagram.android'
)
$ErrorActionPreference = 'Stop'

$raiz = Split-Path -Parent $PSScriptRoot
$inbox = Join-Path $raiz 'apks\inbox'
$adb = if ($env:ANDROID_SDK_ROOT) { Join-Path $env:ANDROID_SDK_ROOT 'platform-tools\adb.exe' } else { $null }
if (-not $adb -or -not (Test-Path $adb)) { $adb = 'C:\Android\Sdk\platform-tools\adb.exe' }
if (-not (Test-Path $adb)) { throw 'adb não encontrado. Defina ANDROID_SDK_ROOT ou instale o platform-tools.' }

function Invoke-Adb {
  # `$Args` seria variável automática do PowerShell e chegaria vazia aqui — daí o nome em português.
  param([string[]]$Argumentos)
  $prefixo = if ($Serial) { @('-s', $Serial) } else { @() }
  & $adb @prefixo @Argumentos 2>&1
}

# ---------------------------------------------------------------- aparelho
$linhas = (& $adb devices) | Select-Object -Skip 1 | Where-Object { $_ -match '\S' -and $_ -notmatch 'offline' }
if (-not $linhas) { throw "Nenhum aparelho conectado. Ligue o celular por USB com depuração USB ativa, ou inicie um emulador com Play Store." }
if (-not $Serial -and $linhas.Count -gt 1) {
  Write-Host 'Mais de um aparelho conectado:' -ForegroundColor Yellow
  $linhas | ForEach-Object { Write-Host "  $_" }
  throw 'Informe -Serial para escolher de qual copiar.'
}
if (-not $Serial) { $Serial = ($linhas[0] -split '\s+')[0] }
Write-Host "Aparelho: $Serial"

# ---------------------------------------------------------------- caminhos do pacote
$saida = Invoke-Adb @('shell', 'pm', 'path', $Pacote)
$caminhos = @($saida | Where-Object { $_ -match '^package:' } | ForEach-Object { ($_ -replace '^package:', '').Trim() })
if (-not $caminhos) {
  throw "O pacote '$Pacote' não está instalado em $Serial. Instale-o pela Play Store nesse aparelho e rode de novo."
}
Write-Host "$($caminhos.Count) arquivo(s) no aparelho:" -ForegroundColor Cyan
$caminhos | ForEach-Object { Write-Host "  $_" }

# ---------------------------------------------------------------- cópia
New-Item -ItemType Directory -Force $inbox | Out-Null
$copiados = @()
foreach ($c in $caminhos) {
  $nome = Split-Path $c -Leaf
  # Dois splits nunca têm o mesmo nome; se tiverem, o sufixo evita sobrescrever em silêncio.
  $destino = Join-Path $inbox $nome
  if (Test-Path $destino) { $destino = Join-Path $inbox ("{0}-{1}{2}" -f [IO.Path]::GetFileNameWithoutExtension($nome), $copiados.Count, [IO.Path]::GetExtension($nome)) }
  Write-Host "  copiando $nome…"
  $r = Invoke-Adb @('pull', $c, $destino)
  if (-not (Test-Path $destino)) { throw "Falha ao copiar ${c}: $r" }
  $copiados += $destino
}

$total = ($copiados | ForEach-Object { (Get-Item $_).Length } | Measure-Object -Sum).Sum
Write-Host ''
Write-Host ("{0} arquivo(s), {1:N1} MB em apks\inbox" -f $copiados.Count, ($total / 1MB)) -ForegroundColor Green
$copiados | ForEach-Object { Write-Host ("  {0}  ({1:N1} MB)" -f (Split-Path $_ -Leaf), ((Get-Item $_).Length / 1MB)) }
Write-Host ''
Write-Host 'Agora importe: Aplicativos → Importar da pasta, ou' -ForegroundColor Cyan
Write-Host '  pwsh -File scripts\instagram.ps1 importar'
Write-Host 'A assinatura precisa ser aprovada de propósito antes da primeira instalação.' -ForegroundColor Yellow
