<#
.SYNOPSIS
  Teste integrado do RODÍZIO: N contas atendidas com K vagas de RAM (liga sob demanda → executa → hiberna).
  1) prepara cada instância em lotes de K (cria AVD se faltar, instala o app de QA, conecta a conta, hiberna);
  2) dispara UM comando para as N contas e mede: pico de aparelhos ligados, RAM do host, tempo do lote,
     boots a frio × acordadas de snapshot, resultado por conta (verificador independente do app de QA).
  Resultado: data\rotation-test-results.json. Não gasta IA quando o backend está em modo simulado.
.EXAMPLE
  .\rotation-test.ps1 -Accounts 10 -Slots 4
#>
[CmdletBinding()]
param(
  [int]$Accounts = 10,
  [int]$Slots = 4,
  [string]$Base = 'http://127.0.0.1:8000',
  [switch]$SkipPrepare,
  [int]$TimeoutSec = 3600,
  # Achado #153: de onde saem as contas. `local` (padrão) = só os emuladores DESTA máquina — são os únicos que
  # disputam as vagas de RAM que este teste mede. `worker:<id>` = só os de um servidor; `todos` = o parque
  # inteiro, menos a loja.
  [string]$Onde = 'local'
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$H = @{ Origin = $Base }
$adb = 'C:\Android\Sdk\platform-tools\adb.exe'
# O parque vem de GET /api/instances, não de `android-{0:d2}` de 1..N (achado #153): gerando por número, um
# `-Accounts 10` arrastava para o rodízio os aparelhos de OUTRA máquina (android-09/10) e media a RAM errada.
$todasAsInstancias = @(Invoke-RestMethod "$Base/api/instances" | Where-Object { $_.kind -ne 'store' })
switch -Regex ($Onde) {
  '^local$' { $todasAsInstancias = @($todasAsInstancias | Where-Object { -not $_.worker_id }) }
  '^worker:(.+)$' { $w = $Matches[1]; $todasAsInstancias = @($todasAsInstancias | Where-Object { $_.worker_id -eq $w }) }
  '^todos$' { }
  default { throw "-Onde aceita 'local', 'todos' ou 'worker:<id>' — recebi '$Onde'." }
}
$ids = @($todasAsInstancias | Sort-Object id | Select-Object -First $Accounts | ForEach-Object { $_.id })
if ($ids.Count -lt $Accounts) { throw "-Onde '$Onde' tem $($ids.Count) aparelho(s); -Accounts pediu $Accounts." }
function Get-Inst { (Invoke-RestMethod "$Base/api/instances") | ForEach-Object { $_ } | Where-Object { $ids -contains $_.id } }
function Post($path, $obj) { Invoke-RestMethod -Method Post "$Base$path" -Headers $H -ContentType 'application/json' -Body ($obj | ConvertTo-Json) }
function Wait-State([string[]]$which, [string[]]$states, [int]$sec) {
  $deadline = (Get-Date).AddSeconds($sec)
  do { Start-Sleep 5; $now = Get-Inst | Where-Object { $which -contains $_.id }; $pending = @($now | Where-Object { $states -notcontains $_.state }) }
  while ($pending.Count -gt 0 -and (Get-Date) -lt $deadline)
  return $pending
}
$ai = Invoke-RestMethod "$Base/api/ai"
$null = Invoke-RestMethod -Method Put "$Base/api/settings" -Headers $H -ContentType 'application/json' -Body (@{ auto_start_devices = $true; max_online_devices = $Slots } | ConvertTo-Json)
$features = (Invoke-RestMethod "$Base/api/health").features
Write-Host "Rodízio: $Accounts contas em $Slots vagas · hibernação=$($features.hibernation) · receitas=$($features.recipes) · IA simulada=$($ai.simulated)"

if (-not $SkipPrepare) {
  for ($i = 0; $i -lt $ids.Count; $i += $Slots) {
    $batch = @($ids[$i..([Math]::Min($i + $Slots, $ids.Count) - 1)])
    $need = @(Get-Inst | Where-Object { $batch -contains $_.id -and $_.state -ne 'online' } | ForEach-Object id)
    Write-Host "[preparo] lote: $($batch -join ', ')"
    if ($need) { $null = Post '/api/instances/bulk' @{ ids = $need; action = 'start' } }
    $bad = Wait-State $batch @('online') 1200
    if ($bad) { Write-Warning "não ficaram online: $(($bad | ForEach-Object { "$($_.id)=$($_.state) $($_.state_detail)" }) -join ' | ')" }
    & (Join-Path $PSScriptRoot 'provision-qa.ps1') -Instances (($batch) -join ',') -Base $Base | Out-Host
    $online = @(Get-Inst | Where-Object { $batch -contains $_.id -and $_.state -eq 'online' } | ForEach-Object id)
    if ($online -and ($i + $Slots) -lt $ids.Count) {           # o último lote fica ligado: o rodízio decide quem cede a vaga
      $null = Post '/api/instances/bulk' @{ ids = $online; action = $(if ($features.hibernation) { 'hibernate' } else { 'stop' }) }
      $null = Wait-State $online @('hibernated', 'stopped') 300
    }
  }
}

$cmd = 'Nas instâncias selecionadas, abra o QA Messenger, entre na conversa com o contato de teste identificado como QA-001 e envie "Rodizio {instance_id} {run_id}". Confirme que a mensagem apareceu como enviada.'
$body = @{ command = $cmd; instance_ids = $ids; idempotency_key = [guid]::NewGuid().ToString(); mode = 'execute' } | ConvertTo-Json
$run = Invoke-RestMethod -Method Post "$Base/api/runs" -Headers $H -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($body))
Write-Host "Execução $($run.id) criada para $Accounts contas"
$t0 = Get-Date; $peakSlots = 0; $minAvail = [double]::MaxValue; $peakEmuMb = 0; $last = ''
do {
  Start-Sleep 5
  $inst = Get-Inst
  $busy = @($inst | Where-Object { $_.state -in 'online', 'booting', 'stopping' }).Count
  $peakSlots = [Math]::Max($peakSlots, $busy)
  try { $m = Invoke-RestMethod "$Base/api/metrics"; $minAvail = [Math]::Min($minAvail, $m.mem_available_gb)
        $peakEmuMb = [Math]::Max($peakEmuMb, (($m.emulators | Measure-Object rss_mb -Sum).Sum)) } catch {}
  $r = Invoke-RestMethod "$Base/api/runs/$($run.id)"
  $line = "ligados=$busy · ok=$($r.counts.succeeded) falha=$($r.counts.failed) bloq=$($r.counts.waiting_user) incerto=$($r.counts.uncertain) andamento=$($r.counts.running) fila=$($r.counts.pending)"
  if ($line -ne $last) { Write-Host ("{0,5:N0}s  {1}" -f ((Get-Date) - $t0).TotalSeconds, $line); $last = $line }
} while ($r.status -in 'planning', 'running' -and ((Get-Date) - $t0).TotalSeconds -lt $TimeoutSec)
$secs = [math]::Round(((Get-Date) - $t0).TotalSeconds)

$serial = @{}; Get-Inst | ForEach-Object { $serial[$_.id] = $_.serial }
$per = foreach ($o in $r.objectives) {
  $st = ((Get-Inst) | Where-Object id -eq $o.instance_id).state
  $n = if ($st -eq 'online') { @(& $adb -s $serial[$o.instance_id] shell content query --uri content://com.pocqa.messenger.provider/messages 2>$null | Where-Object { $_ -match [regex]::Escape($run.id) }).Count } else { $null }
  [ordered]@{ instance = $o.instance_id; status = $o.status; delivery = $o.delivery_level; messages_if_online = $n; detail = $o.status_detail }
}
$steps = $r.steps | Where-Object status -eq 'succeeded' | Group-Object driven_by | ForEach-Object { "$($_.Name)=$($_.Count)" }
$usage = Invoke-RestMethod "$Base/api/usage?run_id=$($run.id)"
$result = [ordered]@{ ts = (Get-Date).ToString('s'); accounts = $Accounts; slots = $Slots; run_id = $run.id; status = $r.status
  seconds = $secs; peak_devices_holding_ram = $peakSlots; min_host_mem_available_gb = $minAvail; peak_emulators_rss_gb = [math]::Round($peakEmuMb / 1024, 1)
  counts = $r.counts; steps_driven_by = ($steps -join ' '); ai_simulated = $ai.simulated; ai_usd = $usage.total_usd; ai_calls = ($usage.groups | Measure-Object calls -Sum).Sum
  hibernation = $features.hibernation; per_instance = @($per) }
$result | ConvertTo-Json -Depth 6 | Set-Content -Encoding utf8 (Join-Path $root 'data\rotation-test-results.json')
Write-Host ''
Write-Host "RESULTADO: $($r.counts.succeeded)/$Accounts contas com sucesso comprovado em $secs s · pico de $peakSlots aparelhos ligados (limite $Slots) · RSS dos emuladores no pico: $([math]::Round($peakEmuMb/1024,1)) GB · RAM livre mínima do host: $minAvail GB"
Write-Host "Etapas concluídas por: $($steps -join ' · ')"
$per | ForEach-Object { Write-Host ("  {0}: {1} {2}" -f $_.instance, $_.status, $(if ($null -ne $_.messages_if_online) { "· mensagens desta execução no app: $($_.messages_if_online)" })) }
