<#
.SYNOPSIS
  Configura w32time com uma fonte NTP comum, no central e no worker (achado #142: ~97 s de desvio medido
  entre as duas máquinas em 21/09, nenhuma das duas com fonte de hora — 'Local CMOS Clock' /
  'Free-running System Clock').

.DESCRIPTION
  Item 0.7 / decisão 6 do plano: hora certa nas duas máquinas é decisão do dono (qual fonte NTP, se a rede do
  parque alcança a internet ou precisa de um peer interno, e a janela em que reiniciar o serviço de hora é
  aceitável). Este script deixa o ATO pronto; NÃO foi executado nesta rodada.

  Rode UMA vez em cada máquina (central e worker), como Administrador. `w32tm /resync` pode causar um salto de
  relógio perceptível na hora em que roda — é o ponto que pede a decisão do dono, não a mudança em si.

.EXAMPLE
  pwsh -File scripts\hora-certa.ps1 -Servidor 'time.windows.com'
#>
[CmdletBinding()]
param([string]$Servidor = 'time.windows.com,0x8')
$ErrorActionPreference = 'Stop'
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
  [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { throw 'Rode como Administrador — w32tm /config exige.' }

Write-Host "Estado atual:"; w32tm /query /status

Write-Host "`nConfigurando fonte NTP manual ($Servidor) e reiniciando o serviço de hora…"
w32tm /config /manualpeerlist:$Servidor /syncfromflags:manual /reliable:yes /update
Restart-Service w32time
Start-Sleep 2
w32tm /resync /force

Write-Host "`nEstado depois:"; w32tm /query /status
