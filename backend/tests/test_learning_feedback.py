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
- a reativação em um clique pelo `POST /api/aprendizado/{kind}/{ref}/status`, com a trilha de volta — só para o que
  ESTAVA publicado: do `candidate`/`validated` o `desfazer` vem nulo, porque a única volta da tabela do D1
  (`→ published`) promoveria o que nunca foi publicado nem aprovado, pulando a fila "Para aprovar";
- o bloco `aprendizado` do GET ("Aprendizado desta execução" no relatório), no formato que o painel lê
  (`model.ts::lerAprendizado`): as cinco listas sempre presentes; uma linha por item em cada grupo; a lição que nasceu
  da execução só como candidata, e a tela que já existia e mudou de estado nela também; a preferência nascida com a
  evidência desta execução como candidata "entre N execuções", nunca como causa única; a falha legada classificada na
  leitura sem gravar e sem o texto do erro; o título com cara de credencial fora e o comprido cortado em `TITULO_MAX`;
  a habilidade e o que é de outra execução fora (a evidência de outra execução contra um item que esta também toca
  não entra no papel dela); a transição de uma pessoa (o desligamento pelo voto) dita como dela, nunca "nesta
  execução"; o item apagado depois da execução sai com o ref e sem título, e o bloco não vira `null`.

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
from app.modules.learning.application.aprendido import TITULO_MAX
from app.modules.learning.application.ports import Ajustes, NovaEvidencia, NovoSinal
from app.modules.learning.application.servico import LearningService
from app.modules.learning.domain.aprendido import (CHAVE_DO_GRUPO, EvidenciaDaExecucao, ExposicaoDaExecucao,
                                                   FatosDaExecucao, Grupo, TentativaDaExecucao, TransicaoDaExecucao,
                                                   aprendido_na_execucao)
from app.modules.learning.domain.ciclo import SYSTEM_ACTOR, EntradaInvalida, SkillState
from app.modules.learning.domain.livro import Escopo, NovoItem
from app.modules.learning.domain.vocabulario import (Braco, LivroKind, MotivoDoVoto, Polaridade, Posicao, SignalKind,
                                                     SourceKind, Veredito)
from app.modules.learning.domain.voto import (AcaoDoEfeito, Conhecimento, Desfecho, ItemVotado, Uso, Verificador,
                                              desfecho, efeitos_do_voto, licao_refutada, motivo_do_desligamento,
                                              reativar_desfaz)
from app.modules.learning.infrastructure.aprendido_sql import LeituraDoAprendidoSql
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


def _receita(db: Database, passo: str, *, status: str, aprendida_em: str, commit: bool = False) -> int:
    acoes = [{"tool": "tap", "args": {}, "selectors": [{"rid": f"botao-{passo}"}], "commit": commit}]
    return int(db.inserted_id(
        "INSERT INTO recipes(app_package, app_version, app_signature, variant, step_hash, step_key, version, status,"
        " actions, learned_from_step, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (PACOTE, "447", "sig", "pt/420", f"h-{passo}", passo, 1, status, json.dumps(acoes), aprendida_em, TS)))


def _fluxo(db: Database, fid: str, *, status: str, origem: str | None, efeito: bool = False) -> None:
    plano = {"summary": fid, "app_id": "instagram", "parameters": {},
             "steps": [{"key": "a", "title": "a", "goal": "a", "side_effect": efeito,
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


def test_reativar_so_desfaz_o_que_estava_publicado() -> None:
    """`disabled → published` é a única volta da tabela do D1: desfaz só o desligamento do publicado. Do candidato ou
    do validado, a mesma chamada PROMOVERIA o que nunca foi publicado nem aprovado."""
    assert [s for s in SkillState if reativar_desfaz(s)] == [S.PUBLISHED]
    assert reativar_desfaz(None) is False


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
    # O aprendido desta execução e a receita aprendida eram CANDIDATOS: desligados, mas sem desfazer — a única volta
    # (→ published) publicaria o que nunca foi publicado nem aprovado.
    aprendido = next(e for e in corpo["efeitos"] if e["ref"] == "fluxo-aprendido")
    assert (aprendido["uso"], aprendido["de"], aprendido["para"], aprendido["aplicado"], aprendido["desfazer"]) == (
        "aprendeu", "candidate", "disabled", True, None)
    receita = next(e for e in corpo["efeitos"] if e["ref"] == str(mundo.receita_aprendida))
    assert (receita["de"], receita["para"], receita["desfazer"]) == ("candidate", "disabled", None)
    assert next(e for e in corpo["efeitos"] if e["kind"] == "backlog")["desfazer"] is None
    assert "fluxo usado fluxo-usado desligado — reativar" in corpo["resumo"]
    assert "fluxo-aprendido desligado — reativar" not in corpo["resumo"]
    assert "fluxo aprendido desta execução fluxo-aprendido desligado (não estava publicado" in corpo["resumo"]
    assert f"{mundo.receita_aprendida} em quarentena — reativar" not in corpo["resumo"]
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


async def test_desligar_o_que_nunca_foi_publicado_nao_oferece_desfazer(mundo: Mundo,
                                                                      cliente: httpx.AsyncClient) -> None:
    """O caso do revisor: fluxo CANDIDATO com etapa de efeito, aprendido desta execução, e receita VALIDADA com
    commit — o que o D1 manda esperar o dono em "Para aprovar". O voto os desliga (rebaixar é automático), mas o
    `desfazer` vem nulo: `→ published` não devolveria o estado de antes, publicaria. A lição candidata que o sistema
    desliga por refutação segue a mesma regra. O publicado usado continua com o seu desfazer."""
    db, run = mundo.db, "r-com-efeito"
    _fluxo(db, "fluxo-publicado", status="active", origem="r-antiga")
    _fluxo(db, "fluxo-com-efeito", status="candidate", origem=run, efeito=True)
    _execucao(db, run, flow_id="fluxo-publicado")
    oid = _objetivo(db, run, "android-06", "succeeded")
    etapa = _etapa(db, oid, "enviar", status="succeeded", verificada=True, acao="enviar_mensagem")
    receita = _receita(db, "enviar", status="validated", aprendida_em=etapa, commit=True)
    licao = mundo.servico.propor(NovoItem(
        kind=LivroKind.LICAO, escopo=Escopo(app=PACOTE, capability="enviar_mensagem", role="actor"),
        content={"texto": "confira o destinatário"}, summary="confira o destinatário",
        source_kind=SourceKind.RECOVERY, side_effect=False)).id
    db.execute("INSERT INTO learning_exposures(item_id, unit_id, role, arm, tokens, run_id, objective_id,"
               " app_package, capability, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
               (licao, f"step:{etapa}", "actor", "with", 9, run, oid, PACOTE, "enviar_mensagem", TS))
    rota, corpo = f"/api/runs/{run}/feedback", {"objective_id": oid, "verdict": "errado", "reason": "alvo_errado"}

    r = await cliente.post(rota, json=corpo)
    assert r.status_code == 201, r.text
    desligados = {e["ref"]: e for e in r.json()["efeitos"] if e["acao"] == "desligar"}
    assert set(desligados) == {"fluxo-publicado", "fluxo-com-efeito", str(receita)}
    fluxo, rec = desligados["fluxo-com-efeito"], desligados[str(receita)]
    assert (fluxo["de"], fluxo["para"], fluxo["aplicado"], fluxo["desfazer"]) == ("candidate", "disabled", True, None)
    assert (rec["de"], rec["para"], rec["aplicado"], rec["desfazer"]) == ("validated", "disabled", True, None)
    assert desligados["fluxo-publicado"]["desfazer"]["body"]["to"] == "published"
    # O invariante do contrato: todo desfazer oferecido devolve ao publicado de onde o item saiu — nunca promove.
    assert all(e["de"] == "published" for e in r.json()["efeitos"] if e["desfazer"] is not None)
    resumo = r.json()["resumo"]
    assert "fluxo usado fluxo-publicado desligado — reativar" in resumo
    assert "fluxo aprendido desta execução fluxo-com-efeito desligado (não estava publicado" in resumo
    assert f"receita aprendida desta execução {receita} em quarentena (não estava publicada" in resumo
    assert "fluxo-com-efeito desligado — reativar" not in resumo and f"{receita} em quarentena — reativar" not in resumo
    # Nada foi publicado: o fluxo e a receita ficam fora de circulação até uma pessoa decidir publicá-los.
    assert mundo.status("flows", "fluxo-com-efeito") == "disabled" and mundo.status("recipes", receita) == "quarantined"

    # A segunda refutação (outra pessoa) faz o SISTEMA desligar a lição candidata: também sem desfazer.
    segunda = await cliente.post(rota, json=corpo, headers={"x-operador": "ana"})
    assert segunda.status_code == 201, segunda.text
    desligou = next(e for e in segunda.json()["efeitos"] if e["acao"] == "desligar" and e["ref"] == licao)
    assert (desligou["de"], desligou["para"], desligou["aplicado"], desligou["desfazer"]) == (
        "candidate", "disabled", True, None)
    assert f"lição exposta {licao} desligada (não estava publicada" in segunda.json()["resumo"]
    assert mundo.db.scalar("SELECT state FROM learning_items WHERE id=?", (licao,)) == "disabled"
    assert all(e["de"] == "published" for e in segunda.json()["efeitos"] if e["desfazer"] is not None)


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
    # A lição estava publicada: o desligamento do sistema tem desfazer (a pessoa a traz de volta).
    desligou = next(e for e in segunda.json()["efeitos"] if e["acao"] == "desligar" and e["ref"] == mundo.licao)
    assert desligou["de"] == "published" and desligou["desfazer"]["body"]["to"] == "published"
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


# ------------------------------------------------------------------ "Aprendizado desta execução" (o bloco do GET)
def _linhas(corpo: dict[str, object], chave: str) -> list[dict[str, object]]:
    bloco = corpo["aprendizado"]
    assert isinstance(bloco, dict)
    linhas = bloco[chave]
    assert isinstance(linhas, list)
    return linhas


def _por_ref(corpo: dict[str, object], chave: str) -> dict[str, dict[str, object]]:
    linhas = _linhas(corpo, chave)
    refs = [str(x["ref"]) for x in linhas]
    assert len(refs) == len(set(refs)), f"ref repetido em {chave}: {refs}"      # a chave do painel é grupo-ref
    return {str(x["ref"]): x for x in linhas}


def test_aprendido_na_execucao_puro() -> None:
    """Uma linha por item em cada grupo, cada item num grupo só, e o papel em texto fechado."""
    do_voto = motivo_do_desligamento(MotivoDoVoto.ALVO_ERRADO, "objective:r-x:android-06")
    fatos = FatosDaExecucao(
        fluxo_usado="f-usado", fluxos_aprendidos=("f-novo",), receitas_usadas=("7", "8"), receitas_aprendidas=("7",),
        transicoes=(TransicaoDaExecucao("fluxo:f-usado", "fluxo", "published", "disabled"),
                    TransicaoDaExecucao("receita:8", "receita", "published", "disabled", por="dono", motivo=do_voto),
                    TransicaoDaExecucao("li-nova", "licao", None, "candidate"),
                    TransicaoDaExecucao("li-nova", "licao", "candidate", "validated"),
                    TransicaoDaExecucao("li-tela", "tela", None, "candidate"),
                    TransicaoDaExecucao("li-tela-velha", "tela", "published", "disabled"),
                    TransicaoDaExecucao("fluxo:f-novo", "fluxo", "candidate", "validated"),
                    TransicaoDaExecucao("fluxo:f-novo", "fluxo", "validated", "published"),
                    TransicaoDaExecucao("fluxo:f-novo", "fluxo", "published", "disabled", por="dono",
                                        motivo="desligado na lista de fluxos do painel")),
        evidencias=(EvidenciaDaExecucao("li-nova", "licao", "for", 1),          # nasceu aqui: fica candidata
                    EvidenciaDaExecucao("li-velha", "licao", "against", 2),
                    EvidenciaDaExecucao("li-voz", "voz", "for", 1),            # só reforçada: sem grupo no painel
                    EvidenciaDaExecucao("li-tela-velha", "tela", "conflict", 1),
                    EvidenciaDaExecucao("habilidade:ig.x@1", None, "against", 1),
                    EvidenciaDaExecucao("fluxo:f-usado", None, "against", 1)),
        exposicoes=(ExposicaoDaExecucao("li-velha", "actor", "with"), ExposicaoDaExecucao("li-velha", "planner", "with"),
                    ExposicaoDaExecucao("li-controle", "actor", "holdout")),
        tentativas=(TentativaDaExecucao("alvo_ausente", "failed", None),
                    TentativaDaExecucao(None, "failed", "Pós-condição não comprovada: o perfil não apareceu"),
                    TentativaDaExecucao("alvo_ausente", "failed", None),
                    TentativaDaExecucao(None, "succeeded", None)))
    itens = aprendido_na_execucao(fatos)
    com_ref = [i for i in itens if i.grupo is not Grupo.FALHA]
    por = {(i.grupo, i.ref): i for i in com_ref}
    assert len(por) == len(com_ref)
    # A ordem dos grupos é a do painel.
    ordem = [i.grupo for i in itens]
    assert ordem == sorted(ordem, key=list(CHAVE_DO_GRUPO).index)
    assert por[(Grupo.FLUXO, "f-usado")].papel == "usado, desligado nesta execução, evidência contra"
    # Só a transição do SISTEMA é da execução: a de uma pessoa diz que foi ela (pelo voto, ou fora dele).
    assert por[(Grupo.FLUXO, "f-novo")].papel == ("aprendido nesta execução, validado e publicado nesta execução, "
                                                   "desligado por uma pessoa")
    assert por[(Grupo.RECEITA, "7")].papel == "usada, aprendida nesta execução"
    assert por[(Grupo.RECEITA, "8")].papel == "usada, posta em quarentena pelo voto de uma pessoa"
    assert not any("dono" in (i.papel or "") or "painel" in (i.papel or "") for i in itens)
    # A lição que nasceu aqui é candidata (com a evidência e a validação dela), e não "lição exposta".
    nova = por[(Grupo.CANDIDATA, "li-nova")]
    assert (nova.kind, nova.papel) == (LivroKind.LICAO, "lição, validada nesta execução, evidência a favor")
    assert (Grupo.LICAO, "li-nova") not in por
    assert por[(Grupo.CANDIDATA, "li-tela")].papel == "tela"
    # A tela que já existia e foi desligada por causa desta execução também é do grupo, e o papel diz o que mudou.
    velha_tela = por[(Grupo.CANDIDATA, "li-tela-velha")]
    assert (velha_tela.kind, velha_tela.papel) == (
        LivroKind.TELA, "tela que já existia, desligada nesta execução, evidência em conflito")
    velha = por[(Grupo.LICAO, "li-velha")]
    assert velha.papel == "exposta ao prompt (ator), exposta ao prompt (planejador), 2 evidências contra"
    assert velha.braco is Braco.WITH
    assert por[(Grupo.LICAO, "li-controle")].papel == "braço de controle (ator)"
    # Sem grupo no painel: a versão de habilidade e a voz só reforçada.
    assert not any(i.ref in ("li-voz", "ig.x@1") for i in itens)
    # A falha: o tipo gravado e o legado classificado na leitura, da mais frequente à menos; sucesso não conta.
    falhas = [(i.failure_kind, i.n, i.papel) for i in itens if i.grupo is Grupo.FALHA]
    assert falhas == [("alvo_ausente", 2, None),
                      ("pos_condicao_nao_comprovada", 1, "classificada na leitura (retroativo)")]
    assert aprendido_na_execucao(FatosDaExecucao()) == ()


def test_preferencia_nascida_da_execucao_nao_e_vendida_como_causa_unica() -> None:
    """A preferência nasce na curadoria, da evidência de várias execuções: a que fechou o limiar a vê como candidata,
    "entre N execuções" (ou "e de outras", sem o número); a que já existia e foi desligada diz que já existia."""
    fatos = FatosDaExecucao(
        transicoes=(TransicaoDaExecucao("li-pref", "preferencia", None, "candidate", entre=3),
                    TransicaoDaExecucao("li-pref", "preferencia", "candidate", "validated"),
                    TransicaoDaExecucao("li-pref", "preferencia", "validated", "published"),
                    TransicaoDaExecucao("li-sem-n", "preferencia", None, "candidate"),
                    TransicaoDaExecucao("li-velha", "preferencia", "published", "disabled")),
        evidencias=(EvidenciaDaExecucao("li-pref", "preferencia", "for", 1),
                    EvidenciaDaExecucao("li-velha", "preferencia", "against", 1)))
    por = {i.ref: i for i in aprendido_na_execucao(fatos)}
    assert {i.grupo for i in por.values()} == {Grupo.CANDIDATA}
    assert (por["li-pref"].kind, por["li-pref"].papel) == (
        LivroKind.PREFERENCIA, "preferência que nasceu com a evidência desta execução, entre 3 execuções, validada e "
                               "publicada nesta execução, evidência a favor")
    assert por["li-sem-n"].papel == "preferência que nasceu com a evidência desta execução e de outras"
    assert por["li-velha"].papel == "preferência que já existia, desligada nesta execução, evidência contra"
    # As outras candidatas que nascem da execução não mudam: a lição nasce de UMA execução.
    licao = aprendido_na_execucao(FatosDaExecucao(transicoes=(
        TransicaoDaExecucao("li-nova", "licao", None, "candidate", entre=3),)))
    assert [i.papel for i in licao] == ["lição"]


async def test_bloco_do_aprendizado_antes_e_depois_do_voto(mundo: Mundo, cliente: httpx.AsyncClient) -> None:
    # Evidência de OUTRA execução contra itens que esta também toca (a lição exposta e o fluxo usado): não é desta.
    for ref in (mundo.licao, "fluxo:fluxo-usado"):
        assert mundo.repo.registrar_evidencia(NovaEvidencia(item_ref=ref, stance=Posicao.AGAINST,
                                                            origin_ref="attempt:r-a-mao:a1", simulated=False,
                                                            run_id="r-a-mao", detail="outra execução"))
    antes = await cliente.get(f"/api/runs/{RUN}/feedback")
    assert antes.status_code == 200, antes.text
    corpo = antes.json()
    assert set(corpo["aprendizado"]) == {"receitas", "fluxos", "falhas", "candidatas", "licoes"}
    fluxos = _por_ref(corpo, "fluxos")
    assert set(fluxos) == {"fluxo-usado", "fluxo-aprendido"}                     # o alheio fica de fora
    usado = fluxos["fluxo-usado"]
    assert (usado["kind"], usado["titulo"], usado["estado"], usado["papel"]) == (
        "fluxo", "cmd fluxo-usado", "published", "usado")
    assert (fluxos["fluxo-aprendido"]["estado"], fluxos["fluxo-aprendido"]["papel"]) == (
        "candidate", "aprendido nesta execução")
    receitas = _por_ref(corpo, "receitas")
    assert set(receitas) == {str(mundo.receita_usada), str(mundo.receita_aprendida)}
    assert (receitas[str(mundo.receita_usada)]["titulo"], receitas[str(mundo.receita_usada)]["papel"]) == (
        "abrir (v1)", "usada")
    assert receitas[str(mundo.receita_aprendida)]["papel"] == "aprendida nesta execução"
    # A falha do android-07 não tinha tipo gravado (legado): classificada na leitura, sem gravar, e sem o texto do erro.
    [falha] = _linhas(corpo, "falhas")
    assert (falha["failure_kind"], falha["n"], falha["ref"], falha["titulo"]) == (
        "pos_condicao_nao_comprovada", 1, None, None)
    assert "retroativo" in str(falha["papel"]) and "perfil não apareceu" not in json.dumps(corpo["aprendizado"])
    assert mundo.db.scalar("SELECT failure_kind FROM attempts WHERE step_id=?", (mundo.etapa_falha,)) is None
    licoes = _por_ref(corpo, "licoes")
    assert (licoes[mundo.licao]["papel"], licoes[mundo.licao]["braco"], licoes[mundo.licao]["titulo"],
            licoes[mundo.licao]["estado"]) == ("exposta ao prompt (ator)", "with", "abra pelo atalho do perfil",
                                               "published")
    assert (licoes[mundo.licao_holdout]["papel"], licoes[mundo.licao_holdout]["braco"]) == (
        "braço de controle (ator)", "holdout")
    assert _linhas(corpo, "candidatas") == []

    voto = await cliente.post(f"/api/runs/{RUN}/feedback",
                              json={"objective_id": mundo.ok, "verdict": "errado", "reason": "alvo_errado"})
    assert voto.status_code == 201, voto.text
    depois = (await cliente.get(f"/api/runs/{RUN}/feedback")).json()
    # O voto é uma decisão de PESSOA, posterior à execução: a trilha a grava em nome de quem votou, e o papel diz que
    # foi o voto de uma pessoa — nunca "desligado nesta execução".
    assert {str(r["decided_by"]) for r in mundo.db.query(
        "SELECT decided_by FROM learning_transitions WHERE run_id=? AND to_state='disabled'", (RUN,))} == {"panel"}
    fluxos = _por_ref(depois, "fluxos")
    assert fluxos["fluxo-usado"]["estado"] == "disabled"
    assert fluxos["fluxo-usado"]["papel"] == "usado, desligado pelo voto de uma pessoa, evidência contra"
    assert fluxos["fluxo-aprendido"]["papel"] == ("aprendido nesta execução, desligado pelo voto de uma pessoa, "
                                                  "evidência contra")
    receita = _por_ref(depois, "receitas")[str(mundo.receita_usada)]
    assert (receita["estado"], receita["papel"]) == ("disabled", "usada, posta em quarentena pelo voto de uma pessoa, "
                                                                 "evidência contra")
    assert "nesta execução" not in str(receita["papel"])
    assert _por_ref(depois, "licoes")[mundo.licao]["papel"] == "exposta ao prompt (ator), evidência contra"
    # A versão de habilidade recebeu evidência contra, mas o painel não tem grupo para ela.
    tudo = json.dumps(depois["aprendizado"])
    assert "ig.abrir" not in tudo and "fluxo-alheio" not in tudo


async def test_candidata_nascida_da_execucao_nao_repete_em_licoes(mundo: Mundo, cliente: httpx.AsyncClient) -> None:
    """O minerador de lições faz `propor(run_id=)` e logo a evidência da mesma execução: a lição é candidata dela."""
    item = mundo.servico.propor(NovoItem(kind=LivroKind.LICAO, escopo=Escopo(app=PACOTE, capability="abrir_perfil",
                                                                             role="actor"),
                                         content={"texto": "espere o perfil carregar"},
                                         summary="espere o perfil carregar", source_kind=SourceKind.RECOVERY,
                                         side_effect=False), run_id=RUN)
    mundo.repo.registrar_evidencia(NovaEvidencia(item_ref=item.id, stance=Posicao.FOR, origin_ref="step:s-1",
                                                 simulated=False, run_id=RUN, detail="recuperou"))
    corpo = (await cliente.get(f"/api/runs/{RUN}/feedback")).json()
    candidata = _por_ref(corpo, "candidatas")[item.id]
    assert (candidata["kind"], candidata["estado"], candidata["titulo"], candidata["papel"]) == (
        "licao", "candidate", "espere o perfil carregar", "lição, evidência a favor")
    assert item.id not in _por_ref(corpo, "licoes")


async def test_titulo_com_cara_de_credencial_nao_sai_no_bloco(mundo: Mundo, cliente: httpx.AsyncClient) -> None:
    """O título do fluxo é o comando que a pessoa digitou: com cara de credencial, a linha sai só com o ref."""
    mundo.db.execute("UPDATE flows SET command_template=? WHERE id='fluxo-usado'", (NOTA_COM_SENHA,))
    corpo = (await cliente.get(f"/api/runs/{RUN}/feedback")).json()
    usado = _por_ref(corpo, "fluxos")["fluxo-usado"]
    assert usado["titulo"] is None and usado["papel"] == "usado"
    assert "Hunter2" not in json.dumps(corpo)


async def test_titulo_comprido_e_cortado_em_titulo_max(mundo: Mundo, cliente: httpx.AsyncClient) -> None:
    """O título é uma linha do relatório: o comando comprido sai cortado, com reticências, e o começo intacto."""
    comando = "abra o perfil e role a lista de fotos devagar até o fim da página " * 5
    assert len(comando.strip()) > TITULO_MAX
    mundo.db.execute("UPDATE flows SET command_template=? WHERE id='fluxo-usado'", (comando,))
    corpo = (await cliente.get(f"/api/runs/{RUN}/feedback")).json()
    titulo = str(_por_ref(corpo, "fluxos")["fluxo-usado"]["titulo"])
    assert len(titulo) <= TITULO_MAX and titulo.endswith("…")
    assert comando.startswith(titulo[:-1]) and len(titulo) > TITULO_MAX - 10


async def test_item_apagado_depois_da_execucao_sai_com_o_ref_e_sem_titulo(mundo: Mundo,
                                                                         cliente: httpx.AsyncClient) -> None:
    """O fluxo usado e a lição exposta somem do banco depois da execução: as linhas continuam, com o ref, sem título
    e sem estado — e o bloco não vira `null`."""
    mundo.db.execute("DELETE FROM flows WHERE id='fluxo-usado'")
    mundo.db.execute("DELETE FROM learning_items WHERE id=?", (mundo.licao,))
    r = await cliente.get(f"/api/runs/{RUN}/feedback")
    assert r.status_code == 200, r.text
    assert r.json()["aprendizado"] is not None
    usado = _por_ref(r.json(), "fluxos")["fluxo-usado"]
    assert (usado["kind"], usado["titulo"], usado["estado"], usado["papel"]) == ("fluxo", None, None, "usado")
    licao = _por_ref(r.json(), "licoes")[mundo.licao]
    assert (licao["titulo"], licao["estado"], licao["papel"]) == (None, None, "exposta ao prompt (ator)")
    # O que não foi apagado continua com título.
    assert _por_ref(r.json(), "fluxos")["fluxo-aprendido"]["titulo"] == "cmd fluxo-aprendido"


async def test_bloco_da_execucao_simulada_e_da_vazia(mundo: Mundo, cliente: httpx.AsyncClient) -> None:
    simulada = (await cliente.get(f"/api/runs/{SIM}/feedback")).json()
    assert set(_por_ref(simulada, "fluxos")) == {"fluxo-simulado"}
    assert set(_por_ref(simulada, "receitas")) == {str(mundo.receita_simulada)}
    _execucao(mundo.db, "r-vazia")
    vazia = await cliente.get("/api/runs/r-vazia/feedback")
    assert vazia.status_code == 200, vazia.text
    assert vazia.json()["aprendizado"] == {"receitas": [], "fluxos": [], "falhas": [], "candidatas": [], "licoes": []}


async def test_leitura_do_bloco_que_quebra_nao_derruba_os_votos(mundo: Mundo, cliente: httpx.AsyncClient,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    """O bloco informa: uma linha legada estranha vira `aprendizado: null`, e os votos e os sinais continuam saindo.
    O painel diz que não conseguiu ler o bloco (nunca "nada aprendido") e mostra os votos e os sinais
    (`ReportTab.test.tsx`, 'o bloco nulo…')."""
    def quebra(self: LeituraDoAprendidoSql, run_id: str) -> None:
        raise TypeError("coluna estranha")

    monkeypatch.setattr(LeituraDoAprendidoSql, "fatos", quebra)
    assert (await cliente.post(f"/api/runs/{RUN}/feedback", json={"verdict": "certo"})).status_code == 201
    r = await cliente.get(f"/api/runs/{RUN}/feedback")
    assert r.status_code == 200, r.text
    assert r.json()["aprendizado"] is None and [v["verdict"] for v in r.json()["votos"]] == ["certo"]


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
