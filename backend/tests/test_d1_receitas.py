"""D1 nas receitas (ADR-054, pacote A5): a candidata que concordou com a IA só passa a agir sozinha se não tiver ação
de efeito externo; com `commit`, ela para em `validated` e espera o dono. O legado ativo com efeito fica intacto e
aparece em "Revisar". Toda mudança de status feita pela própria loja vai para `learning_transitions`, com o mesmo
`content_hash` e escopo que o livro lê — é o que faz o veto funcionar: o caminho que uma PESSOA desligou não volta
pelo sistema (a quarentena do sistema, não: a etapa sempre pôde ser reaprendida e provada de novo). A trilha e o veto
que falham no banco não derrubam a receita nem com o aborto de transação do PostgreSQL (22.5, imitado sobre o SQLite).

Nível de prova: `simulated` (banco de teste migrado pela fábrica da suíte; nenhum aparelho, nenhuma IA).
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.db import Database
from app.metricas import metricas
from app.modules.learning.application.ports import Ajustes
from app.modules.learning.application.servico import LearningService
from app.modules.learning.domain.ciclo import ExigeODono, SkillState
from app.modules.learning.domain.vocabulario import LivroKind
from app.modules.learning.infrastructure import ligar_nativos
from app.modules.learning.infrastructure.fontes import FontesSql
from app.modules.learning.infrastructure.segredo import TriagemDeCredencial
from app.modules.learning.infrastructure.sql_repository import SqlLearningRepository
from app.taskqueue.recipes import RecipeStore
from app.util import now

from .aborto_do_postgres import embrulhar, savepoints
from .conftest import Harness
from .fake_skills import TS
from .fake_skills import banco as banco_migrado

S = SkillState
PKG = "com.pocqa.messenger"
CHAVE = {"signature": "", "variant": "en-US/xhdpi"}
DONO = "painel:dono"
NOME = "Ana Ribeiro"                                                        # o operador que faz login no painel


def _acoes(*, commit: bool, rid: str = "app:id/send") -> list[dict[str, Any]]:
    return [{"tool": "tap", "commit": commit, "why": "a IA explicou", "args": {},
             "selectors": [{"kind": "rid", "rid": rid}]}]


class Mundo:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.decisoes: list[tuple[str, str]] = []
        self.repo = SqlLearningRepository(db, precos=dict)
        self.servico = LearningService(self.repo, FontesSql(db), TriagemDeCredencial(), ajustes=Ajustes, relogio=now,
                                       retencao_de_logs_dias=lambda: 14)
        self.store = RecipeStore(db)
        ligar_nativos.ligar(self.servico, self.repo, db, receitas=self.store,
                            decidir=lambda texto, run_id: self.decisoes.append((run_id, texto)))

    def salva(self, passo: str = "enviar", *, commit: bool, candidate: bool = True, learned_from: str = "s1",
              replaces: int | None = None, rid: str = "app:id/send") -> int | None:
        return self.store.save(package=PKG, app_version="1.0(1)", step_hash=f"h-{passo}", step_key=passo,
                               actions=_acoes(commit=commit, rid=rid), learned_from=learned_from,
                               candidate=candidate, replaces=replaces, **CHAVE)

    def procura(self, passo: str = "enviar") -> Any:
        return self.store.find(PKG, "1.0(1)", f"h-{passo}", **CHAVE)

    def status(self, rid: int) -> str:
        return str(self.db.scalar("SELECT status FROM recipes WHERE id=?", (rid,)))

    def trilha(self, rid: int) -> list[tuple[str | None, str, str]]:
        return [(t.from_state.value if t.from_state else None, t.to_state.value, t.decided_by)
                for t in self.repo.trilha(f"receita:{rid}")]


@pytest.fixture
def mundo(tmp_path: Path) -> Iterator[Mundo]:
    db = banco_migrado(tmp_path, "d1-receitas.sqlite3")
    yield Mundo(db)
    db.close()


# ------------------------------------------------------------------ promoção com e sem efeito
def test_promocao_com_commit_para_em_validated_e_find_nao_devolve(mundo: Mundo) -> None:
    rid = mundo.salva(commit=True)
    assert rid
    assert mundo.store.shadow(rid, True, promote_after=2) is False
    # 2ª concordância seguida: provou-se, mas tem efeito externo — não passa a agir (a resposta diz isso: False)
    assert mundo.store.shadow(rid, True, promote_after=2) is False
    assert mundo.status(rid) == "validated"
    metricas.limpar()
    assert mundo.procura() is None                                   # a etapa não a vê: a IA segue conduzindo
    assert metricas.valor("receita.consulta", resultado="ausente") == 1
    # espera o dono em "Para aprovar"; a trilha diz quem fez cada passo
    assert ("receita", str(rid)) in {(e.kind.value, e.ref) for e in mundo.servico.pendentes()}
    assert mundo.trilha(rid) == [(None, "candidate", "sistema"), ("candidate", "validated", "sistema")]
    motivo = mundo.repo.trilha(f"receita:{rid}")[-1].reason
    assert "efeito externo" in motivo and "dono" in motivo
    # o sistema não a publica nem pelo livro (D1 no domínio e na segunda camada)
    with pytest.raises(ExigeODono):
        mundo.servico.mudar_estado(LivroKind.RECEITA, str(rid), S.PUBLISHED, by="sistema", reason="tentar")
    assert mundo.status(rid) == "validated"
    # o dono aprova: agora sim ela age, e a busca a devolve
    mundo.servico.mudar_estado(LivroKind.RECEITA, str(rid), S.PUBLISHED, by=DONO, reason="aprovada em lote")
    assert mundo.status(rid) == "active" and mundo.procura()["id"] == rid
    assert mundo.trilha(rid)[-1] == ("validated", "published", DONO)


def test_sem_commit_segue_o_aberto_receitas(mundo: Mundo) -> None:
    rid = mundo.salva("abrir", commit=False)
    assert rid
    assert mundo.store.shadow(rid, True, promote_after=2) is False
    assert mundo.store.shadow(rid, True, promote_after=2) is True    # sem efeito: publicada pelo sistema (D1)
    assert mundo.status(rid) == "active" and mundo.procura("abrir")["id"] == rid
    assert mundo.trilha(rid) == [(None, "candidate", "sistema"), ("candidate", "published", "sistema")]
    assert ("receita", str(rid)) not in {(e.kind.value, e.ref) for e in mundo.servico.pendentes()}


def test_validada_segura_a_chave_e_nao_enche_a_fila_do_dono(mundo: Mundo) -> None:
    rid = mundo.salva(commit=True)
    assert rid
    for _ in range(2):
        mundo.store.shadow(rid, True, promote_after=2)
    assert mundo.status(rid) == "validated"
    # a IA segue conduzindo a etapa e comprovando o mesmo caminho: nenhuma versão nova enquanto o dono não decide
    assert mundo.salva(commit=True, learned_from="s2") is None
    assert mundo.salva(commit=True, learned_from="s3", rid="app:id/outro") is None
    assert mundo.db.scalar("SELECT COUNT(*) FROM recipes") == 1


def test_ativas_com_commit_de_antes_ficam_intactas_e_aparecem_em_revisar(mundo: Mundo) -> None:
    db = mundo.db
    db.execute("INSERT INTO recipes(app_package, app_version, app_signature, variant, step_hash, step_key, version,"
               " status, actions, learned_from_step, created_at) VALUES (?,?,?,?,?,?,1,'active',?,?,?)",
               (PKG, "1.0(1)", "", "en-US/xhdpi", "h-enviar", "enviar", json.dumps(_acoes(commit=True)), "s0", TS))
    antiga = int(db.scalar("SELECT id FROM recipes WHERE step_hash='h-enviar'"))
    # nada do D1 rebaixa o legado: a busca a devolve, a sombra só acumula a taxa, o save não a troca
    assert mundo.procura()["id"] == antiga
    assert mundo.store.shadow(antiga, True, promote_after=2) is False
    assert mundo.salva(commit=True) is None
    assert mundo.status(antiga) == "active" and mundo.trilha(antiga) == []
    assert ("receita", str(antiga)) in {(e.kind.value, e.ref) for e in mundo.servico.revisar()}


# ------------------------------------------------------------------ trilha das mudanças da própria loja
def test_as_transicoes_da_loja_gravam_learning_transitions(mundo: Mundo) -> None:
    v1 = mundo.salva("abrir", commit=False)
    assert v1
    assert mundo.store.shadow(v1, False, promote_after=2) is False   # divergiu: a IA comprovou outro caminho
    v2 = mundo.salva("abrir", commit=False, learned_from="s2", replaces=v1, rid="app:id/outra_linha")
    assert v2 and mundo.status(v1) == "superseded"
    assert mundo.trilha(v1) == [(None, "candidate", "sistema"), ("candidate", "deprecated", "sistema")]
    assert "v2" in mundo.repo.trilha(f"receita:{v1}")[-1].reason
    # promovida, depois 3 falhas seguidas ao reproduzir: a quarentena (rebaixar é automático) fica na trilha
    assert mundo.store.shadow(v2, True, promote_after=1) is True
    assert [mundo.store.result(v2, False) for _ in range(3)] == [False, False, True]
    assert mundo.trilha(v2)[-1] == ("published", "disabled", "sistema")
    assert "3 falhas seguidas" in mundo.repo.trilha(f"receita:{v2}")[-1].reason
    # o treino é da pessoa: a trilha diz de qual demonstração veio
    v3 = mundo.salva("abrir", commit=False, candidate=False, learned_from="training:t1")
    assert v3 and mundo.trilha(v3) == [(None, "published", "training:t1")]
    assert mundo.trilha(v2)[-1] == ("disabled", "deprecated", "sistema")
    # o hash e o escopo da trilha são os do livro: é por eles que o veto e o "Revisar" a encontram
    entrada = FontesSql(mundo.db).receita(str(v3))
    assert entrada is not None
    [t] = mundo.repo.trilha(f"receita:{v3}")
    assert (t.content_hash, t.scope_key, t.app_version) == (entrada.content_hash, entrada.scope_key, "1.0(1)")


def test_trilha_da_receita_aponta_a_execucao_que_a_ensinou(mundo: Mundo) -> None:
    db = mundo.db
    db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, instance_ids, created_at)"
               " VALUES ('r-1','k-r-1','x','execute','completed','[\"android-01\"]',?)", (TS,))
    db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version) VALUES"
               " ('r-1:o1','r-1','android-01','succeeded',1)")
    db.execute("INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal,"
               " postcondition, timeout_s, max_attempts, status) VALUES ('r-1:s1','r-1','r-1:o1','android-01',1,1,"
               "'abrir','abrir','abrir','{}',60,3,'succeeded')")
    rid = mundo.salva("abrir", commit=False, learned_from="r-1:s1")
    assert rid
    assert [t.run_id for t in mundo.repo.trilha(f"receita:{rid}")] == ["r-1"]


# ------------------------------------------------------------------ veto: o que a pessoa desligou não volta
def test_caminho_que_uma_pessoa_desligou_nao_volta_pelo_sistema(mundo: Mundo) -> None:
    rid = mundo.salva(commit=False)
    assert rid and mundo.store.shadow(rid, True, promote_after=1) is True
    mundo.servico.mudar_estado(LivroKind.RECEITA, str(rid), S.DISABLED, by=DONO, reason="tocou o botão errado")
    assert mundo.status(rid) == "quarantined"
    # a IA comprova de novo o MESMO caminho: o sistema não o grava outra vez
    assert mundo.salva(commit=False, learned_from="s2") is None
    assert mundo.status(rid) == "quarantined" and mundo.db.scalar("SELECT COUNT(*) FROM recipes") == 1
    # outro caminho, sim (e ele toma o lugar da desligada)
    outro = mundo.salva(commit=False, learned_from="s3", rid="app:id/outro_botao")
    assert outro and mundo.status(rid) == "superseded"
    # a pessoa pode ensinar o caminho que ela mesma desligou (treino não passa pelo veto)
    mundo.servico.mudar_estado(LivroKind.RECEITA, str(outro), S.DISABLED, by=DONO, reason="também não")
    assert mundo.salva(commit=False, candidate=False, learned_from="training:t9")


def test_candidata_com_caminho_vetado_nao_e_promovida(mundo: Mundo) -> None:
    rid = mundo.salva(commit=False)
    assert rid and mundo.store.shadow(rid, True, promote_after=1) is True
    mundo.servico.mudar_estado(LivroKind.RECEITA, str(rid), S.DISABLED, by=DONO, reason="não quero")
    # a mesma chave numa candidata gravada por fora do D1 (loja crua, sem ouvinte): a sombra do central não a promove
    crua = RecipeStore(mundo.db).save(package=PKG, app_version="1.0(1)", step_hash="h-enviar", step_key="enviar",
                                      actions=_acoes(commit=False), learned_from="s2", candidate=True, **CHAVE)
    assert crua
    assert mundo.store.shadow(crua, True, promote_after=1) is False
    assert mundo.status(crua) == "candidate"


def test_quarentena_do_sistema_nao_veta_o_reaprendizado(mundo: Mundo) -> None:
    rid = mundo.salva(commit=False)
    assert rid and mundo.store.shadow(rid, True, promote_after=1) is True
    assert [mundo.store.result(rid, False) for _ in range(3)][-1] is True
    # a etapa volta a ser aprendida com o mesmo caminho: nova candidata, que prova de novo em sombra antes de agir
    v2 = mundo.salva(commit=False, learned_from="s2")
    assert v2 and mundo.status(v2) == "candidate" and mundo.status(rid) == "superseded"


# ------------------------------------------------------------------ a trilha e o veto não derrubam a loja (22.5)
def test_trilha_que_falha_no_banco_nao_derruba_a_receita_nem_com_o_aborto_do_postgres(mundo: Mundo) -> None:
    """O revisor do A5: o erro da trilha, engolido pelo ouvinte dentro da transação da loja, deixava a transação
    abortada no PostgreSQL — `save` devolvia o id de uma receita que nunca existiu, e a promoção e a quarentena
    sumiam caladas. Com o savepoint dentro do `try` do ouvinte, só a trilha sai."""
    if mundo.db.dialect != "sqlite":
        pytest.skip("o embrulho imita o PostgreSQL sobre o SQLite")
    pg = embrulhar(mundo.db)
    pg.falhar_em = "INSERT INTO learning_transitions"
    rid = mundo.salva(commit=False)
    assert rid and mundo.status(rid) == "candidate" and pg.commits_perdidos == 0
    assert "ROLLBACK TO SAVEPOINT sp_1" in savepoints(pg)
    assert mundo.store.shadow(rid, True, promote_after=1) is True and mundo.status(rid) == "active"
    assert [mundo.store.result(rid, False) for _ in range(3)][-1] is True and mundo.status(rid) == "quarantined"
    assert pg.commits_perdidos == 0 and mundo.trilha(rid) == []              # só a trilha saiu, três vezes
    pg.falhar_em = None
    v2 = mundo.salva(commit=False, learned_from="s2")                        # e a trilha volta a entrar junto
    assert v2 and mundo.trilha(v2) == [(None, "candidate", "sistema")]


def test_veto_que_falha_ao_ler_nao_derruba_a_receita(mundo: Mundo) -> None:
    """O veto só LÊ, mas lê dentro da transação da loja: uma consulta que falha também abortaria a transação no
    PostgreSQL. Ilegível não veta (o lado de sempre), e a receita e a trilha entram."""
    if mundo.db.dialect != "sqlite":
        pytest.skip("o embrulho imita o PostgreSQL sobre o SQLite")
    pg = embrulhar(mundo.db)
    pg.falhar_em = "FROM learning_transitions WHERE content_hash=?"
    rid = mundo.salva(commit=False)
    assert rid and mundo.status(rid) == "candidate" and pg.commits_perdidos == 0
    assert mundo.trilha(rid) == [(None, "candidate", "sistema")]
    assert "ROLLBACK TO SAVEPOINT sp_1" in savepoints(pg)


def test_execucao_de_origem_que_falha_ao_ler_nao_derruba_a_receita(mundo: Mundo) -> None:
    """Revisor do 22.5: a execução de origem da receita que nasce também é lida dentro da transação da loja, e tem de
    ser lida SOB o savepoint da trilha — fora dele (mesmo dentro do `try`), a consulta que falha abortaria a transação
    no PostgreSQL. Sem a origem não há trilha (a mesma falha), mas a receita nasce."""
    if mundo.db.dialect != "sqlite":
        pytest.skip("o embrulho imita o PostgreSQL sobre o SQLite")
    pg = embrulhar(mundo.db)
    pg.falhar_em = "JOIN steps s ON s.id = r.learned_from_step"                  # `_execucao_de_origem`
    rid = mundo.salva(commit=False)
    assert rid and mundo.status(rid) == "candidate" and pg.commits_perdidos == 0
    assert mundo.trilha(rid) == []
    assert savepoints(pg).count("ROLLBACK TO SAVEPOINT sp_1") == 1                # o do veto passou; o da trilha, não
    assert not any("INSERT INTO learning_transitions" in i for i in pg.instrucoes)   # falhou antes da trilha


# ------------------------------------------------------------------ a rota antiga passa pelo livro (Harness)
def _cliente(h: Harness, *, base: str = "http://test") -> httpx.AsyncClient:
    """`base="http://127.0.0.1"` para a sessão, como em `test_sessao_do_painel`: o login do loopback dispensa token,
    e o jar do cliente devolve o cookie nas requisições seguintes."""
    from app.main import create_app

    app = create_app(h.cfg, state=h.state)
    app.state.poc = h.state
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=base)


async def test_pela_rota_antiga_a_quarentena_da_pessoa_veta_o_caminho(tmp_path: Path) -> None:
    """`PUT /api/recipes/{id}` grava a trilha com a pessoa. Antes não gravava: a candidata que ela pôs em quarentena
    renascia da próxima execução em que a IA fizesse o mesmo caminho (nova versão, a dela substituída)."""
    h = Harness(tmp_path, 1)
    await h.boot()
    try:
        s = h.state
        assert s is not None
        mundo = Mundo(s.db)
        rid = mundo.salva(commit=False)                                      # candidata em prova
        assert rid and mundo.status(rid) == "candidate"
        s.db.execute("UPDATE recipes SET consecutive_fail=2, nao_aplicavel_seguidas=2 WHERE id=?", (rid,))
        async with _cliente(h) as c:
            r = await c.put(f"/api/recipes/{rid}", json={"status": "quarantined"})
            assert r.status_code == 200 and r.json() == {"id": rid, "status": "quarantined"}   # a resposta de sempre
            assert (await c.put(f"/api/recipes/{rid}", json={"status": "disabled"})).status_code == 400
            nada = await c.put("/api/recipes/987654", json={"status": "quarantined"})
            assert nada.status_code == 404 and nada.json()["detail"]["code"] == "not_found"
        assert mundo.status(rid) == "quarantined"
        assert s.db.scalar("SELECT consecutive_fail FROM recipes WHERE id=?", (rid,)) == 0   # o que a rota sempre fez
        assert s.db.scalar("SELECT nao_aplicavel_seguidas FROM recipes WHERE id=?", (rid,)) == 0   # 30.80
        assert mundo.trilha(rid)[-1] == ("candidate", "disabled", "panel")
        # a IA comprova de novo o MESMO caminho: não renasce candidata (veto da pessoa)
        assert mundo.salva(commit=False, learned_from="s2") is None
        assert s.db.scalar("SELECT COUNT(*) FROM recipes") == 1
        # reativar pela rota é da pessoa: a trilha registra, e o veto dela se desfaz
        async with _cliente(h) as c:
            assert (await c.put(f"/api/recipes/{rid}", json={"status": "active"})).status_code == 200
        assert mundo.status(rid) == "active" and mundo.trilha(rid)[-1] == ("disabled", "published", "panel")
        # a substituída não volta pela rota (o painel já não oferecia o botão)
        outro = mundo.salva("rolar", commit=False, learned_from="s3")
        assert outro
        s.db.execute("UPDATE recipes SET status='superseded' WHERE id=?", (outro,))
        async with _cliente(h) as c:
            volta = await c.put(f"/api/recipes/{outro}", json={"status": "active"})
            assert volta.status_code == 409 and volta.json()["detail"]["code"] == "transition_forbidden"
        assert mundo.status(outro) == "superseded"
    finally:
        if h.state is not None:
            await h.state.stop()


async def test_pela_rota_antiga_a_trilha_diz_o_operador_e_o_gesto_recusado_no_meio_nao_deixa_nada(
        tmp_path: Path) -> None:
    """Com sessão aberta, quem decide na trilha é o nome do login (o `_quem` do livro), não `panel`. E reativar a
    candidata são dois passos do livro (candidata → validada → ativa) num gesto só, na transação da rota: o segundo
    é recusado (outra ativa na mesma chave: nunca duas), e o primeiro também não fica — nem o status, nem a linha na
    trilha, nem o zerar das falhas seguidas."""
    h = Harness(tmp_path, 1)
    await h.boot()
    try:
        s = h.state
        assert s is not None
        mundo = Mundo(s.db)
        for versao, status in ((1, "active"), (2, "candidate")):
            s.db.execute("INSERT INTO recipes(app_package, app_version, app_signature, variant, step_hash, step_key,"
                         " version, status, actions, learned_from_step, consecutive_fail, created_at)"
                         " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                         (PKG, "1.0(1)", "", "en-US/xhdpi", "h-enviar", "enviar", versao, status,
                          json.dumps(_acoes(commit=False)), f"s{versao}", 2, TS))
        ativa, candidata = (int(s.db.scalar("SELECT id FROM recipes WHERE step_hash='h-enviar' AND version=?", (v,)))
                            for v in (1, 2))
        async with _cliente(h, base="http://127.0.0.1") as c:
            entrada = await c.post("/api/login", json={"operator": NOME})
            assert entrada.status_code == 200, entrada.text
            recusa = await c.put(f"/api/recipes/{candidata}", json={"status": "active"})
            assert recusa.status_code == 409, recusa.text
            assert recusa.json()["detail"]["code"] == "state_conflict"
            assert mundo.status(candidata) == "candidate" and mundo.trilha(candidata) == []
            assert s.db.scalar("SELECT consecutive_fail FROM recipes WHERE id=?", (candidata,)) == 2
            quarentena = await c.put(f"/api/recipes/{candidata}", json={"status": "quarantined"})
            assert quarentena.status_code == 200, quarentena.text
        assert mundo.trilha(candidata) == [("candidate", "disabled", NOME)]
        assert mundo.status(ativa) == "active" and mundo.trilha(ativa) == []
    finally:
        if h.state is not None:
            await h.state.stop()
