"""Gera os LOTES de cartões do Trello a partir do plano-100 e do estado (fonte única: o repositório).

Saída: `<destino>/lotes/*.json`, um arquivo por lista de destino, cada cartão com `nome`, `desc` (a parte técnica já
preenchida; os marcadores {{NAO_TECNICO}} e {{POR_QUE}} ficam para quem escreve a parte para leigos), `lista`
(ARI), `etiquetas` (ARIs), `quando`, `status`, `due`. Quem cria o cartão é o MCP do Trello (sem chave no repositório);
este script só prepara o conteúdo e o `mapa.json` recebe os ARIs dos cartões criados.

Uso: python .claude/trello/gerar.py [--destino <pasta>] [--so hoje|historico|pendentes|todos]
Regras: nunca inclui segredo, nome de pessoa real, e-mail, telefone ou texto de comando de execução; só o que
já está em docs/plano-100.md, .claude/plano-100/estado.json e CHANGELOG.md.
"""
from __future__ import annotations

import argparse
import collections
import io
import json
import re
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
ESTRUTURA = json.load(io.open(Path(__file__).with_name("estrutura.json"), encoding="utf-8"))
HOJE = date.today().isoformat()


def grupo_fase(n: int) -> str:
    if n <= 5:
        return "Fases 0–5 · fundação"
    if n <= 10:
        return "Fases 6–10 · apps, IA e operação"
    if n == 11:
        return "Fase 11 · painel"
    if n <= 15:
        return "Fases 14–15 · desempenho e skills"
    if n <= 17:
        return "Fases 16–17 · persona, custo e imagem"
    if n <= 20:
        return "Fases 18–20 · conhecimento, Instagram e aprendizado"
    if n <= 22:
        return "Fases 21–22 · proteção de contas"
    if n <= 27:
        return "Fases 23–27 · terceira evolução"
    return {28: "Fase 28 · pedidos persistentes", 29: "Fase 29 · pendências da evolução 3",
            30: "Fase 30 · aprendizado vivo", 31: "Fase 31 · Jev"}.get(n, f"Fase {n}")


def frente(item: dict) -> str:
    g = (item.get("grupo") or "").lower()
    if "jev" in g:
        return "jev"
    if "apr" in g or "aprend" in g or "curador" in g:
        return "aprendizado"
    if "android" in g or "parque" in g:
        return "android"
    n = int(item["id"].split(".")[0])
    return {30: "aprendizado", 31: "jev"}.get(n, "android" if n in (23, 24, 25, 28, 29) else "plataforma")


def registro_changelog(ident: str, changelog: list[str]) -> str:
    pad = re.compile(r"(?<![\d.])" + re.escape(ident) + r"(?![\d])")
    for l in changelog:
        if pad.search(l):
            return re.sub(r"\s+", " ", l.strip("-* ")).strip()[:300]
    return ""


def desc_tecnica(it: dict, fase: str, registro: str) -> str:
    arquivos = ", ".join(f"`{a}`" for a in (it.get("arquivos") or [])[:6]) or "—"
    testes = ", ".join(f"`{t}`" for t in (it.get("testes") or [])[:4]) or "—"
    prova = it.get("proof") or "not_run"
    evid = (it.get("evidence") or "").replace("\n", " ")[:500]
    return "\n".join([
        "**Para quem não é técnico:** {{NAO_TECNICO}}",
        "**Por que importa:** {{POR_QUE}}",
        "",
        f"**Técnico (requisito):** {it['oque']}",
        f"**Achados / contexto:** {it.get('achados') or '—'}",
        f"**Prova:** `{prova}` — {evid or 'sem evidência registrada'}",
        f"**Arquivos:** {arquivos}",
        f"**Testes:** {testes}",
        f"**Frente:** {it['frente']} · **Esforço:** {it.get('esforco') or it.get('tam') or '—'} · **Modelo:** {it.get('modelo') or '—'}",
        f"**Estado no plano:** `{it['status']}` · **Concluído em:** {it.get('quando') or '—'} · **Fase:** {fase}",
        (f"**Registro (CHANGELOG):** {registro}" if registro else "**Registro (CHANGELOG):** —"),
        f"**Fonte:** `docs/plano-100.md` linha {it['linha']} (`| {it['id']} |`), `.claude/plano-100/estado.json`",
    ])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--destino", default=str(Path(__file__).with_name("saida")))
    ap.add_argument("--so", default="todos", choices=("hoje", "historico", "pendentes", "todos"))
    args = ap.parse_args()
    destino = Path(args.destino) / "lotes"
    destino.mkdir(parents=True, exist_ok=True)

    est = json.load(io.open(RAIZ / ".claude/plano-100/estado.json", encoding="utf-8"))
    itens = est.get("itens") or est.get("items") or est
    estado = dict(itens.items()) if isinstance(itens, dict) else {x.get("id"): x for x in itens}
    changelog = io.open(RAIZ / "CHANGELOG.md", encoding="utf-8").read().split("\n")

    fase = ""
    rows = []
    for i, l in enumerate(io.open(RAIZ / "docs/plano-100.md", encoding="utf-8").read().split("\n")):
        if l.startswith("#"):
            fase = l.strip("# ").strip()
        m = re.match(r"^\|\s*(\d+\.\d+)\s*\|(.*)$", l)
        if not m:
            continue
        cols = [c.strip() for c in m.group(2).split("|")]
        e = estado.get(m.group(1), {})
        oque = cols[0] if cols else ""
        it = {
            "id": m.group(1), "fase": fase, "linha": i + 1, "oque": oque,
            "titulo": re.sub(r"\*\*", "", oque.split(":")[0]).strip()[:90],
            "achados": cols[1] if len(cols) > 1 else "", "tam": cols[2] if len(cols) > 2 else "",
            "status": e.get("status", "pendente"), "proof": e.get("proof"), "evidence": (e.get("evidence") or "")[:500],
            "grupo": e.get("grupo"), "modelo": e.get("modelo"), "esforco": e.get("esforco"), "quando": e.get("quando"),
            "blocker": (e.get("blocker") or "")[:300] if isinstance(e.get("blocker"), str) else "",
            "arquivos": (e.get("arquivos") or e.get("files") or [])[:6], "testes": (e.get("testes") or [])[:4],
        }
        it["frente"] = frente(it)
        rows.append(it)

    ex = ESTRUTURA["quadros"]["execucao"]
    hi = ESTRUTURA["quadros"]["historico"]
    lotes: dict[str, list] = collections.defaultdict(list)
    for it in rows:
        registro = registro_changelog(it["id"], changelog)
        cartao = {
            "id": it["id"], "nome": f"{it['id']} · {it['titulo']}", "status": it["status"], "quando": it.get("quando"),
            "frente": it["frente"], "fase": it["fase"], "desc": desc_tecnica(it, it["fase"], registro),
        }
        quadro = "execucao"
        if it["status"] == "implemented" and (it.get("quando") or "")[:10] == HOJE:
            lista_nome, lista = "concluido_hoje", ex["listas"]["concluido_hoje"]
        elif it["status"] == "implemented":
            quadro = "historico"
            lista_nome = grupo_fase(int(it["id"].split(".")[0]))
            lista = hi["listas"][lista_nome]
        elif it["status"] == "blocked":
            lista_nome, lista = "bloqueado", ex["listas"]["bloqueado"]
        else:
            lista_nome, lista = "proximas", ex["listas"]["proximas"]
        etq = ESTRUTURA["quadros"][quadro]["etiquetas"]
        cartao.update({"quadro": quadro, "lista_nome": lista_nome, "lista": lista,
                       "etiquetas": [etq[it["frente"]]] if it["frente"] in etq else [],
                       "blocker": it.get("blocker") or ""})
        if it["status"] == "blocked" and "bloqueado_urgente" in etq:
            cartao["etiquetas"].append(etq["bloqueado_urgente"])
        chave = {"concluido_hoje": "hoje", "bloqueado": "pendentes", "proximas": "pendentes"}.get(lista_nome, "historico")
        if args.so in ("todos", chave):
            lotes[lista_nome if chave == "historico" else chave].append(cartao)

    for nome, cartoes in lotes.items():
        arq = destino / (re.sub(r"[^\w]+", "_", nome).strip("_").lower() + ".json")
        json.dump(cartoes, io.open(arq, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"{arq.name}: {len(cartoes)} cartões")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
