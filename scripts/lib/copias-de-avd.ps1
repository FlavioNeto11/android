# Cópia a frio dos AVDs (29.39): um aparelho por vez, só hibernado ou parado, com o hash de cada arquivo conferido e
# a restauração que nunca apaga o AVD substituído. Carregado com `.` por `backup.ps1 -AVDs` e pelos testes.
#
# Não há trava que impeça o rodízio de acordar o aparelho no meio da cópia (a pausa de reparo, persistida desde o
# 25.13, segura só reinício e reset automáticos). Por isso a guarda é conferida antes de CADA arquivo e mais uma vez
# no fim: se o aparelho saiu de hibernado/parado ou um emulador abriu o AVD, a cópia para e fica marcada `abortada`
# (nada é apagado; o AVD só foi lido). Manifesto e saída nunca levam nome de conta ou de persona, só o id.

$script:EstadosParados = @('hibernated', 'stopped')

function Get-EstadoDoAparelho {
  param([string]$Id, [string]$Api = 'http://127.0.0.1:8000')
  $lista = Invoke-RestMethod -Uri "$Api/api/instances" -TimeoutSec 10
  if ($lista -isnot [array] -and $lista.instances) { $lista = $lista.instances }
  $rt = $lista | Where-Object { $_.id -eq $Id } | Select-Object -First 1
  if ($rt) { [string]$rt.state } else { 'desconhecido' }
}

function Test-AvdEmUso {
  param([string]$Id)
  $padrao = '-avd\s+' + [regex]::Escape($Id) + '(\s|$)'
  [bool](Get-CimInstance Win32_Process -Filter "Name like 'qemu-system%' or Name like 'emulator%'" |
         Where-Object { $_.CommandLine -match $padrao } | Select-Object -First 1)
}

function Get-MotivoParaNaoCopiar {
  <# `$null` = pode copiar agora. Senão, o porquê (a cópia fica para a próxima ou para no meio). #>
  param([string]$Id, [scriptblock]$LerEstado = { param($i) Get-EstadoDoAparelho $i },
        [scriptblock]$AvdEmUso = { param($i) Test-AvdEmUso $i })
  $estado = & $LerEstado $Id
  if ($estado -notin $script:EstadosParados) { return "aparelho $estado, não hibernado nem parado" }
  if (& $AvdEmUso $Id) { return 'um emulador está usando o AVD' }
  return $null
}

function Get-ArquivosDoAvd {
  <# O `.ini` e o conteúdo do `.avd`, maiores primeiro, sem as travas que o emulador cria (`*.lock`). #>
  param([string]$AvdHome, [string]$Id)
  $pastaAvd = Join-Path $AvdHome "$Id.avd"
  $ini = Get-Item -LiteralPath (Join-Path $AvdHome "$Id.ini")
  $dentro = Get-ChildItem -LiteralPath $pastaAvd -Recurse -File |
            Where-Object { $_.FullName -notmatch '\.lock(\\|$)' } | Sort-Object Length -Descending
  @([pscustomobject]@{ Item = $ini; Rel = "$Id.ini" }) + @($dentro | ForEach-Object {
      [pscustomobject]@{ Item = $_; Rel = "$Id.avd\" + $_.FullName.Substring($pastaAvd.Length).TrimStart('\') } })
}

function Copy-AvdAFrio {
  <# Copia `<AvdHome>\<Id>.avd` e `.ini` para `<Destino>\avd\<Id>\<carimbo>\`. Devolve o manifesto. #>
  param([Parameter(Mandatory)][string]$Id, [Parameter(Mandatory)][string]$AvdHome,
        [Parameter(Mandatory)][string]$Destino, [Parameter(Mandatory)][scriptblock]$Guarda,
        [string]$Commit = '', [long]$LivreMinimoBytes = 50GB)
  if (-not (Test-Path -LiteralPath (Join-Path $AvdHome "$Id.avd")) -or
      -not (Test-Path -LiteralPath (Join-Path $AvdHome "$Id.ini"))) {
    throw "o AVD de $Id não está em $AvdHome (aparelho de outra máquina?)"
  }
  New-Item -ItemType Directory -Force $Destino | Out-Null
  $livre = (Get-PSDrive -Name (Resolve-Path -LiteralPath $Destino).Drive.Name).Free
  $arquivos = Get-ArquivosDoAvd $AvdHome $Id
  $total = ($arquivos | ForEach-Object { $_.Item.Length } | Measure-Object -Sum).Sum
  if ($livre - $total -lt $LivreMinimoBytes) {
    return [ordered]@{ aparelho = $Id; estado = 'recusada'; motivo = "disco: sobrariam menos de $([math]::Round($LivreMinimoBytes / 1GB)) GB livres" }
  }
  $carimbo = Get-Date -Format 'yyyyMMdd-HHmmss'
  $pasta = Join-Path $Destino "avd\$Id\$carimbo"
  New-Item -ItemType Directory -Force $pasta | Out-Null
  $lista = [System.Collections.Generic.List[object]]::new()
  $motivo = $null
  foreach ($a in $arquivos) {
    $motivo = & $Guarda $Id
    if ($motivo) { break }
    $alvo = Join-Path $pasta $a.Rel
    New-Item -ItemType Directory -Force (Split-Path -Parent $alvo) | Out-Null
    Copy-Item -LiteralPath $a.Item.FullName -Destination $alvo
    $hOrigem = (Get-FileHash -LiteralPath $a.Item.FullName -Algorithm SHA256).Hash
    $hCopia = (Get-FileHash -LiteralPath $alvo -Algorithm SHA256).Hash
    if ($hOrigem -ne $hCopia) { $motivo = "o hash de $($a.Rel) mudou durante a cópia"; break }
    $lista.Add([ordered]@{ caminho = $a.Rel; bytes = $a.Item.Length; sha256 = $hCopia })
  }
  # O último arquivo pode ter sido lido com o aparelho já acordando: a guarda vale também depois dele.
  if (-not $motivo) { $m = & $Guarda $Id; if ($m) { $motivo = "depois do último arquivo: $m" } }
  $manifesto = [ordered]@{
    origem = 'avd-semanal'; aparelho = $Id; commit = $Commit; ts = (Get-Date).ToUniversalTime().ToString('o')
    estado = $(if ($motivo) { 'abortada' } else { 'completa' }); motivo = $motivo
    total_bytes = ($lista | ForEach-Object { $_.bytes } | Measure-Object -Sum).Sum; arquivos = $lista
  }
  $manifesto | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $pasta 'manifesto.json') -Encoding utf8
  if ($motivo) { Rename-Item -LiteralPath $pasta -NewName "$carimbo-abortada" }
  $manifesto.pasta = $(if ($motivo) { "$pasta-abortada" } else { $pasta })
  return $manifesto
}

function Restore-AvdAFrio {
  <# Volta a cópia `$Copia` para `<AvdHome>\<Id>`. O AVD atual é MOVIDO (nunca apagado) para
     `<Substituidos>\<carimbo>-avd-substituido\`. O `.ini` passa a apontar para o lugar novo. #>
  param([Parameter(Mandatory)][string]$Copia, [Parameter(Mandatory)][string]$AvdHome,
        [Parameter(Mandatory)][string]$Id, [Parameter(Mandatory)][scriptblock]$Guarda,
        [Parameter(Mandatory)][string]$Substituidos)
  $manifesto = Get-Content -LiteralPath (Join-Path $Copia 'manifesto.json') -Raw -Encoding utf8 | ConvertFrom-Json
  if ($manifesto.estado -ne 'completa') { throw "a cópia $Copia não está completa ($($manifesto.estado))" }
  foreach ($a in $manifesto.arquivos) {                      # a cópia íntegra antes de mexer em qualquer coisa
    $h = (Get-FileHash -LiteralPath (Join-Path $Copia $a.caminho) -Algorithm SHA256).Hash
    if ($h -ne $a.sha256) { throw "a cópia está corrompida em $($a.caminho)" }
  }
  $motivo = & $Guarda $Id
  if ($motivo) { throw "restauração recusada: $motivo" }
  $de = $manifesto.aparelho
  New-Item -ItemType Directory -Force $AvdHome | Out-Null
  $atualAvd, $atualIni = (Join-Path $AvdHome "$Id.avd"), (Join-Path $AvdHome "$Id.ini")
  if ((Test-Path -LiteralPath $atualAvd) -or (Test-Path -LiteralPath $atualIni)) {
    $guardado = Join-Path $Substituidos "$(Get-Date -Format 'yyyyMMdd-HHmmss')-avd-substituido"
    New-Item -ItemType Directory -Force $guardado | Out-Null
    foreach ($p in @($atualAvd, $atualIni)) { if (Test-Path -LiteralPath $p) { Move-Item -LiteralPath $p -Destination $guardado } }
  }
  foreach ($a in $manifesto.arquivos) {
    $rel = $a.caminho -replace ('^' + [regex]::Escape($de)), $Id
    $alvo = Join-Path $AvdHome $rel
    New-Item -ItemType Directory -Force (Split-Path -Parent $alvo) | Out-Null
    Copy-Item -LiteralPath (Join-Path $Copia $a.caminho) -Destination $alvo
    if ($rel -ne "$Id.ini" -and (Get-FileHash -LiteralPath $alvo -Algorithm SHA256).Hash -ne $a.sha256) {
      throw "o hash de $rel não confere depois da restauração"
    }
  }
  # O `.ini` diz onde está o `.avd` (caminho absoluto): aponta para o lugar restaurado.
  $ini = Get-Content -LiteralPath $atualIni -Encoding utf8 | ForEach-Object {
    if ($_ -match '^path=') { "path=$atualAvd" } elseif ($_ -match '^path\.rel=') { $null } else { $_ } }
  Set-Content -LiteralPath $atualIni -Value $ini -Encoding utf8
  return [ordered]@{ aparelho = $Id; de = $Copia; arquivos = @($manifesto.arquivos).Count }
}
