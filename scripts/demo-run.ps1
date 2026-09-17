<#
.SYNOPSIS
  Envia um comando em linguagem natural ao backend e acompanha a execução até o fim, imprimindo o
  relatório individual por instância. Usa a mesma API que o painel.
.EXAMPLE
  .\demo-run.ps1 -Instances android-01,android-02 -Command 'Abra o QA Messenger, entre na conversa com QA-001 e envie "Teste POC {instance_id} {run_id}". Confirme que apareceu como enviada.'
#>
[CmdletBinding()]
param(
  [string[]]$Instances = @('android-01'),
  [string]$Command = 'Nas instâncias selecionadas, abra o QA Messenger, entre na conversa com o contato de teste identificado como QA-001 e envie "Teste POC {instance_id} {run_id}". Confirme que a mensagem apareceu como enviada em cada conta e apresente o resultado individual.',
  [ValidateSet('execute', 'plan')][string]$Mode = 'execute',
  [string]$Base = 'http://127.0.0.1:8000',
  [int]$TimeoutSec = 900,
  [string]$IdempotencyKey = [guid]::NewGuid().ToString()
)
$ErrorActionPreference = 'Stop'
$Instances = @($Instances | ForEach-Object { $_ -split '[,; ]+' } | Where-Object { $_ })   # aceita "a,b,c" vindo de `pwsh -File`
$body = @{ command = $Command; instance_ids = $Instances; idempotency_key = $IdempotencyKey; mode = $Mode } | ConvertTo-Json
$run = Invoke-RestMethod -Method Post -Uri "$Base/api/runs" -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($body))
Write-Host "Execução $($run.id) criada (deduplicated=$($run.deduplicated); simulada=$($run.simulated))"
$deadline = (Get-Date).AddSeconds($TimeoutSec)
$last = ''
do {
  Start-Sleep -Seconds 2
  $r = Invoke-RestMethod "$Base/api/runs/$($run.id)"
  $c = $r.counts
  $line = "[$($r.status)] sucesso=$($c.succeeded) falha=$($c.failed) bloqueio=$($c.waiting_user) incerto=$($c.uncertain) andamento=$($c.running) pendente=$($c.pending)"
  if ($line -ne $last) { Write-Host "$(Get-Date -Format HH:mm:ss) $line"; $last = $line }
} while ($r.status -in 'planning', 'running', 'cancelling' -and (Get-Date) -lt $deadline)
$rep = Invoke-RestMethod "$Base/api/runs/$($run.id)/report"
Write-Host ''
Write-Host $rep.markdown
$rep | ConvertTo-Json -Depth 8 | Set-Content -Encoding utf8 (Join-Path (Split-Path -Parent $PSScriptRoot) "data\last-run-report.json")
