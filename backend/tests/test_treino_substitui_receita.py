"""30.79 (B1 do mapa do ensino): a demonstração da pessoa no modo treinamento SUBSTITUI a receita que segura a chave.

Antes, `RecipeStore.save` descartava em silêncio a demonstração quando já havia receita ativa ou `validated` na chave
("a ativa só sai por quarentena"): a pessoa ensinava e nada mudava. Agora:
- a demonstração com caminho DIFERENTE vira a receita ativa, e a que segurava a chave (a ativa da IA, a `validated`
  que esperava o dono, ou a de uma demonstração anterior) sai como `superseded`, com a trilha nos dois lados assinada
  pela sessão de treino;
- com o MESMO caminho nada se grava: `save` devolve o id da que já vale;
- o salvamento que não é do treino segue como antes (não troca a ativa nem a `validated`);
- `viva()` diz qual segura a chave, e `previa_do_treino()` o que o `save` do treino faria, sem gravar;
- o reparo (31.86, `so_em_chave_virgem=True`) não herda esse poder: grava só em chave que nunca teve receita e sem
  veto da pessoa, conferido DENTRO da transação.

Nível de prova: `simulated` (banco de teste migrado pela fábrica da suíte; nenhum aparelho, nenhuma IA).
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from .fake_skills import banco as banco_migrado
from .test_d1_receitas import CHAVE, PKG, Mundo, _acoes

TREINO = "training:t1"


@pytest.fixture
def mundo(tmp_path: Path) -> Iterator[Mundo]:
    db = banco_migrado(tmp_path, "treino-substitui.sqlite3")
    yield Mundo(db)
    db.close()


def _viva(mundo: Mundo, passo: str = "enviar") -> int | None:
    row = mundo.store.viva(PKG, "1.0(1)", f"h-{passo}", **CHAVE)
    return None if row is None else int(row["id"])


def _motivo(mundo: Mundo, rid: int) -> str:
    return mundo.repo.trilha(f"receita:{rid}")[-1].reason


def test_demonstracao_diferente_substitui_a_ativa_da_ia(mundo: Mundo) -> None:
    da_ia = mundo.salva(commit=False, candidate=False, learned_from="r-ia")
    assert da_ia and mundo.status(da_ia) == "active"
    ensinada = mundo.salva(commit=False, candidate=False, learned_from=TREINO, rid="app:id/outro")
    assert ensinada and ensinada != da_ia
    assert mundo.status(da_ia) == "superseded" and mundo.status(ensinada) == "active"
    assert mundo.procura()["id"] == ensinada and _viva(mundo) == ensinada
    # a trilha nos dois lados, assinada pela sessão de treino (não pelo sistema)
    assert mundo.trilha(da_ia)[-1] == ("published", "deprecated", TREINO)
    assert "demonstrada pela pessoa" in _motivo(mundo, da_ia) and "v2" in _motivo(mundo, da_ia)
    assert mundo.trilha(ensinada) == [(None, "published", TREINO)]
    assert mundo.db.scalar("SELECT COUNT(*) FROM recipes WHERE status='active'") == 1


def test_demonstracao_com_o_mesmo_caminho_devolve_a_que_ja_vale(mundo: Mundo) -> None:
    da_ia = mundo.salva(commit=False, candidate=False, learned_from="r-ia")
    assert mundo.salva(commit=False, candidate=False, learned_from=TREINO) == da_ia
    assert mundo.status(da_ia) == "active"
    assert mundo.db.scalar("SELECT COUNT(*) FROM recipes") == 1
    assert mundo.trilha(da_ia) == [(None, "published", "sistema")]          # nada mudou: nenhuma linha nova


def test_demonstracao_substitui_a_validated_que_esperava_o_dono(mundo: Mundo) -> None:
    validada = mundo.salva(commit=True)
    assert validada
    for _ in range(2):
        mundo.store.shadow(validada, True, promote_after=2)
    assert mundo.status(validada) == "validated" and _viva(mundo) == validada
    ensinada = mundo.salva(commit=True, candidate=False, learned_from=TREINO, rid="app:id/outro")
    assert ensinada and mundo.status(validada) == "superseded" and mundo.status(ensinada) == "active"
    assert mundo.trilha(validada)[-1] == ("validated", "deprecated", TREINO)
    # sai da fila do dono: a que ele aprovaria já não vale
    assert ("receita", str(validada)) not in {(e.kind.value, e.ref) for e in mundo.servico.pendentes()}


def test_demonstracao_nova_substitui_a_anterior(mundo: Mundo) -> None:
    primeira = mundo.salva(commit=False, candidate=False, learned_from=TREINO)
    segunda = mundo.salva(commit=False, candidate=False, learned_from="training:t2", rid="app:id/outro")
    assert primeira and segunda and segunda != primeira
    assert mundo.status(primeira) == "superseded" and mundo.status(segunda) == "active"
    assert mundo.trilha(primeira)[-1] == ("published", "deprecated", "training:t2")


def test_salvamento_que_nao_e_do_treino_segue_como_antes(mundo: Mundo) -> None:
    da_ia = mundo.salva(commit=False, candidate=False, learned_from="r-ia")
    assert mundo.salva(commit=False, candidate=False, learned_from="r-ia-2", rid="app:id/outro") is None
    assert mundo.salva(commit=False, learned_from="r-ia-3", rid="app:id/outro") is None      # nem em prova
    assert mundo.status(da_ia) == "active"
    assert mundo.db.scalar("SELECT COUNT(*) FROM recipes") == 1


def test_viva_e_a_ativa_ou_a_validated_nunca_a_candidata(mundo: Mundo) -> None:
    assert _viva(mundo) is None
    candidata = mundo.salva(commit=True)
    assert candidata and _viva(mundo) is None                              # em prova não segura a chave
    for _ in range(2):
        mundo.store.shadow(candidata, True, promote_after=2)
    assert _viva(mundo) == candidata                                       # validated
    ativa = mundo.salva("abrir", commit=False, candidate=False, learned_from=TREINO)
    assert _viva(mundo, "abrir") == ativa


def test_quarentena_trocada_pela_demonstracao_e_assinada_pela_sessao(mundo: Mundo) -> None:
    da_ia = mundo.salva(commit=False, candidate=False, learned_from="r-ia")
    assert da_ia
    for _ in range(3):
        mundo.store.result(da_ia, False)
    assert mundo.status(da_ia) == "quarantined"
    ensinada = mundo.salva(commit=False, candidate=False, learned_from=TREINO, rid="app:id/outro")
    assert ensinada and mundo.status(da_ia) == "superseded" and mundo.status(ensinada) == "active"
    assert mundo.trilha(da_ia)[-1] == ("disabled", "deprecated", TREINO)        # a causa foi a demonstração


# ------------------------------------------------------------------ junção com o 31.86: a prévia e o reparo
def _previa(mundo: Mundo, rid: str = "app:id/send", passo: str = "enviar") -> tuple[str, int | None]:
    efeito, viva = mundo.store.previa_do_treino(PKG, "1.0(1)", f"h-{passo}", _acoes(commit=False, rid=rid), **CHAVE)
    return efeito, None if viva is None else int(viva["id"])


def _reparo(mundo: Mundo, rid: str = "app:id/send", passo: str = "enviar") -> int | None:
    return mundo.store.save(package=PKG, app_version="1.0(1)", step_hash=f"h-{passo}", step_key=passo,
                            actions=_acoes(commit=False, rid=rid), learned_from=TREINO, candidate=False,
                            so_em_chave_virgem=True, **CHAVE)


def test_previa_do_treino_diz_o_que_o_save_faria_sem_gravar(mundo: Mundo) -> None:
    assert _previa(mundo) == ("grava", None)
    da_ia = mundo.salva(commit=False, candidate=False, learned_from="r-ia")
    antes = mundo.db.scalar("SELECT COUNT(*) FROM recipes")
    assert _previa(mundo) == ("ja_vale", da_ia)
    assert _previa(mundo, rid="app:id/outro") == ("substitui", da_ia)
    assert mundo.db.scalar("SELECT COUNT(*) FROM recipes") == antes                 # só leu
    # e o `save` faz o que a prévia disse
    assert mundo.salva(commit=False, candidate=False, learned_from=TREINO) == da_ia
    assert mundo.salva(commit=False, candidate=False, learned_from=TREINO, rid="app:id/outro") not in (None, da_ia)


def test_o_reparo_nao_substitui_a_viva_nem_ressuscita_a_quarentena(mundo: Mundo) -> None:
    ativa = mundo.salva("abrir", commit=False, candidate=False, learned_from="r-ia")
    assert _reparo(mundo, passo="abrir", rid="app:id/outro") is None                    # a viva fica
    assert mundo.status(ativa) == "active"
    rebaixada = mundo.salva(commit=False, candidate=False, learned_from=TREINO)
    assert rebaixada
    for _ in range(3):
        mundo.store.result(rebaixada, False)
    assert _reparo(mundo) is None and _reparo(mundo, rid="app:id/outro") is None     # a quarentena fica
    assert mundo.status(rebaixada) == "quarantined"
    assert mundo.db.scalar("SELECT COUNT(*) FROM recipes") == 2
    assert _reparo(mundo, passo="nova")                                                # chave virgem: grava


def test_o_reparo_confere_o_veto_dentro_da_transacao(mundo: Mundo, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mundo.store, "caminho_vetado", lambda receita: True)
    assert _reparo(mundo) is None and mundo.db.scalar("SELECT COUNT(*) FROM recipes") == 0
    # a demonstração (sem o reparo) segue sem passar pelo veto: é a pessoa ensinando agora
    assert mundo.salva(commit=False, candidate=False, learned_from=TREINO)
