"""31.154: a operação com N agentes (migração 124, adendo v1.94): criar, ler, cancelar, liberar, teto de custo e a rota.

`simulated`: harness com aparelhos falsos na porta 5640 e provedor simulado. O app dos alvos que ganham execução é o do
aparelho falso (o modelo da operação não tem ramo por app); a prova real no Instagram é a da onda de 07/10.

O que se prova:
- cada alvo = persona + conta + aparelho numa execução PRÓPRIA (marcada com `operacao_id`, chave `op:`, teto `preparar`);
- quem não tem conta para em `conta`, quem não tem sessão para em `sessao`, e o resto segue; a capacidade conta tudo;
- a mesma chave devolve a mesma operação, e com outro corpo é recusada;
- cancelar cancela as execuções dos alvos; liberar exige o eco do texto e respeita o limite de ações executadas;
- o teto de custo da operação barra a IA de qualquer alvo dela (`AIError(kind="budget", motivo="operacao")`);
- `registrar_estagio` marca uma vez e não faz nada fora de operação; a rota HTTP responde, com o filtro em `/api/runs`.
"""
from __future__ import annotations

import secrets as pysecrets
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

from app.main import create_app
from app.models import ProfileCreate, SessionStatus
from app.modules.operacoes.infrastructure.estagios import registrar_estagio
from app.modules.operacoes.infrastructure.servico import (AlvoPedido, OperacaoError, PedidoDeOperacao,
                                                          ServicoDeOperacoes, _motivo)
from app.planning.provider import AIError
from app.planning.routing import RoutingProvider
from app.util import now_iso

from .conftest import COMMAND, Harness

pytestmark = pytest.mark.asyncio
APP = "qa-messenger"


def _persona(h: Harness, nome: str, instance: str | None = None) -> str:
    assert h.state is not None
    return h.state.social.create_profile(ProfileCreate(
        username=f"{nome.lower()}.{pysecrets.token_hex(3)}", instance_id=instance, first_name=nome,
        last_name="Teste")).id


def _conta(h: Harness, pid: str, handle: str, sessao_em: str | None = None) -> str:
    assert h.state is not None
    repo = h.state.social_repo
    conta = repo.create_account(pid, app_id=APP, handle=handle)
    if sessao_em:
        repo.set_account_session(pid, conta, sessao_em, status=SessionStatus.session_ready, verified_at=now_iso())
    return conta


def _servico(h: Harness) -> ServicoDeOperacoes:
    st = h.state
    assert st is not None
    return ServicoDeOperacoes(st.db, st.runs, st.social_repo, st.approval_service, st.settings.get, st.bus,
                              st.cfg.file.ai.prices)


def _pedido(alvos: list[AlvoPedido], chave: str = "teste-op-0001", **kw: Any) -> PedidoDeOperacao:
    return PedidoDeOperacao(command=kw.pop("command", COMMAND), app_id=kw.pop("app_id", APP), alvos=alvos,
                            acao_final=kw.pop("acao_final", "preparar"), idempotency_key=chave,
                            max_usd=kw.pop("max_usd", 1.0), **kw)


def _alvo(op: dict[str, Any], pid: str) -> dict[str, Any]:
    return next(a for a in op["alvos"] if a["profile_id"] == pid)


async def test_cada_alvo_e_uma_execucao_propria_e_quem_nao_tem_conta_ou_sessao_para_com_o_motivo(
        harness: Harness) -> None:
    st = harness.state
    assert st is not None
    sem_conta = _persona(harness, "Ana")
    sem_sessao = _persona(harness, "Bia")
    _conta(harness, sem_sessao, "qa-user-02")
    pronta = _persona(harness, "Cris", "android-01")
    _conta(harness, pronta, "qa-user-01", sessao_em="android-01")
    op = _servico(harness).criar(_pedido([AlvoPedido(sem_conta), AlvoPedido(sem_sessao), AlvoPedido(pronta)]))
    a, b, c = _alvo(op, sem_conta), _alvo(op, sem_sessao), _alvo(op, pronta)
    assert (a["estado"], a["motivo"], a["parou_em"], a["run_id"]) == ("bloqueado", "sem conta", "conta", None)
    assert (b["estado"], b["motivo"], b["parou_em"], b["run_id"]) == ("bloqueado", "sem sessão", "sessao", None)
    assert c["run_id"] and c["estado"] in ("pendente", "em_curso") and c["instance_id"] == "android-01"
    run = st.db.one("SELECT * FROM runs WHERE id=?", (c["run_id"],))
    assert run["operacao_id"] == op["id"] and run["teto_de_autonomia"] == "preparar"
    assert run["idempotency_key"].startswith(f"op:{op['id']}:") and pronta not in run["idempotency_key"]
    cap = op["capacidade"]
    assert (cap["solicitados"], cap["contas_existentes"], cap["sessoes_validas"], cap["bloqueadas"]) == (3, 2, 1, 2)
    assert cap["motivos"] == {"sem conta": 1, "sem sessão": 1}
    assert op["status"] == "em_curso" and op["acao_final"] == "preparar"
    # a execução do alvo conta como do sistema: nada de aviso individual por alvo
    assert st.repo.run_summary(run).operacao_id == op["id"]


async def test_mesma_chave_mesma_operacao_e_outro_corpo_e_recusado(harness: Harness) -> None:
    pid = _persona(harness, "Davi")
    s = _servico(harness)
    op = s.criar(_pedido([AlvoPedido(pid)]))
    assert s.criar(_pedido([AlvoPedido(pid)]))["id"] == op["id"]
    with pytest.raises(OperacaoError) as exc:
        s.criar(_pedido([AlvoPedido(pid)], max_usd=2.0))
    assert exc.value.code == "chave_em_uso"


async def test_cancelar_cancela_as_execucoes_dos_alvos(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    pid = _persona(harness, "Eva", "android-02")
    _conta(harness, pid, "qa-user-03", sessao_em="android-02")
    s = _servico(harness)
    op = s.criar(_pedido([AlvoPedido(pid)], chave="teste-op-cancelar"))
    run_id = _alvo(op, pid)["run_id"]
    depois = s.cancelar(op["id"])
    assert depois["status"] == "cancelada"
    assert st.db.one("SELECT cancel_requested FROM runs WHERE id=?", (run_id,))["cancel_requested"] == 1 or \
        st.db.one("SELECT status FROM runs WHERE id=?", (run_id,))["status"] == "cancelled"


async def test_o_teto_de_custo_da_operacao_barra_a_ia_de_qualquer_alvo(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    pid = _persona(harness, "Fabi", "android-03")
    _conta(harness, pid, "qa-user-04", sessao_em="android-03")
    op = _servico(harness).criar(_pedido([AlvoPedido(pid)], chave="teste-op-teto", max_usd=0.001))
    run_id = _alvo(op, pid)["run_id"]
    modelo = next(iter(st.cfg.file.ai.prices))
    st.db.execute("INSERT INTO ai_calls(ts, run_id, role, model, input_tokens, output_tokens, ok) VALUES (?,?,?,?,?,?,?)",
                  (now_iso(), run_id, "plan", modelo, 2_000_000, 100_000, 1))
    roteador = RoutingProvider(harness.cfg)
    roteador.attach(repo=st.repo, settings_getter=st.settings.get)
    with pytest.raises(AIError) as exc:
        roteador._budget(run_id)  # noqa: SLF001
    assert exc.value.kind == "budget" and exc.value.motivo == "operacao"


async def test_registrar_estagio_marca_uma_vez_e_fora_de_operacao_nao_faz_nada(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    pid = _persona(harness, "Gil", "android-01")
    _conta(harness, pid, "qa-user-05", sessao_em="android-01")
    op = _servico(harness).criar(_pedido([AlvoPedido(pid)], chave="teste-op-marca"))
    run_id = _alvo(op, pid)["run_id"]
    assert registrar_estagio(st.db, run_id, "conhecimento_recuperado") is True
    assert registrar_estagio(st.db, run_id, "conhecimento_recuperado") is False
    assert registrar_estagio(st.db, run_id, "resultado_verificado") is False       # não é marcável de fora
    avulsa = harness.run(["android-02"])
    assert registrar_estagio(st.db, avulsa.id, "conteudo_lido") is False


# ------------------------------------------------------------------ liberar (aprovações falsas)
@dataclass
class _Pedido:
    id: str
    status: str = "pending"
    texto: str = ""

    def to_dict(self) -> dict[str, object]:
        return {"id": self.id, "generated_content": self.texto}


@dataclass
class _Aprovacoes:
    pedidos: dict[str, _Pedido]
    decididos: list[str] = field(default_factory=list)

    @property
    def store(self) -> Any:
        return self

    def for_step(self, step_id: str) -> _Pedido | None:
        return self.pedidos.get(step_id)

    def na_tela(self, item: dict[str, object]) -> dict[str, object]:
        return item

    def decide(self, approval_id: str, verb: str, **_: Any) -> dict[str, object]:
        assert verb == "approve"
        self.decididos.append(approval_id)
        return {}


async def test_liberar_exige_o_texto_lido_e_respeita_o_limite_de_acoes_executadas(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    st.settings.update({"operacao_max_acoes_executadas": 1})
    pids = []
    for i, iid in enumerate(("android-01", "android-02")):
        pid = _persona(harness, f"Hugo{i}", iid)
        _conta(harness, pid, f"qa-user-1{i}", sessao_em=iid)
        pids.append(pid)
    op = _servico(harness).criar(_pedido([AlvoPedido(p) for p in pids], chave="teste-op-liberar"))
    pedidos = {pid: _Pedido(id=f"apr-{i}", texto=f"texto da persona {i}") for i, pid in enumerate(pids)}
    s = ServicoDeOperacoes(st.db, st.runs, st.social_repo, _Aprovacoes({}), st.settings.get, st.bus,  # type: ignore[arg-type]
                           st.cfg.file.ai.prices)
    # O pedido de aprovação da ação preparada de cada alvo (o que a porta grava depois do rascunho), sem rodar o plano.
    s._pedido_pendente = lambda alvo: pedidos.get(str(alvo["profile_id"])) if alvo else None  # type: ignore[method-assign]
    aprovacoes = s.aprovacoes
    out = s.liberar(op["id"], [(pids[0], "outro texto"), (pids[1], "texto da persona 1"),
                               (pids[0], "texto da persona 0")])
    assert out["liberados"] == [pids[1]]
    assert {r["motivo"] for r in out["recusados"]} == {"texto_divergente", "limite de ações executadas"}
    assert out["operacao"]["acao_final"] == "executar"
    assert aprovacoes.decididos == ["apr-1"]  # type: ignore[attr-defined]


# ------------------------------------------------------------------ a rota
async def test_rota_http_criar_ler_listar_filtrar_cancelar_liberar(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    pid = _persona(harness, "Iara", "android-01")
    _conta(harness, pid, "qa-user-21", sessao_em="android-01")
    app = create_app(harness.cfg, state=st)
    app.state.poc = st
    corpo = {"command": COMMAND, "app_id": APP, "alvos": [{"profile_id": pid}], "idempotency_key": "teste-op-http1",
             "max_usd": 0.5, "assunto": "o lançamento da coleção", "fontes": ["https://exemplo.com.br/noticia"]}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/operacoes", json={k: v for k, v in corpo.items() if k != "max_usd"})
        assert r.status_code == 422                                    # max_usd é obrigatório
        for fonte in ("http://inseguro", "https://exemplo.com.br/x?token=abc", "https://eu:senha@exemplo.com.br/"):
            r = await c.post("/api/operacoes", json={**corpo, "fontes": [fonte]})
            assert r.status_code == 422, fonte
        r = await c.post("/api/operacoes", json=corpo)
        assert r.status_code == 201, r.text
        op = r.json()
        assert op["assunto"] == "o lançamento da coleção" and op["fontes"] == ["https://exemplo.com.br/noticia"]
        r = await c.get(f"/api/operacoes/{op['id']}")
        assert r.status_code == 200 and r.json()["alvos"][0]["profile_id"] == pid
        r = await c.get("/api/operacoes")
        assert r.status_code == 200 and r.json()["items"][0]["id"] == op["id"]
        r = await c.get("/api/runs", params={"operacao_id": op["id"]})
        assert r.status_code == 200 and [x["operacao_id"] for x in r.json()["runs"]] == [op["id"]]
        r = await c.post(f"/api/operacoes/{op['id']}/liberar", json={"itens": [{"profile_id": pid, "texto": "x"}]})
        assert r.status_code == 200 and r.json()["recusados"][0]["motivo"] == "sem ação preparada"
        r = await c.post(f"/api/operacoes/{op['id']}/cancelar")
        assert r.status_code == 200 and r.json()["status"] == "cancelada"
        r = await c.post(f"/api/operacoes/{op['id']}/cancelar")
        assert r.status_code == 409 and r.json()["detail"]["code"] == "ja_encerrada"
        r = await c.get("/api/operacoes/nao-existe")
        assert r.status_code == 404 and r.json()["detail"]["code"] == "operacao_inexistente"


async def test_o_limite_conta_a_acao_ja_aprovada_e_uma_segunda_liberacao_nao_passa_do_teto(harness: Harness) -> None:
    """Achado do revisor-segredos (06/10): contar só a ação EXECUTADA deixava uma segunda liberação aprovar de novo antes de a
    primeira rodar."""
    st = harness.state
    assert st is not None
    st.settings.update({"operacao_max_acoes_executadas": 1})
    pids = []
    for i, iid in enumerate(("android-01", "android-02")):
        pid = _persona(harness, f"Jo{i}", iid)
        _conta(harness, pid, f"qa-user-3{i}", sessao_em=iid)
        pids.append(pid)
    op = _servico(harness).criar(_pedido([AlvoPedido(p) for p in pids], chave="teste-op-limite2"))
    st.db.execute("INSERT INTO pending_approvals(id, profile_id, run_id, capability, status, created_at)"
                  " VALUES (?,?,?,?,?,?)", ("apr-ja", pids[0], _alvo(op, pids[0])["run_id"], "CREATE_COMMENT",
                                            "approved", now_iso()))
    s = ServicoDeOperacoes(st.db, st.runs, st.social_repo, _Aprovacoes({}), st.settings.get, st.bus,  # type: ignore[arg-type]
                           st.cfg.file.ai.prices)
    s._pedido_pendente = lambda alvo: _Pedido(id="apr-2", texto="t") if alvo else None  # type: ignore[method-assign]
    out = s.liberar(op["id"], [(pids[1], "t")])
    assert out["liberados"] == [] and out["recusados"] == [{"profile_id": pids[1], "motivo": "limite de ações executadas"}]
    assert out["operacao"]["acao_final"] == "preparar"          # nada liberado: a operação não muda


async def test_credencial_no_assunto_ou_na_fonte_e_recusada_e_o_motivo_sai_redigido(harness: Harness) -> None:
    pid = _persona(harness, "Kai")
    s = _servico(harness)
    for kw in ({"assunto": "entre com Senha: segredo123 e leia"}, {"fontes": ("https://x.com.br/Senha: segredo123",)}):
        with pytest.raises(OperacaoError) as exc:
            s.criar(_pedido([AlvoPedido(pid)], chave=f"teste-op-cred-{len(kw)}{list(kw)[0]}", **kw))
        assert exc.value.code == "credencial_no_comando"
    assert "segredo123" not in (_motivo("a tela pediu Senha: segredo123 de novo") or "")


async def test_custo_por_alvo_e_o_da_execucao_dele(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    """`custo_usd` do alvo = `spent_usd` da execução dele (nulo sem execução); com texto, vem também em `resultado`."""
    st = harness.state
    assert st is not None
    com = _persona(harness, "Gabi", "android-01")
    _conta(harness, com, "qa-user-05", sessao_em="android-01")
    sem = _persona(harness, "Hugo")
    servico = _servico(harness)
    op = servico.criar(_pedido([AlvoPedido(com), AlvoPedido(sem)], chave="teste-op-custo-alvo"))
    run_id = _alvo(op, com)["run_id"]
    modelo = next(iter(st.cfg.file.ai.prices))
    st.db.execute("INSERT INTO ai_calls(ts, run_id, role, model, input_tokens, output_tokens, ok) VALUES (?,?,?,?,?,?,?)",
                  (now_iso(), run_id, "plan", modelo, 10_000, 1_000, 1))
    monkeypatch.setattr(ServicoDeOperacoes, "_resultado", lambda self, a, efeito, marcas: {"texto": "rascunho"})
    lido = servico.ler(op["id"])
    alvo_com, alvo_sem = _alvo(lido, com), _alvo(lido, sem)
    from app.planning import costs
    esperado = round(costs.spent_usd(st.db, st.cfg.file.ai.prices, run_id=run_id), 4)
    assert esperado > 0 and alvo_com["custo_usd"] == esperado and alvo_com["resultado"]["custo_usd"] == esperado
    assert alvo_sem["custo_usd"] is None
    assert lido["custo"]["total_usd"] == esperado                       # a soma dos alvos é o total da operação


async def test_conta_com_sessao_em_dois_aparelhos_executa_so_no_vinculo_principal(harness: Harness) -> None:
    """Sem `instance_id`, a conta com sessão em dois aparelhos vai ao vínculo PRINCIPAL da persona, não à sessão mais
    recente; sem sessão no principal, para com o motivo. Com `instance_id`, vale o pedido (ramo de sempre)."""
    st = harness.state
    assert st is not None
    repo = st.social_repo
    no_principal = _persona(harness, "Ivo", "android-02")              # principal: android-02
    conta = _conta(harness, no_principal, "qa-user-06", sessao_em="android-02")
    repo.set_account_session(no_principal, conta, "android-01", status=SessionStatus.session_ready,
                             verified_at=now_iso())                      # a mais recente é a do android-01
    fora = _persona(harness, "Juca", "android-03")                      # principal: android-03, sem sessão lá
    conta_fora = _conta(harness, fora, "qa-user-07", sessao_em="android-01")
    repo.set_account_session(fora, conta_fora, "android-02", status=SessionStatus.session_ready, verified_at=now_iso())
    op = _servico(harness).criar(_pedido([AlvoPedido(no_principal), AlvoPedido(fora)], chave="teste-op-principal"))
    a, b = _alvo(op, no_principal), _alvo(op, fora)
    assert a["instance_id"] == "android-02" and a["run_id"]
    assert (b["estado"], b["motivo"], b["parou_em"], b["run_id"]) == (
        "bloqueado", "sessão fora do aparelho principal", "sessao", None)
