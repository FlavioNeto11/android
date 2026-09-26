<#
.SYNOPSIS
  Restaura um backup feito por scripts\backup.ps1 — por omissão numa PASTA LIMPA, sem tocar em data\.

.DESCRIPTION
  O padrão é o ensaio, não a restauração de verdade, e isso é deliberado: um procedimento de recuperação que
  ninguém executa é um procedimento que se descobre quebrado no pior dia. Sem `-Confirmar`, este script copia o
  backup para `-Para`, abre o banco restaurado, confere integridade e imprime o que há dentro. Nada em `data\` é
  tocado, então o ensaio pode rodar com o parque no ar.

  Com `-Confirmar`, ele restaura por cima de `data\`. Aí exige o backend PARADO — dois processos escrevendo o mesmo
  SQLite é exatamente o estado que o projeto evita em toda parte — e guarda o que estava lá em
  `data\substituido-<carimbo>\` antes de sobrescrever. Restauração que apaga o original sem rede é como se perde o
  banco duas vezes.

.PARAMETER De
  Pasta de um backup (a que tem poc.sqlite3/parque.dump e manifesto.json).

.PARAMETER Para
  Onde restaurar. Obrigatório no ensaio; ignorado com -Confirmar (que restaura em data\).

.PARAMETER Confirmar
  Restaura POR CIMA de data\. Exige o backend parado.

.EXAMPLE
  pwsh -File scripts\restore.ps1 -De data\backups\20260922-030000 -Para C:\temp\ensaio
  pwsh -File scripts\restore.ps1 -De data\backups\20260922-030000 -Confirmar
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$De,
  [string]$Para = '',
  [switch]$Confirmar
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root 'backend\.venv\Scripts\python.exe'
if (-not (Test-Path $De)) { throw "backup não encontrado: $De" }
$sqlite = Join-Path $De 'poc.sqlite3'
$dump = Join-Path $De 'parque.dump'
if (-not (Test-Path $sqlite) -and -not (Test-Path $dump)) { throw "nem poc.sqlite3 nem parque.dump em $De" }

if ($Confirmar) {
  . (Join-Path $PSScriptRoot 'lib\farm-health.ps1')
  $base = 'http://127.0.0.1:8000'
  $vivo = [bool](Get-FarmHealth $base 2)       # a Farm, não "alguém na 8000" (ver lib/farm-health.ps1)
  if ($vivo) { throw "o backend está no ar. Pare com scripts\stop.ps1 antes de restaurar por cima de data\." }
  $Para = Join-Path $root 'data'
  $guarda = Join-Path $root ('data\substituido-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
  New-Item -ItemType Directory -Force $guarda | Out-Null
  $mover = @('poc.sqlite3', 'poc.sqlite3-wal', 'poc.sqlite3-shm')
  # A chave do cofre só sai do lugar se o backup TROUXER uma para pôr no lugar dela. Um backup feito sem
  # -IncluirSegredos (o padrão) não tem chave nenhuma: mover a que está ali trancaria o cofre desta máquina sem
  # que nada a substituísse — e o banco restauraria tudo menos as credenciais, na máquina em que elas funcionavam.
  if (Test-Path (Join-Path $De 'credentials.key')) { $mover += 'credentials.key' }
  foreach ($f in $mover) {
    $atual = Join-Path $root "data\$f"
    if (Test-Path $atual) { Move-Item $atual (Join-Path $guarda $f) -Force }
  }
  if ($mover -notcontains 'credentials.key') {
    Write-Host 'a chave do cofre atual (data\credentials.key) foi MANTIDA: este backup não traz uma.'
  }
  Write-Host "o estado anterior foi guardado em $guarda (não apague antes de conferir o restaurado)."
} else {
  if (-not $Para) { throw 'informe -Para <pasta-limpa> (ensaio) ou -Confirmar (restaurar por cima de data\)' }
  New-Item -ItemType Directory -Force $Para | Out-Null
}

if (Test-Path $sqlite) {
  Copy-Item $sqlite (Join-Path $Para 'poc.sqlite3') -Force
  # NÃO copiar -wal/-shm: a cópia online já os incorporou. Um -wal antigo ao lado de um banco novo é como se
  # inventa corrupção onde não havia.
  Write-Host 'banco: poc.sqlite3 restaurado'
} else {
  Write-Host "banco: parque.dump presente — restaure com: pg_restore --clean --if-exists -d <DATABASE_URL> `"$dump`""
}
$cfg = Join-Path $De 'config'
if (Test-Path $cfg) { Copy-Item $cfg (Join-Path $Para 'config') -Recurse -Force; Write-Host 'config: restaurada' }
$chave = Join-Path $De 'credentials.key'
if (Test-Path $chave) {
  Copy-Item $chave (Join-Path $Para 'credentials.key') -Force
  Write-Warning ('credentials.key restaurada. Ela é DPAPI: se esta NÃO é a mesma máquina/usuário que a criou, ' +
                 'ela não abre e as credenciais dos perfis precisam ser recadastradas pelo portal.')
} else {
  Write-Warning ('sem credentials.key no backup: as credenciais dos perfis não voltam. Recadastre pelo portal, ' +
                 'ou use CREDENTIALS_MASTER_KEY.')
}

# ------------------------------------------------------------------ a conferência, que é o ponto do ensaio
$restaurado = Join-Path $Para 'poc.sqlite3'
if ((Test-Path $restaurado) -and (Test-Path $py)) {
  $codigo = @'
import json, sqlite3, sys
from pathlib import Path
p = Path(sys.argv[1]).resolve().as_posix()
c = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
out = {"integrity_check": c.execute("PRAGMA integrity_check").fetchone()[0]}
out["migration"] = (c.execute("SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1").fetchone() or [None])[0]
for t in ("runs", "steps", "events", "instagram_profiles", "secrets", "memory_items", "workers"):
    try:
        out[t] = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    except sqlite3.DatabaseError:
        out[t] = None
print(json.dumps(out, ensure_ascii=False))
'@
  $tmp = Join-Path ([IO.Path]::GetTempPath()) ('confere-' + [Guid]::NewGuid().ToString('N') + '.py')
  Set-Content -Path $tmp -Value $codigo -Encoding UTF8
  try { $json = & $py $tmp $restaurado } finally { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
  $info = $json | ConvertFrom-Json
  if ($info.integrity_check -ne 'ok') { throw "banco restaurado NÃO passa no integrity_check: $($info.integrity_check)" }
  Write-Host ("conferido: integridade=ok, migração=$($info.migration), execuções=$($info.runs), " +
              "etapas=$($info.steps), eventos=$($info.events), perfis=$($info.instagram_profiles), " +
              "segredos=$($info.secrets), memória=$($info.memory_items), workers=$($info.workers)")
}
if (-not $Confirmar) {
  Write-Host "ENSAIO: nada em data\ foi tocado. Para restaurar de verdade, pare o backend e use -Confirmar."
}
