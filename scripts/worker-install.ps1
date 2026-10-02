<#
.SYNOPSIS
  Instala (ou atualiza) o AGENTE do parque numa máquina Windows: pacote, ambiente Python, configuração e serviço.

.DESCRIPTION
  Achado #14: cadastrar um servidor era manual e sem roteiro — `docs/worker.md` não dizia sequer COMO o código
  do agente chega à máquina (`grep 'worker-requirements|git clone|venv' docs/worker.md` = vazio), e o passo do
  serviço era prosa. O que existia na máquina real (`C:\farm\run-agente.ps1`, `agente-tarefa.ps1`) não estava
  versionado, e `C:\farm\agent` não era checkout: o central não tinha como saber que código rodava lá.

  O que este script faz, nesta ordem:

  1. **Copia o pacote do agente** — exatamente o que `backend\worker-manifest.txt` lista, que é o fecho de
     import de `app.worker.*` (conferido por `tests/test_pacote_do_agente.py`). A árvore nova é montada ao lado
     (`app.novo`) e só então troca a antiga: o que SAIU do manifesto sai também da máquina.
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

.PARAMETER SoPacote
  Monta só o pacote em -Destino (`app\`, `BUILD_VERSION`, `worker-requirements.txt`) e sai: sem parar a tarefa,
  criar venv nem registrar serviço. Serve para levar o pacote à mão e para o teste provar a cópia DESTE script.

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
  [switch]$Simular,
  [switch]$SoPacote
)
$ErrorActionPreference = 'Stop'
if (-not $Origem) { $Origem = Split-Path -Parent $PSScriptRoot }
if (-not $Config) { $Config = Join-Path $WorkDir 'worker.yaml' }
$origemApp = Join-Path $Origem 'backend\app'
$requisitos = Join-Path $Origem 'backend\worker-requirements.txt'
$exemplo = Join-Path $Origem 'config\worker.example.yaml'
$manifesto = Join-Path $Origem 'backend\worker-manifest.txt'

foreach ($caminho in @($origemApp, $requisitos, $exemplo)) {
  if (-not (Test-Path -LiteralPath $caminho)) { throw "não encontrei $caminho — confira -Origem." }
}

# O que o agente REALMENTE importa vem do manifesto DA ORIGEM, e não de uma lista aqui: a lista descreve o código
# que está sendo copiado. Sem lista de reserva embutida de propósito — ela seria a quarta cópia à mão (K-034).
if (-not (Test-Path -LiteralPath $manifesto)) {
  throw ("não encontrei $manifesto — a origem é de um commit anterior ao manifesto do agente. " +
         'Instale com o scripts\worker-install.ps1 daquele mesmo commit.')
}
$pacote = @(Get-Content -LiteralPath $manifesto -Encoding UTF8 |
            ForEach-Object { ($_ -replace '#.*$', '').Trim() } | Where-Object { $_ })
if ($pacote.Count -eq 0) { throw "o manifesto $manifesto está vazio." }
foreach ($entrada in $pacote) {
  # Relativo a backend\app, com barra normal (o mesmo arquivo serve ao instalador Linux), sem sair da pasta.
  if ($entrada -match '^[\\/]|^[A-Za-z]:|\.\.|\\') { throw "entrada inválida no manifesto: '$entrada'" }
  $tipo = if ($entrada.EndsWith('/')) { 'Container' } else { 'Leaf' }
  if (-not (Test-Path -LiteralPath (Join-Path $origemApp $entrada.TrimEnd('/')) -PathType $tipo)) {
    throw "o manifesto lista '$entrada', que não existe em $origemApp — manifesto e código fora de sincronia."
  }
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
  # Espelha `app/version.py::_pastas_do_git`: num `git worktree` o `.git` é um ARQUIVO `gitdir: <caminho>`; o HEAD
  # fica na pasta do worktree e as refs na pasta comum (arquivo `commondir`) ou em `packed-refs`.
  $git = Join-Path $Origem '.git'
  if (Test-Path -LiteralPath $git -PathType Leaf) {
    $alvo = (Get-Content -LiteralPath $git -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ($alvo -and $alvo.StartsWith('gitdir:')) {
      $git = $alvo.Substring(7).Trim()
      if (-not [System.IO.Path]::IsPathRooted($git)) { $git = [System.IO.Path]::GetFullPath((Join-Path $Origem $git)) }
    }
  }
  $comum = $git
  $arqComum = Join-Path $git 'commondir'
  if (Test-Path -LiteralPath $arqComum -PathType Leaf) {
    $c = (Get-Content -LiteralPath $arqComum -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ($c) {
      $c = $c.Trim()
      $comum = if ([System.IO.Path]::IsPathRooted($c)) { $c } else { [System.IO.Path]::GetFullPath((Join-Path $git $c)) }
    }
  }
  $sha = $null
  $cabeca = (Get-Content -LiteralPath (Join-Path $git 'HEAD') -ErrorAction SilentlyContinue | Select-Object -First 1)
  if ($cabeca -and $cabeca.StartsWith('ref:')) {
    $ref = $cabeca.Substring(4).Trim()
    foreach ($pasta in @($git, $comum)) {
      $arq = Join-Path $pasta $ref
      if (-not $sha -and (Test-Path -LiteralPath $arq -PathType Leaf)) {
        $sha = (Get-Content -LiteralPath $arq -ErrorAction SilentlyContinue | Select-Object -First 1)
      }
    }
    $empacotadas = Join-Path $comum 'packed-refs'      # `git gc` move as refs soltas para cá
    if (-not $sha -and (Test-Path -LiteralPath $empacotadas -PathType Leaf)) {
      foreach ($linha in (Get-Content -LiteralPath $empacotadas -ErrorAction SilentlyContinue)) {
        if ($linha -notmatch '^[#^]' -and $linha.Trim().EndsWith(" $ref")) { $sha = $linha.Trim().Split(' ')[0]; break }
      }
    }
  } else { $sha = $cabeca }
  $sha = if ($sha) { $sha.Trim() } else { $null }
  $versao = if ($sha -and $sha.Length -ge 7) { "0.1.0+$($sha.Substring(0, 7))" } else { '0.1.0+desconhecido' }
}
$versao = $versao.Trim()

if ($Simular) {
  Write-Output "origem: $Origem"
  Write-Output "destino: $Destino"
  Write-Output "versao: $versao"
  Write-Output "manifesto: $manifesto"
  Write-Output "pacote: $($pacote -join ', ')"
  Write-Output "requisitos: $requisitos"
  Write-Output "config: $Config"
  Write-Output "tarefa: $Tarefa"
  Write-Output 'simulacao: nada foi copiado, instalado nem registrado'
  return
}

# Monta `app\` pelo manifesto numa pasta AO LADO e só então troca a antiga. Antes só as pastas listadas eram
# substituídas, e o que saía da lista ficava para sempre na máquina; e uma cópia interrompida no meio deixava o
# agente com meia árvore. `$Destino` guarda só `app\`, `.venv` e `worker-requirements.txt` — `worker.yaml`, a
# credencial e os logs moram em `$WorkDir` —, então trocar `app\` inteira não apaga nada da máquina.
function Copiar-Pacote {
  $destinoApp = Join-Path $Destino 'app'
  $novo = Join-Path $Destino 'app.novo'
  if (Test-Path -LiteralPath $novo) { Remove-Item -LiteralPath $novo -Recurse -Force }
  New-Item -ItemType Directory -Force $novo | Out-Null
  foreach ($entrada in $pacote) {
    $rel = $entrada.TrimEnd('/')
    $alvo = Join-Path $novo $rel
    New-Item -ItemType Directory -Force (Split-Path -Parent $alvo) | Out-Null
    Copy-Item -LiteralPath (Join-Path $origemApp $rel) -Destination $alvo -Recurse -Force
  }
  # `__pycache__` do outro Python (o central roda 3.13, o worker pode rodar 3.12) só confunde diagnóstico.
  Get-ChildItem -LiteralPath $novo -Directory -Recurse -Filter '__pycache__' |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
  Set-Content -LiteralPath (Join-Path $novo 'BUILD_VERSION') -Value $versao -Encoding ascii
  if (Test-Path -LiteralPath $destinoApp) { Remove-Item -LiteralPath $destinoApp -Recurse -Force }
  Move-Item -LiteralPath $novo -Destination $destinoApp
  Copy-Item -LiteralPath $requisitos -Destination (Join-Path $Destino 'worker-requirements.txt') -Force
}

if ($SoPacote) {
  New-Item -ItemType Directory -Force $Destino | Out-Null
  Copiar-Pacote
  Write-Output "pacote do agente montado em $Destino (versao $versao): nada foi parado, instalado nem registrado"
  return
}

# ---------------------------------------------------------------- 1. parar o agente antes de trocar os arquivos
if (Get-ScheduledTask -TaskName $Tarefa -ErrorAction SilentlyContinue) {
  Write-Host "parando a tarefa '$Tarefa' antes de trocar os arquivos…"
  Stop-ScheduledTask -TaskName $Tarefa -ErrorAction SilentlyContinue
  Start-Sleep -Seconds 3
}

# ---------------------------------------------------------------- 2. copiar o pacote
New-Item -ItemType Directory -Force $Destino | Out-Null
Copiar-Pacote
Write-Host "pacote do agente em $Destino (versao $versao, $($pacote.Count) entradas do manifesto)"

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
