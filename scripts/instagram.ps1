<#
.SYNOPSIS
  Operação dos perfis do Instagram pela linha de comando: listar, conectar, verificar, memória, interações,
  aprovações e política. Tudo pelo backend, com as mesmas regras do portal.

.DESCRIPTION
  A senha NUNCA entra aqui. Ela é cadastrada no portal, vai direto para o cofre e é digitada no aparelho pelo canal
  de entrada sensível. Este script não tem parâmetro de senha de propósito: um parâmetro assim acabaria no
  histórico do PowerShell, que é exatamente o que se quer evitar.

.PARAMETER Comando
  perfis | conectar | verificar | sair | memoria | interacoes | aprovacoes | aprovar | editar | rejeitar |
  politica | capabilities | releases | importar

.EXAMPLE
  .\scripts\instagram.ps1 perfis
.EXAMPLE
  .\scripts\instagram.ps1 conectar -Perfil ig-abc123
.EXAMPLE
  .\scripts\instagram.ps1 aprovar -Id apr-xyz -Nota "pode mandar"
.EXAMPLE
  .\scripts\instagram.ps1 editar -Id apr-xyz -Conteudo "bom dia, Ana!"
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory, Position = 0)]
  [ValidateSet('perfis', 'conectar', 'verificar', 'sair', 'memoria', 'interacoes', 'aprovacoes', 'aprovar',
               'editar', 'rejeitar', 'politica', 'capabilities', 'releases', 'importar')]
  [string]$Comando,
  [string]$Perfil,
  [string]$Id,
  [string]$Conteudo,
  [string]$Nota,
  [string]$Assunto,
  [int]$Limite = 20,
  [string]$Base = 'http://127.0.0.1:8000'
)
$ErrorActionPreference = 'Stop'

function Invoke-Api {
  param([string]$Method, [string]$Path, $Body)
  $uri = "$Base/api$Path"
  try {
    if ($null -ne $Body) {
      return Invoke-RestMethod -Method $Method -Uri $uri -ContentType 'application/json' `
        -Body ($Body | ConvertTo-Json -Depth 8 -Compress)
    }
    return Invoke-RestMethod -Method $Method -Uri $uri
  } catch {
    $resposta = $_.ErrorDetails.Message
    if ($resposta) { throw "A API recusou: $resposta" }
    throw
  }
}

function Resolve-Perfil {
  param([string]$Valor)
  if (-not $Valor) { throw 'Informe -Perfil (o id, ex.: ig-abc123, ou o @usuário).' }
  if ($Valor -notmatch '^ig-') {
    $alvo = $Valor.TrimStart('@')
    $achado = (Invoke-Api GET '/instagram/profiles') | Where-Object { $_.username -eq $alvo }
    if (-not $achado) { throw "Nenhum perfil com o usuário '$alvo'." }
    return $achado.id
  }
  return $Valor
}

switch ($Comando) {
  'perfis' {
    Invoke-Api GET '/instagram/profiles' |
      Select-Object id, username, instance_id,
        @{n = 'sessao'; e = { $_.session.status } },
        @{n = 'conta_observada'; e = { $_.session.observed_username } },
        @{n = 'senha'; e = { if ($_.credential.configured) { 'guardada' } else { 'não configurada' } } },
        persona_name |
      Format-Table -AutoSize
  }
  'conectar'   { Invoke-Api POST "/instagram/profiles/$(Resolve-Perfil $Perfil)/connect" | Format-List }
  'verificar'  { Invoke-Api POST "/instagram/profiles/$(Resolve-Perfil $Perfil)/verify" | Format-List }
  'sair'       { Invoke-Api POST "/instagram/profiles/$(Resolve-Perfil $Perfil)/logout" | Format-List }
  'memoria' {
    $pid_ = Resolve-Perfil $Perfil
    if ($Conteudo) {
      if (-not $Assunto) { throw 'Para guardar um fato, informe -Assunto (ex.: @ana).' }
      Invoke-Api POST "/instagram/profiles/$pid_/memory" @{ subject = $Assunto; content = $Conteudo } | Format-List
    } else {
      Invoke-Api GET "/instagram/profiles/$pid_/memory?limit=$Limite" |
        Select-Object subject, content, importance, occurrences, source | Format-Table -AutoSize
    }
  }
  'interacoes' {
    Invoke-Api GET "/instagram/profiles/$(Resolve-Perfil $Perfil)/interactions?limit=$Limite" |
      Select-Object occurred_at, type, counterparty, status, outgoing_content | Format-Table -AutoSize
  }
  'aprovacoes' {
    $rota = '/approvals?status=pending'
    if ($Perfil) { $rota += "&profile_id=$(Resolve-Perfil $Perfil)" }
    Invoke-Api GET $rota | Select-Object id, capability, target, content, created_at | Format-Table -AutoSize
  }
  'aprovar'  {
    if (-not $Id) { throw 'Informe -Id da aprovação (veja em: instagram.ps1 aprovacoes).' }
    Invoke-Api POST "/approvals/$Id/decide" @{ verb = 'approve'; note = $Nota } | Format-List
  }
  'editar'   {
    if (-not $Id -or -not $Conteudo) { throw 'Informe -Id e -Conteudo (o texto que deve ser enviado).' }
    Invoke-Api POST "/approvals/$Id/decide" @{ verb = 'edit'; content = $Conteudo; note = $Nota } | Format-List
  }
  'rejeitar' {
    if (-not $Id) { throw 'Informe -Id da aprovação.' }
    Invoke-Api POST "/approvals/$Id/decide" @{ verb = 'reject'; note = $Nota } | Format-List
  }
  'politica' {
    $p = Invoke-Api GET "/instagram/profiles/$(Resolve-Perfil $Perfil)/policy"
    Write-Host 'Limites:' -ForegroundColor Cyan
    $p.limits | Format-List
    Write-Host 'Ações (padrão do catálogo entre parênteses quando diferente):' -ForegroundColor Cyan
    $p.capabilities.PSObject.Properties | ForEach-Object {
      $padrao = $p.defaults.($_.Name)
      [pscustomobject]@{ acao = $_.Name; politica = $_.Value; padrao = if ($_.Value -ne $padrao) { $padrao } else { '' } }
    } | Format-Table -AutoSize
  }
  'capabilities' {
    Invoke-Api GET '/capabilities' | Select-Object key, title, side_effect, risk, default_policy, limit_bucket |
      Format-Table -AutoSize
  }
  'releases' {
    Invoke-Api GET '/releases' |
      Select-Object package_name, version_name, version_code, artifact_type, status,
        @{n = 'assinatura'; e = { $_.signature_sha256.Substring(0, 16) + '…' } },
        @{n = 'aparelhos'; e = { $_.devices -join ',' } } |
      Format-Table -AutoSize
  }
  'importar' {
    Write-Host 'Lendo apks/inbox… pacote, versão, splits e assinatura vêm do arquivo, não do nome dele.'
    (Invoke-Api POST '/releases/import' @{}).imported |
      Select-Object release_id, package_name, version_code, status, detail | Format-Table -AutoSize
  }
}
