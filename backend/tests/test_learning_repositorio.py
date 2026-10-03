"""Migração 055 e `SqlLearningRepository` (ADR-054), nos dois bancos (PostgreSQL quando `TEST_DATABASE_URL` existe).

O que se prova:
- ATUALIZAÇÃO de um banco na última migração anterior à 055 (calculada, não escrita: outras frentes numeram em
  paralelo), com dados: aplica só a 055, não toca o legado, e as colunas novas nascem NULAS; o esquema de um banco
  novo e de um atualizado é o mesmo; a 055 renderiza sem marca sobrando; a cópia entre bancos acha ordem por FK;
- CAS em toda transição (`ConflitoDeEstado`), e o D1 nas duas portas do repositório: o sistema não publica item com
  efeito ou texto de pessoa nem chamando o repositório direto — nem por `transicionar_item` (o próprio `UPDATE`
  recusa) nem CRIANDO o item já publicado (`criar_item`, percorrido estado × efeito × texto de pessoa × ator);
- o índice PARCIAL de item vivo (o mesmo conteúdo vivo não duplica; desligado, pode ser reaprendido);
- sinal idempotente por `(kind, source_ref, created_by)` e voto como upsert; evidência que deduplica pela origem não
  nula; `decided_by` nunca vazio;
- a régua diária durável (`learning_daily`): recalculada por inteiro e idempotente, só execução real, legado
  classificado na leitura; a chamada de IA contada uma vez só quando a etapa atravessa a meia-noite e os dias são
  calculados em intervalos separados; e a guarda que nunca reescreve um dia já mordido pela purga de `ai_calls`;
- a retenção de `aprendizado.retencao` (e o que nunca é purgado).

Nível de prova: `simulated` (banco de teste; nenhum aparelho).
"""
from __future__ import annotations

import hashlib
import itertools
import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app import db as db_mod
from app.db import INTEGRITY_ERRORS, Database
from app.modules.learning.application.ports import Ajustes, NovaEvidencia, NovoSinal, Retencao
from app.modules.learning.application.servico import LearningService
from app.modules.learning.domain.ciclo import (ESTADOS, SYSTEM_ACTOR, ConflitoDeEstado, EntradaInvalida, ExigeODono,
                                               NotaComCaraDeSegredo, SkillState, Vetado)
from app.modules.learning.domain.livro import Escopo, NovoItem
from app.modules.learning.domain.vocabulario import LivroKind, Polaridade, Posicao, SignalKind, SourceKind
from app.modules.learning.infrastructure.fontes import FontesSql
from app.modules.learning.infrastructure.segredo import TriagemDeCredencial
from app.modules.learning.infrastructure.sql_repository import SqlLearningRepository
from app.tools.migrate_data import _dependencias, ordem_por_fk, tabelas_a_copiar
from app.util import to_iso

from .fake_skills import banco as banco_migrado
from .test_db import _banco, _copia_das_migracoes, _Falso

NOVA = "055_aprendizado_continuo"
ORIGEM = Path(db_mod.__file__).resolve().parents[1] / "migrations"
TABELAS = ("learning_items", "learning_transitions", "learning_signals", "learning_evidence", "learning_exposures",
           "learning_daily", "learning_backlog")
INDICES = ("ix_attempts_failure_kind", "ux_learning_items_vivo", "ix_learning_items_consumo",
           "ix_learning_transitions_item", "ix_learning_transitions_hash", "ux_learning_signals",
           "ix_learning_signals_grupo", "ix_learning_signals_run", "ux_learning_evidence", "ix_learning_evidence_item",
           "ix_learning_exposures_run", "ix_learning_backlog_estado")
# Meio-dia fixo: a semente anda minutos a partir de AGORA e o diário corta por dia; com o relógio real, uma suíte
# perto da meia-noite UTC (suíte 5b, 03/10) punha parte das chamadas no dia seguinte e o teste falhava.
AGORA = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
PRECOS = {"modelo-x": [1.0, 0.1, 1.25, 5.0]}


def iso(dias_atras: float, *, horas: float = 0.0, agora: datetime = AGORA) -> str:
    return to_iso(agora - timedelta(days=dias_atras, hours=horas))


# ------------------------------------------------------------------ semente de execução (usada também na projeção)
def semear_execucao(db: Database, run_id: str, *, dias_atras: float, simulated: bool = False,
                    etapas: list[tuple[str, str | None, str, list[tuple[str, str | None]], int]] | None = None,
                    app_id: str = "instagram", agora: datetime = AGORA) -> None:
    """Uma execução com etapas; cada etapa = (chave, ação, status final, [(status, erro) por tentativa], chamadas de IA
    na ÚLTIMA tentativa). Horários `dias_atras` antes de `agora`; as chamadas vão com `attempt_id` e `usd` gravado.

    `agora` é o AGORA fixo por padrão. Quem lê a semente pelo relógio REAL (a projeção corta por `datetime.now`) passa o
    relógio real: com o fixo, a semente envelhece a cada dia e sai da janela (a retenção de 3 dias caiu em 03/10)."""
    if db.one("SELECT id FROM apps WHERE id=?", (app_id,)) is None:
        db.execute("INSERT INTO apps(id, name, package, builtin) VALUES (?,?,?,0)",
                   (app_id, app_id, "com.instagram.android" if app_id == "instagram" else f"pkg.{app_id}"))
    inicio = iso(dias_atras, agora=agora)
    db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at,"
               " app_ids) VALUES (?,?,?,?,?,?,?,?,?)",
               (run_id, run_id, "abrir o perfil", "execute", "completed", int(simulated), '["android-06"]', inicio,
                json.dumps([app_id])))
    objetivo = f"{run_id}:android-06"
    db.execute("INSERT INTO objectives(id, run_id, instance_id, status) VALUES (?,?,?,?)",
               (objetivo, run_id, "android-06", "succeeded"))
    for seq, (chave, acao, status, tentativas, chamadas) in enumerate(etapas or []):
        sid = f"{objetivo}:v1:{chave}"
        db.execute("INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal,"
                   " postcondition, timeout_s, max_attempts, status, capability, app_id, driven_by, started_at,"
                   " finished_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (sid, run_id, objetivo, "android-06", 1, seq, chave, chave, chave, "{}", 60, 3, status, acao, None,
                    "ai", inicio, iso(dias_atras, horas=-0.1, agora=agora)))
        for n, (st, erro) in enumerate(tentativas, start=1):
            aid = f"{sid}:a{n}"
            db.execute("INSERT INTO attempts(id, step_id, number, status, started_at, finished_at, error)"
                       " VALUES (?,?,?,?,?,?,?)",
                       (aid, sid, n, st, inicio, iso(dias_atras, horas=-0.05, agora=agora), erro))
            if n == len(tentativas):
                for _ in range(chamadas):
                    db.execute("INSERT INTO ai_calls(ts, run_id, objective_id, step_id, role, model, tier,"
                               " input_tokens, output_tokens, attempt_id, usd) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                               (iso(dias_atras, horas=-0.01, agora=agora), run_id, objetivo, sid, "decide",
                                "modelo-x", 0, 1000, 100, aid, 0.01))


@pytest.fixture
def db(tmp_path: Path) -> Database:
    d = banco_migrado(tmp_path, "aprendizado.sqlite3")
    yield d
    d.close()


def repo(db: Database) -> SqlLearningRepository:
    return SqlLearningRepository(db, precos=lambda: PRECOS)


def novo_item(conteudo: str = "tocar em [row_x]", *, efeito: bool = False,
              fonte: SourceKind = SourceKind.RECOVERY) -> NovoItem:
    return NovoItem(kind=LivroKind.LICAO, escopo=Escopo(app="com.instagram.android", capability="OPEN_POST",
                                                        role="actor"),
                    content={"modelo": "alvo_ausente", "alvo": conteudo}, summary=f"Em OPEN_POST: {conteudo}.",
                    source_kind=fonte, side_effect=efeito, app_version="447")


# ================================================================== migração 055
def _anterior() -> str:
    """A última migração antes da 055 que existe NESTA árvore (053 hoje; 054 quando a outra frente chegar)."""
    return max(f.stem for f in ORIGEM.glob("*.sql") if f.stem < NOVA)


def _assinatura(db: Database, tabela: str) -> str:
    linhas = db.query(f"SELECT * FROM {tabela} ORDER BY 1")          # noqa: S608 - nome fixo do teste
    return hashlib.sha256(json.dumps(linhas, sort_keys=True, default=str).encode()).hexdigest()


def _esquema(db: Database) -> dict[str, list[str]]:
    return {t: sorted(db.columns(t)) for t in (*TABELAS, "attempts", "steps")}


def test_atualizacao_para_055_nao_toca_o_legado(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    destino = _copia_das_migracoes(tmp_path, monkeypatch, ate=_anterior())
    db = _banco(tmp_path)
    try:
        db.migrate()
        semear_execucao(db, "r1", dias_atras=3, etapas=[("abrir", "OPEN_POST", "failed",
                                                         [("failed", "Pós-condição não comprovada: x")], 2)])
        legado = ("runs", "objectives", "recipes", "flows", "ai_calls")
        antes = {t: _assinatura(db, t) for t in legado}
        shutil.copy2(ORIGEM / f"{NOVA}.sql", destino / f"{NOVA}.sql")
        assert db.migrate() == [NOVA]
        assert db.divergencias() == [] and db.migrate() == []
        assert {t: _assinatura(db, t) for t in legado} == antes
        assert {"failure_kind", "failure_screen"} <= db.columns("attempts") and "failure_kind" in db.columns("steps")
        assert db.one("SELECT failure_kind, failure_screen FROM attempts") == {"failure_kind": None,
                                                                              "failure_screen": None}
        assert db.scalar("SELECT failure_kind FROM steps") is None       # o legado é classificado na leitura
        assert set(TABELAS) <= db.tables()
    finally:
        db.close()


def test_banco_novo_e_banco_atualizado_tem_o_mesmo_esquema(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    destino = _copia_das_migracoes(tmp_path, monkeypatch, ate=_anterior())
    atualizado = _banco(tmp_path, "atualizado.sqlite3")
    atualizado.migrate()
    shutil.copy2(ORIGEM / f"{NOVA}.sql", destino / f"{NOVA}.sql")
    atualizado.migrate()
    novo = _banco(tmp_path, "novo.sqlite3")
    try:
        assert novo.migrate()[-1] == NOVA
        assert _esquema(novo) == _esquema(atualizado)
        for db in (novo, atualizado):
            assert all(_tem_indice(db, i) for i in INDICES), [i for i in INDICES if not _tem_indice(db, i)]
    finally:
        novo.close()
        atualizado.close()


def _tem_indice(db: Database, nome: str) -> bool:
    if db.dialect == "postgres":
        return db.one("SELECT 1 AS x FROM pg_indexes WHERE indexname=?", (nome,)) is not None
    return db.one("SELECT 1 AS x FROM sqlite_master WHERE type='index' AND name=?", (nome,)) is not None


@pytest.mark.parametrize("dialeto", ["sqlite", "postgres"])
def test_a_055_renderiza_sem_marca_sobrando(dialeto: str) -> None:
    texto = _Falso(dialeto).render((ORIGEM / f"{NOVA}.sql").read_text(encoding="utf-8"))
    assert "{{" not in texto
    instrucoes = Database._instrucoes(texto)
    fecha = ("BEGIN", "END", "COMMIT")                      # o executor de migrações já abre a transação
    assert not any(i.strip().upper().startswith(fecha) for i in instrucoes)
    assert sum(1 for i in instrucoes if i.lstrip().upper().startswith("CREATE TABLE")) == len(TABELAS)
    assert sum(1 for i in instrucoes if i.lstrip().upper().startswith("ALTER TABLE")) == 3
    assert not any("REFERENCES" in i.upper() for i in instrucoes)             # sem FK: a execução é purgada


def test_a_copia_entre_bancos_acha_ordem_com_as_tabelas_novas(db: Database) -> None:
    tabelas = set(tabelas_a_copiar(db, db))
    assert set(TABELAS) <= tabelas
    assert all(not _dependencias(db, tabelas)[t] for t in TABELAS)
    assert set(TABELAS) <= set(ordem_por_fk(db, tabelas))


# ================================================================== itens, CAS e D1
def test_criar_e_mover_item_com_cas_e_trilha(db: Database) -> None:
    r = repo(db)
    item = r.criar_item(novo_item(), by=SYSTEM_ACTOR, estado=SkillState.CANDIDATE, detalhe=None, reason="nascimento")
    assert item.id.startswith("li-") and item.state is SkillState.CANDIDATE and not item.requires_owner
    validado = r.transicionar_item(item, SkillState.VALIDATED, by=SYSTEM_ACTOR, reason="repetiu em 2 execuções")
    with pytest.raises(ConflitoDeEstado):                    # a leitura velha (candidate) perde para quem chegou antes
        r.transicionar_item(item, SkillState.DISABLED, by="painel:flavio", reason="rejeitada")
    publicado = r.transicionar_item(validado, SkillState.PUBLISHED, by=SYSTEM_ACTOR, reason="D1: sem efeito")
    trilha = r.trilha(item.id)
    assert [(t.from_state, t.to_state, t.decided_by) for t in trilha] == [
        (None, SkillState.CANDIDATE, SYSTEM_ACTOR), (SkillState.CANDIDATE, SkillState.VALIDATED, SYSTEM_ACTOR),
        (SkillState.VALIDATED, SkillState.PUBLISHED, SYSTEM_ACTOR)]
    assert all(t.content_hash == item.content_hash and t.scope_key for t in trilha)
    assert publicado.state is SkillState.PUBLISHED


def test_o_banco_nao_publica_pelo_sistema_o_que_exige_o_dono(db: Database) -> None:
    """Segunda camada do D1: chamando o repositório DIRETO (sem o domínio), o `UPDATE` recusa."""
    r = repo(db)
    for efeito, fonte in ((True, SourceKind.RECOVERY), (False, SourceKind.FEEDBACK_NOTE)):
        novo = novo_item(f"alvo {efeito}", efeito=efeito, fonte=fonte)
        # Texto de pessoa só nasce validado pela pessoa (D1, `conferir_nascimento`); aqui o que se prova é a
        # publicação pelo sistema a partir de `validated`.
        criador = "painel:flavio" if novo.human_origin else SYSTEM_ACTOR
        item = r.criar_item(novo, by=criador, estado=SkillState.VALIDATED, detalhe=None, reason="teste")
        assert item.requires_owner
        with pytest.raises(ExigeODono):
            r.transicionar_item(item, SkillState.PUBLISHED, by=SYSTEM_ACTOR, reason="tentativa do sistema")
        assert db.scalar("SELECT state FROM learning_items WHERE id=?", (item.id,)) == "validated"
        assert r.transicionar_item(item, SkillState.PUBLISHED, by="painel:flavio",
                                   reason="aprovado pelo dono").state is SkillState.PUBLISHED


def _nasce(estado: SkillState, *, sistema: bool, efeito: bool, humano: bool) -> bool:
    """A regra do dono para o item que já NASCE num estado, reescrita sem olhar a implementação: o sistema não cria
    publicado o que tem efeito ou texto de pessoa, nem validado o que tem texto de pessoa; a pessoa cria em qualquer
    estado do livro."""
    if not sistema:
        return True
    if estado is SkillState.PUBLISHED:
        return not (efeito or humano)
    if estado is SkillState.VALIDATED:
        return not humano
    return True


def test_o_sistema_nao_cria_item_ja_publicado_que_exige_o_dono(db: Database) -> None:
    """Segunda camada do D1 no NASCIMENTO: a porta `criar_item` expõe `estado`, e um minerador que a chame direto não
    publica sem o dono. Percorre estado × efeito × texto de pessoa × ator; recusado, nada fica gravado (nem a linha nem
    a trilha); aceito, nasce no estado pedido com a trilha de quem decidiu."""
    r = repo(db)
    fontes = (SourceKind.RECOVERY, SourceKind.FEEDBACK_NOTE, SourceKind.MANUAL)
    assert {novo_item(fonte=f).human_origin for f in fontes} == {False, True}
    divergencias = []
    for estado, efeito, fonte, by in itertools.product([s for s in SkillState if s in ESTADOS], (False, True), fontes,
                                                       (SYSTEM_ACTOR, "painel:flavio")):
        novo = novo_item(f"{estado.value} {efeito} {fonte.value} {by}", efeito=efeito, fonte=fonte)
        antes = (db.scalar("SELECT COUNT(*) FROM learning_items"),
                 db.scalar("SELECT COUNT(*) FROM learning_transitions"))
        try:
            criado = r.criar_item(novo, by=by, estado=estado, detalhe=None, reason="matriz do nascimento")
        except ExigeODono:
            aceito = False
            assert (db.scalar("SELECT COUNT(*) FROM learning_items"),
                    db.scalar("SELECT COUNT(*) FROM learning_transitions")) == antes
        else:
            aceito = True
            assert criado.state is estado and criado.state_by == by
            assert [(t.from_state, t.to_state, t.decided_by) for t in r.trilha(criado.id)] == [(None, estado, by)]
        if aceito != _nasce(estado, sistema=by == SYSTEM_ACTOR, efeito=efeito, humano=novo.human_origin):
            divergencias.append((estado.value, efeito, fonte.value, by, aceito))
    assert divergencias == []
    assert db.scalar("SELECT COUNT(*) FROM learning_items WHERE state_by=? AND (side_effect<>0 OR human_origin<>0)"
                     " AND state='published'", (SYSTEM_ACTOR,)) == 0
    assert db.scalar("SELECT COUNT(*) FROM learning_items WHERE state_by=? AND human_origin<>0 AND state='validated'",
                     (SYSTEM_ACTOR,)) == 0


def test_decided_by_nunca_vazio(db: Database) -> None:
    r = repo(db)
    with pytest.raises(EntradaInvalida):
        r.criar_item(novo_item(), by=" ", estado=SkillState.CANDIDATE, detalhe=None, reason="x")
    item = r.criar_item(novo_item(), by=SYSTEM_ACTOR, estado=SkillState.CANDIDATE, detalhe=None, reason="x")
    with pytest.raises(EntradaInvalida):
        r.transicionar_item(item, SkillState.DISABLED, by="", reason="x")
    assert db.scalar("SELECT COUNT(*) FROM learning_transitions WHERE decided_by='' OR decided_by IS NULL") == 0


def test_indice_parcial_de_item_vivo(db: Database) -> None:
    r = repo(db)
    vivo = r.criar_item(novo_item(), by=SYSTEM_ACTOR, estado=SkillState.CANDIDATE, detalhe=None, reason="x")
    assert r.item_vivo(novo_item()) == vivo
    with pytest.raises(ConflitoDeEstado):
        r.criar_item(novo_item(), by=SYSTEM_ACTOR, estado=SkillState.CANDIDATE, detalhe=None, reason="x")
    r.transicionar_item(vivo, SkillState.DISABLED, by="painel:flavio", reason="refutada")
    assert r.item_vivo(novo_item()) is None                      # desligado, o conteúdo pode voltar a nascer
    outro = r.criar_item(novo_item(), by="painel:flavio", estado=SkillState.CANDIDATE, detalhe=None, reason="x")
    assert outro.id != vivo.id
    desligado = r.item(vivo.id)
    assert desligado is not None
    with pytest.raises(ConflitoDeEstado):                        # reativar o velho: já há outro vivo igual
        r.transicionar_item(desligado, SkillState.PUBLISHED, by="painel:flavio", reason="reativar")


def test_o_servico_recusa_conteudo_com_cara_de_credencial_e_respeita_o_veto(db: Database) -> None:
    servico = LearningService(repo(db), FontesSql(db), TriagemDeCredencial(), ajustes=Ajustes,
                              relogio=lambda: AGORA, retencao_de_logs_dias=lambda: 14)
    with pytest.raises(NotaComCaraDeSegredo):
        servico.propor(novo_item("digitar a senha: Hunter2!x9"))
    item = servico.propor(novo_item())
    assert servico.propor(novo_item()).id == item.id              # o mesmo conteúdo vivo só volta o existente
    servico.mudar_estado(LivroKind.LICAO, item.id, SkillState.DISABLED, by="painel:flavio", reason="está errada")
    with pytest.raises(Vetado, match="pessoa"):
        servico.propor(novo_item())                               # desligado por PESSOA não volta pelo sistema
    assert servico.propor(novo_item(), by="painel:flavio").state is SkillState.CANDIDATE


# ================================================================== sinais e evidência
def _sinal(**kw: object) -> NovoSinal:
    base: dict[str, object] = {"kind": SignalKind.FEEDBACK, "source_ref": "run:r1", "created_by": "painel:flavio",
                               "polarity": Polaridade.NEGATIVE, "verdict": "errado", "reason": "alvo_errado",
                               "run_id": "r1", "app_package": "com.instagram.android", "capability": "OPEN_POST"}
    base.update(kw)
    return NovoSinal(**base)  # type: ignore[arg-type]


def test_sinal_idempotente_e_voto_como_upsert(db: Database) -> None:
    r = repo(db)
    primeiro = r.registrar_sinal(_sinal())
    assert primeiro is not None
    assert r.registrar_sinal(_sinal()) is None                     # a varredura sem cursor não duplica
    trocado = r.registrar_sinal(_sinal(verdict="certo", polarity=Polaridade.POSITIVE, reason=None), substituir=True)
    assert trocado == primeiro
    assert db.one("SELECT verdict, polarity FROM learning_signals WHERE id=?", (primeiro,)) == {
        "verdict": "certo", "polarity": "positive"}
    assert r.registrar_sinal(_sinal(created_by="painel:outra")) is not None   # outra pessoa, outro voto
    assert db.scalar("SELECT COUNT(*) FROM learning_signals") == 2


def test_gesto_e_um_sinal_por_evento_e_o_primeiro_autor_fica(db: Database) -> None:
    """O gesto (ADR-054; plano 22.1) é um por `(kind, source_ref)`: o `source_ref` já é o evento, e o mesmo evento
    feito de novo por outra pessoa não vira segunda linha — a régua conta linhas. O voto do D2, sem a opção, continua
    um por pessoa. O `created_by` do gesto é o da sessão, nunca `sistema`."""
    r = repo(db)
    gesto = {"kind": SignalKind.TOMOU_CONTROLE, "source_ref": "takeover:r1:s1:a1", "verdict": None, "reason": None}
    primeiro = r.registrar_sinal(_sinal(**gesto, created_by="Ana Ribeiro"), um_por_evento=True)
    assert primeiro is not None
    assert r.registrar_sinal(_sinal(**gesto, created_by="Bruno Lima"), um_por_evento=True) is None
    assert r.registrar_sinal(_sinal(**gesto, created_by="Ana Ribeiro"), um_por_evento=True) is None
    assert db.query("SELECT id, created_by FROM learning_signals WHERE kind='tomou_controle'") == [
        {"id": primeiro, "created_by": "Ana Ribeiro"}]
    # outro evento (outra tentativa) é outra linha, de quem quer que seja
    assert r.registrar_sinal(_sinal(**{**gesto, "source_ref": "takeover:r1:s1:a2"}, created_by="Bruno Lima"),
                             um_por_evento=True) is not None
    # o voto (sem a opção): duas pessoas no mesmo item são duas opiniões
    assert r.registrar_sinal(_sinal(created_by="Ana Ribeiro")) is not None
    assert r.registrar_sinal(_sinal(created_by="Bruno Lima")) is not None
    assert db.scalar("SELECT COUNT(*) FROM learning_signals") == 4


def test_nota_de_sinal_com_cara_de_credencial(db: Database) -> None:
    servico = LearningService(repo(db), FontesSql(db), TriagemDeCredencial(), ajustes=Ajustes,
                              relogio=lambda: AGORA, retencao_de_logs_dias=lambda: 14)
    with pytest.raises(NotaComCaraDeSegredo):                     # o botão: 409 e nada gravado
        servico.registrar_sinal(_sinal(note="a senha é Hunter2!x9"), recusar_nota=True)
    assert db.scalar("SELECT COUNT(*) FROM learning_signals") == 0
    sid = servico.registrar_sinal(_sinal(note="o código de verificação é 123456", source_ref="run:r2"))
    assert db.one("SELECT note, note_refused FROM learning_signals WHERE id=?", (sid,)) == {
        "note": None, "note_refused": 1}                          # a varredura: grava sem a nota
    ok = servico.registrar_sinal(_sinal(note="abriu o post errado " * 40, source_ref="run:r3"))
    assert len(db.scalar("SELECT note FROM learning_signals WHERE id=?", (ok,))) == 500


def test_evidencia_deduplica_pela_origem_e_so_real_conta(db: Database) -> None:
    r = repo(db)
    item = r.criar_item(novo_item(), by=SYSTEM_ACTOR, estado=SkillState.CANDIDATE, detalhe=None, reason="x")

    def ev(origem: str, run: str, aparelho: str, *, simulado: bool = False,
           posicao: Posicao = Posicao.FOR) -> bool:
        return r.registrar_evidencia(NovaEvidencia(item_ref=item.id, stance=posicao, origin_ref=origem,
                                                   simulated=simulado, run_id=run, instance_id=aparelho))

    assert ev("attempt:a1", "r1", "android-06")
    assert not ev("attempt:a1", "r1", "android-06")               # mesma observação: nada
    assert ev("attempt:a2", "r1", "android-06")                   # outra tentativa, mesma execução
    assert ev("attempt:a3", "r2", "android-01")
    assert ev("attempt:a9", "r9", "android-09", simulado=True)    # gravada, mas não conta
    assert ev("attempt:a4", "r3", "android-01", posicao=Posicao.AGAINST)
    atual = r.item(item.id)
    assert atual is not None
    assert (atual.evidence_for, atual.evidence_against, atual.distinct_runs, atual.distinct_devices) == (3, 1, 2, 2)
    assert len(r.evidencias(item.id)) == 5
    with pytest.raises(INTEGRITY_ERRORS):                          # origem nula não existe: a chave deduplica de verdade
        with db.tx():
            db.execute("INSERT INTO learning_evidence(item_ref, stance, origin_ref, simulated, observed_at)"
                       " VALUES (?,?,?,?,?)", (item.id, "for", None, 0, iso(0)))


# ================================================================== régua diária durável
def test_regua_diaria_recalculada_por_inteiro_e_idempotente(db: Database) -> None:
    r = repo(db)
    semear_execucao(db, "r-real", dias_atras=2, etapas=[
        ("abrir", "OPEN_POST", "succeeded", [("failed", "Pós-condição não comprovada: x"), ("succeeded", None)], 3),
        ("curtir", "LIKE_POST", "waiting_user", [("failed", "O app pede autenticação (senha).")], 1)])
    semear_execucao(db, "r-sim", dias_atras=2, simulated=True, etapas=[("abrir", "OPEN_POST", "succeeded",
                                                                       [("succeeded", None)], 5)])
    dia = iso(2)[:10]
    fim = (AGORA + timedelta(days=1)).strftime("%Y-%m-%d")
    assert r.recalcular_diario(dia, fim) == r.recalcular_diario(dia, fim)
    linhas = {(x["capability"], x["failure_kind"]): x for x in db.query("SELECT * FROM learning_daily")}
    assert set(linhas) == {("OPEN_POST", "pos_condicao_nao_comprovada"), ("OPEN_POST", ""),
                           ("LIKE_POST", "autenticacao")}          # legado classificado na leitura; simulado fora
    sucesso = linhas[("OPEN_POST", "")]
    assert (sucesso["attempts"], sucesso["steps"], sucesso["ai_calls"]) == (1, 1, 3)
    assert abs(sucesso["usd"] - 0.03) < 1e-9 and sucesso["app_package"] == "com.instagram.android"
    assert linhas[("OPEN_POST", "pos_condicao_nao_comprovada")]["steps"] == 0
    assert linhas[("LIKE_POST", "autenticacao")]["interventions"] == 1
    assert db.scalar("SELECT SUM(attempts) FROM learning_daily") == 3
    assert db.scalar("SELECT failure_kind FROM attempts WHERE id LIKE '%curtir:a1'") is None   # nada gravado


def _etapa_na_meia_noite(db: Database, run_id: str, chave: str, acao: str,
                         tentativas: list[tuple[str, str, str, str | None]],
                         chamadas: list[tuple[str, str | None]]) -> None:
    """Uma etapa com horários FIXOS. `tentativas` = (status, início, fim, erro); `chamadas` = (ts, tentativa ou `None`
    para a chamada antiga, sem `attempt_id`, anterior à 045)."""
    if db.one("SELECT id FROM apps WHERE id='instagram'") is None:
        db.execute("INSERT INTO apps(id, name, package, builtin) VALUES ('instagram','instagram',"
                   "'com.instagram.android',0)")
    if db.one("SELECT id FROM runs WHERE id=?", (run_id,)) is None:
        db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at,"
                   " app_ids) VALUES (?,?,?,?,?,?,?,?,?)", (run_id, run_id, "abrir o perfil", "execute", "completed",
                                                            0, '["android-06"]', tentativas[0][1], '["instagram"]'))
        db.execute("INSERT INTO objectives(id, run_id, instance_id, status) VALUES (?,?,?,?)",
                   (f"{run_id}:o", run_id, "android-06", "succeeded"))
    sid = f"{run_id}:{chave}"
    db.execute("INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal,"
               " postcondition, timeout_s, max_attempts, status, capability, app_id, driven_by, started_at,"
               " finished_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (sid, run_id, f"{run_id}:o", "android-06", 1, 0, chave, chave, chave, "{}", 60, 3, "succeeded", acao,
                None, "ai", tentativas[0][1], tentativas[-1][2]))
    for n, (status, inicio, fim, erro) in enumerate(tentativas, start=1):
        db.execute("INSERT INTO attempts(id, step_id, number, status, started_at, finished_at, error)"
                   " VALUES (?,?,?,?,?,?,?)", (f"{sid}:a{n}", sid, n, status, inicio, fim, erro))
    for ts, tentativa in chamadas:
        db.execute("INSERT INTO ai_calls(ts, run_id, objective_id, step_id, role, model, tier, input_tokens,"
                   " output_tokens, attempt_id, usd) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   (ts, run_id, f"{run_id}:o", sid, "decide", "modelo-x", 0, 1000, 100,
                    None if tentativa is None else f"{sid}:{tentativa}", 0.01))


def test_a_chamada_da_etapa_que_atravessa_a_meia_noite_conta_uma_vez_so(db: Database) -> None:
    """A régua é durável e não se recalcula depois que `ai_calls` é purgado: uma chamada somada duas vezes fica somada
    para sempre. No regime normal cada dia é calculado num intervalo SEPARADO (a última linha de um dia é a de quando
    ele era o mais recente), e a etapa que atravessa 00:00 UTC tem tentativas nos dois. A chamada da tentativa que
    terminou em D-1 conta em D-1 e não é somada de novo à tentativa seguinte, em D; a chamada antiga (sem
    `attempt_id`) vai para a última tentativa da etapa, a mesma em qualquer intervalo."""
    r = repo(db)
    erro = "Pós-condição não comprovada: x"
    # A sonda do revisor: a1 termina 23:55Z em D-1 (chamada 23:50Z), a2 termina 00:10Z em D (chamada 00:05Z).
    _etapa_na_meia_noite(db, "r-meia-noite", "abrir", "OPEN_POST",
                         [("failed", "2026-09-20T23:40:00.000Z", "2026-09-20T23:55:00.000Z", erro),
                          ("succeeded", "2026-09-21T00:00:00.000Z", "2026-09-21T00:10:00.000Z", None)],
                         [("2026-09-20T23:50:00.000Z", "a1"), ("2026-09-21T00:05:00.000Z", "a2")])
    # A mesma forma com uma chamada anterior à 045 (só `step_id`), feita durante a1.
    _etapa_na_meia_noite(db, "r-meia-noite", "curtir", "LIKE_POST",
                         [("failed", "2026-09-20T23:41:00.000Z", "2026-09-20T23:56:00.000Z", erro),
                          ("succeeded", "2026-09-21T00:01:00.000Z", "2026-09-21T00:11:00.000Z", None)],
                         [("2026-09-20T23:51:00.000Z", None)])

    def por_dia() -> dict[tuple[str, str], int]:
        return {(x["day"], x["capability"]): int(x["n"]) for x in db.query(
            "SELECT day, capability, SUM(ai_calls) AS n FROM learning_daily GROUP BY day, capability")}

    esperado = {("2026-09-20", "OPEN_POST"): 1, ("2026-09-21", "OPEN_POST"): 1,
                ("2026-09-20", "LIKE_POST"): 0, ("2026-09-21", "LIKE_POST"): 1}
    r.recalcular_diario("2026-09-20", "2026-09-21")
    r.recalcular_diario("2026-09-21", "2026-09-22")
    assert por_dia() == esperado                                     # dias em intervalos separados
    assert db.scalar("SELECT SUM(ai_calls) FROM learning_daily") == 3 == db.scalar("SELECT COUNT(*) FROM ai_calls")
    r.recalcular_diario("2026-09-21", "2026-09-22")                  # D recalculado de novo, sozinho
    assert por_dia() == esperado
    r.recalcular_diario("2026-09-20", "2026-09-22")                  # os dois num intervalo só: o mesmo número
    assert por_dia() == esperado
    assert abs(float(db.scalar("SELECT SUM(usd) FROM learning_daily")) - 0.03) < 1e-9


def test_a_regua_nunca_reescreve_um_dia_ja_mordido_pela_purga(db: Database) -> None:
    """O dia que já perdeu chamadas para a purga não é recalculado: nem o que contém o corte, nem — quando
    `log_retention_days` AUMENTOU — os que a retenção de agora diria estarem dentro da janela."""
    r = repo(db)
    retencao = [14]
    servico = LearningService(r, FontesSql(db), TriagemDeCredencial(), ajustes=Ajustes, relogio=lambda: AGORA,
                              retencao_de_logs_dias=lambda: retencao[0])
    semear_execucao(db, "r-velha", dias_atras=10, etapas=[("abrir", "OPEN_POST", "succeeded", [("succeeded", None)],
                                                          4)])
    semear_execucao(db, "r-nova", dias_atras=1, etapas=[("abrir", "OPEN_POST", "succeeded", [("succeeded", None)], 2)])
    relatorio = servico.curar()
    assert relatorio.falhas == () and relatorio.feito["diario"] >= 2
    dia_velho = iso(10)[:10]
    assert db.scalar("SELECT ai_calls FROM learning_daily WHERE day=?", (dia_velho,)) == 4
    # A purga de 6 h atrás levou as chamadas da execução velha (e só elas), com a retenção de então (8 dias).
    db.execute("DELETE FROM ai_calls WHERE ts < ?", (iso(8),))
    retencao[0] = 30                                                # alguém aumentou a retenção depois
    servico.curar()
    assert db.scalar("SELECT ai_calls FROM learning_daily WHERE day=?", (dia_velho,)) == 4   # não virou 0
    assert servico.antes_da_purga(AGORA - timedelta(days=5)) == 0   # nada faltando entre a fronteira e o corte


def test_antes_da_purga_so_preenche_lacuna_do_que_vai_ser_mordido(db: Database) -> None:
    r = repo(db)
    servico = LearningService(r, FontesSql(db), TriagemDeCredencial(), ajustes=Ajustes, relogio=lambda: AGORA,
                              retencao_de_logs_dias=lambda: 14)
    semear_execucao(db, "r-antiga", dias_atras=13.5, etapas=[("abrir", "OPEN_POST", "succeeded",
                                                              [("succeeded", None)], 3)])
    semear_execucao(db, "r-hoje", dias_atras=0.2, etapas=[("abrir", "OPEN_POST", "succeeded",
                                                           [("succeeded", None)], 1)])
    # A curadoria esteve fora do ar: o dia de 13,5 dias atrás nunca foi agregado. A purga vai mordê-lo agora.
    gravadas = servico.antes_da_purga(AGORA - timedelta(days=13))
    assert gravadas >= 1
    assert db.scalar("SELECT ai_calls FROM learning_daily WHERE day=?", (iso(13.5)[:10],)) == 3
    assert db.scalar("SELECT COUNT(*) FROM learning_daily WHERE day=?", (iso(0.2)[:10],)) == 0   # não é lacuna dela
    desligado = LearningService(r, FontesSql(db), TriagemDeCredencial(), ajustes=lambda: Ajustes(enabled=False),
                                relogio=lambda: AGORA, retencao_de_logs_dias=lambda: 14)
    assert desligado.antes_da_purga(AGORA) == 0 and desligado.curar().pulado


# ================================================================== retenção
def test_retencao_do_aprendizado(db: Database) -> None:
    r = repo(db)
    ret = Retencao(sinais_dias=180, feedback_dias=365, exposicoes_dias=120, evidencias_por_item=2, diario_dias=400,
                   candidata_sem_evidencia_dias=90)
    r.registrar_sinal(_sinal(kind=SignalKind.TOMOU_CONTROLE, source_ref="takeover:velho"))
    r.registrar_sinal(_sinal(source_ref="run:velho"))
    r.registrar_sinal(_sinal(source_ref="run:antigo-demais"))
    db.execute("UPDATE learning_signals SET created_at=? WHERE source_ref IN ('takeover:velho','run:velho')",
               (iso(200),))
    db.execute("UPDATE learning_signals SET created_at=? WHERE source_ref='run:antigo-demais'", (iso(400),))
    db.execute("INSERT INTO learning_exposures(item_id, unit_id, role, arm, created_at, filled_at)"
               " VALUES ('li-x','step:1','actor','with',?,?)", (iso(200), iso(130)))
    db.execute("INSERT INTO learning_daily(day, app_package, capability, failure_kind, driven_by, computed_at)"
               " VALUES (?, 'p', '*', '', '', ?)", (iso(401)[:10], iso(0)))
    publicado = r.criar_item(novo_item("publicado"), by=SYSTEM_ACTOR, estado=SkillState.PUBLISHED, detalhe=None,
                             reason="x")
    velha = r.criar_item(novo_item("velha"), by=SYSTEM_ACTOR, estado=SkillState.CANDIDATE, detalhe=None, reason="x")
    viva = r.criar_item(novo_item("viva"), by=SYSTEM_ACTOR, estado=SkillState.CANDIDATE, detalhe=None, reason="x")
    db.execute("UPDATE learning_items SET created_at=? WHERE id IN (?,?,?)", (iso(100), publicado.id, velha.id,
                                                                             viva.id))
    for n in range(4):
        r.registrar_evidencia(NovaEvidencia(item_ref=viva.id, stance=Posicao.FOR, origin_ref=f"attempt:{n}",
                                            simulated=False, run_id=f"r{n}"))
    removidas = r.aplicar_retencao(ret, AGORA)
    assert removidas >= 6
    assert {x["source_ref"] for x in db.query("SELECT source_ref FROM learning_signals")} == {"run:velho"}
    assert db.scalar("SELECT COUNT(*) FROM learning_exposures") == 0
    assert db.scalar("SELECT COUNT(*) FROM learning_daily") == 0
    assert db.scalar("SELECT COUNT(*) FROM learning_evidence WHERE item_ref=?", (viva.id,)) == 2
    assert (r.item(viva.id) or pytest.fail()).evidence_for == 4       # os contadores guardam o total
    assert r.item(velha.id) is None and r.item(publicado.id) is not None
    assert r.trilha(velha.id)                                         # a trilha fica
