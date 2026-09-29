"""Medida de efeito das lições (ADR-054, decisão 5; pacote A7): braço de controle, exposição, desfecho, veredito
durável e aposentadoria.

O que se prova:
- o braço é por ETAPA (ator) ou por planejamento, determinístico e equilibrado (50% em prova, 10% de controle depois
  de "ajuda"); a repetição da mesma etapa não troca de braço nem grava outra exposição;
- uma lição em prova por (app, ação, papel) de cada vez; as outras esperam na fila;
- a tentativa conduzida pela receita não gera exposição (harness: aparelho falso e IA simulada), e a lição chega ao
  `DecisionRequest` do ator no braço `with`; no `shadow` nada vai ao prompt nem é gravado;
- o desfecho é preenchido no digest (status final, chamadas de IA pela tentativa, US$, segundos, replanejamento) —
  só em execução real: a exposição simulada nunca entra em veredito;
- os vereditos com amostras semeadas (ajuda por sucesso e por chamadas, atrapalha, neutra aos 20, "faltam N"
  abaixo de 8) e o efeito deles no livro, com a trilha que guarda os números;
- as aposentadorias (sem exposição há 60 dias, 2 refutações humanas, absorvida pelo repositório), a versão nova do app
  devolvendo a lição à prova e a proposta `promover_licao` (idempotente, a chave do A3).

Nível de prova: `simulated` (banco de teste; harness da porta 5640; nenhuma chamada de IA).
"""
from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from app.db import Database
from app.modules.learning.domain.ciclo import SkillState
from app.modules.learning.domain.efeito import (Amostra, Efeito, Exposicao, aposentadoria, braco, fracao,
                                                propor_promocao, veredito_de_efeito, volta_a_prova)
from app.modules.learning.domain.licoes import Pedido
from app.modules.learning.domain.livro import Escopo, ItemDeAprendizado, NovoItem
from app.modules.learning.domain.vocabulario import Braco, LivroKind, Papel, SourceKind
from app.modules.learning.infrastructure.sql_repository import SqlLearningRepository
from app.planning.provider import DecisionRequest
from app.util import to_iso

from .conftest import Harness
from .fake_skills import banco as banco_migrado
from .test_learning_licoes import AGORA, IG, Mundo, contraste_ciclo

QA = "com.pocqa.messenger"


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    d = banco_migrado(tmp_path, "efeito.sqlite3")
    yield d
    d.close()


@pytest.fixture
def mundo(db: Database) -> Mundo:
    return Mundo(db)


def licao(mundo: Mundo, texto: str = "Em OPEN_POST: lição x.", *, detalhe: str | None = "em_prova",
          capability: str = "OPEN_POST", papel: str = "actor", versao: str | None = "447",
          dias: float = 0.0) -> ItemDeAprendizado:
    """Uma lição publicada (por pessoa: a porta que o teste pode usar) com o detalhe e o `state_at` pedidos."""
    item = mundo.repo.criar_item(
        NovoItem(kind=LivroKind.LICAO, escopo=Escopo(app=IG, capability=capability, role=papel),
                 content={"texto": texto}, summary=texto, source_kind=SourceKind.RECOVERY, side_effect=False,
                 app_version=versao), by="painel", estado=SkillState.PUBLISHED, detalhe=detalhe, reason="teste")
    mundo.db.execute("UPDATE learning_items SET state_at=? WHERE id=?", (to_iso(AGORA - timedelta(days=dias)),
                                                                          item.id))
    atual = mundo.repo.item(item.id)
    assert atual is not None
    return atual


def exposto(db: Database, item_id: str, unidade: str, arm: str, *, outcome: str | None = "succeeded",
            chamadas: int = 1, horas: float = 1.0, run_id: str = "r-x") -> None:
    """Uma unidade já preenchida (o que o digest teria gravado)."""
    quando = to_iso(AGORA - timedelta(hours=horas))
    db.execute("INSERT INTO learning_exposures(item_id, unit_id, role, arm, tokens, run_id, app_package, capability,"
               " created_at, outcome, ai_calls, usd, seconds, replanned, filled_at)"
               " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (item_id, unidade, "actor", arm, 20 if arm == "with" else 0, run_id, IG, "OPEN_POST", quando, outcome,
                chamadas, 0.01 * chamadas, 30.0, 0, quando if outcome else None))


def amostras(db: Database, item_id: str, *, com: tuple[int, int], sem: tuple[int, int], chamadas_com: int = 2,
             chamadas_sem: int = 2) -> None:
    """(unidades, sucessos) por braço."""
    for braco_, (n, ok), chamadas in (("with", com, chamadas_com), ("holdout", sem, chamadas_sem)):
        for i in range(n):
            exposto(db, item_id, f"step:{braco_}-{i}", braco_, outcome="succeeded" if i < ok else "failed",
                    chamadas=chamadas, horas=1 + i * 0.01)


def pedido(unidade: str = "step:s1", *, simulated: bool = False) -> Pedido:
    return Pedido(papel=Papel.ACTOR, unidade=unidade, run_id="r-x", app=IG, capability="OPEN_POST", step_hash="h",
                  simulated=simulated, objective_id="r-x:android-06", step_id=unidade.removeprefix("step:"))


# ================================================================== braço
def test_braco_por_etapa_deterministico_e_equilibrado(mundo: Mundo) -> None:
    unidades = [f"step:r{i}:android-06:v1:abrir" for i in range(4000)]
    em_prova = Counter(braco("li-abc", u, "em_prova", holdout_publicada=0.1) for u in unidades)
    assert 0.47 < em_prova[Braco.WITH] / 4000 < 0.53                              # 50% com, 50% sem
    ajuda = Counter(braco("li-abc", u, "medida:ajuda", holdout_publicada=0.1) for u in unidades)
    assert 0.08 < ajuda[Braco.HOLDOUT] / 4000 < 0.12                              # 10% de controle
    assert all(braco("li-abc", u, "em_prova", holdout_publicada=0.1) == braco("li-abc", u, "em_prova",
                                                                              holdout_publicada=0.1)
               for u in unidades[:50])
    assert braco("li-abc", "step:x", "fila_de_prova", holdout_publicada=0.1) is None
    assert braco("li-abc", "step:x", None, holdout_publicada=0.1) is None
    esperado = int(hashlib.sha1(b"li-abc|step:x").hexdigest()[:8], 16) / 0x1_0000_0000
    assert fracao("li-abc", "step:x") == esperado                                  # sha1(item|unidade)

    # no banco: duas tentativas da mesma etapa → UMA exposição, o mesmo braço e as mesmas lições
    item = licao(mundo)
    primeira = mundo.licoes.licoes_para(pedido())
    segunda = mundo.licoes.licoes_para(pedido())
    assert primeira == segunda
    linhas = mundo.db.query("SELECT arm, tokens, unit_id, run_id FROM learning_exposures WHERE item_id=?", (item.id,))
    assert len(linhas) == 1 and linhas[0]["unit_id"] == "step:s1" and linhas[0]["run_id"] == "r-x"
    arm = braco(item.id, "step:s1", "em_prova", holdout_publicada=0.1)
    assert linhas[0]["arm"] == arm.value if arm else False
    assert (primeira == [item.summary]) == (arm is Braco.WITH)
    assert (linhas[0]["tokens"] > 0) == (arm is Braco.WITH)                       # 0 token no controle


def test_uma_licao_em_prova_por_escopo(mundo: Mundo) -> None:
    a = licao(mundo, "Em OPEN_POST: a.", detalhe=None, dias=3)
    b = licao(mundo, "Em OPEN_POST: b.", detalhe=None, dias=2)
    c = licao(mundo, "Em OPEN_POST: c.", detalhe="fila_de_prova", dias=1)
    d = licao(mundo, "Em LIKE_POST: d.", detalhe=None, capability="LIKE_POST")
    p = licao(mundo, "Em com.instagram.android: p.", detalhe=None, capability="", papel="planner")
    mundo.servico.curar()
    detalhes = {i.id: mundo.repo.item(i.id).state_detail for i in (a, b, c, d, p)}  # type: ignore[union-attr]
    assert detalhes == {a.id: "em_prova", b.id: "fila_de_prova", c.id: "fila_de_prova", d.id: "em_prova",
                        p.id: "em_prova"}
    mundo.servico.curar()                                                          # idempotente
    assert sum(1 for i in (a, b, c) if mundo.repo.item(i.id).state_detail == "em_prova") == 1  # type: ignore[union-attr]
    # só a em prova (e as com ajuda medida) vão ao prompt; a da fila espera
    textos = set()
    for n in range(40):
        textos.update(mundo.licoes.licoes_para(pedido(f"step:u{n}")))
    assert textos == {a.summary}


# ================================================================== consumo e exposição (harness)
async def test_tentativa_de_receita_sem_exposicao_e_licao_no_ator(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    harness.cfg.file.ai.recipes = "replay"
    harness.cfg.file.aprendizado.licoes.modo = "on"
    harness.cfg.file.aprendizado.licoes.holdout_publicada = 0.0                  # toda unidade no braço `with`
    texto = "Nesta etapa: a tentativa que comprovou começou tocando em [id=tab_home]."
    SqlLearningRepository(st.db).criar_item(
        NovoItem(kind=LivroKind.LICAO, escopo=Escopo(app=QA, role="actor"), content={"t": 1}, summary=texto,
                 source_kind=SourceKind.RECOVERY, side_effect=False),
        by="painel", estado=SkillState.PUBLISHED, detalhe="medida:ajuda", reason="teste")
    decisoes: list[list[str]] = []
    decide = harness.ai.decide

    async def decide_espiado(req: DecisionRequest) -> Any:
        decisoes.append(list(req.lessons))
        return await decide(req)

    harness.ai.decide = decide_espiado          # type: ignore[method-assign]
    r1 = await harness.wait_run(harness.run(["android-01"]).id)                   # a IA conduz tudo
    assert r1.status == "completed"
    assert decisoes and all(licoes == [texto] for licoes in decisoes)              # o bloco chega ao ator
    com_ia = {r["step_id"] for r in st.db.query(
        "SELECT DISTINCT t.step_id FROM actions a JOIN attempts t ON t.id=a.attempt_id JOIN steps s ON s.id=t.step_id"
        " WHERE s.run_id=? AND a.source='ai'", (r1.id,))}
    expostas = st.db.query("SELECT unit_id, arm, filled_at FROM learning_exposures WHERE run_id=?", (r1.id,))
    assert {e["unit_id"] for e in expostas} == {f"step:{s}" for s in com_ia}      # uma por etapa consultada
    assert all(e["arm"] == "with" for e in expostas)
    await harness.wait(lambda: not st._digestoes, 10, "digest encerrado")
    assert all(e["filled_at"] is None for e in st.db.query("SELECT filled_at FROM learning_exposures"))  # simulada

    r2 = await harness.wait_run(harness.run(["android-02"]).id)                   # as receitas conduzem
    assert r2.status == "completed"
    da_receita = {r["id"] for r in st.db.query("SELECT id FROM steps WHERE run_id=? AND driven_by='recipe'",
                                               (r2.id,))}
    assert da_receita, "o cenário precisa de etapas conduzidas pela receita"
    expostas2 = {r["unit_id"] for r in st.db.query("SELECT unit_id FROM learning_exposures WHERE run_id=?",
                                                    (r2.id,))}
    assert not expostas2 & {f"step:{s}" for s in da_receita}                       # receita: sem exposição


def test_no_shadow_nada_vai_ao_prompt_nem_e_gravado(db: Database) -> None:
    sombra = Mundo(db, modo="shadow")
    item = licao(sombra, detalhe="medida:ajuda")
    assert sombra.licoes.licoes_para(pedido()) == []
    assert db.scalar("SELECT COUNT(*) FROM learning_exposures") == 0
    assert ("licao.sombra", 1, {"papel": "actor"}) in sombra.metricas
    ligado = Mundo(db)
    assert ligado.licoes.licoes_para(pedido("step:s2")) in ([item.summary], [])
    assert db.scalar("SELECT COUNT(*) FROM learning_exposures") == 1


# ================================================================== desfecho no digest
def test_desfecho_preenchido_no_digest_so_em_execucao_real(mundo: Mundo) -> None:
    db = mundo.db
    sid = contraste_ciclo(db, "r1")                                               # 2 tentativas, a 2ª comprovou
    tentativas = [r["id"] for r in db.query("SELECT id FROM attempts WHERE step_id=? ORDER BY number", (sid,))]
    for aid, n, usd in ((tentativas[0], 3, 0.03), (tentativas[1], 2, None)):
        for _ in range(n):
            db.execute("INSERT INTO ai_calls(ts, run_id, objective_id, step_id, role, model, tier, input_tokens,"
                       " output_tokens, attempt_id, usd) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                       (to_iso(AGORA - timedelta(days=1)), "r1", "r1:android-06", sid, "decide", "modelo-x", 0,
                        1000, 100, aid, None if usd is None else usd / n))
    db.execute("INSERT INTO ai_calls(ts, run_id, role, model, tier, input_tokens, output_tokens, usd)"
               " VALUES (?,?,?,?,?,?,?,?)", (to_iso(AGORA - timedelta(days=1)), "r1", "plan", "modelo-x", 0, 10, 10,
                                             0.5))
    item = licao(mundo)
    plano = licao(mundo, "Em com.instagram.android: p.", capability="", papel="planner")
    mundo.licoes.licoes_para(Pedido(papel=Papel.ACTOR, unidade=f"step:{sid}", run_id="r1", app=IG,
                                    capability="OPEN_POST", step_hash="h-abrir", simulated=False,
                                    objective_id="r1:android-06", step_id=sid))
    mundo.licoes.licoes_para(Pedido(papel=Papel.PLANNER, unidade="plan:r1", run_id="r1", app=IG, capability="",
                                    step_hash="", simulated=False))
    relatorio = mundo.servico.digerir_execucao("r1")
    assert relatorio.falhas == () and relatorio.feito["licoes.exposicoes"] == 2
    [ator] = mundo.repo.exposicoes(item.id)
    assert ator.preenchida and ator.outcome == "succeeded" and ator.failure_kind is None
    assert ator.ai_calls == 5 and ator.seconds == 60.0 and ator.replanned is False
    assert abs((ator.usd or 0) - (0.03 + 2 * (1000 * 1.0 + 100 * 5.0) / 1_000_000)) < 1e-9   # gravado + tabela
    [planejador] = mundo.repo.exposicoes(plano.id)
    assert planejador.outcome == "completed" and planejador.ai_calls == 6 and planejador.seconds == 600.0
    assert mundo.servico.digerir_execucao("r1").feito["licoes.exposicoes"] == 0       # idempotente

    # a simulada é exposta (a lição foi ao prompt), mas nunca preenchida: não entra em veredito
    contraste_ciclo(db, "r-sim", instancia="android-07", simulated=True)
    mundo.licoes.licoes_para(Pedido(papel=Papel.ACTOR, unidade="step:r-sim:android-07:v1:abrir", run_id="r-sim",
                                    app=IG, capability="OPEN_POST", step_hash="h-abrir", simulated=True))
    assert mundo.servico.digerir_execucao("r-sim").feito["licoes.exposicoes"] == 0
    assert db.scalar("SELECT COUNT(*) FROM learning_exposures WHERE run_id='r-sim' AND filled_at IS NULL") == 1
    assert db.scalar("SELECT last_used_at FROM learning_items WHERE id=?", (item.id,)) in (
        None, to_iso(AGORA))                                                        # simulada não renova o uso


def test_a_curadoria_preenche_o_que_o_digest_perdeu(mundo: Mundo) -> None:
    sid = contraste_ciclo(mundo.db, "r1", dias_atras=0.5)
    item = licao(mundo)
    mundo.licoes.licoes_para(Pedido(papel=Papel.ACTOR, unidade=f"step:{sid}", run_id="r1", app=IG,
                                    capability="OPEN_POST", step_hash="h-abrir", simulated=False))
    mundo.servico.curar()                                                          # sem digest (processo caiu)
    assert all(e.preenchida for e in mundo.repo.exposicoes(item.id))


# ================================================================== vereditos
def _unidades(arm: Braco, n: int, ok: int, chamadas: int = 2) -> list[Exposicao]:
    return [Exposicao(item_id="li-x", unit_id=f"step:{arm.value}{i}", role="actor", arm=arm, tokens=10,
                      run_id="r", objective_id=None, app_package=IG, capability="OPEN_POST",
                      created_at=f"2026-09-2{i // 10}T00:00:{i % 60:02d}.000Z",
                      outcome="succeeded" if i < ok else "failed", ai_calls=chamadas, filled_at="x")
            for i in range(n)]


def test_vereditos_com_amostras_semeadas() -> None:
    ajuda = veredito_de_efeito(_unidades(Braco.WITH, 10, 9) + _unidades(Braco.HOLDOUT, 10, 6))
    assert ajuda.efeito is Efeito.AJUDA and ajuda.delta_sucesso_pp == 30.0 and ajuda.faltam == 0
    mais_barata = veredito_de_efeito(_unidades(Braco.WITH, 8, 6, chamadas=2) + _unidades(Braco.HOLDOUT, 8, 6, 4))
    assert mais_barata.efeito is Efeito.AJUDA and mais_barata.delta_p50 == -2.0
    atrapalha = veredito_de_efeito(_unidades(Braco.WITH, 10, 4) + _unidades(Braco.HOLDOUT, 10, 8))
    assert atrapalha.efeito is Efeito.ATRAPALHA
    cara = veredito_de_efeito(_unidades(Braco.WITH, 10, 10, chamadas=5) + _unidades(Braco.HOLDOUT, 10, 9, 3))
    assert cara.efeito is Efeito.ATRAPALHA                                  # +10 pp E +2 chamadas: rebaixar vence
    parecida = _unidades(Braco.WITH, 12, 9) + _unidades(Braco.HOLDOUT, 12, 9)
    assert veredito_de_efeito(parecida).efeito is None                      # ainda não chegou aos 20
    neutra = veredito_de_efeito(_unidades(Braco.WITH, 25, 15) + _unidades(Braco.HOLDOUT, 20, 15))
    assert neutra.efeito is Efeito.NEUTRA and neutra.com.unidades == 20     # no máximo 20 por braço
    sem_desfecho = [Exposicao(**{**{f: getattr(e, f) for f in e.__dataclass_fields__}, "filled_at": None})
                    for e in _unidades(Braco.WITH, 10, 10)]
    assert veredito_de_efeito(sem_desfecho + _unidades(Braco.HOLDOUT, 10, 0)).faltam == 8   # só preenchidas contam
    assert "efeito ajuda" in ajuda.resumo() and "9/10" in ajuda.resumo() and "6/10" in ajuda.resumo()


def test_faltam_n_abaixo_de_8_por_braco() -> None:
    v = veredito_de_efeito(_unidades(Braco.WITH, 7, 7) + _unidades(Braco.HOLDOUT, 5, 0))
    assert v.efeito is None and v.faltam == 3 and "faltam 3" in v.resumo()
    assert veredito_de_efeito([]).faltam == 8
    assert Amostra(0, 0, None).taxa is None


def test_o_veredito_e_aplicado_e_fica_na_trilha(mundo: Mundo) -> None:
    db = mundo.db
    ajuda = licao(mundo, "Em OPEN_POST: ajuda.", dias=5)
    amostras(db, ajuda.id, com=(10, 9), sem=(10, 5))
    atrapalha = licao(mundo, "Em LIKE_POST: atrapalha.", capability="LIKE_POST", dias=5)
    amostras(db, atrapalha.id, com=(10, 3), sem=(10, 8))
    neutra = licao(mundo, "Em SAVE_POST: neutra.", capability="SAVE_POST", dias=5)
    amostras(db, neutra.id, com=(20, 15), sem=(20, 15))
    pouca = licao(mundo, "Em SHARE_POST: pouca.", capability="SHARE_POST", dias=5)
    amostras(db, pouca.id, com=(3, 3), sem=(8, 0))
    antiga = licao(mundo, "Em FOLLOW: antiga.", capability="FOLLOW", dias=0.02)   # a prova abriu há ~30 min
    amostras(db, antiga.id, com=(10, 10), sem=(10, 0))                   # exposições ANTES do começo da prova
    mundo.servico.curar()
    estado = {i.id: (mundo.repo.item(i.id).state, mundo.repo.item(i.id).state_detail)  # type: ignore[union-attr]
              for i in (ajuda, atrapalha, neutra, pouca, antiga)}
    assert estado == {ajuda.id: (SkillState.PUBLISHED, "medida:ajuda"),
                      atrapalha.id: (SkillState.DISABLED, "medida:atrapalha"),
                      neutra.id: (SkillState.DEPRECATED, "medida:neutra"),
                      pouca.id: (SkillState.PUBLISHED, "em_prova"),
                      antiga.id: (SkillState.PUBLISHED, "em_prova")}
    trilha = mundo.repo.trilha(ajuda.id)[-1]
    assert trilha.decided_by == "sistema" and trilha.reason.startswith("medida:ajuda: efeito ajuda")
    assert "9/10" in trilha.reason and "5/10" in trilha.reason              # o veredito durável: os números ficam
    assert "efeito atrapalha" in mundo.repo.trilha(atrapalha.id)[-1].reason
    assert mundo.licoes.medida(pouca) is not None and mundo.licoes.medida(pouca).faltam == 5  # type: ignore[union-attr]


# ================================================================== aposentadoria
def test_aposentadorias() -> None:
    agora = AGORA
    base = {"detalhe": "medida:ajuda", "desde": agora - timedelta(days=100), "agora": agora, "refutacoes": 0,
            "absorvida_em": None}
    assert aposentadoria(**base, ultima_exposicao=agora - timedelta(days=59)) is None  # type: ignore[arg-type]
    velha = aposentadoria(**base, ultima_exposicao=agora - timedelta(days=61))  # type: ignore[arg-type]
    assert velha is not None and velha.para is SkillState.DEPRECATED and "61 dias" in velha.motivo
    assert aposentadoria(**base, ultima_exposicao=None, sem_exposicao_dias=None) is None  # type: ignore[arg-type]
    refutada = aposentadoria(**{**base, "refutacoes": 2}, ultima_exposicao=agora)  # type: ignore[arg-type]
    assert refutada is not None and refutada.para is SkillState.DISABLED
    absorvida = aposentadoria(**{**base, "absorvida_em": "abc1234"}, ultima_exposicao=agora)  # type: ignore[arg-type]
    assert absorvida is not None and absorvida.para is SkillState.DEPRECATED
    assert absorvida.detalhe == "absorvida:abc1234"
    assert aposentadoria(**{**base, "detalhe": "fila_de_prova"}, ultima_exposicao=None) is None  # type: ignore[arg-type]


def test_aposentadorias_no_livro(mundo: Mundo) -> None:
    db = mundo.db
    parada = licao(mundo, "Em OPEN_POST: parada.", detalhe="medida:ajuda", dias=90)
    exposto(db, parada.id, "step:velha", "with", horas=24 * 70)
    refutada = licao(mundo, "Em LIKE_POST: refutada.", capability="LIKE_POST", detalhe="medida:ajuda", dias=5)
    for n in (1, 2):
        db.execute("INSERT INTO learning_evidence(item_ref, stance, origin_ref, run_id, simulated, observed_at)"
                   " VALUES (?,?,?,?,?,?)", (refutada.id, "against", f"signal:{n}", f"r{n}", 0, to_iso(AGORA)))
    absorvida = licao(mundo, "Em SAVE_POST: absorvida.", capability="SAVE_POST", detalhe="medida:ajuda", dias=20)
    chave = f"promover_licao|{absorvida.id}"
    db.execute("INSERT INTO learning_backlog(id, category, cluster_key, title, state, fixed_in_commit, first_seen,"
               " last_seen) VALUES (?,?,?,?,?,?,?,?)", ("fk-teste", "proposta", chave, "x", "fixed", "abc1234",
                                                        to_iso(AGORA), to_iso(AGORA)))
    viva = licao(mundo, "Em SHARE_POST: viva.", capability="SHARE_POST", detalhe="medida:ajuda", dias=90)
    exposto(db, viva.id, "step:recente", "with", horas=24 * 3)
    mundo.servico.curar()
    lido = {i.id: mundo.repo.item(i.id) for i in (parada, refutada, absorvida, viva)}
    assert (lido[parada.id].state, lido[parada.id].state_detail) == (SkillState.DEPRECATED, "medida:ajuda")  # type: ignore[union-attr]
    assert lido[refutada.id].state is SkillState.DISABLED  # type: ignore[union-attr]
    assert (lido[absorvida.id].state, lido[absorvida.id].state_detail) == (  # type: ignore[union-attr]
        SkillState.DEPRECATED, "absorvida:abc1234")
    assert lido[viva.id].state is SkillState.PUBLISHED  # type: ignore[union-attr]
    # no `shadow` não há exposição (sem prompt): a falta dela não aposenta ninguém
    sombra = Mundo(db, modo="shadow")
    esquecida = licao(sombra, "Em FOLLOW: esquecida.", capability="FOLLOW", detalhe="medida:ajuda", dias=200)
    sombra.servico.curar()
    assert sombra.repo.item(esquecida.id).state is SkillState.PUBLISHED  # type: ignore[union-attr]


def test_versao_nova_do_app_volta_a_em_prova(mundo: Mundo) -> None:
    db = mundo.db
    item = licao(mundo, detalhe="medida:ajuda", versao="447", dias=10)
    amostras(db, item.id, com=(10, 10), sem=(10, 0))
    assert not volta_a_prova("medida:ajuda", "447", "447") and not volta_a_prova("medida:ajuda", None, "448")
    assert volta_a_prova("em_prova", "447", "448") and not volta_a_prova("fila_de_prova", "447", "448")
    mundo.servico.curar()
    assert mundo.repo.item(item.id).state_detail == "medida:ajuda"  # type: ignore[union-attr]  # mesma versão
    # o parque em versões misturadas: vale a MAIS NOVA, não a do aparelho verificado por último
    for aparelho, versao, codigo, horas in (("android-06", "448.0", 448, 5), ("android-07", "447", 447, 1)):
        db.execute("INSERT INTO device_app_state(instance_id, package_name, observed_version_name,"
                   " observed_version_code, verified_at) VALUES (?,?,?,?,?)",
                   (aparelho, IG, versao, codigo, to_iso(AGORA - timedelta(hours=horas))))
    mundo.servico.curar()
    novo = mundo.repo.item(item.id)
    assert novo is not None and novo.state_detail == "em_prova" and novo.app_version == "448.0"
    mundo.servico.curar()                                              # e não alterna a cada verificação
    assert sum(1 for t in mundo.repo.trilha(item.id) if "versão nova do app" in t.reason) == 1
    # as exposições de antes não valem na prova nova (a janela começa no `state_at`)
    assert mundo.licoes.medida(novo) is not None and mundo.licoes.medida(novo).faltam == 8  # type: ignore[union-attr]


def test_proposta_promover_licao(mundo: Mundo) -> None:
    ha_15 = licao(mundo, "Em OPEN_POST: quinze.", detalhe="medida:ajuda", dias=15)
    exposto(mundo.db, ha_15.id, "step:recente", "with", horas=5)
    ha_13 = licao(mundo, "Em LIKE_POST: treze.", capability="LIKE_POST", detalhe="medida:ajuda", dias=13)
    exposto(mundo.db, ha_13.id, "step:recente", "with", horas=5)
    assert propor_promocao("medida:ajuda", AGORA - timedelta(days=14), AGORA)
    assert not propor_promocao("em_prova", AGORA - timedelta(days=40), AGORA)
    mundo.servico.curar()
    mundo.servico.curar()                                                          # idempotente
    linhas = mundo.db.query("SELECT id, category, cluster_key, state, title FROM learning_backlog")
    chave = f"promover_licao|{ha_15.id}"
    esperado = "fk-" + hashlib.sha1(chave.encode()).hexdigest()[:10]
    assert [(r["id"], r["category"], r["cluster_key"], r["state"]) for r in linhas] == [
        (esperado, "proposta", chave, "open")]
    assert ha_15.id in linhas[0]["title"]


def test_licao_de_etapa_com_efeito_nunca_vai_a_prova_sem_o_dono(mundo: Mundo) -> None:
    """A lição de etapa com efeito externo nunca é publicada pelo sistema, nem entra em prova (D1)."""
    contraste_ciclo(mundo.db, "r1", efeito=True)
    contraste_ciclo(mundo.db, "r2", instancia="android-07", efeito=True)
    for r in ("r1", "r2"):
        mundo.licoes.minerar_contrastes(r)
    mundo.servico.curar()
    [item] = mundo.repo.itens(kind=LivroKind.LICAO)
    assert item.state is SkillState.VALIDATED and item.state_detail is None
    assert mundo.licoes.licoes_para(pedido()) == []
    assert mundo.db.scalar("SELECT COUNT(*) FROM learning_exposures") == 0
