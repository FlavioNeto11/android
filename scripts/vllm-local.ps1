<#
.SYNOPSIS
  Sobe um vLLM local (endpoint compatível com OpenAI) para o hub de IA — item 7.1.

.DESCRIPTION
  O provedor compatível com OpenAI já está no backend (`backend/app/planning/openai_provider.py`) e provado sem
  rede (`backend/tests/test_openai_provider.py`). O que ESTE script entrega é o outro lado: o servidor.

  Por que não roda sozinho na esteira, e por que ele PERGUNTA antes de qualquer coisa:

  - vLLM **não roda nativo no Windows**. O caminho aqui é Docker com back-end WSL2 e passagem de GPU
    (`--gpus all`), que exige o NVIDIA Container Toolkit instalado dentro da distribuição WSL.
  - A GPU desta máquina tem **8 GB** (RTX 2000 Ada Laptop). Isso limita o alvo a um modelo de visão pequeno e
    quantizado; `--max-model-len` e `--gpu-memory-utilization` abaixo são o que cabe, não o que se gostaria.
  - A RAM do host **já é disputada pelos emuladores**. Subir o servidor com o parque no ar pode derrubar
    aparelho em execução — e é por isso que o script confere e avisa antes.

  Depois de subir, aponte as funções para ele no `config/config.yaml` (o bloco comentado em `ai:` tem o exemplo)
  e meça com `scripts\eval-run.ps1`, registrando separado o que foi real e o que foi simulado.

.PARAMETER Model
  Id do modelo no Hugging Face. O padrão é um modelo de visão pequeno e quantizado, que é o que cabe em 8 GB.

.PARAMETER Port
  Porta local do endpoint. O `base_url` do config.yaml é http://127.0.0.1:<Port>/v1

.PARAMETER Confirm
  Sem isto o script só CONFERE o ambiente e imprime o comando — não baixa imagem, não ocupa GPU, não sobe nada.

.EXAMPLE
  powershell -File scripts\vllm-local.ps1                 # só confere e mostra o que faria
  powershell -File scripts\vllm-local.ps1 -Confirm        # sobe de verdade
#>
[CmdletBinding()]
param(
    [string] $Model = 'Qwen/Qwen2.5-VL-7B-Instruct-AWQ',
    [int]    $Port = 8001,
    [double] $GpuMemoryUtilization = 0.85,
    [int]    $MaxModelLen = 8192,
    [switch] $Confirm
)

$ErrorActionPreference = 'Stop'

function Passo($texto) { Write-Host "  $texto" -ForegroundColor DarkGray }

Write-Host "`n== Conferindo o ambiente para o vLLM local ==" -ForegroundColor Cyan

$problemas = @()

# 1) Docker com back-end Linux (é o único jeito no Windows).
try {
    $servidor = (& docker version --format '{{.Server.Os}}' 2>$null)
    if ($LASTEXITCODE -ne 0) { throw 'docker não respondeu' }
    Passo "docker: servidor $servidor"
    if ($servidor -ne 'linux') { $problemas += 'O Docker precisa estar no back-end Linux (WSL2), não Windows containers.' }
} catch { $problemas += 'Docker não está disponível nesta máquina (vLLM não roda nativo no Windows).' }

# 2) WSL 2.
try {
    $wsl = (& wsl --status 2>$null) -join ' '
    if ($LASTEXITCODE -eq 0) { Passo "wsl: ok" } else { $problemas += 'WSL não respondeu (`wsl --status`).' }
} catch { $problemas += 'WSL não está disponível.' }

# 3) GPU e VRAM livre.
try {
    $gpu = (& nvidia-smi --query-gpu=name,memory.total,memory.used --format=csv,noheader 2>$null) -join '; '
    if ($LASTEXITCODE -eq 0) {
        Passo "gpu: $gpu"
        $total = [int](($gpu -split ',')[1] -replace '[^0-9]', '')
        if ($total -lt 10000) {
            Passo "VRAM total $total MiB: só cabe modelo de visão pequeno/quantizado (o padrão deste script já é um)."
        }
    } else { $problemas += 'nvidia-smi não respondeu: sem GPU visível, o vLLM subiria em CPU (inviável aqui).' }
} catch { $problemas += 'nvidia-smi não encontrado.' }

# 4) Passagem de GPU do Docker para o WSL — o ponto que mais falha, e que só um teste real responde.
Passo 'passagem de GPU (nvidia-container-toolkit): conferida pelo teste abaixo, não por suposição.'

# 5) O parque está no ar? Subir o servidor agora disputaria RAM com emulador em execução.
try {
    $emus = @(& adb devices 2>$null | Select-String -Pattern '^emulator-\d+' -AllMatches)
    if ($emus.Count -gt 0) {
        Passo "ATENÇÃO: $($emus.Count) emulador(es) no ar. O vLLM disputa RAM com eles."
    }
} catch { }

if ($problemas.Count -gt 0) {
    Write-Host "`nPendências:" -ForegroundColor Yellow
    $problemas | ForEach-Object { Write-Host "  - $_" -ForegroundColor Yellow }
}

$imagem = 'vllm/vllm-openai:latest'
$comando = @(
    'docker run --rm -d --name vllm-local --gpus all',
    "-p ${Port}:8000",
    '-v "$env:USERPROFILE\.cache\huggingface:/root/.cache/huggingface"',
    "$imagem",
    "--model $Model",
    "--gpu-memory-utilization $GpuMemoryUtilization",
    "--max-model-len $MaxModelLen",
    '--served-model-name local-vl'
) -join ' '

Write-Host "`n== Passagem de GPU (teste barato, sem baixar o vLLM) ==" -ForegroundColor Cyan
Write-Host "  docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi" -ForegroundColor Gray

Write-Host "`n== Subir o servidor ==" -ForegroundColor Cyan
Write-Host "  $comando" -ForegroundColor Gray

Write-Host "`n== Apontar o backend para ele (config/config.yaml, bloco `ai:`) ==" -ForegroundColor Cyan
Write-Host @"
  ai:
    providers:
      local: {kind: openai, base_url: 'http://127.0.0.1:$Port/v1', sends_data_externally: false}
      anthropic: {kind: anthropic, fallback_model: claude-sonnet-5}
    models:
      local-vl: {vision: true, tools: true, strict_tools: false, structured_output: json_object,
                 thinking: false, effort: false, max_output: 4096}
    prices:
      local-vl: [0, 0, 0, 0]        # preço DECLARADO de zero; sem esta linha ele contaria pela tarifa mais cara
    roles:
      decide: {provider: local, model: local-vl, timeout_s: 90}
      verify: {provider: local, model: local-vl, timeout_s: 60}
      # SEM `fallback_provider` escrito, a falha do local NÃO cai no provedor pago — o erro sobe. Para permitir
      # a queda, e só então, escreva: fallback_provider: anthropic
"@ -ForegroundColor Gray

Write-Host "`n== Medir ==" -ForegroundColor Cyan
Write-Host "  powershell -File scripts\eval-run.ps1     # registre separado o que foi real e o que foi simulado" -ForegroundColor Gray

if (-not $Confirm) {
    Write-Host "`nNada foi executado. Rode com -Confirm para subir de verdade." -ForegroundColor Yellow
    exit 0
}
if ($problemas.Count -gt 0) {
    Write-Host "`nRecusando subir com pendências acima. Corrija-as e rode de novo." -ForegroundColor Red
    exit 2
}

Write-Host "`nSubindo $imagem (o primeiro arranque baixa vários GB)…" -ForegroundColor Cyan
& docker run --rm -d --name vllm-local --gpus all -p "${Port}:8000" `
    -v "$env:USERPROFILE\.cache\huggingface:/root/.cache/huggingface" `
    $imagem --model $Model --gpu-memory-utilization $GpuMemoryUtilization `
    --max-model-len $MaxModelLen --served-model-name local-vl
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "Pronto. Conferir:  curl http://127.0.0.1:$Port/v1/models" -ForegroundColor Green
