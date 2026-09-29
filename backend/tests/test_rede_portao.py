"""Item 25.6 — portão de rede no scheduler (contrato C4, ADR-056 §3).

O que se prova aqui (tudo `simulated`: aparelho falso de rede no lugar do adb, servidor sing-box falso, provedor por
regras e aparelho falso de QA; nenhuma VPN, nenhum emulador):
- a porta está ligada no `AppState` (`Scheduler.rede_gate` = `ConvergenciaDeRede.motivo_de_espera`);
- política exigida: só `trafego_verificado` DENTRO DA VALIDADE (`rede.validade_verificacao_s`) libera. A medição
  vencida (ou sem data) segura a tarefa com o motivo, aparece como `verificar` na pendência do painel, e a porta
  dispara a sonda de novo; medida outra vez, libera;
- conta vinculada DEPOIS da medição: o estado fica, mas a porta segura até a medição provar o app dela;
- a remedição que não conclui (sem IP de saída) não é repetida a cada volta: espera `rede.sonda.reverificar_s`;
- a varredura, com o aparelho livre, adianta a remedição para o fim da validade (a tarefa não espera por ela);
- política livre: nenhum efeito, nem com a medição velha;
- a queda observada no meio de um objetivo (a linha regrediu) suspende a etapa SEGUINTE do mesmo app, sem gastar
  tentativa, com `wait_reason='rede'`, e o objetivo segue quando a rede volta a `trafego_verificado` — com a porta
  real e com uma porta de mentira que só vira o sinal entre as etapas;
- revisão: o túnel que cai NO APARELHO no meio do objetivo é visto pela releitura entre etapas (ninguém escreve a
  linha), e o reinício que a convergência pede sai com o objetivo suspenso pela rede (`running`, `wait_reason='rede'`)
  — antes, os dois esperavam um pelo outro; o app de conta nunca aberto não trava a tarefa com política exigida (a
  sonda disparada pela porta o abre).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.devices import rede
from app.modules.identity.presentation.schemas import ProfileCreate

from .conftest import Harness
from .test_rede_aplicacao import Reinicios, _envelhecer_configuracao, _linha, _passo, parque  # noqa: F401
from .test_rede_sonda import INSTAGRAM, IP, SondaFalsa, _ate_conectado, _preparar_sonda

IID = "android-01"


def _medicoes(h: Harness, iid: str = IID) -> int:
    return int(h.state.db.scalar("SELECT COUNT(*) FROM network_measurements WHERE instance_id=?",  # type: ignore[union-attr]
                                 (iid,)) or 0)


def _envelhecer_verificacao(h: Harness, s: float, iid: str = IID) -> None:
    quando = (datetime.now(timezone.utc) - timedelta(seconds=s)).isoformat()
    h.state.db.execute("UPDATE device_network SET verified_at=? WHERE instance_id=?", (quando, iid))  # type: ignore[union-attr]


def _liberar_a_porta(h: Harness, iid: str = IID) -> None:
    """A porta só dispara trabalho de 30 em 30 s por aparelho; o teste não espera o relógio."""
    h.state.rede_convergencia.memoria(iid).ultima_tentativa = None  # type: ignore[union-attr]


async def _verificado(h: Harness, monkeypatch: pytest.MonkeyPatch, policy: str = "exigida",
                     reinicios: Reinicios | None = None) -> SondaFalsa:
    ap, _ = _preparar_sonda(h, monkeypatch, policy=policy, reinicios=reinicios)
    await _ate_conectado(h, ap)
    ap.usar("com.android.shell", 1000, 1000)
    assert await _passo(h, "tarefa")
    assert _linha(h)["state"] == "trafego_verificado" and _linha(h)["verified_at"]
    return ap


async def test_a_porta_e_a_da_convergencia(parque: Harness) -> None:  # noqa: F811
    st = parque.state
    assert st is not None and st.scheduler.rede_gate == st.rede_convergencia.motivo_de_espera
    assert st.cfg.file.rede.validade_verificacao_s == 21_600


async def test_verificacao_vencida_segura_a_tarefa_e_a_porta_mede_de_novo(parque: Harness,  # noqa: F811
                                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    st = parque.state
    assert st is not None
    await _verificado(parque, monkeypatch)
    conv = st.rede_convergencia
    validade = float(st.cfg.file.rede.validade_verificacao_s)
    assert conv.motivo_de_espera(IID) is None
    # Dentro da validade a porta (motivo `tarefa`) não mede nada.
    _envelhecer_verificacao(parque, validade * 0.5)
    _liberar_a_porta(parque)
    assert conv.motivo_de_espera(IID) is None and conv.trabalho(st.devices.devices[IID], motivo="tarefa") is None
    # Vencida: vale como inválida — segura, diz por quê, e aparece como pendência de verificar no painel.
    _envelhecer_verificacao(parque, validade + 60)
    _liberar_a_porta(parque)
    antes = _medicoes(parque)
    motivo = conv.motivo_de_espera(IID) or ""
    assert "venceu" in motivo and "validade 6 h" in motivo
    [linha] = [a for a in rede.listar_aparelhos(st)["devices"] if a["instance_id"] == IID]
    assert linha["pending"] == "verificar" and linha["effective_state"] == "trafego_verificado"
    assert [p["falta"] for p in rede.pendencias(st) if p["instance_id"] == IID] == ["verificar"]
    # A porta disparou a sonda (aparelho livre): mediu de novo, renovou `verified_at` e libera.
    await parque.wait(lambda: _medicoes(parque) == antes + 1, what="remedição pela porta")
    await parque.wait(lambda: conv.motivo_de_espera(IID) is None, what="porta liberada")
    idade = rede.idade_da_verificacao(_linha(parque))
    assert idade is not None and idade < 60


async def test_sem_data_de_medicao_nao_vale(parque: Harness, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    st = parque.state
    assert st is not None
    await _verificado(parque, monkeypatch)
    st.db.execute("UPDATE device_network SET verified_at=NULL WHERE instance_id=?", (IID,))
    assert "sem data da última medição" in (st.rede_convergencia.motivo_de_espera(IID) or "")


async def test_remedicao_sem_ip_espera_antes_de_repetir(parque: Harness,  # noqa: F811
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    st = parque.state
    assert st is not None
    ap = await _verificado(parque, monkeypatch)
    conv = st.rede_convergencia
    _envelhecer_verificacao(parque, float(st.cfg.file.rede.validade_verificacao_s) + 60)
    ap.ip4 = None                                           # o eco de IP não responde: medição sem saída
    antes = _medicoes(parque)
    _liberar_a_porta(parque)
    assert await _passo(parque, "tarefa")
    assert _medicoes(parque) == antes + 1
    # O estado não muda sem IP medido, e o vencido continua vencido: a porta segura e NÃO dispara de novo.
    assert _linha(parque)["state"] == "trafego_verificado" and conv.vencida(_linha(parque))
    _liberar_a_porta(parque)
    assert "não concluiu" in (conv.motivo_de_espera(IID) or "")
    _liberar_a_porta(parque)
    assert conv.trabalho(st.devices.devices[IID], motivo="tarefa") is None
    assert conv.trabalho(st.devices.devices[IID], motivo="varredura") is None
    await parque.ticks(2)
    assert _medicoes(parque) == antes + 1
    # Passada a espera, com a saída de volta, a porta mede e libera.
    ap.ip4 = IP
    conv.memoria(IID).espera_ate = 0.0
    _liberar_a_porta(parque)
    assert await _passo(parque, "tarefa")
    assert conv.motivo_de_espera(IID) is None


async def test_varredura_adianta_a_remedicao_perto_do_fim_da_validade(parque: Harness,  # noqa: F811
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    st = parque.state
    assert st is not None
    await _verificado(parque, monkeypatch)
    conv = st.rede_convergencia
    validade = float(st.cfg.file.rede.validade_verificacao_s)
    _envelhecer_verificacao(parque, validade * 0.5)
    assert conv._acao(_linha(parque), "varredura") != "verificar"
    _envelhecer_verificacao(parque, validade * 0.95)
    assert conv.motivo_de_espera(IID) is None                # ainda vale: a tarefa não espera
    antes = _medicoes(parque)
    assert await _passo(parque, "varredura")
    assert _medicoes(parque) == antes + 1 and _linha(parque)["state"] == "trafego_verificado"
    assert (rede.idade_da_verificacao(_linha(parque)) or 1e9) < 60


async def test_conta_vinculada_depois_da_medicao_pede_medir_o_app_dela(parque: Harness,  # noqa: F811
                                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    """A medição verificou sem conta no aparelho (só o mínimo: um app). Uma persona com conta do Instagram é
    vinculada depois: o estado continua o da medição, mas a porta não o aceita sem o app da conta provado."""
    st = parque.state
    assert st is not None
    ap = await _verificado(parque, monkeypatch)
    conv = st.rede_convergencia
    assert conv.motivo_de_espera(IID) is None and rede.apps_sem_prova(st, _linha(parque)) == []
    st.social.create_profile(ProfileCreate(username="lucas.teste", instance_id=IID))
    assert INSTAGRAM in rede.apps_exigidos(st, IID)
    assert _linha(parque)["state"] == "trafego_verificado" and conv.invalida(_linha(parque)) == "apps"
    _liberar_a_porta(parque)
    motivo = conv.motivo_de_espera(IID) or ""
    assert "não cobre" in motivo and INSTAGRAM in motivo
    assert [p["falta"] for p in rede.pendencias(st) if p["instance_id"] == IID] == ["verificar"]
    # O app da conta usou a rede pelo túnel: a sonda disparada pela porta o prova, e a porta libera.
    ap.usar(INSTAGRAM, 3000, 3000)
    await parque.wait(lambda: conv.motivo_de_espera(IID) is None, what="app da conta provado")
    assert rede.apps_sem_prova(st, _linha(parque)) == []


async def test_politica_livre_nao_tem_efeito_nem_com_medicao_velha(parque: Harness,  # noqa: F811
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    st = parque.state
    assert st is not None
    await _verificado(parque, monkeypatch, policy="livre")
    conv = st.rede_convergencia
    _envelhecer_verificacao(parque, float(st.cfg.file.rede.validade_verificacao_s) * 10)
    assert conv.motivo_de_espera(IID) is None and not conv.vencida(_linha(parque))
    assert conv._acao(_linha(parque), "varredura") != "verificar"
    assert [p for p in rede.pendencias(st) if p["instance_id"] == IID] == []
    # Nem sem rede verificada nenhuma: com `livre`, a tarefa não depende da rede.
    rede.registrar_observacao(st, IID, rev=1, estado="configurado", evidencia="deriva: túnel caído (teste)")
    assert conv.motivo_de_espera(IID) is None


# ============================================================================ suspensão entre etapas (scheduler)
async def test_queda_observada_no_meio_suspende_a_etapa_seguinte_e_retoma(parque: Harness,  # noqa: F811
                                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    """Com a porta REAL: verificado no despacho; no meio da primeira etapa com IA, a deriva regride a linha
    (`configurado`, túnel caído). A etapa em curso termina; a seguinte não começa (nenhuma tentativa), o objetivo
    espera com `wait_reason='rede'` e o motivo da convergência; medida de novo, segue até o fim."""
    st = parque.state
    assert st is not None
    await _verificado(parque, monkeypatch)
    conv = st.rede_convergencia
    inner = parque.ai.inner
    decide0 = inner.decide
    caiu: dict[str, str] = {}

    async def decide(req: Any) -> Any:
        if not caiu:
            caiu["etapa"] = req.ctx.step_key
            rede.registrar_observacao(st, IID, rev=1, estado="configurado",
                                      evidencia="deriva (teste): configuração no lugar, túnel caído; reinicia")
            # Sem reinício de verdade aqui: a convergência não mexe até o teste liberar.
            conv.memoria(IID).espera_ate = 1e18
        return await decide0(req)

    inner.decide = decide
    run = parque.run([IID])
    oid = f"{run.id}:{IID}"

    def esperando() -> bool:
        o = st.db.one("SELECT wait_reason FROM objectives WHERE id=?", (oid,))
        return o is not None and o["wait_reason"] == "rede"

    await parque.wait(esperando, 60, "suspensa pela rede")
    await parque.ticks(3)
    obj = st.repo.objective_row(oid)
    assert "reinício" in str(obj["status_detail"]) and obj["status"] in ("running", "pending")
    etapas = {s["key"]: s for s in st.db.query("SELECT key, status, attempts FROM steps WHERE objective_id=?", (oid,))}
    assert etapas[caiu["etapa"]]["status"] == "succeeded"          # a etapa em curso não foi cortada no meio
    prontas = [k for k, s in etapas.items() if s["status"] == "ready"]
    assert prontas and all(etapas[k]["attempts"] == 0 for k in prontas)
    assert all(s["status"] in ("succeeded", "ready", "pending") for s in etapas.values())
    # A rede volta (a sonda mediu de dentro do aparelho): o objetivo segue até o fim.
    rede.registrar_medicao(st, IID, rede.NetworkMeasurementInput(
        method="teste", egress_ipv4=IP, per_app={"com.android.shell": "ok"}), rev=1)
    assert _linha(parque)["state"] == "trafego_verificado"
    detalhe = await parque.wait_run(run.id, timeout=90)
    assert detalhe.status == "completed", [(s.key, s.status, s.status_detail) for s in detalhe.steps]


async def test_porta_perguntada_entre_etapas_do_mesmo_app(harness: Harness) -> None:
    """Com uma porta de mentira que só vira no meio da primeira decisão: antes do 25.6 a etapa seguinte do MESMO app
    começava sem perguntar pela rede (só a troca de app perguntava)."""
    st = harness.state
    assert st is not None
    motivo = "rede exigida (exigida): conectado; aguardando a medição do tráfego"
    sinal = {"segura": False}
    st.scheduler.rede_gate = lambda iid: motivo if sinal["segura"] else None
    inner = harness.ai.inner
    decide0 = inner.decide
    primeira: dict[str, str] = {}

    async def decide(req: Any) -> Any:
        if not primeira:
            primeira["etapa"] = req.ctx.step_key
            sinal["segura"] = True
        return await decide0(req)

    inner.decide = decide
    run = harness.run([IID])
    oid = f"{run.id}:{IID}"
    await harness.wait(lambda: (o := st.db.one("SELECT wait_reason FROM objectives WHERE id=?", (oid,))) is not None
                       and o["wait_reason"] == "rede", 60, "etapa seguinte segurada")
    await harness.ticks(3)
    tentativas = st.db.scalar("SELECT COUNT(*) FROM attempts a JOIN steps s ON s.id=a.step_id WHERE s.objective_id=?",
                              (oid,))
    await harness.ticks(3)
    assert st.db.scalar("SELECT COUNT(*) FROM attempts a JOIN steps s ON s.id=a.step_id WHERE s.objective_id=?",
                        (oid,)) == tentativas                      # parado: nada novo começou
    apps = {r["app_id"] for r in st.db.query("SELECT app_id FROM steps WHERE objective_id=?", (oid,))}
    assert len(apps) == 1                                          # um app só: não foi a porta da troca
    assert st.repo.objective_row(oid)["status_detail"] == motivo
    sinal["segura"] = False
    detalhe = await harness.wait_run(run.id, timeout=90)
    assert detalhe.status == "completed", [(s.key, s.status, s.status_detail) for s in detalhe.steps]


# ============================================================================ queda de verdade no meio (revisão)
class _ReiniciosQueReiniciam(Reinicios):
    """O dublê do `restart` que também faz o aparelho falso passar pelo boot (uptime zerado, always-on religando o
    cliente) — o que o comando de verdade faria."""

    def __init__(self) -> None:
        super().__init__()
        self.ao_reiniciar: Any = None

    def __call__(self, s: Any, instance_id: str, verb: str, motivo: str, *, requested_by: str, **kw: Any) -> str | None:
        cid = super().__call__(s, instance_id, verb, motivo, requested_by=requested_by, **kw)
        if cid is not None and self.ao_reiniciar is not None:
            self.ao_reiniciar()
        return cid


def _relogio(h: Harness) -> list[float]:
    """O relógio da convergência, injetado: a porta só dispara de 30 em 30 s, e o teste não espera o de verdade."""
    agora = [1_000_000.0]
    h.state.rede_convergencia._agora = lambda: agora[0]  # type: ignore[union-attr]
    return agora


async def test_queda_do_tunel_no_meio_e_vista_entre_etapas_e_o_reinicio_sai(parque: Harness,  # noqa: F811
                                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    """Os dois achados da revisão num objetivo só, sem escrever a linha à mão nem segurar a convergência:
    - o túnel cai NO APARELHO (T=0/V=0) no meio da primeira etapa; a releitura entre etapas (`rede_releitura`) o vê e
      a etapa seguinte fica esperando a rede — antes, nada relia o aparelho ocupado e a linha seguia verificada;
    - o objetivo suspenso fica `running` com `wait_reason='rede'`, e o reinício que a convergência pede SAI (antes,
      `objetivo_em_andamento` o recusava para sempre: a rede esperava o objetivo e o objetivo esperava a rede)."""
    st = parque.state
    assert st is not None
    agora = _relogio(parque)
    reinicios = _ReiniciosQueReiniciam()
    ap = await _verificado(parque, monkeypatch, reinicios=reinicios)
    pedidos = len(reinicios.pedidos)

    def boot() -> None:
        ap.depois_do_boot(uptime=1)
        _envelhecer_configuracao(parque)

    reinicios.ao_reiniciar = boot
    # Quem regride a linha, e com qual evidência: só o caminho do produto (nenhuma escrita do teste).
    observacoes: list[tuple[str, str]] = []
    registrar0 = rede.registrar_observacao

    def registrar(st_: Any, iid: str, *, rev: int, estado: str, evidencia: str, **kw: Any) -> Any:
        observacoes.append((estado, evidencia))
        return registrar0(st_, iid, rev=rev, estado=estado, evidencia=evidencia, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(rede, "registrar_observacao", registrar)
    inner = parque.ai.inner
    decide0 = inner.decide
    caiu: dict[str, str] = {}

    async def decide(req: Any) -> Any:
        if not caiu:
            caiu["etapa"] = req.ctx.step_key
            ap.tun = ap.vpn = False                                 # o túnel caiu no aparelho; a config ficou
            ap.uptime = 5000
            agora[0] += 31                                           # passou o intervalo da releitura
        return await decide0(req)

    inner.decide = decide
    run = parque.run([IID])
    oid = f"{run.id}:{IID}"
    viu: dict[str, str] = {}

    def esperou_a_rede() -> bool:
        o = st.db.one("SELECT wait_reason, status, status_detail FROM objectives WHERE id=?", (oid,))
        if o is not None and o["wait_reason"] == "rede":
            viu.update(status=o["status"], detalhe=str(o["status_detail"]))
            return True
        return False

    await parque.wait(esperou_a_rede, 60, "etapa seguinte segurada pela queda")
    assert viu["status"] == "running"                                  # o caso que travava o reinício
    [queda] = [e for e in observacoes if "releitura entre etapas" in e[1]]
    assert queda[0] == "configurado" and "túnel caído" in queda[1]
    etapas = {s["key"]: s for s in st.db.query("SELECT key, status, attempts FROM steps WHERE objective_id=?", (oid,))}
    assert etapas[caiu["etapa"]]["status"] == "succeeded"             # a etapa em curso terminou
    # Ninguém escreveu a linha: a convergência pediu o reinício, o boot religou o túnel, mediu, e o objetivo seguiu.

    def andar() -> bool:
        agora[0] += 31                                               # a porta pode disparar de novo
        return st.repo.run_row(run.id)["status"] in ("completed", "completed_with_issues", "cancelled", "failed")

    await parque.wait(andar, 90, "o objetivo retomado depois da rede voltar")
    fim = st.repo.run_detail(run.id)
    assert fim.status == "completed", [(s.key, s.status, s.status_detail) for s in fim.steps]
    rede_pedidos = reinicios.pedidos[pedidos:]
    assert [i for i, _ in rede_pedidos] == [IID]                     # um reinício, pedido pela rede, e só um
    assert _linha(parque)["state"] == "trafego_verificado"
    acoes = [c["params"] for c in st.db.query(
        "SELECT params FROM commands WHERE verb='device.network' AND instance_id=? ORDER BY created_at", (IID,))]
    assert any('"conectar"' in a for a in acoes) and '"verificar"' in acoes[-1]
    assert st.repo.objective_row(oid)["wait_reason"] is None


async def test_releitura_entre_etapas_sem_queda_nao_segura_e_respeita_o_intervalo(parque: Harness,  # noqa: F811
                                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    st = parque.state
    assert st is not None
    agora = _relogio(parque)
    ap = await _verificado(parque, monkeypatch)
    conv = st.rede_convergencia
    rt = st.devices.devices[IID]
    leituras = sum(1 for c in ap.comandos if c.startswith("echo U=$(id -u); echo A="))
    await conv.reler_entre_etapas(rt)                                # dentro do intervalo: nem lê
    assert sum(1 for c in ap.comandos if c.startswith("echo U=$(id -u); echo A=")) == leituras
    agora[0] += 31
    await conv.reler_entre_etapas(rt)                                # lê, e o túnel está no ar: nada muda
    assert sum(1 for c in ap.comandos if c.startswith("echo U=$(id -u); echo A=")) == leituras + 1
    assert _linha(parque)["state"] == "trafego_verificado"
    # Política livre: a tarefa não depende da rede, e a releitura não é feita.
    rede.atribuir(st, rede.NetworkAssignBody(instance_ids=[IID], vpn_profile_id=_linha(parque)["vpn_profile_id"],
                                             policy="livre", confirm_real_account=[IID]), "teste")
    agora[0] += 31
    antes = len(ap.comandos)
    await conv.reler_entre_etapas(rt)
    assert len(ap.comandos) == antes


async def test_app_nunca_aberto_nao_trava_a_tarefa_com_politica_exigida(parque: Harness,  # noqa: F811
                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    """O ciclo da revisão: conta do Instagram vinculada, app nunca aberto desde a conexão, `abrir_apps` desligado
    (o padrão). A medição sem tarefa deixa `parcial`; com a tarefa segurada pela porta, a sonda abre o app (é o que a
    tarefa faria), o tráfego dele passa pelo túnel e o objetivo roda."""
    st = parque.state
    assert st is not None
    assert st.cfg.file.rede.sonda.abrir_apps is False
    st.social.create_profile(ProfileCreate(username="lucas.teste", instance_id=IID))
    exigidos = rede.apps_exigidos(st, IID)
    assert INSTAGRAM in exigidos
    ap, _ = _preparar_sonda(parque, monkeypatch, policy="exigida")
    for i, pkg in enumerate(exigidos):
        ap.uids.setdefault(pkg, 10300 + i)
    ap.ao_abrir = {pkg: (3000, 3000) for pkg in exigidos}            # aberto, o app usa a rede pelo túnel
    await _ate_conectado(parque, ap)
    assert await _passo(parque, "ligou")                              # a medição da varredura/boot: não abre
    assert _linha(parque)["state"] == "parcial" and ap.abertos == []
    run = parque.run([IID])
    detalhe = await parque.wait_run(run.id, timeout=90)
    assert detalhe.status == "completed", [(s.key, s.status, s.status_detail) for s in detalhe.steps]
    assert sorted(ap.abertos) == sorted(exigidos)
    assert _linha(parque)["state"] == "trafego_verificado"
    [ultima] = st.db.query("SELECT per_app, detail FROM network_measurements WHERE instance_id=? ORDER BY id DESC"
                           " LIMIT 1", (IID,))
    assert "aberto pela sonda" in str(ultima["detail"]) and json.loads(ultima["per_app"])[INSTAGRAM] == "ok"
