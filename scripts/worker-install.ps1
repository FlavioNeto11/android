<#
.SYNOPSIS
  Instala (ou atualiza) o AGENTE do parque numa máquina Windows: pacote, ambiente Python, configuração e serviço.

.DESCRIPTION
  Achado #14: cadastrar um servidor era manual e sem roteiro — `docs/worker.md` não dizia sequer COMO o código
  do agente chega à máquina (`grep 'worker-requirements|git clone|venv' docs/worker.md` = vazio), e o passo do
  serviço era prosa. O que existia na máquina real (`C:\farm\run-agente.ps1`, `agente-tarefa.ps1`) não estava
  versionado, e `C:\farm\agent` não era checkout: o central não tinha como saber que código rodava lá.

  O que este script faz, nesta ordem:

  1. **Copia o pacote do agente** — só os módulos que ele importa de verdade (`app.worker`, `app.workers`,
     `app.devices`, `app.security`, `app.config`, `app.util`, `app.version`), conferido por importação real.
  2. **Grava `app\BUILD_VERSION`** com a versão derivada do commit desta árvore. É o que faz o agente se
     declarar como `0.1.0+<sha7>` lá, e o central marcar "agente defasado" quando o número divergir do dele.
     Sem isto os dois lados diziam `0.1.0` para sempre.
  3. **Cria o venv** e instala `worker-requirements.txt` (sete dependências, não as sessenta do backend).
  4. **Semeia a configuração** a partir de `config\worker.example.yaml`, se ainda não houver uma.
  5. **Registra o serviço** chamando `worker-agent.ps1` (tarefa AtStartup, S4U, reinício em falha).

  ATUALIZAR o agente é rodar isto de novo: ele para a tarefa, troca os arquivos e a religa. A credencial
  permanente (`worker-credential.json`) e o `worker.yaml` NÃO são tocados.

.PARAMETER Origem
  Raiz da árvore do projeto de onde copiar. O padrão é a árvore deste script — o caso de rodar no worker com o
  repositório clonado lá. Copiando de um compartilhamento de rede, aponte para ele.

.PARAMETER Inscrever
  Token de inscrição de uso único, repassado ao `worker-agent.ps1`. Só na primeira instalação.

.PARAMETER Simular
  Imprime o plano e sai, sem copiar arquivo, criar venv nem registrar tarefa.

.EXAMPLE
  pwsh -File scripts\worker-install.ps1 -Simular
  pwsh -File scripts\worker-install.ps1 -Inscrever <token>
  pwsh -File scripts\worker-install.ps1               # atualização do agente
#>
[CmdletBinding()]
param(
  [string]$Destino  = 'C:\farm\agent',
  [string]$WorkDir  = 'C:\farm',
  [string]$Config   = '',
  [string]$Origem   = '',
  [string]$Tarefa   = 'farm-agente',
  [string]$Inscrever = '',
  [switch]$NaoIniciar,
  [switch]$Simular
)
$ErrorActionPreference = 'Stop'
if (-not $Origem) { $Origem = Split-Path -Parent $PSScriptRoot }
if (-not $Config) { $Config = Join-Path $WorkDir 'worker.yaml' }
$origemApp = Join-Path $Origem 'backend\app'
$requisitos = Join-Path $Origem 'backend\worker-requirements.txt'
$exemplo = Join-Path $Origem 'config\worker.example.yaml'

# O que o agente REALMENTE importa. Conferido rodando `import app.worker.agent` e listando `sys.modules`:
# app.config, app.util, app.version, app.devices.*, app.security.redaction, app.worker.*, app.workers.protocol.
$pastas = @('worker', 'workers', 'devices', 'security')
$arquivos = @('__init__.py', 'config.py', 'util.py', 'version.py', 'metricas.py')

foreach ($caminho in @($origemApp, $requisitos, $exemplo)) {
  if (-not (Test-Path -LiteralPath $caminho)) { throw "não encontrei $caminho — confira -Origem." }
}

# A versão que o agente vai declarar. Derivada do commit desta árvore pelo MESMO código que o central usa, sem
# chamar `git`: a máquina do worker não é checkout e `git` pode nem estar no PATH dela.
#
# `Test-Path` ANTES do `&`: com $ErrorActionPreference='Stop', chamar um executável que não existe é erro
# TERMINANTE, e o caminho de baixo — o que existe justamente para a máquina que não tem o venv do central —
# nunca seria alcançado.
$pyDaOrigem = Join-Path $Origem 'backend\.venv\Scripts\python.exe'
$versao = ''
if (Test-Path -LiteralPath $pyDaOrigem) {
  $versao = & $pyDaOrigem -c `
              "import sys; sys.path.insert(0, r'$Origem\backend'); from app.version import agent_version; print(agent_version())" 2>$null
}
if (-not $versao) {
  # Sem o venv do central à mão (o caso de copiar de um compartilhamento), lê o `.git` da árvore de origem.
  $cabeca = (Get-Content -LiteralPath (Join-Path $Origem '.git\HEAD') -ErrorAction SilentlyContinue)
  if ($cabeca -and $cabeca.StartsWith('ref:')) {
    $ref = $cabeca.Substring(4).Trim()
    $sha = (Get-Content -LiteralPath (Join-Path $Origem ".git\$ref") -ErrorAction SilentlyContinue)
  } else { $sha = $cabeca }
  $versao = if ($sha) { "0.1.0+$($sha.Substring(0, 7))" } else { '0.1.0+desconhecido' }
}
$versao = $versao.Trim()

if ($Simular) {
  Write-Output "origem: $Origem"
  Write-Output "destino: $Destino"
  Write-Output "versao: $versao"
  Write-Output "pastas: $($pastas -join ', ')"
  Write-Output "arquivos: $($arquivos -join ', ')"
  Write-Output "requisitos: $requisitos"
  Write-Output "config: $Config"
  Write-Output "tarefa: $Tarefa"
  Write-Output 'simulacao: nada foi copiado, instalado nem registrado'
  return
}

# ---------------------------------------------------------------- 1. parar o agente antes de trocar os arquivos
if (Get-ScheduledTask -TaskName $Tarefa -ErrorAction SilentlyContinue) {
  Write-Host "parando a tarefa '$Tarefa' antes de trocar os arquivos…"
  Stop-ScheduledTask -TaskName $Tarefa -ErrorAction SilentlyContinue
  Start-Sleep -Seconds 3
}

# ---------------------------------------------------------------- 2. copiar o pacote
$destinoApp = Join-Path $Destino 'app'
New-Item -ItemType Directory -Force $destinoApp | Out-Null
foreach ($pasta in $pastas) {
  $alvo = Join-Path $destinoApp $pasta
  if (Test-Path -LiteralPath $alvo) { Remove-Item -LiteralPath $alvo -Recurse -Force }
  Copy-Item -LiteralPath (Join-Path $origemApp $pasta) -Destination $alvo -Recurse -Force
  # `__pycache__` do outro Python (o central roda 3.13, o worker pode rodar 3.12) só confunde diagnóstico.
  Get-ChildItem -LiteralPath $alvo -Directory -Recurse -Filter '__pycache__' |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
}
foreach ($arquivo in $arquivos) {
  Copy-Item -LiteralPath (Join-Path $origemApp $arquivo) -Destination (Join-Path $destinoApp $arquivo) -Force
}
Set-Content -LiteralPath (Join-Path $destinoApp 'BUILD_VERSION') -Value $versao -Encoding ascii
Copy-Item -LiteralPath $requisitos -Destination (Join-Path $Destino 'worker-requirements.txt') -Force
Write-Host "pacote do agente em $Destino (versao $versao)"

# ---------------------------------------------------------------- 3. ambiente Python
$py = Join-Path $Destino '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $py)) {
  $base = (Get-Command python -ErrorAction SilentlyContinue).Source
  if (-not $base) { throw 'python não está no PATH desta máquina. Instale o Python 3.12 ou mais novo.' }
  Write-Host 'criando o ambiente Python do agente…'
  & $base -m venv (Join-Path $Destino '.venv')
}
& $py -m pip install --disable-pip-version-check -q -r (Join-Path $Destino 'worker-requirements.txt')
Write-Host 'dependências do agente instaladas (sete, não as do backend).'

# ---------------------------------------------------------------- 4. configuração
New-Item -ItemType Directory -Force $WorkDir | Out-Null
if (-not (Test-Path -LiteralPath $Config)) {
  Copy-Item -LiteralPath $exemplo -Destination $Config -Force
  # Parênteses: em modo de ARGUMENTO, `"a" + "b"` não concatena — o `+` vira um argumento posicional a mais.
  Write-Warning ("configuração criada em $Config a partir do exemplo. AJUSTE server, worker_id, name e " +
                 "devices ANTES de inscrever — o exemplo não descreve esta máquina.")
} else {
  Write-Host "configuração já existente em $Config (não foi tocada)."
}

# ---------------------------------------------------------------- 5. serviço
$agente = Join-Path $PSScriptRoot 'worker-agent.ps1'
# Repasse EXPLÍCITO: splat de array liga o texto ao primeiro parâmetro POSICIONAL do outro script.
if ($Inscrever) {
  if ($NaoIniciar) { & $agente -Tarefa $Tarefa -AgentDir $Destino -Config $Config -WorkDir $WorkDir -Python $py -Inscrever $Inscrever -NaoIniciar }
  else             { & $agente -Tarefa $Tarefa -AgentDir $Destino -Config $Config -WorkDir $WorkDir -Python $py -Inscrever $Inscrever }
} else {
  if ($NaoIniciar) { & $agente -Tarefa $Tarefa -AgentDir $Destino -Config $Config -WorkDir $WorkDir -Python $py -Instalar -NaoIniciar }
  else             { & $agente -Tarefa $Tarefa -AgentDir $Destino -Config $Config -WorkDir $WorkDir -Python $py -Instalar }
}
