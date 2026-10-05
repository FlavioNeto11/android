"""31.59, a bolha sem `resource_id`: a árvore REAL do fio do Direct medida no 31.26 (04/10 23:50:58Z, android-03), reduzida
a bounds, classes e ids, com pacote e texto fictícios.

A bolha enviada é um `TextView` SEM id, filho direto da lista (`RecyclerView`, que não se declara rolável com o fio curto).
Com a R1 do 31.59, `ultima_bolha_igual` exigia `resource_id` e devolvia `None`: a prova sem IA, que fechou o envio real,
passaria a pagar o juiz. Aqui o contêiner de lista decide o que é "abaixo", e a dúvida continua indo ao juiz:

- a árvore real reduzida prova o envio (`True`);
- a bolha antiga com o mesmo texto e a mesma contagem da linha de base não prova (`False`);
- rótulo curto abaixo da bolha, duas bolhas iguais, bolha fora de lista e texto no campo: nunca "enviado".

Nível de prova: `simulated` (árvore montada; nenhum aparelho).
"""
from __future__ import annotations

from app.automation.hierarchy import parse_hierarchy

PKG = "com.exemplo.chat"
TEXTO = "Mensagem de teste r3159"


def _no(bounds: str, *, classe: str = "android.widget.TextView", rid: str = "", texto: str = "", desc: str = "",
        editavel: bool = False) -> str:
    rid_attr = f"{PKG}:id/{rid}" if rid else ""
    return (f'<node text="{texto}" resource-id="{rid_attr}" class="{"android.widget.EditText" if editavel else classe}"'
            f' package="{PKG}" content-desc="{desc}" clickable="false" enabled="true" scrollable="false"'
            f' bounds="{bounds}" />')


def _fio(*meio: str, lista: bool = True) -> str:
    """O fio reduzido: cabeçalho, a lista (ou um FrameLayout no lugar dela) com o que vier, e o campo de escrita FORA dela."""
    classe = "androidx.recyclerview.widget.RecyclerView" if lista else "android.widget.FrameLayout"
    return ('<hierarchy rotation="0">'
            + _no("[0,0][720,1232]", classe="android.widget.FrameLayout", rid="raiz")
            + _no("[232,70][443,106]", classe="android.widget.Button", rid="titulo", texto="conta-de-teste")
            + _no("[0,160][720,1166]", classe=classe, rid="lista")
            + _no("[0,935][720,998]", texto="Hoje")                       # o rótulo de data, ACIMA da bolha
            + "".join(meio)
            + _no("[44,1116][586,1216]", rid="campo", editavel=True)
            + _no("[586,1132][690,1204]", classe="android.widget.ImageView", rid="enviar", desc="Send")
            + "</hierarchy>")


BOLHA = _no("[236,1042][680,1080]", texto=TEXTO)


def test_a_arvore_real_reduzida_prova_o_envio_sem_ia() -> None:
    arvore = parse_hierarchy(_fio(BOLHA))
    assert arvore.ultima_bolha_igual(TEXTO) is not None
    assert arvore.sent_as_message(TEXTO, antes=0) is True


def test_a_bolha_antiga_com_o_mesmo_texto_e_a_mesma_contagem_nao_prova() -> None:
    """A linha de base do toque vale: a contagem por igualdade tem de AUMENTAR."""
    assert parse_hierarchy(_fio(BOLHA)).sent_as_message(TEXTO, antes=1) is False


def test_rotulo_curto_logo_abaixo_nunca_vira_a_bolha_e_manda_ao_juiz() -> None:
    """A hora, o "Seen" ou a reação logo abaixo: sem o tipo da bolha, a árvore não separa rótulo de uma resposta curta,
    então a prova NÃO afirma o envio (cai no juiz) enquanto o rótulo estiver lá. Ele não é contado como bolha."""
    arvore = parse_hierarchy(_fio(BOLHA, _no("[600,1084][680,1100]", texto="Seen")))
    assert arvore.mensagens_iguais(TEXTO) == 1
    assert arvore.ultima_bolha_igual(TEXTO) is None
    assert arvore.sent_as_message(TEXTO, antes=0) is False


def test_resposta_abaixo_da_bolha_derruba_a_prova() -> None:
    arvore = parse_hierarchy(_fio(BOLHA, _no("[40,1090][300,1110]", texto="ok")))
    assert arvore.sent_as_message(TEXTO, antes=0) is False


def test_duas_bolhas_iguais_sem_id_nao_dizem_qual_e_a_nova() -> None:
    arvore = parse_hierarchy(_fio(_no("[236,600][680,640]", texto=TEXTO), BOLHA))
    assert arvore.mensagens_iguais(TEXTO) == 2
    assert arvore.ultima_bolha_igual(TEXTO) is None
    assert arvore.sent_as_message(TEXTO, antes=1) is False


def test_bolha_fora_de_contener_de_lista_nao_prova() -> None:
    assert parse_hierarchy(_fio(BOLHA, lista=False)).sent_as_message(TEXTO, antes=0) is False


def test_texto_ainda_no_campo_nao_prova() -> None:
    xml = _fio(BOLHA).replace('class="android.widget.EditText" package="com.exemplo.chat" content-desc=""',
                              'class="android.widget.EditText" package="com.exemplo.chat" content-desc=""', 1)
    xml = xml.replace(f'text="" resource-id="{PKG}:id/campo"', f'text="{TEXTO}" resource-id="{PKG}:id/campo"')
    assert parse_hierarchy(xml).sent_as_message(TEXTO, antes=0) is False
