"""Lições por contraste (ADR-054, decisão 5; pacote A7): o texto seguro por construção e o nascimento sem IA.

O que se prova:
- as lacunas dos modelos fechados só aceitam os valores permitidos (ação do catálogo, tipo de pós-condição, contagem,
  sufixo de resource-id, `{parâmetro}`, rótulo curto repetido) — todo modelo só tem lacunas do vocabulário;
- recusa texto de tela (legenda longa), dígito, nome de terceiro (@, ponto, sublinhado, Nome Próprio, valor de
  parâmetro) e o valor de um parâmetro — o elemento cujo texto é o parâmetro entra pelo NOME;
- o rótulo só entra quando o mesmo foi tocado em 2 execuções reais distintas;
- a lista de exclusão: autenticação, desafio, 2FA, CAPTCHA, conta, IA e infraestrutura, a etapa de sessão (pela ação
  do catálogo e pela chave da etapa livre, também na nota de pessoa), a tela de login ou desafio e a execução que
  digitou segredo;
- o contraste falha→sucesso comprovado gera candidata; a falha sem contraste, a confirmação à mão e a execução
  simulada não geram;
- a mesma impressão em 2 execuções valida; sem efeito e com o modo `on`, o sistema publica na fila de prova; com
  efeito, para em "Para aprovar" (D1); no `shadow`, valida e não publica;
- o defeito do plano seguido de plano que comprovou vira lição do planejador;
- a nota do "deu errado" vira candidata `human_origin` — o sistema não a valida nem publica;
- `GET /api/aprendizado/licoes/previa` em nível HTTP (o bloco exato, os tokens, o modo; 422 para papel que não
  recebe lição; 503 antes da composição).

Nível de prova: `simulated` (banco de teste; nenhum aparelho; nenhuma chamada de IA).
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from app.config import LearningCfg
from app.db import Database
from app.modules.learning.application.licoes import ServicoDeLicoes
from app.modules.learning.application.servico import LearningService
from app.modules.learning.domain.ciclo import SYSTEM_ACTOR, ExigeODono, SkillState
from app.modules.learning.domain.falhas import NUNCA_VIRA_LICAO, FailureKind
from app.modules.learning.domain.licoes import (ABRE, LACUNAS, MODELO_DA_NOTA, MODELO_DO_PLANEJADOR,
                                                MODELOS_DO_ATOR, Alvo, AlvoObservado, Contraste,
                                                ContrasteDoPlano, DefeitoDoPlano, MotivoDeRecusa, NotaDeFeedback,
                                                Recusa, TipoDeAlvo, alvo_seguro, lacunas_do_modelo,
                                                licao_de_contraste, licao_de_nota, licao_do_planejador,
                                                rotulo_permitido, sufixo_de_id)
from app.modules.learning.domain.livro import Escopo, ItemDeAprendizado, NovoItem
from app.modules.learning.domain.vocabulario import LivroKind, SourceKind
from app.modules.learning.infrastructure import ligar_licoes
from app.modules.learning.infrastructure.fontes import FontesSql
from app.modules.learning.infrastructure.licoes_sql import SqlLicoesRepository
from app.modules.learning.infrastructure.montagem import ajustes_do_config
from app.modules.learning.infrastructure.segredo import TriagemDeCredencial
from app.modules.learning.infrastructure.sql_repository import SqlLearningRepository
from app.modules.learning.presentation.router import router as learning_router
from app.util import to_iso

from .fake_skills import banco as banco_migrado

IG = "com.instagram.android"
AGORA = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
PRECOS = {"modelo-x": [1.0, 0.1, 1.25, 5.0]}


def rid(sufixo: str) -> str:
    return f"{IG}:id/{sufixo}"


# ================================================================== o mundo
class Mundo:
    """Livro + lições sobre um banco migrado, com relógio fixo e as métricas capturadas."""

    def __init__(self, db: Database, *, modo: str = "on") -> None:
        self.db = db
        self.agora = AGORA
        self.cfg = LearningCfg()
        self.cfg.licoes.modo = modo  # type: ignore[assignment]
        self.metricas: list[tuple[str, float, dict[str, str]]] = []
        self.repo = SqlLearningRepository(db, clock=lambda: to_iso(self.agora), precos=lambda: PRECOS)
        self.servico = LearningService(self.repo, FontesSql(db), TriagemDeCredencial(),
                                       ajustes=lambda: ajustes_do_config(self.cfg), relogio=lambda: self.agora,
                                       retencao_de_logs_dias=lambda: 14)
        self.licoes = ServicoDeLicoes(self.servico, self.repo, SqlLicoesRepository(db, precos=lambda: PRECOS),
                                      ajustes=lambda: ligar_licoes.ajustes_de_licoes(self.cfg.licoes),
                                      relogio=lambda: self.agora, contar=self._contar)
        for m in self.licoes.mineradores():
            self.servico.registrar_minerador(m)
        self.servico.registrar_passo(self.licoes.passo())
        if db.one("SELECT id FROM apps WHERE id='instagram'") is None:
            db.execute("INSERT INTO apps(id, name, package, builtin) VALUES ('instagram','Instagram',?,0)", (IG,))

    def _contar(self, nome: str, n: float = 1, **rotulos: str) -> None:
        self.metricas.append((nome, n, rotulos))

    def recusas(self) -> list[str]:
        return [r["motivo"] for nome, _, r in self.metricas if nome == "aprendizado.recusa"]

    def licoes_do_livro(self) -> list[ItemDeAprendizado]:
        return self.repo.itens(kind=LivroKind.LICAO)


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    d = banco_migrado(tmp_path, "licoes.sqlite3")
    yield d
    d.close()


@pytest.fixture
def mundo(db: Database) -> Mundo:
    return Mundo(db)


# ================================================================== semente de execução com toques
@dataclass
class Toque:
    rid: str | None = None
    texto: str = ""
    status: str = "done"
    tool: str = "tap"
    efeito: bool = False
    desc: str = ""

    def alvo(self) -> str | None:
        if self.status != "done":
            return None
        return json.dumps({"text": self.texto, "desc": self.desc, "resource_id": self.rid or "",
                           "class_name": "android.widget.Button", "package": IG, "bounds": [0, 0, 10, 10],
                           "clickable": True, "enabled": True, "focused": False, "scrollable": False,
                           "editable": False, "checked": False, "password": False}, ensure_ascii=False)


@dataclass
class Tentativa:
    status: str
    erro: str | None = None
    tipo: str | None = None
    toques: list[Toque] = field(default_factory=list)
    tela: str | None = None


def semear(db: Database, run_id: str, tentativas: list[Tentativa], *, instancia: str = "android-06",
           capability: str | None = "OPEN_POST", chave: str = "abrir", template_hash: str | None = "h-abrir",
           status_da_etapa: str = "succeeded", comprovada: bool = True, simulated: bool = False,
           dias_atras: float = 1.0, efeito: bool = False, parametros: dict[str, str] | None = None,
           pos: str = "text_visible", status_da_execucao: str = "completed", versao: int = 1) -> str:
    """Uma execução com UMA etapa e as tentativas dela, com os toques de cada uma. Devolve o id da etapa."""
    inicio = AGORA - timedelta(days=dias_atras)
    if db.one("SELECT id FROM runs WHERE id=?", (run_id,)) is None:
        db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at,"
                   " started_at, finished_at, app_ids) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   (run_id, run_id, "abrir o post", "execute", status_da_execucao, int(simulated),
                    json.dumps([instancia]), to_iso(inicio), to_iso(inicio),
                    to_iso(inicio + timedelta(minutes=10)), json.dumps(["instagram"])))
    objetivo = f"{run_id}:{instancia}"
    if db.one("SELECT id FROM objectives WHERE id=?", (objetivo,)) is None:
        db.execute("INSERT INTO objectives(id, run_id, instance_id, status, parameters) VALUES (?,?,?,?,?)",
                   (objetivo, run_id, instancia, "succeeded", json.dumps(parametros or {}, ensure_ascii=False)))
    sid = f"{objetivo}:v{versao}:{chave}"
    resultado = json.dumps({"verified": comprovada, "evidence_text": "ok"})
    db.execute("INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal,"
               " postcondition, timeout_s, max_attempts, status, capability, app_id, template_hash, side_effect,"
               " result, driven_by, started_at, finished_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (sid, run_id, objetivo, instancia, versao, 0, chave, chave, chave,
                json.dumps({"kind": pos, "value": "x", "description": "x"}), 60, 3, status_da_etapa, capability, None,
                template_hash, int(efeito), resultado if status_da_etapa == "succeeded" else None, "ai",
                to_iso(inicio), to_iso(inicio + timedelta(minutes=5))))
    for n, t in enumerate(tentativas, start=1):
        aid = f"{sid}:a{n}"
        db.execute("INSERT INTO attempts(id, step_id, number, status, started_at, finished_at, error, failure_kind,"
                   " failure_screen) VALUES (?,?,?,?,?,?,?,?,?)",
                   (aid, sid, n, t.status, to_iso(inicio + timedelta(minutes=n)),
                    to_iso(inicio + timedelta(minutes=n, seconds=30)), t.erro, t.tipo, t.tela))
        for seq, q in enumerate(t.toques, start=1):
            db.execute("INSERT INTO actions(attempt_id, seq, tool, args, status, side_effect, intent_at, target)"
                       " VALUES (?,?,?,?,?,?,?,?)",
                       (aid, seq, q.tool, "{}", q.status, int(q.efeito), to_iso(inicio), q.alvo()))
    return sid


def contraste_ciclo(db: Database, run_id: str, *, instancia: str = "android-06", **kw: object) -> str:
    return semear(db, run_id, [
        Tentativa("failed", "Ciclo sem progresso: a mesma ação não muda a tela.", "ciclo_sem_progresso",
                  [Toque(rid("row_feed_photo"))]),
        Tentativa("succeeded", toques=[Toque(rid("row_feed_comment_tv")), Toque(rid("comment_button"))]),
    ], instancia=instancia, **kw)  # type: ignore[arg-type]


# ================================================================== o texto fechado (domínio)
def test_as_lacunas_so_aceitam_os_valores_permitidos() -> None:
    for modelos in MODELOS_DO_ATOR.values():
        for modelo in modelos:
            assert lacunas_do_modelo(modelo) <= LACUNAS and "alvo_bom" in lacunas_do_modelo(modelo)
    assert lacunas_do_modelo(MODELO_DO_PLANEJADOR) == {"app", "tipo", "acao", "n"}
    assert lacunas_do_modelo(MODELO_DA_NOTA) == {"prefixo", "nota"}
    # o sufixo do resource-id é identificador, nunca texto
    assert sufixo_de_id(rid("row_feed_photo")) == "row_feed_photo"
    assert sufixo_de_id("com.x:id/tem espaço") is None and sufixo_de_id("com.x:id/<b>") is None
    assert sufixo_de_id("") is None and sufixo_de_id(None) is None
    base = Contraste(run_id="r1", instance_id="a1", step_id="s1", app=IG, capability="OPEN_POST", step_hash="h",
                     side_effect=False, tipo=FailureKind.CICLO_SEM_PROGRESSO, tentativa_ruim="t1",
                     tentativa_boa="t2", primeiro_alvo_bom=AlvoObservado(resource_id=rid("ok_button")))
    boa = licao_de_contraste(base)
    assert isinstance(boa, NovoItem) and "[id=ok_button]" in boa.summary
    assert boa.content == {"modelo": "ciclo_sem_progresso", "acao": "OPEN_POST",
                           "alvo": {"tipo": "id", "valor": "ok_button"}}
    # ação fora do padrão de identificador do catálogo, app que não é pacote: recusa
    for ruim, motivo in ((Contraste(**{**_campos(base), "capability": "abrir post de <ana>"}),
                          MotivoDeRecusa.ACAO_INVALIDA),
                         (Contraste(**{**_campos(base), "app": "Instagram da Ana"}), MotivoDeRecusa.APP_INVALIDO)):
        assert licao_de_contraste(ruim) == Recusa(motivo)
    # a contagem é número; o tipo da pós-condição do planejador, identificador
    plano = ContrasteDoPlano(app=IG, acao="OPEN_POST", livre=False, tipo="model judged!", execucao_que_comprovou="r",
                             defeitos=(DefeitoDoPlano("s", "r0", None),), side_effect=False)
    assert licao_do_planejador(plano) == Recusa(MotivoDeRecusa.TIPO_INVALIDO)


def _campos(c: Contraste) -> dict[str, object]:
    return {f: getattr(c, f) for f in c.__dataclass_fields__}


def test_recusa_texto_de_tela_digito_nome_de_terceiro_e_valor_de_parametro() -> None:
    params = {"recipient": "@ana.souza", "content": "Parabéns pelo show de ontem"}
    def veja(rotulo: str, execucoes: int = 5) -> Alvo | Recusa:
        return alvo_seguro(AlvoObservado(rotulo=rotulo, rotulo_execucoes=execucoes), params)

    assert veja("Comentar") == Alvo(TipoDeAlvo.ROTULO, "Comentar")
    assert veja("Ver 12 comentários") == Recusa(MotivoDeRecusa.ALVO_INSEGURO)             # dígito
    assert veja("Uma legenda comprida de publicação que alguém escreveu ontem") == Recusa(
        MotivoDeRecusa.ALVO_INSEGURO)                                                       # texto livre de tela
    assert veja("Maria Clara Souza") == Recusa(MotivoDeRecusa.ALVO_INSEGURO)              # nome próprio
    assert veja("@bia") == Recusa(MotivoDeRecusa.ALVO_INSEGURO)                           # identificador de pessoa
    assert veja("bia_lima") == Recusa(MotivoDeRecusa.ALVO_INSEGURO)
    assert veja("bia.lima") == Recusa(MotivoDeRecusa.ALVO_INSEGURO)
    assert veja("ana.souza") == Alvo(TipoDeAlvo.PARAMETRO, "recipient")                   # vai o NOME, não o valor
    assert veja("Conversa com ana.souza") == Recusa(MotivoDeRecusa.VALOR_DE_PARAMETRO)    # contém o valor
    assert veja("Parabéns pelo show") == Recusa(MotivoDeRecusa.VALOR_DE_PARAMETRO)        # pedaço do conteúdo
    assert Alvo(TipoDeAlvo.PARAMETRO, "recipient").texto() == "[texto de {recipient}]"
    assert not rotulo_permitido("x" * 41) and rotulo_permitido("Enviar mensagem")
    # e a lição montada com esses alvos nunca carrega o valor do parâmetro
    c = Contraste(run_id="r1", instance_id="a1", step_id="s1", app=IG, capability="SEND_MESSAGE", step_hash="h",
                  side_effect=False, tipo=FailureKind.ALVO_AUSENTE, tentativa_ruim="t1", tentativa_boa="t2",
                  alvo_ruim=AlvoObservado(rotulo="Maria Clara Souza", rotulo_execucoes=9),
                  primeiro_alvo_bom=AlvoObservado(rotulo="ana.souza"), parametros=params)
    licao = licao_de_contraste(c)
    assert isinstance(licao, NovoItem)
    assert "ana.souza" not in licao.summary and "Maria" not in licao.summary
    assert licao.summary == ("Em SEND_MESSAGE: o alvo não foi achado na tentativa anterior; o que comprovou foi tocar "
                             "em [texto de {recipient}].")


def test_rotulo_so_quando_se_repetiu_em_duas_execucoes() -> None:
    uma = alvo_seguro(AlvoObservado(rotulo="Comentar", rotulo_execucoes=1), {})
    duas = alvo_seguro(AlvoObservado(rotulo="Comentar", rotulo_execucoes=2), {})
    assert uma == Recusa(MotivoDeRecusa.ROTULO_SEM_REPETICAO) and duas == Alvo(TipoDeAlvo.ROTULO, "Comentar")
    assert alvo_seguro(AlvoObservado(resource_id=rid("x_y"), rotulo="Comentar"), {}) == Alvo(TipoDeAlvo.ID, "x_y")


def test_rotulo_repetido_contado_no_banco_em_execucoes_reais(mundo: Mundo) -> None:
    db = mundo.db
    def por_rotulo(run_id: str, instancia: str) -> None:
        semear(db, run_id, [Tentativa("failed", "Ciclo sem progresso: x", "ciclo_sem_progresso",
                                      [Toque(texto="Curtir")]),
                            Tentativa("succeeded", toques=[Toque(texto="Comentar")])], instancia=instancia)

    por_rotulo("r-a", "android-06")
    assert mundo.licoes.minerar_contrastes("r-a") == 0                       # "Comentar" tocado em 1 execução só
    assert MotivoDeRecusa.ROTULO_SEM_REPETICAO.value in mundo.recusas() and mundo.licoes_do_livro() == []
    semear(db, "r-sim", [Tentativa("succeeded", toques=[Toque(texto="Comentar")])], simulated=True,
           instancia="android-09")
    por_rotulo("r-b", "android-07")                                           # a simulada não conta repetição…
    assert mundo.licoes.minerar_contrastes("r-a") == 1                        # …as duas reais, sim
    [licao] = mundo.licoes_do_livro()
    # "Curtir" (o toque que não andou) também foi tocado nas duas: pode entrar como o alvo ruim.
    assert licao.summary == ('Em OPEN_POST: repetir ["Curtir"] na mesma tela não mudou nada; o caminho que '
                             'comprovou começou por ["Comentar"].')
    assert licao.content["alvo"] == {"tipo": "rotulo", "valor": "Comentar"}


# ================================================================== exclusões
def test_lista_de_exclusao_autenticacao_desafio_ia_e_infraestrutura(mundo: Mundo) -> None:
    base = Contraste(run_id="r1", instance_id="a1", step_id="s1", app=IG, capability="OPEN_POST", step_hash="h",
                     side_effect=False, tipo=FailureKind.CICLO_SEM_PROGRESSO, tentativa_ruim="t1",
                     tentativa_boa="t2", primeiro_alvo_bom=AlvoObservado(resource_id=rid("ok")))
    assert FailureKind.AUTENTICACAO in NUNCA_VIRA_LICAO and FailureKind.IA_INDISPONIVEL in NUNCA_VIRA_LICAO
    for tipo in NUNCA_VIRA_LICAO:
        assert licao_de_contraste(Contraste(**{**_campos(base), "tipo": tipo})) == Recusa(MotivoDeRecusa.TIPO_EXCLUIDO)
    assert {t.value for t in NUNCA_VIRA_LICAO} >= {"autenticacao", "conta_errada", "ia_indisponivel", "ia_recusa",
                                                    "ia_orcamento", "ia_saldo", "ia_chamada_invalida",
                                                    "ia_declarou_bloqueio", "sessao_de_automacao", "app_anr",
                                                    "ui_ocupada", "aparelho_travado", "interrompida"}
    for acao in ("AUTHENTICATE_INSTAGRAM", "LOGIN_WITH_PASSWORD", "SOLVE_CAPTCHA", "CONFIRM_2FA_CODE"):
        assert licao_de_contraste(Contraste(**{**_campos(base), "capability": acao})) == Recusa(
            MotivoDeRecusa.ACAO_DE_SESSAO)
    for tela in ("login", "desafio", "dois_fatores"):
        assert licao_de_contraste(Contraste(**{**_campos(base), "tela_ruim": tela})) == Recusa(
            MotivoDeRecusa.TELA_SENSIVEL)
    assert licao_de_contraste(Contraste(**{**_campos(base), "sensivel": True})) == Recusa(
        MotivoDeRecusa.TELA_SENSIVEL)
    assert licao_de_contraste(Contraste(**{**_campos(base), "tipo": FailureKind.PRAZO_DA_ETAPA})) == Recusa(
        MotivoDeRecusa.SEM_MODELO)                                           # sem modelo fechado: não vira lição

    # no banco: a execução que digitou segredo e a que tocou uma tela de desafio não ensinam nada
    semear(mundo.db, "r-secreta", [
        Tentativa("failed", "Ciclo sem progresso", "ciclo_sem_progresso", [Toque(tool="type_secret")]),
        Tentativa("succeeded", toques=[Toque(rid("ok"))])])
    semear(mundo.db, "r-desafio", [
        Tentativa("failed", "Ciclo sem progresso", "ciclo_sem_progresso", [Toque(texto="Verify your account")]),
        Tentativa("succeeded", toques=[Toque(rid("ok"))])], instancia="android-07")
    semear(mundo.db, "r-ia", [
        Tentativa("failed", "IA indisponível: falha de rede ao contatar o provedor", None, [Toque(rid("x"))]),
        Tentativa("succeeded", toques=[Toque(rid("ok"))])], instancia="android-08")
    assert sum(mundo.licoes.minerar_contrastes(r) for r in ("r-secreta", "r-desafio", "r-ia")) == 0
    assert mundo.licoes_do_livro() == []
    assert mundo.recusas().count(MotivoDeRecusa.TELA_SENSIVEL.value) == 2
    assert MotivoDeRecusa.TIPO_EXCLUIDO.value in mundo.recusas()


def test_etapa_livre_de_sessao_login_ou_desafio_nunca_vira_licao(mundo: Mundo) -> None:
    """A etapa livre não tem ação do catálogo para a lista de sessão olhar: é a CHAVE dela (do planejador) que diz
    que ela é de login ou de desafio — e o desafio segue com a pessoa (ADR-009). Sem rótulo no toque (só um id), a
    barreira do texto de desafio não pega; a da chave, sim."""
    base = Contraste(run_id="r1", instance_id="a1", step_id="s1", app=IG, capability="*", step_hash="h-des",
                     side_effect=False, tipo=FailureKind.CICLO_SEM_PROGRESSO, tentativa_ruim="t1",
                     tentativa_boa="t2", step_key="abrir_post",
                     primeiro_alvo_bom=AlvoObservado(resource_id=rid("continue_button")))
    assert isinstance(licao_de_contraste(base), NovoItem)                     # contraprova: a chave neutra passa
    for chave in ("resolver_desafio", "fazer_login", "digitar_senha", "confirmar_2fa", "resolver_desafio_i2"):
        assert licao_de_contraste(Contraste(**{**_campos(base), "step_key": chave})) == Recusa(
            MotivoDeRecusa.ACAO_DE_SESSAO)
    # a chave de sessão numa etapa com ação do catálogo também não ensina
    assert licao_de_contraste(Contraste(**{**_campos(base), "capability": "OPEN_POST", "step_key": "fazer_login"})) \
        == Recusa(MotivoDeRecusa.ACAO_DE_SESSAO)

    # no banco, o cenário que publicava: duas execuções reais, a etapa livre falha em ciclo e passa tocando num id
    for chave, passo in (("resolver_desafio", "h-des"), ("fazer_login", "h-login")):
        for run, instancia in ((f"r-{chave}-1", "android-06"), (f"r-{chave}-2", "android-07")):
            semear(mundo.db, run, [
                Tentativa("failed", "Ciclo sem progresso: a mesma ação não muda a tela.", "ciclo_sem_progresso",
                          [Toque(rid("row_feed_photo"))]),
                Tentativa("succeeded", toques=[Toque(rid("continue_button"))]),
            ], instancia=instancia, capability=None, chave=chave, template_hash=passo)
            mundo.servico.digerir_execucao(run)                               # pelo digest, como em produção
    assert mundo.licoes_do_livro() == []
    assert mundo.recusas().count(MotivoDeRecusa.ACAO_DE_SESSAO.value) == 4

    # a nota de pessoa na etapa livre de login também não vira candidata
    nota = NotaDeFeedback(signal_id=1, app=IG, capability="*", step_hash="h-login", nota="toque em continuar",
                          side_effect=False, step_key="fazer_login")
    assert licao_de_nota(nota) == Recusa(MotivoDeRecusa.ACAO_DE_SESSAO)
    assert isinstance(licao_de_nota(NotaDeFeedback(**{f: getattr(nota, f) for f in nota.__dataclass_fields__
                                                      if f != "step_key"})), NovoItem)
    sid = "r-fazer_login-1:android-06:v1:fazer_login"
    mundo.db.execute(
        "INSERT INTO learning_signals(kind, polarity, verdict, reason, note, source_ref, created_by, run_id, step_id,"
        " app_package, capability, step_hash, data, simulated, created_at) VALUES"
        " ('feedback','negative','errado','alvo_errado',?,?,?,?,?,?,?,?,'{}',0,?)",
        ("Toque em continuar antes", "objective:r-fazer_login-1:android-06", "panel", "r-fazer_login-1", sid, IG,
         "*", "h-login", to_iso(AGORA - timedelta(hours=1))))
    mundo.servico.curar()
    assert mundo.licoes_do_livro() == []
    assert mundo.recusas().count(MotivoDeRecusa.ACAO_DE_SESSAO.value) == 5


# ================================================================== contraste → candidata
def test_contraste_gera_candidata_e_falha_sem_contraste_nao_gera(mundo: Mundo) -> None:
    contraste_ciclo(mundo.db, "r1")
    assert mundo.licoes.minerar_contrastes("r1") == 1
    [licao] = mundo.licoes_do_livro()
    assert licao.state is SkillState.CANDIDATE and licao.source_kind is SourceKind.RECOVERY
    assert licao.escopo == Escopo(app=IG, capability="OPEN_POST", role="actor")
    assert licao.summary == ("Em OPEN_POST: repetir [id=row_feed_photo] na mesma tela não mudou nada; o caminho que "
                             "comprovou começou por [id=row_feed_comment_tv].")
    assert licao.content == {"modelo": "ciclo_sem_progresso", "acao": "OPEN_POST",
                             "alvo": {"tipo": "id", "valor": "row_feed_comment_tv"}}
    assert (licao.evidence_for, licao.distinct_runs, licao.side_effect, licao.human_origin) == (1, 1, False, False)
    assert licao.tokens is not None and licao.tokens > 0
    assert mundo.licoes.minerar_contrastes("r1") == 0                         # a mesma observação não soma de novo

    # falha repetida sem sucesso: não prova o que funciona (vai para o backlog, não para a lição)
    semear(mundo.db, "r2", [Tentativa("failed", "Ciclo sem progresso", "ciclo_sem_progresso", [Toque(rid("a"))]),
                            Tentativa("failed", "Ciclo sem progresso", "ciclo_sem_progresso", [Toque(rid("b"))])],
           status_da_etapa="failed", instancia="android-07")
    # sucesso por confirmação à mão (`verified=false`): nunca é evidência a favor
    semear(mundo.db, "r3", [Tentativa("failed", "Ciclo sem progresso", "ciclo_sem_progresso", [Toque(rid("a"))]),
                            Tentativa("succeeded", toques=[Toque(rid("c"))])], comprovada=False,
           instancia="android-08")
    # execução simulada: nunca é fonte
    contraste_ciclo(mundo.db, "r4", instancia="android-09", simulated=True)
    assert sum(mundo.licoes.minerar_contrastes(r) for r in ("r2", "r3", "r4")) == 0
    assert len(mundo.licoes_do_livro()) == 1


def test_contraste_atravessa_a_versao_revista_do_plano(mundo: Mundo) -> None:
    """A recuperação revê o plano: a mesma etapa (mesma chave e modelo) na versão seguinte é o sucesso do contraste;
    a cópia de outra chave (outro item de `for_each`) não é."""
    semear(mundo.db, "r1", [Tentativa("failed", "Pós-condição não comprovada: x", "pos_condicao_nao_comprovada",
                                      [Toque(rid("row_errado"))])], status_da_etapa="failed")
    semear(mundo.db, "r1", [Tentativa("succeeded", toques=[Toque(rid("row_certo"))])], versao=2, dias_atras=0.9)
    semear(mundo.db, "r1", [Tentativa("succeeded", toques=[Toque(rid("row_outro"))])], chave="abrir_i2",
           dias_atras=0.8)
    assert mundo.licoes.minerar_contrastes("r1") == 1
    [licao] = mundo.licoes_do_livro()
    assert licao.summary == ("Em OPEN_POST: a tentativa que não comprovou tocou em [id=row_errado]; a que comprovou "
                             "tocou em [id=row_certo].")


def test_etapa_livre_fica_no_escopo_da_etapa_exata(mundo: Mundo) -> None:
    contraste_ciclo(mundo.db, "r1", capability=None, template_hash="h-livre")
    mundo.licoes.minerar_contrastes("r1")
    [licao] = mundo.licoes_do_livro()
    assert licao.escopo == Escopo(app=IG, capability="*", step_hash="h-livre", role="actor")
    assert licao.summary.startswith("Nesta etapa: ")


def test_fingerprint_em_duas_execucoes_valida_e_publica_na_fila_sem_efeito(mundo: Mundo) -> None:
    contraste_ciclo(mundo.db, "r1")
    mundo.licoes.minerar_contrastes("r1")
    assert mundo.licoes_do_livro()[0].state is SkillState.CANDIDATE            # uma execução não basta
    contraste_ciclo(mundo.db, "r2", instancia="android-07")
    mundo.servico.digerir_execucao("r2")                                      # pelo digest, como em produção
    [licao] = mundo.licoes_do_livro()
    assert (licao.distinct_runs, licao.distinct_devices) == (2, 2)
    assert licao.state is SkillState.PUBLISHED and licao.state_detail == "fila_de_prova"
    trilha = [(t.from_state, t.to_state, t.decided_by) for t in mundo.repo.trilha(licao.id)]
    assert trilha == [(None, SkillState.CANDIDATE, SYSTEM_ACTOR), (SkillState.CANDIDATE, SkillState.VALIDATED,
                                                                  SYSTEM_ACTOR),
                      (SkillState.VALIDATED, SkillState.PUBLISHED, SYSTEM_ACTOR)]
    mundo.servico.curar()                                                     # a curadoria abre a prova
    assert mundo.repo.item(licao.id).state_detail == "em_prova"  # type: ignore[union-attr]


def test_com_efeito_para_em_para_aprovar_e_no_shadow_nao_publica(db: Database) -> None:
    com_efeito = Mundo(db)
    contraste_ciclo(db, "r1", efeito=True)
    contraste_ciclo(db, "r2", instancia="android-07", efeito=True)
    for r in ("r1", "r2"):
        com_efeito.licoes.minerar_contrastes(r)
    [licao] = com_efeito.licoes_do_livro()
    assert licao.side_effect and licao.state is SkillState.VALIDATED and licao.requires_owner
    assert [e.ref for e in com_efeito.servico.pendentes()] == [licao.id]      # "Para aprovar", com o dono
    with pytest.raises(ExigeODono):
        com_efeito.servico.mudar_estado(LivroKind.LICAO, licao.id, SkillState.PUBLISHED, by=SYSTEM_ACTOR,
                                        reason="tentativa do sistema")

    sombra = Mundo(db, modo="shadow")
    contraste_ciclo(db, "r3", capability="LIKE_POST", chave="curtir", template_hash="h-curtir")
    contraste_ciclo(db, "r4", instancia="android-07", capability="LIKE_POST", chave="curtir",
                    template_hash="h-curtir")
    for r in ("r3", "r4"):
        sombra.licoes.minerar_contrastes(r)
    curtir = next(i for i in sombra.licoes_do_livro() if i.escopo.capability == "LIKE_POST")
    assert curtir.state is SkillState.VALIDATED and not curtir.requires_owner   # valida, mas não publica

    desligado = Mundo(db, modo="off")
    contraste_ciclo(db, "r5", instancia="android-08", capability="SAVE_POST", chave="salvar",
                    template_hash="h-salvar")
    assert desligado.licoes.minerar_contrastes("r5") == 0                      # `off` não grava


# ================================================================== planejador
def test_defeito_do_plano_seguido_de_plano_que_comprovou_vira_licao_do_planejador(mundo: Mundo) -> None:
    semear(mundo.db, "r-defeito", [Tentativa("failed", "Defeito do plano — a pós-condição não é comprovável",
                                             "defeito_do_plano")],
           status_da_etapa="failed", pos="model_judged", dias_atras=5, status_da_execucao="failed")
    mundo.db.execute("UPDATE steps SET failure_kind='defeito_do_plano' WHERE run_id='r-defeito'")
    semear(mundo.db, "r-ok", [Tentativa("succeeded", toques=[Toque(rid("ok"))])], pos="text_visible", dias_atras=1)
    assert mundo.licoes.minerar_plano("r-defeito") == 0                      # a falha sozinha não ensina
    assert mundo.licoes.minerar_plano("r-ok") == 1
    [licao] = mundo.licoes_do_livro()
    assert licao.escopo == Escopo(app=IG, role="planner") and licao.source_kind is SourceKind.PLAN_DEFECT
    assert licao.summary == ("Em com.instagram.android: não use a pós-condição model_judged em OPEN_POST; foi "
                             "julgada não comprovável 1×.")
    [ev] = mundo.repo.evidencias(licao.id)
    assert ev.origin_ref == "step:r-defeito:android-06:v1:abrir" and ev.run_id == "r-defeito"
    assert "model_judged" in json.dumps(licao.content)


SELETOR = "seletor_em_elementos_diferentes"
DETALHE_DO_SELETOR = "Defeito do plano — as partes do seletor estão em elementos diferentes desta tela"


def _defeito(mundo: Mundo, run: str, *, pos: str, falha: str, detalhe: str, dias: int = 5) -> None:
    semear(mundo.db, run, [Tentativa("failed", detalhe, falha)], status_da_etapa="failed", pos=pos, dias_atras=dias,
           status_da_execucao="failed")
    mundo.db.execute("UPDATE steps SET failure_kind=? WHERE run_id=?", (falha, run))


def test_seletor_em_elementos_diferentes_vira_licao_do_seletor_31_32(mundo: Mundo) -> None:
    """31.32: o tipo novo é lido pelo minerador. A lição diz para não juntar partes de elementos diferentes, sem
    proibir o tipo da pós-condição."""
    _defeito(mundo, "r-seletor", pos="model_judged", falha=SELETOR, detalhe=DETALHE_DO_SELETOR)
    semear(mundo.db, "r-ok", [Tentativa("succeeded", toques=[Toque(rid("ok"))])], pos="text_visible", dias_atras=1)
    assert mundo.licoes.minerar_plano("r-ok") == 1
    [licao] = mundo.licoes_do_livro()
    assert licao.source_kind is SourceKind.PLAN_DEFECT and licao.escopo == Escopo(app=IG, role="planner")
    assert licao.summary == ("Em com.instagram.android: na pós-condição model_judged de OPEN_POST, não junte num "
                             "seletor só partes que a tela tem em elementos diferentes; falhou assim 1×.")
    assert licao.content["modelo"] == SELETOR


def test_o_defeito_do_seletor_ensina_contra_o_mesmo_tipo_e_o_generico_nao_31_32(mundo: Mundo) -> None:
    """O conserto do seletor é um elemento só no MESMO tipo: o plano seguinte que comprovou com `text_visible` ensina.
    O defeito genérico do mesmo tipo segue sem contraste (nada mudou de tipo)."""
    _defeito(mundo, "r-generico", pos="text_visible", falha="defeito_do_plano",
             detalhe="Defeito do plano — a pós-condição não é comprovável", dias=6)
    semear(mundo.db, "r-ok1", [Tentativa("succeeded", toques=[Toque(rid("ok"))])], pos="text_visible", dias_atras=5)
    assert mundo.licoes.minerar_plano("r-ok1") == 0
    _defeito(mundo, "r-seletor", pos="text_visible", falha=SELETOR, detalhe=DETALHE_DO_SELETOR, dias=3)
    semear(mundo.db, "r-ok2", [Tentativa("succeeded", toques=[Toque(rid("ok"))])], pos="text_visible", dias_atras=1)
    assert mundo.licoes.minerar_plano("r-ok2") == 1
    [licao] = mundo.licoes_do_livro()
    assert licao.content["modelo"] == SELETOR and "text_visible" in licao.summary
    [ev] = mundo.repo.evidencias(licao.id)
    assert ev.run_id == "r-seletor"                                          # só o do seletor; o genérico ficou fora


def test_defeitos_misturados_ficam_com_a_licao_do_tipo_31_32(mundo: Mundo) -> None:
    """Um genérico no meio dos do seletor: vale a lição mais ampla (não use o tipo)."""
    _defeito(mundo, "r-seletor", pos="model_judged", falha=SELETOR, detalhe=DETALHE_DO_SELETOR, dias=6)
    _defeito(mundo, "r-generico", pos="model_judged", falha="defeito_do_plano",
             detalhe="Defeito do plano — a pós-condição não é comprovável", dias=4)
    semear(mundo.db, "r-ok", [Tentativa("succeeded", toques=[Toque(rid("ok"))])], pos="text_visible", dias_atras=1)
    assert mundo.licoes.minerar_plano("r-ok") == 2                           # uma evidência por defeito
    [licao] = mundo.licoes_do_livro()                                       # numa lição só
    assert licao.content["modelo"] == "defeito_do_plano"
    assert licao.summary.startswith("Em com.instagram.android: não use a pós-condição model_judged em OPEN_POST")


# ================================================================== nota humana
def test_nota_humana_nasce_human_origin_e_so_o_dono_valida(mundo: Mundo) -> None:
    sid = contraste_ciclo(mundo.db, "r1")
    mundo.db.execute(
        "INSERT INTO learning_signals(kind, polarity, verdict, reason, note, source_ref, created_by, run_id, step_id,"
        " app_package, capability, step_hash, data, simulated, created_at) VALUES"
        " ('feedback','negative','errado','alvo_errado',?,?,?,?,?,?,?,?,'{}',0,?)",
        ("Toque no balão do post certo <antes> de comentar", "objective:r1:android-06", "panel", "r1", sid, IG,
         "OPEN_POST", "h-abrir", to_iso(AGORA - timedelta(hours=1))))
    mundo.servico.curar()
    [licao] = [i for i in mundo.licoes_do_livro() if i.source_kind is SourceKind.FEEDBACK_NOTE]
    assert licao.human_origin and licao.requires_owner and licao.state is SkillState.CANDIDATE
    assert licao.summary == "Em OPEN_POST: nota de quem acompanhou: Toque no balão do post certo antes de comentar"
    assert "<" not in licao.summary                                          # marcação nunca chega ao bloco
    assert licao.id in {e.ref for e in mundo.servico.pendentes()}             # "Para aprovar"
    with pytest.raises(ExigeODono):
        mundo.servico.mudar_estado(LivroKind.LICAO, licao.id, SkillState.VALIDATED, by=SYSTEM_ACTOR, reason="x")
    mundo.servico.curar()                                                     # a curadoria não a promove sozinha
    assert mundo.repo.item(licao.id).state is SkillState.CANDIDATE  # type: ignore[union-attr]
    assert licao_de_nota(NotaDeFeedback(signal_id=1, app=IG, capability="OPEN_POST", step_hash="", nota="x" * 201,
                                        side_effect=False)) == Recusa(MotivoDeRecusa.NOTA_INVALIDA)


# ================================================================== a rota da prévia
def _publicar(mundo: Mundo, texto: str, *, capability: str = "OPEN_POST", papel: str = "actor",
              detalhe: str = "em_prova", evidencia: int = 2) -> ItemDeAprendizado:
    item = mundo.repo.criar_item(
        NovoItem(kind=LivroKind.LICAO, escopo=Escopo(app=IG, capability=capability, role=papel),
                 content={"texto": texto}, summary=texto, source_kind=SourceKind.RECOVERY, side_effect=False,
                 app_version="447"),
        by="painel", estado=SkillState.PUBLISHED, detalhe=detalhe, reason="teste")
    mundo.db.execute("UPDATE learning_items SET evidence_for=? WHERE id=?", (evidencia, item.id))
    atual = mundo.repo.item(item.id)
    assert atual is not None
    return atual


@pytest.fixture
async def cliente(db: Database) -> AsyncIterator[tuple[Mundo, httpx.AsyncClient]]:
    mundo = Mundo(db, modo="shadow")
    ligar_licoes.ligar(mundo.servico, mundo.repo, db, config=lambda: mundo.cfg.licoes, precos=lambda: PRECOS,
                       relogio=lambda: mundo.agora)
    app = FastAPI()
    app.include_router(learning_router)
    app.state.poc = SimpleNamespace(learning=mundo.servico)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield mundo, c


async def test_rota_da_previa_devolve_o_bloco_exato_e_os_tokens(cliente: tuple[Mundo, httpx.AsyncClient]) -> None:
    mundo, c = cliente
    a = _publicar(mundo, "Em OPEN_POST: repetir a ação na mesma tela não mudou nada; o caminho que comprovou começou "
                         "por [id=row_x].", detalhe="medida:ajuda")
    b = _publicar(mundo, "Em OPEN_POST: a tentativa que comprovou começou tocando em [id=botao_ok].")
    _publicar(mundo, "Em LIKE_POST: outra ação.", capability="LIKE_POST")
    _publicar(mundo, "Em com.instagram.android: não use a pós-condição model_judged em OPEN_POST; foi julgada não "
                     "comprovável 2×.", capability="", papel="planner")
    r = await c.get("/api/aprendizado/licoes/previa?app=com.instagram.android&acao=OPEN_POST&papel=actor")
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["modo"] == "shadow" and corpo["vai_ao_prompt"] is False     # de fábrica, nada vai ao prompt
    assert [x["id"] for x in corpo["licoes"]] == [a.id, b.id]               # "ajuda" antes de "em prova"
    assert corpo["bloco"].startswith(ABRE) and a.summary in corpo["bloco"] and "LIKE_POST" not in corpo["bloco"]
    assert corpo["tokens"]["bloco"] > corpo["tokens"]["licoes"] > 0 and corpo["teto"]["tokens"] == 120
    em_prova = next(x for x in corpo["licoes"] if x["id"] == b.id)
    assert em_prova["faltam"] == 8 and next(x for x in corpo["licoes"] if x["id"] == a.id)["faltam"] is None

    plano = (await c.get("/api/aprendizado/licoes/previa?app=com.instagram.android&papel=planner")).json()
    assert len(plano["licoes"]) == 1 and plano["teto"]["tokens"] == 150 and "model_judged" in plano["bloco"]
    vazio = (await c.get("/api/aprendizado/licoes/previa?app=com.outro.app")).json()
    assert vazio["bloco"] == "" and vazio["licoes"] == []
    assert (await c.get("/api/aprendizado/licoes/previa?app=com.instagram.android&papel=verifier")).status_code == 422
    assert (await c.get("/api/aprendizado/licoes/previa")).status_code == 422
    # o detalhe do livro de uma lição traz as exposições
    detalhe = await c.get(f"/api/aprendizado/licao/{a.id}")
    assert detalhe.status_code == 200 and detalhe.json()["exposicoes"] == []


async def test_rota_da_previa_antes_da_composicao_e_503(db: Database) -> None:
    mundo = Mundo(db)                                                        # sem `ligar`: nada composto
    app = FastAPI()
    app.include_router(learning_router)
    app.state.poc = SimpleNamespace(learning=mundo.servico)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/aprendizado/licoes/previa?app=com.instagram.android")
    assert r.status_code == 503 and r.json()["detail"]["code"] == "not_ready"
