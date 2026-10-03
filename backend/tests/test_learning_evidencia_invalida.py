"""Evidência inválida (item 30.23): o tipo próprio de desligamento do que foi aprendido de um sucesso falso.

- o motivo é estruturado (`evidencia_invalida:<run>`, casamento exato) e só a ação própria o escreve: a rota genérica
  recusa o formato reservado; a ação aceita só a execução de ORIGEM do item, desliga o vivo e, no já desligado (o caso
  da receita 109 e do fluxo do Outlook), grava a linha `disabled → disabled` que reclassifica, sem mexer no status;
- o veto desse tipo barra só renascer da MESMA execução; outra execução REAL ensina de novo (simulada não);
- o que renasce no escopo é "reaprendido": espera o dono (classe B forçada) — a sombra da receita e a do fluxo param
  em `validated`, o sistema não publica (domínio e segunda camada), "Para aprovar" o mostra e o evento diz
  `reaprendido`; a evidência da execução invalidada não conta na sombra do fluxo; depois que uma pessoa publica no
  escopo, a confiança volta;
- a receita reaprendida aponta para a invalidada (`reaprende`) e a invalidada para ela (`reaprendida_por`); o fluxo
  renasce na mesma linha (`match_key` única) e não aponta para si mesmo;
- a arrumação da loja (`disabled → deprecated` da quarentenada substituída) não levanta mais o veto de uma pessoa.

Nível de prova: `simulated` (banco de teste migrado pela fábrica da suíte; execução "real" é uma linha de `runs` com
`simulated=0`; nenhum aparelho, nenhuma IA).
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from app.db import Database
from app.models import Plan, PlannerInfo, PlanStep, Postcondition, StepResult
from app.modules.learning.application.ports import Ajustes, MudancaNativa
from app.modules.learning.application.servico import LearningService
from app.modules.learning.domain.ciclo import (Desligamento, EntradaInvalida, ExigeODono, SkillState,
                                               TransicaoProibida, conferir_transicao, exige_o_dono, motivo_do_veto,
                                               permitido)
from app.modules.learning.domain.espera import AvisoDeEspera, Faixa, MotivoDeEntrada
from app.modules.learning.domain.evidencia_invalida import (Renascimento, ja_invalidada, motivo_de_evidencia_invalida,
                                                            reaprendizado, reservado, run_da_etapa, run_invalidada)
from app.modules.learning.domain.livro import Transicao, por_que_o_sistema_nao_publica
from app.modules.learning.domain.politica_de_risco import ClasseDeRisco, FatosDeRisco, Razao, classificar
from app.modules.learning.domain.vocabulario import LivroKind
from app.modules.learning.infrastructure import ligar_nativos
from app.modules.learning.infrastructure.fontes import FontesSql
from app.modules.learning.infrastructure.segredo import TriagemDeCredencial
from app.modules.learning.infrastructure.sql_repository import SqlLearningRepository
from app.modules.learning.presentation.router import router as learning_router
from app.modules.skills.domain.lifecycle import Actor
from app.taskqueue.flows import FlowStore
from app.taskqueue.recipes import RecipeStore
from app.util import now, now_iso

from .fake_skills import banco as banco_migrado

S = SkillState
DONO = "painel:dono"
PKG = "com.microsoft.office.outlook"
CHAVE = {"signature": "sig-caixa", "variant": "en-US/xhdpi"}
#: Ids no formato de `new_run_id`: a gramática do tipo só aceita esses.
FALSA = "r-20261002204347-8c3f6e"                 # o sucesso falso (a execução da receita 109 e do fluxo do Outlook)
OUTRA = "r-20261003101500-a1b2c3"
MAIS_UMA = "r-20261003111500-d4e5f6"
SIMULADA = "r-20261003120000-0f0f0f"
ESCOPO = "receita|pkg|1.0|sig|var|h"
AGORA = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)


def _etapa(run_id: str, chave: str = "open_inbox") -> str:
    return f"{run_id}:android-01:v2:{chave}"


def _acoes(*, commit: bool = False) -> list[dict[str, Any]]:
    return [{"tool": "tap", "commit": commit, "why": "a IA explicou", "args": {},
             "selectors": [{"kind": "rid", "rid": "app:id/inbox"}]}]


def _t(i: int, item: str, de: S | None, para: S, *, por: str = "sistema", motivo: str = "x") -> Transicao:
    return Transicao(id=i, item_ref=item, item_kind=LivroKind.RECEITA, content_hash="c", scope_key=ESCOPO,
                     app_version="1.0", from_state=de, to_state=para, reason=motivo, decided_by=por,
                     decided_at="2026-10-02T22:00:00.000Z", run_id=None)


def _d(para: S, *, por: str = DONO, motivo: str = "x", de: S | None = None) -> Desligamento:
    return Desligamento(to_state=para, decided_by=por, decided_at="2026-10-02T22:42:05.952Z", app_version="1.0",
                        reason=motivo, from_state=de)


# ------------------------------------------------------------------ domínio puro
def test_o_motivo_e_estruturado_de_vocabulario_fechado() -> None:
    assert motivo_de_evidencia_invalida(FALSA) == f"evidencia_invalida:{FALSA}"
    assert run_invalidada(f"evidencia_invalida:{FALSA}") == FALSA
    # casamento exato: texto livre, o motivo escrito à mão antes do tipo e maiúsculas não são o tipo
    for texto in (f"evidencia_invalida:{FALSA} porque sim", f"Evidencia_Invalida:{FALSA}", "evidencia_invalida:r-1",
                  f"Invalidado (decisão 12.5 §3): aprendido do sucesso falso da {FALSA}", "", None):
        assert run_invalidada(texto) is None
    with pytest.raises(ValueError):
        motivo_de_evidencia_invalida("r-1")
    # reservado para a ação: qualquer coisa que comece com o prefixo; o texto por extenso segue livre
    assert reservado(f"  Evidencia_Invalida:{FALSA}") and reservado("evidencia_invalida") and not reservado(
        "Evidência inválida: a execução não comprovou")
    assert run_da_etapa(_etapa(FALSA)) == FALSA
    assert run_da_etapa("training:t9") is None and run_da_etapa("s1") is None and run_da_etapa(None) is None


def test_o_veto_do_tipo_barra_so_a_mesma_execucao_e_so_execucao_real() -> None:
    historico = [_d(S.CANDIDATE, por="sistema"), _d(S.DISABLED, motivo=motivo_de_evidencia_invalida(FALSA),
                                                    de=S.CANDIDATE)]
    veto = motivo_do_veto(historico, agora=AGORA, app_version="1.0", renascimento=Renascimento(FALSA, real=True))
    assert veto is not None and "evidência inválida" in veto and FALSA in veto
    assert motivo_do_veto(historico, agora=AGORA, app_version="1.0",
                          renascimento=Renascimento(OUTRA, real=True)) is None
    assert motivo_do_veto(historico, agora=AGORA, app_version="1.0",
                          renascimento=Renascimento(OUTRA, real=False)) is not None          # simulada não ensina
    assert motivo_do_veto(historico, agora=AGORA, app_version="1.0") is not None             # sem quem renasce, fica
    # vale venha de quem vier: escrito pelo sistema, não vira o veto de 90 dias nem o da versão
    do_sistema = [_d(S.DISABLED, por="sistema", motivo=motivo_de_evidencia_invalida(FALSA))]
    assert motivo_do_veto(do_sistema, agora=AGORA, app_version="2.0",
                          renascimento=Renascimento(FALSA, real=True)) is not None
    assert motivo_do_veto(do_sistema, agora=AGORA, app_version="1.0",
                          renascimento=Renascimento(OUTRA, real=True)) is None


def test_a_arrumacao_da_loja_nao_levanta_o_veto_de_uma_pessoa() -> None:
    """Antes, a quarentenada desligada por pessoa virava `superseded` quando outra versão nascia na chave, e o
    `disabled → deprecated` da loja era a "última decisão": o veto sumia."""
    historico = [_d(S.DISABLED, de=S.CANDIDATE), _d(S.DEPRECATED, por="sistema", de=S.DISABLED)]
    assert motivo_do_veto(historico, agora=AGORA, app_version="1.0") is not None
    # a aposentadoria de verdade (publicada → aposentada) segue sem vetar
    assert motivo_do_veto([_d(S.DEPRECATED, de=S.PUBLISHED)], agora=AGORA, app_version="1.0") is None


def test_reaprendido_e_o_que_nasce_no_escopo_depois_da_evidencia_invalida() -> None:
    marca = motivo_de_evidencia_invalida(FALSA)
    trilha = [_t(1, "receita:109", None, S.CANDIDATE), _t(2, "receita:109", S.CANDIDATE, S.DISABLED, por=DONO),
              _t(3, "receita:109", S.DISABLED, S.DISABLED, por=DONO, motivo=marca),
              _t(4, "receita:109", S.DISABLED, S.DEPRECATED), _t(5, "receita:120", None, S.CANDIDATE)]
    r = reaprendizado(trilha, "receita:120")
    assert r is not None and (r.run_invalidada, r.item_invalidado) == (FALSA, "receita:109")
    assert reaprendizado(trilha, "receita:109") is None                       # nasceu antes da marca
    assert reaprendizado(trilha[:3], "receita:120") is None                   # ainda não nasceu
    # uma pessoa publicou no escopo depois da marca: a confiança volta para quem nasce depois
    publicada = [*trilha, _t(6, "receita:120", S.VALIDATED, S.PUBLISHED, por=DONO),
                 _t(7, "receita:120", S.PUBLISHED, S.DISABLED), _t(8, "receita:130", None, S.CANDIDATE)]
    assert reaprendizado(publicada, "receita:130") is None
    assert reaprendizado(publicada, "receita:120") is not None                # o que ela aprovou segue marcado
    # o fluxo renasce na MESMA linha (`disabled → candidate`, escrito só pela loja): reaprendido de si mesmo
    fluxo = [_t(1, "fluxo:f", None, S.CANDIDATE), _t(2, "fluxo:f", S.CANDIDATE, S.DISABLED, por=DONO, motivo=marca),
             _t(3, "fluxo:f", S.DISABLED, S.CANDIDATE)]
    rf = reaprendizado(fluxo, "fluxo:f")
    assert rf is not None and rf.item_invalidado == "fluxo:f"
    assert ja_invalidada(fluxo[:2], FALSA) and not ja_invalidada(fluxo, FALSA)


def test_reaprendido_espera_o_dono_classe_b() -> None:
    assert exige_o_dono(False, False, reaprendido=True) and not exige_o_dono(False, False)
    with pytest.raises(ExigeODono, match="reaprendido"):
        conferir_transicao(S.VALIDATED, S.PUBLISHED, "sistema", side_effect=False, human_origin=False,
                           modo_publica=True, reaprendido=True)
    assert permitido(S.VALIDATED, S.PUBLISHED, Actor.PERSON, reaprendido=True)          # a pessoa publica
    assert permitido(S.CANDIDATE, S.VALIDATED, Actor.SYSTEM, reaprendido=True)          # validar segue do sistema
    c = classificar(FatosDeRisco(side_effect=False, human_origin=False, tem_catalogo=True, reaprendido=True))
    assert (c.classe, c.razoes, c.motivo) == (ClasseDeRisco.B, (Razao.REAPRENDIDO_DE_EVIDENCIA_INVALIDA,),
                                              MotivoDeEntrada.REAPRENDIDO)
    assert c.aceita_lote and not c.decide_sozinho


# ------------------------------------------------------------------ receita: a loja e o livro
class Barramento:
    def __init__(self) -> None:
        self.avisos: list[AvisoDeEspera] = []

    def esperando_a_pessoa(self, aviso: AvisoDeEspera) -> None:
        self.avisos.append(aviso)


class Receitas:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.repo = SqlLearningRepository(db, precos=dict)
        self.barramento = Barramento()
        self.servico = LearningService(self.repo, FontesSql(db), TriagemDeCredencial(), ajustes=Ajustes, relogio=now,
                                       retencao_de_logs_dias=lambda: 14, eventos=self.barramento)
        self.store = RecipeStore(db)
        ligar_nativos.ligar(self.servico, self.repo, db, receitas=self.store)

    def execucao(self, run_id: str, *, simulada: bool = False) -> None:
        self.db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids,"
                        " created_at) VALUES (?,?,?,?,?,?,?,?)",
                        (run_id, f"k-{run_id}", "ler a caixa de entrada", "execute", "completed", int(simulada),
                         json.dumps(["android-01"]), now_iso()))

    def salva(self, run_id: str, *, commit: bool = False, candidate: bool = True) -> int | None:
        return self.store.save(package=PKG, app_version="5.2635.3", step_hash="h-open-inbox", step_key="open_inbox",
                               actions=_acoes(commit=commit), learned_from=_etapa(run_id), candidate=candidate,
                               **CHAVE)

    def status(self, rid: int) -> str:
        return str(self.db.scalar("SELECT status FROM recipes WHERE id=?", (rid,)))

    def trilha(self, rid: int) -> list[tuple[str | None, str, str]]:
        return [(t.from_state.value if t.from_state else None, t.to_state.value, t.reason)
                for t in self.repo.trilha(f"receita:{rid}")]

    def entrada(self, rid: int) -> Any:
        return self.servico.entrada(LivroKind.RECEITA, str(rid))


@pytest.fixture
def receitas(tmp_path: Path) -> Iterator[Receitas]:
    db = banco_migrado(tmp_path, "evidencia-invalida-receitas.sqlite3")
    yield Receitas(db)
    db.close()


def test_a_receita_109_reclassificada_e_reaprendida_por_outra_execucao_real(receitas: Receitas) -> None:
    m = receitas
    for run, simulada in ((FALSA, False), (OUTRA, False), (MAIS_UMA, False), (SIMULADA, True)):
        m.execucao(run, simulada=simulada)
    rid = m.salva(FALSA)
    assert rid and m.entrada(rid).nasceu_de == FALSA
    # como foi feito no central em 02/10, antes do tipo existir: desligada à mão, com texto livre
    m.servico.mudar_estado(LivroKind.RECEITA, str(rid), S.DISABLED, by=DONO,
                           reason=f"Invalidado (decisão 12.5 §3): aprendido do sucesso falso da {FALSA}")
    assert m.salva(OUTRA) is None                         # desligada por pessoa: nem outra execução a traz de volta
    # a transição corretiva: o status nativo não muda, a trilha ganha o tipo
    m.servico.invalidar_evidencia(LivroKind.RECEITA, str(rid), FALSA, by=DONO)
    assert m.status(rid) == "quarantined"
    assert m.trilha(rid)[-1] == ("disabled", "disabled", f"evidencia_invalida:{FALSA}")
    n = len(m.trilha(rid))
    m.servico.invalidar_evidencia(LivroKind.RECEITA, str(rid), FALSA, by=DONO)          # idempotente
    assert len(m.trilha(rid)) == n
    # renascer: a mesma execução não; a simulada não; outra real sim — candidata, versão nova, reaprendida
    assert m.salva(FALSA) is None
    assert m.salva(SIMULADA) is None
    nova = m.salva(OUTRA)
    assert nova and nova != rid and m.status(nova) == "candidate"
    # a loja aposenta a quarentenada da chave quando nasce a versão nova (comportamento de sempre do `save`)
    assert m.status(rid) == "superseded"
    e = m.entrada(nova)
    assert e.reaprendido is not None and (e.reaprendido.run_invalidada, e.reaprendido.item_invalidado) == (
        FALSA, f"receita:{rid}")
    assert e.requires_owner
    # a sombra concorda duas vezes: sem `commit`, mas reaprendida → validated, espera o dono (classe B)
    assert m.store.shadow(nova, True, promote_after=2) is False
    assert m.store.shadow(nova, True, promote_after=2) is False
    assert m.status(nova) == "validated"
    assert "reaprendida depois de uma evidência inválida" in m.trilha(nova)[-1][2]
    assert m.store.find(PKG, "5.2635.3", "h-open-inbox", **CHAVE) is None               # não age
    assert ("receita", str(nova)) in {(x.kind.value, x.ref) for x in m.servico.pendentes()}
    [aviso] = [a for a in m.barramento.avisos if a.ref == str(nova)]
    assert (aviso.faixa, aviso.aguardando, aviso.motivo) == (Faixa.B, True, "reaprendido")
    # o sistema não publica: nem pelo livro, nem pela segunda camada do repositório
    with pytest.raises(ExigeODono):
        m.servico.mudar_estado(LivroKind.RECEITA, str(nova), S.PUBLISHED, by="sistema", reason="tentar")
    viva = m.entrada(nova)
    with pytest.raises(ExigeODono, match="reaprendida"):
        m.repo.transicionar_nativo(MudancaNativa(
            kind=LivroKind.RECEITA, ref=str(nova), de_status="validated", para_status="active",
            de_estado=S.VALIDATED, para_estado=S.PUBLISHED, content_hash=viva.content_hash,
            scope_key=viva.scope_key, app_version=viva.app_version), by="sistema", reason="tentar")
    # as relações: a nova reaprende a 109; a 109 é reaprendida pela nova
    rel_nova = m.servico.detalhe(LivroKind.RECEITA, str(nova)).relacoes
    assert {"tipo": "reaprende", "kind": "receita", "ref": str(rid)} in [
        {k: r[k] for k in ("tipo", "kind", "ref")} for r in rel_nova]
    rel_109 = m.servico.detalhe(LivroKind.RECEITA, str(rid)).relacoes
    assert {"tipo": "reaprendida_por", "kind": "receita", "ref": str(nova)} in [
        {k: r[k] for k in ("tipo", "kind", "ref")} for r in rel_109]
    # o dono aprova: ela age; e a versão seguinte do escopo já segue o D1 de sempre
    m.servico.mudar_estado(LivroKind.RECEITA, str(nova), S.PUBLISHED, by=DONO, reason="aprovada")
    assert m.status(nova) == "active"
    aprovada = m.entrada(nova)
    assert aprovada.reaprendido is not None and por_que_o_sistema_nao_publica(aprovada) is None   # a espera acabou
    assert [m.store.result(nova, False) for _ in range(3)][-1] is True                  # quarentena do sistema
    seguinte = m.salva(MAIS_UMA)
    assert seguinte and m.entrada(seguinte).reaprendido is None
    assert m.store.shadow(seguinte, True, promote_after=1) is True and m.status(seguinte) == "active"


def test_a_acao_desliga_o_vivo_e_so_aceita_a_execucao_de_origem(receitas: Receitas) -> None:
    m = receitas
    m.execucao(FALSA)
    rid = m.salva(FALSA)
    assert rid
    with pytest.raises(TransicaoProibida, match="não da"):
        m.servico.invalidar_evidencia(LivroKind.RECEITA, str(rid), OUTRA, by=DONO)
    with pytest.raises(EntradaInvalida):
        m.servico.invalidar_evidencia(LivroKind.RECEITA, str(rid), "r-123", by=DONO)
    with pytest.raises(EntradaInvalida):
        m.servico.invalidar_evidencia(LivroKind.LICAO, "li-1", FALSA, by=DONO)
    depois = m.servico.invalidar_evidencia(LivroKind.RECEITA, str(rid), FALSA, by=DONO)
    assert depois.state is S.DISABLED and m.status(rid) == "quarantined"
    [*_, ultima] = m.repo.trilha(f"receita:{rid}")
    assert (ultima.from_state, ultima.to_state, ultima.reason, ultima.decided_by) == (
        S.CANDIDATE, S.DISABLED, f"evidencia_invalida:{FALSA}", DONO)
    # a rota genérica não escreve o tipo num motivo livre
    m.servico.mudar_estado(LivroKind.RECEITA, str(rid), S.PUBLISHED, by=DONO, reason="reativada")
    with pytest.raises(EntradaInvalida, match="ação própria"):
        m.servico.mudar_estado(LivroKind.RECEITA, str(rid), S.DISABLED, by=DONO,
                               reason=f"evidencia_invalida:{FALSA}")
    # treino não tem execução de origem; a aposentada não muda
    treino = m.store.save(package=PKG, app_version="5.2635.3", step_hash="h-outra", step_key="outra",
                          actions=_acoes(), learned_from="training:t9", **CHAVE)
    assert treino
    with pytest.raises(TransicaoProibida, match="não foi aprendido de uma execução"):
        m.servico.invalidar_evidencia(LivroKind.RECEITA, str(treino), FALSA, by=DONO)


def test_reaprendida_com_commit_segue_no_dono_e_a_aposentada_nao_muda(receitas: Receitas) -> None:
    m = receitas
    m.execucao(FALSA)
    m.execucao(OUTRA)
    rid = m.salva(FALSA, commit=True)
    assert rid
    m.servico.invalidar_evidencia(LivroKind.RECEITA, str(rid), FALSA, by=DONO)
    nova = m.salva(OUTRA, commit=True)
    assert nova and m.store.shadow(nova, True, promote_after=1) is False and m.status(nova) == "validated"
    # o motivo do efeito vem antes (o de sempre); a marca do reaprendido segue na entrada
    assert "efeito externo" in m.trilha(nova)[-1][2] and m.entrada(nova).reaprendido is not None
    with pytest.raises(TransicaoProibida, match="aposentado"):
        m.servico.invalidar_evidencia(LivroKind.RECEITA, str(rid), FALSA, by=DONO)      # já `superseded`


# ------------------------------------------------------------------ fluxo: a mesma linha renasce
MODELO = "no outlook, leia o remetente da mensagem de {remetente}"


def _plano(remetente: str) -> Plan:
    ler = PlanStep(key="ler", title=f"Ler a mensagem de {remetente}", goal=f"ler {remetente}", capability="READ_MAIL",
                   bindings={"remetente": remetente},
                   postcondition=Postcondition(kind="text_visible", value=remetente, description="mensagem lida"))
    return Plan(summary=f"Ler a mensagem de {remetente}", app_id="outlook", parameters={"remetente": remetente},
                steps=[ler], planner=PlannerInfo(provider="anthropic", model="claude", simulated=False))


class Fluxos:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.decisoes: list[tuple[str, str]] = []
        self.repo = SqlLearningRepository(db, precos=dict)
        self.servico = LearningService(self.repo, FontesSql(db), TriagemDeCredencial(), ajustes=Ajustes, relogio=now,
                                       retencao_de_logs_dias=lambda: 14)
        self.flows = FlowStore(db)
        ligar_nativos.ligar(self.servico, self.repo, db, fluxos=self.flows, concordancias=lambda: 1,
                            decidir=lambda texto, run_id: self.decisoes.append((run_id, texto)))

    def execucao(self, run_id: str, remetente: str, *, simulada: bool = False) -> dict[str, Any]:
        plano = _plano(remetente)
        self.db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, plan,"
                        " created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                        (run_id, f"k-{run_id}", MODELO.replace("{remetente}", remetente), "execute", "completed",
                         int(simulada), json.dumps(["android-01"]), plano.model_dump_json(), now_iso()))
        self.db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version) VALUES (?,?,?,?,?)",
                        (f"{run_id}:o1", run_id, "android-01", "succeeded", 1))
        for seq, passo in enumerate(plano.steps, start=1):
            self.db.execute(
                "INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal,"
                " postcondition, timeout_s, max_attempts, status, result) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (f"{run_id}:{passo.key}", run_id, f"{run_id}:o1", "android-01", 1, seq, passo.key, passo.title,
                 passo.goal, passo.postcondition.model_dump_json(), 60, 3, "succeeded",
                 StepResult(verified=True, evidence_text="visto na tela").model_dump_json()))
        linha = self.db.one("SELECT * FROM runs WHERE id=?", (run_id,))
        assert linha is not None
        return dict(linha)

    def roda(self, run_id: str, remetente: str, *, simulada: bool = False) -> str | None:
        run = self.execucao(run_id, remetente, simulada=simulada)
        flow_id = self.flows.learn_from_run(run)
        relatorio = self.servico.digerir_execucao(run_id)
        assert not relatorio.falhas, relatorio
        return flow_id

    def status(self, flow_id: str) -> str:
        return str(self.db.scalar("SELECT status FROM flows WHERE id=?", (flow_id,)))


def _eficacia(saude: Any) -> float | None:
    [d] = [d for d in saude.dimensoes if d.nome.value == "eficacia"]
    return None if d.desconhecida else d.valor


@pytest.fixture
def fluxos(tmp_path: Path) -> Iterator[Fluxos]:
    db = banco_migrado(tmp_path, "evidencia-invalida-fluxos.sqlite3")
    db.execute("INSERT INTO apps(id, name, package, builtin) VALUES ('outlook','Outlook',?,0)", (PKG,))
    yield Fluxos(db)
    db.close()


def test_o_fluxo_do_outlook_reclassificado_renasce_na_mesma_linha_e_espera_o_dono(fluxos: Fluxos) -> None:
    m = fluxos
    fid = m.roda(FALSA, "Ana")
    assert fid and m.status(fid) == "candidate"
    falsa = m.db.one("SELECT * FROM runs WHERE id=?", (FALSA,))
    assert falsa is not None
    m.servico.mudar_estado(LivroKind.FLUXO, fid, S.DISABLED, by=DONO, reason="Invalidado à mão (texto livre)")
    assert m.roda(OUTRA, "Bia") is None                    # desligado por pessoa: não renasce
    antes = m.servico.detalhe(LivroKind.FLUXO, fid).saude
    assert antes is not None and _eficacia(antes) == 1.0                  # a evidência da falsa media a eficácia
    m.servico.invalidar_evidencia(LivroKind.FLUXO, fid, FALSA, by=DONO)
    assert m.status(fid) == "disabled"
    depois = m.servico.detalhe(LivroKind.FLUXO, fid)
    assert depois.saude is not None and _eficacia(depois.saude) is None   # ...e deixa de medir: "sem dado", nunca zero
    assert [x.run_id for x in depois.evidencias] == [FALSA]               # mas fica à vista (o painel a marca)
    # a mesma execução não o traz de volta; a simulada também não
    assert m.flows.learn_from_run(dict(falsa)) is None
    assert m.roda(SIMULADA, "Caio", simulada=True) is None
    # outra execução real: a MESMA linha renasce candidata, reaprendida de si mesma
    assert m.roda(MAIS_UMA, "Duda") == fid and m.status(fid) == "candidate"
    e = m.servico.entrada(LivroKind.FLUXO, fid)
    assert e.reaprendido is not None and e.reaprendido.item_invalidado == f"fluxo:{fid}" and e.requires_owner
    assert e.nasceu_de == MAIS_UMA
    assert not [r for r in m.servico.detalhe(LivroKind.FLUXO, fid).relacoes if r["tipo"] == "reaprende"]
    nascimento = next(texto for run, texto in m.decisoes if run == MAIS_UMA)
    assert "o dono o publica (reaprendido depois de uma evidência inválida)" in nascimento
    # a evidência a favor da execução falsa (mesmo plano) NÃO conta: com uma execução real a mais, só valida
    proxima = "r-20261003130000-abcdef"
    m.roda(proxima, "Edu")
    assert m.status(fid) == "validated"
    assert any(run == proxima and "reaprendido depois de uma evidência inválida" in t for run, t in m.decisoes)
    assert ("fluxo", fid) in {(x.kind.value, x.ref) for x in m.servico.pendentes()}
    with pytest.raises(ExigeODono):
        m.servico.mudar_estado(LivroKind.FLUXO, fid, S.PUBLISHED, by="sistema", reason="tentar")
    m.servico.mudar_estado(LivroKind.FLUXO, fid, S.PUBLISHED, by=DONO, reason="aprovado")
    assert m.status(fid) == "active"


# ------------------------------------------------------------------ a rota e o JSON
@pytest.fixture
async def cliente(receitas: Receitas) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.include_router(learning_router)
    app.state.poc = SimpleNamespace(learning=receitas.servico)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def test_a_rota_marca_mostra_o_tipo_e_recusa_o_motivo_reservado(receitas: Receitas,
                                                                     cliente: httpx.AsyncClient) -> None:
    m = receitas
    m.execucao(FALSA)
    m.execucao(OUTRA)
    rid = m.salva(FALSA)
    assert rid
    r = await cliente.get(f"/api/aprendizado/receita/{rid}")
    assert r.status_code == 200, r.text
    assert r.json()["invalidar_evidencia"] == {"run_id": FALSA} and r.json()["item"]["nasceu_de"] == FALSA
    r = await cliente.post(f"/api/aprendizado/receita/{rid}/status",
                           json={"to": "disabled", "reason": f"evidencia_invalida:{FALSA}"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid"
    r = await cliente.post(f"/api/aprendizado/receita/{rid}/evidencia-invalida", json={"run_id": OUTRA})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "transition_forbidden"
    r = await cliente.post(f"/api/aprendizado/receita/{rid}/evidencia-invalida", json={"run_id": FALSA})
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["item"]["state"] == "disabled" and corpo["invalidar_evidencia"] is None
    [*_, ultima] = corpo["trilha"]
    assert (ultima["tipo"], ultima["run_invalidada"], ultima["reason"]) == (
        "evidencia_invalida", FALSA, f"evidencia_invalida:{FALSA}")
    assert corpo["trilha"][0]["tipo"] is None
    assert "evidência inválida" in corpo["item"]["por_que_nao_publica"]["detalhe"]
    # a reaprendida: `reaprendido` no item, `reaprendido` no porquê, a relação para a invalidada
    nova = m.salva(OUTRA)
    r = await cliente.get(f"/api/aprendizado/receita/{nova}")
    item = r.json()["item"]
    assert item["reaprendido"] == {"run_invalidada": FALSA, "item": {"kind": "receita", "ref": str(rid)}}
    assert item["por_que_nao_publica"] == {"codigo": "reaprendido", "espera_o_dono": True, "detalhe": FALSA}
    assert item["requires_owner"] is True
    tipos = [x["tipo"] for x in r.json()["relacoes"]]
    assert tipos.count("reaprende") == 1 and "reaprendida_por" not in tipos
    lista = (await cliente.get("/api/aprendizado?kind=receita")).json()["itens"]
    assert {x["ref"]: x["reaprendido"] is not None for x in lista} == {str(rid): False, str(nova): True}
