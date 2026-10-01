"""CLI do retrieval de contexto: `python -m app.modules.context_retrieval.presentation.cli "<pergunta>"`.

Para a pessoa e para os scripts de preparação de tarefa (`scripts/plano-100-pacotes.py --contexto`). Sem `--mode`, vale
o `context_retrieval:` da configuração, e com ele desligado a CLI diz que está desligado e sai com 0, sem tocar no
repositório. `--mode` é um pedido EXPLÍCITO de quem digitou (útil para experimentar), e `--provider` aceita só
`none` e `fake`: o provedor remoto nunca é ligado por flag de linha de comando, só pela configuração, e ainda assim
a política de envio nega repositório privado (ADR-063).

Saída: texto curto para leitura ou `--json` (o `ContextPack`, sem o texto das regiões a menos que `--with-text`).
Código de saída: 0 (inclusive desligado ou sem resultado), 2 para uso incorreto. Falha do retrieval nunca é erro.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from ....config import load_config
from ..domain.model import ContextPack, RetrievalMode
from ..infrastructure.providers.factory import build_provider
from ..wiring import build_service


def _texto(pack: ContextPack) -> str:
    linhas = [f"modo={pack.mode.value} origem={pack.origin} revisão={pack.revision[:12]} "
              f"latência={pack.metadata.get('latency_ms')}ms"
              + (f" fallback={pack.metadata.get('fallback_reason')}" if pack.metadata.get("fallback_used") else "")]
    linhas += [f"  arquivo  {f.path}  ({f.source}, {f.score:.3f})" for f in pack.files]
    linhas += [f"  trecho   {r.path}:{r.start_line}-{r.end_line}" for r in pack.regions]
    linhas += [f"  aviso    {w}" for w in pack.warnings]
    if not pack.files:
        linhas.append("  (nenhum arquivo candidato)")
    return "\n".join(linhas)


def _stdout_utf8() -> None:
    """A pergunta e os caminhos têm acento e seta; o console do Windows (cp1252) e um pipe sem codificação quebravam a CLI."""
    for fluxo in (sys.stdout, sys.stderr):
        reconfigurar = getattr(fluxo, "reconfigure", None)
        if reconfigurar is not None:
            reconfigurar(encoding="utf-8", errors="replace")


def main(argv: Sequence[str] | None = None) -> int:
    _stdout_utf8()
    ap = argparse.ArgumentParser(prog="context_retrieval", description=__doc__.splitlines()[0])
    ap.add_argument("query", help="a pergunta ou a descrição da tarefa")
    ap.add_argument("--root", type=Path, default=None, help="raiz do repositório (padrão: a do projeto)")
    ap.add_argument("--mode", choices=[m.value for m in RetrievalMode], default=None)
    ap.add_argument("--provider", choices=["none", "fake"], default=None, help="só para experimentar o pipeline")
    ap.add_argument("--top-k", type=int, default=None)
    ap.add_argument("--scope", action="append", default=[], help="prefixo de caminho; repetível")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--with-text", action="store_true", help="inclui o texto das regiões (já sem segredo)")
    args = ap.parse_args(argv)

    cfg = load_config()
    modo = RetrievalMode(args.mode) if args.mode else None
    provedor = build_provider(args.provider, env={}) if args.provider else None
    servico = build_service(cfg, root=args.root, mode=modo, provider=provedor)
    pack = servico.gather(args.query, scope=tuple(args.scope), top_k=args.top_k, with_text=args.with_text)
    if pack is None:
        print(json.dumps({"enabled": False, "mode": "disabled"}) if args.json else
              "retrieval desligado (context_retrieval.enabled: false); nada foi lido. Use --mode para pedir um.")
        return 0
    print(json.dumps(pack.to_dict(with_text=args.with_text), ensure_ascii=False) if args.json else _texto(pack))
    return 0


if __name__ == "__main__":
    sys.exit(main())
