"""RA-19, fatia B (leitura 2, decidida pela orquestradora em 03/10): o que uma execução SIMULADA ensina não publica.

- origem simulada nunca NASCE ativa: a receita nem com `ai.recipes_promote_after: 0`, o fluxo nem com
  `aprendizado.fluxo.com_prova: false`;
- a concordância de uma execução simulada não promove receita (`RecipeStore.shadow(simulada=True)`); a divergência dela
  zera a prova, como qualquer outra;
- a sombra continua promovendo com evidência REAL (o fluxo já só contava execução real), e a pessoa promove à mão;
- `aprendizado.simulada_publica: true` é o modo anterior, só da suíte (o Harness é todo simulado).

A prova de aceite é a consulta de join: nenhuma receita ou fluxo de origem simulada em `active`/`validated` depois de
execuções que só tiveram evidência simulada.

Nível de prova: `simulated` (banco de teste e Harness na porta 5640, aparelhos e IA falsos).
"""
from __future__ import annotations

from typing import Any

from app.modules.learning.application.nativos import D1Nativo
from app.taskqueue.recipes import RecipeStore
from app.util import now

from .conftest import Harness
from .test_d1_fluxos import Mundo, _comando, mundo  # noqa: F401 - `mundo` é a fixture do mundo do D1 dos fluxos

PKG = "com.pocqa.messenger"

#: A consulta do aceite: conhecimento de origem simulada que passou a agir (ou espera só o dono para agir).
JOIN_RECEITAS = ("SELECT COUNT(*) FROM recipes r JOIN steps s ON s.id=r.learned_from_step JOIN runs ru ON ru.id=s.run_id"
                 " WHERE ru.simulated=1 AND r.status IN ('active','validated')")
JOIN_FLUXOS = ("SELECT COUNT(*) FROM flows f JOIN runs ru ON ru.id=f.source_run_id"
               " WHERE ru.simulated=1 AND f.status IN ('active','validated')")


def _regra_de_producao(h: Harness) -> None:
    """O Harness é todo simulado; a suíte liga o modo anterior (`conftest`). Aqui vale a regra de produção, com as
    receitas reproduzindo e os fluxos ligados, e os dois atalhos do modo anterior abertos de propósito."""
    h.cfg.file.aprendizado.simulada_publica = False
    h.cfg.file.ai.recipes = "replay"
    h.cfg.file.ai.flows = True
    assert h.cfg.file.ai.recipes_promote_after == 0 and not h.cfg.file.aprendizado.fluxo.com_prova


# ------------------------------------------------------------------ ponta a ponta (executor + lojas + D1)
async def test_execucao_simulada_nao_publica_receita_nem_fluxo_mesmo_com_os_atalhos_abertos(harness: Harness) -> None:
    _regra_de_producao(harness)
    db = harness.state.db                                                          # type: ignore[union-attr]
    primeira = await harness.wait_run(harness.run(["android-01"]).id)
    assert primeira.status == "completed"
    assert db.scalar("SELECT simulated FROM runs WHERE id=?", (primeira.id,)) == 1  # o Harness é simulado
    receitas = {r["step_key"]: r["status"] for r in db.query("SELECT step_key, status FROM recipes")}
    assert {"open_conversation", "compose_message", "send_message"} <= set(receitas)
    assert set(receitas.values()) == {"candidate"}                  # nasceria ativa com `recipes_promote_after: 0`
    assert [f["status"] for f in db.query("SELECT status FROM flows")] == ["candidate"]   # nasceria ativo sem prova

    # a segunda execução, também simulada, faz o mesmo caminho: concorda com as candidatas, e nada sobe
    harness.ai.calls.clear()
    segunda = await harness.wait_run(harness.run(["android-02"]).id)
    assert segunda.status == "completed"
    assert harness.ai.count("plan") == 1                            # o fluxo candidato não é reaproveitado
    assert harness.ai.count("decide", step="send_message") > 0      # a candidata não reproduz: a IA decide
    assert {r["status"] for r in db.query("SELECT status FROM recipes")} == {"candidate"}
    assert db.scalar("SELECT MAX(shadow_agree) FROM recipes") == 0  # a concordância simulada não soma à prova
    # o aceite
    assert db.scalar(JOIN_RECEITAS) == 0 and db.scalar(JOIN_FLUXOS) == 0


async def test_modo_anterior_da_suite_segue_publicando(harness: Harness) -> None:
    """Controle: com `simulada_publica` (o `conftest`), a mesma execução publica como antes; o teste acima mede a
    regra, e não um Harness que deixou de aprender."""
    harness.cfg.file.ai.recipes = "replay"
    assert harness.cfg.file.aprendizado.simulada_publica
    run = await harness.wait_run(harness.run(["android-01"]).id)
    assert run.status == "completed"
    assert harness.state.db.scalar(JOIN_RECEITAS) > 0                               # type: ignore[union-attr]


# ------------------------------------------------------------------ a sombra da receita
def _candidata(store: RecipeStore, passo: str) -> int:
    acoes = [{"tool": "tap", "commit": False, "why": "abrir", "args": {}, "selectors": [{"kind": "rid", "rid": "b"}]}]
    rid = store.save(package=PKG, app_version="1.0(1)", step_hash=f"h-{passo}", step_key=passo, actions=acoes,
                     learned_from="s1", signature="", variant="en-US/xhdpi", candidate=True)
    assert rid is not None
    return rid


def _status(store: RecipeStore, rid: int) -> Any:
    return store.db.scalar("SELECT status FROM recipes WHERE id=?", (rid,))


async def test_concordancia_simulada_nao_promove_e_a_real_promove(harness: Harness) -> None:
    store = RecipeStore(harness.state.db)                                          # type: ignore[union-attr]
    rid = _candidata(store, "abrir")
    for _ in range(3):
        assert store.shadow(rid, True, promote_after=1, simulada=True) is False
    assert _status(store, rid) == "candidate"
    assert store.db.scalar("SELECT shadow_agree FROM recipes WHERE id=?", (rid,)) == 0
    assert store.shadow(rid, True, promote_after=1) is True                  # a primeira concordância REAL promove
    assert _status(store, rid) == "active"


async def test_divergencia_simulada_zera_a_prova_da_candidata(harness: Harness) -> None:
    store = RecipeStore(harness.state.db)                                          # type: ignore[union-attr]
    rid = _candidata(store, "enviar")
    assert store.shadow(rid, True, promote_after=2) is False                 # 1 real de 2
    assert store.shadow(rid, False, promote_after=2, simulada=True) is False  # a simulada divergiu: recomeça
    assert store.shadow(rid, True, promote_after=2) is False                 # 1 de 2 de novo
    assert _status(store, rid) == "candidate"
    assert store.shadow(rid, True, promote_after=2) is True
    assert _status(store, rid) == "active"


# ------------------------------------------------------------------ o nascimento do fluxo (D1)
def test_fluxo_de_execucao_simulada_nao_publica(mundo: Mundo) -> None:  # noqa: F811 - a fixture importada
    """Sem a prova (`com_prova: false`), o fluxo de execução REAL nasce ativo (o modo anterior) e o de execução
    SIMULADA nasce candidato; aí só as execuções reais que o texto promete o publicam (a sombra só conta real)."""
    mundo.config["com_prova"] = False
    real = mundo.roda("r-real", "@nasa", comando="abrir o perfil de @nasa no instagram e só olhar")
    assert real and mundo.status(real) == "active"
    simulado = mundo.roda("r-sim", "@nasa", simulada=True)
    assert simulado and mundo.status(simulado) == "candidate"
    assert mundo.flows.match(_comando("@spacex")) is None                     # candidato não é reaproveitado
    assert mundo.db.scalar(JOIN_FLUXOS) == 0
    # a sombra só conta execução real: 1 + concordancias reais com o mesmo plano o publicam (sem efeito externo)
    mundo.roda("r-2", "@spacex")
    mundo.roda("r-3", "@esa")
    assert mundo.status(simulado) == "active"


def test_nascimento_do_fluxo_pela_origem() -> None:
    """A regra no D1, sem banco: `run_id` real ou simulado, com e sem o modo anterior, com e sem a prova."""
    def d1(*, com_prova: bool, simulada_publica: bool) -> D1Nativo:
        return D1Nativo(None, com_prova=lambda: com_prova, relogio=now,          # type: ignore[arg-type]
                        execucao_real=lambda run: run == "r-real", simulada_publica=lambda: simulada_publica)

    sem_prova = d1(com_prova=False, simulada_publica=False)
    assert sem_prova.fluxo_ao_nascer("k", None, None, "r-real") == "active"
    assert sem_prova.fluxo_ao_nascer("k", None, None, "r-sim") == "candidate"
    assert sem_prova.fluxo_ao_nascer("k", None, None, None) == "active"         # sem origem: o modo anterior
    assert d1(com_prova=False, simulada_publica=True).fluxo_ao_nascer("k", None, None, "r-sim") == "active"
    assert d1(com_prova=True, simulada_publica=False).fluxo_ao_nascer("k", None, None, "r-real") == "candidate"
