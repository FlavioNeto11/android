"""31.249: a receita só reproduz no mesmo escopo do alvo em que foi aprendida (conta da persona ou de terceiro).

Medido em 07/10 (só leitura, central `8552b160`): a receita 111 (`open_post_1`) foi aprendida em 03/10 num post da PRÓPRIA
conta da persona (o `post_author` da etapa era a conta dela); as etapas `open_post_1` da onda 2 e da rodada iam a post
de terceiro, com o MESMO `template_hash` (a etapa não cita o autor na pós-condição). Na onda 2 a 111 reproduziu nos 3
alvos, divergiu nos 3, foi à quarentena e a IA pagou o caminho: US$ 0,1415, 39 % da onda. Só uma receita viva cabe
por chave, então o escopo não entra na identidade (a receita de um lado deixaria a do outro órfã): entra na CONSULTA.
A receita de outro escopo não reproduz (`receita.consulta{resultado=outro_escopo}`), e a IA decide a etapa.

O que estes testes protegem:
* a regra (`recipes.escopo_do_alvo`): `post_author`/`username` igual a uma conta da persona (ou o marcador dela) é
  `proprio`; outra conta é `terceiro`; sem conta-alvo, `None` (não filtra);
* o escopo da receita sai da etapa em que ela foi aprendida (`learned_from_step`, a persona daquela execução);
* a consulta: mesmo escopo acha, outro escopo não reproduz e conta `outro_escopo`; escopo desconhecido (de um lado ou
  do outro) segue como antes.

Nível de prova: `simulated` (harness, valores inventados). `real`: a 1ª operação em post de terceiro depois do deploy,
sem a receita da conta própria reproduzindo.
"""
from __future__ import annotations

import json

from app.metricas import metricas
from app.taskqueue.recipes import ESCOPO_PROPRIO, ESCOPO_TERCEIRO, RecipeStore, escopo_do_alvo

from .conftest import Harness
from .test_funil_de_receitas import PKG, _acoes
from .test_nota_do_juiz_mascarada import USUARIO
from .test_registro_mascarado_da_persona import _execucao

CHAVE = {"signature": "", "variant": "pt-BR/xhdpi"}


def test_a_regra_do_escopo() -> None:
    assert escopo_do_alvo({"post_author": "@conta.inventada"}, ["conta.inventada"]) == ESCOPO_PROPRIO
    assert escopo_do_alvo({"username": "Conta.Inventada"}, ["@conta.inventada"]) == ESCOPO_PROPRIO   # sem caixa nem @
    assert escopo_do_alvo({"username": "{conta_instagram_usuario}"}, []) == ESCOPO_PROPRIO           # o marcador
    assert escopo_do_alvo({"post_author": "@terceiro.inventado"}, ["conta.inventada"]) == ESCOPO_TERCEIRO
    assert escopo_do_alvo({"caption_contains": "legenda"}, ["conta.inventada"]) is None              # sem conta-alvo
    assert escopo_do_alvo({"post_author": ""}, ["conta.inventada"]) is None


def _aprendida_em(harness: Harness, autor: str, step_hash: str) -> tuple[RecipeStore, int]:
    """A receita aprendida numa etapa cujo `post_author` é `autor`, na execução da persona sintética do 31.113."""
    st = harness.state
    assert st is not None
    _run_id, aid = _execucao(harness)
    sid = aid.rsplit(":", 1)[0]
    st.db.execute("UPDATE steps SET bindings=? WHERE id=?", (json.dumps({"post_author": autor, "target": "a 1ª"}), sid))
    store = RecipeStore(st.db)
    rid = store.save(package=PKG, app_version="1.0(1)", step_hash=step_hash, step_key="abrir", actions=_acoes(),
                     learned_from=sid, **CHAVE)
    assert rid
    return store, rid


async def test_o_escopo_vem_da_etapa_em_que_a_receita_foi_aprendida(harness: Harness) -> None:
    store, rid = _aprendida_em(harness, f"@{USUARIO}", "h-propria")
    db = harness.state.db  # type: ignore[union-attr]
    linha = db.one("SELECT * FROM recipes WHERE id=?", (rid,))
    assert store.escopo_da_receita(linha) == ESCOPO_PROPRIO
    sid = str(linha["learned_from_step"])
    db.execute("UPDATE steps SET bindings=? WHERE id=?", (json.dumps({"post_author": "@terceiro.inventado"}), sid))
    assert RecipeStore(db).escopo_da_receita(linha) == ESCOPO_TERCEIRO      # a mesma etapa, outro autor
    db.execute("UPDATE steps SET bindings='{}' WHERE id=?", (sid,))
    assert RecipeStore(db).escopo_da_receita(linha) is None                 # sem conta-alvo


async def test_a_consulta_so_reproduz_no_mesmo_escopo(harness: Harness) -> None:
    metricas.limpar()
    store, _rid = _aprendida_em(harness, f"@{USUARIO}", "h-abrir")             # a 111: aprendida na conta própria
    assert store.find(PKG, "1.0(1)", "h-abrir", escopo=ESCOPO_PROPRIO, **CHAVE) is not None
    assert store.find(PKG, "1.0(1)", "h-abrir", escopo=ESCOPO_TERCEIRO, **CHAVE) is None    # post de terceiro: a IA
    assert metricas.valor("receita.consulta", resultado="outro_escopo") == 1
    assert store.find(PKG, "1.0(1)", "h-abrir", escopo=None, **CHAVE) is not None           # sem escopo: como antes


async def test_receita_sem_escopo_conhecido_vale_nos_dois(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    store = RecipeStore(st.db)
    assert store.save(package=PKG, app_version="1.0(1)", step_hash="h-sem", step_key="abrir", actions=_acoes(),
                      learned_from="etapa-apagada", **CHAVE)
    assert store.find(PKG, "1.0(1)", "h-sem", escopo=ESCOPO_TERCEIRO, **CHAVE) is not None
    assert store.find(PKG, "1.0(1)", "h-sem", escopo=ESCOPO_PROPRIO, **CHAVE) is not None


async def test_o_executor_passa_o_escopo_da_etapa_a_consulta(harness: Harness, monkeypatch: object) -> None:
    from .test_espera_nao_e_veredito_da_receita import _receita_aprendida

    vistos: list[object] = []
    original = RecipeStore.find

    def _find(self: RecipeStore, *a: object, **kw: object) -> object:
        vistos.append(kw.get("escopo", "ausente"))
        return original(self, *a, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(RecipeStore, "find", _find)  # type: ignore[attr-defined]
    await _receita_aprendida(harness)
    assert vistos and "ausente" not in vistos           # toda consulta do executor leva o escopo (aqui, sem conta-alvo)
