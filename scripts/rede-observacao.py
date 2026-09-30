"""Observação da rede por aparelho desde um instante (item 29.4, a janela do P16): só LEITURA do banco.

O que ela separa, por aparelho, desde `--desde` (UTC, ISO):
- **teste de vazamento** — o defeito do P16 era este se repetir depois de um reinício do backend: evento
  `network.updated` com `acao: vazamento` que NÃO é adoção (o cliente VPN foi parado), e o ensaio interrompido;
- **adoção** da prova anterior (a transição da migração 063): não para o cliente;
- **reinícios pedidos pela rede**, com o motivo: "teste de vazamento" é do P16; "túnel" (não subiu, caído) é do 29.3;
- comandos `device.network` por ação e estado, as medições, e a linha de cada aparelho (`leak_*`).

Uso (na raiz do repositório do central):
    python scripts/rede-observacao.py --desde 2026-09-30T16:00:00Z            # uma leitura
    python scripts/rede-observacao.py --desde … --a-cada 600 --ate 2026-09-30T22:00:00Z --saida data/rede/obs.md

Não escreve no banco, não chama a API, não toca em aparelho. A saída não tem segredo: o IP de saída sai mascarado.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
_IP = re.compile(r"\b(\d{1,3})\.(\d{1,3})\.\d{1,3}\.\d{1,3}\b")


def mascarar(texto: object) -> str:
    return _IP.sub(r"\1.\2.x.x", str(texto or ""))


def agora() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ler(banco: Path, desde: str) -> dict[str, object]:
    con = sqlite3.connect(f"file:{banco.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        linhas = [dict(r) for r in con.execute("SELECT * FROM device_network ORDER BY instance_id")]
        eventos = []
        for r in con.execute("SELECT ts AS created_at, instance_id, message, data FROM events"
                             " WHERE kind='network.updated' AND ts>=? ORDER BY id", (desde,)):
            try:
                dados = json.loads(r["data"] or "{}")
            except ValueError:
                dados = {}
            if dados.get("acao") == "vazamento":
                eventos.append({"quando": r["created_at"], "aparelho": r["instance_id"], "mensagem": r["message"],
                                "adotada": bool(dados.get("adotada")), "resultado": dados.get("leak_result")})
        reinicios = [dict(r) for r in con.execute(
            "SELECT id, instance_id, state, created_at, reason FROM commands WHERE verb='restart'"
            " AND requested_by='rede' AND created_at>=? ORDER BY created_at", (desde,))]
        comandos = [dict(r) for r in con.execute(
            "SELECT instance_id, state, params, reason, result, created_at FROM commands WHERE verb='device.network'"
            " AND created_at>=? ORDER BY created_at", (desde,))]
        medicoes = [dict(r) for r in con.execute(
            "SELECT id, instance_id, measured_at, udp_ok, leak_blocked, detail FROM network_measurements"
            " WHERE measured_at>=? ORDER BY id", (desde,))]
    finally:
        con.close()
    return {"linhas": linhas, "eventos": eventos, "reinicios": reinicios, "comandos": comandos, "medicoes": medicoes}


def classificar_reinicio(reinicio: dict[str, object], comandos: list[dict[str, object]]) -> str:
    """De onde veio o reinício pedido pela rede. O comando `restart` não guarda o motivo; quem o diz é o passo
    `device.network` do mesmo aparelho que veio logo antes: `verificar` que parou o cliente (desfecho com a chave
    `leak_blocked` e sem medição) é o teste de vazamento; `conectar` é o túnel que não subiu ou caiu; `aplicar` e
    `desfazer` são a própria aplicação."""
    antes = [c for c in comandos if c["instance_id"] == reinicio["instance_id"]
             and str(c["created_at"]) <= str(reinicio["created_at"])]
    if not antes:
        return "sem passo anterior na janela"
    ultimo = antes[-1]
    try:
        acao = json.loads(str(ultimo["params"] or "{}")).get("acao", "?")
        desfecho = (json.loads(str(ultimo["result"] or "{}")) or {}).get("outcome") or {}
    except ValueError:
        acao, desfecho = "?", {}
    if acao == "verificar":
        if "leak_blocked" in desfecho and not desfecho.get("measured"):
            return "teste de vazamento (P16)"
        return "túnel caído na verificação (29.3)"
    if acao == "conectar":
        return "túnel (29.3)"
    if acao in ("aplicar", "desfazer"):
        return "aplicação"
    return "outro"


def relatorio(dados: dict[str, object], desde: str) -> str:
    linhas, eventos, reinicios = dados["linhas"], dados["eventos"], dados["reinicios"]   # type: ignore[assignment]
    comandos, medicoes = dados["comandos"], dados["medicoes"]                            # type: ignore[assignment]
    out = [f"## Leitura de {agora()} (desde {desde})", ""]
    out += ["| Aparelho | Estado | Verificado em | Prova (rev, resultado, quando) | Testes de vazamento | Adoções | "
            "Reinícios pela rede | Medições (com bloqueio provado) | `device.network` |",
            "|---|---|---|---|---|---|---|---|---|"]
    for ln in linhas:                                                                    # type: ignore[union-attr]
        iid = ln["instance_id"]
        ev = [e for e in eventos if e["aparelho"] == iid]                                # type: ignore[union-attr]
        testes = [e for e in ev if not e["adotada"]]
        adocoes = [e for e in ev if e["adotada"]]
        rs = [r for r in reinicios if r["instance_id"] == iid]                           # type: ignore[union-attr]
        por_tipo: dict[str, int] = {}
        for r in rs:
            tipo = classificar_reinicio(r, comandos)                                     # type: ignore[arg-type]
            por_tipo[tipo] = por_tipo.get(tipo, 0) + 1
        ms = [m for m in medicoes if m["instance_id"] == iid]                            # type: ignore[union-attr]
        cs = [c for c in comandos if c["instance_id"] == iid]                            # type: ignore[union-attr]
        acoes: dict[str, int] = {}
        for c in cs:
            try:
                acao = json.loads(c["params"] or "{}").get("acao", "?")
            except ValueError:
                acao = "?"
            chave = acao if c["state"] == "succeeded" else f"{acao}:{c['state']}"
            acoes[chave] = acoes.get(chave, 0) + 1
        prova = (f"rev {ln['leak_rev']}, {'pendente' if ln['leak_pending'] else ln['leak_result']}, {ln['leak_at']}"
                 if ln["leak_rev"] is not None or ln["leak_pending"] else "—")
        out.append(f"| {iid} | {ln['state']} | {ln['verified_at'] or '—'} | {prova} | {len(testes)} | {len(adocoes)} | "
                   f"{', '.join(f'{n}× {t}' for t, n in por_tipo.items()) or '0'} | "
                   f"{len(ms)} ({sum(1 for m in ms if m['leak_blocked'] == 1)}) | "
                   f"{', '.join(f'{k} {v}' for k, v in sorted(acoes.items())) or '—'} |")
    out.append("")
    for e in eventos:                                                                    # type: ignore[union-attr]
        out.append(f"- {e['quando']} {e['aparelho']}: {'ADOÇÃO' if e['adotada'] else 'TESTE'} — {mascarar(e['mensagem'])}")
    for r in reinicios:                                                                  # type: ignore[union-attr]
        out.append(f"- {r['created_at']} {r['instance_id']}: reinício `{r['id']}` ({r['state']}) — "
                   f"{classificar_reinicio(r, comandos)}")                                # type: ignore[arg-type]
    for c in comandos:                                                                   # type: ignore[union-attr]
        if c["state"] not in ("succeeded",):
            out.append(f"- {c['created_at']} {c['instance_id']}: `device.network` {c['state']} — "
                       f"{mascarar(c['reason'])[:200]}")
    for ln in linhas:                                                                    # type: ignore[union-attr]
        if ln["leak_detail"]:
            out.append(f"- prova de {ln['instance_id']}: {mascarar(ln['leak_detail'])[:220]}")
    out.append("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--desde", required=True, help="Instante inicial, UTC, ISO (ex.: 2026-09-30T16:00:00Z).")
    p.add_argument("--banco", default=str(RAIZ / "data" / "poc.sqlite3"))
    p.add_argument("--saida", help="Arquivo em que cada leitura é ACRESCENTADA (senão, só a tela).")
    p.add_argument("--a-cada", type=int, default=0, help="Segundos entre leituras; 0 = uma leitura só.")
    p.add_argument("--ate", help="Instante em que o laço para, UTC, ISO.")
    a = p.parse_args(argv)
    while True:
        texto = relatorio(ler(Path(a.banco), a.desde), a.desde)
        if a.saida:
            destino = Path(a.saida)
            destino.parent.mkdir(parents=True, exist_ok=True)
            with destino.open("a", encoding="utf-8") as f:
                f.write(texto + "\n")
        else:
            sys.stdout.write(texto + "\n")
        if not a.a_cada or (a.ate and agora() >= a.ate):
            return 0
        time.sleep(a.a_cada)


if __name__ == "__main__":
    raise SystemExit(main())
