<#
.SYNOPSIS
  Subir o código atual no ambiente central (desenvolvimento e validação do dono): parar → copiar o banco → subir → conferir. Nesta ordem, sempre.
  Com a tarefa `farm-central` registrada, "parar" e "subir" são da tarefa (o supervisor sobe o backend).

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
  Numa subida de verdade, só até 60 min depois de um `-Ensaio` no mesmo commit (a cópia dele vale para a subida);
  sem esse ensaio, o script recusa antes de parar qualquer coisa. Com `-Ensaio`, só reexecuta a conferência.

.PARAMETER PularFrontend
  Não reconstrói `frontend/dist`. Só quando o que mudou é comprovadamente backend, ou quando o `npm` não está
  disponível na máquina — e aí o painel servido continua sendo o do build anterior.

.PARAMETER SemTag
  Não cria a tag `deploy-AAAAMMDD-HHMM` nem o release no GitHub depois de uma subida conferida (29.159). A linha em
  `data\deploys.jsonl` é gravada sempre; a tag é no melhor esforço e nunca faz o deploy falhar.

.PARAMETER PularDependencias
  Não roda `uv pip install -r backend\requirements.txt` entre parar e subir. Só quando o `requirements.txt` não
  mudou e o `uv` não está disponível.

.PARAMETER PularDocsCheck
  Não roda `scripts\docs-check.py` antes de parar qualquer coisa (29.166). Só quando o erro que ele acusa é
  comprovadamente só de documentação e a subida não pode esperar; o passo existe para pegar `config.example.yaml` ou
  `.claude/plano-100.json` fora do formato ANTES do backend parar.

.EXAMPLE
  pwsh -File scripts\deploy.ps1 -Ensaio      # sem janela: só backup + retrato do que está no ar
  pwsh -File scripts\deploy.ps1              # a subida
#>
[CmdletBinding()]
param([switch]$Ensaio, [switch]$StopEmulators, [switch]$PularBackup, [switch]$PularFrontend, [switch]$PularDependencias, [switch]$SemTag, [switch]$PularDocsCheck)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$base = 'http://127.0.0.1:8000'

. (Join-Path $PSScriptRoot 'lib\farm-health.ps1')   # "responde na porta" não é "a Farm responde" (26/09/2026)
. (Join-Path $PSScriptRoot 'lib\historico-de-deploy.ps1')   # uma linha por subida e a tag do deploy (29.159)
function Saude {
  # Só a Farm conta: com ela parada, o `cartorio-api-1` (0.0.0.0:8000) respondia 404 aqui. A VERSÃO (commit) é
  # conferida depois, pelo chamador — a identidade não muda de um commit para outro.
  return Get-FarmHealth $base 3
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

# 29.159: o histórico do deploy. A linha só existe para subida de verdade que PAROU o backend (a que mexe no que está no
# ar), e é gravada nos dois desfechos: no fim, se a subida conferiu, e pelo `trap`, se algo lançou depois do stop. Um
# ensaio, uma recusa do portão e a falha do build do painel (antes de parar) não mudam nada no ar e não entram.
$inicioDoDeploy = Get-Date
$arquivoDeDeploys = Join-Path $root 'data\deploys.jsonl'
$script:subidaParou = $false
$script:subidaRegistrada = $false
$pastaDoBackup = $null
$backupDoEnsaio = $false
$opcoesDoDeploy = @($PSBoundParameters.Keys | Where-Object { $_ -ne 'Ensaio' } | Sort-Object)
trap {
  if ($script:subidaParou -and -not $script:subidaRegistrada) {
    $script:subidaRegistrada = $true
    Close-EtapaDoDeploy $estadoDeEtapas 'interrompida'   # o tempo gasto na etapa que falhou
    try {
      Add-RegistroDeDeploy -Caminho $arquivoDeDeploys -Registro (New-RegistroDeDeploy -Resultado 'falhou' `
        -CommitAntes $antes.commit -MigracaoAntes $antes.migration -CommitDepois $esperadoCommit `
        -Backup $pastaDoBackup -BackupDoEnsaio $backupDoEnsaio -Motivo $_.Exception.Message `
        -DuracaoS ((Get-Date) - $inicioDoDeploy).TotalSeconds -Opcoes $opcoesDoDeploy -EtapasS $estadoDeEtapas.etapas)
    } catch { Write-Warning "não consegui gravar a linha do histórico de deploys: $($_.Exception.Message)" }
  }
  throw $_
}

$estadoDeEtapas = New-EstadoDeEtapas   # tempo por etapa (29.156): vai à linha de data\deploys.jsonl e à tela
# ------------------------------------------------------------------ 1. cópia, ANTES de qualquer coisa
# Teto de 10 cópias de deploy (29.38): eram 161 cópias e 23 GB em 04/10, com dez deploys num dia.
$tetoDeCopias = 10
# >>> portão do -PularBackup (29.94: `scripts/tests/test_deploy_portao_do_ensaio.py` roda este trecho de verdade)
$minutosDoEnsaio = 60
if ($PularBackup -and -not $Ensaio) {
  # Subida sem cópia própria só logo depois de um `-Ensaio` da MESMA árvore: a cópia dele é a cópia desta subida.
  # Sem esse ensaio, recusa antes de parar qualquer coisa.
  . (Join-Path $PSScriptRoot 'lib\copias-de-backup.ps1')
  # 29.94: NÃO `$ensaio`. Variável no PowerShell não diferencia caixa, e `$ensaio` é o próprio `[switch]$Ensaio`:
  # a cópia (DirectoryInfo) não converte em switch, e o deploy 34 morreu aqui. Se convertesse, o `if ($Ensaio)`
  # adiante trataria a subida de verdade como ensaio.
  $copiaDoEnsaio = Find-EnsaioRecente (Join-Path $root 'data\backups') $esperadoCommit $minutosDoEnsaio
  if (-not $copiaDoEnsaio) {
    throw ("-PularBackup só vale até $minutosDoEnsaio min depois de um 'deploy.ps1 -Ensaio' no commit " +
           "$esperadoCommit, e não há cópia de ensaio assim em data\backups. Rode o -Ensaio ou suba sem -PularBackup.")
  }
  Write-Host "backup: a cópia do ensaio $($copiaDoEnsaio.Name) (mesmo commit, há menos de $minutosDoEnsaio min) vale para esta subida."
  $pastaDoBackup = $copiaDoEnsaio.Name
  $backupDoEnsaio = $true
}
# <<< portão do -PularBackup
if (-not $PularBackup) {
  Write-Host '--- backup (com o backend no ar; a API de backup do SQLite é consistente) ---'
  $origem = if ($Ensaio) { 'ensaio' } else { 'deploy' }
  & pwsh -NoProfile -File (Join-Path $PSScriptRoot 'backup.ps1') -IncluirSegredos -Origem $origem -Teto $tetoDeCopias
  if ($LASTEXITCODE -ne 0) { throw 'o backup falhou; a subida NÃO continua sem cópia do banco.' }
  # A pasta que o backup.ps1 acabou de criar é a mais nova de data\backups (ele a anuncia na linha `pronto:`).
  $pastaDoBackup = (Get-ChildItem (Join-Path $root 'data\backups') -Directory -ErrorAction SilentlyContinue |
                    Sort-Object LastWriteTime | Select-Object -Last 1).Name
}

Close-EtapaDoDeploy $estadoDeEtapas 'backup'
# ------------------------------------------------------------------ 1b. a pasta do site institucional (29.77)
# Com `portal.site_ligado`, um arquivo fora da lista de extensões em `site/` (um `Thumbs.db` que o Explorer deixou, um
# rascunho) DERRUBA a subida do central inteiro, de propósito (ADR-075): nada fora da lista vai à internet. Conferido
# aqui, antes de parar qualquer coisa e também no -Ensaio, para a recusa aparecer com o backend ainda no ar.
Write-Host '--- pasta do site institucional (29.77) ---'
Push-Location (Join-Path $root 'backend')
try {
  & (Join-Path $root 'backend\.venv\Scripts\python.exe') -c @'
import sys
from pathlib import Path
from app.config import get_config
from app.modules.portal.presentation.site import SiteInvalido, ler_site
try:
    ligado = get_config().file.portal.site_ligado
except Exception as e:  # o bloco `portal` recusa chave desconhecida (29.83): a subida falharia do mesmo jeito
    print(f"    config.yaml: {e}")
    sys.exit(2)
try:
    print(f"    site/: {len(ler_site(Path('..') / 'site'))} arquivo(s) na lista; portal.site_ligado={ligado}")
except (SiteInvalido, OSError) as e:
    print(f"    site/: {e}")
    sys.exit(1 if ligado else 0)
'@
} finally { Pop-Location }
if ($LASTEXITCODE -eq 2) {
  throw 'o config.yaml não carrega (acima, a chave): o central não subiria. Corrija o arquivo antes de parar qualquer coisa.'
}
if ($LASTEXITCODE -ne 0) {
  throw 'a pasta site/ tem arquivo fora da lista e portal.site_ligado está ligado: o central não subiria. Limpe a pasta.'
}

Close-EtapaDoDeploy $estadoDeEtapas 'site'
# ------------------------------------------------------------------ 1c. docs-check (29.166)
# O docs-check (links, IDs, mapa e, quando o 29.160 entrar, o formato de `config/config.example.yaml` e de
# `.claude/plano-100.json`) roda aqui, com o Python do venv do backend (que tem PyYAML e pydantic: sem eles o docs-check só
# AVISA que não conferiu), no ensaio e na subida de verdade, antes de parar qualquer coisa. ERRO recusa; AVISO não. O `config.yaml` da instalação nunca é aberto por ele.
$pythonDoBackend = Join-Path $root 'backend\.venv\Scripts\python.exe'
# >>> docs-check (29.166)
Write-Host '--- docs-check (29.166) ---'
if ($PularDocsCheck) {
  Write-Host '    PULADO por -PularDocsCheck: o formato dos arquivos de configuração versionados NÃO foi conferido.'
} else {
  $saidaDocs = & $pythonDoBackend (Join-Path $root 'scripts\docs-check.py') --raiz $root 2>&1
  $codigoDocs = $LASTEXITCODE
  $saidaDocs | ForEach-Object { Write-Host "    $_" }
  if (($saidaDocs -join "`n") -match 'NAO conferido|NÃO conferido') {
    Write-Warning 'o docs-check avisou que NÃO conferiu parte do formato (falta pacote no venv?): a subida segue, mas essa parte está sem prova.'
  }
  if ($codigoDocs -ne 0) {
    throw ('o docs-check reprovou (acima): corrija antes de parar qualquer coisa. Se o erro é só de documentação e a ' +
           'subida não pode esperar, -PularDocsCheck.')
  }
}
# <<< docs-check (29.166)

if ($Ensaio) {
  Write-Host ''
  Write-Host 'ENSAIO: backup feito, nada foi parado. Para ensaiar a migração sem tocar no banco do ambiente central:'
  Write-Host '  pwsh -File scripts\restore.ps1 -De data\backups\<carimbo> -Para C:\temp\ensaio-016-017'
  Write-Host '  cd backend; .venv\Scripts\python.exe -c "from app.db import Database; d=Database(r''C:\temp\ensaio-016-017\poc.sqlite3''); print(d.migrate())"'
  Write-Host '  (aplica 016/017 na CÓPIA e imprime o que aplicou; o banco do ambiente central não é tocado)'
  return
}

Close-EtapaDoDeploy $estadoDeEtapas 'docs_check'
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

Close-EtapaDoDeploy $estadoDeEtapas 'painel'
# ------------------------------------------------------------------ 3. parar
# Com a tarefa `farm-central` registrada (install-central-service.ps1), quem sobe o backend é o SUPERVISOR: parar
# só o processo faria o supervisor religar o código velho em até 15 s, disputando a porta com o start.ps1. A
# tarefa é parada antes e religada depois; sem ela, vale o stop/start de sempre.
$supervisionado = [bool](Get-ScheduledTask -TaskName 'farm-central' -ErrorAction SilentlyContinue)
$script:subidaParou = $true     # daqui em diante uma falha deixa o backend parado ou no meio: entra no histórico
Write-Host ('--- parando o backend' + $(if ($supervisionado) { ' (e a tarefa farm-central)' }) + ' ---')
if ($supervisionado) { Stop-ScheduledTask -TaskName 'farm-central' -ErrorAction SilentlyContinue }
& pwsh -NoProfile -File (Join-Path $PSScriptRoot 'stop.ps1') @(if ($StopEmulators) { '-StopEmulators' })
if ((Saude) -ne $null) { throw 'o backend ainda responde depois do stop; não suba um segundo dono do banco.' }

Close-EtapaDoDeploy $estadoDeEtapas 'parada'
# ------------------------------------------------------------------ 3b. dependências
# Faltava, como o painel faltou: o deploy trocava o código e deixava o venv como estava, e uma versão nova no
# `requirements.txt` (cryptography 46 → 50, item T.4) nunca chegava à produção. Com o backend PARADO de propósito:
# no Windows a `.pyd` carregada fica travada e a troca falharia no meio. O venv é do `uv` (sem pip dentro), o
# mesmo caminho do `start.ps1`. Sem mudança no arquivo, a instalação é só uma conferência e leva segundos.
if (-not $PularDependencias) {
  Write-Host '--- dependências do backend (uv pip install -r requirements.txt) ---'
  $venvPy = Join-Path $root 'backend\.venv\Scripts\python.exe'
  Push-Location (Join-Path $root 'backend')
  try { & uv pip install -q -r requirements.txt --python $venvPy } finally { Pop-Location }
  if ($LASTEXITCODE -ne 0) {
    throw ('a instalação das dependências falhou e o backend está PARADO. Corrija e rode de novo, ou suba o que ' +
           'estava: Start-ScheduledTask farm-central (a cópia do banco está em data\backups).')
  }
}

# ------------------------------------------------------------------ 3c. dependências do Appium
# A mesma falta, no terceiro lugar: `tools/appium/node_modules` também é artefato e não acompanha o `git pull`. Em
# 30/09 o lock passou a exigir a troca de uma dependência empacotada vulnerável (K-064), e sem esta etapa o central
# seguiria com a versão antiga no disco enquanto o CI dizia que estava corrigido. Com o backend PARADO: é ele que
# sobe o Appium, e o `npm ci` apaga a pasta inteira. Só reinstala quando o lock mudou desde o commit que estava no
# ar (ou a pasta não existe); a conferência do disco roda sempre e, se reprovar, reinstala uma vez.
if (-not $PularDependencias) {
  $appium = Join-Path $root 'tools\appium'
  $conferir = { & node (Join-Path $appium 'corrigir-empacotados.mjs') --conferir | Out-Host; $LASTEXITCODE -eq 0 }
  $mudou = -not (Test-Path (Join-Path $appium 'node_modules'))
  if (-not $mudou -and $antes -and $antes.commit -and $antes.commit -ne $esperadoCommit) {
    & git -C $root diff --quiet $antes.commit $esperadoCommit -- tools/appium/package.json tools/appium/package-lock.json
    $mudou = $LASTEXITCODE -ne 0
  }
  Write-Host ('--- dependências do Appium (' + $(if ($mudou) { 'npm ci: o lock mudou' } else { 'só a conferência do disco' }) + ') ---')
  if ($mudou -or -not (& $conferir)) {
    Push-Location $appium
    try { & npm ci --no-audit --no-fund 2>&1 | Select-Object -Last 3 | Out-Host } finally { Pop-Location }
    if ($LASTEXITCODE -ne 0 -or -not (& $conferir)) {
      throw ('a instalação do Appium falhou ou o disco não bate com o lock, e o backend está PARADO. Corrija e rode ' +
             'de novo, ou suba o que estava: Start-ScheduledTask farm-central.')
    }
  }
}

Close-EtapaDoDeploy $estadoDeEtapas 'dependencias'
# ------------------------------------------------------------------ 4. subir (a migração acontece aqui)
Write-Host '--- subindo (AppState aplica as migrações pendentes na inicialização) ---'
if ($supervisionado) {
  Start-ScheduledTask -TaskName 'farm-central'
  # 300 s, não 120: em 27/09 a subida com quatro aparelhos online (abrindo as sessões do Appium) passou dos 120 s,
  # o script abortou a conferência e o backend respondeu `ok` logo depois. A migração não demorou; o arranque sim.
  $limite = (Get-Date).AddSeconds(300)
  while (-not (Saude) -and (Get-Date) -lt $limite) { Start-Sleep -Seconds 3 }
  if (-not (Saude)) { throw 'a tarefa farm-central subiu, mas /api/health não respondeu em 300 s; veja data\logs\supervisor.log' }
} else {
  & pwsh -NoProfile -File (Join-Path $PSScriptRoot 'start.ps1') -NoBrowser
  if ($LASTEXITCODE -ne 0) { throw 'o start falhou; veja data\logs\backend.err.log' }
}

Close-EtapaDoDeploy $estadoDeEtapas 'subida'
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

Close-EtapaDoDeploy $estadoDeEtapas 'conferencia'
# ------------------------------------------------------------------ 6. histórico e tag (29.159)
# Só aqui, com a subida conferida: a tag nomeia um commit que está no ar e passou na conferência. Tudo no melhor
# esforço (sem gh, sem rede, sem permissão): o aviso sai na tela e na linha do histórico, e o deploy continua.
$resultadoDaTag = $null
if (-not $SemTag) {
  $resultadoDaTag = Publish-TagDeDeploy -Raiz $root -Commit $esperadoCommit -Migracao $depois.migration
  if ($resultadoDaTag.aviso) { Write-Warning $resultadoDaTag.aviso }
  if ($resultadoDaTag.tag) {
    Write-Host ("tag $($resultadoDaTag.tag)" + $(if ($resultadoDaTag.empurrada) { ' enviada à origem' } else { ' (só local)' }) +
                $(if ($resultadoDaTag.release) { '; release criado.' } else { '.' }))
  }
}
Close-EtapaDoDeploy $estadoDeEtapas 'tag'
$script:subidaRegistrada = $true
try {
  Add-RegistroDeDeploy -Caminho $arquivoDeDeploys -Registro (New-RegistroDeDeploy -Resultado 'ok' `
    -CommitAntes $antes.commit -MigracaoAntes $antes.migration -CommitDepois $depois.commit -MigracaoDepois $depois.migration `
    -Backup $pastaDoBackup -BackupDoEnsaio $backupDoEnsaio -Tag $resultadoDaTag.tag -Motivo $resultadoDaTag.aviso `
    -DuracaoS ((Get-Date) - $inicioDoDeploy).TotalSeconds -Opcoes $opcoesDoDeploy -EtapasS $estadoDeEtapas.etapas)
  Write-Host 'histórico: uma linha em data\deploys.jsonl'
  Write-Host ('tempo por etapa (s): ' + (($estadoDeEtapas.etapas.GetEnumerator() | ForEach-Object { '{0} {1:F1}' -f $_.Key, $_.Value }) -join ', '))
} catch { Write-Warning "não consegui gravar a linha do histórico de deploys: $($_.Exception.Message)" }

Write-Host ''
Write-Host 'CONFERÊNCIA DO TÚNEL DO WORKER (o -R aponta para a 8010 desde 23/09):'
Write-Host '  1) Aqui:  Get-NetTCPConnection -LocalPort 8010 -State Listen   (tem de haver um listener)'
Write-Host '  2) Do worker: GET http://127.0.0.1:18000/api/health e /docs devem dar 404 — pelo túnel só o WebSocket existe.'
Write-Host '  3) Na Infraestrutura, o worker tem de voltar a "online" em até um minuto (batida a cada 10 s).'
Write-Host '  Se a 8010 não escuta: config\config.yaml foi recriado do exemplo (worker_port: 0). Restaure-o de'
Write-Host '  data\backups\<carimbo>\config\ — o start.ps1 agora para nesse caso em vez de recriar.'
Write-Host ''
Write-Host 'E O AGENTE DO WORKER, que é cópia manual e não acompanha este deploy:'
Write-Host '  O central passou a RECUSAR resultado de comando sem cerca (fence), e a esperar `inflight` no hello.'
Write-Host '  Agente anterior a 89f5508 não manda cerca: os comandos dele terminariam em `uncertain` para sempre.'
# Mandava copiar só `backend/app/worker/`: o agente importa também `devices`, `security`, `config`… e a cópia à
# mão produzia ImportError lá. O pacote é o de `backend/worker-manifest.txt`, a mesma lista dos instaladores.
$pacoteDoAgente = @(Get-Content -LiteralPath (Join-Path $root 'backend\worker-manifest.txt') -Encoding UTF8 |
                    ForEach-Object { ($_ -replace '#.*$', '').Trim() } | Where-Object { $_ })
Write-Host '  Na máquina do worker, com esta árvore acessível, rode (com autorização do dono):'
Write-Host '    pwsh -File scripts\worker-install.ps1 -Origem <esta árvore>'
Write-Host ("  Ele monta C:\farm\agent\app com as $($pacoteDoAgente.Count) entradas de backend/worker-manifest.txt " +
            '(não copie só backend/app/worker/), grava a versão e religa o agente:')
Write-Host "    $($pacoteDoAgente -join ', ')"
Write-Host '  Faça isso ANTES de considerar o deploy concluído. Confira na Infraestrutura que a batida voltou, e'
Write-Host '  mande um `stop` de teste num aparelho dele: tem de fechar como `succeeded`, não `uncertain`.'
Write-Host 'pronto.'
