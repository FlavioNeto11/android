"""28.24 (achado a): o script local `.claude/canais/telegram_status.py` grava a mensagem que manda ao dono em `canal_enviadas`
(`origem='ana'`, sem fato), pelo repositório do produto; se a gravação falhar, o envio segue. Prova `simulated`: Bot API falsa
e SQLite; nenhuma mensagem real."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import httpx
import pytest

from app.db import Database
from app.modules.avisos.infrastructure.entrada_sql import EntradasDoCanal

from .conftest import make_config

pytestmark = pytest.mark.asyncio
SCRIPT = Path(__file__).resolve().parents[2] / ".claude" / "canais" / "telegram_status.py"


def _modulo() -> ModuleType:
    spec = importlib.util.spec_from_file_location("telegram_status_sob_teste", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class CanalFalso:
    def __init__(self, *_a: object, **_k: object) -> None: ...

    async def _chamar(self, _metodo: str, **_k: object) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 777}})


def _preparar(mod: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    class Env:
        telegram_bot_token = "x"
        telegram_chat_id = "1"

    monkeypatch.setattr(mod, "EnvSettings", Env)
    monkeypatch.setattr(mod, "_segredo", lambda v: str(v))
    monkeypatch.setattr(mod, "CanalTelegram", CanalFalso)


async def test_grava_em_canal_enviadas_com_origem_ana_e_o_reply_e_reconhecido(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = make_config(tmp_path)
    cfg.ensure_dirs()
    db = Database(cfg.db_dsn)
    db.migrate()
    mod = _modulo()
    _preparar(mod, monkeypatch)
    monkeypatch.setattr(mod, "_abrir_repositorio_do_produto", lambda: EntradasDoCanal(db, canal="telegram"))
    assert await mod._enviar("<b>oi</b>", None) == 0
    linha = EntradasDoCanal(db, canal="telegram").enviada("777")
    assert linha is not None and linha["origem"] == "ana" and linha["fato"] is None
    # contraprova: sem a gravação, a resposta do dono não acharia a mensagem
    assert EntradasDoCanal(db, canal="telegram").enviada("778") is None


async def test_falha_na_gravacao_nao_falha_o_envio(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    mod = _modulo()
    _preparar(mod, monkeypatch)

    def quebra() -> object:
        raise RuntimeError("banco fora do ar")

    monkeypatch.setattr(mod, "_abrir_repositorio_do_produto", quebra)
    assert await mod._enviar("oi", None) == 0                                   # o envio segue
    saida = capsys.readouterr().out
    assert "enviado" in saida and "aviso: a mensagem saiu, mas não foi registrada" in saida and "banco fora do ar" not in saida


async def test_convidado_nao_grava_em_canal_enviadas(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _modulo()
    _preparar(mod, monkeypatch)
    chamadas: list[object] = []
    monkeypatch.setattr(mod, "_chat_de_convidado", lambda _c: True)
    monkeypatch.setattr(mod, "_nomes_proibidos", lambda: [])
    monkeypatch.setattr(mod, "_historico_de_saida", lambda *a: None)
    monkeypatch.setattr(mod, "_gravar_enviada", lambda mid, *a: chamadas.append(mid))
    assert await mod._enviar("oi", None, chat="999") == 0 and chamadas == []
