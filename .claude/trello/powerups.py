"""Liga e configura os Power-Ups nativos aprovados pelo dono (03/10 ~19:05Z: "sim pra tudo", itens 1 a 5 do estudo).

O conector MCP do Trello não liga Power-Up; a API sim (`boardPlugins`), com a chave e o token do dono que ficam só no
`.env` (`TRELLO_API_KEY`, `TRELLO_TOKEN`). Lidos pelo `EnvSettings`, nunca impressos: a autenticação vai no cabeçalho
`Authorization` (não na URL, que o httpx loga) e a saída é só status e o nome do Power-Up.

Pare se a resposta pedir pagamento ou plano pago: o script não insiste e diz qual foi.

Uso (python do backend/.venv, a partir de C:/git/android):
  powerups.py --checar      só True/False: a chave e o token estão no .env?
  powerups.py --estado      Power-Ups ligados em cada quadro
  powerups.py --ligar       liga os aprovados (idempotente) e cria os campos personalizados que faltarem
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import httpx

RAIZ = Path(r"C:\git\android")
sys.path.insert(0, str(RAIZ / "backend"))
from pydantic import Field, SecretStr  # noqa: E402

from app.config import EnvSettings  # noqa: E402

logging.getLogger("httpx").setLevel(logging.WARNING)
API = "https://api.trello.com/1"
EST = json.loads((Path(__file__).with_name("estrutura.json")).read_text(encoding="utf-8"))


def _id(ari: str) -> str:
    return ari.rsplit("/", 1)[-1]


QUADRO = {q: _id(EST["quadros"][q]["id"]) for q in ("execucao", "programa", "historico")}
#: ids do diretório público (autor "Trello Inc"), lidos em 03/10 18:59Z
PLUGINS = {
    "Custom Fields": ("56d5e249a98895a9797bebb9", ("execucao", "historico")),
    "Calendar Power-Up": ("55a5d917446f517774210011", ("execucao",)),
    "Card Aging": ("55a5d917446f517774210012", ("execucao",)),
    "List Limits": ("5c2462c384ab8949b1724a20", ("execucao",)),
    "Dashcards": ("6048e897c73d032a983e2a7c", ("programa",)),
}
#: campos do modelo de cartão (skill trello): Frente, Prova, Esforço, Suíte e Custo pago
CAMPOS = [
    {"name": "Frente", "type": "list",
     "options": ["Android", "Jev", "Aprendizado", "Canais", "Orquestradora", "Avulsa", "Plataforma"]},
    {"name": "Prova", "type": "list", "options": ["real", "simulated", "not_run"]},
    {"name": "Esforço", "type": "text"},
    {"name": "Suíte", "type": "number"},
    {"name": "Custo pago", "type": "text"},
]
_PAGO = ("upgrade", "premium", "standard", "payment", "billing", "paid", "pago")


class _Env(EnvSettings):
    trello_api_key: SecretStr | None = Field(default=None, alias="TRELLO_API_KEY")
    trello_token: SecretStr | None = Field(default=None, alias="TRELLO_TOKEN")


def _segredo(v: SecretStr | None) -> str:
    return v.get_secret_value() if v is not None else ""


def _cliente() -> httpx.Client:
    env = _Env()
    chave, token = _segredo(env.trello_api_key), _segredo(env.trello_token)
    if not chave or not token:
        raise SystemExit("TRELLO_API_KEY ou TRELLO_TOKEN ausente no .env")
    auth = f'OAuth oauth_consumer_key="{chave}", oauth_token="{token}"'
    return httpx.Client(base_url=API, headers={"Authorization": auth}, timeout=20)


def _resumo(r: httpx.Response) -> str:
    texto = r.text[:200].replace("\n", " ")
    # a resposta de erro do Trello não ecoa credencial, mas por garantia não imprime nada que pareça token longo
    return "".join("…" if len(p) > 40 else p + " " for p in texto.split()).strip()


def checar() -> int:
    env = _Env()
    print(f"TRELLO_API_KEY presente: {bool(_segredo(env.trello_api_key))}")
    print(f"TRELLO_TOKEN presente: {bool(_segredo(env.trello_token))}")
    return 0


def estado() -> int:
    with _cliente() as c:
        for nome, bid in QUADRO.items():
            r = c.get(f"/boards/{bid}/boardPlugins")
            ligados = [p.get("idPlugin") for p in r.json()] if r.status_code == 200 else []
            nomes = [n for n, (pid, _) in PLUGINS.items() if pid in ligados]
            print(f"{nome}: {r.status_code} · {', '.join(nomes) or 'nenhum dos aprovados'}")
    return 0


def ligar() -> int:
    with _cliente() as c:
        for nome, (pid, quadros) in PLUGINS.items():
            for q in quadros:
                bid = QUADRO[q]
                r = c.get(f"/boards/{bid}/boardPlugins")
                if r.status_code == 200 and any(p.get("idPlugin") == pid for p in r.json()):
                    print(f"{q} · {nome}: já ligado")
                    continue
                r = c.post(f"/boards/{bid}/boardPlugins", params={"idPlugin": pid})
                if r.status_code != 200:
                    pago = any(x in r.text.lower() for x in _PAGO)
                    print(f"{q} · {nome}: FALHOU {r.status_code}{' (pede plano pago: parado)' if pago else ''} · "
                          f"{_resumo(r)}")
                    if pago:
                        return 3
                    continue
                print(f"{q} · {nome}: ligado")
        for q in PLUGINS["Custom Fields"][1]:
            bid = QUADRO[q]
            r = c.get(f"/boards/{bid}/customFields")
            existentes = {f.get("name") for f in r.json()} if r.status_code == 200 else set()
            for pos, campo in enumerate(CAMPOS, start=1):
                if campo["name"] in existentes:
                    continue
                corpo = {"idModel": bid, "modelType": "board", "name": campo["name"], "type": campo["type"],
                         "pos": pos, "display_cardFront": campo["name"] in ("Frente", "Prova")}
                if campo["type"] == "list":
                    corpo["options"] = [{"value": {"text": o}, "pos": i} for i, o in enumerate(campo["options"], 1)]
                r = c.post("/customFields", json=corpo)
                print(f"{q} · campo {campo['name']}: {'criado' if r.status_code == 200 else f'FALHOU {r.status_code} · {_resumo(r)}'}")
    print("List Limits: os limites (8 em Em execução, 10 em Em validação) se definem no menu da lista; a API não "
          "grava os dados do Power-Up de outro autor.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--checar", action="store_true")
    g.add_argument("--estado", action="store_true")
    g.add_argument("--ligar", action="store_true")
    a = ap.parse_args()
    return checar() if a.checar else estado() if a.estado else ligar()


if __name__ == "__main__":
    raise SystemExit(main())
