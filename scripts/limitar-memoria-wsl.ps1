<#
.SYNOPSIS
  Escreve o teto de memória do WSL2 em `%USERPROFILE%\.wslconfig` e mostra como aplicar.

.DESCRIPTION
  Item 10.3 (achado #146): o WSL desta máquina pode tomar até 56 GB dos 63,5 GB do host (`.wslconfig` atual:
  `memory=56GB`) sem que o backend saiba disso ou reserve nada contra isso — com 0 emuladores ligados já só
  sobrava 1 vaga local. `vmmemWSL` mediu 31,5 GB reais num momento em que 14 contêineres `gpv-*` de OUTRO
  projeto e o `farm-pg-teste` estavam rodando dentro do WSL.

  Este script só ESCREVE o arquivo — decidir o número novo e RODAR o script é do dono da máquina. Nunca é
  chamado automaticamente por este projeto (item 6 da chamada que criou este arquivo: nenhum ato real do mundo
  por conta própria). `-Simular` (padrão nenhum parâmetro além de `-MemoriaGB` é passado) mostra o que seria
  escrito sem tocar no arquivo.

  Depois de escrever, a mudança só vale para novas instâncias do WSL: rode `wsl --shutdown` (encerra TODAS as
  distribuições e contêineres rodando nelas — avise quem depende delas antes) e deixe o Windows recriar a VM na
  próxima vez que algo pedir WSL.

.PARAMETER MemoriaGB
  Novo teto, em GB. Decisão do dono: não há valor padrão. Pense no que MAIS este host hospeda no WSL (outros
  projetos, `docker ps` mostrou 14 contêineres `gpv-*` alheios na medição do achado) antes de escolher.

.PARAMETER Simular
  Mostra o `.wslconfig` resultante sem escrever nada.

.EXAMPLE
  pwsh -File scripts\limitar-memoria-wsl.ps1 -MemoriaGB 24 -Simular    # confere antes
  pwsh -File scripts\limitar-memoria-wsl.ps1 -MemoriaGB 24             # escreve
  wsl --shutdown                                                      # aplica (derruba WSL inteiro; avise antes)
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [ValidateRange(1, 512)]
  [int]$MemoriaGB,
  [switch]$Simular
)
$ErrorActionPreference = 'Stop'

$caminho = Join-Path $env:USERPROFILE '.wslconfig'
$linhaNova = "memory=${MemoriaGB}GB"

$conteudo = if (Test-Path $caminho) { Get-Content $caminho -Raw } else { '' }
$linhas = if ($conteudo) { $conteudo -split "`r?`n" } else { @() }

if ($linhas -notcontains '[wsl2]' -and -not ($linhas | Where-Object { $_.Trim() -eq '[wsl2]' })) {
  $linhas = @('[wsl2]') + $linhas       # arquivo novo, ou sem a seção — cria no topo
}

$dentroDoWsl2 = $false
$achouMemoria = $false
$saida = foreach ($linha in $linhas) {
  if ($linha.Trim() -eq '[wsl2]') { $dentroDoWsl2 = $true; $linha; continue }
  if ($linha.Trim().StartsWith('[') -and $linha.Trim() -ne '[wsl2]') { $dentroDoWsl2 = $false }
  if ($dentroDoWsl2 -and $linha.Trim() -match '^memory\s*=') {
    $achouMemoria = $true
    $linhaNova     # substitui o valor antigo (ex.: "memory=56GB") em vez de duplicar a chave
  } else {
    $linha
  }
}
if (-not $achouMemoria) {
  # Não achou a chave dentro de [wsl2]: insere logo depois do cabeçalho da seção.
  $saida = foreach ($linha in $saida) {
    $linha
    if ($linha.Trim() -eq '[wsl2]') { $linhaNova }
  }
}
$resultado = ($saida -join "`r`n").TrimEnd() + "`r`n"

if ($Simular) {
  Write-Host "SIMULADO — conteúdo que seria escrito em $caminho`:"
  Write-Host $resultado
  return
}
Set-Content -Path $caminho -Value $resultado -Encoding utf8 -NoNewline
Write-Host "Escrito: $caminho ($linhaNova)"
Write-Warning ("Não aplicado ainda. Rode 'wsl --shutdown' para a VM subir de novo com o teto novo — isso " +
               "encerra TODAS as distribuições e contêineres do WSL nesta máquina; avise antes de rodar.")
