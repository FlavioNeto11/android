<#
.SYNOPSIS
  Tarefas agendadas do host para os scripts da Canais (29.186): o resumo diário ao dono e o espelho do deploy sob demanda.
  Hoje os dois dependem de uma sessão viva (cron de sessão, ou a Canais rodando o comando à mão); como tarefa do host
  sobrevivem a ela.

.DESCRIPTION
  Duas tarefas, no molde da `farm-restore-ensaio`:

  * `-Tarefa resumo-diario` -> tarefa `farm-canais-resumo-diario`: todo dia às 07:03 do horário LOCAL do host (Brasília), a
    partir de `-APartirDe` (padrão 2026-10-08). Roda `.claude\canais\resumo_diario.py` do checkout onde este script está. A
    tarefa registrada leva `-Enviar` (manda ao Telegram do dono); uma execução manual sem `-Enviar` é só ensaio e IMPRIME o
    texto. Execução atrasada demais (mais de 6 h depois das 07:03, por exemplo o host ligou à tarde) NÃO envia: um resumo
    "de hoje cedo" às 15:00 engana; `-Forcar` ignora a trava.
  * `-Tarefa espelho-do-deploy` -> tarefa `farm-canais-espelho-deploy`: sem gatilho (sob demanda). Quem fecha o deploy pede
    com `-Pedir -Raiz <checkout em origin/main> [-Deploy NN] [-Aplicar]`: o pedido vira `data\canais\tarefas\espelho-pedido.json`
    e a tarefa é disparada. Sem `-Aplicar` o espelho só relata (nada é gravado no Trello). O pedido é consumido (renomeado com
    carimbo) antes de rodar, então disparar a tarefa de novo sem pedido novo não repete nada.

  Segredo: nenhum token na linha de comando da tarefa nem no log. O token do Telegram e a chave do Trello são lidos pelos
  próprios scripts da Canais, do config/.env do checkout central. O log por execução (`data\canais\tarefas\<carimbo>-<tarefa>.log`)
  tem só: início, tarefa, commit do checkout, a saída do script (com padrões de segredo cobertos por `***` e no máximo 20 mil
  caracteres), código de saída e duração. Logs com mais de 30 dias são apagados (só os desta pasta).

  Códigos de saída: 0 ok; 1 o script da Canais falhou; 2 faltou script ou python; 3 atrasada demais (resumo); 4 sem pedido
  (espelho); 5 pedido inválido.

  Prioridade ociosa, sem rede própria (a rede é a dos scripts da Canais), sem tocar WSL, túnel, relógio nem `.wslconfig`.

.PARAMETER Tarefa
  `resumo-diario` ou `espelho-do-deploy`.
.PARAMETER Instalar
  Registra a tarefa e sai (não roda). Com `-Simular` só mostra o plano em JSON. O real fica para depois que os scripts da Canais
  estiverem no checkout central.
.PARAMETER Remover
  Remove a tarefa e sai.
.PARAMETER Executar
  Roda a tarefa agora (é o que a tarefa agendada faz).
.PARAMETER Pedir
  Só espelho-do-deploy: grava o pedido e dispara a tarefa.
.PARAMETER Raiz
  Com `-Pedir`: checkout em origin/main de onde vêm plano, CHANGELOG e Git (dentro da pasta-pai deste checkout).
.PARAMETER Deploy
  Com `-Pedir`: número do deploy (padrão: o mais recente do CHANGELOG da raiz).
.PARAMETER Aplicar
  Com `-Pedir`: grava no Trello (sem isto, só relata).
.PARAMETER Enviar
  Só resumo-diario: envia ao Telegram do dono (sem isto, ensaio).
.PARAMETER Forcar
  Só resumo-diario: ignora a trava de execução atrasada.
.PARAMETER Simular
  Com `-Instalar`: imprime o plano e não registra nada.
.PARAMETER Python
  Python do venv do backend (padrão `backend\.venv\Scripts\python.exe`).
.PARAMETER Pasta
  Onde ficam logs e pedido (padrão `data\canais\tarefas`).
.PARAMETER SemDisparar
  Com `-Pedir`: só grava o pedido, não dispara a tarefa (os testes usam isto para nunca acionar a tarefa real).
.PARAMETER AgoraLocal
  Só para teste: relógio local injetado (AAAA-MM-DD HH:mm) na trava de execução atrasada.
.PARAMETER APartirDe
  Primeiro dia do resumo diário, AAAA-MM-DD (padrão 2026-10-08).

.EXAMPLE
  pwsh -File scripts\canais-agendadas.ps1 -Tarefa resumo-diario -Instalar
  pwsh -File scripts\canais-agendadas.ps1 -Tarefa espelho-do-deploy -Pedir -Raiz C:\git\android-wt-deploy -Deploy 60 -Aplicar
  pwsh -File scripts\canais-agendadas.ps1 -Tarefa resumo-diario -Executar        # ensaio: imprime, não envia
#>
[CmdletBinding()]
param([Parameter(Mandatory)][ValidateSet('resumo-diario', 'espelho-do-deploy')][string]$Tarefa,
      [switch]$Instalar, [switch]$Remover, [switch]$Executar, [switch]$Pedir,
      [string]$Raiz = '', [int]$Deploy = 0, [switch]$Aplicar, [switch]$Enviar, [switch]$Forcar, [switch]$Simular,
      [switch]$SemDisparar, [string]$AgoraLocal = '', [string]$Python = '', [string]$Pasta = '', [string]$APartirDe = '2026-10-08')
$ErrorActionPreference = 'Stop'
$raizDoCheckout = Split-Path -Parent $PSScriptRoot
if (-not $Python) { $Python = Join-Path $raizDoCheckout 'backend\.venv\Scripts\python.exe' }
if (-not $Pasta) { $Pasta = Join-Path $raizDoCheckout 'data\canais\tarefas' }
$inv = [Globalization.CultureInfo]::InvariantCulture
$nomeDaTarefa = if ($Tarefa -eq 'resumo-diario') { 'farm-canais-resumo-diario' } else { 'farm-canais-espelho-deploy' }
$scriptDaCanais = if ($Tarefa -eq 'resumo-diario') { Join-Path $raizDoCheckout '.claude\canais\resumo_diario.py' }
                  else { Join-Path $raizDoCheckout '.claude\trello\espelho_do_deploy.py' }
$modos = @($Instalar, $Remover, $Executar, $Pedir) | Where-Object { $_ }
if (@($modos).Count -ne 1) { throw 'escolha exatamente um: -Instalar, -Remover, -Executar ou -Pedir' }
if ($Pedir -and $Tarefa -ne 'espelho-do-deploy') { throw '-Pedir só vale para espelho-do-deploy' }

if ($Instalar) {
  $script = $MyInvocation.MyCommand.Path
  $exe = (Get-Command pwsh -ErrorAction SilentlyContinue).Source
  if (-not $exe) { $exe = 'powershell.exe' }
  $argumentos = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`" -Tarefa $Tarefa -Executar"
  if ($Tarefa -eq 'resumo-diario') { $argumentos += ' -Enviar' }
  $dia = [datetime]::ParseExact($APartirDe, 'yyyy-MM-dd', $inv)
  $plano = [ordered]@{ tarefa = $nomeDaTarefa; executavel = $exe; argumentos = $argumentos
                       gatilho = $(if ($Tarefa -eq 'resumo-diario') { "diario 07:03 local a partir de $APartirDe" } else { 'nenhum (sob demanda)' })
                       limite = $(if ($Tarefa -eq 'resumo-diario') { '15 min' } else { '30 min' }) }
  if ($Simular) { $plano | ConvertTo-Json -Compress; return }
  $eu = ([Security.Principal.WindowsIdentity]::GetCurrent()).Name
  Unregister-ScheduledTask -TaskName $nomeDaTarefa -Confirm:$false -ErrorAction SilentlyContinue
  $acao = New-ScheduledTaskAction -Execute $exe -Argument $argumentos
  $principal = New-ScheduledTaskPrincipal -UserId $eu -LogonType S4U -RunLevel Highest
  $limite = if ($Tarefa -eq 'resumo-diario') { New-TimeSpan -Minutes 15 } else { New-TimeSpan -Minutes 30 }
  $ajustes = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
               -MultipleInstances IgnoreNew -ExecutionTimeLimit $limite
  if ($Tarefa -eq 'resumo-diario') {
    $gatilho = New-ScheduledTaskTrigger -Daily -At ($dia.Date.AddHours(7).AddMinutes(3))
    Register-ScheduledTask -TaskName $nomeDaTarefa -Action $acao -Trigger $gatilho -Principal $principal -Settings $ajustes | Out-Null
  } else {
    Register-ScheduledTask -TaskName $nomeDaTarefa -Action $acao -Principal $principal -Settings $ajustes | Out-Null
  }
  Write-Host "tarefa '$nomeDaTarefa' registrada ($($plano.gatilho)). NÃO foi executada."
  return
}
if ($Remover) {
  Unregister-ScheduledTask -TaskName $nomeDaTarefa -Confirm:$false -ErrorAction SilentlyContinue
  Write-Host "tarefa '$nomeDaTarefa' removida (se existia)."
  return
}

New-Item -ItemType Directory -Force $Pasta | Out-Null
$arquivoDoPedido = Join-Path $Pasta 'espelho-pedido.json'

if ($Pedir) {
  if (-not $Raiz) { throw '-Pedir exige -Raiz' }
  if ($Deploy -lt 0 -or $Deploy -gt 999) { throw '-Deploy fora de 0..999' }
  $pedido = [ordered]@{ raiz = [IO.Path]::GetFullPath($Raiz); deploy = $Deploy; aplicar = [bool]$Aplicar }
  [IO.File]::WriteAllText($arquivoDoPedido, ($pedido | ConvertTo-Json -Compress), (New-Object Text.UTF8Encoding($false)))
  Write-Host "pedido gravado: raiz=$($pedido.raiz) deploy=$(if ($Deploy) { $Deploy } else { 'mais recente' }) aplicar=$($pedido.aplicar)"
  if ($SemDisparar) { return }
  if (-not (Get-ScheduledTask -TaskName $nomeDaTarefa -ErrorAction SilentlyContinue)) {
    Write-Host "a tarefa '$nomeDaTarefa' não está registrada: rode -Instalar, ou -Executar para rodar o pedido agora."
    return
  }
  Start-ScheduledTask -TaskName $nomeDaTarefa
  Write-Host "tarefa '$nomeDaTarefa' disparada; o log fica em $Pasta."
  return
}

# ---------------------------------------------------------------------------------------------- -Executar
try { (Get-Process -Id $PID).PriorityClass = 'Idle' } catch { }
[Console]::OutputEncoding = [Text.Encoding]::UTF8   # a saída dos scripts da Canais é UTF-8
$inicio = if ($AgoraLocal) { [datetime]::Parse($AgoraLocal, $inv) } else { Get-Date }
$relogio = [Diagnostics.Stopwatch]::StartNew()
$carimbo = $inicio.ToString('yyyyMMdd-HHmmss', $inv)
$arquivoDeLog = Join-Path $Pasta "$carimbo-$Tarefa.log"

function Remove-Segredos([string]$texto) {
  # Defesa em profundidade: os scripts da Canais já não imprimem segredo; o que parecer token vira ***.
  $t = $texto -replace '\b\d{6,}:[A-Za-z0-9_-]{30,}\b', '***'
  $t = $t -replace '\b(?:[0-9a-fA-F]{32}|[0-9a-fA-F]{64})\b', '***'   # chave (32) e token (64) do Trello; o SHA do Git (40) fica
  $t = $t -replace '\b(sk|ghp|github_pat|xox[bp])[-_][A-Za-z0-9_-]{16,}', '***'
  $t = $t -replace '(?i)\b(bearer|token|key|secret|password)\s*[=:]\s*\S+', '$1=***'
  return $t
}
function Add-Log([string]$linha) { Add-Content -LiteralPath $arquivoDeLog -Value (Remove-Segredos $linha) -Encoding utf8 }
function Close-Execucao([int]$codigo, [string]$motivo) {
  Add-Log ("fim rc={0} dur={1:N1}s {2}" -f $codigo, $relogio.Elapsed.TotalSeconds, $motivo)
  # Retenção: só os logs e pedidos consumidos desta pasta, pela data do NOME, com mais de 30 dias.
  $limite = $inicio.Date.AddDays(-30)
  foreach ($f in Get-ChildItem -LiteralPath $Pasta -File -ErrorAction SilentlyContinue) {
    $d = [datetime]::MinValue
    if ($f.Name -match '^(?:espelho-pedido-)?(\d{8})-\d{6}' -and
        [datetime]::TryParseExact($Matches[1], 'yyyyMMdd', $inv, 'None', [ref]$d) -and $d -lt $limite) {
      Remove-Item -LiteralPath $f.FullName -Force -ErrorAction SilentlyContinue
    }
  }
  exit $codigo
}

$commit = ''
try { $commit = (& git -C $raizDoCheckout rev-parse --short HEAD 2>$null) } catch { }
Add-Log "inicio $($inicio.ToString('o', $inv)) tarefa=$Tarefa commit=$commit"
if (-not (Test-Path -LiteralPath $Python) -or -not (Test-Path -LiteralPath $scriptDaCanais)) {
  Close-Execucao 2 'faltou o python do backend ou o script da Canais neste checkout'
}

$argumentosDoScript = @()
if ($Tarefa -eq 'resumo-diario') {
  if ($Enviar) {
    $limiteDoDia = $inicio.Date.AddHours(7).AddMinutes(3).AddHours(6)
    if ($inicio -gt $limiteDoDia -and -not $Forcar) { Close-Execucao 3 'atrasada demais: não envia (use -Forcar)' }
    $argumentosDoScript += '--enviar'
  }
} else {
  if (-not (Test-Path -LiteralPath $arquivoDoPedido)) { Close-Execucao 4 'sem pedido (use -Pedir)' }
  $consumido = Join-Path $Pasta "espelho-pedido-$carimbo.json"
  Move-Item -LiteralPath $arquivoDoPedido -Destination $consumido -Force
  try {
    $p = Get-Content -LiteralPath $consumido -Raw | ConvertFrom-Json
    $raizPedida = [IO.Path]::GetFullPath([string]$p.raiz)
    $irmaos = [IO.Path]::GetFullPath((Split-Path -Parent $raizDoCheckout)) + [IO.Path]::DirectorySeparatorChar
    if (-not $raizPedida.StartsWith($irmaos, [StringComparison]::OrdinalIgnoreCase) -or -not (Test-Path -LiteralPath $raizPedida -PathType Container)) {
      throw 'raiz fora da pasta dos checkouts ou inexistente'
    }
    $numero = [int]$p.deploy
    if ($numero -lt 0 -or $numero -gt 999) { throw 'deploy fora de 0..999' }
    $argumentosDoScript += @('--raiz', $raizPedida)
    if ($numero -gt 0) { $argumentosDoScript += @('--deploy', [string]$numero) }
    if ($p.aplicar -eq $true) { $argumentosDoScript += '--aplicar' }
  } catch {
    Close-Execucao 5 "pedido inválido ($($_.Exception.Message))"
  }
}
Add-Log ('comando: python {0} {1}' -f (Split-Path -Leaf $scriptDaCanais), ($argumentosDoScript -join ' '))
$saida = ''
$codigoDoScript = 1
try {
  $saida = (& $Python $scriptDaCanais @argumentosDoScript 2>&1 | Out-String)
  $codigoDoScript = $LASTEXITCODE
} catch { $saida = "erro ao rodar: $($_.Exception.GetType().Name)"; $codigoDoScript = 1 }
if ($saida.Length -gt 20000) { $saida = $saida.Substring(0, 20000) + "`n[saída cortada em 20000 caracteres]" }
Add-Log $saida
if ($Tarefa -eq 'resumo-diario' -and -not $Enviar) { Write-Host $saida }
Close-Execucao $(if ($codigoDoScript -eq 0) { 0 } else { 1 }) "script_rc=$codigoDoScript"
