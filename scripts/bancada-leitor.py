"""Bancada do leitor da leitura visual (item 12.5, ADR-070): o portão para ligar `ai.leitura_visual.enabled`.

O provedor é real, as capturas são guardadas e o gabarito vem do lado de quem enviou cada e-mail de teste. Usa o
MESMO código da produção:
- `saidas.recortar`, sem margem e com o teto de linha;
- a triagem do recorte;
- `conferir_transcricao` (NFKC, caixa, pontas, palavras inteiras contíguas);
- o hub (`RoutingProvider.transcribe`).

Cada chamada pede UMA saída, como `ler_valor_visual` faz. O leitor nunca recebe o valor do ator: a conferência é local.

Por recorte e por campo (remetente, assunto), a transcrição única é conferida contra quatro valores do "ator":
- verdadeiro: o gabarito, e tem de concordar;
- linha_errada: o mesmo campo de OUTRO e-mail de teste (nunca uma linha de terceiro), e tem de recusar;
- letra_trocada: o gabarito com uma letra trocada, e tem de recusar;
- campos_invertidos: o outro campo do mesmo e-mail, e tem de recusar.

Critério da decisão do 12.5 (§2.9): N ≥ 30 pares, ZERO concordância falsa e ≥ 90 % de concordância nos verdadeiros.

O gabarito e o resultado têm dado pessoal (nomes e assuntos de e-mail) e ficam em `data/`, fora do Git. Cada
chamada vira uma linha em `ai_calls` (`role='leitura'`, `origem='leitura'`, `ref='bancada-12.5'`), que
`/api/usage` mostra. O custo é estimado pelos preços de `ai.prices`, e a bancada para antes de passar do teto.

Uso, a partir da raiz:
  backend/.venv/Scripts/python.exe scripts/bancada-leitor.py --ensaio          só recorta e grava a folha dos recortes
  backend/.venv/Scripts/python.exe scripts/bancada-leitor.py --leitor openai   chamada paga
  backend/.venv/Scripts/python.exe scripts/bancada-leitor.py --leitor gemini --modelo gemini-3.1-flash-lite
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "backend"))

from app.config import load_config  # noqa: E402
from app.db import Database  # noqa: E402
from app.events import EventBus  # noqa: E402
from app.planning.provider import LeituraRequest  # noqa: E402
from app.planning.routing import RoutingProvider  # noqa: E402
from app.taskqueue.repository import Repository  # noqa: E402
from app.taskqueue.saidas import (LeituraVisualRecusada, _triagem_do_recorte, conferir_transcricao,  # noqa: E402
                                  forma_de_codigo, limpar, recortar, triagem)

CAMPOS = ("remetente", "assunto")
REF = "bancada-12.5"


def agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def letra_trocada(valor: str) -> str:
    """Troca UMA letra no meio da palavra mais longa (determinístico): "Padilha" → "Padilia"."""
    palavras = valor.split(" ")
    i = max(range(len(palavras)), key=lambda k: len(palavras[k]))
    p = palavras[i]
    j = len(p) // 2
    nova = "a" if p[j].lower() != "a" else "e"
    palavras[i] = p[:j] + nova + p[j + 1:]
    return " ".join(palavras)


def valores_do_ator(item: dict, campo: str, itens: list[dict]) -> list[tuple[str, str, bool]]:
    """(tipo, valor, deve_concordar) para um campo de um recorte."""
    certo = item["campos"][campo]
    outro_email = next((x["campos"][campo] for x in itens
                        if x["email"] != item["email"] and x["campos"][campo] != certo), None)
    outro_campo = item["campos"][next(c for c in CAMPOS if c != campo)]
    saida = [("verdadeiro", certo, True), ("letra_trocada", letra_trocada(certo), False),
             ("campos_invertidos", outro_campo, False)]
    if outro_email is not None:
        saida.insert(1, ("linha_errada", outro_email, False))
    return saida


def conferir(valor: str, campo: str, t) -> str:
    """O mesmo caminho de `ler_valor_visual` depois da transcrição: triagem do recorte, a concordância e a triagem do
    valor do leitor (com a forma de código)."""
    texto = " ".join(t.linhas)
    motivo = _triagem_do_recorte(t.linhas, texto)
    if motivo is not None:
        return f"triagem:{motivo}"
    try:
        conferir_transcricao(limpar(valor), campo, t)
    except LeituraVisualRecusada as exc:
        return exc.codigo
    lido = limpar(t.campos[campo] or "")
    motivo = triagem(lido, do_elemento=texto, da_tela=texto)
    if motivo is None and forma_de_codigo(lido):
        motivo = "código de verificação"
    return f"triagem:{motivo}" if motivo else "concorda"


def conferir_sem_truncado_alheio(valor: str, campo: str, t) -> str:
    """SÓ DIAGNÓSTICO, não é o critério: a mesma conferência ignorando o "truncado" que vem de OUTRA linha (a prévia
    cortada da linha da caixa), com o campo e a linha que contém o valor ainda exigidos inteiros."""
    linhas = [x for x in t.linhas if not x.rstrip().endswith(("…", "..."))]
    sem = t.model_copy(update={"linhas": linhas, "truncado": False})
    return conferir(valor, campo, sem)


def custo(cfg, usage) -> float:
    precos = cfg.file.ai.prices.get(usage.model)
    if not precos:
        raise SystemExit(f"modelo sem preço em ai.prices: {usage.model} (a bancada não roda sem teto conferível)")
    entrada, lido, gravado, saida = precos
    fresco = max(0, usage.input_tokens - usage.cache_read_tokens - usage.cache_write_tokens)
    return (fresco * entrada + usage.cache_read_tokens * lido + usage.cache_write_tokens * gravado
            + usage.output_tokens * saida) / 1_000_000


def config_do_leitor(leitor: str, modelo: str | None):
    """A config do central; para outro leitor que o de `ai.roles.leitura`, uma cópia temporária só com essa linha trocada
    (o config do central não muda)."""
    base = RAIZ / "config" / "config.yaml"
    cfg = load_config(base)
    papel = cfg.ai_leitura()
    if papel is not None and papel.provider == leitor and (modelo is None or modelo == papel.model):
        return cfg
    if modelo is None:
        raise SystemExit("--modelo é obrigatório para um leitor diferente do configurado em ai.roles.leitura")
    texto = base.read_text(encoding="utf-8")
    novo, n = re.subn(r"(?m)^    leitura: \{[^}\n]*\}", f"    leitura: {{provider: {leitor}, model: {modelo}, timeout_s: 30}}",
                      texto)
    if n != 1:
        raise SystemExit("não achei a linha `leitura:` em ai.roles do config")
    tmp = Path(tempfile.mkdtemp(prefix="bancada-")) / "config.yaml"
    tmp.write_text(novo, encoding="utf-8")
    return load_config(tmp)


async def rodar(args) -> int:
    gabarito = json.loads((RAIZ / args.gabarito).read_text(encoding="utf-8"))
    itens, L, A = gabarito["itens"], gabarito["largura"], gabarito["altura"]
    recortes = [recortar((RAIZ / it["captura"]).read_bytes(), L, A, tuple(it["limites"])) for it in itens]
    saida_dir = (RAIZ / args.gabarito).parent
    if args.ensaio:
        from io import BytesIO

        from PIL import Image
        ims = [Image.open(BytesIO(r)) for r in recortes]
        folha = Image.new("RGB", (max(i.width for i in ims), sum(i.height + 6 for i in ims)), "red")
        y = 0
        for im in ims:
            folha.paste(im, (0, y))
            y += im.height + 6
        destino = saida_dir / "folha-dos-recortes.png"
        folha.save(destino)
        print(f"ensaio: {len(recortes)} recortes, nenhuma chamada; folha em {destino}")
        return 0

    cfg = config_do_leitor(args.leitor, args.modelo)
    hub = RoutingProvider(cfg)
    db = Database(RAIZ / "data" / "poc.sqlite3")
    repo = Repository(db, EventBus(db), RAIZ / "data" / "evidence")
    papel = hub.roles["leitura"]
    gasto, chamadas, pares = 0.0, [], []
    inicio = agora()
    for k, (item, recorte) in enumerate(zip(itens, recortes)):
        for campo in CAMPOS:
            if gasto >= args.teto_usd:
                print(f"TETO de US$ {args.teto_usd} atingido (US$ {gasto:.4f}): a bancada parou")
                break
            t0 = time.monotonic()
            try:
                t, usage = await hub.transcribe(LeituraRequest(recorte=recorte, saidas={campo: campo.replace("_", " ")}))
            except Exception as exc:  # o erro do provedor conta como recusa do par (nunca como concordância)
                chamadas.append({"item": k, "campo": campo, "erro": f"{type(exc).__name__}: {str(exc)[:200]}"})
                for tipo, valor, deve in valores_do_ator(item, campo, itens):
                    pares.append({"item": k, "email": item["email"], "campo": campo, "tipo": tipo, "deve_concordar": deve,
                                  "obtido": "erro_do_leitor", "ok": not deve})
                continue
            usage.origem, usage.ref = "leitura", REF
            repo.add_usage(None, None, usage)
            c = custo(cfg, usage)
            gasto += c
            chamadas.append({"item": k, "campo": campo, "ms": round((time.monotonic() - t0) * 1000), "modelo": usage.model,
                             "entrada": usage.input_tokens, "saida": usage.output_tokens, "usd": round(c, 6),
                             "legivel": t.legivel, "truncado": t.truncado, "campo_lido": t.campos.get(campo),
                             "linhas": t.linhas})
            for tipo, valor, deve in valores_do_ator(item, campo, itens):
                obtido = conferir(valor, campo, t)
                diag = conferir_sem_truncado_alheio(valor, campo, t)
                pares.append({"item": k, "email": item["email"], "campo": campo, "tipo": tipo, "valor_do_ator": valor,
                              "deve_concordar": deve, "obtido": obtido, "ok": (obtido == "concorda") == deve,
                              "diagnostico_sem_truncado_alheio": diag})
        else:
            continue
        break
    ids = [r["id"] for r in db.query("SELECT id FROM ai_calls WHERE ref=? AND ts>=? ORDER BY id", (REF, inicio))]
    verdadeiros = [p for p in pares if p["deve_concordar"]]
    controles = [p for p in pares if not p["deve_concordar"]]
    concordancias = sum(1 for p in verdadeiros if p["obtido"] == "concorda")
    falsas = [p for p in controles if p["obtido"] == "concorda"]
    taxa = concordancias / len(verdadeiros) if verdadeiros else 0.0
    passou = len(pares) >= 30 and not falsas and taxa >= 0.9
    # Diagnóstico (não decide nada): como ficaria sem o "truncado" da linha alheia.
    d_certas = sum(1 for p in verdadeiros if p.get("diagnostico_sem_truncado_alheio") == "concorda")
    d_falsas = sum(1 for p in controles if p.get("diagnostico_sem_truncado_alheio") == "concorda")
    resumo = {"leitor": papel.provider, "modelo": papel.model, "inicio": inicio, "fim": agora(), "recortes": len(recortes),
              "chamadas": len(chamadas), "erros": sum(1 for c in chamadas if "erro" in c), "pares": len(pares),
              "verdadeiros": len(verdadeiros), "concordancias_certas": concordancias, "taxa": round(taxa, 4),
              "controles": len(controles), "concordancias_falsas": len(falsas), "usd_estimado": round(gasto, 6),
              "ai_calls": [ids[0], ids[-1]] if ids else [], "n_ai_calls": len(ids), "passou": passou,
              "recusas_dos_verdadeiros": {c: sum(1 for p in verdadeiros if p["obtido"] == c)
                                          for c in sorted({p["obtido"] for p in verdadeiros} - {"concorda"})},
              "diagnostico_sem_truncado_alheio": {"concordancias_certas": d_certas, "concordancias_falsas": d_falsas}}
    destino = saida_dir / f"resultado-{papel.provider}-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    destino.write_text(json.dumps({"resumo": resumo, "chamadas": chamadas, "pares": pares}, ensure_ascii=False, indent=1),
                       encoding="utf-8")
    print(json.dumps(resumo, ensure_ascii=False))
    for p in pares:
        if not p["ok"]:
            print(f"  FALHA item {p['item']} {p['email']} {p['campo']} {p['tipo']}: {p['obtido']}")
    print(f"resultado em {destino}")
    return 0 if passou else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--gabarito", default="data/bancada-12-5/gabarito.json")
    ap.add_argument("--leitor", default="openai")
    ap.add_argument("--modelo", default=None)
    ap.add_argument("--teto-usd", type=float, default=1.0)
    ap.add_argument("--ensaio", action="store_true")
    return asyncio.run(rodar(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
