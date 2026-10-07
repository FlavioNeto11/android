"""29.154, fatia 3 (ADR-079): cliente de linha de comando do comando remoto dos workers. SEM IA.

`python scripts/worker-comando.py --worker worker-lan-01 --linha "Get-Date"` pede ao central que o agente execute UMA linha
(ou um `--argv-json '["cmd","arg"]'`) na máquina do worker, espera o resultado e imprime a saída exatamente como a central a
devolveu (já REDIGIDA lá; este cliente não redige nem reinterpreta nada). Só fala com as rotas `/api/workers/{id}/comandos`.

A sessão de operador (cookie `parque_sessao`) vem da VARIÁVEL DE AMBIENTE `CENTRAL_SESSAO`, nunca da linha de comando (a linha
de comando fica no histórico do shell e na lista de processos). Sem ela o central responde 401 e o script nem tenta: o
comando remoto exige sessão NOMEADA (o token compartilhado e o loopback sem sessão não valem).

Códigos de saída: o próprio código do comando quando ele terminou (`succeeded` ou `failed` com `exit_code`); 2 = o central ou
o agente RECUSOU ou perdeu o fio (`rejected`, `uncertain`, `cancelled`, erro 4xx do pedido); 3 = prazo (`timed_out`, ou a
espera local acabou sem estado final: o comando pode ter rodado, confira pelo id impresso); 4 = sem sessão, sem permissão ou
rota inexistente; 5 = central fora do ar. `uncertain` NUNCA é repetido às cegas: o script imprime o id e não reenvia.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Sequence

COOKIE = "parque_sessao"
FINAIS = ("succeeded", "failed", "timed_out", "cancelled", "uncertain", "rejected")
BASE_PADRAO = "http://127.0.0.1:8000"

# (metodo, url, corpo|None, cookie, origem) -> (status HTTP, corpo JSON ou {}). Injetável para o teste.
Transporte = Callable[[str, str, dict | None, str, str], tuple[int, dict]]


def transporte_http(metodo: str, url: str, corpo: dict | None, cookie: str, origem: str) -> tuple[int, dict]:
    dados = json.dumps(corpo).encode("utf-8") if corpo is not None else None
    req = urllib.request.Request(url, data=dados, method=metodo, headers={
        "Cookie": f"{COOKIE}={cookie}", "Origin": origem, "Accept": "application/json",
        **({"Content-Type": "application/json"} if dados is not None else {})})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, _json(r.read())
    except urllib.error.HTTPError as exc:
        return exc.code, _json(exc.read())


def _json(bruto: bytes) -> dict:
    try:
        valor = json.loads(bruto.decode("utf-8", errors="replace") or "{}")
    except ValueError:
        return {}
    return valor if isinstance(valor, dict) else {}


def _motivo(corpo: dict) -> str:
    d = corpo.get("detail", corpo)
    if isinstance(d, dict):
        return f"{d.get('code', '?')}: {d.get('message', '')}".strip()
    return str(d)[:300]


def _corpo_do_pedido(a: argparse.Namespace) -> dict:
    corpo: dict = {"idempotency_key": a.chave or f"cli-{uuid.uuid4().hex[:20]}"}
    if a.linha is not None:
        corpo["linha"] = a.linha
    else:
        argv = json.loads(a.argv_json)
        if not isinstance(argv, list) or not argv or not all(isinstance(x, str) for x in argv):
            raise ValueError("--argv-json precisa ser uma lista JSON de strings, não vazia")
        corpo["argv"] = argv
    if a.pasta:
        corpo["pasta"] = a.pasta
    if a.timeout is not None:
        corpo["timeout_s"] = a.timeout
    return corpo


def _codigo_final(r: dict) -> int:
    estado = r.get("state")
    if estado in ("succeeded", "failed"):
        codigo = r.get("exit_code")
        return int(codigo) if isinstance(codigo, int) else (0 if estado == "succeeded" else 1)
    return 3 if estado == "timed_out" else 2


def executar(a: argparse.Namespace, cookie: str, *, transporte: Transporte = transporte_http,
             dormir: Callable[[float], None] = time.sleep, agora: Callable[[], float] = time.monotonic,
             saida=None, erro=None) -> int:
    out, err = saida or sys.stdout, erro or sys.stderr
    base = a.base.rstrip("/")
    raiz = f"{base}/api/workers/{a.worker}/comandos"
    try:
        corpo = _corpo_do_pedido(a)
        status, resp = transporte("POST", raiz, corpo, cookie, base)
    except ValueError as exc:
        print(f"pedido inválido: {exc}", file=err)
        return 2
    except (urllib.error.URLError, OSError) as exc:
        print(f"central fora do ar ({type(exc).__name__}).", file=err)
        return 5
    if status in (401, 403, 404):
        print(f"recusado ({status}): {_motivo(resp)}", file=err)
        return 4
    if status != 202:
        print(f"recusado ({status}): {_motivo(resp)}", file=err)
        return 2
    cid = str(resp.get("id") or "")
    if not cid:
        print("a central aceitou mas não devolveu o id do comando.", file=err)
        return 2
    print(f"comando {cid} aceito (chave {corpo['idempotency_key']}).", file=err)
    limite = agora() + (a.espera if a.espera is not None else (a.timeout or 60.0) + 30.0)
    atual = resp
    while atual.get("state") not in FINAIS:
        if agora() >= limite:
            print(f"a espera local acabou com o comando em '{atual.get('state')}': ele PODE ter rodado; confira o id {cid} "
                  f"no histórico. Não reenvie às cegas.", file=err)
            return 3
        dormir(a.intervalo)
        try:
            status, atual = transporte("GET", f"{raiz}/{cid}", None, cookie, base)
        except (urllib.error.URLError, OSError) as exc:
            print(f"central fora do ar ao consultar o comando {cid} ({type(exc).__name__}).", file=err)
            return 5
        if status != 200:
            print(f"consulta recusada ({status}): {_motivo(atual)}", file=err)
            return 4 if status in (401, 403, 404) else 2
    out.write(atual.get("stdout") or "")
    if atual.get("stderr"):
        err.write(atual["stderr"])
    if atual.get("truncated"):
        print("(saída cortada pela central)", file=err)
    print(f"estado {atual.get('state')}, código {atual.get('exit_code')}, {atual.get('duration_ms')} ms"
          + (f", motivo: {atual['reason']}" if atual.get("reason") else "") + f" (id {cid}).", file=err)
    return _codigo_final(atual)


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Executa UMA linha na máquina de um worker pelo comando remoto (29.154).")
    ap.add_argument("--worker", required=True, help="id do worker (ex.: worker-lan-01)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--linha", help="a linha de comando, interpretada pelo shell do agente")
    g.add_argument("--argv-json", help='lista JSON, sem shell: \'["cmd","arg"]\'')
    ap.add_argument("--pasta", help="pasta de trabalho no worker")
    ap.add_argument("--timeout", type=float, help="prazo do comando em segundos (padrão do central 60, máximo 600)")
    ap.add_argument("--chave", help="idempotency_key (mínimo 8 caracteres); a mesma chave devolve o mesmo registro")
    ap.add_argument("--base", default=os.environ.get("CENTRAL_URL", BASE_PADRAO))
    ap.add_argument("--espera", type=float, help="quanto esperar o estado final, em segundos (padrão: prazo + 30)")
    ap.add_argument("--intervalo", type=float, default=2.0, help="intervalo entre consultas, em segundos")
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    a = _parser().parse_args(argv)
    cookie = os.environ.get("CENTRAL_SESSAO", "").strip()
    if not cookie:
        print("sem sessão de operador: ponha o valor do cookie `parque_sessao` na variável CENTRAL_SESSAO "
              "(nunca na linha de comando).", file=sys.stderr)
        return 4
    return executar(a, cookie)


if __name__ == "__main__":
    sys.exit(main())
