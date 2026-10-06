"""Item 32.5 — `GET /api/canais/estado`, o estado dos canais para a tela Canais do painel.

Prova `simulated` (`arquivo::teste`): `AppState` do harness, SQLite, nenhuma chamada ao Telegram nem ao Trello. O que se
trava aqui é o CONTRATO de privacidade: a resposta é uma lista fechada de chaves e nenhum valor carrega conteúdo de aviso,
mensagem, cartão ou segredo, mesmo com o banco cheio deles.
"""
from __future__ import annotations

import json

import httpx
import pytest
from pydantic import SecretStr

from app.config import Config
from app.main import create_app
from app.models import Problem
from app.modules.avisos.infrastructure.estado_sql import (ESTADOS_DA_ENTRADA, ESTADOS_DA_FILA, ESTADOS_DO_CARTAO, MOTIVOS,
                                                          EstadoDosCanais, motivo_da_falha)
from app.modules.avisos.infrastructure.entrada_sql import EntradasDoCanal

from .conftest import Harness

TOKEN_DO_BOT = "123456:ABCsegredoDoBot"            # token de teste, não existe fora daqui
CHAT_DO_DONO = "998877665"
TOKEN_DA_API = "tk-parque-canais-5d1e"

#: Os caminhos permitidos na resposta. `*` é uma chave do vocabulário fechado (estado) daquele ramo.
CAMINHOS = {
    "gerado_em",
    "aviso_telegram", "aviso_telegram.ligado", "aviso_telegram.segredo_presente", "aviso_telegram.fila",
    "aviso_telegram.ultimo_envio_em", "aviso_telegram.ultima_falha", "aviso_telegram.ultima_falha.em",
    "aviso_telegram.ultima_falha.motivo", "aviso_telegram.problemas",
    "conversa_telegram", "conversa_telegram.ligada", "conversa_telegram.ultima_leitura_em",
    "conversa_telegram.entradas", "conversa_telegram.problemas",
    "trello", "trello.ligado", "trello.webhook_ligado", "trello.cadastro_automatico",
    "trello.ultima_reconciliacao_em", "trello.cartoes", "trello.entradas", "trello.problemas",
    "trello.comentarios_de_app_em_alvo_desconhecido",
    *(f"aviso_telegram.fila.{e}" for e in ESTADOS_DA_FILA),
    *(f"conversa_telegram.entradas.{e}" for e in ESTADOS_DA_ENTRADA),
    *(f"trello.entradas.{e}" for e in ESTADOS_DA_ENTRADA),
    *(f"trello.cartoes.{e}" for e in ESTADOS_DO_CARTAO),
}


def _caminhos(no: object, prefixo: str = "") -> set[str]:
    achados: set[str] = set()
    if isinstance(no, dict):
        for chave, valor in no.items():
            caminho = f"{prefixo}.{chave}" if prefixo else str(chave)
            achados.add(caminho)
            achados |= _caminhos(valor, caminho)
    return achados


def _estado(h: Harness) -> dict:
    return EstadoDosCanais(
        h.state.db, h.cfg, problemas_do_aviso=[h.state.avisos.problemas],
        problemas_da_conversa=[h.state.telegram_entrada.problemas],
        problemas_do_trello=[h.state.trello_espelho.problemas, h.state.trello_leitor.problemas]).ler()


def _semear(h: Harness) -> list[str]:
    """Enche as tabelas dos canais de conteúdo que NUNCA pode sair. Devolve os textos plantados."""
    db = h.state.db
    plantados = ["Fulana", "comando secreto", "api.telegram.org", "ABC", "card-secreto-777", "quadro-secreto-888",
                 "lista-secreta-999", "membro-secreto-111", "approval:4242"]
    insere = ("INSERT INTO avisos_entregas(chave, tipo, titulo, corpo, link, canal, estado, tentativas, iniciado_em,"
              " enviado_em, ultimo_erro, criado_em) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)")
    db.execute(insere, ("approval:4242", "approval.pending", "Fulana pediu aprovação", "corpo com Fulana", "https://x/y",
                        "telegram", "enviado", 1, "2026-10-04T10:00:00Z", "2026-10-04T10:00:05Z", None,
                        "2026-10-04T09:59:00Z"))
    db.execute(insere, ("run:1:needs_input", "run.needs_input", "Fulana precisa de você", "corpo", None, "telegram",
                        "pendente", 2, "2026-10-04T11:00:00Z", None,
                        "falha de rede ao falar com o Telegram (ConnectError) https://api.telegram.org/bot123:ABC/sendMessage",
                        "2026-10-04T10:55:00Z"))
    db.execute(insere, ("run:2:needs_input", "run.needs_input", "Fulana de novo", "corpo", None, "telegram",
                        "falhou", 3, "2026-10-04T08:00:00Z", None, "Telegram recusou (401): Unauthorized",
                        "2026-10-04T07:55:00Z"))
    repo = EntradasDoCanal(db, canal="telegram")
    repo.gravar_inicio(None)                                                   # o marco da 1ª subida não conta
    repo.gravar(id_externo="501", ordem=501, tipo="mensagem", do_dono=True, ref_mensagem="9001", responde_a=None,
                texto="comando secreto", tamanho=15)
    repo.gravar(id_externo="502", ordem=502, tipo="mensagem", do_dono=False, ref_mensagem=None, responde_a=None,
                texto=None, tamanho=40, estado="ignorada")
    EntradasDoCanal(db, canal="trello").gravar_aviso("act-membro-secreto-111")
    db.execute("INSERT INTO trello_cartoes(chave, card_id, quadro, lista, hash, estado, criado_em, atualizado_em)"
               " VALUES (?,?,?,?,?,?,?,?)", ("approval:4242", "card-secreto-777", "quadro-secreto-888",
                                              "lista-secreta-999", "h", "ativo", "2026-10-04T10:00:00Z",
                                              "2026-10-04T10:00:00Z"))
    db.execute("INSERT INTO trello_cartoes(chave, card_id, quadro, lista, hash, estado, criado_em, atualizado_em)"
               " VALUES (?,?,?,?,?,?,?,?)", ("run:1", "card-secreto-778", "quadro-secreto-888", "lista-secreta-999",
                                              "h", "arquivado", "2026-10-04T10:00:00Z", "2026-10-04T10:00:00Z"))
    db.execute("INSERT INTO trello_cursor(quadro, ultima_action, ultima_data, atualizado_em) VALUES (?,?,?,?)",
               ("quadro-secreto-888", "membro-secreto-111", "2026-10-04T10:30:00Z", "2026-10-04T10:31:00Z"))
    return plantados


def _ligar(cfg: Config) -> None:
    cfg.file.avisos.enabled = True
    cfg.file.avisos.entrada.enabled = True
    cfg.file.trello.enabled = True
    cfg.file.trello.webhook.enabled = True
    cfg.env.telegram_bot_token = SecretStr(TOKEN_DO_BOT)
    cfg.env.telegram_chat_id = SecretStr(CHAT_DO_DONO)


# ---------------------------------------------------------------- o motivo da falha
@pytest.mark.parametrize("erro, esperado", [
    ("tempo esgotado ao falar com o Telegram", "tempo_esgotado"),
    ("falha de rede ao falar com o Telegram (ConnectError)", "rede"),
    ("Telegram pediu para esperar (429): Too Many Requests: retry after 5", "429"),
    ("Telegram recusou (401): Unauthorized", "401"),
    ("Telegram recusou (400): Bad Request: chat not found", "outro"),
    # A descrição do Telegram vem depois do status e pode ter qualquer número: ela não decide o motivo.
    ("Telegram recusou (400): erro 429 e (401) na descrição", "outro"),
    ("o processo parou durante o envio; pode ter saído, não foi reenviado", "outro"),
    ("venceu: ficou mais de 6 h sem sair", "outro"),
    ("", "outro"),
    (None, "outro"),
])
def test_motivo_da_falha_e_de_lista_fechada(erro: str | None, esperado: str) -> None:
    motivo = motivo_da_falha(erro)
    assert motivo == esperado
    assert motivo in MOTIVOS


# ---------------------------------------------------------------- o estado
async def test_tudo_desligado_e_vazio(harness: Harness) -> None:
    r = _estado(harness)
    assert _caminhos(r) <= CAMINHOS
    assert r["aviso_telegram"]["ligado"] is False
    assert r["aviso_telegram"]["fila"] == dict.fromkeys(ESTADOS_DA_FILA, 0)
    assert r["aviso_telegram"]["ultimo_envio_em"] is None and r["aviso_telegram"]["ultima_falha"] is None
    assert r["conversa_telegram"]["ligada"] is False and r["conversa_telegram"]["ultima_leitura_em"] is None
    assert r["conversa_telegram"]["entradas"] == dict.fromkeys(ESTADOS_DA_ENTRADA, 0)
    assert r["trello"]["ligado"] is False and r["trello"]["webhook_ligado"] is False
    assert r["trello"]["ultima_reconciliacao_em"] is None
    assert r["trello"]["cartoes"] == dict.fromkeys(ESTADOS_DO_CARTAO, 0)
    assert r["aviso_telegram"]["problemas"] == r["conversa_telegram"]["problemas"] == r["trello"]["problemas"] == []


async def test_estado_com_dados(harness: Harness) -> None:
    _ligar(harness.cfg)
    _semear(harness)
    r = _estado(harness)
    assert _caminhos(r) <= CAMINHOS
    aviso = r["aviso_telegram"]
    assert aviso["ligado"] is True and aviso["segredo_presente"] is True
    assert aviso["fila"] == {"pendente": 1, "enviando": 0, "enviado": 1, "falhou": 1, "incerto": 0, "descartado": 0}
    assert aviso["ultimo_envio_em"] == "2026-10-04T10:00:05Z"
    # A falha vigente é a de tentativa mais recente entre as linhas ainda pendentes ou desistidas.
    assert aviso["ultima_falha"] == {"em": "2026-10-04T11:00:00Z", "motivo": "rede"}
    conversa = r["conversa_telegram"]
    assert conversa["ligada"] is True
    assert conversa["entradas"]["recebida"] == 1 and conversa["entradas"]["ignorada"] == 1   # sem o marco `inicio`
    assert conversa["ultima_leitura_em"] is not None
    trello = r["trello"]
    assert trello["ligado"] is True and trello["webhook_ligado"] is True and trello["cadastro_automatico"] is False
    assert trello["cartoes"] == {"ativo": 1, "arquivado": 1, "criando": 0}
    assert trello["ultima_reconciliacao_em"] == "2026-10-04T10:31:00Z"
    assert trello["entradas"]["aviso"] == 1 and trello["entradas"]["recebida"] == 0


async def test_segredo_ausente_aparece_como_booleano_e_como_codigo(harness: Harness) -> None:
    harness.cfg.file.avisos.enabled = True
    harness.cfg.env.telegram_bot_token = None
    harness.cfg.env.telegram_chat_id = None
    harness.state.avisos._canal = None
    aviso = _estado(harness)["aviso_telegram"]
    assert aviso["segredo_presente"] is False
    assert aviso["problemas"] == ["avisos_sem_segredo"]


async def test_estado_desconhecido_soma_em_outro_sem_virar_chave_nova(harness: Harness) -> None:
    db = harness.state.db
    db.execute("INSERT INTO canal_entradas(canal, id_externo, tipo, estado, recebida_em) VALUES (?,?,?,?,?)",
               ("trello", "x1", "outro", "estado-que-ninguem-conhece", "2026-10-04T10:00:00Z"))
    entradas = _estado(harness)["trello"]["entradas"]
    assert entradas["outro"] == 1
    assert set(entradas) == set(ESTADOS_DA_ENTRADA) | {"outro"}


async def test_so_o_codigo_do_problema_sai(harness: Harness) -> None:
    """A mensagem e a dica do `Problem` citam o que falta no `.env`: a tela recebe só o `code`."""
    fonte = lambda: [Problem(code="trello_recusado", message="TRELLO_TOKEN inválido: abc123", hint="troque o TRELLO_TOKEN"),  # noqa: E731
                     Problem(code="trello_recusado", message="de novo", hint="x"),
                     Problem(code="outra_familia", message="não é do Trello", hint="y")]
    r = EstadoDosCanais(harness.state.db, harness.cfg, problemas_do_aviso=[], problemas_da_conversa=[],
                        problemas_do_trello=[fonte]).ler()
    assert r["trello"]["problemas"] == ["trello_recusado"]
    assert "abc123" not in json.dumps(r) and "TRELLO_TOKEN" not in json.dumps(r)


# ---------------------------------------------------------------- a rota
def _app(h: Harness, *, base: str, token: str | None = None, par: tuple[str, int] = ("127.0.0.1", 123)):
    app = create_app(h.cfg, state=h.state)
    app.state.poc = h.state
    cab = {"Authorization": f"Bearer {token}"} if token else {}
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=par), base_url=base, headers=cab)


def _expor(h: Harness) -> None:
    h.cfg.file.server.host = "0.0.0.0"                       # noqa: S104 - o cenário sob teste: acesso de fora
    h.cfg.file.server.public_hosts = ["parque.local"]
    h.cfg.env.api_token = SecretStr(TOKEN_DA_API)


async def test_rota_sem_sessao_responde_401(harness: Harness) -> None:
    _expor(harness)
    async with _app(harness, base="http://parque.local") as c:
        r = await c.get("/api/canais/estado")
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "unauthorized"


async def test_rota_com_credencial_responde_o_estado(harness: Harness) -> None:
    _expor(harness)
    async with _app(harness, base="http://parque.local", token=TOKEN_DA_API) as c:
        r = await c.get("/api/canais/estado")
    assert r.status_code == 200
    assert set(r.json()) == {"gerado_em", "aviso_telegram", "conversa_telegram", "trello"}


async def test_rota_so_le(harness: Harness) -> None:
    """Sem escrita: nenhum método além do GET existe no caminho."""
    async with _app(harness, base="http://localhost") as c:
        for metodo in ("post", "put", "patch", "delete"):
            r = await getattr(c, metodo)("/api/canais/estado")
            assert r.status_code == 405, metodo


async def test_resposta_nao_leva_conteudo_nem_segredo(harness: Harness) -> None:
    """O teste que importa: banco cheio de conteúdo, segredos no ambiente, e a resposta INTEIRA não carrega nada disso."""
    _ligar(harness.cfg)
    plantados = _semear(harness)
    harness.cfg.env.trello_api_key = SecretStr("chave-trello-secreta")
    harness.cfg.env.trello_token = SecretStr("token-trello-secreto")
    async with _app(harness, base="http://localhost") as c:
        r = await c.get("/api/canais/estado")
    assert r.status_code == 200
    corpo = r.json()
    # (a) só as chaves permitidas
    assert _caminhos(corpo) <= CAMINHOS, _caminhos(corpo) - CAMINHOS
    # (b) nenhum valor carrega o que foi plantado, nem os segredos
    texto = json.dumps(corpo, ensure_ascii=False)
    for proibido in (*plantados, TOKEN_DO_BOT, CHAT_DO_DONO, "chave-trello-secreta", "token-trello-secreto", "bot123",
                     "https://", "sendMessage", "Unauthorized"):
        assert proibido not in texto, proibido
    # (c) todo valor textual é hora, código de lista fechada ou nome de chave; nada de frase
    assert corpo["aviso_telegram"]["ultima_falha"]["motivo"] in MOTIVOS
