<#
.SYNOPSIS
  Item 6.6 — prova do conjunto REAL do Instagram (base de ~238 MB + split, `install-multiple`) num aparelho
  REMOTO, pelo túnel, medindo tempo de instalação, prova de abertura e estado final.

.DESCRIPTION
  O caminho "instalar sai do central pelo túnel" está provado para APK pequeno (20 MB em 1,6 s, docs/worker.md).
  Para o app que é o alvo real do produto — conjunto de splits de ~243 MB por uma única conexão SSH, com prova de
  abertura de até 90 s num convidado que já mostrou `adb shell excedeu 30s` sob carga — não há execução real.

  Este script FAZ essa execução. Ele NÃO é rodado por nenhum teste e não roda sozinho: instalar o Instagram num
  aparelho do parque é ato do dono. Use a rota POR APARELHO (`/app/install`) e nunca "Distribuir", que empurraria
  a versão para o parque inteiro.

  O que medir, e onde entra no relatório de validação (§10.3, "infraestrutura real"):
    1. tempo do `install-multiple` pelo túnel, por aparelho;
    2. se a leitura pós-instalação (`inspect`, `releases.inspect_timeout_s`) coube no prazo;
    3. se a prova de abertura coube em `releases.launch_deadline_s`;
    4. estado final em `GET /api/app-state` (`ready` é o único desfecho bom);
    5. com -Paralelos, a disputa: os mesmos números com 3 remotos ao mesmo tempo.

  Se 1–3 estourarem, o ajuste é uma linha de YAML em `config/config.yaml`:
      releases:
        profile_timeout_s: 30      # leitura do perfil do aparelho
        inspect_timeout_s: 40      # leitura do estado do app
        install_timeout_s: 900     # install/install-multiple
        launch_deadline_s: 90      # prova de abertura
  (antes disso, os prazos eram constantes no código e "ajustar ao medido" exigia editar Python).

.PARAMETER Aparelhos
  Ids lógicos. Padrão: android-12, que já tem app_id=instagram e está sem o pacote.

.PARAMETER ReleaseId
  Id da release do Instagram a instalar. Sem isto, o script lista as candidatas e para.

.PARAMETER Paralelos
  Dispara os aparelhos ao mesmo tempo, para medir a disputa no worker.

.EXAMPLE
  pwsh -File scripts/prova-instagram-remoto.ps1                       # lista as releases e não instala nada
  pwsh -File scripts/prova-instagram-remoto.ps1 -ReleaseId rel-abc123
  pwsh -File scripts/prova-instagram-remoto.ps1 -ReleaseId rel-abc123 -Aparelhos android-12,android-13,android-14 -Paralelos
#>
[CmdletBinding()]
param(
  [string[]]$Aparelhos = @('android-12'),
  [string]$ReleaseId,
  [switch]$Paralelos,
  [string]$Base = 'http://127.0.0.1:8000'
)
$ErrorActionPreference = 'Stop'
$pacote = 'com.instagram.android'

$releases = Invoke-RestMethod -Method Get "$Base/api/releases?package=$pacote"
if (-not $ReleaseId) {
  Write-Host 'Releases do Instagram no catálogo (escolha uma e repita com -ReleaseId):'
  $releases | Select-Object id, version_name, version_code, status, channel, serves | Format-Table | Out-String | Write-Host
  Write-Host 'Nada foi instalado. Instalar o conjunto real num aparelho do parque é decisão do dono.'
  return
}

$alvo = $releases | Where-Object { $_.id -eq $ReleaseId }
if (-not $alvo) { throw "Release $ReleaseId não está no catálogo para $pacote." }
Write-Host ("Conjunto: {0} ({1}), {2} arquivo(s), serve a: {3}" -f `
    $alvo.version_name, $alvo.version_code, $alvo.files.Count, ($alvo.serves -join ' / '))

function Instalar([string]$id) {
  $inicio = Get-Date
  $corpo = @{ release_id = $ReleaseId; idempotency_key = "prova-6.6-$id-$ReleaseId" } | ConvertTo-Json
  $r = Invoke-RestMethod -Method Post "$Base/api/instances/$id/app/install" -Body $corpo -ContentType 'application/json'
  Write-Host ("{0}: comando {1} ({2})" -f $id, $r.command_id, $r.state)
  # O comando tem desfecho honesto: succeeded / failed / uncertain. `uncertain` NÃO é falha — quer dizer que o
  # adb não respondeu e o aparelho ainda vai ser relido.
  while ($true) {
    Start-Sleep -Seconds 5
    $cmd = Invoke-RestMethod -Method Get "$Base/api/commands/$($r.command_id)"
    if ($cmd.state -in @('succeeded', 'failed', 'uncertain', 'rejected', 'cancelled')) {
      $seg = [int]((Get-Date) - $inicio).TotalSeconds
      $estado = (Invoke-RestMethod -Method Get "$Base/api/app-state?package=$pacote") |
                  Where-Object { $_.instance_id -eq $id }
      [pscustomobject]@{
        aparelho = $id; comando = $cmd.state; motivo = $cmd.reason; segundos = $seg
        estado_do_app = $estado.state; versao_observada = $estado.observed_version_code
        verificado_em = $estado.verified_at; detalhe = $estado.detail
      }
      break
    }
  }
}

$resultado = if ($Paralelos) {
  $jobs = $Aparelhos | ForEach-Object { Start-ThreadJob -ScriptBlock ${function:Instalar} -ArgumentList $_ }
  $jobs | Receive-Job -Wait -AutoRemoveJob
} else {
  $Aparelhos | ForEach-Object { Instalar $_ }
}
$resultado | Format-Table | Out-String | Write-Host
Write-Host 'Registre estes números em docs/relatorio-validacao.md §10.3 como INFRAESTRUTURA REAL, com a data.'
