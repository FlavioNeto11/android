<#
.SYNOPSIS
  Mantém o relógio do servidor central certo pelo NTP.br quando o serviço do Windows (w32time) não consegue.

.DESCRIPTION
  Medido em 28/09/2026 no central (WIN-7S2UASNLFOP): o relógio estava "Free-running", +6,2 s atrás do NTP.br, e o
  backend acertava o relógio dos convidados pelo do hospedeiro enquanto o auto_time do Android acertava pela rede —
  ~100 acertos por dia por aparelho (ADR-053, C12). Configurar o w32time não resolveu: os pedidos dele saem pela
  porta de ORIGEM 123 e nenhuma resposta volta (evento 47, "No valid response ... after 8 attempts"), enquanto
  `w32tm /stripchart`, que sai por porta efêmera, mede normalmente. A rede bloqueia NTP com origem 123.

  Então este script mede o desvio com o stripchart (três servidores, três amostras cada, mediana) e, se passar de
  -Limite segundos, ajusta o relógio com Set-Date -Adjust. Registra cada rodada em data\logs\relogio.log.

  -Instalar registra a tarefa agendada `farm-relogio` (SYSTEM, a cada 15 min) e tira do w32time o papel de sincronizar
  (dois donos do relógio brigariam). Exige administrador. Autorizado pelo dono em 28/09/2026 ("eu autorizo tudo").

.PARAMETER Limite
  Desvio mínimo, em segundos, para ajustar. Abaixo disso só registra.
.PARAMETER Simular
  Mede e registra, mas não ajusta.
.PARAMETER Instalar
  Registra a tarefa agendada e desliga a sincronização do w32time.
#>
param(
  [double]$Limite = 0.2,
  [switch]$Simular,
  [switch]$Instalar
)
$ErrorActionPreference = 'Stop'
$raiz = Split-Path -Parent $PSScriptRoot
$log = Join-Path $raiz 'data\logs\relogio.log'
$servidores = @('a.st1.ntp.br', 'b.st1.ntp.br', 'c.st1.ntp.br')

function Registrar([string]$texto) {
  $linha = "{0:yyyy-MM-dd HH:mm:ss} {1}" -f (Get-Date), $texto
  New-Item -ItemType Directory -Force (Split-Path -Parent $log) | Out-Null
  Add-Content -LiteralPath $log -Value $linha -Encoding utf8
  Write-Output $linha
}

if ($Instalar) {
  $acao = New-ScheduledTaskAction -Execute 'pwsh.exe' `
            -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
  $gatilho = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 15)
  $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
  $ajustes = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
  Register-ScheduledTask -TaskName 'farm-relogio' -Action $acao -Trigger $gatilho -Principal $principal `
    -Settings $ajustes -Force | Out-Null
  # Um dono só do relógio: o w32time continua rodando (outros componentes consultam), mas não sincroniza.
  w32tm /config /syncfromflags:NO /update | Out-Null
  Registrar "tarefa farm-relogio registrada (a cada 15 min); w32time sem sincronização própria"
}

$amostras = @()
foreach ($s in $servidores) {
  $saida = w32tm /stripchart /computer:$s /samples:3 /dataonly 2>&1
  foreach ($linha in $saida) {
    if ("$linha" -match ',\s*([+-]\d+(?:[.,]\d+)?)s\s*$') {
      $amostras += [double]::Parse(($Matches[1] -replace ',', '.'), [Globalization.CultureInfo]::InvariantCulture)
    }
  }
}
if ($amostras.Count -lt 3) {
  Registrar "sem amostras suficientes do NTP.br ($($amostras.Count)); nada ajustado"
  exit 1
}
$ordenadas = $amostras | Sort-Object
$mediana = $ordenadas[[int][Math]::Floor($ordenadas.Count / 2)]
$texto = "desvio mediano {0:+0.000;-0.000} s em {1} amostras" -f $mediana, $amostras.Count
if ([Math]::Abs($mediana) -lt $Limite) {
  Registrar "$texto (abaixo de $Limite s; nada ajustado)"
} elseif ($Simular) {
  Registrar "$texto (simulação; não ajustado)"
} else {
  # stripchart dá (servidor - local): positivo = o relógio local está atrasado; somar o desvio acerta.
  Set-Date -Adjust ([TimeSpan]::FromSeconds($mediana)) | Out-Null
  Registrar "$texto; relógio ajustado"
}
