<#
.SYNOPSIS
  Subir o código atual na produção: parar → copiar o banco → subir → conferir. Nesta ordem, sempre.

.DESCRIPTION
  Existe porque a subida que importava aconteceu sem ele. O backend que servia o parque foi iniciado ANTES das
  migrações `016_step_ownership` e `017_busca_sem_acento` existirem: o processo era de 21/09 16:05, as migrações
  foram escritas às 16:19 e 16:59, e o banco vivo parava em `015_workers`. Ou seja, a primeira subida do código
  atual sobre os dados reais ainda não tinha acontecido — e ia acontecer no próximo restart, planejado ou não,
  **sem cópia prévia do banco**, porque não havia procedimento.

  O que este script garante, e que um `stop` seguido de `start` não garante:

  1. A cópia do banco é feita com o backend ainda no ar (consistente, sem parar nada) e **antes** de qualquer
     migração tocar nos dados. Se a subida der errado, existe para onde voltar.
  2. A migração não é um passo separado que alguém esquece: `AppState.__init__` chama `db.migrate()` na subida.
     Este script não migra — ele **confere** que migrou, comparando a última migração aplicada com o último
     arquivo de `backend/migrations/`.
  3. A conferência é do código em execução, não da intenção: `/api/health` passou a devolver `commit` e
     `migration`, e aqui eles são comparados com o `git rev-parse HEAD` da árvore.

  Nada aqui é irreversível sem aviso: com `-Ensaio` o script faz backup e confere, e não para o backend.

.PARAMETER Ensaio
  Faz o backup e a conferência do que ESTÁ no ar, sem parar nada. É o passo que se roda antes de marcar a janela.

.PARAMETER StopEmulators
  Repassa para stop.ps1: desliga também os emuladores que o projeto iniciou.

.PARAMETER PularBackup
  Só para reexecutar a conferência. Não use numa subida de verdade.

.PARAMETER PularFrontend
  Não reconstrói `frontend/dist`. Só quando o que mudou é comprovadamente backend, ou quando o `npm` não está
  disponível na máquina — e aí o painel servido continua sendo o do build anterior.

.EXAMPLE
  pwsh -File scripts\deploy.ps1 -Ensaio      # sem janela: só backup + retrato do que está no ar
  pwsh -File scripts\deploy.ps1              # a subida
#>
[CmdletBinding()]
param([switch]$Ensaio, [switch]$StopEmulators, [switch]$PularBackup, [switch]$PularFrontend)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$base = 'http://127.0.0.1:8000'

function Saude {
  try { return Invoke-RestMethod "$base/api/health" -TimeoutSec 3 } catch { return $null }
}

$esperadoCommit = (& git -C $root rev-parse HEAD 2>$null)
$ultimaMigracao = (Get-ChildItem (Join-Path $root 'backend\migrations') -Filter '*.sql' |
                   Sort-Object Name | Select-Object -Last 1).BaseName
Write-Host "árvore: commit $esperadoCommit | última migração no código: $ultimaMigracao"

$antes = Saude
if ($antes) {
  Write-Host ("no ar agora: commit $($antes.commit ?? '(backend anterior a esta mudança)') | " +
              "migração $($antes.migration ?? '?') | status $($antes.status)")
} else {
  Write-Host 'no ar agora: nada respondeu em /api/health.'
}

# ------------------------------------------------------------------ 1. cópia, ANTES de qualquer coisa
if (-not $PularBackup) {
  Write-Host '--- backup (com o backend no ar; a API de backup do SQLite é consistente) ---'
  & pwsh -NoProfile -File (Join-Path $PSScriptRoot 'backup.ps1') -IncluirSegredos
  if ($LASTEXITCODE -ne 0) { throw 'o backup falhou; a subida NÃO continua sem cópia do banco.' }
}

if ($Ensaio) {
  Write-Host ''
  Write-Host 'ENSAIO: backup feito, nada foi parado. Para ensaiar a migração sem tocar na produção:'
  Write-Host '  pwsh -File scripts\restore.ps1 -De data\backups\<carimbo> -Para C:\temp\ensaio-016-017'
  Write-Host '  cd backend; .venv\Scripts\python.exe -c "from app.db import Database; d=Database(r''C:\temp\ensaio-016-017\poc.sqlite3''); print(d.migrate())"'
  Write-Host '  (aplica 016/017 na CÓPIA e imprime o que aplicou; o banco de produção não é tocado)'
  return
}

# ------------------------------------------------------------------ 2. o painel
# Isto faltava, e o sintoma não denuncia a causa: o backend sobe no commit novo, `/api/health` confere commit e
# migração, tudo diz "ok" — e o navegador continua servindo o bundle antigo, porque `frontend/dist` é ARTEFATO e
# não acompanha o `git pull`. Medido em produção: depois de um deploy conferido, o `dist` no ar era de dois dias
# antes, sem nenhuma das telas novas. A conferência de commit não pega isso porque ela olha o processo Python.
#
# Antes do stop de propósito: o build é a parte lenta e não precisa de janela. O `StaticFiles` lê do disco a cada
# requisição, então trocar o `dist` com o backend no ar é inofensivo — e o restart logo abaixo fecha qualquer
# dúvida. Depois do portão do -Ensaio também de propósito: ensaio não mexe no que está sendo servido.
if (-not $PularFrontend) {
  Write-Host '--- reconstruindo o painel (frontend/dist é artefato: não vem no git pull) ---'
  $front = Join-Path $root 'frontend'
  if (-not (Test-Path (Join-Path $front 'node_modules'))) {
    Write-Host '    node_modules ausente: npm ci'
    & npm --prefix $front ci
    if ($LASTEXITCODE -ne 0) { throw 'npm ci falhou; o painel não seria reconstruído.' }
  }
  & npm --prefix $front run build
  if ($LASTEXITCODE -ne 0) { throw 'o build do painel falhou; a subida NÃO continua servindo bundle velho.' }

  # Conferir o RESULTADO, não o código de saída: um build que "passa" e não escreve nada deixaria o bundle antigo
  # no ar exatamente como antes desta etapa existir.
  $dist = Join-Path $front 'dist'
  $maisNovo = Get-ChildItem $dist -Recurse -File -ErrorAction SilentlyContinue |
              Sort-Object LastWriteTime | Select-Object -Last 1
  if (-not $maisNovo) { throw "o build terminou sem erro e $dist está vazio." }
  $idade = (Get-Date) - $maisNovo.LastWriteTime
  if ($idade.TotalMinutes -gt 10) {
    throw ("o build terminou sem erro e o arquivo mais novo de dist é de " +
           "$($maisNovo.LastWriteTime) — nada foi reescrito.")
  }
  Write-Host ("    dist reconstruído: $($maisNovo.Name), $($maisNovo.LastWriteTime)")
}

# ------------------------------------------------------------------ 3. parar
Write-Host '--- parando o backend ---'
& pwsh -NoProfile -File (Join-Path $PSScriptRoot 'stop.ps1') @(if ($StopEmulators) { '-StopEmulators' })
if ((Saude) -ne $null) { throw 'o backend ainda responde depois do stop; não suba um segundo dono do banco.' }

# ------------------------------------------------------------------ 4. subir (a migração acontece aqui)
Write-Host '--- subindo (AppState aplica as migrações pendentes na inicialização) ---'
& pwsh -NoProfile -File (Join-Path $PSScriptRoot 'start.ps1') -NoBrowser
if ($LASTEXITCODE -ne 0) { throw 'o start falhou; veja data\logs\backend.err.log' }

# ------------------------------------------------------------------ 5. conferir o que subiu
$depois = Saude
if (-not $depois) { throw '/api/health não respondeu depois da subida.' }
$problemas = @()
if ($esperadoCommit -and $depois.commit -ne $esperadoCommit) {
  $problemas += "o processo no ar diz commit '$($depois.commit)' e a árvore está em '$esperadoCommit'"
}
if ($depois.migration -ne $ultimaMigracao) {
  $problemas += "o banco está em '$($depois.migration)' e o código traz até '$ultimaMigracao'"
}
Write-Host ''
Write-Host ("depois da subida: status $($depois.status) | commit $($depois.commit) | migração $($depois.migration)")
$depois.problems | ForEach-Object { Write-Warning "$($_.message) → $($_.hint)" }
if ($problemas) { throw ("a subida não confere: " + ($problemas -join '; ')) }

Write-Host ''
Write-Host 'PRÓXIMO PASSO, MANUAL: o túnel do worker.'
Write-Host '  O -R antigo aponta para a porta 8000 e deixa a API inteira ao alcance da máquina do worker.'
Write-Host '  A ORDEM importa: só reaponte DEPOIS desta subida. Antes dela nada escuta na 8010, e o agente iria'
Write-Host '  para uma porta fechada sem voltar sozinho. Não há janela sem canal: o worker_router também está no'
Write-Host '  app principal, então enquanto o -R estiver na 8000 o WebSocket segue atendendo.'
Write-Host ''
Write-Host '  1) Confira que a 8010 está escutando:  Get-NetTCPConnection -LocalPort 8010 -State Listen'
Write-Host '  2) Confira o mapa SEM abrir túnel (o arquivo é que manda quando -MapaArquivo é passado):'
Write-Host "       pwsh -File scripts\worker-tunnel.ps1 -MostrarMapa -MapaArquivo data\tunnel\worker-lan-01.map"
Write-Host '     Têm de sair os SEIS pares. Passar -MapaArquivo SEM -Mapa derruba 4 dos 6 aparelhos: se o arquivo'
Write-Host '     ainda não existe, a resolução cai no -Mapa, cujo padrão tem só duas portas.'
Write-Host '  3) Só então reinstale a tarefa, com os dois argumentos:'
Write-Host "       pwsh -File scripts\worker-tunnel.ps1 -Instalar -Worker 192.168.1.19 -Usuario Administrator ``"
Write-Host "         -Chave `"`$HOME\.ssh\worker_ed25519`" ``"
Write-Host "         -Mapa '15555:5555,15557:5557,15559:5559,15561:5561,15563:5563,15565:5565' ``"
Write-Host "         -MapaArquivo data\tunnel\worker-lan-01.map -MapaReverso '18000:8010'"
Write-Host '  4) Confira do worker: GET http://127.0.0.1:18000/api/health deve dar 404 (e não 200).'
Write-Host ''
Write-Host 'E O AGENTE DO WORKER, que é cópia manual e não acompanha este deploy:'
Write-Host '  O central passou a RECUSAR resultado de comando sem cerca (fence), e a esperar `inflight` no hello.'
Write-Host '  Agente anterior a 89f5508 não manda cerca: os comandos dele terminariam em `uncertain` para sempre.'
Write-Host '  Copie backend/app/worker/ e backend/worker-requirements.txt para a máquina do worker e reinicie o'
Write-Host '  agente ANTES de considerar o deploy concluído. Confira na Infraestrutura que a batida voltou, e'
Write-Host '  mande um `stop` de teste num aparelho dele: tem de fechar como `succeeded`, não `uncertain`.'
Write-Host 'pronto.'
