<#
.SYNOPSIS
  Mede o custo real de uma system image: instala (se faltar), cria um AVD temporário, faz cold boot
  headless e registra tempo de boot, RAM efetiva imposta pelo emulador, memória do processo e
  se o screenshot funciona. Remove o AVD temporário ao final.
  Uso: .\probe-image.ps1 -Image 'system-images;android-34;aosp_atd;x86_64' -RamMb 1536
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory)][string]$Image,
  [int]$RamMb = 1536,
  [int]$Cores = 2,
  [int]$Port = 5580,
  [string]$SdkRoot = 'C:\Android\Sdk',
  [string]$OutFile = ''
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$env:ANDROID_HOME = $SdkRoot; $env:ANDROID_SDK_ROOT = $SdkRoot
$env:ANDROID_AVD_HOME = Join-Path $projectRoot 'data\avd-probe'
New-Item -ItemType Directory -Force $env:ANDROID_AVD_HOME | Out-Null
$adb = Join-Path $SdkRoot 'platform-tools\adb.exe'
$name = 'probe-' + (($Image -replace 'system-images;', '') -replace '[^a-zA-Z0-9]+', '-')
$serial = "emulator-$Port"

$imgDir = Join-Path $SdkRoot (($Image -replace ';', '\'))
if (-not (Test-Path $imgDir)) {
  (1..20 | ForEach-Object { 'y' }) | & (Join-Path $SdkRoot 'cmdline-tools\latest\bin\sdkmanager.bat') --sdk_root=$SdkRoot $Image 2>$null | Out-Null
}
'no' | & (Join-Path $SdkRoot 'cmdline-tools\latest\bin\avdmanager.bat') create avd -n $name -k $Image --force 2>$null | Out-Null
$cfg = Join-Path $env:ANDROID_AVD_HOME "$name.avd\config.ini"
$over = @{ 'hw.lcd.width' = '720'; 'hw.lcd.height' = '1280'; 'hw.lcd.density' = '320'; 'hw.ramSize' = "$RamMb"; 'hw.cpu.ncore' = "$Cores";
  'hw.gpu.enabled' = 'yes'; 'hw.gpu.mode' = 'swiftshader_indirect'; 'hw.keyboard' = 'yes'; 'hw.audioInput' = 'no'; 'hw.audioOutput' = 'no';
  'hw.sdCard' = 'no'; 'hw.camera.back' = 'none'; 'disk.dataPartition.size' = '4G'; 'fastboot.forceColdBoot' = 'yes'; 'fastboot.forceFastBoot' = 'no' }
$lines = Get-Content $cfg
foreach ($k in $over.Keys) {
  $found = $false
  $lines = $lines | ForEach-Object { if ($_ -like "$k=*") { $found = $true; "$k=$($over[$k])" } else { $_ } }
  if (-not $found) { $lines += "$k=$($over[$k])" }
}
Set-Content -Path $cfg -Value $lines -Encoding utf8

$log = Join-Path $projectRoot "data\logs\$name.log"
New-Item -ItemType Directory -Force (Split-Path $log) | Out-Null
$t0 = Get-Date
$p = Start-Process -FilePath (Join-Path $SdkRoot 'emulator\emulator.exe') -PassThru -WindowStyle Hidden `
  -ArgumentList '-avd', $name, '-port', $Port, '-no-window', '-no-audio', '-no-boot-anim', '-no-snapshot', '-gpu', 'swiftshader_indirect', '-accel', 'on', '-memory', $RamMb, '-cores', $Cores, '-no-metrics' `
  -RedirectStandardOutput $log -RedirectStandardError "$log.err"
$booted = $false
for ($i = 0; $i -lt 120; $i++) {
  Start-Sleep -Seconds 3
  $b = (& $adb -s $serial shell getprop sys.boot_completed 2>$null)
  if ("$b".Trim() -eq '1') { $booted = $true; break }
  if ($p.HasExited) { break }
}
$bootS = [math]::Round(((Get-Date) - $t0).TotalSeconds)
Start-Sleep -Seconds 20   # deixa o sistema assentar antes de medir
$q = Get-CimInstance Win32_Process -Filter "ParentProcessId=$($p.Id)" | Where-Object { $_.Name -like 'qemu*' } | Select-Object -First 1
$ws = if ($q) { [math]::Round((Get-Process -Id $q.ProcessId).WorkingSet64 / 1GB, 2) } else { $null }
$priv = if ($q) { [math]::Round((Get-Process -Id $q.ProcessId).PrivateMemorySize64 / 1GB, 2) } else { $null }
$ramLine = (Select-String -Path $log -Pattern 'Increasing RAM size' | Select-Object -First 1).Line
$memTotal = (& $adb -s $serial shell "grep MemTotal /proc/meminfo" 2>$null)
$shot = Join-Path $projectRoot "data\logs\$name.png"
cmd /c "`"$adb`" -s $serial exec-out screencap -p > `"$shot`"" 2>$null
$shotBytes = if (Test-Path $shot) { (Get-Item $shot).Length } else { 0 }
$release = (& $adb -s $serial shell getprop ro.build.version.release 2>$null)

$result = [ordered]@{ image = $Image; requested_ram_mb = $RamMb; booted = $booted; first_boot_s = $bootS; emulator_ram_note = "$ramLine".Trim();
  guest_memtotal = "$memTotal".Trim(); qemu_ws_gb = $ws; qemu_private_gb = $priv; screenshot_bytes = $shotBytes; android_release = "$release".Trim() }
$json = $result | ConvertTo-Json -Compress
Write-Output $json
if ($OutFile) { Add-Content -Path $OutFile -Value $json }

& $adb -s $serial emu kill 2>$null | Out-Null
for ($i = 0; $i -lt 20 -and -not $p.HasExited; $i++) { Start-Sleep -Seconds 1 }
if (-not $p.HasExited) { Stop-Process -Id $p.Id -Force -Confirm:$false }
Start-Sleep -Seconds 2
Remove-Item (Join-Path $env:ANDROID_AVD_HOME "$name.avd") -Recurse -Force -Confirm:$false -ErrorAction SilentlyContinue
Remove-Item (Join-Path $env:ANDROID_AVD_HOME "$name.ini") -Force -Confirm:$false -ErrorAction SilentlyContinue
