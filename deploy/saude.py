"""Saúde do central DENTRO do contêiner: está vivo (padrão, o `healthcheck`) e está pronto (`--pronto`).

    python /app/deploy/saude.py            # vivo?   saída 0 = quem responde na 8000 É a Farm
    python /app/deploy/saude.py --pronto   # pronto? vivo + banco respondendo + esquema na migração da imagem

Por que não `curl -f /api/health`. Três motivos, todos medidos no código e não supostos:

1. **A rota responde 200 sempre**, com o estado no corpo (`status: ok | degraded | error`). Conferir o código HTTP
   não diz nada.
2. **"Alguém responde na 8000" não é "a Farm responde"** (26/09/2026, `app/identidade.py`): o `cartorio-api-1`
   devolvia 404 na mesma porta e o supervisor o tomou por backend vivo. Aqui a pergunta é a mesma do supervisor —
   `corpo_e_da_farm`, a função dele, reaproveitada em vez de reescrita.
3. **No contêiner, `status` é `error` para sempre**: `sdk_missing` é problema DURO em `state.health()`, e a imagem
   não tem Android SDK de propósito (os emuladores vivem fora: Windows e worker). Um healthcheck por `status`
   marcaria o contêiner doente desde o primeiro segundo.

Por isso vivo = identidade, e SÓ identidade: `degraded` e `error` são a Farm viva. O Docker (fora do Swarm) nunca
reinicia um contêiner por estar `unhealthy` — o estado é informativo —, então esta resposta nunca vira ciclo de
reinício. Pronto é outra pergunta, feita à mão (ou por quem orquestra) antes de mandar trabalho.

Sem dependência nenhuma além da biblioteca padrão e de `app/identidade.py` (que não importa o app). `python:slim`
não traz `curl`.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

#: A raiz do projeto: `/app` na imagem, o checkout quando os testes carregam este arquivo.
RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "backend"))

from app.identidade import corpo_e_da_farm  # noqa: E402 - depende do sys.path acima

#: O socket PRINCIPAL, visto de dentro do contêiner. Loopback de propósito: o par é 127.0.0.1 de verdade, então a
#: isenção de loopback de `security.access.avaliar` vale e a sondagem não precisa do API_TOKEN.
URL = "http://127.0.0.1:8000/api/health"

#: Problemas que impedem dizer "pronto" mesmo com o banco respondendo: o banco caiu no meio, ou o arquivo de uma
#: migração já aplicada mudou (o esquema vivo pode não ser o que o código espera).
BLOQUEIAM_PRONTO = frozenset({"database_down", "migration_changed"})


def ler(url: str = URL, prazo_s: float = 4.0) -> bytes | None:
    """O corpo de `/api/health`, ou `None` se ninguém respondeu. Erro HTTP ainda tem corpo, e ele é lido: um 503 da
    Farm continua sendo a Farm."""
    try:
        with urllib.request.urlopen(url, timeout=prazo_s) as resposta:  # noqa: S310 - URL fixa, loopback
            return resposta.read()
    except urllib.error.HTTPError as exc:
        return exc.read()
    except (urllib.error.URLError, OSError, ValueError):
        return None


def vivo(corpo: bytes | str | None) -> bool:
    """Liveness: quem respondeu é a Farm. O `status` NÃO entra (ver o topo)."""
    return corpo_e_da_farm(corpo)


def ultima_migracao(pasta: Path = RAIZ / "backend" / "migrations") -> str | None:
    """A migração mais nova QUE ESTA IMAGEM traz (`041_loja_de_apps`), no formato de `health.migration`."""
    nomes = sorted(p.stem for p in pasta.glob("*.sql"))
    return nomes[-1] if nomes else None


def pronto(corpo: bytes | str | None, esperada: str | None) -> tuple[bool, str]:
    """Readiness: vivo, banco respondendo agora e esquema na última migração que a imagem conhece.

    A migração não é um passo separado (`AppState.__init__` migra na subida): se o banco ficou atrás, a subida
    falhou no meio; se ficou À FRENTE, esta imagem é mais velha que o banco — o caso do rollback de imagem sem
    rollback de esquema, que NÃO é seguro (docs/operacao.md, contêineres).
    """
    if not vivo(corpo):
        return False, "quem responde não é a Farm (ou ninguém responde)"
    dados: Any = json.loads(corpo or "{}")
    banco = dados.get("database") or {}
    if not banco.get("reachable"):
        return False, "banco não respondeu"
    duros = sorted({p.get("code") for p in dados.get("problems") or [] if p.get("code") in BLOQUEIAM_PRONTO})
    if duros:
        return False, "problema: " + ", ".join(duros)
    if esperada is None or dados.get("migration") != esperada:
        return False, f"migração do banco {dados.get('migration')!r}, a imagem espera {esperada!r}"
    return True, f"pronto (status {dados.get('status')}, migração {esperada})"


def main(argv: list[str]) -> int:
    corpo = ler()
    if "--pronto" in argv:
        ok, motivo = pronto(corpo, ultima_migracao())
    else:
        ok = vivo(corpo)
        motivo = "a Farm responde" if ok else "quem responde não é a Farm (ou ninguém responde)"
    # Uma linha, sem conteúdo do corpo: o `docker inspect` guarda a saída do healthcheck, e o corpo da saúde não
    # tem segredo — mas não há por que repeti-lo.
    print(("ok: " if ok else "falha: ") + motivo)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
