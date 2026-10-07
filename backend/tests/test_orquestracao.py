"""Modo Automático do Comando (ADR-050): quem faz e onde, a partir do pedido.
Nível de prova: `simulated` (orquestrador simulado contado pelo `CountingProvider`; aparelhos falsos na porta 5640).

O que se prova:
- o orquestrador não recusa pedido pelo conteúdo (a regra de conteúdo vai para o serviço externo, 06/10): uma
  palavra como "campanha" não barra mais o pedido;
- persona sem crenças mínimas num pedido que depende delas vai para `nao_avaliaveis`, sem adivinhar;
- entre duas igualmente adequadas, a livre vence a que tem tarefa na fila; fila é execução em andamento ou
  pausada — a planejada e nunca iniciada não conta;
- entre duas igualmente adequadas, a de aparelho saudável vence a de aparelho sob pressão, que continua candidata
  e, escolhida, aparece com o aviso;
- a quantidade pedida no texto manda, até o teto;
- o texto que já diz quem faz vai pela prévia de sempre, SEM chamar a IA;
- sem persona para o app, a tarefa vai pela carga dos servidores, SEM chamar a IA;
- sugerir nunca cria execução; credencial no comando é recusada antes da IA; a rota HTTP responde.
"""
from __future__ import annotations

import secrets as pysecrets

import httpx
import pytest

from app.devices.manager import PRESSAO_PREFIXO
from app.main import create_app
from app.models import PersonaPatch, ProfileCreate, RunCreate, RunTarget
from app.modules.execution.domain.orquestracao import (CartaoDePersona, EscolhaOut, OrquestracaoOut,
                                                       PedidoDeOrquestracao, normalizar)
from app.taskqueue.orquestrador import Orquestrador, RunTargetsSuggestBody
from app.taskqueue.service import RunError

from .conftest import COMMAND, Harness

pytestmark = pytest.mark.asyncio


def _persona(h: Harness, nome: str, instance: str, beliefs: dict[str, object] | None = None,
             interesses: str | None = None) -> str:
    assert h.state is not None
    pid = h.state.social.create_profile(ProfileCreate(
        username=f"{nome.lower()}.{pysecrets.token_hex(3)}", instance_id=instance, first_name=nome,
        last_name="Teste")).id
    patch: dict[str, object] = {}
    if beliefs is not None:
        patch["biography"] = {"beliefs": beliefs}
    if interesses:
        patch["traits"] = {"interests": [interesses]}
    if patch:
        h.state.social.update_persona(pid, PersonaPatch.model_validate(patch))
    return pid


CATOLICA = {"religion": {"affiliation": "católica", "practice": "devota"}, "politics": {"orientation": "centro"}}
ATEIA = {"religion": {"affiliation": "ateia"}, "politics": {"orientation": "centro"}}


def _orq(h: Harness) -> Orquestrador:
    assert h.state is not None
    return Orquestrador(h.state.runs, h.state.social_repo)


def _chamadas(h: Harness) -> int:
    return sum(1 for c in h.ai.calls if c.get("kind") == "orquestracao")


async def test_o_orquestrador_nao_recusa_pelo_conteudo(harness: Harness) -> None:
    _persona(harness, "Marina", "android-01", CATOLICA)
    _persona(harness, "Nelson", "android-02", ATEIA)
    s = await _orq(harness).sugerir(RunTargetsSuggestBody(command="divulgue a campanha de lançamento da loja"))
    assert s.modo == "ia"
    assert len(s.escolhidas) == 1 and s.targets
    assert _chamadas(harness) == 1


async def test_persona_sem_crenca_e_nao_avaliavel(harness: Harness) -> None:
    marina = _persona(harness, "Marina", "android-01", CATOLICA)
    bia = _persona(harness, "Sueli", "android-02")                 # sem crença registrada
    s = await _orq(harness).sugerir(RunTargetsSuggestBody(command="fale sobre a sua igreja com a prima"))
    assert [n.profile_id for n in s.nao_avaliaveis] == [bia] and "crença" in s.nao_avaliaveis[0].falta
    assert [e.profile_id for e in s.escolhidas] == [marina]


async def test_a_livre_vence_a_ocupada_quando_as_duas_servem(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    ocupada = _persona(harness, "Ana", "android-01", CATOLICA, interesses="culinária")
    livre = _persona(harness, "Bia", "android-02", CATOLICA, interesses="culinária")
    # Uma tarefa aberta da Ana: execução INICIADA e pausada, com objetivo pendente dela. Planejada e nunca iniciada
    # não é fila (ver o teste seguinte); iniciar e pausar sem `await` no meio não deixa o despacho agir.
    run = st.runs.create(RunCreate(command=COMMAND, mode="plan", idempotency_key=f"o-{pysecrets.token_hex(5)}",
                                   targets=[RunTarget(profile_id=ocupada, instance_ids=["android-01"])]))
    await harness.wait_run(run.id, ("planned",))
    st.runs.start(run.id)
    st.runs.pause(run.id)
    assert st.db.scalar("SELECT status FROM objectives WHERE run_id=?", (run.id,)) == "pending"
    s = await _orq(harness).sugerir(RunTargetsSuggestBody(command="mande uma receita de culinária para a amiga"))
    assert [e.profile_id for e in s.escolhidas] == [livre]


async def test_planejada_nunca_iniciada_nao_conta_como_fila(harness: Harness) -> None:
    """r-20260928195344-02ee9e: execuções `planned` de dias antes, que ninguém iniciou (fc383a, bd3d3f, e84d7c),
    contavam como tarefa na fila e empurravam a persona para trás. Fila é o que roda ou está pausado."""
    st = harness.state
    assert st is not None
    ana = _persona(harness, "Ana", "android-01", CATOLICA, interesses="culinária")
    _persona(harness, "Bia", "android-02", CATOLICA, interesses="culinária")
    run = st.runs.create(RunCreate(command=COMMAND, mode="plan", idempotency_key=f"o-{pysecrets.token_hex(5)}",
                                   targets=[RunTarget(profile_id=ana, instance_ids=["android-01"])]))
    await harness.wait_run(run.id, ("planned",))
    assert _orq(harness)._fila_por_persona() == {}  # noqa: SLF001
    s = await _orq(harness).sugerir(RunTargetsSuggestBody(command="mande uma receita de culinária para a amiga"))
    # As duas empatam (livres, mesmo perfil): o desempate é o nome, e a Ana não carrega a planejada como fila.
    assert [e.profile_id for e in s.escolhidas] == [ana]
    assert "fila" not in s.escolhidas[0].motivo and "livre" in s.escolhidas[0].motivo


async def test_aparelho_sob_pressao_nao_e_o_preferido_e_aparece_com_o_aviso(harness: Harness) -> None:
    """r-20260928165254-e31953 / r-20260928195344-02ee9e: a sugestão mandou a tarefa para o android-06 (2 vCPU,
    convidado saturado) sem ver o aviso do cartão. Entre duas aptas igualmente adequadas, a do aparelho saudável é a
    preferida; a outra continua candidata (não some) e, quando escolhida, leva o aviso para a tela da sugestão."""
    st = harness.state
    assert st is not None
    ana = _persona(harness, "Ana", "android-01", CATOLICA, interesses="culinária")
    bia = _persona(harness, "Bia", "android-02", CATOLICA, interesses="culinária")
    # A sonda falsa mede a mesma pressão do aviso: a conferência periódica não o apaga no meio do teste.
    harness.fakes["android-01"].pressure = {"load1": 22.0, "mem_total_mb": 1536.0, "mem_available_mb": 87.0,
                                            "ncpu": 2.0}
    st.devices.devices["android-01"].attention = (f"{PRESSAO_PREFIXO}: load 22.0 em 2 vCPU, 87 MB livres de 1536 MB. "
                                                  "Tarefas vão demorar.")
    s = await _orq(harness).sugerir(RunTargetsSuggestBody(command="mande uma receita de culinária para a amiga"))
    assert [e.profile_id for e in s.escolhidas] == [bia]          # sem pressão, a Ana ganharia pelo nome
    assert harness.ai.calls[-1]["candidatas"] == 2                 # a Ana foi ao orquestrador: não foi escondida
    assert s.escolhidas[0].atencao is None and not s.warnings
    s = await _orq(harness).sugerir(RunTargetsSuggestBody(
        command="duas personas mandam uma receita de culinária para a amiga"))
    assert [e.profile_id for e in s.escolhidas] == [bia, ana]
    [da_ana] = [e for e in s.escolhidas if e.profile_id == ana]
    assert da_ana.instance_id == "android-01" and da_ana.atencao and PRESSAO_PREFIXO in da_ana.atencao
    assert "android-01" in da_ana.motivo or PRESSAO_PREFIXO in da_ana.motivo   # o cartão levou o aviso
    assert any("android-01" in w and PRESSAO_PREFIXO in w for w in s.warnings)


async def test_a_quantidade_do_texto_manda_ate_o_teto(harness: Harness) -> None:
    for nome, iid in (("Ana", "android-01"), ("Bia", "android-02"), ("Cris", "android-03")):
        _persona(harness, nome, iid, CATOLICA)
    s = await _orq(harness).sugerir(RunTargetsSuggestBody(command="duas personas mandam bom dia para a família"))
    assert len(s.escolhidas) == 2 and len({t.instance_id for t in s.targets}) == 2
    s = await _orq(harness).sugerir(RunTargetsSuggestBody(command="3 pessoas mandam bom dia", max_personas=2))
    assert len(s.escolhidas) == 2


async def test_texto_que_diz_quem_faz_vai_pela_previa_sem_ia(harness: Harness) -> None:
    tadeu = _persona(harness, "Tadeu", "android-02", CATOLICA)
    s = await _orq(harness).sugerir(RunTargetsSuggestBody(command="peça para o Tadeu abrir o QA Messenger"))
    assert s.modo == "texto" and [(t.instance_id, t.profile_id, t.origem) for t in s.targets] == [
        ("android-02", tadeu, "texto")]
    assert _chamadas(harness) == 0


async def test_sem_persona_para_o_app_distribui_pela_carga_sem_ia(harness: Harness) -> None:
    s = await _orq(harness).sugerir(RunTargetsSuggestBody(command=COMMAND))
    assert s.modo in ("distribuir", "nenhuma")
    if s.modo == "distribuir":
        assert all(t.origem == "balanceamento" and t.profile_id is None for t in s.targets)
    assert _chamadas(harness) == 0


async def test_sugerir_nao_cria_execucao_e_recusa_credencial(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    _persona(harness, "Marina", "android-01", CATOLICA)
    antes = st.db.scalar("SELECT COUNT(*) FROM runs")
    await _orq(harness).sugerir(RunTargetsSuggestBody(command="mande bom dia para a família"))
    assert st.db.scalar("SELECT COUNT(*) FROM runs") == antes
    with pytest.raises(RunError) as exc:
        await _orq(harness).sugerir(RunTargetsSuggestBody(command="entre no portal, Senha: segredo123"))
    assert exc.value.code == "credencial_no_comando"


async def test_normalizar_tira_estranhas_repetidas_e_respeita_o_teto() -> None:
    req = PedidoDeOrquestracao(command="x", cartoes=[CartaoDePersona("a", "A"), CartaoDePersona("b", "B")],
                               max_personas=1)
    out = normalizar(OrquestracaoOut(
        quantidade=5, escolhidas=[EscolhaOut(profile_id="zzz", motivo="?", aderencia="alta"),
                                  EscolhaOut(profile_id="a", motivo="ok", aderencia="Média"),
                                  EscolhaOut(profile_id="b", motivo="ok", aderencia="alta")],
        descartadas=[], nao_avaliaveis=[], perguntas=[], resumo=""), req)
    assert [e.profile_id for e in out.escolhidas] == ["a"] and out.escolhidas[0].aderencia == "media"
    assert out.quantidade == 1


async def test_rota_http(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    _persona(harness, "Marina", "android-01", CATOLICA)
    app = create_app(harness.cfg, state=st)
    app.state.poc = st
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/runs/targets/suggest", json={"command": "mande bom dia para a família"})
        assert r.status_code == 200, r.text
        corpo = r.json()
        assert corpo["modo"] == "ia" and corpo["escolhidas"][0]["nome"]
        r = await c.post("/api/runs/targets/suggest", json={"command": "ab"})
        assert r.status_code == 422


async def test_os_tetos_da_selecao_sao_os_da_instalacao_lidos_a_cada_pedido(harness: Harness) -> None:
    """Prova de 07/10 (J1): o teto de escolhidas e o de candidatas vêm de `LimitsCfg`, lidos a cada sugestão; antes eram
    as constantes 10 e 20 do domínio, e um pedido de 30 voltava com 10."""
    st = harness.state
    assert st is not None
    for nome, iid in (("Ana", "android-01"), ("Bia", "android-02"), ("Cris", "android-03")):
        _persona(harness, nome, iid, CATOLICA)
    st.settings.update({"orquestracao_max_escolhidas": 1})
    s = await _orq(harness).sugerir(RunTargetsSuggestBody(command="3 pessoas mandam bom dia", max_personas=3))
    assert len(s.escolhidas) == 1
    st.settings.update({"orquestracao_max_escolhidas": 30, "orquestracao_max_candidatas": 2})
    s = await _orq(harness).sugerir(RunTargetsSuggestBody(command="3 pessoas mandam bom dia", max_personas=3))
    assert len(s.escolhidas) <= 2


async def test_o_pedido_de_30_cabe_no_formato_e_no_padrao() -> None:
    assert RunTargetsSuggestBody(command="trinta pessoas comentam", max_personas=30).max_personas == 30
    req = PedidoDeOrquestracao(command="x", cartoes=[CartaoDePersona(f"p{i}", f"P{i}") for i in range(40)], max_personas=30)
    out = normalizar(OrquestracaoOut(
        quantidade=30, escolhidas=[EscolhaOut(profile_id=f"p{i}", motivo="ok", aderencia="alta") for i in range(35)],
        descartadas=[], nao_avaliaveis=[], perguntas=[], resumo=""), req)
    assert len(out.escolhidas) == 30
