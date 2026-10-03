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
from app.models import InstanceState, PersonaCreate, ProfileCreate, SessionStatus
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


# ---------------------------------------------------------------------- travas contra apagar conta VIVA de outra persona
# Revisão adversarial do 29.27: o `pm clear` apaga o app inteiro, e a plataforma só protege o que conhece. A sessão deixou
# de ser pista de aparelho; a hora de executar confere "outra conta" antes de limpar; a energia confere os marcadores.

async def _persona_viva_no_aparelho(s: Any, usuario: str) -> str:
    """Persona B com conta VIVA do app âncora, vinculada e logada em `IID`; devolve o id dela."""
    pid = s.social.create_profile(ProfileCreate(username=usuario, password="Viva#Senha1", instance_id=IID)).id
    s.social_repo.set_account_session(pid, str(s.social_repo.conta_ancora(pid)["id"]), IID,
                                      status=SessionStatus.session_ready, verified_at=now_iso())
    return pid


def _legado_d2a(s: Any, nome: str, usuario: str) -> str:
    """Estado de LEGADO que o 29.29 hoje recusa na porta do serviço: pessoa vinculada SEM app que ganhou a conta do app
    depois, servindo o mesmo app que outra persona no mesmo aparelho. Montado direto no repositório, de propósito, para a
    trava `outra_conta` do 29.27 seguir exercitada contra dado que já exista no banco (anterior ao 29.29)."""
    pid = s.social.create_persona(PersonaCreate(name=nome)).id
    s.social_repo.bind(pid, IID)                        # sem conta ainda: o vínculo sem app não tem app a conferir
    s.social_repo.adopt_account(pid, username=usuario, first_name=None, last_name=None, display_name=None,
                                birth_date=None, email=None)
    s.social_repo.create_account(pid, app_id="instagram", handle=usuario)
    return pid


@pytest.mark.parametrize("status_antigo", [SessionStatus.session_ready, SessionStatus.wrong_account,
                                           SessionStatus.needs_person])
async def test_sessao_velha_de_aparelho_desvinculado_nao_e_pista_e_nao_limpa_conta_viva(
        harness: Harness, correio_registrado: None, monkeypatch: pytest.MonkeyPatch,
        status_antigo: SessionStatus) -> None:
    s = estado(harness)
    g = _armar(s, monkeypatch)
    pid_a, ancora_a, _ = persona_com_duas_contas(s)
    s.social_repo.set_account_session(pid_a, ancora_a, IID, status=status_antigo, verified_at=now_iso())
    s.social_repo.unbind(pid_a, IID)                    # `unbind` não apaga a sessão: ela sobra como pista falsa
    pid_b = await _persona_viva_no_aparelho(s, "bia.viva")
    assert [str(b["profile_id"]) for b in s.social_repo.profiles_of_instance(IID)] == [pid_b]
    res = s.social.retirar_conta_bloqueada(pid_a, ancora_a, origem="declarado", autor="dono")
    # sem marcador nem vínculo, o aparelho nem entra no pedido: sessão não é fonte
    assert res["limpeza_dos_aparelhos"] == {"agendada": False, "aparelhos": 0}
    await _esperar_a_limpeza(s)
    assert g.pacotes == [] and g.ordem == [] and _eventos(s) == []


async def test_vinculo_sem_app_de_pessoa_que_ganhou_conta_depois_nao_limpa_conta_viva_de_outra(
        harness: Harness, correio_registrado: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """O furo antigo da D2-a: pessoa vinculada SEM app ganhou a conta do app depois, e duas personas servem o mesmo app no
    mesmo aparelho. O 29.29 fechou a porta no serviço; o dado de antes dele continua possível, e a limpeza de uma não
    pode apagar a outra."""
    s = estado(harness)
    g = _armar(s, monkeypatch)
    pid_c = await _persona_viva_no_aparelho(s, "caio.vivo")
    pid_a = _legado_d2a(s, "Ana Sem Conta", "ana.depois")
    ancora_a = str(s.social_repo.conta_ancora(pid_a)["id"])
    res = s.social.retirar_conta_bloqueada(pid_a, ancora_a, origem="declarado", autor="dono")
    assert res["limpeza_dos_aparelhos"] == {"agendada": True, "aparelhos": 1}     # o vínculo de A aponta para IID
    await _esperar_a_limpeza(s)
    assert g.pacotes == []                                                         # mas a trava recusa: C é viva ali
    [ev] = _eventos(s)
    assert ev["level"] == "error" and ev["data"]["resultado"] == "falhou" and ev["data"]["passo"] == "outra_conta"
    assert [str(b["profile_id"]) for b in s.social_repo.profiles_of_instance(IID, "instagram")] == [pid_c]
    assert "ana.depois" not in json.dumps(ev) and ARROBA not in json.dumps(ev)


@pytest.mark.parametrize("pista", ["vinculo", "sessao"])
async def test_outra_persona_no_mesmo_app_do_aparelho_recusa_a_limpeza_e_a_quarentena_fica_aberta(
        harness: Harness, correio_registrado: None, monkeypatch: pytest.MonkeyPatch, pista: str) -> None:
    """O aparelho da retirada (com o marcador dela aberto) também serve a OUTRA persona viva. Com a quarentena aberta o
    serviço já recusa vínculo novo ali, então B chega antes: pelo legado da D2-a (vínculo sem app, conta do app depois,
    anterior ao 29.29) ou com só a sessão que o desvínculo deixou."""
    s = estado(harness)
    g = _armar(s, monkeypatch)
    pid, ancora = _cenario(s, marcador=False)
    if pista == "vinculo":
        pid_b = _legado_d2a(s, "Bia Sem Conta", "bia.viva")
    else:
        pid_b = s.social.create_profile(ProfileCreate(username="bia.viva", password="Viva#Senha1")).id
        s.social_repo.set_account_session(pid_b, str(s.social_repo.conta_ancora(pid_b)["id"]), IID,
                                          status=SessionStatus.unknown)    # qualquer status: houve app aberto ali
        assert [str(b["profile_id"]) for b in s.social_repo.profiles_of_instance(IID)] == [pid]
    # a quarentena abre depois, com B já servida ali: o aparelho entra no pedido pelo marcador e pelo vínculo de A
    s.social_repo.marcar_conta_travada(IID, ARROBA, "tela de verificação", "observado", profile_id=pid)
    res = s.social.retirar_conta_bloqueada(pid, ancora, origem="declarado", autor="dono")
    assert res["limpeza_dos_aparelhos"] == {"agendada": True, "aparelhos": 1}
    await _esperar_a_limpeza(s)
    assert g.pacotes == []                                                         # nenhum pm clear
    assert [m["resolved_at"] for m in _marcadores(s)] == [None]                    # quarentena segue aberta
    [ev] = _eventos(s)
    assert ev["level"] == "error" and ev["data"]["resultado"] == "falhou" and ev["data"]["passo"] == "outra_conta"
    assert ev["data"]["resolvidos"] == 0 and ARROBA not in json.dumps(ev) and "bia.viva" not in json.dumps(ev)
    # uma tentativa e fim: esperar de novo não limpa depois
    await _esperar_a_limpeza(s)
    assert g.pacotes == [] and len(_eventos(s)) == 1


async def test_marcador_de_outra_conta_com_aparelho_hibernado_nao_acorda_nem_limpa(
        harness: Harness, correio_registrado: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """Acordar vai com `confirm_locked_account=True`, que passa por cima de QUALQUER marcador do aparelho: com marcador
    aberto de outra conta ali, a limpeza não acorda."""
    s = estado(harness)
    g = _armar(s, monkeypatch, partida=InstanceState.hibernated)
    pid, ancora = _cenario(s)
    assert s.social_repo.marcar_conta_travada(IID, "outra.pessoa", "tela de verificação", "observado") is True
    s.social.retirar_conta_bloqueada(pid, ancora, origem="declarado", autor="dono")
    await _esperar_a_limpeza(s)
    assert g.energia == [] and g.pacotes == [] and g.ordem == []                   # nem acordou, nem capturou
    assert s.devices.devices[IID].state == InstanceState.hibernated
    assert [m["resolved_at"] for m in _marcadores(s)] == [None, None]
    [ev] = _eventos(s)
    assert ev["level"] == "error" and ev["data"]["passo"] == "outra_conta" and ev["data"]["energia"] == "nao_alterada"
    assert "outra.pessoa" not in json.dumps(ev) and ARROBA not in json.dumps(ev)
