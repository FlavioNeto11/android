"""A tela onde a tentativa falhou, GRAVADA (item 22.3; ADR-054, pendência A2).

`attempts.failure_screen` existia desde a migração 055 e ninguém gravava. Agora:

- `executor.tela_da_falha` classifica a última observação pelo conhecimento DECLARADO do app da etapa (o `telas.yaml`
  do pacote): o nome da regra, ou o tipo do motor nas telas de verificação e de login (o vocabulário que a exclusão
  das lições lê); `None` quando a tela é desconhecida, de outro app ou de app sem conhecimento. Nunca texto da tela;
- o scheduler (`_run_guarded`) só usa uma árvore observada por ESTA tentativa — sem observação nova, nulo;
- `repository.finish_attempt` grava a tela no mesmo UPDATE do tipo, e só quando há tipo de falha;
- a verificação achada DENTRO de uma ferramenta (`quick_tree`, que não atualiza `rt.last_tree`) grava o tipo da trava
  que o executor devolve, e não a tela de antes do gesto;
- todo desfecho de falha (retry, waiting_user, uncertain, device_stuck, failed) grava; pausa, cancelamento e sucesso
  nunca; e nenhum erro da classificação derruba a etapa.

O cuidado do backlog (a linha antiga sem tela não vira corrigida porque a falha passou a vir com tela) está em
`test_learning_backlog.py`.

Nível de prova: `simulated` (dublê do Instagram, banco de teste; nenhum aparelho, nenhuma IA).
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from app.automation import conhecimento_de_telas as telas_do_app
from app.automation.hierarchy import SUBTIPO_CONTA_TRAVADA, ContaTravada, UiTree, parse_hierarchy
from app.automation.tools import TelaDeContaTravada
from app.models import AttemptStatus, StepDTO, StepResult, StepStatus
from app.modules.learning.domain.falhas import FailureKind
from app.modules.learning.domain.licoes import TELAS_EXCLUIDAS
from app.taskqueue import executor as executor_mod
from app.taskqueue import scheduler as scheduler_mod
from app.taskqueue.executor import Outcome, StepOutcome, tela_da_falha
from app.util import now_iso

from .conftest import Harness
from .fake_instagram import PKG, FakeInstagram
from .test_detector_conta_travada import COMANDO, IID, _ate_parar, _estado, _parque, _perfil_pronto

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


def test_outro_app_com_campo_de_senha_grava_nulo() -> None:
    """O "campo de senha é login" vale só no app da etapa: a senha do Chrome (ou de outro app na frente) não é a tela
    de login do app que falhou."""
    outro = parse_hierarchy(
        '<hierarchy rotation="0"><node class="android.widget.EditText" package="com.android.chrome" text=""'
        ' resource-id="com.android.chrome:id/senha" content-desc="" clickable="true" enabled="true"'
        ' focused="false" password="true" scrollable="false" bounds="[0,0][720,100]" /></hierarchy>')
    assert any(e.password for e in outro.elements)
    assert tela_da_falha(outro, PKG) is None


def test_nenhum_erro_da_classificacao_escapa(monkeypatch: pytest.MonkeyPatch) -> None:
    """A tela da falha é registro, calculada depois de o desfecho já estar decidido: nenhuma parte dela derruba a
    etapa — nem a leitura da regra declarada, que fica depois da classificação."""
    def quebra(self: Any, tela: str) -> Any:
        raise RuntimeError("regra ilegível")

    monkeypatch.setattr(telas_do_app.ConhecimentoDeTelas, "regra", quebra)
    assert tela_da_falha(_com_id("action_bar_inbox_button"), PKG) is None


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

    def app_context(run: Any, rt: Any, step_app_id: str | None = None, **kw: Any) -> Any:
        app, conta = original(run, rt, step_app_id, **kw)       # `profile_id` (item 24.4): a conta do app da etapa
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


async def _contexto(h: Harness, monkeypatch: pytest.MonkeyPatch, *, rodando: bool = False) -> tuple[Any, ...]:
    """A etapa `open_app` de uma execução só planejada, do Instagram, com uma tentativa aberta. `rodando`: o objetivo e
    a etapa em `running`, com a tentativa consumida, como o laço os deixa antes de `_apply`."""
    st = h.state
    assert st is not None
    _app_do_instagram(monkeypatch, h)
    run_id, etapa = await _etapa_planejada(h)
    if rodando:
        st.db.execute("UPDATE objectives SET status='running' WHERE run_id=?", (run_id,))
        st.db.execute("UPDATE steps SET status='running', attempts=1 WHERE id=?", (etapa,))
    tentativa = _nova_tentativa(h, etapa, 1)
    run, step = st.repo.run_row(run_id), st.repo.step_dto(st.repo.step_row(etapa))
    return run, st.repo.objective_row(step.objective_id), step, tentativa, st.devices.get("android-01")


@pytest.mark.parametrize(("desfecho", "status_da_tentativa"), [
    (Outcome.retry, "failed"), (Outcome.waiting_user, "interrupted"), (Outcome.uncertain, "uncertain"),
    (Outcome.device_stuck, "failed"),
])
async def test_todo_desfecho_de_falha_grava_a_tela(harness: Harness, monkeypatch: pytest.MonkeyPatch,
                                                   desfecho: Outcome, status_da_tentativa: str) -> None:
    """`_run_guarded` → `_apply` → `finish_attempt` em cada ramo de falha que não é `failed` (este vai de ponta a ponta
    acima): nenhum deles pode esquecer a tela."""
    st = harness.state
    assert st is not None
    run, obj, step, tentativa, rt = await _contexto(harness, monkeypatch, rodando=True)

    async def observando(**kw: Any) -> StepOutcome:
        kw["rt"].last_tree = _com_id("action_bar_inbox_button")
        return StepOutcome(desfecho, ALVO_AUSENTE)

    monkeypatch.setattr(st.scheduler.executor, "run_step", observando)
    out = await st.scheduler._run_guarded(run, obj, step, tentativa, rt, False)       # noqa: SLF001
    assert (out.outcome, out.tela_da_falha) == (desfecho, "feed")
    st.scheduler._apply(out, obj, step, tentativa, rt)                              # noqa: SLF001
    linha = st.db.one("SELECT status, failure_kind, failure_screen FROM attempts WHERE id=?", (tentativa,))
    assert linha is not None
    assert (linha["status"], linha["failure_screen"]) == (status_da_tentativa, "feed")
    assert linha["failure_kind"] is not None


@pytest.mark.parametrize("desfecho", [Outcome.yielded, Outcome.cancelled, Outcome.succeeded])
async def test_pausa_cancelamento_e_sucesso_nunca_levam_tela(harness: Harness, monkeypatch: pytest.MonkeyPatch,
                                                              desfecho: Outcome) -> None:
    """Mesmo com a tentativa tendo observado uma tela conhecida — e mesmo que o desfecho viesse com uma tela."""
    st = harness.state
    assert st is not None
    run, obj, step, tentativa, rt = await _contexto(harness, monkeypatch, rodando=True)

    async def observando(**kw: Any) -> StepOutcome:
        kw["rt"].last_tree = _com_id("action_bar_inbox_button")
        return StepOutcome(desfecho, "pause", tela_da_falha="feed")

    monkeypatch.setattr(st.scheduler.executor, "run_step", observando)
    out = await st.scheduler._run_guarded(run, obj, step, tentativa, rt, False)       # noqa: SLF001
    assert (out.outcome, out.tela_da_falha) == (desfecho, None)
    if desfecho is Outcome.yielded:
        # a pausa num ponto seguro é `interrompida` na tentativa: tem tipo, e ainda assim nenhuma tela
        st.scheduler._apply(out, obj, step, tentativa, rt)                          # noqa: SLF001
        linha = st.db.one("SELECT failure_kind, failure_screen FROM attempts WHERE id=?", (tentativa,))
        assert linha is not None
        assert (linha["failure_kind"], linha["failure_screen"]) == (FailureKind.INTERROMPIDA.value, None)


async def test_run_guarded_respeita_a_tela_que_o_executor_ja_sabe(harness: Harness,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    """A trava achada dentro de uma ferramenta volta com o tipo dela; a última árvore (a de antes do gesto) não a
    sobrescreve."""
    st = harness.state
    assert st is not None
    run, obj, step, tentativa, rt = await _contexto(harness, monkeypatch)

    async def trava_na_ferramenta(**kw: Any) -> StepOutcome:
        kw["rt"].last_tree = _com_id("action_bar_inbox_button")
        return StepOutcome(Outcome.waiting_user, "auth_challenge", trava_da_conta=True, tela_da_falha="desafio")

    monkeypatch.setattr(st.scheduler.executor, "run_step", trava_na_ferramenta)
    out = await st.scheduler._run_guarded(run, obj, step, tentativa, rt, False)       # noqa: SLF001
    assert (out.outcome, out.tela_da_falha) == (Outcome.waiting_user, "desafio")


async def test_erro_na_classificacao_nao_derruba_o_desfecho(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    st = harness.state
    assert st is not None
    run, obj, step, tentativa, rt = await _contexto(harness, monkeypatch)

    async def observando(**kw: Any) -> StepOutcome:
        kw["rt"].last_tree = _com_id("action_bar_inbox_button")
        return StepOutcome(Outcome.failed, ALVO_AUSENTE)

    def quebra(arvore: object, pacote: str | None) -> str | None:
        raise RuntimeError("classificador quebrado")

    monkeypatch.setattr(st.scheduler.executor, "run_step", observando)
    monkeypatch.setattr(scheduler_mod, "tela_da_falha", quebra)
    out = await st.scheduler._run_guarded(run, obj, step, tentativa, rt, False)       # noqa: SLF001
    assert (out.outcome, out.detail, out.tela_da_falha) == (Outcome.failed, ALVO_AUSENTE, None)


async def test_trava_achada_dentro_da_ferramenta_grava_a_tela_da_trava(tmp_path: Path,
                                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    """Com o executor de verdade: a leitura rápida DENTRO da ferramenta (`quick_tree`) acha a verificação e levanta
    `TelaDeContaTravada`. `quick_tree` não atualiza `rt.last_tree`, que ainda mostra a tela de antes do gesto; a
    tentativa grava a tela da trava ('desafio'), que é onde ela de fato parou."""
    ferramentas: list[str] = []

    async def acha_a_trava(ctx: Any, nome: str, args: Any) -> Any:
        ferramentas.append(nome)
        raise TelaDeContaTravada(ContaTravada(SUBTIPO_CONTA_TRAVADA, "confirm you're human"), PKG)

    monkeypatch.setattr(executor_mod, "execute_tool", acha_a_trava)
    async for h in _parque(tmp_path):
        _perfil_pronto(h)
        run = h.run([IID], command=COMANDO)
        objetivo = await _ate_parar(h, run.id)
        assert ferramentas, "nenhuma ferramenta chegou a rodar"
        assert objetivo["status"] == "waiting_user", dict(objetivo)
        s = _estado(h)
        linhas = s.db.query("SELECT a.status, a.failure_kind, a.failure_screen FROM attempts a"
                            " JOIN steps p ON p.id = a.step_id WHERE p.run_id=? AND a.failure_kind IS NOT NULL",
                            (run.id,))
        assert [(r["status"], r["failure_kind"], r["failure_screen"]) for r in linhas] == [
            ("interrupted", FailureKind.INTERROMPIDA.value, "desafio")]
        # a última árvore observada é a de antes do gesto, e NÃO é a verificação: a tela gravada veio da trava
        assert tela_da_falha(s.devices.get(IID).last_tree, PKG) != "desafio"
