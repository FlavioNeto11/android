<#
.SYNOPSIS
  Diagnóstico do HOST Windows onde os emuladores vão rodar. Execute-o na própria máquina (de preferência
  como Administrador, para ler os recursos do Windows). Não altera nada.
  Saída: tabela no console + data\diagnostics-host.json (exibido na tela Diagnóstico do painel).
#>
[CmdletBinding()]
param([string]$SdkRoot = $(if ($env:ANDROID_SDK_ROOT) { $env:ANDROID_SDK_ROOT } else { 'C:\Android\Sdk' }))
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
$os = Get-CimInstance Win32_OperatingSystem
$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
$cs = Get-CimInstance Win32_ComputerSystem
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

function Get-Feature($name) {
  if (-not $isAdmin) { return 'desconhecido (execute como Administrador)' }
  $o = dism.exe /online /english /get-featureinfo /featurename:$name 2>&1 | Select-String '^State'
  if ($o) { ($o.Line -replace 'State\s*:\s*', '').Trim() } else { 'ausente' }
}
function Get-Ver($cmd, $cmdArgs) { try { (& $cmd @cmdArgs 2>&1 | Select-Object -First 1).ToString().Trim() } catch { $null } }

$launch = if ($isAdmin) { ((bcdedit /enum '{current}' 2>$null | Select-String 'hypervisorlaunchtype').Line -replace '.*\s', '') } else { 'desconhecido' }
$features = [ordered]@{
  HypervisorPlatform     = Get-Feature 'HypervisorPlatform'       # WHPX — exigido pelo emulador quando o Hyper-V está ativo
  'Microsoft-Hyper-V'    = Get-Feature 'Microsoft-Hyper-V'
  VirtualMachinePlatform = Get-Feature 'VirtualMachinePlatform'
}
$emulator = Join-Path $SdkRoot 'emulator\emulator.exe'
$accel = if (Test-Path $emulator) { (& $emulator -accel-check 2>&1 | Out-String).Trim() } else { 'emulator não instalado' }
$disk = Get-PSDrive -PSProvider FileSystem | Where-Object { $_.Free } | ForEach-Object { [ordered]@{ drive = $_.Name; free_gb = [math]::Round($_.Free / 1GB, 1); used_gb = [math]::Round($_.Used / 1GB, 1) } }
$top = Get-Process | Sort-Object WorkingSet64 -Descending | Select-Object -First 6 | ForEach-Object { [ordered]@{ name = $_.Name; pid = $_.Id; ws_gb = [math]::Round($_.WorkingSet64 / 1GB, 2) } }

$availGb = try { [math]::Round((Get-Counter '\Memory\Available MBytes').CounterSamples[0].CookedValue / 1024, 1) } catch { [math]::Round($os.FreePhysicalMemory / 1MB, 1) }
$hyperv = $cs.HypervisorPresent
$advice = if ($accel -match 'usable') { 'Aceleração OK.' }
elseif ($hyperv -and $features.HypervisorPlatform -ne 'Enabled') { 'Hyper-V ativo sem WHPX: habilite "Windows Hypervisor Platform" (Enable-WindowsOptionalFeature -Online -FeatureName HypervisorPlatform) e reinicie.' }
elseif (-not $hyperv) { 'Sem hipervisor ativo: habilite WHPX (recomendado) ou instale o driver AEHD do Android SDK com o Hyper-V desligado; confira VT-x/AMD-V no BIOS.' }
else { 'Verifique a saída de emulator -accel-check.' }

$result = [ordered]@{
  collected_at = (Get-Date).ToUniversalTime().ToString('s') + 'Z'; measured_on = $env:COMPUTERNAME; is_admin = $isAdmin
  os = "$($os.Caption) $($os.Version) build $($os.BuildNumber)"; arch = $os.OSArchitecture
  cpu = $cpu.Name; cores = $cpu.NumberOfCores; logical = $cpu.NumberOfLogicalProcessors
  ram_total_gb = [math]::Round($os.TotalVisibleMemorySize / 1MB, 1); ram_available_gb = $availGb
  commit_used_gb = [math]::Round(($os.TotalVirtualMemorySize - $os.FreeVirtualMemory) / 1MB, 1); commit_limit_gb = [math]::Round($os.TotalVirtualMemorySize / 1MB, 1)
  disks = @($disk); top_memory_processes = @($top)
  hypervisor_present = $hyperv; hypervisorlaunchtype = $launch; windows_features = $features
  accel_check = $accel; advice = $advice
  tools = [ordered]@{
    java = Get-Ver 'java' @('-version'); node = Get-Ver 'node' @('--version'); python = Get-Ver 'python' @('--version')
    adb = if (Test-Path "$SdkRoot\platform-tools\adb.exe") { Get-Ver "$SdkRoot\platform-tools\adb.exe" @('version') } else { $null }
    emulator = if (Test-Path $emulator) { (& $emulator -version 2>&1 | Select-String 'emulator version' | Select-Object -First 1).Line } else { $null }
    sdk_root = $SdkRoot; sdk_found = (Test-Path $SdkRoot)
  }
  estimate = [ordered]@{ per_instance_gb = 3.3; note = 'imagem Android 34 (piso de 2560 MB imposto pelo emulador) ≈ 3,3 GB reais por instância'
    fits_now = [math]::Max(0, [math]::Floor(($availGb - 4) / 3.3)) }
}
New-Item -ItemType Directory -Force (Join-Path $root 'data') | Out-Null
$result | ConvertTo-Json -Depth 6 | Set-Content -Encoding utf8 (Join-Path $root 'data\diagnostics-host.json')
$result | ConvertTo-Json -Depth 6
Write-Host "`n>> $advice  | Instâncias que cabem agora (estimativa): $($result.estimate.fits_now)" -ForegroundColor Cyan
