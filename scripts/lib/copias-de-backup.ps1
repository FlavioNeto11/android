<#
.SYNOPSIS
  As regras sobre as cópias em `data\backups` que `backup.ps1` e `deploy.ps1` compartilham (item 29.38).

.DESCRIPTION
  Cada cópia é uma pasta `<AAAAMMDD-HHmmss>` com `manifesto.json`. Desde o 29.38 o manifesto diz `origem`
  (`deploy`, `ensaio`, `diario` ou `manual`) e o `commit` da árvore que fez a cópia.

  - **Teto das cópias de deploy.** Em 04/10/2026 havia 161 cópias em `data\backups`, 23 GB, quase todas de deploy:
    com dez deploys num dia, a retenção por dias (14) não segura nada. `Get-CopiasAlemDoTeto` devolve as cópias de
    deploy (e de ensaio, que são o mesmo backup feito minutos antes) que passam do teto, das mais velhas para as mais
    novas. Uma cópia SEM `origem` conta como de deploy: antes do 29.38 a tarefa diária nunca foi registrada no
    ambiente central (conferido em 04/10), então as cópias antigas vieram do deploy ou de corrida à mão. Cópia
    `diario` e `manual` não entram no teto; quem as limita é a retenção em dias.
  - **`-PularBackup` logo depois de um `-Ensaio`.** O ensaio já fez a cópia do que está no ar; refazê-la na subida
    gasta minutos sem ganhar nada. `Find-EnsaioRecente` acha a cópia de ensaio feita há no máximo N minutos NA
    MESMA árvore (commit igual): ensaio de outro commit não vale, porque o deploy seguinte pode trazer migração
    que aquele ensaio não ensaiou.

  Uso: `. (Join-Path $PSScriptRoot 'lib\copias-de-backup.ps1')`.
#>

function Read-ManifestoDeCopia([string]$pasta) {
  $arq = Join-Path $pasta 'manifesto.json'
  if (-not (Test-Path $arq)) { return $null }
  try { return Get-Content -Raw -Encoding UTF8 $arq | ConvertFrom-Json } catch { return $null }
}

function Get-CopiasDeBackup([string]$destino) {
  # Só as pastas com o carimbo exato: `20261003-120818-antes-ra20b` e os `config-antes-*.yaml` soltos são cópias
  # feitas à mão com nome escolhido, e nenhuma regra automática apaga o que alguém nomeou.
  return @(Get-ChildItem -Path $destino -Directory -ErrorAction SilentlyContinue |
           Where-Object { $_.Name -match '^\d{8}-\d{6}$' } | Sort-Object Name)
}

function Get-CopiasAlemDoTeto([string]$destino, [int]$teto, [string]$preservar = '') {
  if ($teto -le 0) { return @() }
  $deDeploy = @(Get-CopiasDeBackup $destino | Where-Object {
    $m = Read-ManifestoDeCopia $_.FullName
    $origem = if ($m -and $m.PSObject.Properties['origem']) { $m.origem } else { '' }
    $origem -in @('deploy', 'ensaio', '')
  })
  if ($deDeploy.Count -le $teto) { return @() }
  # Nunca a cópia que acabou de ser feita, mesmo que o relógio a ponha fora de ordem.
  return @($deDeploy[0..($deDeploy.Count - $teto - 1)] | Where-Object { $_.FullName -ne $preservar })
}

function Find-EnsaioRecente([string]$destino, [string]$commit, [int]$minutos) {
  if (-not $commit) { return $null }
  $corte = (Get-Date).AddMinutes(-$minutos)
  foreach ($p in @(Get-CopiasDeBackup $destino) | Sort-Object Name -Descending) {
    $m = Read-ManifestoDeCopia $p.FullName
    if (-not $m -or -not $m.PSObject.Properties['origem'] -or $m.origem -ne 'ensaio') { continue }
    if (-not $m.PSObject.Properties['commit'] -or $m.commit -ne $commit) { continue }
    # O `ts` do manifesto, e não a data da pasta: copiar ou restaurar a pasta muda a data e não muda quando o
    # backup foi feito.
    # O pwsh 7 já devolve o `ts` ISO como [datetime] no ConvertFrom-Json; o Windows PowerShell, como texto.
    $quando = if ($m.ts -is [datetime]) { $m.ts } else {
      [datetimeoffset]::Parse([string]$m.ts, [Globalization.CultureInfo]::InvariantCulture).LocalDateTime }
    if ($quando.ToLocalTime() -ge $corte) { return $p }
  }
  return $null
}
