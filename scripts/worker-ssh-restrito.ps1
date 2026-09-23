<#
.SYNOPSIS
  Prepara a máquina WORKER para receber o túnel do central com uma conta de serviço sem privilégio e uma chave
  que só encaminha portas. Roda NO WORKER, como administrador, uma vez por máquina.

.DESCRIPTION
  O que isto corrige (achado #123). Até aqui a chave do túnel — sem passphrase, porque a tarefa agendada precisa
  subir sozinha no boot — era autorizada em `C:\ProgramData\ssh\administrators_authorized_keys` SEM nenhuma
  opção. Medido: com ela dava para rodar `whoami`, `icacls`, `Get-Content` e qualquer outra coisa como
  Administrator no worker. Ou seja: um arquivo no central equivalia a administrador do sistema operacional em
  cada máquina do parque. Num parque de N workers, isso é movimento lateral central → todos os hosts.

  O túnel precisa de exatamente duas coisas: `-L` até as portas de ADB dos emuladores (127.0.0.1) e `-R` para
  abrir uma porta no worker que chega ao listener do central. Nada disso exige shell, pty, agente nem privilégio.

  O desenho aqui:

  1. **Conta de serviço** (`farm-tunel`) sem grupo além de `Users` — em particular fora de `Administrators` e de
     `Remote Desktop Users` — e com senha aleatória que ninguém anota (a autenticação é por chave).
  2. **Chave restrita**: a linha do `authorized_keys` leva
     `restrict,port-forwarding,permitopen="127.0.0.1:<adb>",...,permitlisten="127.0.0.1:<reversa>",command="exit"`.
     `restrict` sozinho NÃO impede execução de comando — ele tira pty, agente, X11 e user-rc; quem fecha a porta
     do shell é o `command="exit"`. E `port-forwarding` precisa ser reativado depois do `restrict`, senão o túnel
     não sobe.
  3. **Arquivo por usuário em ProgramData** (`__PROGRAMDATA__/ssh/authorized_keys/%u`) em vez de `~/.ssh`: o
     perfil de uma conta de serviço que nunca faz logon interativo pode simplesmente não existir ainda.
  4. **Bloco `Match User`** no fim do `sshd_config`, com `AllowTcpForwarding yes`, `PermitTTY no`,
     `PermitTunnel no`, `X11Forwarding no`, `AllowAgentForwarding no` e `ForceCommand`.

  Sobre `permitopen="127.0.0.1:*"`: a porta de ADB é uma POR APARELHO (5555, 5557, 5559, ...) e o parque ganha
  aparelho sem ninguém reeditar o sshd. Passando `-PortasAdb` a lista fica exata; sem ela, o padrão é
  `127.0.0.1:*` — que continua sendo só loopback DO WORKER, jamais a LAN dele.

.PARAMETER Simular
  Imprime exatamente a linha do `authorized_keys` e o bloco do `sshd_config` que seriam gravados, e sai sem tocar
  em conta, arquivo ou serviço. É o modo que o teste automatizado usa.

.EXAMPLE
  # No worker, PowerShell como administrador. A chave pública vem do central:
  #   ssh-keygen -y -f C:\Users\Administrator\.ssh\worker_ed25519
  pwsh -File scripts\worker-ssh-restrito.ps1 -ChavePublica "ssh-ed25519 AAAA... central" -PortasAdb "5555,5557" -Simular
  pwsh -File scripts\worker-ssh-restrito.ps1 -ChavePublica "ssh-ed25519 AAAA... central" -PortasAdb "5555,5557"
#>
[CmdletBinding()]
param(
  [string]$Usuario = 'farm-tunel',
  # A chave PÚBLICA do central, uma linha ("ssh-ed25519 AAAA... comentário"). Ou `-ArquivoChavePublica`.
  [string]$ChavePublica = '',
  [string]$ArquivoChavePublica = '',
  # TEXTO, não array: `pwsh -File ... -PortasAdb 5555,5557` NÃO vira array — chega como um texto só e o
  # encaminhamento sai com a porta "55555557", que não existe (medido; a mesma armadilha do -Mapa do túnel).
  # Vazio = `127.0.0.1:*` (qualquer porta de loopback do worker).
  [string]$PortasAdb = '',
  # Porta que o `-R` do túnel abre NO WORKER e que o agente usa para falar com o central.
  [int]$PortaReversa = 18000,
  # Remove do `administrators_authorized_keys` a linha cuja chave for IGUAL à informada — é o acesso irrestrito
  # de Administrator que este script existe para substituir. Explícito de propósito: apagar a única forma de
  # entrar numa máquina remota antes de confirmar que a nova funciona é como se perde um worker.
  [switch]$RemoverChaveDeAdministrador,
  [switch]$Simular
)
$ErrorActionPreference = 'Stop'

$SshData   = Join-Path $env:ProgramData 'ssh'
$SshdConf  = Join-Path $SshData 'sshd_config'
$AdminKeys = Join-Path $SshData 'administrators_authorized_keys'
$DirChaves = Join-Path $SshData 'authorized_keys'
$ArqChave  = Join-Path $DirChaves $Usuario
$MARCA     = "# --- farm: tunel restrito ($Usuario) --- nao edite a mao; regerado por scripts/worker-ssh-restrito.ps1"

function Get-ChavePublica {
  $texto = $ChavePublica
  if (-not $texto -and $ArquivoChavePublica) {
    $texto = (Get-Content -Raw -LiteralPath $ArquivoChavePublica).Trim()
  }
  $texto = ($texto -replace '\s+', ' ').Trim()
  if (-not $texto) { throw 'informe -ChavePublica "ssh-ed25519 AAAA... comentario" ou -ArquivoChavePublica' }
  $campos = $texto -split ' '
  if ($campos.Count -lt 2 -or $campos[0] -notmatch '^(ssh-ed25519|ssh-rsa|ecdsa-sha2-\S+|sk-ssh-ed25519@openssh\.com)$') {
    throw "isto não parece uma chave PÚBLICA de uma linha: '$($campos[0])'. Gere com ssh-keygen -y -f <chave privada>."
  }
  if ($texto -match 'PRIVATE KEY') { throw 'isto é uma chave PRIVADA. Passe a pública (ssh-keygen -y -f ...).' }
  return $texto
}

function Get-OpcoesDaChave {
  <# As opções que transformam "shell de administrador" em "só encaminha estas portas". A ordem importa:
     `restrict` nega tudo, e `port-forwarding` reabre a única coisa de que o túnel precisa. #>
  $opcoes = @('restrict', 'port-forwarding')
  $portas = @($PortasAdb -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
  if ($portas) {
    foreach ($p in $portas) {
      if ($p -notmatch '^\d+$') { throw "porta de ADB inválida: '$p' (esperado número)" }
      $opcoes += "permitopen=`"127.0.0.1:$p`""
    }
  } else {
    $opcoes += 'permitopen="127.0.0.1:*"'
  }
  # Duas formas do mesmo limite. A que faz o túnel funcionar é a SEGUNDA: `-R 18000:127.0.0.1:8010` sem endereço
  # de bind viaja como o nome `localhost`, que o sshd trata como diferente de `127.0.0.1`. A primeira fica para
  # quem um dia pedir `-R 127.0.0.1:18000:...` explicitamente. Em nenhuma das duas o limite deixa de ser ESTA
  # porta, e quem garante que a escuta é só loopback é o `GatewayPorts no` padrão do sshd.
  $opcoes += "permitlisten=`"127.0.0.1:$PortaReversa`""
  $opcoes += "permitlisten=`"$PortaReversa`""
  # `restrict` tira o pty, mas NÃO impede executar comando. Sem isto, a chave ainda roda `whoami` na conta.
  $opcoes += 'command="exit"'
  return ($opcoes -join ',')
}

function Get-BlocoSshd {
  @(
    $MARCA,
    "Match User $Usuario",
    "    AuthorizedKeysFile __PROGRAMDATA__/ssh/authorized_keys/%u",
    "    AllowTcpForwarding yes",
    "    PermitTTY no",
    "    PermitTunnel no",
    "    X11Forwarding no",
    "    AllowAgentForwarding no",
    # Porta SOZINHA, sem endereço, e isto NÃO é descuido. O cliente pede `-R 18000:127.0.0.1:8010` sem endereço
    # de bind, e o que viaja no fio nesse caso é o nome `localhost` — que o sshd_config(5) trata como COISA
    # DIFERENTE de `127.0.0.1`. Com `PermitListen 127.0.0.1:18000`, o sshd recusaria o encaminhamento do próprio
    # túnel, e com `ExitOnForwardFailure=yes` ele nunca subiria. `GatewayPorts no` (o padrão) continua sendo quem
    # garante que a escuta é só em loopback do worker.
    "    PermitListen $PortaReversa",
    "    ForceCommand exit"
  )
}

$chave  = Get-ChavePublica
$linha  = "$(Get-OpcoesDaChave) $chave"
$bloco  = Get-BlocoSshd

if ($Simular) {
  Write-Output "# conta de servico: $Usuario"
  Write-Output "# arquivo de chaves: $ArqChave"
  Write-Output '# --- authorized_keys ---'
  Write-Output $linha
  Write-Output '# --- sshd_config (no FIM do arquivo: bloco Match vale ate o proximo Match) ---'
  $bloco | ForEach-Object { Write-Output $_ }
  return
}

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
          ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { throw 'rode como administrador: este script cria conta local e edita o sshd_config' }

# ------------------------------------------------------------------ 1. conta de serviço
if (-not (Get-LocalUser -Name $Usuario -ErrorAction SilentlyContinue)) {
  # Senha aleatória que ninguém anota: a autenticação é por CHAVE. Ela existe porque o Windows exige uma, não
  # porque alguém vá usá-la. Some da memória do processo quando ele termina.
  $bytes = [byte[]]::new(32)
  [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
  $senha = ConvertTo-SecureString ([Convert]::ToBase64String($bytes) + '!aA9') -AsPlainText -Force
  New-LocalUser -Name $Usuario -Password $senha -FullName 'Tunel do parque (somente encaminhamento)' `
                -Description 'Conta de servico do tunel SSH do central. Sem shell: ver sshd_config.' `
                -PasswordNeverExpires -UserMayNotChangePassword -AccountNeverExpires | Out-Null
  Write-Host "conta '$Usuario' criada"
} else {
  Write-Host "conta '$Usuario' ja existe"
}
# Grupo `Users` (por SID: o nome muda em Windows não-inglês) e nada além dele.
Add-LocalGroupMember -SID 'S-1-5-32-545' -Member $Usuario -ErrorAction SilentlyContinue
foreach ($g in @('S-1-5-32-544', 'S-1-5-32-555', 'S-1-5-32-562')) {   # Administrators, Remote Desktop, DCOM
  Remove-LocalGroupMember -SID $g -Member $Usuario -ErrorAction SilentlyContinue
}

# ------------------------------------------------------------------ 2. chave restrita
New-Item -ItemType Directory -Force $DirChaves | Out-Null
# `utf8NoBOM`, nunca `ascii`: o comentário da chave pode ter acento, e `ascii` o transformaria em `?` calado. BOM
# o OpenSSH não entende.
Set-Content -LiteralPath $ArqChave -Value $linha -Encoding utf8NoBOM
# O sshd recusa arquivo de chaves gravável por terceiros (e nem lê, calado, se a ACL estiver frouxa).
# `/inheritance:r` corta a herança de ProgramData; concessões por SID, pelo mesmo motivo de sempre.
& icacls $ArqChave /inheritance:r /grant:r '*S-1-5-18:F' '*S-1-5-32-544:F' "${Usuario}:R" | Out-Null
Write-Host "chave restrita gravada em $ArqChave"

# ------------------------------------------------------------------ 3. sshd_config
$copia = "$SshdConf.bak-$(Get-Date -Format yyyyMMddHHmmss)"
Copy-Item -LiteralPath $SshdConf -Destination $copia
$atual = @(Get-Content -LiteralPath $SshdConf)
$i = [Array]::IndexOf($atual, $MARCA)
# Regera o bloco em vez de empilhar cópias dele. O `-eq 0` é caso real, não teoria: `$atual[0..(-1)]` em
# PowerShell devolve os elementos 0 e -1 (o último) — duas linhas do nada em vez de um arquivo vazio.
if ($i -gt 0) { $atual = @($atual[0..($i - 1)]) } elseif ($i -eq 0) { $atual = @() }
Set-Content -LiteralPath $SshdConf -Value (@($atual) + @('') + $bloco) -Encoding utf8NoBOM
$teste = & sshd -t 2>&1
if ($LASTEXITCODE -ne 0) {
  Copy-Item -LiteralPath $copia -Destination $SshdConf -Force
  throw "sshd -t recusou a configuracao (restaurada de $copia): $teste"
}
Restart-Service sshd
Write-Host "sshd_config atualizado (copia em $copia) e servico reiniciado"

# ------------------------------------------------------------------ 4. tirar o acesso de administrador
if ($RemoverChaveDeAdministrador -and (Test-Path -LiteralPath $AdminKeys)) {
  # Compara pelo MATERIAL da chave (tipo + base64), não pela linha inteira: o comentário no fim muda entre
  # máquinas e não faz parte da identidade da chave.
  $campos = $chave -split ' '
  $alvo = "$($campos[0]) $($campos[1])"
  $ficam = @(Get-Content -LiteralPath $AdminKeys | Where-Object { $_ -notlike "*$alvo*" })
  Copy-Item -LiteralPath $AdminKeys -Destination "$AdminKeys.bak-$(Get-Date -Format yyyyMMddHHmmss)"
  Set-Content -LiteralPath $AdminKeys -Value $ficam -Encoding utf8NoBOM
  Write-Host 'chave do central removida de administrators_authorized_keys'
  Write-Host 'CONFIRA ANTES o tunel novo: sem esta linha, a chave do central nao entra mais como Administrator.'
}

Write-Host ''
Write-Host 'Pronto. No CENTRAL, aponte o tunel para a conta nova e registre a chave de host deste worker:'
Write-Host "  pwsh -File scripts\worker-tunnel.ps1 -Worker <ip-deste-worker> -RegistrarChaveDeHost"
Write-Host "  pwsh -File scripts\worker-tunnel.ps1 -Worker <ip-deste-worker> -Usuario $Usuario -Instalar"
