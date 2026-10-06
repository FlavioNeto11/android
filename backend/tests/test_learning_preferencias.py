"""Preferências como sugestão pré-preenchida (ADR-054, pacote A9) e a porta da desambiguação em `skills.application`.

- a MESMA resposta 3 vezes (mesmo campo, mesmo modelo de comando, mesmo perfil), sem nenhuma diferente, vira
  candidata; antes disso, e com uma resposta diferente no meio, não; depois, a diferente a contradiz;
- campo que é alvo de terceiro (destinatário, perfil, conversa...) nunca vira padrão;
- a preferência só PRÉ-PREENCHE: a sugestão é lida, a execução continua esperando a pessoa e nada nasce sozinho;
- o `PreferenceStage` decide sozinho só quando a habilidade escolhida não tem etapa com efeito externo E a versão
  candidata agora é uma das conferidas quando a preferência nasceu; com efeito, ou numa versão nova (que pode ter
  ganho uma etapa com efeito), a pergunta continua, com a opção pré-selecionada;
- a escolha no desambiguador é observada pela sucessora (a execução que a pessoa respondeu) e vira preferência sem
  texto de pessoa: o sistema a valida pela repetição e a publica só sem efeito e com o modo em `on`;
- a preferência nasce na curadoria, mas a trilha leva a execução cuja observação FECHOU o limiar (a n-ésima, não a
  última, e a observação sem execução não conta), e cada decisão do sistema, a da evidência que virou o veredito —
  só entre a que chegou depois da última mudança de estado (a reativação por uma pessoa não devolve a culpa à
  execução antiga); a publicação numa passada posterior (o modo mudou) fica sem execução; no "Aprendizado desta
  execução" (`GET /api/runs/{id}/feedback`) ela é candidata só da que fechou, "entre N";
- `skills` nunca importa `learning` (DAG).

Nível de prova: `simulated` (banco de teste pela fábrica da suíte, habilidades falsas; nenhum aparelho, nenhuma IA).
"""
from __future__ import annotations

import ast
import hashlib
import json
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from app.db import Database
from app.modules.learning.application.ports import Ajustes
from app.modules.learning.application.preferencias import (EscolhaObservada, Observacao, PerguntasAbertas,
                                                           ServicoDePreferencias, campo_de_terceiro,
                                                           desde_a_ultima_mudanca, execucao_que_fechou)
from app.modules.learning.application.servico import LearningService
from app.modules.learning.domain.ciclo import SkillState
from app.modules.learning.domain.livro import ItemDeAprendizado, Transicao
from app.modules.learning.domain.promocao import Evidencia
from app.modules.learning.domain.vocabulario import LivroKind, Modo, Posicao, SignalKind, SourceKind
from app.modules.learning.infrastructure.fontes import FontesSql
from app.modules.learning.infrastructure.ligar_costuras import costuras_do_livro
from app.modules.learning.infrastructure.preferencias_sql import (PreferenciasDoLivro, PreferenciasSql,
                                                                  montar_preferencias)
from app.modules.learning.infrastructure.segredo import TriagemDeCredencial
from app.modules.learning.infrastructure.sql_repository import SqlLearningRepository
from app.modules.learning.presentation.router import router as learning_router
from app.modules.skills.application.intent_ports import PreferenceHint
from app.modules.skills.application.intent_resolver import IntentRequest, IntentResolver, ParameterExtractor
from app.modules.skills.domain.intent import ResolutionMethod, ResolutionStatus, SkillMatch, StageOutcome
from app.modules.skills.domain.refs import SkillRef
from app.taskqueue.costuras import RespostaAPergunta
from app.util import to_iso

from .fake_skills import banco, perfil
from .test_intencao_dominio import HANDLE, IG, Fonte, habilidade

OTTILIE, BIA = "p-ottilie", "p-bia"
DONO = "flavio"
PERGUNTA = "mande a mensagem de bom dia para o grupo"
RESPOSTA = "mande a mensagem 'bom dia, família!' para o grupo da família"
AMBIGUO = "abra @ana"


#: O relógio do livro e das execuções semeadas (injetado, nunca o do sistema).
AGORA = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)


@dataclass
class Mundo:
    db: Database
    ajustes: list[Ajustes] = field(default_factory=lambda: [Ajustes(modo_preferencias=Modo.SHADOW)])
    n: int = 0
    #: O relógio do livro: parado em `AGORA`, salvo quando o teste o avança (`passar`) para separar uma passada da
    #: curadoria de um gesto de pessoa, como no mundo — com ele parado, tudo cai no mesmo milissegundo.
    relogio: list[datetime] = field(default_factory=lambda: [AGORA])

    def __post_init__(self) -> None:
        perfil(self.db, OTTILIE)
        perfil(self.db, BIA)
        self.repo = SqlLearningRepository(self.db, precos=dict, clock=lambda: to_iso(self.relogio[0]))
        self.servico = LearningService(self.repo, FontesSql(self.db), TriagemDeCredencial(),
                                       ajustes=lambda: self.ajustes[0], relogio=lambda: self.relogio[0],
                                       retencao_de_logs_dias=lambda: 14)
        assert montar_preferencias(self.db, self.servico) is not None
        # O serviço sob teste grava a evidência pelo MESMO relógio da trilha, como na composição (os dois
        # repositórios com `now_iso`): a atribuição da execução compara os dois carimbos (`desde_a_ultima_mudanca`).
        self.prefs = ServicoDePreferencias(self.servico, self.repo, PreferenciasSql(self.db), TriagemDeCredencial())
        self.costuras = costuras_do_livro(self.servico, self.db)

    def modo(self, modo: Modo) -> None:
        self.ajustes[0] = Ajustes(modo_preferencias=modo)

    def passar(self, segundos: int = 60) -> None:
        self.relogio[0] += timedelta(seconds=segundos)

    # ---------------------------------------------------------------- execuções
    def execucao(self, comando: str, *, status: str, perfis: Sequence[str] = (OTTILIE,), plano: object = None,
                 skill_id: str | None = None, versao: int = 1, simulado: bool = False) -> str:
        self.n += 1
        run = f"r-{self.n:03d}"
        foto = {"alvos": [{"instance_id": f"android-{i:02d}", "profile_id": p} for i, p in enumerate(perfis, 6)],
                "command_sem_destinos": comando, "device_policy": "one", "pedido": {"profile_ids": list(perfis)}}
        self.db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids,"
                        " created_at, plan, targets, skill_id, skill_version) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (run, run, comando, "plan", status, int(simulado), "[]", to_iso(AGORA),
                         json.dumps(plano) if plano is not None else None, json.dumps(foto), skill_id,
                         versao if skill_id else None))
        return run

    def responder(self, campo: str, resposta: str = RESPOSTA, *, perfis: Sequence[str] = (OTTILIE,),
                  efeito: bool = True, comando: str = PERGUNTA, simulado: bool = False) -> tuple[str, str]:
        """A pessoa respondeu a pergunta `campo` de uma execução em `needs_input`: nasce a sucessora e o sinal
        `respondeu_pergunta` do A2 (com o sha256 do comando respondido, nunca o valor)."""
        pergunta = {"summary": "falta", "steps": [], "missing": [{"field": campo, "question": "Qual?"}]}
        antiga = self.execucao(comando, status="cancelled", perfis=perfis, plano=pergunta, simulado=simulado)
        nova = self.execucao(resposta, status="completed", perfis=perfis, simulado=simulado,
                             plano={"summary": "s", "steps": [{"key": "a", "side_effect": efeito}]})
        self.costuras.respondeu_pergunta(RespostaAPergunta(
            run_id=antiga, run_sucessora=nova, campos=(campo,),
            resposta_sha256=hashlib.sha256(resposta.encode()).hexdigest()))
        return antiga, nova

    def escolher(self, escolhida: str, *, efeito: bool = False, perfis: Sequence[str] = (OTTILIE,),
                 candidatas: Sequence[str] = ("ig.a@1", "ig.b@1"), versao: int = 1) -> tuple[str, str]:
        """A pessoa respondeu o empate entre habilidades reescrevendo o comando: a sucessora resolveu `escolhida`, na
        versão `versao` (`runs.skill_version`)."""
        antiga = self.execucao(AMBIGUO, status="cancelled", perfis=perfis)
        pergunta = {"field": "skill", "question": "Qual delas?", "reason": "ambiguous_intent",
                    "options": list(candidatas)}
        self.db.execute("INSERT INTO events(ts, kind, level, run_id, message, data) VALUES (?,?,?,?,?,?)",
                        (to_iso(AGORA), "log", "warn", antiga, "empate",
                         json.dumps({"skill": None, "questions": [pergunta], "candidates": list(candidatas)})))
        nova = self.execucao(f"{AMBIGUO} ({escolhida})", status="completed", perfis=perfis, skill_id=escolhida,
                             versao=versao, plano={"summary": "s", "steps": [{"key": "a", "side_effect": efeito}]})
        self.costuras.respondeu_pergunta(RespostaAPergunta(
            run_id=antiga, run_sucessora=nova, campos=("skill",), resposta_sha256="x" * 64))
        return antiga, nova

    def preferencias(self, estado: SkillState | None = None) -> list[ItemDeAprendizado]:
        return self.repo.itens(kind=LivroKind.PREFERENCIA, state=estado)

    def publicar(self, item: ItemDeAprendizado) -> None:
        if item.state is SkillState.CANDIDATE:
            self.servico.mudar_estado(LivroKind.PREFERENCIA, item.id, SkillState.VALIDATED, by=DONO, reason="isso")
        self.servico.mudar_estado(LivroKind.PREFERENCIA, item.id, SkillState.PUBLISHED, by=DONO, reason="pode usar")


@pytest.fixture
def mundo(tmp_path: Path) -> Mundo:
    db = banco(tmp_path, "preferencias.sqlite3")
    yield Mundo(db)
    db.close()


# ================================================================== respostas às perguntas (needs_input)
def test_tres_respostas_iguais_viram_candidata_de_pessoa(mundo: Mundo) -> None:
    mundo.responder("mensagem")
    mundo.responder("mensagem")
    mundo.prefs.minerar(AGORA)
    assert mundo.preferencias() == []                                  # 2 não bastam
    mundo.responder("mensagem")
    mundo.prefs.minerar(AGORA)
    [p] = mundo.preferencias()
    assert p.state is SkillState.CANDIDATE and p.source_kind is SourceKind.ANSWER
    assert p.human_origin and p.requires_owner and p.side_effect       # texto de pessoa que alimenta efeito
    assert p.content["valor"] == RESPOSTA and p.content["campo"] == "mensagem"
    assert (p.escopo.profile_id, p.escopo.capability) == (OTTILIE, "mensagem")
    assert (p.evidence_for, p.distinct_runs) == (3, 3)
    # Idempotente, e o sistema não valida texto de pessoa: ela fica na fila do dono.
    mundo.modo(Modo.ON)
    mundo.prefs.minerar(AGORA)
    [p] = mundo.preferencias()
    assert p.state is SkillState.CANDIDATE and p.evidence_for == 3
    assert [e.ref for e in mundo.servico.pendentes() if e.kind is LivroKind.PREFERENCIA] == [p.id]
    # Uma resposta diferente depois a contradiz: o sistema a desliga (rebaixar é automático).
    mundo.responder("mensagem", "mande a mensagem 'boa noite' para o grupo")
    mundo.prefs.minerar(AGORA)
    [p] = mundo.preferencias()
    assert p.state is SkillState.DISABLED and p.state_by == "sistema" and p.evidence_against == 1


def test_resposta_diferente_no_meio_impede_a_candidata(mundo: Mundo) -> None:
    mundo.responder("mensagem")
    mundo.responder("mensagem", "mande a mensagem 'oi' para o grupo")
    mundo.responder("mensagem")
    mundo.responder("mensagem")
    mundo.prefs.minerar(AGORA)
    assert mundo.preferencias() == []


def test_perfis_e_modelos_de_comando_nao_se_misturam(mundo: Mundo) -> None:
    mundo.responder("mensagem")
    mundo.responder("mensagem", perfis=(BIA,))
    mundo.responder("mensagem", comando="mande a mensagem de boa noite para o grupo")
    mundo.responder("mensagem", simulado=True)                          # simulada nunca conta
    mundo.prefs.minerar(AGORA)
    assert mundo.preferencias() == []


def test_alvo_de_terceiro_nunca_vira_padrao(mundo: Mundo) -> None:
    for campo in ("destinatario", "perfil", "recipient", "conversa"):
        for _ in range(3):
            mundo.responder(campo, "mande para @ana.souza")
    mundo.prefs.minerar(AGORA)
    assert mundo.preferencias() == []
    assert all(campo_de_terceiro(c) for c in ("destinatario", "perfil", "recipient", "conversa", "thread_key",
                                               "username", "alvo", "target_handle"))
    assert not any(campo_de_terceiro(c) for c in ("mensagem", "skill", "quantos", "aba"))


def test_resposta_com_cara_de_segredo_nao_vira_preferencia(mundo: Mundo) -> None:
    for _ in range(3):
        mundo.responder("mensagem", "mande a mensagem 'minha senha é hunter2-XY' para o grupo")
    mundo.prefs.minerar(AGORA)
    assert mundo.preferencias() == []


def test_modo_off_nao_minera(mundo: Mundo) -> None:
    mundo.modo(Modo.OFF)
    for _ in range(3):
        mundo.responder("mensagem")
    assert mundo.prefs.minerar(AGORA) == 0 and mundo.preferencias() == []


# ------------------------------------------------------------------ só pré-preenche
@pytest.fixture
async def cliente(mundo: Mundo) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.include_router(learning_router)
    app.state.poc = SimpleNamespace(learning=mundo.servico, db=mundo.db)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def test_so_pre_preenche(mundo: Mundo, cliente: httpx.AsyncClient) -> None:
    for _ in range(3):
        mundo.responder("mensagem")
    mundo.prefs.minerar(AGORA)
    [p] = mundo.preferencias()
    pergunta = {"summary": "falta", "steps": [], "missing": [{"field": "mensagem", "question": "Qual?"},
                                                              {"field": "destinatario", "question": "Para quem?"}]}
    espera = mundo.execucao(PERGUNTA, status="needs_input", plano=pergunta)
    mundo.modo(Modo.ON)
    assert mundo.prefs.sugestoes(espera) == ()                          # candidata não sugere: o dono publica antes
    mundo.publicar(p)
    execucoes = mundo.db.scalar("SELECT COUNT(*) FROM runs")
    [s] = mundo.prefs.sugestoes(espera)
    assert (s.campo, s.valor, s.item_id) == ("mensagem", RESPOSTA, p.id)
    r = await cliente.get("/api/aprendizado/preferencias/sugestoes", params={"run_id": espera})
    assert r.status_code == 200, r.text
    assert r.json() == {"run_id": espera, "modo": "on",
                        "sugestoes": [{"campo": "mensagem", "valor": RESPOSTA, "item_id": p.id}]}
    # Nada respondeu por ela: a execução continua esperando, nenhuma sucessora nasceu, nada foi gravado.
    assert mundo.db.scalar("SELECT status FROM runs WHERE id=?", (espera,)) == "needs_input"
    assert mundo.db.scalar("SELECT COUNT(*) FROM runs") == execucoes
    mundo.ajustes[0] = Ajustes(enabled=False, modo_preferencias=Modo.ON)     # `enabled: false` desliga o consumo
    assert mundo.prefs.sugestoes(espera) == ()
    mundo.modo(Modo.ON)
    # Outro perfil não herda a preferência; o modo shadow não sugere.
    de_outra = mundo.execucao(PERGUNTA, status="needs_input", plano=pergunta, perfis=(OTTILIE, BIA))
    assert mundo.prefs.sugestoes(de_outra) == ()
    mundo.modo(Modo.SHADOW)
    assert mundo.prefs.sugestoes(espera) == ()
    assert (await cliente.get("/api/aprendizado/preferencias/sugestoes",
                              params={"run_id": "r-nao-existe"})).status_code == 404
    assert (await cliente.get("/api/aprendizado/preferencias/sugestoes")).status_code == 422


# ================================================================== a porta na desambiguação (skills.application)
class Preferida:
    def __init__(self, dica: PreferenceHint | None, *, quebra: bool = False) -> None:
        self.dica = dica
        self.quebra = quebra
        self.pedidos: list[tuple[str, tuple[str, ...], tuple[str | None, ...] | None]] = []

    def preferred(self, command: str, candidates: Sequence[SkillMatch],
                  profile_ids: tuple[str | None, ...] | None) -> PreferenceHint | None:
        self.pedidos.append((command, tuple(str(c.ref) for c in candidates), profile_ids))
        if self.quebra:
            raise RuntimeError("livro fora do ar")
        return self.dica


def _empate(fonte: Preferida | PreferenciasDoLivro | None, *, versao_b: int = 1) -> IntentResolver:
    """O empate ig.a@1 × ig.b@`versao_b` (a versão publicada de ig.b agora)."""
    a = habilidade("ig.a", "abra {u}", {"u": "@ana"}, [HANDLE])
    b = habilidade("ig.b", "abra {v}", {"v": "@ana"}, [{**HANDLE, "name": "v"}])
    if versao_b != 1:
        b = replace(b, version=replace(b.version, ref=SkillRef("ig.b", versao_b), parent_version=versao_b - 1))
    return IntentResolver.standard(Fonte(SkillMatch(a), SkillMatch(b)), ParameterExtractor(lambda app_id: IG),
                                   preferences=fonte)


def test_preference_stage_decide_sozinho_so_sem_efeito() -> None:
    pedido = IntentRequest(AMBIGUO, (OTTILIE,))
    sem_efeito = Preferida(PreferenceHint("ig.b", decide=True, versions=(1,)))
    r = _empate(sem_efeito).resolve(pedido)
    assert r.status is ResolutionStatus.RESOLVED and r.intent is not None
    assert str(r.intent.ref) == "ig.b@1" and r.intent.method is ResolutionMethod.PREFERENCE
    assert ("preference", StageOutcome.MATCHED) in [(t.stage, t.outcome) for t in r.trace]
    assert sem_efeito.pedidos == [(AMBIGUO, ("ig.a@1", "ig.b@1"), (OTTILIE,))]
    # Com efeito: a pergunta continua, com a opção pré-selecionada.
    com_efeito = _empate(Preferida(PreferenceHint("ig.b", decide=False, versions=(1,)))).resolve(pedido)
    assert com_efeito.status is ResolutionStatus.NEEDS_INPUT and com_efeito.intent is None
    [q] = com_efeito.questions
    assert q.suggested == "ig.b@1" and q.as_dict()["suggested"] == "ig.b@1" and "ig.b@1" in q.options
    assert ("preference", StageOutcome.SUGGESTED) in [(t.stage, t.outcome) for t in com_efeito.trace]
    # A versão candidata AGORA não é uma das conferidas (versão nova, ou fonte que não diz a versão): só pré-seleciona.
    for dica, versao in ((PreferenceHint("ig.b", decide=True, versions=(1,)), 2),
                         (PreferenceHint("ig.b", decide=True), 1)):
        r = _empate(Preferida(dica), versao_b=versao).resolve(pedido)
        assert r.status is ResolutionStatus.NEEDS_INPUT and r.intent is None
        assert r.questions[0].suggested == f"ig.b@{versao}"
        assert ("preference", StageOutcome.SUGGESTED) in [(t.stage, t.outcome) for t in r.trace]
    # Sugestão fora dos candidatos, ausente ou porta quebrada: a pergunta de sempre, sem sugestão.
    for fonte in (Preferida(PreferenceHint("ig.fora", decide=True, versions=(1,))), Preferida(None),
                  Preferida(PreferenceHint("ig.b", decide=True, versions=(1,)), quebra=True)):
        r = _empate(fonte).resolve(pedido)
        assert r.status is ResolutionStatus.NEEDS_INPUT and r.questions[0].suggested is None
        assert "suggested" not in r.questions[0].as_dict()
    # Sem fonte, a cadeia é a de antes (as quatro etapas).
    assert _empate(None).stages == ("template", "typed", "semantic", "llm")
    assert _empate(Preferida(None)).stages == ("template", "typed", "semantic", "preference", "llm")


def test_preference_stage_nao_mexe_no_que_ja_decidiu() -> None:
    a = habilidade("ig.a", "abra {u}", {"u": "@ana"}, [HANDLE])
    fonte = Preferida(PreferenceHint("ig.a", decide=True, versions=(1,)))
    r = IntentResolver.standard(Fonte(SkillMatch(a)), ParameterExtractor(lambda app_id: IG),
                                preferences=fonte).resolve(IntentRequest(AMBIGUO, (OTTILIE,)))
    assert r.intent is not None and r.intent.method is ResolutionMethod.TEMPLATE and fonte.pedidos == []


# ------------------------------------------------------------------ a escolha no desambiguador vira preferência
def test_escolha_repetida_sem_efeito_decide_sozinha(mundo: Mundo) -> None:
    for _ in range(3):
        mundo.escolher("ig.b")
    mundo.prefs.minerar(AGORA)
    assert mundo.db.scalar("SELECT COUNT(*) FROM learning_signals WHERE kind=?",
                           (SignalKind.ESCOLHEU_HABILIDADE.value,)) == 3
    [p] = mundo.preferencias()
    assert p.source_kind is SourceKind.DISAMBIGUATION and not p.human_origin and not p.side_effect
    assert p.state is SkillState.VALIDATED                              # modo shadow: valida, não publica
    fonte = PreferenciasDoLivro(mundo.prefs)
    assert _empate(fonte).resolve(IntentRequest(AMBIGUO, (OTTILIE,))).status is ResolutionStatus.NEEDS_INPUT
    mundo.modo(Modo.ON)
    mundo.prefs.minerar(AGORA)
    [p] = mundo.preferencias()
    assert p.state is SkillState.PUBLISHED and p.state_by == "sistema"   # D1: sem efeito, com o modo em 'on'
    assert p.provenance["versoes"] == [1]                                   # a versão cujo plano foi conferido
    assert {json.loads(s["data"])["versao"] for s in mundo.db.query(
        "SELECT data FROM learning_signals WHERE kind=?", (SignalKind.ESCOLHEU_HABILIDADE.value,))} == {1}
    r = _empate(fonte).resolve(IntentRequest(AMBIGUO, (OTTILIE,)))
    assert r.intent is not None and str(r.intent.ref) == "ig.b@1" and r.intent.method is ResolutionMethod.PREFERENCE
    mundo.ajustes[0] = Ajustes(enabled=False, modo_preferencias=Modo.ON)     # `enabled: false` desliga o consumo
    assert _empate(fonte).resolve(IntentRequest(AMBIGUO, (OTTILIE,))).status is ResolutionStatus.NEEDS_INPUT
    mundo.modo(Modo.ON)
    # Perfil sem a preferência (ou prévia sem aparelho): ninguém decide por ele.
    for perfis in ((OTTILIE, BIA), (BIA,), None, ()):
        assert _empate(fonte).resolve(IntentRequest(AMBIGUO, perfis)).status is ResolutionStatus.NEEDS_INPUT


def test_versao_nova_da_habilidade_escolhida_so_pre_seleciona(mundo: Mundo) -> None:
    """O efeito foi conferido nos planos da versão OBSERVADA (ig.b@1). A versão nova (ig.b@2) pode ter ganho uma
    etapa com efeito externo (seguir, mandar DM): a preferência não responde por ela, só a pré-seleciona."""
    mundo.modo(Modo.ON)
    for _ in range(3):
        mundo.escolher("ig.b")
    mundo.prefs.minerar(AGORA)
    [p] = mundo.preferencias()
    assert p.state is SkillState.PUBLISHED and not p.side_effect
    fonte = PreferenciasDoLivro(mundo.prefs)
    assert _empate(fonte).resolve(IntentRequest(AMBIGUO, (OTTILIE,))).intent is not None
    nova = _empate(fonte, versao_b=2).resolve(IntentRequest(AMBIGUO, (OTTILIE,)))
    assert nova.status is ResolutionStatus.NEEDS_INPUT and nova.intent is None
    [q] = nova.questions
    assert q.suggested == "ig.b@2" and q.as_dict()["suggested"] == "ig.b@2"
    [etapa] = [t for t in nova.trace if t.stage == "preference"]
    assert etapa.outcome is StageOutcome.SUGGESTED and "ig.b@2" in etapa.detail
    # Escolher a v2 depois NÃO estende o que foi conferido: o efeito do plano só é lido no nascimento da preferência.
    for _ in range(3):
        mundo.escolher("ig.b", versao=2)
    mundo.prefs.minerar(AGORA)
    [p] = mundo.preferencias()
    assert p.state is SkillState.PUBLISHED and p.provenance["versoes"] == [1] and p.evidence_for == 6
    assert _empate(fonte, versao_b=2).resolve(IntentRequest(AMBIGUO, (OTTILIE,))).intent is None


def test_escolhas_em_versoes_diferentes_conferem_as_duas(mundo: Mundo) -> None:
    """Nascida de escolhas em ig.b@1 e ig.b@2, todas sem efeito: as duas versões foram conferidas e decidem; a v3,
    não."""
    mundo.modo(Modo.ON)
    mundo.escolher("ig.b")
    mundo.escolher("ig.b", versao=2)
    mundo.escolher("ig.b", versao=2)
    mundo.prefs.minerar(AGORA)
    [p] = mundo.preferencias()
    assert p.state is SkillState.PUBLISHED and p.provenance["versoes"] == [1, 2]
    fonte = PreferenciasDoLivro(mundo.prefs)
    for versao in (1, 2):
        r = _empate(fonte, versao_b=versao).resolve(IntentRequest(AMBIGUO, (OTTILIE,)))
        assert r.intent is not None and str(r.intent.ref) == f"ig.b@{versao}"
    r = _empate(fonte, versao_b=3).resolve(IntentRequest(AMBIGUO, (OTTILIE,)))
    assert r.intent is None and r.questions[0].suggested == "ig.b@3"


def test_o_veto_do_dono_vale_para_a_escolha_e_nao_para_a_versao(mundo: Mundo) -> None:
    """O dono desligou "no empate, escolhe ig.b": uma versão nova observada depois não é conteúdo novo. As versões
    conferidas ficam fora da identidade do item (o veto casa pelo `content_hash`), senão o sistema traria de volta —
    e publicaria sozinho — o que uma pessoa desligou."""
    mundo.modo(Modo.ON)
    for _ in range(3):
        mundo.escolher("ig.b")
    mundo.prefs.minerar(AGORA)
    [p] = mundo.preferencias()
    mundo.servico.mudar_estado(LivroKind.PREFERENCIA, p.id, SkillState.DISABLED, by=DONO, reason="não quero")
    for _ in range(3):
        mundo.escolher("ig.b", versao=2)
    mundo.prefs.minerar(AGORA)
    assert [(i.id, i.state) for i in mundo.preferencias()] == [(p.id, SkillState.DISABLED)]


def test_escolha_com_efeito_espera_o_dono_e_so_pre_seleciona(mundo: Mundo) -> None:
    mundo.modo(Modo.ON)
    for _ in range(3):
        mundo.escolher("ig.b", efeito=True)
    mundo.escolher("ig.fora")                                           # reescreveu para outra coisa: não conta
    mundo.prefs.minerar(AGORA)
    [p] = mundo.preferencias()
    assert p.side_effect and p.state is SkillState.VALIDATED            # o sistema não publica o que tem efeito
    assert [e.ref for e in mundo.servico.pendentes() if e.kind is LivroKind.PREFERENCIA] == [p.id]
    fonte = PreferenciasDoLivro(mundo.prefs)
    assert _empate(fonte).resolve(IntentRequest(AMBIGUO, (OTTILIE,))).questions[0].suggested is None
    mundo.publicar(p)
    r = _empate(fonte).resolve(IntentRequest(AMBIGUO, (OTTILIE,)))
    assert r.status is ResolutionStatus.NEEDS_INPUT and r.questions[0].suggested == "ig.b@1"


def test_escolha_diferente_contradiz(mundo: Mundo) -> None:
    mundo.modo(Modo.ON)
    for _ in range(3):
        mundo.escolher("ig.b")
    mundo.prefs.minerar(AGORA)
    mundo.escolher("ig.a")
    mundo.prefs.minerar(AGORA)
    [p] = mundo.preferencias()
    assert p.state is SkillState.DISABLED
    assert _empate(PreferenciasDoLivro(mundo.prefs)).resolve(
        IntentRequest(AMBIGUO, (OTTILIE,))).status is ResolutionStatus.NEEDS_INPUT


# ================================================================== a execução na trilha e no bloco do D2
def _trilha(mundo: Mundo, item_id: str) -> list[tuple[str | None, str, str | None]]:
    return [(r["from_state"], r["to_state"], r["run_id"]) for r in mundo.db.query(
        "SELECT from_state, to_state, run_id FROM learning_transitions WHERE item_ref=? ORDER BY id", (item_id,))]


def test_nascimento_leva_a_execucao_que_fechou_o_limiar(mundo: Mundo) -> None:
    """Cinco escolhas iguais antes da curadoria: quem fechou o limiar foi a TERCEIRA (as seguintes só reforçam). A
    validação na mesma passada vem da mesma evidência; a publicação numa passada seguinte, porque o modo passou a
    'on', não tem execução que a cause."""
    execucoes = [mundo.escolher("ig.b")[0] for _ in range(5)]
    mundo.prefs.minerar(AGORA)
    [p] = mundo.preferencias()
    terceira = execucoes[2]
    assert p.provenance["limiar"] == {"run_id": terceira, "execucoes": 3}
    assert p.provenance["runs"] == execucoes                               # a proveniência segue com todas
    assert _trilha(mundo, p.id) == [(None, "candidate", terceira), ("candidate", "validated", terceira)]
    mundo.modo(Modo.ON)
    mundo.prefs.minerar(AGORA)
    assert _trilha(mundo, p.id)[-1] == ("validated", "published", None)


def test_com_o_modo_on_nascer_validar_publicar_e_desligar_levam_a_execucao(mundo: Mundo) -> None:
    mundo.modo(Modo.ON)
    execucoes = [mundo.escolher("ig.b")[0] for _ in range(3)]
    mundo.prefs.minerar(AGORA)
    [p] = mundo.preferencias()
    terceira = execucoes[2]
    assert _trilha(mundo, p.id) == [(None, "candidate", terceira), ("candidate", "validated", terceira),
                                    ("validated", "published", terceira)]
    # A escolha diferente depois contradiz: a execução dela é a que desligou.
    diferente, _ = mundo.escolher("ig.a")
    mundo.prefs.minerar(AGORA)
    assert _trilha(mundo, p.id)[-1] == ("published", "disabled", diferente)


def test_resposta_repetida_nasce_e_e_contradita_com_a_execucao(mundo: Mundo) -> None:
    """Texto de pessoa: o sistema não valida, mas o nascimento e o desligamento pela resposta diferente levam a
    execução que os fechou."""
    execucoes = [mundo.responder("mensagem")[0] for _ in range(4)]
    mundo.prefs.minerar(AGORA)
    [p] = mundo.preferencias()
    assert p.provenance["limiar"] == {"run_id": execucoes[2], "execucoes": 3}
    assert _trilha(mundo, p.id) == [(None, "candidate", execucoes[2])]
    diferente, _ = mundo.responder("mensagem", "mande a mensagem 'boa noite' para o grupo")
    mundo.prefs.minerar(AGORA)
    assert _trilha(mundo, p.id)[-1] == ("candidate", "disabled", diferente)


def test_a_diferente_seguida_de_iguais_desliga_com_a_execucao_da_diferente(mundo: Mundo) -> None:
    """A escolha diferente e, depois dela, mais iguais antes da mesma curadoria: quem desligou foi a diferente, não a
    última registrada (a evidência mais nova é uma "a favor")."""
    mundo.modo(Modo.ON)
    for _ in range(3):
        mundo.escolher("ig.b")
    mundo.prefs.minerar(AGORA)
    [p] = mundo.preferencias()
    mundo.passar()
    diferente, _ = mundo.escolher("ig.a")
    iguais = [mundo.escolher("ig.b")[0] for _ in range(2)]
    mundo.prefs.minerar(AGORA)
    assert _trilha(mundo, p.id)[-1] == ("published", "disabled", diferente)
    assert iguais[-1] != diferente


def test_reativada_pela_pessoa_o_desligamento_seguinte_nao_culpa_a_execucao_antiga(mundo: Mundo) -> None:
    """A pessoa reativou o que a escolha diferente desligou. A curadoria seguinte desliga de novo (o veredito pesa toda
    a evidência, e isso é de antes desta regra), mas a execução antiga já tinha sido pesada — por quem desligou e por
    quem reativou —: sem evidência nova, a trilha fica sem `run_id`. Uma escolha diferente nova, depois da reativação,
    é a execução que desliga."""
    mundo.modo(Modo.ON)
    for _ in range(3):
        mundo.escolher("ig.b")
    mundo.prefs.minerar(AGORA)
    diferente, _ = mundo.escolher("ig.a")
    mundo.prefs.minerar(AGORA)
    [p] = mundo.preferencias()
    assert _trilha(mundo, p.id)[-1] == ("published", "disabled", diferente)
    mundo.passar()
    mundo.servico.mudar_estado(LivroKind.PREFERENCIA, p.id, SkillState.PUBLISHED, by=DONO, reason="quero de volta")
    mundo.passar()
    mundo.prefs.minerar(AGORA)
    assert _trilha(mundo, p.id)[-2:] == [("disabled", "published", None), ("published", "disabled", None)]
    mundo.passar()
    mundo.servico.mudar_estado(LivroKind.PREFERENCIA, p.id, SkillState.PUBLISHED, by=DONO, reason="de novo")
    mundo.passar()
    nova, _ = mundo.escolher("ig.a")
    mundo.prefs.minerar(AGORA)
    assert _trilha(mundo, p.id)[-1] == ("published", "disabled", nova)


def test_so_a_evidencia_desde_a_ultima_mudanca_de_estado() -> None:
    def ev(n: int, quando: str) -> Evidencia:
        return Evidencia(item_ref="li-x", stance=Posicao.FOR, origin_ref=f"signal:{n}", run_id=f"r{n}",
                         instance_id=None, app_version=None, simulated=False, detail=None, observed_at=quando)

    def tr(n: int, de: SkillState | None, para: SkillState, quando: str) -> Transicao:
        return Transicao(id=n, item_ref="li-x", item_kind=LivroKind.PREFERENCIA, content_hash=None, scope_key="k",
                         app_version=None, from_state=de, to_state=para, reason="r", decided_by="sistema",
                         decided_at=quando, run_id=None)

    t0, t1, t2 = "2026-09-29T12:00:00.000Z", "2026-09-29T12:01:00.000Z", "2026-09-29T12:02:00.000Z"
    evs = [ev(1, t0), ev(2, t0), ev(3, t1), ev(4, t2)]
    assert desde_a_ultima_mudanca(evs, []) == evs                                   # sem trilha: toda
    # O nascimento e a evidência da mesma passada caem no mesmo milissegundo: são dele.
    assert desde_a_ultima_mudanca(evs, [tr(1, None, SkillState.CANDIDATE, t0)]) == evs
    nascida_e_reativada = [tr(1, None, SkillState.CANDIDATE, t0), tr(2, SkillState.CANDIDATE, SkillState.DISABLED, t0),
                           tr(3, SkillState.DISABLED, SkillState.PUBLISHED, t1)]
    assert desde_a_ultima_mudanca(evs, nascida_e_reativada) == evs[2:]
    # A mudança de detalhe (`de = para`) não é mudança de estado: o marco continua o anterior.
    detalhe = [*nascida_e_reativada, tr(4, SkillState.PUBLISHED, SkillState.PUBLISHED, t2)]
    assert desde_a_ultima_mudanca(evs, detalhe) == evs[2:]


def test_execucao_que_fechou_nao_inventa() -> None:
    def obs(run: str) -> Observacao:
        return Observacao(origem=f"signal:{run}", run_id=run, perfil=OTTILIE, campo="skill", modelo="emp-x",
                          valor="ig.b", app_package="com.instagram.android", run_sucessora=None, simulated=False)

    assert execucao_que_fechou([obs("r1"), obs("r1"), obs("r2")]) is None          # 2 execuções não fecham
    assert execucao_que_fechou([obs("r1"), obs("r2"), obs("r2"), obs("r3"), obs("r4")]) == ("r3", 3)
    # A observação sem execução não conta como uma (como no veredito da validação): nem fecha, nem soma no "entre N".
    assert execucao_que_fechou([obs("r1"), obs("r2"), obs("")]) is None
    assert execucao_que_fechou([obs(""), obs("r1"), obs("r2")]) is None
    assert execucao_que_fechou([obs(""), obs("r1"), obs("r2"), obs("r3")]) == ("r3", 3)


@dataclass
class _SoEscolhas:
    """A leitura com escolhas dadas pelo teste (a do banco sempre traz a execução; `''` é a linha legada sem ela)."""

    obs: list[Observacao]

    def respostas(self) -> list[Observacao]:
        return []

    def escolhas_a_registrar(self, desde: str) -> list[EscolhaObservada]:
        return []

    def escolhas(self) -> list[Observacao]:
        return list(self.obs)

    def comando(self, run_id: str) -> str | None:
        return None

    def plano_tem_efeito(self, run_id: str) -> bool:
        return False

    def perguntas_abertas(self, run_id: str) -> PerguntasAbertas | None:
        return None


def test_escolha_sem_execucao_nao_conta_para_o_nascimento(mundo: Mundo) -> None:
    """O nascimento conta as execuções como a validação: a escolha sem `run_id` não é uma. Com ela e mais duas, nada
    nasce (a validação, com as mesmas, não fecharia); com três de verdade, nasce "entre 3", sem a vazia na
    proveniência."""
    runs = [mundo.execucao(AMBIGUO, status="cancelled") for _ in range(3)]

    def obs(run: str, n: int) -> Observacao:
        return Observacao(origem=f"signal:{n}", run_id=run, perfil=OTTILIE, campo="skill", modelo="emp-x",
                          valor="ig.b", app_package="com.instagram.android", run_sucessora=None, simulated=False,
                          opcoes=("ig.a", "ig.b"), versao=1)

    leitura = _SoEscolhas([obs("", 1), obs(runs[0], 2), obs(runs[1], 3)])
    prefs = ServicoDePreferencias(mundo.servico, mundo.repo, leitura, TriagemDeCredencial())
    prefs.minerar(AGORA)
    assert mundo.preferencias() == []
    leitura.obs.append(obs(runs[2], 4))
    prefs.minerar(AGORA)
    [p] = mundo.preferencias()
    assert p.provenance["limiar"] == {"run_id": runs[2], "execucoes": 3}
    assert p.provenance["runs"] == runs
    assert _trilha(mundo, p.id)[0] == (None, "candidate", runs[2])


async def test_a_preferencia_aparece_no_bloco_da_execucao_que_fechou_o_limiar(
        mundo: Mundo, cliente: httpx.AsyncClient) -> None:
    """`GET /api/runs/{id}/feedback` → `aprendizado.candidatas`: só na execução que fechou o limiar, "entre 3
    execuções"; nas outras ela só foi reforçada, e o bloco não a mostra (a página Aprendizado, sim)."""
    mundo.modo(Modo.ON)
    execucoes = [mundo.escolher("ig.b")[0] for _ in range(4)]
    mundo.prefs.minerar(AGORA)
    [p] = mundo.preferencias()

    async def bloco(run: str) -> dict[str, list[dict[str, object]]]:
        r = await cliente.get(f"/api/runs/{run}/feedback")
        assert r.status_code == 200, r.text
        lido = r.json()["aprendizado"]
        assert isinstance(lido, dict), lido            # `null` é a leitura que quebrou: não passa em silêncio
        return lido

    lido = await bloco(execucoes[2])
    [linha] = lido["candidatas"]
    assert all(not v for k, v in lido.items() if k != "candidatas")
    assert (linha["kind"], linha["ref"], linha["estado"]) == (LivroKind.PREFERENCIA.value, p.id,
                                                              SkillState.PUBLISHED.value)
    assert linha["papel"] == ("preferência que nasceu com a evidência desta execução, entre 3 execuções, validada e "
                              "publicada nesta execução, evidência a favor")
    assert isinstance(linha["titulo"], str) and "ig.b" in linha["titulo"]
    for outra in (execucoes[0], execucoes[1], execucoes[3]):
        assert all(not v for v in (await bloco(outra)).values())
    # Sem a marca na proveniência (item de antes desta regra, ou ilegível): sem número, "e de outras".
    mundo.db.execute("UPDATE learning_items SET provenance='{}' WHERE id=?", (p.id,))
    [linha] = (await bloco(execucoes[2]))["candidatas"]
    assert isinstance(linha["papel"], str) and linha["papel"].startswith(
        "preferência que nasceu com a evidência desta execução e de outras,")


# ================================================================== DAG
def test_skills_nao_importa_learning() -> None:
    raiz = Path(__file__).resolve().parents[1] / "app" / "modules" / "skills"
    for arquivo in raiz.rglob("*.py"):
        for no in ast.walk(ast.parse(arquivo.read_text(encoding="utf-8"))):
            if isinstance(no, ast.ImportFrom):
                assert "learning" not in (no.module or ""), arquivo
            elif isinstance(no, ast.Import):
                assert not any("learning" in n.name for n in no.names), arquivo
