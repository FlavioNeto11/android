<#
.SYNOPSIS
  Amostrador permanente do host central (29.156, fatia 1): 1 linha por minuto com CPU, RAM, disco, VM do WSL, emuladores,
  os processos que mais usaram CPU e os avisos de pressão por aparelho. Só leitura; nunca para o que mede.

.DESCRIPTION
  A medição de 06/10 mostrou que o host central não é o gargalo e que a pressão de CPU é do convidado (do notebook, na maior
  parte), mas a série foi um amostrador solto em `.claude/handoffs` e NÃO dizia qual processo causa um pico de CPU do host.
  Este script é a versão permanente, para o 29.165 e para a prova ler sempre a mesma coisa.

  Um arquivo por dia (UTC) em `data\observabilidade\host\AAAAMMDD.csv`, com cabeçalho. Retenção de 7 dias: ao iniciar e a cada
  virada de dia, apaga só os arquivos `AAAAMMDD.csv` mais velhos que isso nessa pasta (nada fora dela). Colunas:

    ts_utc            AAAA-MM-DDTHH:MM:SSZ
    cpu_host_pct      CPU total do host (%)
    vm_convidado_nucleos  soma de % de execução convidada das VMs Hyper-V / 100 (a VM do WSL é a do Docker Desktop); vazio se o
                      contador não existir
    vmmem_ws_mb       memória residente da VM do WSL (MB)
    qemu_host_pct     CPU dos emuladores (qemu) em % do host no minuto (vazio na 1ª linha de cada execução)
    ram_livre_mb      RAM física livre (MB)
    disco_livre_gb    espaço livre do disco onde está o checkout (GB)
    processos_top     até 3 NOMES de processo (sem linha de comando, sem usuário) com mais CPU no minuto, em % do host:
                      `python:12.3;pwsh:4.1`. Processos de mesmo nome somam; `qemu-system-x86_64` e este amostrador ficam fora
    avisos_pressao    avisos "Convidado sob pressão de CPU" do minuto por aparelho: `android-05:3;android-01:1`; vazio = nenhum
                      OU não medido (banco indisponível)
  Linha de falha: `ts_utc,erro,<tipo da exceção>` e o laço segue.

  Prioridade ociosa, uma instância só (mutex), sem rede, sem escrever fora de `data\observabilidade\host`. Não lê `.env`, o
  config da instalação nem a chave do cofre; do banco lê só o `instance_id` dos avisos (`amostrador-host-pressao.py`, `mode=ro`).

.PARAMETER Saida
  Pasta dos CSVs (padrão `data\observabilidade\host`).
.PARAMETER Amostras
  Quantas linhas gravar e sair (0 = para sempre; é o que a tarefa agendada usa).
.PARAMETER IntervaloS
  Segundos entre o início de uma linha e o da seguinte (padrão 60).
.PARAMETER JanelaS
  Segundos de amostra dos contadores de desempenho dentro de cada linha (padrão 5).
.PARAMETER RetencaoDias
  Dias de arquivos mantidos (padrão 7).
.PARAMETER Banco
  `poc.sqlite3` de onde sai `avisos_pressao` (padrão `data\poc.sqlite3`).
.PARAMETER NomeDoMutex
  Nome do mutex de instância única (padrão `Global\farm-amostrador-host`, um por host). Só os testes o trocam, para nunca disputar
  com o amostrador real que estiver rodando.
.PARAMETER Instalar
  Registra a tarefa `farm-amostrador-host` (ao ligar o host e todo dia 00:05; uma instância; prioridade ociosa; reinicia se
  cair) e sai. Não inicia o amostrador.

.EXAMPLE
  pwsh -File scripts\amostrador-host.ps1 -Amostras 3 -Saida $env:TEMP\amostra
  pwsh -File scripts\amostrador-host.ps1 -Instalar
#>
[CmdletBinding()]
param([string]$Saida = '', [int]$Amostras = 0, [int]$IntervaloS = 60, [int]$JanelaS = 5, [int]$RetencaoDias = 7,
      [string]$Banco = '', [string]$Python = '', [string]$NomeDoMutex = 'Global\farm-amostrador-host', [switch]$Instalar)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
if (-not $Saida) { $Saida = Join-Path $root 'data\observabilidade\host' }
if (-not $Banco) { $Banco = Join-Path $root 'data\poc.sqlite3' }
if (-not $Python) { $Python = Join-Path $root 'backend\.venv\Scripts\python.exe' }
$ajudante = Join-Path $PSScriptRoot 'amostrador-host-pressao.py'
$tarefa = 'farm-amostrador-host'
$inv = [Globalization.CultureInfo]::InvariantCulture

if ($Instalar) {
  $script = $MyInvocation.MyCommand.Path
  $exe = (Get-Command pwsh -ErrorAction SilentlyContinue).Source
  if (-not $exe) { $exe = 'powershell.exe' }
  $eu = ([Security.Principal.WindowsIdentity]::GetCurrent()).Name
  Unregister-ScheduledTask -TaskName $tarefa -Confirm:$false -ErrorAction SilentlyContinue
  $acao = New-ScheduledTaskAction -Execute $exe -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`""
  # Ao ligar o host e, como rede de segurança, todo dia 00:05: com IgnoreNew a 2ª partida não duplica o amostrador vivo.
  $gatilhos = @((New-ScheduledTaskTrigger -AtStartup), (New-ScheduledTaskTrigger -Daily -At 00:05))
  $principal = New-ScheduledTaskPrincipal -UserId $eu -LogonType S4U -RunLevel Highest
  $ajustes = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
               -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
  Register-ScheduledTask -TaskName $tarefa -Action $acao -Trigger $gatilhos -Principal $principal -Settings $ajustes | Out-Null
  Write-Host "tarefa '$tarefa' registrada (ao ligar o host e todo dia 00:05; prioridade ociosa). O amostrador NÃO foi iniciado."
  return
}

try { (Get-Process -Id $PID).PriorityClass = 'Idle' } catch { }
$mutex = New-Object Threading.Mutex($false, $NomeDoMutex)   # um por host, qualquer que seja o checkout; o padrão é o global, só teste troca
$achou = $false
try { $achou = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $achou = $true }   # o dono anterior morreu: a posse é nossa
if (-not $achou) { Write-Host 'amostrador-host: ja ha um em execucao; saindo.'; exit 3 }
New-Item -ItemType Directory -Force $Saida | Out-Null
# A retenção apaga arquivos AAAAMMDD.csv desta pasta: ela não pode ser junção/link para outro lugar.
if ((Get-Item -LiteralPath $Saida -Force).Attributes.HasFlag([IO.FileAttributes]::ReparsePoint)) {
  Write-Host 'amostrador-host: -Saida e uma juncao/link; recusado.'; $mutex.ReleaseMutex(); exit 4
}
$cabecalho = 'ts_utc,cpu_host_pct,vm_convidado_nucleos,vmmem_ws_mb,qemu_host_pct,ram_livre_mb,disco_livre_gb,processos_top,avisos_pressao'
$nCpus = (Get-CimInstance Win32_Processor | Measure-Object NumberOfLogicalProcessors -Sum).Sum
$disco = (Get-Item -LiteralPath $root).PSDrive
$meuPid = $PID

# Retenção: só arquivos AAAAMMDD.csv desta pasta, mais velhos que $RetencaoDias dias (pela data do NOME, em UTC).
function Remove-ArquivosVelhos {
  $limite = [datetime]::UtcNow.Date.AddDays(-$RetencaoDias)
  foreach ($f in Get-ChildItem -LiteralPath $Saida -File -Filter '*.csv' -ErrorAction SilentlyContinue) {
    $d = [datetime]::MinValue
    if ($f.BaseName -match '^\d{8}$' -and [datetime]::TryParseExact($f.BaseName, 'yyyyMMdd', $inv, 'AssumeUniversal,AdjustToUniversal', [ref]$d) -and $d -lt $limite) {
      Remove-Item -LiteralPath $f.FullName -Force -ErrorAction SilentlyContinue
    }
  }
}
function Get-CpuPorProcesso {
  $m = @{}
  foreach ($p in Get-Process -ErrorAction SilentlyContinue) {
    if ($p.Id -eq $meuPid -or $null -eq $p.CPU) { continue }
    # Nome limpo de tudo que quebra o CSV ou o formato `nome:pct;nome:pct` (o nome do processo é dado do host, não nosso).
    $m[$p.Id] = @((($p.ProcessName -replace '[^A-Za-z0-9._-]', '_') -replace '^[=+@-]+', '_'), [double]$p.CPU)
  }
  return $m
}

Remove-ArquivosVelhos
$diaDaLimpeza = [datetime]::UtcNow.Date
$antes = Get-CpuPorProcesso
$relogio = [Diagnostics.Stopwatch]::StartNew()
$primeira = $true
$i = 0
try {
  while ($Amostras -eq 0 -or $i -lt $Amostras) {
    $inicio = [datetime]::UtcNow
    $arquivo = Join-Path $Saida ($inicio.ToString('yyyyMMdd') + '.csv')
    try {
      if (-not (Test-Path -LiteralPath $arquivo)) { Set-Content -LiteralPath $arquivo -Value $cabecalho -Encoding utf8 }
      $cpu = $null; $vm = $null
      # Os dois contadores numa chamada só (uma janela de $JanelaS s); sem o do Hyper-V (outro host), só a CPU.
      try {
        $c = (Get-Counter '\Processor(_Total)\% Processor Time', '\Hyper-V Hypervisor Virtual Processor(*)\% Guest Run Time' `
              -SampleInterval $JanelaS -MaxSamples 1 -ErrorAction Stop).CounterSamples
        $cpu = ($c | Where-Object { $_.Path -like '*\processor(_total)\*' }).CookedValue
        $vps = @($c | Where-Object { $_.Path -like '*hyper-v*' -and $_.InstanceName -ne '_total' })
        if ($vps.Count -gt 0) { $vm = ($vps | Measure-Object CookedValue -Sum).Sum / 100 }   # sem instância = não medido, não 0
      } catch {
        $cpu = (Get-Counter '\Processor(_Total)\% Processor Time' -SampleInterval $JanelaS -MaxSamples 1 -ErrorAction Stop).CounterSamples[0].CookedValue
      }
      $ws = ((Get-Process vmmemWSL -ErrorAction SilentlyContinue | Measure-Object WorkingSet64 -Sum).Sum) / 1MB
      $livre = (Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1KB
      $discoLivre = (Get-PSDrive -Name $disco.Name).Free / 1GB
      $agora = Get-CpuPorProcesso
      $dt = [math]::Max(1.0, $relogio.Elapsed.TotalSeconds); $relogio.Restart()
      $porNome = @{}; $qemu = 0.0
      foreach ($k in $agora.Keys) {
        if (-not $antes.ContainsKey($k)) { continue }
        $delta = $agora[$k][1] - $antes[$k][1]
        if ($delta -le 0) { continue }
        $nome = $agora[$k][0]
        if ($nome -like 'qemu-system*') { $qemu += $delta; continue }
        $porNome[$nome] = [double]$porNome[$nome] + $delta
      }
      $antes = $agora
      $top = ($porNome.GetEnumerator() | Sort-Object Value -Descending | Select-Object -First 3 |
              ForEach-Object { [pscustomobject]@{ N = $_.Key; P = $_.Value / $dt / $nCpus * 100 } } |
              Where-Object { $_.P -ge 1 } | ForEach-Object { '{0}:{1}' -f $_.N, $_.P.ToString('F1', $inv) }) -join ';'
      $avisos = ''
      if ((Test-Path -LiteralPath $python) -and (Test-Path -LiteralPath $Banco)) {
        $desde = $inicio.AddSeconds(-$IntervaloS).ToString('yyyy-MM-ddTHH:mm:ss')
        $avisos = [string]((& $python $ajudante $Banco $desde 2>$null) -join '')
      }
      $qpct = if ($primeira) { '' } else { ($qemu / $dt / $nCpus * 100).ToString('F1', $inv) }
      $linha = [string]::Format($inv, '{0},{1:F1},{2},{3:F0},{4},{5:F0},{6:F1},{7},{8}', $inicio.ToString('yyyy-MM-ddTHH:mm:ssZ'),
                 $cpu, $(if ($null -eq $vm) { '' } else { $vm.ToString('F2', $inv) }), $ws, $qpct, $livre, $discoLivre, $top, $avisos)
      Add-Content -LiteralPath $arquivo -Value $linha -Encoding utf8
      $primeira = $false
    } catch {
      Add-Content -LiteralPath $arquivo -Value ('{0},erro,{1}' -f $inicio.ToString('yyyy-MM-ddTHH:mm:ssZ'), $_.Exception.GetType().Name) -Encoding utf8 -ErrorAction SilentlyContinue
    }
    $i++
    if ($Amostras -ne 0 -and $i -ge $Amostras) { break }
    if ([datetime]::UtcNow.Date -ne $diaDaLimpeza) { Remove-ArquivosVelhos; $diaDaLimpeza = [datetime]::UtcNow.Date }
    $resta = $IntervaloS - ([datetime]::UtcNow - $inicio).TotalSeconds
    if ($resta -gt 0) { Start-Sleep -Milliseconds ([int]($resta * 1000)) }
  }
} finally {
  try { $mutex.ReleaseMutex() } catch { }
  $mutex.Dispose()
}
