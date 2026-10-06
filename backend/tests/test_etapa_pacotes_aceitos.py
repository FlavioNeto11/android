"""31.123 (migração 120, adendo v1.84): a etapa conclui no pacote vizinho que a demonstração mostrou.

A busca do Configurações é de outro pacote. Na execução r-20261006012340-d92795 o executor recusou concluir a etapa
fora do app dela, e ela estourou o orçamento de 10 chamadas de IA (33 chamadas, US$ 0,331). Agora:

- `PlanStep`/`StepDTO.pacotes_aceitos` (coluna `steps.pacotes_aceitos`, JSON nulo): os pacotes, além do app da etapa,
  em que a tela COMPROVA a conclusão. Omitido quando vazio: o plano e o hash das etapas já gravadas não mudam.
- `StepExecutor._tela_fora_do_app` aceita esses pacotes como o do app. Pacote desconhecido e qualquer outro seguem
  não comprovando.
- O ensino preenche a lista com os pacotes das entradas da etapa, sem o próprio app, o systemui, o lançador e os apps
  cadastrados. A prévia e o `save` avisam em `warnings`.

Nível de prova: `simulated` (funções puras, banco de teste e harness com aparelho falso; nenhuma IA).
"""
from __future__ import annotations

import json
from typing import Any

from app.automation.hierarchy import UiTree
from app.devices.manager import Observation
from app.models import Plan, PlannerInfo, PlanStep, Postcondition, RunCreate, StepDTO, StepStatus
from app.taskqueue.executor import StepExecutor
from app.taskqueue.recipes import step_template_hash
from app.training import partida

from .conftest import Harness
from .test_modo_treinamento import _no_controle
from .test_treino_segredo_na_gravacao import _el
from .test_treino_validacao_do_salvar import _etapa

SETTINGS, BUSCA = "com.android.settings", "com.google.android.settings.intelligence"
POST = Postcondition(kind="text_visible", value="No results", description="a busca mostrou o resultado")


def _dto(aceitos: list[str]) -> StepDTO:
    return StepDTO(id="r-p:android-04:v1:buscar", run_id="r-p", objective_id="r-p:android-04", instance_id="android-04",
                   plan_version=1, seq=1, key="buscar", title="Buscar", goal="buscar", depends_on=[],
                   side_effect=False, postcondition=POST, timeout_s=60, max_attempts=2, attempts=1,
                   status=StepStatus.verifying, pacotes_aceitos=aceitos)


def _obs(pacote: str | None, *na_arvore: str) -> Observation:
    return Observation(frame_id="f", ts="t", width=720, height=1280, jpeg=None,
                       tree=UiTree(elements=[], packages=list(na_arvore), sensitive=False), package=pacote,
                       sensitive=False)


# ------------------------------------------------------------------ a trava do executor
def test_o_vizinho_declarado_comprova_e_o_resto_segue_fora() -> None:
    fora = StepExecutor._tela_fora_do_app                                      # noqa: SLF001
    assert fora(_dto([BUSCA]), _obs(BUSCA), SETTINGS) is None
    assert fora(_dto([BUSCA]), _obs(None, BUSCA), SETTINGS) is None              # só a árvore diz o pacote
    assert fora(_dto([BUSCA]), _obs("com.outro.app"), SETTINGS) == "com.outro.app"
    assert fora(_dto([BUSCA]), _obs(None), SETTINGS) == "desconhecido"           # desconhecido nunca comprova
    assert fora(_dto([]), _obs(BUSCA), SETTINGS) == BUSCA                        # sem a lista, como antes
    assert fora(_dto([]), _obs(SETTINGS), SETTINGS) is None


def test_o_plano_e_o_hash_das_etapas_antigas_nao_mudam() -> None:
    antiga = PlanStep(key="buscar", title="Buscar", goal="g", postcondition=POST)
    nova = antiga.model_copy(update={"pacotes_aceitos": [BUSCA]})
    assert "pacotes_aceitos" not in antiga.model_dump(mode="json")              # omitido quando vazio
    assert nova.model_dump(mode="json")["pacotes_aceitos"] == [BUSCA]
    assert step_template_hash(antiga) == step_template_hash(nova)


def test_os_vizinhos_da_demonstracao_sem_app_systemui_lancador_nem_cadastrado() -> None:
    entradas = [{"package": SETTINGS}, {"package": BUSCA}, {"package": "com.android.systemui"},
                {"package": "com.google.android.apps.nexuslauncher"}, {"package": "com.pocqa.messenger"},
                {"package": BUSCA}, {"package": None}]
    assert partida.pacotes_vizinhos(entradas, SETTINGS, ["com.pocqa.messenger", SETTINGS]) == [BUSCA]
    assert partida.pacotes_vizinhos(entradas, None, []) == []
    assert partida.aviso_dos_vizinhos([("Buscar", [BUSCA]), ("Outra", [])]) == [
        f"Etapa “Buscar”: a demonstração terminou fora do app, em {BUSCA}; a etapa passa a aceitar a conclusão nessa tela."]


# ------------------------------------------------------------------ a coluna (migração 120)
async def test_a_etapa_materializada_guarda_e_le_os_pacotes(harness: Harness) -> None:
    st = harness.state
    run = st.runs.create(RunCreate(command="buscar nas configurações", instance_ids=["android-01"],
                                   idempotency_key="pacotes-120", mode="plan"))
    await harness.wait_run(run.id, statuses=("planned", "needs_input", "completed", "completed_with_issues", "failed"))
    plano = Plan(summary="s", app_id="qa-messenger", app_package="com.pocqa.messenger",
                 steps=[PlanStep(key="buscar", title="Buscar", goal="g", postcondition=POST, pacotes_aceitos=[BUSCA]),
                        PlanStep(key="voltar", title="Voltar", goal="g", postcondition=POST)],
                 planner=PlannerInfo(provider="t", model="t", simulated=True))
    st.db.execute("DELETE FROM steps WHERE run_id=?", (run.id,))
    st.db.execute("DELETE FROM objectives WHERE run_id=?", (run.id,))
    st.repo.save_plan(run.id, plano)
    st.repo.materialize(run.id, plano, [{"instance_id": "android-01"}])
    buscar = st.repo.step_row(f"{run.id}:android-01:v1:buscar")
    voltar = st.repo.step_row(f"{run.id}:android-01:v1:voltar")
    assert json.loads(buscar["pacotes_aceitos"]) == [BUSCA] and voltar["pacotes_aceitos"] is None
    assert st.repo.step_dto(buscar).pacotes_aceitos == [BUSCA] and st.repo.step_dto(voltar).pacotes_aceitos == []
    assert "pacotes_aceitos" not in st.repo.step_dto(voltar).model_dump(mode="json")      # omitido na API


# ------------------------------------------------------------------ o ensino
async def _gravada_com_busca(harness: Harness) -> tuple[Any, str]:
    """A demonstração abre o app, toca na busca (ainda no app) e digita na tela da busca (o pacote vizinho)."""
    st, rt, lease = await _no_controle(harness)
    s = st.training.start("android-01", intent="Buscar no app", lease_id=lease, app_id="qa-messenger")
    st.training.record(rt, {"type": "open_app", "app_id": "qa-messenger"}, None)
    lupa = _el("e1", text="Buscar", rid="com.pocqa.messenger:id/buscar", clickable=True, bounds=(0, 0, 100, 100))
    st.training.record(rt, {"type": "tap", "x": 50, "y": 50},
                       UiTree(elements=[lupa], packages=["com.pocqa.messenger"], sensitive=False))
    campo = _el("e2", rid=f"{BUSCA}:id/campo", editable=True, focused=True)
    st.training.record(rt, {"type": "text", "text": "wifi"}, UiTree(elements=[campo], packages=[BUSCA], sensitive=False))
    st.training.stop(s["id"], lease_id=lease)
    return st, s["id"]


def _proposta() -> dict[str, Any]:
    return {"summary": "buscar", "command_template": "busque wifi no app", "app_id": "qa-messenger",
            "parameters": [], "discarded": [], "questions": [],
            "steps": [_etapa("abrir_busca", [1, 2]), _etapa("buscar", [3])]}


async def test_o_ensino_declara_o_vizinho_da_demonstracao_e_avisa(harness: Harness) -> None:
    st, sid = await _gravada_com_busca(harness)
    assert [e["package"] for e in st.training.get(sid)["inputs"]][1:] == ["com.pocqa.messenger", BUSCA]
    previa = await st.skills.preview(sid, proposal=_proposta(), profile_ids=[], group_ids=[])
    assert [a for a in previa["warnings"] if BUSCA in a] == [
        f"Etapa “Etapa buscar”: a demonstração terminou fora do app, em {BUSCA}; a etapa passa a aceitar a conclusão "
        "nessa tela."]
    salvo = await st.skills.save(sid, proposal=_proposta(), profile_ids=[], group_ids=[])
    plano = json.loads(st.db.scalar("SELECT plan FROM flows WHERE id=?", (salvo["flow_id"],)))
    por_chave = {p["key"]: p for p in plano["steps"]}
    assert por_chave["buscar"]["pacotes_aceitos"] == [BUSCA] and "pacotes_aceitos" not in por_chave["abrir_busca"]
