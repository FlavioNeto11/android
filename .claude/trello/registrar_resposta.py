"""28.65: registra a resposta do dono a um cartão de pergunta, move o cartão e cria a decisão em Programa.

Antes, a cada resposta a Canais rodava à mão um script avulso. Este faz os três passos de uma vez, e repetido não duplica:
  (a) põe no TOPO da descrição do cartão da pergunta o bloco "**RESPOSTA DO DONO (...)**" com o literal e a leitura,
      separado do resto por uma linha `---`;
  (b) move o cartão para "✔️ Perguntas respondidas" (Execução), no topo, e acrescenta " · respondida em DD/MM" ao nome;
  (c) cria o cartão de decisão em Programa › Decisões do dono ("⚖️ <nome>", com a data), com o mesmo bloco e o link do
      cartão da pergunta, no topo.

O literal do dono vai como está (é o que ele escreveu; o redator só AVISA na saída se mudaria algo, para a Canais decidir).
A leitura e a confirmação são da Canais e passam por `redacao.redigir` antes de ir ao Trello.

Uso (da raiz):
  backend/.venv/Scripts/python.exe .claude/trello/registrar_resposta.py --cartao <id curto ou completo> --entrada 3573 \\
      --canal trello|telegram --quando "06/10 19:38Z" --literal "<texto exato>" [--leitura "..."] \\
      [--confirmacao "Telegram, entrada 3576, 'respondido'"] [--aplicar]
Sem `--aplicar` só relata o que faria (ensaio).
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
from dataclasses import dataclass
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))
BACKEND_CENTRAL = Path(r"C:\git\android\backend")

LISTA_RESPONDIDAS = "6ac3c2040bf1463a15b96aac"     # Execução › ✔️ Perguntas respondidas
LISTA_DECISOES = "6ac13b2880553e615cef8845"        # Programa › Decisões do dono
PREFIXO_REGISTRO = "**RESPOSTA DO DONO"
SEPARADOR = "\n\n---\n\n"
PREFIXO_DECISAO = "⚖️ "
MARCA_DATA = " · respondida em "
_CANAIS = {"trello": "digitada por ele no app do Trello", "telegram": "digitada por ele no Telegram"}


@dataclass
class Acao:
    tipo: str                       # "descrever" | "mover" | "renomear" | "criar_decisao"
    nome: str = ""
    desc: str = ""


def data_curta(quando: str) -> str:
    """DD/MM de "06/10 19:38Z"; erro claro se o formato não for esse (a data entra no título, não se adivinha)."""
    m = re.match(r"\s*(\d{2}/\d{2})\b", quando)
    if not m:
        raise ValueError(f'--quando deve começar por DD/MM (ex.: "06/10 19:38Z"), veio {quando!r}')
    return m.group(1)


def nome_base(nome: str) -> str:
    """O nome sem o prefixo da decisão e sem o sufixo de data: a chave que liga pergunta e decisão."""
    n = nome[len(PREFIXO_DECISAO):] if nome.startswith(PREFIXO_DECISAO) else nome
    return n.split(MARCA_DATA)[0].strip()


def nome_com_data(nome: str, data: str) -> str:
    """Acrescenta ' · respondida em DD/MM' se o nome ainda não tem a marca (idempotente)."""
    return nome if MARCA_DATA in nome else f"{nome}{MARCA_DATA}{data}"


def titulo_decisao(nome: str, data: str) -> str:
    return PREFIXO_DECISAO + nome_com_data(nome_base(nome), data)


def ja_registrada(desc: str) -> bool:
    return (desc or "").lstrip().startswith(PREFIXO_REGISTRO)


def montar_registro(*, quando: str, entrada: int, canal: str, literal: str, leitura: str = "",
                    confirmacao: str = "", redigir=lambda t: t) -> str:  # noqa: ANN001
    """O bloco do topo. O literal NÃO passa pelo redator (é a palavra dele); leitura e confirmação passam."""
    if canal not in _CANAIS:
        raise ValueError(f"canal deve ser um de {sorted(_CANAIS)}")
    texto = (f'{PREFIXO_REGISTRO} ({quando.strip()}, entrada {entrada}, {_CANAIS[canal]}; conferida no banco):** '
             f'"{literal.strip()}".')
    if confirmacao.strip():
        texto += f" Confirmada em bloco: {redigir(confirmacao.strip()).rstrip('.')}."
    if leitura.strip():
        texto += f" Lido como: {redigir(leitura.strip())}"
    return texto.rstrip() + " Repassada literal à orquestradora."


def descricao_da_decisao(registro: str, curto: str) -> str:
    return f"{registro}\n\nCartão da pergunta (Perguntas respondidas): https://trello.com/c/{curto}"


def decidir(cartao: dict[str, str], decisoes: list[str], registro: str, data: str) -> list[Acao]:
    """`cartao`: name, desc, idList, shortLink. `decisoes`: nomes dos cartões abertos em Decisões do dono.

    Só devolve o que falta: com o bloco já no topo não reescreve a descrição (usa o que está lá para a decisão); já
    na lista de respondidas não move; decisão com o mesmo nome-base não cria outra. Assim uma execução que caiu no meio
    se completa na repetição."""
    ja = ja_registrada(cartao.get("desc", ""))
    registro_efetivo = cartao["desc"].split(SEPARADOR, 1)[0] if ja else registro
    acoes: list[Acao] = []
    if not ja:
        acoes.append(Acao("descrever", desc=registro + SEPARADOR + cartao.get("desc", "")))
    novo = nome_com_data(cartao["name"], data)
    if cartao.get("idList") != LISTA_RESPONDIDAS:
        acoes.append(Acao("mover", nome=novo))
    elif not ja and novo != cartao["name"]:
        acoes.append(Acao("renomear", nome=novo))
    if nome_base(cartao["name"]) not in {nome_base(d) for d in decisoes}:
        acoes.append(Acao("criar_decisao", nome=titulo_decisao(cartao["name"], data),
                          desc=descricao_da_decisao(registro_efetivo, cartao.get("shortLink", ""))))
    return acoes


async def _principal(args: argparse.Namespace) -> int:
    # as chaves do Trello moram no checkout central (por instalação, fora do Git); o worktree não as tem
    sys.path.insert(0, str(BACKEND_CENTRAL))
    from app.config import EnvSettings  # noqa: PLC0415 - só no modo de rede
    from app.modules.avisos.adapters.trello import ClienteTrello  # noqa: PLC0415
    from redacao import redigir  # noqa: PLC0415
    if redigir(args.literal) != args.literal:
        print("AVISO: o redator mudaria o literal do dono (vai como está; confira se algum trecho deve ser mascarado):")
        print("  ->", redigir(args.literal))
    data = data_curta(args.quando)
    registro = montar_registro(quando=args.quando, entrada=args.entrada, canal=args.canal, literal=args.literal,
                               leitura=args.leitura, confirmacao=args.confirmacao, redigir=redigir)
    e = EnvSettings()
    cl = ClienteTrello(e.trello_api_key.get_secret_value().strip(), e.trello_token.get_secret_value().strip())
    cartao = await cl._pedir("GET", f"/1/cards/{args.cartao}", params={"fields": "name,desc,idList,shortLink"})
    existentes = await cl._pedir("GET", f"/1/lists/{LISTA_DECISOES}/cards", params={"fields": "name", "filter": "open"})
    acoes = decidir(cartao, [c["name"] for c in existentes], registro, data)
    print(f"cartão: {cartao['name'][:80]} ({cartao['shortLink']})")
    if ja_registrada(cartao.get("desc", "")):
        print("já registrada: a descrição já começa com a resposta do dono (não reescrevo)")
    if not acoes:
        print("nada a fazer: tudo já está no lugar")
    for a in acoes:
        print(f"  {a.tipo:14} {(a.nome or a.desc)[:100]}")
    if not args.aplicar:
        print("(ensaio: nada foi escrito; use --aplicar)")
        return 0
    cid = cartao["id"]
    for a in acoes:
        if a.tipo == "descrever":
            await cl.atualizar_cartao(cid, desc=a.desc)
        elif a.tipo == "mover":
            await cl._pedir("PUT", f"/1/cards/{cid}", corpo={"idList": LISTA_RESPONDIDAS, "pos": "top", "name": a.nome})
        elif a.tipo == "renomear":
            await cl.atualizar_cartao(cid, nome=a.nome)
        else:
            r = await cl.criar_cartao(LISTA_DECISOES, a.nome, a.desc)
            await cl._pedir("PUT", f"/1/cards/{r['id']}", corpo={"pos": "top"})
            print("  decisão:", r.get("shortUrl"))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--cartao", required=True, help="id curto (do link) ou completo do cartão da pergunta")
    p.add_argument("--entrada", required=True, type=int, help="número da entrada no banco")
    p.add_argument("--canal", required=True, choices=sorted(_CANAIS))
    p.add_argument("--quando", required=True, help='ex.: "06/10 19:38Z"')
    p.add_argument("--literal", required=True, help="texto exato do dono (vai como está)")
    p.add_argument("--leitura", default="", help="como a Canais leu a resposta")
    p.add_argument("--confirmacao", default="", help="confirmação em bloco, ex.: Telegram, entrada 3576, 'respondido'")
    p.add_argument("--aplicar", action="store_true")
    args = p.parse_args(argv)
    data_curta(args.quando)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]  # console do Windows é cp1252
    return asyncio.run(_principal(args))


if __name__ == "__main__":
    raise SystemExit(main())
