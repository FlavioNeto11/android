"""30.50: o P4 só oferece aparelho que tem cada variável de aparelho que o plano da prova usa.

Em 04/10 a prova fec1a1 caiu no android-04, sem `account_label`: o `text={account_label}` do `check_account` virou
vazio, a etapa não tinha como passar e a prova fechou `ator_sem_acao`, gastando uma das duas provas da janela.

O que se prova:
- a fonte lê do plano que a validação rodaria as `VARIAVEIS_DE_APARELHO` usadas (`{account_label}`), e nenhuma quando o
  plano não as usa ou não há plano;
- o despacho tira da lista o aparelho sem valor para a variável exigida (o android-04 sem conta), e sem exigência a
  lista é a de antes;
- toda variável de aparelho do domínio tem a sua coluna no despacho;
- o serviço pede ao despacho os aparelhos com a exigência do plano do pedido.

Armação: banco migrado (`fake_skills.banco`) e um parque falso só com `candidatos_de`. Nível de prova: `simulated`.
"""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from app.db import Database
from app.models import Plan, PlannerInfo, PlanStep, Postcondition
from app.modules.learning.domain.validacao import VARIAVEIS_DE_APARELHO
from app.modules.learning.infrastructure.ligar_validacao import COLUNA_DA_VARIAVEL, DespachoDoParque
from app.modules.learning.infrastructure.validacoes_sql import FontesDaValidacaoSql
from app.taskqueue.balanceamento import Candidato

from .fake_skills import banco as banco_migrado

QA = "com.pocqa.messenger"
MOLDE = "No QA Messenger, conferir {alvo}"


def _plano(valor: str) -> str:
    post = Postcondition(kind="element_present", value=valor, description="d")
    return Plan(summary="conferir", app_id="qa-messenger", planner=PlannerInfo(provider="fluxo", model="m", simulated=True),
                steps=[PlanStep(key="check_account", title="Confirmar a conta", goal="conferir",
                                postcondition=post)]).model_dump_json()


def _fluxo(db: Database, fid: str, valor: str) -> None:
    db.execute("INSERT INTO flows(id, name, match_key, command_template, plan, app_id, status, created_at)"
               " VALUES (?,?,?,?,?,'qa-messenger','candidate','2026-10-04T11:00:00Z')",
               (fid, fid, f"cmd {fid}", MOLDE, _plano(valor)))


class Parque:
    def candidatos_de(self, ids: Sequence[str]) -> list[Candidato]:
        return [Candidato(instance_id=i, servidor="central", ligado=True, acordavel=False, ocupado=False) for i in ids]


def _aparelho(db: Database, idx: int, conta: str | None) -> str:
    iid = f"android-{idx:02d}"
    db.execute("INSERT INTO instances(id, idx, avd_name, console_port, system_port, mjpeg_port, chromedriver_port,"
               " account_label) VALUES (?,?,?,?,?,?,?,?)",
               (iid, idx, f"avd-{idx}", 5600 + 2 * idx, 8200 + idx, 9200 + idx, 9500 + idx, conta))
    db.execute("INSERT INTO device_app_state(instance_id, package_name, state) VALUES (?,?,'ready')", (iid, QA))
    return iid


def test_a_fonte_le_as_variaveis_de_aparelho_do_plano(tmp_path: Path) -> None:
    db = banco_migrado(tmp_path, "variaveis.sqlite3")
    fontes = FontesDaValidacaoSql(db, precos=dict, fluxo_ativo_para=lambda c: False, vetado=lambda e: False)
    _fluxo(db, "f-conta", "id=com.pocqa.messenger:id/account_label|text={account_label}")
    _fluxo(db, "f-sem", "id=com.pocqa.messenger:id/message_input")
    comando = "No QA Messenger, conferir a conta"
    assert fontes.variaveis_de_aparelho("fluxo:f-conta", comando) == frozenset({"account_label"})
    assert fontes.variaveis_de_aparelho("fluxo:f-sem", comando) == frozenset()
    assert fontes.variaveis_de_aparelho("fluxo:nao-existe", comando) == frozenset()
    db.close()


def test_o_despacho_nao_oferece_aparelho_sem_a_variavel_exigida(tmp_path: Path) -> None:
    db = banco_migrado(tmp_path, "aparelhos.sqlite3")
    despacho = DespachoDoParque(db, None, Parque(), saudavel=lambda: True)  # type: ignore[arg-type]
    sem_conta = _aparelho(db, 4, None)                    # o android-04 da fec1a1
    em_branco = _aparelho(db, 5, "  ")
    com_conta = _aparelho(db, 9, "qa-user-09")
    exigindo = [a.id for a in despacho.aparelhos([QA], frozenset({"account_label"}))]
    assert exigindo == [com_conta]
    assert sorted(a.id for a in despacho.aparelhos([QA])) == sorted([sem_conta, em_branco, com_conta])  # sem exigência
    db.close()


def test_toda_variavel_de_aparelho_tem_a_sua_coluna() -> None:
    assert set(VARIAVEIS_DE_APARELHO) == set(COLUNA_DA_VARIAVEL)
