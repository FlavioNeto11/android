<#
.SYNOPSIS
  Prepara o app de QA nas instâncias ONLINE: instala o APK (pela API do backend) e faz o login da conta
  fictícia de cada uma pelo atalho de provisionamento do próprio app de QA (intent com extras).
  É específico do QA Messenger; apps reais usam login manual pelo painel (Assumir controle).
#>
[CmdletBinding()]
param(
  [string[]]$Instances,
  [string]$Base = 'http://127.0.0.1:8000',
  [string]$SdkRoot = 'C:\Android\Sdk',
  [switch]$SkipInstall
)
$ErrorActionPreference = 'Stop'
$Instances = @($Instances | ForEach-Object { $_ -split '[,; ]+' } | Where-Object { $_ })   # aceita "a,b,c" vindo de `pwsh -File`
$adb = Join-Path $SdkRoot 'platform-tools\adb.exe'
$all = Invoke-RestMethod "$Base/api/instances"
$targets = $all | Where-Object { $_.state -eq 'online' -and (-not $Instances -or $Instances -contains $_.id) }
if (-not $targets) { Write-Warning 'Nenhuma instância online para provisionar.'; return }
foreach ($i in $targets) {
  $installed = (& $adb -s $i.serial shell 'pm list packages com.pocqa.messenger') -match 'com.pocqa.messenger'
  if (-not $SkipInstall -and -not $installed) {
    $null = Invoke-RestMethod -Method Post "$Base/api/instances/$($i.id)/actions/install_apk" -ContentType 'application/json' -Body '{"app_id":"qa-messenger"}'
    for ($n = 0; $n -lt 90; $n++) { Start-Sleep 2; if ((& $adb -s $i.serial shell 'pm list packages com.pocqa.messenger') -match 'com.pocqa.messenger') { break } }
  }
  $account = if ($i.account_label) { $i.account_label } else { 'qa-user-{0:d2}' -f $i.index }
  $current = (& $adb -s $i.serial shell 'content query --uri content://com.pocqa.messenger.provider/session' 2>$null) -join ' '
  if ($current -notmatch "account=$account\b") {
    $null = & $adb -s $i.serial shell "am start -n com.pocqa.messenger/.LoginActivity --es account $account --es pin 1234"
    Start-Sleep 3
    $current = (& $adb -s $i.serial shell 'content query --uri content://com.pocqa.messenger.provider/session') -join ' '
  }
  Write-Host ("{0} ({1}): {2}" -f $i.id, $i.serial, $current.Trim())
}
