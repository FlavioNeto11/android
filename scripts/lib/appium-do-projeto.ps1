<#
.SYNOPSIS
  "Quem escuta na porta do Appium é o Appium DESTE projeto?" — e, se for e o backend já caiu, encerrá-lo.

.DESCRIPTION
  K-039 (27 e 28/09/2026, três deploys): o `node …appium` do backend anterior sobrevivia ao `stop.ps1`, porque o
  backend só encerra o Appium que ele mesmo sabe ter subido, e um backend derrubado à força (a tarefa `farm-central`
  parada pelo `deploy.ps1`) não encerra nada. O backend novo achava o servidor na porta, READOTAVA pelo
  `data\appium.pid` e subia `degraded`: `appium_log_masking_off` (preenchimento de credencial bloqueado) ou
  `appium_down`. A correção à mão era sempre a mesma: parar, matar o `node.exe` dono da 4723, religar.

  Isto automatiza essa correção com duas travas, porque a máquina tem outros `node` (o painel em modo dev, o
  Codex, outros projetos) e a porta pode ser de outra coisa:

  1. **A porta** é a do bloco `appium:` do `config/config.yaml` (`host`/`port`), com os padrões de `AppiumCfg`
     (`backend/app/config.py`) quando o arquivo ou a chave faltam.
  2. **O processo** tem de ser `node.exe` E a linha de comando tem de apontar para `<appium.dir>\node_modules\appium`
     desta árvore, que é como `automation/appium_server.py` o sobe. É o mesmo critério do `_own_orphan` do backend.
     Linha de comando ilegível (processo elevado visto de um shell comum) não é prova: fica, com aviso.

  O que responde na porta e não passa nas duas travas fica como está, com aviso. Quem chama (o `stop.ps1`) só
  pede o encerramento depois de o backend da Farm ter parado de responder: com ele no ar, o Appium é dele.

  Uso: `. (Join-Path $PSScriptRoot 'lib\appium-do-projeto.ps1')`.
#>

function ConvertFrom-EscalarYaml {
  # Só o que o bloco `appium:` usa: número, texto nu, texto entre aspas; comentário no fim da linha sai.
  param([string]$Valor)
  if ($Valor -match '^"([^"]*)"') { return $Matches[1] }
  if ($Valor -match "^'([^']*)'") { return $Matches[1] }
  return ($Valor -replace '\s+#.*$', '').Trim()
}

function Get-AppiumConfig {
  <#
    host/port/dir do bloco `appium:`. Leitura por linha, sem módulo de YAML (o pwsh do central não tem um): só as
    chaves filhas diretas do bloco valem, então o `port:` de `server:` ou de outro bloco nunca vira a porta do Appium.
  #>
  param([string]$Caminho)
  $v = [ordered]@{ host = '127.0.0.1'; port = '4723'; dir = 'tools/appium' }   # os padrões de AppiumCfg
  if ($Caminho -and (Test-Path -LiteralPath $Caminho)) {
    $dentro = $false
    $recuo = $null
    foreach ($linha in Get-Content -LiteralPath $Caminho -Encoding UTF8) {
      if ($linha -match '^\s*(#.*)?$') { continue }                               # vazia ou só comentário
      if ($linha -match '^\S') { $dentro = ($linha -match '^appium:\s*(#.*)?$'); $recuo = $null; continue }
      if (-not $dentro) { continue }
      $m = [regex]::Match($linha, '^(\s+)([A-Za-z_]+):\s*(.*)$')
      if (-not $m.Success) { continue }
      if ($null -eq $recuo) { $recuo = $m.Groups[1].Value.Length }
      if ($m.Groups[1].Value.Length -ne $recuo) { continue }                      # chave aninhada mais funda
      $chave = $m.Groups[2].Value
      if ($v.Contains($chave)) { $v[$chave] = ConvertFrom-EscalarYaml $m.Groups[3].Value }
    }
  }
  $porta = 0
  if (-not [int]::TryParse([string]$v.port, [ref]$porta) -or $porta -lt 1 -or $porta -gt 65535) {
    throw "appium.port inválido em ${Caminho}: '$($v.port)'"
  }
  [pscustomobject]@{ Host = [string]$v.host; Porta = $porta; Pasta = [string]$v.dir }
}

function Resolve-PastaAppium {
  # Como `Config.path` do backend: relativo é relativo à raiz da árvore.
  param([string]$Raiz, [string]$Pasta)
  $p = if ([IO.Path]::IsPathRooted($Pasta)) { $Pasta } else { Join-Path $Raiz $Pasta }
  return [IO.Path]::GetFullPath($p)
}

function ConvertTo-CaminhoComparavel {
  param([string]$Texto)
  return ($Texto -replace '/', '\').ToLowerInvariant()
}

function Select-AppiumDoProjeto {
  <#
    Dos processos que escutam na porta (objetos com ProcessId, Name, CommandLine), só os que são o Appium desta
    árvore. Pura: é o que o teste exercita sem Windows e sem porta de verdade.
  #>
  param([object[]]$Processos, [string]$PastaAppium)
  # Sem pasta, o padrão viraria "qualquer coisa" e todo `node` na porta seria o nosso. Concatenação e não
  # `Join-Path`: este erra (e devolve vazio) com um caminho de drive que não existe na máquina que avalia.
  $pasta = ([string]$PastaAppium).Trim().TrimEnd('\', '/')
  if (-not $pasta) { throw 'PastaAppium vazia: sem ela não há como reconhecer o Appium deste projeto.' }
  $entrada = ConvertTo-CaminhoComparavel ($pasta + '\node_modules\appium')
  # A fronteira depois de `node_modules\appium` impede `…\node_modules\appium-uiautomator2-driver` e similares.
  $padrao = [regex]::Escape($entrada) + '(\\|"|\s|$)'
  return @($Processos | Where-Object {
      $_ -and (@('node.exe', 'node') -contains [string]$_.Name) -and $_.CommandLine -and
      ((ConvertTo-CaminhoComparavel ([string]$_.CommandLine)) -match $padrao)
    })
}

function Get-DonosDaPorta {
  # O procedimento manual do K-039, em função: Get-NetTCPConnection (Listen) → OwningProcess → Win32_Process.
  param([int]$Porta)
  if (-not (Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue)) {
    Write-Warning 'Get-NetTCPConnection não existe neste sistema (não é Windows): não há como ver quem escuta na porta.'
    return @()
  }
  $ids = @(Get-NetTCPConnection -LocalPort $Porta -State Listen -ErrorAction SilentlyContinue |
           Select-Object -ExpandProperty OwningProcess -Unique | Where-Object { $_ -gt 0 })
  return @(foreach ($id in $ids) {
      $p = Get-CimInstance Win32_Process -Filter "ProcessId=$id" -ErrorAction SilentlyContinue
      if ($p) { [pscustomobject]@{ ProcessId = [int]$p.ProcessId; Name = $p.Name; CommandLine = $p.CommandLine } }
    })
}

function Stop-AppiumDoProjeto {
  <#
    Encerra o Appium desta árvore que escuta em -Porta e confere que ele saiu. Com -Simular, só diz o que faria.
    -Donos existe para o teste trocar a listagem da porta (Windows) por processos que ele mesmo controla.
  #>
  param(
    [int]$Porta,
    [string]$PastaAppium,
    [string]$ArquivoPid,
    [switch]$Simular,
    [scriptblock]$Donos = { param($p) Get-DonosDaPorta $p },
    [int]$CarenciaS = 10,
    [int]$PrazoS = 15
  )
  $todos = @(& $Donos $Porta)
  if (-not $todos.Count) { Write-Host "Appium: ninguém escuta na porta $Porta."; return }
  $nossos = @(Select-AppiumDoProjeto $todos $PastaAppium)
  foreach ($d in $todos) {
    if ($nossos.ProcessId -contains $d.ProcessId) { continue }
    $porque = if (-not $d.CommandLine) { 'linha de comando ilegível (sem permissão? rode elevado)' }
              else { "não é o Appium de $PastaAppium" }
    Write-Warning "Porta ${Porta}: pid $($d.ProcessId) ($($d.Name)) fica como está — $porque."
  }
  if (-not $nossos.Count) { return }
  if ($Simular) {
    $nossos | ForEach-Object { Write-Host "encerraria: pid $($_.ProcessId) $($_.Name) — Appium deste projeto na porta $Porta" }
    return
  }
  # O backend que acabou de parar de responder pode ainda estar saindo: ele mesmo fecha as sessões e desliga o
  # Appium que subiu. A carência deixa isso acontecer na ordem dele; só o que sobra depois dela é órfão.
  $limite = (Get-Date).AddSeconds($CarenciaS)
  while ($nossos.Count -and (Get-Date) -lt $limite) {
    Start-Sleep -Milliseconds 500
    $nossos = @(Select-AppiumDoProjeto @(& $Donos $Porta) $PastaAppium)
  }
  if (-not $nossos.Count) { Write-Host "O Appium deste projeto saiu sozinho da porta $Porta."; return }
  foreach ($n in $nossos) {
    Write-Host "Encerrando o Appium deste projeto que sobrou na porta $Porta (pid $($n.ProcessId))…"
    try { Stop-Process -Id $n.ProcessId -Force -Confirm:$false -ErrorAction Stop }
    catch { Write-Warning "Não consegui encerrar o pid $($n.ProcessId): $($_.Exception.Message)" }
  }
  # Conferir o resultado, não o pedido: a porta tem de ficar sem o NOSSO Appium, senão o backend novo o readota.
  $limite = (Get-Date).AddSeconds($PrazoS)
  do {
    Start-Sleep -Milliseconds 500
    $resta = @(Select-AppiumDoProjeto @(& $Donos $Porta) $PastaAppium)
  } while ($resta.Count -and (Get-Date) -lt $limite)
  if ($resta.Count) {
    Write-Warning ("O Appium deste projeto continua na porta $Porta (pid $($resta.ProcessId -join ', ')). O backend " +
                   'novo vai readotá-lo sem mascaramento comprovado (K-039): encerre-o antes de subir.')
    return
  }
  # O pid gravado pelo backend aponta para quem acabou de sair: apagá-lo evita que um PID reciclado pareça órfão.
  if ($ArquivoPid -and (Test-Path -LiteralPath $ArquivoPid)) {
    $gravado = 0
    $texto = [string](Get-Content -LiteralPath $ArquivoPid -Raw -ErrorAction SilentlyContinue)
    if ([int]::TryParse($texto.Trim(), [ref]$gravado) -and ($nossos.ProcessId -contains $gravado)) {
      Remove-Item -LiteralPath $ArquivoPid -Force -Confirm:$false -ErrorAction SilentlyContinue
    }
  }
  Write-Host "Appium deste projeto encerrado; a porta $Porta está livre dele."
}
