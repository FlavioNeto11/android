"""O que mais falha e o backlog da plataforma (ADR-054, decisão 7; pacote A3), com banco semeado.

O que se prova:
- agrupamento pela `cluster_key` canônica (app pelo PACOTE, `*` para a etapa livre, tela vazia) e o id `fk-*` estável,
  o mesmo no relatório e na linha gravada pela curadoria;
- o legado (`attempts.failure_kind` nulo) classificado na LEITURA e marcado retroativo, sem nunca gravar a coluna;
  `retroativo=False` conta só o que a execução classificou;
- a pontuação: US$ das chamadas de IA por `attempt_id` (a chamada da tentativa que deu certo não entra), minutos e
  intervenções humanas; o custo total só ordena, e o falso positivo do verificador fica SEMPRE no topo;
- o mínimo de 3 ocorrências no topo (a seção de verificação sai inteira), a tendência 7 d × 7 d, a camada `pessoa`
  fora por padrão, a porcentagem de `outro` e o simulado fora por padrão;
- o custo além da janela de `ai_calls` vem de `learning_daily` (um dia, uma fonte só), com `custo_parcial` quando um
  dia não tem nem uma nem outra; o diário recalculado pela curadoria é idempotente;
- a prova da correção: `fixed` com ≥10 elegíveis e taxa ≤ 50% da base; `reopened` acima; "faltam N" abaixo de 10;
  reincidência depois de `fixed` também reabre, medida nas últimas 2 × `prova_minimo` tentativas elegíveis (a volta
  concentrada da falha não se dilui nas boas depois da prova); `fixed` e `reopened` nunca por pessoa;
- as propostas `acao_de_catalogo`, `promover_licao` e `promover_tela`.

Nível de prova: `simulated` (banco de teste; nenhum aparelho, nenhuma IA).
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.config import BacklogCfg, LearningCfg
from app.db import Database
from app.modules.learning.application.falhas import ServicoDeFalhas
from app.modules.learning.application.servico import LearningService
from app.modules.learning.domain.backlog import (QUALQUER, ROTULO, ChaveDoGrupo, Medida, RegrasDoBacklog,
                                                 TipoDeVerificacao, Veredito, chave_do_grupo, id_do_backlog, provar)
from app.modules.learning.domain.ciclo import (EntradaInvalida, NaoEncontrado, NotaComCaraDeSegredo,
                                               TransicaoProibida)
from app.modules.learning.domain.falhas import Camada, FailureKind
from app.modules.learning.domain.vocabulario import EstadoDoBacklog, TipoDeProposta
from app.modules.learning.infrastructure.montagem import montar_aprendizado
from app.modules.learning.infrastructure import relatorio_sql
from app.modules.learning.infrastructure.relatorio_sql import FontesDeFalhaSql
from app.util import to_iso

from .fake_skills import banco as banco_migrado

AGORA = datetime.now(UTC).replace(microsecond=0)
PACOTE = "com.instagram.android"
PRECOS = {"modelo-x": [1.0, 0.1, 1.25, 5.0]}
E = EstadoDoBacklog


def iso(quando: datetime) -> str:
    return to_iso(quando)


# ------------------------------------------------------------------ semente
@dataclass
class T:
    """Uma tentativa: status, erro, chamadas de IA (cada uma com `usd`), tipo GRAVADO (None = legado) e tela."""

    status: str
    erro: str | None = None
    chamadas: int = 0
    usd: float = 0.01
    tipo: str | None = None
    tela: str | None = None
    minutos: float = 1.0


@dataclass
class Etapa:
    chave: str
    acao: str | None
    status: str
    tentativas: list[T] = field(default_factory=list)
    driven_by: str | None = "ai"
    verificada: bool | None = None
    template_hash: str | None = None


def semear(db: Database, run_id: str, quando: datetime, etapas: list[Etapa], *, simulated: bool = False,
           instancia: str = "android-06", app_id: str = "instagram", flow_id: str | None = None) -> None:
    if db.one("SELECT id FROM apps WHERE id=?", (app_id,)) is None:
        db.execute("INSERT INTO apps(id, name, package, builtin) VALUES (?,?,?,0)",
                   (app_id, app_id, PACOTE if app_id == "instagram" else f"pkg.{app_id}"))
    db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at,"
               " app_ids, flow_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
               (run_id, run_id, "abrir o perfil", "execute", "completed", int(simulated), json.dumps([instancia]),
                iso(quando), json.dumps([app_id]), flow_id))
    objetivo = f"{run_id}:{instancia}"
    db.execute("INSERT INTO objectives(id, run_id, instance_id, status) VALUES (?,?,?,?)",
               (objetivo, run_id, instancia, "succeeded"))
    relogio = quando
    for seq, e in enumerate(etapas):
        sid = f"{objetivo}:v1:{e.chave}"
        resultado = None if e.verificada is None else json.dumps({"verified": e.verificada, "detail": "x"})
        db.execute("INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal,"
                   " postcondition, timeout_s, max_attempts, status, capability, app_id, driven_by, started_at,"
                   " finished_at, result, template_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (sid, run_id, objetivo, instancia, 1, seq, e.chave, e.chave, e.chave, "{}", 60, 3, e.status,
                    e.acao, None, e.driven_by, iso(relogio), iso(relogio + timedelta(minutes=30)), resultado,
                    e.template_hash))
        for n, t in enumerate(e.tentativas, start=1):
            aid = f"{sid}:a{n}"
            inicio, fim = relogio, relogio + timedelta(minutes=t.minutos)
            db.execute("INSERT INTO attempts(id, step_id, number, status, started_at, finished_at, error,"
                       " failure_kind, failure_screen) VALUES (?,?,?,?,?,?,?,?,?)",
                       (aid, sid, n, t.status, iso(inicio), iso(fim), t.erro, t.tipo, t.tela))
            for _ in range(t.chamadas):
                db.execute("INSERT INTO ai_calls(ts, run_id, objective_id, step_id, role, model, tier,"
                           " input_tokens, output_tokens, attempt_id, usd, ok, error_kind)"
                           " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (iso(fim - timedelta(seconds=5)), run_id, objetivo, sid, "decide", "modelo-x", 0, 1000,
                            100, aid, t.usd, 1, None))
            relogio = fim + timedelta(seconds=10)


def falhando(run_id: str, quando: datetime, db: Database, *, acao: str = "OPEN_POST", erro: str,
             n: int = 1, status_final: str = "failed", chamadas: int = 0, tela: str | None = None,
             tipo: str | None = None, simulated: bool = False, instancia: str = "android-06") -> None:
    """Uma execução com UMA etapa que falhou `n` vezes com o mesmo erro."""
    semear(db, run_id, quando, [Etapa("abrir", acao, status_final,
                                      [T("failed", erro, chamadas=chamadas, tela=tela, tipo=tipo)
                                       for _ in range(n)])], simulated=simulated, instancia=instancia)


def sucesso(run_id: str, quando: datetime, db: Database, *, acao: str = "OPEN_POST", n_etapas: int = 1) -> None:
    semear(db, run_id, quando, [Etapa(f"e{i}", acao, "succeeded", [T("succeeded", chamadas=1)])
                                for i in range(n_etapas)])


def sinal(db: Database, kind: str, source_ref: str, quando: datetime, *, verdict: str | None = None,
          verificada: int | None = None, capability: str = "OPEN_POST", attempt_id: str | None = None,
          step_id: str | None = None, run_id: str | None = None, created_by: str = "flavio",
          simulated: int = 0, data: str = "{}") -> None:
    db.execute("INSERT INTO learning_signals(kind, polarity, verdict, source_ref, created_by, run_id, attempt_id,"
               " step_id, app_package, capability, step_verified, data, simulated, created_at)"
               " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (kind, "negative", verdict, source_ref, created_by, run_id, attempt_id, step_id, PACOTE, capability,
                verificada, data, simulated, iso(quando)))


class Relogio:
    def __init__(self) -> None:
        self.agora = AGORA

    def __call__(self) -> datetime:
        return self.agora


@dataclass
class Mundo:
    db: Database
    relogio: Relogio
    livro: LearningService
    falhas: ServicoDeFalhas


def montar(db: Database, *, backlog: BacklogCfg | None = None, commit: str | None = "abc1234def") -> Mundo:
    relogio = Relogio()
    cfg = LearningCfg(backlog=backlog or BacklogCfg())
    livro = montar_aprendizado(db, config=lambda: cfg, retencao_de_logs_dias=lambda: 14, precos=lambda: PRECOS,
                               relogio=relogio, commit=lambda: commit)
    falhas = livro.extensao(ServicoDeFalhas)
    assert falhas is not None, "a montagem pendura o serviço de falhas no LearningService"
    return Mundo(db, relogio, livro, falhas)


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    d = banco_migrado(tmp_path, "backlog.sqlite3")
    yield d
    d.close()


@pytest.fixture
def mundo(db: Database) -> Mundo:
    return montar(db)


def dias(n: float) -> datetime:
    return AGORA - timedelta(days=n)


def grupo(m: Mundo, tipo: str, *, acao: str = "OPEN_POST", tela: str = "", **kw: object) -> object:
    rel = m.falhas.relatorio(**kw)  # type: ignore[arg-type]
    for linha in rel.itens:
        c = linha.grupo.chave
        if (c.tipo, c.capability, c.tela) == (tipo, acao, tela):
            return linha
    return None


# ------------------------------------------------------------------ domínio: chave e id
def test_chave_canonica_e_id_estavel() -> None:
    c = chave_do_grupo(None, None, "alvo_ausente", None)
    assert c == ChaveDoGrupo(QUALQUER, QUALQUER, "alvo_ausente", "")
    assert chave_do_grupo("", "", "alvo_ausente", "") == c                      # vazio e nulo são o mesmo grupo
    assert c.cluster_key == "*|*|alvo_ausente|"
    assert c.id == "fk-" + hashlib.sha1(b"*|*|alvo_ausente|").hexdigest()[:10] == id_do_backlog(c.cluster_key)
    outra = chave_do_grupo(PACOTE, "OPEN_POST", "alvo_ausente", "feed")
    assert outra.cluster_key == f"{PACOTE}|OPEN_POST|alvo_ausente|feed" and outra.id != c.id
    # Todo tipo tem rótulo (o título nunca cai num KeyError), inclusive os da verificação.
    assert set(ROTULO) >= {k.value for k in FailureKind} | {k.value for k in TipoDeVerificacao}


def test_prova_pura_faltam_corrigido_e_reabre() -> None:
    regras = RegrasDoBacklog(prova_minimo=10, prova_fator=0.5)
    base = Medida(desde="a", ate="b", elegiveis=20, ocorrencias=10, ids=())                   # taxa 0,5
    pouco = provar(E.FIXED_PENDING_PROOF, base, Medida("b", "c", 6, 0, ()), regras)
    assert (pouco.veredito, pouco.novo_estado, pouco.faltam) == (Veredito.FALTAM, None, 4)
    bom = provar(E.FIXED_PENDING_PROOF, base, Medida("b", "c", 12, 3, ()), regras)            # 0,25 ≤ 0,25
    assert (bom.veredito, bom.novo_estado, bom.faltam) == (Veredito.CORRIGIDO, E.FIXED, 0)
    ruim = provar(E.FIXED_PENDING_PROOF, base, Medida("b", "c", 10, 3, ()), regras)           # 0,3 > 0,25
    assert (ruim.veredito, ruim.novo_estado) == (Veredito.REABRE, E.REOPENED)
    assert provar(E.FIXED, base, Medida("b", "c", 12, 3, ()), regras).novo_estado is None     # segue corrigido
    assert provar(E.FIXED, base, Medida("b", "c", 10, 5, ()), regras).novo_estado is E.REOPENED
    # Sem base (nenhuma tentativa elegível antes): só zero ocorrência prova — incerteza nunca conta como sucesso.
    sem_base = Medida("a", "b", 0, 0, ())
    assert sem_base.taxa is None
    assert provar(E.FIXED_PENDING_PROOF, sem_base, Medida("b", "c", 10, 0, ()), regras).novo_estado is E.FIXED
    assert provar(E.FIXED_PENDING_PROOF, sem_base, Medida("b", "c", 10, 1, ()), regras).novo_estado is E.REOPENED
    with pytest.raises(ValueError):
        provar(E.OPEN, base, Medida("b", "c", 10, 0, ()), regras)


# ------------------------------------------------------------------ agrupamento e retroativo
def test_agrupa_pela_cluster_key_com_o_pacote_e_a_tela(mundo: Mundo) -> None:
    db = mundo.db
    for i in range(3):
        falhando(f"r-a{i}", dias(1 + i * 0.1), db, erro=f"Pós-condição não comprovada: tentativa {i}")
    for i in range(3):   # o mesmo tipo em outra tela é outro grupo
        falhando(f"r-b{i}", dias(2 + i * 0.1), db, erro="Pós-condição não comprovada", tela="feed",
                 tipo="pos_condicao_nao_comprovada")
    rel = mundo.falhas.relatorio(dias=14)
    chaves = {linha.grupo.chave for linha in rel.itens}
    assert chaves == {ChaveDoGrupo(PACOTE, "OPEN_POST", "pos_condicao_nao_comprovada", ""),
                      ChaveDoGrupo(PACOTE, "OPEN_POST", "pos_condicao_nao_comprovada", "feed")}
    sem_tela = next(x for x in rel.itens if x.grupo.chave.tela == "")
    assert (sem_tela.grupo.ocorrencias, sem_tela.grupo.execucoes, sem_tela.grupo.etapas) == (3, 3, 3)
    assert sem_tela.grupo.camada is Camada.VERIFICACAO
    assert sem_tela.grupo.id == id_do_backlog(f"{PACOTE}|OPEN_POST|pos_condicao_nao_comprovada|")
    assert "pós-condição não comprovada" in sem_tela.grupo.titulo
    assert sem_tela.estado is E.OPEN and not sem_tela.registrado
    # A chave é estável: a curadoria grava a mesma que o relatório mostra.
    assert mundo.falhas.executar(AGORA) >= 2
    gravadas = {r["id"]: r for r in db.query("SELECT * FROM learning_backlog WHERE category='falha'")}
    assert sem_tela.grupo.id in gravadas
    assert gravadas[sem_tela.grupo.id]["cluster_key"] == sem_tela.grupo.chave.cluster_key
    assert gravadas[sem_tela.grupo.id]["failure_screen"] == "" and gravadas[sem_tela.grupo.id]["app_package"] == PACOTE
    depois = mundo.falhas.relatorio(dias=14)
    assert all(x.registrado for x in depois.itens)


def test_legado_classificado_como_retroativo_sem_gravar(mundo: Mundo) -> None:
    db = mundo.db
    for i in range(3):
        falhando(f"r-leg{i}", dias(1 + i * 0.1), db, erro="O app parou de responder (ANR) na tela")
    falhando("r-novo", dias(0.5), db, erro="O app parou de responder (ANR)", tipo="app_anr")
    linha = grupo(mundo, "app_anr", dias=14)
    assert linha is not None and linha.grupo.ocorrencias == 4 and linha.grupo.retroativas == 3   # type: ignore[attr-defined]
    mundo.falhas.executar(AGORA)
    assert db.scalar("SELECT COUNT(*) FROM attempts WHERE failure_kind IS NULL") == 3    # nada gravado no legado
    # Sem o retroativo, só o que a execução classificou (e ele não passa do mínimo de 3).
    so_gravado = mundo.falhas.relatorio(dias=14, retroativo=False)
    assert so_gravado.itens == () and so_gravado.abaixo_do_minimo == 1


# ------------------------------------------------------------------ pontuação e ordem
def test_pontuacao_com_usd_por_tentativa_e_ordem_pelo_custo(db: Database) -> None:
    m = montar(db, backlog=BacklogCfg(pessoa_usd=0.25, aparelho_usd_min=0.0))
    # Caro: 3 tentativas com 5 chamadas de US$ 0,02 cada (US$ 0,30), e a etapa que deu certo depois NÃO entra.
    for i in range(3):
        semear(db, f"r-caro{i}", dias(1 + i * 0.1), [Etapa("abrir", "OPEN_POST", "succeeded", [
            T("failed", "Ciclo sem progresso: 3 ações iguais", chamadas=5, usd=0.02),
            T("succeeded", chamadas=7, usd=0.5)])])
    # Barato mas com gente: 3 falhas finais em waiting_user (intervenção), sem IA. Depois da primeira chamada de IA:
    # tentativa real ANTES dela é o sinal de que a purga já levou chamadas (a fronteira da régua do A1).
    for i in range(3):
        falhando(f"r-gente{i}", dias(0.5 + i * 0.1), db, acao="LIKE_POST", erro="O app pede autenticação (senha).",
                 status_final="waiting_user")
    rel = m.falhas.relatorio(dias=14)
    caro = next(x.grupo for x in rel.itens if x.grupo.chave.tipo == "ciclo_sem_progresso")
    gente = next(x.grupo for x in rel.itens if x.grupo.chave.tipo == "autenticacao")
    assert abs(caro.usd_perdido - 0.30) < 1e-9 and caro.intervencoes == 0
    assert abs(caro.min_perdidos - 3.0) < 1e-9
    assert gente.usd_perdido == 0 and gente.intervencoes == 3
    regras = RegrasDoBacklog(pessoa_usd=0.25)
    assert abs(caro.custo_total(regras) - 0.30) < 1e-9 and abs(gente.custo_total(regras) - 0.75) < 1e-9
    assert [x.grupo.chave.tipo for x in rel.itens] == ["autenticacao", "ciclo_sem_progresso"]
    # As três colunas saem separadas; o custo total só ordena.
    m2 = montar(db, backlog=BacklogCfg(pessoa_usd=0.0))
    assert [x.grupo.chave.tipo for x in m2.falhas.relatorio(dias=14).itens][0] == "ciclo_sem_progresso"


def test_falso_positivo_sempre_no_topo_e_a_verificacao_sai_inteira(mundo: Mundo) -> None:
    db = mundo.db
    for i in range(3):   # caro
        falhando(f"r-caro{i}", dias(1 + i * 0.1), db, erro="Ciclo sem progresso", chamadas=20)
    for i in range(3):   # 'deu errado' em etapa que o verificador comprovou: sucesso mascarado, sem custo nenhum
        sinal(db, "feedback", f"run:r-fp{i}", dias(1 + i * 0.1), verdict="errado", verificada=1)
    sinal(db, "feedback", "run:r-fn", dias(1), verdict="certo", verificada=0)          # falso negativo: só 1
    sinal(db, "confirmou_a_mao", "resolve:o1:1", dias(1), capability="LIKE_POST")        # lacuna: só 1
    sinal(db, "feedback", "run:r-ok", dias(1), verdict="certo", verificada=1)           # concordou: não é falha
    rel = mundo.falhas.relatorio(dias=14)
    assert rel.itens[0].grupo.chave.tipo == TipoDeVerificacao.FALSO_POSITIVO.value
    assert rel.itens[0].grupo.camada is Camada.VERIFICACAO and rel.itens[0].grupo.usd_perdido == 0
    assert TipoDeVerificacao.FALSO_NEGATIVO.value not in {x.grupo.chave.tipo for x in rel.itens}   # mínimo de 3
    verificacao = {g.chave.tipo: g.ocorrencias for g in rel.verificacao}
    assert verificacao == {TipoDeVerificacao.FALSO_POSITIVO.value: 3, TipoDeVerificacao.FALSO_NEGATIVO.value: 1,
                           TipoDeVerificacao.CONFIRMOU_A_MAO.value: 1}
    lacuna = next(g for g in rel.verificacao if g.chave.tipo == TipoDeVerificacao.CONFIRMOU_A_MAO.value)
    assert lacuna.intervencoes == 1 and lacuna.chave.capability == "LIKE_POST"


def test_minimo_de_tres_ocorrencias_e_camada_pessoa_fora(mundo: Mundo) -> None:
    db = mundo.db
    for i in range(2):
        falhando(f"r-dois{i}", dias(1 + i * 0.1), db, erro="Alvo ausente na tela")
    for i in range(3):
        falhando(f"r-pessoa{i}", dias(1 + i * 0.1), db, acao="SEND_DM", erro="Parâmetro ausente: destinatário")
    rel = mundo.falhas.relatorio(dias=14)
    assert rel.itens == () and rel.abaixo_do_minimo == 1                # pessoa não conta nem como abaixo
    so_pessoa = mundo.falhas.relatorio(dias=14, camada=Camada.PESSOA)
    assert [x.grupo.chave.tipo for x in so_pessoa.itens] == ["falta_informacao"]
    mundo.falhas.executar(AGORA)
    assert db.scalar("SELECT COUNT(*) FROM learning_backlog WHERE category='falha'") == 0


def test_tendencia_sete_contra_sete(mundo: Mundo) -> None:
    db = mundo.db
    for i in range(3):
        falhando(f"r-recente{i}", dias(1 + i), db, erro="Alvo ausente")
    falhando("r-antiga", dias(10), db, erro="Alvo ausente")
    # dias=7: as métricas contam a janela, mas a tendência compara sempre as duas semanas.
    linha = grupo(mundo, "alvo_ausente", dias=7)
    assert linha is not None
    g = linha.grupo  # type: ignore[attr-defined]
    assert g.ocorrencias == 3 and (g.tendencia.ultimos_7d, g.tendencia.anteriores_7d) == (3, 1)
    assert g.tendencia.direcao == "subindo"


def test_outro_simulado_exemplos_redigidos_e_intervencao_por_sinal(mundo: Mundo) -> None:
    db = mundo.db
    segredo = "Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123456789"
    for i in range(3):
        falhando(f"r-x{i}", dias(1 + i * 0.1), db, erro=f"erro esquisito {i} " + segredo + " " + "y" * 400)
    for i in range(3):
        falhando(f"r-sim{i}", dias(1), db, erro="Alvo ausente", simulated=True)
    sinal(db, "tomou_controle", "takeover:r-x0:android-06:v1:abrir:a1", dias(1),
          attempt_id="r-x0:android-06:v1:abrir:a1")
    rel = mundo.falhas.relatorio(dias=14)
    assert [x.grupo.chave.tipo for x in rel.itens] == ["outro"]
    outro = rel.itens[0].grupo
    assert outro.camada is Camada.INDEFINIDA and outro.intervencoes == 1
    assert len(outro.exemplos) == 3 and all(len(e.erro or "") <= 200 for e in outro.exemplos)
    assert all("abcdefghijklmnopqrstuvwxyz0123456789" not in (e.erro or "") for e in outro.exemplos)
    assert outro.exemplos[0].attempt_id and outro.exemplos[0].run_id
    assert (rel.outro.ocorrencias, rel.outro.total) == (3, 3) and rel.outro.alerta
    com_simulado = mundo.falhas.relatorio(dias=14, simulados=True)
    assert "alvo_ausente" in {x.grupo.chave.tipo for x in com_simulado.itens}


# ------------------------------------------------------------------ régua durável além de ai_calls
def test_custo_alem_da_janela_de_ai_calls_vem_do_diario(mundo: Mundo) -> None:
    db = mundo.db
    for i in range(3):   # 20 dias atrás, com custo; o diário do dia foi gravado quando o dia estava inteiro
        falhando(f"r-velha{i}", dias(20) + timedelta(minutes=i * 5), db, erro="Alvo ausente", chamadas=2)
    dia = iso(dias(20))[:10]
    fim = (dias(20) + timedelta(days=1)).strftime("%Y-%m-%d")
    assert db.scalar("SELECT COUNT(*) FROM learning_daily") == 0
    from app.modules.learning.infrastructure.sql_repository import SqlLearningRepository
    SqlLearningRepository(db, precos=lambda: PRECOS).recalcular_diario(dia, fim)
    antes = next(x.grupo for x in mundo.falhas.relatorio(dias=30).itens if x.grupo.chave.tipo == "alvo_ausente")
    assert abs(antes.usd_perdido - 0.06) < 1e-9
    # A purga de 14 dias leva as chamadas velhas; uma tentativa real recente mantém o sinal de "já purgado".
    db.execute("DELETE FROM ai_calls WHERE ts < ?", (iso(dias(14)),))
    sucesso("r-hoje", dias(0.5), db)
    rel = mundo.falhas.relatorio(dias=30)
    depois = next(x.grupo for x in rel.itens if x.grupo.chave.tipo == "alvo_ausente")
    assert abs(depois.usd_perdido - 0.06) < 1e-9 and not rel.janela.custo_parcial
    # Um dia velho SEM linha no diário: o custo dele é desconhecido, e o relatório diz.
    for i in range(3):
        falhando(f"r-sem-diario{i}", dias(18) + timedelta(minutes=i), db, erro="Alvo ausente")
    assert mundo.falhas.relatorio(dias=30).janela.custo_parcial


def test_banco_nunca_purgado_com_primeira_tentativa_sem_ia_nao_vira_custo_parcial(mundo: Mundo) -> None:
    """O caso do central (17/09): a primeira tentativa real falhou SEM chamada de IA, então "há tentativa antes da
    primeira chamada" — o sinal de purga da régua — é falso. Os eventos sem execução são purgados com o MESMO corte
    de `ai_calls` e existem todo dia: o mais antigo deles é a testemunha de até onde nada foi purgado."""
    db = mundo.db
    db.execute("INSERT INTO events(ts, kind, level, message) VALUES (?,?,?,?)",
               (iso(dias(3)), "instance.updated", "info", "boot"))
    for i in range(3):
        falhando(f"r-sem-ia{i}", dias(2.5 + i * 0.01), db, erro="Sessão de automação indisponível", chamadas=0)
    for i in range(3):
        falhando(f"r-com-ia{i}", dias(1 + i * 0.1), db, erro="Alvo ausente", chamadas=2)
    rel = mundo.falhas.relatorio(dias=14)
    alvo = next(x.grupo for x in rel.itens if x.grupo.chave.tipo == "alvo_ausente")
    sessao = next(x.grupo for x in rel.itens if x.grupo.chave.tipo == "sessao_de_automacao")
    assert abs(alvo.usd_perdido - 0.06) < 1e-9 and sessao.usd_perdido == 0
    assert not rel.janela.custo_parcial and not sessao.custo_parcial
    # Sem a testemunha, a régua do A1 acharia que houve purga, e o custo desses dias seria desconhecido — nunca zero.
    db.execute("DELETE FROM events")
    sem_testemunha = mundo.falhas.relatorio(dias=14)
    assert sem_testemunha.janela.custo_parcial


def test_chamada_anterior_a_045_vai_para_a_tentativa_em_andamento(mundo: Mundo) -> None:
    """Antes da 045 a chamada só tem `step_id`: ela é da tentativa que estava em andamento no instante dela (a última
    que começou antes), não da última tentativa da etapa — senão a falha que precedeu o sucesso sairia de graça."""
    db = mundo.db
    for i in range(3):
        semear(db, f"r-045-{i}", dias(1 + i * 0.1), [Etapa("abrir", "OPEN_POST", "succeeded", [
            T("failed", "Alvo ausente", chamadas=4, usd=0.05), T("succeeded", chamadas=1, usd=0.5)])])
    db.execute("UPDATE ai_calls SET attempt_id=NULL")
    alvo = next(x.grupo for x in mundo.falhas.relatorio(dias=14).itens if x.grupo.chave.tipo == "alvo_ausente")
    assert abs(alvo.usd_perdido - 0.60) < 1e-9


def test_diario_recalculado_pela_curadoria_e_idempotente(mundo: Mundo) -> None:
    db = mundo.db
    for i in range(3):
        falhando(f"r-d{i}", dias(1 + i * 0.3), db, erro="Alvo ausente", chamadas=1)
    assert mundo.livro.curar().falhas == ()
    primeira = sorted(tuple(sorted((k, v) for k, v in r.items() if k != "computed_at"))
                      for r in db.query("SELECT * FROM learning_daily"))
    assert mundo.livro.curar().falhas == ()
    segunda = sorted(tuple(sorted((k, v) for k, v in r.items() if k != "computed_at"))
                     for r in db.query("SELECT * FROM learning_daily"))
    assert primeira == segunda and primeira
    assert db.scalar("SELECT COUNT(*) FROM learning_backlog") == 1              # o passo do backlog rodou junto


# ------------------------------------------------------------------ prova da correção
def _base_e_correcao(m: Mundo, *, ruins_antes: int = 5, total_antes: int = 10) -> str:
    """10 tentativas elegíveis de OPEN_POST nas 4 semanas antes da correção, 5 com alvo ausente (taxa 0,5)."""
    db = m.db
    for i in range(total_antes):
        if i < ruins_antes:
            falhando(f"r-base{i}", dias(10 + i * 0.1), db, erro="Alvo ausente")
        else:
            sucesso(f"r-base{i}", dias(10 + i * 0.1), db)
    m.falhas.executar(AGORA)
    linha = grupo(m, "alvo_ausente", dias=14)
    assert linha is not None
    fk = linha.grupo.id  # type: ignore[attr-defined]
    corrigida = m.falhas.alterar(fk, estado=E.FIXED_PENDING_PROOF, fixed_in_commit="ABC1234", by="sessao-dev")
    assert corrigida.state is E.FIXED_PENDING_PROOF and corrigida.fixed_in_commit == "abc1234"
    assert corrigida.fixed_at == iso(AGORA) and corrigida.updated_by == "sessao-dev"
    assert corrigida.baseline is not None and corrigida.baseline["elegiveis"] == total_antes
    assert corrigida.baseline["ocorrencias"] == ruins_antes and corrigida.baseline["taxa"] == 0.5
    assert corrigida.verification is not None and corrigida.verification["commit_implantado"] == "abc1234def"
    return fk


def _depois(m: Mundo, *, ruins: int, bons: int, a_partir: datetime) -> None:
    for i in range(ruins):
        falhando(f"r-dep-ruim{i}-{a_partir.timestamp()}", a_partir + timedelta(minutes=i * 5), m.db,
                 erro="Alvo ausente")
    for i in range(bons):
        sucesso(f"r-dep-bom{i}-{a_partir.timestamp()}", a_partir + timedelta(minutes=100 + i * 5), m.db)


def test_prova_da_correcao_marca_fixed_com_dez_elegiveis(mundo: Mundo) -> None:
    fk = _base_e_correcao(mundo)
    mundo.relogio.agora = AGORA + timedelta(days=2)
    _depois(mundo, ruins=1, bons=5, a_partir=AGORA + timedelta(hours=1))
    mundo.falhas.executar(mundo.relogio.agora)
    parcial = mundo.falhas.linha(fk).linha
    assert parcial.state is E.FIXED_PENDING_PROOF and parcial.verification is not None
    assert parcial.verification["faltam"] == 4 and parcial.verification["elegiveis"] == 6
    _depois(mundo, ruins=1, bons=4, a_partir=AGORA + timedelta(days=1))
    mundo.falhas.executar(mundo.relogio.agora)
    provada = mundo.falhas.linha(fk).linha
    assert provada.state is E.FIXED and provada.verification is not None
    assert provada.verification["elegiveis"] == 11 and provada.verification["ocorrencias"] == 2
    assert provada.verification["faltam"] == 0 and provada.verification["limite"] == 0.25
    assert provada.verification["provado_em"] == iso(mundo.relogio.agora)
    ids = provada.verification["ids"]
    assert isinstance(ids, list) and ids and all(isinstance(i, str) for i in ids)
    trilha = mundo.db.query("SELECT state, updated_by FROM learning_backlog WHERE id=?", (fk,))
    assert trilha[0]["updated_by"] == "sistema"
    # Reincidência depois de corrigido também reabre (medida desde a prova, não declaração).
    mundo.relogio.agora = AGORA + timedelta(days=6)
    _depois(mundo, ruins=6, bons=4, a_partir=AGORA + timedelta(days=3))
    mundo.falhas.executar(mundo.relogio.agora)
    reaberta = mundo.falhas.linha(fk).linha
    assert reaberta.state is E.REOPENED and reaberta.reopened_count == 1


def test_prova_da_correcao_reabre_acima_da_metade_da_base(mundo: Mundo) -> None:
    fk = _base_e_correcao(mundo)
    mundo.relogio.agora = AGORA + timedelta(days=2)
    _depois(mundo, ruins=4, bons=6, a_partir=AGORA + timedelta(hours=1))       # 0,4 > 0,25
    mundo.falhas.executar(mundo.relogio.agora)
    reaberta = mundo.falhas.linha(fk).linha
    assert reaberta.state is E.REOPENED and reaberta.reopened_count == 1
    assert reaberta.verification is not None and reaberta.verification["taxa"] == 0.4


def test_reincidencia_concentrada_depois_de_muitas_boas_reabre(mundo: Mundo) -> None:
    """Depois de `fixed`, a reincidência é medida nas últimas 2 × `prova_minimo` tentativas elegíveis (janela que
    anda), não desde a prova: as muitas boas depois dela não diluem a volta da falha. Achado do revisor sobre 2adae8c
    (a janela [provado_em, agora) deixava a linha `fixed` com 8 falhas em 10 no último dia: 11,4% em 70)."""
    fk = _base_e_correcao(mundo)
    mundo.relogio.agora = AGORA + timedelta(days=2)
    _depois(mundo, ruins=0, bons=10, a_partir=AGORA + timedelta(hours=1))
    mundo.falhas.executar(mundo.relogio.agora)
    assert mundo.falhas.linha(fk).linha.state is E.FIXED
    for i in range(60):                                   # a correção funcionando por quase duas semanas...
        sucesso(f"r-bom-{i}", AGORA + timedelta(days=3, hours=i * 4), mundo.db)
    mundo.relogio.agora = AGORA + timedelta(days=15)
    mundo.falhas.executar(mundo.relogio.agora)
    seguindo = mundo.falhas.linha(fk).linha
    assert seguindo.state is E.FIXED and seguindo.verification is not None
    medida = seguindo.verification["reincidencia"]
    assert isinstance(medida, dict)
    assert (medida["elegiveis"], medida["ocorrencias"], medida["veredito"]) == (20, 0, "corrigido")
    assert medida["janela_de_tentativas"] == 20
    # ...e a falha volta forte no último dia: 8 em 10 (80% > 25%).
    mundo.relogio.agora = AGORA + timedelta(days=20)
    for i in range(8):
        falhando(f"r-volta-{i}", AGORA + timedelta(days=19, minutes=i * 10), mundo.db, erro="Alvo ausente")
    for i in range(2):
        sucesso(f"r-volta-ok-{i}", AGORA + timedelta(days=19, hours=5, minutes=i * 10), mundo.db)
    mundo.falhas.executar(mundo.relogio.agora)
    reaberta = mundo.falhas.linha(fk).linha
    assert reaberta.state is E.REOPENED and reaberta.reopened_count == 1
    assert reaberta.verification is not None
    medida = reaberta.verification["reincidencia"]
    assert isinstance(medida, dict)
    assert (medida["elegiveis"], medida["ocorrencias"], medida["taxa"]) == (20, 8, 0.4)
    assert medida["veredito"] == "reabre" and medida["janela_de_tentativas"] == 20
    desde = medida["desde"]
    assert isinstance(desde, str) and desde > iso(AGORA + timedelta(days=3))      # a janela andou
    assert reaberta.verification["provado_em"] == iso(AGORA + timedelta(days=2))   # a prova fica registrada


@pytest.mark.parametrize("lote", [100, 1])
def test_inicio_das_ultimas_elegiveis_da_app_e_acao(db: Database, lote: int, monkeypatch: pytest.MonkeyPatch) -> None:
    """O início da janela da reincidência: a n-ésima tentativa elegível mais recente da (app, ação), no mesmo filtro
    do denominador (real, não cancelada, app pelo pacote, `*` = etapa livre); `None` com menos de n. Com lote 1, a
    leitura atravessa páginas (a de outro app ocupa lugar na primeira)."""
    monkeypatch.setattr(relatorio_sql, "_LOTE_DA_JANELA", lote)
    fontes = FontesDeFalhaSql(db)
    for i in range(5):
        sucesso(f"r-u{i}", dias(5 - i), db)                       # 5, 4, 3, 2 e 1 dia atrás
    semear(db, "r-sim", dias(0.5), [Etapa("e", "OPEN_POST", "succeeded", [T("succeeded")])], simulated=True)
    semear(db, "r-canc", dias(0.4), [Etapa("e", "OPEN_POST", "cancelled", [T("cancelled")])])
    semear(db, "r-outra", dias(0.3), [Etapa("e", "OPEN_POST", "succeeded", [T("succeeded")])], app_id="outro")
    semear(db, "r-livre", dias(0.2), [Etapa("e", None, "succeeded", [T("succeeded")])])

    def fim(run_id: str) -> str:
        valor = db.scalar("SELECT finished_at FROM attempts WHERE id LIKE ?", (f"{run_id}:%",))
        assert isinstance(valor, str)
        return valor

    desde, ate = iso(dias(30)), iso(AGORA)
    # As 3 mais recentes são as de 1, 2 e 3 dias atrás: simulada, cancelada e de outro app não contam.
    assert fontes.inicio_das_ultimas(PACOTE, "OPEN_POST", desde, ate, n=3) == fim("r-u2")
    assert fontes.inicio_das_ultimas(PACOTE, "OPEN_POST", desde, ate, n=5) == fim("r-u0")
    assert fontes.inicio_das_ultimas(PACOTE, "OPEN_POST", desde, ate, n=6) is None            # só há 5
    assert fontes.inicio_das_ultimas(PACOTE, "OPEN_POST", iso(dias(2.5)), ate, n=3) is None   # 2 depois de `desde`
    assert fontes.inicio_das_ultimas(PACOTE, "OPEN_POST", desde, iso(dias(1.5)), n=1) == fim("r-u3")
    assert fontes.inicio_das_ultimas(PACOTE, "*", desde, ate, n=1) == fim("r-livre")
    assert fontes.inicio_das_ultimas("pkg.outro", "OPEN_POST", desde, ate, n=1) == fim("r-outra")


def test_fixed_e_reopened_nunca_por_pessoa_e_as_outras_recusas(mundo: Mundo) -> None:
    db = mundo.db
    for i in range(3):
        falhando(f"r-p{i}", dias(1 + i * 0.1), db, erro="Alvo ausente")
    fk = grupo(mundo, "alvo_ausente", dias=14).grupo.id  # type: ignore[union-attr]
    for proibido in (E.FIXED, E.REOPENED):
        with pytest.raises(TransicaoProibida):
            mundo.falhas.alterar(fk, estado=proibido, by="flavio")
    with pytest.raises(EntradaInvalida):
        mundo.falhas.alterar(fk, estado=E.FIXED_PENDING_PROOF, by="flavio")                  # sem commit
    with pytest.raises(EntradaInvalida):
        mundo.falhas.alterar(fk, estado=E.FIXED_PENDING_PROOF, fixed_in_commit="não-é-sha", by="flavio")
    with pytest.raises(NotaComCaraDeSegredo):
        mundo.falhas.alterar(fk, estado=E.TRIAGED, notes="a senha do lucas é hunter2", by="flavio")
    assert db.scalar("SELECT COUNT(*) FROM learning_backlog") == 0                      # a recusa não grava nada
    with pytest.raises(NaoEncontrado):
        mundo.falhas.alterar("fk-0000000000", estado=E.TRIAGED, by="flavio")
    # O id ainda não gravado (a curadoria roda a cada 15 min) é resolvido pelo relatório e gravado no PATCH.
    planejada = mundo.falhas.alterar(fk, estado=E.PLANNED, plan_item="20.3", notes="ver o prazo", by="flavio")
    assert (planejada.state, planejada.plan_item, planejada.notes) == (E.PLANNED, "20.3", "ver o prazo")
    assert db.scalar("SELECT state FROM learning_backlog WHERE id=?", (fk,)) == "planned"


# ------------------------------------------------------------------ propostas
def _item(db: Database, iid: str, kind: str, state: str, *, detalhe: str | None = None, dias_no_estado: float,
          contra: int = 0) -> None:
    db.execute("INSERT INTO learning_items(id, kind, state, state_detail, scope_app, scope_capability, content,"
               " content_hash, summary, source_kind, created_by, created_at, state_at, evidence_against)"
               " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (iid, kind, state, detalhe, PACOTE, "OPEN_POST", "{}", f"h-{iid}", f"resumo {iid}", "recovery",
                "sistema", iso(dias(30)), iso(dias(dias_no_estado)), contra))


def test_propostas_de_acao_licao_e_tela(mundo: Mundo) -> None:
    db = mundo.db
    for i in range(3):   # etapa livre comprovada em 3 execuções, mesmo modelo
        semear(db, f"r-livre{i}", dias(1 + i * 0.1), [Etapa("rolar_feed", None, "succeeded", [T("succeeded")],
                                                              verificada=True, template_hash="th-rolar")])
    for i in range(2):   # só 2 execuções: não propõe
        semear(db, f"r-duas{i}", dias(1 + i * 0.1), [Etapa("abrir_x", None, "succeeded", [T("succeeded")],
                                                             verificada=True, template_hash="th-duas")])
    for i in range(3):   # confirmada à mão (verified=false): não é comprovada
        semear(db, f"r-mao{i}", dias(1 + i * 0.1), [Etapa("abrir_y", None, "succeeded", [T("succeeded")],
                                                            verificada=False, template_hash="th-mao")])
    _item(db, "li-ajuda", "licao", "published", detalhe="medida:ajuda", dias_no_estado=15)
    _item(db, "li-cedo", "licao", "published", detalhe="medida:ajuda", dias_no_estado=10)
    _item(db, "li-tela", "tela", "published", dias_no_estado=8)
    _item(db, "li-tela-conflito", "tela", "published", dias_no_estado=8, contra=1)
    rel = mundo.falhas.relatorio(dias=14)
    propostas = {(p.proposta.tipo, p.proposta.ref) for p in rel.propostas}
    assert propostas == {(TipoDeProposta.ACAO_DE_CATALOGO, f"{PACOTE}|th-rolar"),
                         (TipoDeProposta.PROMOVER_LICAO, "li-ajuda"), (TipoDeProposta.PROMOVER_TELA, "li-tela")}
    acao = next(p.proposta for p in rel.propostas if p.proposta.tipo is TipoDeProposta.ACAO_DE_CATALOGO)
    assert "th-rolar" in acao.fragmento and "rolar_feed" in acao.fragmento and "3 execuções" in acao.detalhe
    mundo.falhas.executar(AGORA)
    gravadas = {r["cluster_key"] for r in db.query("SELECT cluster_key FROM learning_backlog WHERE category='proposta'")}
    assert gravadas == {f"acao_de_catalogo|{PACOTE}|th-rolar", "promover_licao|li-ajuda", "promover_tela|li-tela"}
    assert mundo.falhas.executar(AGORA) >= 0                                             # idempotente
    assert db.scalar("SELECT COUNT(*) FROM learning_backlog WHERE category='proposta'") == 3


def test_saude_do_aprendizado(mundo: Mundo) -> None:
    db = mundo.db
    semear(db, "r-rec", dias(1), [Etapa("a", "OPEN_POST", "succeeded", [T("succeeded")], driven_by="recipe"),
                                  Etapa("b", "OPEN_POST", "succeeded", [T("succeeded")], driven_by="ai"),
                                  Etapa("c", "OPEN_POST", "succeeded", [T("succeeded")], driven_by=None)],
           flow_id="fluxo-1")
    semear(db, "r-ia", dias(1), [Etapa("a", "OPEN_POST", "waiting_user", [T("failed", "O app pede autenticação")])])
    saude = mundo.falhas.relatorio(dias=14).saude
    assert saude.execucoes == 2 and saude.execucoes_com_fluxo == 1 and saude.fluxos_distintos == 1
    assert saude.etapas_por_conducao == {"recipe": 1, "ai": 2, "-": 1}         # "-": sem registro de quem conduziu
    assert saude.intervencoes == 1 and saude.intervencoes_por_10_execucoes == 5.0
    assert saude.pct_por_receita == pytest.approx(1 / 3)                        # só entre as que registraram
