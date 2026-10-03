"""29.40 item 2 (PROTÓTIPO para a revisão da Android): o toque num contêiner sem identidade (o `LinearLayout` da linha
da lista, sem id, texto nem descrição) vira receita pelo FILHO rotulado gravado com ele.

Antes, o `distill` recusava a etapa inteira ("o alvo não tinha seletor estável e único"): 92 de 340 toques no central
desde 26/09. Agora a gravação (`_safe_target`) guarda até 3 filhos não clicáveis, contidos nos bounds do alvo e únicos
na tela; a destilação usa o primeiro cujo texto vire seletor, e a reprodução toca o centro dele, que fica dentro do
contêiner. Ação de efeito pelo filho é recusada.

Revisão da Android (03/10), incorporada:
- trava de hit-test: o menor clicável no centro do filho tem de ser o contêiner, na gravação e na reprodução;
- a janela da ordem do dump (os descendentes vêm logo depois do alvo);
- a recusa de rótulo que muda com o estado (tempo, "Following", "Active now") e de @ literal.

Nível de prova: `simulated` (árvores sintéticas).
"""
from __future__ import annotations

import json

from app.automation.hierarchy import parse_hierarchy
from app.taskqueue.executor import _safe_target
from app.taskqueue.recipes import RecipeDiverged, Replayer, build_selectors, distill, eh_generica, filhos_rotulados

#: Duas linhas de conversa: o contêiner clicável não tem identidade; dentro, o nome (não clicável) e, na 1ª, um botão
#: "Mais" clicável (que faria outra coisa).
XML = ('<hierarchy>'
       '<node class="android.widget.LinearLayout" clickable="true" bounds="[0,100][700,160]"/>'
       '<node class="android.widget.TextView" text="QA-001" resource-id="app:id/name" bounds="[20,110][400,150]"/>'
       '<node class="android.widget.ImageButton" content-desc="Mais" clickable="true" bounds="[620,110][690,150]"/>'
       '<node class="android.widget.LinearLayout" clickable="true" bounds="[0,200][700,260]"/>'
       '<node class="android.widget.TextView" text="QA-002" resource-id="app:id/name" bounds="[20,210][400,250]"/>'
       '</hierarchy>')
VARIAVEIS = {"recipient": "QA-001", "instance_id": "android-01", "run_id": "r-1"}


def _linha_do_toque(alvo: dict[str, object], *, efeito: bool = False) -> dict[str, object]:
    return {"tool": "tap", "status": "done", "source": "ai", "args": '{"element_id":"e1"}', "target": json.dumps(alvo),
            "side_effect": int(efeito), "rationale": "abrir a conversa"}


def test_a_gravacao_guarda_o_filho_rotulado_nao_clicavel_do_conteiner_sem_identidade() -> None:
    tree = parse_hierarchy(XML)
    linha = next(e for e in tree.elements if e.class_name.endswith("LinearLayout"))
    alvo = _safe_target(linha, tree)
    assert alvo is not None and alvo["unique"] == []
    filhos = alvo["filhos"]
    assert [f["text"] for f in filhos] == ["QA-001"]                     # o botão "Mais" é clicável: fica fora
    assert filhos[0]["unique"][0] == "rid+text"
    # O alvo que já se identifica sozinho não ganha filhos (nada muda para ele).
    nome = next(e for e in tree.elements if e.text == "QA-001")
    assert "filhos" not in (_safe_target(nome, tree) or {})
    assert filhos_rotulados(tree, nome) == []                            # nada dentro do nome


def test_o_conteiner_vira_receita_pelo_filho_e_reproduz_para_outro_valor() -> None:
    tree = parse_hierarchy(XML)
    linha = next(e for e in tree.elements if e.class_name.endswith("LinearLayout"))
    acoes, motivo = distill([_linha_do_toque(_safe_target(linha, tree) or {})], VARIAVEIS)   # type: ignore[list-item]
    assert motivo == "ok" and acoes is not None
    assert acoes[0]["selectors"][0] == {"kind": "rid+text", "rid": "app:id/name", "text": "{recipient}", "via": "filho",
                                         "conteiner": "android.widget.LinearLayout"}
    # Outro aparelho, outro destinatário: o toque cai no nome da 2ª linha, dentro do contêiner dela.
    decisao = Replayer(recipe_id=1, version=1, actions=acoes, variables={**VARIAVEIS, "recipient": "QA-002"}).next(tree)
    assert decisao is not None
    tocado = tree.by_id(decisao.args["element_id"])
    assert tocado is not None and tocado.text == "QA-002"
    assert 200 <= tocado.center[1] <= 260                                # dentro da 2ª linha
    # Destinatário fora da tela: diverge, como sempre (nunca toca no "mais parecido").
    try:
        Replayer(recipe_id=1, version=1, actions=acoes, variables={**VARIAVEIS, "recipient": "QA-404"}).next(tree)
        raise AssertionError("deveria divergir")
    except RecipeDiverged:
        pass


def test_efeito_pelo_filho_e_filho_ambiguo_continuam_recusados() -> None:
    tree = parse_hierarchy(XML)
    linha = next(e for e in tree.elements if e.class_name.endswith("LinearLayout"))
    alvo = _safe_target(linha, tree) or {}
    acoes, motivo = distill([{**_linha_do_toque(alvo, efeito=True), "args": json.dumps(                   # type: ignore[list-item]
        {"element_id": "e1", "is_commit_action": True})}], VARIAVEIS)
    assert acoes is None and "filho" in motivo
    # Duas linhas com o mesmo rótulo ("Abrir"): o filho não é único, a gravação não o guarda e a destilação recusa.
    ambiguo = parse_hierarchy(XML.replace('text="QA-001"', 'text="Abrir"').replace('text="QA-002"', 'text="Abrir"'))
    linha2 = next(e for e in ambiguo.elements if e.class_name.endswith("LinearLayout"))
    alvo2 = _safe_target(linha2, ambiguo) or {}
    assert "filhos" not in alvo2
    acoes2, motivo2 = distill([_linha_do_toque(alvo2)], VARIAVEIS)                                          # type: ignore[list-item]
    assert acoes2 is None and "seletor estável e único" in motivo2
    # O alvo antigo (gravado antes, sem `filhos`) segue como antes.
    assert build_selectors({**alvo, "filhos": []}, VARIAVEIS) == []

def _linha(tree):
    return next(e for e in tree.elements if e.class_name.endswith("LinearLayout"))


def test_hit_test_e_janela_do_dump_deixam_de_fora_sobreposicao_e_outra_camada() -> None:
    # Um botao clicavel cobre o nome (o toque no centro do nome iria para ele): o nome nao vira filho.
    coberto = parse_hierarchy(XML.replace(
        '<node class="android.widget.ImageButton" content-desc="Mais" clickable="true" bounds="[620,110][690,150]"/>',
        '<node class="android.widget.Button" text="Enviar" clickable="true" bounds="[10,105][410,155]"/>'))
    assert filhos_rotulados(coberto, _linha(coberto)) == []
    # Um rotulo de outra camada (o FAB, no fim do dump) dentro dos bounds da linha: fora da janela, nao entra.
    fab = parse_hierarchy(XML.replace('</hierarchy>',
                                      '<node class="android.widget.TextView" text="Nova conversa" bounds="[450,115][600,145]"/>'
                                      '</hierarchy>'))
    assert [f["text"] for f in filhos_rotulados(fab, _linha(fab))] == ["QA-001"]


def test_rotulo_que_muda_com_o_estado_ou_arroba_literal_nao_vira_seletor() -> None:
    for rotulo in ("Active now", "2h", "Following", "10:42", "Yesterday", "@lucas", "por @lucas", "Foto de @lucas",
                   "Online", "offline", "typing…", "digitando...", "New", "Nova"):
        tree = parse_hierarchy(XML.replace('text="QA-001"', f'text="{rotulo}"'))
        alvo = _safe_target(_linha(tree), tree) or {}
        assert alvo.get("filhos"), rotulo                                    # gravado (a gravacao nao conhece as variaveis)
        assert build_selectors(alvo, {"recipient": "QA-009"}) == [], rotulo  # mas nao vira seletor
    # O nome de pessoa templatizado vale (o literal e so o parametro).
    tree = parse_hierarchy(XML.replace('text="QA-001"', 'text="@qa001"'))
    sels = build_selectors(_safe_target(_linha(tree), tree) or {}, {"usuario": "@qa001"})
    assert sels and sels[0]["text"] == "{usuario}"


def test_filho_com_o_literal_do_valor_da_etapa_deixa_a_receita_na_chave_especifica() -> None:
    """Com a chave genérica (RA-20 B): o seletor `via: filho` mora em `selectors`, e `_literais` o lê como os outros. O
    filho cujo literal é o valor da etapa ("nasa", sem parâmetro que o templatize) deixa a receita ESPECÍFICA; o mesmo
    filho templatizado é genérico (revisão da Android)."""
    tree = parse_hierarchy(XML.replace('text="QA-001"', 'text="nasa"'))
    alvo = _safe_target(_linha(tree), tree) or {}
    acoes, motivo = distill([_linha_do_toque(alvo)], {"instance_id": "android-01", "run_id": "r-1"})  # type: ignore[list-item]
    assert motivo == "ok" and acoes is not None
    assert acoes[0]["selectors"][0]["via"] == "filho" and acoes[0]["selectors"][0]["text"] == "nasa"
    assert eh_generica(acoes, "o perfil de nasa aberto", {"username": "nasa"}) is False
    assert eh_generica(acoes, "o perfil aberto", None, titulo="Abrir o perfil de nasa") is False
    templatizadas, _ = distill([_linha_do_toque(alvo)], {"username": "nasa", "instance_id": "android-01",  # type: ignore[list-item]
                                                         "run_id": "r-1"})
    assert templatizadas is not None and templatizadas[0]["selectors"][0]["text"] == "{username}"
    assert eh_generica(templatizadas, "o perfil de {username} aberto", {"username": "nasa"}) is True


def test_na_reproducao_um_clicavel_por_cima_do_rotulo_faz_divergir_antes_do_toque() -> None:
    tree = parse_hierarchy(XML)
    acoes, _ = distill([_linha_do_toque(_safe_target(_linha(tree), tree) or {})], VARIAVEIS)  # type: ignore[list-item]
    assert acoes is not None
    # Outra tela: na linha do QA-002 apareceu um botao "Seguir" exatamente sobre o nome.
    outra = parse_hierarchy(XML.replace('</hierarchy>',
                                        '<node class="android.widget.Button" text="Seguir" clickable="true"'
                                        ' bounds="[10,205][410,255]"/></hierarchy>'))
    try:
        Replayer(recipe_id=1, version=1, actions=acoes, variables={**VARIAVEIS, "recipient": "QA-002"}).next(outra)
        raise AssertionError("deveria divergir")
    except RecipeDiverged as exc:
        assert "contêiner" in str(exc)
