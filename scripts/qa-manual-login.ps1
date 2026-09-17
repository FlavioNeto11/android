<#
.SYNOPSIS
  Exercita o fluxo "usuário assume → faz login → devolve" pela MESMA API que o painel usa (lease + frame_id),
  na tela de reautenticação do app de QA: digita a conta e o PIN fictícios da instância.
  É um teste integrado do controle manual; em apps reais o login é feito por você no painel (Assumir controle).
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory)][string]$Instance,
  [string]$Pin = '1234',                     # PIN fictício das contas qa-user-NN do app de QA
  [string]$Base = 'http://127.0.0.1:8000'
)
$ErrorActionPreference = 'Stop'
$H = @{ Origin = $Base }
function Get-Inst { (Invoke-RestMethod "$Base/api/instances") | ForEach-Object { $_ } | Where-Object id -eq $Instance }
function Post($path, $obj) { Invoke-RestMethod -Method Post "$Base$path" -Headers $H -ContentType 'application/json' -Body ($obj | ConvertTo-Json) }
function Fresh-FrameId {                     # a entrada manual só vale sobre um frame recente: espera o próximo
  $old = (Get-Inst).frame.id
  for ($n = 0; $n -lt 40; $n++) { Start-Sleep -Milliseconds 500; $f = (Get-Inst).frame; if ($f.id -ne $old) { return $f.id } }
  throw 'nenhum frame novo em 20 s'
}
function Send-Input($obj) { $null = Post "/api/instances/$Instance/input" (@{ lease_id = $script:lease; frame_id = (Fresh-FrameId) } + $obj) }
function Tap-Element($el) { $b = $el.bounds; Send-Input @{ type = 'tap'; x = [int](($b[0] + $b[2]) / 2); y = [int](($b[1] + $b[3]) / 2) } }

$inst = Get-Inst
$take = Post "/api/instances/$Instance/control/take" @{}
$script:lease = $take.lease_id
if (-not $script:lease) { throw "controle não concedido de imediato (status=$($take.status)); a IA ainda está num ponto não seguro" }
Write-Host "controle assumido (status=$($take.status))"
try {
  $els = (Invoke-RestMethod "$Base/api/instances/$Instance/hierarchy").elements
  $account = $els | Where-Object { $_.resource_id -like '*:id/login_account' } | Select-Object -First 1
  $pinField = $els | Where-Object { $_.password } | Select-Object -First 1
  $button = $els | Where-Object { $_.resource_id -like '*:id/login_button' } | Select-Object -First 1
  if (-not $account -or -not $pinField -or -not $button) { throw 'tela de login do app de QA não reconhecida' }
  Tap-Element $account;  Send-Input @{ type = 'text'; text = $inst.account_label }
  Tap-Element $pinField; Send-Input @{ type = 'text'; text = $Pin }
  Tap-Element $button
  Start-Sleep 3
  Write-Host "login manual concluído como $($inst.account_label)"
} finally {
  $null = Post "/api/instances/$Instance/control/release" @{ lease_id = $script:lease }
  Write-Host 'controle devolvido'
}
