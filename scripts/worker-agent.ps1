<#
.SYNOPSIS
  Registra o AGENTE do worker como tarefa agendada: sobe no boot, religa se o processo morrer.

.DESCRIPTION
  Roda NA MÁQUINA DO WORKER. Achados #137, #38 e #14: `docs/worker.md` mandava "deixar como serviço", mas o
  repositório não entregava o script que faz isso — o que existia era um `run-agente.ps1` escrito à mão, que
  vivia só naquela máquina, sem gatilho de boot e sem reinício (medido por SSH: tarefas `farm-agente` e
  `farm-emulador-worker-01..06` com ZERO gatilhos e `RestartCount=0`). Reiniciar o notebook deixava os seis
  aparelhos e o agente fora até alguém entrar por SSH.

  Três coisas que a tarefa registrada aqui tem e a de antes não tinha:

  1. **Gatilho de boot** (`AtStartup`) com `LogonType S4U`: sobe sem ninguém logado e sem guardar senha.
  2. **Reinício em falha** (`RestartCount=999`, a cada 1 min): processo morto volta sozinho.
  3. **Sem `--enroll` na linha de comando.** O token de inscrição é de USO ÚNICO e já foi gasto na primeira
     partida; mantê-lo na ação da tarefa (era o caso) é segredo gasto exposto em texto, que só atrapalha quem
     tentar entender a recuperação depois. A inscrição é um passo à parte (`-Inscrever`), em primeiro plano.

  O log do agente vai para `<WorkDir>\logs\agente.log` COM ROTAÇÃO (5 × 5 MB), pelo `--log-file` do próprio
  agente. O redirecionamento `*>` do lançador antigo deixava um arquivo crescendo para sempre na mesma máquina
  que hospeda seis emuladores.

  Os emuladores NÃO ganham tarefa de boot própria de propósito: quem os religa é o agente, pelo estado desejado
  que o central manda na reconexão. Duas fontes ligando o mesmo aparelho brigariam pela porta do console.

.PARAMETER Inscrever
  Token de inscrição de uso único (gerado no painel, em Infraestrutura). Roda o agente em PRIMEIRO PLANO uma vez
  para trocar o token pela credencial permanente e, quando o arquivo de credencial aparece, encerra e registra a
  tarefa — sem o token.

.PARAMETER Simular
  Imprime o que seria registrado e sai. Não cria tarefa, não encerra processo, não escreve arquivo.

.EXAMPLE
  pwsh -File scripts\worker-agent.ps1 -Inscrever <token>   # primeira vez, com o painel aberto
  pwsh -File scripts\worker-agent.ps1 -Instalar            # depois, ou para reinstalar a tarefa
  pwsh -File scripts\worker-agent.ps1 -Simular -Instalar
#>
[CmdletBinding()]
param(
  [string]$Tarefa   = 'farm-agente',
  # Pasta onde o pacote do agente foi copiado (contém `app\worker`). É a raiz de execução: o agente roda como
  # `python -m app.worker`, então o processo precisa estar NESTA pasta.
  [string]$AgentDir = 'C:\farm\agent',
  [string]$Config   = 'C:\farm\worker.yaml',
  [string]$WorkDir  = 'C:\farm',
  [string]$Python   = '',
  [string]$Inscrever = '',
  [switch]$Instalar,
  [switch]$Remover,
  [switch]$NaoIniciar,
  [switch]$Simular
)
$ErrorActionPreference = 'Stop'

if ($Tarefa -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]*$') {
  throw ("nome de tarefa inválido: '$Tarefa'. Parece um parâmetro que chegou como valor posicional — passe as " +
         "opções pelo nome, nunca por splat de array.")
}
if (-not $Python) { $Python = Join-Path $AgentDir '.venv\Scripts\python.exe' }
$logDir = Join-Path $WorkDir 'logs'
$logAgente = Join-Path $logDir 'agente.log'
$credencial = Join-Path $WorkDir 'worker-credential.json'

if ($Remover) {
  if ($Simular) { Write-Output "removeria a tarefa: $Tarefa"; return }
  Stop-ScheduledTask -TaskName $Tarefa -ErrorAction SilentlyContinue
  Unregister-ScheduledTask -TaskName $Tarefa -Confirm:$false -ErrorAction SilentlyContinue
  Write-Host "tarefa '$Tarefa' removida."
  return
}

# A ação registrada NUNCA carrega o token: ele é de uso único e já foi gasto na inscrição.
$argumentos = "-m app.worker --config `"$Config`" --log-file `"$logAgente`""

if ($Simular) {
  Write-Output "tarefa: $Tarefa"
  Write-Output "executavel: $Python"
  Write-Output "argumentos: $argumentos"
  Write-Output "pasta: $AgentDir"
  Write-Output "log: $logAgente (rotacao 5 x 5 MB)"
  Write-Output 'gatilho: AtStartup'
  Write-Output 'reinicio: RestartCount=999 a cada 1 min'
  if ($Inscrever) { Write-Output 'inscricao: rodaria o agente em primeiro plano uma vez (token nao e impresso)' }
  Write-Output 'simulacao: nada foi registrado nem iniciado'
  return
}

if (-not (Test-Path -LiteralPath $Python)) {
  throw ("o interpretador do agente não existe em $Python. Rode `pwsh -File scripts\worker-install.ps1` " +
         "primeiro — uma tarefa apontando para executável ausente sobe no boot só para falhar em silêncio.")
}
if (-not (Test-Path -LiteralPath $Config)) {
  throw "configuração do worker não encontrada em $Config (copie config\worker.example.yaml e ajuste)."
}
New-Item -ItemType Directory -Force $logDir | Out-Null

# ---------------------------------------------------------------- inscrição (uma vez, em primeiro plano)
if ($Inscrever) {
  if (Test-Path -LiteralPath $credencial) {
    Write-Host "já existe credencial em $credencial; a inscrição foi ignorada (token de uso único não se repete)."
  } else {
    Write-Host 'trocando o token de inscrição pela credencial permanente (primeiro plano)…'
    # `Start-Process` e não `&`: o agente não termina sozinho, então quem espera é o laço abaixo, olhando o
    # arquivo de credencial aparecer. O token vai como argumento e NÃO é impresso em lugar nenhum.
    $p = Start-Process -FilePath $Python -ArgumentList @('-m', 'app.worker', '--config', $Config,
                                                         '--log-file', $logAgente, '--enroll', $Inscrever) `
                       -WorkingDirectory $AgentDir -PassThru -WindowStyle Hidden
    $limite = (Get-Date).AddSeconds(120)
    while ((Get-Date) -lt $limite -and -not (Test-Path -LiteralPath $credencial) -and -not $p.HasExited) {
      Start-Sleep -Seconds 2
    }
    $temCredencial = Test-Path -LiteralPath $credencial
    if (-not $p.HasExited) { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue }
    if (-not $temCredencial) {
      throw ("a credencial permanente não apareceu em $credencial. Veja o fim de ${logAgente}: token vencido, " +
             "já usado, ou o central inalcançável são as três causas comuns.")
    }
    Write-Host "credencial gravada em $credencial. O token não entra na tarefa agendada."
  }
}

# ---------------------------------------------------------------- a tarefa
$eu = ([Security.Principal.WindowsIdentity]::GetCurrent()).Name
Stop-ScheduledTask -TaskName $Tarefa -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName $Tarefa -Confirm:$false -ErrorAction SilentlyContinue
# Processo do agente iniciado numa sessão SSH pertence ao job dela e morre no logout (medido em 19/09). A tarefa
# roda sob o serviço Agendador, fora do job da sessão.
$acao = New-ScheduledTaskAction -Execute $Python -Argument $argumentos -WorkingDirectory $AgentDir
$gatilho = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId $eu -LogonType S4U -RunLevel Highest
$ajustes = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
             -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew `
             -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $Tarefa -Action $acao -Trigger $gatilho -Principal $principal -Settings $ajustes | Out-Null
Write-Host "tarefa '$Tarefa' registrada: sobe no boot e religa se o agente cair."
if (-not $NaoIniciar) {
  Start-ScheduledTask -TaskName $Tarefa
  Write-Host "tarefa iniciada. Acompanhe em $logAgente"
}
