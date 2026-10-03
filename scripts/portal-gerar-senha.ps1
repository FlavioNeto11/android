<#
Senha do portal publico da Central (API_TOKEN). Rodado PELO DONO.

O que faz: gera uma senha aleatoria de 32 caracteres, grava a linha API_TOKEN=... no .env da raiz do
checkout central e copia a senha para a area de transferencia. NAO mostra a senha na tela.

  pwsh -File scripts/portal-gerar-senha.ps1            -> cria a senha. Se ja existir uma, nao mexe em nada.
  pwsh -File scripts/portal-gerar-senha.ps1 -Trocar    -> TROCA a senha existente por uma nova (use se a antiga
                                                          passou por chat, Telegram, e-mail ou tela compartilhada).

O .env e o da raiz do checkout onde este script esta (-Env aponta outro). Nenhuma sessao de IA roda este script
no .env de verdade: quem gera, ve e guarda a senha e o dono.

A senha passa a valer no proximo reinicio do central; ate la vale a antiga. Para ver depois: abra o .env e
procure a linha API_TOKEN=.

Na troca, so a linha API_TOKEN= muda. O script confere, antes de gravar, que todas as outras linhas do .env
ficaram identicas; se nao ficarem, nao grava nada.

Texto so em ASCII de proposito: roda igual no PowerShell 7 e no 5.1.
#>
param(
    [string]$Env = (Join-Path (Split-Path -Parent $PSScriptRoot) '.env'),
    [switch]$Trocar,
    # So para ensaio com um arquivo de mentira: nao toca na area de transferencia do dono.
    [switch]$SemAreaDeTransferencia
)
$ErrorActionPreference = 'Stop'

if (-not (Test-Path $Env)) { Write-Host "[PAROU] Nao achei $Env" -ForegroundColor Red; exit 1 }

# Ja existe com valor? So a resposta sim/nao sai daqui; o valor nunca e impresso.
$jaTem = [bool](Select-String -Path $Env -Pattern '^\s*API_TOKEN\s*=\s*\S+' -Quiet)
if ($jaTem -and -not $Trocar) {
    Write-Host 'O .env ja tem um API_TOKEN com valor. Nao mexi em nada.'
    Write-Host 'Para ver a senha, abra o arquivo e procure a linha API_TOKEN=.'
    Write-Host 'Para TROCAR por uma nova: rode de novo com -Trocar.'
    exit 0
}

# 24 bytes do gerador criptografico -> 32 caracteres [A-Za-z0-9_-] (sem aspas, espaco ou # que confundam o .env).
$bytes = New-Object byte[] 24
$rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
$senha = [Convert]::ToBase64String($bytes).Replace('+', '-').Replace('/', '_').Replace('=', '')

$utf8 = New-Object System.Text.UTF8Encoding($false, $true)   # sem BOM proprio; falha em byte invalido
$padraoDaLinha = '(?m)^([ \t]*API_TOKEN[ \t]*=)[^\r\n]*'
$original = $utf8.GetString([System.IO.File]::ReadAllBytes($Env))
$quantas = [regex]::Matches($original, $padraoDaLinha).Count

if ($quantas -gt 1) {
    Write-Host '[PAROU] O .env tem mais de uma linha API_TOKEN=. Deixe so uma (abra o arquivo) e rode de novo.' -ForegroundColor Red
    $senha = $null; exit 1
}

if ($quantas -eq 1) {
    # Troca no lugar: so o que vem depois do "=" naquela linha.
    $novo = [regex]::Replace($original, $padraoDaLinha, { param($m) $m.Groups[1].Value + $senha })
    # Conferencia antes de gravar: mesmo numero de linhas e toda linha que nao e a do API_TOKEN identica.
    $antes = $original -split "`n"
    $depois = $novo -split "`n"
    $ok = ($antes.Count -eq $depois.Count)
    if ($ok) {
        for ($i = 0; $i -lt $antes.Count; $i++) {
            $ehALinha = $antes[$i] -match '^[ \t]*API_TOKEN[ \t]*='
            if (-not $ehALinha -and ($antes[$i] -cne $depois[$i])) { $ok = $false; break }
            if ($ehALinha -and ($depois[$i] -notmatch '^[ \t]*API_TOKEN[ \t]*=[A-Za-z0-9_-]{32}\r?$')) { $ok = $false; break }
        }
    }
    if (-not $ok) {
        Write-Host '[PAROU] A conferencia antes de gravar falhou. NAO gravei nada: o .env esta como estava.' -ForegroundColor Red
        $senha = $null; $original = $null; $novo = $null; exit 1
    }
    [System.IO.File]::WriteAllBytes($Env, $utf8.GetBytes($novo))
    $original = $null; $novo = $null; $antes = $null; $depois = $null
    $feito = 'trocada'
} else {
    # Nao existe a linha: acrescenta no fim. Se o arquivo nao termina em quebra de linha, a linha nova colaria
    # na ultima variavel e estragaria as duas.
    $prefixo = ''
    if ($original.Length -gt 0 -and -not $original.EndsWith("`n")) { $prefixo = "`r`n" }
    $original = $null
    $semBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::AppendAllText($Env, $prefixo + 'API_TOKEN=' + $senha + "`r`n", $semBom)
    $feito = 'gravada'
}

$copiada = $false
if (-not $SemAreaDeTransferencia) {
    try { Set-Clipboard -Value $senha; $copiada = $true } catch { $copiada = $false }
}
$senha = $null

Write-Host "Senha do portal $feito na linha API_TOKEN= do .env (32 caracteres). Ela NAO foi mostrada na tela."
if ($copiada) { Write-Host 'Ela esta na area de transferencia desta maquina: cole no seu gerenciador de senhas agora.' }
elseif (-not $SemAreaDeTransferencia) { Write-Host 'Nao consegui copiar para a area de transferencia: abra o .env para ver a senha.' }
if ($feito -eq 'trocada') { Write-Host 'A senha ANTIGA continua valendo ate o proximo reinicio do central; depois dele, so a nova.' }
else { Write-Host 'Passa a valer no proximo reinicio do central.' }
Write-Host 'Nunca cole a senha em chat nenhum: a quem estiver ajudando, diga so "pronto".'
