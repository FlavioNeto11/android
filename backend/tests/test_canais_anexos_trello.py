"""28.24 F2: anexar ao cartão do Trello a imagem que o DONO mandou (exceção (b) do dono, 04/10 15:17Z).

Prova `simulated`: `AppState` do harness (SQLite), Trello falso por `httpx.MockTransport`; nenhuma rede, nenhum cartão real.
O que se trava: só anexo de ENTRADA de mensagem do DONO sobe; convidado, saída, recusado e apagado não; o cartão tem de ser de um
quadro configurado; o nome no cartão é neutro; a chave e o token do Trello ficam no cabeçalho e fora de resposta, erro e log.
"""
from __future__ import annotations

import json
import logging

import httpx
import pytest

from app.main import create_app
from app.modules.avisos.adapters.trello import ClienteTrello
from app.modules.avisos.infrastructure.entrada_sql import EntradasDoCanal

from .conftest import Harness

pytestmark = pytest.mark.asyncio

CHAVE = "chave-trello-de-teste-77aa"
TOKEN = "token-trello-de-teste-99bb"
QUADRO = "b" * 24
CARTAO = "c" * 24
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x02" * 60


class TrelloFalso:
    def __init__(self) -> None:
        self.pedidos: list[tuple[str, str, dict[str, str], bytes]] = []
        self.quadro = QUADRO
        self.falha_no_anexo: tuple[int, str] | None = None

    def handler(self, req: httpx.Request) -> httpx.Response:
        self.pedidos.append((req.method, req.url.path, dict(req.headers), req.content))
        assert CHAVE not in str(req.url) and TOKEN not in str(req.url)                # a credencial NUNCA vai na URL
        if req.method == "GET" and req.url.path == f"/1/cards/{CARTAO}":
            return httpx.Response(200, json={"id": CARTAO, "idBoard": self.quadro})
        if req.method == "POST" and req.url.path == f"/1/cards/{CARTAO}/attachments":
            if self.falha_no_anexo is not None:
                return httpx.Response(self.falha_no_anexo[0], text=self.falha_no_anexo[1])
            return httpx.Response(200, json={"id": "a" * 24, "url": "https://trello.com/1/cards/x/attachments/y/download/n"})
        return httpx.Response(404, text="not found")

    def anexou(self) -> int:
        return sum(1 for m, p, _, _ in self.pedidos if m == "POST" and p.endswith("/attachments"))


@pytest.fixture
def trello() -> TrelloFalso:
    return TrelloFalso()


def _ligar(h: Harness, trello: TrelloFalso) -> None:
    h.cfg.file.trello.enabled = True
    h.cfg.file.trello.quadros = [QUADRO]
    h.state.trello_espelho._cliente = ClienteTrello(                                # noqa: SLF001 - o cliente do teste
        CHAVE, TOKEN, client=httpx.AsyncClient(transport=httpx.MockTransport(trello.handler)))


def _anexo(h: Harness, *, do_dono: bool = True, tipo: str = "mensagem", direcao: str = "entrada", canal: str = "telegram",
           estado: str = "guardado") -> int:
    repo = EntradasDoCanal(h.state.db, canal=canal)
    n = int(h.state.db.scalar("SELECT COUNT(*) FROM canal_entradas") or 0) + 1
    repo.gravar(id_externo=str(n), ordem=n, tipo=tipo, do_dono=do_dono, ref_mensagem=str(n), responde_a=None, texto=None,
                tamanho=0, estado="ignorada")
    entrada = repo.id_de(str(n))
    arm = h.state.anexos_canal
    if estado == "guardado":
        linha = arm.guardar(PNG, tipos=("image/png",), max_bytes=10_000, entrada_id=entrada, direcao=direcao)
    else:
        linha = arm.recusar("voz não é aceita.", entrada_id=entrada)
        if estado == "apagado":
            h.state.db.execute("UPDATE canal_anexos SET estado='apagado' WHERE id=?", (linha["id"],))
    return int(linha["id"])


def _cliente(h: Harness) -> httpx.AsyncClient:
    app = create_app(h.cfg, state=h.state)
    app.state.poc = h.state
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 123)), base_url="http://localhost")


async def _postar(h: Harness, ident: int, corpo: dict[str, object]) -> httpx.Response:
    async with _cliente(h) as cli:
        return await cli.post(f"/api/canais/anexos/{ident}/trello", json=corpo)


async def test_a_imagem_do_dono_vai_ao_cartao_com_nome_neutro_e_credencial_so_no_cabecalho(
        harness: Harness, trello: TrelloFalso, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    _ligar(harness, trello)
    ident = _anexo(harness)
    r = await _postar(harness, ident, {"card": CARTAO, "confirmar": True})
    assert r.status_code == 200 and r.json() == {"anexo_id": ident, "card": CARTAO, "trello_anexo": "a" * 24}
    [(_, caminho, cab, corpo)] = [p for p in trello.pedidos if p[0] == "POST"]
    assert caminho == f"/1/cards/{CARTAO}/attachments" and cab["content-type"].startswith("multipart/form-data")
    assert PNG in corpo and b'filename="anexo-' in corpo and b".png" in corpo
    assert CHAVE in cab["authorization"] and TOKEN in cab["authorization"]               # só no cabeçalho
    tudo = r.text + " | ".join(logging.Formatter().format(x) for x in caplog.records
                               if not x.name.startswith(("httpx", "httpcore")))
    assert CHAVE not in tudo and TOKEN not in tudo and CARTAO not in tudo.replace(r.text, "")


@pytest.mark.parametrize("quem", [
    {"do_dono": False},                 # o convidado
    {"direcao": "saida"},               # o que a Central mandou (a captura de aparelho tem a exceção (a), não esta)
    {"tipo": "botao"},
    {"estado": "recusado"},
    {"estado": "apagado"},
])
async def test_so_o_anexo_de_entrada_do_dono_e_guardado_sobe(harness: Harness, trello: TrelloFalso,
                                                              quem: dict[str, object]) -> None:
    _ligar(harness, trello)
    ident = _anexo(harness, **quem)                                                 # type: ignore[arg-type]
    r = await _postar(harness, ident, {"card": CARTAO, "confirmar": True})
    assert r.status_code == 409 and r.json()["detail"]["code"] in ("anexo_nao_permitido", "anexo_sem_arquivo")
    assert trello.pedidos == []                                                     # nem a API foi consultada


async def test_exige_confirmacao_cartao_valido_quadro_configurado_e_trello_ligado(harness: Harness,
                                                                                  trello: TrelloFalso) -> None:
    ident = _anexo(harness)
    ok = {"card": CARTAO, "confirmar": True}
    r = await _postar(harness, ident, {"card": CARTAO})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "confirmacao_necessaria"
    r = await _postar(harness, ident, ok)                                           # Trello desligado
    assert r.status_code == 503 and r.json()["detail"]["code"] == "trello_desligado"
    _ligar(harness, trello)
    for ruim in ("../x", "abc", CARTAO.upper(), CARTAO + "0"):
        r = await _postar(harness, ident, {"card": ruim, "confirmar": True})
        assert r.status_code == 422 and r.json()["detail"]["code"] == "cartao_invalido", ruim
    assert (await _postar(harness, 99999, ok)).status_code == 404
    trello.quadro = "d" * 24                                                        # cartão de um quadro que não é nosso
    r = await _postar(harness, ident, ok)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "cartao_fora_dos_quadros" and trello.anexou() == 0


async def test_falha_do_trello_vira_502_sem_chave_nem_token(harness: Harness, trello: TrelloFalso) -> None:
    _ligar(harness, trello)
    ident = _anexo(harness)
    trello.falha_no_anexo = (500, f"erro interno {CHAVE} {TOKEN}")
    r = await _postar(harness, ident, {"card": CARTAO, "confirmar": True})
    assert r.status_code == 502 and r.json()["detail"]["code"] == "trello_falhou"
    assert CHAVE not in r.text and TOKEN not in r.text


async def test_o_cliente_so_escreve_o_anexo_pelo_metodo_novo_e_nao_leva_credencial_na_url(trello: TrelloFalso) -> None:
    cli = ClienteTrello(CHAVE, TOKEN, client=httpx.AsyncClient(transport=httpx.MockTransport(trello.handler)))
    assert await cli.anexar_arquivo(CARTAO, PNG, "image/png", "anexo-x.png") == "a" * 24
    assert await cli.quadro_do_cartao(CARTAO) == QUADRO
    assert json.dumps([p[1] for p in trello.pedidos]).count("oauth") == 0


