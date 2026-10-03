"""Item 32.2 (3/6) — o espelho do Trello (ADR-072): reconciliador por fato, marcos e custos, tudo redigido.

Prova `simulated`: `httpx.MockTransport` (um Trello falso com estado mínimo), relógio falso e SQLite (ou o PostgreSQL de
`TEST_DATABASE_URL`); nenhuma rede.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from app.config import Config
from app.db import Database
from app.modules.avisos.adapters.trello import BaldeDeRequisicoes, ClienteTrello
from app.modules.avisos.application.espelho import LinhaDeCusto
from app.modules.avisos.infrastructure.entrada import Pendencia
from app.modules.avisos.infrastructure.espelho import EspelhoDoTrello, FontesDaCentral
from app.modules.avisos.infrastructure.espelho_sql import CartoesDoTrello
from app.taskqueue.travas import AVISOS

from .conftest import make_config

pytestmark = pytest.mark.asyncio

CHAVE, TOKEN = "chave-falsa-0123456789abcdef", "token-falso-fedcba9876543210"
CENTRAL, MARCOS, CUSTOS = "lista-central", "lista-marcos", "lista-custos"
PAINEL = "http://painel.lan:8000"
# Tudo o que NÃO pode chegar ao Trello: e-mail, @conta, nome de persona (de fixture) e IP.
SUJEIRA = ["ana.souza@exemplo.com", "@lucas_ig", "Marina Souza", "192.168.1.19"]
TEXTO_SUJO = "responder para ana.souza@exemplo.com sobre @lucas_ig da Marina Souza em 192.168.1.19"


class Relogio:
    def __init__(self) -> None:
        self.t = datetime(2026, 10, 3, 10, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.t

    async def dormir(self, s: float) -> None:
        self.t += timedelta(seconds=s)


class TrelloFalso:
    """Guarda os pedidos de escrita e responde como o Trello. `falhar` = status a devolver nos próximos pedidos."""

    def __init__(self) -> None:
        self.pedidos: list[httpx.Request] = []
        self.falhar: list[int] = []
        self.n = 0

    def __call__(self, pedido: httpx.Request) -> httpx.Response:
        self.pedidos.append(pedido)
        if self.falhar:
            return httpx.Response(self.falhar.pop(0), text="falha")
        if pedido.method == "POST" and pedido.url.path == "/1/cards":
            self.n += 1
            return httpx.Response(200, json={"id": f"card{self.n}", "idBoard": "quadro1"})
        if pedido.url.path.endswith("/actions/comments"):
            return httpx.Response(200, json={"id": "acao1"})
        return httpx.Response(200, json={})

    def escritas(self) -> list[httpx.Request]:
        return [p for p in self.pedidos if p.method != "GET"]

    def corpos(self) -> list[dict[str, object]]:
        return [json.loads(p.content) for p in self.pedidos if p.content]


class Cenario:
    def __init__(self, tmp_path: Path, *, marcos: bool = False, custos: bool = False) -> None:
        self.cfg: Config = make_config(tmp_path)
        self.cfg.ensure_dirs()
        self.cfg.file.avisos.url_painel = PAINEL
        t = self.cfg.file.trello
        t.enabled = True
        t.quadros = ["quadro1"]
        t.listas = {"central_automatico": CENTRAL, **({"marcos": MARCOS} if marcos else {}),
                    **({"custos": CUSTOS} if custos else {})}
        self.cfg.env.trello_api_key = SecretStr(CHAVE)
        self.cfg.env.trello_token = SecretStr(TOKEN)
        self.db = Database(self.cfg.db_dsn)
        self.db.migrate()
        self.relogio = Relogio()
        self.trello = TrelloFalso()
        self.pendencias: list[Pendencia] = []
        self.lider: int | None = 1
        self.versao: tuple[str | None, str | None] = ("51270b9c9d", "086_sombra_estado_hash")
        self.custos: list[LinhaDeCusto] = [LinhaDeCusto("anthropic", 0.5, 7.25, "USD", "ok")]
        balde = BaldeDeRequisicoes(relogio=lambda: self.relogio.t.timestamp(), dormir=self.relogio.dormir)
        cliente = ClienteTrello(CHAVE, TOKEN, client=httpx.AsyncClient(transport=httpx.MockTransport(self.trello)),
                                balde=balde, dormir=self.relogio.dormir)
        self.espelho = EspelhoDoTrello(
            self.cfg, CartoesDoTrello(self.db, self.relogio),
            FontesDaCentral(self.db, lambda: self.pendencias, lambda: self.cfg.file.avisos.url_painel),
            lider=lambda nome: self.lider if nome == AVISOS else None, versao=lambda: self.versao,
            custos=lambda: self.custos, relogio=self.relogio, cliente=cliente, dormir=self.relogio.dormir)

    def pedido(self, ident: str, estado: str, titulo: str = "Pedido") -> None:
        em = "2026-10-03T00:00:00.000Z"
        self.db.execute("INSERT INTO pedidos(id, titulo, objetivo, estado, criado_em, atualizado_em)"
                        " VALUES (?,?,?,?,?,?)", (ident, titulo, TEXTO_SUJO, estado, em, em))

    def validacao(self, ident: str, item_ref: str, estado: str = "pendente") -> None:
        em = "2026-10-03T00:00:00.000Z"
        self.db.execute(
            "INSERT INTO learning_validations(id, created_at, updated_at, review_id, item_ref, item_kind, grupo,"
            " comando, estado, expira_em) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (ident, em, em, "rv-1", item_ref, "fluxo", "qa", TEXTO_SUJO, estado, "2026-10-04T00:00:00.000Z"))

    def linhas(self) -> dict[str, str]:
        return {str(r["chave"]): str(r["estado"]) for r in self.db.query("SELECT chave, estado FROM trello_cartoes")}


@pytest.fixture
def cen(tmp_path: Path) -> Cenario:
    return Cenario(tmp_path)


@pytest.fixture
def cen_tudo(tmp_path: Path) -> Cenario:
    return Cenario(tmp_path, marcos=True, custos=True)


def _criacoes(c: Cenario) -> list[httpx.Request]:
    return [p for p in c.trello.pedidos if p.method == "POST" and p.url.path == "/1/cards"]


# --------------------------------------------------------------------- reconciliador
async def test_fato_novo_cria_o_cartao_na_lista_certa_e_grava_a_linha(cen: Cenario) -> None:
    cen.pendencias = [Pendencia("aprovacao", "ap-123456", TEXTO_SUJO), Pendencia("pergunta", "r-2026-abcdef", TEXTO_SUJO)]
    cen.pedido("pd-aaaaaa", "ativo")
    cen.pedido("pd-bbbbbb", "rascunho")               # rascunho não é fato
    cen.validacao("lv-cccccc", "fluxo:f1", "pendente")
    cen.validacao("lv-dddddd", "fluxo:f2", "feita")   # terminada não é fato
    cen.versao = (None, None)                         # sem commit, sem marco
    assert await cen.espelho.uma_volta() is True
    corpos = [json.loads(p.content) for p in _criacoes(cen)]
    assert len(corpos) == 4 and {c["idList"] for c in corpos} == {CENTRAL}
    assert set(cen.linhas()) == {"approval:ap-123456", "run:r-2026-abcdef:needs_input", "pedido:pd-aaaaaa",
                                 "livro:lv-cccccc"}
    assert set(cen.linhas().values()) == {"ativo"}
    desc = next(c["desc"] for c in corpos if str(c["name"]).startswith("Aprovação"))
    assert "**Para quem não é técnico:**" in desc and "**Técnico:**" in desc
    assert "tipo `approval.pending`" in desc and f"{PAINEL}/#/pendencias" in desc


async def test_segunda_volta_sem_mudanca_nao_escreve_nada(cen: Cenario) -> None:
    cen.pendencias = [Pendencia("aprovacao", "ap-123456", "x")]
    cen.pedido("pd-aaaaaa", "pausado")
    assert await cen.espelho.uma_volta()
    assert len(cen.trello.escritas()) == 2
    cen.trello.pedidos.clear()
    assert await cen.espelho.uma_volta() and await cen.espelho.uma_volta()
    assert cen.trello.pedidos == [], "sem mudança, nenhuma chamada (nem de leitura)"


async def test_atualiza_so_o_cartao_cujo_hash_mudou(cen: Cenario) -> None:
    cen.pendencias = [Pendencia("aprovacao", "ap-123456", "x")]
    cen.pedido("pd-aaaaaa", "ativo")
    cen.versao = (None, None)
    await cen.espelho.uma_volta()
    cen.trello.pedidos.clear()
    cen.db.execute("UPDATE pedidos SET estado='pausado' WHERE id='pd-aaaaaa'")
    await cen.espelho.uma_volta()
    (escrita,) = cen.trello.escritas()
    assert (escrita.method, escrita.url.path) == ("PUT", "/1/cards/card2")
    assert "Pedido pausado" in json.loads(escrita.content)["name"]
    cen.trello.pedidos.clear()
    await cen.espelho.uma_volta()
    assert cen.trello.pedidos == [], "o hash novo ficou gravado: nada mais a fazer"


async def test_fato_que_sumiu_comenta_o_desfecho_arquiva_e_nao_repete(cen: Cenario) -> None:
    cen.pendencias = [Pendencia("aprovacao", "ap-123456", TEXTO_SUJO)]
    cen.versao = (None, None)
    await cen.espelho.uma_volta()
    cen.trello.pedidos.clear()
    cen.pendencias = []
    cen.relogio.t = datetime(2026, 10, 3, 21, 47, tzinfo=timezone.utc)
    await cen.espelho.uma_volta()
    comentario, arquivo = cen.trello.escritas()
    assert comentario.url.path == "/1/cards/card1/actions/comments"
    assert json.loads(comentario.content)["text"] == "🤖 21:47Z · resolvido: a aprovação foi decidida ou perdeu a validade"
    assert (arquivo.method, arquivo.url.path, json.loads(arquivo.content)) == ("PUT", "/1/cards/card1", {"closed": True})
    assert cen.linhas() == {"approval:ap-123456": "arquivado"}
    cen.trello.pedidos.clear()
    await cen.espelho.uma_volta()
    assert cen.trello.pedidos == []


async def test_o_fato_que_volta_depois_de_arquivado_ganha_cartao_novo(cen: Cenario) -> None:
    cen.pendencias = [Pendencia("aprovacao", "ap-123456", "x")]
    cen.versao = (None, None)
    await cen.espelho.uma_volta()
    cen.pendencias = []
    await cen.espelho.uma_volta()
    cen.pendencias = [Pendencia("aprovacao", "ap-123456", "x")]
    await cen.espelho.uma_volta()
    assert len(_criacoes(cen)) == 2 and cen.linhas() == {"approval:ap-123456": "ativo"}


async def test_falha_nao_definitiva_nao_grava_estado_errado_e_a_proxima_volta_refaz(cen: Cenario) -> None:
    cen.pendencias = [Pendencia("aprovacao", "ap-123456", "x")]
    cen.versao = (None, None)
    cen.trello.falhar = [503]
    assert await cen.espelho.uma_volta() is False
    assert cen.linhas() == {} and cen.espelho.problemas() == []
    assert await cen.espelho.uma_volta() is True
    assert cen.linhas() == {"approval:ap-123456": "ativo"}


async def test_falha_ao_arquivar_nao_marca_arquivado_e_nao_comenta_duas_vezes(cen: Cenario) -> None:
    cen.pendencias = [Pendencia("aprovacao", "ap-123456", "x")]
    cen.versao = (None, None)
    await cen.espelho.uma_volta()
    cen.pendencias = []
    cen.trello.pedidos.clear()
    # o comentário passa; o arquivamento falha com 503
    original = cen.trello.__call__

    def alvo(pedido: httpx.Request) -> httpx.Response:
        if pedido.method == "PUT":
            cen.trello.pedidos.append(pedido)
            return httpx.Response(503, text="falha")
        return original(pedido)

    cen.espelho._cliente._client = httpx.AsyncClient(transport=httpx.MockTransport(alvo))   # type: ignore[union-attr]
    assert await cen.espelho.uma_volta() is False
    assert cen.linhas() == {"approval:ap-123456": "ativo"}
    cen.espelho._cliente._client = httpx.AsyncClient(transport=httpx.MockTransport(cen.trello))  # type: ignore[union-attr]
    cen.trello.pedidos.clear()
    assert await cen.espelho.uma_volta() is True
    assert [p.url.path.endswith("/actions/comments") for p in cen.trello.escritas()] == [False], "só arquivou"
    assert cen.linhas() == {"approval:ap-123456": "arquivado"}


async def test_leitura_que_falha_nunca_vira_fato_sumido(cen: Cenario) -> None:
    cen.pendencias = [Pendencia("aprovacao", "ap-123456", "x")]
    cen.versao = (None, None)
    await cen.espelho.uma_volta()
    cen.trello.pedidos.clear()

    def quebra() -> list[Pendencia]:
        raise RuntimeError("banco fora do ar")

    cen.espelho.fontes._pendencias = quebra
    assert await cen.espelho.uma_volta() is False
    assert cen.trello.pedidos == [] and cen.linhas() == {"approval:ap-123456": "ativo"}


async def test_credencial_recusada_vira_trello_recusado_e_some_quando_volta(cen: Cenario) -> None:
    cen.pendencias = [Pendencia("aprovacao", "ap-123456", "x")]
    cen.trello.falhar = [401]
    assert await cen.espelho.uma_volta() is False
    (p,) = cen.espelho.problemas()
    assert p.code == "trello_recusado" and "401" in p.message
    assert CHAVE not in p.message + p.hint and TOKEN not in p.message + p.hint
    assert cen.linhas() == {}
    assert await cen.espelho.uma_volta() is True
    assert cen.espelho.problemas() == []


async def test_outro_4xx_e_pedido_invalido_e_nao_acusa_credencial(cen: Cenario) -> None:
    cen.pendencias = [Pendencia("aprovacao", "ap-123456", "x")]
    cen.trello.falhar = [400]
    await cen.espelho.uma_volta()
    assert [p.code for p in cen.espelho.problemas()] == ["trello_pedido_invalido"]


async def test_desligado_ou_sem_lider_ou_sem_lista_nao_chama_o_trello(cen: Cenario) -> None:
    cen.pendencias = [Pendencia("aprovacao", "ap-123456", "x")]
    cen.lider = None
    assert await cen.espelho.uma_volta() is False
    cen.lider = 1
    cen.cfg.file.trello.listas = {}
    await cen.espelho.uma_volta()
    cen.cfg.file.trello.enabled = False
    assert await cen.espelho.uma_volta() is False and cen.espelho.problemas() == []
    assert cen.trello.pedidos == []


async def test_sem_segredo_nao_chama_o_trello(tmp_path: Path) -> None:
    c = Cenario(tmp_path)
    c.espelho._cliente = None
    c.cfg.env.trello_token = None
    assert await c.espelho.uma_volta() is False and c.trello.pedidos == []


# --------------------------------------------------------------------- marcos
async def test_o_marco_so_nasce_quando_o_commit_muda(tmp_path: Path) -> None:
    cen = Cenario(tmp_path, marcos=True)
    await cen.espelho.uma_volta()
    marcos = [json.loads(p.content) for p in _criacoes(cen)]
    assert len(marcos) == 1 and marcos[0]["idList"] == MARCOS and "51270b9c" in marcos[0]["name"]
    await cen.espelho.uma_volta()
    assert len(_criacoes(cen)) == 1, "mesma versão: nenhum cartão novo"
    cen.versao = ("aaaa1111bbbb", "087_trello")
    await cen.espelho.uma_volta()
    assert len(_criacoes(cen)) == 2
    cen.versao = ("aaaa1111bbbb", "087_trello")
    await cen.espelho.uma_volta()
    assert len(_criacoes(cen)) == 2 and sum(k.startswith("deploy:") for k in cen.linhas()) == 2


async def test_sem_commit_ou_sem_lista_de_marcos_nao_ha_marco(tmp_path: Path) -> None:
    c = Cenario(tmp_path, marcos=False, custos=False)
    await c.espelho.uma_volta()
    c.versao = (None, "087")
    c.cfg.file.trello.listas["marcos"] = MARCOS
    await c.espelho.uma_volta()
    assert c.trello.pedidos == []


async def test_o_marco_arquivado_nunca_e_arquivado_pelo_reconciliador(tmp_path: Path) -> None:
    cen = Cenario(tmp_path, marcos=True)
    await cen.espelho.uma_volta()
    cen.trello.pedidos.clear()
    cen.versao = ("zzzz9999yyyy", "088")
    await cen.espelho.uma_volta()
    assert not [p for p in cen.trello.escritas() if p.method == "PUT"], "deploy antigo fica no quadro"


# --------------------------------------------------------------------- custos
async def test_custo_um_cartao_por_dia_e_no_maximo_um_por_hora(tmp_path: Path) -> None:
    cen = Cenario(tmp_path, custos=True)
    await cen.espelho.uma_volta()
    (criado,) = _criacoes(cen)
    corpo = json.loads(criado.content)
    assert corpo["idList"] == CUSTOS and corpo["name"] == "Custo de IA · 2026-10-03"
    assert "anthropic: gasto 24 h US$ 0.5000, saldo estimado 7.25 USD (ok)" in corpo["desc"]
    cen.custos = [LinhaDeCusto("anthropic", 0.9, 6.85, "USD", "ok")]
    cen.relogio.t += timedelta(minutes=30)
    cen.trello.pedidos.clear()
    await cen.espelho.uma_volta()
    assert cen.trello.pedidos == [], "menos de 1 h: não relê nem escreve"
    cen.relogio.t += timedelta(minutes=31)
    await cen.espelho.uma_volta()
    (escrita,) = cen.trello.escritas()
    assert escrita.method == "PUT" and "0.9000" in json.loads(escrita.content)["desc"]
    cen.relogio.t += timedelta(minutes=90)
    cen.trello.pedidos.clear()
    await cen.espelho.uma_volta()
    assert cen.trello.pedidos == [], "passou da hora mas o conteúdo é o mesmo: hash igual, nada a escrever"


async def test_custo_do_dia_seguinte_e_outro_cartao(tmp_path: Path) -> None:
    cen = Cenario(tmp_path, custos=True)
    cen.versao = (None, None)
    await cen.espelho.uma_volta()
    cen.relogio.t += timedelta(hours=24)
    await cen.espelho.uma_volta()
    assert len(_criacoes(cen)) == 2 and {"custo:2026-10-03", "custo:2026-10-04"} <= set(cen.linhas())


async def test_custo_com_falha_tenta_de_novo_na_volta_seguinte(tmp_path: Path) -> None:
    cen = Cenario(tmp_path, custos=True)
    cen.versao = (None, None)
    cen.trello.falhar = [503]
    assert await cen.espelho.uma_volta() is False
    assert await cen.espelho.uma_volta() is True
    assert len(cen.linhas()) == 1


# --------------------------------------------------------------------- redação
async def test_nada_do_texto_de_origem_chega_ao_trello(cen_tudo: Cenario) -> None:
    """E-mail, @conta, nome de persona e IP estão no resumo da pendência, no título e no objetivo do pedido e no comando
    da validação. Nenhum deles pode estar em NENHUM pedido ao Trello (corpo, URL ou cabeçalho), nem no desfecho."""
    cen_tudo.pendencias = [Pendencia("aprovacao", "ap-123456", TEXTO_SUJO), Pendencia("pergunta", "r-1-abcdef", TEXTO_SUJO)]
    cen_tudo.pedido("pd-aaaaaa", "ativo", titulo=TEXTO_SUJO)
    cen_tudo.validacao("lv-cccccc", "fluxo:f1")
    await cen_tudo.espelho.uma_volta()
    cen_tudo.pendencias = []
    cen_tudo.db.execute("UPDATE pedidos SET estado='pausado'")
    await cen_tudo.espelho.uma_volta()
    cen_tudo.db.execute("UPDATE pedidos SET estado='concluido'")
    cen_tudo.db.execute("UPDATE learning_validations SET estado='feita'")
    await cen_tudo.espelho.uma_volta()
    assert len(cen_tudo.trello.escritas()) >= 10
    tudo = "\n".join(f"{p.method} {p.url} {dict(p.headers).get('x-extra', '')} {p.content.decode()}"
                     for p in cen_tudo.trello.pedidos)
    for item in SUJEIRA:
        assert item not in tudo, f"{item!r} chegou ao Trello"
    assert "painel.lan" in tudo, "o link do painel (LAN) é permitido"
    # a chave e o token só no cabeçalho de autenticação, nunca na URL nem no corpo
    for p in cen_tudo.trello.pedidos:
        assert CHAVE not in str(p.url) and TOKEN not in str(p.url)
        assert CHAVE.encode() not in p.content and TOKEN.encode() not in p.content
