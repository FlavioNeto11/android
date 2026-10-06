"""30.85: a lista do Livro leva `ensinado_em_prova` (achado da Portal no percurso do deploy 42).

O Livro mostrava o fluxo ensinado em prova como "Publicado", sem selo, porque a entrada da lista não levava o campo
que as outras respostas do 30.81 levam. Agora a entrada do fluxo ensinado ativo traz `ensinado_em_prova`
`{persona, sessao}` pela MESMA regra do casamento (`taskqueue.flows.ensinado_em_prova`), e o campo fica AUSENTE quando
não se aplica: provado, confirmado por uma pessoa, desligado, ou fluxo que não veio do treino. Receita e lição não
mudam.

Nível de prova: `simulated` (banco de teste, sem aparelho nem IA).
"""
from __future__ import annotations

from app.modules.learning.domain.vocabulario import LivroKind, Origem
from app.modules.learning.presentation.livro import _entrada

from .test_ensinado_em_prova import ANA, MODELO, SESSAO, Mundo, _plano, mundo  # noqa: F401 - `mundo` é fixture


def _da_lista(mundo: Mundo, fid: str) -> dict[str, object]:  # noqa: F811
    """A entrada como a lista do Livro (`GET /api/aprendizado`) a monta: `servico.livro` e `_entrada`."""
    livro = mundo.servico.livro(kind=LivroKind.FLUXO, state=None, app=None, origem=None, rotulo=None)
    [e] = [e for e in livro.itens if e.ref == fid]
    return _entrada(e, mundo.servico)


def test_o_ensinado_em_prova_leva_o_selo_na_lista(mundo: Mundo) -> None:  # noqa: F811
    fid = mundo.ensina()
    item = _da_lista(mundo, fid)
    assert item["state"] == "published"                                  # o estado segue "Publicado"…
    assert item["ensinado_em_prova"] == {"persona": ANA, "sessao": SESSAO}  # …e o selo diz que espera a prova


def test_a_sessao_sem_persona_leva_persona_nula(mundo: Mundo) -> None:  # noqa: F811
    fid = mundo.ensina(persona=None)
    assert _da_lista(mundo, fid)["ensinado_em_prova"] == {"persona": None, "sessao": SESSAO}


def test_provado_o_selo_sai(mundo: Mundo) -> None:  # noqa: F811
    fid = mundo.ensina()
    mundo.execucao_de_prova(fid, "r-ok")
    mundo.evidencia(fid, "r-ok", "for")
    assert "ensinado_em_prova" not in _da_lista(mundo, fid)


def test_confirmado_por_uma_pessoa_o_selo_sai(mundo: Mundo) -> None:  # noqa: F811
    """A linha "confirmado que fica" de uma pessoa (o gesto do Livro) tira o selo; a do sistema, não (a regra é a do
    casamento, `test_ensinado_em_prova.py`)."""
    fid = mundo.ensina()
    mundo.trilha_de_pessoa(fid, "sistema", "confirmado que fica")
    assert "ensinado_em_prova" in _da_lista(mundo, fid)
    mundo.trilha_de_pessoa(fid, "painel:dono", "confirmado que fica")
    assert "ensinado_em_prova" not in _da_lista(mundo, fid)


def test_desligado_o_selo_sai(mundo: Mundo) -> None:  # noqa: F811
    fid = mundo.ensina()
    mundo.db.execute("UPDATE flows SET status='disabled' WHERE id=?", (fid,))
    assert "ensinado_em_prova" not in _da_lista(mundo, fid)


def test_o_fluxo_que_nao_veio_do_treino_nao_leva_o_campo(mundo: Mundo) -> None:  # noqa: F811
    plano = _plano("{username}")
    plano.parameters = {"username": "{username}"}
    fid = mundo.flows.learn_from_plan(plano, MODELO, source="importado")
    item = _da_lista(mundo, str(fid))
    assert item["origin"] != Origem.TREINO.value and "ensinado_em_prova" not in item


def test_a_receita_do_treino_nao_leva_o_campo(mundo: Mundo) -> None:  # noqa: F811
    mundo.ensina()
    rid = mundo.receita_do_treino()
    entrada = _entrada(mundo.servico.entrada(LivroKind.RECEITA, str(rid)), mundo.servico)
    assert "ensinado_em_prova" not in entrada and "espera_a_pessoa" not in entrada
