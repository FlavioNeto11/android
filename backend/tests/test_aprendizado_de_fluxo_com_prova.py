"""Fluxo só se aprende de execução TODA comprovada, e o modelo congela em forma de modelo tudo o que leva o valor.
Nível de prova: `simulated` (banco de teste; nenhum aparelho, nenhuma IA).

Motivação real (28/09, android-06): as execuções r-20260928165254-e31953 e r-20260928195344-02ee9e falharam de novo
e de novo no Instagram. Uma execução `completed` com etapa CONFIRMADA À MÃO (`verified=false`) virava fluxo
reaproveitável — o plano congelado passava a valer como caminho comprovado sem nunca ter sido observado — e o
congelamento trocava o valor por `{nome}` nos textos, mas não nos argumentos (`bindings`), nas guardas da linha
(`band_guard`) nem nos critérios de sucesso: o fluxo reaproveitado para outro alvo mirava o alvo da execução-fonte.

O que se prova:
- execução `completed` com UMA etapa confirmada à mão não cria fluxo (nem quando a confirmação foi numa versão
  anterior do plano, que a recuperação aproveitou);
- execução toda comprovada cria o fluxo com `bindings`, `band_guard` e `success_criteria` em forma de modelo, e o
  fluxo reaproveitado para outro alvo leva o valor NOVO a todos eles.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.db import Database
from app.models import Plan, PlannerInfo, PlanStep, Postcondition, StepResult
from app.taskqueue.flows import FlowStore
from app.taskqueue.repository import resolve_templates
from app.util import now_iso

from .fake_skills import banco

RUN = "r-origem-prova"
COMANDO = "mande bom dia para @ana no instagram"


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    d = banco(tmp_path)
    yield d
    d.close()


def _plano() -> Plan:
    """O plano como o planejador o escreve de verdade: o VALOR (`@ana`) por extenso nos argumentos, na guarda da
    linha e no critério de sucesso, além dos textos que o aprendizado já trocava."""
    abrir = PlanStep(key="abrir_conversa", title="Abrir a conversa com @ana", goal="Abrir a conversa com @ana",
                     capability="OPEN_THREAD", bindings={"username": "@ana"},
                     postcondition=Postcondition(kind="text_visible", value="@ana", description="conversa com @ana"))
    enviar = PlanStep(key="enviar", title="Enviar bom dia", goal="Enviar bom dia para @ana", depends_on=["abrir_conversa"],
                      side_effect=True, capability="SEND_MESSAGE", commit_guard=["@ana"], band_guard=["@ana"],
                      bindings={"username": "@ana", "content": "bom dia"},
                      postcondition=Postcondition(kind="text_visible", value="bom dia", description="mensagem enviada"))
    return Plan(summary="Mandar bom dia para @ana", app_id="instagram", parameters={"username": "@ana"},
                success_criteria=["Mensagem visível na conversa de @ana"], steps=[abrir, enviar],
                planner=PlannerInfo(provider="anthropic", model="claude", simulated=False))


def _execucao(db: Database, etapas: list[tuple[int, str, bool]]) -> dict[str, object]:
    """Uma execução `completed` com o objetivo concluído e as etapas `(versão, chave, verified)` já `succeeded`.
    A última versão citada é a do objetivo (a recuperação cria uma versão nova com o que faltava)."""
    agora = now_iso()
    db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, instance_ids, plan, created_at)"
               " VALUES (?,?,?,?,?,?,?,?)",
               (RUN, f"k-{RUN}", COMANDO, "execute", "completed", json.dumps(["android-01"]),
                _plano().model_dump_json(), agora))
    versao = max(v for v, _, _ in etapas)
    db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version) VALUES (?,?,?,?,?)",
               (f"{RUN}:o1", RUN, "android-01", "succeeded", versao))
    for seq, (v, chave, comprovada) in enumerate(etapas, start=1):
        resultado = StepResult(verified=comprovada, evidence_text=(
            "texto visível na tela" if comprovada else "Confirmado manualmente pelo usuário."))
        db.execute("INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal,"
                   " postcondition, timeout_s, max_attempts, status, result) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (f"{RUN}:android-01:v{v}:{chave}", RUN, f"{RUN}:o1", "android-01", v, seq, chave, chave, chave,
                    Postcondition(kind="text_visible", value="x", description="x").model_dump_json(), 180, 3,
                    "succeeded", resultado.model_dump_json()))
    linha = db.one("SELECT * FROM runs WHERE id=?", (RUN,))
    assert linha is not None
    return dict(linha)


def test_execucao_completa_com_etapa_confirmada_a_mao_nao_vira_fluxo(db: Database) -> None:
    run = _execucao(db, [(1, "abrir_conversa", True), (1, "enviar", False)])
    assert FlowStore(db).learn_from_run(run) is None
    assert db.scalar("SELECT COUNT(*) FROM flows") == 0


def test_confirmacao_manual_numa_versao_anterior_do_plano_tambem_impede(db: Database) -> None:
    """A recuperação recomeça só com o que faltava: a etapa confirmada à mão na v1 não é refeita na v2, e o
    caminho congelado dependeria dela do mesmo jeito."""
    run = _execucao(db, [(1, "abrir_conversa", False), (2, "enviar", True)])
    assert FlowStore(db).learn_from_run(run) is None
    assert db.scalar("SELECT COUNT(*) FROM flows") == 0


def test_execucao_toda_comprovada_congela_argumentos_guardas_e_criterios_em_modelo(db: Database) -> None:
    run = _execucao(db, [(1, "abrir_conversa", True), (1, "enviar", True)])
    flows = FlowStore(db)
    flow_id = flows.learn_from_run(run)
    assert flow_id is not None
    congelado = Plan.model_validate_json(str(db.scalar("SELECT plan FROM flows WHERE id=?", (flow_id,))))
    abrir, enviar = congelado.steps
    assert abrir.bindings == {"username": "{username}"}
    assert enviar.bindings == {"username": "{username}", "content": "bom dia"}
    assert enviar.band_guard == ["{username}"] and enviar.commit_guard == ["{username}"]
    assert congelado.success_criteria == ["Mensagem visível na conversa de {username}"]
    # Reaproveitado para OUTRO alvo: nada do alvo da execução-fonte sobra no que a etapa usa para agir e comprovar.
    achado = flows.match("mande bom dia para @bia.2 no instagram")
    assert achado is not None
    _, plano = achado
    assert plano.parameters == {"username": "@bia.2"}
    assert plano.success_criteria == ["Mensagem visível na conversa de @bia.2"]
    valores = dict(plano.parameters)
    enviar_novo = plano.steps[1]
    assert {k: resolve_templates(v, valores) for k, v in enviar_novo.bindings.items()} == {
        "username": "@bia.2", "content": "bom dia"}
    assert [resolve_templates(g, valores) for g in enviar_novo.band_guard] == ["@bia.2"]
    assert "@ana" not in plano.model_dump_json()
