"""28.67: o espelho do deploy num comando só (cartões que faltam, reconciliação dos 3 quadros, marco, M8 e M9).

Ao fechar um deploy a Canais encadeava à mão, em scripts soltos fora do Git, quatro coisas. Agora é um comando:

    backend/.venv/Scripts/python.exe .claude/trello/espelho_do_deploy.py --raiz <checkout em origin/main> [--deploy NN] [--aplicar]

Sem `--aplicar` só relata o que faria em cada passo e as contagens previstas (nada é gravado no Trello).

`--raiz` é um checkout destacado em `origin/main` (`git -C <raiz> checkout --detach origin/main`), de onde vêm o plano, o
estado do plano, o CHANGELOG e o Git: o checkout central pode estar atrás da `main`. Quem prepara a raiz é quem fecha o
deploy; o script não troca de ramo, só confere (passo 0) e para se a raiz não for o `origin/main` mais recente.

Passos (cada um para no primeiro erro de rede e diz qual foi, sem repetir os que já foram feitos):

  0. confere a raiz e o número do deploy (padrão: o mais recente do CHANGELOG da raiz);
  1. cria em Próximas um cartão para cada ID do plano que não tem cartão aberto em nenhum dos 3 quadros;
  2. reconciliação TOTAL dos 3 quadros pelo estado do plano (`reconciliar.py`: a lógica de decisão é a dele);
  3. marco do deploy e leituras M8 e M9 (`marco.py`).

Idempotente: o passo 1 só cria o que não tem cartão (a segunda rodada acha 0 a criar); o 2 só move o que o plano manda
(a segunda rodada acha 0 ações) e a linha de prova é trocada, não empilhada; o 3 acha o marco pelo prefixo do título e
substitui a leitura anterior de M8 e M9. Uma queda no meio do passo 1 deixa os cartões já criados, e repetir o comando
cria só o resto. Nada de segredo, handle ou nome de persona na saída: o texto vai por `redacao.redigir`, que aqui lê os
nomes do banco do checkout central (um worktree não tem o banco) e, com `--aplicar`, o comando se recusa a gravar se não
conseguir lê-los.

Última linha impressa (a única que a Canais cola no relato):

    deploy NN: plano X IDs, Y cartões, Z sem cartão; nasceram N; para Concluído a; para Em validação b; só linha c;
    marco <url>; M8 <texto>; M9 <texto>; falhas nenhuma|<n>
"""
from __future__ import annotations

import argparse
import asyncio
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))
import modelo_de_foco as MF  # noqa: E402

# a chave e o token do Trello ficam no checkout central; o worktree não os tem (lidos por EnvSettings, nunca impressos)
BACKEND_CENTRAL = Path(r"C:\git\android\backend")
RAIZ_CENTRAL = BACKEND_CENTRAL.parent

LISTA_PROXIMAS = MF.POS_PROXIMAS          # posição lógica: EM CURSO com a etiqueta "Estado · Próximas"
QUADROS = ("6ac13aeda5570365d020f8e2",   # Execução
           "6ac13aeffc0ac80f9dc4edb3",   # Programa
           "6ac13af1b3229189f1741536")   # Histórico

_ID_NO_NOME = re.compile(r"^(?:\[A\]\s*)?(\d+\.\d+|T\.\d+)(?![\d.])")
_LINHA_DO_PLANO = re.compile(r"^\| (\d+\.\d+|T\.\d+) \| (.*) \| ([^|]*) \| ([^|]*) \|\s*$")
_APARELHO = re.compile(r"(?:android|emulator|worker)-\d+")


class PassoFalhou(Exception):
    """Um passo parou: `numero` e `nome` dizem qual, `causa` só o tipo do erro (a mensagem pode trazer URL ou chave)."""

    def __init__(self, numero: int, nome: str, causa: str) -> None:
        super().__init__(f"passo {numero} ({nome}): {causa}")
        self.numero, self.nome, self.causa = numero, nome, causa


# ------------------------------------------------------------------------------------------ passo 0 (puro)
def raiz_em_origin_main(head: str, origin: str) -> bool:
    return bool(head) and head.strip() == origin.strip()


# ------------------------------------------------------------------------------------------ passo 1 (puro)
@dataclass(frozen=True)
class ItemDoPlano:
    pid: str
    corpo: str
    origem: str
    esforco: str


def itens_do_plano(texto: str) -> list[ItemDoPlano]:
    """As linhas `| id | corpo | origem | esforço |` de `docs/plano-100.md`, na ordem do arquivo."""
    achados = []
    for linha in texto.splitlines():
        m = _LINHA_DO_PLANO.match(linha.rstrip("\n"))
        if m:
            achados.append(ItemDoPlano(m.group(1), m.group(2), m.group(3).strip(), m.group(4).strip()))
    return achados


def ids_com_cartao(nomes: list[str]) -> set[str]:
    """IDs de item (`N.N` ou `T.N`) que aparecem no começo do nome de algum cartão aberto, com ou sem o `[A]` do dono."""
    achados = set()
    for n in nomes:
        m = _ID_NO_NOME.match(n)
        if m:
            achados.add(m.group(1))
    return achados


def ids_sem_cartao(itens: list[ItemDoPlano], com_cartao: set[str]) -> list[ItemDoPlano]:
    """Os itens do plano sem cartão aberto em nenhum dos 3 quadros (cada ID uma vez só, mesmo repetido no plano)."""
    vistos: set[str] = set()
    faltam = []
    for it in itens:
        if it.pid in com_cartao or it.pid in vistos:
            continue
        vistos.add(it.pid)
        faltam.append(it)
    return faltam


def _sem_aparelho(texto: str) -> str:
    """O plano cita aparelhos (`android-13`, `emulator-5560`, `worker-01`): no Trello vai só "aparelho"."""
    return _APARELHO.sub("aparelho", texto)


def montar_cartao(item: ItemDoPlano, deploy: int, redigir: Callable[[str], str]) -> tuple[str, str]:
    """(nome, descrição) do cartão novo, só do que o plano diz, passado por `redigir` e sem nome de aparelho."""
    def limpo(t: str) -> str:
        return _sem_aparelho(redigir(t))

    t = re.match(r"\*\*(.+?)\*\*\s*(?:—\s*(.*))?$", item.corpo, re.S)
    titulo, resto = (t.group(1), t.group(2) or "") if t else (item.corpo, "")
    titulo = limpo(re.sub(r"\*\*", "", titulo).split(": ")[0].split(". ")[0].strip().rstrip(".:"))[:110]
    resto = limpo(resto.strip())[:1500]
    nome = f"{item.pid} · {titulo}"
    desc = (f"**Para quem não é técnico:** {titulo}.\n\n**Detalhe do plano:** {resto or 'sem detalhe além do título.'}\n\n"
            f"**Origem:** {limpo(item.origem)} · **Esforço:** {item.esforco or '?'} · **Custo pago:** não\n\n"
            f"**Fonte:** linha do item em `docs/plano-100.md` (cartão criado ao fechar o deploy {deploy}, por "
            "`.claude/trello/espelho_do_deploy.py`; a linha de prova e o deploy vêm do estado do plano na reconciliação).")
    return nome, desc


# ------------------------------------------------------------------------------------------ passo 2 (puro)
def contar_acoes(acoes: list[tuple[str, str | None]]) -> dict[str, int]:
    """`acoes`: (tipo, para) de cada ação da reconciliação. `listar` é só relato e não conta."""
    c = {"concluido": 0, "em_validacao": 0, "so_linha": 0, "outros": 0}
    for tipo, para in acoes:
        if tipo == "marcar":
            c["so_linha"] += 1
        elif tipo == "mover":
            c[para if para in ("concluido", "em_validacao") else "outros"] += 1
    return c


# ------------------------------------------------------------------------------------------ resumo e linha final
@dataclass
class Resumo:
    deploy: int
    plano: int = 0
    com_cartao: int = 0
    sem_cartao: int = 0
    nasceram: int = 0
    acoes: list[tuple[str, str | None]] = field(default_factory=list)
    falhas: int = 0
    marco: str = "não rodou"
    m8: str = "não rodou"
    m9: str = "não rodou"


def linha_final(r: Resumo, ensaio: bool) -> str:
    c = contar_acoes(r.acoes)
    nasc = "nasceriam" if ensaio else "nasceram"
    partes = [f"deploy {r.deploy}: plano {r.plano} IDs, {r.com_cartao} cartões, {r.sem_cartao} sem cartão",
              f"{nasc} {r.nasceram}",
              f"para Concluído {c['concluido']}", f"para Em validação {c['em_validacao']}", f"só linha {c['so_linha']}"]
    if c["outros"]:
        partes.append(f"outros movimentos {c['outros']}")
    partes += [f"marco {r.marco}", f"M8 {r.m8}", f"M9 {r.m9}", f"falhas {r.falhas or 'nenhuma'}"]
    return "; ".join(partes)


# ------------------------------------------------------------------------------------------ orquestração
@dataclass
class Passos:
    """Os quatro passos como funções, para o teste trocar a rede por falsos. Cada uma devolve um dict (ver `orquestrar`)."""
    conferir: Callable[[], dict]
    cartoes: Callable[[int], dict]
    reconciliar: Callable[[], dict]
    marco: Callable[[int], dict]


def _tentar(numero: int, nome: str, fn: Callable[[], dict]) -> dict:
    try:
        return fn()
    except PassoFalhou:
        raise
    except Exception as ex:  # noqa: BLE001 - só o tipo do erro sobe: a mensagem pode trazer URL ou chave
        raise PassoFalhou(numero, nome, type(ex).__name__) from ex


def orquestrar(passos: Passos, deploy_pedido: int | None, feitos: list[str]) -> Resumo:
    """Roda 0 a 3 em ordem e para no primeiro erro. `feitos` recebe o nome de cada passo concluído, para quem imprime a
    falha dizer o que já foi feito (e que não se repete)."""
    base = _tentar(0, "conferir a raiz", passos.conferir)
    deploy = deploy_pedido or base["deploy"]
    feitos.append("0 conferir a raiz")
    r = Resumo(deploy=deploy)
    p1 = _tentar(1, "cartões que faltam", lambda: passos.cartoes(deploy))
    r.plano, r.com_cartao, r.sem_cartao, r.nasceram = p1["plano"], p1["com_cartao"], p1["sem_cartao"], p1["nasceram"]
    feitos.append("1 cartões que faltam")
    p2 = _tentar(2, "reconciliação dos 3 quadros", passos.reconciliar)
    r.acoes, r.falhas = p2["acoes"], p2["falhas"]
    feitos.append("2 reconciliação dos 3 quadros")
    p3 = _tentar(3, "marco, M8 e M9", lambda: passos.marco(deploy))
    r.marco, r.m8, r.m9 = p3["marco"], p3["m8"], p3["m9"]
    feitos.append("3 marco, M8 e M9")
    return r


# ------------------------------------------------------------------------------------------ rede e Git (só daqui para baixo)
def _git(raiz: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(raiz), *args], capture_output=True, text=True, encoding="utf-8", check=False)
    if r.returncode != 0:
        raise RuntimeError(f"git {args[0]} falhou")
    return r.stdout.strip()


def _novo_cliente():  # noqa: ANN202 - o tipo vem do backend do central
    sys.path.insert(0, str(BACKEND_CENTRAL))
    from app.config import EnvSettings  # noqa: PLC0415 - só no modo de rede
    from app.modules.avisos.adapters.trello import ClienteTrello  # noqa: PLC0415
    e = EnvSettings()
    return MF.ClienteDePosicoes(ClienteTrello(e.trello_api_key.get_secret_value().strip(),
                                              e.trello_token.get_secret_value().strip()))


async def nomes_dos_cartoes_abertos(cl) -> list[str]:  # noqa: ANN001
    nomes: list[str] = []
    for quadro in QUADROS:
        cs = await cl._pedir("GET", f"/1/boards/{quadro}/cards", params={"fields": "name", "filter": "open"})
        nomes += [str(c["name"]) for c in cs]
    return nomes


async def criar_cartoes_que_faltam(cl, faltam: list[ItemDoPlano], deploy: int, aplicar: bool,  # noqa: ANN001
                                   redigir: Callable[[str], str]) -> list[str]:
    """Cria em Próximas (e só lá) um cartão por item de `faltam`; devolve os nomes (criados, ou que seriam criados no
    ensaio). Para no primeiro erro de rede: os já criados ficam e a próxima rodada cria só o resto."""
    nomes = []
    for it in faltam:
        nome, desc = montar_cartao(it, deploy, redigir)
        if aplicar:
            await cl.criar_cartao(LISTA_PROXIMAS, nome, desc)
        nomes.append(nome)
    return nomes


def passos_reais(raiz: Path, aplicar: bool) -> Passos:
    import marco as M  # noqa: PLC0415 - o import lê o banco de nomes (redacao); só no modo real
    import reconciliar as R  # noqa: PLC0415
    import redacao  # noqa: PLC0415

    changelog = raiz / "CHANGELOG.md"

    def conferir() -> dict:
        _git(raiz, "fetch", "-q", "origin")
        head, origin = _git(raiz, "rev-parse", "HEAD"), _git(raiz, "rev-parse", "origin/main")
        if not raiz_em_origin_main(head, origin):
            raise PassoFalhou(0, "conferir a raiz",
                              f"raiz desatualizada (HEAD {head[:8]}, origin/main {origin[:8]}); prepare com "
                              "`git -C <raiz> checkout --detach origin/main`")
        texto = changelog.read_text(encoding="utf-8")
        # o worktree não tem data/poc.sqlite3: os nomes de persona a esconder vêm do banco do checkout central
        if not redacao.recarregar(RAIZ_CENTRAL) and aplicar:
            raise PassoFalhou(0, "conferir a raiz", "não consegui ler os nomes a esconder (banco do central); nada foi gravado")
        return {"deploy": M.numero_mais_recente(texto)}

    def cartoes(deploy: int) -> dict:
        if M.achar_secao(changelog.read_text(encoding="utf-8"), deploy) is None:
            raise PassoFalhou(1, "cartões que faltam", f"o deploy {deploy} não tem registro no CHANGELOG da raiz")

        async def rodar() -> dict:
            cl = _novo_cliente()
            itens = itens_do_plano((raiz / "docs" / "plano-100.md").read_text(encoding="utf-8"))
            com = ids_com_cartao(await nomes_dos_cartoes_abertos(cl))
            faltam = ids_sem_cartao(itens, com)
            nomes = await criar_cartoes_que_faltam(cl, faltam, deploy, aplicar, redacao.redigir)
            for n in nomes:
                print(f"  {'criado' if aplicar else 'criaria'} em Próximas: {n[:90]}")
            plano = {it.pid for it in itens}
            return {"plano": len(plano), "com_cartao": len(plano & com), "sem_cartao": len(faltam), "nasceram": len(nomes)}
        return asyncio.run(rodar())

    def reconciliar() -> dict:
        resultado: dict = {}
        asyncio.run(R._principal(aplicar, raiz, resultado))
        return {"acoes": resultado["acoes"], "falhas": resultado["falhas"]}

    def marco(deploy: int) -> dict:
        texto = changelog.read_text(encoding="utf-8")
        d = M.extrair(M.achar_secao(texto, deploy) or [])
        resultado: dict = {}
        asyncio.run(M._principal(d, d.numero == M.numero_mais_recente(texto), aplicar, False, resultado))
        return {k: resultado.get(k, "não tocado") for k in ("marco", "m8", "m9")}

    return Passos(conferir=conferir, cartoes=cartoes, reconciliar=reconciliar, marco=marco)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--raiz", type=Path, required=True, help="checkout em origin/main (git checkout --detach origin/main)")
    p.add_argument("--deploy", type=int, help="número do deploy (padrão: o mais recente do CHANGELOG da raiz)")
    p.add_argument("--aplicar", action="store_true", help="grava no Trello (sem isto só relata)")
    a = p.parse_args(argv)
    for fluxo in (sys.stdout, sys.stderr):
        if hasattr(fluxo, "reconfigure"):
            fluxo.reconfigure(encoding="utf-8")
    sys.path.insert(0, str(BACKEND_CENTRAL))
    feitos: list[str] = []
    try:
        resumo = orquestrar(passos_reais(a.raiz.resolve(), a.aplicar), a.deploy, feitos)
    except PassoFalhou as ex:
        print(f"PAROU no passo {ex.numero} ({ex.nome}): {ex.causa}", file=sys.stderr)
        print("já feitos (não se repetem): " + (", ".join(feitos) or "nenhum"), file=sys.stderr)
        return 2 if ex.numero == 0 else 1
    if not a.aplicar:
        print("(ensaio: nada foi gravado; as ações do passo 2 não incluem os cartões que o passo 1 criaria)")
    print(linha_final(resumo, ensaio=not a.aplicar))
    return 1 if resumo.falhas else 0


if __name__ == "__main__":
    raise SystemExit(main())
