"""Item 32.2 (4/6) — o leitor e a saída do Trello (ADR-072): só o dono comanda, aprovar pede confirmação fora do Trello.

Prova `simulated`: `httpx.MockTransport` (um Trello falso com as actions do quadro), relógio falso, portas falsas do
Telegram (`test_telegram_entrada.PortasFalsas`) e banco de teste; nenhuma rede, nenhum Trello de verdade.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from app.config import Config
from app.contracts.identidade import NOME_DA_IA
from app.db import Database
from app.modules.avisos.adapters.trello import BaldeDeRequisicoes, ClienteTrello
from app.modules.avisos.application.entrada import RESPOSTA_IDENTIDADE
from app.modules.avisos.domain.mensagem import Aviso
from app.modules.avisos.infrastructure.entrada import RESPOSTA_DO_REPASSE, ConversaDoCanal, Recebida
from app.modules.avisos.infrastructure.entrada_sql import EntradasDoCanal
from app.modules.avisos.infrastructure.espelho_sql import CartoesDoTrello, CursorDoTrello
from app.modules.avisos.infrastructure.trello_leitor import (
    AJUDA_DO_TRELLO,
    RESPOSTA_APROVAR_FORA,
    RESPOSTA_COMANDO_LIVRE,
    RESPOSTA_CONVIDADO,
    RESPOSTA_SO_O_DONO,
    LeitorDoTrello,
    RefDoTrello,
    SaidaDoTrello,
    recebida_da_action,
)
from app.modules.learning.infrastructure.segredo import TriagemDeCredencial
from app.taskqueue.travas import AVISOS

from .conftest import make_config
from .test_canais_contrato import SaidaFalsa
from .test_telegram_entrada import PortasFalsas

pytestmark = pytest.mark.asyncio

CHAVE, TOKEN = "chave-falsa-0123456789abcdef", "token-falso-fedcba9876543210"
QUADRO, L_OK, L_NAO, L_OUTRA = "quadro1", "lista-aprovado", "lista-vetado", "lista-outra"
DONO, CONVIDADO, AMIGO = "membro-dono", "membro-convidado", "membro-amigo"
APROVACAO, PERGUNTA = "approval:apr-0000aa11", "run:r-20261002181523-4985a1:needs_input"
C_APR, C_RUN, C_DEPLOY, C_MANUAL = "card-apr", "card-run", "card-deploy", "card-manual"
PREFIXO = f"🤖 {NOME_DA_IA} · 10:00Z · "
SEGREDINHO = "SEGREDINHO-DO-CONVIDADO"        # o texto do convidado: não pode aparecer em lugar nenhum


class Relogio:
    def __init__(self) -> None:
        self.t = datetime(2026, 10, 3, 10, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.t

    async def dormir(self, s: float) -> None:
        self.t += timedelta(seconds=s)


def _iso(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%S.000Z")


class TrelloFalso:
    """O Trello falso: as actions do quadro (da mais velha para a mais nova; a API devolve ao contrário) e os comentários
    que a Central escreve. `eco` = o comentário da Central volta como action do dono (o token é o dele)."""

    def __init__(self, relogio: Relogio) -> None:
        self.relogio = relogio
        self.acoes: list[dict[str, object]] = []
        self.pedidos: list[httpx.Request] = []
        self.comentarios: list[tuple[str, str]] = []
        self.falhar: list[int] = []
        self.falhar_comentario: list[int] = []          # 28.38: só o POST do comentário falha (a leitura segue)
        self.ignorar_since = False
        self.eco = True
        self.n = 0
        self.nomes: dict[str, str] = {}                 # 28.30: o nome de cada cartão (GET /1/cards/{id}?fields=name)

    def acao(self, tipo: str, autor: str, card: str, *, texto: str | None = None, para: str | None = None,
             ha_s: float = 0, quadro: str = QUADRO, ident: str | None = None) -> dict[str, object]:
        self.n += 1
        dados: dict[str, object] = {"board": {"id": quadro}, "card": {"id": card}}
        if texto is not None:
            dados["text"] = texto
        if para is not None:
            dados["listAfter"] = {"id": para}
        a: dict[str, object] = {"id": ident or f"a{self.n:04d}", "idMemberCreator": autor, "type": tipo,
                                "date": _iso(self.relogio.t - timedelta(seconds=ha_s)), "data": dados}
        self.acoes.append(a)
        return a

    def comenta(self, autor: str, card: str, texto: str, **kw: object) -> dict[str, object]:
        return self.acao("commentCard", autor, card, texto=texto, **kw)  # type: ignore[arg-type]

    def move(self, autor: str, card: str, para: str, **kw: object) -> dict[str, object]:
        return self.acao("updateCard", autor, card, para=para, **kw)  # type: ignore[arg-type]

    def __call__(self, pedido: httpx.Request) -> httpx.Response:
        self.pedidos.append(pedido)
        if self.falhar:
            return httpx.Response(self.falhar.pop(0), text="falha")
        caminho = pedido.url.path
        if pedido.method == "GET" and caminho == f"/1/boards/{QUADRO}/actions":
            q = dict(pedido.url.params)
            lista = list(self.acoes)
            desde = q.get("since")
            if desde and not self.ignorar_since:
                ids = [str(a["id"]) for a in lista]
                lista = lista[ids.index(desde) + 1:] if desde in ids else [a for a in lista if str(a["date"]) > desde]
            return httpx.Response(200, json=lista[::-1][: int(q.get("limit", 1000))])
        if pedido.method == "GET" and caminho.startswith("/1/actions/"):
            achada = [a for a in self.acoes if a["id"] == caminho.split("/")[3]]
            return httpx.Response(200, json=achada[0]) if achada else httpx.Response(404, text="not found")
        if pedido.method == "GET" and caminho.startswith("/1/cards/") and caminho.count("/") == 3:
            card = caminho.split("/")[3]
            return httpx.Response(200, json={"id": card, "name": self.nomes.get(card, "Cartão de teste")})
        if pedido.method == "POST" and caminho.endswith("/actions/comments"):
            if self.falhar_comentario:
                return httpx.Response(self.falhar_comentario.pop(0), text="falha")
            corpo = json.loads(pedido.content)
            card = caminho.split("/")[3]
            self.comentarios.append((card, corpo["text"]))
            ident = f"cm{len(self.comentarios):04d}"
            if self.eco:
                self.comenta(DONO, card, corpo["text"], ident=ident)
            return httpx.Response(200, json={"id": ident})
        return httpx.Response(200, json={})

    def escritas(self) -> list[httpx.Request]:
        return [p for p in self.pedidos if p.method != "GET"]

    def textos(self) -> list[str]:
        return [t for _, t in self.comentarios]


class Cenario:
    def __init__(self, tmp_path: Path, *, responder_convidados: bool = False, comando_livre: bool = False,
                 autorizados: list[str] | None = None, dono: str = DONO) -> None:
        self.cfg: Config = make_config(tmp_path)
        self.cfg.ensure_dirs()
        t = self.cfg.file.trello
        t.enabled = True
        t.quadros = [QUADRO]
        t.listas = {"central_automatico": "lista-central", "aprovado": L_OK, "vetado": L_NAO}
        t.membro_dono = dono
        t.responder_convidados = responder_convidados
        t.comando_livre = comando_livre
        t.membros_autorizados = autorizados or []
        self.cfg.env.trello_api_key = SecretStr(CHAVE)
        self.cfg.env.trello_token = SecretStr(TOKEN)
        self.db = Database(self.cfg.db_dsn)
        self.db.migrate()
        self.relogio = Relogio()
        self.trello = TrelloFalso(self.relogio)
        self.portas = PortasFalsas()
        self.avisos: list[Aviso] = []
        self.lider: int | None = 1
        self.repo = EntradasDoCanal(self.db, canal="trello", relogio=self.relogio)
        self.cartoes = CartoesDoTrello(self.db, self.relogio)
        self.cursor = CursorDoTrello(self.db, self.relogio)
        for chave, card in ((APROVACAO, C_APR), (PERGUNTA, C_RUN), ("deploy:51270b9c", C_DEPLOY)):
            self.cartoes.gravar(chave, card, QUADRO, "lista-central", "h")
        balde = BaldeDeRequisicoes(relogio=lambda: self.relogio.t.timestamp(), dormir=self.relogio.dormir)
        self.cliente = ClienteTrello(CHAVE, TOKEN, client=httpx.AsyncClient(transport=httpx.MockTransport(self.trello)),
                                     balde=balde, dormir=self.relogio.dormir)
        triagem = TriagemDeCredencial()
        self.leitor = LeitorDoTrello(
            self.cfg, self.repo, self.cartoes, self.cursor, self.portas, lider=lambda n: self.lider if n == AVISOS else None,
            recusa=triagem.recusa, redigir=triagem.redigir, avisar_dono=self._avisar, relogio=self.relogio,
            cliente=self.cliente, dormir=self.relogio.dormir)

    def _avisar(self, aviso: Aviso) -> bool:
        self.avisos.append(aviso)
        return True

    async def sobe(self) -> None:
        """A 1ª subida: o histórico que já estava no quadro é descartado e o cursor fica na action mais nova."""
        await self.leitor.uma_volta()

    async def volta(self) -> int:
        return await self.leitor.uma_volta()

    def linha(self, id_externo: str) -> dict[str, object]:
        ident = self.repo.id_de(id_externo)
        assert ident is not None, id_externo
        linha = self.repo.linha(ident)
        assert linha is not None
        return linha

    def acoes_da_central(self) -> list[tuple[str, tuple[object, ...], str | None]]:
        return [c for c in self.portas.chamadas if c[0] != "pergunta_sensivel"]

    def corpos(self) -> str:
        """Tudo o que a Central mandou ao Trello: URL e corpo de cada escrita."""
        return " | ".join(f"{p.url} {p.content.decode()}" for p in self.trello.escritas())


@pytest.fixture
async def c(tmp_path: Path) -> Cenario:
    cen = Cenario(tmp_path)
    await cen.sobe()
    return cen


# ---------------------------------------------------------------------------------------------- cursor e 1ª subida
async def test_primeira_subida_grava_o_cursor_e_nao_trata_o_historico(tmp_path: Path) -> None:
    cen = Cenario(tmp_path)
    cen.trello.comenta(DONO, C_APR, "/vetar")                      # de ontem: não pode agir
    ultima = cen.trello.move(DONO, C_APR, L_NAO)
    assert await cen.volta() == 0
    marco = cen.cursor.ler(QUADRO)
    assert marco is not None and marco["ultima_action"] == ultima["id"]
    assert cen.acoes_da_central() == [] and cen.trello.escritas() == []
    assert cen.repo.contagens() == {}
    # O que chega depois da subida é tratado.
    cen.trello.comenta(DONO, C_APR, "/vetar")
    assert await cen.volta() == 1
    assert cen.acoes_da_central() == [("decidir", ("apr-0000aa11", "reject"), f"trello:{DONO}")]


async def test_primeira_subida_de_quadro_vazio_nao_descarta_o_que_vem_depois(tmp_path: Path) -> None:
    cen = Cenario(tmp_path)
    await cen.sobe()                                               # nenhuma action: o cursor nasce mesmo assim
    marco = cen.cursor.ler(QUADRO)
    assert marco is not None and marco["ultima_action"] is None
    cen.trello.comenta(DONO, C_APR, "/vetar")
    assert await cen.volta() == 1
    assert [x[0] for x in cen.acoes_da_central()] == ["decidir"]


async def test_cursor_anda_e_a_leitura_seguinte_pede_so_o_que_veio_depois(c: Cenario) -> None:
    primeira = c.trello.comenta(DONO, C_MANUAL, "/status")
    assert await c.volta() == 1
    assert c.cursor.ler(QUADRO)["ultima_action"] == primeira["id"]          # type: ignore[index]
    await c.volta()                                                 # lê só o eco da resposta da Central
    pedidas = [p for p in c.trello.pedidos if p.method == "GET" and p.url.path.endswith("/actions")]
    assert dict(pedidas[-1].url.params)["since"] == primeira["id"]
    assert c.cursor.ler(QUADRO)["ultima_action"] == "cm0001"                # type: ignore[index]
    assert "Authorization" in pedidas[-1].headers and TOKEN not in str(pedidas[-1].url)


async def test_a_mesma_action_lida_de_novo_nao_roda_duas_vezes(c: Cenario) -> None:
    c.trello.eco = False
    c.trello.move(DONO, C_APR, L_NAO)
    c.trello.ignorar_since = True                                   # o Trello devolve a de antes junto (since inclusivo)
    await c.volta()
    await c.volta()
    await c.volta()
    assert c.acoes_da_central() == [("decidir", ("apr-0000aa11", "reject"), f"trello:{DONO}")]
    assert c.repo.contagens() == {"feita": 1}


async def test_sem_lideranca_sem_dono_ou_sem_segredo_nao_le_o_trello(tmp_path: Path) -> None:
    sem_lider = Cenario(tmp_path / "a")
    sem_lider.lider = None
    sem_dono = Cenario(tmp_path / "b", dono="")
    sem_segredo = Cenario(tmp_path / "c")
    sem_segredo.cfg.env.trello_token = None
    sem_segredo.leitor._cliente = None                              # noqa: SLF001 - o teste tira o cliente injetado
    for cen in (sem_lider, sem_dono, sem_segredo):
        cen.trello.comenta(DONO, C_APR, "/vetar")
        assert await cen.volta() == 0
        assert cen.trello.pedidos == [] and cen.acoes_da_central() == []


# ---------------------------------------------------------------------------------------------- a tradução
def _traduz(cfg: Config, acao: dict[str, object], chave: str | None = APROVACAO) -> Recebida | None:
    return recebida_da_action(acao, cfg.file.trello, chave_do_cartao=lambda _c: chave, quadro=QUADRO)


async def test_recebida_da_action_traduz_comentario_e_movimento(c: Cenario) -> None:
    com = c.trello.comenta(DONO, C_APR, "sim", ha_s=30)
    r = _traduz(c.cfg, com)
    assert r is not None
    assert (r.id_externo, r.tipo, r.do_dono, r.texto, r.responde_a) == (com["id"], "mensagem", True, "sim", f"fato:{APROVACAO}")
    assert r.escrita_em == pytest.approx((c.relogio.t - timedelta(seconds=30)).timestamp())
    assert RefDoTrello.ler(r.ref_mensagem) == RefDoTrello(C_APR, str(com["id"]), DONO)
    ok, nao = c.trello.move(DONO, C_APR, L_OK), c.trello.move(DONO, C_APR, L_NAO)
    assert [(x.tipo, x.texto) for x in (_traduz(c.cfg, ok), _traduz(c.cfg, nao)) if x] == [("botao", "sim"), ("botao", "não")]
    # O resto não vira comando e é gravado sem texto: cartão novo, outra lista, cartão que não é de aprovação.
    novo = c.trello.acao("createCard", DONO, C_APR)
    outra = c.trello.move(DONO, C_APR, L_OUTRA)
    assert [(x.tipo, x.texto) for x in (_traduz(c.cfg, novo), _traduz(c.cfg, outra)) if x] == [("outro", ""), ("outro", "")]
    run = c.trello.move(DONO, C_RUN, L_OK)
    r2 = _traduz(c.cfg, run, PERGUNTA)
    assert r2 is not None and r2.tipo == "outro"                    # mover uma pergunta para ✅ não é a resposta "sim"
    assert _traduz(c.cfg, {"type": "commentCard"}) is None          # sem id


async def test_so_o_autor_lido_na_action_e_um_quadro_da_config_valem_como_dono(c: Cenario) -> None:
    convidado = c.trello.comenta(CONVIDADO, C_APR, "sim")
    fora = c.trello.comenta(DONO, C_APR, "sim", quadro="quadro-de-outro")
    sem_autor = {"id": "x1", "type": "commentCard", "data": {"board": {"id": QUADRO}, "card": {"id": C_APR}, "text": "sim"}}
    assert [x.do_dono for x in (_traduz(c.cfg, convidado), _traduz(c.cfg, fora)) if x] == [False, False]
    assert _traduz(c.cfg, fora).tipo == "outro"                     # type: ignore[union-attr]
    # Sem dono configurado, "" == "" não pode abrir a porta.
    c.cfg.file.trello.membro_dono = ""
    r = _traduz(c.cfg, sem_autor)
    assert r is not None and r.do_dono is False


# ---------------------------------------------------------------------------------------------- aprovar não aprova
@pytest.mark.parametrize("como", ["mover", "sim", "/aprovar", "/aprovar apr-0000aa11 pode", "Pode"])
async def test_aprovar_pelo_trello_so_pede_confirmacao_fora_e_nao_decide(c: Cenario, como: str) -> None:
    if como == "mover":
        c.trello.move(DONO, C_APR, L_OK)
    else:
        c.trello.comenta(DONO, C_APR if como != "/aprovar apr-0000aa11 pode" else C_MANUAL, como)
    await c.volta()
    assert c.acoes_da_central() == []                               # NENHUM `decidir`
    assert c.trello.textos() == [PREFIXO + RESPOSTA_APROVAR_FORA]
    assert PREFIXO + "para aprovar, confirme no painel ou no Telegram" == c.trello.textos()[0]


async def test_aprovar_um_id_que_nao_esta_pendente_diz_que_nao_achou_e_nao_decide(c: Cenario) -> None:
    c.trello.comenta(DONO, C_MANUAL, "/aprovar zzzz9999")
    await c.volta()
    assert c.acoes_da_central() == []
    assert len(c.trello.textos()) == 1 and "Não achei" in c.trello.textos()[0]


async def test_vetar_pelo_trello_veta_com_o_operador_do_autor(c: Cenario) -> None:
    c.trello.move(DONO, C_APR, L_NAO)
    await c.volta()
    assert c.acoes_da_central() == [("decidir", ("apr-0000aa11", "reject"), f"trello:{DONO}")]
    assert c.trello.textos() == [PREFIXO + "Vetado: nada é enviado."]
    for texto in ("não", "/vetar"):
        c.trello.comenta(DONO, C_APR, texto)
        await c.volta()
    assert [x[1] for x in c.acoes_da_central()] == [("apr-0000aa11", "reject")] * 3
    c.trello.comenta(DONO, C_MANUAL, "/vetar apr-0000bb22 não cabe")
    await c.volta()
    assert c.acoes_da_central()[-1] == ("decidir", ("apr-0000bb22", "reject", "não cabe"), f"trello:{DONO}")


async def test_a_aprovacao_so_decide_no_trello_se_for_veto_mesmo_com_o_dono_certo(c: Cenario) -> None:
    c.trello.comenta(DONO, C_APR, "sim")
    c.trello.move(DONO, C_APR, L_OK)
    c.trello.comenta(DONO, C_APR, "/aprovar")
    await c.volta()
    assert all(x[0] != "decidir" or x[1][1] == "reject" for x in c.acoes_da_central())
    assert c.acoes_da_central() == []


# ---------------------------------------------------------------------------------------------- o convidado
async def test_convidado_com_o_padrao_nao_executa_e_o_trello_fica_em_silencio(c: Cenario) -> None:
    c.trello.eco = False
    c.trello.comenta(CONVIDADO, C_APR, "/vetar")
    c.trello.comenta(CONVIDADO, C_APR, f"sim, {SEGREDINHO}")
    c.trello.move(CONVIDADO, C_APR, L_NAO)
    c.trello.move(CONVIDADO, C_APR, L_OK)
    c.trello.comenta(CONVIDADO, C_APR, f"{SEGREDINHO} qual o status?")
    await c.volta()
    assert c.acoes_da_central() == []
    assert c.trello.escritas() == []                                # silêncio no Trello
    linhas = c.db.query("SELECT estado, texto, do_dono FROM canal_entradas WHERE canal='trello'")
    assert len(linhas) == 5 and {(r["estado"], r["texto"], r["do_dono"]) for r in linhas} == {("ignorada", None, 0)}
    # O pedido (não a pergunta) vira um aviso ao dono, sem o texto nem o nome do convidado.
    assert len(c.avisos) == 2
    assert all(SEGREDINHO not in f"{a.titulo} {a.corpo}" and CONVIDADO not in f"{a.chave} {a.titulo} {a.corpo}"
               for a in c.avisos)


async def test_convidado_com_responder_convidados_a_pergunta_recebe_o_resumo_e_o_pedido_o_comentario_fixo(
        tmp_path: Path) -> None:
    cen = Cenario(tmp_path, responder_convidados=True)
    await cen.sobe()
    cen.trello.comenta(CONVIDADO, C_APR, f"como está a Central? {SEGREDINHO}?")
    cen.trello.comenta(CONVIDADO, C_APR, "/status")
    await cen.volta()
    assert cen.trello.textos() == [PREFIXO + "Central: ok", PREFIXO + "Central: ok"]
    assert cen.avisos == []
    assert [x[0] for x in cen.acoes_da_central()] == ["status", "status"]
    # Pedido: o comentário fixo e o aviso. Nada executa.
    cen.trello.comenta(CONVIDADO, C_APR, f"/vetar {SEGREDINHO}")
    cen.trello.comenta(CONVIDADO, C_APR, f"abre o app agora {SEGREDINHO}")
    await cen.volta()
    assert cen.trello.textos()[2:] == [PREFIXO + "recebido; aguardando o dono"] * 2
    assert RESPOSTA_CONVIDADO in cen.trello.textos()[-1]
    assert len(cen.avisos) == 2 and [x[0] for x in cen.acoes_da_central()] == ["status", "status"]
    assert SEGREDINHO not in cen.corpos() and SEGREDINHO not in " ".join(a.corpo for a in cen.avisos)
    assert cen.db.scalar("SELECT COUNT(*) FROM canal_entradas WHERE canal='trello' AND texto LIKE ?",
                         (f"%{SEGREDINHO}%",)) == 0


async def test_o_convidado_nao_faz_a_central_falar_sem_parar_e_a_velha_nao_responde(tmp_path: Path) -> None:
    cen = Cenario(tmp_path, responder_convidados=True)
    await cen.sobe()
    cen.trello.comenta(CONVIDADO, C_APR, "pedido velho", ha_s=3600)                 # escrita com a Central fora do ar
    for i in range(15):
        cen.trello.comenta(CONVIDADO, C_APR, f"pedido {i}")
    await cen.volta()
    assert cen.trello.textos() == [PREFIXO + RESPOSTA_CONVIDADO] * 10 and len(cen.avisos) == 10


async def test_membro_autorizado_pede_mas_nao_decide_e_o_padrao_e_vazio(tmp_path: Path) -> None:
    cen = Cenario(tmp_path / "a")
    assert cen.cfg.file.trello.membros_autorizados == [] and cen.cfg.file.trello.responder_convidados is False
    aut = Cenario(tmp_path / "b", autorizados=[AMIGO])
    await aut.sobe()
    aut.trello.comenta(AMIGO, C_APR, "/status")
    aut.trello.comenta(AMIGO, C_APR, "/vetar")
    aut.trello.comenta(AMIGO, C_APR, "sim")
    aut.trello.comenta(AMIGO, C_MANUAL, "/para android-09 abrir x")
    await aut.volta()
    assert aut.acoes_da_central() == [("status", (), f"trello:{AMIGO}")]       # o operador é o do AUTOR
    assert aut.trello.textos() == [PREFIXO + "Central: ok", PREFIXO + RESPOSTA_SO_O_DONO, PREFIXO + RESPOSTA_SO_O_DONO,
                                   PREFIXO + RESPOSTA_COMANDO_LIVRE]


# ---------------------------------------------------------------------------------------------- credencial e pergunta
@pytest.mark.parametrize("texto", ["minha senha é Abc!2345xyz", "123456", "/responder 4985a1 123456"])
async def test_credencial_no_comentario_e_recusada_sem_eco_e_pede_que_o_dono_apague(c: Cenario, texto: str) -> None:
    c.trello.comenta(DONO, C_RUN, texto)
    await c.volta()
    r = c.db.query("SELECT estado, texto, tamanho FROM canal_entradas WHERE canal='trello' ORDER BY id")[0]
    assert (r["estado"], r["texto"], r["tamanho"]) == ("recusada", None, len(texto))
    assert c.acoes_da_central() == []
    [resposta] = c.trello.textos()
    assert resposta.startswith(PREFIXO) and "Apague o comentário do cartão" in resposta and "chat" not in resposta
    assert texto not in resposta and texto not in c.corpos()
    await c.volta()
    await c.volta()
    assert len(c.trello.textos()) == 1                                # a recusa responde uma vez só


async def test_resposta_a_pergunta_de_credencial_e_recusada_pelo_contexto(c: Cenario) -> None:
    c.portas.pergunta = "Qual é a senha da conta?"
    c.trello.comenta(DONO, C_RUN, "kiwi2024")
    await c.volta()
    assert all(x[0] != "responder" for x in c.portas.chamadas)
    r = c.db.query("SELECT estado, texto FROM canal_entradas WHERE canal='trello' ORDER BY id")[0]
    assert (r["estado"], r["texto"]) == ("recusada", None)
    [resposta] = c.trello.textos()
    assert "pergunta por senha" in resposta and "Apague o comentário do cartão" in resposta and "kiwi2024" not in resposta


async def test_texto_ou_responder_num_cartao_de_pergunta_e_a_resposta(c: Cenario) -> None:
    c.trello.comenta(DONO, C_RUN, "Marina")
    c.trello.comenta(DONO, C_RUN, "/responder Carlos")
    await c.volta()
    assert [x[:2] for x in c.acoes_da_central()] == [("responder", ("r-20261002181523-4985a1", "Marina")),
                                                     ("responder", ("r-20261002181523-4985a1", "Carlos"))]
    assert all(x[2] == f"trello:{DONO}" for x in c.acoes_da_central())
    assert "Respondida" in c.trello.textos()[0]
    # O desfecho da execução nova volta ao cartão, sem a "Evidência" (texto do aparelho).
    c.portas.desfechos["r-nova-000001"] = "Execução 000001: concluída.\nObjetivos: 1 de 1 com sucesso.\nEvidência: @maria_ig"
    await c.volta()
    desfecho = c.trello.textos()[-1]
    assert "concluída" in desfecho and "Evidência" not in desfecho and "@maria_ig" not in c.corpos()


async def test_desfecho_que_nao_saiu_no_cartao_tenta_de_novo(c: Cenario) -> None:
    """28.38: o laço do desfecho é o da conversa; o Trello só troca o texto. O 5xx no comentário não marca a linha."""
    c.trello.comenta(DONO, C_RUN, "Marina")
    await c.volta()
    c.portas.desfechos["r-nova-000001"] = "Execução 000001: concluída.\nEvidência: @maria_ig"
    c.trello.falhar_comentario = [503]
    await c.volta()
    assert not any("concluída" in t for t in c.trello.textos())
    await c.volta()
    [final] = [t for t in c.trello.textos() if "concluída" in t]
    assert final.endswith("Execução 000001: concluída.\nO detalhe está no painel.")
    await c.volta()
    assert len([t for t in c.trello.textos() if "concluída" in t]) == 1


# ---------------------------------------------------------------------------------------------- comando livre
async def test_para_e_texto_livre_ficam_desligados_no_trello(c: Cenario) -> None:
    c.trello.comenta(DONO, C_MANUAL, "/para android-09 abrir o QA Messenger")
    c.trello.comenta(DONO, C_DEPLOY, "abre o app no android-09 agora")
    await c.volta()
    assert c.acoes_da_central() == []
    assert c.trello.textos() == [PREFIXO + RESPOSTA_COMANDO_LIVRE] * 2
    assert "comando livre está desligado no Trello; use o painel ou o Telegram" in c.trello.textos()[0]


async def test_com_comando_livre_o_trello_so_mostra_a_previa_e_nao_executa(tmp_path: Path) -> None:
    cen = Cenario(tmp_path, comando_livre=True)
    await cen.sobe()
    cen.trello.comenta(DONO, C_MANUAL, "/para android-09 abrir o QA Messenger")
    await cen.volta()
    assert [x[0] for x in cen.acoes_da_central()] == ["previa"]                     # nenhum `criar`
    [resposta] = cen.trello.textos()
    assert resposta.startswith(PREFIXO + "Prévia: android-09.") and "abrir" not in resposta and "QA" not in resposta


async def test_com_comando_livre_o_espelho_do_deploy_nao_vira_previa(tmp_path: Path) -> None:
    """28.41 (regra de fundo no Trello): o comentário num cartão cujo fato não tem ramo, como o espelho do deploy, nunca
    vira prévia, nem com o comando livre ligado; responde o "comando livre está desligado" de sempre."""
    cen = Cenario(tmp_path, comando_livre=True)
    await cen.sobe()
    cen.trello.comenta(DONO, C_DEPLOY, "abre o app no android-09 agora")
    await cen.volta()
    assert cen.acoes_da_central() == []
    assert cen.trello.textos() == [PREFIXO + RESPOSTA_COMANDO_LIVRE]


async def test_pergunta_no_espelho_do_deploy_chega_a_orquestradora(c: Cenario) -> None:
    """28.41, G1 de ponta a ponta: a pergunta num cartão cujo fato não tem ramo não é texto livre; a linha fica para a
    orquestradora (28.28) e o cartão ouve a frase do repasse, sem nada na Central."""
    c.trello.comenta(DONO, C_DEPLOY, "o que houve?")
    await c.volta()
    assert c.acoes_da_central() == []
    assert c.repo.contagens() == {"orquestradora": 1}
    assert c.trello.textos() == [PREFIXO + RESPOSTA_DO_REPASSE["pergunta"]]


async def test_comentario_do_dono_num_cartao_sem_aviso_nao_fica_mudo(c: Cenario) -> None:
    """28.30 (antes: anotação solta só registrada, e o "Autorizado" do dono ficou 1 h 30 sem ninguém ver). Nada executa: vai
    à orquestradora, pede a confirmação dele no Telegram e responde no cartão. O cartão novo segue só registrado."""
    c.trello.comenta(DONO, C_MANUAL, "lembrar de olhar isto amanhã")
    c.trello.acao("createCard", DONO, C_MANUAL)
    await c.volta()
    assert c.acoes_da_central() == []
    assert c.repo.contagens() == {"ignorada": 1, "orquestradora": 1}
    assert [a.tipo for a in c.avisos] == ["trello.comentario"]
    assert c.trello.textos() == [PREFIXO + "Recebi o seu comentário. Pedi a sua confirmação no Telegram: o sim de lá é "
                                           "que vale."]


# ---------------------------------------------------------------------------------------------- /status, /pendencias, /ajuda
async def test_status_pendencias_e_ajuda_do_dono_respondem_em_comentario_curto_e_sem_o_resumo(c: Cenario) -> None:
    for texto in ("/status", "/pendencias", "/ajuda"):
        c.trello.comenta(DONO, C_MANUAL, texto)
    await c.volta()
    status, pendencias, ajuda = c.trello.textos()
    assert status == PREFIXO + "Central: ok"
    assert "2 esperando você" in pendencias and "0000aa11"[-6:] in pendencias and "4985a1" in pendencias
    assert "comentar" not in pendencias and '"oi"' not in pendencias             # o resumo da aprovação não vai ao Trello
    assert ajuda.startswith(PREFIXO + "Comandos da Central") and AJUDA_DO_TRELLO.strip() in ajuda


async def test_quem_e_a_ana_no_trello_responde_que_e_ia(c: Cenario) -> None:
    """28.17: `/quem` num cartão qualquer responde com a apresentação e "uma IA"; nada vai à prévia."""
    c.trello.comenta(DONO, C_MANUAL, "/quem")
    await c.volta()
    assert c.trello.textos() == [PREFIXO + RESPOSTA_IDENTIDADE]
    assert c.acoes_da_central() == []


async def test_a_resposta_passa_pela_redacao_e_tira_email_conta_e_ip(c: Cenario) -> None:
    s = SaidaDoTrello(c.cliente, TriagemDeCredencial().redigir, c.relogio)
    await s.responder("fale com ana@exemplo.com ou @lucas_ig em 192.168.1.19 senha=abc123456",
                      responde_a=str(RefDoTrello(C_APR, "x", DONO)))
    [texto] = c.trello.textos()
    assert all(v not in texto for v in ("ana@exemplo.com", "@lucas_ig", "192.168.1.19", "abc123456"))
    assert texto.startswith(PREFIXO) and not await s.apagar("x") and await s.responder("oi") is None


# ---------------------------------------------------------------------------------------------- idade, eco e saúde
async def test_action_velha_fica_ignorada_sem_texto_e_nao_executa(c: Cenario) -> None:
    c.trello.move(DONO, C_APR, L_NAO, ha_s=3600)
    c.trello.comenta(DONO, C_APR, "/vetar", ha_s=3600)
    await c.volta()
    assert c.acoes_da_central() == [] and c.trello.escritas() == []
    r = c.db.query("SELECT estado, texto FROM canal_entradas WHERE canal='trello'")
    assert {(x["estado"], x["texto"]) for x in r} == {("ignorada", None)}


async def test_o_que_a_propria_central_escreveu_volta_como_action_do_dono_e_e_ignorado(c: Cenario) -> None:
    c.trello.comenta(DONO, C_MANUAL, "/status")
    await c.volta()
    await c.volta()
    await c.volta()
    assert len(c.trello.textos()) == 1                              # o eco do comentário não virou comando nem laço
    eco = c.db.query("SELECT estado, texto FROM canal_entradas WHERE canal='trello' AND id_externo='cm0001'")
    assert [(x["estado"], x["texto"]) for x in eco] == [("ignorada", None)]
    # E o comentário com o prefixo da Central que NÃO está no registro de enviadas (o desfecho do espelho) também.
    c.trello.eco = False
    c.trello.comenta(DONO, C_APR, PREFIXO + "resolvido: a aprovação foi decidida")
    await c.volta()
    assert len(c.trello.textos()) == 1 and c.acoes_da_central()[-1][0] == "status"
    # O comentário de outra IA com a conta do dono (a orquestradora, ou o formato antigo da Canais) também não é pedido.
    antes = len(c.acoes_da_central())
    for texto in ("🤖 ORQ · 10:00Z · /status", "🤖 10:00Z · /pendencias"):
        acao = c.trello.comenta(DONO, C_MANUAL, texto)
        await c.volta()
        linha = c.db.query("SELECT estado, texto FROM canal_entradas WHERE canal='trello' AND id_externo=?",
                           (acao["id"],))
        assert [(x["estado"], x["texto"]) for x in linha] == [("ignorada", None)]
    assert len(c.trello.textos()) == 1 and len(c.acoes_da_central()) == antes


async def test_toda_escrita_comeca_com_o_prefixo_da_ia_e_nada_do_convidado_vai_ao_trello(tmp_path: Path) -> None:
    cen = Cenario(tmp_path, responder_convidados=True)
    await cen.sobe()
    cen.trello.comenta(DONO, C_MANUAL, "/status")
    cen.trello.comenta(DONO, C_MANUAL, "/pendencias")
    cen.trello.comenta(DONO, C_APR, "sim")
    cen.trello.move(DONO, C_APR, L_NAO)
    cen.trello.comenta(DONO, C_RUN, "123456")
    cen.trello.comenta(CONVIDADO, C_APR, f"{SEGREDINHO} /vetar")
    cen.trello.comenta(CONVIDADO, C_APR, f"{SEGREDINHO}?")
    await cen.volta()
    assert len(cen.trello.escritas()) == len(cen.trello.comentarios) >= 6
    assert all(t.startswith(f"🤖 {NOME_DA_IA} · ") for t in cen.trello.textos())
    assert all(p.url.path.endswith("/actions/comments") for p in cen.trello.escritas())      # só comenta: nada de mover/apagar
    assert SEGREDINHO not in cen.corpos() and CONVIDADO not in cen.corpos()
    assert CHAVE not in cen.corpos() and TOKEN not in cen.corpos()


async def test_saude_leitor_atrasado_so_quando_a_leitura_para_e_nao_num_quadro_quieto(c: Cenario) -> None:
    assert c.leitor.problemas() == []
    await c.relogio.dormir(500)                                    # quadro quieto, mas a leitura segue passando
    await c.volta()
    assert c.leitor.problemas() == []
    await c.relogio.dormir(3 * 60 + 1)                              # a leitura parou (3 × reconciliar_s = 180 s)
    assert [p.code for p in c.leitor.problemas()] == ["trello_leitor_atrasado"]
    await c.volta()
    assert c.leitor.problemas() == []
    c.cfg.file.trello.enabled = False
    await c.relogio.dormir(9999)
    assert c.leitor.problemas() == []


async def test_saude_trello_recusado_aparece_na_leitura_e_some_quando_volta(c: Cenario) -> None:
    c.trello.falhar = [401]
    assert await c.volta() == 0
    [p] = c.leitor.problemas()
    assert p.code == "trello_recusado" and "401" in p.message and CHAVE not in p.message + p.hint
    await c.volta()
    assert [x.code for x in c.leitor.problemas()] == []


async def test_falha_nao_definitiva_nao_vira_problema_e_a_proxima_volta_le(c: Cenario) -> None:
    c.trello.comenta(DONO, C_APR, "/vetar")
    c.trello.falhar = [503]
    await c.volta()
    assert c.leitor.problemas() == [] and c.acoes_da_central() == []
    await c.volta()
    assert [x[0] for x in c.acoes_da_central()] == ["decidir"]


# ---------------------------------------------------------------------------------------------- o contrato comum
@pytest.mark.parametrize("texto,esperado", [("/vetar", ["decidir"]), ("123456", []), ("/status", ["status"]),
                                            ("/aprovar", ["decidir"])])
async def test_o_mesmo_comando_por_telegram_e_por_trello_passa_pelas_mesmas_politicas(
        tmp_path: Path, texto: str, esperado: list[str]) -> None:
    # Trello: o cartão de aprovação é o fato, então o id não se escreve.
    cen = Cenario(tmp_path / "t")
    await cen.sobe()
    cen.trello.comenta(DONO, C_APR, texto)
    await cen.volta()
    do_trello = [(n, a) for n, a, _ in cen.acoes_da_central()]
    # Telegram: a mesma conversa comum, com o id escrito (o aviso respondido faria o mesmo).
    cfg = make_config(tmp_path / "g")
    cfg.ensure_dirs()
    db = Database(cfg.db_dsn)
    db.migrate()
    repo, portas, saida = EntradasDoCanal(db, canal="telegram"), PortasFalsas(), SaidaFalsa(pode_apagar=True)
    triagem = TriagemDeCredencial()
    conversa = ConversaDoCanal(cfg, repo, portas, recusa=triagem.recusa, redigir=triagem.redigir)
    escrito = texto.replace("/vetar", "/vetar apr-0000aa11").replace("/aprovar", "/aprovar apr-0000aa11")
    await conversa.registrar(Recebida(id_externo="1", ordem=1, tipo="mensagem", do_dono=True, texto=escrito,
                                      ref_mensagem="m1"), saida)
    await conversa.tratar_pendentes(saida)
    do_telegram = [(n, a) for n, a, _ in portas.chamadas if n != "pergunta_sensivel"]
    # Credencial e status: idênticos nos dois. Veto: o mesmo `decidir reject`. O que difere é a regra (b): o Telegram
    # APROVA e o Trello só pede confirmação.
    if texto == "/aprovar":
        assert do_trello == [] and [n for n, _ in do_telegram] == ["decidir"]
    else:
        assert [n for n, _ in do_trello] == esperado == [n for n, _ in do_telegram]
        assert do_trello == do_telegram or texto == "/vetar"
    if texto == "123456":
        assert repo.contagens() == cen.repo.contagens() == {"recusada": 1}
        assert "Apague" in saida.textos()[0] or "apaguei" in saida.textos()[0]
