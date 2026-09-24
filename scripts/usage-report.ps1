<#
.SYNOPSIS
  Custo de IA por função (planejar/decidir/verificar) e por modelo, em US$, a partir das chamadas gravadas em
  `ai_calls`. Preços em config.yaml → ai.prices. Não chama o provedor: só lê o que já foi gasto.
.EXAMPLE
  .\usage-report.ps1                 # últimos 7 dias
  .\usage-report.ps1 -RunId r-20260917-abc123
#>
[CmdletBinding()]
param([string]$RunId = '', [int]$Days = 7, [string]$Base = 'http://127.0.0.1:8000')
$ErrorActionPreference = 'Stop'
$q = if ($RunId) { "run_id=$RunId" } else { "days=$Days" }
$u = Invoke-RestMethod "$Base/api/usage?$q"
$scope = if ($RunId) { "execução $RunId" } else { "últimos $Days dias" }
Write-Host "Custo de IA — $scope"
$u.groups | ForEach-Object {
  [pscustomobject]@{ Funcao = $_.role; Modelo = $_.model; Nivel = $_.tier; Chamadas = $_.calls; TokensNovos = $_.fresh
    CacheLido = $_.cache_read; CacheGravado = $_.cache_write; Saida = $_.output; ComImagem = $_.with_image; Erros = $_.errors
    MsMedio = $_.avg_ms; USD = if ($null -eq $_.usd) { 'sem preço' } else { '{0:N4}' -f $_.usd } }
} | Format-Table -AutoSize
Write-Host ("Total: US$ {0:N4} · {1} aparelho-comando(s) com IA · {2} chamadas e US$ {3:N4} por aparelho-comando" -f `
    $u.total_usd, $u.objectives_with_ai, $u.calls_per_objective, $u.usd_per_objective)
$d = $u.steps_driven_by
if ($d) { Write-Host ("Etapas concluídas — por receita (sem IA): {0} · receita + IA: {1} · só IA: {2}" -f [int]$d.recipe, [int]$d.'recipe+ai', [int]$d.ai) }
if ($u.unpriced_models) { Write-Host "Modelos sem preço em ai.prices: $($u.unpriced_models -join ', ')" }
foreach ($c in $u.cache_inativo) {
  Write-Warning ("Cache de prompt INATIVO em decide/{0}: {1} chamada(s) sem leitura nem gravação de cache — cada " +
                 "decisão paga o prefixo inteiro (tools + system). Confira o cache_control no provedor.") -f $c.model, $c.calls
}
