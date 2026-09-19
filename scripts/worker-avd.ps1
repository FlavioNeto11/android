# Cria o AVD do worker com o MESMO perfil de hardware do parque central e sobe o emulador destacado.
# Espelha backend/app/devices/avd.py (apply_hardware) e emulator.py (build_args).
# Nao usa servidor adb local de proposito: quem fala com a porta 5555 e o adb do servidor CENTRAL, pelo tunel.
[CmdletBinding()]
param(
  [string]$SdkRoot   = 'C:\Android\Sdk',
  [string]$AvdHome   = 'C:\farm\avd',
  [string]$Name      = 'worker-01',
  [int]$ConsolePort  = 5554,
  [string]$Image     = 'system-images;android-34;google_apis;x86_64',
  [switch]$SkipCreate,
  [switch]$NoLaunch   # so cria/ajusta o AVD; quem sobe e a tarefa agendada (processo em sessao SSH morre no logout)
)
$ErrorActionPreference = 'Continue'   # sdkmanager/avdmanager escrevem avisos em stderr; isso NAO e erro
$env:ANDROID_HOME     = $SdkRoot
$env:ANDROID_SDK_ROOT = $SdkRoot
$env:ANDROID_AVD_HOME = $AvdHome

function Step($m) { Write-Host "[$(Get-Date -Format HH:mm:ss)] $m" }

New-Item -ItemType Directory -Force $AvdHome | Out-Null
$cfgPath = Join-Path $AvdHome "$Name.avd\config.ini"

if (-not $SkipCreate) {
  Step "Criando AVD '$Name' a partir de $Image"
  $avdmanager = Join-Path $SdkRoot 'cmdline-tools\latest\bin\avdmanager.bat'
  if (-not (Test-Path $avdmanager)) { throw "avdmanager nao encontrado em $avdmanager" }
  # "no" recusa o perfil de hardware customizado, igual ao input_text do avd.py
  'no' | & $avdmanager create avd -n $Name -k $Image --force 2>&1 | Where-Object { $_ -notmatch 'deprecated|android-cli|d.android.com' } | Out-String | Write-Host
  if (-not (Test-Path $cfgPath)) { throw "AVD nao foi criado: $cfgPath ausente" }
}

Step "Aplicando o perfil de hardware do parque (-lowram, 1536 MB, 2 nucleos, 720x1280)"
$ov = [ordered]@{
  'hw.lcd.width' = '720'; 'hw.lcd.height' = '1280'; 'hw.lcd.density' = '320'
  'hw.ramSize' = '1536'; 'hw.cpu.ncore' = '2'
  'hw.gpu.enabled' = 'yes'; 'hw.gpu.mode' = 'swiftshader_indirect'
  'hw.keyboard' = 'yes'; 'hw.mainKeys' = 'no'; 'showDeviceFrame' = 'no'
  'hw.audioInput' = 'no'; 'hw.audioOutput' = 'no'; 'hw.sdCard' = 'no'
  'hw.camera.back' = 'none'; 'hw.camera.front' = 'none'
  'disk.dataPartition.size' = '4G'; 'vm.heapSize' = '256M'
  'fastboot.forceColdBoot' = 'no'; 'fastboot.forceFastBoot' = 'no'
  'firstboot.bootFromDownloadableSnapshot' = 'no'
  'firstboot.bootFromLocalSnapshot' = 'no'
  'firstboot.saveToLocalSnapshot' = 'no'
}
$linhas = Get-Content $cfgPath
$vistas = New-Object 'System.Collections.Generic.HashSet[string]'
$saida = foreach ($ln in $linhas) {
  $chave = ($ln -split '=', 2)[0].Trim()
  if ($ov.Contains($chave)) { [void]$vistas.Add($chave); "$chave=$($ov[$chave])" } else { $ln }
}
$saida = @($saida) + @($ov.Keys | Where-Object { -not $vistas.Contains($_) } | ForEach-Object { "$_=$($ov[$_])" })
Set-Content -Path $cfgPath -Value $saida -Encoding ascii

if ($NoLaunch) { Step "AVD pronto; -NoLaunch: quem sobe e a tarefa agendada."; return }

Step "Subindo o emulador DESTACADO na porta $ConsolePort (sobrevive ao fim da sessao SSH)"
$emulator = Join-Path $SdkRoot 'emulator\emulator.exe'
$log = "C:\farm\emulator-$Name.log"
$args = @('-avd', $Name, '-port', "$ConsolePort", '-no-window', '-no-audio', '-no-boot-anim',
          '-no-snapshot-load', '-no-snapshot-save', '-gpu', 'swiftshader_indirect',
          '-accel', 'on', '-no-metrics', '-lowram')
$p = Start-Process -FilePath $emulator -ArgumentList $args -PassThru -WindowStyle Hidden `
                   -RedirectStandardOutput $log -RedirectStandardError "$log.err"
Set-Content -Path "C:\farm\$Name.pid" -Value $p.Id -Encoding ascii
Step "PID $($p.Id) gravado em C:\farm\$Name.pid; log em $log"
Start-Sleep -Seconds 10
if ($p.HasExited) { throw "o emulador MORREU em menos de 10s; veja $log.err" }
Step "vivo apos 10s. Portas em escuta:"
netstat -ano -p tcp | Select-String 'LISTENING' | Select-String ":$ConsolePort\s|:$($ConsolePort+1)\s"
Step "Concluido."
