<#
.SYNOPSIS
  Cópia de segurança do estado do parque: banco, configuração e a chave do cofre de credenciais.
  Roda com o backend NO AR — não para nada e não trava quem escreve.

.DESCRIPTION
  O que este backup contém, e por que cada coisa:

  - **O banco** (`data/poc.sqlite3`), copiado pela API de backup online do SQLite. Não é `Copy-Item`: o banco roda
    em WAL, e copiar só o `.sqlite3` com o backend no ar produz um arquivo sem as últimas transações — que abre,
    parece íntegro e está velho. Ver `scripts/sqlite-copia.py`. Com `DATABASE_URL` apontando para PostgreSQL, usa
    `pg_dump`.
  - **`config/`**, porque o banco sozinho não descreve o parque.
  - **`data/credentials.key`** (a chave do cofre) — com `-IncluirSegredos`. Sem ela o banco restaura tudo MENOS as
    credenciais dos perfis.

  **A pegadinha da chave, escrita aqui para não ser descoberta na hora errada.** `data/credentials.key` é
  embrulhada por DPAPI e **só abre com o mesmo usuário na mesma máquina** (`security/secret_store.py`). Copiá-la
  para outro servidor não recupera credencial nenhuma: o arquivo vai junto, e não serve. Para poder restaurar em
  OUTRA máquina existem dois caminhos, e é preciso escolher um ANTES de precisar:

    (a) Definir `INSTAGRAM_CREDENTIALS_MASTER_KEY` no `.env` (chave mestra explícita, guardada fora do host, por
        exemplo num gerenciador de senhas). A partir daí o cofre deixa de depender do DPAPI.
    (b) Aceitar o recadastro: restaurar o banco, e redigitar a senha de cada perfil pelo portal. O backend já
        reporta isso como problema `secret_store_locked` em `/api/health` e a interface pede o recadastro.

  O `.env` **não entra** no backup e não é impresso em lugar nenhum: ele é o arquivo de segredos, e um backup que
  o carrega multiplica cópias do segredo em pastas que ninguém audita. Guarde-o à parte, no gerenciador de senhas.

  **AVDs não entram.** `data/avd` tem 64 GB e guarda as sessões (inclusive a conta Google da VM-loja e os logins
  do Instagram); um `.zip` disso a cada dia não cabe, e a cópia a quente de um emulador ligado não é confiável.
  A política está em `docs/banco.md`: cópia a frio, sob demanda, com os emuladores desligados.

.PARAMETER Destino
  Pasta raiz dos backups. Cada corrida cria `<Destino>\<AAAAMMDD-HHmmss>\`.

.PARAMETER Reter
  Dias de retenção. Pastas mais velhas que isso são apagadas ao fim de uma corrida bem-sucedida.

.PARAMETER Banco
  Caminho do SQLite a copiar. Por omissão, `data\poc.sqlite3` da raiz do projeto.

.PARAMETER IncluirSegredos
  Inclui `data\credentials.key`. Fora por omissão: o destino do backup passa a exigir o mesmo cuidado do original.

.PARAMETER Instalar
  Registra a tarefa agendada diária (03:00) e sai.

.EXAMPLE
  pwsh -File scripts\backup.ps1
  pwsh -File scripts\backup.ps1 -IncluirSegredos -Reter 30
  pwsh -File scripts\backup.ps1 -Instalar
#>
[CmdletBinding()]
param(
  [string]$Destino = '',
  [int]$Reter = 14,
  [string]$Banco = '',
  [switch]$IncluirSegredos,
  [switch]$Instalar
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
if (-not $Destino) { $Destino = Join-Path $root 'data\backups' }
if (-not $Banco)   { $Banco   = Join-Path $root 'data\poc.sqlite3' }
$py = Join-Path $root 'backend\.venv\Scripts\python.exe'
$tarefa = 'parque-backup-diario'

if ($Instalar) {
  $script = $MyInvocation.MyCommand.Path
  $argumentos = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`" " +
                "-Destino `"$Destino`" -Reter $Reter" + $(if ($IncluirSegredos) { ' -IncluirSegredos' } else { '' })
  $exe = (Get-Command pwsh -ErrorAction SilentlyContinue).Source
  if (-not $exe) { $exe = 'powershell.exe' }
  $eu = ([Security.Principal.WindowsIdentity]::GetCurrent()).Name
  Unregister-ScheduledTask -TaskName $tarefa -Confirm:$false -ErrorAction SilentlyContinue
  $acao = New-ScheduledTaskAction -Execute $exe -Argument $argumentos
  $gatilho = New-ScheduledTaskTrigger -Daily -At 03:00
  $principal = New-ScheduledTaskPrincipal -UserId $eu -LogonType S4U -RunLevel Highest
  $ajustes = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
               -StartWhenAvailable -MultipleInstances IgnoreNew
  Register-ScheduledTask -TaskName $tarefa -Action $acao -Trigger $gatilho -Principal $principal -Settings $ajustes | Out-Null
  Write-Host "tarefa '$tarefa' registrada (diária, 03:00). Destino: $Destino; retenção: $Reter dias."
  return
}

$carimbo = Get-Date -Format 'yyyyMMdd-HHmmss'
$pasta = Join-Path $Destino $carimbo
New-Item -ItemType Directory -Force $pasta | Out-Null
$resumo = [ordered]@{ ts = (Get-Date).ToString('o'); pasta = $pasta }

# ------------------------------------------------------------------ o banco
# `-Banco` explícito é uma ordem, não uma sugestão: quem aponta um arquivo quer AQUELE arquivo. Sem esta guarda,
# o dia em que `.env` ganhasse um `DATABASE_URL` faria este script ignorar o `-Banco` e rodar `pg_dump` na
# produção — inclusive de dentro da suíte de testes, que chama `backup.ps1 -Banco <tmp>`.
$dsn = if ($PSBoundParameters.ContainsKey('Banco')) { $null } else { $env:DATABASE_URL }
if (-not $dsn -and -not $PSBoundParameters.ContainsKey('Banco')) {
  # .env não é impresso nem carregado inteiro: só se procura a linha que decide QUAL banco copiar.
  $envFile = Join-Path $root '.env'
  if (Test-Path $envFile) {
    $linha = Select-String -Path $envFile -Pattern '^\s*DATABASE_URL\s*=' -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($linha) { $dsn = ($linha.Line -split '=', 2)[1].Trim().Trim('"').Trim("'") }
  }
}

if ($dsn -and $dsn -match '^postgres') {
  $saida = Join-Path $pasta 'parque.dump'
  if (-not (Get-Command pg_dump -ErrorAction SilentlyContinue)) {
    throw "DATABASE_URL aponta para PostgreSQL mas pg_dump não está no PATH. Instale o cliente do PostgreSQL."
  }
  # -Fc: formato próprio, restaurável com pg_restore e independente da versão do servidor.
  & pg_dump --dbname=$dsn --format=custom --file=$saida
  if ($LASTEXITCODE -ne 0) { throw "pg_dump falhou com código $LASTEXITCODE" }
  $resumo['banco'] = 'postgresql'
  $resumo['arquivo'] = 'parque.dump'
  $resumo['bytes'] = (Get-Item $saida).Length
  Write-Host "banco: pg_dump -> parque.dump ($([Math]::Round((Get-Item $saida).Length / 1MB, 1)) MB)"
} else {
  if (-not (Test-Path $Banco)) { throw "banco não encontrado: $Banco" }
  if (-not (Test-Path $py)) { throw "interpretador do backend não encontrado: $py (rode scripts\start.ps1 uma vez)" }
  $saida = Join-Path $pasta 'poc.sqlite3'
  $json = & $py (Join-Path $PSScriptRoot 'sqlite-copia.py') $Banco $saida
  if ($LASTEXITCODE -ne 0) { throw "a cópia do SQLite falhou: $json" }
  $info = $json | ConvertFrom-Json
  if ($info.integrity_check -ne 'ok') { throw "a cópia saiu corrompida (integrity_check=$($info.integrity_check))" }
  $resumo['banco'] = 'sqlite'
  $resumo['arquivo'] = 'poc.sqlite3'
  $resumo['bytes'] = $info.bytes
  $resumo['migration'] = $info.migration
  $resumo['tables'] = $info.tables
  Write-Host ("banco: cópia online consistente -> poc.sqlite3 ($([Math]::Round($info.bytes / 1MB, 1)) MB, " +
              "integridade=$($info.integrity_check), migração=$($info.migration))")
}

# ------------------------------------------------------------------ configuração e chave do cofre
Copy-Item (Join-Path $root 'config') (Join-Path $pasta 'config') -Recurse -Force
$resumo['config'] = $true

$chave = Join-Path $root 'data\credentials.key'
if ($IncluirSegredos -and (Test-Path $chave)) {
  Copy-Item $chave (Join-Path $pasta 'credentials.key') -Force
  $resumo['credentials_key'] = $true
  Write-Warning ('credentials.key incluída: esta pasta passa a exigir o mesmo cuidado do original. E lembre: ' +
                 'a chave é DPAPI e NÃO abre em outra máquina/usuário (veja o LEIA-ME).')
} else {
  $resumo['credentials_key'] = $false
}

# O LEIA-ME viaja COM a cópia: quem restaura pode não ser quem fez o backup, e pode não ter o repositório.
@"
Backup do parque — $carimbo

Conteúdo
  poc.sqlite3 / parque.dump   estado completo (execuções, perfis, comandos, memória social)
  config/                     configuração do parque
  credentials.key             chave do cofre (só quando o backup foi feito com -IncluirSegredos)

Restaurar
  pwsh -File scripts\restore.ps1 -De "$pasta" -Para <pasta-limpa>
  (sem -Confirmar ele NÃO toca em data\ ; restaura numa pasta à parte e confere)

A chave do cofre é DPAPI
  credentials.key só abre com o MESMO usuário na MESMA máquina. Em outro servidor ela vai junto e não serve.
  Para restaurar credenciais em outra máquina é preciso ter adotado INSTAGRAM_CREDENTIALS_MASTER_KEY no .env
  ANTES (chave guardada fora do host). Sem isso, o caminho é recadastrar a senha de cada perfil pelo portal —
  o backend acusa isso como 'secret_store_locked' em /api/health.

O .env NÃO está aqui, de propósito
  É o arquivo de segredos. Guarde-o no gerenciador de senhas, fora do backup.

AVDs NÃO estão aqui
  data\avd tem dezenas de GB e guarda as sessões (conta Google da loja, logins do Instagram). Política de cópia
  a frio em docs\banco.md. Aparelho novo sem esses dados significa refazer login e passar por desafio de
  verificação.
"@ | Set-Content -Path (Join-Path $pasta 'LEIA-ME.txt') -Encoding UTF8

$resumo | ConvertTo-Json -Depth 4 | Set-Content -Path (Join-Path $pasta 'manifesto.json') -Encoding UTF8

# ------------------------------------------------------------------ retenção
if ($Reter -gt 0) {
  $corte = (Get-Date).AddDays(-$Reter)
  $velhas = @(Get-ChildItem -Path $Destino -Directory -ErrorAction SilentlyContinue |
              Where-Object { $_.Name -match '^\d{8}-\d{6}$' -and $_.CreationTime -lt $corte })
  # Nunca apaga a última cópia, mesmo que a retenção diga que sim: backup vazio é pior que backup velho.
  $todas = @(Get-ChildItem -Path $Destino -Directory -ErrorAction SilentlyContinue |
             Where-Object { $_.Name -match '^\d{8}-\d{6}$' } | Sort-Object Name)
  if ($todas.Count -gt 0) { $velhas = $velhas | Where-Object { $_.Name -ne $todas[-1].Name } }
  foreach ($v in $velhas) { Remove-Item $v.FullName -Recurse -Force -Confirm:$false; Write-Host "removido (retenção): $($v.Name)" }
}

Write-Host "pronto: $pasta"
