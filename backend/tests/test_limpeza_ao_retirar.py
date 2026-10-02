"""Limpeza automática do app ao retirar conta bloqueada (item 29.27, emenda do ADR-068; decisão do dono de 02/10).

Conta do app âncora bloqueada e retirada leva os dados do app (`pm clear` do pacote que o `app.yaml` declara, e só ele)
dos aparelhos onde estava logada, com captura de tela antes e depois, e resolve a quarentena com a nota da regra.

Nível de prova: `simulated` (Harness com AppState de verdade, aparelho falso: `Adb.clear_data` e a captura de tela são
gravadores, e o pedido de energia é um dublê que vira o estado do aparelho). Nada real: nenhum aparelho, nenhum adb.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from app.commands.limpeza_ao_retirar import NOTA, QUEM
from app.devices.adb import Adb, AdbError
from app.models import InstanceState, SessionStatus
from app.social.contas_nossas import MARCADOR
from app.util import now_iso

from .conftest import Harness
from .fake_device import PNG
from .fake_instagram import PKG as IG_PKG
from .test_sessao_por_conta import IID, correio_registrado, estado, persona_com_duas_contas

__all__ = ["correio_registrado"]

ARROBA = "ana.ancora"


class Gravador:
    """O aparelho falso desta suíte: o que o `pm clear`, a captura e o pedido de energia fizeram, em ordem."""

    def __init__(self) -> None:
        self.ordem: list[str] = []
        self.pacotes: list[str] = []
        self.energia: list[tuple[str, bool]] = []
        self.falha_no_pm: bool = False
        self.falha_na_captura_antes: bool = False


def _armar(s: Any, mp: pytest.MonkeyPatch, *, partida: InstanceState = InstanceState.online) -> Gravador:
    """Troca os efeitos do aparelho por gravadores, sem pausa entre os passos de espera."""
    g = Gravador()
    rt = s.devices.devices[IID]

    def clear_data(pacote: str) -> None:
        g.ordem.append(f"pm:{pacote}")
        g.pacotes.append(pacote)
        if g.falha_no_pm:
            raise AdbError("pm clear falhou")

    def captura() -> bytes:
        if g.falha_na_captura_antes and "captura" not in g.ordem:
            g.ordem.append("captura:falhou")
            raise AdbError("screencap falhou")
        g.ordem.append("captura")
        return PNG

    mp.setattr(rt.adb, "clear_data", clear_data)
    mp.setattr(rt.io, "screenshot_png", captura)
    limpeza = s.limpeza_ao_retirar

    async def sem_pausa(_s: float) -> None:
        await asyncio.sleep(0)

    mp.setattr(limpeza, "_dormir", sem_pausa)

    async def pedir_energia(r: Any, verbo: str, *, confirmar_quarentena: bool) -> None:
        g.energia.append((verbo, confirmar_quarentena))
        r.state = InstanceState.online if verbo in ("wake", "start") else partida

    mp.setattr(limpeza, "_pedir_energia", pedir_energia)
    rt.state = partida
    return g


def _cenario(s: Any, *, marcador: bool = True) -> tuple[str, str]:
    """Persona com a âncora logada e (opcional) a quarentena aberta no aparelho. Origem `observado` e sem a atividade de
    desafio: o gatilho NÃO retira sozinho, a retirada é o que o teste pede."""
    pid, ancora, _ = persona_com_duas_contas(s)
    s.social_repo.set_account_session(pid, ancora, IID, status=SessionStatus.session_ready, verified_at=now_iso())
    if marcador:
        s.social_repo.marcar_conta_travada(IID, ARROBA, "tela de verificação", "observado", profile_id=pid)
    return pid, ancora


async def _esperar_a_limpeza(s: Any) -> None:
    tarefas = [t for t in s._bg if t.get_name().startswith("limpeza-")]
    if tarefas:
        await asyncio.wait_for(asyncio.gather(*tarefas), timeout=30)


def _eventos(s: Any) -> list[dict[str, Any]]:
    return [{"level": r["level"], "message": r["message"], "data": json.loads(r["data"] or "{}")}
            for r in s.db.query("SELECT level, message, data FROM events WHERE kind='device.account_cleanup' ORDER BY id")]


def _marcadores(s: Any) -> list[Any]:
    return s.db.query("SELECT * FROM device_locked_accounts ORDER BY id")


async def test_limpa_so_o_pacote_declarado_com_as_capturas_e_resolve_a_quarentena(
        harness: Harness, correio_registrado: None, monkeypatch: pytest.MonkeyPatch) -> None:
    s = estado(harness)
    g = _armar(s, monkeypatch)
    pid, ancora = _cenario(s)
    # O elo real (não um mock): o AppState liga o gancho do serviço social à tarefa de fundo.
    assert s.social.ao_limpar_aparelhos == s.limpeza_ao_retirar.agendar

    res = s.social.retirar_conta_bloqueada(pid, ancora, origem="declarado", autor="dono")
    assert res["retirada"] is True and res["limpeza_dos_aparelhos"] == {"agendada": True, "aparelhos": 1}
    await _esperar_a_limpeza(s)

    # captura antes, pm clear do pacote declarado (e SÓ dele), captura depois: nenhum toque, nenhum outro pacote
    assert g.ordem == ["captura", f"pm:{IG_PKG}", "captura"] and g.pacotes == [IG_PKG]
    assert g.energia == []                                              # já estava online: a energia não é tocada
    [marcador] = _marcadores(s)
    assert marcador["resolved_at"] is not None and marcador["resolved_by"] == QUEM and marcador["resolution"] == NOTA
    assert marcador["handle"] == MARCADOR                               # mascarado na retirada (29.24)
    [ev] = _eventos(s)
    assert ev["level"] == "warn" and ev["data"]["resultado"] == "concluida" and ev["data"]["package"] == IG_PKG
    assert ev["data"]["instance_id"] == IID and ev["data"]["resolvidos"] == 1
    for chave in (ev["data"]["antes"], ev["data"]["depois"]):
        assert s.storage.exists(chave) and chave.startswith(f"limpeza-de-conta/{IID}/")
    # o comando que apagou os dados está no histórico do aparelho, com id e desfecho
    [cmd] = s.db.query("SELECT verb, state, requested_by FROM commands WHERE instance_id=? AND verb='session.logout'",
                       (IID,))
    assert cmd["state"] == "succeeded" and cmd["requested_by"] == QUEM
    # nem o evento, nem o log, nem o comando carregam o @ da conta retirada
    tudo = json.dumps(_eventos(s)) + json.dumps([dict(r) for r in s.db.query("SELECT * FROM commands")], default=str)
    assert ARROBA not in tudo


async def test_aparelho_hibernado_acorda_com_a_confirmacao_do_sistema_e_volta_a_hibernar(
        harness: Harness, correio_registrado: None, monkeypatch: pytest.MonkeyPatch) -> None:
    s = estado(harness)
    g = _armar(s, monkeypatch, partida=InstanceState.hibernated)
    pid, ancora = _cenario(s)
    s.social.retirar_conta_bloqueada(pid, ancora, origem="declarado", autor="dono")
    await _esperar_a_limpeza(s)
    assert g.energia == [("wake", True), ("hibernate", False)]          # só o acordar leva a confirmação da quarentena
    assert g.ordem == ["captura", f"pm:{IG_PKG}", "captura"]
    assert _marcadores(s)[0]["resolved_by"] == QUEM
    assert _eventos(s)[0]["data"]["energia"] == "restaurada"
    assert s.devices.devices[IID].state == InstanceState.hibernated


async def test_aparelho_parado_liga_e_volta_a_parar(
        harness: Harness, correio_registrado: None, monkeypatch: pytest.MonkeyPatch) -> None:
    s = estado(harness)
    g = _armar(s, monkeypatch, partida=InstanceState.stopped)
    pid, ancora = _cenario(s)
    s.social.retirar_conta_bloqueada(pid, ancora, origem="declarado", autor="dono")
    await _esperar_a_limpeza(s)
    assert g.energia == [("start", True), ("stop", False)] and g.pacotes == [IG_PKG]


async def test_pacote_sem_a_chave_declarada_nao_limpa_nada(
        harness: Harness, correio_registrado: None, monkeypatch: pytest.MonkeyPatch) -> None:
    s = estado(harness)
    g = _armar(s, monkeypatch)
    pid, ancora, correio = persona_com_duas_contas(s)
    s.social_repo.marcar_conta_travada(IID, "ana.correio", "tela de verificação", "observado", profile_id=pid,
                                       app_id="correio")
    res = s.social.retirar_conta_bloqueada(pid, correio, origem="declarado", autor="dono")
    assert res["retirada"] is True and res["limpeza_dos_aparelhos"] == {"agendada": False, "aparelhos": 0}
    await _esperar_a_limpeza(s)
    assert g.ordem == [] and g.energia == [] and _eventos(s) == []
    assert [m["resolved_at"] for m in _marcadores(s)] == [None]         # a quarentena do correio segue para a pessoa


async def test_falha_no_pm_clear_deixa_a_quarentena_aberta_avisa_e_nao_repete(
        harness: Harness, correio_registrado: None, monkeypatch: pytest.MonkeyPatch) -> None:
    s = estado(harness)
    g = _armar(s, monkeypatch, partida=InstanceState.hibernated)
    g.falha_no_pm = True
    pid, ancora = _cenario(s)
    s.social.retirar_conta_bloqueada(pid, ancora, origem="declarado", autor="dono")
    await _esperar_a_limpeza(s)
    assert g.pacotes == [IG_PKG]                                        # UMA tentativa, sem laço
    assert [m["resolved_at"] for m in _marcadores(s)] == [None]
    [ev] = _eventos(s)
    assert ev["level"] == "error" and ev["data"]["resultado"] == "falhou" and ev["data"]["passo"] == "limpar"
    assert ARROBA not in json.dumps(ev)
    assert g.energia == [("wake", True), ("hibernate", False)]          # a energia volta mesmo com a falha
    [cmd] = s.db.query("SELECT state FROM commands WHERE instance_id=? AND verb='session.logout'", (IID,))
    assert cmd["state"] == "failed"
    # repetir a retirada não repete a limpeza: a conta já não existe
    assert s.social.retirar_conta_bloqueada(pid, ancora, origem="declarado", autor="dono")["retirada"] is False
    await _esperar_a_limpeza(s)
    assert g.pacotes == [IG_PKG]


async def test_falha_na_captura_de_antes_nao_apaga_nada(
        harness: Harness, correio_registrado: None, monkeypatch: pytest.MonkeyPatch) -> None:
    s = estado(harness)
    g = _armar(s, monkeypatch)
    g.falha_na_captura_antes = True
    pid, ancora = _cenario(s)
    s.social.retirar_conta_bloqueada(pid, ancora, origem="declarado", autor="dono")
    await _esperar_a_limpeza(s)
    assert g.pacotes == [] and [m["resolved_at"] for m in _marcadores(s)] == [None]
    assert _eventos(s)[0]["data"]["passo"] == "captura_antes"


async def test_aparelho_so_com_vinculo_e_limpo_sem_resolver_marcador_nenhum(
        harness: Harness, correio_registrado: None, monkeypatch: pytest.MonkeyPatch) -> None:
    s = estado(harness)
    g = _armar(s, monkeypatch)
    pid, ancora = _cenario(s, marcador=False)
    s.social.retirar_conta_bloqueada(pid, ancora, origem="declarado", autor="dono")
    await _esperar_a_limpeza(s)
    assert g.pacotes == [IG_PKG] and _marcadores(s) == []
    assert _eventos(s)[0]["data"]["resolvidos"] == 0


async def test_quarentena_ja_resolvida_por_uma_pessoa_nao_repete_a_limpeza(
        harness: Harness, correio_registrado: None, monkeypatch: pytest.MonkeyPatch) -> None:
    s = estado(harness)
    g = _armar(s, monkeypatch)
    pid, ancora = _cenario(s)
    s.social.retirar_conta_bloqueada(pid, ancora, origem="declarado", autor="dono")
    # a pessoa resolve pela rota do 29.24 antes de a tarefa de fundo ter a vez
    assert s.social_repo.resolver_conta_travada(IID, por="dono", nota="limpei à mão") == 1
    await _esperar_a_limpeza(s)
    assert g.ordem == [] and _eventos(s)[0]["data"]["resultado"] == "dispensada"
    assert _marcadores(s)[0]["resolved_by"] == "dono"


async def test_retirada_anterior_ao_deploy_nao_e_limpa_na_subida(
        harness: Harness, correio_registrado: None, monkeypatch: pytest.MonkeyPatch) -> None:
    s = estado(harness)
    s.social.ao_limpar_aparelhos = None                                 # o mundo de antes: retirada sem limpeza
    pid, ancora = _cenario(s)
    s.social.retirar_conta_bloqueada(pid, ancora, origem="declarado", autor="dono")
    chamadas: list[str] = []
    monkeypatch.setattr(Adb, "clear_data", lambda self, pacote: chamadas.append(pacote))
    await s.stop()
    novo = await harness.boot()                                         # a subida: nada de retroativo
    assert novo.social.ao_limpar_aparelhos is not None
    novo.social_repo.mascarar_contas_retiradas()
    novo.social_repo.sincronizar_rotulos()
    await asyncio.sleep(0.2)
    assert chamadas == [] and _eventos(novo) == []
    assert [m["resolved_at"] for m in _marcadores(novo)] == [None]      # segue para a rota manual


async def test_sem_executor_ligado_a_retirada_e_so_banco(
        harness: Harness, correio_registrado: None) -> None:
    s = estado(harness)
    s.social.ao_limpar_aparelhos = None
    pid, ancora = _cenario(s)
    res = s.social.retirar_conta_bloqueada(pid, ancora, origem="declarado", autor="dono")
    assert res["retirada"] is True and res["limpeza_dos_aparelhos"]["agendada"] is False


async def test_agendar_que_falha_vira_evento_de_erro_e_a_retirada_vale(
        harness: Harness, correio_registrado: None) -> None:
    s = estado(harness)

    def quebra(_pedido: Any) -> None:
        raise RuntimeError("sem laço")

    s.social.ao_limpar_aparelhos = quebra
    pid, ancora = _cenario(s)
    res = s.social.retirar_conta_bloqueada(pid, ancora, origem="declarado", autor="dono")
    assert res["retirada"] is True and res["limpeza_dos_aparelhos"]["agendada"] is False
    [ev] = _eventos(s)
    assert ev["level"] == "error" and ev["data"]["resultado"] == "nao_agendada" and ARROBA not in json.dumps(ev)


def test_o_app_yaml_do_app_ancora_declara_a_limpeza_e_o_carregador_recusa_valor_torto(tmp_path: Path) -> None:
    from app.integrations.app_declarado.pacote import PacoteInvalido, definicao_de_dados
    from app.planning.catalog import capabilities_of

    assert capabilities_of(IG_PKG).clear_on_account_retire is True
    base = {"app": "com.exemplo.app", "nome": "Exemplo"}
    assert definicao_de_dados(base, "x").clear_on_account_retire is False          # padrão: não limpa nada
    assert definicao_de_dados({**base, "limpar_ao_retirar": True}, "x").clear_on_account_retire is True
    with pytest.raises(PacoteInvalido):
        definicao_de_dados({**base, "limpar_ao_retirar": "sim"}, "x")
