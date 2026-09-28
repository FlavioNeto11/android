"""Modo Automático do Comando (ADR-050): quem faz e onde, a partir do pedido.
Nível de prova: `simulated` (orquestrador simulado contado pelo `CountingProvider`; aparelhos falsos na porta 5640).

O que se prova:
- pedido de propaganda, voto ou campanha volta com `alerta_conduta` e ninguém é escolhido;
- persona cujas crenças contradizem o pedido nunca é escolhida (vai para as descartadas, com motivo);
- persona sem crenças mínimas num pedido que depende delas vai para `nao_avaliaveis`, sem adivinhar;
- entre duas igualmente adequadas, a livre vence a que tem tarefa na fila;
- a quantidade pedida no texto manda, até o teto;
- o texto que já diz quem faz vai pela prévia de sempre, SEM chamar a IA;
- sem persona para o app, a tarefa vai pela carga dos servidores, SEM chamar a IA;
- sugerir nunca cria execução; credencial no comando é recusada antes da IA; a rota HTTP responde.
"""
from __future__ import annotations

import secrets as pysecrets

import httpx
import pytest

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


async def test_propaganda_ou_voto_nao_e_roteado(harness: Harness) -> None:
    _persona(harness, "Marina", "android-01", CATOLICA)
    _persona(harness, "Rafael", "android-02", ATEIA)
    s = await _orq(harness).sugerir(RunTargetsSuggestBody(command="comente nos posts pedindo voto no candidato X"))
    assert s.modo == "ia" and s.alerta_conduta
    assert s.targets == [] and s.escolhidas == []
    assert _chamadas(harness) == 1


async def test_quem_contradiz_o_pedido_nunca_e_escolhida(harness: Harness) -> None:
    marina = _persona(harness, "Marina", "android-01", CATOLICA)
    rafael = _persona(harness, "Rafael", "android-02", ATEIA)
    s = await _orq(harness).sugerir(RunTargetsSuggestBody(
        command="responda à tia no direct contando como foi a missa de domingo, com a sua fé"))
    assert [e.profile_id for e in s.escolhidas] == [marina]
    assert s.escolhidas[0].instance_id == "android-01" and s.escolhidas[0].motivo
    assert [d.profile_id for d in s.descartadas] == [rafael] and s.descartadas[0].motivo
    assert [(t.instance_id, t.profile_id) for t in s.targets] == [("android-01", marina)]


async def test_sem_crencas_num_pedido_de_crenca_e_nao_avaliavel(harness: Harness) -> None:
    marina = _persona(harness, "Marina", "android-01", CATOLICA)
    bia = _persona(harness, "Beatriz", "android-02")                 # sem crença registrada
    s = await _orq(harness).sugerir(RunTargetsSuggestBody(command="fale sobre a sua igreja com a prima"))
    assert [n.profile_id for n in s.nao_avaliaveis] == [bia] and "crença" in s.nao_avaliaveis[0].falta
    assert [e.profile_id for e in s.escolhidas] == [marina]


async def test_a_livre_vence_a_ocupada_quando_as_duas_servem(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    ocupada = _persona(harness, "Ana", "android-01", CATOLICA, interesses="culinária")
    livre = _persona(harness, "Bia", "android-02", CATOLICA, interesses="culinária")
    # Uma tarefa aberta da Ana (execução planejada com objetivo pendente dela).
    run = st.runs.create(RunCreate(command=COMMAND, mode="plan", idempotency_key=f"o-{pysecrets.token_hex(5)}",
                                   targets=[RunTarget(profile_id=ocupada, instance_ids=["android-01"])]))
    await harness.wait_run(run.id, ("planned",))
    s = await _orq(harness).sugerir(RunTargetsSuggestBody(command="mande uma receita de culinária para a amiga"))
    assert [e.profile_id for e in s.escolhidas] == [livre]


async def test_a_quantidade_do_texto_manda_ate_o_teto(harness: Harness) -> None:
    for nome, iid in (("Ana", "android-01"), ("Bia", "android-02"), ("Cris", "android-03")):
        _persona(harness, nome, iid, CATOLICA)
    s = await _orq(harness).sugerir(RunTargetsSuggestBody(command="duas personas mandam bom dia para a família"))
    assert len(s.escolhidas) == 2 and len({t.instance_id for t in s.targets}) == 2
    s = await _orq(harness).sugerir(RunTargetsSuggestBody(command="3 pessoas mandam bom dia", max_personas=2))
    assert len(s.escolhidas) == 2


async def test_texto_que_diz_quem_faz_vai_pela_previa_sem_ia(harness: Harness) -> None:
    lucas = _persona(harness, "Lucas", "android-02", CATOLICA)
    s = await _orq(harness).sugerir(RunTargetsSuggestBody(command="peça para o Lucas abrir o QA Messenger"))
    assert s.modo == "texto" and [(t.instance_id, t.profile_id, t.origem) for t in s.targets] == [
        ("android-02", lucas, "texto")]
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
        descartadas=[], nao_avaliaveis=[], alerta_conduta="", perguntas=[], resumo=""), req)
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
