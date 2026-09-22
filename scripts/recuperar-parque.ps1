<#
.SYNOPSIS
  Recupera o parque remoto: reinicia (pelo worker) os aparelhos ligados cujo system_server do convidado
  morreu, e devolve o desfecho de cada um.

.DESCRIPTION
  Item 0.7 do plano-100 / achado #1: android-09 e android-10 ficam `online` no central mesmo com o
  system_server do Android caído (serviços settings/activity/package "not found" — sonda em 21/09, ver
  .claude/plano-100/pacotes/0.7.md). O caminho de restart-pelo-worker já existe e não precisou de código novo
  (backend/app/api.py:940-953 roteia `restart` para o worker quando o verbo está em `worker_verbs`;
  backend/app/worker/executor.py:197 executa `_v_restart`). Este script só orquestra a chamada e confere o
  desfecho — não é um caminho de execução novo.

  NÃO roda sozinho por decisão do projeto (regra 6 desta rodada: nenhuma chamada desta sessão reinicia
  emulador do parque vivo por conta própria). Requer confirmação explícita do dono (-Confirmar) antes de
  despachar qualquer coisa.

.EXAMPLE
  # Só confere se o caminho está pronto (worker vivo, fora de manutenção, aparelhos existem) — não despacha nada.
  pwsh -File scripts\recuperar-parque.ps1

  # Despacha de verdade, com autorização explícita do dono.
  pwsh -File scripts\recuperar-parque.ps1 -Confirmar
#>
[CmdletBinding()]
param(
  [string[]]$Instances = @('android-09', 'android-10'),
  [string]$Base = 'http://127.0.0.1:8000',
  [switch]$Confirmar,
  [int]$TimeoutMin = 12
)
$ErrorActionPreference = 'Stop'

Write-Host "Conferindo o parque antes de qualquer coisa (somente leitura)…"
$inst = Invoke-RestMethod "$Base/api/instances"
$workers = Invoke-RestMethod "$Base/api/workers"

$alvos = @()
foreach ($id in $Instances) {
  $rt = $inst | Where-Object id -eq $id
  if (-not $rt) { Write-Warning "${id}: não existe no central."; continue }
  if (-not $rt.worker_id) { Write-Warning "${id}: sem worker dono; 'restart' sairia local, não pelo worker."; continue }
  $w = $workers | Where-Object id -eq $rt.worker_id
  if (-not $w) { Write-Warning "${id}: worker '$($rt.worker_id)' não está no registro."; continue }
  if (-not $w.connected) { Write-Warning "${id}: worker '$($rt.worker_id)' não está conectado agora."; continue }
  if ($w.maintenance) { Write-Warning "${id}: worker '$($rt.worker_id)' está em manutenção — não recebe comando (por desenho)."; continue }
  if ('restart' -notin $w.verbs) { Write-Warning "${id}: worker '$($rt.worker_id)' não declara o verbo 'restart'."; continue }
  Write-Host ("  {0}: worker={1} state={2} verbs_ok=sim -> PRONTO" -f $id, $rt.worker_id, $rt.state)
  $alvos += $rt
}

if (-not $alvos) { Write-Host 'Nada pronto para reiniciar. Nenhuma chamada feita.'; return }

if (-not $Confirmar) {
  Write-Host "`nCaminho conferido e pronto para $(@($alvos.id) -join ', '). Nada foi despachado."
  Write-Host "Rode de novo com -Confirmar para reiniciar de verdade estes aparelhos do parque vivo — decisão do dono."
  return
}

foreach ($rt in $alvos) {
  $idem = [guid]::NewGuid().ToString()
  Write-Host "Despachando restart em $($rt.id) (idempotency_key=$idem)…"
  $body = @{ idempotency_key = $idem } | ConvertTo-Json
  $resp = Invoke-RestMethod -Method Post "$Base/api/instances/$($rt.id)/actions/restart" `
    -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($body))
  $deadline = (Get-Date).AddMinutes($TimeoutMin)
  do {
    Start-Sleep 5
    $cmd = Invoke-RestMethod "$Base/api/commands/$($resp.command_id)"
  } while ($cmd.state -in 'pending', 'dispatched', 'running' -and (Get-Date) -lt $deadline)
  Write-Host ("  {0}: comando {1} -> {2}{3}" -f $rt.id, $cmd.id, $cmd.state, $(if ($cmd.reason) { " ($($cmd.reason))" } else { '' }))
}
