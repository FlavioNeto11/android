<#
.SYNOPSIS
  Ensaio do rollback com migração (29.156, fatia 3): prova que o código do deploy ANTERIOR abre o banco do backup que o
  runbook manda restaurar. Não para nada, não toca `data\poc.sqlite3` nem o checkout central.

.DESCRIPTION
  O runbook do § 6 de `docs/operacao.md` diz, para o deploy que trouxe migração: restaure o banco do backup da linha e volte
  o código para `commit_antes`. O que ele afirma e ninguém tinha provado é que ESSE código abre ESSE banco sem querer migrá-lo.
  Este script ensaia exatamente isso, em pasta própria, em prioridade ociosa:

  1. lê a linha `ok` mais nova de `data\deploys.jsonl` que tenha `commit_antes`, `migracao_antes` e `backup`;
  2. extrai SÓ `backend\` desse commit (`git archive`, sem mexer no checkout nem em `.git`);
  3. roda `restore.ps1` (sem `-Confirmar`) sobre a pasta do backup, numa pasta de trabalho;
  4. abre a cópia com o código antigo (`restore-ensaio-confere.py --migrar`, com o `backend` extraído) e confere: integridade,
     migração da cópia == `migracao_antes` da linha, e NENHUMA migração a aplicar (o código antigo não pode querer mexer num
     banco que era o dele);
  5. apaga a pasta de trabalho (que guarda a chave do cofre restaurada) e conferido que sumiu.

  Veredito em `data\rollback-ensaio\ultimo.json` e uma linha em `historico.jsonl` (só fatos). Saída: `ok` 0, `falhou` 1,
  `pulado` 2 (sem linha de deploy aproveitável, backup ou commit ausente: aviso, não aprovação).

.PARAMETER Raiz
  Checkout de onde ler `data\deploys.jsonl`, `data\backups` e o git (padrão: a raiz deste script).
.PARAMETER Python
  Python do venv do backend.
#>
[CmdletBinding()]
param([string]$Raiz = '', [string]$Python = '')
$ErrorActionPreference = 'Stop'
if (-not $Raiz) { $Raiz = Split-Path -Parent $PSScriptRoot }
if (-not $Python) { $Python = Join-Path $Raiz 'backend\.venv\Scripts\python.exe' }
try { (Get-Process -Id $PID).PriorityClass = 'Idle' } catch { }
$hospedeiro = (Get-Process -Id $PID).Path
$pastaDoVeredito = Join-Path $Raiz 'data\rollback-ensaio'
$pastaDeTrabalho = Join-Path $pastaDoVeredito 'trabalho'
$inicio = Get-Date

function Remove-PastaDeTrabalho([string]$caminho) {
  if (-not (Test-Path -LiteralPath $caminho)) { return $true }
  $alvo = Get-Item -LiteralPath $caminho -Force
  $dentro = $alvo.FullName.StartsWith((Get-Item -LiteralPath $pastaDeTrabalho -Force).FullName + [IO.Path]::DirectorySeparatorChar)
  if ($dentro -and -not $alvo.Attributes.HasFlag([IO.FileAttributes]::ReparsePoint)) {
    Remove-Item -LiteralPath $alvo.FullName -Recurse -Force -ErrorAction SilentlyContinue
  }
  return -not (Test-Path -LiteralPath $caminho)
}
$sobras = 0
if (Test-Path -LiteralPath $pastaDeTrabalho) {
  foreach ($antiga in Get-ChildItem -LiteralPath $pastaDeTrabalho -Directory -Force) {
    if (-not (Remove-PastaDeTrabalho $antiga.FullName)) { $sobras++ }
  }
}

$veredito = [ordered]@{
  ts_utc = $inicio.ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ'); resultado = 'falhou'; motivo = ''
  deploy_ts = ''; commit_antes = ''; migracao_antes = ''; backup = ''; integridade = ''; migracao_da_copia = ''
  migracao_do_codigo_antigo = ''; migracoes_que_o_codigo_antigo_quis_aplicar = @(); tabelas = $null; tempo_s = $null
  sobras_antigas = $sobras
}
$codigoDeSaida = 1
$trabalho = $null
$limpezaIncompleta = $false
try {
  $arquivoDeDeploys = Join-Path $Raiz 'data\deploys.jsonl'
  $linha = $null
  if (Test-Path -LiteralPath $arquivoDeDeploys) {
    foreach ($texto in (Get-Content -LiteralPath $arquivoDeDeploys -Encoding UTF8)) {
      if (-not $texto.Trim()) { continue }
      try { $d = $texto | ConvertFrom-Json } catch { continue }
      if ($d.resultado -eq 'ok' -and $d.commit_antes -and $d.migracao_antes -and $d.backup) { $linha = $d }
    }
  }
  if (-not $linha) {
    $veredito.resultado = 'pulado'; $veredito.motivo = 'não há linha ok em data\deploys.jsonl com commit_antes, migracao_antes e backup'
    $codigoDeSaida = 2
  } else {
    $tsDoDeploy = $linha.ts_utc
    if ($tsDoDeploy -is [datetime]) { $tsDoDeploy = $tsDoDeploy.ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ', [cultureinfo]::InvariantCulture) }
    $veredito.deploy_ts = [string]$tsDoDeploy; $veredito.commit_antes = [string]$linha.commit_antes
    $veredito.migracao_antes = [string]$linha.migracao_antes; $veredito.backup = [string]$linha.backup
    $pastaDoBackup = Join-Path (Join-Path $Raiz 'data\backups') ([string]$linha.backup)
    & git -C $Raiz cat-file -e "$($linha.commit_antes)^{commit}" 2>$null
    $temCommit = ($LASTEXITCODE -eq 0)
    if (-not (Test-Path -LiteralPath (Join-Path $pastaDoBackup 'poc.sqlite3'))) {
      $veredito.resultado = 'pulado'; $veredito.motivo = 'a pasta do backup da linha não existe (ou não é SQLite): foi podada?'; $codigoDeSaida = 2
    } elseif (-not $temCommit) {
      $veredito.resultado = 'pulado'; $veredito.motivo = 'o commit_antes da linha não está neste repositório'; $codigoDeSaida = 2
    } else {
      $trabalho = Join-Path $pastaDeTrabalho (Get-Date -Format 'yyyyMMdd-HHmmss')
      New-Item -ItemType Directory -Force $trabalho | Out-Null
      $zip = Join-Path $trabalho 'codigo-antigo.zip'
      & git -C $Raiz archive --format=zip -o $zip ([string]$linha.commit_antes) backend 2>$null
      if ($LASTEXITCODE -ne 0) { throw 'git archive falhou' }
      Expand-Archive -LiteralPath $zip -DestinationPath (Join-Path $trabalho 'arvore') -Force
      $saidaDoRestore = & $hospedeiro -NoProfile -File (Join-Path $PSScriptRoot 'restore.ps1') -De $pastaDoBackup -Para (Join-Path $trabalho 'banco') 2>&1
      if ($LASTEXITCODE -ne 0) {
        $veredito.motivo = "restore.ps1 falhou (código $LASTEXITCODE)"
      } else {
        $conferido = (& $Python (Join-Path $PSScriptRoot 'restore-ensaio-confere.py') (Join-Path $trabalho 'banco\poc.sqlite3') `
                       --backend (Join-Path $trabalho 'arvore\backend') --migrar 2>$null) | ConvertFrom-Json
        $veredito.integridade = if ($conferido.integridade -eq 'ok') { 'ok' } else { 'falhou' }
        $veredito.migracao_da_copia = [string]$conferido.migracao_da_copia
        $veredito.migracao_do_codigo_antigo = [string]$conferido.migracao_do_codigo
        $veredito.migracoes_que_o_codigo_antigo_quis_aplicar = @($conferido.migracoes_aplicadas_na_copia)
        $veredito.tabelas = $conferido.tabelas
        $problemas = @()
        if ($conferido.erro) { $problemas += [string]$conferido.erro }
        if ($conferido.integridade -ne 'ok') { $problemas += 'integrity_check não deu ok' }
        if ($veredito.migracao_da_copia -ne $veredito.migracao_antes) {
          $problemas += "a migração do backup ($($veredito.migracao_da_copia)) difere da migracao_antes da linha ($($veredito.migracao_antes))"
        }
        if (@($conferido.migracoes_aplicadas_na_copia).Count -gt 0) {
          $problemas += 'o código antigo quis aplicar migração no banco que era o dele (o backup não corresponde a esse código)'
        }
        if ($problemas.Count -eq 0) { $veredito.resultado = 'ok'; $codigoDeSaida = 0 } else { $veredito.motivo = $problemas -join '; ' }
      }
    }
  }
} catch {
  $veredito.resultado = 'falhou'; $veredito.motivo = 'erro no ensaio: ' + $_.Exception.GetType().Name
  $codigoDeSaida = 1
} finally {
  if ($trabalho -and -not (Remove-PastaDeTrabalho $trabalho)) { $limpezaIncompleta = $true }
}
if ($limpezaIncompleta -or $sobras -gt 0) {
  $veredito.resultado = 'falhou'; $codigoDeSaida = 1
  $veredito.motivo = (@($veredito.motivo, 'limpeza incompleta: sobrou pasta de trabalho com a chave restaurada') -ne '') -join '; '
}
$veredito.tempo_s = [math]::Round(((Get-Date) - $inicio).TotalSeconds, 1)
New-Item -ItemType Directory -Force $pastaDoVeredito | Out-Null
$semBom = New-Object Text.UTF8Encoding($false)
[IO.File]::WriteAllText((Join-Path $pastaDoVeredito 'ultimo.json'), ($veredito | ConvertTo-Json -Depth 4), $semBom)
[IO.File]::AppendAllText((Join-Path $pastaDoVeredito 'historico.jsonl'), (($veredito | ConvertTo-Json -Depth 4 -Compress) + "`n"), $semBom)
Write-Host ("rollback-ensaio: {0} | deploy {1} | código {2} | migração {3} | {4} s{5}" -f $veredito.resultado, $veredito.deploy_ts,
            ([string]$veredito.commit_antes).Substring(0, [math]::Min(8, ([string]$veredito.commit_antes).Length)),
            $veredito.migracao_da_copia, $veredito.tempo_s, $(if ($veredito.motivo) { ' | ' + $veredito.motivo } else { '' }))
exit $codigoDeSaida
