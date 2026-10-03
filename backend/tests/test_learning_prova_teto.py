"""30.41: o teto da prova de fluxo é proporcional ao plano, `min(0,40; 0,05 + 0,02 × etapas)`, e o plano que passa do
máximo não despacha (`recusada/plano_acima_do_teto`, sem execução nem gasto). O `for_each` conta pelo tamanho da lista
que a execução de origem do fluxo coletou. O mesmo vale para o pedido de RECEITA cujo comando resolve num fluxo ativo
grande; a receita segue com o teto fixo do 30.40. Banco migrado (SQLite, ou PostgreSQL com `TEST_DATABASE_URL`),
parque e fila falsos. Nível de prova: `simulated`."""
from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest

from app.db import Database
from app.models import Plan, PlannerInfo, PlanStep, Postcondition
from app.modules.learning.application.validacao import AjustesDaValidacao, ServicoDeValidacao
from app.modules.learning.domain.ciclo import SkillState
from app.modules.learning.domain.livro import EntradaDoLivro
from app.modules.learning.domain.validacao import teto_da_prova
from app.modules.learning.domain.vocabulario import LivroKind, Modo, Origem
from app.modules.learning.infrastructure.validacoes_sql import FontesDaValidacaoSql, RegistroDeValidacoesSql
from app.util import to_iso

from .fake_skills import banco as banco_migrado
from .test_learning_prova_validacao import ORIGEM, ParqueDeProva
from .test_learning_validacao_sql import B, COMANDO, MOLDE, PEDE, QA, Relogio, _ap, _linha

Mundo = tuple[Database, ServicoDeValidacao, ParqueDeProva, dict[str, object], dict[str, Plan]]


def _post(kind: str) -> Postcondition:
    return Postcondition(kind=kind, value="x", description="x")  # type: ignore[arg-type]


def _plano(fluxo_id: str, *, modelos: int) -> Plan:
    """3 etapas fixas (abrir, coletar, relatar) e `modelos` etapas-modelo sobre a coleta (0 = sem `for_each`)."""
    passos = [PlanStep(key="open_app", title="a", goal="a", postcondition=_post("app_foreground")),
              PlanStep(key="collect", title="c", goal="c", depends_on=["open_app"], postcondition=_post("items_collected"))]
    anterior = "collect"
    for i in range(modelos):
        passos.append(PlanStep(key=f"m{i}", title="{item}", goal="g", depends_on=[anterior], for_each="collect",
                               postcondition=_post("text_visible")))
        anterior = f"m{i}"
    passos.append(PlanStep(key="report", title="r", goal="r", depends_on=[anterior], postcondition=_post("text_visible")))
    return Plan(summary="x", planner=PlannerInfo(provider="fluxo", model=f"fluxo:{fluxo_id}", simulated=True),
                steps=passos)


@pytest.fixture
def mundo(tmp_path: Path) -> Iterator[Mundo]:
    db = banco_migrado(tmp_path, "teto.sqlite3")
    db.execute("INSERT INTO apps(id, name, package, builtin, category) VALUES ('qa-messenger','QA Messenger',?,1,'qa')",
               (QA,))
    db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at)"
               " VALUES (?,'k-origem',?,'execute','completed',0,'[\"android-01\"]',?)",
               (ORIGEM, COMANDO, to_iso(datetime.now())))
    ajustes: dict[str, object] = {"modo": Modo.ON, "teto_por_pedido_usd": 0.15}
    ativo: dict[str, Plan] = {}
    parque = ParqueDeProva(db)
    parque.lista = [_ap("android-10")]
    fontes = FontesDaValidacaoSql(db, precos=lambda: {"m": [3.0, 0.3, 3.75, 15.0]},
                                  fluxo_ativo_para=lambda c: c == COMANDO, vetado=lambda e: False,
                                  plano_ativo_para=lambda c: ativo.get(c))
    servico = ServicoDeValidacao(RegistroDeValidacoesSql(db), fontes, parque, triagem=lambda t: "senha" in t,
                                 ajustes=lambda: AjustesDaValidacao(**ajustes),  # type: ignore[arg-type]
                                 relogio=Relogio())
    yield db, servico, parque, ajustes, ativo
    db.close()


def _fluxo(db: Database, fid: str, plano: Plan, *, coletados: int | None) -> EntradaDoLivro:
    """O fluxo `fid`, aprendido da execução de ORIGEM; `coletados`: o tamanho da lista que ela coletou (`None`: nada)."""
    db.execute("INSERT INTO flows(id, name, match_key, command_template, plan, app_id, source_run_id, status, created_at)"
               " VALUES (?,?,?,?,?,?,?,?,?)", (fid, fid, f"cmd {fid}", MOLDE, plano.model_dump_json(), "qa-messenger",
                                               ORIGEM, "candidate", to_iso(datetime.now())))
    db.execute("INSERT INTO flow_required_apps(flow_id, app_id) VALUES (?,?)", (fid, "qa-messenger"))
    if coletados is not None:
        db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version, collected) VALUES (?,?,?,?,?,?)",
                   (f"{ORIGEM}:{fid}", ORIGEM, "android-01", "succeeded", 2,
                    json.dumps({"collect": [f"c{i}" for i in range(coletados)]})))
    return EntradaDoLivro(kind=LivroKind.FLUXO, ref=fid, state=SkillState.PUBLISHED, native_status="candidate", title=fid,
                          app=QA, origin=Origem.EXECUCAO, side_effect=True, nasceu_de=ORIGEM, apps=(QA,))


def _receita() -> EntradaDoLivro:
    return EntradaDoLivro(kind=LivroKind.RECEITA, ref="78", state=SkillState.PUBLISHED, native_status="active",
                          title="send_message (v1)", app=QA, origin=Origem.EXECUCAO, side_effect=True, nasceu_de=ORIGEM)


# ------------------------------------------------------------------ a regra pura
def test_o_teto_e_proporcional_com_piso_e_maximo() -> None:
    assert teto_da_prova(0) == pytest.approx(0.05)
    assert teto_da_prova(6) == pytest.approx(0.17)
    assert teto_da_prova(11) == pytest.approx(0.27)
    assert teto_da_prova(17) == pytest.approx(0.39)
    assert teto_da_prova(18) is None                     # 0,41 passa do máximo: não despacha
    assert teto_da_prova(None) is None                   # tamanho desconhecido: o lado seguro


# ------------------------------------------------------------------ o fluxo
def test_o_for_each_de_8_contatos_fecha_acima_do_teto_sem_executar(mundo: Mundo) -> None:
    """O caso do P4 de 03/10 (0f0d85): 3 fixas + 4 modelos × 8 contatos = 35 etapas, teto 0,75 > 0,40."""
    db, servico, parque, _, _ = mundo
    pid = servico.ao_parecer(_fluxo(db, "f-grande", _plano("f-grande", modelos=4), coletados=8), "lr-1", PEDE, B)
    assert pid is not None
    assert servico.uma_volta(lambda: 1) is None
    linha = _linha(db, pid)
    assert (linha["estado"], linha["motivo"], linha["run_id"]) == ("recusada", "plano_acima_do_teto", None)
    assert parque.provas == []


def test_o_for_each_pequeno_despacha_com_o_teto_proporcional(mundo: Mundo) -> None:
    db, servico, parque, _, _ = mundo
    pid = servico.ao_parecer(_fluxo(db, "f-dois", _plano("f-dois", modelos=2), coletados=2), "lr-1", PEDE, B)
    assert pid is not None and servico.uma_volta(lambda: 1) is not None
    linha = _linha(db, pid)
    assert linha["estado"] == "rodando" and parque.provas == ["f-dois"]
    assert float(str(linha["teto_usd"])) == pytest.approx(teto_da_prova(3 + 2 * 2))      # 0,19, não o 0,15 fixo


def test_o_for_each_sem_lista_coletada_na_origem_nao_despacha(mundo: Mundo) -> None:
    db, servico, parque, _, _ = mundo
    pid = servico.ao_parecer(_fluxo(db, "f-sem", _plano("f-sem", modelos=1), coletados=None), "lr-1", PEDE, B)
    assert pid is not None and servico.uma_volta(lambda: 1) is None
    assert _linha(db, pid)["motivo"] == "plano_acima_do_teto" and parque.provas == []


# ------------------------------------------------------------------ a receita
def test_a_receita_que_resolve_num_fluxo_ativo_grande_fecha_sem_executar(mundo: Mundo) -> None:
    db, servico, parque, _, ativo = mundo
    plano = _plano("f-ativo", modelos=4)
    _fluxo(db, "f-ativo", plano, coletados=8)
    ativo[COMANDO] = plano
    pid = servico.ao_parecer(_receita(), "lr-1", PEDE, B)
    assert pid is not None and servico.uma_volta(lambda: 1) is None
    linha = _linha(db, pid)
    assert (linha["estado"], linha["motivo"]) == ("recusada", "plano_acima_do_teto")
    assert parque.enfileiradas == []


def test_a_receita_com_fluxo_ativo_pequeno_segue_com_o_teto_fixo(mundo: Mundo) -> None:
    """O "teto gravado não muda" e o legado que herda (30.40) seguem valendo para a receita."""
    db, servico, _, ajustes, ativo = mundo
    plano = _plano("f-ativo", modelos=0)
    _fluxo(db, "f-ativo", plano, coletados=None)
    ativo[COMANDO] = plano
    pid = servico.ao_parecer(_receita(), "lr-1", PEDE, B)                 # nasce com 0,15
    assert pid is not None
    ajustes["teto_por_pedido_usd"] = 0.20
    assert servico.uma_volta(lambda: 1) is not None
    assert float(str(_linha(db, pid)["teto_usd"])) == pytest.approx(0.15)


def test_a_receita_legada_sem_teto_herda_o_da_config(mundo: Mundo) -> None:
    db, servico, _, ajustes, _ = mundo
    pid = servico.ao_parecer(_receita(), "lr-1", PEDE, B)
    assert pid is not None
    db.execute("UPDATE learning_validations SET teto_usd=NULL WHERE id=?", (pid,))
    ajustes["teto_por_pedido_usd"] = 0.12
    assert servico.uma_volta(lambda: 1) is not None
    assert float(str(_linha(db, pid)["teto_usd"])) == pytest.approx(0.12)
