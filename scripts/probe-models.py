"""Sonda de formato por modelo (Fase 0.6): o que cada modelo configurado aceita e se o cache de prompt pega.
Faz 2 chamadas mínimas por função (decidir/verificar) com as MESMAS definições do backend e imprime, por modelo:
parâmetros recusados (thinking/effort/strict/fallback), tokens novos × cache lido e latência.
GASTA tokens de verdade (poucos centavos). A chave é lida pelo carregador do projeto e nunca é impressa.

Item 7.1: com `--yaml` a sonda também IMPRIME o bloco `ai.models` pronto para colar no config.yaml. Era esse o
elo que faltava — o resultado morria no terminal, e a capacidade de cada modelo continuava sendo aprendida por
erro 400 a cada arranque (17 recusas em 3 dias de log real). O que a sonda mede passa a virar declaração.

Uso:  backend\\.venv\\Scripts\\python.exe scripts\\probe-models.py [--models claude-sonnet-5] --yaml --yes
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
    ap.add_argument("--yaml", action="store_true", help="imprime o bloco ai.models pronto para colar no config.yaml")
    # Achado #98: ~10% de HTTP 500 no verificador em Haiku 4.5 na noite da troca (7 de 60), sem causa determinada
    # — pode ser instabilidade passageira do provedor ou o formato da requisição (endpoint beta com `fallbacks`
    # + `json_schema` nesse modelo). `--repeticoes` faz N chamadas por função (padrão 2) em vez de 1, para dar
    # chance ao 500 de se repetir; `--sem-fallback` desliga `betas=[...]/fallbacks="default"` (endpoint beta
    # comum) para comparar: se o 500 sumir sem o beta, o formato é suspeito; se persistir, é o provedor.
    ap.add_argument("--repeticoes", type=int, default=2, help="chamadas por função além da 1ª (revela cache E repetição de 500)")
    ap.add_argument("--sem-fallback", action="store_true",
                    help="desliga o endpoint beta de fallback (betas=[...]/fallbacks=\"default\") nesta rodada, "
                         "para comparar a taxa de HTTP 500 com e sem ele")
    a = ap.parse_args()
    cfg = get_config()
    base = AnthropicProvider(cfg)
    if not base.configured:
        print("ANTHROPIC_API_KEY ausente no .env.")
        return 2
    models = [m for m in a.models.split(",") if m] or sorted(set(base.models.values()))
    n_chamadas = 2 * (1 + max(0, a.repeticoes))
    if not a.yes:
        print(f"Vou fazer {n_chamadas} chamadas mínimas em cada um de: {', '.join(models)}"
              f"{' (sem o fallback beta)' if a.sem_fallback else ''}. Rode com --yes para confirmar o gasto.")
        return 2
    medidos: dict[str, list[str]] = {}
    erros_5xx: dict[str, int] = {}
    screen = ScreenInput(width=432, height=768, jpeg=None, elements=ELEMENTS, package="com.pocqa.messenger", sensitive=False)
    for model in models:
        p = AnthropicProvider(cfg)
        p.models = {k: model for k in p.models}
        if a.sem_fallback:
            p._use_fallback = False  # noqa: SLF001 - comparação deliberada, não é o padrão de produção
        print(f"\n== {model}{' [sem fallback beta]' if a.sem_fallback else ''}")
        erros_5xx[model] = 0
        for role in ("decide", "verify"):
            for n in range(1, 2 + max(0, a.repeticoes)):        # a partir da 2ª chamada, revela cache E repetição de 500
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
                    status = getattr(exc, "status", None)
                    if status and status >= 500:
                        erros_5xx[model] += 1
                    print(f"  {role} #{n}: ERRO {type(exc).__name__} (status={status}): {exc}")
        recusados = sorted(p._unsupported.get(model, set()))  # noqa: SLF001
        print(f"  parâmetros que este modelo recusou (desligados automaticamente): {recusados or 'nenhum'}")
        print(f"  HTTP 5xx nesta rodada: {erros_5xx[model]} de {n_chamadas}")
        medidos[model] = recusados
    if a.yaml:
        print("\n# ---- cole em config/config.yaml, sob `ai.models:` (item 7.1) ----")
        for model, recusados in medidos.items():
            print(f"  {model}: {{vision: true, tools: true, "
                  f"strict_tools: {str('strict' not in recusados).lower()}, structured_output: json_schema, "
                  f"thinking: {str('thinking' not in recusados).lower()}, "
                  f"effort: {str('effort' not in recusados).lower()}}}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
