"""Sonda de formato por modelo (Fase 0.6): o que cada modelo configurado aceita e se o cache de prompt pega.
Faz 2 chamadas mínimas por função (decidir/verificar) com as MESMAS definições do backend e imprime, por modelo:
parâmetros recusados (thinking/effort/strict/fallback), tokens novos × cache lido e latência.
GASTA tokens de verdade (poucos centavos). A chave é lida pelo carregador do projeto e nunca é impressa.

Uso:  backend\\.venv\\Scripts\\python.exe scripts\\probe-models.py [--models claude-sonnet-5,claude-haiku-4-5] --yes
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.config import get_config  # noqa: E402
from app.planning.anthropic_provider import AnthropicProvider  # noqa: E402
from app.planning.provider import AppContext, DecisionRequest, ScreenInput, StepContext, VerifyRequest  # noqa: E402

APP = AppContext("qa-messenger", "QA Messenger", "com.pocqa.messenger", ".MainActivity", None, None)
ELEMENTS = ['e1 | TextView | text="Conta: qa-user-01" | id=account_label | [20,40,500,100]',
            'e2 | TextView | text="QA-001" | id=conversation_name | clickable | [20,560,700,620]',
            'e3 | Button | text="Perfil" | id=btn_profile | clickable | [520,40,700,100]']


def ctx() -> StepContext:
    return StepContext(run_id="r-sonda", instance_id="android-01", objective_summary="abrir a conversa com QA-001",
                       parameters={"recipient": "QA-001"}, step_key="open_conversation", step_title="Abrir a conversa",
                       step_goal="Abrir a conversa com QA-001", side_effect=False, commit_done=False, commit_guard=[],
                       precondition=None, postcondition_description="cabeçalho da conversa mostra QA-001",
                       remaining_steps=[], app=APP, account_label="qa-user-01")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="", help="lista separada por vírgula (padrão: os modelos por função do .env)")
    ap.add_argument("--yes", action="store_true")
    a = ap.parse_args()
    cfg = get_config()
    base = AnthropicProvider(cfg)
    if not base.configured:
        print("ANTHROPIC_API_KEY ausente no .env.")
        return 2
    models = [m for m in a.models.split(",") if m] or sorted(set(base.models.values()))
    if not a.yes:
        print(f"Vou fazer 4 chamadas mínimas em cada um de: {', '.join(models)}. Rode com --yes para confirmar o gasto.")
        return 2
    screen = ScreenInput(width=432, height=768, jpeg=None, elements=ELEMENTS, package="com.pocqa.messenger", sensitive=False)
    for model in models:
        p = AnthropicProvider(cfg)
        p.models = {k: model for k in p.models}
        print(f"\n== {model}")
        for role in ("decide", "verify"):
            for n in (1, 2):                                   # a 2ª chamada revela se o prefixo entrou no cache
                try:
                    if role == "decide":
                        d, u = await p.decide(DecisionRequest(ctx=ctx(), screen=screen))
                        out = f"ferramenta={d.tool} alvo={d.args.get('element_id')}"
                    else:
                        v, u = await p.verify(VerifyRequest(ctx=ctx(), screen=screen))
                        out = f"veredito={v.satisfied}"
                    fresh = u.input_tokens - u.cache_read_tokens - u.cache_write_tokens
                    print(f"  {role} #{n}: {out} · novos={fresh} cache_lido={u.cache_read_tokens} "
                          f"cache_gravado={u.cache_write_tokens} saída={u.output_tokens} · {u.ms} ms")
                except Exception as exc:  # noqa: BLE001
                    print(f"  {role} #{n}: ERRO {type(exc).__name__}: {exc}")
        print(f"  parâmetros que este modelo recusou (desligados automaticamente): {sorted(p._unsupported.get(model, set())) or 'nenhum'}")  # noqa: SLF001
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
