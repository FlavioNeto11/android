"""Item 32.2 (5/6) — o webhook do Trello (ADR-072, `docs/design/trello-integracao.md` §8): a rota que só ANOTA o aviso, a
assinatura que falha fechada, a releitura da action pela API pelo líder e o cadastro (só com ensaio).

Prova `simulated`: cliente ASGI do FastAPI (sem rede), `httpx.MockTransport` como Trello falso, relógio falso. Nenhum
webhook foi cadastrado no Trello de verdade, e nenhum teste chama a rota de login.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import importlib.util
import io
import json
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from app.config import Config
from app.main import create_app
from app.modules.avisos.adapters.trello import ClienteTrello
from app.modules.avisos.infrastructure.trello_webhook import (
    DESCRICAO,
    JANELA_INVALIDAS_S,
    LIMITE_INVALIDAS,
    LOG_DE_RECUSA_A_CADA_S,
    MAX_INVALIDAS_GUARDADAS,
    CadastroDoWebhook,
    PortaDoWebhook,
)

from .conftest import Harness
from .test_trello_leitor import (
    APROVACAO,
    C_APR,
    CHAVE,
    DONO,
    CONVIDADO,
    L_NAO,
    PREFIXO,
    QUADRO,
    TOKEN,
    Cenario,
)

pytestmark = pytest.mark.asyncio

SEGREDO_APP = "segredo-do-aplicativo-falso-0123456789"
CALLBACK = "https://dev.exemplo.test/api/canais/trello/webhook"
HOST_PUBLICO = "dev.exemplo.test"
ROTA = "/api/canais/trello/webhook"
ID_ACAO = "5f0000000000000000000a01"


def assinar(corpo: bytes, *, segredo: str = SEGREDO_APP, url: str = CALLBACK) -> str:
    """A conta do Trello escrita de novo aqui, sem importar a do código: base64(HMAC-SHA1(segredo, corpo + callbackURL))."""
    return base64.b64encode(hmac.new(segredo.encode(), corpo + url.encode(), hashlib.sha1).digest()).decode()


def corpo_de(ident: str = ID_ACAO, tipo: str = "commentCard", autor: str = DONO, texto: str = "/vetar", **dados: object) -> bytes:
    return json.dumps({"action": {"id": ident, "type": tipo, "idMemberCreator": autor,
                                  "data": {"text": texto, "card": {"id": C_APR}, "board": {"id": QUADRO}, **dados}}}).encode()


def _ligar(cfg: Config, *, enabled: bool = True, segredo: str | None = SEGREDO_APP, url: str | None = CALLBACK) -> None:
    cfg.file.trello.webhook.enabled = enabled
    cfg.file.trello.webhook.callback_url = url
    cfg.env.trello_api_secret = SecretStr(segredo) if segredo else None


def _publico(h: Harness) -> None:
    """O portão como no túnel (ADR-073): par 127.0.0.1, `Host` público, credencial exigida de quem não é loopback de verdade."""
    h.cfg.file.server.public_hosts = [HOST_PUBLICO]
    h.cfg.env.api_token = SecretStr("token-do-painel-de-teste-0123456789")
    _ligar(h.cfg)


def _cliente(h: Harness, host: str = HOST_PUBLICO) -> httpx.AsyncClient:
    app = create_app(h.cfg, state=h.state)
    app.state.poc = h.state
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 123)), base_url=f"http://{host}")


def _avisos(h: Harness) -> list[dict[str, object]]:
    assert h.state is not None
    return [dict(r) for r in h.state.db.query(
        "SELECT * FROM canal_entradas WHERE canal='trello' AND estado='aviso' ORDER BY id")]


# ---------------------------------------------------------------------------------------------- HEAD
async def test_head_responde_200_ligado_e_404_sem_ligar_ou_sem_segredo_ou_sem_url(harness: Harness) -> None:
    _publico(harness)
    async with _cliente(harness) as c:
        assert (await c.head(ROTA)).status_code == 200                       # o Trello confere a URL ao cadastrar
        for ajuste in (dict(enabled=False), dict(segredo=None), dict(url=None)):
            _ligar(harness.cfg, **ajuste)                                    # type: ignore[arg-type]
            assert (await c.head(ROTA)).status_code == 404, ajuste
            _ligar(harness.cfg)


# ---------------------------------------------------------------------------------------------- POST: assinatura
async def test_post_com_assinatura_valida_grava_so_o_id_e_acorda_o_leitor(harness: Harness) -> None:
    _publico(harness)
    assert harness.state is not None
    corpo = corpo_de(texto="SEGREDINHO-NO-CORPO")
    acordou: list[int] = []
    harness.state.trello_webhook._acordar = lambda: acordou.append(1)      # noqa: SLF001 - o laço real consumiria o evento
    async with _cliente(harness) as c:
        # O caminho por extenso (não `ROTA`): a cobertura de rotas (`test_cobertura_de_rotas`) só lê literal.
        r = await c.post("/api/canais/trello/webhook", content=corpo, headers={"X-Trello-Webhook": assinar(corpo)})
        assert r.status_code == 200 and r.content == b""
        outra = await c.post(ROTA, content=corpo, headers={"X-Trello-Webhook": assinar(corpo)})     # as 3 repetições do Trello
        assert outra.status_code == 200
    [linha] = _avisos(harness)
    assert (linha["id_externo"], linha["estado"], linha["texto"], linha["do_dono"], linha["ref_mensagem"]) == (
        ID_ACAO, "aviso", None, 0, None)                   # nem texto, nem autor, nem cartão do corpo: só o id
    assert acordou == [1]                                  # acordou o leitor UMA vez (a repetição não é aviso novo)
    todas = harness.state.db.query("SELECT * FROM canal_entradas WHERE canal='trello'")
    assert len(todas) == 1 and "SEGREDINHO" not in str([dict(x) for x in todas])


@pytest.mark.parametrize("como", ["sem_cabecalho", "assinatura_errada", "segredo_errado", "url_diferente", "corpo_mexido"])
async def test_assinatura_invalida_da_401_sem_corpo_e_sem_gravar_nada(harness: Harness, como: str) -> None:
    _publico(harness)
    corpo = corpo_de()
    cab = {"X-Trello-Webhook": {"sem_cabecalho": "", "assinatura_errada": "AAAA",
                                "segredo_errado": assinar(corpo, segredo="outro-segredo"),
                                "url_diferente": assinar(corpo, url=CALLBACK + "/"),
                                "corpo_mexido": assinar(corpo)}[como]}
    if como == "sem_cabecalho":
        cab = {}
    async with _cliente(harness) as c:
        r = await c.post(ROTA, content=corpo + b" " if como == "corpo_mexido" else corpo, headers=cab)
    assert r.status_code == 401 and r.content == b""
    assert _avisos(harness) == []


async def test_falha_fechada_ligado_sem_segredo_ou_sem_url_da_401_e_desligado_da_404(harness: Harness) -> None:
    _publico(harness)
    corpo = corpo_de()
    async with _cliente(harness) as c:
        _ligar(harness.cfg, segredo=None)
        # Mesmo uma "assinatura" feita com segredo vazio não abre a porta.
        assert (await c.post(ROTA, content=corpo, headers={"X-Trello-Webhook": assinar(corpo, segredo="")})).status_code == 401
        _ligar(harness.cfg, url=None)
        assert (await c.post(ROTA, content=corpo, headers={"X-Trello-Webhook": assinar(corpo, url="")})).status_code == 401
        _ligar(harness.cfg, enabled=False)
        assert (await c.post(ROTA, content=corpo, headers={"X-Trello-Webhook": assinar(corpo)})).status_code == 404
    assert _avisos(harness) == []


async def test_corpo_acima_do_teto_da_413_sem_ler_o_resto(harness: Harness) -> None:
    _publico(harness)
    harness.cfg.file.trello.webhook.max_bytes = 1024
    grande = corpo_de(texto="x" * 5000)
    async with _cliente(harness) as c:
        assert (await c.post(ROTA, content=grande, headers={"X-Trello-Webhook": assinar(grande)})).status_code == 413

        async def em_pedacos():  # type: ignore[no-untyped-def]
            for _ in range(5):                                   # sem Content-Length: o teto vale pelo que se lê
                yield b"y" * 400
        assert (await c.post(ROTA, content=em_pedacos(), headers={"X-Trello-Webhook": "x"})).status_code == 413
    assert _avisos(harness) == []


@pytest.mark.parametrize("corpo", [b"isto nao e json", b"[]", json.dumps({"action": {"id": "ab", "type": "commentCard"}}).encode(),
                                   corpo_de(tipo="addMemberToCard"), corpo_de(tipo="updateCard"),
                                   corpo_de(ident="id com espaco/e barra")])
async def test_assinado_mas_irrelevante_responde_200_e_nao_grava(harness: Harness, corpo: bytes) -> None:
    _publico(harness)
    async with _cliente(harness) as c:
        assert (await c.post(ROTA, content=corpo, headers={"X-Trello-Webhook": assinar(corpo)})).status_code == 200
    assert _avisos(harness) == []


async def test_card_movido_de_lista_e_aviso_mas_a_descricao_atualizada_nao(harness: Harness) -> None:
    _publico(harness)
    movido = corpo_de(ident="5f0000000000000000000a02", tipo="updateCard", listAfter={"id": L_NAO})
    async with _cliente(harness) as c:
        assert (await c.post(ROTA, content=movido, headers={"X-Trello-Webhook": assinar(movido)})).status_code == 200
    assert [a["id_externo"] for a in _avisos(harness)] == ["5f0000000000000000000a02"]


# ---------------------------------------------------------------------------------------------- o portão
async def test_so_este_caminho_e_estes_metodos_ficam_fora_do_login(harness: Harness) -> None:
    """Nada de login aqui: só se mostra que o RESTO de `/api/` segue pedindo credencial pelo Host público."""
    _publico(harness)
    corpo = corpo_de()
    async with _cliente(harness) as c:
        for metodo, caminho in (("GET", "/api/health"), ("GET", ROTA), ("PUT", ROTA), ("DELETE", ROTA),
                                ("POST", ROTA + "/"), ("POST", ROTA + "/x"), ("POST", "/api/canais/trello"),
                                ("POST", "/api/canais/trello/webhook2")):
            r = await c.request(metodo, caminho, content=corpo, headers={"X-Trello-Webhook": assinar(corpo)})
            assert r.status_code == 401, (metodo, caminho)
    # O Host não declarado segue 403 (nenhuma exceção ao `forbidden_host`), mesmo com assinatura certa.
    async with _cliente(harness, host="outro.exemplo.test") as c:
        assert (await c.post(ROTA, content=corpo, headers={"X-Trello-Webhook": assinar(corpo)})).status_code == 403
        assert (await c.head(ROTA)).status_code == 403
    assert _avisos(harness) == []


async def test_a_rota_ignora_o_cookie_e_nao_deixa_o_corpo_virar_operador(harness: Harness) -> None:
    _publico(harness)
    corpo = corpo_de()
    async with _cliente(harness) as c:
        r = await c.post(ROTA, content=corpo, headers={"X-Trello-Webhook": "AAAA", "Cookie": "poc_session=qualquer"})
    assert r.status_code == 401 and _avisos(harness) == []


# ---------------------------------------------------------------------------------------------- saúde da assinatura
async def test_assinaturas_invalidas_em_serie_viram_problema_e_a_janela_expira(tmp_path: Path) -> None:
    cen = Cenario(tmp_path)
    _ligar(cen.cfg)
    tempo = [0.0]
    porta = PortaDoWebhook(cen.cfg, cen.repo, relogio=lambda: tempo[0])
    for _ in range(LIMITE_INVALIDAS - 1):
        assert porta.receber(b"{}", "ruim").status == 401
    assert porta.problemas() == []
    porta.receber(b"{}", "ruim")
    [p] = porta.problemas()
    assert p.code == "trello_webhook_assinatura_invalida" and SEGREDO_APP not in p.message + p.hint
    tempo[0] += 3600
    assert porta.problemas() == []
    _ligar(cen.cfg, enabled=False)
    for _ in range(10):
        porta.receber(b"{}", "ruim")
    assert porta.problemas() == []                                       # desligado: a rota nem existe


async def test_assinatura_invalida_em_massa_nao_cresce_a_memoria_e_o_log_sai_por_amostra(tmp_path: Path) -> None:
    """Revisão da Android do #177: a rota é pública e a Cloudflare só limita o login. Recusa em massa não pode crescer a
    memória nem virar uma linha de log por pedido."""
    cen = Cenario(tmp_path)
    _ligar(cen.cfg)
    tempo = [0.0]
    porta = PortaDoWebhook(cen.cfg, cen.repo, relogio=lambda: tempo[0])
    for _ in range(MAX_INVALIDAS_GUARDADAS * 5):
        porta.receber(b"{}", "ruim")
    assert len(porta._invalidas) == MAX_INVALIDAS_GUARDADAS          # noqa: SLF001 - o teto é o que se prova
    assert porta.problemas()                                          # e o alarme continua acendendo
    assert porta.amostra_de_log("assinatura") == 0                    # a 1ª linha sai
    assert [porta.amostra_de_log("assinatura") for _ in range(50)] == [None] * 50
    tempo[0] += LOG_DE_RECUSA_A_CADA_S
    assert porta.amostra_de_log("assinatura") == 50                   # a próxima diz quantas ficaram de fora
    tempo[0] += JANELA_INVALIDAS_S + 1
    porta.receber(b"{}", "ruim")
    assert len(porta._invalidas) == 1                                 # noqa: SLF001 - a poda roda no append


# ---------------------------------------------------------------------------------------------- a releitura pela API (líder)
def _porta(cen: Cenario) -> PortaDoWebhook:
    _ligar(cen.cfg)
    return PortaDoWebhook(cen.cfg, cen.repo, acordar=cen.leitor.acordar)


async def test_o_lider_rele_a_action_pela_api_e_o_autor_forjado_no_corpo_nao_vale(tmp_path: Path) -> None:
    """Segredo vazado: corpo assinado CORRETAMENTE com o dono como autor, mas a API diz que o autor é um convidado."""
    cen = Cenario(tmp_path)
    await cen.sobe()
    real = cen.trello.comenta(CONVIDADO, C_APR, "/vetar", ident=ID_ACAO)
    corpo = corpo_de(ident=str(real["id"]), autor=DONO, texto="/vetar")
    assert _porta(cen).receber(corpo, assinar(corpo)).status == 200
    cen.trello.eco = False
    assert await cen.leitor.uma_volta(so_avisos=True) == 1
    assert cen.acoes_da_central() == []                                  # nada executa
    linha = cen.linha(ID_ACAO)
    assert (linha["estado"], linha["texto"], linha["do_dono"]) == ("ignorada", None, 0)
    assert cen.trello.escritas() == [] and cen.avisos and CONVIDADO not in cen.corpos()
    assert [p.url.path for p in cen.trello.pedidos if p.method == "GET"] == [f"/1/boards/{QUADRO}/actions",
                                                                              f"/1/actions/{ID_ACAO}"]


async def test_aviso_de_action_do_dono_e_relida_e_vira_o_comando_com_o_operador_do_autor(tmp_path: Path) -> None:
    cen = Cenario(tmp_path)
    await cen.sobe()
    cen.trello.eco = False
    cen.trello.comenta(DONO, C_APR, "/vetar", ident=ID_ACAO)
    corpo = corpo_de(ident=ID_ACAO, autor=CONVIDADO, texto="isto é só um aviso")      # o corpo mente; a API manda
    _porta(cen).receber(corpo, assinar(corpo))
    assert await cen.leitor.uma_volta(so_avisos=True) == 1
    assert cen.acoes_da_central() == [("decidir", ("apr-0000aa11", "reject"), f"trello:{DONO}")]
    assert cen.linha(ID_ACAO)["estado"] == "feita" and cen.repo.avisos_pendentes() == []
    # A reconciliação que lê a mesma action depois cai no dedupe: não decide de novo.
    await cen.volta()
    assert [x[0] for x in cen.acoes_da_central()] == ["decidir"]


async def test_action_que_a_api_nao_devolve_de_outro_quadro_ou_velha_fica_ignorada_sem_texto(tmp_path: Path) -> None:
    cen = Cenario(tmp_path)
    await cen.sobe()
    cen.trello.eco = False
    outro = cen.trello.comenta(DONO, C_APR, "/vetar", quadro="quadro-de-outro", ident="5f0000000000000000000b02")
    velha = cen.trello.comenta(DONO, C_APR, "/vetar", ha_s=3600, ident="5f0000000000000000000b03")
    porta = _porta(cen)
    for ident in ("5f0000000000000000000b01", str(outro["id"]), str(velha["id"])):          # o 1º não existe (404)
        corpo = corpo_de(ident=ident)
        assert porta.receber(corpo, assinar(corpo)).status == 200
    await cen.relogio.dormir(0)
    antigo = cen.repo.avisos_pendentes()[-1]
    cen.db.execute("UPDATE canal_entradas SET recebida_em='2026-10-03T09:00:00.000Z' WHERE id=?", (antigo["id"],))
    await cen.leitor.uma_volta(so_avisos=True)
    estados = {str(r["id_externo"]): (r["estado"], r["texto"]) for r in cen.db.query(
        "SELECT id_externo, estado, texto FROM canal_entradas WHERE canal='trello'")}
    assert all(v == ("ignorada", None) for v in estados.values()) and len(estados) >= 3
    assert cen.acoes_da_central() == [] and cen.trello.escritas() == []


async def test_acordar_so_trata_avisos_e_nao_varre_os_quadros(tmp_path: Path) -> None:
    cen = Cenario(tmp_path)
    await cen.sobe()
    antes = len(cen.trello.pedidos)
    assert await cen.leitor.uma_volta(so_avisos=True) == 0
    assert len(cen.trello.pedidos) == antes                              # nenhum GET: nada anotado, nada a reler
    cen.leitor.acordar()
    assert cen.leitor._acordado.is_set()                                 # noqa: SLF001


# ---------------------------------------------------------------------------------------------- o cadastro
class TrelloDosWebhooks:
    def __init__(self) -> None:
        self.pedidos: list[httpx.Request] = []
        self.webhooks: list[dict[str, object]] = []
        self.falhar_criar: int | None = None

    def __call__(self, pedido: httpx.Request) -> httpx.Response:
        self.pedidos.append(pedido)
        caminho = pedido.url.path
        if pedido.method == "GET" and caminho == "/1/members/me/tokens":
            return httpx.Response(200, json=[{"id": "tok1", "identifier": "x", "webhooks": self.webhooks}, {"id": "tok2"}])
        if pedido.method == "POST" and caminho == "/1/webhooks":
            if self.falhar_criar:
                return httpx.Response(self.falhar_criar, text="falha")
            corpo = json.loads(pedido.content)
            self.webhooks.append({"id": f"wh{len(self.webhooks) + 1}", "description": corpo["description"],
                                  "idModel": corpo["idModel"], "callbackURL": corpo["callbackURL"], "active": True})
            return httpx.Response(200, json={"id": "novo"})
        if pedido.method == "DELETE" and caminho.startswith("/1/webhooks/"):
            ident = caminho.split("/")[3]
            self.webhooks = [w for w in self.webhooks if w["id"] != ident]
            return httpx.Response(200, json={})
        return httpx.Response(404, text="?")

    def escritas(self) -> list[tuple[str, str]]:
        return [(p.method, p.url.path) for p in self.pedidos if p.method != "GET"]


def _wh(cfg: Config, falso: TrelloDosWebhooks) -> tuple[CadastroDoWebhook, ClienteTrello]:
    cfg.file.trello.enabled = True
    cfg.file.trello.quadros = [QUADRO]
    cfg.env.trello_api_key, cfg.env.trello_token = SecretStr(CHAVE), SecretStr(TOKEN)
    _ligar(cfg)
    return CadastroDoWebhook(cfg), ClienteTrello(CHAVE, TOKEN, client=httpx.AsyncClient(transport=httpx.MockTransport(falso)))


def _config(tmp_path: Path) -> Config:
    from .conftest import make_config
    cfg = make_config(tmp_path)
    cfg.ensure_dirs()
    return cfg


async def test_cadastro_cria_o_que_falta_recria_o_inativo_e_nao_toca_o_de_outro(tmp_path: Path) -> None:
    falso = TrelloDosWebhooks()
    cad, cliente = _wh(_config(tmp_path), falso)
    alheio = {"id": "alheio", "description": "outro-sistema", "idModel": QUADRO, "callbackURL": "https://x.test/a", "active": True}
    falso.webhooks.append(alheio)
    assert [(p.acao, p.quadro) for p in await cad.planejar(cliente)] == [("criar", QUADRO)]
    assert falso.escritas() == []                                        # planejar (o ensaio) não escreve
    await cad.reconciliar(cliente)
    assert falso.escritas() == [("POST", "/1/webhooks")]
    assert [p.acao for p in await cad.planejar(cliente)] == ["manter"]
    # O Trello desativou o webhook: recria. Um webhook alheio nunca é tocado.
    nosso = next(w for w in falso.webhooks if str(w["description"]).startswith(DESCRICAO))
    nosso["active"] = False
    await cad.reconciliar(cliente)
    assert falso.escritas()[1:] == [("DELETE", f"/1/webhooks/{nosso['id']}"), ("POST", "/1/webhooks")]
    assert alheio in falso.webhooks and cad.problemas() == []
    assert all(TOKEN not in str(p.url) and CHAVE not in str(p.url) for p in falso.pedidos)       # o token só no cabeçalho
    posts = [json.loads(p.content) for p in falso.pedidos if p.method == "POST"]
    assert all(b["callbackURL"] == CALLBACK and b["description"] == DESCRICAO + QUADRO for b in posts)
    assert all(TOKEN not in json.dumps(b) and SEGREDO_APP not in json.dumps(b) for b in posts)


async def test_cadastro_corrige_url_diferente_apaga_duplicado_e_sobra_ao_desligar(tmp_path: Path) -> None:
    falso = TrelloDosWebhooks()
    cfg = _config(tmp_path)
    cad, cliente = _wh(cfg, falso)
    falso.webhooks += [{"id": "a", "description": DESCRICAO + QUADRO, "callbackURL": "https://velha.test/x", "active": True},
                       {"id": "b", "description": DESCRICAO + "quadro-que-saiu", "callbackURL": CALLBACK, "active": True}]
    assert [(p.acao, p.id_webhook) for p in await cad.planejar(cliente)] == [
        ("recriar", "a"), ("apagar", "b")]
    cfg.file.trello.webhook.enabled = False
    falso.webhooks.append({"id": "c", "description": "alheio", "active": True})
    with pytest.raises(ValueError):                                      # desligar a flag NÃO é pedir para apagar
        await cad.planejar(cliente)
    # O pedido explícito (`desligar`) apaga os da Central, e só eles.
    assert sorted(p.id_webhook or "" for p in await cad.planejar(cliente, desligar=True)) == ["a", "b"]


@pytest.mark.parametrize("url", ["http://dev.exemplo.test/x", "https://dev.exemplo.test/x?token=abc", "https://u:p@dev.exemplo.test/x",
                                 "https://dev.exemplo.test/x#a", "https://dev.exemplo.test/ x"])
async def test_cadastro_recusa_url_que_poderia_levar_segredo(tmp_path: Path, url: str) -> None:
    falso = TrelloDosWebhooks()
    cad, cliente = _wh(_config(tmp_path), falso)
    cad.cfg.file.trello.webhook.cadastro_automatico = True           # a saúde do cadastro só fala com o recadastro ligado
    cad.cfg.file.trello.webhook.callback_url = url
    await cad.reconciliar(cliente)
    assert falso.escritas() == [] and [p.code for p in cad.problemas()] == ["trello_webhook_inativo"]


async def test_cadastro_que_falha_vira_problema_e_o_401_sobe_para_o_leitor(tmp_path: Path) -> None:
    falso = TrelloDosWebhooks()
    cad, cliente = _wh(_config(tmp_path), falso)
    cad.cfg.file.trello.webhook.cadastro_automatico = True
    falso.falhar_criar = 400                                             # o HEAD do Trello na URL falhou
    await cad.reconciliar(cliente)
    [p] = cad.problemas()
    assert p.code == "trello_webhook_inativo" and TOKEN not in p.message + p.hint
    falso.falhar_criar = None
    await cad.reconciliar(cliente)
    assert cad.problemas() == []
    falso.falhar_criar = 401
    falso.webhooks.clear()
    with pytest.raises(Exception) as exc:
        await cad.reconciliar(cliente)
    assert getattr(exc.value, "status", None) == 401


async def test_o_lider_so_recadastra_com_a_chave_propria_uma_vez_por_hora(tmp_path: Path) -> None:
    cen = Cenario(tmp_path)
    falso = TrelloDosWebhooks()
    cad, cliente = _wh(cen.cfg, falso)
    cen.leitor._cadastro, cen.leitor._cliente = cad, cliente         # noqa: SLF001 - o Trello falso dos webhooks
    # `enabled` ligado, `cadastro_automatico` desligado (o padrão): o líder NUNCA chama /1/webhooks, nem para ler.
    assert cen.cfg.file.trello.webhook.enabled and cen.cfg.file.trello.webhook.cadastro_automatico is False
    for _ in range(3):
        await cen.leitor._cadastro_se_devido()                       # noqa: SLF001
    cen.leitor._cadastro_em = None                                   # noqa: SLF001 - passou uma hora
    await cen.leitor._cadastro_se_devido()                           # noqa: SLF001
    assert falso.pedidos == [] and cad.problemas() == []
    assert not [p for p in falso.pedidos if p.method in ("POST", "PUT", "DELETE")]
    # Com a chave própria ligada, cadastra uma vez por hora.
    cen.cfg.file.trello.webhook.cadastro_automatico = True
    await cen.leitor._cadastro_se_devido()                           # noqa: SLF001
    await cen.leitor._cadastro_se_devido()                           # noqa: SLF001
    assert falso.escritas() == [("POST", "/1/webhooks")]
    # Sem `enabled`, nem a chave própria faz o líder agir.
    cen.leitor._cadastro_em = None                                   # noqa: SLF001
    cen.cfg.file.trello.webhook.enabled = False
    await cen.leitor._cadastro_se_devido()                           # noqa: SLF001
    assert falso.escritas() == [("POST", "/1/webhooks")]


async def test_ligar_a_flag_nao_cadastra_a_volta_inteira_do_lider_nunca_escreve_em_webhooks(tmp_path: Path) -> None:
    cen = Cenario(tmp_path)
    falso = TrelloDosWebhooks()
    cad, cliente = _wh(cen.cfg, falso)
    cen.leitor._cadastro, cen.leitor._cliente = cad, cliente         # noqa: SLF001
    await cen.sobe()
    await cen.volta()
    await cen.leitor._cadastro_se_devido()                           # noqa: SLF001
    assert [p for p in falso.pedidos if p.url.path.startswith("/1/webhooks")] == []


# ---------------------------------------------------------------------------------------------- o script
def _script():  # type: ignore[no-untyped-def]
    caminho = Path(__file__).resolve().parents[2] / "scripts" / "trello-webhook.py"
    spec = importlib.util.spec_from_file_location("trello_webhook_script", caminho)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


async def test_script_ensaio_nao_escreve_e_aplicar_cadastra_sem_imprimir_segredo(tmp_path: Path) -> None:
    script, falso = _script(), TrelloDosWebhooks()
    cfg = _config(tmp_path)
    _wh(cfg, falso)
    http = httpx.AsyncClient(transport=httpx.MockTransport(falso))
    saida = io.StringIO()
    assert await script.executar(["--ensaio"], cfg, saida, http) == 0
    assert await script.executar([], cfg, saida, http) == 0              # sem flag = ensaio
    assert falso.escritas() == [] and "criar" in saida.getvalue() and "nada foi escrito" in saida.getvalue()
    assert await script.executar(["--aplicar"], cfg, saida, http) == 0
    assert falso.escritas() == [("POST", "/1/webhooks")]
    assert all(v not in saida.getvalue() for v in (TOKEN, CHAVE, SEGREDO_APP))
    # Flag desligada: --aplicar recusa e não toca em nada (nem apaga o que já existe).
    cfg.file.trello.webhook.enabled = False
    antes = list(falso.webhooks)
    recusa = io.StringIO()
    assert await script.executar(["--aplicar"], cfg, recusa, http) == 2
    assert "enabled é false" in recusa.getvalue() and falso.webhooks == antes
    # Só o pedido explícito remove, e só os da Central.
    falso.webhooks.append({"id": "alheio", "description": "outro", "active": True})
    final = io.StringIO()
    assert await script.executar(["--desligar"], cfg, final, http) == 0
    assert [w["id"] for w in falso.webhooks] == ["alheio"] and "Removido" in final.getvalue()
    cfg.file.trello.webhook.enabled = True
    cfg.env.trello_api_secret = None
    assert await script.executar(["--aplicar"], cfg, io.StringIO(), http) == 2          # sem o segredo do app, não nasce
    cfg.env.trello_token = None
    assert await script.executar(["--ensaio"], cfg, io.StringIO(), http) == 2
