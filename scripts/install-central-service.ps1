<#
.SYNOPSIS
  Registra o BACKEND DO CENTRAL como tarefa supervisionada: sobe no boot e religa se cair.

.DESCRIPTION
  Achados #136 e #38. O boot do central provava que o túnel volta sozinho — a tarefa `farm-tunel-*` tem gatilho
  de boot. O backend não: ele existia porque alguém o iniciou à mão, numa sessão interativa do console (medido:
  SessionId=1, processo pai já inexistente). Logoff, Windows Update ou um crash derrubavam API, agendador e
  Appium até alguém voltar à máquina, com o agente do worker reconectando no vazio e a fila parada no banco.

  O que é registrado NÃO é o backend direto: é `python -m app.supervisor`, que sobe o backend, vigia
  `/api/health` e o religa quando ele para de responder. A tarefa sozinha cobre "o processo morreu"; o
  supervisor cobre "o processo está vivo e travado", que é o caso que `RestartCount` não enxerga.

  Executável = o python do venv (`backend\.venv\Scripts\python.exe`), caminho estável desta árvore. De
  propósito NÃO é `pwsh`: neste central o único pwsh é o pacote da Microsoft Store, cujo caminho carrega a
  versão e some na próxima atualização (achado #138) — é o mesmo tropeço que já derruba a tarefa do túnel.

  Ordem de subida, para quem for conferir depois de um reboot: **túnel → backend → (o backend sobe o Appium
  local e o agendador)**. As duas tarefas são independentes e o backend não espera o túnel; o que o túnel traz é
  o ADB dos aparelhos remotos e o canal reverso do agente, e o backend trata a ausência deles como worker
  offline até o túnel aparecer. Os aparelhos que o agendador religa sozinho são os de `auto_start_devices`.

.PARAMETER Simular
  Imprime o que seria registrado e sai. Nada é criado, nada é iniciado — é o que torna isto testável.

.PARAMETER Remover
  Desregistra a tarefa (não encerra o backend que já está no ar).

.PARAMETER NaoIniciar
  Registra a tarefa sem dispará-la agora. Use quando já há um backend no ar e a janela de reinício é outra.

.EXAMPLE
  pwsh -File scripts\install-central-service.ps1 -Simular
  pwsh -File scripts\install-central-service.ps1
#>
[CmdletBinding()]
param(
  [string]$Tarefa = 'farm-central',
  [string]$Health = 'http://127.0.0.1:8000/api/health',
  [int]$IntervaloS = 15,
  [switch]$NaoIniciar,
  [switch]$Remover,
  [switch]$Simular
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root 'backend\.venv\Scripts\python.exe'

# Guarda contra chamada errada de quem chama. Um `-Simular` repassado por splat de array vira VALOR de `-Tarefa`
# (ele é o primeiro posicional), e o que era para ser um ensaio registra e INICIA uma tarefa chamada "-Simular".
# Aconteceu. Um nome de tarefa começando por '-' nunca é intenção de ninguém: é um parâmetro que se perdeu.
if ($Tarefa -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]*$') {
  throw ("nome de tarefa inválido: '$Tarefa'. Parece um parâmetro que chegou como valor posicional — passe as " +
         "opções pelo nome (`-Simular`), nunca por splat de array.")
}

if ($Remover) {
  if ($Simular) { Write-Output "removeria a tarefa: $Tarefa"; return }
  Stop-ScheduledTask -TaskName $Tarefa -ErrorAction SilentlyContinue
  Unregister-ScheduledTask -TaskName $Tarefa -Confirm:$false -ErrorAction SilentlyContinue
  Write-Host "tarefa '$Tarefa' removida (o backend que já está no ar continua de pé)."
  return
}

if (-not (Test-Path -LiteralPath $py)) {
  throw ("o ambiente Python do backend não existe em $py. Rode `pwsh -File scripts\start.ps1` uma vez para " +
         "criá-lo, e só então registre o serviço — uma tarefa que aponta para um executável ausente sobe no " +
         "boot para falhar em silêncio.")
}

$argumentos = "-m app.supervisor --health `"$Health`" --intervalo $IntervaloS"
$pasta = Join-Path $root 'backend'
# `WindowsIdentity` e não `$env:USERNAME`: numa sessão SSH o domínio pode vir vazio, e o token nunca mente.
$eu = ([Security.Principal.WindowsIdentity]::GetCurrent()).Name

if ($Simular) {
  Write-Output "tarefa: $Tarefa"
  Write-Output "executavel: $py"
  Write-Output "argumentos: $argumentos"
  Write-Output "pasta: $pasta"
  Write-Output "identidade: $eu"
  Write-Output 'gatilho: AtStartup'
  Write-Output 'reinicio: RestartCount=999 a cada 1 min'
  Write-Output 'simulacao: nada foi registrado nem iniciado'
  return
}

Stop-ScheduledTask -TaskName $Tarefa -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName $Tarefa -Confirm:$false -ErrorAction SilentlyContinue
$acao = New-ScheduledTaskAction -Execute $py -Argument $argumentos -WorkingDirectory $pasta
$gatilho = New-ScheduledTaskTrigger -AtStartup
# S4U: roda sem ninguém logado e sem guardar senha. RunLevel Highest porque o backend mexe com ADB e emulador.
$principal = New-ScheduledTaskPrincipal -UserId $eu -LogonType S4U -RunLevel Highest
$ajustes = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
             -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew `
             -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $Tarefa -Action $acao -Trigger $gatilho -Principal $principal -Settings $ajustes | Out-Null
Write-Host "tarefa '$Tarefa' registrada: sobe no boot e religa o backend se ele parar de responder."
if (-not $NaoIniciar) {
  Start-ScheduledTask -TaskName $Tarefa
  Write-Host 'tarefa iniciada. Acompanhe em data\logs\supervisor.log'
} else {
  Write-Host 'não iniciada (-NaoIniciar): ela sobe no próximo boot, ou rode Start-ScheduledTask quando quiser.'
}
