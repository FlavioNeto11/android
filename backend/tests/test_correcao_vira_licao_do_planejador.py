"""31.149, caminho alternativo: a correção que não ligou à etapa que falhou ensina o PLANEJADOR.

Quando a correção não vira receita na etapa que falhou (nome que a execução não tem, efeito, app diferente), o caminho
ensinado ainda vale como conhecimento do app: vira lição do planejador ("quando a etapa X falhar, o caminho que uma
pessoa ensinou foi A → B"). Não depende do 31.151.

O que estes testes protegem:
* a lição nasce candidata, de origem humana (só o dono a publica), no escopo do planejador do app, e o `save` diz o id;
* o texto tem só chaves de etapa: nem o valor do objetivo, nem o exemplo, nem o comando;
* a chave com valor de parâmetro, a etapa de sessão e a execução simulada não viram lição;
* publicada, ela cabe no bloco do planejador daquele app;
* a correção que ligou não gera lição.

Nível de prova: `simulated` (harness com aparelho falso, sem IA).
"""
from __future__ import annotations

import json

from app.modules.learning.domain.licoes import (CorrecaoSemReceita, MotivoDeRecusa, Pedido, Recusa, escolher,
                                                licao_da_correcao)
from app.modules.learning.domain.tokens import TETOS_DE_FABRICA
from app.modules.learning.domain.vocabulario import Papel
from app.modules.learning.infrastructure.sql_repository import item_da_linha

from .conftest import Harness
from .test_correcao_volta_ao_comando import PKG, _corrigir, _falha

TEXTO = ("Em com.pocqa.messenger: quando a etapa abrir_conversa falhar, o caminho que uma pessoa ensinou foi "
         "tocar_busca → digitar_nome.")


def _c(**kw: object) -> CorrecaoSemReceita:
    base: dict[str, object] = {"app": PKG, "chave": "abrir_conversa", "caminho": ("tocar_busca", "digitar_nome"),
                               "sessao": "s1", "run_id": "r1", "step_id": "st1", "side_effect": False,
                               "valores": ("QA-001",)}
    return CorrecaoSemReceita(**{**base, **kw})  # type: ignore[arg-type]


def test_so_chaves_entram_e_o_que_nao_pode_e_recusado() -> None:
    novo = licao_da_correcao(_c())
    assert not isinstance(novo, Recusa)
    assert novo.summary == TEXTO and novo.human_origin and novo.escopo.role == Papel.PLANNER.value
    assert novo.content == {"modelo": "correcao", "acao": "abrir_conversa", "caminho": ["tocar_busca", "digitar_nome"]}
    assert licao_da_correcao(_c(caminho=("tocar_busca", "digitar_ana"), valores=("Ana",))) == Recusa(
        MotivoDeRecusa.VALOR_DE_PARAMETRO)
    assert licao_da_correcao(_c(caminho=("falar_com_ana",), valores=("Ana Souza",))) == Recusa(   # uma palavra basta
        MotivoDeRecusa.VALOR_DE_PARAMETRO)
    assert licao_da_correcao(_c(caminho=("fazer_login",))) == Recusa(MotivoDeRecusa.ACAO_DE_SESSAO)
    assert licao_da_correcao(_c(caminho=("Digitar QA-001",))) == Recusa(MotivoDeRecusa.ACAO_INVALIDA)
    assert licao_da_correcao(_c(caminho=())) == Recusa(MotivoDeRecusa.ACAO_INVALIDA)
    assert licao_da_correcao(_c(simulated=True)) == Recusa(MotivoDeRecusa.SIMULADA)
    assert licao_da_correcao(_c(app="sem pacote")) == Recusa(MotivoDeRecusa.APP_INVALIDO)


async def test_a_correcao_nao_ligada_vira_licao_candidata_do_planejador(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    falha = _falha(st, chave="abrir_conversa", parametros="{}")
    salvo = await _corrigir(harness, falha=falha, chaves=("tocar_busca", "digitar_nome"),
                            parametros=[{"name": "nome", "example": "QA-001", "description": ""}])
    assert salvo["correcao"]["ligada"] is False                 # {nome} que a execução não tem
    licao = salvo["correcao"]["licao"]
    assert licao["estado"] == "candidate" and licao["texto"] == TEXTO, licao
    linha = st.db.one("SELECT * FROM learning_items WHERE id=?", (licao["id"],))
    assert (linha["kind"], linha["scope_app"], linha["scope_role"], linha["human_origin"], linha["source_kind"]) == (
        "licao", PKG, "planner", 1, "teaching_correction")
    assert "QA-001" not in linha["summary"] and "QA-001" not in linha["content"]
    assert json.loads(linha["provenance"])["sessao"] == f"training:{salvo['sid']}"
    ev = st.db.one("SELECT data FROM events WHERE kind='log' AND data LIKE ? ORDER BY id DESC LIMIT 1",
                   (f"%{salvo['sid']}%",))
    assert json.loads(ev["data"])["licao_id"] == licao["id"]
    # publicada pelo dono (aqui, direto no banco), cabe no bloco do planejador deste app
    st.db.execute("UPDATE learning_items SET state='published', state_detail='em_prova' WHERE id=?", (licao["id"],))
    item = item_da_linha(st.db.one("SELECT * FROM learning_items WHERE id=?", (licao["id"],)))
    escolha = escolher([item], Pedido(papel=Papel.PLANNER, unidade="plan:r", run_id="r", app=PKG, capability="",
                                      step_hash="", simulated=False), TETOS_DE_FABRICA[Papel.PLANNER],
                       holdout_publicada=0.0, sortear=False)
    assert list(escolha.textos) == [TEXTO]


async def test_a_correcao_que_ligou_nao_gera_licao(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    from .test_treino_escopo_ao_provar import _persona
    falha = _falha(st, chave="abrir_conversa", parametros='{"contato": "QA-001"}')
    salvo = await _corrigir(harness, falha=falha, chaves=("tocar_busca", "digitar_nome"),
                            parametros=[{"name": "nome", "example": "QA-001", "description": ""}],
                            persona=_persona(st, "ensinou.licao"))
    assert salvo["correcao"]["ligada"] is True and "licao" not in salvo["correcao"]
    assert st.db.scalar("SELECT COUNT(*) FROM learning_items WHERE source_kind='teaching_correction'") == 0
