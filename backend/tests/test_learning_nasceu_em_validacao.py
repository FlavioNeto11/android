"""30.43: a receita aprendida dentro de uma execução de validação leva a origem à vista (livro, API e dossiê).

O caso que o abriu (deploy 14): a `receita:135` nasceu numa prova de fluxo e levou as pendências do dono de 14 para 15
sem dizer de onde veio. Segue na fila do dono (decisão da orquestradora, 03/10); só ganha a marca. Nível de prova:
`simulated`.
"""
from __future__ import annotations

from app.contracts.origem import PREFIXO_VALIDACAO
from app.modules.learning.domain.curador import NASCEU_EM_VALIDACAO
from app.modules.learning.domain.vocabulario import LivroKind

from .test_learning_evidencia_receita import Mundo, mundo  # noqa: F401  (a fixture `mundo` é a armação da 30.39)


def _nascida_em(m: Mundo, run_id: str, *, prova: str | None = None, chave: str | None = None) -> int:
    r = m.receita()
    m.execucao(run_id)
    m.db.execute("UPDATE runs SET prova_fluxo_id=?, idempotency_key=COALESCE(?, idempotency_key) WHERE id=?",
                 (prova, chave, run_id))
    sid = m.etapa(run_id, 1, driven_by="ai", receita=None)
    m.db.execute("UPDATE recipes SET learned_from_step=? WHERE id=?", (sid, r))
    return r


def test_receita_nascida_numa_prova_de_fluxo_leva_a_marca(mundo: Mundo) -> None:  # noqa: F811
    r = _nascida_em(mundo, "run-p", prova="f-qa")
    e = mundo.servico.entrada(LivroKind.RECEITA, str(r))
    assert e is not None and e.nasceu_em == "prova_fluxo"
    item = mundo.dossie(LivroKind.RECEITA, str(r))["item"]
    assert isinstance(item, dict) and item["nasceu_em_validacao"] == NASCEU_EM_VALIDACAO.format(origem="prova_fluxo")


def test_receita_nascida_na_reexecucao_da_validacao_do_qa(mundo: Mundo) -> None:  # noqa: F811
    r = _nascida_em(mundo, "run-q", chave=f"{PREFIXO_VALIDACAO}lv-1")
    e = mundo.servico.entrada(LivroKind.RECEITA, str(r))
    assert e is not None and e.nasceu_em == "validacao_qa"


def test_receita_de_pedido_de_pessoa_nao_ganha_marca_nem_chave_no_dossie(mundo: Mundo) -> None:  # noqa: F811
    r = _nascida_em(mundo, "run-c")
    e = mundo.servico.entrada(LivroKind.RECEITA, str(r))
    assert e is not None and e.nasceu_em is None
    item = mundo.dossie(LivroKind.RECEITA, str(r))["item"]
    assert isinstance(item, dict) and "nasceu_em_validacao" not in item       # o mesmo `dossie_hash` de antes
