"""Item 32.2 (2/6) — o cliente REST do Trello (ADR-072): autenticação só no cabeçalho, balde de 60 por 10 s, 429, erros
sem segredo.

Prova `simulated`: `httpx.MockTransport`, relógio e sono falsos, nenhuma rede.
"""
from __future__ import annotations

import httpx
import pytest

from app.modules.avisos.adapters.trello import (API, BALDE_LIMITE, BaldeDeRequisicoes, ClienteTrello, FalhaDoTrello,
                                             MAX_REPETICOES)

CHAVE = "chave-falsa-0123456789abcdef"
TOKEN = "token-falso-fedcba9876543210"


class Relogio:
    """Tempo falso: `dormir` só anda o relógio, e guarda o que lhe pediram."""

    def __init__(self) -> None:
        self.t = 1000.0
        self.sonos: list[float] = []

    def __call__(self) -> float:
        return self.t

    async def dormir(self, s: float) -> None:
        self.sonos.append(s)
        self.t += s


class Servidor:
    """O Trello falso: grava os pedidos e responde pela fila de respostas (a última se repete)."""

    def __init__(self, *respostas: httpx.Response) -> None:
        self.respostas = list(respostas) or [httpx.Response(200, json={})]
        self.pedidos: list[httpx.Request] = []

    def __call__(self, pedido: httpx.Request) -> httpx.Response:
        self.pedidos.append(pedido)
        return self.respostas.pop(0) if len(self.respostas) > 1 else self.respostas[0]


def _cliente(servidor: Servidor, relogio: Relogio | None = None) -> tuple[ClienteTrello, Relogio]:
    relogio = relogio or Relogio()
    balde = BaldeDeRequisicoes(relogio=relogio, dormir=relogio.dormir)
    http = httpx.AsyncClient(transport=httpx.MockTransport(servidor))
    return ClienteTrello(CHAVE, TOKEN, client=http, balde=balde, dormir=relogio.dormir), relogio


def _sem_segredo_na_url(pedido: httpx.Request) -> bool:
    url = str(pedido.url)
    return CHAVE not in url and TOKEN not in url


# --------------------------------------------------------------------- autenticação
async def test_autentica_so_no_cabecalho_e_nunca_na_url() -> None:
    srv = Servidor(httpx.Response(200, json={"id": "a1"}), httpx.Response(200, json=[]),
                   httpx.Response(200, json={"id": "c1"}), httpx.Response(200, json={"id": "c1"}),
                   httpx.Response(200, json={"id": "k1"}), httpx.Response(200, json={}),
                   httpx.Response(200, json={"id": "w1"}), httpx.Response(200, json={}))
    cli, _ = _cliente(srv)
    await cli.acao("a1")
    await cli.acoes_do_quadro("q1", "2026-10-03T00:00:00.000Z")
    await cli.criar_cartao("l1", "Nome", "Descrição")
    await cli.atualizar_cartao("c1", nome="Outro")
    await cli.comentar("c1", "oi")
    await cli.arquivar_cartao("c1")
    await cli.criar_webhook("q1", "https://exemplo.test/hook", "central-de-aparelhos:q1")
    await cli.apagar_webhook("w1")
    assert len(srv.pedidos) == 8
    for p in srv.pedidos:
        assert p.headers["authorization"] == f'OAuth oauth_consumer_key="{CHAVE}", oauth_token="{TOKEN}"'
        assert _sem_segredo_na_url(p), "a chave e o token não podem ir na URL"
        assert not {"key", "token"} & set(dict(p.url.params)), "nem como parâmetro"
        assert CHAVE.encode() not in p.content and TOKEN.encode() not in p.content, "nem no corpo"


async def test_webhook_e_webhooks_do_membro_sem_token_na_url_e_so_devolvem_o_webhook() -> None:
    wh = {"id": "w1", "description": "central-de-aparelhos:q1", "idModel": "q1", "callbackURL": "https://x.test/h",
          "active": True, "consecutiveFailures": 0}
    tokens = [{"id": "t1", "identifier": "ident-do-token", "idMember": "m1", "permissions": [{"idModel": "q1"}],
               "dateExpires": None, "webhooks": [wh, {"id": "w2", "idModel": "q2", "callbackURL": "https://y.test/h",
                                                      "active": False}]},
              {"id": "t2", "identifier": "outro", "webhooks": []}, {"id": "t3"}]
    srv = Servidor(httpx.Response(200, json=wh), httpx.Response(200, json=tokens))
    cli, _ = _cliente(srv)
    assert await cli.webhook("w1") == {"id": "w1", "description": "central-de-aparelhos:q1", "idModel": "q1",
                                       "callbackURL": "https://x.test/h", "active": True}
    lista = await cli.webhooks_do_membro()
    assert lista == [
        {"id": "w1", "description": "central-de-aparelhos:q1", "idModel": "q1", "callbackURL": "https://x.test/h",
         "active": True},
        {"id": "w2", "idModel": "q2", "callbackURL": "https://y.test/h", "active": False}]
    assert "ident-do-token" not in repr(lista) and "permissions" not in repr(lista), "nenhum campo de token sai"
    um, dois = srv.pedidos
    assert (um.method, um.url.path, um.url.query) == ("GET", "/1/webhooks/w1", b"")
    assert (dois.method, dois.url.path, dois.url.query) == ("GET", "/1/members/me/tokens", b"webhooks=true")
    for p in srv.pedidos:
        assert _sem_segredo_na_url(p) and p.headers["authorization"].startswith("OAuth oauth_consumer_key=")


async def test_webhooks_do_membro_recusa_resposta_que_nao_e_lista() -> None:
    cli, _ = _cliente(Servidor(httpx.Response(200, json={"id": "t1"})))
    with pytest.raises(FalhaDoTrello):
        await cli.webhooks_do_membro()


async def test_nenhum_metodo_publico_poe_a_chave_ou_o_token_em_qualquer_parte_da_url() -> None:
    """Percorre TODOS os métodos públicos do cliente e confere cada `request.url` (caminho, query e fragmento)."""
    import inspect

    srv = Servidor(httpx.Response(200, json={"id": "x1"}))
    cli, _ = _cliente(srv)
    chamadas = {
        "acao": ("a1",), "acoes_do_quadro": ("q1", "2026-10-03T00:00:00.000Z"),
        "criar_cartao": ("l1", "N", "D"), "atualizar_cartao": ("c1",), "arquivar_cartao": ("c1",),
        "comentar": ("c1", "oi"), "cartoes_da_lista": ("l1",), "comentarios": ("c1", 5), "webhook": ("w1",), "webhooks_do_membro": (),
        "criar_webhook": ("q1", "https://exemplo.test/h", "d"), "apagar_webhook": ("w1",)}
    nomes = {n for n, f in inspect.getmembers(ClienteTrello, inspect.iscoroutinefunction) if not n.startswith("_")}
    assert nomes == set(chamadas), f"método público sem cobertura neste teste: {nomes ^ set(chamadas)}"
    for nome, args in chamadas.items():
        antes = len(srv.pedidos)
        srv.respostas = [httpx.Response(200, json=[]) if nome in ("webhooks_do_membro", "cartoes_da_lista", "comentarios") else httpx.Response(200, json={"id": "x1"})]
        kw = {"nome": "Outro"} if nome == "atualizar_cartao" else {}
        await getattr(cli, nome)(*args, **kw)
        assert len(srv.pedidos) == antes + 1, nome
        url = srv.pedidos[-1].url
        for parte in (str(url), url.path, url.query.decode(), url.fragment, url.host):
            assert CHAVE not in parte and TOKEN not in parte, f"{nome}: segredo na URL"
        assert not {"key", "token"} & set(dict(url.params)), nome


async def test_os_pedidos_levam_o_metodo_o_caminho_e_o_corpo_certos() -> None:
    srv = Servidor(httpx.Response(200, json={"id": "c9"}), httpx.Response(200, json={"id": "c9"}),
                   httpx.Response(200, json={"id": "c9"}), httpx.Response(200, json={"id": "k7"}),
                   httpx.Response(200, json=[]))
    cli, _ = _cliente(srv)
    assert await cli.criar_cartao("l1", "Aprovar X", "texto") == {"id": "c9"}
    await cli.atualizar_cartao("c9", desc="nova", lista="l2")
    await cli.arquivar_cartao("c9")
    assert await cli.comentar("c9", "feito") == "k7"
    await cli.acoes_do_quadro("q1", None, filtro="commentCard", limite=50)
    metodos = [(p.method, p.url.path) for p in srv.pedidos]
    assert metodos == [("POST", "/1/cards"), ("PUT", "/1/cards/c9"), ("PUT", "/1/cards/c9"),
                       ("POST", "/1/cards/c9/actions/comments"), ("GET", "/1/boards/q1/actions")]
    import json as _json
    assert _json.loads(srv.pedidos[0].content) == {"idList": "l1", "name": "Aprovar X", "desc": "texto"}
    assert _json.loads(srv.pedidos[1].content) == {"desc": "nova", "idList": "l2"}   # só o que mudou
    assert _json.loads(srv.pedidos[2].content) == {"closed": True}
    assert _json.loads(srv.pedidos[3].content) == {"text": "feito"}
    q = dict(srv.pedidos[4].url.params)
    assert q == {"filter": "commentCard", "limit": "50"}, "sem `since` quando não há cursor"


async def test_desde_vira_since_e_o_filtro_padrao_e_o_do_desenho() -> None:
    srv = Servidor(httpx.Response(200, json=[]))
    cli, _ = _cliente(srv)
    await cli.acoes_do_quadro("q1", "2026-10-03T12:00:00.000Z")
    q = dict(srv.pedidos[0].url.params)
    assert q["since"] == "2026-10-03T12:00:00.000Z" and q["limit"] == "1000"
    assert q["filter"] == "commentCard,updateCard:idList,createCard"


async def test_atualizar_cartao_sem_campo_e_erro_de_programacao() -> None:
    cli, _ = _cliente(Servidor())
    with pytest.raises(ValueError):
        await cli.atualizar_cartao("c1")


async def test_comentar_sem_id_na_resposta_e_falha() -> None:
    cli, _ = _cliente(Servidor(httpx.Response(200, json={"sem": "id"})))
    with pytest.raises(FalhaDoTrello):
        await cli.comentar("c1", "oi")


def test_sem_chave_ou_token_nao_constroi() -> None:
    with pytest.raises(ValueError):
        ClienteTrello("", TOKEN)
    with pytest.raises(ValueError):
        ClienteTrello(CHAVE, "")


# --------------------------------------------------------------------- 429
async def test_429_com_retry_after_espera_e_repete() -> None:
    srv = Servidor(httpx.Response(429, headers={"Retry-After": "7"}, text="rate limit"),
                   httpx.Response(200, json={"id": "a1"}))
    cli, rel = _cliente(srv)
    assert await cli.acao("a1") == {"id": "a1"}
    assert len(srv.pedidos) == 2
    assert 7.0 in rel.sonos, "esperou o que o Trello pediu"


async def test_429_sem_retry_after_usa_o_intervalo_do_x_rate_limit_ou_10s() -> None:
    srv = Servidor(httpx.Response(429, headers={"x-rate-limit-api-token-interval-ms": "10000"}),
                   httpx.Response(429), httpx.Response(200, json={}))
    cli, rel = _cliente(srv)
    await cli.acao("a1")
    assert rel.sonos[:2] == [10.0, 10.0]


async def test_429_persistente_vira_falha_com_status_429_apos_3_repeticoes() -> None:
    srv = Servidor(httpx.Response(429, headers={"Retry-After": "1"}))
    cli, rel = _cliente(srv)
    with pytest.raises(FalhaDoTrello) as e:
        await cli.acao("a1")
    assert e.value.status == 429 and not e.value.definitiva
    assert len(srv.pedidos) == MAX_REPETICOES + 1 == 4
    assert rel.sonos.count(1.0) == MAX_REPETICOES


async def test_retry_after_absurdo_e_limitado() -> None:
    srv = Servidor(httpx.Response(429, headers={"Retry-After": "99999"}), httpx.Response(200, json={}))
    cli, rel = _cliente(srv)
    await cli.acao("a1")
    assert max(rel.sonos) <= 60.0


# --------------------------------------------------------------------- erros
@pytest.mark.parametrize("status", [401, 403])
async def test_credencial_recusada_e_definitiva_e_o_motivo_nao_tem_segredo(status: int) -> None:
    # o Trello às vezes ecoa o que recebeu: o motivo tem de sair limpo mesmo assim
    srv = Servidor(httpx.Response(status, text=f"invalid token {TOKEN} for key {CHAVE}"))
    cli, _ = _cliente(srv)
    with pytest.raises(FalhaDoTrello) as e:
        await cli.acao("a1")
    assert e.value.definitiva and e.value.status == status
    assert CHAVE not in e.value.motivo and TOKEN not in e.value.motivo
    assert CHAVE not in str(e.value) and TOKEN not in str(e.value)
    assert len(srv.pedidos) == 1, "recusa não se repete"


async def test_5xx_nao_e_definitiva() -> None:
    cli, _ = _cliente(Servidor(httpx.Response(503, text="indisponível")))
    with pytest.raises(FalhaDoTrello) as e:
        await cli.acao("a1")
    assert e.value.status == 503 and not e.value.definitiva


async def test_404_e_definitiva_e_deixa_o_status_para_quem_chama() -> None:
    cli, _ = _cliente(Servidor(httpx.Response(404, text="The requested resource was not found.")))
    with pytest.raises(FalhaDoTrello) as e:
        await cli.acao("a1")
    assert e.value.status == 404 and e.value.definitiva


async def test_falha_de_rede_nao_e_definitiva_e_nao_traz_url_nem_segredo() -> None:
    def cai(pedido: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"sem rota para {pedido.url}")

    rel = Relogio()
    http = httpx.AsyncClient(transport=httpx.MockTransport(cai))
    cli = ClienteTrello(CHAVE, TOKEN, client=http, balde=BaldeDeRequisicoes(relogio=rel, dormir=rel.dormir))
    with pytest.raises(FalhaDoTrello) as e:
        await cli.webhooks_do_membro()
    assert not e.value.definitiva and e.value.status is None
    assert TOKEN not in e.value.motivo and "trello.com" not in e.value.motivo and "ConnectError" in e.value.motivo
    assert e.value.__cause__ is None, "`from None`: o erro original (com a URL) não viaja"


async def test_timeout_nao_e_definitivo() -> None:
    def demora(pedido: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("lento", request=pedido)

    http = httpx.AsyncClient(transport=httpx.MockTransport(demora))
    rel = Relogio()
    cli = ClienteTrello(CHAVE, TOKEN, client=http, balde=BaldeDeRequisicoes(relogio=rel, dormir=rel.dormir))
    with pytest.raises(FalhaDoTrello) as e:
        await cli.acao("a1")
    assert not e.value.definitiva


async def test_resposta_que_nao_e_json_e_falha_sem_corpo() -> None:
    cli, _ = _cliente(Servidor(httpx.Response(200, text="<html>nao sou json</html>")))
    with pytest.raises(FalhaDoTrello) as e:
        await cli.acao("a1")
    assert "html" not in e.value.motivo


async def test_corpo_vazio_de_sucesso_e_objeto_vazio() -> None:
    cli, _ = _cliente(Servidor(httpx.Response(200, content=b"")))
    assert await cli.apagar_webhook("w1") == {}


# --------------------------------------------------------------------- balde
async def test_balde_nao_deixa_passar_mais_de_60_em_10s() -> None:
    rel = Relogio()
    balde = BaldeDeRequisicoes(relogio=rel, dormir=rel.dormir)
    marcas: list[float] = []
    for _ in range(150):
        await balde.tomar()
        marcas.append(rel.t)
    assert BALDE_LIMITE == 60
    for i, t in enumerate(marcas):
        na_janela = [m for m in marcas if t - 10.0 < m <= t]
        assert len(na_janela) <= 60, f"{len(na_janela)} retiradas em 10 s ao redor da {i}"
    assert sum(1 for m in marcas if m == marcas[0]) == 60, "as 60 primeiras passam de uma vez, sem dormir"
    assert rel.sonos, "a 61ª esperou a janela andar"
    assert marcas[-1] - marcas[0] >= 20.0, "150 pedidos precisam de pelo menos duas janelas depois da primeira"


async def test_o_cliente_passa_pelo_balde_a_cada_pedido_e_a_repeticao_do_429_tambem() -> None:
    srv = Servidor(httpx.Response(429, headers={"Retry-After": "0"}), httpx.Response(200, json={}))
    cli, rel = _cliente(srv)
    for _ in range(59):
        await cli.acao("a1")
    # 59 + (429, 200) = 61 retiradas no mesmo instante falso: a última tem de ter esperado a janela
    assert len(srv.pedidos) == 60
    await cli.acao("a1")
    assert any(s >= 9.0 for s in rel.sonos), "o 61º pedido esperou a janela"


def test_a_url_base_e_a_da_api_oficial() -> None:
    assert API == "https://api.trello.com"


async def test_cartoes_da_lista_e_comentarios_leem_so_o_necessario_com_auth_no_cabecalho() -> None:
    cartoes = [{"id": "c1", "name": "N", "desc": "D", "idMembers": ["m1"], "badges": {}}, {"id": "c2", "name": "M"}]
    acoes = [{"id": "a2", "type": "commentCard", "data": {"text": "🤖 12:00Z · resolvido: x"}, "memberCreator": {"id": "m"}},
             {"id": "a1", "data": {"text": "oi"}}, {"id": "a0", "data": {}}]
    srv = Servidor(httpx.Response(200, json=cartoes), httpx.Response(200, json=acoes))
    cli, _ = _cliente(srv)
    assert await cli.cartoes_da_lista("l1") == [{"id": "c1", "name": "N", "desc": "D"}, {"id": "c2", "name": "M"}]
    assert await cli.comentarios("c1", 5) == ["🤖 12:00Z · resolvido: x", "oi"]
    lista, coment = srv.pedidos
    assert (lista.method, lista.url.path, dict(lista.url.params)) == ("GET", "/1/lists/l1/cards", {"fields": "id,name,desc"})
    assert (coment.url.path, dict(coment.url.params)) == ("/1/cards/c1/actions", {"filter": "commentCard", "limit": "5"})
    for p in srv.pedidos:
        assert _sem_segredo_na_url(p) and p.headers["authorization"].startswith("OAuth oauth_consumer_key=")
    with pytest.raises(FalhaDoTrello):
        await _cliente(Servidor(httpx.Response(200, json={})))[0].cartoes_da_lista("l1")
