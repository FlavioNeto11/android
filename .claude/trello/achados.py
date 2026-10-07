"""28.63: um cartão por achado de revisão automática de PR (coletor 29.170 da Frente GitHub), no quadro Execução.

O coletor (`scripts/coletar_achados_revisao.py --json`, só leitura) devolve uma lista de achados com id estável
(`PR:arquivo:linha:revisor`). Este script, no host e fora do backend, cria UM cartão por id em Próximas e fecha o cartão
(vai a Concluído) quando o PR sai de "open". Achado é "a conferir", nunca ordem: o cartão diz isso e a decisão é da
orquestradora. Nada escreve no GitHub, e o cartão não leva trecho de código nem handle (a frase já vem mascarada do
coletor, e passa de novo por `redacao.redigir`).

O nome do cartão não começa com ID do plano, então a reconciliação (`reconciliar.py`) o ignora; a chave que o liga ao
achado fica na descrição ("Chave do achado: <id>"), o que torna a execução idempotente.

Uso (da raiz):
  backend/.venv/Scripts/python.exe .claude/trello/achados.py --json achados.json [--aplicar]
  backend/.venv/Scripts/python.exe .claude/trello/achados.py --coletor "<repo>" [--horas 48] [--aplicar]
Sem `--aplicar` só relata (ensaio).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parents[1]
sys.path.insert(0, str(AQUI))
BACKEND_CENTRAL = Path(r"C:\git\android\backend")

LISTA_PROXIMAS = "6ac13b17a670feab8e9d3f4c"
LISTA_CONCLUIDO = "6ac13b1d2b3e0ab6f1126112"
QUADRO_EXECUCAO = "6ac13aeda5570365d020f8e2"
PREFIXO_DA_CHAVE = "Chave do achado: "
NOME_MAX = 100
FRASE_MAX = 400



@dataclass
class Acao:
    tipo: str                       # "criar" | "fechar"
    chave: str
    cartao: str | None = None       # id do cartão (só em "fechar")
    nome: str = ""
    desc: str = ""
    posicao: str = "top"


def achados_do_json(texto: str) -> list[dict[str, object]]:
    """A lista do coletor, só com os itens do formato certo (id, PR inteiro, arquivo texto); o resto é descartado."""
    bruto = json.loads(texto)
    if not isinstance(bruto, list):
        raise ValueError("o coletor deve devolver uma lista JSON")
    saida: list[dict[str, object]] = []
    for a in bruto:
        if (isinstance(a, dict) and isinstance(a.get("id"), str) and a["id"]
                and isinstance(a.get("pr"), int) and isinstance(a.get("arquivo"), str)):
            saida.append(a)
    return saida


def chave_da_descricao(desc: str) -> str | None:
    m = re.search(rf"^{re.escape(PREFIXO_DA_CHAVE)}(.+?)\s*$", desc or "", flags=re.M)
    return m.group(1) if m else None


def _onde(a: dict[str, object]) -> str:
    linha = a.get("linha")
    return f"{a['arquivo']}:{linha}" if isinstance(linha, int) else str(a["arquivo"])


def nome_do_cartao(a: dict[str, object]) -> str:
    grav = a.get("gravidade") if a.get("gravidade") in ("P0", "P1", "P2", "P3") else "-"
    baixa = "(baixa prioridade) " if a.get("artefato") is True else ""
    nome = f"🔎 {baixa}PR {a['pr']} · {grav} · {_onde(a)} · {a.get('revisor', '?')}"
    return nome if len(nome) <= NOME_MAX else nome[:NOME_MAX - 1] + "…"


def desc_do_cartao(a: dict[str, object], redigir=lambda t: t) -> str:  # noqa: ANN001
    frase = redigir(str(a.get("frase") or ""))[:FRASE_MAX]
    url = a.get("url")
    linhas = [
        f"**Achado de revisão automática (PR {a['pr']}, {a.get('revisor', '?')}), a conferir: nunca ordem.** "
        "A decisão é da orquestradora.",
        "",
        f"Gravidade: {a.get('gravidade') or '-'} · Onde: {_onde(a)}",
        f"Frase do revisor (mascarada, sem trecho de código): {frase or '(sem frase)'}",
    ]
    if a.get("artefato") is True:
        linhas.append("Baixa prioridade: só repete regra de conduta de agente aplicada a PR de sessão.")
    if isinstance(url, str) and url.startswith("https://github.com/"):
        linhas.append(f"Comentário: {url}")
    linhas += ["", f"{PREFIXO_DA_CHAVE}{a['id']}"]
    return "\n".join(linhas)


def decidir(achados: list[dict[str, object]], existentes: dict[str, dict[str, str]],
            redigir=lambda t: t) -> list[Acao]:  # noqa: ANN001
    """`existentes`: chave do achado → {"id": cartão, "lista": id da lista}. Cria o que falta (PR ainda aberto e com
    arquivo) e fecha o que o PR já resolveu (closed ou merged). Um achado sem `pr_estado` conhecido não fecha nada."""
    acoes: list[Acao] = []
    for a in achados:
        chave = str(a["id"])
        if not a.get("arquivo"):
            continue                              # o resumo geral da revisão não vira cartão
        estado = a.get("pr_estado")
        ja = existentes.get(chave)
        if ja is None:
            if estado == "open":
                baixa = a.get("artefato") is True or a.get("gravidade") in ("P3", "-")
                acoes.append(Acao("criar", chave, None, nome_do_cartao(a), desc_do_cartao(a, redigir),
                                  "bottom" if baixa else "top"))
        elif estado in ("closed", "merged") and ja.get("lista") != LISTA_CONCLUIDO:
            acoes.append(Acao("fechar", chave, ja["id"], "", f"PR {estado}: achado fora de uso"))
    return acoes


def _coletar(repo: str, horas: int) -> str:
    saida = subprocess.run([sys.executable, str(RAIZ / "scripts" / "coletar_achados_revisao.py"), "--repo", repo,
                            "--horas", str(horas), "--json"], capture_output=True, text=True, check=True, timeout=300)
    return saida.stdout


async def _principal(achados: list[dict[str, object]], aplicar: bool) -> int:
    # as chaves do Trello moram no checkout central (por instalação, fora do Git); o worktree não as tem
    sys.path.insert(0, str(BACKEND_CENTRAL))
    from app.config import EnvSettings  # noqa: PLC0415 - só no modo de rede
    from app.modules.avisos.adapters.trello import ClienteTrello  # noqa: PLC0415
    from redacao import redigir  # noqa: PLC0415
    e = EnvSettings()
    cl = ClienteTrello(e.trello_api_key.get_secret_value().strip(), e.trello_token.get_secret_value().strip())
    cs = await cl._pedir("GET", f"/1/boards/{QUADRO_EXECUCAO}/cards",
                         params={"fields": "name,idList,desc", "filter": "open"})
    existentes = {k: {"id": c["id"], "lista": c["idList"]}
                  for c in cs for k in [chave_da_descricao(c.get("desc", ""))] if k}
    acoes = decidir(achados, existentes, redigir)
    print("achados:", len(achados), "· com cartão:", len(existentes), "· ações:", len(acoes))
    for a in acoes:
        print(f"  {a.tipo:6} {a.chave[:60]} | {a.nome[:70]}")
    if not aplicar:
        return 0
    for a in acoes:
        if a.tipo == "criar":
            r = await cl.criar_cartao(LISTA_PROXIMAS, a.nome, a.desc)
            if a.posicao == "bottom":
                await cl._pedir("PUT", f"/1/cards/{r['id']}", corpo={"pos": "bottom"})
        else:
            atual = await cl._pedir("GET", f"/1/cards/{a.cartao}", params={"fields": "desc"})
            await cl.atualizar_cartao(a.cartao, desc=f"**{a.desc}.**\n\n{atual['desc']}", lista=LISTA_CONCLUIDO)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--json", help="arquivo com a saída `--json` do coletor")
    g.add_argument("--coletor", metavar="DONO/NOME", help="roda o coletor para este repositório (só leitura)")
    p.add_argument("--horas", type=int, default=48)
    p.add_argument("--aplicar", action="store_true")
    args = p.parse_args(argv)
    texto = Path(args.json).read_text(encoding="utf-8") if args.json else _coletar(args.coletor, args.horas)
    return asyncio.run(_principal(achados_do_json(texto), args.aplicar))


if __name__ == "__main__":
    raise SystemExit(main())
