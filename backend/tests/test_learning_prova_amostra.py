"""30.48: a prova de fluxo com `for_each` roda uma AMOSTRA, os N primeiros itens na ordem da tela.

O `for_each` de tamanho desconhecido nunca cabia no teto da prova (30.41) e o fluxo nunca se validava por pedido (o
lv-5cf7389f13e4e0f0, 04/10). O que se prova:
- a regra pura: N é o maior que cabe no teto máximo (17 etapas), entre 2 e 3; sem laço ou sem caber o mínimo, `None`;
  o rastro diz a amostra, ou a prova inteira quando a lista tem N itens ou menos;
- a estimativa da prova de FLUXO usa a amostra (o tamanho desconhecido deixa de barrar); a da receita, não;
- o laço de verdade (Harness): a PROVA expande só os 3 primeiros dos 5 contatos, com o rastro, e envia 3; a execução
  comum expande os 5; os itens de fora nem viram etapa;
- o rótulo: a evidência da prova com amostra diz "provado em amostra de N (de M itens)", e é uma evidência como outra.

Armações: o Harness (porta 5640, aparelho de QA falso com 5 contatos) e o `mundo` de `test_learning_prova`.
Nível de prova: `simulated`.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.models import Plan, PlannerInfo, PlanStep, Postcondition, RunCreate
from app.modules.learning.application.ports import Ajustes
from app.modules.learning.application.servico import LearningService
from app.modules.learning.domain.prova import amostra_do_rastro, rastro_da_amostra
from app.modules.learning.domain.validacao import maximo_de_etapas_da_prova, tamanho_da_amostra, teto_da_prova
from app.modules.learning.domain.vocabulario import LivroKind
from app.modules.learning.infrastructure.dossies import DossiesSql
from app.modules.learning.infrastructure.fontes import FontesSql
from app.modules.learning.infrastructure.montagem import GuardaDoFluxo
from app.modules.learning.infrastructure.segredo import TriagemDeCredencial
from app.modules.learning.infrastructure.sql_repository import SqlLearningRepository
from app.modules.learning.infrastructure.validacoes_sql import FontesDaValidacaoSql
from app.modules.skills.infrastructure.sql_repository import SqlSkillRepository

from .conftest import Harness
from .fake_device import CONTACTS
from .fake_skills import ValidadorFalso, fluxo
from .fake_skills import banco as banco_migrado
from .test_for_each import ALL
from .test_learning_classe_do_fluxo import COMENTAR, INSTAGRAM, LER, OUTLOOK, PLANO, CatalogoPorApp
from .test_learning_prova import Mundo, _candidato, _digerir, _evidencias, _prova, _versao_2, mundo  # noqa: F401


# ------------------------------------------------------------------ a regra pura
def test_o_tamanho_da_amostra_e_o_que_cabe_no_teto() -> None:
    assert maximo_de_etapas_da_prova() == 17
    assert teto_da_prova(17) is not None and teto_da_prova(18) is None
    assert tamanho_da_amostra(3, 4) == 3                  # o "todos os contatos" do QA: 3 + 3 × 4 = 15 etapas
    assert tamanho_da_amostra(9, 4) == 2                  # 9 + 2 × 4 = 17
    assert tamanho_da_amostra(10, 4) is None              # nem 2 itens cabem
    assert tamanho_da_amostra(3, 0) is None               # sem laço


def test_o_rastro_diz_a_amostra_ou_a_prova_inteira() -> None:
    assert amostra_do_rastro([f"Expandido para 3 item(ns) lidos em 'Ler' ({rastro_da_amostra(3, 8)})"]) == (3, 8)
    assert "prova inteira, 2 de 2 itens" in rastro_da_amostra(2, 2)
    assert amostra_do_rastro([rastro_da_amostra(2, 2)]) is None
    assert amostra_do_rastro(["Expandido para 8 item(ns) lidos em 'Ler'"]) is None


# ------------------------------------------------------------------ a estimativa da prova
def test_a_estimativa_da_prova_de_fluxo_usa_a_amostra_e_a_da_receita_nao(tmp_path: Path) -> None:
    db = banco_migrado(tmp_path, "amostra.sqlite3")
    post = Postcondition(kind="text_visible", value="x", description="x")
    passos = [PlanStep(key="abrir", title="abrir", goal="abrir", postcondition=post),
              PlanStep(key="ler", title="ler", goal="ler",
                       postcondition=Postcondition(kind="items_collected", value="contatos", description="lidos")),
              *(PlanStep(key=f"p{i}", title="p {item}", goal="p", for_each="ler", postcondition=post) for i in range(4))]
    plano = Plan(summary="todos", app_id="qa-messenger", planner=PlannerInfo(provider="fluxo", model="m", simulated=True),
                 steps=passos)
    fontes = FontesDaValidacaoSql(db, precos=dict, fluxo_ativo_para=lambda c: False, vetado=lambda e: False)
    # a origem não coletou a lista (o tamanho não se sabe): antes, `None` e o pedido nunca despachava
    assert fontes._etapas_do_plano(plano, "f-todos", amostra=True) == 2 + 3 * 4
    assert fontes._etapas_do_plano(plano, "f-todos") is None
    db.close()


# ------------------------------------------------------------------ o laço de verdade
async def test_a_prova_expande_so_a_amostra_e_a_execucao_comum_a_lista_inteira(tmp_path: Path) -> None:
    assert len(CONTACTS) == 5
    h = Harness(tmp_path, 1)
    h.cfg.file.ai.flows = True
    await h.boot()
    try:
        s = h.state
        assert s is not None
        h.pular_o_tempo()
        fake = h.fakes["android-01"]
        origem = s.runs.create(RunCreate(command=ALL, instance_ids=["android-01"], idempotency_key="k-origem-amostra"))
        assert (await h.wait_run(origem.id)).status == "completed"
        assert len(fake.messages) == 5                                      # fora da prova: a lista inteira
        [flow_id] = [str(r["id"]) for r in s.db.query("SELECT id FROM flows ORDER BY id")]
        s.db.execute("UPDATE flows SET status='candidate' WHERE id=?", (flow_id,))
        prova = s.runs.create(RunCreate(command=ALL, instance_ids=["android-01"], idempotency_key="k-prova-amostra"),
                              prova=flow_id)
        assert (await h.wait_run(prova.id)).status == "completed"
        assert len(fake.messages) == 5 + 3                                  # a prova: só os 3 primeiros
        assert [m.contact for m in fake.messages[5:]] == CONTACTS[:3]       # na ordem da tela, sem sorteio
        [motivo] = [str(r["reason"]) for r in s.db.query(
            "SELECT v.reason FROM plan_versions v JOIN objectives o ON o.id = v.objective_id"
            " WHERE o.run_id=? AND v.version > 1 ORDER BY v.version", (prova.id,))]
        assert motivo.startswith("Expandido para 3 item(ns)") and amostra_do_rastro([motivo]) == (3, 5)
        itens = {str(r["key"]).rsplit("_i", 1)[-1] for r in s.db.query(
            "SELECT key FROM steps WHERE run_id=? AND key LIKE '%\\_i%' ESCAPE '\\' ORDER BY key", (prova.id,))}
        assert itens == {"1", "2", "3"}                                     # os de fora nem viram etapa
    finally:
        if h.state is not None:
            await h.state.stop()


# ------------------------------------------------------------------ o rótulo na evidência
@pytest.mark.parametrize(("motivo", "rotulo"), [
    (f"Expandido para 3 item(ns) lidos em 'coletar' ({rastro_da_amostra(3, 8)})", "provado em amostra de 3 (de 8 itens)"),
    (f"Expandido para 2 item(ns) lidos em 'coletar' ({rastro_da_amostra(2, 2)})", None),
])
def test_a_evidencia_da_prova_diz_a_amostra(mundo: Mundo, motivo: str, rotulo: str | None) -> None:  # noqa: F811
    flow_id = _candidato(mundo, concordancias=5)
    _prova(mundo, "p-1", flow_id, [("abrir_perfil", "succeeded"), ("seguir", "pending")])
    _versao_2(mundo, "p-1", motivo, ["seguir_i1", "seguir_i2"])
    _digerir(mundo, "p-1")
    [(stance, _, detalhe)] = _evidencias(mundo, flow_id, "p-1")
    assert stance == "for" and "3/3 etapas comprovadas" in detalhe          # uma evidência a favor como outra
    if rotulo is None:
        assert "amostra" not in detalhe
    else:
        assert detalhe.endswith(rotulo)


def test_o_parecer_do_curador_ve_a_amostra_na_evidencia(tmp_path: Path) -> None:
    """O dossiê do curador cita a evidência com `amostra: "N de M"` e explica o que é; sem amostra, nem a chave."""
    db = banco_migrado(tmp_path, "dossie-amostra.sqlite3")
    fluxo(db, "comentar-1", "Comente no post de {perfil}", plano=PLANO, status="candidate")
    catalogo = CatalogoPorApp({(INSTAGRAM, "OPEN_PROFILE"): LER, (INSTAGRAM, "CREATE_COMMENT"): COMENTAR,
                               (OUTLOOK, "READ_EMAIL"): LER})
    repo = SqlLearningRepository(db, guarda_do_fluxo=GuardaDoFluxo(db, SqlSkillRepository(db, ValidadorFalso())),
                                 precos=dict)
    servico = LearningService(repo, FontesSql(db), TriagemDeCredencial(), ajustes=Ajustes,
                              relogio=lambda: datetime(2026, 10, 4, 12, 0, tzinfo=UTC),
                              retencao_de_logs_dias=lambda: 14, catalogo_de_risco=catalogo)
    for i, detalhe in enumerate(["[abc123] prova: 9/9 etapas comprovadas — provado em amostra de 3 (de 8 itens)",
                                 "[abc123] prova: 4/4 etapas comprovadas"], start=1):
        db.execute("INSERT INTO learning_evidence(item_ref, stance, origin_ref, run_id, simulated, detail, observed_at)"
                   " VALUES ('fluxo:comentar-1','for',?,?,1,?,?)", (f"run:r-{i}", f"r-{i}", detalhe,
                                                                      f"2026-10-04T11:0{i}:00Z"))
    d = DossiesSql(db, servico, repo, catalogo).dossie(servico.entrada(LivroKind.FLUXO, "comentar-1"))
    assert d is not None
    dados = d.como_dados()["evidencias"]
    assert isinstance(dados, dict) and "amostra_e" in dados and "provado em amostra de N" in str(dados["amostra_e"])
    por_run = {x["run_id"]: x for x in dados["lista"]}                   # type: ignore[union-attr]
    assert por_run["r-1"]["amostra"] == "3 de 8" and "amostra" not in por_run["r-2"]
    db.close()
