"""28.69: um cartão por aparelho COM CONTA REAL no quadro Execução, com o estado da sessão lido pela API da central.

    backend/.venv/Scripts/python.exe .claude/trello/cartoes_de_aparelho.py [--base http://127.0.0.1:8000 | --arquivo instancias.json] [--aplicar]

Sem `--aplicar` é ENSAIO: lê a central (só GET) e imprime as ações que faria; nada é gravado no Trello.

Fonte (lida pela API, loopback, sem credencial):
  - `GET /api/instances`: `id`, `state`, `locked_account` (conta travada, marcador aberto), `repair_pause` (pausa do reparo);
  - `GET /api/instances/{id}/personas`: quem está vinculado ao aparelho, com `session` (`status`, `stale`, `unknown_at_cap`,
    `status_since`, `verified_at`). É o vínculo que faz o aparelho ter conta real (ADR-055: vínculo ativo ou conta travada).
`--arquivo` aceita o JSON de `GET /api/instances` (lista) ou `{"instances": [...], "personas": {"<id>": [...]}}`; cada
aparelho pode trazer as `personas` dentro dele. Serve para ensaiar sem a central.

O que entra no cartão: SÓ o rótulo do aparelho (o id da API, `android-01`), o estado da sessão (pronta, vencida, com erro, em
pausa de reparo, ou sem conta real), o motivo em vocabulário FIXO, desde quando está assim e o próximo passo curto. NUNCA entram
handle, nome de persona, e-mail, telefone, IP, serial, o texto livre da pausa (`reason`), `attention` nem `detail` da sessão:
nada disso é lido. Defesa em profundidade: nome e descrição passam por `_sem_contato` e `redacao.redigir`.
O "há quanto tempo" é a data de início (`Desde:`), não uma idade ("há 3 h"): a idade mudaria a descrição a cada rodada e a
segunda execução nunca teria 0 ações. O ensaio imprime a idade na saída, que não vai ao Trello.

Idempotente e sem duplicata: o cartão é achado pela linha `Aparelho: <rótulo>` na descrição (só nas listas Em execução, Em
validação e Concluído do quadro Execução; cartão de item do plano, com ID no nome, nunca casa). Nome, descrição e lista só são
gravados quando mudam. Pronta → Em execução; vencida, com erro e em pausa → Em validação; o aparelho que deixa de ter conta
real vai para Concluído. Duas rodadas seguidas: a segunda tem 0 ações. Rótulo com mais de um cartão: nada é tocado, é contado
em `duplicados`. NUNCA comenta em cartão (o comentário entra como "do dono"): só a descrição muda.

Falha na leitura de UM aparelho (personas): ele é pulado e contado em `falhas`; nunca vira "sem conta real" por engano.

Última linha impressa (a que a Canais cola no relato):
    aparelhos <n>; com conta real <n>; criar|criados <n>; atualizar|atualizados <n>; concluir|concluídos <n>; duplicados <n>; falhas <n|nenhuma>
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))
sys.path.insert(0, str(AQUI.parent / "canais"))

# a chave e o token do Trello ficam no checkout central; o worktree não os tem (lidos por EnvSettings, nunca impressos)
BACKEND_CENTRAL = Path(r"C:\git\android\backend")
RAIZ_CENTRAL = BACKEND_CENTRAL.parent

import redacao  # noqa: E402
from redacao import redigir  # noqa: E402
from resumo_laco import _sem_contato  # noqa: E402

BASE_PADRAO = "http://127.0.0.1:8000"
QUADRO_EXECUCAO = "6ac13aeda5570365d020f8e2"
LISTA_EM_EXECUCAO = "6ac13b19b13017ef2d3ead07"
LISTA_EM_VALIDACAO = "6ac13b1a043b867572d9dc49"
LISTA_CONCLUIDO = "6ac13b1d2b3e0ab6f1126112"
LISTAS = {LISTA_EM_EXECUCAO: "Em execução", LISTA_EM_VALIDACAO: "Em validação", LISTA_CONCLUIDO: "Concluído"}

#: o id do aparelho na API (`android-01`): é rótulo de infraestrutura, não serial nem hostname. Fora desse formato o
#: aparelho é pulado (nada vai ao Trello com texto que não se reconhece).
_ROTULO = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,15}-\d{1,4}$")
_LINHA_APARELHO = re.compile(r"(?m)^Aparelho: (\S+)[ \t]*$")
#: cartão de item do plano (`28.69 · ...`, `[A] T.3 · ...`): nunca é cartão de aparelho
_ID_NO_NOME = re.compile(r"^(?:\[A\]\s*)?(\d+\.\d+|T\.\d+)(?![\d.])")

PRONTA, VENCIDA, ERRO, PAUSA, SEM_CONTA = "pronta", "vencida", "com erro", "em pausa de reparo", "sem conta real"
#: estado → lista do quadro Execução
LISTA_DO_ESTADO = {PRONTA: LISTA_EM_EXECUCAO, VENCIDA: LISTA_EM_VALIDACAO, ERRO: LISTA_EM_VALIDACAO,
                   PAUSA: LISTA_EM_VALIDACAO, SEM_CONTA: LISTA_CONCLUIDO}

# vocabulário FIXO: motivo → próximo passo
PASSO = {
    "sessão verificada": "nenhum; a sessão está pronta.",
    "verificação antiga": "reverificar a sessão pelo painel.",
    "login necessário": "conectar a conta pelo painel (Foco do aparelho).",
    "desafio do app": "uma pessoa resolve o desafio no aparelho.",
    "conta errada no aparelho": "conferir a conta logada e entrar de novo.",
    "precisa de uma pessoa": "uma pessoa olha o aparelho pelo painel.",
    "sessão não verificada": "verificar a sessão pelo painel.",
    "sessão em estado desconhecido": "verificar a sessão pelo painel.",
    "conta travada": "o dono decide; o aparelho fica em quarentena.",
    "aparelho com erro": "esperar o reparo automático; se esgotar, o dono olha.",
    "pausa de reparo": "esperar o fim da pausa ou encerrá-la pelo painel.",
    "sem vínculo com conta": "nenhum; o cartão é arquivado como concluído.",
}
#: estado da sessão da API → (estado do cartão, motivo, gravidade: maior = pior)
_SESSAO = {
    "auth_challenge": (ERRO, "desafio do app", 6),
    "wrong_account": (ERRO, "conta errada no aparelho", 5),
    "needs_person": (ERRO, "precisa de uma pessoa", 4),
    "auth_required": (ERRO, "login necessário", 3),
    "unknown": (ERRO, "sessão não verificada", 2),
}


class FalhaDeLeitura(Exception):
    """A central (ou o arquivo) não deu o que o cartão precisa; `causa` é só o tipo do erro, sem URL nem texto."""

    def __init__(self, causa: str) -> None:
        super().__init__(causa)
        self.causa = causa


@dataclass(frozen=True)
class Estado:
    estado: str                  # PRONTA | VENCIDA | ERRO | PAUSA | SEM_CONTA
    motivo: str                  # chave de PASSO
    desde: datetime | None = None

    @property
    def lista(self) -> str:
        return LISTA_DO_ESTADO[self.estado]


# ------------------------------------------------------------------------------------------ puro: estado do aparelho
def rotulo_valido(ident: object) -> str | None:
    return ident if isinstance(ident, str) and _ROTULO.match(ident) else None


def _hora(valor: object) -> datetime | None:
    if not isinstance(valor, str) or not valor.strip():
        return None
    try:
        dt = datetime.fromisoformat(valor.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _da_sessao(sessao: object) -> tuple[Estado, int]:
    """(estado do cartão, gravidade) de UMA sessão do vínculo. Sem sessão ou formato estranho: "não verificada"."""
    s = sessao if isinstance(sessao, dict) else {}
    status = s.get("status")
    desde = _hora(s.get("status_since")) or _hora(s.get("verified_at"))
    if status == "session_ready":
        if s.get("stale") is True:
            return Estado(VENCIDA, "verificação antiga", desde), 1
        return Estado(PRONTA, "sessão verificada", desde), 0
    if status == "unknown" and s.get("unknown_at_cap") is True:
        return Estado(ERRO, "precisa de uma pessoa", desde), 4
    if status in _SESSAO:
        est, motivo, grav = _SESSAO[status]
        return Estado(est, motivo, desde), grav
    if not s:
        return Estado(ERRO, "sessão não verificada", None), 2
    return Estado(ERRO, "sessão em estado desconhecido", desde), 2


def estado_do_aparelho(inst: dict, personas: list | None) -> Estado | None:
    """O estado do cartão do aparelho, `SEM_CONTA` se ele não tem conta real, ou `None` se NÃO dá para saber (as personas
    não foram lidas): quem chama pula o aparelho em vez de concluir o cartão por engano.

    Conta real = vínculo ativo com persona OU conta travada (marcador aberto): a mesma regra do ADR-055. Precedência:
    conta travada, pausa do reparo, aparelho em `error`, e só então a PIOR sessão entre os vínculos (a mais antiga, no
    empate)."""
    if inst.get("locked_account"):
        return Estado(ERRO, "conta travada", None)
    if personas is None:
        return None
    if not personas:
        return Estado(SEM_CONTA, "sem vínculo com conta", None)
    pausa = inst.get("repair_pause")
    if isinstance(pausa, dict):
        return Estado(PAUSA, "pausa de reparo", _hora(pausa.get("since")))
    if inst.get("state") == "error":
        return Estado(ERRO, "aparelho com erro", None)
    pior: tuple[Estado, int] | None = None
    for p in personas:
        est, grav = _da_sessao(p.get("session") if isinstance(p, dict) else None)
        if pior is None or grav > pior[1] or (grav == pior[1] and _antes(est.desde, pior[0].desde)):
            pior = (est, grav)
    assert pior is not None
    return pior[0]


def _antes(a: datetime | None, b: datetime | None) -> bool:
    return a is not None and (b is None or a < b)


def idade(desde: datetime | None, agora: datetime) -> str:
    if desde is None:
        return "tempo não informado"
    s = max(0, int((agora - desde).total_seconds()))
    if s < 60:
        return "há menos de 1 min"
    if s < 3600:
        return f"há {s // 60} min"
    if s < 86400:
        return f"há {s // 3600} h"
    return f"há {s // 86400} d"


# ------------------------------------------------------------------------------------------ puro: texto do cartão
def _limpo(texto: str) -> str:
    return redigir(_sem_contato(texto))


def montar_cartao(rotulo: str, e: Estado) -> tuple[str, str]:
    """(nome, descrição) do cartão. Só vocabulário fixo e o rótulo; ainda assim passa pelo filtro do Trello."""
    nome = f"{rotulo} · {'sessão ' + e.estado if e.estado in (PRONTA, VENCIDA, ERRO) else e.estado}"
    desde = e.desde.astimezone(UTC).strftime("%Y-%m-%d %H:%MZ") if e.desde else "não informado"
    linhas = [
        f"Aparelho: {rotulo}",
        f"Estado da sessão: {e.estado}",
        f"Motivo: {e.motivo}",
        f"Desde: {desde}",
        f"Próximo passo: {PASSO[e.motivo]}",
        "",
        "Gerado pela Canais (28.69; a descrição é reescrita quando o estado muda). Sem handle, nome de persona, e-mail, "
        "telefone, IP nem serial.",
    ]
    return _limpo(nome), _limpo("\n".join(linhas))


def _igual(a: str, b: str) -> bool:
    return a.replace("\r\n", "\n").strip() == b.replace("\r\n", "\n").strip()


# ------------------------------------------------------------------------------------------ puro: plano de ações
@dataclass(frozen=True)
class Cartao:
    id: str
    nome: str
    desc: str
    lista: str


@dataclass(frozen=True)
class Acao:
    tipo: str                    # criar | atualizar | concluir
    rotulo: str
    estado: Estado
    lista: str                   # lista de destino
    nome: str
    desc: str
    card_id: str | None = None   # só em atualizar e concluir
    muda: tuple[str, ...] = ()   # o que muda em atualizar/concluir: nome, descrição, lista


@dataclass
class Plano:
    acoes: list[Acao] = field(default_factory=list)
    duplicados: list[str] = field(default_factory=list)


def cartoes_de_aparelho(cartoes: list[Cartao]) -> tuple[dict[str, Cartao], list[str]]:
    """Os cartões abertos que SÃO de aparelho (linha `Aparelho: <rótulo>`, nas 3 listas, sem ID de plano no nome):
    rótulo → cartão, e os rótulos com mais de um cartão (esses não se tocam)."""
    achados: dict[str, list[Cartao]] = {}
    for c in cartoes:
        if c.lista not in LISTAS or _ID_NO_NOME.match(c.nome):
            continue
        m = _LINHA_APARELHO.search(c.desc or "")
        if m:
            achados.setdefault(m.group(1), []).append(c)
    unicos = {r: cs[0] for r, cs in achados.items() if len(cs) == 1}
    return unicos, sorted(r for r, cs in achados.items() if len(cs) > 1)


def planejar(estados: dict[str, Estado | None], cartoes: list[Cartao]) -> Plano:
    """`estados`: rótulo → estado (`None` = não deu para ler: pula). Rótulo sem conta real que ainda tem cartão vai para
    Concluído; rótulo que a central nem lista mais (some do `estados`) também, mas só se `estados` veio completo."""
    existentes, duplicados = cartoes_de_aparelho(cartoes)
    plano = Plano(duplicados=duplicados)
    for rotulo in sorted(estados):
        e = estados[rotulo]
        if e is None or rotulo in duplicados:
            continue
        c = existentes.get(rotulo)
        if e.estado == SEM_CONTA:
            if c is None:
                continue
            nome, desc = montar_cartao(rotulo, e)
            muda = _diferencas(c, nome, desc, LISTA_CONCLUIDO)
            if muda:
                plano.acoes.append(Acao("concluir", rotulo, e, LISTA_CONCLUIDO, nome, desc, c.id, muda))
            continue
        nome, desc = montar_cartao(rotulo, e)
        if c is None:
            plano.acoes.append(Acao("criar", rotulo, e, e.lista, nome, desc))
            continue
        muda = _diferencas(c, nome, desc, e.lista)
        if muda:
            plano.acoes.append(Acao("atualizar", rotulo, e, e.lista, nome, desc, c.id, muda))
    # cartão de aparelho que a central não lista mais: o aparelho foi aposentado, o cartão vai para Concluído
    for rotulo, c in sorted(existentes.items()):
        if rotulo in estados or rotulo in duplicados:
            continue
        e = Estado(SEM_CONTA, "sem vínculo com conta")
        nome, desc = montar_cartao(rotulo, e)
        muda = _diferencas(c, nome, desc, LISTA_CONCLUIDO)
        if muda:
            plano.acoes.append(Acao("concluir", rotulo, e, LISTA_CONCLUIDO, nome, desc, c.id, muda))
    return plano


def _diferencas(c: Cartao, nome: str, desc: str, lista: str) -> tuple[str, ...]:
    muda = []
    if not _igual(c.nome, nome):
        muda.append("nome")
    if not _igual(c.desc, desc):
        muda.append("descrição")
    if c.lista != lista:
        muda.append("lista")
    return tuple(muda)


def contar(plano: Plano) -> dict[str, int]:
    out = {"criar": 0, "atualizar": 0, "concluir": 0}
    for a in plano.acoes:
        out[a.tipo] += 1
    return out


def linha_final(total: int, com_conta: int, plano: Plano, falhas: list[str], ensaio: bool) -> str:
    n = contar(plano)
    verbo = ("criar", "atualizar", "concluir") if ensaio else ("criados", "atualizados", "concluídos")
    return (f"aparelhos {total}; com conta real {com_conta}; {verbo[0]} {n['criar']}; {verbo[1]} {n['atualizar']}; "
            f"{verbo[2]} {n['concluir']}; duplicados {len(plano.duplicados)}; "
            f"falhas {', '.join(falhas) if falhas else 'nenhuma'}")


# ------------------------------------------------------------------------------------------ leitura (central ou arquivo)
Obter = Callable[[str], object]


def _obter_http(url: str) -> object:
    try:
        with urllib.request.urlopen(url, timeout=15) as r:  # noqa: S310 - loopback do central, só GET
            return json.loads(r.read().decode("utf-8"))
    except Exception as erro:  # noqa: BLE001 - só o tipo do erro sai (a mensagem pode trazer a URL)
        raise FalhaDeLeitura(type(erro).__name__) from None


def ler_da_central(base: str, obter: Obter = _obter_http) -> tuple[list[dict], dict[str, list | None], list[str]]:
    """(instâncias, personas por id — `None` onde a leitura falhou, falhas). Falha na lista inteira levanta."""
    instancias = obter(f"{base.rstrip('/')}/api/instances")
    if not isinstance(instancias, list):
        raise FalhaDeLeitura("resposta de /api/instances sem lista")
    personas: dict[str, list | None] = {}
    falhas: list[str] = []
    for inst in instancias:
        ident = rotulo_valido(inst.get("id")) if isinstance(inst, dict) else None
        if ident is None:
            continue
        try:
            r = obter(f"{base.rstrip('/')}/api/instances/{ident}/personas")
            if not isinstance(r, list):
                raise FalhaDeLeitura("resposta sem lista")
            personas[ident] = r
        except FalhaDeLeitura:
            personas[ident] = None
            falhas.append(f"personas de {ident}")
    return [i for i in instancias if isinstance(i, dict)], personas, falhas


def ler_do_arquivo(caminho: Path) -> tuple[list[dict], dict[str, list | None], list[str]]:
    try:
        bruto = json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, ValueError) as erro:
        raise FalhaDeLeitura(type(erro).__name__) from None
    por_id = bruto.get("personas", {}) if isinstance(bruto, dict) else {}
    instancias = bruto.get("instances") if isinstance(bruto, dict) else bruto
    if not isinstance(instancias, list):
        raise FalhaDeLeitura("arquivo sem lista de instâncias")
    personas: dict[str, list | None] = {}
    for inst in instancias:
        if not isinstance(inst, dict) or rotulo_valido(inst.get("id")) is None:
            continue
        dentro = inst.get("personas")
        personas[inst["id"]] = dentro if isinstance(dentro, list) else (por_id.get(inst["id"]) or [])
    return [i for i in instancias if isinstance(i, dict)], personas, []


def estados_dos_aparelhos(instancias: list[dict], personas: dict[str, list | None]) -> tuple[dict[str, Estado | None],
                                                                                          list[str]]:
    """rótulo → estado; pula (e conta) o rótulo fora do formato."""
    estados: dict[str, Estado | None] = {}
    fora: list[str] = []
    for inst in instancias:
        rotulo = rotulo_valido(inst.get("id"))
        if rotulo is None:
            fora.append("rótulo fora do formato")
            continue
        estados[rotulo] = estado_do_aparelho(inst, personas.get(rotulo))
    return estados, fora


# ------------------------------------------------------------------------------------------ Trello
def _novo_cliente():  # noqa: ANN202 - o tipo vem do backend do central
    sys.path.insert(0, str(BACKEND_CENTRAL))
    from app.config import EnvSettings  # noqa: PLC0415 - só no modo de rede
    from app.modules.avisos.adapters.trello import ClienteTrello  # noqa: PLC0415
    e = EnvSettings()
    return ClienteTrello(e.trello_api_key.get_secret_value().strip(), e.trello_token.get_secret_value().strip())


async def ler_cartoes(cl) -> list[Cartao]:  # noqa: ANN001
    """Os cartões ABERTOS do quadro Execução (uma chamada), só `id`, `name`, `desc` e a lista."""
    cs = await cl._pedir("GET", f"/1/boards/{QUADRO_EXECUCAO}/cards",  # noqa: SLF001
                         params={"fields": "id,name,desc,idList", "filter": "open"})
    return [Cartao(str(c["id"]), str(c.get("name", "")), str(c.get("desc", "")), str(c.get("idList", "")))
            for c in cs if isinstance(c, dict) and "id" in c]


async def aplicar(cl, plano: Plano) -> list[str]:  # noqa: ANN001
    """Grava as ações pelo cliente (NUNCA `comentar`). Para no primeiro erro: o que já foi feito fica e a próxima rodada faz
    só o resto. Devolve as falhas (só o tipo do erro)."""
    for a in plano.acoes:
        try:
            if a.tipo == "criar":
                await cl.criar_cartao(a.lista, a.nome, a.desc)
            else:
                campos = {"nome": a.nome if "nome" in a.muda else None, "desc": a.desc if "descrição" in a.muda else None,
                          "lista": a.lista if "lista" in a.muda else None}
                await cl.atualizar_cartao(a.card_id, **campos)
        except Exception as erro:  # noqa: BLE001 - só o tipo do erro sai
            return [f"{a.tipo} de {a.rotulo}: {type(erro).__name__}"]
    return []


# ------------------------------------------------------------------------------------------ comando
def descrever(a: Acao, ensaio: bool, agora: datetime) -> str:
    verbo = {"criar": "criaria" if ensaio else "criado", "atualizar": "atualizaria" if ensaio else "atualizado",
             "concluir": "concluiria" if ensaio else "concluído"}[a.tipo]
    onde = f" → {LISTAS[a.lista]}" if a.tipo != "atualizar" or "lista" in a.muda else ""
    mudou = f" ({', '.join(a.muda)})" if a.muda else ""
    return _limpo(f"  {verbo}: {a.nome}{onde}{mudou}; {idade(a.estado.desde, agora)}")


def principal(instancias: list[dict], personas: dict[str, list | None], falhas_leitura: list[str], cartoes: list[Cartao],
              agora: datetime, ensaio: bool, escrever: Callable[[Plano], list[str]] | None = None,
              imprimir: Callable[[str], None] = print) -> tuple[Plano, list[str]]:
    """Calcula e (se `escrever`) grava. Devolve o plano e as falhas. Central sem nenhum aparelho: recusa (concluir todos os
    cartões por uma resposta vazia seria o erro pior)."""
    if not instancias:
        raise FalhaDeLeitura("a central não listou aparelho nenhum")
    estados, fora = estados_dos_aparelhos(instancias, personas)
    plano = planejar(estados, cartoes)
    falhas = list(falhas_leitura) + fora
    for a in plano.acoes:
        imprimir(descrever(a, ensaio, agora))
    if escrever is not None:
        falhas += escrever(plano)
    total_com_conta = sum(1 for e in estados.values() if e is not None and e.estado != SEM_CONTA)
    imprimir(linha_final(len(estados), total_com_conta, plano, falhas, ensaio))
    return plano, falhas


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--base", default=BASE_PADRAO, help="central (padrão: %(default)s)")
    p.add_argument("--arquivo", type=Path, help="JSON de /api/instances (ensaio sem a central)")
    p.add_argument("--aplicar", action="store_true", help="grava no Trello (sem isto é só ensaio)")
    args = p.parse_args(argv)
    ensaio = not args.aplicar
    try:
        # os nomes de persona a esconder vêm do banco do checkout central (um worktree não o tem)
        if not redacao.recarregar(RAIZ_CENTRAL) and args.aplicar:
            print("não consegui ler os nomes a esconder (banco do central); nada foi gravado")
            return 1
        instancias, personas, falhas = (ler_do_arquivo(args.arquivo) if args.arquivo
                                        else ler_da_central(args.base))
        cl = None
        cartoes: list[Cartao] = []
        if args.aplicar or not args.arquivo:
            cl = _novo_cliente()
            cartoes = asyncio.run(ler_cartoes(cl))
        escrever = (lambda plano: asyncio.run(aplicar(cl, plano))) if args.aplicar else None  # noqa: E731
        _, falhas_final = principal(instancias, personas, falhas, cartoes, datetime.now(UTC), ensaio, escrever)
    except FalhaDeLeitura as erro:
        print(f"leitura falhou: {erro.causa}; nada foi gravado")
        return 1
    except Exception as erro:  # noqa: BLE001 - a mensagem pode trazer URL ou chave: só o tipo
        print(f"falhou: {type(erro).__name__}; o que já foi gravado fica, repita o comando para fazer o resto")
        return 1
    return 1 if falhas_final else 0


if __name__ == "__main__":
    raise SystemExit(main())
