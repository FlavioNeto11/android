# 29.198: o deploy mantém o amostrador do host (`farm-amostrador-host`) em dia sozinho.
#
# O amostrador é um processo de vida longa: o `git pull` do deploy troca o script no disco, mas o processo que já roda segue com o código
# antigo (no 59 ele ficou com o formato de 9 colunas até alguém rodar `-Instalar` à mão). Aqui a decisão é uma função pura (`Get-DecisaoDoAmostrador`),
# testável sem tarefa agendada, e a ação (`Update-AmostradorDoDeploy`) é Stop + `-Instalar` + Start da tarefa, sem lacuna: a 1ª linha nova do CSV
# sai em menos de um minuto. Tudo no melhor esforço: quem chama nunca deixa o amostrador derrubar o deploy.

$script:NomeDaTarefaDoAmostrador = 'farm-amostrador-host'

function Get-CabecalhoDoScript {
  <# O cabeçalho que o `amostrador-host.ps1` escreve (a linha `$cabecalho = '...'`), ou '' se não achar. #>
  param([Parameter(Mandatory)][string]$Script)
  if (-not (Test-Path -LiteralPath $Script)) { return '' }
  $texto = Get-Content -LiteralPath $Script -Raw -Encoding UTF8
  $m = [regex]::Match($texto, "(?m)^\s*\`$cabecalho\s*=\s*'(ts_utc[^']*)'")
  if ($m.Success) { return $m.Groups[1].Value } else { return '' }
}

function Get-CabecalhoDoUltimoCsv {
  <# O ÚLTIMO cabeçalho do CSV mais recente da pasta (o amostrador anexa um novo quando o formato cresce no meio do dia), ou '' sem CSV. #>
  param([Parameter(Mandatory)][string]$Pasta)
  if (-not (Test-Path -LiteralPath $Pasta)) { return '' }
  $arquivo = Get-ChildItem -LiteralPath $Pasta -Filter '2*.csv' -File -ErrorAction SilentlyContinue | Sort-Object Name | Select-Object -Last 1
  if (-not $arquivo) { return '' }
  $ultimo = ''
  foreach ($linha in (Get-Content -LiteralPath $arquivo.FullName -Encoding UTF8)) {
    if ($linha.StartsWith('ts_utc,')) { $ultimo = $linha.Trim() }
  }
  return $ultimo
}

function Get-DecisaoDoAmostrador {
  <#
    O que o deploy faz com o amostrador. `acao`: nada | iniciar | reinstalar; `motivo`: uma frase. Entradas puras (sem tocar o sistema):
    a tarefa existe? o processo está rodando e desde quando? o cabeçalho do script e o do último CSV; a hora do script no disco.
  #>
  param(
    [bool]$Registrada,
    [bool]$Rodando,
    [string]$CabecalhoDoScript = '',
    [string]$CabecalhoDoCsv = '',
    $ProcessoDesde = $null,
    $ScriptEm = $null
  )
  if (-not $Registrada) {
    return @{ acao = 'nada'; motivo = "a tarefa $script:NomeDaTarefaDoAmostrador não está registrada (rode scripts\amostrador-host.ps1 -Instalar uma vez)" }
  }
  if (-not $Rodando) {
    return @{ acao = 'reinstalar'; motivo = 'a tarefa está registrada mas o amostrador não está rodando' }
  }
  if ($CabecalhoDoScript -and $CabecalhoDoCsv -and $CabecalhoDoScript -ne $CabecalhoDoCsv) {
    $nScript = @($CabecalhoDoScript -split ',').Count
    $nCsv = @($CabecalhoDoCsv -split ',').Count
    return @{ acao = 'reinstalar'; motivo = "o cabeçalho do CSV tem $nCsv colunas e o do script tem ${nScript}: o processo roda o código antigo" }
  }
  if ($null -ne $ProcessoDesde -and $null -ne $ScriptEm -and ([datetime]$ScriptEm) -gt ([datetime]$ProcessoDesde).AddMinutes(1)) {
    return @{ acao = 'reinstalar'; motivo = 'o script no disco é mais novo que o processo que roda' }
  }
  return @{ acao = 'nada'; motivo = 'em dia' }
}

function Get-EstadoDoAmostrador {
  <# Lê o sistema: tarefa, processo (CIM) e arquivos. É a única parte da decisão que toca o ambiente. #>
  param([Parameter(Mandatory)][string]$Raiz)
  $caminho = Join-Path $Raiz 'scripts\amostrador-host.ps1'
  $tarefa = Get-ScheduledTask -TaskName $script:NomeDaTarefaDoAmostrador -ErrorAction SilentlyContinue
  $processo = $null
  try {
    $processo = Get-CimInstance Win32_Process -ErrorAction Stop |
      Where-Object { $_.CommandLine -and $_.CommandLine -like '*amostrador-host.ps1*' -and $_.CommandLine -notlike '*Get-CimInstance*' -and $_.ProcessId -ne $PID } |
      Select-Object -First 1
  } catch { }
  return @{
    Registrada = [bool]$tarefa
    Rodando = [bool]$processo
    ProcessoDesde = $(if ($processo) { $processo.CreationDate } else { $null })
    ScriptEm = $(if (Test-Path -LiteralPath $caminho) { (Get-Item -LiteralPath $caminho).LastWriteTime } else { $null })
    CabecalhoDoScript = (Get-CabecalhoDoScript -Script $caminho)
    CabecalhoDoCsv = (Get-CabecalhoDoUltimoCsv -Pasta (Join-Path $Raiz 'data\observabilidade\host'))
  }
}

function Update-AmostradorDoDeploy {
  <#
    Decide e age. Devolve @{ resultado = nada|reinstalado|simulado|falhou; motivo; lacuna_s }. `-Simular` só decide e diz o que faria.
    `lacuna_s`: segundos entre a última linha do CSV antes da troca e a primeira depois (o alvo é < 90 s: a cadência é de 1 linha por minuto).
  #>
  param(
    [Parameter(Mandatory)][string]$Raiz,
    [switch]$Simular,
    [int]$EsperaMaximaS = 150,
    [int]$PausaS = 3
  )
  $e = Get-EstadoDoAmostrador -Raiz $Raiz
  $d = Get-DecisaoDoAmostrador -Registrada $e.Registrada -Rodando $e.Rodando -CabecalhoDoScript $e.CabecalhoDoScript `
         -CabecalhoDoCsv $e.CabecalhoDoCsv -ProcessoDesde $e.ProcessoDesde -ScriptEm $e.ScriptEm
  if ($d.acao -eq 'nada') { return @{ resultado = 'nada'; motivo = $d.motivo; lacuna_s = $null } }
  if ($Simular) { return @{ resultado = 'simulado'; motivo = ('faria Stop + -Instalar + Start: ' + $d.motivo); lacuna_s = $null } }

  $nome = $script:NomeDaTarefaDoAmostrador
  $caminho = Join-Path $Raiz 'scripts\amostrador-host.ps1'
  $pasta = Join-Path $Raiz 'data\observabilidade\host'
  $ultimaAntes = $null
  $arquivo = Get-ChildItem -LiteralPath $pasta -Filter '2*.csv' -File -ErrorAction SilentlyContinue | Sort-Object Name | Select-Object -Last 1
  if ($arquivo) { $ultimaAntes = (Get-Content -LiteralPath $arquivo.FullName -Encoding UTF8 | Where-Object { $_ -match '^\d{4}-' } | Select-Object -Last 1) }
  Stop-ScheduledTask -TaskName $nome -ErrorAction SilentlyContinue
  Start-Sleep -Seconds $PausaS
  & $caminho -Instalar | Out-Null
  Start-ScheduledTask -TaskName $nome
  $inicio = Get-Date
  $lacuna = $null
  while (((Get-Date) - $inicio).TotalSeconds -lt $EsperaMaximaS) {
    Start-Sleep -Seconds $PausaS
    $arquivo = Get-ChildItem -LiteralPath $pasta -Filter '2*.csv' -File -ErrorAction SilentlyContinue | Sort-Object Name | Select-Object -Last 1
    $agora = if ($arquivo) { (Get-Content -LiteralPath $arquivo.FullName -Encoding UTF8 | Where-Object { $_ -match '^\d{4}-' } | Select-Object -Last 1) } else { $null }
    if ($agora -and $agora -ne $ultimaAntes) {
      $t0 = if ($ultimaAntes) { [datetime]::Parse(($ultimaAntes -split ',')[0], [cultureinfo]::InvariantCulture, 'AdjustToUniversal,AssumeUniversal') } else { $null }
      $t1 = [datetime]::Parse(($agora -split ',')[0], [cultureinfo]::InvariantCulture, 'AdjustToUniversal,AssumeUniversal')
      if ($t0) { $lacuna = [math]::Round(($t1 - $t0).TotalSeconds, 0) }
      return @{ resultado = 'reinstalado'; motivo = $d.motivo; lacuna_s = $lacuna }
    }
  }
  return @{ resultado = 'falhou'; motivo = ("reinstalei a tarefa mas nenhuma linha nova do CSV apareceu em $EsperaMaximaS s (" + $d.motivo + ')'); lacuna_s = $null }
}
