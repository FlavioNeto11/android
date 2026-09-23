<#
.SYNOPSIS
  Achado #98/#99: rejulga com o modelo CARO (Opus 5), por imagem, as capturas de verificação que o Haiku 4.5
  já julgou desde a troca — mede a concordância dos veredictos. Não fala com o backend nem com o parque: só lê
  o banco (evidence) e chama a API da Anthropic. GASTA tokens de verdade (pede -Yes).
.EXAMPLE
  .\eval-rejudge.ps1 -Yes
  .\eval-rejudge.ps1 -Since 2026-09-19T21:42:00Z -Limit 10 -Yes
#>
[CmdletBinding()]
param(
  [string]$Since = '2026-09-19T21:42:00Z',
  [int]$Limit = 0,
  [string]$Modelo = 'claude-opus-5',
  [switch]$Yes
)
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root 'backend\.venv\Scripts\python.exe'
$args2 = @((Join-Path $PSScriptRoot 'eval_rejudge.py'), '--since', $Since, '--modelo', $Modelo)
if ($Limit) { $args2 += @('--limit', $Limit) }
if ($Yes) { $args2 += '--yes' }
$env:PYTHONIOENCODING = 'utf-8'
& $py @args2
exit $LASTEXITCODE
