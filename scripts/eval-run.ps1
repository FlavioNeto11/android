<#
.SYNOPSIS
  Bateria de avaliação congelada (config\eval-set.yaml): sucesso comprovado × custo de IA por configuração.
  Uma alavanca de custo só fica se esta bateria não perder casos. Com o provedor REAL gasta tokens (pede -Yes).
.EXAMPLE
  .\eval-run.ps1 -Label opus-tudo -Yes
  .\eval-run.ps1 -Label sonnet-ator+receitas -Cases msg-qa001,msg-qa001-repeticao -Yes
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory)][string]$Label,
  [string]$Cases = '',
  [string]$Instances = '',
  [string]$Base = 'http://127.0.0.1:8000',
  [switch]$Yes
)
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root 'backend\.venv\Scripts\python.exe'
$args2 = @((Join-Path $PSScriptRoot 'eval_run.py'), '--label', $Label, '--base', $Base)
if ($Cases) { $args2 += @('--cases', $Cases) }
if ($Instances) { $args2 += @('--instances', $Instances) }
if ($Yes) { $args2 += '--yes' }
$env:PYTHONIOENCODING = 'utf-8'
& $py @args2
exit $LASTEXITCODE
