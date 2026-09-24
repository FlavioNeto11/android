<#
.SYNOPSIS
  Confere o ambiente para um Ollama local (endpoint compatível com OpenAI) no hub de IA — item 7.8.

.DESCRIPTION
  Este script NÃO instala o Ollama, NÃO baixa modelo nenhum e NÃO sobe servidor nenhum — ele só CONFERE o que já
  está nesta máquina e diz o que falta. É o irmão mais simples do `scripts\vllm-local.ps1`: o Ollama roda NATIVO
  no Windows (sem WSL2, sem Docker, sem passagem de GPU para conferir), então não há nada aqui para "subir" — só
  para checar antes de o dono decidir ligar a função.

  O que é conferido, nesta ordem:

  1. GPU NVIDIA e VRAM total (`nvidia-smi`) — informativo: o Ollama roda em CPU também, só mais devagar.
  2. `ollama --version` — o binário está instalado e no PATH.
  3. `ollama list` contém o modelo pedido (`-Model`) — baixado, não só instalado.
  4. O endpoint HTTP responde (`http://127.0.0.1:11434/v1/models`) — o serviço está NO AR, não só instalado.

  Ao final, imprime o bloco de `config/config.yaml` pronto para colar em `ai:` — o mesmo formato comentado que
  está em `config/config.example.yaml`. Colar sozinho não liga nada: falta o `.env`/YAML apontar `ai.roles` para
  ele, e é o dono quem decide isso (item 7.8 é "preparado no código, sem ligar").

.PARAMETER Model
  Id do modelo no Ollama (como `ollama list` o mostra). O padrão é o modelo de visão pequeno já testado no hub.

.PARAMETER Port
  Porta do serviço Ollama. O padrão (11434) é o dele mesmo — mudar aqui só ajuda a conferir uma instalação que
  já roda noutra porta; não move o serviço.

.EXAMPLE
  powershell -File scripts\ollama-local.ps1
  powershell -File scripts\ollama-local.ps1 -Model qwen3-vl:4b -Port 11434
#>
[CmdletBinding()]
param(
    [string] $Model = 'qwen3-vl:4b',
    [int]    $Port = 11434
)

$ErrorActionPreference = 'Stop'

function Passo($texto) { Write-Host "  $texto" -ForegroundColor DarkGray }

Write-Host "`n== Conferindo o ambiente para o Ollama local (nada é instalado, baixado ou ligado) ==" -ForegroundColor Cyan

$problemas = @()

# 1) GPU e VRAM — informativo: o Ollama também roda em CPU, só que mais devagar.
try {
    $gpu = (& nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>$null) -join '; '
    if ($LASTEXITCODE -eq 0 -and $gpu) {
        Passo "gpu: $gpu"
    } else {
        Passo 'nvidia-smi não respondeu: sem GPU NVIDIA visível, o Ollama roda em CPU (mais lento, mas funciona).'
    }
} catch {
    Passo 'nvidia-smi não encontrado: sem GPU NVIDIA visível, o Ollama roda em CPU (mais lento, mas funciona).'
}

# 2) O binário está instalado e no PATH.
$versao = $null
try {
    $versao = (& ollama --version 2>$null)
    if ($LASTEXITCODE -eq 0 -and $versao) {
        Passo "ollama: $versao"
    } else {
        $problemas += 'ollama não respondeu a `ollama --version` — instalação ausente ou fora do PATH.'
    }
} catch {
    $problemas += 'ollama não encontrado — baixe em https://ollama.com/download (este script não instala nada).'
}

# 3) O modelo pedido já foi baixado (instalar o binário não baixa modelo nenhum sozinho).
if ($versao) {
    try {
        $lista = (& ollama list 2>$null)
        if ($LASTEXITCODE -eq 0) {
            # -SimpleMatch já trata o padrão como texto literal (sem regex) — escapar aqui duplicaria as barras
            # invertidas do escape e QUEBRARIA a busca para modelos com ponto no nome (ex.: qwen2.5-vl:7b).
            $achado = $lista | Select-String -Pattern $Model -SimpleMatch
            if ($achado) {
                Passo "modelo '$Model' já baixado: $achado"
            } else {
                $problemas += "modelo '$Model' não aparece em 'ollama list' — baixe com: ollama pull $Model"
            }
        } else {
            $problemas += 'ollama list não respondeu (o serviço do Ollama está rodando?).'
        }
    } catch {
        $problemas += 'não foi possível rodar `ollama list`.'
    }
} else {
    Passo 'ollama list: pulado (binário ausente).'
}

# 4) O serviço está NO AR — instalado e com modelo baixado não é o mesmo que o endpoint respondendo.
$endpoint = "http://127.0.0.1:$Port/v1/models"
try {
    $resp = Invoke-WebRequest -Uri $endpoint -TimeoutSec 3 -UseBasicParsing 2>$null
    if ($resp.StatusCode -eq 200) {
        Passo "endpoint: $endpoint respondeu (200)"
    } else {
        $problemas += "endpoint $endpoint respondeu com status $($resp.StatusCode)."
    }
} catch {
    $problemas += "endpoint $endpoint não respondeu — o serviço do Ollama está rodando? (ollama serve, " +
                  'ou o instalador do Windows já registra como serviço automático.)'
}

if ($problemas.Count -gt 0) {
    Write-Host "`nPendências:" -ForegroundColor Yellow
    $problemas | ForEach-Object { Write-Host "  - $_" -ForegroundColor Yellow }
} else {
    Write-Host "`nAmbiente pronto: binário, modelo e endpoint conferidos." -ForegroundColor Green
}

Write-Host "`n== Apontar o backend para ele (config/config.yaml, bloco 'ai:') ==" -ForegroundColor Cyan
Write-Host @"
  ai:
    providers:
      local:
        kind: openai
        base_url: http://127.0.0.1:$Port/v1
        sends_data_externally: false
        extra_body: {options: {num_ctx: 16384}}   # o tamanho de contexto fica no SERVIDOR (ver abaixo), isto só
                                                    # repassa a opção por chamada
      anthropic:
        kind: anthropic
        fallback_model: claude-sonnet-5
    models:
      ${Model}: {vision: true, tools: true, strict_tools: false, structured_output: json_object,
               thinking: false, effort: false, max_output: 4096}
    prices:
      ${Model}: [0, 0, 0, 0]   # preço DECLARADO de zero; sem esta linha ele contaria pela tarifa mais cara
    roles:
      decide: {provider: local, model: ${Model}, fallback_provider: anthropic, timeout_s: 45}
      verify: {provider: anthropic}
      plan: {provider: anthropic}
      escalation: {provider: anthropic}
      social: {provider: anthropic, refusal_fallback: false}
"@ -ForegroundColor Gray

Write-Host "`n== Contexto do modelo (item 7.8) ==" -ForegroundColor Cyan
Write-Host '  O `num_ctx` do extra_body acima é por CHAMADA; sem o SERVIDOR aceitar esse tamanho o prompt é' -ForegroundColor Gray
Write-Host '  truncado em silêncio (nenhum erro). Configure no Ollama, por UMA destas vias:' -ForegroundColor Gray
Write-Host '    - variável de ambiente do serviço: OLLAMA_CONTEXT_LENGTH=16384 (e reinicie o serviço)' -ForegroundColor Gray
Write-Host "    - Modelfile do modelo: PARAMETER num_ctx 16384, depois: ollama create $Model-16k -f Modelfile" -ForegroundColor Gray

Write-Host "`nNada foi instalado, baixado nem ligado. Corrija as pendências acima e rode de novo para conferir." -ForegroundColor Yellow
if ($problemas.Count -gt 0) { exit 2 }
exit 0
