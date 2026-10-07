"""30.39: a evidência datada da receita (`application/evidencia_da_receita.py`, `infrastructure/reproducao_sql.py`).

- o minerador do digest grava `learning_evidence` de `receita:<id>` por (receita, execução, posição): a favor quando a
  etapa foi conduzida só pela receita e comprovada, contra quando a receita foi consultada e a etapa não terminou por
  ela; idempotente; execução simulada marcada; datada pela etapa;
- a retrocarga (passo da curadoria) completa o que o digest não viu, sem passar da retenção;
- o dossiê da receita ganha `evidencias.contadores_e` e, sem amostra de sombra, `evidencias.sombra_e`; o do fluxo não;
- a saúde da receita não muda com a evidência a favor nem com a contrária fora da janela de contestação.

Nível de prova: `simulated` (banco de teste migrado; nenhuma execução, aparelho ou conta reais).
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest

from app.db import Database
from app.modules.learning.application.evidencia_da_receita import RetrocargaDaReceita
from app.modules.learning.application.ports import Ajustes
from app.modules.learning.application.servico import LearningService
from app.modules.learning.domain.curador import CONTADORES_DA_RECEITA, SOMBRA_DA_RECEITA
from app.modules.learning.domain.vocabulario import LivroKind
from app.modules.learning.infrastructure import ligar_nativos
from app.modules.learning.infrastructure.dossies import DossiesSql
from app.modules.learning.infrastructure.fontes import FontesSql
from app.modules.learning.infrastructure.reproducao_sql import ReproducoesSql
from app.modules.learning.infrastructure.segredo import TriagemDeCredencial
from app.modules.learning.infrastructure.sql_repository import SqlLearningRepository
from app.util import now, to_iso

from .fake_skills import banco as banco_migrado

PACOTE = "com.instagram.android"
AGORA = now()
ANTIGA = to_iso(AGORA - timedelta(days=400))


class Mundo:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.repo = SqlLearningRepository(db, precos=dict)
        self.servico = LearningService(self.repo, FontesSql(db), TriagemDeCredencial(), ajustes=Ajustes, relogio=now,
                                       retencao_de_logs_dias=lambda: 14)
        ligar_nativos.ligar(self.servico, self.repo, db)
        self.limite = 200
        self._runs: set[str] = set()

    def receita(self, passo: str = "enviar", *, status: str = "active", shadow_total: int = 0,
                versao_do_app: str = "447") -> int:
        acoes = [{"tool": "tap", "args": {}, "selectors": [{"rid": "botao"}], "commit": False}]
        return int(self.db.inserted_id(
            "INSERT INTO recipes(app_package, app_version, app_signature, variant, step_hash, step_key, version, status,"
            " actions, learned_from_step, created_at, shadow_total, replay_ok, replay_fail)"
            " VALUES (?,?,?,?,?,?,1,?,?,?,?,?,33,2)",
            (PACOTE, versao_do_app, "sig", "pt/420", f"h-{passo}", passo, status, json.dumps(acoes), f"r0:a:v1:{passo}",
             ANTIGA, shadow_total)))

    def execucao(self, run_id: str, *, simulada: bool = False, status: str = "completed") -> None:
        self.db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids,"
                        " created_at) VALUES (?,?,?,?,?,?,?,?)",
                        (run_id, f"k-{run_id}", "enviar", "execute", status, int(simulada),
                         json.dumps(["android-06"]), ANTIGA))
        self.db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version) VALUES (?,?,?,?,?)",
                        (f"{run_id}:o", run_id, "android-06", "succeeded", 1))
        self._runs.add(run_id)

    def etapa(self, run_id: str, seq: int, *, driven_by: str | None, receita: int | None, status: str = "succeeded",
              aparelho: str = "android-06", fim: str = "2026-10-01T12:00:00.000Z",
              tentativas: tuple[int | None, ...] | None = None) -> str:
        """`tentativas`: o `recipe_id` de cada tentativa, em ordem (o padrão é uma só, com `receita`)."""
        sid = f"{run_id}:{aparelho}:v1:e{seq}"
        self.db.execute(
            "INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal,"
            " postcondition, timeout_s, max_attempts, status, driven_by, finished_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sid, run_id, f"{run_id}:o", aparelho, 1, seq, f"e{seq}", "enviar", "enviar", "{}", 60, 3, status,
             driven_by, fim))
        for n, rid in enumerate(tentativas if tentativas is not None else (receita,), start=1):
            self.db.execute("INSERT INTO attempts(id, step_id, number, status, started_at, finished_at, recipe_id)"
                            " VALUES (?,?,?,?,?,?,?)", (f"{sid}:a{n}", sid, n, status, fim, fim, rid))
        return sid

    def digerir(self, run_id: str) -> None:
        r = self.servico.digerir_execucao(run_id)
        assert not r.falhas, r

    def linhas(self, receita: int) -> list[dict[str, object]]:
        return [dict(r) for r in self.db.query(
            "SELECT stance, origin_ref, run_id, instance_id, app_version, simulated, detail, observed_at"
            " FROM learning_evidence WHERE item_ref=? ORDER BY origin_ref, stance", (f"receita:{receita}",))]

    def dossie(self, kind: LivroKind, ref: str) -> dict[str, object]:
        d = DossiesSql(self.db, self.servico, self.repo, None).dossie(self.servico.entrada(kind, ref))
        assert d is not None
        return d.como_dados()


@pytest.fixture
def mundo(tmp_path: Path) -> Iterator[Mundo]:
    db = banco_migrado(tmp_path, "evidencia_receita.sqlite3")
    yield Mundo(db)
    db.close()


# ------------------------------------------------------------------ a favor e contra, por etapa
def test_a_favor_e_contra_por_etapa(mundo: Mundo) -> None:
    ok, divergiu, sem_ator = mundo.receita("a"), mundo.receita("b"), mundo.receita("c")
    mundo.execucao("run-1")
    mundo.etapa("run-1", 1, driven_by="recipe", receita=ok)
    mundo.etapa("run-1", 2, driven_by="recipe+ai", receita=divergiu)
    mundo.etapa("run-1", 3, driven_by="sem_ator", receita=sem_ator)
    mundo.digerir("run-1")
    [favor] = mundo.linhas(ok)
    assert (favor["stance"], favor["origin_ref"], favor["run_id"], favor["instance_id"]) == (
        "for", "reproducao:run-1", "run-1", "android-06")
    assert (favor["app_version"], favor["simulated"], favor["detail"]) == ("447", 0, "etapa 1 (e1): reproduzida")
    assert favor["observed_at"] == "2026-10-01T12:00:00.000Z"                     # a data da etapa, não a do digest
    [contra] = mundo.linhas(divergiu)
    assert (contra["stance"], contra["detail"]) == ("against", "etapa 2 (e2): divergiu, a IA assumiu")
    [contra_sem_ator] = mundo.linhas(sem_ator)
    assert contra_sem_ator["stance"] == "against" and "sem a IA decidir" in str(contra_sem_ator["detail"])


def test_o_que_nao_e_reproducao_nao_vira_linha(mundo: Mundo) -> None:
    r = mundo.receita()
    mundo.execucao("run-1")
    mundo.etapa("run-1", 1, driven_by="ai", receita=None)                          # a IA conduziu, sem receita
    mundo.etapa("run-1", 2, driven_by="ai", receita=r)                             # sombra: a IA decide
    mundo.etapa("run-1", 3, driven_by="recipe", receita=r, status="failed")         # `recipe` sem comprovação
    mundo.etapa("run-1", 4, driven_by=None, receita=r)                             # legado: sem `driven_by`
    mundo.digerir("run-1")
    assert mundo.linhas(r) == []


def test_a_ultima_tentativa_da_etapa_decide(mundo: Mundo) -> None:
    r1, r2 = mundo.receita("a"), mundo.receita("b")
    mundo.execucao("run-1")
    # tentativa 1 consultou r1 e divergiu; a 2 reproduziu r2 limpa: o `driven_by` final é `recipe` e é de r2
    mundo.etapa("run-1", 1, driven_by="recipe", receita=None, tentativas=(r1, r2))
    mundo.digerir("run-1")
    assert mundo.linhas(r1) == []
    assert [x["stance"] for x in mundo.linhas(r2)] == ["for"]


def test_uma_linha_por_receita_execucao_e_posicao(mundo: Mundo) -> None:
    r = mundo.receita()
    mundo.execucao("run-1")
    for seq in (1, 2, 3):                                                          # o for_each: três cópias da etapa
        mundo.etapa("run-1", seq, driven_by="recipe", receita=r, fim=f"2026-10-01T12:0{seq}:00.000Z")
    mundo.etapa("run-1", 4, driven_by="recipe+ai", receita=r)                      # outra posição: outra linha
    mundo.digerir("run-1")
    linhas = mundo.linhas(r)
    assert [x["stance"] for x in linhas] == ["against", "for"]
    favor = linhas[1]
    assert favor["detail"] == "etapa 1 (e1): reproduzida (e mais 2 da mesma receita nesta execução)"
    assert favor["observed_at"] == "2026-10-01T12:03:00.000Z"                      # o fim da mais recente


def test_minerar_duas_vezes_nao_duplica(mundo: Mundo) -> None:
    r = mundo.receita()
    mundo.execucao("run-1")
    mundo.etapa("run-1", 1, driven_by="recipe", receita=r)
    mundo.digerir("run-1")
    mundo.digerir("run-1")
    assert len(mundo.linhas(r)) == 1


def test_execucao_simulada_vai_marcada(mundo: Mundo) -> None:
    r = mundo.receita()
    mundo.execucao("run-s", simulada=True)
    mundo.etapa("run-s", 1, driven_by="recipe", receita=r)
    mundo.digerir("run-s")
    [x] = mundo.linhas(r)
    assert x["simulated"] == 1


# ------------------------------------------------------------------ a retrocarga
def test_a_retrocarga_completa_o_que_o_digest_nao_viu_e_e_idempotente(mundo: Mundo) -> None:
    r = mundo.receita()
    mundo.execucao("run-velha")
    mundo.etapa("run-velha", 1, driven_by="recipe", receita=r, fim="2026-09-20T10:00:00.000Z")
    mundo.execucao("run-andando", status="running")       # ainda não assentou: o "contra" fica de fora (31.178)
    mundo.etapa("run-andando", 1, driven_by="recipe+ai", receita=r, status="failed")
    passo = next(p for p in mundo.servico._passos if isinstance(p, RetrocargaDaReceita))
    assert passo.executar(AGORA) == 1
    assert passo.executar(AGORA) == 0
    [x] = mundo.linhas(r)
    assert (x["origin_ref"], x["observed_at"]) == ("reproducao:run-velha", "2026-09-20T10:00:00.000Z")


def test_a_retrocarga_respeita_a_retencao(mundo: Mundo) -> None:
    r = mundo.receita()
    for i in (1, 2, 3):
        mundo.execucao(f"run-{i}")
        mundo.etapa(f"run-{i}", 1, driven_by="recipe", receita=r, fim=f"2026-09-2{i}T10:00:00.000Z")
    passo = RetrocargaDaReceita(mundo.repo, ReproducoesSql(mundo.db), limite_por_receita=lambda: 2)
    assert passo.executar(AGORA) == 2                                              # as duas mais novas
    assert passo.executar(AGORA) == 0                                              # cheia: não desfaz a purga
    assert [x["origin_ref"] for x in mundo.linhas(r)] == ["reproducao:run-2", "reproducao:run-3"]


# ------------------------------------------------------------------ o dossiê
def test_dossie_da_receita_traz_a_lista_e_as_duas_notas(mundo: Mundo) -> None:
    r = mundo.receita(shadow_total=0)
    mundo.execucao("run-1")
    mundo.etapa("run-1", 1, driven_by="recipe", receita=r)
    mundo.digerir("run-1")
    ev = mundo.dossie(LivroKind.RECEITA, str(r))["evidencias"]
    assert isinstance(ev, dict) and ev["total"] == 1
    [linha] = ev["lista"]                                                           # type: ignore[index]
    assert (linha["posicao"], linha["origin_ref"], linha["run_id"], linha["aparelho"], linha["app_version"],
            linha["simulated"]) == ("for", "reproducao:run-1", "run-1", "android-06", "447", False)
    assert ev["contadores_e"] == CONTADORES_DA_RECEITA and ev["sombra_e"] == SOMBRA_DA_RECEITA


def test_sombra_e_so_sem_amostra_de_sombra(mundo: Mundo) -> None:
    r = mundo.receita(shadow_total=3)
    ev = mundo.dossie(LivroKind.RECEITA, str(r))["evidencias"]
    assert isinstance(ev, dict) and "contadores_e" in ev and "sombra_e" not in ev


def test_o_dossie_do_fluxo_nao_ganha_as_chaves_novas(mundo: Mundo) -> None:
    plano = {"summary": "f1", "app_id": "instagram", "parameters": {},
             "steps": [{"key": "a", "title": "a", "goal": "a", "side_effect": False,
                        "postcondition": {"kind": "app_foreground", "value": "x", "description": "x"}}]}
    mundo.db.execute("INSERT INTO flows(id, name, match_key, command_template, plan, app_id, status, created_at, source)"
                     " VALUES ('f1','f1','cmd f1','cmd f1',?,?,'active',?,'run')",
                     (json.dumps(plano), "instagram", ANTIGA))
    ev = mundo.dossie(LivroKind.FLUXO, "f1")["evidencias"]
    assert isinstance(ev, dict) and "contadores_e" not in ev and "sombra_e" not in ev


# ------------------------------------------------------------------ a saúde não muda
def test_a_saude_da_receita_nao_muda_com_a_evidencia_datada(mundo: Mundo) -> None:
    r = mundo.receita()
    antes = mundo.servico.detalhe(LivroKind.RECEITA, str(r)).saude
    mundo.execucao("run-1")
    mundo.etapa("run-1", 1, driven_by="recipe", receita=r)
    mundo.execucao("run-2")
    mundo.etapa("run-2", 1, driven_by="recipe+ai", receita=r, fim=ANTIGA)           # contra, fora da janela de 30 dias
    mundo.digerir("run-1")
    mundo.digerir("run-2")
    assert [x["stance"] for x in mundo.linhas(r)] == ["for", "against"]
    assert mundo.servico.detalhe(LivroKind.RECEITA, str(r)).saude == antes


def test_o_contra_da_reproducao_dentro_da_janela_nao_contesta_a_receita(mundo: Mundo) -> None:
    """O `against` datado da reprodução é o mesmo `replay_fail` que a saúde já lê nos contadores: dentro da janela de
    contestação ele não vira `contestado_recentemente` (antes da origem própria, a receita ia a `degradando`)."""
    r = mundo.receita()
    antes = mundo.servico.detalhe(LivroKind.RECEITA, str(r)).saude
    mundo.execucao("run-1")
    mundo.etapa("run-1", 1, driven_by="recipe+ai", receita=r)                        # contra, dentro da janela
    mundo.digerir("run-1")
    assert [(x["stance"], x["origin_ref"]) for x in mundo.linhas(r)] == [("against", "reproducao:run-1")]
    assert mundo.servico.detalhe(LivroKind.RECEITA, str(r)).saude == antes


def test_o_detalhe_do_item_lista_a_evidencia_na_ordem_do_acontecido(mundo: Mundo) -> None:
    """30.44: a retrocarga grava depois linhas de antes (a receita:87); o detalhe as mostra pela data, não pelo id."""
    from app.modules.learning.presentation.livro import _detalhe

    r = mundo.receita()
    mundo.execucao("run-nova")
    mundo.etapa("run-nova", 1, driven_by="recipe", receita=r, fim="2026-10-03T22:00:00.000Z")
    mundo.digerir("run-nova")                                      # gravada primeiro (id menor), aconteceu depois
    mundo.execucao("run-velha")
    mundo.etapa("run-velha", 1, driven_by="recipe", receita=r, fim="2026-10-02T09:00:00.000Z")
    mundo.digerir("run-velha")
    d = mundo.servico.detalhe(LivroKind.RECEITA, str(r))
    assert d is not None
    saida = _detalhe(d, mundo.servico)["evidencias"]
    assert isinstance(saida, list) and [x["run_id"] for x in saida] == ["run-nova", "run-velha"]   # type: ignore[index]


# ------------------------------------------------------------------ 31.178: o "a favor" da execução aberta
def test_a_execucao_parada_ja_da_o_a_favor_e_o_contra_espera_ela_assentar(mundo: Mundo) -> None:
    """Na onda 1 (06/10) a execução parou em `awaiting_person` e o aprendizado dela deu 0: a evidência só saía quando
    ela assentava. A etapa comprovada pela receita é final; a que falhou pode voltar no "tentar de novo"."""
    r = mundo.receita()
    mundo.execucao("run-parada", status="awaiting_person")
    mundo.etapa("run-parada", 1, driven_by="recipe", receita=r)
    mundo.etapa("run-parada", 2, driven_by="recipe+ai", receita=r, status="failed")
    mundo.etapa("run-parada", 3, driven_by="recipe", receita=r, status="waiting_user")
    passo = next(p for p in mundo.servico._passos if isinstance(p, RetrocargaDaReceita))
    assert passo.executar(AGORA) == 1
    [x] = mundo.linhas(r)
    assert (x["origin_ref"], x["stance"]) == ("reproducao:run-parada", "for")
    assert passo.executar(AGORA) == 0                                              # idempotente
    mundo.db.execute("UPDATE runs SET status='completed_with_issues' WHERE id='run-parada'")
    assert passo.executar(AGORA) == 1                                              # assentou: agora o contra
    assert sorted(x["stance"] for x in mundo.linhas(r)) == ["against", "for"]
    mundo.digerir("run-parada")                                                    # o digest não repete o a favor
    assert sorted(x["stance"] for x in mundo.linhas(r)) == ["against", "for"]
