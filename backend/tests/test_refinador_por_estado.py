"""31.282 (ADR-087, adendo v1.132): o refinador conhece o ESTADO das contas e a senha deixa de ser pergunta.

O defeito de 08/10: "pegue personas sem Outlook e vamos guiá-las a criar contas" virava a pergunta "a senha da nova conta já
está guardada ou será definida?", e as duas respostas caíam no 409 `credencial_na_resposta`. Agora o servidor lê o estado de
cada par (persona, app de conta), põe só o estado no prompt, descarta a pergunta de credencial que o modelo ainda faça e
devolve `acoes_de_conta`, ações por id, sem texto livre.

Prova `simulated`: o refinador é um dublê que devolve a pergunta circular; nenhuma conta real em nenhum serviço.
"""
from __future__ import annotations

import json
import secrets as pysecrets

import httpx
import pytest
from pydantic import SecretStr

from app.main import create_app
from app.models import ProfileAccountCreate, ProfileCreate
from app.modules.execution.domain.command_refinement import (CommandRefinement, RefineQuestion, RefineRequest,
                                                            refine_system, refine_user)
from app.taskqueue.assistente import CommandRefineBody, ComandoAssistido

from .conftest import Harness

COMANDO = "Pegue as personas escolhidas e vamos guiá-las para criar contas no Outlook, com os dados de cada uma."
PERGUNTA_CIRCULAR = RefineQuestion(
    field="senha_outlook", question="A senha da nova conta Outlook já está guardada na persona ou será definida por vocês?",
    options=["Já está guardada na conta da persona", "Será guardada na aba Contas antes de rodar"])


def _app_outlook(h: Harness) -> None:
    if not h.state.db.scalar("SELECT id FROM apps WHERE id='outlook'"):
        h.state.db.execute("INSERT INTO apps(id, name, package, activity, builtin) VALUES "
                           "('outlook','Outlook','com.microsoft.office.outlook','Main',0)")


def _persona(h: Harness, nome: str = "Maria") -> str:
    return h.state.social.create_profile(ProfileCreate(username=f"p.{pysecrets.token_hex(3)}", first_name=nome,
                                                       last_name="Souza")).id


def _modelo(h: Harness, monkeypatch: pytest.MonkeyPatch, *, perguntas: list[RefineQuestion], pronto: bool = False,
            vistos: list[RefineRequest] | None = None) -> None:
    """O refinador devolve o que o teste manda e guarda o pedido que recebeu (o que o modelo VÊ)."""
    from app.planning.provider import Usage

    async def refine(req: RefineRequest) -> tuple[CommandRefinement, Usage]:
        if vistos is not None:
            vistos.append(req)
        return (CommandRefinement(command=f"Objetivo: {req.command}\nApp ou site: Outlook", summary="x",
                                  questions=list(perguntas), ready=pronto, notes=[]),
                Usage(calls=1, role="plan", model="simulado"))

    monkeypatch.setattr(h.state.runs.provider, "refine_command", refine, raising=False)


def _assistente(h: Harness) -> ComandoAssistido:
    return ComandoAssistido(h.state.runs)


async def test_persona_sem_outlook_a_pergunta_circular_some_e_vira_acao_estruturada(
        harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    _app_outlook(harness)
    pid = _persona(harness)
    vistos: list[RefineRequest] = []
    _modelo(harness, monkeypatch, perguntas=[PERGUNTA_CIRCULAR], vistos=vistos)
    r = await _assistente(harness).refinar(CommandRefineBody(command=COMANDO, profile_ids=[pid]))
    assert r.questions == [] and not r.ready                     # não há pergunta de senha; e ainda não está pronto
    assert len(r.acoes_de_conta) == 1
    a = r.acoes_de_conta[0]
    assert (a.persona_id, a.app_id, a.estado, a.host) == (pid, "outlook", "sem_conta", None)
    assert a.persona_nome == "Maria Souza"
    assert a.acoes == ["preparar_credencial", "abrir_contas_e_acesso", "continuar"] and a.reutilizavel_de == []
    assert any("não é pergunta" in n for n in r.notes)
    # o modelo viu só o ESTADO
    assert vistos and vistos[0].contas == [f"{a.persona_nome}, Outlook: sem_conta"]
    assert "senha" not in " ".join(vistos[0].contas).lower() and "@" not in " ".join(vistos[0].contas)


async def test_com_a_credencial_preparada_nao_ha_acao_e_fica_pronto(harness: Harness,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    _app_outlook(harness)
    pid = _persona(harness)
    async with _api(harness) as c:
        aid = (await c.post(f"/api/instagram/profiles/{pid}/accounts/planned",
                            json={"app_id": "outlook", "desired_handle": "maria.souza94"})).json()["id"]
        antes = await _assistente(harness).refinar(CommandRefineBody(command=COMANDO, profile_ids=[pid]))
        assert [x.estado for x in antes.acoes_de_conta] == ["planejada"]
        r = await c.post(f"/api/instagram/profiles/{pid}/accounts/{aid}/credential/prepare",
                         json={"modo": "gerar", "consent": True})
        assert r.status_code == 200
    vistos: list[RefineRequest] = []
    _modelo(harness, monkeypatch, perguntas=[PERGUNTA_CIRCULAR], vistos=vistos)
    depois = await _assistente(harness).refinar(CommandRefineBody(command=COMANDO, profile_ids=[pid]))
    assert depois.acoes_de_conta == [] and depois.questions == [] and depois.ready     # a pendência foi reavaliada
    assert vistos[0].contas == ["Maria Souza, Outlook: credencial_preparada"]


async def test_falha_e_reutilizacao_aparecem_na_acao(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    _app_outlook(harness)
    pid = _persona(harness)
    if not harness.state.db.scalar("SELECT id FROM apps WHERE id='chrome'"):
        harness.state.db.execute("INSERT INTO apps(id, name, package, activity, builtin) VALUES "
                                 "('chrome','Chrome','com.android.chrome','Main',0)")
    origem = harness.state.social.add_account(
        pid, ProfileAccountCreate(app_id="chrome", handle="qa", host="portal.exemplo.test",
                                  password=SecretStr("Tst-" + pysecrets.token_urlsafe(9)), consent=True),
        by="painel:teste").id
    async with _api(harness) as c:
        aid = (await c.post(f"/api/instagram/profiles/{pid}/accounts/planned", json={"app_id": "outlook"})).json()["id"]
        harness.state.db.execute("UPDATE profile_accounts SET provisioning_state='falha', resume_state='planejada' "
                                 "WHERE id=?", (aid,))
    _modelo(harness, monkeypatch, perguntas=[])
    r = await _assistente(harness).refinar(CommandRefineBody(command=COMANDO, profile_ids=[pid]))
    a = r.acoes_de_conta[0]
    assert a.estado == "falha" and "usar_credencial_existente" in a.acoes and a.acoes[-1] == "continuar"
    assert [(x.account_id, x.app_id) for x in a.reutilizavel_de] == [(origem, "chrome")]
    assert not r.ready


async def test_sem_persona_escolhida_so_a_nota_e_perguntas_comuns_ficam(harness: Harness,
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    _app_outlook(harness)
    comum = RefineQuestion(field="quantidade", question="Quantas contas por persona?", options=["1", "2"])
    _modelo(harness, monkeypatch, perguntas=[PERGUNTA_CIRCULAR, comum])
    r = await _assistente(harness).refinar(CommandRefineBody(command=COMANDO))
    assert [q.field for q in r.questions] == ["quantidade"] and r.acoes_de_conta == [] and not r.ready
    assert any("Contas e acesso" in n for n in r.notes)


async def test_so_a_pergunta_de_senha_e_o_modelo_diz_pronto_sem_personas_fica_pronto(
        harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    _app_outlook(harness)
    _modelo(harness, monkeypatch, perguntas=[PERGUNTA_CIRCULAR])
    r = await _assistente(harness).refinar(CommandRefineBody(command=COMANDO))
    assert r.questions == [] and r.ready and r.acoes_de_conta == []


async def test_a_rota_devolve_acoes_de_conta_e_nunca_a_senha(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    _app_outlook(harness)
    pid = _persona(harness)
    _modelo(harness, monkeypatch, perguntas=[PERGUNTA_CIRCULAR])
    async with _api(harness) as c:
        r = await c.post("/api/commands/refine", json={"command": COMANDO, "profile_ids": [pid]})
        assert r.status_code == 200, r.text
        corpo = r.json()
        assert corpo["questions"] == [] and corpo["ready"] is False
        assert set(corpo["acoes_de_conta"][0]) == {"persona_id", "persona_nome", "app_id", "app_nome", "host", "estado",
                                                   "acoes", "reutilizavel_de"}
        # o 409 de antes (resposta à pergunta circular) não acontece mais: não há pergunta a responder
        assert "credencial_na_resposta" not in r.text


def test_o_prompt_leva_o_bloco_de_contas_e_a_regra_de_que_senha_nao_e_pergunta() -> None:
    req = RefineRequest(command="faça X", contas=["Maria Souza, Outlook: sem_conta"])
    texto = refine_user(req)
    assert "Contas das personas escolhidas" in texto and "- Maria Souza, Outlook: sem_conta" in texto
    assert "Contas das personas escolhidas" not in refine_user(RefineRequest(command="faça X"))
    sistema = refine_system("")
    assert "Contas e senhas NUNCA são pergunta" in sistema and "credencial_preparada" in sistema


def _api(h: Harness) -> httpx.AsyncClient:
    app = create_app(h.cfg, state=h.state)
    app.state.poc = h.state
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def test_json_da_acao_nao_tem_campo_de_texto_livre() -> None:
    from app.modules.execution.domain.command_refinement import AcaoDeConta

    campos = AcaoDeConta.model_json_schema()["properties"]
    assert "texto" not in campos and "mensagem" not in campos and "pergunta" not in campos
    assert json.loads(AcaoDeConta(persona_id="p", app_id="a", estado="sem_conta").model_dump_json())["host"] is None
