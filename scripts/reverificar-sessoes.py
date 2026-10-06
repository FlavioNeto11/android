"""Reverifica as sessões de contas reais pela API do central, só observando: um comando por aparelho.

Para cada alvo `aparelho:perfil:conta` (ids, nunca o @), chama
`POST /api/instagram/profiles/{perfil}/accounts/{conta}/session/verify?instance_id=<aparelho>` (relê da tela qual conta
está aberta, sem digitar senha nem tocar em credencial) e acompanha o comando até o desfecho em `GET /api/commands/{id}`.
Sem adb direto: o aparelho é tocado só pela verificação da plataforma, que é de leitura.

Regras de mundo real (CLAUDE.md, `.claude/rules/segredos-e-mundo-real.md`):
- no máximo `--tentativas` (padrão 2) por aparelho, e só repete a que falhou;
- o motivo sai com o @ trocado por `@***` (o motivo pode citar a conta que a tela mostrou);
- um motivo com "confirm you're human" ou "human" para o aparelho na hora: a tela é de conta bloqueada e nada a toca.

Uso, da raiz do checkout, no central:
    backend/.venv/Scripts/python.exe scripts/reverificar-sessoes.py android-01:ig-…:acc-… android-03:ig-…:acc-… \
        [--api http://127.0.0.1:8000/api] [--tentativas 2] [--prazo-s 240] [--json saida.json]
Saída: uma linha por aparelho no stdout e, com `--json`, a lista completa (só ids, estados, horas e motivos redigidos).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

_ARROBA = re.compile(r"@[\w.]+")
_HUMANO = re.compile(r"confirm.{0,10}human|human", re.I)
_FINAIS = {"succeeded", "failed", "rejected", "cancelled", "timed_out", "unknown"}


def _agora() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _redigir(texto: object) -> str | None:
    if texto is None:
        return None
    return _ARROBA.sub("@***", " ".join(str(texto).split()))[:300]


def _pedir(metodo: str, url: str, timeout: float = 30.0) -> tuple[int, dict[str, object]]:
    req = urllib.request.Request(url, method=metodo, data=b"" if metodo == "POST" else None)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - loopback do central
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            corpo = json.loads(exc.read() or b"{}")
        except ValueError:
            corpo = {}
        return exc.code, corpo if isinstance(corpo, dict) else {}


def _uma(api: str, aparelho: str, perfil: str, conta: str, prazo_s: float) -> dict[str, object]:
    status, corpo = _pedir("POST", f"{api}/instagram/profiles/{perfil}/accounts/{conta}/session/verify"
                                   f"?instance_id={aparelho}")
    saida: dict[str, object] = {"aparelho": aparelho, "perfil": perfil, "conta": conta, "pedido_em": _agora(),
                                "http": status}
    cid = corpo.get("command_id")
    if status != 202 or not cid or corpo.get("accepted") is False:
        detalhe = corpo.get("detail")
        saida.update(estado="recusado", motivo=_redigir(detalhe if detalhe is not None else corpo.get("state")))
        return saida
    saida["command_id"] = cid
    fim = time.monotonic() + prazo_s
    estado, motivo = "pending", None
    while time.monotonic() < fim:
        time.sleep(5)
        st, cmd = _pedir("GET", f"{api}/commands/{cid}")
        if st != 200:
            continue
        estado, motivo = str(cmd.get("state")), cmd.get("reason")
        if estado in _FINAIS:
            saida["terminou_em"] = cmd.get("finished_at")
            break
    saida.update(estado=estado if estado in _FINAIS else "sem_desfecho_no_prazo", motivo=_redigir(motivo))
    return saida


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=(__doc__ or "").split("\n", 1)[0])
    p.add_argument("alvos", nargs="+", help="aparelho:perfil:conta (ids)")
    p.add_argument("--api", default="http://127.0.0.1:8000/api")
    p.add_argument("--tentativas", type=int, default=2)
    p.add_argument("--prazo-s", type=float, default=240.0)
    p.add_argument("--json")
    a = p.parse_args(argv)
    resultados: list[dict[str, object]] = []
    for alvo in a.alvos:
        partes = alvo.split(":")
        if len(partes) != 3 or not all(partes):
            print(f"alvo inválido (esperado aparelho:perfil:conta): {_redigir(alvo)}", file=sys.stderr)
            return 2
        aparelho, perfil, conta = partes
        for tentativa in range(1, max(1, min(a.tentativas, 2)) + 1):
            r = _uma(a.api, aparelho, perfil, conta, a.prazo_s)
            r["tentativa"] = tentativa
            resultados.append(r)
            print(f"{aparelho} tentativa {tentativa}: {r['estado']} {r.get('command_id', '')} {r.get('motivo') or ''}")
            if r["estado"] == "succeeded" or _HUMANO.search(str(r.get("motivo") or "")):
                break   # comprovada, ou tela de conta bloqueada: não toca de novo
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(resultados, f, ensure_ascii=False, indent=1)
    ultimo = {str(r["aparelho"]): r["estado"] for r in resultados}   # a última tentativa de cada aparelho
    return 0 if all(e == "succeeded" for e in ultimo.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
