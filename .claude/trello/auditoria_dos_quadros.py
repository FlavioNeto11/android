"""28.72: auditoria diária de COERÊNCIA dos 3 quadros do Trello, SÓ POR LEITURA (nunca escreve no Trello nem avisa ninguém).

    backend/.venv/Scripts/python.exe .claude/trello/auditoria_dos_quadros.py [--json] [--raiz <checkout>]

Rodar com a pasta do checkout central como diretório atual: a chave e o token do Trello vêm de `EnvSettings` (lidos, nunca
impressos). `--raiz` é o checkout de onde vêm o plano (`docs/plano-100.md`), o estado (`.claude/plano-100/estado.json`), o
CHANGELOG e o Git; o padrão é o checkout central.

Seis verificações, contadas e com até 5 exemplos cada (SÓ o ID: `NN.NN`, `P-NNN` ou o rótulo `android-NN` do aparelho;
nunca o nome do cartão nem texto da descrição):

  1. duplicados: dois cartões abertos do MESMO quadro cujo nome começa pelo mesmo ID de plano, com o mesmo sufixo de subitem
     (`-C`, `F5` ou nenhum), depois de tirar só o `🙋`; nome com `[A]` ou `#NNN ·` nunca duplica; `28.24 F5` não é duplicata
     de `28.24`. Vale também para o mesmo P-NNN no Execução;
  2. sem_cartao: item de `docs/plano-100.md` sem cartão aberto em nenhum dos 3 quadros (a conta do `espelho_do_deploy`, com a
     leitura de ID do `reconciliar`; cartão da lista "Prova 07/10" conta como cartão);
  3. pergunta_incoerente: cartão P-NNN com a resposta do dono no topo ("**RESPOSTA DO DONO" ou "**Resposta do dono") mas ainda
     em "Perguntas para você", ou em "Perguntas respondidas" sem NENHUM bloco datado em negrito no topo (os cartões
     antigos têm a leitura da orquestradora, não o bloco do `registrar_resposta`);
  4. aparelho_incoerente: cartão de aparelho (linha `Aparelho: <rótulo>`, sem ID de plano no nome) duplicado, fora das listas
     Em execução, Em validação e Concluído, ou numa lista que não é a do "Estado da sessão" escrito na própria descrição;
  5. concluido_fora: cartão de item que o plano já dá como implementado e implantado (a decisão é a do
     `reconciliar.decidir`, que aqui só REPORTA: mover para Concluído ou Histórico) e que segue em lista de trabalho;
  6. sem_lista: cartão numa lista que não se conhece (lista fechada ou sumida; no Execução, também lista que o papel do
     `reconciliar` não reconhece). Programa e Histórico têm listas próprias: só se confere que a lista existe.

Os cartões da lista "Prova 07/10" ficam fora de 1, 3 a 6, como no `reconciliar`. Plano ilegível não derruba o resto: as
verificações 2 e 5 saem como `não lidas`. Quadro ilegível (rede, chave): `ok: false`, e o resumo diz "não consegui ler",
nunca "nada fora do lugar". Todo texto de saída passa por `_sem_contato` e `redacao.redigir`.

`--json` imprime o resultado (para outro script); `secao_do_resumo(resultado)` devolve a UMA linha HTML do Telegram que o
`canais/resumo_diario.py` usa.
"""
from __future__ import annotations

import argparse
import asyncio
import html
import json
import re
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))
sys.path.insert(0, str(AQUI.parent / "canais"))

# a chave e o token do Trello ficam no checkout central; o worktree não os tem (lidos por EnvSettings, nunca impressos)
BACKEND_CENTRAL = Path(r"C:\git\android\backend")
RAIZ_CENTRAL = BACKEND_CENTRAL.parent

import reconciliar as R  # noqa: E402
from cartoes_de_aparelho import _LINHA_APARELHO, _ROTULO, LISTA_DO_ESTADO, LISTAS  # noqa: E402
from espelho_do_deploy import QUADROS, ids_sem_cartao, itens_do_plano  # noqa: E402
from redacao import redigir  # noqa: E402
from registrar_resposta import ja_registrada  # noqa: E402
from resumo_laco import _sem_contato  # noqa: E402

NOMES_DOS_QUADROS = dict(zip(("execucao", "programa", "historico"), QUADROS, strict=True))
MAX_EXEMPLOS = 5

#: chave → (rótulo curto do resumo, rótulo da saída de console)
VERIFICACOES = {
    "duplicados": ("duplicados", "cartão duplicado (mesmo ID em dois cartões abertos)"),
    "sem_cartao": ("sem cartão", "item do plano sem cartão"),
    "pergunta_incoerente": ("perguntas", "pergunta P-NNN em lista incoerente com a resposta"),
    "aparelho_incoerente": ("aparelhos", "cartão de aparelho duplicado ou em lista incoerente"),
    "concluido_fora": ("concluídos fora de Concluído", "item já concluído e implantado, ainda em lista de trabalho"),
    "sem_lista": ("sem lista conhecida", "cartão em lista que não se conhece"),
}
#: verificações que dependem do plano (2 e 5)
DO_PLANO = ("sem_cartao", "concluido_fora")

#: papéis do Execução que NÃO são lista de trabalho (o `reconciliar.decidir` também os deixa quietos ou os move só a mão)
NAO_E_TRABALHO = {"concluido", "prova", "como_ler", "central", "perguntas", "perguntas_respondidas", "espera_voce"}
#: estado escrito no cartão do aparelho (`Estado da sessão: pronta`) → lista em que ele tem de estar (a regra do 28.69)
_LISTA_DO_ESTADO_ESCRITO = dict(LISTA_DO_ESTADO)
_LINHA_ESTADO = re.compile(r"(?m)^Estado da sessão: (.+?)[ \t]*$")
#: topo da descrição com a resposta do dono (o bloco do `registrar_resposta` ou o "Resposta do dono (data, comentário)" antigo)
_RESPOSTA_NO_TOPO = re.compile(r"^\s*\*\*resposta do dono", re.I)
#: qualquer bloco datado em negrito no topo ("**Leitura da orquestradora (06/10 14:19Z): …"): a pergunta já foi tratada
_BLOCO_DATADO = re.compile(r"^\s*\*\*[^\n]*?\(\d{2}/\d{2} \d{2}:\d{2}Z")
_ID_PERGUNTA = re.compile(r"^(?:\[A\]|[^\w])*(P-\d{1,4})\b")


def _limpo(texto: str) -> str:
    return redigir(_sem_contato(texto))


# ------------------------------------------------------------------------------------------------- cartões (puro)
def _papel(c: dict) -> str:
    """Papel da lista no Execução (`reconciliar.papel_da_lista`); nos outros quadros não há papel."""
    if c.get("quadro") != "execucao":
        return "outro_quadro"
    return R.papel_da_lista(c.get("lista") or "") if c.get("lista") else "sem_lista"


def _id_de(c: dict) -> str | None:
    return R.id_do_item(str(c.get("nome") or ""))


def _p_de(c: dict) -> str | None:
    if c.get("quadro") != "execucao":
        return None
    m = _ID_PERGUNTA.match(str(c.get("nome") or ""))
    return m.group(1) if m else None


def _quem(c: dict) -> str:
    """A identificação que pode aparecer na saída: o ID do item, o P-NNN, ou os 6 últimos caracteres do id do cartão."""
    return _id_de(c) or _p_de(c) or "sem ID (cartão …" + str(c.get("id") or "")[-6:] + ")"


def _resultado(exemplos: list[str], n: int | None = None) -> dict:
    uniq = list(dict.fromkeys(exemplos))
    return {"n": len(uniq) if n is None else n, "exemplos": [_limpo(e) for e in uniq[:MAX_EXEMPLOS]]}


#: nome que COMEÇA pelo ID do item (só o `🙋` pode vir antes), com o sufixo de subitem opcional: `-C` ou ` F5`. Nome com
#: `[A]` (cartão de pergunta ao dono) ou `#NNN ·` (cartão de PR/atividade) não casa: esses nunca são duplicata do item.
_NOME_DO_ITEM = re.compile(r"^(?:🙋\s*)?(?P<id>\d+\.\d+|T\.\d+)(?P<suf>-[A-Za-z]|\s+F\d+)?(?=$|[\s·:—–])")


def _chave_de_duplicata(c: dict) -> tuple[str, str] | None:
    """(ID com o sufixo de subitem, quadro) se o cartão pode ser duplicata de outro; `None` se não pode. Item é o mesmo só
    com o MESMO ID, o MESMO sufixo e no MESMO quadro: `28.24 F5` não duplica `28.24`, nem `31.90-C` duplica `31.90-D`."""
    m = _NOME_DO_ITEM.match(str(c.get("nome") or ""))
    if m:
        return m["id"] + re.sub(r"\s+", " ", (m["suf"] or "")).upper(), str(c.get("quadro"))
    p = _p_de(c)
    return (p, str(c.get("quadro"))) if p else None


def verificar_duplicados(cartoes: list[dict]) -> dict:
    """Dois cartões abertos com o mesmo ID de item (mesmo sufixo, mesmo quadro) ou o mesmo P-NNN no Execução."""
    por_chave: dict[tuple[str, str], int] = {}
    for c in cartoes:
        if _papel(c) == "prova":
            continue
        chave = _chave_de_duplicata(c)
        if chave:
            por_chave[chave] = por_chave.get(chave, 0) + 1
    achados = [(k, n) for k, n in sorted(por_chave.items()) if n > 1]
    return _resultado([f"{k[0]} ({n} cartões)" for k, n in achados], n=len(achados))


def verificar_sem_cartao(cartoes: list[dict], itens: list) -> dict:
    # a leitura de ID é a do reconciliar (aceita `🙋` e `#N ·`), a mesma de (1): as duas verificações não discordam
    com_cartao = {pid for c in cartoes if (pid := _id_de(c))}
    return _resultado([it.pid for it in ids_sem_cartao(itens, com_cartao)])


def verificar_perguntas(cartoes: list[dict]) -> dict:
    achados = []
    for c in cartoes:
        papel, p = _papel(c), _p_de(c)
        if not p:
            continue
        desc = str(c.get("desc") or "")
        if papel == "perguntas" and (ja_registrada(desc) or _RESPOSTA_NO_TOPO.match(desc)):
            achados.append(f"{p} (respondida, ainda em Perguntas para você)")
        elif papel == "perguntas_respondidas" and not (ja_registrada(desc) or _BLOCO_DATADO.match(desc)):
            achados.append(f"{p} (em Perguntas respondidas sem a resposta registrada)")
    return _resultado(achados)


def _aparelho_do_cartao(c: dict) -> str | None:
    """O rótulo `android-NN` do cartão de aparelho (só no Execução; cartão de item do plano nunca é de aparelho)."""
    if c.get("quadro") != "execucao" or _id_de(c) or _p_de(c):
        return None
    m = _LINHA_APARELHO.search(str(c.get("desc") or ""))
    return m.group(1) if m else None


def verificar_aparelhos(cartoes: list[dict]) -> dict:
    por_rotulo: dict[str, list[dict]] = {}
    for c in cartoes:
        if _papel(c) == "prova":
            continue
        rotulo = _aparelho_do_cartao(c)
        if rotulo:
            por_rotulo.setdefault(rotulo, []).append(c)
    achados = []
    for rotulo, cs in sorted(por_rotulo.items()):
        # o rótulo só sai se tem o formato do id da API (o mesmo que `cartoes_de_aparelho` aceita)
        nome = rotulo if _ROTULO.match(rotulo) else "aparelho de rótulo fora do formato"
        if len(cs) > 1:
            achados.append(f"{nome} (duplicado)")
            continue
        c = cs[0]
        lista_certa = LISTAS.get(c.get("id_lista"))
        estado = _LINHA_ESTADO.search(str(c.get("desc") or ""))
        esperada = _LISTA_DO_ESTADO_ESCRITO.get(estado.group(1)) if estado else None
        if lista_certa is None:
            achados.append(f"{nome} (fora das listas de aparelho)")
        elif esperada is not None and c.get("id_lista") != esperada:
            achados.append(f"{nome} (lista diferente da do estado escrito)")
    return _resultado(achados)


def verificar_sem_lista(cartoes: list[dict]) -> dict:
    achados = []
    for c in cartoes:
        papel = _papel(c)
        if papel == "prova":
            continue
        if not c.get("lista") or papel == "outra":
            achados.append(_quem(c))
    return _resultado(achados)


def verificar_concluidos(cartoes: list[dict], estado: dict, decidir_fn: Callable[[list[dict], dict], list]) -> dict:
    """Só os cartões do Execução em lista de trabalho cujo item o plano dá como implementado entram na decisão (o resto
    nunca vira "mover para Concluído"), então o Git só é consultado se há candidato."""
    itens = estado.get("itens", {})
    candidatos = []
    for c in cartoes:
        pid = _id_de(c)
        if c.get("quadro") != "execucao" or not pid or _papel(c) in NAO_E_TRABALHO:
            continue
        if (itens.get(pid) or {}).get("status") == "implemented":
            candidatos.append(c)
    if not candidatos:
        return _resultado([])
    acoes = decidir_fn(candidatos, estado)
    pids = []
    for a in acoes:
        if a.tipo == "mover" and a.para in ("concluido", "historico"):
            pid = R.id_do_item(a.nome)
            if pid:
                pids.append(pid)
    return _resultado(pids)


def auditar(cartoes: list[dict], *, itens_do_plano_: list | None, estado: dict | None,
            decidir_fn: Callable[[list[dict], dict], list] | None) -> dict:
    """Pura (menos o `decidir_fn`, que no uso real lê o Git). `cartoes`: [{"id", "nome", "lista" (nome), "id_lista", "desc",
    "quadro": "execucao"|"programa"|"historico"}]. Plano ou estado `None` = ilegível: 2 e 5 saem como não lidas."""
    v: dict[str, dict | None] = {
        "duplicados": verificar_duplicados(cartoes),
        "sem_cartao": verificar_sem_cartao(cartoes, itens_do_plano_) if itens_do_plano_ is not None else None,
        "pergunta_incoerente": verificar_perguntas(cartoes),
        "aparelho_incoerente": verificar_aparelhos(cartoes),
        "concluido_fora": (verificar_concluidos(cartoes, estado, decidir_fn)
                           if estado is not None and decidir_fn is not None else None),
        "sem_lista": verificar_sem_lista(cartoes),
    }
    por_quadro = {q: sum(1 for c in cartoes if c.get("quadro") == q) for q in NOMES_DOS_QUADROS}
    return {"ok": True, "quadros": por_quadro, "verificacoes": v,
            "nao_lidas": [k for k, x in v.items() if x is None],
            "total": sum(x["n"] for x in v.values() if x is not None)}


# --------------------------------------------------------------------------------------------------- leitura (rede)
class SoLeitura:
    """Envolve o cliente do Trello e recusa tudo o que não for GET: esta auditoria nunca escreve."""

    def __init__(self, cliente) -> None:  # noqa: ANN001
        self._c = cliente

    async def _pedir(self, metodo: str, caminho: str, **kw):  # noqa: ANN003, ANN202
        if metodo.upper() != "GET":
            raise PermissionError("a auditoria é só leitura")
        return await self._c._pedir(metodo, caminho, **kw)


async def ler_cartoes(cl) -> tuple[list[dict], dict[str, str]]:  # noqa: ANN001
    """Os cartões abertos dos 3 quadros (6 GETs) e as listas do Histórico (nome → id, para o `reconciliar.decidir`)."""
    cartoes: list[dict] = []
    hist: dict[str, str] = {}
    for quadro, bid in NOMES_DOS_QUADROS.items():
        ls = await cl._pedir("GET", f"/1/boards/{bid}/lists", params={"fields": "name", "filter": "open"})
        listas = {x["id"]: x["name"] for x in ls}
        if quadro == "historico":
            hist = {n: i for i, n in listas.items()}
        cs = await cl._pedir("GET", f"/1/boards/{bid}/cards", params={"fields": "name,idList,desc", "filter": "open"})
        cartoes += [{"id": c["id"], "nome": c["name"], "lista": listas.get(c["idList"], ""), "id_lista": c["idList"],
                     "desc": c.get("desc") or "", "quadro": quadro} for c in cs]
    return cartoes, hist


def _plano_da_raiz(raiz: Path) -> tuple[list | None, dict | None]:
    try:
        itens = itens_do_plano((raiz / "docs" / "plano-100.md").read_text(encoding="utf-8"))
    except OSError:
        itens = None
    try:
        estado = json.loads((raiz / ".claude" / "plano-100" / "estado.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        estado = None
    return itens, estado


def _decidir_real(raiz: Path, hist: dict[str, str], agora: datetime) -> Callable[[list[dict], dict], list]:
    def decidir(candidatos: list[dict], estado: dict) -> list:
        changelog = (raiz / "CHANGELOG.md").read_text(encoding="utf-8")
        por_commit = R.deploys_por_commit(changelog)
        return R.decidir(candidatos, estado, agora=agora, horas=R.horas_dos_deploys(raiz),
                         suite_de=lambda pid: R.suite_do_commit(pid, raiz), listas_do_historico=hist, redigir=_limpo,
                         citados=R.ids_citados_por_deploy(changelog), por_commit=por_commit,
                         deploy_git=lambda pid: R.deploy_pelo_git(pid, por_commit, raiz)).acoes
    return decidir


async def auditar_tudo(cliente, raiz: Path | None = None, agora: datetime | None = None) -> dict:  # noqa: ANN001
    """Lê (só GET) e audita. Nunca levanta: falha de leitura dos quadros vira `{"ok": False, "falha": <tipo do erro>}`."""
    raiz = raiz or RAIZ_CENTRAL
    agora = agora or datetime.now(timezone.utc)
    try:
        cartoes, hist = await ler_cartoes(SoLeitura(cliente))
    except Exception as ex:  # noqa: BLE001 - o texto do erro pode trazer URL ou chave: só o tipo sai
        return {"ok": False, "falha": type(ex).__name__}
    itens, estado = _plano_da_raiz(raiz)
    try:
        return auditar(cartoes, itens_do_plano_=itens, estado=estado, decidir_fn=_decidir_real(raiz, hist, agora))
    except Exception as ex:  # noqa: BLE001
        return {"ok": False, "falha": type(ex).__name__}


def novo_cliente():  # noqa: ANN201
    sys.path.insert(0, str(BACKEND_CENTRAL))
    from app.config import EnvSettings  # noqa: PLC0415 - só no modo de rede
    from app.modules.avisos.adapters.trello import ClienteTrello  # noqa: PLC0415

    e = EnvSettings()
    return ClienteTrello(e.trello_api_key.get_secret_value().strip(), e.trello_token.get_secret_value().strip())


# ------------------------------------------------------------------------------------------------------------ saída
def _n(valor: object) -> int:
    try:
        return max(0, int(valor))  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return 0


def secao_do_resumo(resultado: object) -> str:
    """UMA linha HTML do Telegram, no estilo do resumo diário. Leitura que falhou NUNCA vira "nada fora do lugar"."""
    rotulo = "• <b>Coerência dos quadros:</b> "
    if not isinstance(resultado, dict) or resultado.get("ok") is not True or not isinstance(resultado.get("verificacoes"), dict):
        return rotulo + "não consegui ler o Trello."
    v = resultado["verificacoes"]
    # verificação ausente ou nula = não lida (formato estranho nunca conta como "sem achado")
    lidas = [k for k in VERIFICACOES if isinstance(v.get(k), dict)]
    nao_lidas = [VERIFICACOES[k][0] for k in VERIFICACOES if k not in lidas]
    total = sum(_n(v[k].get("n")) for k in lidas)
    partes = ", ".join(f"{VERIFICACOES[k][0]} {_n(v[k].get('n'))}" for k in lidas if _n(v[k].get("n")))
    if total == 0:
        corpo = f"não consegui ler o plano ({', '.join(nao_lidas)})." if nao_lidas else "nada fora do lugar."
    else:
        corpo = f"{total} {'achado' if total == 1 else 'achados'} ({partes})"
        corpo += f"; sem leitura de {', '.join(nao_lidas)}." if nao_lidas else "."
    return rotulo + " ".join(html.escape(_limpo(corpo), quote=False).split())


def texto_do_console(resultado: dict) -> str:
    if not resultado.get("ok"):
        return f"não consegui ler os quadros ({_limpo(str(resultado.get('falha') or 'erro'))}); nada foi alterado"
    q = resultado.get("quadros", {})
    linhas = [f"Auditoria dos quadros (só leitura): Execução {_n(q.get('execucao'))}, Programa {_n(q.get('programa'))}, "
              f"Histórico {_n(q.get('historico'))} cartões; {_n(resultado.get('total'))} achados"]
    for k, (_curto, longo) in VERIFICACOES.items():
        x = (resultado.get("verificacoes") or {}).get(k)
        if not isinstance(x, dict):
            linhas.append(f"  {k}: não lida ({longo})")
            continue
        ex = ", ".join(str(e) for e in x.get("exemplos", []))
        resto = _n(x.get("n")) - len(x.get("exemplos", []))
        linhas.append(f"  {k}: {_n(x.get('n'))} ({longo})" + (f" — {ex}" if ex else "") + (f" e mais {resto}" if resto > 0 else ""))
    return "\n".join(linhas)


def main(argv: list[str] | None = None) -> int:
    for fluxo in (sys.stdout, sys.stderr):
        if hasattr(fluxo, "reconfigure"):
            fluxo.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--json", action="store_true", help="imprime o resultado em JSON (para outro script)")
    p.add_argument("--raiz", type=Path, default=RAIZ_CENTRAL, help="checkout de onde vêm o plano, o estado e o Git")
    a = p.parse_args(argv)
    try:
        cliente = novo_cliente()
    except Exception as ex:  # noqa: BLE001
        resultado: dict = {"ok": False, "falha": type(ex).__name__}
    else:
        resultado = asyncio.run(auditar_tudo(cliente, a.raiz.resolve()))
    print(json.dumps(resultado, ensure_ascii=False) if a.json else texto_do_console(resultado))
    return 0 if resultado.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
