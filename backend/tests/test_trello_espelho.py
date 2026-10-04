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
from app.contracts.identidade import NOME_DA_IA
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
    """O Trello falso, com estado mínimo: cartões por lista (abertos ou fechados) e os comentários de cada um.
    `falhar` = status a devolver nos próximos pedidos."""

    def __init__(self) -> None:
        self.pedidos: list[httpx.Request] = []
        self.falhar: list[int] = []
        self.n = 0
        self.cartoes: dict[str, dict[str, object]] = {}
        self.comentarios: dict[str, list[str]] = {}

    def humano(self, lista: str, nome: str = "Ideia do dono", desc: str = "anotação minha") -> str:
        """Um cartão feito por pessoa na lista (sem a marca do espelho)."""
        self.n += 1
        ident = f"card{self.n}"
        self.cartoes[ident] = {"id": ident, "name": nome, "desc": desc, "idList": lista, "closed": False}
        return ident

    def __call__(self, pedido: httpx.Request) -> httpx.Response:
        self.pedidos.append(pedido)
        if self.falhar:
            return httpx.Response(self.falhar.pop(0), text="falha")
        caminho = pedido.url.path
        corpo = json.loads(pedido.content) if pedido.content else {}
        if pedido.method == "POST" and caminho == "/1/cards":
            ident = self.humano(corpo["idList"], corpo["name"], corpo["desc"])
            return httpx.Response(200, json={"id": ident, "idBoard": "quadro1"})
        if caminho.endswith("/actions/comments"):
            self.comentarios.setdefault(caminho.split("/")[3], []).insert(0, corpo["text"])
            return httpx.Response(200, json={"id": "acao1"})
        if pedido.method == "GET" and caminho.startswith("/1/lists/") and caminho.endswith("/cards"):
            lista = caminho.split("/")[3]
            return httpx.Response(200, json=[{k: c[k] for k in ("id", "name", "desc")} for c in self.cartoes.values()
                                             if c["idList"] == lista and not c["closed"]])
        if pedido.method == "GET" and caminho.endswith("/actions"):
            textos = self.comentarios.get(caminho.split("/")[3], [])
            return httpx.Response(200, json=[{"id": f"a{i}", "data": {"text": t}} for i, t in enumerate(textos)])
        if pedido.method == "PUT" and caminho.startswith("/1/cards/"):
            card = self.cartoes.get(caminho.split("/")[3])
            if card is None:
                return httpx.Response(404, text="not found")
            card.update({"name": corpo.get("name", card["name"]), "desc": corpo.get("desc", card["desc"]),
                         "closed": corpo.get("closed", card["closed"])})
        return httpx.Response(200, json={})

    def escritas(self) -> list[httpx.Request]:
        return [p for p in self.pedidos if p.method != "GET"]

    def corpos(self) -> list[dict[str, object]]:
        return [json.loads(p.content) for p in self.pedidos if p.content]


class FontesDeTeste:
    """O `FontesDoEspelho` do teste: lê as MESMAS tabelas que as portas dos módulos donos (o adaptador real de
    `state.py` tem o teste dele mais abaixo)."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def pedidos_abertos(self) -> list[tuple[str, str]]:
        return [(str(r["id"]), str(r["estado"])) for r in self.db.query(
            "SELECT id, estado FROM pedidos WHERE estado IN ('ativo','pausado','aguardando_pessoa') ORDER BY id")]

    def livro_em_validacao(self) -> list[tuple[str, str, str]]:
        return [(str(r["id"]), str(r["item_ref"]), str(r["estado"])) for r in self.db.query(
            "SELECT id, item_ref, estado FROM learning_validations WHERE estado IN ('pendente','rodando')"
            " ORDER BY created_at, id")]


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
            FontesDaCentral(lambda: self.pendencias, FontesDeTeste(self.db), lambda: self.cfg.file.avisos.url_painel),
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
    livro = next(c["desc"] for c in corpos if str(c["name"]).startswith("Conhecimento"))
    assert f"{PAINEL}/#/aprendizado?aba=aprendido&item=fluxo:f1" in livro


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
    assert json.loads(comentario.content)["text"] == f"🤖 {NOME_DA_IA} · 21:47Z · resolvido: a aprovação foi decidida ou perdeu a validade"
    assert json.loads(comentario.content)["text"].startswith("🤖 ANA ·")
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
    assert cen.linhas() == {"approval:ap-123456": "criando"} and cen.espelho.problemas() == []
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
    assert cen.linhas() == {"approval:ap-123456": "criando"}
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


# --------------------------------------------------------------------- banco primeiro, adoção, desfecho único
def _falha_ao_gravar(cen: Cenario) -> None:
    """O Trello cria o cartão e o banco falha ao gravar o `card_id` (a queda entre as duas escritas)."""
    original = cen.espelho.repo.gravar
    estado = {"vez": 0}

    def gravar(*args: str) -> None:
        estado["vez"] += 1
        if estado["vez"] == 1:
            raise RuntimeError("banco caiu")
        original(*args)

    cen.espelho.repo.gravar = gravar      # type: ignore[method-assign]


async def test_a_linha_de_intencao_nasce_antes_de_o_trello_ser_chamado(cen: Cenario) -> None:
    cen.pendencias = [Pendencia("aprovacao", "ap-123456", "x")]
    vistas: list[dict[str, str]] = []
    original = cen.trello.__call__

    def espiar(pedido: httpx.Request) -> httpx.Response:
        if pedido.method == "POST" and pedido.url.path == "/1/cards":
            vistas.append({str(r["chave"]): f'{r["estado"]}|{r["card_id"]}'
                           for r in cen.db.query("SELECT chave, estado, card_id FROM trello_cartoes")})
        return original(pedido)

    cen.espelho._cliente._client = httpx.AsyncClient(transport=httpx.MockTransport(espiar))   # type: ignore[union-attr]
    await cen.espelho.uma_volta()
    assert vistas == [{"approval:ap-123456": "criando|criando:approval:ap-123456"}], "a intenção já estava gravada"
    assert cen.db.one("SELECT card_id FROM trello_cartoes")["card_id"] == "card1"
    assert cen.linhas() == {"approval:ap-123456": "ativo"}


async def test_criou_no_trello_e_o_banco_falhou_a_volta_seguinte_adota_e_nao_cria_outro(cen: Cenario) -> None:
    cen.pendencias = [Pendencia("aprovacao", "ap-123456", "x")]
    _falha_ao_gravar(cen)
    assert await cen.espelho.uma_volta() is False
    assert cen.linhas() == {"approval:ap-123456": "criando"} and len(_criacoes(cen)) == 1
    assert await cen.espelho.uma_volta() is True
    assert len(_criacoes(cen)) == 1, "adotou o cartão marcado em vez de criar outro"
    linha = cen.db.one("SELECT * FROM trello_cartoes")
    assert (linha["estado"], linha["card_id"], linha["lista"]) == ("ativo", "card1", CENTRAL)
    cen.trello.pedidos.clear()
    assert await cen.espelho.uma_volta() is True and cen.trello.pedidos == [], "hash igual: nada a atualizar"


async def test_cartao_adotado_com_conteudo_velho_e_atualizado_na_volta_seguinte(cen: Cenario) -> None:
    cen.pendencias = [Pendencia("aprovacao", "ap-123456", "x")]
    cen.trello.humano(CENTRAL, "Nome antigo", "texto antigo\n\n🤖 chave: approval:ap-123456")
    await cen.espelho.uma_volta()
    assert len(_criacoes(cen)) == 0 and cen.linhas() == {"approval:ap-123456": "ativo"}
    await cen.espelho.uma_volta()
    (escrita,) = cen.trello.escritas()
    assert escrita.method == "PUT" and json.loads(escrita.content)["name"].startswith("Aprovação")


async def test_cartao_humano_na_lista_nunca_e_tocado(cen: Cenario) -> None:
    humano = cen.trello.humano(CENTRAL)
    parecido = cen.trello.humano(CENTRAL, "Aprovação aguardando", "minha nota\n🤖 chave: nao-e-chave")
    cen.pendencias = [Pendencia("aprovacao", "ap-123456", "x")]
    await cen.espelho.uma_volta()
    cen.pendencias = []
    await cen.espelho.uma_volta()
    for ident in (humano, parecido):
        assert cen.trello.cartoes[ident]["closed"] is False
        assert cen.trello.cartoes[ident]["name"] in ("Ideia do dono", "Aprovação aguardando")
    mexeu = [p.url.path for p in cen.trello.escritas() if p.url.path.endswith((f"/{humano}", f"/{parecido}"))]
    assert mexeu == [] and len(_criacoes(cen)) == 1


async def test_duas_marcas_iguais_adota_a_mais_antiga_e_nao_arquiva_a_outra(cen: Cenario) -> None:
    """Raro (dois processos criaram o mesmo fato): fica o mais antigo (o id do Trello começa pelo instante de criação) e
    o outro não é tocado, porque a Central não tem como saber qual deles o dono já mexeu."""
    marca = "texto\n\n🤖 chave: approval:ap-123456"
    antigo = cen.trello.humano(CENTRAL, "Aprovação", marca)
    novo = cen.trello.humano(CENTRAL, "Aprovação", marca)
    assert antigo < novo
    cen.pendencias = [Pendencia("aprovacao", "ap-123456", "x")]
    await cen.espelho.uma_volta()
    assert cen.db.one("SELECT card_id FROM trello_cartoes")["card_id"] == antigo and len(_criacoes(cen)) == 0
    assert cen.trello.cartoes[novo]["closed"] is False
    cen.pendencias = []
    await cen.espelho.uma_volta()
    assert cen.trello.cartoes[antigo]["closed"] is True and cen.trello.cartoes[novo]["closed"] is False


async def test_o_fato_some_com_a_criacao_em_aberto_arquiva_o_cartao_que_nasceu(cen: Cenario) -> None:
    cen.pendencias = [Pendencia("aprovacao", "ap-123456", "x")]
    _falha_ao_gravar(cen)
    await cen.espelho.uma_volta()
    cen.pendencias = []
    assert await cen.espelho.uma_volta() is True
    assert cen.trello.cartoes["card1"]["closed"] is True and cen.linhas() == {"approval:ap-123456": "arquivado"}
    assert cen.trello.comentarios["card1"][0].startswith("🤖 ANA ·")


async def test_o_fato_some_com_a_criacao_em_aberto_e_sem_cartao_so_fecha_a_linha(cen: Cenario) -> None:
    cen.pendencias = [Pendencia("aprovacao", "ap-123456", "x")]
    cen.trello.falhar = [503]
    await cen.espelho.uma_volta()
    cen.pendencias = []
    assert await cen.espelho.uma_volta() is True
    assert cen.linhas() == {"approval:ap-123456": "arquivado"} and cen.trello.escritas() == []


async def test_depois_de_reiniciar_o_desfecho_ja_comentado_nao_e_comentado_de_novo(cen: Cenario) -> None:
    cen.pendencias = [Pendencia("aprovacao", "ap-123456", "x")]
    await cen.espelho.uma_volta()
    cen.pendencias = []
    original = cen.espelho.repo.arquivar
    cen.espelho.repo.arquivar = lambda chave: (_ for _ in ()).throw(RuntimeError("banco caiu"))   # type: ignore[method-assign]
    assert await cen.espelho.uma_volta() is False        # comentou e arquivou no Trello; o banco falhou
    assert len(cen.trello.comentarios["card1"]) == 1
    cen.espelho.repo.arquivar = original                  # type: ignore[method-assign]
    cen.espelho._comentados.clear()                       # o processo reiniciou: a memória se perdeu
    cen.trello.cartoes["card1"]["closed"] = False         # (o cartão segue aberto no fake para a volta repetir)
    assert await cen.espelho.uma_volta() is True
    assert len(cen.trello.comentarios["card1"]) == 1, "o último comentário já era o desfecho: só arquivou"
    assert cen.linhas() == {"approval:ap-123456": "arquivado"} and cen.trello.cartoes["card1"]["closed"] is True


async def test_comentario_humano_recente_nao_conta_como_desfecho(cen: Cenario) -> None:
    cen.pendencias = [Pendencia("aprovacao", "ap-123456", "x")]
    await cen.espelho.uma_volta()
    cen.trello.comentarios["card1"] = ["resolvido, valeu", "🤖 ANA · 10:00Z · outra coisa"]
    cen.pendencias = []
    await cen.espelho.uma_volta()
    assert cen.trello.comentarios["card1"][0].startswith("🤖 ANA · 10:00Z · resolvido")


# --------------------------------------------------------------------- as portas dos módulos donos (state.py)
async def test_o_adaptador_do_state_le_pelas_portas_publicas_e_pagina(tmp_path: Path) -> None:
    from app.modules.learning.infrastructure.validacoes_sql import RegistroDeValidacoesSql
    from app.state import _FontesDoEspelhoDoTrello

    c = Cenario(tmp_path)
    c.validacao("lv-a", "fluxo:f1", "pendente")
    c.validacao("lv-b", "receita:r1", "rodando")
    c.validacao("lv-c", "fluxo:f2", "feita")
    paginas = [{"items": [{"id": "pd-1", "estado": "ativo", "titulo": "segredo"}, {"id": "pd-2", "estado": "pausado"}],
                "proximo_cursor": "2"}, {"items": [{"id": "pd-3", "estado": "aguardando_pessoa"}], "proximo_cursor": None}]
    chamadas: list[dict[str, object]] = []

    class PedidosFalso:
        def listar(self, **kw: object) -> dict[str, object]:
            chamadas.append(kw)
            return paginas[len(chamadas) - 1]

    fontes = _FontesDoEspelhoDoTrello(lambda: PedidosFalso(), RegistroDeValidacoesSql(c.db))      # type: ignore[arg-type]
    assert fontes.pedidos_abertos() == [("pd-1", "ativo"), ("pd-2", "pausado"), ("pd-3", "aguardando_pessoa")]
    assert chamadas[0]["estado"] == ["ativo", "pausado", "aguardando_pessoa"] and chamadas[1]["cursor"] == "2"
    assert fontes.livro_em_validacao() == [("lv-a", "fluxo:f1", "pendente"), ("lv-b", "receita:r1", "rodando")]


async def test_o_nome_da_ia_vem_do_contrato() -> None:
    from app.contracts.identidade import APRESENTACAO_DA_IA

    assert NOME_DA_IA == "ANA" and APRESENTACAO_DA_IA == "ANA, a IA Gerente de Operações da Central"


async def test_link_do_cartao_leva_so_id_e_nunca_texto_derivado_do_pedido() -> None:
    """32.4: só o formato real de id vai no link (lista de permissão). O id de fluxo pode ser o slug do objetivo, e o
    quadro tem convidados: o resto abre só a tela."""
    from app.modules.avisos.application.espelho import fato_de_pedido, fato_de_pendencia, fato_de_validacao

    base = f"{PAINEL}/#/aprendizado?aba=aprendido"
    # texto derivado do pedido: o do cartão de 04/10 e os três da revisão (uma palavra, curto, com número)
    for ref in ("fluxo:ler-sem-abrir-conversas-nem-enviar-nada-", "fluxo:comentar", "fluxo:ler-x", "fluxo:post1-marca",
                "receita:abc", "fluxo:F1", "fluxo:9a1c2e7"):
        fato = fato_de_validacao("lv-32fded52ec729464", ref, "pendente", PAINEL)
        assert fato is not None and base in fato.descricao, ref
        assert "item=" not in fato.descricao and ref.split(":", 1)[1] not in fato.descricao, ref
    # ids reais seguem no link
    for ref in ("receita:87", "fluxo:f1", "fluxo:9a1c2e7b"):
        fato = fato_de_validacao("lv-1", ref, "pendente", PAINEL)
        assert fato is not None and f"{base}&item={ref}" in fato.descricao, ref
    # execução: só `r-<14 dígitos>-<6 hex>`
    run = fato_de_pendencia("pergunta", "r-20261004003742-e8e49e", PAINEL)
    assert run is not None and f"{PAINEL}/#/execucoes/r-20261004003742-e8e49e" in run.descricao
    for ident in ("r-comentar-no-post", "r-2026-abcdef"):
        fato = fato_de_pendencia("pergunta", ident, PAINEL)
        assert fato is not None and "#/execucoes/" not in fato.descricao and f"{PAINEL}/#/pendencias" in fato.descricao
    # pedido: só `ped_` + 22 de base64 url-safe
    real = "ped_" + "Ab3_dE-9" * 2 + "xYz012"
    assert len(real) == 26
    ped = fato_de_pedido(real, "ativo", PAINEL)
    assert ped is not None and f"{PAINEL}/#/pedidos/{real}" in ped.descricao
    for ident in ("comentar-no-post-da-marca", "ped_curto", "pd-aaaaaa"):
        fato = fato_de_pedido(ident, "ativo", PAINEL)
        assert fato is not None and "#/pedidos/" not in fato.descricao and f"{PAINEL}/#/pedidos" in fato.descricao
