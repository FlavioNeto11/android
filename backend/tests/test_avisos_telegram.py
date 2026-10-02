"""Item 28.11 — o canal Telegram e a descoberta do chat_id. Prova `simulated`: `httpx.MockTransport`, nenhuma rede, nenhum
token real (o valor abaixo é inventado e a suíte confere que ele não aparece em NADA que sai do adaptador)."""
from __future__ import annotations

import asyncio
import importlib.util
import io
import logging
import sys
from pathlib import Path

import httpx
import pytest

from app.config import EnvSettings
from app.modules.avisos.adapters.telegram import CanalTelegram, TokenAusente
from app.modules.avisos.application.entrega import FalhaDeEnvio

TOKEN = "123456789:AAFake-token_so-para-teste"
RAIZ = Path(__file__).resolve().parents[2]


def _canal(handler: object, chat_id: str = "42") -> tuple[CanalTelegram, list[httpx.Request]]:
    vistos: list[httpx.Request] = []

    def embrulho(req: httpx.Request) -> httpx.Response:
        vistos.append(req)
        return handler(req)  # type: ignore[operator]

    cliente = httpx.AsyncClient(transport=httpx.MockTransport(embrulho))
    return CanalTelegram(TOKEN, chat_id, client=cliente), vistos


def _ok(_: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})


def _falha(canal: CanalTelegram) -> FalhaDeEnvio:
    with pytest.raises(FalhaDeEnvio) as e:
        asyncio.run(canal.enviar("t", "c", None))
    return e.value


def test_envio_ok_usa_send_message_com_chat_e_texto_e_sem_link_preview() -> None:
    canal, vistos = _canal(_ok)
    asyncio.run(canal.enviar("Central de Aparelhos: x", "Abra a caixa.", "http://p/#/pendencias"))
    (req,) = vistos
    assert req.method == "POST" and req.url.path == f"/bot{TOKEN}/sendMessage" and req.url.host == "api.telegram.org"
    corpo = httpx.Response(200, content=req.content).json()
    assert corpo["chat_id"] == "42" and corpo["disable_web_page_preview"] is True
    assert corpo["text"] == "Central de Aparelhos: x\nAbra a caixa.\nhttp://p/#/pendencias"


def test_429_devolve_a_espera_do_retry_after_do_cabecalho_ou_do_corpo() -> None:
    canal, _ = _canal(lambda r: httpx.Response(429, headers={"Retry-After": "17"},
                                                 json={"ok": False, "description": "Too Many Requests"}))
    f = _falha(canal)
    assert f.espera_s == 17.0 and not f.definitiva and "429" in f.motivo
    canal, _ = _canal(lambda r: httpx.Response(429, json={"ok": False, "description": "flood",
                                                           "parameters": {"retry_after": 5}}))
    assert _falha(canal).espera_s == 5.0
    canal, _ = _canal(lambda r: httpx.Response(429, json={"ok": False}))
    assert _falha(canal).espera_s == 30.0, "429 sem número legível repetiu na hora"


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_recusa_do_telegram_e_definitiva(status: int) -> None:
    canal, _ = _canal(lambda r: httpx.Response(status, json={"ok": False, "description": "Bad Request: chat not found"}))
    f = _falha(canal)
    assert f.definitiva and str(status) in f.motivo and "chat not found" in f.motivo


def test_5xx_e_200_sem_ok_nao_sao_sucesso_nem_definitivos() -> None:
    canal, _ = _canal(lambda r: httpx.Response(502, text="Bad Gateway"))
    assert not _falha(canal).definitiva
    canal, _ = _canal(lambda r: httpx.Response(200, json={"ok": False}))
    _falha(canal)


def test_erro_de_rede_e_de_tempo_nao_vazam_o_token_nem_a_url(caplog: pytest.LogCaptureFixture) -> None:
    def cai(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"falhou em {req.url}", request=req)       # o `str()` desta exceção traz a URL

    def demora(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout(f"timeout em {req.url}", request=req)

    with caplog.at_level(logging.DEBUG):
        for handler in (cai, demora):
            canal, _ = _canal(handler)
            f = _falha(canal)
            assert TOKEN not in f.motivo and "api.telegram.org" not in f.motivo and "/bot" not in f.motivo
            assert TOKEN not in repr(f) and not f.definitiva
            assert f.__cause__ is None and f.__suppress_context__, "a exceção original (com a URL) ficou encadeada"
    assert TOKEN not in caplog.text


def test_descricao_do_telegram_com_o_token_e_limpa() -> None:
    canal, _ = _canal(lambda r: httpx.Response(401, json={"ok": False, "description": f"Unauthorized {TOKEN}\nfim"}))
    f = _falha(canal)
    assert TOKEN not in f.motivo and "\n" not in f.motivo


def test_sem_token_o_canal_nem_nasce_e_a_mensagem_diz_o_que_fazer() -> None:
    with pytest.raises(TokenAusente, match="TELEGRAM_BOT_TOKEN"):
        CanalTelegram("", "42")


# ===================================================================== descoberta do chat_id
def _updates(*itens: dict) -> httpx.Response:
    return httpx.Response(200, json={"ok": True, "result": [{"update_id": i, **it} for i, it in enumerate(itens, 1)]})


def test_descoberta_lista_so_id_tipo_nome_e_usuario_sem_repetir_chat() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return _updates(
            {"message": {"text": "/start", "chat": {"id": 777, "type": "private", "first_name": "Flavio",
                                                    "last_name": "Neto", "username": "flavio"}}},
            {"message": {"text": "segredo da conversa", "chat": {"id": 777, "type": "private", "first_name": "Flavio"}}},
            {"my_chat_member": {"chat": {"id": -100123, "type": "supergroup", "title": "Avisos da Central"}}},
            {"edited_message": "lixo"}, {"callback_query": {}})

    canal, vistos = _canal(handler, chat_id="")
    chats = asyncio.run(canal.descobrir_chats())
    assert [(c.id, c.tipo, c.titulo, c.username) for c in chats] == [
        (777, "private", "Flavio Neto", "flavio"), (-100123, "supergroup", "Avisos da Central", None)]
    (req,) = vistos
    assert req.method == "GET" and req.url.path == f"/bot{TOKEN}/getUpdates"
    assert "offset" not in req.url.params, "offset confirma (descarta) os updates: a descoberta só pode ler"
    assert req.url.params["timeout"] == "0"
    assert "segredo da conversa" not in repr(chats)                             # texto de mensagem nunca é lido


def test_descoberta_sem_chat_diz_vazio_e_erro_nao_vaza_o_token() -> None:
    canal, _ = _canal(lambda r: httpx.Response(200, json={"ok": True, "result": []}))
    assert asyncio.run(canal.descobrir_chats()) == []
    canal, _ = _canal(lambda r: httpx.Response(401, json={"ok": False, "description": "Unauthorized"}))
    with pytest.raises(FalhaDeEnvio) as e:
        asyncio.run(canal.descobrir_chats())
    assert TOKEN not in e.value.motivo and "401" in e.value.motivo


# ===================================================================== o script scripts/avisos-telegram.py
def _script():
    spec = importlib.util.spec_from_file_location("avisos_telegram", RAIZ / "scripts" / "avisos-telegram.py")
    mod = importlib.util.module_from_spec(spec)                                  # type: ignore[arg-type]
    sys.modules["avisos_telegram"] = mod
    spec.loader.exec_module(mod)                                                  # type: ignore[union-attr]
    return mod


def _env(token: str | None, chat: str | None) -> EnvSettings:
    return EnvSettings.model_construct(telegram_bot_token=None if token is None else _segredo(token),
                                       telegram_chat_id=None if chat is None else _segredo(chat))


def _segredo(v: str):
    from pydantic import SecretStr
    return SecretStr(v)


def _rodar(comando: str, env: EnvSettings, handler: object | None = None) -> tuple[int, str]:
    mod = _script()
    cliente = httpx.AsyncClient(transport=httpx.MockTransport(handler or _ok))  # type: ignore[arg-type]
    out = io.StringIO()
    codigo = asyncio.run(mod.executar([comando], env, out, cliente))
    return codigo, out.getvalue()


def test_script_descobrir_mostra_os_chats_e_nunca_o_token() -> None:
    codigo, saida = _rodar("descobrir", _env(TOKEN, None), lambda r: _updates(
        {"message": {"chat": {"id": 777, "type": "private", "first_name": "Flavio", "username": "flavio"}}}))
    assert codigo == 0 and "id=777" in saida and "@flavio" in saida and TOKEN not in saida


def test_script_sem_token_da_erro_claro_e_nunca_chama_a_rede() -> None:
    chamado: list[int] = []
    codigo, saida = _rodar("descobrir", _env(None, None), lambda r: chamado.append(1) or _ok(r))
    assert codigo == 2 and "TELEGRAM_BOT_TOKEN" in saida and not chamado


def test_script_testar_exige_chat_id_e_envia_uma_mensagem() -> None:
    codigo, saida = _rodar("testar", _env(TOKEN, None))
    assert codigo == 2 and "TELEGRAM_CHAT_ID" in saida
    vistos: list[httpx.Request] = []
    codigo, saida = _rodar("testar", _env(TOKEN, "42"), lambda r: vistos.append(r) or _ok(r))
    assert codigo == 0 and len(vistos) == 1 and TOKEN not in saida


def test_script_erro_do_telegram_sai_sem_o_token() -> None:
    codigo, saida = _rodar("descobrir", _env(TOKEN, None),
                           lambda r: httpx.Response(401, json={"ok": False, "description": f"Unauthorized {TOKEN}"}))
    assert codigo == 1 and TOKEN not in saida
