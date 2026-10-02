#!/usr/bin/env python
"""Prova REAL e pontual do cache de prompt do verificador (J9, achado #100).

Pergunta que a medição responde: com o MESMO prompt do verificador (`prompts.VERIFIER_SYSTEM` + o ponto de cache que o
`AnthropicProvider` já pede) duas chamadas seguidas num modelo cujo mínimo cacheável o prefixo alcança devolvem
`cache_read > 0`? E no modelo de produção do verificador (Haiku 4.5, mínimo 4096), o prefixo é mesmo curto demais?

Regras (autorização do dono, 02/10): propósito claro, TETO por rodada (`--teto-usd`, padrão 0,50), ZERO retry
(`max_retries=0` e nenhuma repetição em caso de erro), custo registrado. Nenhum código do repositório vai ao provedor:
o texto é o prompt fixo do verificador e uma tela sintética. A chave é lida pelo próprio `EnvSettings` do arquivo
`.env` indicado em `--env`; este script nunca a lê, imprime nem grava, e a saída traz só contagens de tokens.

Uso (a partir da raiz do worktree, com o venv do backend):
    backend/.venv/Scripts/python.exe scripts/verifier-cache-probe.py --env C:/git/android/.env --out saida.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "backend"))

from app.config import AppConfigFile, Config, EnvSettings  # noqa: E402
from app.planning import prompts  # noqa: E402
from app.planning.anthropic_provider import AnthropicProvider  # noqa: E402
from app.planning.costs import usd  # noqa: E402
from app.planning.provider import AppContext, ScreenInput, StepContext, VerifyRequest  # noqa: E402

#: Modelo de produção do verificador primeiro; depois os de mínimo menor, onde o prefixo pode cruzar o piso.
MODELOS = ("claude-haiku-4-5", "claude-sonnet-5", "claude-opus-5-5")
CHAMADAS_POR_MODELO = 2


def contexto() -> StepContext:
    app = AppContext("qa-messenger", "QA Messenger", "com.pocqa.messenger", ".MainActivity", "dicas", {})
    return StepContext(run_id="probe", instance_id="android-probe", objective_summary="enviar uma mensagem",
                       parameters={}, step_key="send_message", step_title="Enviar", step_goal="Tocar em Enviar",
                       side_effect=True, commit_done=False, commit_guard=[], precondition=None,
                       postcondition_description="a mensagem aparece como enviada", remaining_steps=[], app=app,
                       account_label="conta-de-teste", required_delivery_level="sent")


def tela() -> ScreenInput:
    # Sem imagem: o ponto de cache fica no system, antes da mensagem; a imagem não muda o prefixo.
    return ScreenInput(width=720, height=1280, jpeg=None, package="com.pocqa.messenger", sensitive=False,
                       elements=['e1 | TextView | text="Olá, tudo bem?" | [10,10,300,60]',
                                 'e2 | TextView | text="Enviada ✓" | [10,70,300,110]'])


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", required=True, help="arquivo .env que o EnvSettings carrega (não é lido por este script)")
    ap.add_argument("--teto-usd", type=float, default=0.50)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    env = EnvSettings(_env_file=args.env)
    with tempfile.TemporaryDirectory() as tmp:
        cfg = Config(AppConfigFile.model_validate({}), env, root=Path(tmp))
        provider = AnthropicProvider(cfg)
        if not provider.configured:
            print(json.dumps({"erro": "chave Anthropic não configurada"}))
            return 2
        provider._client = provider._client.with_options(max_retries=0)      # noqa: SLF001 - zero retry
        precos = cfg.file.ai.prices
        gasto = 0.0
        linhas: list[dict] = []
        sistema = [{"type": "text", "text": prompts.VERIFIER_SYSTEM}]
        for modelo in MODELOS:
            medido = await provider._client.messages.count_tokens(           # noqa: SLF001 - não cobra
                model=modelo, system=sistema, messages=[{"role": "user", "content": "x"}])
            provider.models["verify"] = modelo
            minimo = cfg.model_caps(modelo).min_cache_tokens
            for n in range(1, CHAMADAS_POR_MODELO + 1):
                if gasto >= args.teto_usd:
                    linhas.append({"modelo": modelo, "chamada": n, "pulada": "teto de gasto atingido"})
                    continue
                try:
                    _veredito, u = await provider.verify(VerifyRequest(ctx=contexto(), screen=tela()))
                except Exception as exc:  # noqa: BLE001 - registra e segue; nenhuma nova tentativa
                    linhas.append({"modelo": modelo, "chamada": n, "erro": f"{type(exc).__name__}: {str(exc)[:200]}"})
                    continue
                fresco = max(0, u.input_tokens - u.cache_read_tokens - u.cache_write_tokens)
                custo = usd(precos, u.model or modelo, [fresco, u.cache_read_tokens, u.cache_write_tokens, u.output_tokens])
                gasto += custo
                linhas.append({"modelo": modelo, "respondeu": u.model, "chamada": n, "prefixo_medido_tokens": medido.input_tokens,
                               "minimo_cacheavel": minimo, "entrada_total": u.input_tokens, "cache_lido": u.cache_read_tokens,
                               "cache_gravado": u.cache_write_tokens, "saida": u.output_tokens, "ms": u.ms,
                               "custo_usd": round(custo, 6)})
        resultado = {"quando": datetime.now(timezone.utc).isoformat(timespec="seconds"), "teto_usd": args.teto_usd,
                     "gasto_usd": round(gasto, 6), "chamadas": linhas}
        Path(args.out).write_text(json.dumps(resultado, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(resultado, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
