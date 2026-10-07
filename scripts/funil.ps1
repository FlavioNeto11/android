<#
.SYNOPSIS
  O funil de um corte (29.196): encadeia as etapas, roda tudo sob teto de CPU (com-teto-de-cpu.ps1, 25 % por padrao) e grava um run.txt
  padronizado. PowerShell 5.1 (o pwsh 7 deste host e um app MSIX e escapa do job: nunca dentro do teto).

.DESCRIPTION
  Etapas (ids 1 a 6, as do funil da Android):
    1 scripts   scripts/tests -n 4
    2 sqlite    SQLite inteiro -n 6 (backend)
    3 frontend  typecheck + vitest + build
    4 catracas  catracas do backend e dos scripts + docs-check
    5 mypy      scripts\mypy-catraca.py (MYPY_PYTHON)
    6 pg        PG dirigido em partes (scripts\pg-rapido.py --lista <ListaPg> --partes N); sem -ListaPg a etapa e PULADA e o run.txt diz
                (pulada nunca conta como verde)

  Sem -SemTeto, o proprio funil se relanca sob com-teto-de-cpu.ps1 (-Teto 25, batimento de 30 s, teto por etapa por arquivo) e o
  encadeamento roda em `powershell.exe` 5.1 DENTRO do job: pytest, workers do xdist, node e netos ficam sob o teto. O wrapper recusa
  comando que use pwsh 7.

  Teto por etapa: `-TetoPorEtapa "pg=40,sqlite=25"` (chave ou id). Antes de cada etapa o funil escreve o percentual no arquivo de teto e o
  wrapper o aplica na hora (inclusive ao job que ja roda). Etapa sem entrada usa -Teto.

  run.txt (um registro por linha, chave=valor; datas em UTC; o detalhe de cada etapa vai para <run>.<id>-<chave>.txt):
    FUNIL inicio=... raiz=... commit=... teto=25 teto_por_etapa=... hospedeiro=5.1
    ETAPA id=2 chave=sqlite nome="sqlite inteiro -n 6" ini=... fim=... dur_s=1128 rc=0 status=ok teto=25 passed=12647 failed=0 skipped=14 errors=0
    ETAPA id=6 chave=pg nome="pg dirigido" status=pulado motivo="sem -ListaPg"
    FUNIL fim=... rc=0 ok=5 falhas=0 puladas=1
  status: ok | falhou | pulado | nao_rodou (depois de uma falha com -ParaNoErro). Codigo de saida: 0 so se nenhuma etapa falhou e
  nenhuma foi pulada ou deixada de rodar; 1 se alguma falhou; 2 se ficou so pulada/nao rodada.

  Nao chama IA, nao toca aparelho nem o banco do central; o PG dirigido mexe so no conteiner `farm-pg-rapido` (pg-rapido.py).

.PARAMETER Raiz
  Checkout a testar (padrao: o checkout onde este script esta).
.PARAMETER Saida
  Caminho do run.txt (padrao: data\funil\AAAAMMDD-HHMMSSZ-run.txt dentro da raiz).
.PARAMETER ListaPg
  Arquivo com os testes afetados para o PG dirigido; sem ele a etapa 6 e pulada.
.PARAMETER PartesPg
  Em quantas partes o pg-rapido divide a lista (padrao 2).
.PARAMETER ResumoPg
  Arquivo de resumo do pg-rapido (padrao <Saida>.pg-resumo.txt).
.PARAMETER Etapas
  Ids das etapas a rodar, separados por virgula (padrao 1,2,3,4,5,6).
.PARAMETER Teto
  Teto de CPU em % do total de threads (padrao 25).
.PARAMETER TetoPorEtapa
  `chave=pct` ou `id=pct`, separados por virgula, por exemplo `pg=40`.
.PARAMETER SemTeto
  Roda sem o wrapper (so para diagnostico; o run.txt registra teto=sem).
.PARAMETER ParaNoErro
  Para na primeira etapa que falha; as seguintes ficam `nao_rodou`.
.PARAMETER Simular
  Imprime o plano em JSON e nao roda nada.
.PARAMETER Interno
  Uso do proprio funil: o encadeamento ja esta dentro do job.
.PARAMETER ArquivoDeTeto
  Uso do proprio funil: arquivo onde o percentual de cada etapa e escrito (padrao <Saida>.teto).
.PARAMETER ComandosDeTeste
  Gancho de teste: JSON {"chave": [["exe","arg",...], ...]} com os comandos que substituem os reais de cada etapa.
.PARAMETER Python
  Python do venv do backend (padrao <Raiz>\backend\.venv\Scripts\python.exe).
.PARAMETER MypyPython
  Python do mypy (padrao C:\farm\ferramentas\mypy\Scripts\python.exe, se existir).
.PARAMETER BatimentoS
  Intervalo do batimento do wrapper (padrao 30 s).

.EXAMPLE
  powershell -NoProfile -File scripts\funil.ps1 -Raiz C:\git\android\.claude\worktrees\suite10 -ListaPg C:\tmp\pg-afetados.txt -Saida C:\tmp\funilcorte61-run.txt
#>
[CmdletBinding()]
param([string]$Raiz = '', [string]$Saida = '', [string]$ListaPg = '', [int]$PartesPg = 2, [string]$ResumoPg = '',
      [string]$Etapas = '1,2,3,4,5,6', [ValidateRange(1, 100)][int]$Teto = 25, [string]$TetoPorEtapa = '', [switch]$SemTeto,
      [switch]$ParaNoErro, [switch]$Simular, [switch]$Interno, [string]$ArquivoDeTeto = '', [string]$ComandosDeTeste = '',
      [string]$Python = '', [string]$MypyPython = '', [ValidateRange(0, 3600)][int]$BatimentoS = 30)
$ErrorActionPreference = 'Stop'
$inv = [Globalization.CultureInfo]::InvariantCulture
$esteScript = $MyInvocation.MyCommand.Path
if (-not $Raiz) { $Raiz = Split-Path -Parent (Split-Path -Parent $esteScript) }
$Raiz = [IO.Path]::GetFullPath($Raiz)
if (-not $Python) { $Python = Join-Path $Raiz 'backend\.venv\Scripts\python.exe' }
if (-not $MypyPython) { $MypyPython = 'C:\farm\ferramentas\mypy\Scripts\python.exe' }
if (-not $Saida) { $Saida = Join-Path $Raiz ('data\funil\' + (Get-Date).ToUniversalTime().ToString('yyyyMMdd-HHmmss', $inv) + 'Z-run.txt') }
$Saida = [IO.Path]::GetFullPath($Saida)
if (-not $ArquivoDeTeto) { $ArquivoDeTeto = $Saida + '.teto' }
if (-not $ResumoPg) { $ResumoPg = $Saida + '.pg-resumo.txt' }
foreach ($v in @($Raiz, $Saida, $ListaPg, $ResumoPg, $ArquivoDeTeto, $Python, $MypyPython, $ComandosDeTeste, $TetoPorEtapa, $Etapas)) {
  if ($v -match '["`$]') { throw "valor com aspas, crase ou cifrao nao e aceito: $v" }
}

# ---------------------------------------------------------------------------------------------- definicao das etapas
$definicao = @(
  @{ id = 1; chave = 'scripts';   nome = 'scripts/tests -n 4' },
  @{ id = 2; chave = 'sqlite';    nome = 'sqlite inteiro -n 6' },
  @{ id = 3; chave = 'frontend';  nome = 'frontend typecheck + vitest + build' },
  @{ id = 4; chave = 'catracas';  nome = 'catracas + docs-check' },
  @{ id = 5; chave = 'mypy';      nome = 'mypy' },
  @{ id = 6; chave = 'pg';        nome = 'pg dirigido' })
$ids = @()
foreach ($t in ($Etapas -split ',')) {
  $n = 0
  if (-not [int]::TryParse($t.Trim(), [ref]$n) -or $n -lt 1 -or $n -gt 6) { throw "-Etapas: id invalido '$t' (1 a 6)" }
  $ids += $n
}
$ids = @($ids | Sort-Object -Unique)
$tetos = @{}
if ($TetoPorEtapa) {
  foreach ($par in ($TetoPorEtapa -split ',')) {
    $kv = $par.Split('=')
    $pct = 0
    if ($kv.Count -ne 2 -or -not [int]::TryParse($kv[1].Trim(), [ref]$pct) -or $pct -lt 1 -or $pct -gt 100) { throw "-TetoPorEtapa: '$par' invalido (chave=1..100)" }
    $chave = $kv[0].Trim()
    $existe = $false
    foreach ($d in $definicao) { if ($chave -eq $d.chave -or $chave -eq [string]$d.id) { $tetos[[string]$d.id] = $pct; $existe = $true } }
    if (-not $existe) { throw "-TetoPorEtapa: etapa desconhecida '$chave'" }
  }
}
function Get-TetoDaEtapa([int]$id) { if ($tetos.ContainsKey([string]$id)) { return [int]$tetos[[string]$id] } else { return $Teto } }

# Cada comando: @{ exe; args; cwd; env }. Os reais seguem o funil da Android; o gancho de teste substitui por etapa.
$py = $Python
function New-Cmd([string]$exe, [string[]]$arg, [string]$cwd, [hashtable]$ambiente = @{}) { return @{ exe = $exe; args = $arg; cwd = $cwd; amb = $ambiente } }
$backend = Join-Path $Raiz 'backend'
$frontend = Join-Path $Raiz 'frontend'
$real = @{
  scripts  = @((New-Cmd $py @('-m', 'pytest', '-q', '-n', '4', '-p', 'no:cacheprovider', 'scripts/tests') $Raiz))
  sqlite   = @((New-Cmd $py @('-m', 'pytest', '-q', '-n', '6', '-p', 'no:cacheprovider') $backend))
  frontend = @((New-Cmd 'npm' @('run', 'typecheck') $frontend), (New-Cmd 'npm' @('test') $frontend), (New-Cmd 'npm' @('run', 'build') $frontend))
  catracas = @((New-Cmd $py @('-m', 'pytest', '-q', '-p', 'no:cacheprovider', '@tests/catracas.txt') $backend),
               (New-Cmd $py @('-m', 'pytest', '-q', '-p', 'no:cacheprovider', '@scripts/tests/catracas.txt') $Raiz),
               (New-Cmd $py @('scripts\docs-check.py') $Raiz))
  mypy     = @((New-Cmd $py @('scripts\mypy-catraca.py') $Raiz @{ MYPY_PYTHON = $MypyPython }))
  pg       = @()
}
if ($ListaPg) {
  $real['pg'] = @((New-Cmd $py @('scripts\pg-rapido.py', '--lista', $ListaPg, '--partes', [string]$PartesPg, '--saidas', ($Saida + '.pg'), '--resumo', $ResumoPg) $Raiz))
}
$comandos = $real
if ($ComandosDeTeste) {
  $injetado = Get-Content -LiteralPath $ComandosDeTeste -Raw | ConvertFrom-Json
  $comandos = @{}
  foreach ($d in $definicao) {
    $lista = @()
    $dados = $injetado.($d.chave)
    if ($null -ne $dados) { foreach ($c in @($dados)) { $v = @($c); $lista += , (New-Cmd ([string]$v[0]) ([string[]]($v | Select-Object -Skip 1)) $Raiz) } }
    $comandos[$d.chave] = $lista
  }
}

# ---------------------------------------------------------------------------------------------- plano
$hospedeiroInterno = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$wrapper = Join-Path $PSScriptRoot 'com-teto-de-cpu.ps1'
function Get-ArgumentosEncaminhados {
  $a = @('-Raiz', $Raiz, '-Saida', $Saida, '-PartesPg', [string]$PartesPg, '-ResumoPg', $ResumoPg, '-Etapas', $Etapas, '-Teto', [string]$Teto,
         '-ArquivoDeTeto', $ArquivoDeTeto, '-Python', $Python, '-MypyPython', $MypyPython)
  if ($ListaPg) { $a += @('-ListaPg', $ListaPg) }
  if ($TetoPorEtapa) { $a += @('-TetoPorEtapa', $TetoPorEtapa) }
  if ($ComandosDeTeste) { $a += @('-ComandosDeTeste', $ComandosDeTeste) }
  if ($ParaNoErro) { $a += '-ParaNoErro' }
  return $a
}
function Join-Citado([string[]]$partes) { return (($partes | ForEach-Object { if ($_ -match '\s' -or $_ -eq '') { '"' + $_ + '"' } else { $_ } }) -join ' ') }
$linhaInterna = '"' + $hospedeiroInterno + '" -NoProfile -ExecutionPolicy Bypass -File "' + $esteScript + '" -Interno ' + (Join-Citado (Get-ArgumentosEncaminhados))

if ($Simular) {
  $plano = foreach ($id in $ids) {
    $d = $definicao | Where-Object { $_.id -eq $id }
    $cmds = @($comandos[$d.chave] | ForEach-Object { (Join-Citado (@($_.exe) + @($_.args))) })
    [ordered]@{ id = $id; chave = $d.chave; nome = $d.nome; teto = $(if ($SemTeto) { 'sem' } else { Get-TetoDaEtapa $id })
                status_previsto = $(if ($cmds.Count -eq 0) { 'pulado' } else { 'roda' }); comandos = $cmds }
  }
  [ordered]@{ raiz = $Raiz; saida = $Saida; arquivo_de_teto = $ArquivoDeTeto; teto = $(if ($SemTeto) { 'sem' } else { $Teto }); para_no_erro = [bool]$ParaNoErro
              relanca_sob_wrapper = (-not $SemTeto); linha_interna = $linhaInterna; etapas = @($plano) } | ConvertTo-Json -Depth 6 -Compress
  return
}

New-Item -ItemType Directory -Force (Split-Path -Parent $Saida) | Out-Null
$utf8 = New-Object Text.UTF8Encoding($false)
function Add-Run([string]$linha) { [IO.File]::AppendAllText($Saida, $linha + "`r`n", $utf8) }
function Get-Agora { return (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ', $inv) }
try { (Get-Process -Id $PID).PriorityClass = 'Idle' } catch { }

# ---------------------------------------------------------------------------------------------- relancamento sob o teto
if (-not $Interno -and -not $SemTeto) {
  if (-not (Test-Path -LiteralPath $wrapper)) { throw "faltou $wrapper" }
  New-Item -ItemType Directory -Force (Split-Path -Parent $ArquivoDeTeto) | Out-Null
  [IO.File]::WriteAllText($ArquivoDeTeto, [string]$Teto, $utf8)
  # Chamado como script (nao como exe nativo): no 5.1 as aspas dentro de -Linha se perderiam na passagem a um processo; o wrapper roda neste
  # mesmo processo (fora do job) e e ele quem cria o job e lanca o encadeamento dentro dele.
  & $wrapper -Teto $Teto -BatimentoS $BatimentoS -ArquivoDeTeto $ArquivoDeTeto -Linha $linhaInterna *>&1 | ForEach-Object { Add-Content -LiteralPath ($Saida + '.wrapper.txt') -Value ([string]$_) -Encoding UTF8 }
  exit $LASTEXITCODE
}

# ---------------------------------------------------------------------------------------------- encadeamento (dentro do job)
function Get-Contagens([string]$texto) {
  $c = @{ passed = 0; failed = 0; skipped = 0; errors = 0 }
  foreach ($linha in ($texto -split "`r?`n")) {
    if ($linha -match '^\s*Test Files') { continue }
    $m = [regex]::Match($linha, '(\d+) passed'); if ($m.Success) { $c.passed += [int]$m.Groups[1].Value }
    $m = [regex]::Match($linha, '(\d+) failed'); if ($m.Success) { $c.failed += [int]$m.Groups[1].Value }
    $m = [regex]::Match($linha, '(\d+) skipped'); if ($m.Success) { $c.skipped += [int]$m.Groups[1].Value }
    $m = [regex]::Match($linha, '(\d+) errors?\b'); if ($m.Success) { $c.errors += [int]$m.Groups[1].Value }
  }
  return $c
}
$commit = ''
try { $commit = (& git -C $Raiz rev-parse --short HEAD 2>$null) } catch { }
$tetoDeclarado = if ($SemTeto) { 'sem' } else { [string]$Teto }
Add-Run ('FUNIL inicio={0} raiz={1} commit={2} teto={3} teto_por_etapa={4} hospedeiro={5}' -f (Get-Agora), $Raiz, $commit, $tetoDeclarado, $(if ($TetoPorEtapa) { $TetoPorEtapa } else { 'nenhum' }), $PSVersionTable.PSVersion.ToString(2))
$ok = 0; $falhas = 0; $puladas = 0; $naoRodou = 0; $parou = $false
foreach ($d in $definicao) {
  if ($ids -notcontains $d.id) { continue }
  $rotulo = 'id={0} chave={1} nome="{2}"' -f $d.id, $d.chave, $d.nome
  if ($parou) { Add-Run ("ETAPA $rotulo status=nao_rodou motivo=`"parou na falha anterior`""); $naoRodou++; continue }
  $lista = @($comandos[$d.chave])
  if ($lista.Count -eq 0) {
    $motivo = if ($d.chave -eq 'pg') { 'sem -ListaPg' } else { 'sem comando' }
    Add-Run ("ETAPA $rotulo status=pulado motivo=`"$motivo`""); $puladas++; continue
  }
  $tetoDaEtapa = Get-TetoDaEtapa $d.id
  if (-not $SemTeto) { [IO.File]::WriteAllText($ArquivoDeTeto, [string]$tetoDaEtapa, $utf8); Start-Sleep -Milliseconds 2500 }   # o wrapper le o arquivo a cada 2 s
  $detalhe = '{0}.{1}-{2}.txt' -f $Saida, $d.id, $d.chave
  Remove-Item -LiteralPath $detalhe -Force -ErrorAction SilentlyContinue
  $ini = Get-Agora; $relogio = [Diagnostics.Stopwatch]::StartNew()
  $rc = 0
  $ErrorActionPreference = 'Continue'
  foreach ($c in $lista) {
    Push-Location $c.cwd
    $guardadas = @{}
    foreach ($k in $c.amb.Keys) { $guardadas[$k] = [Environment]::GetEnvironmentVariable($k); [Environment]::SetEnvironmentVariable($k, [string]$c.amb[$k]) }
    try {
      $global:LASTEXITCODE = 0
      $argumentosDoComando = @($c.args)
      & $c.exe @argumentosDoComando 2>&1 | ForEach-Object { [string]$_ } | Out-File -LiteralPath $detalhe -Append -Encoding utf8
      $codigo = $LASTEXITCODE
      if ($null -eq $codigo) { $codigo = 0 }
    } catch { Add-Content -LiteralPath $detalhe -Value ('erro ao rodar: ' + $_.Exception.GetType().Name); $codigo = 1 }
    finally {
      foreach ($k in $guardadas.Keys) { [Environment]::SetEnvironmentVariable($k, $guardadas[$k]) }
      Pop-Location
    }
    if ($codigo -ne 0 -and $rc -eq 0) { $rc = $codigo }
  }
  $ErrorActionPreference = 'Stop'
  $texto = ''
  if (Test-Path -LiteralPath $detalhe) { $texto = Get-Content -LiteralPath $detalhe -Raw }
  $cont = Get-Contagens $texto
  $status = if ($rc -eq 0 -and $cont.failed -eq 0 -and $cont.errors -eq 0) { 'ok' } else { 'falhou' }
  Add-Run ('ETAPA {0} ini={1} fim={2} dur_s={3:F0} rc={4} status={5} teto={6} passed={7} failed={8} skipped={9} errors={10}' -f $rotulo, $ini, (Get-Agora),
           $relogio.Elapsed.TotalSeconds, $rc, $status, $(if ($SemTeto) { 'sem' } else { $tetoDaEtapa }), $cont.passed, $cont.failed, $cont.skipped, $cont.errors)
  if ($status -eq 'ok') { $ok++ } else { $falhas++; if ($ParaNoErro) { $parou = $true } }
}
$rcFinal = if ($falhas -gt 0) { 1 } elseif ($puladas -gt 0 -or $naoRodou -gt 0) { 2 } else { 0 }
Add-Run ('FUNIL fim={0} rc={1} ok={2} falhas={3} puladas={4} nao_rodou={5}' -f (Get-Agora), $rcFinal, $ok, $falhas, $puladas, $naoRodou)
exit $rcFinal
