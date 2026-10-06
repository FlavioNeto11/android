"""28.64: o marco do deploy gerado do CHANGELOG (cartão em Programa › Marcos e deploys) e as leituras M8 e M9.

Uso (host da Canais, com as chaves do checkout central):
    python .claude/trello/marco.py [--deploy NN] [--aplicar] [--offline]

- sem `--deploy`, o registro mais recente do CHANGELOG ("## AAAA-MM-DD — Deploy NN (...)");
- sem `--aplicar` só imprime o que faria (a leitura de rede do ensaio é só GET: lista Marcos e `/api/instances`);
- `--offline` não toca em rede nenhuma (imprime o marco e o M9; o M8 fica de fora).

O texto vem SÓ do CHANGELOG: nada é inventado. O trecho "Para quem não é técnico" é uma frase genérica montada da lista de
itens e vem marcada "(gerado do CHANGELOG; a Canais pode editar)". Tudo passa por `redacao.redigir` antes de sair.
As funções de extração e de montagem são puras (testadas em `test_marco.py`); a rede só existe em `_principal`.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parents[1]
sys.path.insert(0, str(AQUI))
#: as chaves do Trello moram no checkout central (por instalação, fora do Git); o worktree não as tem
BACKEND_CENTRAL = Path(r"C:\git\android\backend")
API_CENTRAL = "http://localhost:8000/api/instances"

QUADRO_PROGRAMA = "6ac13aeffc0ac80f9dc4edb3"
LISTA_MARCOS = "6ac13b26ef76296b0f9d4f58"
CARTAO_M8 = "6ac14002154c95e90441837c"
CARTAO_M9 = "6ac1401a1b2a40845a9e71ea"

NOTA_GERADO = "(gerado do CHANGELOG; a Canais pode editar)"
SEPARADOR = "\n\n---\n\n"
PREFIXO_LEITURA = "**Leitura de "

_CABECALHO = re.compile(r"^## (\d{4}-\d{2}-\d{2}) — Deploy (\d+)(?: \((.*)\))?\s*$")
_HORA = re.compile(r"(?:Implantado|no ar)[^\d\n]{0,12}(\d{1,2}:\d{2})Z")
_COMMIT = re.compile(r"central em `([0-9a-f]{7,40})`")
_BACKUP = re.compile(r"backup `(\d{8}-\d{6})`")
_TAG = re.compile(r"\btag (deploy-[\w-]+)")
_AGENTE = re.compile(r"`(0\.\d+\.\d+\+[0-9a-f]{6,40})`")
#: o número seguinte só vale com 2 ou 3 dígitos e fora de "8 pontas"/"16 pontas" (a contagem de pontas vem logo depois)
_MIGRACOES = re.compile(r"migra(?:ção|ções) (\d+(?:\s*\([^()]*\))?(?:(?:,\s*| e )\d{2,3}(?!\d|\s+pontas)(?:\s*\([^()]*\))?)*)")
#: ID de plano (31.155, 28.61), ADR e C-número; "v1.95" (versão do contrato) e "0.1.0" (versão do agente) ficam de fora
_ITEM = re.compile(r"(?<![\w.+/-])(\d{1,2}\.\d{1,3})(?![\w]|\.\d)|\b(ADR-\d+)\b|\b(C\d{1,3})\b")


@dataclass
class Deploy:
    numero: int
    data: str  # AAAA-MM-DD
    resumo: str = ""
    hora: str | None = None  # HH:MM, em UTC
    commit_longo: str | None = None
    migracoes: list[str] = field(default_factory=list)
    backup: str | None = None
    tag: str | None = None
    agente: str | None = None
    itens: list[str] = field(default_factory=list)
    real: str | None = None
    simulated: str | None = None
    not_run: str | None = None
    contagens: dict[str, str] = field(default_factory=dict)

    @property
    def commit(self) -> str | None:
        return self.commit_longo[:8] if self.commit_longo else None


# --------------------------------------------------------------------------------------- extração (puro)
def _secoes(texto: str) -> list[tuple[int, list[str]]]:
    """Cada registro de deploy do CHANGELOG: (número, linhas da seção), na ordem do arquivo (o mais novo vem primeiro)."""
    saida: list[tuple[int, list[str]]] = []
    atual: list[str] | None = None
    for linha in texto.splitlines():
        if linha.startswith("## "):
            atual = None
            m = _CABECALHO.match(linha)
            if m:
                atual = [linha]
                saida.append((int(m.group(2)), atual))
        elif atual is not None:
            atual.append(linha)
    return saida


def achar_secao(texto: str, numero: int | None = None) -> list[str] | None:
    """As linhas da seção do deploy `numero` (ou do mais recente, na ordem do arquivo). `None` se não existir."""
    for n, linhas in _secoes(texto):
        if numero is None or n == numero:
            return linhas
    return None


def numero_mais_recente(texto: str) -> int | None:
    secoes = _secoes(texto)
    return secoes[0][0] if secoes else None


def _marcadores(linhas: list[str]) -> list[str]:
    return [ln[2:].strip() for ln in linhas if ln.startswith("- ")]


def _resto(linha: str | None, marca: str) -> str | None:
    """O que vem depois da marca (`real`, `simulated`, `not_run`) na linha do marcador."""
    if not linha:
        return None
    i = linha.find(marca)
    return linha[i + len(marca):].strip() if i >= 0 else None


def migracoes_de(texto: str) -> list[str]:
    """Números das migrações do registro; vazio quando é "sem migração" ou nada é dito. Só olha o trecho de "Implantado"."""
    m = _MIGRACOES.search(texto)
    if not m:
        return []
    sem_parenteses = re.sub(r"\([^()]*\)", "", m.group(1))
    return re.findall(r"\d+", sem_parenteses)


def itens_de(texto: str) -> list[str]:
    """IDs citados (do plano, ADR e C-número), sem repetir, na ordem em que aparecem. Se há "Itens:" só vale o que vem depois."""
    i = texto.find("Itens:")
    trecho = texto[i + len("Itens:"):] if i >= 0 else texto
    vistos: list[str] = []
    for m in _ITEM.finditer(trecho):
        item = next(g for g in m.groups() if g)
        if item not in vistos:
            vistos.append(item)
    return vistos


def _numero(s: str) -> str:
    return " + ".join(re.findall(r"\d+", s))


def contagens_de(simulated: str | None) -> dict[str, str]:
    """As contagens da suíte na linha `simulated`. Só entra a que está no texto; o resto some (nunca vira 0)."""
    t = simulated or ""
    achados: dict[str, str] = {}
    padroes = {
        "scripts": r"`?scripts/tests`?\W+(\d+)",
        "backend": r"backend em SQLite inteiro (\d+)",
        "frontend": r"frontend (\d+)",
        "mypy": r"mypy (\d+)",
        "docs-check": r"docs-check (\d+)",
        "postgresql": r"PostgreSQL[^;]*?(\d+(?: \+ \d+)*)",
    }
    for chave, padrao in padroes.items():
        m = re.search(padrao, t)
        if m:
            achados[chave] = _numero(m.group(1))
    m = re.search(r"catracas (\d+) \(backend\) e (\d+) \(scripts\)", t)
    if m:
        achados["catracas"] = f"{m.group(1)} e {m.group(2)}"
    return achados


def extrair(linhas: list[str]) -> Deploy:
    m = _CABECALHO.match(linhas[0])
    if not m:
        raise ValueError("a primeira linha não é o cabeçalho de um deploy")
    d = Deploy(numero=int(m.group(2)), data=m.group(1), resumo=(m.group(3) or "").strip())
    marcadores = _marcadores(linhas)
    implantado = next((x for x in marcadores if "Implantado" in x), "")
    if h := _HORA.search(implantado):
        d.hora = h.group(1).zfill(5)
    if c := _COMMIT.search(implantado):
        d.commit_longo = c.group(1)
        d.migracoes = migracoes_de(implantado[c.end():])
    else:
        d.migracoes = migracoes_de(implantado)
    d.itens = itens_de(implantado)
    real = next((x for x in marcadores if x.startswith("Prova `real`")), None)
    simulated = next((x for x in marcadores if x.startswith("Prova `simulated`")), None)
    not_run = next((x for x in marcadores if x.startswith("`not_run`")), None)
    d.real = _resto(real, "`real`")
    d.simulated = _resto(simulated, "`simulated`")
    d.not_run = _resto(not_run, "`not_run`")
    base = f"{implantado} {d.real or ''}"
    if b := _BACKUP.search(base):
        d.backup = b.group(1)
    if t := _TAG.search(base):
        d.tag = t.group(1)
    if a := _AGENTE.search(base):
        d.agente = a.group(1)
    d.contagens = contagens_de(d.simulated)
    return d


# --------------------------------------------------------------------------------------- montagem (puro)
def _milhar(valor: str) -> str:
    """"12528" vira "12 528"; "5968 + 4120" vira "5 968 + 4 120"."""
    return " + ".join(f"{int(p):,}".replace(",", " ") for p in valor.split(" + "))


def _data_hora(d: Deploy) -> str:
    dia = f"{d.data[8:10]}/{d.data[5:7]}"
    return f"{dia} {d.hora}Z" if d.hora else dia


def _brasilia(d: Deploy) -> str | None:
    if not d.hora:
        return None
    t = datetime.fromisoformat(f"{d.data}T{d.hora}:00") - timedelta(hours=3)
    return t.strftime("%H:%M")


def _migracoes_texto(d: Deploy) -> str:
    if not d.migracoes:
        return "sem migração"
    if len(d.migracoes) == 1:
        return f"migração {d.migracoes[0]}"
    return "migrações " + ", ".join(d.migracoes[:-1]) + f" e {d.migracoes[-1]}"


def _resumo_curto(resumo: str, limite: int = 110) -> str:
    if not resumo:
        return "sem resumo no cabeçalho do CHANGELOG"
    partes = [p.strip() for p in resumo.split(",")]
    curto = ", ".join(partes[:3])
    return curto if len(curto) <= limite else curto[:limite - 1].rstrip() + "…"


def titulo_marco(d: Deploy) -> str:
    sha = f" ({d.commit})" if d.commit else ""
    resumo, mig = _resumo_curto(d.resumo), _migracoes_texto(d)
    # O cabeçalho do CHANGELOG já costuma dizer "sem migração"/"migração 126": não repetir no fim do título.
    fim = "" if mig.lower() in resumo.lower() else f" · {mig}"
    return f"📅 Deploy {d.numero} · {_data_hora(d)}{sha} · {resumo}{fim}"


def _com_rotulo(rotulo: str, resto: str | None, vazio: str) -> str:
    if not resto:
        return f"**{rotulo}:** {vazio}"
    if resto.startswith("("):
        return f"**{rotulo}** {resto}"
    return f"**{rotulo}:** {resto.lstrip(': ')}"


def _frase_itens(d: Deploy) -> str:
    ids = [i for i in d.itens if re.fullmatch(r"\d{1,2}\.\d{1,3}", i)]
    outros = [i for i in d.itens if i not in ids]
    if not d.itens:
        return "o registro do CHANGELOG não lista itens"
    partes = []
    if ids:
        partes.append(f"{len(ids)} itens do plano ({', '.join(ids)})")
    if outros:
        partes.append(f"referências {', '.join(outros)}")
    return " e ".join(partes)


def corpo_marco(d: Deploy) -> str:
    quando = f"{d.data[8:10]}/{d.data[5:7]}/{d.data[:4]}"
    if d.hora:
        quando += f" às {d.hora}Z ({_brasilia(d)} Brasília)"
    tecnico = [f"no ar em {quando}"]
    if d.commit_longo:
        tecnico.append(f"central no commit `{d.commit}`")
    tecnico.append(_migracoes_texto(d))
    if d.backup:
        tecnico.append(f"backup `{d.backup}`")
    if d.tag:
        tecnico.append(f"tag `{d.tag}`")
    if d.agente:
        tecnico.append(f"agente do notebook `{d.agente}`")
    itens = ", ".join(d.itens) if d.itens else "o registro não lista itens"
    resumo = f" Resumo do cabeçalho: {d.resumo}." if d.resumo else ""
    linhas = [
        f"**Para quem não é técnico:** a implantação {d.numero} colocou no ar {_frase_itens(d)}.{resumo} {NOTA_GERADO}",
        "",
        f"**Por que importa:** o que cada item muda está no registro do deploy {d.numero} do CHANGELOG; o que ainda não "
        f"foi provado fica na linha `not_run` abaixo. {NOTA_GERADO}",
        "",
        f"**Técnico:** {'; '.join(tecnico)}.\nItens: {itens}.",
        _com_rotulo("Prova `real`", d.real, "não consta no registro do CHANGELOG"),
        _com_rotulo("Prova `simulated`", d.simulated, "não consta no registro do CHANGELOG"),
        _com_rotulo("`not_run`", d.not_run, "não consta no registro do CHANGELOG"),
        f"**Fonte:** `CHANGELOG.md` (registro do deploy {d.numero}); cartão gerado por `.claude/trello/marco.py`.",
    ]
    return "\n".join(linhas)


def decidir_marco(numero: int, cartoes: list[dict[str, object]]) -> tuple[str, str | None]:
    """("criar", None) ou ("atualizar", id do cartão). Acha o marco pelo prefixo do título, para o título poder mudar."""
    prefixo = f"📅 Deploy {numero} "
    for c in cartoes:
        nome = c.get("name")
        if isinstance(nome, str) and nome.startswith(prefixo):
            return "atualizar", str(c.get("id"))
    return "criar", None


def sem_prefixo_leitura(desc: str) -> str:
    """Tira a leitura anterior do topo da descrição (para não empilhar), como no molde de m89."""
    if desc.startswith(PREFIXO_LEITURA) and SEPARADOR in desc:
        return desc.split(SEPARADOR, 1)[1]
    return desc


def agora_rotulo(agora: datetime | None = None) -> str:
    return (agora or datetime.now(timezone.utc)).strftime("%d/%m %H:%MZ")


def montar_m9(d: Deploy, rotulo: str) -> tuple[str, str] | None:
    """(nome do cartão, texto do topo) de M9, das contagens do CHANGELOG; `None` se o registro não traz contagem."""
    c = d.contagens
    if not c:
        return None
    soma = [f"{_milhar(c[k])} {k}" for k in ("backend", "scripts", "frontend") if k in c]
    atual = " + ".join(soma) if soma else "ver o registro"
    nome = f"📏 M9 · Testes automatizados — meta: toda suíte verde antes de integrar · atual: {atual} (suíte {d.numero})"
    itens = []
    if "backend" in c:
        itens.append(f"backend em SQLite inteiro {_milhar(c['backend'])}")
    if "scripts" in c:
        itens.append(f"scripts/tests {_milhar(c['scripts'])} passed")
    if "frontend" in c:
        itens.append(f"frontend {_milhar(c['frontend'])}")
    for k in ("catracas", "docs-check", "mypy"):
        if k in c:
            itens.append(f"{k} {c[k]}")
    if "postgresql" in c:
        itens.append(f"PostgreSQL dirigido {_milhar(c['postgresql'])}")
    sha = f" sobre {d.commit}" if d.commit else ""
    topo = (f"{PREFIXO_LEITURA}{rotulo} (prova simulada, CHANGELOG, deploy {d.numero}{sha}):** {'; '.join(itens)}. "
            f"As falhas e os vermelhos do funil estão declarados no registro do deploy {d.numero} do CHANGELOG."
            f"{SEPARADOR}")
    return nome, topo


def contar_aparelhos(instancias: list[dict[str, object]]) -> Counter[str]:
    return Counter(str(i.get("state") or "sem estado") for i in instancias)


def montar_m8(d: Deploy, estados: Counter[str], rotulo: str) -> tuple[str, str]:
    total = sum(estados.values())
    online = estados.get("online", 0)
    fora = total - online
    nome = f"📏 M8 · Aparelhos online — meta ≥ 99 % do tempo · atual: {online} online de {total} ({fora} fora de online)"
    detalhe = ", ".join(f"{k} {v}" for k, v in sorted(estados.items(), key=lambda kv: (-kv[1], kv[0])))
    sha = f" ({d.commit})" if d.commit else ""
    topo = (f"{PREFIXO_LEITURA}{rotulo} (real, GET /api/instances no central; último deploy no CHANGELOG: {d.numero}{sha}):** "
            f"{total} aparelhos cadastrados, {online} online e {fora} em outro estado ({detalhe}). "
            "A leitura é pontual: o percentual de tempo online não é medido por este cartão."
            f"{SEPARADOR}")
    return nome, topo


# --------------------------------------------------------------------------------------- rede (só aqui)
def _ler_aparelhos() -> list[dict[str, object]]:
    with urllib.request.urlopen(API_CENTRAL, timeout=15) as r:  # noqa: S310 - loopback do central
        dados = json.loads(r.read().decode("utf-8"))
    return [i for i in dados if isinstance(i, dict)]


async def _principal(d: Deploy, e_recente: bool, aplicar: bool, offline: bool, resultado: dict | None = None) -> int:
    """`resultado`, se dado, recebe o que o espelho do deploy (28.67) mostra na linha final: `marco` (url ou o que faria),
    `m8` e `m9` (a leitura nova ou por que não foi tocada)."""
    resultado = resultado if resultado is not None else {}
    from redacao import redigir  # noqa: PLC0415 - lê o banco ro na importação

    titulo, corpo = redigir(titulo_marco(d)), redigir(corpo_marco(d))
    rotulo = agora_rotulo()
    cl = None
    if not offline:
        sys.path.insert(0, str(BACKEND_CENTRAL))
        from app.config import EnvSettings  # noqa: PLC0415 - só no modo de rede
        from app.modules.avisos.adapters.trello import ClienteTrello  # noqa: PLC0415
        e = EnvSettings()
        cl = ClienteTrello(e.trello_api_key.get_secret_value().strip(), e.trello_token.get_secret_value().strip())
    acao, cartao = ("criar", None)
    if cl is not None:
        acao, cartao = decidir_marco(d.numero, await cl.cartoes_da_lista(LISTA_MARCOS))
    print(f"== marco ({acao}{' ' + cartao if cartao else ''}) ==\n{titulo}\n\n{corpo}\n")
    if aplicar and cl is not None:
        if acao == "criar":
            r = await cl.criar_cartao(LISTA_MARCOS, titulo, corpo)
            await cl._pedir("PUT", f"/1/cards/{r['id']}", corpo={"pos": "top"})
            print("marco criado:", r.get("shortUrl"))
            resultado["marco"] = f"criado {r.get('shortUrl')}"
        else:
            r = await cl.atualizar_cartao(str(cartao), nome=titulo, desc=corpo)
            print("marco atualizado:", cartao)
            resultado["marco"] = f"atualizado {r.get('shortUrl') or cartao}"

    resultado.setdefault("marco", f"ensaio: {'criaria' if acao == 'criar' else 'atualizaria ' + str(cartao)}")
    if not e_recente:
        print(f"M8 e M9 não tocados: o deploy {d.numero} não é o mais recente do CHANGELOG.")
        resultado["m8"] = resultado["m9"] = "não tocado (deploy não é o mais recente)"
        return 0
    leituras: list[tuple[str, str, str]] = []
    m9 = montar_m9(d, rotulo)
    if m9:
        leituras.append((CARTAO_M9, *m9))
    else:
        print(f"M9 não tocado: o registro do deploy {d.numero} não traz contagem da suíte.")
        resultado["m9"] = "não tocado (sem contagem no registro)"
    if cl is not None:
        try:
            leituras.append((CARTAO_M8, *montar_m8(d, contar_aparelhos(_ler_aparelhos()), rotulo)))
        except Exception as erro:  # noqa: BLE001 - central fora do ar: M8 fica como está, o resto segue
            print(f"M8 não tocado: não consegui ler {API_CENTRAL} ({type(erro).__name__}).")
            resultado["m8"] = "não tocado (central não respondeu)"
    for cid, nome, topo in leituras:
        nome, topo = redigir(nome), redigir(topo)
        lida = re.search(r"atual: (.*?)(?: \(suíte \d+\))?$", nome)
        resultado["m8" if cid == CARTAO_M8 else "m9"] = f"{'' if aplicar else 'ensaio '}{lida.group(1) if lida else nome}"
        print(f"== leitura ({cid}) ==\n{nome}\n{topo.removesuffix(SEPARADOR)}\n")
        if aplicar and cl is not None:
            atual = await cl._pedir("GET", f"/1/cards/{cid}", params={"fields": "desc"})
            await cl.atualizar_cartao(cid, nome=nome, desc=topo + sem_prefixo_leitura(str(atual.get("desc", ""))))
            print("atualizado:", cid)
    if not aplicar:
        print("(ensaio: nada foi gravado no Trello; use --aplicar)")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--deploy", type=int, help="número do deploy (padrão: o mais recente do CHANGELOG)")
    p.add_argument("--aplicar", action="store_true", help="grava no Trello (sem isto, só imprime)")
    p.add_argument("--offline", action="store_true", help="não usa rede: nem Trello nem /api/instances")
    p.add_argument("--changelog", type=Path, default=RAIZ / "CHANGELOG.md")
    args = p.parse_args(argv)
    for fluxo in (sys.stdout, sys.stderr):
        if hasattr(fluxo, "reconfigure"):
            fluxo.reconfigure(encoding="utf-8")
    texto = args.changelog.read_text(encoding="utf-8")
    linhas = achar_secao(texto, args.deploy)
    if linhas is None:
        print(f"Não achei o registro do deploy {args.deploy if args.deploy else '(nenhum)'} no CHANGELOG.", file=sys.stderr)
        return 2
    d = extrair(linhas)
    return asyncio.run(_principal(d, d.numero == numero_mais_recente(texto), args.aplicar, args.offline))


if __name__ == "__main__":
    raise SystemExit(main())
