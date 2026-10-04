"""Os candidatos a item do plano que saem do histórico de erros do portal (item 29.72, parte do 30.55).

O que ele faz: pede ao central `GET /api/aprendizado/falhas` (JSON) duas vezes, a de sempre e a da camada `pessoa`
(que o backlog deixa de fora por padrão), e grava em `data/aprendizado/candidatos-do-portal.json` (fora do Git) os
grupos que ainda não têm item: abertos (`estado: open`), sem `plan_item` e com pelo menos `--minimo` ocorrências,
mais as propostas abertas do relatório ("investigar …"). Cada candidato traz a contagem, exemplos por id (execução
e tentativa, nunca o texto do erro), a frente sugerida e onde alterar.

O arquivo é para a orquestradora ler. NADA entra no plano por aqui: o número do item é dela, e este script não
escreve no central, no plano nem no estado do plano-100. Só GET, nenhuma IA.

Lote nosso e data (ajuste da orquestradora, 04/10): cada candidato diz quantos dos exemplos vieram de execução NOSSA
(`amostra_de_lote: "n de m"`: chave de idempotência `lote:`/`ensaio:` ou prova de fluxo) e há quantos dias foi a última
ocorrência. O grupo cuja amostra inteira é nossa, ou que não ocorre há mais de `DIAS_PARADO` dias, vai para o fim da
lista: provavelmente é medida nossa ou já parou. A API não expõe a chave de idempotência, então ela vem do banco do
central aberto SÓ PARA LEITURA (`--banco`, `mode=ro`), como outros scripts de diagnóstico; sem o banco, a amostra fica
`null` e nada é rebaixado por ela.

Uso:  python scripts/candidatos-do-portal.py [--dias 14] [--minimo 3] [--limite 200] [--simulados]
                                             [--base URL] [--saida ARQUIVO] [--banco data/poc.sqlite3]
O token da API (quando houver) vem de `API_TOKEN` no ambiente e NUNCA é impresso — nem na saída, nem no erro.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BASE_PADRAO = "http://127.0.0.1:8000"
ROTA = "/api/aprendizado/falhas"
#: O único método que este script usa: ler. Um teste confere.
METODO = "GET"
SAIDA_PADRAO = ROOT / "data" / "aprendizado" / "candidatos-do-portal.json"
REGRA = "candidato não é item: nada entra no plano sem número dado pela orquestradora"

#: A frente que costuma mexer em cada camada do classificador (`falhas.Camada`). É SUGESTÃO: quem decide é a
#: orquestradora. `pessoa` é a informação que só quem pediu tem: o conserto costuma ser a pergunta, o comando ou o canal.
FRENTE_DA_CAMADA: Mapping[str, str] = {
    "execucao": "android", "aparelho": "android", "automacao": "android", "conta_sessao": "android",
    "ia_ator": "jev", "plano": "jev", "verificacao": "jev", "provedor_ia": "jev", "orcamento": "jev",
    "conhecimento_do_app": "aprendizado", "indefinida": "aprendizado",
    "pessoa": "canais",
}
#: Exemplos por candidato: ids bastam para a orquestradora abrir a execução.
EXEMPLOS = 3
#: Sem ocorrência há mais que isto, o grupo vai para o fim da lista (provavelmente já parou).
DIAS_PARADO = 7
BANCO_PADRAO = ROOT / "data" / "poc.sqlite3"
#: Execução NOSSA: a marca de lote das frentes (`lote:<frente>:<id>`), o ensaio e a prova de fluxo (pedido de teste).
PREFIXOS_NOSSOS = ("lote:", "ensaio:")

#: run_id → a execução é nossa? (None = não se sabe). Injetável: os testes não abrem banco.
DeLote = Callable[[Sequence[str]], dict[str, bool]]

#: (endereço, cabeçalhos) → corpo. Injetável: os testes não abrem rede.
Buscador = Callable[[str, dict[str, str]], str]


def url(base: str, *, dias: int, limite: int, simulados: bool = False, camada: str | None = None) -> str:
    params: dict[str, str | int] = {"dias": dias, "formato": "json", "limite": limite}
    if camada:
        params["camada"] = camada
    if simulados:
        params["simulados"] = 1
    return base.rstrip("/") + ROTA + "?" + urllib.parse.urlencode(params)


def cabecalhos(token: str | None) -> dict[str, str]:
    """Sem token, nenhum `Authorization` — o loopback não exige credencial."""
    cab = {"Accept": "application/json"}
    if token:
        cab["Authorization"] = f"Bearer {token}"
    return cab


def buscar(endereco: str, cab: dict[str, str], timeout: float = 60.0) -> str:
    req = urllib.request.Request(endereco, headers=cab, method=METODO)
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - URL é do próprio central
        return resp.read().decode("utf-8")


def _exemplos(grupo: Mapping[str, Any]) -> list[dict[str, Any]]:
    saida: list[dict[str, Any]] = []
    for e in list(grupo.get("exemplos") or [])[:EXEMPLOS]:
        if isinstance(e, Mapping):
            saida.append({k: e.get(k) for k in ("run_id", "attempt_id", "quando") if e.get(k)})
    return saida


def de_lote_no_banco(caminho: Path) -> DeLote:
    """Lê `runs` do banco do central SÓ PARA LEITURA. Banco ausente ou ilegível = dicionário vazio (amostra `null`)."""
    def ler(run_ids: Sequence[str]) -> dict[str, bool]:
        if not run_ids or not caminho.exists():
            return {}
        try:
            # `closing`: o `with` do sqlite3 só faz commit, e a conexão aberta prende o arquivo no Windows.
            with closing(sqlite3.connect(f"file:{caminho.as_posix()}?mode=ro", uri=True)) as c:
                marcas = ",".join("?" for _ in run_ids)
                linhas = c.execute(f"SELECT id, idempotency_key, prova_fluxo_id FROM runs WHERE id IN ({marcas})",
                                   list(run_ids)).fetchall()
        except sqlite3.Error:
            return {}
        return {str(i): bool(p) or str(k or "").startswith(PREFIXOS_NOSSOS) for i, k, p in linhas}
    return ler


def _amostra_de_lote(c: dict[str, Any], nossos: Mapping[str, bool]) -> tuple[int, int] | None:
    ids = [e["run_id"] for e in c.get("exemplos") or [] if e.get("run_id") in nossos]
    return (sum(1 for i in ids if nossos[i]), len(ids)) if ids else None


def _dias_sem_ocorrer(ultima: object, agora: datetime) -> float | None:
    try:
        quando = datetime.fromisoformat(str(ultima).replace("Z", "+00:00"))
    except ValueError:
        return None
    return round((agora - quando) / timedelta(days=1), 1)


def candidato_do_grupo(grupo: Mapping[str, Any], origem: str) -> dict[str, Any]:
    camada = str(grupo.get("camada") or "indefinida")
    onde = grupo.get("onde_alterar") if isinstance(grupo.get("onde_alterar"), Mapping) else {}
    return {
        "chave": grupo.get("id"), "origem": origem, "titulo": grupo.get("titulo"),
        "causa": {"camada": camada, "tipo": grupo.get("tipo"), "app": grupo.get("app"),
                  "capability": grupo.get("capability"), "tela": grupo.get("tela") or None},
        "contagem": {k: grupo.get(k) for k in ("ocorrencias", "execucoes", "aparelhos", "intervencoes")},
        "custo_usd": grupo.get("custo_total"), "tendencia": grupo.get("tendencia"),
        "primeira": grupo.get("primeira"), "ultima": grupo.get("ultima"),
        "exemplos": _exemplos(grupo),
        "frente_sugerida": FRENTE_DA_CAMADA.get(camada, "a decidir"),
        "onde_alterar": onde.get("arquivos") or onde.get("onde"), "prova_sugerida": onde.get("prova"),
    }


def candidato_da_proposta(p: Mapping[str, Any], camada_do_pai: Mapping[str, str]) -> dict[str, Any]:
    camada = camada_do_pai.get(str(p.get("parent_id") or ""), "indefinida")
    return {"chave": p.get("id"), "origem": "proposta", "titulo": p.get("titulo"), "detalhe": p.get("detalhe"),
            "causa": {"camada": camada, "tipo": p.get("tipo"), "app": p.get("app"), "grupo": p.get("parent_id")},
            "frente_sugerida": FRENTE_DA_CAMADA.get(camada, "a decidir")}


def montar(relatorios: Sequence[Mapping[str, Any]], *, minimo: int, agora: datetime,
           de_lote: DeLote | None = None) -> dict[str, Any]:
    """Os candidatos dos relatórios (o de sempre e o da camada `pessoa`), sem repetir chave, do mais caro ao mais
    barato; o grupo de amostra toda nossa ou parado há mais de `DIAS_PARADO` dias vai para o fim (`rebaixado`). O que
    ficou de fora é contado pelo motivo, para a orquestradora saber que não sumiu nada em silêncio."""
    candidatos: list[dict[str, Any]] = []
    vistos: set[str] = set()
    fora = {"com_item_do_plano": 0, "fora_de_aberto": 0, "abaixo_do_minimo": 0}
    camada_do_pai: dict[str, str] = {}
    commit = None
    for rel in relatorios:
        commit = commit or rel.get("commit")
        for g in list(rel.get("itens") or []) + list(rel.get("verificacao") or []):
            if not isinstance(g, Mapping) or not g.get("id"):
                continue
            camada_do_pai[str(g["id"])] = str(g.get("camada") or "indefinida")
            if str(g["id"]) in vistos:
                continue
            vistos.add(str(g["id"]))
            if g.get("plan_item"):
                fora["com_item_do_plano"] += 1
            elif g.get("estado") not in (None, "open"):
                fora["fora_de_aberto"] += 1
            elif int(g.get("ocorrencias") or 0) < minimo:
                fora["abaixo_do_minimo"] += 1
            else:
                candidatos.append(candidato_do_grupo(g, "pessoa" if g.get("camada") == "pessoa" else "falhas"))
    nossos = (de_lote or (lambda _ids: {}))([e["run_id"] for c in candidatos for e in c["exemplos"] if e.get("run_id")])
    for c in candidatos:
        amostra = _amostra_de_lote(c, nossos)
        c["amostra_de_lote"] = None if amostra is None else f"{amostra[0]} de {amostra[1]}"
        c["dias_sem_ocorrer"] = _dias_sem_ocorrer(c.get("ultima"), agora)
        motivos = []
        if amostra is not None and amostra[0] == amostra[1]:
            motivos.append("amostra toda de execução nossa (lote, ensaio ou prova)")
        if c["dias_sem_ocorrer"] is not None and c["dias_sem_ocorrer"] > DIAS_PARADO:
            motivos.append(f"sem ocorrência há mais de {DIAS_PARADO} dias")
        c["rebaixado"] = "; ".join(motivos) or None
    candidatos.sort(key=lambda c: (c["rebaixado"] is not None, -(float(c.get("custo_usd") or 0))))
    for rel in relatorios:
        for p in rel.get("propostas") or []:
            if (isinstance(p, Mapping) and p.get("id") and str(p["id"]) not in vistos
                    and p.get("estado") in (None, "open") and not p.get("plan_item")):
                vistos.add(str(p["id"]))
                candidatos.append(candidato_da_proposta(p, camada_do_pai))
    return {"gerado_em": agora.isoformat().replace("+00:00", "Z"), "commit": commit, "regra": REGRA,
            "minimo_de_ocorrencias": minimo, "candidatos": candidatos, "fora": fora}


def _sem_token(texto: str, token: str | None) -> str:
    return texto.replace(token, "***") if token else texto


def main(argv: Sequence[str] | None = None, *, buscador: Buscador = buscar, agora: datetime | None = None,
         token: str | None = None, de_lote: DeLote | None = None) -> int:
    ap = argparse.ArgumentParser(description="Candidatos a item do plano a partir dos erros do portal (só GET).")
    ap.add_argument("--base", default=BASE_PADRAO, help="endereço do central (padrão: %(default)s)")
    ap.add_argument("--dias", type=int, default=14, help="janela em dias (padrão: %(default)s)")
    ap.add_argument("--minimo", type=int, default=3, help="ocorrências para virar candidato (padrão: %(default)s)")
    ap.add_argument("--limite", type=int, default=200, help="grupos pedidos ao relatório (padrão: %(default)s)")
    ap.add_argument("--simulados", action="store_true", help="inclui as execuções simuladas")
    ap.add_argument("--saida", default=str(SAIDA_PADRAO), help="arquivo JSON (padrão: data/aprendizado/…)")
    ap.add_argument("--banco", default=str(BANCO_PADRAO),
                    help="banco do central, aberto só para leitura, para a amostra de lote (padrão: data/poc.sqlite3)")
    a = ap.parse_args(argv)
    chave = token if token is not None else (os.environ.get("API_TOKEN") or None)
    relatorios: list[Mapping[str, Any]] = []
    for camada in (None, "pessoa"):
        endereco = url(a.base, dias=a.dias, limite=a.limite, simulados=a.simulados, camada=camada)
        try:
            relatorios.append(json.loads(buscador(endereco, cabecalhos(chave))))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            print(_sem_token(f"erro ao ler {endereco}: {type(exc).__name__}: {exc}", chave), file=sys.stderr)
            return 2
    saida = montar(relatorios, minimo=a.minimo, agora=agora or datetime.now(timezone.utc),
                   de_lote=de_lote or de_lote_no_banco(Path(a.banco)))
    destino = Path(a.saida)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps(saida, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"gravado: {destino} · {len(saida['candidatos'])} candidato(s) · fora: "
          + ", ".join(f"{k} {v}" for k, v in saida["fora"].items()))
    for c in saida["candidatos"][:5]:
        print(_sem_token(f"{c['chave']} · {c['frente_sugerida']} · {c['titulo']}", chave))
    return 0


if __name__ == "__main__":
    sys.exit(main())
