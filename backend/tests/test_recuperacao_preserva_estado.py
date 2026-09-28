"""Recuperação automática que não destrói o estado (execuções r-20260928165254-e31953 e r-20260928195344-02ee9e).

No android-06 (2 vCPU, convidado saturado) a IA ocupou só 4,5% do tempo; o resto foi o nosso código transformando
lentidão em falha. A recuperação gravava `_restart_app` e fazia force-stop sem olhar se o app estava vivo na frente
nem por que a etapa falhou — um Instagram vivo, com a folha de comentários aberta, foi encerrado — e o plano revisado
cortava na fronteira do efeito comprovado (o LIKE): a v3 nasceu `[open_comments_1 depends_on [], comment_1]`, sem o
caminho até a publicação, sem `commit_guard` e sem `bindings.content`, sobre um app encerrado. Seguiram 448,7 s de
partidas a frio com ANR até "Tempo total do objetivo esgotado".

Todas as provas daqui são SIMULADAS: aparelho falso (`FakeQaDevice`) e provedor simulado; a falha é injetada no
`run_step` do executor, com as mensagens reais que ele produz.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

import pytest

from app.db import loads
from app.models import Plan, PlannerInfo, PlanStep, Postcondition, StepDTO
from app.taskqueue.executor import Outcome, StepOutcome
from app.taskqueue.projecao import EstatisticaDeAcao, Faixa

from .conftest import Harness

PRAZO = "Tempo da etapa esgotado (180s)."


def _falhar(monkeypatch: pytest.MonkeyPatch, harness: Harness, chave: str, detalhe: str, *, vezes: int = 1,
            antes: Callable[[StepDTO], None] | None = None) -> None:
    """A etapa `chave` devolve `failed` com `detalhe` nas `vezes` primeiras vezes, sem tocar no aparelho."""
    assert harness.state is not None
    executor = harness.state.scheduler.executor
    original = executor.run_step
    restantes = [vezes]

    async def run_step(**kw: object) -> StepOutcome:
        step = kw["step"]
        assert isinstance(step, StepDTO)
        if step.key == chave and restantes[0] > 0:
            if antes is not None and restantes[0] == vezes:
                antes(step)
            restantes[0] -= 1
            return StepOutcome(Outcome.failed, detalhe)
        return await original(**kw)  # type: ignore[arg-type]

    monkeypatch.setattr(executor, "run_step", run_step)


def _versoes(harness: Harness, run_id: str) -> list[tuple[int, str, list[dict[str, object]]]]:
    assert harness.state is not None
    return [(int(r["version"]), str(r["reason"]), loads(r["steps"], []))
            for r in harness.state.db.query(
                "SELECT version, reason, steps FROM plan_versions WHERE objective_id=? ORDER BY version",
                (f"{run_id}:android-01",))]


def _recuperacao(harness: Harness, run_id: str) -> list[dict[str, object]]:
    revisoes = [passos for _, motivo, passos in _versoes(harness, run_id) if motivo.startswith("Recuperação automática")]
    assert len(revisoes) == 1, _versoes(harness, run_id)
    return revisoes[0]


async def test_app_vivo_na_frente_e_falha_de_prazo_retoma_da_tela_atual_sem_encerrar(
        harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    """(1) A conversa está aberta e o app vivo na frente; a etapa estoura o prazo. Encerrar o app jogaria fora a tela
    certa e, num convidado saturado, pagaria partidas a frio com ANR — a recuperação retoma de onde está."""
    _falhar(monkeypatch, harness, "compose_message", PRAZO)
    run = await harness.wait_run(harness.run(["android-01"]).id, timeout=60)
    fake = harness.fakes["android-01"]

    assert "force_stop" not in fake.calls
    assert run.status == "completed" and len(fake.messages) == 1
    # Só o que falta: a navegação comprovada (abrir o app, a conta, a conversa) não é refeita da tela atual.
    assert [s["key"] for s in _recuperacao(harness, run.id)] == ["compose_message", "send_message", "verify_sent"]
    assert harness.state is not None
    textos = [r["message"] for r in harness.state.db.query(
        "SELECT message FROM events WHERE run_id=? AND kind='decision'", (run.id,))]
    assert any("retomando da tela atual" in t for t in textos), textos


async def test_corte_no_efeito_comprovado_reinclui_a_navegacao_sem_repetir_o_efeito(
        harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    """(2) O envio (efeito externo) já foi comprovado e a etapa seguinte falhou de um jeito que pede o app encerrado.
    Recomeçar do app fechado precisa do caminho até a conversa — sem ele a etapa nasce órfã, como a v3 da e31953 —
    mas o efeito comprovado NUNCA volta ao plano: no Instagram, um LIKE repetido DESCURTE."""
    _falhar(monkeypatch, harness, "verify_sent", "Pós-condição não comprovada: o status da mensagem não aparece.")
    run = await harness.wait_run(harness.run(["android-01"]).id, timeout=60)
    fake = harness.fakes["android-01"]

    passos = _recuperacao(harness, run.id)
    chaves = [s["key"] for s in passos]
    assert "send_message" not in chaves and len(fake.messages) == 1      # o efeito não se repete
    assert {"open_app", "confirm_account", "open_conversation"} <= set(chaves), chaves
    verificar = next(s for s in passos if s["key"] == "verify_sent")
    # quem dependia do efeito comprovado herda o que o precedia, e não fica solto no ar
    assert verificar["depends_on"] == ["compose_message"], verificar
    assert "force_stop" in fake.calls
    # (e) o evento diz se o app estava vivo quando foi encerrado
    assert harness.state is not None
    eventos = [loads(r["data"], {}) for r in harness.state.db.query(
        "SELECT data FROM events WHERE run_id=? AND kind='decision'", (run.id,))]
    encerramentos = [d for d in eventos if "app_vivo" in d]
    assert encerramentos and encerramentos[0]["app_vivo"] is True, eventos
    assert encerramentos[0]["package"] == "com.pocqa.messenger"


async def test_formato_da_e31953_like_comprovado_fica_fora_e_o_caminho_ate_o_post_volta(harness: Harness) -> None:
    """(2) no formato exato da e31953: perfil → post → LIKE (comprovado) → comentários → comentar (falhou).
    Com o app encerrado, a revisão refaz perfil e post e liga os comentários ao post; o LIKE nunca volta. Da tela
    atual (app vivo), só o que falta — nada de navegação comprovada."""
    assert harness.state is not None
    db = harness.state.db
    post = Postcondition(kind="model_judged", value="x", description="y")
    formato = [("open_profile_1", [], False, "succeeded"), ("open_post_1", ["open_profile_1"], False, "succeeded"),
               ("like_1", ["open_post_1"], True, "succeeded"), ("open_comments_1", ["like_1"], False, "succeeded"),
               ("comment_1", ["open_comments_1"], True, "failed")]
    plano = Plan(summary="curtir e comentar", planner=PlannerInfo(provider="fake", model="t", simulated=True),
                 steps=[PlanStep(key=k, title=k, goal="g", depends_on=d, side_effect=se, postcondition=post)
                        for k, d, se, _ in formato])
    db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at,"
               " plan) VALUES ('run-e','ke','curta e comente','execute','running',1,'[\"android-01\"]',"
               "'2026-09-28T16:52:54Z',?)", (plano.model_dump_json(),))
    db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version, parameters)"
               " VALUES ('run-e:android-01','run-e','android-01','running',1,'{}')")
    for seq, (chave, deps, se, status) in enumerate(formato, start=1):
        db.execute(
            "INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal,"
            " depends_on, side_effect, commit_guard, postcondition, timeout_s, max_attempts, status)"
            " VALUES (?,'run-e','run-e:android-01','android-01',1,?,?,?,'g',?,?,'[]',"
            "'{\"kind\":\"model_judged\",\"value\":\"x\",\"description\":\"y\"}',180,1,?)",
            (f"run-e:android-01:v1:{chave}", seq, chave, chave, json.dumps(deps), int(se), status))
    run = db.one("SELECT * FROM runs WHERE id='run-e'")
    sched = harness.state.scheduler

    encerrado = {s.key: s.depends_on for s in sched.recovery_steps(run, "run-e:android-01")}
    assert encerrado == {"open_profile_1": [], "open_post_1": ["open_profile_1"],
                         "open_comments_1": ["open_post_1"], "comment_1": ["open_comments_1"]}
    vivo = {s.key: s.depends_on for s in sched.recovery_steps(run, "run-e:android-01", da_tela_atual=True)}
    assert vivo == {"comment_1": []}


async def test_texto_escrito_e_guarda_sobrevivem_a_revisao(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    """(3) O texto que a persona escreveu (e que alguém pode ter aprovado) mora na LINHA da etapa, não em `runs.plan`.
    Revisar a partir do plano sem carregar isso gerava outro texto — ou comprometia sem a guarda que o protege."""
    assert harness.state is not None
    db = harness.state.db
    conteudo = "Que foto linda!"

    def escrever(step: StepDTO) -> None:
        # O mesmo que a porta de rascunho grava (`definir_texto` + `guardar_rascunho`), direto na linha da etapa.
        linha = db.one("SELECT bindings, commit_guard FROM steps WHERE id=?", (step.id,))
        bindings = {**(loads(linha["bindings"], {}) or {}), "content": conteudo}
        guardas = [*(loads(linha["commit_guard"], []) or []), conteudo]
        db.execute("UPDATE steps SET bindings=?, commit_guard=?, draft_meta=? WHERE id=?",
                   (json.dumps(bindings), json.dumps(guardas), json.dumps({"rationale": "elogio"}), step.id))

    _falhar(monkeypatch, harness, "send_message", "Pré-condições do efeito externo não foram atendidas.", vezes=2,
            antes=escrever)
    run = await harness.wait_run(harness.run(["android-01"]).id, timeout=60)

    # objetivo que falha deixa a execução "concluída com problemas" (nunca "concluída")
    assert run.status == "completed_with_issues" and not harness.fakes["android-01"].messages
    assert db.scalar("SELECT status FROM objectives WHERE id=?", (f"{run.id}:android-01",)) == "failed"
    linhas = {int(r["plan_version"]): r for r in db.query(
        "SELECT plan_version, bindings, commit_guard, draft_meta FROM steps WHERE objective_id=? AND key='send_message'",
        (f"{run.id}:android-01",))}
    assert len(linhas) == 2, linhas
    antes, depois = linhas[1], linhas[2]
    assert (loads(depois["bindings"], {}) or {}).get("content") == conteudo
    assert conteudo in loads(depois["commit_guard"], [])
    assert loads(depois["commit_guard"], []) == loads(antes["commit_guard"], [])     # guardas do plano também ficam
    assert loads(depois["draft_meta"], {}) == {"rationale": "elogio"}              # a porta não reescreve o texto


async def test_revisao_que_nao_cabe_no_prazo_falha_com_o_motivo(harness: Harness,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    """(4) O histórico medido diz que refazer o que falta leva muito mais do que o prazo que resta. Começar a revisão
    seria girar até "Tempo total do objetivo esgotado" (448,7 s na e31953): falha agora, dizendo por quê."""
    assert harness.state is not None
    lento = EstatisticaDeAcao(app="qa-messenger", acao="*", amostras=50, chamadas=Faixa(100.0, 100.0),
                              segundos=Faixa(4000.0, 6000.0), usd=Faixa(0.0, 0.0))
    monkeypatch.setattr(harness.state.scheduler.executor.historico, "de", lambda app, acao: lento)
    _falhar(monkeypatch, harness, "compose_message", PRAZO)
    run = await harness.wait_run(harness.run(["android-01"]).id, timeout=60)

    assert run.status == "completed_with_issues"
    assert not [v for v in _versoes(harness, run.id) if v[1].startswith("Recuperação automática")]
    assert "force_stop" not in harness.fakes["android-01"].calls
    obj = harness.state.db.one("SELECT status, blocked_reason FROM objectives WHERE id=?", (f"{run.id}:android-01",))
    motivo = str(obj["blocked_reason"])
    assert obj["status"] == "failed"
    assert PRAZO in motivo and "restam" in motivo and "12000" in motivo, motivo


def test_falhas_que_preservam_a_tela_sao_mensagens_que_o_executor_produz() -> None:
    """A classificação é pelo começo da mensagem do executor. Se ele mudar a frase, este teste avisa — senão a
    recuperação voltaria, calada, a encerrar o app vivo."""
    from app.taskqueue.scheduler import FALHAS_QUE_PRESERVAM_A_TELA

    fonte = (Path(__file__).resolve().parents[1] / "app" / "taskqueue" / "executor.py").read_text(encoding="utf-8")
    for prefixo in FALHAS_QUE_PRESERVAM_A_TELA:
        assert prefixo in fonte, prefixo
