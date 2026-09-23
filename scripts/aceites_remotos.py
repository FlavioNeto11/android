"""Item T.1 (aceites 1, 6 e 8): roteiro de ciclo de vida num aparelho LOCAL e num REMOTO, pela MESMA rota do
painel, colhendo os `command_id` e os tempos para a tabela dos nove aceites (docs/relatorio-validacao.md §13).

O que ele faz: para cada aparelho, despacha `stop → start → hibernate → wake → restart` por
`POST /api/instances/{id}/actions/{verbo}`, espera cada comando chegar a um desfecho por
`GET /api/commands/{id}` e imprime a tabela pronta para colar no relatório. Nada mais: não fala com o agente,
não mexe em arquivo do parque, não instala nada.

DUAS TRAVAS, de propósito:
  * sem `--yes` ele NÃO despacha nada — só imprime o roteiro que despacharia;
  * `reset` (que apaga os dados do aparelho) só entra com `--com-reset`, e nunca no roteiro padrão.

Uso:  backend\\.venv\\Scripts\\python.exe scripts\\aceites_remotos.py --local android-01 --remoto android-13 --yes
Saída: tabela em Markdown no stdout + `data/aceites-remotos.json` com o retorno bruto de cada comando.

O token da API (quando houver) vem de `API_TOKEN` no ambiente e NUNCA é impresso — nem na saída, nem no erro.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.commands.states import COMMAND_TERMINAL, COMMAND_UNSETTLED  # noqa: E402

#: O ciclo de vida do aceite 1, na ordem em que um aparelho aguenta: parar, ligar, hibernar, acordar, reiniciar.
#: `hibernate` e `wake` andam colados — acordar sem o snapshot que o hibernar acabou de salvar é recusa, não prova.
VERBOS_PADRAO: tuple[str, ...] = ("stop", "start", "hibernate", "wake", "restart")
#: Fora do roteiro padrão: apaga os dados do aparelho. Só com `--com-reset`, e sempre por último.
VERBO_DESTRUTIVO = "reset"

#: Prazo de espera do desfecho, por verbo (segundos). Generoso de propósito: o boot medido no parque foi de
#: 104–192 s em 19/09 e o prazo do próprio agente é de 480 s (docs/relatorio-validacao.md §13).
PRAZO_POR_VERBO: dict[str, int] = {
    "stop": 180, "start": 660, "hibernate": 300, "wake": 240, "restart": 780, "reset": 900,
}
PRAZO_PADRAO = 300

#: Estados em que parar de esperar. `uncertain` NÃO é terminal para o sistema (alguém ainda decide), mas é
#: desfecho para este roteiro: o agente já desistiu, e é exatamente o caso que o aceite 8 quer ver registrado.
DESFECHOS = {s.value for s in COMMAND_TERMINAL} | {s.value for s in COMMAND_UNSETTLED}


def roteiro(com_reset: bool = False) -> tuple[str, ...]:
    """Os verbos, na ordem. `reset` só entra quando pedido, e sempre no fim."""
    return VERBOS_PADRAO + ((VERBO_DESTRUTIVO,) if com_reset else ())


def plano(local: str, remoto: str, com_reset: bool = False) -> list[tuple[str, str]]:
    """`(aparelho, verbo)` na ordem de execução: o roteiro inteiro no local, depois o inteiro no remoto.

    Aparelho por aparelho, e não verbo por verbo, por dois motivos: `hibernate`/`wake` só fazem sentido
    colados, e dois comandos no mesmo aparelho são recusados com `device_busy` (é o aceite 9).
    """
    passos: list[tuple[str, str]] = []
    for aparelho in (local, remoto):
        if not aparelho:
            continue
        passos.extend((aparelho, verbo) for verbo in roteiro(com_reset))
    return passos


def url_da_acao(base: str, instance_id: str, verbo: str) -> str:
    return (f"{base.rstrip('/')}/api/instances/{urllib.parse.quote(instance_id, safe='')}"
            f"/actions/{urllib.parse.quote(verbo, safe='')}")


def url_do_comando(base: str, command_id: str) -> str:
    return f"{base.rstrip('/')}/api/commands/{urllib.parse.quote(command_id, safe='')}"


def cabecalhos(token: str | None) -> dict[str, str]:
    """Cabeçalhos do pedido. Sem token, nenhum `Authorization` — o loopback não exige credencial."""
    cab = {"Content-Type": "application/json"}
    if token:
        cab["Authorization"] = f"Bearer {token}"
    return cab


def terminou(estado: str | None) -> bool:
    """Parar de esperar? Só com um desfecho de verdade — `dispatched`/`running` não são desfecho."""
    return bool(estado) and estado in DESFECHOS


def prazo_do_verbo(verbo: str) -> int:
    return PRAZO_POR_VERBO.get(verbo, PRAZO_PADRAO)


def _celula(texto: Any) -> str:
    """Texto do modelo/backend nunca quebra a tabela: `|` vira `\\|` e a quebra de linha vira espaço."""
    if texto is None:
        return "—"
    return str(texto).replace("|", "\\|").replace("\r", " ").replace("\n", " ").strip() or "—"


def tabela(resultados: list[dict[str, Any]]) -> str:
    """A tabela em Markdown, uma linha por comando — para colar no relatório."""
    linhas = ["| aparelho | verbo | command_id | estado | s | motivo |",
              "|---|---|---|---|---|---|"]
    for r in resultados:
        segundos = r.get("segundos")
        linhas.append("| {} | `{}` | `{}` | **{}** | {} | {} |".format(
            _celula(r.get("instance_id")), _celula(r.get("verbo")), _celula(r.get("command_id")),
            _celula(r.get("state")), "—" if segundos is None else f"{float(segundos):.0f}",
            _celula(r.get("reason"))))
    return "\n".join(linhas)


def resumo(resultados: list[dict[str, Any]]) -> str:
    """Uma linha por aparelho, no formato da coluna *Real* da tabela dos aceites."""
    por_aparelho: dict[str, list[str]] = {}
    for r in resultados:
        estado = str(r.get("state") or "sem desfecho")
        por_aparelho.setdefault(str(r.get("instance_id")), []).append(
            f"`{r.get('verbo')}` `{r.get('command_id') or '—'}` ({estado})")
    return "\n".join(f"- **{aparelho}**: " + "; ".join(itens) for aparelho, itens in por_aparelho.items())


def texto_do_roteiro(local: str, remoto: str, com_reset: bool, base: str) -> str:
    """O que o `--yes` faria. É isto, e só isto, que sai quando ele não é passado."""
    passos = plano(local, remoto, com_reset)
    linhas = [f"Roteiro dos aceites 1/6/8 contra {base} — {len(passos)} comandos, nesta ordem:"]
    linhas += [f"  {i:>2}. POST /api/instances/{aparelho}/actions/{verbo}   (espera até {prazo_do_verbo(verbo)} s)"
               for i, (aparelho, verbo) in enumerate(passos, 1)]
    if com_reset:
        linhas.append("  ATENÇÃO: `reset` APAGA os dados do aparelho (sessões e apps instalados).")
    linhas.append("NADA foi despachado: repita com --yes para executar de verdade.")
    return "\n".join(linhas)


# --------------------------------------------------------------------------------------------------- HTTP


def _pedir(url: str, token: str | None, metodo: str = "GET", timeout: float = 30.0) -> dict[str, Any]:
    req = urllib.request.Request(url, method=metodo, headers=cabecalhos(token),
                                 data=b"{}" if metodo == "POST" else None)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - URL é do próprio painel
            corpo = resp.read().decode("utf-8") or "{}"
    except urllib.error.HTTPError as e:  # o corpo do erro do painel explica a recusa; o token não aparece nele
        return {"_erro": f"HTTP {e.code}", "_detalhe": (e.read().decode("utf-8", "replace") or "")[:300]}
    except OSError as e:
        return {"_erro": type(e).__name__, "_detalhe": str(e)[:300]}
    try:
        return json.loads(corpo)
    except json.JSONDecodeError:
        return {"_erro": "resposta não é JSON", "_detalhe": corpo[:300]}


def executar(base: str, local: str, remoto: str, com_reset: bool, token: str | None,
             pedir: Callable[..., dict[str, Any]] = _pedir, dormir: Callable[[float], None] = time.sleep,
             agora: Callable[[], float] = time.monotonic) -> list[dict[str, Any]]:
    """Despacha o roteiro e espera cada desfecho. `pedir`/`dormir`/`agora` são injetáveis para teste."""
    resultados: list[dict[str, Any]] = []
    for aparelho, verbo in plano(local, remoto, com_reset):
        t0 = agora()
        aceite = pedir(url_da_acao(base, aparelho, verbo), token, "POST")
        registro: dict[str, Any] = {"instance_id": aparelho, "verbo": verbo,
                                    "command_id": aceite.get("command_id"), "state": aceite.get("state"),
                                    "reason": aceite.get("reason") or aceite.get("_detalhe"),
                                    "deduplicated": aceite.get("deduplicated")}
        if registro["command_id"]:
            prazo = prazo_do_verbo(verbo)
            while not terminou(registro.get("state")) and agora() - t0 < prazo:
                dormir(3.0)
                atual = pedir(url_do_comando(base, str(registro["command_id"])), token, "GET")
                if "_erro" in atual:
                    # Backend fora do ar no meio do roteiro não pode virar uma linha muda dizendo `dispatched`
                    # por 660 s: quem lê a tabela precisa saber que foi a CONSULTA que falhou, não o aparelho.
                    registro["reason"] = f"{atual['_erro']}: {atual.get('_detalhe', '')}".strip(": ")
                    continue
                registro["state"] = atual.get("state", registro.get("state"))
                registro["reason"] = atual.get("reason") or registro.get("reason")
        registro["segundos"] = agora() - t0
        resultados.append(registro)
        print(f"  {aparelho} {verbo}: {registro.get('state')} "
              f"({registro.get('command_id') or registro.get('reason')})", flush=True)
    return resultados


def main(argv: list[str] | None = None, executor: Callable[..., list[dict[str, Any]]] = executar) -> int:
    p = argparse.ArgumentParser(description="Roteiro de ciclo de vida local × remoto para a tabela dos aceites.")
    p.add_argument("--base", default="http://127.0.0.1:8000", help="painel (padrão: http://127.0.0.1:8000)")
    p.add_argument("--local", required=True, help="aparelho DESTA máquina, ex.: android-01")
    p.add_argument("--remoto", required=True, help="aparelho de um worker, ex.: android-13")
    p.add_argument("--com-reset", action="store_true", help="inclui `reset` — APAGA os dados do aparelho")
    p.add_argument("--saida", default=str(ROOT / "data" / "aceites-remotos.json"))
    p.add_argument("--yes", action="store_true", help="sem isto, nada é despachado")
    a = p.parse_args(argv)

    if not a.yes:
        print(texto_do_roteiro(a.local, a.remoto, a.com_reset, a.base))
        return 0

    resultados = executor(a.base, a.local, a.remoto, a.com_reset, os.environ.get("API_TOKEN") or None)
    destino = Path(a.saida)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps(resultados, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n" + tabela(resultados) + "\n\n" + resumo(resultados))
    print(f"\nBruto em {destino}")
    return 0 if all(r.get("state") == "succeeded" for r in resultados) else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
