<#
.SYNOPSIS
  Histórico, tag e release de cada subida do deploy nativo (29.159, candidatos 3 e 4 do comparativo da Frente DevOps).

.DESCRIPTION
  Antes disto só se sabia o estado ATUAL (`/api/health`) e o nome da pasta do backup. Não havia como responder "o que
  estava no ar antes deste deploy" nem "para que commit eu volto". Três funções, todas chamadas pelo `deploy.ps1`:

  - `Add-RegistroDeDeploy`: UMA linha JSON por subida em `data\deploys.jsonl` (fora do Git, como o resto de `data\`):
    hora UTC, resultado, commit e migração ANTES e DEPOIS, pasta do backup e a tag. Só o que o próprio deploy sabe: nada
    de segredo, de configuração nem de saída de comando (a mensagem de falha vai cortada em 300 caracteres).
  - `Get-NomeDaTagDeDeploy`: `deploy-AAAAMMDD-HHMM` (UTC); numa colisão (dois deploys no mesmo minuto) acrescenta `-2`.
  - `Publish-TagDeDeploy`: tag anotada no commit que subiu, enviada à origem, e release com as notas que o `gh` gera.
    Tudo no melhor esforço: sem `gh`, sem rede ou sem permissão, devolve um aviso e o deploy segue (a subida já deu certo;
    tag não desfaz nem atrasa nada). A tag só existe depois de conferida a subida.

  Por que a tag não dispara o `conteiner.yml`: o 29.157 fez aquele workflow disparar só em push da `main` e ignorar tags.
#>

function Get-NomeDaTagDeDeploy {
  param(
    [datetime]$UtcAgora = (Get-Date).ToUniversalTime(),
    [string[]]$Existentes = @()
  )
  $base = 'deploy-' + $UtcAgora.ToString('yyyyMMdd-HHmm', [cultureinfo]::InvariantCulture)
  if ($Existentes -notcontains $base) { return $base }
  for ($i = 2; $i -le 99; $i++) {
    $candidata = "$base-$i"
    if ($Existentes -notcontains $candidata) { return $candidata }
  }
  throw "99 tags $base já existem; algo está errado."
}

# Tempo por etapa do deploy (29.156, fatia 2): latência por etapa é métrica de primeira classe. `New-EstadoDeEtapas` guarda a
# marca de início; `Close-EtapaDoDeploy` soma ao `Nome` o tempo desde a marca anterior e move a marca (mesmo nome duas vezes
# soma). Só medição: nunca lança, nunca muda o rumo do deploy.
function New-EstadoDeEtapas {
  param([datetime]$Agora = (Get-Date))
  return @{ marca = $Agora; etapas = [ordered]@{} }
}

function Close-EtapaDoDeploy {
  param([Parameter(Mandatory)]$Estado, [Parameter(Mandatory)][string]$Nome, [datetime]$Agora = (Get-Date))
  try {
    $s = [math]::Max(0.0, ($Agora - $Estado.marca).TotalSeconds)
    $Estado.marca = $Agora
    if ($Estado.etapas.Contains($Nome)) { $Estado.etapas[$Nome] = $Estado.etapas[$Nome] + $s } else { $Estado.etapas[$Nome] = $s }
  } catch { }
}

function New-RegistroDeDeploy {
  param(
    [Parameter(Mandatory)][ValidateSet('ok', 'falhou')][string]$Resultado,
    [datetime]$UtcAgora = (Get-Date).ToUniversalTime(),
    [string]$CommitAntes,
    [string]$MigracaoAntes,
    [string]$CommitDepois,
    [string]$MigracaoDepois,
    [string]$Backup,
    [bool]$BackupDoEnsaio = $false,
    [string]$Tag,
    [string]$Motivo,
    [double]$DuracaoS = 0,
    [string[]]$Opcoes = @(),
    [System.Collections.IDictionary]$EtapasS
  )
  # A mensagem de falha não pode virar canal de saída de comando: uma linha, no máximo 300 caracteres.
  $curto = if ($Motivo) { (($Motivo -replace '\s+', ' ').Trim()) } else { $null }
  if ($curto -and $curto.Length -gt 300) { $curto = $curto.Substring(0, 300) }
  $registro = [ordered]@{
    ts_utc          = $UtcAgora.ToString("yyyy-MM-dd'T'HH:mm:ss'Z'", [cultureinfo]::InvariantCulture)
    resultado       = $Resultado
    commit_antes    = if ($CommitAntes) { $CommitAntes } else { $null }
    migracao_antes  = if ($MigracaoAntes) { $MigracaoAntes } else { $null }
    commit_depois   = if ($CommitDepois) { $CommitDepois } else { $null }
    migracao_depois = if ($MigracaoDepois) { $MigracaoDepois } else { $null }
    backup          = if ($Backup) { $Backup } else { $null }
    backup_do_ensaio = $BackupDoEnsaio
    tag             = if ($Tag) { $Tag } else { $null }
    motivo          = $curto
    duracao_s       = [math]::Round($DuracaoS, 1)
    opcoes          = @($Opcoes)
  }
  # Só aparece quando o deploy mediu etapas: o formato das linhas antigas não muda.
  if ($EtapasS -and $EtapasS.Count -gt 0) {
    $etapas = [ordered]@{}
    foreach ($k in $EtapasS.Keys) { $etapas[[string]$k] = [math]::Round([double]$EtapasS[$k], 1) }
    $registro['etapas_s'] = $etapas
  }
  return $registro
}

function Add-RegistroDeDeploy {
  param(
    [Parameter(Mandatory)][string]$Caminho,
    [Parameter(Mandatory)]$Registro
  )
  $pasta = Split-Path -Parent $Caminho
  if ($pasta -and -not (Test-Path -LiteralPath $pasta)) { New-Item -ItemType Directory -Force -Path $pasta | Out-Null }
  $linha = $Registro | ConvertTo-Json -Compress -Depth 4
  # Sem BOM e com `\n`: JSON Lines, uma linha por subida, lida por `Get-Content | ConvertFrom-Json` e por `jq`.
  [System.IO.File]::AppendAllText($Caminho, $linha + "`n", [System.Text.UTF8Encoding]::new($false))
}

function Publish-TagDeDeploy {
  param(
    [Parameter(Mandatory)][string]$Raiz,
    [Parameter(Mandatory)][string]$Commit,
    [string]$Migracao,
    [datetime]$UtcAgora = (Get-Date).ToUniversalTime()
  )
  $resultado = [ordered]@{ tag = $null; empurrada = $false; release = $false; aviso = $null }
  try {
    $existentes = @(& git -C $Raiz tag --list 'deploy-*' 2>$null)
    $tag = Get-NomeDaTagDeDeploy -UtcAgora $UtcAgora -Existentes $existentes
    & git -C $Raiz tag -a $tag $Commit -m "deploy $Commit (migração $Migracao)" 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { $resultado.aviso = "git tag falhou para $tag"; return $resultado }
    $resultado.tag = $tag
    & git -C $Raiz push origin $tag 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { $resultado.aviso = "a tag $tag ficou só local (git push falhou)"; return $resultado }
    $resultado.empurrada = $true
    if (-not (Get-Command gh -ErrorAction SilentlyContinue)) { $resultado.aviso = "sem gh: tag $tag enviada, sem release"; return $resultado }
    # `--verify-tag`: a release usa a tag que acabou de subir, não cria outra. `--latest=false`: não vira a "última".
    Push-Location $Raiz
    try { & gh release create $tag --generate-notes --verify-tag --latest=false --title $tag 2>&1 | Out-Null }
    finally { Pop-Location }
    if ($LASTEXITCODE -ne 0) { $resultado.aviso = "tag $tag enviada; o release pelo gh falhou" } else { $resultado.release = $true }
  } catch {
    $resultado.aviso = "tag e release no melhor esforço: $($_.Exception.Message)"
  }
  return $resultado
}
