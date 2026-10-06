<#
.SYNOPSIS
  Ensaio semanal de restauração (29.167): prova que a cópia mais nova de `data\backups` abre, está íntegra, tem a migração
  que o manifesto diz e aceita a migração do código atual. Não toca `data\poc.sqlite3`, o relógio, o túnel nem o WSL.

.DESCRIPTION
  Backup que nunca foi restaurado é suposição. Este script faz, em prioridade ociosa e numa pasta de trabalho própria
  (`data\restore-ensaio\trabalho\<carimbo>`, apagada no fim):

  1. escolhe a cópia SQLite mais nova de `data\backups` (pasta `<AAAAMMDD-HHmmss>` com `manifesto.json`);
  2. roda `restore.ps1 -De <cópia> -Para <trabalho>` SEM `-Confirmar` (só copia para a pasta limpa e confere);
  3. confere na cópia: integridade, migração igual à do manifesto, número de tabelas igual ao do manifesto e, a menos que
     `-SemMigrar`, aplica a migração do código ATUAL na cópia (é o ensaio de migração sobre dados reais; o banco vivo
     não é tocado);
  4. grava o veredito em `data\restore-ensaio\ultimo.json` e uma linha em `historico.jsonl` (só fatos: carimbo da cópia,
     commit, tempos, contagens; nenhum valor de tabela, nenhum segredo, nenhum caminho de usuário).

  Veredito e saída: `ok` (0), `falhou` (1) ou `pulado` (2: sem cópia SQLite, ou a mais nova é de PostgreSQL). Também falha
  quando a cópia mais nova tem mais de 48 h: é o sinal de que a tarefa diária `farm-backup` parou.

  AVISO PELO CANAL: este script NÃO manda Telegram. O canal de aviso (docs/operacao.md § 15) só aceita os tipos de evento
  montados dentro do backend, e não há rota para um script do host enfileirar aviso. Um veredito `falhou` fica em
  `ultimo.json` e no código de saída da tarefa agendada; ligar isso ao canal é um item do backend (ver o resultado do 29.167).

.PARAMETER Destino
  Pasta das cópias (padrão `data\backups`).
.PARAMETER Python
  Python do venv do backend (padrão `backend\.venv\Scripts\python.exe`).
.PARAMETER Backend
  Pasta `backend` cujo `app.db` e `migrations` valem para o ensaio de migração (padrão `backend`).
.PARAMETER SemMigrar
  Não aplica a migração do código na cópia (só integridade, migração e tabelas do manifesto).
.PARAMETER Instalar
  Registra a tarefa agendada `farm-restore-ensaio` (semanal, domingo 04:30, prioridade ociosa) e sai. Não roda o ensaio.

.EXAMPLE
  pwsh -File scripts\restore-ensaio.ps1             # o ensaio agora
  pwsh -File scripts\restore-ensaio.ps1 -Instalar   # registra a tarefa semanal
#>
[CmdletBinding()]
param([string]$Destino = '', [string]$Python = '', [string]$Backend = '', [switch]$SemMigrar, [switch]$Instalar)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
if (-not $Destino) { $Destino = Join-Path $root 'data\backups' }
if (-not $Python)  { $Python  = Join-Path $root 'backend\.venv\Scripts\python.exe' }
if (-not $Backend) { $Backend = Join-Path $root 'backend' }
$tarefa = 'farm-restore-ensaio'
. (Join-Path $PSScriptRoot 'lib\copias-de-backup.ps1')

if ($Instalar) {
  $script = $MyInvocation.MyCommand.Path
  $argumentos = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`""
  $exe = (Get-Command pwsh -ErrorAction SilentlyContinue).Source
  if (-not $exe) { $exe = 'powershell.exe' }
  $eu = ([Security.Principal.WindowsIdentity]::GetCurrent()).Name
  Unregister-ScheduledTask -TaskName $tarefa -Confirm:$false -ErrorAction SilentlyContinue
  $acao = New-ScheduledTaskAction -Execute $exe -Argument $argumentos
  $gatilho = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 04:30
  $principal = New-ScheduledTaskPrincipal -UserId $eu -LogonType S4U -RunLevel Highest
  $ajustes = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
               -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 1)
  Register-ScheduledTask -TaskName $tarefa -Action $acao -Trigger $gatilho -Principal $principal -Settings $ajustes | Out-Null
  Write-Host "tarefa '$tarefa' registrada (semanal, domingo 04:30, prioridade ociosa). O ensaio NÃO foi rodado."
  return
}

try { (Get-Process -Id $PID).PriorityClass = 'Idle' } catch { }
$pastaDoVeredito = Join-Path $root 'data\restore-ensaio'
$pastaDeTrabalho = Join-Path $pastaDoVeredito 'trabalho'
$hospedeiro = (Get-Process -Id $PID).Path   # o mesmo pwsh que roda a tarefa chama o restore.ps1

# Apaga uma pasta de trabalho (ela guarda a chave do cofre restaurada): só se estiver DENTRO de `trabalho\` e não for
# junção. Devolve $true se não sobrou nada.
function Remove-PastaDeTrabalho([string]$caminho) {
  if (-not (Test-Path -LiteralPath $caminho)) { return $true }
  $alvo = Get-Item -LiteralPath $caminho -Force
  $dentro = $alvo.FullName.StartsWith((Get-Item -LiteralPath $pastaDeTrabalho -Force).FullName + [IO.Path]::DirectorySeparatorChar)
  if ($dentro -and -not $alvo.Attributes.HasFlag([IO.FileAttributes]::ReparsePoint)) {
    Remove-Item -LiteralPath $alvo.FullName -Recurse -Force -ErrorAction SilentlyContinue
  }
  return -not (Test-Path -LiteralPath $caminho)
}
# Sobra de um ensaio anterior que morreu antes do `finally` (limite de tempo da tarefa, queda do processo).
$sobras = 0
if (Test-Path -LiteralPath $pastaDeTrabalho) {
  foreach ($antiga in Get-ChildItem -LiteralPath $pastaDeTrabalho -Directory -Force) {
    if (-not (Remove-PastaDeTrabalho $antiga.FullName)) { $sobras++ }
  }
}
$inicio = Get-Date
$veredito = [ordered]@{
  ts_utc = $inicio.ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ'); resultado = 'falhou'; motivo = ''
  copia = ''; origem = ''; commit_da_copia = ''; idade_h = $null; bytes = $null; tempo_s = $null
  integridade = ''; migracao_da_copia = ''; migracao_do_manifesto = ''; migracao_do_codigo = ''
  migracoes_aplicadas_na_copia = @(); tabelas = $null; tabelas_do_manifesto = $null; sobras_antigas = $sobras
}
$codigoDeSaida = 1
$trabalho = $null
$limpezaIncompleta = $false
try {
  $copias = @(Get-CopiasDeBackup $Destino | Where-Object {
    (Test-Path -LiteralPath (Join-Path $_.FullName 'manifesto.json')) -and
    ((Test-Path -LiteralPath (Join-Path $_.FullName 'poc.sqlite3')) -or (Test-Path -LiteralPath (Join-Path $_.FullName 'parque.dump')))
  })
  if ($copias.Count -eq 0) {
    $veredito.resultado = 'pulado'; $veredito.motivo = 'não há cópia com manifesto em data\backups'; $codigoDeSaida = 2
  } else {
    $maisNova = $copias[-1]
    $manifesto = Read-ManifestoDeCopia $maisNova.FullName
    $veredito.copia = $maisNova.Name
    $manifestoCompleto = $manifesto -and $manifesto.PSObject.Properties['migration'] -and $manifesto.PSObject.Properties['tables']
    $veredito.origem = if ($manifesto -and $manifesto.PSObject.Properties['origem']) { [string]$manifesto.origem } else { '' }
    $veredito.commit_da_copia = if ($manifesto -and $manifesto.PSObject.Properties['commit']) { [string]$manifesto.commit } else { '' }
    $veredito.migracao_do_manifesto = if ($manifesto -and $manifesto.PSObject.Properties['migration']) { [string]$manifesto.migration } else { '' }
    $veredito.tabelas_do_manifesto = if ($manifesto -and $manifesto.PSObject.Properties['tables']) { [int]$manifesto.tables } else { $null }
    if ($manifesto -and $manifesto.PSObject.Properties['bytes']) { $veredito.bytes = [int64]$manifesto.bytes }
    $quando = $maisNova.CreationTime
    if ($manifesto -and $manifesto.PSObject.Properties['ts']) {
      $quando = if ($manifesto.ts -is [datetime]) { $manifesto.ts } else {
        [datetimeoffset]::Parse([string]$manifesto.ts, [Globalization.CultureInfo]::InvariantCulture).LocalDateTime }
    }
    $veredito.idade_h = [math]::Round(((Get-Date) - $quando.ToLocalTime()).TotalHours, 1)
    if (-not (Test-Path -LiteralPath (Join-Path $maisNova.FullName 'poc.sqlite3'))) {
      $veredito.resultado = 'pulado'; $veredito.motivo = 'a cópia mais nova não é SQLite (PostgreSQL usa pg_restore, não ensaiado aqui)'
      $codigoDeSaida = 2
    } else {
      $trabalho = Join-Path $pastaDeTrabalho (Get-Date -Format 'yyyyMMdd-HHmmss')
      $saidaDoRestore = & $hospedeiro -NoProfile -File (Join-Path $PSScriptRoot 'restore.ps1') -De $maisNova.FullName -Para $trabalho 2>&1
      if ($LASTEXITCODE -ne 0) {
        $veredito.motivo = "restore.ps1 falhou (código $LASTEXITCODE)"
      } else {
        $argumentos = @((Join-Path $PSScriptRoot 'restore-ensaio-confere.py'), (Join-Path $trabalho 'poc.sqlite3'), '--backend', $Backend)
        if (-not $SemMigrar) { $argumentos += '--migrar' }
        $conferido = (& $Python @argumentos 2>$null) | ConvertFrom-Json
        $veredito.integridade = if ($conferido.integridade -eq 'ok') { 'ok' } else { 'falhou' }
        $veredito.migracao_da_copia = [string]$conferido.migracao_da_copia
        $veredito.migracao_do_codigo = [string]$conferido.migracao_do_codigo
        $veredito.migracoes_aplicadas_na_copia = @($conferido.migracoes_aplicadas_na_copia)
        $veredito.tabelas = $conferido.tabelas
        $problemas = @()
        if (-not $manifestoCompleto) { $problemas += 'manifesto ilegível ou sem migration/tables: não há contra o que conferir' }
        if ($conferido.erro) { $problemas += [string]$conferido.erro }
        if ($conferido.integridade -ne 'ok') { $problemas += 'integrity_check não deu ok' }
        if ($veredito.migracao_do_manifesto -and $veredito.migracao_da_copia -ne $veredito.migracao_do_manifesto) {
          $problemas += "migração da cópia ($($veredito.migracao_da_copia)) difere da do manifesto ($($veredito.migracao_do_manifesto))"
        }
        if ($null -ne $veredito.tabelas_do_manifesto -and $conferido.tabelas -ne $veredito.tabelas_do_manifesto) {
          $problemas += "tabelas da cópia ($($conferido.tabelas)) diferem das do manifesto ($($veredito.tabelas_do_manifesto))"
        }
        if ($veredito.idade_h -gt 48) {
          $problemas += "a cópia mais nova tem $($veredito.idade_h) h (mais de 48 h: a tarefa diária farm-backup parou?)"
        }
        if ($problemas.Count -eq 0) { $veredito.resultado = 'ok'; $codigoDeSaida = 0 } else { $veredito.motivo = $problemas -join '; ' }
      }
    }
  }
} catch {
  $veredito.resultado = 'falhou'; $veredito.motivo = 'erro no ensaio: ' + $_.Exception.GetType().Name
  $codigoDeSaida = 1
} finally {
  # Só a pasta que ESTE ensaio criou (a cópia traz a chave do cofre restaurada): apagar e CONFERIR que sumiu.
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
Write-Host ("restore-ensaio: {0} | cópia {1} ({2} h) | migração {3} | tabelas {4} | {5} s{6}" -f $veredito.resultado,
            $veredito.copia, $veredito.idade_h, $veredito.migracao_da_copia, $veredito.tabelas, $veredito.tempo_s,
            $(if ($veredito.motivo) { ' | ' + $veredito.motivo } else { '' }))
exit $codigoDeSaida
