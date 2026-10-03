<#
.SYNOPSIS
  Inicia a POC: backend (processo único, 127.0.0.1:8000) que sobe o Appium local e serve o painel.
  Os emuladores são iniciados pelo painel (ou com -StartInstances N).
.PARAMETER Dev
  Também inicia o Vite (127.0.0.1:5173) com hot reload do frontend.
.PARAMETER StartInstances
  Quantidade de instâncias a iniciar após o backend ficar pronto (0 = nenhuma).
.PARAMETER Instalar
  Registra o backend como tarefa supervisionada (sobe no boot, religa se cair) em vez de iniciá-lo nesta sessão.
  Delega para scripts\install-central-service.ps1; `-Simular` mostra o que seria registrado sem tocar em nada.
  Existe porque um backend iniciado à mão numa sessão interativa morre no logoff e não volta (achado #136).
#>
[CmdletBinding()]
param([switch]$Dev, [int]$StartInstances = 0, [switch]$NoBrowser, [switch]$Simulated,
      [switch]$Instalar, [switch]$Simular)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
if ($Instalar) {
  # Repasse EXPLÍCITO, nunca por splat de array. `& script @extras` com `@('-Simular')` liga o texto ao primeiro
  # parâmetro POSICIONAL do outro script — aqui, `-Tarefa` — e o `-Simular` some. Medido da pior forma: a
  # chamada registrou (e iniciou) uma tarefa agendada chamada "-Simular" que um ensaio jamais deveria criar.
  $instalador = Join-Path $PSScriptRoot 'install-central-service.ps1'
  if ($Simular) { & $instalador -Simular } else { & $instalador }
  return
}
$py = Join-Path $root 'backend\.venv\Scripts\python.exe'
$data = Join-Path $root 'data'
New-Item -ItemType Directory -Force (Join-Path $data 'logs') | Out-Null

if (-not (Test-Path $py)) {
  Write-Host 'Criando o ambiente Python (backend\.venv)…'
  Push-Location (Join-Path $root 'backend')
  try { uv venv --python 3.13 .venv; uv pip install -r requirements.txt --python .venv\Scripts\python.exe } finally { Pop-Location }
}
# Nascer do exemplo é certo na PRIMEIRA partida e desastroso em qualquer outra, e a diferença entre as duas não
# é o arquivo que falta — é o banco que existe. Medido: `config/config.yaml` deixou de ser versionado, e um
# `git checkout` para o commit novo APAGOU o arquivo da árvore, porque no commit velho ele era rastreado. A
# partida seguinte achou que era instalação nova, copiou o exemplo por cima e o backend subiu com
# `worker_port: 0` e ZERO aparelhos remotos. Nada reclamou: `/api/health` conferia commit e migração, que
# estavam certos. O parque simplesmente ficou menor e o canal do túnel, fechado.
#
# Então: banco com dados + configuração ausente = configuração PERDIDA, e a partida para. Recriar do exemplo
# aqui é escolher a leitura otimista num caso em que a pessimista é a única compatível com os fatos.
function Semear($arquivo, $exemplo, $oQueE) {
  if (Test-Path $arquivo) { return }
  # O marcador de "instalação existente" NÃO pode ser só o SQLite: numa instalação PostgreSQL não há
  # `poc.sqlite3`, e o guard deixaria passar exatamente o caso que existe para pegar. `credentials.key` nasce
  # na primeira subida em qualquer banco, e some só se alguém apagar `data/` — aí é instalação nova mesmo.
  $banco = Join-Path $root 'data\poc.sqlite3'
  $chave = Join-Path $root 'data\credentials.key'
  if ((Test-Path $banco) -or (Test-Path $chave)) {
    $copias = Join-Path $root 'data\backups'
    $nome = Split-Path $arquivo -Leaf
    $achado = Get-ChildItem $copias -Directory -ErrorAction SilentlyContinue |
              Sort-Object Name -Descending |
              Where-Object { Test-Path (Join-Path $_.FullName (Join-Path 'config' $nome)) } |
              Select-Object -First 1
    $onde = if ($achado) { "A cópia mais recente que ainda o tem: $($achado.FullName)\config\$nome" }
            else { "Nenhuma cópia em $copias ainda tem esse arquivo — procure mais atrás, ou refaça a mão." }
    $prova = if (Test-Path $banco) { $banco } else { $chave }
    throw ("$oQueE não existe, mas $prova existe: esta instalação NÃO é nova, então o arquivo foi perdido " +
           "(um 'git checkout' apaga o que era rastreado no commit anterior). Recriar do exemplo subiria o " +
           "backend com outra configuração — sem aparelhos remotos e sem o canal do túnel — sem nada reclamar. " +
           "$onde  Restaure-o e rode de novo; ou, se esta instalação é mesmo nova, apague/renomeie o banco.")
  }
  Copy-Item $exemplo $arquivo
  Write-Host "$oQueE criado a partir do exemplo (primeira partida: não havia banco)."
}

Semear (Join-Path $root '.env') (Join-Path $root '.env.example') '.env'
# `config/config.yaml` é de CADA instalação e não é versionado (achado #177): na primeira partida ele nasce
# do exemplo neutro — 4 emuladores locais, sem remoto e sem loja —, como o `.env`. Nunca sobrescreve o que já existe.
$cfg = Join-Path $root 'config\config.yaml'
Semear $cfg (Join-Path $root 'config\config.example.yaml') 'config\config.yaml'
if (-not $Dev -and -not (Test-Path (Join-Path $root 'frontend\dist\index.html'))) {
  Write-Host 'Compilando o frontend (frontend\dist)…'
  Push-Location (Join-Path $root 'frontend')
  try { if (-not (Test-Path node_modules)) { npm ci --no-audit --no-fund }; npm run build } finally { Pop-Location }
}

. (Join-Path $PSScriptRoot 'lib\farm-health.ps1')   # "responde na porta" não é "a Farm responde" (26/09/2026)
$base = 'http://127.0.0.1:8000'
$up = [bool](Get-FarmHealth $base 2)
if ($up) { Write-Host 'Backend já está em execução.' }
else {
  if ($Simulated) { $env:AI_PROVIDER = 'simulated'; Write-Warning 'MODO SIMULADO: nenhuma IA será consultada.' }
  $p = Start-Process -FilePath $py -ArgumentList '-m', 'app.main' -WorkingDirectory (Join-Path $root 'backend') -PassThru -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $data 'logs\backend.out.log') -RedirectStandardError (Join-Path $data 'logs\backend.err.log')
  Set-Content -Path (Join-Path $data 'backend.pid') -Value $p.Id
  Write-Host "Backend iniciado (pid $($p.Id)). Aguardando ficar pronto…"
  for ($i = 0; $i -lt 90; $i++) {
    Start-Sleep -Seconds 1
    $h = Get-FarmHealth $base 2
    if ($h) { break }
    if ($p.HasExited) { Get-Content (Join-Path $data 'logs\backend.err.log') -Tail 20; throw 'O backend encerrou ao iniciar.' }
  }
  if (-not $h) { throw 'O backend não respondeu em 90 s. Veja data\logs\backend.err.log' }
  Write-Host "Saúde: $($h.status) | IA: $($h.ai.provider) configurada=$($h.ai.configured) simulada=$($h.ai.simulated) | Appium: $($h.appium.running)"
  $h.problems | ForEach-Object { Write-Warning "$($_.message) → $($_.hint)" }
}
if ($Dev) {
  Start-Process -FilePath 'npm.cmd' -ArgumentList 'run', 'dev' -WorkingDirectory (Join-Path $root 'frontend') -WindowStyle Minimized
  $url = 'http://127.0.0.1:5173/central/'
} else { $url = $base }
if ($StartInstances -gt 0) {
  # Achado #153: gerar `android-{0:d2}` de 1..N mandava `start` a aparelho de OUTRA máquina no dia em que
  # android-09/10 viraram remotos — e prendia o parque a um teto de 10 que ele já passou. A lista sai de quem
  # existe de verdade: os emuladores DESTE host (sem a loja, sem os de worker), em ordem.
  $locais = @(Invoke-RestMethod "$base/api/instances" | Where-Object { $_.kind -eq 'emulator' } | Sort-Object id | ForEach-Object { $_.id })
  $ids = @($locais | Select-Object -First $StartInstances)
  if (-not $ids.Count) { throw 'Nenhum emulador local configurado (instances.count em config\config.yaml).' }
  $body = @{ ids = $ids; action = 'start' } | ConvertTo-Json
  $r = Invoke-RestMethod -Method Post "$base/api/instances/bulk" -ContentType 'application/json' -Body $body
  Write-Host "Iniciando: $($r.accepted -join ', ')"
}
Write-Host "Painel: $url"
if (-not $NoBrowser) { Start-Process $url }
