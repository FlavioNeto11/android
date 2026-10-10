"""31.302: a releitura PERIÓDICA e leve da sessão da conta âncora (observação sem IA, a cada N horas).

Por que existe: a validade da sessão só reverifica antes de uma tarefa; a persona que ninguém usa fica `session_ready`
para sempre, mesmo com o app já mostrando o desafio (a conta bloqueada só é vista por observação, ADR-055/068).

O que estes testes guardam:

* padrão DESLIGADO (`verificacao_periodica_h: 0`): nenhuma releitura, nenhum comando, nenhum evento;
* conta vencida num aparelho livre: UMA releitura `observe_only=True` (nunca autentica), pelo verbo `session.verify` com autor
  `verificacao-periodica`, e o evento `session.verificacao_periodica`;
* cada motivo de pulo (aparelho fora do ar, controle manual, ocupado, quarentena, pausa de reparo, host carregado,
  manutenção do worker, portão da sessão, tentada há pouco) NÃO abre comando algum e o evento diz por quê (uma vez);
* uma conta por volta; conta recém-lida, persona bloqueada e sessão que não é `session_ready` não entram;
* o desafio visto na tela passa pelo motor de sessão de verdade (`session.needs_person`, perfil `blocked`, aparelho em
  quarentena): o laço não tem um segundo caminho para isso (ADR-068).

Prova `simulated`: harness com aparelho falso e o motor de sessão sobre o `FakeInstagram`. `real`: `not_run`.
"""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from app.config import ContasCfg
from app.integrations.app_declarado.conhecimento import do_app
from app.integrations.app_declarado.sessao import AuthResult, Outcome, SessaoDeclarada
from app.models import ControlOwner, InstanceState, ProfileCreate, SessionStatus
from app.modules.identity.application import verificacao_periodica as regras
from app.modules.identity.infrastructure.verificacao_periodica import EVENTO
from app.security.sensitive_input import SensitiveInputChannel
from app.state import AppState
from app.util import now, to_iso

from .conftest import Harness
from .fake_instagram import PKG, FakeInstagram
from .test_instagram_auth import SENHA, USUARIO, FakeDevices, FakeRt

IID = "android-01"


class Espiao:
    """Provedor de sessão que só registra quem pediu o quê — e, se mandado, falha como um driver que caiu."""

    def __init__(self, falha: Exception | None = None) -> None:
        self.falha = falha
        self.chamadas: list[tuple[str, str, bool, bool]] = []

    async def ensure_session(self, rt: Any, profile_id: str, *, account_id: str | None = None,
                             force_login: bool = False, automatic: bool = False, observe_only: bool = False) -> AuthResult:
        self.chamadas.append((rt.id, profile_id, automatic, observe_only))
        if self.falha is not None:
            raise self.falha
        return AuthResult(Outcome.UNCERTAIN, "espião: nada lido")


def _perfil(s: AppState, username: str, instance_id: str = IID, *, horas_atras: float | None = 10) -> tuple[str, str]:
    """Persona ativa, vinculada ao aparelho, com a sessão da conta âncora pronta lida `horas_atras` h atrás."""
    pid = s.social_repo.create_profile(username=username, first_name=None, last_name=None, display_name=None,
                                       birth_date=None, email=None, persona_id=None)
    pid = pid if isinstance(pid, str) else pid["id"]
    s.social_repo.bind(pid, instance_id)
    s.social_repo.set_session(pid, status=SessionStatus.session_ready, instance_id=instance_id,
                              verified_at=to_iso(now() - timedelta(hours=horas_atras)) if horas_atras is not None else None,
                              detail="pronta")
    linha = s.social_repo.session_row(pid, instance_id)
    assert linha is not None
    return pid, str(linha["account_id"])


def _ligar(s: AppState, monkeypatch: pytest.MonkeyPatch, *, horas: int = 6, espiao: Espiao | None = None,
           portao_livre: bool = True, host_folgado: bool = True) -> Espiao:
    """Liga o recurso e põe o espião no lugar do provedor de sessão. O portão da sessão (app instalado) e a CPU do host
    são as duas leituras que o harness não tem: ficam abertas, e cada teste de pulo as fecha de propósito."""
    s.cfg.file.contas.verificacao_periodica_h = horas
    espiao = espiao or Espiao()
    monkeypatch.setattr(s.sessoes, "for_package", lambda _pacote: espiao)
    if portao_livre:
        monkeypatch.setattr(s.verificacao_periodica, "_motivo_do_portao", lambda _alvo, _rt: None)
    if host_folgado:
        monkeypatch.setattr(s.scheduler, "cpu_do_host_acima", lambda _rt, _limiar: None)
    return espiao


def _eventos(s: AppState) -> list[dict[str, Any]]:
    import json
    linhas = s.db.query("SELECT data FROM events WHERE kind=? ORDER BY id", (EVENTO,))
    return [json.loads(r["data"]) for r in linhas]


def _comandos(s: AppState) -> list[Any]:
    return s.db.query("SELECT verb, requested_by, state FROM commands WHERE verb='session.verify'")


async def _terminar(s: AppState, instance_id: str = IID) -> None:
    """Espera o trabalho de aparelho que a volta despachou."""
    tarefa = s.scheduler.workers.get(instance_id)
    if tarefa is not None:
        await tarefa


def _estado(h: Harness) -> AppState:
    assert h.state is not None
    return h.state


# ---------------------------------------------------------------- desligada de fábrica
@pytest.mark.asyncio
async def test_desligada_de_fabrica_nao_toca_em_nada(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    s = _estado(harness)
    _perfil(s, "conta_a")
    espiao = _ligar(s, monkeypatch, horas=0)
    assert ContasCfg().verificacao_periodica_h == 0                    # o padrão do modelo: nada liga sozinho
    assert await s.verificacao_periodica.uma_volta() is None
    assert espiao.chamadas == [] and _comandos(s) == [] and _eventos(s) == []


# ---------------------------------------------------------------- conta vencida, aparelho livre
@pytest.mark.asyncio
async def test_conta_vencida_e_relida_uma_vez_so_observando(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    s = _estado(harness)
    pid, conta = _perfil(s, "conta_a")
    espiao = _ligar(s, monkeypatch)

    alvo = await s.verificacao_periodica.uma_volta()
    assert alvo is not None and (alvo.profile_id, alvo.account_id, alvo.instance_id) == (pid, conta, IID)
    await _terminar(s)

    assert espiao.chamadas == [(IID, pid, False, True)]              # `observe_only`: nunca autentica, nunca digita
    comandos = _comandos(s)
    assert [(c["verb"], c["requested_by"], c["state"]) for c in comandos] == [
        ("session.verify", "verificacao-periodica", "succeeded")]
    eventos = _eventos(s)
    assert len(eventos) == 1
    assert eventos[0]["resultado"] == "verificada"                    # o espião não grava: a sessão segue `session_ready`
    assert {"profile_id", "account_id", "instance_id", "resultado"} <= set(eventos[0])
    # só ids e códigos: nenhum @ nem texto da tela
    assert "conta_a" not in str(eventos[0])


@pytest.mark.asyncio
async def test_a_sessao_que_segue_pronta_gera_evento_verificada(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    s = _estado(harness)
    pid, conta = _perfil(s, "conta_a")

    class Grava(Espiao):
        async def ensure_session(self, rt: Any, profile_id: str, **kw: Any) -> AuthResult:
            await super().ensure_session(rt, profile_id, **kw)
            s.social_repo.set_session(profile_id, status=SessionStatus.session_ready, instance_id=rt.id,
                                      verified_at=to_iso(now()), detail="relida")
            return AuthResult(Outcome.SESSION_READY, "ok")

    _ligar(s, monkeypatch, espiao=Grava())
    assert await s.verificacao_periodica.uma_volta() is not None
    await _terminar(s)
    assert [e["resultado"] for e in _eventos(s)] == ["verificada"]
    # lida agora: a próxima volta não a pega de novo (a janela é de 6 h)
    assert await s.verificacao_periodica.uma_volta() is None


@pytest.mark.asyncio
async def test_releitura_que_quebra_vira_evento_de_erro_e_espera_para_tentar_de_novo(
        harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    s = _estado(harness)
    _perfil(s, "conta_a")
    espiao = _ligar(s, monkeypatch, espiao=Espiao(falha=RuntimeError("driver caiu")))
    assert await s.verificacao_periodica.uma_volta() is not None
    await _terminar(s)
    assert [e["resultado"] for e in _eventos(s)] == ["erro"]
    # a volta seguinte NÃO martela: a tentativa que não leu a tela espera meia janela
    assert await s.verificacao_periodica.uma_volta() is None
    assert len(espiao.chamadas) == 1
    assert [e.get("motivo") for e in _eventos(s)][-1] == regras.TENTADA_HA_POUCO


# ---------------------------------------------------------------- quem entra
@pytest.mark.asyncio
async def test_so_entra_sessao_pronta_vencida_de_persona_ativa(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    s = _estado(harness)
    _perfil(s, "recem_lida", "android-01", horas_atras=1)                  # dentro da janela
    p_bloq, _ = _perfil(s, "bloqueada", "android-02", horas_atras=20)
    s.db.execute("UPDATE instagram_profiles SET status='blocked' WHERE id=?", (p_bloq,))
    p_precisa, _ = _perfil(s, "precisa_de_pessoa", "android-03", horas_atras=20)
    s.social_repo.set_session(p_precisa, status=SessionStatus.auth_challenge, instance_id="android-03",
                              verified_at=to_iso(now() - timedelta(hours=20)), detail="desafio")
    espiao = _ligar(s, monkeypatch)
    assert await s.verificacao_periodica.uma_volta() is None
    assert espiao.chamadas == [] and _comandos(s) == []


@pytest.mark.asyncio
async def test_uma_conta_por_volta_a_mais_antiga_primeiro(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    s = _estado(harness)
    _perfil(s, "mais_nova", "android-01", horas_atras=8)
    p_velha, c_velha = _perfil(s, "mais_velha", "android-02", horas_atras=30)
    p_sem, c_sem = _perfil(s, "nunca_lida", "android-03", horas_atras=None)
    espiao = _ligar(s, monkeypatch)

    primeiro = await s.verificacao_periodica.uma_volta()
    assert primeiro is not None and primeiro.account_id == c_sem and primeiro.profile_id == p_sem   # sem leitura vem primeiro
    # enquanto ela trabalha, a volta seguinte não despacha outra (uma por vez no parque)
    assert await s.verificacao_periodica.uma_volta() is None
    await _terminar(s, "android-03")
    segundo = await s.verificacao_periodica.uma_volta()
    assert segundo is not None and segundo.account_id == c_velha and segundo.profile_id == p_velha
    await _terminar(s, "android-02")
    assert [c[0] for c in espiao.chamadas] == ["android-03", "android-02"]


# ---------------------------------------------------------------- quando pular: nada abre comando
async def _pulou(s: AppState, motivo: str, espiao: Espiao) -> None:
    assert await s.verificacao_periodica.uma_volta() is None
    assert espiao.chamadas == []
    assert _comandos(s) == [], f"abriu comando mesmo pulando por {motivo}"
    assert [e.get("motivo") for e in _eventos(s)] == [motivo]
    assert [e["resultado"] for e in _eventos(s)] == ["pulada"]
    # o MESMO motivo na volta seguinte não gera outro evento (o tique é de minutos)
    assert await s.verificacao_periodica.uma_volta() is None
    assert len(_eventos(s)) == 1


@pytest.mark.asyncio
async def test_pula_aparelho_fora_do_ar_e_nunca_o_liga(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    s = _estado(harness)
    _perfil(s, "conta_a")
    espiao = _ligar(s, monkeypatch)
    rt = s.devices.get(IID)
    ligou: list[str] = []
    monkeypatch.setattr(s.devices, "request_start", lambda *a, **k: ligou.append("start"))
    rt.state = InstanceState.stopped
    await _pulou(s, regras.APARELHO_FORA_DO_AR, espiao)
    assert ligou == []


@pytest.mark.asyncio
async def test_pula_controle_manual_o_409_device_busy(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    s = _estado(harness)
    _perfil(s, "conta_a")
    espiao = _ligar(s, monkeypatch)
    s.devices.get(IID).control = ControlOwner.user
    await _pulou(s, regras.CONTROLE_MANUAL, espiao)


@pytest.mark.asyncio
async def test_pula_aparelho_com_trabalho_ou_comando_aberto(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    s = _estado(harness)
    _perfil(s, "conta_a")
    espiao = _ligar(s, monkeypatch)
    s.scheduler.workers[IID] = object()                                 # type: ignore[assignment]  # trabalho em curso
    try:
        await _pulou(s, regras.APARELHO_OCUPADO, espiao)
    finally:
        s.scheduler.workers.pop(IID, None)


@pytest.mark.asyncio
async def test_pula_quarentena_de_conta_travada(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    s = _estado(harness)
    _perfil(s, "conta_a")
    espiao = _ligar(s, monkeypatch)
    monkeypatch.setattr(s, "quarentena", lambda _iid: "conta travada logada")
    await _pulou(s, regras.QUARENTENA, espiao)


@pytest.mark.asyncio
async def test_pula_pausa_de_reparo(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    s = _estado(harness)
    _perfil(s, "conta_a")
    espiao = _ligar(s, monkeypatch)
    monkeypatch.setattr(s.devices, "pausa_de_reparo", lambda _rt: object())
    await _pulou(s, regras.PAUSA_DE_REPARO, espiao)


@pytest.mark.asyncio
async def test_pula_host_carregado_funil_ou_suite(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    s = _estado(harness)
    _perfil(s, "conta_a")
    espiao = _ligar(s, monkeypatch, host_folgado=False)
    monkeypatch.setattr(s.scheduler, "cpu_do_host_acima", lambda _rt, _limiar: "CPU 91 %")
    await _pulou(s, regras.HOST_CARREGADO, espiao)


@pytest.mark.asyncio
async def test_pula_worker_em_manutencao(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    s = _estado(harness)
    _perfil(s, "conta_a")
    espiao = _ligar(s, monkeypatch)
    s.devices.get(IID).worker_id = "worker-x"
    monkeypatch.setattr(s.scheduler, "worker_gate", lambda _w: "em manutenção")
    await _pulou(s, regras.WORKER_EM_MANUTENCAO, espiao)


@pytest.mark.asyncio
async def test_pula_quando_o_portao_da_sessao_recusa(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    s = _estado(harness)
    _perfil(s, "conta_a")
    espiao = _ligar(s, monkeypatch, portao_livre=False)
    monkeypatch.setattr(s.verificacao_periodica, "_motivo_do_portao", lambda _alvo, _rt: regras.PORTAO)
    await _pulou(s, regras.PORTAO, espiao)


@pytest.mark.asyncio
async def test_pula_sem_provedor_de_sessao(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    s = _estado(harness)
    _perfil(s, "conta_a")
    espiao = _ligar(s, monkeypatch)
    monkeypatch.setattr(s.sessoes, "for_package", lambda _pacote: None)
    await _pulou(s, regras.SEM_PROVEDOR, espiao)


# ---------------------------------------------------------------- a CPU do host (leitura real da capacidade)
def test_cpu_do_host_local_le_a_amostra_do_gerente_de_aparelhos(harness: Harness) -> None:
    """O aparelho da máquina do central: a amostra do laço de métricas, a mesma do reparo e do boot."""
    from app.models import Metrics

    s = _estado(harness)
    rt = s.devices.get(IID)
    rt.worker_id = s.cfg.owner_id
    s.devices.last_metrics = None
    assert s.scheduler.cpu_do_host_acima(rt, 50.0) is not None                    # sem amostra: pula
    for cpu, pula in ((91.0, True), (50.0, False), (12.0, False)):
        s.devices.last_metrics = Metrics(ts=to_iso(now()), cpu_percent=cpu, mem_total_gb=16.0, mem_available_gb=8.0,
                                         mem_used_percent=50.0)
        assert (s.scheduler.cpu_do_host_acima(rt, 50.0) is not None) is pula, cpu


def test_cpu_do_host_remoto_le_a_batida_do_worker(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    s = _estado(harness)
    rt = s.devices.get(IID)
    rt.worker_id = "worker-remoto"

    class Cap:
        def __init__(self, cpu: float | None, stale: bool = False) -> None:
            self.cpu_percent, self.stale, self.name = cpu, stale, "host"

        def cpu_acima(self, limiar: float | None) -> str | None:
            return "alta" if (limiar is not None and self.cpu_percent is not None and self.cpu_percent > limiar) else None

    for cap, esperado_pula in ((None, True), (Cap(None), True), (Cap(30.0, stale=True), True), (Cap(80.0), True),
                               (Cap(30.0), False)):
        monkeypatch.setattr(s.scheduler, "_capacidade", lambda _w, c=cap: c)
        assert (s.scheduler.cpu_do_host_acima(rt, 50.0) is not None) is esperado_pula


# ---------------------------------------------------------------- o portão da sessão de verdade
@pytest.mark.asyncio
async def test_o_portao_do_botao_verificar_conta_vale_para_o_laco(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    """Sem trocar o `_motivo_do_portao`: o aparelho do harness não tem o Instagram instalado, e a regra que desabilita o
    botão "Verificar conta" (`session_actions.verify`) é a mesma que impede o laço de abrir um app que não existe."""
    s = _estado(harness)
    _perfil(s, "conta_a")
    espiao = _ligar(s, monkeypatch, portao_livre=False)
    assert await s.verificacao_periodica.uma_volta() is None
    assert espiao.chamadas == [] and _comandos(s) == []
    assert [e.get("motivo") for e in _eventos(s)] == [regras.PORTAO]


# ---------------------------------------------------------------- a cadeia do ADR-068 é a do motor de sessão
class _ProvedorDoMotor:
    """O motor de sessão de verdade (`SessaoDeclarada`) sobre o Instagram de mentira, no lugar do provedor do harness."""

    def __init__(self, motor: SessaoDeclarada, app: FakeInstagram) -> None:
        self.motor, self.app = motor, app
        self.chamadas: list[bool] = []

    async def ensure_session(self, rt: Any, profile_id: str, **kw: Any) -> AuthResult:
        self.chamadas.append(bool(kw.get("observe_only")))
        return await self.motor.ensure_session(FakeRt(self.app, rt.id), profile_id, **kw)


@pytest.mark.asyncio
async def test_o_desafio_visto_na_releitura_bloqueia_pelo_caminho_do_motor_de_sessao(
        harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    """O laço só manda olhar. Quem bloqueia a persona é o motor de sessão (`aplicar_desafio`, ADR-068): aqui o motor de
    verdade lê a tela "Confirm you're human" de um app que o painel dava como pronto, sem uma tarefa que o olhasse."""
    s = _estado(harness)
    s.appium.log_masking_active = True
    pid = s.social.create_profile(ProfileCreate(username=USUARIO, password=SENHA, instance_id=IID)).id
    s.social_repo.set_session(pid, status=SessionStatus.session_ready, instance_id=IID,
                              verified_at=to_iso(now() - timedelta(hours=20)), detail="pronta")
    app = FakeInstagram(screen="challenge", stored_password=SENHA)
    ajustes = s.cfg.file.contas.ajustes(PKG)
    ajustes.settle_s, ajustes.open_timeout_s = 0.01, 0.3
    motor = SessaoDeclarada(do_app(PKG), s.cfg, FakeDevices(app), s.social_repo,  # type: ignore[arg-type]
                            s.secrets, SensitiveInputChannel(lambda: True), s.bus)
    motor.focus_poll_s = 0.01
    provedor = _ProvedorDoMotor(motor, app)
    _ligar(s, monkeypatch, espiao=provedor)                          # type: ignore[arg-type]

    assert await s.verificacao_periodica.uma_volta() is not None
    await _terminar(s)

    assert provedor.chamadas == [True]                                # só observou
    assert s.db.scalar("SELECT status FROM instagram_profiles WHERE id=?", (pid,)) == "blocked"
    assert (s.social_repo.session_row(pid, IID) or {})["status"] == SessionStatus.auth_challenge.value
    assert s.db.scalar("SELECT COUNT(*) FROM events WHERE kind='session.needs_person'") >= 1
    assert s.quarentena(IID) is not None                              # o aparelho fica em quarentena (ADR-055)
    assert [e["resultado"] for e in _eventos(s)] == ["mudou"]
    assert _eventos(s)[0]["perfil"] == "blocked" and _eventos(s)[0]["status"] == "auth_challenge"
    # persona bloqueada sai da fila: a volta seguinte não a relê e o aparelho em quarentena fica quieto
    assert await s.verificacao_periodica.uma_volta() is None
    assert provedor.chamadas == [True]


# ---------------------------------------------------------------- a linha no relatório de saúde do aprendizado
@pytest.mark.asyncio
async def test_a_saude_do_aprendizado_conta_as_releituras_da_janela(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.modules.learning.infrastructure.relatorio_sql import FontesDeFalhaSql

    s = _estado(harness)
    janela = (to_iso(now() - timedelta(days=1)), to_iso(now() + timedelta(minutes=5)))
    assert FontesDeFalhaSql(s.db).saude(*janela, simulados=True).verificacoes_de_sessao == {}      # desligada: nada

    pid, _conta = _perfil(s, "conta_a")
    monkeypatch.setattr(s.sessoes, "for_package", lambda _pacote: Espiao())
    _ligar(s, monkeypatch)
    assert await s.verificacao_periodica.uma_volta() is not None
    await _terminar(s)
    s.devices.get(IID).control = ControlOwner.user                      # e uma volta que pula
    s.social_repo.set_session(pid, status=SessionStatus.session_ready, instance_id=IID,
                              verified_at=to_iso(now() - timedelta(hours=9)), detail="pronta")
    assert await s.verificacao_periodica.uma_volta() is None

    saude = FontesDeFalhaSql(s.db).saude(*janela, simulados=True)
    assert dict(saude.verificacoes_de_sessao) == {"verificada": 1, "pulada": 1}


# ---------------------------------------------------------------- reinício na janela: a fila anda
@pytest.mark.asyncio
async def test_depois_de_um_reinicio_o_alvo_deduplicado_nao_trava_a_fila(harness: Harness,
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    """A releitura anterior quebrou (a sessão não foi atualizada) e o central reiniciou dentro da mesma janela da chave: o
    mesmo alvo volta a ser o primeiro, o despacho devolve o comando ORIGINAL (deduplicado) e nada é agendado. Ele precisa
    sair da frente (`tentada_ha_pouco`) para a conta de trás ser relida."""
    from app.modules.identity.infrastructure.verificacao_periodica import VerificacaoPeriodica

    s = _estado(harness)
    _perfil(s, "primeira", "android-01", horas_atras=30)
    _p2, c2 = _perfil(s, "segunda", "android-02", horas_atras=20)
    espiao = _ligar(s, monkeypatch, espiao=Espiao(falha=RuntimeError("driver caiu")))
    monkeypatch.setattr(VerificacaoPeriodica, "_janela", lambda _self: 7)        # a mesma janela antes e depois do reinício

    assert await s.verificacao_periodica.uma_volta() is not None
    await _terminar(s, "android-01")
    assert [c[0] for c in espiao.chamadas] == ["android-01"]

    s.verificacao_periodica = VerificacaoPeriodica(s)                            # o reinício: a memória volta vazia
    monkeypatch.setattr(s.verificacao_periodica, "_motivo_do_portao", lambda _alvo, _rt: None)
    assert await s.verificacao_periodica.uma_volta() is None                     # deduplicado: nada agendado agora
    assert len(_comandos(s)) == 1
    proximo = await s.verificacao_periodica.uma_volta()
    assert proximo is not None and proximo.account_id == c2                      # a fila andou
    await _terminar(s, "android-02")
    assert [c[0] for c in espiao.chamadas] == ["android-01", "android-02"]
