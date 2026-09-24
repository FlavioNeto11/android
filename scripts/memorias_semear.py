"""Semeia a memória de cada perfil a partir do PRÓPRIO histórico social dele — sem IA e sem inventar nada.

Cada interação CONFIRMADA (`social_interactions`, status `confirmed`) vira um fato sobre a contraparte: "mandei
DM para @x em 19/09 dizendo …", "segui @x", "respondi o comentário de @x". É o que o perfil já viveu; a memória
só passa a lembrar. Idempotente: um fato com o mesmo assunto e o mesmo texto não é gravado duas vezes.

Nada aqui lê ou grava credencial; texto que parece segredo é recusado pelo próprio serviço de memória.

Uso
---
    python scripts/memorias_semear.py            # mostra o que seria gravado
    python scripts/memorias_semear.py --aplicar  # grava
"""
from __future__ import annotations

import argparse
import json
import urllib.request

VERBOS = {
    "dm_sent": "mandei DM para", "dm_received": "recebi DM de", "comment_replied": "comentei para",
    "comment_liked": "curti o comentário", "post_liked": "curti a publicação", "post_unliked": "descurti a publicação",
    "followed": "passei a seguir", "unfollowed": "deixei de seguir",
    "follow_request_accepted": "aceitei o pedido para seguir", "follow_request_declined": "recusei o pedido para seguir",
}
IMPORTANCIA = {"dm_sent": 0.7, "dm_received": 0.8, "comment_replied": 0.6, "followed": 0.5}


def pedir(base: str, metodo: str, caminho: str, corpo: dict | None = None):
    req = urllib.request.Request(base + caminho, method=metodo, headers={"Content-Type": "application/json"},
                                 data=json.dumps(corpo).encode() if corpo is not None else None)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r) if r.status != 204 else None


def fato(i: dict) -> tuple[str, str, float] | None:
    alvo = (i.get("counterparty") or i.get("target") or "").strip()
    if not alvo or i.get("status") != "confirmed":
        return None
    assunto = alvo if alvo.startswith("@") else f"@{alvo}"
    quando = (i.get("occurred_at") or "")[:10]
    dia = f"{quando[8:10]}/{quando[5:7]}" if len(quando) == 10 else "data desconhecida"
    verbo = VERBOS.get(i.get("type") or "", (i.get("type") or "interagi").replace("_", " "))
    texto = f"Em {dia}, {verbo} {assunto}."
    enviado = (i.get("outgoing_content") or "").strip()
    recebido = (i.get("incoming_content") or "").strip()
    if recebido:
        texto += f" Ele/ela disse: “{recebido[:240]}”."
    if enviado:
        texto += f" Eu escrevi: “{enviado[:240]}”."
    return assunto, texto, IMPORTANCIA.get(i.get("type") or "", 0.4)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default="http://127.0.0.1:8000/api")
    ap.add_argument("--aplicar", action="store_true")
    a = ap.parse_args()
    total = 0
    for p in pedir(a.base, "GET", "/instagram/profiles"):
        pid, usuario = p["id"], p["username"]
        ja = {(m["subject"].lower(), m["content"]) for m in pedir(a.base, "GET", f"/instagram/profiles/{pid}/memory?limit=500")}
        novos = []
        for i in pedir(a.base, "GET", f"/instagram/profiles/{pid}/interactions"):
            f = fato(i)
            if f and (f[0].lower(), f[1]) not in ja:
                novos.append(f)
                ja.add((f[0].lower(), f[1]))
        print(f"@{usuario}: {len(novos)} fato(s) novo(s)")
        for assunto, texto, imp in novos:
            print(f"   {assunto}: {texto[:110]}")
            if a.aplicar:
                pedir(a.base, "POST", f"/instagram/profiles/{pid}/memory",
                      {"subject": assunto, "content": texto, "importance": imp, "confidence": 1.0})
        total += len(novos)
    print(f"\n{total} fato(s) {'gravado(s)' if a.aplicar else 'a gravar — rode com --aplicar'}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
