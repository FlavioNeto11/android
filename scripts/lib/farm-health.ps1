<#
.SYNOPSIS
  "Quem responde em /api/health é a Farm?" — a mesma regra de `backend/app/identidade.py`, para os scripts.

.DESCRIPTION
  Deploy de 26/09/2026: o container `cartorio-api-1` (outro projeto) publica `0.0.0.0:8000`. Com a Farm parada, o
  `/api/health` caiu nele e voltou `404 {"detail":"Not Found"}`. "Alguma coisa responde na porta" não é "o backend
  da Farm responde": o supervisor recusou a subida e os scripts tomariam o Cartório por backend no ar.

  `Get-FarmHealth` devolve o objeto do health SÓ quando o corpo identifica a Farm — `service` =
  'android-farm-central', ou o esquema antigo completo (status + version + ai + appium{port,running} + sdk{found}),
  para não tomar uma Farm anterior a `service` por serviço estrangeiro e subir um segundo dono do banco.
  Silêncio, HTML, JSON malformado ou de outro serviço devolvem $null. O status HTTP não decide: 503 da Farm é a
  Farm viva e degradada. O COMMIT não é identidade: quem confere a versão é o deploy, depois desta pergunta.

  Uso: `. (Join-Path $PSScriptRoot 'lib\farm-health.ps1')` e `Get-FarmHealth 'http://127.0.0.1:8000'`.
#>

# Mesmo valor de `SERVICO` em backend/app/identidade.py (conferido por teste).
$FarmServico = 'android-farm-central'

function Test-CorpoDaFarm {
  param([object]$Dados)
  if ($null -eq $Dados -or $Dados -isnot [System.Management.Automation.PSCustomObject]) { return $false }
  $nomes = @($Dados.PSObject.Properties.Name)
  if ($nomes -contains 'service') { return ($Dados.service -eq $FarmServico) }
  foreach ($k in 'status', 'version', 'ai', 'appium', 'sdk') { if ($nomes -notcontains $k) { return $false } }
  if (@('ok', 'degraded', 'error') -notcontains $Dados.status) { return $false }
  $appium = @($Dados.appium.PSObject.Properties.Name)
  $sdk = @($Dados.sdk.PSObject.Properties.Name)
  return ($Dados.ai -is [System.Management.Automation.PSCustomObject]) -and
         ($appium -contains 'port') -and ($appium -contains 'running') -and ($sdk -contains 'found')
}

function Get-FarmHealth {
  param([string]$Base = 'http://127.0.0.1:8000', [int]$TimeoutSec = 3)
  try {
    $r = Invoke-WebRequest -Uri "$Base/api/health" -TimeoutSec $TimeoutSec -SkipHttpErrorCheck -UseBasicParsing `
                           -ErrorAction Stop
  } catch { return $null }                        # conexão recusada, tempo esgotado: ninguém da Farm ali
  try { $dados = $r.Content | ConvertFrom-Json -ErrorAction Stop } catch { return $null }   # HTML, lixo
  if (Test-CorpoDaFarm $dados) { return $dados }
  return $null
}
