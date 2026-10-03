"""30.44: o título da etapa ao lado da chave na evidência do detalhe, lido da execução na hora e sem gravar.

- a linha de evidência da receita continua com a chave ("etapa N (chave): reproduzida"); o título é texto do
  planejador e não entra no `detail` gravado;
- o detalhe (`GET /api/aprendizado/{kind}/{ref}`) traz `etapa_titulo` em cada evidência: o título da etapa citada, achado
  por `steps.run_id` e `steps.key` (preferindo a posição citada);
- a execução sem a etapa, o texto sem etapa citada e o título que a triagem recusa dão `null`.

Nível de prova: `simulated` (banco de teste migrado; nenhuma execução, aparelho ou conta reais).
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from app.modules.learning.domain.prova import etapa_citada
from app.modules.learning.domain.vocabulario import LivroKind
from app.modules.learning.presentation import livro as apresentacao

from .fake_skills import banco as banco_migrado
from .test_learning_evidencia_receita import Mundo


@pytest.fixture
def mundo(tmp_path: Path) -> Iterator[Mundo]:
    db = banco_migrado(tmp_path, "titulo_da_etapa.sqlite3")
    yield Mundo(db)
    db.close()


def _evidencias(mundo: Mundo, receita: int) -> list[dict[str, object]]:
    detalhe = apresentacao._detalhe(mundo.servico.detalhe(LivroKind.RECEITA, str(receita)), mundo.servico)
    return [dict(e) for e in detalhe["evidencias"]]  # type: ignore[union-attr]


def test_etapa_citada_le_a_posicao_e_a_chave() -> None:
    assert etapa_citada("etapa 5 (send_message): reproduzida") == (5, "send_message")
    assert etapa_citada("prova: etapa 2 (open_inbox) reprovada: x") == (2, "open_inbox")
    assert etapa_citada("etapa 3 (e3): divergiu, a IA assumiu (e mais 2 da mesma receita nesta execução)") == (3, "e3")
    assert etapa_citada("sombra: 4 de 5") is None and etapa_citada(None) is None and etapa_citada("") is None


def test_o_detalhe_traz_o_titulo_e_o_texto_gravado_fica_com_a_chave(mundo: Mundo) -> None:
    r = mundo.receita("enviar")
    mundo.execucao("run-1")
    mundo.etapa("run-1", 1, driven_by="recipe", receita=r)
    mundo.db.execute("UPDATE steps SET title=? WHERE run_id=? AND seq=1", ("Enviar a mensagem", "run-1"))
    mundo.digerir("run-1")
    [gravada] = mundo.linhas(r)
    assert gravada["detail"] == "etapa 1 (e1): reproduzida"                       # a chave fica; o título não é gravado
    [ev] = _evidencias(mundo, r)
    assert ev["detail"] == "etapa 1 (e1): reproduzida"
    assert ev["etapa_titulo"] == "Enviar a mensagem"


def test_a_execucao_sem_a_etapa_da_null(mundo: Mundo) -> None:
    r = mundo.receita("enviar")
    mundo.execucao("run-1")
    mundo.etapa("run-1", 1, driven_by="recipe", receita=r)
    mundo.digerir("run-1")
    mundo.db.execute("DELETE FROM attempts WHERE step_id LIKE ?", ("run-1:%",))   # a retenção limpa a etapa
    mundo.db.execute("DELETE FROM steps WHERE run_id=?", ("run-1",))
    [ev] = _evidencias(mundo, r)
    assert ev["detail"] == "etapa 1 (e1): reproduzida" and ev["etapa_titulo"] is None


def test_a_mesma_chave_em_outra_posicao_prefere_a_citada_e_sem_ela_a_primeira(mundo: Mundo) -> None:
    r = mundo.receita("enviar")
    mundo.execucao("run-1")
    mundo.etapa("run-1", 2, driven_by="recipe", receita=r)
    # a mesma chave "e2" em outra posição e instância: o título da posição 2 é o que vale
    mundo.db.execute("UPDATE steps SET key='e2', title=? WHERE run_id=? AND seq=2", ("Segunda", "run-1"))
    mundo.etapa("run-1", 9, driven_by=None, receita=None, aparelho="android-07")
    mundo.db.execute("UPDATE steps SET key='e2', title=? WHERE run_id=? AND seq=9", ("Nona", "run-1"))
    mundo.digerir("run-1")
    [ev] = _evidencias(mundo, r)
    assert ev["etapa_titulo"] == "Segunda"
    mundo.db.execute("UPDATE steps SET seq=3 WHERE run_id=? AND title=?", ("run-1", "Segunda"))   # a posição citada some
    [ev] = _evidencias(mundo, r)
    assert ev["etapa_titulo"] == "Segunda"                                          # a de menor posição com a chave


def test_o_texto_sem_etapa_citada_e_o_titulo_sensivel_dao_null(mundo: Mundo) -> None:
    r = mundo.receita("enviar")
    mundo.execucao("run-1")
    mundo.etapa("run-1", 1, driven_by="recipe", receita=r)
    mundo.db.execute("UPDATE steps SET title=? WHERE run_id=?", ("senha: hunter2hunter2", "run-1"))
    mundo.digerir("run-1")
    [ev] = _evidencias(mundo, r)
    assert ev["etapa_titulo"] is None                                               # recusado pela triagem de credencial
    mundo.db.execute("UPDATE learning_evidence SET detail=? WHERE item_ref=?", ("sombra: 4 de 5", f"receita:{r}"))
    [ev] = _evidencias(mundo, r)
    assert ev["etapa_titulo"] is None
