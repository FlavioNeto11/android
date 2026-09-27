<#
.SYNOPSIS
  Mede o custo real de uma system image/configuração num AVD TEMPORÁRIO (não toca nas instâncias do projeto):
  cold boot headless, RAM efetiva imposta pelo emulador, memória do processo, ABIs aceitas (tradução ARM),
  Google Play Services, screenshot e — opcionalmente — instalação de APKs reais e hibernação por snapshot.
  Remove o AVD temporário ao final. Uma linha JSON por execução (stdout e -OutFile).
.EXAMPLE
  .\probe-image.ps1 -Image 'system-images;android-30;google_apis;x86_64' -Label 'a30-540p-lowram' -Width 540 -Height 960 -Density 240 -ExtraArgs '-lowram'
  .\probe-image.ps1 -Image 'system-images;android-30;google_apis;x86_64' -Label 'a30-snap' -SnapshotSpike
  .\probe-image.ps1 -Image '...' -Apk C:\git\android\apks\app1.apk,C:\git\android\apks\app2.apk
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory)][string]$Image,
  [string]$Label = '',
  [int]$RamMb = 1536,
  [int]$Cores = 2,
  [int]$Width = 720,
  [int]$Height = 1280,
  [int]$Density = 320,
  [string]$GpuMode = 'swiftshader_indirect',
  [string]$HeapSize = '',                 # vazio = padrão da imagem
  [string]$ExtraArgs = '',                # ex.: '-lowram'
  [string[]]$Apk = @(),
  [switch]$SnapshotSpike,
  [int]$SettleSec = 45,
  [int]$Port = 5580,
  [string]$SdkRoot = 'C:\Android\Sdk',
  [string]$OutFile = ''
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $OutFile) { $OutFile = Join-Path $projectRoot 'data\logs\image-probe-results.jsonl' }
$env:ANDROID_HOME = $SdkRoot; $env:ANDROID_SDK_ROOT = $SdkRoot
$env:ANDROID_AVD_HOME = Join-Path $projectRoot 'data\avd-probe'
New-Item -ItemType Directory -Force $env:ANDROID_AVD_HOME | Out-Null
$adb = Join-Path $SdkRoot 'platform-tools\adb.exe'
$emulator = Join-Path $SdkRoot 'emulator\emulator.exe'
$Apk = @($Apk | ForEach-Object { $_ -split '[,;]+' } | Where-Object { $_ })
$name = 'probe-' + (((($Image -replace 'system-images;', '') + '-' + $Label) -replace '[^a-zA-Z0-9]+', '-').Trim('-'))
$serial = "emulator-$Port"
$avdDir = Join-Path $env:ANDROID_AVD_HOME "$name.avd"

function Adb-Shell([string]$cmd) { ((& $adb -s $serial shell $cmd 2>$null) -join "`n").Trim() }
function Qemu-Mem([int]$parentPid) {
  $q = Get-CimInstance Win32_Process -Filter "ParentProcessId=$parentPid" | Where-Object { $_.Name -like 'qemu*' } | Select-Object -First 1
  if (-not $q) { return @{ ws = $null; priv = $null } }
  $gp = Get-Process -Id $q.ProcessId
  @{ ws = [math]::Round($gp.WorkingSet64 / 1GB, 2); priv = [math]::Round($gp.PrivateMemorySize64 / 1GB, 2) }
}
function Start-Emu([string[]]$snapArgs, [string]$log) {
  $argList = @('-avd', $name, '-port', $Port, '-no-window', '-no-audio', '-no-boot-anim') + $snapArgs +
    @('-gpu', $GpuMode, '-accel', 'on', '-memory', $RamMb, '-cores', $Cores, '-no-metrics')
  if ($ExtraArgs) { $argList += ($ExtraArgs -split '\s+' | Where-Object { $_ }) }
  Start-Process -FilePath $emulator -PassThru -WindowStyle Hidden -ArgumentList $argList -RedirectStandardOutput $log -RedirectStandardError "$log.err"
}
function Wait-Boot($proc, [int]$maxSec) {
  $t = Get-Date
  while (((Get-Date) - $t).TotalSeconds -lt $maxSec) {
    Start-Sleep -Seconds 2
    if ((Adb-Shell 'getprop sys.boot_completed') -eq '1') { return [math]::Round(((Get-Date) - $t).TotalSeconds) }
    if ($proc.HasExited) { return $null }
  }
  return $null
}
function Test-SnapshotRestored([string]$uptimeText, [double]$elapsedS, [double]$marginS) {
  # num boot a frio o kernel nasce depois do processo, então o /proc/uptime nunca passa do tempo de parede desde o
  # lançamento; restaurado, ele continua do ponto salvo (boot + SettleSec). A margem só absorve a latência do adb.
  # uptime ilegível = $null: sem leitura não há veredito, e incerteza não conta como restaurado
  $v = 0.0
  $inv = [Globalization.CultureInfo]::InvariantCulture
  if (-not [double]::TryParse("$uptimeText".Trim(), [Globalization.NumberStyles]::Float, $inv, [ref]$v)) { return $null }
  return $v -gt ($elapsedS + $marginS)
}
function Stop-Emu($proc) {
  # o emulator.exe é só o lançador: o qemu filho pode sobreviver a ele — guarda o PID antes e confere depois
  $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$($proc.Id)" | Where-Object { $_.Name -like 'qemu*' } | ForEach-Object ProcessId)
  & $adb -s $serial emu kill 2>$null | Out-Null
  for ($i = 0; $i -lt 30 -and -not $proc.HasExited; $i++) { Start-Sleep -Seconds 1 }
  if (-not $proc.HasExited) { Stop-Process -Id $proc.Id -Force -Confirm:$false -ErrorAction SilentlyContinue }
  Start-Sleep -Seconds 2
  foreach ($c in $children) {
    $alive = Get-CimInstance Win32_Process -Filter "ProcessId=$c" | Where-Object { $_.CommandLine -like "*-avd $name*" }
    if ($alive) { Stop-Process -Id $c -Force -Confirm:$false -ErrorAction SilentlyContinue }
  }
}

$imgDir = Join-Path $SdkRoot (($Image -replace ';', '\'))
if (-not (Test-Path $imgDir)) {
  (1..20 | ForEach-Object { 'y' }) | & (Join-Path $SdkRoot 'cmdline-tools\latest\bin\sdkmanager.bat') --sdk_root=$SdkRoot $Image 2>$null | Out-Null
}
'no' | & (Join-Path $SdkRoot 'cmdline-tools\latest\bin\avdmanager.bat') create avd -n $name -k $Image --force 2>$null | Out-Null
$cfg = Join-Path $avdDir 'config.ini'
$over = @{ 'hw.lcd.width' = "$Width"; 'hw.lcd.height' = "$Height"; 'hw.lcd.density' = "$Density"; 'hw.ramSize' = "$RamMb"; 'hw.cpu.ncore' = "$Cores";
  'hw.gpu.enabled' = 'yes'; 'hw.gpu.mode' = $GpuMode; 'hw.keyboard' = 'yes'; 'hw.audioInput' = 'no'; 'hw.audioOutput' = 'no';
  'hw.sdCard' = 'no'; 'hw.camera.back' = 'none'; 'hw.camera.front' = 'none'; 'disk.dataPartition.size' = '4G';
  'fastboot.forceColdBoot' = $(if ($SnapshotSpike) { 'no' } else { 'yes' }); 'fastboot.forceFastBoot' = 'no' }
if ($HeapSize) { $over['vm.heapSize'] = $HeapSize }
$lines = Get-Content $cfg
foreach ($k in $over.Keys) {
  $found = $false
  $lines = $lines | ForEach-Object { if ($_ -like "$k=*") { $found = $true; "$k=$($over[$k])" } else { $_ } }
  if (-not $found) { $lines += "$k=$($over[$k])" }
}
Set-Content -Path $cfg -Value $lines -Encoding utf8

$log = Join-Path $projectRoot "data\logs\$name.log"
New-Item -ItemType Directory -Force (Split-Path $log) | Out-Null
# frio: nunca carrega snapshot; o salvamento automático ao sair fica sempre desligado (só snapshot explícito)
$coldArgs = if ($SnapshotSpike) { @('-no-snapshot-load', '-no-snapshot-save') } else { @('-no-snapshot') }
$p = Start-Emu $coldArgs $log
$bootS = Wait-Boot $p 420
$booted = $null -ne $bootS
Start-Sleep -Seconds $SettleSec   # deixa o sistema assentar antes de medir
$mem = Qemu-Mem $p.Id
$ramLine = (Select-String -Path $log -Pattern 'Increasing RAM size' | Select-Object -First 1).Line
$shot = Join-Path $projectRoot "data\logs\$name.png"
cmd /c "`"$adb`" -s $serial exec-out screencap -p > `"$shot`"" 2>$null
$shotBytes = if (Test-Path $shot) { (Get-Item $shot).Length } else { 0 }
$abilist = Adb-Shell 'getprop ro.product.cpu.abilist'

$result = [ordered]@{
  ts = (Get-Date).ToString('s'); image = $Image; label = $Label; requested_ram_mb = $RamMb; cores = $Cores
  resolution = "${Width}x${Height}@${Density}"; gpu = $GpuMode; extra_args = $ExtraArgs; heap = $HeapSize
  booted = $booted; first_boot_s = $bootS; emulator_ram_note = "$ramLine".Trim()
  guest_memtotal = (Adb-Shell 'grep MemTotal /proc/meminfo'); guest_memavailable = (Adb-Shell 'grep MemAvailable /proc/meminfo')
  qemu_ws_gb = $mem.ws; qemu_private_gb = $mem.priv; screenshot_bytes = $shotBytes
  android_release = (Adb-Shell 'getprop ro.build.version.release'); abilist = $abilist
  arm_translation = [bool]($abilist -match 'arm64-v8a|armeabi'); native_bridge = (Adb-Shell 'getprop ro.dalvik.vm.native.bridge')
  has_gms = [bool]((Adb-Shell 'pm list packages com.google.android.gms') -match 'com.google.android.gms')
  low_ram_prop = (Adb-Shell 'getprop ro.config.low_ram')
}

if ($booted -and $Apk.Count -gt 0) {
  $apkResults = @()
  foreach ($a in $Apk) {
    if (-not (Test-Path $a)) { $apkResults += [ordered]@{ apk = $a; installed = $false; detail = 'arquivo não encontrado' }; continue }
    $before = @(& $adb -s $serial shell pm list packages -3 2>$null)
    $t = Get-Date
    $outI = ((& $adb -s $serial install -r -g $a 2>&1) -join ' ').Trim()
    $after = @(& $adb -s $serial shell pm list packages -3 2>$null)
    $pkg = (@($after | Where-Object { $before -notcontains $_ }) | Select-Object -First 1) -replace '^package:', ''
    $abi = if ($pkg) { ((Adb-Shell "dumpsys package $pkg") -split "`n" | Where-Object { $_ -match 'primaryCpuAbi' } | Select-Object -First 1) } else { '' }
    $launched = $false
    if ($pkg) { $null = Adb-Shell "monkey -p $pkg -c android.intent.category.LAUNCHER 1"; Start-Sleep 12
      $launched = (Adb-Shell 'dumpsys window | grep mCurrentFocus') -match [regex]::Escape($pkg)
      cmd /c "`"$adb`" -s $serial exec-out screencap -p > `"$projectRoot\data\logs\$name-$pkg.png`"" 2>$null }
    $apkResults += [ordered]@{ apk = (Split-Path $a -Leaf); installed = ($outI -match 'Success'); seconds = [math]::Round(((Get-Date) - $t).TotalSeconds)
      package = $pkg; primary_abi = "$abi".Trim(); launched_foreground = $launched; detail = $outI.Substring(0, [Math]::Min(200, $outI.Length)) }
  }
  $result.apks = $apkResults
  $m2 = Qemu-Mem $p.Id; $result.qemu_ws_gb_after_apps = $m2.ws
}

if ($booted -and $SnapshotSpike) {
  $snap = [ordered]@{}
  $null = Adb-Shell 'sync'
  $t = Get-Date
  $saveOut = ((& $adb -s $serial emu avd snapshot save poc_hib 2>&1) -join ' ').Trim()
  $snap.save_s = [math]::Round(((Get-Date) - $t).TotalSeconds, 1); $snap.save_output = $saveOut
  Stop-Emu $p
  $snapDir = Join-Path $avdDir 'snapshots\poc_hib'
  $snap.disk_gb = if (Test-Path $snapDir) { [math]::Round(((Get-ChildItem $snapDir -Recurse -File | Measure-Object Length -Sum).Sum) / 1GB, 2) } else { 0 }
  $hostBefore = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
  Start-Sleep -Seconds 20               # simula tempo hibernado, para enxergar relógio parado
  $t = Get-Date
  $p = Start-Emu @('-snapshot', 'poc_hib', '-no-snapshot-save') "$log.wake"
  $snap.wake_boot_completed_s = Wait-Boot $p 180
  $snap.wake_total_s = [math]::Round(((Get-Date) - $t).TotalSeconds, 1)
  $snap.uptime_after_wake_s = ((Adb-Shell 'cat /proc/uptime') -split ' ')[0]
  # o tempo de parede é tomado DEPOIS da leitura: é o teto do uptime que um boot a frio poderia mostrar
  $snap.wake_elapsed_at_uptime_s = [math]::Round(((Get-Date) - $t).TotalSeconds, 1)
  $snap.uptime_margin_s = 10
  $snap.restored_by_uptime = Test-SnapshotRestored $snap.uptime_after_wake_s $snap.wake_elapsed_at_uptime_s $snap.uptime_margin_s
  # só informativo: o stdout do emulador redirecionado para arquivo desce ao disco em blocos e na saída, então com o
  # processo vivo a linha "Successfully loaded snapshot" costuma faltar (falso negativo nos 4 braços de 27/09)
  $snap.restored_by_log = [bool](Select-String -Path "$log.wake", "$log.wake.err" -Pattern 'loaded snapshot|Successfully loaded|snapshot.*load' -ErrorAction SilentlyContinue | Select-Object -First 1)
  $snap.loaded_from_snapshot = $snap.restored_by_uptime
  $guest = Adb-Shell 'date +%s'; $hostNow = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
  $snap.clock_skew_s = if ($guest -match '^\d+$') { [int64]$guest - $hostNow } else { $null }
  Start-Sleep 8
  $guest2 = Adb-Shell 'date +%s'; $snap.clock_skew_after_8s = if ($guest2 -match '^\d+$') { [int64]$guest2 - [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() } else { $null }
  $snap.network_ok = [bool]((Adb-Shell 'ping -c 1 -W 3 10.0.2.2') -match '1 received|1 packets received')
  $snap.adb_state = ((& $adb -s $serial get-state 2>&1) -join ' ').Trim()
  $mw = Qemu-Mem $p.Id; $snap.qemu_ws_gb_after_wake = $mw.ws
  $result.snapshot = $snap
}

$json = $result | ConvertTo-Json -Compress -Depth 6
Write-Output $json
Add-Content -Path $OutFile -Value $json

Stop-Emu $p
Remove-Item $avdDir -Recurse -Force -Confirm:$false -ErrorAction SilentlyContinue
Remove-Item (Join-Path $env:ANDROID_AVD_HOME "$name.ini") -Force -Confirm:$false -ErrorAction SilentlyContinue
