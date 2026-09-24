"""Execuções f41d10 e eda77f (23/09/2026): looping tocar→voltar e etapas fora de ordem depois do replano."""
from __future__ import annotations

from app.models import PlanStep, Postcondition, StepStatus
from app.taskqueue.executor import ciclo_sem_progresso

from .conftest import Harness


def _post(kind: str = "text_visible", value: str = "x") -> Postcondition:
    return Postcondition(kind=kind, value=value, description=value)  # type: ignore[arg-type]


def test_ciclo_de_duas_acoes_alternadas_e_detectado() -> None:
    # (tela exata, tela estrutural, ação): a tela do post muda o texto a cada visita ("há 32 min", curtidas), mas
    # a estrutura é a mesma — é pela estrutura que o par se reconhece
    grade = ("grade-exata", "grade", "tap:e72")
    seq: list[tuple[str, str, str]] = []
    resultado = None
    for volta in range(1, 4):
        seq += [grade, (f"post-exata-{volta}", "post", "press_back:")]
        resultado = ciclo_sem_progresso(seq, 4)
        if volta < 3:
            assert resultado is None, volta                 # duas voltas ainda podem ser tentativa legítima
    assert resultado and "se alternam" in resultado and "tap:e72" in resultado
    # progresso de verdade não dispara: telas diferentes a cada passo
    assert ciclo_sem_progresso([(c, c, a) for c, a in zip("abcdef", "xyzxyz")], 4) is None
    # rolar uma lista longa repete ação e estrutura, mas o TEXTO muda: é progresso, não ciclo
    assert ciclo_sem_progresso([(f"lista-{i}", "lista", "scroll:down") for i in range(6)], 4) is None
    # o caso antigo (mesma ação na mesma tela exata) continua valendo com o mesmo limite
    assert ciclo_sem_progresso([grade] * 3, 4) is None and "mesma ação" in (ciclo_sem_progresso([grade] * 4, 4) or "")


async def test_dependencia_refeita_no_replano_so_conta_comprovada_na_versao_atual(harness: Harness) -> None:
    run = harness.run(["android-01"])
    assert (await harness.wait_run(run.id)).status == "completed"
    repo = harness.state.repo                                                    # type: ignore[union-attr]
    obj = repo.db.one("SELECT * FROM objectives WHERE run_id=?", (run.id,))
    feita = repo.db.one("SELECT key FROM steps WHERE objective_id=? AND status='succeeded' ORDER BY seq LIMIT 1",
                        (obj["id"],))["key"]
    repo.revise_plan(obj["id"], "teste: refaz uma etapa já comprovada", [
        PlanStep(key=feita, title="de novo", goal="g", postcondition=_post()),
        PlanStep(key="depois", title="depois", goal="g", depends_on=[feita], postcondition=_post()),
        PlanStep(key="orfa", title="órfã", goal="g", depends_on=["inexistente_na_v2"], postcondition=_post()),
    ])
    repo.promote(run.id)
    v2 = {r["key"]: r for r in repo.db.query("SELECT id, key, status FROM steps WHERE objective_id=? AND plan_version=2",
                                              (obj["id"],))}
    assert v2[feita]["status"] == "ready"
    assert v2["depois"]["status"] == "pending", "a v1 comprovada NÃO vale enquanto a v2 da mesma etapa não fechar"
    assert v2["orfa"]["status"] == "pending"
    for alvo in (StepStatus.running, StepStatus.verifying, StepStatus.succeeded):
        repo.transition_step(v2[feita]["id"], alvo)
    repo.promote(run.id)
    assert repo.db.one("SELECT status FROM steps WHERE id=?", (v2["depois"]["id"],))["status"] == "ready"
