<#
.SYNOPSIS
  Mantém de pé o túnel SSH que traz os aparelhos de uma máquina worker para o servidor central.

.DESCRIPTION
  O emulador do Android escuta a porta de ADB SOMENTE em 127.0.0.1 (medido em 19/09/2026 nas duas máquinas),
  então não existe `adb connect ip-da-lan:5555`: o túnel não é escolha de segurança, é requisito de
  funcionamento. Cada aparelho remoto vira uma porta local aqui, e o `adb connect 127.0.0.1:<porta>` do
  servidor central passa a enxergá-lo como aparelho de rede comum.

  Reconecta sozinho: o `ssh` cai quando o Wi-Fi oscila, e o laço o levanta de novo. O `ServerAliveInterval`
  faz o cliente perceber queda em ~90 s em vez de ficar pendurado numa conexão morta.

  Registre como tarefa agendada (-Instalar) para sobreviver a logout e reinício. Processo iniciado dentro de
  uma sessão SSH ou de um console morre com ela — foi assim que o primeiro emulador do worker caiu.

.EXAMPLE
  pwsh -File scripts\worker-tunnel.ps1 -Instalar
  pwsh -File scripts\worker-tunnel.ps1                # roda em primeiro plano, para diagnóstico
#>
[CmdletBinding()]
param(
  [string]$Worker   = '192.168.1.19',
  [string]$Usuario  = 'Administrator',
  [string]$Chave    = 'C:\Users\Administrator\.ssh\worker_ed25519',
  # Uma entrada "portaLocal:portaRemota" por aparelho do worker; a remota é a de ADB (console + 1).
  # É TEXTO de propósito: `pwsh -File ... -Portas 1,2` NÃO vira array — chega como um texto só, e a tarefa
  # agendada subia encaminhando a porta "1555515557", que não existe (medido).
  [string]$Mapa = '15555:5555,15557:5557',
  # Mapa vindo de ARQUIVO, gerado pelo central (`data/tunnel/<worker>.map`, uma linha `local:remota,...`). Com
  # ele, acrescentar um aparelho deixa de exigir `-Instalar` de novo: o laco rele o arquivo a cada 5 s e, quando
  # o conteudo muda, derruba o `ssh` e sobe outro ja com as portas novas. Vazio, ou arquivo ausente/vazio, faz o
  # script cair no `-Mapa` de sempre - a tarefa agendada que ja existe continua funcionando sem ser tocada.
  [string]$MapaArquivo = '',
  # Imprime o mapa resolvido e sai. Serve para conferir o arquivo gerado sem abrir tunel nenhum.
  [switch]$MostrarMapa,
  # Encaminhamento REVERSO: abre uma porta NA MÁQUINA DO WORKER que chega até um serviço desta aqui. É como o
  # agente alcança o central sem que o central deixe de escutar só em loopback.
  #
  # A porta DAQUI tem de ser a do listener dedicado (`server.worker_port`, 8010), NUNCA a 8000. Por quê: toda
  # conexão que chega pelo `-R` tem par 127.0.0.1 de verdade, e loopback isenta de credencial. Apontando para a
  # 8000, qualquer processo/usuário local da máquina do worker alcançava a API INTEIRA do central sem token
  # (medido: /api/workers, /api/instagram/profiles e /api/commands respondiam 200) e chegava a POST
  # /api/admin/shutdown e PUT de credencial de perfil. Na 8010 só existe /api/worker/ws, que autentica na
  # primeira mensagem; o resto responde 404.
  # Formato "portaNoWorker:portaAqui". Vazio = sem encaminhamento reverso.
  [string]$MapaReverso = '18000:8010',
  [string]$LogDir   = 'C:\git\android\data\logs',
  [switch]$Instalar
)
$ErrorActionPreference = 'Stop'
$tarefa = "farm-tunel-$Worker"

if ($Instalar) {
  $eu = ([Security.Principal.WindowsIdentity]::GetCurrent()).Name
  $script = $MyInvocation.MyCommand.Path
  $argumentos = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`" " +
                "-Worker $Worker -Usuario $Usuario -Chave `"$Chave`" -Mapa `"$Mapa`" " +
                "-MapaArquivo `"$MapaArquivo`" " +
                "-MapaReverso `"$MapaReverso`" -LogDir `"$LogDir`""
  # pwsh, não powershell.exe: a tarefa rodava no 5.1 e morria com LastTaskResult=1 em cmdlets só do 7 (medido)
  $exe = (Get-Command pwsh -ErrorAction SilentlyContinue).Source
  if (-not $exe) { $exe = 'powershell.exe' }

  # Desregistrar NÃO mata o laço que já roda nem o `ssh` filho dele: reinstalar deixava DOIS túneis disputando
  # as mesmas portas locais (medido). Derruba o antigo primeiro, e só então registra.
  Stop-ScheduledTask -TaskName $tarefa -ErrorAction SilentlyContinue
  Unregister-ScheduledTask -TaskName $tarefa -Confirm:$false -ErrorAction SilentlyContinue
  foreach ($p in @(Get-CimInstance Win32_Process -Filter "Name='ssh.exe'" -ErrorAction SilentlyContinue)) {
    if ($p.CommandLine -and $p.CommandLine -match [regex]::Escape($Worker) -and $p.CommandLine -match '\s-N\s') {
      Write-Host "encerrando túnel anterior (pid $($p.ProcessId))"
      Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
  }
  foreach ($p in @(Get-CimInstance Win32_Process -Filter "Name='pwsh.exe' OR Name='powershell.exe'" -ErrorAction SilentlyContinue)) {
    if ($p.CommandLine -and $p.CommandLine -match 'worker-tunnel\.ps1' -and $p.ProcessId -ne $PID) {
      Write-Host "encerrando laço anterior (pid $($p.ProcessId))"
      Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
  }
  Start-Sleep -Seconds 2
  $acao = New-ScheduledTaskAction -Execute $exe -Argument $argumentos
  $gatilho = New-ScheduledTaskTrigger -AtStartup
  $principal = New-ScheduledTaskPrincipal -UserId $eu -LogonType S4U -RunLevel Highest
  $ajustes = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
               -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew `
               -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
  Register-ScheduledTask -TaskName $tarefa -Action $acao -Trigger $gatilho -Principal $principal -Settings $ajustes | Out-Null
  Start-ScheduledTask -TaskName $tarefa
  Write-Host "tarefa '$tarefa' registrada (sobe no boot) e iniciada."
  return
}

function Resolve-Mapa {
  <# O mapa que vale AGORA: o arquivo gerado pelo central quando existe e tem conteúdo, senão o `-Mapa` dos
     argumentos. Arquivo ausente, vazio ou ilegível NÃO derruba o túnel — cair para o mapa dos argumentos mantém
     de pé exatamente o que já funcionava antes de este parâmetro existir. #>
  if ($MapaArquivo) {
    try {
      if (Test-Path -LiteralPath $MapaArquivo) {
        $bruto = (Get-Content -Raw -LiteralPath $MapaArquivo -ErrorAction Stop).Trim()
        if ($bruto) { return $bruto }
      }
    } catch { }
  }
  return $Mapa
}

function ConvertTo-Pares($texto) {
  @($texto -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ } | ForEach-Object {
    $p = $_ -split ':'
    if ($p.Count -ne 2) { throw "entrada inválida no mapa: '$_' (esperado portaLocal:portaRemota)" }
    [pscustomobject]@{ Local = [int]$p[0]; Remota = [int]$p[1] }
  })
}

$mapaAtual = Resolve-Mapa
if ($MostrarMapa) { Write-Output $mapaAtual; return }
$pares = ConvertTo-Pares $mapaAtual
if (-not $pares) { throw 'mapa vazio: nem -MapaArquivo nem -Mapa trouxeram portas' }
New-Item -ItemType Directory -Force $LogDir | Out-Null
$log = Join-Path $LogDir "tunel-$Worker.log"
function Registra($m) { "$([DateTime]::Now.ToString('yyyy-MM-dd HH:mm:ss')) $m" | Tee-Object -FilePath $log -Append | Write-Host }

$reversos = @($MapaReverso -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ } | ForEach-Object {
  $p = $_ -split ':'
  if ($p.Count -ne 2) { throw "entrada inválida no -MapaReverso: '$_' (esperado portaNoWorker:portaAqui)" }
  [pscustomobject]@{ NoWorker = [int]$p[0]; Aqui = [int]$p[1] }
})
function Get-Encaminhamentos($pares) {
  $lista = @()
  foreach ($p in $pares) { $lista += @('-L', "$($p.Local):127.0.0.1:$($p.Remota)") }
  foreach ($r in $reversos) { $lista += @('-R', "$($r.NoWorker):127.0.0.1:$($r.Aqui)") }
  return $lista
}
$base = @('-i', $Chave, '-N',
          '-o', 'BatchMode=yes',
          '-o', 'ExitOnForwardFailure=yes',   # porta ocupada é falha, não túnel meio pronto
          '-o', 'ServerAliveInterval=30',
          '-o', 'ServerAliveCountMax=3',
          '-o', 'StrictHostKeyChecking=accept-new',
          '-o', "UserKnownHostsFile=$(Split-Path $Chave)\known_hosts")

Registra ("iniciando" + $(if ($MapaArquivo) { " (mapa de $MapaArquivo)" } else { "" }) + "; " +
          (($pares | ForEach-Object { "$($_.Local)->$Worker`:$($_.Remota)" }) -join ' ') +
          $(if ($reversos) { " | reverso: " + (($reversos | ForEach-Object { "$Worker`:$($_.NoWorker)->$($_.Aqui)" }) -join ' ') } else { "" }))
$seguidas = 0
while ($true) {
  # Relê o mapa a cada volta: aparelho adotado no painel entra no túnel sem ninguém reinstalar a tarefa.
  $mapaAtual = Resolve-Mapa
  $pares = ConvertTo-Pares $mapaAtual
  if (-not $pares) { Registra 'mapa vazio; nova tentativa em 30s'; Start-Sleep -Seconds 30; continue }
  $encaminhamentos = Get-Encaminhamentos $pares
  $t0 = Get-Date
  $saida = Join-Path $LogDir "tunel-$Worker.ssh.log"
  # `Start-Process` em vez de `&`: com o `ssh` num processo próprio dá para VIGIAR o arquivo de mapa enquanto o
  # túnel está de pé. Sem isso, a única forma de trocar as portas era matar a tarefa agendada e reinstalá-la.
  $proc = Start-Process -FilePath ssh -ArgumentList (@($base) + $encaminhamentos + "$Usuario@$Worker") `
                        -NoNewWindow -PassThru -RedirectStandardError $saida
  while (-not $proc.HasExited) {
    Start-Sleep -Seconds 5
    if ((Resolve-Mapa) -ne $mapaAtual) {
      Registra 'o mapa do túnel mudou; derrubando o ssh para subir com as portas novas'
      Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
      break
    }
  }
  $proc.WaitForExit()
  if (Test-Path -LiteralPath $saida) {
    foreach ($linha in @(Get-Content -LiteralPath $saida -ErrorAction SilentlyContinue)) {
      if ($linha) { Registra "ssh: $linha" }
    }
    Remove-Item -LiteralPath $saida -Force -ErrorAction SilentlyContinue
  }
  $viveu = ((Get-Date) - $t0).TotalSeconds
  # queda em menos de 10 s é falha real (chave, porta ocupada, host fora): espera mais para não martelar
  if ($viveu -lt 10) { $seguidas++ } else { $seguidas = 0 }
  $espera = [Math]::Min(60, 5 * [Math]::Max(1, $seguidas))
  Registra "ssh saiu depois de $([Math]::Round($viveu))s (falhas rapidas seguidas=$seguidas); nova tentativa em ${espera}s"
  Start-Sleep -Seconds $espera
}
