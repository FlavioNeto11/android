"""31.284 (ADR-087): a validação ponta a ponta da conta planejada, com provedor SIMULADO.

O cenário do pedido do dono (08/10): três personas sem Outlook, o refinador, a preparação da credencial, o cadastro externo, a
verificação, a falha e a retomada, e a regressão do que já existia. Nenhuma conta real em nenhum serviço: o "provedor" é uma
classe do teste que decide, por persona, se a verificação chega, se falha ou se a pessoa desiste. A parte que o provedor
real faria (CAPTCHA, código, e-mail) é da pessoa (ADR-009), e o provedor simulado só devolve o desfecho.

Prova `simulated`: harness na porta 5640; o modelo do refinador é um dublê que ainda faz a pergunta circular de 08/10.
O que NÃO está aqui: o percurso no navegador do painel (31.283, Portal) e o cadastro com aparelho real (`not_run`).
"""
from __future__ import annotations

import json
import secrets as pysecrets
from dataclasses import dataclass, field

import httpx
import pytest
from pydantic import SecretStr

from app.main import create_app
from app.models import ProfileAccountCreate, ProfileCreate
from app.modules.execution.domain.command_refinement import CommandRefinement, RefineQuestion, RefineRequest
from app.modules.identity.application.available_data import common_data
from app.planning.provider import Usage
from app.taskqueue.assistente import CommandRefineBody, ComandoAssistido

from .conftest import Harness

OUTLOOK = "outlook"
PACOTE = "com.microsoft.office.outlook"
COMANDO = ("Pegue 3 personas que ainda não têm Outlook cadastrado e vamos guiá-las para criar contas no Outlook, "
           "usando os dados disponíveis de cada persona.")
PERGUNTA_CIRCULAR = RefineQuestion(
    field="senha_outlook", question="A senha da nova conta Outlook já está guardada na persona ou será definida por vocês?",
    options=["Já está guardada na conta da persona", "Será guardada na aba Contas antes de rodar"])
DONO = "painel:teste"


@dataclass
class ProvedorSimulado:
    """O "Outlook" do teste: devolve o desfecho de cada cadastro. Nunca toca a rede nem uma conta real."""

    roteiro: dict[str, list[str]]                        # persona -> desfechos em ordem: "verificado" | "falha"
    chamadas: list[tuple[str, str]] = field(default_factory=list)

    def desfecho(self, persona: str) -> str:
        self.chamadas.append((persona, "verificacao"))
        return self.roteiro[persona].pop(0)


class ClienteGravado(httpx.AsyncClient):
    """Guarda o corpo de TODA resposta HTTP: a varredura final procura a senha em tudo o que o painel chegou a ler."""

    def __init__(self, *a: object, **kw: object) -> None:
        super().__init__(*a, **kw)                       # type: ignore[arg-type]
        self.vistas: list[str] = []

    async def request(self, *a: object, **kw: object) -> httpx.Response:        # type: ignore[override]
        r = await super().request(*a, **kw)             # type: ignore[arg-type]
        self.vistas.append(r.text)
        return r


def _cliente(h: Harness) -> ClienteGravado:
    app = create_app(h.cfg, state=h.state)
    app.state.poc = h.state
    return ClienteGravado(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _app_outlook(h: Harness) -> None:
    if not h.state.db.scalar("SELECT id FROM apps WHERE id=?", (OUTLOOK,)):
        h.state.db.execute("INSERT INTO apps(id, name, package, activity, builtin) VALUES (?,?,?,?,0)",
                           (OUTLOOK, "Outlook", PACOTE, "Main"))


def _personas(h: Harness, n: int = 3) -> list[str]:
    nomes = ["Ana", "Bruno", "Carla", "Davi"][:n]
    return [h.state.social.create_profile(ProfileCreate(username=f"p.{pysecrets.token_hex(3)}", first_name=nm,
                                                        last_name="Teste", birth_date="1990-05-04")).id
            for nm in nomes]


def _modelo(h: Harness, monkeypatch: pytest.MonkeyPatch, vistos: list[RefineRequest]) -> None:
    async def refine(req: RefineRequest) -> tuple[CommandRefinement, Usage]:
        vistos.append(req)
        return (CommandRefinement(command=f"Objetivo: {req.command}\nApp ou site: Outlook", summary="x",
                                  questions=[PERGUNTA_CIRCULAR], ready=False, notes=[]),
                Usage(calls=1, role="plan", model="simulado"))

    monkeypatch.setattr(h.state.runs.provider, "refine_command", refine, raising=False)


def _senha_do_cofre(h: Harness, aid: str) -> str:
    ref = h.state.db.scalar("SELECT secret_ref FROM account_credentials WHERE account_id=?", (aid,))
    return h.state.social.secrets.get_secret(ref)


async def _evento(c: httpx.AsyncClient, pid: str, aid: str, evento: str, esperado: str, **extra: object) -> dict:
    r = await c.post(f"/api/instagram/profiles/{pid}/accounts/{aid}/provisioning",
                     json={"evento": evento, "estado_esperado": esperado, **extra})
    assert r.status_code == 200, (evento, r.text)
    return r.json()


async def test_tres_personas_sem_outlook_do_refinador_ate_a_conta_confirmada(harness: Harness,
                                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    _app_outlook(harness)
    ana, bruno, carla = _personas(harness)
    nomes = {ana: "Ana Teste", bruno: "Bruno Teste", carla: "Carla Teste"}
    provedor = ProvedorSimulado({ana: ["verificado"], bruno: ["falha", "verificado"], carla: ["falha"]})
    vistos: list[RefineRequest] = []
    _modelo(harness, monkeypatch, vistos)
    assistente = ComandoAssistido(harness.state.runs)
    async with _cliente(harness) as c:
        # (1) o sistema identifica a ausência e (7/8) o refinador não repete a pergunta
        r = await assistente.refinar(CommandRefineBody(command=COMANDO, profile_ids=[ana, bruno, carla]))
        assert r.questions == [] and not r.ready
        assert sorted((a.persona_id, a.estado) for a in r.acoes_de_conta) == sorted(
            [(ana, "sem_conta"), (bruno, "sem_conta"), (carla, "sem_conta")])
        assert all(a.acoes[0] == "preparar_credencial" and a.host is None for a in r.acoes_de_conta)
        conta_de: dict[str, str] = {}
        for acao in r.acoes_de_conta:
            pid = acao.persona_id
            # (3) sugere o identificador pelos dados da persona; (4) a pessoa edita um deles
            sug = (await c.get(f"/api/instagram/profiles/{pid}/accounts/handle-suggestions",
                               params={"app_id": acao.app_id})).json()["suggestions"]
            assert sug and sug[0]["handle"].startswith(nomes[pid].split()[0].lower())
            desejado = sug[0]["handle"] if pid != bruno else "bruno.escolhido.90"
            # (2) abre a preparação / (6) registra como planejada
            plan = await c.post(f"/api/instagram/profiles/{pid}/accounts/planned",
                                json={"app_id": acao.app_id, "desired_handle": desejado})
            assert plan.status_code == 201 and plan.json()["provisioning"]["state"] == "planejada"
            conta_de[pid] = plan.json()["id"]
            # (5) gera a senha, independente por persona, direto no cofre
            prep = await c.post(f"/api/instagram/profiles/{pid}/accounts/{conta_de[pid]}/credential/prepare",
                                json={"modo": "gerar", "consent": True})
            assert prep.status_code == 200 and prep.json()["provisioning"]["state"] == "credencial_preparada"
        senhas = {pid: _senha_do_cofre(harness, aid) for pid, aid in conta_de.items()}
        assert len(set(senhas.values())) == 3 and all(len(s) >= 20 for s in senhas.values())

        # (7) volta ao refinador: a pendência está resolvida e o plano é coerente, sem repetir a pergunta
        vistos.clear()
        depois = await assistente.refinar(CommandRefineBody(command=COMANDO, profile_ids=[ana, bruno, carla]))
        assert depois.acoes_de_conta == [] and depois.questions == [] and depois.ready
        assert sorted(vistos[0].contas) == sorted(f"{n}, Outlook: credencial_preparada" for n in nomes.values())
        for pid in nomes:
            dados = {d.name for d in common_data(harness.state.runs.dados, [pid])}
            assert {"conta_outlook_usuario", "conta_outlook_senha"} <= dados

        # (9) cadastro externo, verificação, falha e retomada; o provedor decide o desfecho
        for pid, aid in conta_de.items():
            await _evento(c, pid, aid, "iniciar_cadastro", "credencial_preparada")
            await _evento(c, pid, aid, "enviado", "aguardando_cadastro_externo")
        # Ana: a verificação chega e a sessão observada mostra o usuário desejado
        assert provedor.desfecho(ana) == "verificado"
        desejado_ana = harness.state.db.scalar("SELECT desired_handle FROM profile_accounts WHERE id=?", (conta_de[ana],))
        harness.state.db.execute(
            "INSERT INTO account_sessions(account_id, instance_id, status, observed_handle, verified_at, updated_at)"
            " VALUES (?,?,?,?,?,?)", (conta_de[ana], "android-01", "session_ready", desejado_ana,
                                      "2026-10-10T12:00:00Z", "2026-10-10T12:00:00Z"))
        conf = await _evento(c, pid=ana, aid=conta_de[ana], evento="confirmar", esperado="aguardando_verificacao",
                             evidencia={"tipo": "sessao", "sessao_id": "android-01"})
        assert conf["provisioning"]["state"] == "confirmada" and conf["handle"] == desejado_ana
        assert conf["provisioning"]["authenticated"] is False       # sessão do aparelho 1 não é a vinculada
        # Bruno: falha na verificação, retoma do mesmo ponto e a pessoa confirma
        assert provedor.desfecho(bruno) == "falha"
        falha = await _evento(c, bruno, conta_de[bruno], "falhar", "aguardando_verificacao",
                              motivo="o código do e-mail não chegou")
        assert falha["provisioning"]["state"] == "falha" and falha["provisioning"]["resume_state"] == "aguardando_verificacao"
        assert _senha_do_cofre(harness, conta_de[bruno]) == senhas[bruno]               # nada se perdeu
        retomada = await _evento(c, bruno, conta_de[bruno], "retomar", "falha")
        assert retomada["provisioning"]["state"] == "aguardando_verificacao"
        assert provedor.desfecho(bruno) == "verificado"
        conf = await _evento(c, bruno, conta_de[bruno], "confirmar", "aguardando_verificacao",
                             evidencia={"tipo": "declarada", "handle_confirmado": "bruno.escolhido.90@outlook.test"})
        assert conf["handle"] == "bruno.escolhido.90@outlook.test" and conf["provisioning"]["evidence"]["kind"] == "declarada"
        # Carla: falha e a pessoa desiste; a conta e a senha somem do banco e do cofre
        assert provedor.desfecho(carla) == "falha"
        ref_carla = harness.state.db.scalar("SELECT secret_ref FROM account_credentials WHERE account_id=?", (conta_de[carla],))
        await _evento(c, carla, conta_de[carla], "falhar", "aguardando_verificacao", motivo="CAPTCHA recusado")
        cancel = await _evento(c, carla, conta_de[carla], "cancelar", "falha")
        assert cancel == {"removida": True, "credencial_removida": True}
        assert not harness.state.social.secrets.exists(ref_carla)
        assert harness.state.db.scalar("SELECT COUNT(*) FROM profile_accounts WHERE id=?", (conta_de[carla],)) == 0
        # a Carla volta a ser "sem conta" para o refinador: nada ficou pela metade
        vistos.clear()
        de_novo = await assistente.refinar(CommandRefineBody(command=COMANDO, profile_ids=[carla]))
        assert [a.estado for a in de_novo.acoes_de_conta] == ["sem_conta"]

        # (10) nenhuma senha à IA, em log, evento, tabela ou resposta que o painel leu
        for pid, senha in senhas.items():
            if pid == carla:
                continue
            for tabela in ("events", "ai_calls", "actions", "runs", "memory_items", "profile_accounts",
                           "account_credentials", "account_sessions"):
                for linha in harness.state.db.query(f"SELECT * FROM {tabela}"):             # noqa: S608
                    assert senha not in json.dumps(dict(linha), default=str), tabela
            assert all(senha not in corpo for corpo in c.vistas)
        for req in vistos:
            assert not any(s in json.dumps(req.__dict__, default=str) for s in senhas.values())
        assert provedor.chamadas == [(ana, "verificacao"), (bruno, "verificacao"), (bruno, "verificacao"),
                                     (carla, "verificacao")]


async def test_regressao_conta_existente_clone_consentimento_e_login_normal(harness: Harness,
                                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    """O que já funcionava continua igual: a conta criada pelo caminho de sempre nasce confirmada, o clone não herda o
    consentimento, a senha sem consentimento continua recusada e o refinador não acusa conta que já existe."""
    _app_outlook(harness)
    (pid,) = _personas(harness, 1)
    senha = "Tst-" + pysecrets.token_urlsafe(9)
    social = harness.state.social
    conta = social.add_account(pid, ProfileAccountCreate(app_id=OUTLOOK, handle="ana.antiga@outlook.test",
                                                         password=SecretStr(senha), consent=True), by=DONO)
    assert conta.provisioning.state == "confirmada" and conta.handle == "ana.antiga@outlook.test"
    assert conta.provisioning.actions == [] and conta.credential.consent_at and conta.provisioning.evidence is None
    # sem consentimento a senha segue recusada (409), e nada é gravado
    harness.state.db.execute("INSERT INTO apps(id, name, package, activity, builtin) VALUES ('outro','Outro',"
                             "'com.exemplo.outro','Main',0)")
    with pytest.raises(Exception) as erro:
        social.add_account(pid, ProfileAccountCreate(app_id="outro", handle="x", password=SecretStr(senha)), by=DONO)
    assert getattr(erro.value, "code", "") == "consentimento_de_credencial"
    # clonar de uma conta confirmada: a conta nova nasce SEM consentimento
    clone = social.add_account(pid, ProfileAccountCreate(app_id="outro", handle="y", clonar_de=conta.id), by=DONO)
    assert clone.credential_configured and clone.credential.consent_at is None
    assert clone.provisioning.state == "confirmada"
    # o refinador não acusa nem oferece ação para a conta que já existe
    vistos: list[RefineRequest] = []
    _modelo(harness, monkeypatch, vistos)
    r = await ComandoAssistido(harness.state.runs).refinar(CommandRefineBody(command=COMANDO, profile_ids=[pid]))
    assert r.acoes_de_conta == [] and vistos[0].contas == ["Ana Teste, Outlook: confirmada"]
    # a conta confirmada segue sendo conta real para o Automático
    async with _cliente(harness) as c:
        lista = (await c.get(f"/api/instagram/profiles/{pid}/accounts")).json()
        assert {a["id"]: a["provisioning"]["state"] for a in lista}[conta.id] == "confirmada"
        assert all(senha not in corpo for corpo in c.vistas)
