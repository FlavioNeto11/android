"""Reconciliação total dos cartões dos três quadros do Trello com o estado do plano-100 (regra de 06/10/2026).

Uso (no checkout central, com a venv do backend, que traz a chave e o token do Trello):

    backend/.venv/Scripts/python.exe .claude/trello/reconciliar.py            # só relata o que mudaria
    backend/.venv/Scripts/python.exe .claude/trello/reconciliar.py --aplicar  # move e escreve a linha de prova

Quando rodar: a cada espelho de deploy e a cada `claude-plan-100.py aplicar`. É idempotente: depois de aplicar, a rodada
seguinte não acha nada para mudar. A regra e a máquina de estados estão em docs/dominios/canais.md (C-28).

Só move cartão de item do plano (nome com id `N.N`) do quadro Execução. Cartão que espera o dono ("Espera você") e item sem
estado no plano nunca se movem: são listados. O Histórico e o Programa são conferidos e só relatados (item que o plano não
dá como feito; métrica, custo ou risco sem leitura datada nos últimos três dias). Nada de segredo,
nome de persona ou dado de conta entra na linha escrita: ela vem do estado do plano, passado por `redacao.redigir`.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parent.parent
sys.path.insert(0, str(AQUI))

MARCA = "**Saída de "
SEPARADOR = "\n\n---\n\n"
BRASILIA = timezone(timedelta(hours=-3))
CARTAO_DO_DONO = ("espera_voce",)

# papel de cada lista do quadro Execução, achado pelo nome (os nomes levam emoji e mudam de texto)
PAPEIS = (("Concluído", "concluido"), ("Em validação", "em_validacao"), ("Em execução", "em_execucao"),
          ("Próximas", "proximas"), ("Aguardando", "aguardando"), ("Espera você", "espera_voce"),
          ("Bloqueado", "bloqueado"), ("Perguntas respondidas", "perguntas_respondidas"),
          ("Perguntas para você", "perguntas"), ("Como ler", "como_ler"), ("Central", "central"))
ROTULO = {"concluido": "Concluído", "em_validacao": "Em validação", "em_execucao": "Em execução",
          "proximas": "Próximas", "aguardando": "Aguardando data ou evento", "bloqueado": "Bloqueado",
          "historico": "o Histórico", "espera_voce": "Espera você"}


@dataclass
class Acao:
    """O que fazer com um cartão: `mover` (para outra lista), `marcar` (só a linha) ou `listar` (nada, só o relato)."""
    cartao: str
    nome: str
    tipo: str
    de: str
    para: str | None
    linha: str
    motivo: str
    lista_do_historico: str | None = None


@dataclass
class Relatorio:
    acoes: list[Acao] = field(default_factory=list)

    def contagem(self) -> dict[str, int]:
        c: dict[str, int] = {}
        for a in self.acoes:
            c[a.tipo] = c.get(a.tipo, 0) + 1
        return c


def inicio_da_semana(agora: datetime) -> str:
    """Segunda-feira 00:00 em Brasília, em UTC ISO (a semana do quadro Execução vira aí)."""
    local = agora.astimezone(BRASILIA)
    segunda = (local - timedelta(days=local.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return segunda.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def id_do_item(nome: str) -> str | None:
    m = re.match(r"^(?:\[A\]\s*)?(?:🙋\s*)?(?:#\d+\s*·\s*)?(\d+\.\d+)(?![\d.])", nome)
    return m.group(1) if m else None


def papel_da_lista(nome: str) -> str:
    for trecho, papel in PAPEIS:
        if trecho in nome:
            return papel
    return "outra"


def deploys_do_changelog(texto: str) -> list[int]:
    return sorted({int(n) for n in re.findall(r"^## \d{4}-\d{2}-\d{2} — Deploy (\d+)\b", texto, flags=re.M)})


def deploy_do_item(quando: str | None, suite: int | None, horas: dict[int, str]) -> int | None:
    """Número do deploy em que o item entrou: 0 = antes do primeiro deploy conhecido; None = ainda não implantado.

    `suite` vem do commit "merge: ID (...) na suíte N"; sem ele, vale a hora em que o item foi classificado contra a hora
    de registro de cada deploy (`horas`: n → ISO UTC).
    """
    if suite is not None:
        return suite if suite in horas else None
    q = (quando or "")[:19]
    if not q or not horas:
        return None
    ordem = sorted(horas)
    if q < horas[ordem[0]][:19]:
        return 0
    for n in ordem:
        if q < horas[n][:19]:
            return n
    return None


def deploys_por_commit(texto: str) -> dict[str, int]:
    """Prefixo de 8 caracteres do commit que o central rodou → número do deploy, lido da linha "central em `sha`" de cada
    seção `## … — Deploy N` do CHANGELOG."""
    achados: dict[str, int] = {}
    for m in re.finditer(r"^## \d{4}-\d{2}-\d{2} — Deploy (\d+)\b.*?(?=^## |\Z)", texto, flags=re.M | re.S):
        c = re.search(r"central em `([0-9a-f]{7,40})`", m.group(0))
        if c:
            achados.setdefault(c.group(1)[:8], int(m.group(1)))
    return achados


def deploy_pela_evidencia(item: dict, por_commit: dict[str, int], ultimo: int = 0) -> int | None:
    """O item foi classificado depois do registro do deploy, mas a evidência real diz onde foi provado: o commit do
    central ("central 7154d7cf") ou o número ("deploy 32", só se já houve deploy até esse número). Vale o menor."""
    ev = str(item.get("evidence") or "")
    ns = [n for sha, n in por_commit.items() if sha in ev]
    ns += [int(n) for n in re.findall(r"\bdeploy (\d+)\b", ev) if int(n) <= ultimo]
    return min(ns) if ns else None


def deploy_pelo_git(pid: str, por_commit: dict[str, int], raiz: Path | None = None) -> int | None:
    """O primeiro commit que pôs o cabeçalho do item no CHANGELOG decide: vale o menor deploy cujo commit do central o
    contém. É a fonte para o item classificado na mesma rodada do registro do deploy, quando a hora engana."""
    raiz = raiz or RAIZ
    cabecalho = "^## [0-9-]*[0-9] — " + re.escape(pid) + "[^0-9.]"
    r = subprocess.run(["git", "-C", str(raiz), "log", "origin/main", "--reverse", "--format=%H", "-G" + cabecalho, "--",
                        "CHANGELOG.md"], capture_output=True, text=True, encoding="utf-8", check=False).stdout.split()
    if not r:
        return None
    ordem = sorted(por_commit.items(), key=lambda kv: kv[1])
    for sha, n in ordem:
        ok = subprocess.run(["git", "-C", str(raiz), "merge-base", "--is-ancestor", r[0], sha], check=False,
                            capture_output=True).returncode == 0
        if ok:
            # no primeiro deploy com commit conhecido não dá para separar "entrou nele" de "já estava antes": o sinal
            # negativo diz "neste deploy ou num anterior"
            return -n if n == ordem[0][1] else n
    return None


def _provas(item: dict) -> list[str]:
    ev = str(item.get("evidence") or "") + " " + " ".join(str(x) for x in (item.get("testes") or []))
    achados = re.findall(r"[\w./-]*(?:test|spec)[\w./-]*\.(?:py|tsx?|ps1)(?:::[\w*\[\]-]+)?", ev)
    return list(dict.fromkeys(achados))[:2]


def _real(item: dict) -> tuple[str, list[str]]:
    ev = str(item.get("evidence") or "")
    d = re.search(r"\b(\d{1,2}/\d{1,2}(?:/\d{4})?)", ev)
    data = d.group(1) if d else str(item.get("quando", ""))[:10]
    ids = list(dict.fromkeys(re.findall(r"\b(r-\d{8,14}-\w+|trn-\w{6,}|f-[0-9a-f]{8,}|lote:[\w.:-]+)\b", ev)))
    return data, ids[:3]


def _limpa(texto: str, redigir: Callable[[str], str]) -> str:
    t = redigir(texto).strip().split("\n")[0]
    t = re.sub(r"\s*\(\[persona\]\)", "", t).replace("[persona]", "persona").replace("[conta]", "uma conta")
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) > 230:
        corte = max(t.rfind("; ", 0, 230), t.rfind(". ", 0, 230))
        t = t[:corte] if corte > 80 else t[:230].rsplit(" ", 1)[0] + " (…)"
    return t.rstrip(".;")


def linha_de_prova(pid: str, item: dict, deploy: int, primeiro: int) -> str:
    rotulo = (f"um deploy anterior ao {primeiro}" if deploy == 0 else f"o deploy {-deploy} ou um anterior" if deploy < 0
              else f"o deploy {deploy}")
    if item.get("proof") == "real":
        data, ids = _real(item)
        return (f"prova real ({data}" + (f", {', '.join(ids)}" if ids else "")
                + f"; evidência no estado do plano, item {pid}); no ar desde {rotulo}")
    ts = _provas(item)
    return "prova simulada (" + (", ".join(ts) if ts else f"ver o item {pid} no estado do plano") + f"), no ar desde {rotulo}"


def nivel_da_prova(pid: str, item: dict) -> str:
    """"prova real (data, ids; …)" ou "prova simulada (arquivo::teste)", como na linha dos itens concluídos."""
    if item.get("proof") == "real":
        data, ids = _real(item)
        return f"prova real ({data}" + (f", {', '.join(ids)}" if ids else "") + f"; evidência no estado do plano, item {pid})"
    ts = _provas(item)
    return "prova simulada (" + (", ".join(ts) if ts else f"ver o item {pid} no estado do plano") + ")"


def frase_da_evidencia(item: dict, redigir: Callable[[str], str]) -> str | None:
    """A oração da evidência do item que começa com "Falta", "Faltam" ou "faltam", sem o ponto final."""
    m = re.search(r"\b([Ff]altam?\b.*?)(?:\.(?=\s|$)|;|\n|$)", str(item.get("evidence") or ""))
    if not m:
        return None
    t = _limpa(m.group(1), redigir)
    return t[0].lower() + t[1:] if t else None


def frase_do_cartao(c: dict) -> str | None:
    """A frase do que falta que já está na linha do topo do cartão (`; falta: …`), sem o ponto final."""
    primeira = str(c.get("desc", "")).split(SEPARADOR, 1)[0]
    if not primeira.startswith(MARCA):
        return None
    m = re.search(r";\s*(falta[^\n]*)$", primeira.strip())
    return m.group(1).rstrip(".;").strip() if m else None


def linha_do_parcial(pid: str, item: dict, c: dict, redigir: Callable[[str], str]) -> str:
    """Nível da prova + a frase do que falta: a da evidência do item; senão a que o cartão já tem; senão o campo de
    detalhe, a nota ou o bloqueio do estado."""
    frase = frase_da_evidencia(item, redigir) or frase_do_cartao(c)
    if frase is None:
        for k in ("status_detail", "status_note", "blocker"):
            if item.get(k):
                frase = "falta: " + _limpa(str(item[k]), redigir)
                break
    return f"item parcial no plano, com {nivel_da_prova(pid, item)}; " + (frase or "o estado do plano não diz o que falta")


def _linha_desatualizada(c: dict, linha: str) -> bool:
    """O cartão não tem a linha do plano: sem a marca, ou com uma linha que não é esta (a prova mudou, por exemplo)."""
    primeira = str(c.get("desc", "")).split(SEPARADOR, 1)[0]
    return not primeira.startswith(MARCA) or linha[1:] not in primeira


def _tem_linha(c: dict) -> bool:
    return str(c.get("desc", "")).startswith(MARCA)


def _deploy_mudou(c: dict, linha: str) -> bool:
    """A linha do cartão concluído diz "no ar desde" um deploy diferente do que o script calcula agora."""
    primeira = str(c.get("desc", "")).split(SEPARADOR, 1)[0]
    pat = r"no ar desde (o deploy \d+(?: ou um anterior)?|um deploy anterior ao \d+)"
    antes, agora = re.search(pat, primeira), re.search(pat, linha)
    return bool(primeira.startswith(MARCA) and antes and agora and antes.group(1) != agora.group(1))


def _prova_subiu(c: dict, linha: str) -> bool:
    """A linha do cartão diz "prova simulada" e o plano agora tem prova real: a linha de prova tem de ser refeita."""
    primeira = str(c.get("desc", "")).split(SEPARADOR, 1)[0]
    return primeira.startswith(MARCA) and "Prova simulada" in primeira and linha.startswith("prova real")


def _lista_da_fase(pid: str, listas: dict[str, str]) -> str | None:
    n = int(pid.split(".")[0])
    for nome, lid in listas.items():
        m = re.search(r"Fases?\s+(\d+)(?:\s*[–-]\s*(\d+))?", nome)
        if m and int(m.group(1)) <= n <= int(m.group(2) or m.group(1)):
            return lid
    return None


def decidir(cartoes: list[dict], estado: dict, *, agora: datetime, horas: dict[int, str],
            suite_de: Callable[[str], int | None], listas_do_historico: dict[str, str],
            redigir: Callable[[str], str] = lambda t: t, citados: dict[str, int] | None = None,
            por_commit: dict[str, int] | None = None,
            deploy_git: Callable[[str], int | None] | None = None) -> Relatorio:
    """Compara cada cartão do quadro Execução com o estado do plano e devolve o que mudar.

    `cartoes`: [{"id", "nome", "lista" (nome da lista), "desc"}]. `listas_do_historico`: nome da lista de fase → id.
    """
    itens = estado.get("itens", {})
    semana = inicio_da_semana(agora)
    primeiro = min(horas) if horas else 0
    rel = Relatorio()
    for c in cartoes:
        nome, atual = c["nome"], papel_da_lista(c["lista"])
        pid = id_do_item(nome)
        if not pid:
            continue
        it = itens.get(pid)
        if it is None:
            if atual in ("concluido", "em_validacao"):
                rel.acoes.append(Acao(c["id"], nome, "listar", atual, None, "", "dado como feito, mas sem estado no plano"))
            continue
        if atual in CARTAO_DO_DONO:
            rel.acoes.append(Acao(c["id"], nome, "listar", atual, None, "", "espera o dono; não se move"))
            continue
        if atual in ("como_ler", "central", "perguntas", "perguntas_respondidas"):
            continue
        st = it.get("status")
        if st == "blocked":
            if atual != "bloqueado":
                motivo = it.get("blocker") or it.get("status_detail") or "sem motivo no estado"
                rel.acoes.append(Acao(c["id"], nome, "mover", atual, "bloqueado",
                                      "item bloqueado no plano: " + _limpa(str(motivo), redigir), "bloqueado no plano"))
        elif st == "partial":
            linha = linha_do_parcial(pid, it, c, redigir)
            if atual in ("concluido", "proximas"):
                rel.acoes.append(Acao(c["id"], nome, "mover", atual, "em_validacao", linha, "parcial no plano"))
            elif _linha_desatualizada(c, linha):
                motivo = "parcial sem a linha do que falta" if not _tem_linha(c) else "a linha do parcial mudou (prova ou o que falta)"
                rel.acoes.append(Acao(c["id"], nome, "marcar", atual, None, linha, motivo))
        elif st == "implemented":
            n = deploy_do_item(it.get("quando"), suite_de(pid) or (citados or {}).get(pid), horas)
            sem_fonte_forte = not suite_de(pid) and pid not in (citados or {})
            if deploy_git and sem_fonte_forte and (n is None or n == max(horas, default=0)):
                n = deploy_git(pid) or n          # a hora do registro engana quando o item entra na mesma rodada dele
            if n is None and it.get("proof") == "real":
                n = deploy_pela_evidencia(it, por_commit or {}, max(horas, default=0))
            if n is None:
                if atual != "em_validacao":
                    rel.acoes.append(Acao(c["id"], nome, "mover", atual, "em_validacao",
                                          "implementado no plano, mas ainda não implantado: aguarda o próximo deploy",
                                          "implementado e não implantado"))
                continue
            linha = linha_de_prova(pid, it, n, primeiro)
            fase = _lista_da_fase(pid, listas_do_historico) if str(it.get("quando") or "")[:19] < semana else None
            if fase:
                rel.acoes.append(Acao(c["id"], nome, "mover", atual, "historico", linha, "feito em semana anterior", fase))
            elif atual != "concluido":
                rel.acoes.append(Acao(c["id"], nome, "mover", atual, "concluido", linha, "implementado e implantado"))
            elif not _tem_linha(c) or _prova_subiu(c, linha) or _deploy_mudou(c, linha):
                motivo = ("concluído sem a linha de prova" if not _tem_linha(c)
                          else "o plano ganhou prova real" if _prova_subiu(c, linha) else "o deploy do item mudou")
                rel.acoes.append(Acao(c["id"], nome, "marcar", atual, None, linha, motivo))
    return rel


LISTAS_COM_LEITURA = ("Métricas", "Custos", "Riscos")
DIAS_SEM_LEITURA = 3


def auditar_historico_e_programa(historico: list[dict], programa: list[dict], estado: dict, *,
                                 agora: datetime) -> Relatorio:
    """Os outros dois quadros: só relata, nunca move. Histórico com item que o plano não dá como feito, e cartão de
    métrica, custo ou risco sem leitura há mais de três dias (`acao`: data da última atividade do cartão, ISO UTC)."""
    itens = estado.get("itens", {})
    rel = Relatorio()
    for c in historico:
        pid = id_do_item(c["nome"])
        if not pid:
            continue
        it = itens.get(pid)
        if it is None:
            rel.acoes.append(Acao(c["id"], c["nome"], "listar", "historico", None, "", "no Histórico, mas sem estado no plano"))
        elif it.get("status") != "implemented":
            rel.acoes.append(Acao(c["id"], c["nome"], "listar", "historico", None, "",
                                  f"no Histórico, mas o plano diz {it.get('status')}"))
    for c in programa:
        if not any(t in c["lista"] for t in LISTAS_COM_LEITURA) or "Como ler" in c["nome"]:
            continue
        data = leitura_datada(str(c.get("desc", "")), agora)
        if data is None or agora - data > timedelta(days=DIAS_SEM_LEITURA):
            rel.acoes.append(Acao(c["id"], c["nome"], "listar", "programa", None, "",
                                  "sem leitura datada" if data is None else f"leitura de {data:%d/%m}"))
    return rel


def leitura_datada(desc: str, agora: datetime) -> datetime | None:
    """Data da linha `**Leitura de DD/MM HH:MMZ ...` no topo do cartão de métrica, custo ou risco (UTC)."""
    m = re.match(r"\*\*Leitura de (\d{2})/(\d{2}) (\d{2}):(\d{2})Z", desc)
    if not m:
        return None
    d, mes, h, mi = (int(x) for x in m.groups())
    try:
        return datetime(agora.year, mes, d, h, mi, tzinfo=timezone.utc)
    except ValueError:
        return None


def topo_da_linha(acao: Acao, selo: str) -> str:
    """Primeira linha do cartão. `selo` é a data e hora UTC já formatadas; quem chama lê `date -u` na hora."""
    corpo = acao.linha[0].upper() + acao.linha[1:] if acao.linha else ""
    de = ROTULO.get(acao.de, acao.de)
    if acao.tipo == "mover":
        return f"{MARCA}{de} ({selo}, regra de 06/10):** vai para {ROTULO.get(acao.para or '', '')}. {corpo}.{SEPARADOR}"
    return f"{MARCA}estado do plano ({selo}):** fica em {de}. {corpo}.{SEPARADOR}"


def com_linha(desc: str, topo: str) -> str:
    """Troca a linha de uma passada anterior em vez de empilhar."""
    if desc.startswith(MARCA) and SEPARADOR in desc:
        desc = desc.split(SEPARADOR, 1)[1]
    return topo + desc


# ---- fontes (Git e plano) e rede -----------------------------------------------------------------------------------

def suite_do_commit(pid: str, raiz: Path | None = None) -> int | None:
    raiz = raiz or RAIZ
    r = subprocess.run(["git", "-C", str(raiz), "log", "origin/main", "--format=%s", "-E", "--grep",
                        r"^merge: " + re.escape(pid) + r" \(.*na suíte [0-9]+"], capture_output=True, text=True,
                       encoding="utf-8", check=False).stdout
    ns = [int(m) for m in re.findall(r"na suíte (\d+)", r)]
    return min(ns) if ns else None


def ids_citados_por_deploy(texto: str) -> dict[str, int]:
    """Id de item → menor deploy cuja seção do CHANGELOG o cita. Vale quando o item não tem commit "merge: … na suíte N"
    (entrou no deploy junto de outro e foi classificado depois do registro dele)."""
    achados: dict[str, int] = {}
    for m in re.finditer(r"^## \d{4}-\d{2}-\d{2} — Deploy (\d+)\b.*?(?=^## |\Z)", texto, flags=re.M | re.S):
        for pid in set(re.findall(r"\b(\d{1,2}\.\d{1,3})\b", m.group(0))):
            achados[pid] = min(achados.get(pid, 10**6), int(m.group(1)))
    return achados


def horas_dos_deploys(raiz: Path | None = None) -> dict[int, str]:
    raiz = raiz or RAIZ
    texto = (raiz / "CHANGELOG.md").read_text(encoding="utf-8")
    saida: dict[int, str] = {}
    for n in deploys_do_changelog(texto):
        r = subprocess.run(["git", "-C", str(raiz), "log", "--format=%cI", f"-G^## [0-9-]*[0-9] — Deploy {n}[^0-9]", "--", "CHANGELOG.md"],
                           capture_output=True, text=True, encoding="utf-8", check=False).stdout.split()
        if r:
            saida[n] = datetime.fromisoformat(r[-1]).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    return saida


async def _cartoes(cl, board: str) -> list[dict]:
    ls = await cl._pedir("GET", f"/1/boards/{board}/lists", params={"fields": "name", "filter": "open"})
    listas = {x["id"]: x["name"] for x in ls}
    cs = await cl._pedir("GET", f"/1/boards/{board}/cards", params={"fields": "name,idList,desc", "filter": "open"})
    return [{"id": c["id"], "nome": c["name"], "lista": listas.get(c["idList"], ""), "desc": c.get("desc", "")} for c in cs]


async def _principal(aplicar: bool) -> int:
    from app.config import EnvSettings  # noqa: PLC0415 - só no modo de rede
    from app.modules.avisos.adapters.trello import ClienteTrello  # noqa: PLC0415
    from redacao import redigir  # noqa: PLC0415
    est = json.loads((AQUI / "estrutura.json").read_text(encoding="utf-8"))["quadros"]
    ident = {q: v["id"].rsplit("/", 1)[-1] for q, v in est.items()}
    e = EnvSettings()
    cl = ClienteTrello(e.trello_api_key.get_secret_value().strip(), e.trello_token.get_secret_value().strip())
    ls = await cl._pedir("GET", f"/1/boards/{ident['execucao']}/lists", params={"fields": "name", "filter": "open"})
    listas = {x["id"]: x["name"] for x in ls}
    cs = await cl._pedir("GET", f"/1/boards/{ident['execucao']}/cards",
                         params={"fields": "name,idList,desc", "filter": "open"})
    cartoes = [{"id": c["id"], "nome": c["name"], "lista": listas.get(c["idList"], ""), "desc": c.get("desc", "")} for c in cs]
    hl = await cl._pedir("GET", f"/1/boards/{ident['historico']}/lists", params={"fields": "name", "filter": "open"})
    hist = {x["name"]: x["id"] for x in hl}
    estado = json.loads((RAIZ / ".claude/plano-100/estado.json").read_text(encoding="utf-8"))
    agora = datetime.now(timezone.utc)
    changelog = (RAIZ / "CHANGELOG.md").read_text(encoding="utf-8")
    rel = decidir(cartoes, estado, agora=agora, horas=horas_dos_deploys(), suite_de=suite_do_commit,
                  listas_do_historico=hist, redigir=redigir, citados=ids_citados_por_deploy(changelog),
                  por_commit=deploys_por_commit(changelog),
                  deploy_git=lambda pid: deploy_pelo_git(pid, deploys_por_commit(changelog)))
    outros = auditar_historico_e_programa(await _cartoes(cl, ident["historico"]), await _cartoes(cl, ident["programa"]),
                                          estado, agora=agora)
    print("Execução:", len(cartoes), "cartões; ações:", rel.contagem() or "nenhuma")
    print("Histórico e Programa (só relato):", outros.contagem() or "nenhuma")
    for a in outros.acoes:
        print(f"  listar {a.de} | {a.nome[:60]} | {a.motivo}")
    for a in rel.acoes:
        print(f"  {a.tipo:7} {a.de} → {a.para or '-'} | {a.nome[:60]} | {a.motivo}")
    if not aplicar:
        return 0
    selo = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%MZ")
    lista_do_papel = {papel_da_lista(n): lid for lid, n in listas.items()}
    falhas = []
    for a in rel.acoes:
        if a.tipo == "listar":
            continue
        try:
            d = (await cl._pedir("GET", f"/1/cards/{a.cartao}", params={"fields": "desc"}))["desc"]
            corpo: dict = {"desc": com_linha(d, topo_da_linha(a, selo))}
            if a.tipo == "mover":
                if a.para == "historico":
                    corpo.update({"idBoard": ident["historico"], "idList": a.lista_do_historico})
                else:
                    corpo["idList"] = lista_do_papel[a.para or ""]
            await cl._pedir("PUT", f"/1/cards/{a.cartao}", corpo=corpo)
        except Exception as ex:  # noqa: BLE001 - relata e segue com os outros cartões
            falhas.append((a.nome[:40], type(ex).__name__))
    print("falhas:", falhas or "nenhuma")
    return 1 if falhas else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--aplicar", action="store_true", help="grava no Trello (sem isto só relata)")
    a = p.parse_args(argv)
    sys.path.insert(0, str(RAIZ / "backend"))
    return asyncio.run(_principal(a.aplicar))


if __name__ == "__main__":
    raise SystemExit(main())
