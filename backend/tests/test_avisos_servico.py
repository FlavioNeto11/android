"""Item 28.11 — o serviço do aviso fora do painel: barramento → fila → canal, com líder, dedupe e saúde.

Prova `simulated`: canal falso e `httpx.MockTransport`, nenhuma rede. Dois backends são dois `Database` no mesmo banco.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from app.config import Config
from app.db import Database
from app.events import EventBus
from app.modules.avisos.adapters.telegram import CanalTelegram
from app.modules.avisos.application.entrega import FalhaDeEnvio
from app.modules.avisos.infrastructure.fila_sql import FilaDeAvisos
from app.modules.avisos.infrastructure.servico import ServicoDeAvisos
from app.taskqueue.travas import AVISOS, Lideranca
from app.util import now

from .conftest import make_config

AQUI, LAH = "servidor-a", "servidor-b"
TOKEN = "987654321:AAFake-outro_token-de-teste"


class Relogio:
    def __init__(self) -> None:
        self.t = now()

    def __call__(self) -> datetime:
        return self.t

    def avancar(self, s: float) -> None:
        self.t += timedelta(seconds=s)


class CanalFalso:
    def __init__(self, *falhas: FalhaDeEnvio) -> None:
        self.falhas = list(falhas)
        self.enviados: list[tuple[str, str, str | None]] = []

    async def enviar(self, titulo: str, corpo: str, link: str | None) -> None:
        if self.falhas:
            raise self.falhas.pop(0)
        self.enviados.append((titulo, corpo, link))


def _cfg(tmp_path: Path, *, ligado: bool = True, segredos: bool = True) -> Config:
    cfg = make_config(tmp_path)
    cfg.ensure_dirs()
    cfg.file.avisos.enabled = ligado
    cfg.file.avisos.url_painel = "http://painel.local:8000"
    cfg.env.telegram_bot_token = SecretStr(TOKEN) if segredos else None
    cfg.env.telegram_chat_id = SecretStr("42") if segredos else None
    return cfg


def _backend(cfg: Config, dono: str, r: Relogio, *, canal: CanalFalso | None = None,
             db: Database | None = None) -> tuple[ServicoDeAvisos, Database, Lideranca]:
    banco = db or Database(cfg.db_dsn)
    banco.migrate()
    lid = Lideranca(banco, dono=dono, relogio=r)
    servico = ServicoDeAvisos(cfg, EventBus(banco, origin=dono), FilaDeAvisos(banco, relogio=r), lid, canal=canal)
    return servico, banco, lid


def _pendencia(servico: ServicoDeAvisos, run_id: str = "r1") -> bool:
    return servico.enfileirar_evento("run.updated", {"run": {"id": run_id, "status": "needs_input"}}, 10)


def _volta(servico: ServicoDeAvisos) -> object:
    return asyncio.run(servico.entregar_uma_vez())


def test_ligado_o_evento_de_pendencia_sai_uma_vez_com_o_link_da_caixa(tmp_path: Path) -> None:
    r, canal = Relogio(), CanalFalso()
    servico, banco, _ = _backend(_cfg(tmp_path), AQUI, r, canal=canal)
    assert _pendencia(servico) is True
    assert _pendencia(servico) is False, "o mesmo fato enfileirou duas vezes"
    _volta(servico)
    _volta(servico)
    assert canal.enviados == [("Central de Aparelhos: Uma execução parou pedindo informação",
                               "Abra a caixa de Pendências do painel para ver.", "http://painel.local:8000/#/pendencias")]
    assert banco.one("SELECT estado, tentativas FROM avisos_entregas")["estado"] == "enviado"


def test_desligado_nao_enfileira_nem_envia(tmp_path: Path) -> None:
    r, canal = Relogio(), CanalFalso()
    servico, banco, _ = _backend(_cfg(tmp_path, ligado=False), AQUI, r, canal=canal)
    assert _pendencia(servico) is False
    assert _volta(servico) is None
    assert banco.scalar("SELECT COUNT(*) FROM avisos_entregas") == 0 and canal.enviados == []
    assert servico.problemas() == [], "desligado não é problema de saúde"


def test_so_o_lider_envia_e_o_seguidor_assume_quando_o_lider_cai(tmp_path: Path) -> None:
    r = Relogio()
    cfg = _cfg(tmp_path)
    ca, cb = CanalFalso(), CanalFalso()
    a, banco_a, lid_a = _backend(cfg, AQUI, r, canal=ca)
    b, banco_b, lid_b = _backend(cfg, LAH, r, canal=cb, db=Database(cfg.db_dsn))
    assert lid_a.tomar(AVISOS) == 1
    # os dois viram o mesmo evento (a réplica B recebe o que A publicou): uma linha só
    assert _pendencia(a) is True and _pendencia(b) is False
    assert _volta(b) is None and cb.enviados == [], "o seguidor enviou"
    _volta(a)
    assert len(ca.enviados) == 1 and cb.enviados == []
    # A cai: sem renovar, o mandato vence e B assume — e o aviso já enviado NÃO sai de novo
    r.avancar(200)
    _volta(b)
    assert len(ca.enviados) == 1 and cb.enviados == []
    _pendencia(b, "r2")
    _volta(b)
    assert len(cb.enviados) == 1


def test_segredo_ausente_com_o_canal_ligado_vira_problema_e_nao_enfileira(tmp_path: Path) -> None:
    servico, banco, _ = _backend(_cfg(tmp_path, segredos=False), AQUI, Relogio())
    (p,) = servico.problemas()
    assert p.code == "avisos_sem_segredo" and "TELEGRAM_BOT_TOKEN" in p.message and "TELEGRAM_CHAT_ID" in p.message
    assert TOKEN not in p.message + p.hint
    assert _pendencia(servico) is False and _volta(servico) is None
    assert banco.scalar("SELECT COUNT(*) FROM avisos_entregas") == 0


def test_so_falta_o_chat_id_e_o_problema_diz_so_ele(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    cfg.env.telegram_chat_id = None
    (p,) = _backend(cfg, AQUI, Relogio())[0].problemas()
    assert "TELEGRAM_CHAT_ID" in p.message and "TELEGRAM_BOT_TOKEN" not in p.message


def test_com_os_dois_segredos_nao_ha_problema(tmp_path: Path) -> None:
    assert _backend(_cfg(tmp_path), AQUI, Relogio())[0].problemas() == []


def test_429_do_canal_pausa_o_laco_e_a_linha_continua_pendente(tmp_path: Path) -> None:
    r = Relogio()
    canal = CanalFalso(FalhaDeEnvio("429", espera_s=300.0))
    servico, banco, _ = _backend(_cfg(tmp_path), AQUI, r, canal=canal)
    _pendencia(servico)
    res = _volta(servico)
    assert res.adiados == 1 and res.esperar_s == 300.0                          # type: ignore[attr-defined]
    assert _volta(servico) is None, "o laço bateu de novo dentro da espera pedida pelo canal"
    assert banco.one("SELECT estado FROM avisos_entregas")["estado"] == "pendente"


def test_o_canal_real_sobre_transporte_falso_nao_deixa_o_token_em_log_nem_no_banco(
        tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if "falha" in req.content.decode() or False:
            raise AssertionError
        raise httpx.ConnectError(f"sem rota para {req.url}", request=req)

    r = Relogio()
    cfg = _cfg(tmp_path)
    servico, banco, _ = _backend(cfg, AQUI, r)
    cliente = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    servico._canal = CanalTelegram(TOKEN, "42", client=cliente)                  # noqa: SLF001
    _pendencia(servico)
    with caplog.at_level(logging.DEBUG):
        res = _volta(servico)
    assert res.adiados == 1                                                      # type: ignore[attr-defined]
    erro = banco.one("SELECT ultimo_erro FROM avisos_entregas")["ultimo_erro"]
    assert TOKEN not in erro and "api.telegram.org" not in erro
    assert TOKEN not in caplog.text
    for tabela_linha in banco.query("SELECT * FROM avisos_entregas"):
        assert TOKEN not in repr(dict(tabela_linha))


def test_conteudo_da_fila_nao_leva_dado_de_persona(tmp_path: Path) -> None:
    servico, banco, _ = _backend(_cfg(tmp_path), AQUI, Relogio(), canal=CanalFalso())
    servico.enfileirar_evento("approval.pending", {"approval": {
        "id": "ap1", "summary": "responder a Maria", "target": "@maria", "content": "meu CPF é 123", "profile_id": "p-lucas"}}, 3)
    servico.enfileirar_evento("session.needs_person", {"active": True, "profile_id": "p-lucas", "instance_id": "android-01",
                                                       "detail": "senha errada da conta lucas.real"}, 4)
    linhas = banco.query("SELECT * FROM avisos_entregas")
    assert len(linhas) == 2
    for linha in linhas:
        texto = repr(dict(linha))
        for proibido in ("Maria", "maria", "CPF", "123", "p-lucas", "android-01", "lucas", "senha"):
            assert proibido not in texto, proibido


def test_laco_enfileira_do_barramento_e_entrega(tmp_path: Path) -> None:
    """O laço de ponta a ponta: um `emit` real no barramento vira mensagem, sem chamar nada à mão."""
    r, canal = Relogio(), CanalFalso()
    cfg = _cfg(tmp_path)
    cfg.file.avisos.intervalo_s = 5
    servico, banco, lid = _backend(cfg, AQUI, r, canal=canal)

    async def cenario() -> None:
        servico.bus.bind_loop(asyncio.get_running_loop())
        tarefa = asyncio.create_task(servico.laco())
        await asyncio.sleep(0.05)
        servico.bus.emit("run.updated", "parou", level="warn", run_id="r9",
                         data={"run": {"id": "r9", "status": "needs_input"}})
        servico.bus.emit("run.updated", "andando", run_id="r9", data={"run": {"id": "r9", "status": "running"}})
        servico.bus.emit("log", "ruído")
        for _ in range(50):
            await asyncio.sleep(0.05)
            if canal.enviados:
                break
        tarefa.cancel()
        with pytest.raises(asyncio.CancelledError):
            await tarefa

    asyncio.run(cenario())
    assert len(canal.enviados) == 1
    assert banco.scalar("SELECT COUNT(*) FROM avisos_entregas") == 1


async def test_a_saude_do_backend_mostra_o_segredo_que_falta(tmp_path: Path) -> None:
    """Pela porta de verdade (`AppState.health`): ligado e sem segredo é Problem; com os dois segredos, não é."""
    from .conftest import Harness

    h = Harness(tmp_path, 1)
    h.cfg.file.avisos.enabled = True
    h.cfg.env.telegram_bot_token = None
    h.cfg.env.telegram_chat_id = None
    s = await h.boot()
    try:
        codigos = {p.code for p in s.health().problems}
        assert "avisos_sem_segredo" in codigos
        h.cfg.env.telegram_bot_token = SecretStr(TOKEN)
        h.cfg.env.telegram_chat_id = SecretStr("42")
        assert "avisos_sem_segredo" not in {p.code for p in s.health().problems}
    finally:
        await s.stop()


@pytest.mark.parametrize("ligado", [False, True])
async def test_trava_de_avisos_so_com_o_aviso_ligado(tmp_path: Path, ligado: bool) -> None:
    """Desligado, o backend não toma nem renova a trava `avisos`: senão seguraria o líder e o backend com o aviso
    ligado nunca enviaria (mesma regra da trava `pedidos`, 28.4)."""
    from app.taskqueue.travas import AVISOS

    from .conftest import Harness

    hh = Harness(tmp_path, 1)
    hh.cfg.file.avisos.enabled = ligado
    await hh.boot()
    try:
        hh.state._manter_travas()
        trava = hh.state.db.one("SELECT dono FROM travas WHERE nome=?", (AVISOS,))
        if ligado:
            assert trava is not None and trava["dono"] is not None
        else:
            assert trava is None or trava["dono"] is None, "desligado: não segura a trava"
    finally:
        await hh.state.stop()


def _run(banco: Database, run_id: str, *, chave: str, prova: str | None = None) -> None:
    banco.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, instance_ids, created_at, prova_fluxo_id)"
                  " VALUES (?,?,?,?,?,?,?,?)", (run_id, chave, "abrir o app", "single", "planning", "[]", now().isoformat(), prova))


def test_execucao_de_prova_nunca_vira_aviso_ao_dono(tmp_path: Path) -> None:
    """30.37: a prova de fluxo é do sistema. Nem a pergunta (`needs_input`) nem a aprovação dela avisam; o mesmo evento
    de uma execução comum avisa. Reconhece pelo campo no evento, pelo banco (`prova_fluxo_id`) e pela chave `validacao:`."""
    servico, banco, _ = _backend(_cfg(tmp_path), AQUI, Relogio(), canal=CanalFalso())
    _run(banco, "rp", chave="k-prova", prova="fluxo-1")
    _run(banco, "rv", chave="validacao:p9")
    _run(banco, "rc", chave="k-comum")
    needs = lambda rid, extra=None: servico.enfileirar_evento(  # noqa: E731
        "run.updated", {"run": {"id": rid, "status": "needs_input", **(extra or {})}}, 1)
    assert needs("rp", {"prova_fluxo_id": "fluxo-1"}) is False, "o campo do evento já a denuncia"
    assert needs("rp") is False, "evento sem o campo: o banco diz que é prova"
    assert needs("rv") is False, "a validação do curador pela chave de idempotência"
    assert servico.enfileirar_evento("approval.pending", {"approval": {"id": "ap-p", "run_id": "rp"}}, 2) is False
    assert servico.enfileirar_evento("approval.pending", {"approval": {"id": "ap-v", "run_id": "rv"}}, 3) is False
    assert banco.scalar("SELECT COUNT(*) FROM avisos_entregas") == 0
    assert needs("rc") is True, "a execução comum continua avisando"
    assert servico.enfileirar_evento("approval.pending", {"approval": {"id": "ap-c", "run_id": "rc"}}, 4) is True
    assert banco.scalar("SELECT COUNT(*) FROM avisos_entregas") == 2


def test_run_summary_expoe_o_fluxo_provado_so_na_execucao_de_prova(tmp_path: Path) -> None:
    """30.37: `RunSummary.prova_fluxo_id` vem de `runs.prova_fluxo_id` (o painel rotula a prova com ele)."""
    from app.taskqueue.repository import Repository
    _, banco, _ = _backend(_cfg(tmp_path), AQUI, Relogio())
    _run(banco, "rp", chave="k-prova", prova="fluxo-1")
    _run(banco, "rc", chave="k-comum")
    repo = Repository(banco, EventBus(banco), tmp_path / "ev")
    assert repo.run_summary(repo.run_row("rp")).prova_fluxo_id == "fluxo-1"
    assert repo.run_summary(repo.run_row("rc")).prova_fluxo_id is None
