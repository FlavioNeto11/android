"""31.268: o roteiro de prova real dos itens do aprendizado dos deploys 60 e 61 (31.231, 31.232, 31.236 a 31.239,
31.242 a 31.244, 31.248 a 31.250, 31.262). SÓ LEITURA: o banco abre em `mode=ro`, a saúde é um GET.

Dado o id da operação, por item:
1. o commit do item estava no ar quando a operação começou? O commit no ar sai de `--commit`, ou da linha do tempo dos
   deploys do `CHANGELOG.md` ("Deploy NN" + "Implantado às HH:MMZ: central em `sha`"), o último antes da operação; sem
   deploy antes dela, o da saúde. O item fora dele: `nao_no_ar`;
2. no ar, a leitura do item diz o achado:
   - `presente`: a operação exercitou o item e a evidência esperada está lá. Prova `real`;
   - `divergente`: exercitou, e o registro contradiz o item. Prova `real` de um defeito, à parte;
   - `ausente`: havia o caso, e a evidência não apareceu;
   - `sem_caso`: a operação não passou pelo caso do item.

A saída é o formato do plano-100 só com as linhas `real` dos itens `presente` (`resultados`). Os outros vão a
`pendentes` (`not_run`) ou a `divergencias`, e NÃO entram no `aplicar`: uma linha `not_run` apaga a prova simulada já
registrada (aprendizado de 06/10). Com `--saida`, grava também um JSON por item (`aprendizado-real-31-NNN.json`).
Nada de nome, @, legenda ou texto de memória: só ids, contagens e chaves.

Exemplo, a partir da raiz do repositório:
    backend/.venv/Scripts/python.exe scripts/prova-real-aprendizado.py --operacao op-... --saida .claude/handoffs/prova61
    backend/.venv/Scripts/python.exe scripts/prova-real-aprendizado.py --operacao op-... --commit 42cba3cd --tabela
"""
from __future__ import annotations

import argparse
import json
import re
import socket
import sqlite3
import subprocess
import sys
import urllib.request
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
SAUDE = "http://127.0.0.1:8000/api/health"
ACHADOS = ("presente", "divergente", "ausente", "sem_caso")
#: A imagem que pode ir com a decisão do commit no forte (31.232): o alvo fora da árvore, ou pedida por um problema.
_IMAGEM_QUE_VALE = ("alvo_fora_da_arvore", "pedida", "problema", "politica_sempre")


@dataclass(frozen=True)
class Leitura:
    achado: str                              # um de ACHADOS
    detalhe: str                             # ids e contagens; nunca valor


@dataclass(frozen=True)
class Item:
    commit: str                              # o do `feat` do item (o último, quando houve correção)
    esperado: str                            # a evidência que a leitura procura
    ler: Callable[[sqlite3.Connection, str], Leitura]


# ------------------------------------------------------------------ apoio
def _em(n: int) -> str:
    return ",".join("?" * n)


def _runs(c: sqlite3.Connection, op: str) -> list[str]:
    return [str(r[0]) for r in c.execute("SELECT id FROM runs WHERE operacao_id=? ORDER BY id", (op,))]


def _eventos(c: sqlite3.Connection, runs: Sequence[str], kind: str, *, mensagem: str | None = None,
             dado: str | None = None) -> list[sqlite3.Row]:
    if not runs:
        return []
    sql = f"SELECT id, run_id, step_id FROM events WHERE run_id IN ({_em(len(runs))}) AND kind=?"
    args: list[object] = [*runs, kind]
    if mensagem is not None:
        sql += " AND message LIKE ?"
        args.append(mensagem)
    if dado is not None:
        sql += " AND data LIKE ?"
        args.append(dado)
    return list(c.execute(sql + " ORDER BY id", args))


def _contas(c: sqlite3.Connection, runs: Sequence[str]) -> list[str]:
    """As contas das personas dos objetivos da operação (só para comparar; nunca saem do script)."""
    if not runs:
        return []
    return [str(r[0]) for r in c.execute(
        "SELECT DISTINCT pa.handle FROM objectives o JOIN profile_accounts pa ON pa.profile_id = o.profile_id"
        f" WHERE o.run_id IN ({_em(len(runs))}) AND pa.handle IS NOT NULL AND pa.handle <> ''", tuple(runs))]


def _padrao(contas: Iterable[str]) -> re.Pattern[str] | None:
    nomes = sorted({h.strip().lstrip("@") for h in contas if h and h.strip().lstrip("@")}, key=len, reverse=True)
    if not nomes:
        return None
    return re.compile(r"(?<![\w.])@?(?:" + "|".join(re.escape(n) for n in nomes) + r")(?![\w])", re.IGNORECASE)


def _com_conta(textos: Iterable[object], padrao: re.Pattern[str] | None) -> int:
    return 0 if padrao is None else sum(1 for t in textos if t and padrao.search(str(t)))


# ------------------------------------------------------------------ as leituras
def ler_31_231(c: sqlite3.Connection, op: str) -> Leitura:
    runs = _runs(c, op)
    reuso = _eventos(c, runs, "log", mensagem="%pesquisa reaproveitada do Livro%")
    if reuso:
        return Leitura("presente", f"pesquisa reaproveitada do Livro, sem chamada paga (eventos {[r['id'] for r in reuso]})")
    paga = _eventos(c, runs, "log", mensagem="%: pesquisa % fato(s)%")
    if paga:
        return Leitura("ausente", f"pesquisa paga (eventos {[r['id'] for r in paga]}): o Livro não cobriu o assunto")
    return Leitura("sem_caso", "a operação não pesquisou")


def ler_31_248(c: sqlite3.Connection, op: str) -> Leitura:
    runs = _runs(c, op)
    linha = c.execute("SELECT assunto FROM operacoes WHERE id=?", (op,)).fetchone()
    if linha is not None and (linha[0] or "").strip():
        return Leitura("sem_caso", "a operação tem assunto no pedido")
    pela_leitura = _eventos(c, runs, "log", mensagem="%pesquisa com o assunto da leitura do alvo%")
    if pela_leitura:
        return Leitura("presente", f"pesquisa com o assunto da leitura do alvo (eventos {[r['id'] for r in pela_leitura]})")
    reuso = _eventos(c, runs, "log", mensagem="%pesquisa reaproveitada do Livro%")
    if reuso:
        return Leitura("presente", f"assunto da leitura coberto pelo Livro (eventos {[r['id'] for r in reuso]})")
    return Leitura("ausente", f"operação sem assunto e sem pesquisa nas {len(runs)} execução(ões)")


def ler_31_232(c: sqlite3.Connection, op: str) -> Leitura:
    runs = _runs(c, op)
    if not runs:
        return Leitura("sem_caso", "a operação não tem execução")
    linhas = list(c.execute(
        "SELECT id, with_image, image_reason FROM ai_calls WHERE role='decide' AND escalate='efeito' AND tier >= 1"
        f" AND run_id IN ({_em(len(runs))}) ORDER BY id", tuple(runs)))
    if not linhas:
        return Leitura("sem_caso", "nenhuma decisão de commit no modelo forte")
    com_imagem = [r["id"] for r in linhas if r["with_image"] and r["image_reason"] not in _IMAGEM_QUE_VALE]
    if com_imagem:
        return Leitura("divergente", f"{len(com_imagem)} de {len(linhas)} decisão(ões) de commit no forte com imagem "
                                     f"sem o alvo fora da árvore (ai_calls {com_imagem[:8]})")
    return Leitura("presente", f"{len(linhas)} decisão(ões) de commit no forte, nenhuma com imagem sem motivo "
                               f"(ai_calls {[r['id'] for r in linhas][:8]})")


def ler_31_236(c: sqlite3.Connection, op: str) -> Leitura:
    linha = c.execute("SELECT parametros FROM operacoes WHERE id=?", (op,)).fetchone()
    try:
        fixos = json.loads(linha[0]) if linha is not None and linha[0] else {}
    except ValueError:
        fixos = {}
    if not fixos:
        return Leitura("sem_caso", "a operação não tem parâmetro fixo")
    runs = _runs(c, op)
    paradas = [str(r[0]) for r in c.execute(
        f"SELECT id FROM runs WHERE id IN ({_em(len(runs))}) AND status='needs_input' ORDER BY id", tuple(runs))] if runs else []
    if paradas:
        return Leitura("divergente", f"{len(paradas)} de {len(runs)} execução(ões) pararam pedindo informação com "
                                     f"{len(fixos)} parâmetro(s) fixo(s) ({paradas[:5]})")
    if not runs:
        return Leitura("sem_caso", "a operação não tem execução")
    return Leitura("presente", f"{len(runs)} execução(ões) com {len(fixos)} parâmetro(s) fixo(s), nenhuma em needs_input")


def ler_31_237(c: sqlite3.Connection, op: str) -> Leitura:
    runs = _runs(c, op)
    if not runs:
        return Leitura("sem_caso", "a operação não tem execução")
    planos = list(c.execute(
        "SELECT id, cache_read, cache_write FROM ai_calls WHERE role='plan' AND motivo='plano' AND ok=1"
        f" AND run_id IN ({_em(len(runs))}) ORDER BY ts, id", tuple(runs)))
    if len(planos) < 2:
        return Leitura("sem_caso", f"{len(planos)} plano(s): sem irmão para ler o cache")
    frios = [r["id"] for r in planos[1:] if not (r["cache_read"] or 0)]
    if frios:
        return Leitura("divergente", f"{len(frios)} de {len(planos) - 1} plano(s) irmão(s) sem ler o cache "
                                     f"(ai_calls {frios[:8]})")
    return Leitura("presente", f"o 1º plano gravou o cache e os {len(planos) - 1} irmão(s) leram "
                               f"(ai_calls {[r['id'] for r in planos][:8]})")


def ler_31_238(c: sqlite3.Connection, op: str) -> Leitura:
    runs = _runs(c, op)
    dispensas = _eventos(c, runs, "decision", dado="%rejulgamento_dispensado%")
    if dispensas:
        return Leitura("presente", f"{len(dispensas)} rejulgamento(s) dispensado(s) (eventos {[r['id'] for r in dispensas][:8]})")
    pela_prova = [r for r in _eventos(c, runs, "decision", mensagem="%comprovad% pela árvore local%")
                  if r["step_id"] and c.execute("SELECT side_effect FROM steps WHERE id=?", (r["step_id"],)).fetchone()
                  and c.execute("SELECT side_effect FROM steps WHERE id=?", (r["step_id"],)).fetchone()[0]]
    if pela_prova:
        return Leitura("ausente", f"{len(pela_prova)} efeito(s) pela prova local e nenhum rejulgamento dispensado "
                                  "(o app sem o direito na janela, ou o efeito sem prova local)")
    return Leitura("sem_caso", "nenhum efeito comprovado pela prova local do app")


def ler_31_239(c: sqlite3.Connection, op: str) -> Leitura:
    runs = _runs(c, op)
    if not runs:
        return Leitura("sem_caso", "a operação não tem execução")
    etapas = [str(r[0]) for r in c.execute(
        f"SELECT id FROM steps WHERE run_id IN ({_em(len(runs))}) AND capability='CREATE_COMMENT' ORDER BY id",
        tuple(runs))]
    if not etapas:
        return Leitura("sem_caso", "nenhuma etapa de comentário")
    provadas = _eventos(c, runs, "decision", mensagem="%comentário comprovado pela árvore local%")
    if provadas:
        return Leitura("presente", f"{len(provadas)} comentário(s) comprovado(s) pela árvore, 1º juiz dispensado "
                                   f"(eventos {[r['id'] for r in provadas][:8]})")
    publicadas = [str(r[0]) for r in c.execute(
        f"SELECT id FROM steps WHERE id IN ({_em(len(etapas))}) AND status='succeeded'", tuple(etapas))]
    if not publicadas:
        return Leitura("sem_caso", f"{len(etapas)} etapa(s) de comentário, nenhuma comprovada")
    return Leitura("ausente", f"{len(publicadas)} comentário(s) comprovado(s) sem a prova da árvore ({publicadas[:5]})")


def ler_31_242(c: sqlite3.Connection, op: str) -> Leitura:
    runs = _runs(c, op)
    if not runs:
        return Leitura("sem_caso", "a operação não tem execução")
    notas = list(c.execute(f"SELECT id, note FROM evidence WHERE run_id IN ({_em(len(runs))}) AND note IS NOT NULL",
                           tuple(runs)))
    if not notas:
        return Leitura("sem_caso", "nenhuma nota de evidência")
    padrao = _padrao(_contas(c, runs))
    if padrao is None:
        return Leitura("sem_caso", "as personas da operação não têm conta com usuário")
    abertas = [r["id"] for r in notas if r["note"] and padrao.search(str(r["note"]))]
    if abertas:
        return Leitura("divergente", f"{len(abertas)} de {len(notas)} nota(s) com o usuário da conta sem máscara "
                                     f"(evidence {abertas[:8]})")
    return Leitura("presente", f"{len(notas)} nota(s), nenhuma com o usuário da conta (evidence {[r['id'] for r in notas][:8]})")


def ler_31_243(c: sqlite3.Connection, op: str) -> Leitura:
    runs = _runs(c, op)
    if not runs:
        return Leitura("sem_caso", "a operação não tem execução")
    padrao = _padrao(_contas(c, runs))
    if padrao is None:
        return Leitura("sem_caso", "as personas da operação não têm conta com usuário")
    em = _em(len(runs))
    eventos = list(c.execute(f"SELECT message, data FROM events WHERE run_id IN ({em})", tuple(runs)))
    acoes = list(c.execute(f"SELECT a.args, a.rationale, a.error FROM actions a JOIN attempts t ON t.id = a.attempt_id"
                           f" JOIN steps s ON s.id = t.step_id WHERE s.run_id IN ({em})", tuple(runs)))
    etapas = list(c.execute(f"SELECT status_detail FROM steps WHERE run_id IN ({em})", tuple(runs)))
    fontes = {"eventos": _com_conta((x for r in eventos for x in r), padrao),
              "acoes": _com_conta((x for r in acoes for x in r), padrao),
              "status_detail": _com_conta((r[0] for r in etapas), padrao)}
    total = len(eventos) + len(acoes) + len(etapas)
    if any(fontes.values()):
        return Leitura("divergente", f"usuário da conta sem máscara em {fontes} de {total} registro(s)")
    return Leitura("presente", f"{total} registro(s) (eventos {len(eventos)}, ações {len(acoes)}, etapas {len(etapas)}), "
                               "nenhum com o usuário da conta")


def ler_31_244(c: sqlite3.Connection, op: str) -> Leitura:
    runs = _runs(c, op)
    if not runs:
        return Leitura("sem_caso", "a operação não tem execução")
    receitas = list(c.execute(
        "SELECT r.id, r.actions FROM recipes r JOIN steps s ON s.id = r.learned_from_step"
        f" WHERE s.run_id IN ({_em(len(runs))}) ORDER BY r.id", tuple(runs)))
    if not receitas:
        return Leitura("sem_caso", "nenhuma receita aprendida nas execuções da operação")
    padrao = _padrao(_contas(c, runs))
    abertas = [r["id"] for r in receitas if padrao is not None and padrao.search(str(r["actions"] or ""))]
    if abertas:
        return Leitura("divergente", f"{len(abertas)} receita(s) com o usuário da conta gravado ({abertas})")
    marcadas = [r["id"] for r in receitas if re.search(r"\{(?:perfil|conta)_\w+\}", str(r["actions"] or ""))]
    if marcadas:
        return Leitura("presente", f"{len(marcadas)} receita(s) com o marcador da persona ({marcadas})")
    return Leitura("sem_caso", f"{len(receitas)} receita(s) aprendida(s), nenhuma de passo com dado da persona")


def _escopo(bindings: dict[str, object], contas: Sequence[str]) -> str | None:
    """A regra do 31.249 (`recipes.escopo_do_alvo`), a única fonte; o backend entra no caminho só aqui."""
    if str(RAIZ / "backend") not in sys.path:
        sys.path.insert(0, str(RAIZ / "backend"))
    from app.taskqueue.recipes import escopo_do_alvo  # noqa: PLC0415 - só quem lê o 31.249 paga a importação

    return escopo_do_alvo(bindings, contas)


def _contas_da_etapa(c: sqlite3.Connection, step_id: str) -> list[str]:
    return [str(r[0]) for r in c.execute(
        "SELECT pa.handle FROM steps s JOIN objectives o ON o.id = s.objective_id"
        " JOIN profile_accounts pa ON pa.profile_id = o.profile_id WHERE s.id=? AND pa.handle IS NOT NULL", (step_id,))]


def ler_31_249(c: sqlite3.Connection, op: str) -> Leitura:
    runs = _runs(c, op)
    if not runs:
        return Leitura("sem_caso", "a operação não tem execução")
    casos, divergentes = [], []
    for e in c.execute(f"SELECT id, bindings, template_hash, driven_by, started_at FROM steps"
                       f" WHERE run_id IN ({_em(len(runs))}) AND template_hash IS NOT NULL ORDER BY id", tuple(runs)):
        try:
            ligacoes = json.loads(e["bindings"] or "{}") or {}
        except ValueError:
            continue
        escopo = _escopo(ligacoes, _contas_da_etapa(c, str(e["id"])))
        if escopo is None:
            continue
        outras = []
        for r in c.execute("SELECT id, learned_from_step FROM recipes WHERE step_hash=? AND created_at < ?",
                           (e["template_hash"], e["started_at"] or "9999")):
            origem = c.execute("SELECT bindings FROM steps WHERE id=?", (r["learned_from_step"],)).fetchone()
            try:
                da_receita = _escopo(json.loads(origem[0] or "{}") or {}, _contas_da_etapa(c, str(r["learned_from_step"])))
            except (TypeError, ValueError):
                continue
            if da_receita is not None and da_receita != escopo:
                outras.append(int(r["id"]))
        if not outras:
            continue
        casos.append(str(e["id"]))
        usada = c.execute("SELECT recipe_id FROM attempts WHERE step_id=? AND recipe_id IS NOT NULL ORDER BY number DESC",
                          (e["id"],)).fetchone()
        if e["driven_by"] in ("recipe", "recipe+ai") and usada is not None and int(usada[0]) in outras:
            divergentes.append(str(e["id"]))
    if divergentes:
        return Leitura("divergente", f"{len(divergentes)} etapa(s) conduzidas por receita de outro escopo ({divergentes[:5]})")
    if casos:
        return Leitura("presente", f"{len(casos)} etapa(s) com receita de outro escopo na chave, nenhuma reproduzida "
                                   f"({casos[:5]})")
    return Leitura("sem_caso", "nenhuma etapa com receita de outro escopo na mesma chave")


def ler_31_250(c: sqlite3.Connection, op: str) -> Leitura:
    runs = _runs(c, op)
    if not runs:
        return Leitura("sem_caso", "a operação não tem execução")
    marcadas = _eventos(c, runs, "decision", mensagem="%pelo marcador do catálogo%")
    if marcadas:
        return Leitura("presente", f"{len(marcadas)} nível(is) pelo marcador, 1º juiz dispensado "
                                   f"(eventos {[r['id'] for r in marcadas][:8]})")
    livres = [str(r[0]) for r in c.execute(
        f"SELECT id FROM steps WHERE run_id IN ({_em(len(runs))}) AND side_effect=1 AND capability IS NULL"
        " AND json_extract(postcondition, '$.required_delivery_level') IS NOT NULL ORDER BY id", tuple(runs))]
    if not livres:
        return Leitura("sem_caso", "nenhuma etapa livre com nível de entrega (o QA Messenger)")
    return Leitura("ausente", f"{len(livres)} etapa(s) livre(s) com nível, sem o marcador ({livres[:5]})")


def ler_31_262(c: sqlite3.Connection, op: str) -> Leitura:
    runs = _runs(c, op)
    em_prova = _eventos(c, runs, "decision", dado='%"em_prova": true%')
    if em_prova:
        return Leitura("presente", f"{len(em_prova)} candidata(s) que não se aplicaram seguiram em prova "
                                   f"(eventos {[r['id'] for r in em_prova][:8]})")
    return Leitura("sem_caso", "nenhuma candidata em prova começou fora do estado dela")


ITENS: dict[str, Item] = {
    "31.231": Item("cd2924f792a0c1aed37115b0d61df0b1ef4d243e",
                   "log 'pesquisa reaproveitada do Livro' (sem chamada paga) na operação de assunto repetido",
                   ler_31_231),
    "31.232": Item("dd4e680a09ec5dc536d2f6e5500ed5afc82ca383",
                   "decisão de commit no forte (decide, escalate=efeito, tier>=1) sem imagem com o alvo na árvore",
                   ler_31_232),
    "31.236": Item("7cb8383a1f634ad544b7610be5e03768fd90c269",
                   "operação com parâmetro fixo sem execução em needs_input", ler_31_236),
    "31.237": Item("993c1cbd9dda5bcf2290ecd21e8ddef485e57812",
                   "o 1º plano grava o cache e os planos irmãos leem (cache_read > 0)", ler_31_237),
    "31.238": Item("7092a5298f260ccebc7a3d67ea3f001486e26648",
                   "evento decision kind=rejulgamento_dispensado no efeito comprovado pela prova local", ler_31_238),
    "31.239": Item("36c6b625f95f94df13536b29e7e6089bd3e96351",
                   "decision 'comentário comprovado pela árvore local' no CREATE_COMMENT", ler_31_239),
    "31.242": Item("c9fa33c212f9996d494f2c74f5ccb607bec7c470",
                   "notas de evidência sem o usuário da conta da persona", ler_31_242),
    "31.243": Item("19a48302a5af3fc2613cd62189f17f3c44ce9d90",
                   "eventos, ações e status_detail sem o usuário da conta da persona", ler_31_243),
    "31.244": Item("f334222c55eca6fca8c4e647923c22a7fc240a44",
                   "receita aprendida na operação com o marcador da persona e sem o valor", ler_31_244),
    "31.248": Item("6db7df11468bbb2d860d1f202f1f7651edb40556",
                   "operação sem assunto: log 'pesquisa com o assunto da leitura do alvo'", ler_31_248),
    "31.249": Item("a02c2f392dcc2a2af66adfdea8d647d21574436b",
                   "etapa com receita de outro escopo na chave, conduzida pela IA (nunca por ela)", ler_31_249),
    "31.250": Item("d32d3efb933aa8d90e37a82ffcd9d9ce40abd073",
                   "decision 'nível ... pelo marcador do catálogo' na etapa livre do QA Messenger", ler_31_250),
    "31.262": Item("6b952f935e9020a4e976b6d883f399906b3b3d90",
                   "evento receita_nao_aplicavel com em_prova=true, a candidata sem troca", ler_31_262),
}


# ------------------------------------------------------------------ o commit no ar
def no_ar(commit_do_item: str, commit_no_ar: str, *, repo: Path = RAIZ) -> bool | None:
    """True/False pelo git; None se um dos commits não existe neste clone."""
    r = subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", commit_do_item, commit_no_ar],
                       capture_output=True, text=True, check=False)
    return True if r.returncode == 0 else False if r.returncode == 1 else None


def codigo_mudou(antigo: str, novo: str, *, repo: Path = RAIZ) -> bool:
    """`novo` descende de `antigo` e muda o backend? Só documentação depois do deploy (o checkout do central anda nos
    commits de docs) não é outro código no ar."""
    if no_ar(antigo, novo, repo=repo) is not True:
        return False
    r = subprocess.run(["git", "-C", str(repo), "diff", "--quiet", antigo, novo, "--", "backend"],
                       capture_output=True, text=True, check=False)
    return r.returncode == 1


_DEPLOY = re.compile(r"^## (\d{4}-\d{2}-\d{2}) — Deploy (\d+)\b")
_IMPLANTADO = re.compile(r"\*\*Implantado\*\* às (\d{2}:\d{2})Z: central em `([0-9a-f]{7,40})`")


def deploys(changelog: str) -> list[tuple[str, str, str]]:
    """(instante ISO, número, commit) de cada deploy do CHANGELOG, em ordem de instante."""
    saida: list[tuple[str, str, str]] = []
    atual: tuple[str, str] | None = None
    for linha in changelog.splitlines():
        if m := _DEPLOY.match(linha):
            atual = (m.group(1), m.group(2))
        elif atual is not None and (m := _IMPLANTADO.search(linha)):
            saida.append((f"{atual[0]}T{m.group(1)}:00Z", atual[1], m.group(2)))
            atual = None
    return sorted(saida)


def commit_na_hora(instante: str, linha_do_tempo: Sequence[tuple[str, str, str]]) -> tuple[str, str] | None:
    """O (commit, deploy) no ar no `instante`: o último deploy antes dele."""
    antes = [d for d in linha_do_tempo if d[0] <= instante[:16] + ":00Z"]
    return (antes[-1][2], f"deploy {antes[-1][1]}") if antes else None


def commit_da_saude(url: str = SAUDE) -> str:
    if not url.startswith(("http://", "https://")):
        raise ValueError("a saúde é uma URL http(s)")
    with urllib.request.urlopen(url, timeout=10) as resp:      # noqa: S310 - URL local fixa ou dada pelo operador
        return str(json.load(resp).get("commit") or "")


# ------------------------------------------------------------------ o roteiro
def marcar(c: sqlite3.Connection, op: str, commit: str, *, origem_do_commit: str = "",
           ancestral: Callable[[str, str], bool | None] = no_ar, agora: str | None = None,
           maquina: str | None = None, itens: dict[str, Item] | None = None) -> dict[str, object]:
    c.row_factory = sqlite3.Row
    quando = agora or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    host = maquina or socket.gethostname()
    reais: list[dict[str, object]] = []
    pendentes: list[dict[str, object]] = []
    divergencias: list[dict[str, object]] = []
    por_item: dict[str, dict[str, str]] = {}
    for chave, item in (itens or ITENS).items():
        tem = ancestral(item.commit, commit)
        if not tem:
            motivo = (f"o commit {item.commit[:8]} do item não estava no central ({commit[:8]})" if tem is False
                      else f"commit {item.commit[:8]} ou {commit[:8]} desconhecido neste clone")
            pendentes.append({"id": chave, "proof": "not_run", "achado": "nao_no_ar", "motivo": motivo})
            por_item[chave] = {"achado": "nao_no_ar", "esperado": item.esperado, "detalhe": motivo}
            continue
        leitura = item.ler(c, op)
        por_item[chave] = {"achado": leitura.achado, "esperado": item.esperado, "detalhe": leitura.detalhe}
        base = f"{quando}, {host}, central {commit[:8]}, operação {op}: {leitura.detalhe}"
        if leitura.achado == "presente":
            reais.append({"id": chave, "status": "implemented", "proof": "real", "evidence": base})
        elif leitura.achado == "divergente":
            divergencias.append({"id": chave, "proof": "real", "achado": "divergente", "evidence": base})
        else:
            pendentes.append({"id": chave, "proof": "not_run", "achado": leitura.achado,
                              "motivo": f"operação {op}: {leitura.detalhe}"})
    return {"resultados": [{"grupo": "aprendizado", "items": reais}] if reais else [], "pendentes": pendentes,
            "divergencias": divergencias, "por_item": por_item, "commit_no_ar": commit,
            "origem_do_commit": origem_do_commit, "operacao": op, "lido_em": quando}


def tabela(saida: dict[str, object]) -> str:
    linhas = [f"operação {saida['operacao']} · central {str(saida['commit_no_ar'])[:8]} ({saida['origem_do_commit']}) · "
              f"lido em {saida['lido_em']}"]
    for chave, v in dict(saida["por_item"]).items():  # type: ignore[call-overload]
        linhas.append(f"{chave}  {v['achado']:<10}  esperado: {v['esperado']}")
        linhas.append(f"{'':8}{'':12}achado:   {v['detalhe']}")
    return "\n".join(linhas)


def gravar_por_item(saida: dict[str, object], pasta: Path) -> list[Path]:
    """Um JSON por item, no formato do `aplicar`: `resultados` só com o `real` do item presente."""
    pasta.mkdir(parents=True, exist_ok=True)
    reais = {i["id"]: i for g in saida["resultados"] for i in g["items"]}  # type: ignore[attr-defined]
    outras = {i["id"]: (k, i) for k in ("pendentes", "divergencias") for i in saida[k]}  # type: ignore[attr-defined]
    feitos = []
    for chave in saida["por_item"]:  # type: ignore[attr-defined]
        corpo: dict[str, object] = {"operacao": saida["operacao"], "commit_no_ar": saida["commit_no_ar"],
                                    "lido_em": saida["lido_em"]}
        if chave in reais:
            corpo["resultados"] = [{"grupo": "aprendizado", "items": [reais[chave]]}]
        else:
            k, linha = outras[chave]
            corpo["resultados"] = []
            corpo[k] = [linha]
        caminho = pasta / f"aprendizado-real-{chave.replace('.', '-')}.json"
        caminho.write_text(json.dumps(corpo, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        feitos.append(caminho)
    return feitos


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--operacao", required=True)
    ap.add_argument("--banco", default=str(RAIZ / "data" / "poc.sqlite3"))
    ap.add_argument("--commit", help="o commit no ar durante a operação; sem ele, o do CHANGELOG, e então o da saúde")
    ap.add_argument("--saude", default=SAUDE)
    ap.add_argument("--changelog", default=str(RAIZ / "CHANGELOG.md"))
    ap.add_argument("--saida", help="pasta para um JSON por item (aprendizado-real-31-NNN.json)")
    ap.add_argument("--tabela", action="store_true", help="imprime a tabela legível em vez do JSON")
    a = ap.parse_args(argv)
    c = sqlite3.connect(f"file:{Path(a.banco).as_posix()}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    try:
        linha = c.execute("SELECT created_at FROM operacoes WHERE id=?", (a.operacao,)).fetchone()
        if linha is None:
            print(f"a operação {a.operacao} não existe neste banco", file=sys.stderr)
            return 2
        commit, origem = a.commit, "--commit"
        if not commit:
            texto = Path(a.changelog).read_text(encoding="utf-8") if Path(a.changelog).is_file() else ""
            achado = commit_na_hora(str(linha[0]), deploys(texto))
            commit, origem = achado if achado else (commit_da_saude(a.saude), "saúde")
            if achado:
                # O CHANGELOG ganha o deploy DEPOIS dele: a operação logo após um deploy ainda não registrado sairia com
                # o commit do anterior, e os itens novos como `nao_no_ar`. A saúde diferente avisa (sem trocar nada).
                try:
                    saude = commit_da_saude(a.saude)
                except (OSError, ValueError):
                    saude = ""
                if saude and not saude.startswith(commit[:7]) and codigo_mudou(commit, saude):
                    origem += (f"; a saúde diz {saude[:8]}, mais novo: se o deploy dele é anterior à operação e o "
                               "CHANGELOG ainda não o tem, use --commit")
        if not commit:
            print("sem o commit no ar (nem --commit, nem CHANGELOG, nem saúde)", file=sys.stderr)
            return 2
        saida = marcar(c, a.operacao, commit, origem_do_commit=origem)
    finally:
        c.close()
    if a.saida:
        gravar_por_item(saida, Path(a.saida))
    print(tabela(saida) if a.tabela else json.dumps(saida, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
