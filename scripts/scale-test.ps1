<#
.SYNOPSIS
  Teste de escala gradual (1 → 2 → 5 → 10 → 14 instâncias) usando a API do backend. Em cada degrau:
  inicia as instâncias que faltam, espera ficarem online (ou o backend recusar por falta de memória),
  provisiona o app de QA, mede memória/CPU reais e executa o comando de demonstração em todas as online.
  Para no primeiro degrau que o hardware não sustenta e registra a capacidade medida.
  Resultado: data\scale-test-results.json (também aparece em Diagnóstico).
.DESCRIPTION
  Item 10.3 (achado #146): "10 instâncias simultâneas" nunca tinha sido demonstrado em NENHUMA combinação — o
  degrau máximo antigo (10) parava exatamente onde os 4 locais + 6 remotos deveriam se encontrar, mas sem cobrir
  o parque completo. O alvo agora vai até 14: os 15 slots configurados (`instances.count`) MENOS `android-11`
  (a loja, propositalmente fora de um teste de escala genérico). Quem entra em cada degrau vem de
  `GET /api/instances`, não de um id gerado por número (achado #153): locais primeiro, depois os de worker.
  `-Onde local` mede só o host desta máquina (o único cuja RAM este script consegue medir), `-Onde worker:<id>`
  só os de um servidor, `-Onde todos` (padrão) o parque inteiro menos a loja.
#>
[CmdletBinding()]
param(
  [string]$Steps = '1,2,5,10,14',   # texto para funcionar também com `pwsh -File` (que não converte "1,2,5" em int[])
  [string]$Base = 'http://127.0.0.1:8000',
  [int]$BootTimeoutSec = 900,
  [switch]$SkipRun,
  [switch]$StopAtEnd,
  # Achado #153: quem participa do teste. `local` = só os emuladores DESTA máquina (os únicos cuja RAM este
  # script mede); `worker:<id>` = só os de um servidor; `todos` = o parque inteiro, menos a loja.
  [string]$Onde = 'todos'
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$out = Join-Path $root 'data\scale-test-results.json'
$results = @()
$ai = Invoke-RestMethod "$Base/api/ai"

# Invoke-RestMethod devolve o array JSON como UM objeto; os parênteses forçam a enumeração no pipeline
function Get-Instances { (Invoke-RestMethod "$Base/api/instances") | ForEach-Object { $_ } }

# O parque vem de GET /api/instances, não de `android-{0:d2}` de 1..N (achado #153): no dia em que 09/10 e
# 12..15 viraram aparelhos de OUTRA máquina, o número virou mentira — e `-StopAtEnd` mandava `stop` aos remotos
# pelo worker enquanto media só a RAM local. A loja (`kind = 'store'`) nunca entra: não é aparelho de tarefa.
# A ordem é local primeiro, depois remoto, para que um alvo pequeno continue testando só o host, como sempre.
function Get-Alvos {
  $todas = @(Get-Instances | Where-Object { $_.kind -ne 'store' })
  switch -Regex ($Onde) {
    '^local$' { $todas = @($todas | Where-Object { -not $_.worker_id }) }
    '^worker:(.+)$' { $w = $Matches[1]; $todas = @($todas | Where-Object { $_.worker_id -eq $w }) }
    '^todos$' { }
    default { throw "-Onde aceita 'local', 'todos' ou 'worker:<id>' — recebi '$Onde'." }
  }
  if (-not $todas.Count) { throw "Nenhum aparelho casa com -Onde '$Onde' em $Base/api/instances." }
  @($todas | Sort-Object { if ($_.worker_id) { 1 } else { 0 } }, id | ForEach-Object { $_.id })
}
$Alvos = Get-Alvos
Write-Host ("Alvos (-Onde $Onde): " + ($Alvos -join ', '))
$stepList = $Steps -split '[,; ]+' | Where-Object { $_ } | ForEach-Object { [int]$_ } | Where-Object { $_ -ge 1 -and $_ -le $Alvos.Count }
foreach ($target in $stepList) {
  $ids = @($Alvos | Select-Object -First $target)
  $before = Get-Instances
  $toStart = $before | Where-Object { $ids -contains $_.id -and $_.state -notin 'online', 'booting' } | ForEach-Object id
  $t0 = Get-Date
  if ($toStart) {
    $null = Invoke-RestMethod -Method Post "$Base/api/instances/bulk" -ContentType 'application/json' -Body (@{ ids = @($toStart); action = 'start' } | ConvertTo-Json)
    Write-Host "[$target] iniciando: $($toStart -join ', ')"
  }
  $deadline = (Get-Date).AddSeconds($BootTimeoutSec)
  do {
    Start-Sleep 5
    $now = Get-Instances | Where-Object { $ids -contains $_.id }
    $online = @($now | Where-Object state -eq 'online')
    $pending = @($now | Where-Object { $_.state -eq 'booting' })
    Write-Host ("[{0}] online={1} booting={2} ({3:N0}s)" -f $target, $online.Count, $pending.Count, ((Get-Date) - $t0).TotalSeconds)
  } while ($pending.Count -gt 0 -and (Get-Date) -lt $deadline)
  $refused = @($now | Where-Object { $_.state -ne 'online' })
  Start-Sleep 30   # assentar antes de medir
  $m = Invoke-RestMethod "$Base/api/metrics"
  $now = Get-Instances | Where-Object { $ids -contains $_.id }
  $online = @($now | Where-Object state -eq 'online')
  $step = [ordered]@{
    target = $target; online = $online.Count; ts = (Get-Date).ToString('s')
    boot_wall_seconds = [math]::Round(((Get-Date) - $t0).TotalSeconds - 30)
    boot_seconds_each = @($online | ForEach-Object { [ordered]@{ id = $_.id; boot_seconds = $_.boot_seconds } })
    not_online = @($refused | ForEach-Object { [ordered]@{ id = $_.id; state = $_.state; detail = $_.state_detail } })
    host_cpu_percent = $m.cpu_percent; mem_available_gb = $m.mem_available_gb; mem_used_percent = $m.mem_used_percent
    emulator_rss_mb = @($m.emulators | ForEach-Object { [ordered]@{ id = $_.instance_id; rss_mb = $_.rss_mb; cpu = $_.cpu_percent } })
    ai_provider = $ai.provider; ai_simulated = $ai.simulated
  }
  if (-not $SkipRun -and $online.Count -gt 0) {
    & (Join-Path $PSScriptRoot 'provision-qa.ps1') -Instances ($online.id) -Base $Base | Out-Host
    # aguarda a sessão de automação de todas
    for ($n = 0; $n -lt 60; $n++) { $ready = @(Get-Instances | Where-Object { $online.id -contains $_.id -and $_.automation.state -eq 'ready' }); if ($ready.Count -eq $online.Count) { break }; Start-Sleep 5 }
    $r0 = Get-Date
    & (Join-Path $PSScriptRoot 'demo-run.ps1') -Instances ($online.id) -Base $Base -TimeoutSec 1500 | Out-Host
    $rep = Get-Content (Join-Path $root 'data\last-run-report.json') -Raw | ConvertFrom-Json
    $m2 = Invoke-RestMethod "$Base/api/metrics"
    $step.run = [ordered]@{ id = $rep.run.id; status = $rep.run.status; seconds = [math]::Round(((Get-Date) - $r0).TotalSeconds)
      totals = $rep.totals; simulated = $rep.run.simulated; host_cpu_percent_after = $m2.cpu_percent; mem_available_gb_after = $m2.mem_available_gb }
  }
  $results += $step
  $results | ConvertTo-Json -Depth 8 | Set-Content -Encoding utf8 $out
  if ($online.Count -lt $target) {
    Write-Warning "Degrau $target não sustentado: $($online.Count) online. Motivo(s): $(($refused | ForEach-Object { "$($_.id): $($_.state_detail)" }) -join ' | ')"
    break
  }
}
Write-Host "Resultados em $out"
if ($StopAtEnd) { $null = Invoke-RestMethod -Method Post "$Base/api/instances/bulk" -ContentType 'application/json' -Body (@{ ids = $Alvos; action = 'stop' } | ConvertTo-Json) }
