#requires -Version 7
<#
Tunel publico da Central (item 29.54, ADR-073). Rodado PELO DONO, uma vez, depois de:
    winget install --id Cloudflare.cloudflared -e
    cloudflared tunnel login          (consentimento na Cloudflare; grava cert.pem)

O que faz, nesta ordem:
  1. cria o tunel 'central-farm' na conta da Cloudflare (se ainda nao existir);
  2. grava C:\cloudflared-central\config.yml (so esta maquina; pasta fechada a Administradores e SYSTEM);
  3. aponta o DNS de dev.nvit.com.br para este tunel (tira o dominio do tunel antigo do notebook);
  4. instala e inicia o servico 'Cloudflared' do Windows.

O que NAO faz: nao abre porta, nao mexe no roteador, nao mexe no central nem no .env, nao imprime segredo,
nao apaga o tunel antigo.

Travas (ADR-073): o destino e sempre http://127.0.0.1:<porta do central>; nunca a porta do canal do worker;
a config nunca leva httpHostHeader (o central separa o acesso publico do local pelo Host); /api/worker/ vai a 404
(com a barra final: /api/workers, que e tela do painel, continua passando).

Exige PowerShell 7 (usa ?. e -SkipHttpErrorCheck). Texto so em ASCII de proposito: o arquivo nao leva BOM.
#>
param(
    [string]$Hostname = 'dev.nvit.com.br',
    [string]$Tunel = 'central-farm',
    [int]$PortaDoCentral = 8000,
    [int]$PortaDoWorker = 8010,
    [switch]$SemDns,
    [switch]$SemServico
)
$ErrorActionPreference = 'Stop'

function Parar([string]$msg) { Write-Host "[PAROU] $msg" -ForegroundColor Red; exit 1 }
function Passo([string]$msg) { Write-Host "-> $msg" -ForegroundColor Cyan }

# ---------------------------------------------------------------- conferencias
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { Parar 'Abra o terminal como Administrador: instalar servico exige isso.' }
if ($PortaDoCentral -eq $PortaDoWorker) { Parar 'O destino nao pode ser a porta do canal do worker.' }
if ($Hostname -notmatch '^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$') { Parar "Hostname invalido: $Hostname" }

$exe = (Get-Command cloudflared -ErrorAction SilentlyContinue)?.Source
if (-not $exe) {
    foreach ($p in @("${env:ProgramFiles(x86)}\cloudflared\cloudflared.exe", "$env:ProgramFiles\cloudflared\cloudflared.exe")) {
        if (Test-Path $p) { $exe = $p; break }
    }
}
if (-not $exe) { Parar 'cloudflared nao encontrado. Rode antes: winget install --id Cloudflare.cloudflared -e (e abra um terminal novo).' }
Write-Host ('cloudflared: ' + (& $exe --version 2>&1 | Select-Object -First 1))

$dirUsuario = Join-Path $env:USERPROFILE '.cloudflared'
if (-not (Test-Path (Join-Path $dirUsuario 'cert.pem'))) {
    Parar 'Falta o consentimento na Cloudflare. Rode antes: cloudflared tunnel login (escolha nvit.com.br e autorize).'
}

$svc = Get-Service Cloudflared -ErrorAction SilentlyContinue
$dirSistema = 'C:\cloudflared-central'
$cfgPath = Join-Path $dirSistema 'config.yml'
if ($svc -and -not (Test-Path $cfgPath)) {
    Parar 'Ja existe um servico Cloudflared nesta maquina que nao e o da Central. Nao vou sobrescrever: confira qual e antes de seguir.'
}

# O central tem de estar respondendo no destino, senao o tunel sobe apontando para o nada.
try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:$PortaDoCentral/api/health" -TimeoutSec 8 -SkipHttpErrorCheck
    Write-Host "central em 127.0.0.1:$PortaDoCentral -> HTTP $($r.StatusCode)"
} catch { Parar "O central nao respondeu em 127.0.0.1:$PortaDoCentral. Confira antes de expor." }

# ---------------------------------------------------------------- 1. tunel
Passo "tunel '$Tunel'"
function Achar-Tunel { @(& $exe tunnel list --output json 2>$null | ConvertFrom-Json) | Where-Object { $_ -and $_.name -eq $Tunel } | Select-Object -First 1 }
$tun = Achar-Tunel
if (-not $tun) {
    # A saida do 'create' cita o caminho do arquivo de credencial, nao o conteudo.
    & $exe tunnel create $Tunel 2>&1 | Out-Host
    $tun = Achar-Tunel
} else { Write-Host "tunel '$Tunel' ja existe" }
if (-not $tun) { Parar "Nao consegui criar nem achar o tunel '$Tunel'." }
$uuid = $tun.id
if ($uuid -notmatch '^[0-9a-f-]{36}$') { Parar 'Id do tunel fora do formato esperado.' }
Write-Host "tunel '$Tunel' id=$uuid"

# ---------------------------------------------------------------- 2. config
Passo "config em $cfgPath"
New-Item -ItemType Directory -Force -Path $dirSistema | Out-Null
# Pasta so para SYSTEM (S-1-5-18) e Administradores (S-1-5-32-544): o arquivo de credencial do tunel mora aqui.
& icacls $dirSistema /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
$credOrigem = Join-Path $dirUsuario "$uuid.json"
$credDestino = Join-Path $dirSistema "$uuid.json"
if (Test-Path $credOrigem) { Copy-Item $credOrigem $credDestino -Force }
if (-not (Test-Path $credDestino)) {
    Parar "Falta o arquivo de credencial do tunel ($uuid.json). O tunel foi criado em outra maquina? A credencial fica so onde o tunel nasceu."
}
$cfg = @"
tunnel: $uuid
credentials-file: $credDestino
ingress:
  - hostname: $Hostname
    path: ^/api/worker/
    service: http_status:404
  - hostname: $Hostname
    service: http://127.0.0.1:$PortaDoCentral
  - service: http_status:404
"@
if ($cfg -match '(?i)httpHostHeader' -or $cfg -match ":$PortaDoWorker\b") { Parar 'Config recusada pelas travas do ADR-073.' }
Set-Content -Path $cfgPath -Value $cfg -Encoding utf8
& $exe tunnel --config $cfgPath ingress validate 2>&1 | Out-Host
if ($LASTEXITCODE -ne 0) { Parar 'A validacao do ingress falhou.' }
$regraWorker = (& $exe tunnel --config $cfgPath ingress rule "https://$Hostname/api/worker/ws" 2>&1 | Out-String)
if ($regraWorker -notmatch 'http_status:404') { Parar 'O caminho do worker nao caiu na regra 404. Nao sigo.' }
$regraPainel = (& $exe tunnel --config $cfgPath ingress rule "https://$Hostname/central/" 2>&1 | Out-String)
if ($regraPainel -notmatch "127\.0\.0\.1:$PortaDoCentral") { Parar 'O painel nao caiu na regra do central. Nao sigo.' }
Write-Host 'ingress conferido: /api/worker -> 404; o resto do hostname -> central; qualquer outro hostname -> 404'

# ---------------------------------------------------------------- 3. DNS
if ($SemDns) { Write-Host 'DNS: pulado (-SemDns)' }
else {
    Passo "DNS: $Hostname passa a apontar para este tunel (sai do tunel antigo)"
    & $exe tunnel route dns --overwrite-dns $Tunel $Hostname 2>&1 | Out-Host
    if ($LASTEXITCODE -ne 0) { Parar 'A troca do DNS falhou. O dominio segue como estava.' }
}

# ---------------------------------------------------------------- 4. servico
if ($SemServico) { Write-Host 'Servico: pulado (-SemServico)' }
else {
    Passo 'servico do Windows'
    if (-not $svc) { & $exe --config $cfgPath service install 2>&1 | Out-Host; Start-Sleep -Seconds 2 }
    # O 'service install' nao guarda o --config: sem corrigir o comando, o servico sobe sem tunel nenhum.
    $img = '"' + $exe + '" --config "' + $cfgPath + '" tunnel run ' + $Tunel
    Set-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Services\Cloudflared' -Name ImagePath -Value $img
    # O processo que o 'service install' sobe (sem argumentos) nao atende ao pedido de parada: um Restart-Service
    # fica minutos em "Waiting for service to stop" (visto na 1a execucao real, 03/10/2026 19:45Z). Por isso o
    # processo SEM o comando do tunel e encerrado a mao, e so depois o servico e iniciado com o comando certo.
    Get-CimInstance Win32_Process -Filter "Name='cloudflared.exe'" |
        Where-Object { $_.CommandLine -notmatch 'tunnel run' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 4
    if ((Get-Service Cloudflared).Status -ne 'Running') { Start-Service Cloudflared -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 8
    $svc = Get-Service Cloudflared -ErrorAction SilentlyContinue
    if (-not $svc -or $svc.Status -ne 'Running') {
        Parar "O servico nao ficou em execucao. Teste em primeiro plano: `"$exe`" --config `"$cfgPath`" tunnel run $Tunel"
    }
    Write-Host "servico Cloudflared: $($svc.Status)"
}

# ---------------------------------------------------------------- prova rapida
Passo 'prova de fora (pode levar um minuto para o DNS trocar)'
foreach ($caminho in @('/api/instances', '/api/worker/ws')) {
    try {
        $r = Invoke-WebRequest -Uri "https://$Hostname$caminho" -TimeoutSec 15 -SkipHttpErrorCheck
        Write-Host ("https://{0}{1} -> HTTP {2}" -f $Hostname, $caminho, $r.StatusCode)
    } catch { Write-Host ("https://{0}{1} -> sem resposta ainda ({2})" -f $Hostname, $caminho, $_.Exception.Message) }
}
Write-Host ''
Write-Host 'Esperado AGORA: /api/instances = 403 (o central ainda nao conhece o hostname) e /api/worker/ws = 404.'
Write-Host 'Se /api/instances der 200, PARE o servico (Stop-Service Cloudflared) e investigue antes de religar.'
Write-Host 'Pronto. Proximos passos (docs/operacao.md): python scripts/portal-config.py ligar, reiniciar a tarefa'
Write-Host 'farm-central e bash scripts/portal-prova-de-fora.sh depois.'
