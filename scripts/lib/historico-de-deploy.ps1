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

# Notas de release do deploy (29.156, fatia 5): o que o CHANGELOG registrou desde o deploy anterior, em vez da lista de PRs que o
# `gh --generate-notes` monta (os cortes entram por commit direto e a lista sai pobre). Texto SEM dado de máquina nem segredo.
function Remove-DadosDaMaquina {
  param([string]$Texto)
  $t = [string]$Texto
  $t = [regex]::Replace($t, '\b\d{1,3}(\.\d{1,3}){3}\b', '<ip>')
  $t = [regex]::Replace($t, '\bWIN-[A-Za-z0-9]{6,}\b', '<máquina>')
  $t = [regex]::Replace($t, '\bworker-[a-z]+-\d+\b', '<worker>')
  $t = [regex]::Replace($t, '\b[A-Za-z]:\\[^\s`''")]+', '<caminho>')
  $t = [regex]::Replace($t, '[\w.+-]+@[\w-]+(\.[\w-]+)+', '<e-mail>')
  $t = [regex]::Replace($t, '\b(sk-|ghp_|gho_|ghs_|github_pat_|xox[abp]-|AIza)[A-Za-z0-9_\-]{10,}', '<segredo>')
  # Sequência longa com maiúscula, minúscula E dígito (chave em base64/base64url); um SHA de commit (hex puro) e um nome de branch
  # em minúsculas (`feat/adr-081-...`) são públicos e ficam.
  $t = [regex]::Replace($t, '[A-Za-z0-9+/_\-]{40,}={0,2}', {
      param($m)
      if ($m.Value -cmatch '[A-Z]' -and $m.Value -cmatch '[a-z]' -and $m.Value -match '\d') { '<token>' } else { $m.Value }
    })
  return $t
}

function New-NotasDeRelease {
  param(
    [Parameter(Mandatory)][string]$Raiz,
    [Parameter(Mandatory)][string]$Tag,
    [Parameter(Mandatory)][string]$Commit,
    [string]$CommitAnterior,
    [string]$Migracao,
    [string]$MigracaoAnterior
  )
  # Sem o deploy anterior não há o que comparar: devolve $null e o chamador usa as notas geradas pelo `gh`.
  if (-not $CommitAnterior) { return $null }
  $saidaAntes = [Console]::OutputEncoding
  try {
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
    $antigos = @(& git -C $Raiz show "${CommitAnterior}:CHANGELOG.md" 2>$null | Where-Object { $_ -like '## *' })
    if ($LASTEXITCODE -ne 0) { return $null }
    $atuais = @(& git -C $Raiz show "${Commit}:CHANGELOG.md" 2>$null | Where-Object { $_ -like '## *' })
    if ($LASTEXITCODE -ne 0) { return $null }
    $jaTinha = [System.Collections.Generic.HashSet[string]]::new([string[]]$antigos)
    $novos = @($atuais | Where-Object { -not $jaTinha.Contains($_) })
    $nCommits = (& git -C $Raiz rev-list --count "${CommitAnterior}..${Commit}" 2>$null)
    $curto = $Commit.Substring(0, [math]::Min(8, $Commit.Length))
    $curtoAntes = $CommitAnterior.Substring(0, [math]::Min(8, $CommitAnterior.Length))
    $migracaoTexto = if ($MigracaoAnterior -and $Migracao -and $MigracaoAnterior -ne $Migracao) { "migração ``$MigracaoAnterior`` → ``$Migracao``" }
                     elseif ($Migracao) { "migração ``$Migracao`` (sem migração nova)" } else { 'migração desconhecida' }
    $l = [System.Collections.Generic.List[string]]::new()
    $l.Add("Deploy ``$Tag`` · commit ``$curto`` · $migracaoTexto")
    $l.Add('')
    $l.Add("### Registrado no CHANGELOG desde o deploy anterior ($($novos.Count))")
    $l.Add('')
    if ($novos.Count -eq 0) { $l.Add('Nenhuma entrada nova no CHANGELOG nesta subida; o que mudou está nos commits abaixo.') }
    foreach ($h in ($novos | Select-Object -First 40)) {
      $t = Remove-DadosDaMaquina (($h -replace '^##\s+', '').Trim())
      if ($t.Length -gt 220) { $t = $t.Substring(0, 220) + '…' }
      $l.Add("- $t")
    }
    if ($novos.Count -gt 40) { $l.Add("- … e mais $($novos.Count - 40) entradas") }
    $l.Add('')
    $l.Add('### Commits')
    $l.Add('')
    $linhaDosCommits = "$nCommits commits desde ``$curtoAntes``."
    # Só `dono/repositório`: a URL da origem pode trazer credencial e nunca vai ao texto.
    $origem = (& git -C $Raiz remote get-url origin 2>$null)
    if ($origem -match 'github\.com[:/]([^/\s@]+)/([^/\s]+?)(\.git)?\s*$') {
      $linhaDosCommits += " Comparar: https://github.com/$($Matches[1])/$($Matches[2])/compare/$CommitAnterior...$Commit"
    }
    $l.Add($linhaDosCommits)
    return ($l -join "`n")
  } catch {
    return $null
  } finally {
    [Console]::OutputEncoding = $saidaAntes
  }
}

function Publish-TagDeDeploy {
  param(
    [Parameter(Mandatory)][string]$Raiz,
    [Parameter(Mandatory)][string]$Commit,
    [string]$Migracao,
    [datetime]$UtcAgora = (Get-Date).ToUniversalTime(),
    [string]$CommitAnterior,
    [string]$MigracaoAnterior
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
    # Notas do CHANGELOG quando há deploy anterior para comparar; senão, as que o `gh` gera (comportamento de antes).
    $notas = New-NotasDeRelease -Raiz $Raiz -Tag $tag -Commit $Commit -CommitAnterior $CommitAnterior -Migracao $Migracao -MigracaoAnterior $MigracaoAnterior
    $arquivoDeNotas = $null
    Push-Location $Raiz
    try {
      if ($notas) {
        $arquivoDeNotas = Join-Path ([IO.Path]::GetTempPath()) ("notas-$tag-" + [Guid]::NewGuid().ToString('N') + '.md')
        [IO.File]::WriteAllText($arquivoDeNotas, $notas, [System.Text.UTF8Encoding]::new($false))
        & gh release create $tag --notes-file $arquivoDeNotas --verify-tag --latest=false --title $tag 2>&1 | Out-Null
      } else {
        & gh release create $tag --generate-notes --verify-tag --latest=false --title $tag 2>&1 | Out-Null
      }
    }
    finally {
      Pop-Location
      if ($arquivoDeNotas) { Remove-Item -LiteralPath $arquivoDeNotas -Force -ErrorAction SilentlyContinue }
    }
    if ($LASTEXITCODE -ne 0) { $resultado.aviso = "tag $tag enviada; o release pelo gh falhou" } else { $resultado.release = $true }
  } catch {
    $resultado.aviso = "tag e release no melhor esforço: $($_.Exception.Message)"
  }
  return $resultado
}
