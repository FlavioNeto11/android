"""31.287: a receita concorda POR ALVO, soma a prova entre aparelhos e a prova vale por efeito.

O defeito (execução de 08/10, 3 aparelhos em sequência, mesmo comando): cada aparelho gravava a sua candidata do FOLLOW e
apagava a prova da anterior. Duas causas, as duas medidas aqui:

1. A comparação da sombra rodava em TODA decisão da IA, inclusive na que o executor descarta: com `strong_model_only_on_commit`
   (31.223) o toque de efeito do modelo barato sobe ao forte e é refeito. A 1ª comparação gastava a única ação da receita; a
   refeita via a receita esgotada e contava "a IA escolheu outra ação": divergência falsa em TODO FOLLOW, que zerava a prova
   e deixava o caminho do aparelho seguinte substituir a candidata.
2. `_caminho` comparava a lista inteira de seletores: o mesmo botão gravado com 5 seletores num aparelho e 4 noutro eram
   "caminhos diferentes". Vale o ALVO (o seletor de maior confiança); alternativos, `why` e o teto de rolagem ficam de fora.

E o limiar passou a valer por efeito: `recipes_promote_after` (1) para a etapa sem efeito externo e
`recipes_promote_after_com_efeito` (2) para a com `commit`, que mesmo provada para em `validated` (D1 do ADR-054).

Nível de prova: `simulated` (harness com provedor e aparelhos falsos; receitas copiadas de captura real, sem nome de pessoa).
"""
from __future__ import annotations

from typing import Any

from app.modules.learning.infrastructure.fontes import FontesSql
from app.taskqueue.recipes import RecipeStore, _caminho

from .conftest import Harness
from .test_receita_candidata import PASSOS, PKG, _driven, _receitas

RID = "com.instagram.android:id/profile_header_follow_button"


def _follow(selectors: list[dict[str, str]], why: str = "tocar em Follow") -> list[dict[str, Any]]:
    return [{"tool": "tap", "commit": True, "why": why, "args": {}, "selectors": selectors}]


# O mesmo botão como dois aparelhos o gravaram: o 2º não tem o seletor `text` do fim (captura de 08/10, sem nomes).
SEL_A = [{"kind": "rid+text", "rid": RID, "text": "Follow"}, {"kind": "rid+desc", "rid": RID, "desc": "Follow X"},
         {"kind": "rid", "rid": RID}, {"kind": "desc", "desc": "Follow X"}, {"kind": "text", "text": "Follow"}]
SEL_B = SEL_A[:4]


def test_o_caminho_vale_pelo_alvo_e_nao_pela_lista_de_seletores() -> None:
    assert _caminho(_follow(SEL_A, "um porquê")) == _caminho(_follow(SEL_B, "outro porquê"))
    outro_botao = [{"kind": "rid+text", "rid": "com.instagram.android:id/profile_header_message_button",
                    "text": "Follow"}]
    assert _caminho(_follow(SEL_A)) != _caminho(_follow(outro_botao))
    assert _caminho(_follow(SEL_A)) != _caminho([{**_follow(SEL_A)[0], "commit": False}])
    digita = {"tool": "type_text", "commit": False, "args": {"text": "{username}"}, "selectors": []}
    assert _caminho([digita]) != _caminho([{**digita, "args": {"text": "outro"}}])
    rola = {"tool": "scroll", "commit": False, "args": {"direction": "down"}, "selectors": []}
    assert _caminho([rola]) != _caminho([{**rola, "args": {"direction": "up"}}])
    # o teto de rolagens prévias não é caminho; a direção dela é
    com_hint = {**_follow(SEL_A)[0], "scroll": {"direction": "down", "max": 4}}
    assert _caminho([com_hint]) == _caminho([{**com_hint, "scroll": {"direction": "down", "max": 9}}])
    assert _caminho([com_hint]) != _caminho([{**com_hint, "scroll": {"direction": "up", "max": 4}}])


async def test_a_candidata_do_mesmo_alvo_nao_e_substituida_pela_copia_do_outro_aparelho(harness: Harness) -> None:
    store = RecipeStore(harness.state.db)                                   # type: ignore[union-attr]
    chave = {"package": PKG, "app_version": "1.0(1)", "step_hash": "h-seguir", "step_key": "follow_1", "signature": "",
             "variant": "en-US/xhdpi"}
    primeira = store.save(actions=_follow(SEL_A), learned_from="s1", candidate=True, **chave)
    assert primeira
    # o aparelho seguinte aprende o mesmo botão com a lista mais curta e a primeira "divergiu" (replaces): a cópia não vira versão
    segunda = store.save(actions=_follow(SEL_B), learned_from="s2", candidate=True, replaces=primeira, **chave)
    assert segunda is None
    vivas = harness.state.db.query("SELECT id, status FROM recipes WHERE step_hash='h-seguir'")  # type: ignore[union-attr]
    assert [(r["id"], r["status"]) for r in vivas] == [(primeira, "candidate")]


async def test_commit_do_barato_descartado_nao_gasta_a_receita_e_a_prova_soma_entre_aparelhos(harness: Harness) -> None:
    """O quadro real, no app de prova: o envio tem `commit`; o modelo forte refaz o toque. A prova soma de aparelho em
    aparelho (1 concordância basta SEM efeito, 2 COM efeito) e a do envio para em `validated` esperando o dono."""
    harness.pular_o_tempo()
    ia = harness.cfg.file.ai
    ia.recipes = "replay"
    ia.strong_model_for_side_effect = True                      # a etapa com efeito começa no barato e o commit sobe
    assert ia.strong_model_only_on_commit is True
    ia.recipes_promote_after, ia.recipes_promote_after_com_efeito = 1, 2

    r1 = await harness.wait_run(harness.run(["android-01"]).id)
    assert r1.status == "completed"
    assert {_receitas(harness)[k]["status"] for k in PASSOS} == {"candidate"}

    r2 = await harness.wait_run(harness.run(["android-02"]).id)
    assert r2.status == "completed"
    assert harness.ai.count("decide", step="send_message", tier=1) >= 1           # o commit subiu ao forte, de fato
    receitas = _receitas(harness)
    for k in ("open_conversation", "compose_message"):                           # sem efeito: 1 concordância promove
        assert receitas[k]["status"] == "active", k
    envio = receitas["send_message"]                                              # com efeito: a divergência falsa zerava aqui
    assert (envio["status"], envio["shadow_agree"], envio["shadow_total"]) == ("candidate", 1, 1)
    assert {v for k, v in _driven(harness, r2.id).items() if k in PASSOS} == {"ai"}

    r3 = await harness.wait_run(harness.run(["android-03"]).id)
    assert r3.status == "completed"
    envio = _receitas(harness)["send_message"]
    assert envio["status"] == "validated"                                          # 2 concordâncias; o dono decide (D1)
    pendentes = {(e.kind.value, e.ref) for e in harness.state.learning.pendentes()}  # type: ignore[union-attr]
    assert ("receita", str(envio["id"])) in pendentes                              # chega a "Para aprovar"
    assert harness.state.db.scalar(                                                # nada apagou a prova de outro aparelho
        "SELECT COUNT(*) FROM recipes WHERE step_key='send_message' AND status='superseded'") == 0  # type: ignore[union-attr]


async def test_o_livro_diz_quantas_concordancias_cada_candidata_pede(harness: Harness) -> None:
    db = harness.state.db                                                   # type: ignore[union-attr]
    store = RecipeStore(db)
    base = {"package": PKG, "app_version": "1.0(1)", "signature": "", "variant": "en-US/xhdpi", "learned_from": "s1",
            "candidate": True}
    sem = store.save(step_hash="h-abrir", step_key="abrir", actions=[
        {"tool": "tap", "commit": False, "why": "abrir", "args": {}, "selectors": [{"kind": "rid", "rid": "a:id/x"}]}],
        **base)
    com = store.save(step_hash="h-seguir", step_key="follow_1", actions=_follow(SEL_A), **base)
    fontes = FontesSql(db, necessarias=lambda efeito: 2 if efeito else 1)
    assert fontes.receita(str(sem)).prova_da_candidata.necessarias == 1        # type: ignore[union-attr]
    assert fontes.receita(str(com)).prova_da_candidata.necessarias == 2        # type: ignore[union-attr]
