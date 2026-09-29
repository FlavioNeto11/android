"""A tela onde a tentativa falhou, GRAVADA (item 22.3; ADR-054, pendência A2).

`attempts.failure_screen` existia desde a migração 055 e ninguém gravava. Agora:

- `executor.tela_da_falha` classifica a última observação pelo conhecimento DECLARADO do app da etapa (o `telas.yaml`
  do pacote): o nome da regra, ou o tipo do motor nas telas de verificação e de login (o vocabulário que a exclusão
  das lições lê); `None` quando a tela é desconhecida, de outro app ou de app sem conhecimento. Nunca texto da tela;
- o scheduler (`_run_guarded`) só usa uma árvore observada por ESTA tentativa — sem observação nova, nulo;
- `repository.finish_attempt` grava a tela no mesmo UPDATE do tipo, e só quando há tipo de falha.

O cuidado do backlog (a linha antiga sem tela não vira corrigida porque a falha passou a vir com tela) está em
`test_learning_backlog.py`.

Nível de prova: `simulated` (dublê do Instagram, banco de teste; nenhum aparelho, nenhuma IA).
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from app.automation.hierarchy import UiTree, parse_hierarchy
from app.models import AttemptStatus, StepDTO, StepResult, StepStatus
from app.modules.learning.domain.falhas import FailureKind
from app.modules.learning.domain.licoes import TELAS_EXCLUIDAS
from app.taskqueue.executor import Outcome, StepOutcome, tela_da_falha
from app.util import now_iso

from .conftest import Harness
from .fake_instagram import PKG, FakeInstagram

#: O que a pessoa digitou no campo de senha do dublê (valor falso): não pode sair da tela para coluna nenhuma.
DIGITADO = "digitado-no-campo-9f3"
ALVO_AUSENTE = "Alvo ausente: o botão de curtir não está na tela."


def _tela(screen: str, **kw: Any) -> UiTree:
    """Uma tela do dublê do Instagram (verificação, login, intersticial, launcher)."""
    return parse_hierarchy(FakeInstagram(account="eu.teste", screen=screen, **kw).page_source())


def _com_id(rid: str, cls: str = "android.widget.ImageView", pacote: str = PKG) -> UiTree:
    """Uma tela com UM elemento de id conhecido: as telas de dentro do app casam por id (o feed do dublê mostra a
    conta com `@` e casa como perfil — é do dublê, não do que se prova aqui)."""
    return parse_hierarchy(
        f'<hierarchy rotation="0"><node class="{cls}" package="{pacote}" text="" resource-id="{pacote}:id/{rid}"'
        ' content-desc="" clickable="true" enabled="true" focused="false" password="false" scrollable="false"'
        ' bounds="[0,100][720,160]" /></hierarchy>')


# ------------------------------------------------------------------ a classificação (pura)
@pytest.mark.parametrize(("rid", "esperada"), [
    ("action_bar_inbox_button", "feed"), ("row_thread_composer_edittext", "thread"),
    ("layout_comment_thread_edittext", "comments"), ("row_feed_button_like", "post"),
    ("action_bar_search_edit_text", "search"),
])
def test_tela_conhecida_grava_o_nome_declarado(rid: str, esperada: str) -> None:
    assert tela_da_falha(_com_id(rid), PKG) == esperada


@pytest.mark.parametrize(("screen", "esperada"), [
    ("save_login", "save_login_prompt"),
    # verificação e login pelo TIPO do motor: é o que a exclusão das lições reconhece (ADR-009)
    ("challenge", "desafio"), ("two_factor", "dois_fatores"), ("login", "login"),
])
def test_intersticial_verificacao_e_login(screen: str, esperada: str) -> None:
    assert tela_da_falha(_tela(screen), PKG) == esperada


def test_verificacao_e_login_caem_na_exclusao_das_licoes() -> None:
    for screen in ("challenge", "two_factor", "login"):
        assert tela_da_falha(_tela(screen), PKG) in TELAS_EXCLUIDAS


def test_tela_desconhecida_outro_app_e_app_sem_conhecimento_gravam_nulo() -> None:
    assert tela_da_falha(_tela("launcher"), PKG) is None             # do app, mas sem regra que case
    chrome = parse_hierarchy(
        '<hierarchy rotation="0"><node class="android.widget.EditText" package="com.android.chrome" text="x"'
        ' resource-id="com.android.chrome:id/url_bar" content-desc="" clickable="true" enabled="true"'
        ' focused="false" password="false" scrollable="false" bounds="[0,0][720,100]" /></hierarchy>')
    assert tela_da_falha(chrome, PKG) is None                         # outro app na frente
    feed = _com_id("action_bar_inbox_button")
    assert tela_da_falha(feed, "com.pocqa.messenger") is None          # app sem telas.yaml
    assert tela_da_falha(feed, None) is None
    assert tela_da_falha(feed, "../../etc") is None                    # nunca um caminho fora da pasta dos apps
    assert tela_da_falha(None, PKG) is None and tela_da_falha("feed", PKG) is None


def test_tela_sensivel_grava_so_o_nome_e_nunca_o_texto() -> None:
    tree = _tela("login", username_field="eu.teste", password_field=DIGITADO)
    assert tree.sensitive
    tela = tela_da_falha(tree, PKG)
    assert tela == "login" and DIGITADO not in tela and "eu.teste" not in tela


# ------------------------------------------------------------------ a gravação
async def _etapa_planejada(h: Harness) -> tuple[str, str]:
    run = h.run(["android-01"], mode="plan")
    await h.wait_run(run.id, statuses=("planned",))
    return run.id, f"{run.id}:android-01:v1:open_app"


def _nova_tentativa(h: Harness, step_id: str, numero: int) -> str:
    assert h.state is not None
    tentativa = f"{step_id}:a{numero}"
    h.state.db.execute("INSERT INTO attempts(id, step_id, number, status, started_at) VALUES (?,?,?,?,?)",
                       (tentativa, step_id, numero, "running", now_iso()))
    return tentativa


async def test_finish_attempt_grava_a_tela_so_junto_do_tipo_de_falha(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    _, etapa = await _etapa_planejada(harness)
    a1 = _nova_tentativa(harness, etapa, 1)
    st.repo.finish_attempt(a1, AttemptStatus.failed, error=ALVO_AUSENTE, screen="feed")
    a2 = _nova_tentativa(harness, etapa, 2)
    st.repo.finish_attempt(a2, AttemptStatus.failed, error=ALVO_AUSENTE)           # desconhecida
    a3 = _nova_tentativa(harness, etapa, 3)
    st.repo.finish_attempt(a3, AttemptStatus.succeeded, screen="feed")             # comprovada: sem "onde falhou"
    a4 = _nova_tentativa(harness, etapa, 4)
    st.repo.finish_attempt(a4, AttemptStatus.cancelled, error="Cancelado pelo usuário", screen="feed")
    linhas = {r["id"]: (r["failure_kind"], r["failure_screen"])
              for r in st.db.query("SELECT id, failure_kind, failure_screen FROM attempts WHERE step_id=?", (etapa,))}
    assert linhas == {a1: (FailureKind.ALVO_AUSENTE.value, "feed"), a2: (FailureKind.ALVO_AUSENTE.value, None),
                      a3: (None, None), a4: (None, None)}


def _app_do_instagram(monkeypatch: pytest.MonkeyPatch, h: Harness) -> None:
    """A etapa do harness é do QA Messenger (sem `telas.yaml`); aqui ela passa a ser do Instagram, o único app com
    conhecimento de telas declarado. Só o pacote muda — o resto do contexto é o que o scheduler montou."""
    assert h.state is not None
    scheduler = h.state.scheduler
    original = scheduler._app_context                                   # noqa: SLF001

    def app_context(run: Any, rt: Any, step_app_id: str | None = None) -> Any:
        app, conta = original(run, rt, step_app_id)
        return replace(app, package=PKG), conta

    monkeypatch.setattr(scheduler, "_app_context", app_context)


async def test_run_guarded_so_usa_a_arvore_observada_pela_tentativa(harness: Harness,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    st = harness.state
    assert st is not None
    _app_do_instagram(monkeypatch, harness)
    run_id, etapa = await _etapa_planejada(harness)
    rt = st.devices.get("android-01")
    run, step = st.repo.run_row(run_id), st.repo.step_dto(st.repo.step_row(etapa))
    obj = st.repo.objective_row(step.objective_id)
    executor = st.scheduler.executor
    tentativa = _nova_tentativa(harness, etapa, 1)

    async def sem_observar(**kw: Any) -> StepOutcome:
        return StepOutcome(Outcome.failed, ALVO_AUSENTE)

    async def observando(**kw: Any) -> StepOutcome:
        kw["rt"].last_tree = _com_id("action_bar_inbox_button")
        return StepOutcome(Outcome.failed, ALVO_AUSENTE)

    async def estourando(**kw: Any) -> StepOutcome:
        kw["rt"].last_tree = _com_id("row_thread_composer_edittext", "android.widget.EditText")
        raise RuntimeError("defeito interno")

    async def comprovando(**kw: Any) -> StepOutcome:
        kw["rt"].last_tree = _com_id("action_bar_inbox_button")
        return StepOutcome(Outcome.succeeded)

    # A árvore de antes é uma tela conhecida do Instagram — mas é de OUTRA etapa: sem observação nova, nulo.
    rt.last_tree = _com_id("row_feed_button_like")
    monkeypatch.setattr(executor, "run_step", sem_observar)
    out = await st.scheduler._run_guarded(run, obj, step, tentativa, rt, False)       # noqa: SLF001
    assert (out.outcome, out.tela_da_falha) == (Outcome.failed, None)

    monkeypatch.setattr(executor, "run_step", observando)
    out = await st.scheduler._run_guarded(run, obj, step, tentativa, rt, False)       # noqa: SLF001
    assert (out.outcome, out.tela_da_falha) == (Outcome.failed, "feed")

    # Saída por exceção também leva a tela (o desfecho é montado pelo scheduler, não pelo executor).
    monkeypatch.setattr(executor, "run_step", estourando)
    out = await st.scheduler._run_guarded(run, obj, step, tentativa, rt, False)       # noqa: SLF001
    assert out.outcome is Outcome.failed and out.tela_da_falha == "thread"

    monkeypatch.setattr(executor, "run_step", comprovando)
    out = await st.scheduler._run_guarded(run, obj, step, tentativa, rt, False)       # noqa: SLF001
    assert (out.outcome, out.tela_da_falha) == (Outcome.succeeded, None)


@pytest.mark.parametrize(("arvore", "esperada"), [
    (lambda: _com_id("action_bar_inbox_button"), "feed"),
    # tela sensível com a senha digitada no campo: vai o nome, nunca o texto
    (lambda: _tela("login", username_field="eu.teste", password_field=DIGITADO), "login"),
    (lambda: _tela("launcher"), None),
], ids=["feed", "login-sensivel", "desconhecida"])
async def test_a_execucao_grava_a_tela_da_tentativa_que_falhou(harness: Harness, monkeypatch: pytest.MonkeyPatch,
                                                               arvore: Any, esperada: str | None) -> None:
    """De ponta a ponta pelo laço do scheduler (`_run_guarded` → `_apply` → `finish_attempt`): a primeira tentativa de
    `open_app` falha na tela dada; as demais etapas comprovam sem tocar no dublê (que é do QA Messenger, e aqui a
    etapa é do Instagram)."""
    st = harness.state
    assert st is not None
    _app_do_instagram(monkeypatch, harness)
    falhou: list[str] = []

    async def run_step(**kw: Any) -> StepOutcome:
        step = kw["step"]
        assert isinstance(step, StepDTO)
        if step.key == "open_app" and not falhou:
            falhou.append(step.id)
            kw["rt"].last_tree = arvore()
            return StepOutcome(Outcome.failed, ALVO_AUSENTE)
        # comprovada como o executor comprova: a etapa passa por `verifying` e a tentativa fecha comprovada
        kw["rt"].last_tree = _com_id("action_bar_inbox_button")
        st.repo.transition_step(step.id, StepStatus.verifying)
        st.repo.transition_step(step.id, StepStatus.succeeded, detail="comprovada (dublê)",
                                result=StepResult(verified=True, evidence_text="comprovada (dublê)"))
        st.repo.finish_attempt(str(kw["attempt_id"]), AttemptStatus.succeeded)
        return StepOutcome(Outcome.succeeded)

    monkeypatch.setattr(st.scheduler.executor, "run_step", run_step)
    run = harness.run(["android-01"])
    await harness.wait_run(run.id, timeout=60)
    assert falhou
    linhas = st.db.query("SELECT a.* FROM attempts a JOIN steps s ON s.id = a.step_id WHERE s.run_id=?", (run.id,))
    com_falha = [(r["step_id"], r["failure_kind"], r["failure_screen"]) for r in linhas if r["failure_kind"]]
    assert com_falha == [(falhou[0], FailureKind.ALVO_AUSENTE.value, esperada)]
    # a tela comprovada depois não deixa tela em tentativa nenhuma sem falha
    assert all(r["failure_screen"] is None for r in linhas if not r["failure_kind"])
    # nada da tela sensível (a senha digitada) em coluna nenhuma das tentativas
    assert all(DIGITADO not in str(v) for r in linhas for v in dict(r).values())
