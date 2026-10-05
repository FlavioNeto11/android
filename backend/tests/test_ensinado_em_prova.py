"""30.81: o fluxo ensinado no modo treinamento nasce ativo, mas só vale para a persona que ensinou até a prova.

Antes, o `save` do treino publicava o fluxo na hora e ele casava para qualquer aparelho, sem prova nenhuma. Agora:
- o `match` com aparelhos só o usa quando TODOS os perfis são a persona do ensino; a prévia sem aparelhos casa como
  antes; a sessão sem persona não casa em lugar nenhum (e o salvar avisa);
- a volta da validação abre a prova (reprodução em outro aparelho, com o molde preenchido pelos exemplos da proposta e
  o aparelho do treino de fora), UM pedido por vez, idempotente pelo `review_id` `ensino:<sessão>`;
- a prova real a favor libera para todas; a decisão de uma pessoa também ("Confirmar que fica", desligar);
- o veredito CONTRÁRIO da prova desliga o fluxo e põe as receitas do mesmo treino em quarentena (o 30.80 B avisa); a
  falha de infraestrutura não rebaixa: o pedido reabre até o teto e, esgotado, passa à pessoa;
- o que a prova automática não cobre (classe C, efeito em app real, sem exemplo...) nasce `recusada` com o motivo e sai
  `learning.ensinado_espera_decisao`; a decisão da pessoa sai em `learning.ensinado_decidido`, uma por nascimento;
- o fluxo que não veio do treino não paga consulta a mais.

Nível de prova: `simulated` (banco de teste migrado pela fábrica da suíte; barramento e parque falsos; nenhuma IA).
"""
from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.db import Database
from app.modules.learning.application.nativos import ExecucaoAssentada, ProvaDaExecucao, SombraDosFluxos
from app.modules.learning.application.ports import Ajustes
from app.modules.learning.application.servico import LearningService
from app.modules.learning.application.validacao import AjustesDaValidacao, ServicoDeValidacao
from app.modules.learning.domain.ciclo import ConflitoDeEstado, SkillState
from app.modules.learning.domain.ensinado import (CAMPOS_DA_DECISAO, CAMPOS_DA_ESPERA, TIPO_DECIDIDO,
                                                  TIPO_ESPERA_DECISAO, TIPO_REBAIXADO, TIPO_SEM_RECEITA)
from app.modules.learning.domain.politica_de_risco import Classificacao
from app.modules.learning.domain.validacao import (TETO_DE_TENTATIVAS_DO_ENSINO, Ambiente, AparelhoCandidato,
                                                   Motivo)
from app.modules.learning.domain.vocabulario import LivroKind, Modo, Posicao
from app.modules.learning.infrastructure import ligar_nativos
from app.modules.learning.infrastructure.ensinado_sql import EnsinoDaValidacaoSql, LeitorDoEnsinadoSql
from app.modules.learning.infrastructure.eventos import EventosNoBarramento
from app.modules.learning.infrastructure.fontes import FontesSql
from app.modules.learning.infrastructure.segredo import TriagemDeCredencial
from app.modules.learning.infrastructure.sql_repository import SqlLearningRepository
from app.modules.learning.infrastructure.validacoes_sql import FontesDaValidacaoSql, RegistroDeValidacoesSql
from app.modules.learning.presentation.livro import _entrada  # noqa: PLC2701 - a entrada como o Livro a devolve
from app.social.capacidades import cobertura_do_fluxo
from app.taskqueue.flows import FlowStore, ensinado_em_prova
from app.taskqueue.recipes import RecipeStore
from app.training.skills import _aviso_sem_persona  # noqa: PLC2701 - a linha do aviso do salvar
from app.util import now_iso

from .fake_skills import banco as banco_migrado
from .test_d1_fluxos import MODELO, _comando, _plano
from .test_learning_pedir_validacao import C
from .test_learning_validacao_sql import B

ANA, BIA = "p-ana", "p-bia"
SESSAO = "trn-ensino-1"
APARELHO_DO_TREINO = "android-03"
TIPOS_DO_ENSINO = (TIPO_ESPERA_DECISAO, TIPO_DECIDIDO, TIPO_REBAIXADO, TIPO_SEM_RECEITA)


@dataclass
class Barramento:
    eventos: list[tuple[str, str, str, dict[str, object]]] = field(default_factory=list)

    def emit(self, kind: str, message: str, *, level: str = "info", instance_id: str | None = None,
             data: dict[str, object] | None = None) -> object:
        self.eventos.append((kind, message, level, dict(data or {})))
        return None

    def do_tipo(self, tipo: str) -> list[tuple[str, str, str, dict[str, object]]]:
        return [e for e in self.eventos if e[0] == tipo]


class Parado:
    """O parque com o central ocupado: a volta abre os pedidos e não despacha nenhum."""

    def ambiente(self) -> Ambiente:
        return Ambiente(saudavel=False, execucoes_em_curso=1)

    def aparelhos(self, pacotes: Sequence[str], exige: frozenset[str] = frozenset()) -> Sequence[AparelhoCandidato]:
        return []

    def gasto_da_operacao(self, agora: datetime, dias: int) -> float:
        return 0.0

    def enfileirar(self, comando: str, aparelho: str, chave: str, prova: str | None = None) -> str:
        raise AssertionError("nada é despachado nestes testes")


class Relogio:
    def __init__(self) -> None:
        self.agora = datetime.now(UTC)

    def __call__(self) -> datetime:
        return self.agora


class Mundo:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.bus = Barramento()
        self.repo = SqlLearningRepository(db, precos=dict)
        porta = EventosNoBarramento(self.bus)
        self.servico = LearningService(self.repo, FontesSql(db), TriagemDeCredencial(), ajustes=Ajustes,
                                       relogio=lambda: datetime.now(UTC), retencao_de_logs_dias=lambda: 14,
                                       eventos=porta, ensinado=porta, leitor_do_ensinado=LeitorDoEnsinadoSql(db))
        self.store = RecipeStore(db)
        self.flows = FlowStore(db)
        ligar_nativos.ligar(self.servico, self.repo, db, fluxos=self.flows, receitas=self.store,
                            decidir=lambda texto, run_id: None)
        self.risco: Classificacao | None = B
        self.relogio = Relogio()
        fontes = FontesDaValidacaoSql(db, precos=lambda: {"m": [3.0, 0.3, 3.75, 15.0]},
                                      fluxo_ativo_para=lambda c: False, vetado=lambda e: False)
        self.validacao = ServicoDeValidacao(RegistroDeValidacoesSql(db), fontes, Parado(),
                                            triagem=lambda t: "senha" in t,
                                            ajustes=lambda: AjustesDaValidacao(modo=Modo.ON),
                                            relogio=self.relogio, risco_do_item=lambda e: self.risco,
                                            ensino=EnsinoDaValidacaoSql(db, self.servico))

    # ---------------------------------------------------------------- o ensino
    def ensina(self, *, sessao: str = SESSAO, persona: str | None = ANA, exemplo: str = "@bia",
               efeito: bool = False, modelo: str = MODELO) -> str:
        agora = now_iso()
        self.db.execute(
            "INSERT INTO training_sessions(id, instance_id, profile_id, intent, status, proposal, created_at,"
            " updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (sessao, APARELHO_DO_TREINO, persona, "abrir um perfil", "saved",
             json.dumps({"parameters": [{"name": "username", "example": exemplo}]}), agora, agora))
        plano = _plano("{username}", efeito=efeito)
        plano.parameters = {"username": "{username}"}
        fid = self.flows.learn_from_plan(plano, modelo, source=f"training:{sessao}")
        self.db.execute("UPDATE training_sessions SET flow_id=? WHERE id=?", (fid, sessao))
        return fid

    def receita_do_treino(self, sessao: str = SESSAO) -> int:
        rid = self.store.save(package="com.instagram.android", app_version="1.0(1)", step_hash="h-abrir",
                              step_key="abrir_perfil", learned_from=f"training:{sessao}", candidate=False,
                              signature="", variant="en-US/xhdpi",
                              actions=[{"tool": "tap", "commit": False, "why": "abrir", "args": {},
                                        "selectors": [{"kind": "rid", "rid": "app:id/perfil"}]}])
        assert rid
        return rid

    def casa(self, profile_ids: list[str | None] | None) -> bool:
        return self.flows.match(_comando("@carla"), profile_ids) is not None

    def espera(self, fid: str) -> dict[str, str | None] | None:
        row = self.db.one("SELECT * FROM flows WHERE id=?", (fid,))
        assert row is not None
        return ensinado_em_prova(self.db, dict(row))

    # ---------------------------------------------------------------- a prova
    def execucao_de_prova(self, fid: str, run_id: str) -> None:
        self.db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids,"
                        " created_at, prova_fluxo_id) VALUES (?,?,?,?,?,?,?,?,?)",
                        (run_id, f"k-{run_id}", _comando("@bia"), "execute", "completed", 0,
                         json.dumps(["android-05"]), now_iso(), fid))

    def evidencia(self, fid: str, run_id: str, stance: str, *, simulada: bool = False) -> None:
        self.db.execute("INSERT INTO learning_evidence(item_ref, stance, origin_ref, run_id, instance_id, simulated,"
                        " detail, observed_at) VALUES (?,?,?,?,?,?,?,?)",
                        (f"fluxo:{fid}", stance, f"run:{run_id}", run_id, "android-05", int(simulada), "x",
                         now_iso()))

    def trilha_de_pessoa(self, fid: str, por: str) -> None:
        self.db.execute("INSERT INTO learning_transitions(item_ref, item_kind, scope_key, from_state, to_state,"
                        " reason, decided_by, decided_at) VALUES (?,?,?,?,?,?,?,?)",
                        (f"fluxo:{fid}", "fluxo", "", "published", "published", "confirmado que fica", por,
                         now_iso()))

    def minera_prova(self, fid: str, run_id: str, posicao: Posicao | None, *, simulada: bool = False) -> int:
        e = self.servico.entrada(LivroKind.FLUXO, fid)
        leitura = _LeituraFixa(self.db, ExecucaoAssentada(
            run_id=run_id, comando=_comando("@bia"), simulada=simulada, aparelho="android-05", assinatura=None,
            prova=ProvaDaExecucao(fluxo_id=fid, content_hash=e.content_hash or "", posicao=posicao,
                                  detalhe="a etapa 1 não comprovou")))
        sombra = SombraDosFluxos(self.servico, self.repo, leitura, concordancias=lambda: 1,
                                 ensinado=ligar_nativos.LeituraSql(self.db).ensinado_a_esperar)
        return sombra.minerar(run_id)

    # ---------------------------------------------------------------- a volta da validação
    def volta(self) -> None:
        self.validacao.uma_volta(lambda: 1)

    def pedidos(self, fid: str) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.query(
            "SELECT * FROM learning_validations WHERE item_ref=? ORDER BY created_at, id", (f"fluxo:{fid}",))]

    def status(self, fid: str) -> str:
        return str(self.db.scalar("SELECT status FROM flows WHERE id=?", (fid,)))


class _LeituraFixa(ligar_nativos.LeituraSql):
    """A leitura do central, com a execução de prova já lida (o veredito vem pronto)."""

    def __init__(self, db: Database, execucao: ExecucaoAssentada) -> None:
        super().__init__(db)
        self._fixa = execucao

    def execucao(self, run_id: str) -> ExecucaoAssentada | None:
        return self._fixa if run_id == self._fixa.run_id else None


@pytest.fixture
def mundo(tmp_path: Path) -> Iterator[Mundo]:
    db = banco_migrado(tmp_path, "ensinado-em-prova.sqlite3")
    db.execute("INSERT INTO apps(id, name, package, builtin) VALUES ('instagram','Instagram','com.instagram.android',0)")
    yield Mundo(db)
    db.close()


# ------------------------------------------------------------------ o match
def test_sem_prova_o_ensinado_so_casa_para_a_persona_que_ensinou(mundo: Mundo) -> None:
    fid = mundo.ensina()
    assert mundo.status(fid) == "active"
    assert mundo.espera(fid) == {"persona": ANA, "sessao": SESSAO}
    assert mundo.casa([ANA]) and mundo.casa([ANA, ANA])
    assert not mundo.casa([ANA, BIA])                    # duas personas, uma delas a do ensino: não casa
    assert not mundo.casa([BIA]) and not mundo.casa([None]) and not mundo.casa([])
    assert mundo.casa(None)                              # a prévia sem aparelhos casa como antes


def test_a_sessao_sem_persona_nao_casa_em_lugar_nenhum_e_o_salvar_avisa(mundo: Mundo) -> None:
    fid = mundo.ensina(persona=None)
    assert mundo.espera(fid) == {"persona": None, "sessao": SESSAO}
    assert not mundo.casa([ANA]) and not mundo.casa([None])
    assert mundo.casa(None)
    assert len(_aviso_sem_persona({"profile_id": None})) == 1 and _aviso_sem_persona({"profile_id": ANA}) == []


def test_a_prova_real_a_favor_libera_para_todas(mundo: Mundo) -> None:
    fid = mundo.ensina()
    mundo.execucao_de_prova(fid, "r-sim")
    mundo.evidencia(fid, "r-sim", "for", simulada=True)         # simulada não prova nada
    mundo.execucao_de_prova(fid, "r-inv")
    mundo.evidencia(fid, "r-inv", "for")
    mundo.evidencia(fid, "r-inv", "invalida")                   # a favor invalidado (30.42) também não
    assert not mundo.casa([BIA])
    mundo.execucao_de_prova(fid, "r-ok")
    mundo.evidencia(fid, "r-ok", "for")
    assert mundo.espera(fid) is None and mundo.casa([BIA]) and mundo.casa([ANA, BIA])


@pytest.mark.parametrize("por, libera", [("sistema", False), ("plataforma", False), ("training:trn-2", False),
                                         ("painel:dono", True)])
def test_so_a_decisao_de_uma_pessoa_libera(mundo: Mundo, por: str, libera: bool) -> None:
    fid = mundo.ensina()
    mundo.trilha_de_pessoa(fid, por)
    assert mundo.casa([BIA]) is libera


def test_o_fluxo_que_nao_veio_do_treino_nao_paga_consulta_a_mais(mundo: Mundo,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    plano = _plano("{username}")
    plano.parameters = {"username": "{username}"}
    mundo.flows.learn_from_plan(plano, MODELO, source="importado")
    consultas: list[str] = []
    one, query = mundo.db.one, mundo.db.query
    monkeypatch.setattr(mundo.db, "one", lambda sql, *a, **k: (consultas.append(sql), one(sql, *a, **k))[1])
    monkeypatch.setattr(mundo.db, "query", lambda sql, *a, **k: (consultas.append(sql), query(sql, *a, **k))[1])
    assert mundo.casa([BIA])
    assert not any("training_sessions" in s or "learning_evidence" in s for s in consultas), consultas


def test_a_cobertura_traz_o_campo_so_do_ensinado_em_prova(mundo: Mundo) -> None:
    fid = mundo.ensina()
    s = SimpleNamespace(db=mundo.db, releases=SimpleNamespace(promoted_release=lambda pacote: None))
    linha = dict(mundo.db.one("SELECT * FROM flows WHERE id=?", (fid,)) or {})
    assert cobertura_do_fluxo(s, linha, medianas=(None, None))["ensinado_em_prova"] == {"persona": ANA,
                                                                                         "sessao": SESSAO}
    mundo.trilha_de_pessoa(fid, "painel:dono")
    assert "ensinado_em_prova" not in cobertura_do_fluxo(s, linha, medianas=(None, None))


# ------------------------------------------------------------------ o rebaixamento
def test_a_prova_contra_desliga_o_fluxo_e_as_receitas_do_treino_e_o_30_80_b_avisa(mundo: Mundo) -> None:
    fid = mundo.ensina()
    rid = mundo.receita_do_treino()
    mundo.execucao_de_prova(fid, "r-contra")
    assert mundo.minera_prova(fid, "r-contra", Posicao.AGAINST) == 1
    assert mundo.status(fid) == "disabled"
    assert mundo.db.scalar("SELECT status FROM recipes WHERE id=?", (rid,)) == "quarantined"
    ultima = mundo.repo.trilha(f"fluxo:{fid}")[-1]
    assert (ultima.to_state, ultima.decided_by, ultima.run_id) == (SkillState.DISABLED, "sistema", "r-contra")
    avisados = {(e[3]["kind"], e[3]["ref"]) for e in mundo.bus.eventos if e[0] in (TIPO_REBAIXADO, TIPO_SEM_RECEITA)}
    assert ("fluxo", fid) in avisados and ("receita", str(rid)) in avisados


@pytest.mark.parametrize("posicao, simulada", [(None, False), (Posicao.AGAINST, True), (Posicao.FORMA, False)])
def test_infraestrutura_simulada_ou_forma_nao_rebaixam(mundo: Mundo, posicao: Posicao | None,
                                                       simulada: bool) -> None:
    fid = mundo.ensina()
    mundo.execucao_de_prova(fid, "r-x")
    mundo.minera_prova(fid, "r-x", posicao, simulada=simulada)
    assert mundo.status(fid) == "active" and mundo.espera(fid) is not None


def test_o_ensinado_ja_provado_nao_e_rebaixado_por_uma_prova_contra(mundo: Mundo) -> None:
    fid = mundo.ensina()
    mundo.execucao_de_prova(fid, "r-ok")
    mundo.evidencia(fid, "r-ok", "for")
    mundo.execucao_de_prova(fid, "r-contra")
    mundo.minera_prova(fid, "r-contra", Posicao.AGAINST)
    assert mundo.status(fid) == "active"                 # a espera acabou: quem cuida dele agora é a saúde (D-5)


# ------------------------------------------------------------------ a volta da validação abre a prova
def test_a_volta_abre_um_pedido_de_prova_com_o_exemplo_e_sem_o_aparelho_do_treino(mundo: Mundo) -> None:
    fid = mundo.ensina()
    mundo.volta()
    mundo.volta()                                        # idempotente: um pedido vivo por vez
    (p,) = mundo.pedidos(fid)
    assert (p["review_id"], p["estado"], p["motivo"]) == (f"ensino:{SESSAO}", "pendente", None)
    assert p["comando"] == _comando("@bia") and p["aparelho_excluido"] == APARELHO_DO_TREINO
    assert p["run_origem"] is None and "reproducao_em_outro_aparelho" in p["falta"]
    assert not mundo.bus.do_tipo(TIPO_ESPERA_DECISAO)


def test_o_ensinado_provado_ou_que_nao_veio_do_treino_nao_abre_pedido(mundo: Mundo) -> None:
    fid = mundo.ensina()
    mundo.execucao_de_prova(fid, "r-ok")
    mundo.evidencia(fid, "r-ok", "for")
    mundo.volta()
    assert mundo.pedidos(fid) == []


def _recusado_espera_a_pessoa(mundo: Mundo, fid: str, motivo: Motivo) -> None:
    mundo.volta()
    mundo.volta()
    (p,) = mundo.pedidos(fid)
    assert (p["estado"], p["motivo"], p["review_id"]) == ("recusada", motivo.value, f"ensino:{SESSAO}")
    (ev,) = mundo.bus.do_tipo(TIPO_ESPERA_DECISAO)
    _, mensagem, nivel, dados = ev
    assert tuple(dados) == CAMPOS_DA_ESPERA and nivel == "warn"
    assert dados == {"kind": "fluxo", "ref": fid, "app": mundo.servico.entrada(LivroKind.FLUXO, fid).app or "instagram",
                     "treino": SESSAO, "persona": ANA,
                     "desde": mundo.db.scalar("SELECT created_at FROM flows WHERE id=?", (fid,))}
    assert fid not in mensagem and ANA not in mensagem   # nem slug nem persona no `message` (combinado com a Canais)
    assert not mundo.casa([BIA])                         # e a restrição fica até a pessoa decidir


def test_a_classe_c_passa_a_pessoa(mundo: Mundo) -> None:
    mundo.risco = C
    _recusado_espera_a_pessoa(mundo, mundo.ensina(), Motivo.CLASSE_C)


def test_sem_dossie_de_agora_tambem_e_classe_c(mundo: Mundo) -> None:
    mundo.risco = None
    _recusado_espera_a_pessoa(mundo, mundo.ensina(), Motivo.CLASSE_C)


def test_o_efeito_em_app_real_passa_a_pessoa(mundo: Mundo) -> None:
    _recusado_espera_a_pessoa(mundo, mundo.ensina(efeito=True), Motivo.EFEITO_REAL)


def test_sem_o_exemplo_do_parametro_passa_a_pessoa(mundo: Mundo) -> None:
    _recusado_espera_a_pessoa(mundo, mundo.ensina(exemplo=""), Motivo.SEM_ORIGEM)


def test_as_tentativas_sem_veredito_esgotam_e_passam_a_pessoa(mundo: Mundo) -> None:
    fid = mundo.ensina()
    for n in range(TETO_DE_TENTATIVAS_DO_ENSINO):
        mundo.volta()
        assert len(mundo.pedidos(fid)) == n + 1
        mundo.db.execute("UPDATE learning_validations SET estado='expirada' WHERE item_ref=? AND estado='pendente'",
                         (f"fluxo:{fid}",))              # o aparelho não apareceu: falha de infraestrutura
    assert mundo.status(fid) == "active"
    mundo.volta()
    ultimo = mundo.pedidos(fid)[-1]
    assert (ultimo["estado"], ultimo["motivo"]) == ("recusada", Motivo.TENTATIVAS_ESGOTADAS.value)
    assert len(mundo.bus.do_tipo(TIPO_ESPERA_DECISAO)) == 1
    mundo.volta()
    assert len(mundo.pedidos(fid)) == TETO_DE_TENTATIVAS_DO_ENSINO + 1


# ------------------------------------------------------------------ a decisão da pessoa
def test_confirmar_que_fica_libera_o_ensinado_que_espera_e_avisa_uma_vez(mundo: Mundo) -> None:
    mundo.risco = C
    fid = mundo.ensina()
    mundo.volta()
    mundo.servico.confirmar_que_fica(LivroKind.FLUXO, fid, by="painel:dono")
    (ev,) = mundo.bus.do_tipo(TIPO_DECIDIDO)
    dados = ev[3]
    assert tuple(dados) == CAMPOS_DA_DECISAO and dados["decisao"] == "liberado" and fid not in ev[1]
    assert dados["desde"] == mundo.bus.do_tipo(TIPO_ESPERA_DECISAO)[0][3]["desde"]
    assert mundo.casa([BIA]) and mundo.espera(fid) is None
    with pytest.raises(ConflitoDeEstado):                # uma por nascimento: não confirma de novo
        mundo.servico.confirmar_que_fica(LivroKind.FLUXO, fid, by="painel:dono")
    assert len(mundo.bus.do_tipo(TIPO_DECIDIDO)) == 1


def test_desligar_o_ensinado_que_espera_avisa_desligado(mundo: Mundo) -> None:
    mundo.risco = C
    fid = mundo.ensina()
    mundo.volta()
    mundo.servico.mudar_estado(LivroKind.FLUXO, fid, SkillState.DISABLED, by="painel:dono", reason="não serve")
    (ev,) = mundo.bus.do_tipo(TIPO_DECIDIDO)
    assert ev[3]["decisao"] == "desligado"


def test_confirmar_o_ensinado_que_ainda_esta_na_prova_automatica_e_recusado(mundo: Mundo) -> None:
    fid = mundo.ensina()
    mundo.volta()                                        # pedido pendente: a prova automática ainda cobre
    with pytest.raises(ConflitoDeEstado):
        mundo.servico.confirmar_que_fica(LivroKind.FLUXO, fid, by="painel:dono")
    assert not mundo.bus.do_tipo(TIPO_DECIDIDO) and not mundo.casa([BIA])


def test_nenhum_evento_do_ensino_no_nascimento(mundo: Mundo) -> None:
    mundo.ensina()
    assert not [e for e in mundo.bus.eventos if e[0] in TIPOS_DO_ENSINO]


def test_o_livro_diz_que_o_ensinado_espera_a_pessoa_e_por_que(mundo: Mundo) -> None:
    def espera(fid: str) -> object:
        return _entrada(mundo.servico.entrada(LivroKind.FLUXO, fid), mundo.servico)["espera_a_pessoa"]

    na_prova = mundo.ensina()
    mundo.volta()
    assert espera(na_prova) is None                      # a prova automática ainda cobre: o botão não vale
    mundo.risco = C
    classe_c = mundo.ensina(sessao="trn-ensino-2", modelo="mostre o perfil de {username}")
    mundo.volta()
    assert espera(classe_c) == Motivo.CLASSE_C.value
    mundo.servico.confirmar_que_fica(LivroKind.FLUXO, classe_c, by="painel:dono")
    assert espera(classe_c) is None                      # decidido: acabou a espera
    assert "espera_a_pessoa" not in _entrada(mundo.servico.entrada(LivroKind.RECEITA, str(mundo.receita_do_treino())),
                                             mundo.servico)
