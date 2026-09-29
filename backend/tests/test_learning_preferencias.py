"""Preferências como sugestão pré-preenchida (ADR-054, pacote A9) e a porta da desambiguação em `skills.application`.

- a MESMA resposta 3 vezes (mesmo campo, mesmo modelo de comando, mesmo perfil), sem nenhuma diferente, vira
  candidata; antes disso, e com uma resposta diferente no meio, não; depois, a diferente a contradiz;
- campo que é alvo de terceiro (destinatário, perfil, conversa...) nunca vira padrão;
- a preferência só PRÉ-PREENCHE: a sugestão é lida, a execução continua esperando a pessoa e nada nasce sozinho;
- o `PreferenceStage` decide sozinho só quando a habilidade escolhida não tem etapa com efeito externo; com efeito, a
  pergunta continua, com a opção pré-selecionada;
- a escolha no desambiguador é observada pela sucessora (a execução que a pessoa respondeu) e vira preferência sem
  texto de pessoa: o sistema a valida pela repetição e a publica só sem efeito e com o modo em `on`;
- `skills` nunca importa `learning` (DAG).

Nível de prova: `simulated` (banco de teste pela fábrica da suíte, habilidades falsas; nenhum aparelho, nenhuma IA).
"""
from __future__ import annotations

import ast
import hashlib
import json
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from app.db import Database
from app.modules.learning.application.ports import Ajustes
from app.modules.learning.application.preferencias import ServicoDePreferencias, campo_de_terceiro
from app.modules.learning.application.servico import LearningService
from app.modules.learning.domain.ciclo import SkillState
from app.modules.learning.domain.livro import ItemDeAprendizado
from app.modules.learning.domain.vocabulario import LivroKind, Modo, SignalKind, SourceKind
from app.modules.learning.infrastructure.fontes import FontesSql
from app.modules.learning.infrastructure.ligar_costuras import costuras_do_livro
from app.modules.learning.infrastructure.preferencias_sql import PreferenciasDoLivro, montar_preferencias
from app.modules.learning.infrastructure.segredo import TriagemDeCredencial
from app.modules.learning.infrastructure.sql_repository import SqlLearningRepository
from app.modules.learning.presentation.router import router as learning_router
from app.modules.skills.application.intent_ports import PreferenceHint
from app.modules.skills.application.intent_resolver import IntentRequest, IntentResolver, ParameterExtractor
from app.modules.skills.domain.intent import ResolutionMethod, ResolutionStatus, SkillMatch, StageOutcome
from app.taskqueue.costuras import RespostaAPergunta
from app.util import to_iso

from .fake_skills import banco, perfil
from .test_intencao_dominio import HANDLE, IG, Fonte, habilidade

ANDRE, BIA = "p-andre", "p-bia"
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

    def __post_init__(self) -> None:
        perfil(self.db, ANDRE)
        perfil(self.db, BIA)
        self.repo = SqlLearningRepository(self.db, precos=dict, clock=lambda: to_iso(AGORA))
        self.servico = LearningService(self.repo, FontesSql(self.db), TriagemDeCredencial(),
                                       ajustes=lambda: self.ajustes[0], relogio=lambda: AGORA,
                                       retencao_de_logs_dias=lambda: 14)
        prefs = montar_preferencias(self.db, self.servico)
        assert prefs is not None
        self.prefs: ServicoDePreferencias = prefs
        self.costuras = costuras_do_livro(self.servico, self.db)

    def modo(self, modo: Modo) -> None:
        self.ajustes[0] = Ajustes(modo_preferencias=modo)

    # ---------------------------------------------------------------- execuções
    def execucao(self, comando: str, *, status: str, perfis: Sequence[str] = (ANDRE,), plano: object = None,
                 skill_id: str | None = None, simulado: bool = False) -> str:
        self.n += 1
        run = f"r-{self.n:03d}"
        foto = {"alvos": [{"instance_id": f"android-{i:02d}", "profile_id": p} for i, p in enumerate(perfis, 6)],
                "command_sem_destinos": comando, "device_policy": "one", "pedido": {"profile_ids": list(perfis)}}
        self.db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids,"
                        " created_at, plan, targets, skill_id, skill_version) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (run, run, comando, "plan", status, int(simulado), "[]", to_iso(AGORA),
                         json.dumps(plano) if plano is not None else None, json.dumps(foto), skill_id,
                         1 if skill_id else None))
        return run

    def responder(self, campo: str, resposta: str = RESPOSTA, *, perfis: Sequence[str] = (ANDRE,),
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

    def escolher(self, escolhida: str, *, efeito: bool = False, perfis: Sequence[str] = (ANDRE,),
                 candidatas: Sequence[str] = ("ig.a@1", "ig.b@1")) -> tuple[str, str]:
        """A pessoa respondeu o empate entre habilidades reescrevendo o comando: a sucessora resolveu `escolhida`."""
        antiga = self.execucao(AMBIGUO, status="cancelled", perfis=perfis)
        pergunta = {"field": "skill", "question": "Qual delas?", "reason": "ambiguous_intent",
                    "options": list(candidatas)}
        self.db.execute("INSERT INTO events(ts, kind, level, run_id, message, data) VALUES (?,?,?,?,?,?)",
                        (to_iso(AGORA), "log", "warn", antiga, "empate",
                         json.dumps({"skill": None, "questions": [pergunta], "candidates": list(candidatas)})))
        nova = self.execucao(f"{AMBIGUO} ({escolhida})", status="completed", perfis=perfis, skill_id=escolhida,
                             plano={"summary": "s", "steps": [{"key": "a", "side_effect": efeito}]})
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
    assert (p.escopo.profile_id, p.escopo.capability) == (ANDRE, "mensagem")
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
    # Outro perfil não herda a preferência; o modo shadow não sugere.
    de_outra = mundo.execucao(PERGUNTA, status="needs_input", plano=pergunta, perfis=(ANDRE, BIA))
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


def _empate(fonte: Preferida | PreferenciasDoLivro | None) -> IntentResolver:
    a = habilidade("ig.a", "abra {u}", {"u": "@ana"}, [HANDLE])
    b = habilidade("ig.b", "abra {v}", {"v": "@ana"}, [{**HANDLE, "name": "v"}])
    return IntentResolver.standard(Fonte(SkillMatch(a), SkillMatch(b)), ParameterExtractor(lambda app_id: IG),
                                   preferences=fonte)


def test_preference_stage_decide_sozinho_so_sem_efeito() -> None:
    pedido = IntentRequest(AMBIGUO, (ANDRE,))
    sem_efeito = Preferida(PreferenceHint("ig.b", decide=True))
    r = _empate(sem_efeito).resolve(pedido)
    assert r.status is ResolutionStatus.RESOLVED and r.intent is not None
    assert str(r.intent.ref) == "ig.b@1" and r.intent.method is ResolutionMethod.PREFERENCE
    assert ("preference", StageOutcome.MATCHED) in [(t.stage, t.outcome) for t in r.trace]
    assert sem_efeito.pedidos == [(AMBIGUO, ("ig.a@1", "ig.b@1"), (ANDRE,))]
    # Com efeito: a pergunta continua, com a opção pré-selecionada.
    com_efeito = _empate(Preferida(PreferenceHint("ig.b", decide=False))).resolve(pedido)
    assert com_efeito.status is ResolutionStatus.NEEDS_INPUT and com_efeito.intent is None
    [q] = com_efeito.questions
    assert q.suggested == "ig.b@1" and q.as_dict()["suggested"] == "ig.b@1" and "ig.b@1" in q.options
    assert ("preference", StageOutcome.SUGGESTED) in [(t.stage, t.outcome) for t in com_efeito.trace]
    # Sugestão fora dos candidatos, ausente ou porta quebrada: a pergunta de sempre, sem sugestão.
    for fonte in (Preferida(PreferenceHint("ig.fora", decide=True)), Preferida(None),
                  Preferida(PreferenceHint("ig.b", decide=True), quebra=True)):
        r = _empate(fonte).resolve(pedido)
        assert r.status is ResolutionStatus.NEEDS_INPUT and r.questions[0].suggested is None
        assert "suggested" not in r.questions[0].as_dict()
    # Sem fonte, a cadeia é a de antes (as quatro etapas).
    assert _empate(None).stages == ("template", "typed", "semantic", "llm")
    assert _empate(Preferida(None)).stages == ("template", "typed", "semantic", "preference", "llm")


def test_preference_stage_nao_mexe_no_que_ja_decidiu() -> None:
    a = habilidade("ig.a", "abra {u}", {"u": "@ana"}, [HANDLE])
    fonte = Preferida(PreferenceHint("ig.a", decide=True))
    r = IntentResolver.standard(Fonte(SkillMatch(a)), ParameterExtractor(lambda app_id: IG),
                                preferences=fonte).resolve(IntentRequest(AMBIGUO, (ANDRE,)))
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
    assert _empate(fonte).resolve(IntentRequest(AMBIGUO, (ANDRE,))).status is ResolutionStatus.NEEDS_INPUT
    mundo.modo(Modo.ON)
    mundo.prefs.minerar(AGORA)
    [p] = mundo.preferencias()
    assert p.state is SkillState.PUBLISHED and p.state_by == "sistema"   # D1: sem efeito, com o modo em 'on'
    r = _empate(fonte).resolve(IntentRequest(AMBIGUO, (ANDRE,)))
    assert r.intent is not None and str(r.intent.ref) == "ig.b@1" and r.intent.method is ResolutionMethod.PREFERENCE
    # Perfil sem a preferência (ou prévia sem aparelho): ninguém decide por ele.
    for perfis in ((ANDRE, BIA), (BIA,), None, ()):
        assert _empate(fonte).resolve(IntentRequest(AMBIGUO, perfis)).status is ResolutionStatus.NEEDS_INPUT


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
    assert _empate(fonte).resolve(IntentRequest(AMBIGUO, (ANDRE,))).questions[0].suggested is None
    mundo.publicar(p)
    r = _empate(fonte).resolve(IntentRequest(AMBIGUO, (ANDRE,)))
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
        IntentRequest(AMBIGUO, (ANDRE,))).status is ResolutionStatus.NEEDS_INPUT


# ================================================================== DAG
def test_skills_nao_importa_learning() -> None:
    raiz = Path(__file__).resolve().parents[1] / "app" / "modules" / "skills"
    for arquivo in raiz.rglob("*.py"):
        for no in ast.walk(ast.parse(arquivo.read_text(encoding="utf-8"))):
            if isinstance(no, ast.ImportFrom):
                assert "learning" not in (no.module or ""), arquivo
            elif isinstance(no, ast.Import):
                assert not any("learning" in n.name for n in no.names), arquivo
