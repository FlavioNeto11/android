"""O botão do D2 (ADR-054, decisão 2): "deu certo / deu errado + motivo", os efeitos do voto e as rotas de sinais.

O que se prova:
- `POST|GET /api/runs/{id}/feedback` e `GET /api/aprendizado/sinais` em nível HTTP; e, pelo app INTEIRO
  (`create_app`), que o voto não cai em `POST /api/runs/{run_id}/{op}` (a ordem dos roteadores);
- a borda: 'errado' sem motivo, motivo fora do vocabulário, 'certo' com motivo e corpo com campo a mais → 422;
  execução ou objetivo inexistente (ou de outra execução) → 404; nota com cara de credencial → 409
  `note_looks_secret` SEM gravar nada (nem o sinal, nem o rebaixamento);
- um voto por pessoa e por item (upsert); sem `objective_id` o voto vale para a execução (`run:<id>`); votar de
  novo troca o voto e NÃO reativa o que foi desligado; `created_by` pela regra `_quem`;
- 'errado' de navegação num item `succeeded` desliga o fluxo que a execução usou (`runs.flow_id`) e o que ela gerou
  (`source_run_id`), põe em quarentena as receitas que o item usou (`attempts.recipe_id`) e as que aprendeu
  (`learned_from_step`) — com trilha em nome de quem votou, e nada fora do item; a versão de habilidade e a lição
  exposta recebem evidência contra (a habilidade nunca é desabilitada); duas refutações desligam a lição;
- etapa comprovada pela tela (`verified=true`) → falso positivo do verificador (o sinal leva `step_verified=1` e
  `data.verificador`, que o relatório "O que mais falha" do A3 lê); etapa confirmada à mão não é falso positivo;
- 'certo' num item que falhou: falso negativo, e o status do item NÃO muda; 'certo' num item concluído: evidência a
  favor do que ele usou;
- `texto_ruim` só alimenta a voz; `demorou_ou_gastou` só o relatório; `pediu_ajuda_a_toa` só tela e lição;
- execução simulada: o voto é gravado com `simulated=1` e nada é rebaixado;
- a reativação em um clique pelo `POST /api/aprendizado/{kind}/{ref}/status`, com a trilha de volta.

Nível de prova: `simulated` (banco de teste; nenhum aparelho, nenhuma IA). O último teste usa o Harness da porta 5640.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, Request, Response

from app.db import Database
from app.modules.learning.application.ports import Ajustes, NovoSinal
from app.modules.learning.application.servico import LearningService
from app.modules.learning.domain.ciclo import SYSTEM_ACTOR, EntradaInvalida, SkillState
from app.modules.learning.domain.livro import Escopo, NovoItem
from app.modules.learning.domain.vocabulario import (LivroKind, MotivoDoVoto, Polaridade, SignalKind, SourceKind,
                                                     Veredito)
from app.modules.learning.domain.voto import (AcaoDoEfeito, Conhecimento, Desfecho, ItemVotado, Uso, Verificador,
                                              desfecho, efeitos_do_voto, licao_refutada)
from app.modules.learning.infrastructure.fontes import FontesSql
from app.modules.learning.infrastructure.montagem import GuardaDoFluxo
from app.modules.learning.infrastructure.segredo import TriagemDeCredencial
from app.modules.learning.infrastructure.sql_repository import SqlLearningRepository
from app.modules.learning.presentation.router import router as learning_router
from app.modules.skills.infrastructure.sql_repository import SqlSkillRepository

from .conftest import Harness
from .fake_skills import TS, ValidadorFalso
from .fake_skills import banco as banco_migrado

S = SkillState
PACOTE = "com.instagram.android"
AGORA = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
RUN = "r-20260928234657-bbdf3c"
SIM = "r-simulada"
NOTA_COM_SENHA = "a senha é Hunter2!x9"          # valor falso com FORMATO de credencial (o mesmo do A1)


# ------------------------------------------------------------------ o mundo semeado
def _execucao(db: Database, run_id: str, *, simulated: bool = False, status: str = "completed",
              flow_id: str | None = None, skill: tuple[str, int] | None = None) -> None:
    db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at,"
               " app_ids, flow_id, skill_id, skill_version) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
               (run_id, run_id, "abrir o perfil de @alguem", "execute", status, int(simulated),
                '["android-06","android-07"]', TS, '["instagram"]', flow_id, skill[0] if skill else None,
                skill[1] if skill else None))


def _objetivo(db: Database, run_id: str, instancia: str, status: str, *, perfil: str | None = None) -> str:
    oid = f"{run_id}:{instancia}"
    db.execute("INSERT INTO objectives(id, run_id, instance_id, status, profile_id) VALUES (?,?,?,?,?)",
               (oid, run_id, instancia, status, perfil))
    return oid


def _etapa(db: Database, oid: str, chave: str, *, status: str, verificada: bool | None, seq: int = 0,
           receita: int | None = None, erro: str | None = None, acao: str = "abrir_perfil") -> str:
    run_id, _, instancia = oid.rpartition(":")
    sid = f"{oid}:v1:{chave}"
    resultado = json.dumps({"verified": verificada}) if verificada is not None else None
    db.execute("INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal,"
               " postcondition, timeout_s, max_attempts, status, capability, app_id, template_hash, result)"
               " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (sid, run_id, oid, instancia, 1, seq, chave, chave, chave, "{}", 60, 3, status, acao, "instagram",
                f"h-{chave}", resultado))
    tentativa = "succeeded" if status == "succeeded" else "failed"
    db.execute("INSERT INTO attempts(id, step_id, number, status, started_at, finished_at, error, recipe_id)"
               " VALUES (?,?,?,?,?,?,?,?)", (f"{sid}:a1", sid, 1, tentativa, TS, TS, erro, receita))
    return sid


def _receita(db: Database, passo: str, *, status: str, aprendida_em: str) -> int:
    acoes = [{"tool": "tap", "args": {}, "selectors": [{"rid": f"botao-{passo}"}], "commit": False}]
    return int(db.inserted_id(
        "INSERT INTO recipes(app_package, app_version, app_signature, variant, step_hash, step_key, version, status,"
        " actions, learned_from_step, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (PACOTE, "447", "sig", "pt/420", f"h-{passo}", passo, 1, status, json.dumps(acoes), aprendida_em, TS)))


def _fluxo(db: Database, fid: str, *, status: str, origem: str | None) -> None:
    plano = {"summary": fid, "app_id": "instagram", "parameters": {},
             "steps": [{"key": "a", "title": "a", "goal": "a", "side_effect": False,
                        "postcondition": {"kind": "app_foreground", "value": "x", "description": "x"}}]}
    db.execute("INSERT INTO flows(id, name, match_key, command_template, plan, app_id, status, created_at, source,"
               " source_run_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
               (fid, fid, f"cmd {fid}", f"cmd {fid}", json.dumps(plano), "instagram", status, TS, "run", origem))


def _habilidade(db: Database, sid: str, estado: str) -> None:
    db.execute("INSERT INTO skill_definitions(id, name, app_id, created_at, updated_at) VALUES (?,?,?,?,?)",
               (sid, f"Habilidade {sid}", "instagram", TS, TS))
    db.execute("INSERT INTO skill_versions(id, skill_id, version, state, content, content_hash, source_kind,"
               " created_at, state_at) VALUES (?,?,?,?,?,?,?,?,?)",
               (f"{sid}@1", sid, 1, estado, "{}", "h", "teaching", TS, TS))


class Mundo:
    """Uma execução real com dois itens (android-06 concluiu comprovado pela tela; android-07 falhou), um item de
    outra execução concluído com confirmação à mão, e uma execução simulada — cada um com o que usou e aprendeu."""

    def __init__(self, db: Database) -> None:
        self.db = db
        db.execute("INSERT INTO apps(id, name, package, builtin) VALUES ('instagram','Instagram',?,0)", (PACOTE,))
        _habilidade(db, "ig.abrir", "published")
        _fluxo(db, "fluxo-usado", status="active", origem="r-antiga")
        _fluxo(db, "fluxo-aprendido", status="candidate", origem=RUN)
        _fluxo(db, "fluxo-alheio", status="active", origem="r-outra")
        _execucao(db, RUN, flow_id="fluxo-usado", skill=("ig.abrir", 1))
        self.ok = _objetivo(db, RUN, "android-06", "succeeded", perfil="p-lucas")
        self.falhou = _objetivo(db, RUN, "android-07", "failed")
        self.receita_usada = _receita(db, "abrir", status="active", aprendida_em="r-antiga:android-06:v1:abrir")
        self.receita_alheia = _receita(db, "voltar", status="active", aprendida_em="r-outra:android-01:v1:voltar")
        self.etapa_abrir = _etapa(db, self.ok, "abrir", status="succeeded", verificada=True,
                                  receita=self.receita_usada)
        self.etapa_rolar = _etapa(db, self.ok, "rolar", status="succeeded", verificada=True, seq=1)
        self.receita_aprendida = _receita(db, "rolar", status="candidate", aprendida_em=self.etapa_rolar)
        self.etapa_falha = _etapa(db, self.falhou, "abrir", status="failed", verificada=None,
                                  erro="Pós-condição não comprovada: o perfil não apareceu", acao="abrir_perfil")
        # Outra execução real, concluída com uma etapa CONFIRMADA À MÃO (verified=false): não é falso positivo.
        _execucao(db, "r-a-mao", flow_id="fluxo-alheio")
        self.a_mao = _objetivo(db, "r-a-mao", "android-06", "succeeded")
        _etapa(db, self.a_mao, "abrir", status="succeeded", verificada=False)
        # Execução simulada: usou um fluxo e uma receita ativos; o voto nela não rebaixa nada.
        _fluxo(db, "fluxo-simulado", status="active", origem="r-velha")
        self.receita_simulada = _receita(db, "curtir", status="active", aprendida_em="r-velha:android-01:v1:curtir")
        _execucao(db, SIM, simulated=True, flow_id="fluxo-simulado")
        self.simulado = _objetivo(db, SIM, "android-01", "succeeded")
        _etapa(db, self.simulado, "curtir", status="succeeded", verificada=True, receita=self.receita_simulada)
        habilidades = SqlSkillRepository(db, ValidadorFalso())
        self.repo = SqlLearningRepository(db, guarda_do_fluxo=GuardaDoFluxo(db, habilidades), precos=dict)
        self.servico = LearningService(self.repo, FontesSql(db), TriagemDeCredencial(), ajustes=Ajustes,
                                       relogio=lambda: AGORA, retencao_de_logs_dias=lambda: 14)
        # Uma lição publicada exposta (braço `with`) na etapa do item que deu certo, e outra no braço de controle.
        self.licao = self._licao("abra pelo atalho do perfil")
        self.licao_holdout = self._licao("role devagar")
        for item, braco in ((self.licao, "with"), (self.licao_holdout, "holdout")):
            db.execute("INSERT INTO learning_exposures(item_id, unit_id, role, arm, tokens, run_id, objective_id,"
                       " app_package, capability, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                       (item, f"step:{self.etapa_abrir}", "actor", braco, 12 if braco == "with" else 0, RUN, self.ok,
                        PACOTE, "abrir_perfil", TS))

    def _licao(self, texto: str) -> str:
        item = self.servico.propor(NovoItem(kind=LivroKind.LICAO, escopo=Escopo(app=PACOTE, capability="abrir_perfil",
                                                                                role="actor"),
                                            content={"texto": texto}, summary=texto, source_kind=SourceKind.RECOVERY,
                                            side_effect=False))
        self.servico.mudar_estado(LivroKind.LICAO, item.id, S.VALIDATED, by="dono", reason="repetiu")
        self.servico.mudar_estado(LivroKind.LICAO, item.id, S.PUBLISHED, by="dono", reason="aprovada")
        return item.id

    def status(self, tabela: str, ref: str | int) -> str:
        return str(self.db.scalar(f"SELECT status FROM {tabela} WHERE id=?", (ref,)))

    def sinais(self) -> list[dict[str, object]]:
        return [dict(r) for r in self.db.query("SELECT * FROM learning_signals ORDER BY id")]


@pytest.fixture
def mundo(tmp_path: Path) -> Mundo:
    db = banco_migrado(tmp_path, "feedback.sqlite3")
    yield Mundo(db)
    db.close()


@pytest.fixture
async def cliente(mundo: Mundo) -> AsyncIterator[httpx.AsyncClient]:
    """O roteador do aprendizado sobre um app mínimo; o operador da sessão vem do cabeçalho `x-operador` (o papel do
    middleware de `main.py`)."""
    app = FastAPI()

    @app.middleware("http")
    async def operador(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        request.state.operador = request.headers.get("x-operador")
        return await call_next(request)

    app.include_router(learning_router)
    app.state.poc = SimpleNamespace(learning=mundo.servico, db=mundo.db)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


def _por_acao(corpo: dict[str, object]) -> set[tuple[str, str, str]]:
    efeitos = corpo["efeitos"]
    assert isinstance(efeitos, list)
    return {(str(e["acao"]), str(e["kind"]), str(e["ref"])) for e in efeitos}


# ------------------------------------------------------------------ domínio (puro)
def test_efeitos_do_voto_puro() -> None:
    usados = (Conhecimento(LivroKind.FLUXO, "f", S.PUBLISHED, Uso.USOU),
              Conhecimento(LivroKind.FLUXO, "ja-desligado", S.DISABLED, Uso.USOU),
              Conhecimento(LivroKind.RECEITA, "7", S.CANDIDATE, Uso.APRENDEU),
              Conhecimento(LivroKind.RECEITA, "8", S.DEPRECATED, Uso.USOU),
              Conhecimento(LivroKind.HABILIDADE, "ig.x@1", S.PUBLISHED, Uso.USOU),
              Conhecimento(LivroKind.LICAO, "li-1", S.PUBLISHED, Uso.EXPOSTA))
    ok = ItemVotado(desfecho=Desfecho.SUCESSO, simulado=False, etapas_comprovadas=2, etapas_a_mao=0,
                    conhecimentos=usados, perfil="p1", etapa="s1")
    plano = efeitos_do_voto(Veredito.ERRADO, MotivoDoVoto.ALVO_ERRADO, ok)
    assert plano.polaridade is Polaridade.NEGATIVE and plano.verificador is Verificador.FALSO_POSITIVO
    acoes = {(e.acao, e.kind, e.ref, e.para) for e in plano.efeitos}
    assert (AcaoDoEfeito.DESLIGAR, "fluxo", "f", S.DISABLED) in acoes
    assert (AcaoDoEfeito.DESLIGAR, "receita", "7", S.DISABLED) in acoes
    assert not any(e.ref in ("ja-desligado", "8") for e in plano.efeitos)       # sem transição para desligar
    assert (AcaoDoEfeito.EVIDENCIA_CONTRA, "habilidade", "ig.x@1", S.PUBLISHED) in acoes   # nunca desabilitada
    assert (AcaoDoEfeito.EVIDENCIA_CONTRA, "licao", "li-1", S.PUBLISHED) in acoes
    assert (AcaoDoEfeito.BACKLOG, "backlog", "verificacao_falso_positivo", None) in acoes
    # Confirmada à mão: rebaixa igual, mas o verificador não é culpado.
    a_mao = ItemVotado(desfecho=Desfecho.SUCESSO, simulado=False, etapas_comprovadas=1, etapas_a_mao=1,
                       conhecimentos=usados)
    assert efeitos_do_voto(Veredito.ERRADO, MotivoDoVoto.NAO_TERMINOU, a_mao).verificador is None
    # Simulado: nada.
    sim = ItemVotado(desfecho=Desfecho.SUCESSO, simulado=True, etapas_comprovadas=2, etapas_a_mao=0,
                     conhecimentos=usados)
    assert efeitos_do_voto(Veredito.ERRADO, MotivoDoVoto.ALVO_ERRADO, sim).efeitos == ()
    # 'certo' em falha: falso negativo e nada mais; 'certo' em sucesso: evidência a favor do que é do livro.
    falha = ItemVotado(desfecho=Desfecho.FALHA, simulado=False, etapas_comprovadas=0, etapas_a_mao=0,
                       conhecimentos=usados)
    fn = efeitos_do_voto(Veredito.CERTO, None, falha)
    assert fn.verificador is Verificador.FALSO_NEGATIVO and fn.polaridade is Polaridade.POSITIVE
    assert [(e.acao, e.ref) for e in fn.efeitos] == [(AcaoDoEfeito.BACKLOG, "verificacao_falso_negativo")]
    favor = efeitos_do_voto(Veredito.CERTO, None, ok)
    assert {e.acao for e in favor.efeitos} == {AcaoDoEfeito.EVIDENCIA_A_FAVOR}
    assert not any(e.ref in ("ja-desligado", "8") for e in favor.efeitos)       # só o que está vivo
    # Os motivos que não são de navegação não rebaixam nada.
    assert [(e.acao, e.ref) for e in efeitos_do_voto(Veredito.ERRADO, MotivoDoVoto.TEXTO_RUIM, ok).efeitos] == [
        (AcaoDoEfeito.VOZ, "p1")]
    assert [(e.acao, e.ref) for e in efeitos_do_voto(Veredito.ERRADO, MotivoDoVoto.PEDIU_AJUDA_A_TOA, ok).efeitos] == [
        (AcaoDoEfeito.TELA_E_LICAO, "s1")]
    assert [e.acao for e in efeitos_do_voto(Veredito.ERRADO, MotivoDoVoto.DEMOROU_OU_GASTOU, ok).efeitos] == [
        AcaoDoEfeito.RELATORIO]
    # A borda do vocabulário.
    with pytest.raises(EntradaInvalida):
        efeitos_do_voto(Veredito.ERRADO, None, ok)
    with pytest.raises(EntradaInvalida):
        efeitos_do_voto(Veredito.CERTO, MotivoDoVoto.OUTRO, ok)
    assert desfecho("succeeded", da_execucao=False) is Desfecho.SUCESSO
    assert desfecho("uncertain", da_execucao=False) is Desfecho.FALHA
    assert desfecho("waiting_user", da_execucao=False) is Desfecho.OUTRO
    assert desfecho("completed", da_execucao=True) is Desfecho.SUCESSO
    assert desfecho("completed_with_issues", da_execucao=True) is Desfecho.FALHA
    assert not licao_refutada(1) and licao_refutada(2)


# ------------------------------------------------------------------ o voto de navegação (o caso principal)
async def test_errado_de_navegacao_rebaixa_o_usado_e_o_aprendido(mundo: Mundo, cliente: httpx.AsyncClient) -> None:
    r = await cliente.post(f"/api/runs/{RUN}/feedback",
                           json={"objective_id": mundo.ok, "verdict": "errado", "reason": "alvo_errado",
                                 "note": "abriu o perfil de outra pessoa"})
    assert r.status_code == 201, r.text
    corpo = r.json()
    sinal = corpo["signal"]
    assert (sinal["kind"], sinal["verdict"], sinal["reason"], sinal["polarity"]) == (
        "feedback", "errado", "alvo_errado", "negative")
    assert sinal["source_ref"] == f"objective:{mundo.ok}" and sinal["created_by"] == "panel"
    assert sinal["note"] == "abriu o perfil de outra pessoa" and sinal["simulated"] is False
    assert (sinal["instance_id"], sinal["profile_id"], sinal["app_package"]) == ("android-06", "p-lucas", PACOTE)
    # Etapas comprovadas pela tela: falso positivo do verificador, que o relatório do A3 lê do próprio sinal.
    assert sinal["step_verified"] is True and sinal["data"]["verificador"] == "falso_positivo"
    efeitos = _por_acao(corpo)
    assert {("desligar", "fluxo", "fluxo-usado"), ("desligar", "fluxo", "fluxo-aprendido"),
            ("desligar", "receita", str(mundo.receita_usada)), ("desligar", "receita", str(mundo.receita_aprendida)),
            ("evidencia_contra", "habilidade", "ig.abrir@1"), ("evidencia_contra", "licao", mundo.licao),
            ("backlog", "backlog", "verificacao_falso_positivo")} <= efeitos
    assert not any(ref in (mundo.licao_holdout, "fluxo-alheio", str(mundo.receita_alheia)) for _, _, ref in efeitos)
    desligou = next(e for e in corpo["efeitos"] if e["ref"] == "fluxo-usado")
    assert (desligou["de"], desligou["para"], desligou["aplicado"], desligou["uso"]) == (
        "published", "disabled", True, "usou")
    assert desligou["desfazer"]["href"] == "/api/aprendizado/fluxo/fluxo-usado/status"
    assert desligou["desfazer"]["body"]["to"] == "published"
    assert next(e for e in corpo["efeitos"] if e["ref"] == "fluxo-aprendido")["uso"] == "aprendeu"
    assert next(e for e in corpo["efeitos"] if e["kind"] == "backlog")["desfazer"] is None
    assert "fluxo aprendido desta execução fluxo-aprendido desligado — reativar" in corpo["resumo"]
    assert "falso positivo" in corpo["resumo"]
    # O banco: rebaixado o que o item usou e aprendeu; o resto intacto; a habilidade nunca é desabilitada.
    assert mundo.status("flows", "fluxo-usado") == "disabled" and mundo.status("flows", "fluxo-aprendido") == "disabled"
    assert mundo.status("recipes", mundo.receita_usada) == "quarantined"
    assert mundo.status("recipes", mundo.receita_aprendida) == "quarantined"
    assert mundo.status("flows", "fluxo-alheio") == "active" and mundo.status("recipes", mundo.receita_alheia) == "active"
    assert mundo.db.scalar("SELECT state FROM skill_versions WHERE id='ig.abrir@1'") == "published"
    assert mundo.status("objectives", mundo.ok) == "succeeded"
    # A trilha em nome de quem votou, com a execução; e a evidência contra ligada ao sinal.
    trilha = mundo.db.query("SELECT item_ref, from_state, to_state, decided_by, reason, run_id FROM learning_transitions"
                            " WHERE item_ref LIKE 'fluxo:%' OR item_ref LIKE 'receita:%' ORDER BY id")
    assert {t["item_ref"] for t in trilha} == {"fluxo:fluxo-usado", "fluxo:fluxo-aprendido",
                                               f"receita:{mundo.receita_usada}", f"receita:{mundo.receita_aprendida}"}
    assert all(t["decided_by"] == "panel" and t["run_id"] == RUN and "alvo_errado" in t["reason"] for t in trilha)
    origem = f"signal:{sinal['id']}"
    contra = {r["item_ref"] for r in mundo.db.query(
        "SELECT item_ref FROM learning_evidence WHERE stance='against' AND origin_ref=?", (origem,))}
    assert {"habilidade:ig.abrir@1", mundo.licao, "fluxo:fluxo-usado"} <= contra
    assert mundo.db.scalar("SELECT evidence_against FROM learning_items WHERE id=?", (mundo.licao,)) == 1
    assert mundo.db.scalar("SELECT state FROM learning_items WHERE id=?", (mundo.licao,)) == "published"  # 1 refutação
    # O GET devolve o voto do item.
    g = await cliente.get(f"/api/runs/{RUN}/feedback")
    assert g.status_code == 200, g.text
    assert [(v["objective_id"], v["verdict"]) for v in g.json()["votos"]] == [(mundo.ok, "errado")]


async def test_confirmada_a_mao_rebaixa_sem_culpar_o_verificador(mundo: Mundo, cliente: httpx.AsyncClient) -> None:
    r = await cliente.post("/api/runs/r-a-mao/feedback",
                           json={"objective_id": mundo.a_mao, "verdict": "errado", "reason": "nao_terminou"})
    assert r.status_code == 201, r.text
    assert r.json()["signal"]["step_verified"] is False and "verificador" not in r.json()["signal"]["data"]
    assert ("desligar", "fluxo", "fluxo-alheio") in _por_acao(r.json())
    assert not any(a == "backlog" for a, _, _ in _por_acao(r.json()))


# ------------------------------------------------------------------ a borda
async def test_a_borda_do_voto(mundo: Mundo, cliente: httpx.AsyncClient) -> None:
    rota = f"/api/runs/{RUN}/feedback"
    sem_motivo = await cliente.post(rota, json={"objective_id": mundo.ok, "verdict": "errado"})
    assert sem_motivo.status_code == 422 and sem_motivo.json()["detail"]["code"] == "invalid"
    fora = await cliente.post(rota, json={"objective_id": mundo.ok, "verdict": "errado", "reason": "nao_gostei"})
    assert fora.status_code == 422
    assert (await cliente.post(rota, json={"verdict": "talvez"})).status_code == 422
    assert (await cliente.post(rota, json={"verdict": "certo", "reason": "outro"})).status_code == 422
    assert (await cliente.post(rota, json={"verdict": "certo", "extra": 1})).status_code == 422
    assert (await cliente.post(rota, json={"verdict": "errado", "reason": "outro", "note": "x" * 501})).status_code == 422
    inexistente = await cliente.post("/api/runs/r-nao-existe/feedback", json={"verdict": "certo"})
    assert inexistente.status_code == 404 and inexistente.json()["detail"]["code"] == "not_found"
    de_outra = await cliente.post(rota, json={"objective_id": mundo.a_mao, "verdict": "certo"})
    assert de_outra.status_code == 404
    assert (await cliente.get("/api/runs/r-nao-existe/feedback")).status_code == 404
    assert mundo.sinais() == []                                    # nenhuma recusa gravou nada
    assert mundo.status("flows", "fluxo-usado") == "active"


async def test_nota_com_cara_de_credencial_e_recusada_sem_gravar(mundo: Mundo, cliente: httpx.AsyncClient) -> None:
    r = await cliente.post(f"/api/runs/{RUN}/feedback", json={"objective_id": mundo.ok, "verdict": "errado",
                                                               "reason": "alvo_errado", "note": NOTA_COM_SENHA})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "note_looks_secret"
    assert "Hunter2" not in r.text
    assert mundo.sinais() == []
    assert mundo.status("flows", "fluxo-usado") == "active" and mundo.status("recipes", mundo.receita_usada) == "active"
    assert mundo.db.scalar("SELECT COUNT(*) FROM learning_transitions WHERE item_ref LIKE 'fluxo:%'") == 0
    assert mundo.db.scalar("SELECT COUNT(*) FROM learning_evidence") == 0


# ------------------------------------------------------------------ um voto por pessoa e por item
async def test_upsert_por_pessoa_e_item_e_voto_da_execucao(mundo: Mundo, cliente: httpx.AsyncClient) -> None:
    rota = f"/api/runs/{RUN}/feedback"
    assert (await cliente.post(rota, json={"objective_id": mundo.ok, "verdict": "errado",
                                           "reason": "fez_outra_coisa"})).status_code == 201
    troca = await cliente.post(rota, json={"objective_id": mundo.ok, "verdict": "certo"})
    assert troca.status_code == 201, troca.text
    linhas = [s for s in mundo.sinais() if s["source_ref"] == f"objective:{mundo.ok}"]
    assert len(linhas) == 1 and linhas[0]["verdict"] == "certo" and linhas[0]["polarity"] == "positive"
    assert linhas[0]["reason"] is None and linhas[0]["updated_at"] is not None
    # Votar de novo troca o voto, mas NÃO reativa o que o primeiro voto desligou (reativar é gesto próprio).
    assert mundo.status("flows", "fluxo-usado") == "disabled"
    assert mundo.status("recipes", mundo.receita_usada) == "quarantined"
    # Outra pessoa tem o próprio voto; sem objective_id o voto é da execução.
    ana = await cliente.post(rota, json={"objective_id": mundo.ok, "verdict": "certo"}, headers={"x-operador": "ana"})
    assert ana.status_code == 201 and ana.json()["signal"]["created_by"] == "ana"
    execucao = await cliente.post(rota, json={"verdict": "errado", "reason": "demorou_ou_gastou"})
    assert execucao.status_code == 201, execucao.text
    assert execucao.json()["signal"]["source_ref"] == f"run:{RUN}" and execucao.json()["signal"]["objective_id"] is None
    votos = (await cliente.get(rota)).json()["votos"]
    assert sorted((v["source_ref"], v["created_by"], v["verdict"]) for v in votos) == sorted([
        (f"objective:{mundo.ok}", "panel", "certo"), (f"objective:{mundo.ok}", "ana", "certo"),
        (f"run:{RUN}", "panel", "errado")])


async def test_created_by_pela_regra_quem(mundo: Mundo, cliente: httpx.AsyncClient) -> None:
    rota = f"/api/runs/{RUN}/feedback"
    sistema = await cliente.post(rota, json={"objective_id": mundo.ok, "verdict": "errado", "reason": "alvo_errado"},
                                 headers={"x-operador": SYSTEM_ACTOR})
    assert sistema.status_code == 201 and sistema.json()["signal"]["created_by"] == "painel:sistema"
    # A trilha também: pela rota decide sempre uma pessoa, nunca o ator de sistema.
    assert {r["decided_by"] for r in mundo.db.query(
        "SELECT decided_by FROM learning_transitions WHERE item_ref LIKE 'fluxo:%'")} == {"painel:sistema"}
    vazio = await cliente.post(rota, json={"verdict": "certo"}, headers={"x-operador": "  "})
    assert vazio.status_code == 201 and vazio.json()["signal"]["created_by"] == "panel"


# ------------------------------------------------------------------ os outros veredictos e motivos
async def test_certo_em_item_que_falhou_e_falso_negativo_sem_mudar_o_status(
        mundo: Mundo, cliente: httpx.AsyncClient) -> None:
    r = await cliente.post(f"/api/runs/{RUN}/feedback", json={"objective_id": mundo.falhou, "verdict": "certo"})
    assert r.status_code == 201, r.text
    sinal = r.json()["signal"]
    assert sinal["polarity"] == "positive" and sinal["data"]["verificador"] == "falso_negativo"
    assert sinal["failure_kind"] == "pos_condicao_nao_comprovada" and sinal["step_id"] == mundo.etapa_falha
    assert _por_acao(r.json()) == {("backlog", "backlog", "verificacao_falso_negativo")}
    assert mundo.status("objectives", mundo.falhou) == "failed"             # para isso existe confirm_done
    assert mundo.status("steps", mundo.etapa_falha) == "failed"
    assert mundo.db.scalar("SELECT COUNT(*) FROM learning_evidence") == 0


async def test_certo_em_item_concluido_da_evidencia_a_favor(mundo: Mundo, cliente: httpx.AsyncClient) -> None:
    r = await cliente.post(f"/api/runs/{RUN}/feedback", json={"objective_id": mundo.ok, "verdict": "certo"})
    assert r.status_code == 201, r.text
    assert {("evidencia_a_favor", "fluxo", "fluxo-usado"), ("evidencia_a_favor", "fluxo", "fluxo-aprendido"),
            ("evidencia_a_favor", "receita", str(mundo.receita_usada)),
            ("evidencia_a_favor", "licao", mundo.licao)} <= _por_acao(r.json())
    assert mundo.db.scalar("SELECT evidence_for FROM learning_items WHERE id=?", (mundo.licao,)) == 1
    assert mundo.db.scalar("SELECT evidence_for FROM learning_items WHERE id=?", (mundo.licao_holdout,)) == 0
    assert mundo.status("flows", "fluxo-usado") == "active" and "verificador" not in r.json()["signal"]["data"]


async def test_texto_ruim_so_alimenta_a_voz(mundo: Mundo, cliente: httpx.AsyncClient) -> None:
    r = await cliente.post(f"/api/runs/{RUN}/feedback",
                           json={"objective_id": mundo.ok, "verdict": "errado", "reason": "texto_ruim"})
    assert r.status_code == 201, r.text
    assert _por_acao(r.json()) == {("voz", "voz", "p-lucas")}
    assert r.json()["signal"]["reason"] == "texto_ruim" and r.json()["signal"]["profile_id"] == "p-lucas"
    assert mundo.status("flows", "fluxo-usado") == "active" and mundo.status("recipes", mundo.receita_usada) == "active"
    assert mundo.db.scalar("SELECT COUNT(*) FROM learning_evidence") == 0
    assert mundo.db.scalar("SELECT COUNT(*) FROM learning_transitions WHERE item_ref NOT LIKE 'li-%'") == 0


async def test_demorou_so_relatorio_e_ajuda_a_toa_so_tela_e_licao(mundo: Mundo, cliente: httpx.AsyncClient) -> None:
    rota = f"/api/runs/{RUN}/feedback"
    demorou = await cliente.post(rota, json={"objective_id": mundo.ok, "verdict": "errado",
                                             "reason": "demorou_ou_gastou"})
    assert demorou.status_code == 201 and _por_acao(demorou.json()) == {("relatorio", "relatorio", "demorou_ou_gastou")}
    ajuda = await cliente.post(rota, json={"objective_id": mundo.falhou, "verdict": "errado",
                                           "reason": "pediu_ajuda_a_toa"})
    assert ajuda.status_code == 201 and _por_acao(ajuda.json()) == {("tela_e_licao", "tela", mundo.etapa_falha)}
    assert mundo.status("flows", "fluxo-usado") == "active" and mundo.status("recipes", mundo.receita_usada) == "active"


async def test_execucao_simulada_nao_rebaixa_nada(mundo: Mundo, cliente: httpx.AsyncClient) -> None:
    r = await cliente.post(f"/api/runs/{SIM}/feedback",
                           json={"objective_id": mundo.simulado, "verdict": "errado", "reason": "alvo_errado"})
    assert r.status_code == 201, r.text
    assert r.json()["signal"]["simulated"] is True and r.json()["efeitos"] == []
    assert "simulada" in r.json()["resumo"]
    assert mundo.status("flows", "fluxo-simulado") == "active"
    assert mundo.status("recipes", mundo.receita_simulada) == "active"
    assert mundo.db.scalar("SELECT COUNT(*) FROM learning_evidence") == 0


# ------------------------------------------------------------------ reativar e as duas refutações
async def test_reativacao_em_um_clique_com_a_trilha_de_volta(mundo: Mundo, cliente: httpx.AsyncClient) -> None:
    voto = await cliente.post(f"/api/runs/{RUN}/feedback",
                              json={"objective_id": mundo.ok, "verdict": "errado", "reason": "alvo_errado"})
    desfazer = next(e for e in voto.json()["efeitos"] if e["ref"] == "fluxo-usado")["desfazer"]
    volta = await cliente.post(desfazer["href"], json=desfazer["body"])
    assert volta.status_code == 200, volta.text
    assert mundo.status("flows", "fluxo-usado") == "active"
    assert [(t["from"], t["to"], t["decided_by"]) for t in volta.json()["trilha"]] == [
        ("published", "disabled", "panel"), ("disabled", "published", "panel")]
    receita = next(e for e in voto.json()["efeitos"] if e["ref"] == str(mundo.receita_usada))["desfazer"]
    assert receita["href"] == f"/api/aprendizado/receita/{mundo.receita_usada}/status"
    assert (await cliente.post(receita["href"], json=receita["body"])).status_code == 200
    assert mundo.status("recipes", mundo.receita_usada) == "active"


async def test_duas_refutacoes_desligam_a_licao(mundo: Mundo, cliente: httpx.AsyncClient) -> None:
    rota = f"/api/runs/{RUN}/feedback"
    corpo = {"objective_id": mundo.ok, "verdict": "errado", "reason": "fez_outra_coisa"}
    assert (await cliente.post(rota, json=corpo)).status_code == 201
    # O mesmo voto de novo (a mesma pessoa) não conta duas vezes.
    assert (await cliente.post(rota, json=corpo)).status_code == 201
    assert mundo.db.scalar("SELECT state FROM learning_items WHERE id=?", (mundo.licao,)) == "published"
    segunda = await cliente.post(rota, json=corpo, headers={"x-operador": "ana"})
    assert segunda.status_code == 201, segunda.text
    assert ("desligar", "licao", mundo.licao) in _por_acao(segunda.json())
    assert mundo.db.scalar("SELECT state FROM learning_items WHERE id=?", (mundo.licao,)) == "disabled"
    assert mundo.db.scalar("SELECT decided_by FROM learning_transitions WHERE item_ref=? ORDER BY id DESC LIMIT 1",
                           (mundo.licao,)) == SYSTEM_ACTOR
    assert mundo.db.scalar("SELECT state FROM learning_items WHERE id=?", (mundo.licao_holdout,)) == "published"


# ------------------------------------------------------------------ a aba Sinais
async def test_sinais_por_dias_tipo_e_app(mundo: Mundo, cliente: httpx.AsyncClient) -> None:
    assert (await cliente.post(f"/api/runs/{RUN}/feedback", json={"objective_id": mundo.ok, "verdict": "certo"})
            ).status_code == 201
    mundo.servico.registrar_sinal(NovoSinal(kind=SignalKind.TOMOU_CONTROLE, source_ref="takeover:x:a1",
                                            created_by=SYSTEM_ACTOR, polarity=Polaridade.NEGATIVE, run_id=RUN,
                                            app_package=PACOTE, capability="abrir_perfil"))
    mundo.servico.registrar_sinal(NovoSinal(kind=SignalKind.REPETIU_ITEM, source_ref="retry:y", created_by="panel",
                                            app_package="pkg.outro"))
    todos = await cliente.get("/api/aprendizado/sinais")
    assert todos.status_code == 200, todos.text
    assert todos.json()["total"] == 3 and todos.json()["contagem"] == {"feedback": 1, "tomou_controle": 1,
                                                                      "repetiu_item": 1}
    so_controle = (await cliente.get("/api/aprendizado/sinais?dias=7&kind=tomou_controle")).json()
    assert [s["source_ref"] for s in so_controle["sinais"]] == ["takeover:x:a1"]
    do_app = (await cliente.get(f"/api/aprendizado/sinais?app={PACOTE}")).json()
    assert {s["kind"] for s in do_app["sinais"]} == {"feedback", "tomou_controle"}
    assert (await cliente.get("/api/aprendizado/sinais?kind=inventado")).status_code == 422
    assert (await cliente.get("/api/aprendizado/sinais?dias=0")).status_code == 422
    # O GET da execução separa os votos dos sinais implícitos dela.
    da_execucao = (await cliente.get(f"/api/runs/{RUN}/feedback")).json()
    assert [v["kind"] for v in da_execucao["votos"]] == ["feedback"]
    assert [s["kind"] for s in da_execucao["sinais"]] == ["tomou_controle"]


async def test_sem_o_aprendizado_composto_o_voto_diz_503() -> None:
    app = FastAPI()
    app.include_router(learning_router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/runs/r1/feedback", json={"verdict": "certo"})
        assert r.status_code == 503 and r.json()["detail"]["code"] == "not_ready"


# ------------------------------------------------------------------ o app inteiro (a ordem dos roteadores)
async def test_o_voto_pelo_app_inteiro_nao_cai_na_operacao_da_execucao(harness: Harness) -> None:
    """`api.py` tem `POST /api/runs/{run_id}/{op}`; se o roteador do aprendizado viesse depois, o voto viraria
    "Operação desconhecida" (404). Só o `create_app` inteiro prova a ordem."""
    from app.main import create_app

    state = harness.state
    assert state is not None
    run = harness.run(["android-01"])
    await harness.wait_run(run.id)
    app = create_app(harness.cfg, state=state)
    app.state.poc = state
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post(f"/api/runs/{run.id}/feedback", json={"verdict": "certo"})
        assert r.status_code == 201, r.text
        item = await c.post(f"/api/runs/{run.id}/feedback", json={"objective_id": f"{run.id}:android-01",
                                                                   "verdict": "errado", "reason": "demorou_ou_gastou"})
        assert item.status_code == 201, item.text
        votos = (await c.get(f"/api/runs/{run.id}/feedback")).json()["votos"]
        assert {v["source_ref"] for v in votos} == {f"run:{run.id}", f"objective:{run.id}:android-01"}
        # As operações de sempre continuam no lugar.
        assert (await c.post(f"/api/runs/{run.id}/inventada")).status_code == 404
