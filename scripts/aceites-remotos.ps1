<#
.SYNOPSIS
  Item T.1 (aceites 1, 6 e 8): roda `stop → start → hibernate → wake → restart` num aparelho LOCAL e num
  REMOTO pela mesma rota do painel, espera o desfecho de cada comando e imprime a tabela com os `command_id`
  para colar em docs/relatorio-validacao.md §13.
.DESCRIPTION
  Sem -Yes ele NÃO despacha nada: só mostra o roteiro. `reset` (que apaga os dados do aparelho) só entra com
  -ComReset. O token da API, quando houver, vem de API_TOKEN no ambiente e não é impresso.
.EXAMPLE
  .\aceites-remotos.ps1 -Local android-01 -Remoto android-13
  .\aceites-remotos.ps1 -Local android-01 -Remoto android-13 -Yes
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$Local,
  [Parameter(Mandatory = $true)][string]$Remoto,
  [string]$Base = 'http://127.0.0.1:8000',
  [switch]$ComReset,
  [switch]$Yes
)
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root 'backend\.venv\Scripts\python.exe'
$argumentos = @((Join-Path $PSScriptRoot 'aceites_remotos.py'), '--base', $Base, '--local', $Local, '--remoto', $Remoto)
if ($ComReset) { $argumentos += '--com-reset' }
if ($Yes) { $argumentos += '--yes' }
$env:PYTHONIOENCODING = 'utf-8'
& $py @argumentos
exit $LASTEXITCODE
