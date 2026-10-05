"""Item 31.51: o botão que fecha um diálogo do site, achado pela árvore, sem IA.

Na r-20261004190200-5b56e6 (Chrome, android-10) dois diálogos do site cobriam a página, um depois do outro: o "Abra o
app e ganhe frete grátis" (com o botão "Agora não", `download-app-bottom-banner-close`) e o de cookies. A limpeza
opcional deixava a IA decidir: ela apertou "voltar" (saiu do site), reabriu o Chrome e gastou as 3 ações da etapa sem
tocar no botão que estava na árvore; no plano revisado tocou "Configurar cookies", que abre OUTRO modal. Aqui o executor
acha o botão de fechar ou recusar por uma lista fechada de rótulos e toca nele antes de chamar o ator, um diálogo por
vez, com teto próprio (`LIMITE_DE_DIALOGOS`), sem gastar as ações da etapa.

Privacidade: nos avisos de cookies só se recusa o não essencial. "Aceitar", "Permitir", "Concordo" e "Configurar"
nunca são tocados pela regra; um aviso que só oferece aceitar ou configurar fica com a IA, como antes.
"""
from __future__ import annotations

import re
import unicodedata

from ..automation.hierarchy import UiElement, UiTree

#: Quantos diálogos em série a regra fecha numa tentativa da limpeza (os da 5b56e6 eram dois).
LIMITE_DE_DIALOGOS = 4

#: Revisão do #308: a regra só vale no navegador (o item é dele). Num app com conta real, um aviso não reconhecido com
#: "Dismiss" não se fecha por regra sem pessoa.
NAVEGADORES = frozenset({"com.android.chrome"})

#: Fração mínima da tela que um diálogo cobre para contar como "o que cobre" quando a área do juiz não é conhecida
#: (revisão do #308, 3a: um id banner/modal que sobra pequeno na página não transforma a limpeza certa em falha).
FRACAO_QUE_COBRE = 0.15


def e_navegador(pacote: str | None) -> bool:
    return (pacote or "") in NAVEGADORES

#: Rótulos que fecham ou recusam, na ordem de preferência (recusar o não essencial antes de só fechar). Comparados sem
#: acento e sem caixa, com o rótulo INTEIRO (não "contém"): "Não aceitar cookies" não casa com "aceitar".
_ROTULOS_QUE_FECHAM: tuple[str, ...] = (
    "rejeitar todos", "rejeitar tudo", "rejeitar", "recusar todos", "recusar tudo", "recusar",
    # O "Rejeitar cookies" da folha do gov.br (captura de 05/10): sem isto, a trava do 31.72 recusaria a saída certa.
    "rejeitar cookies", "recusar cookies", "reject cookies", "decline cookies",
    "recusar opcionais", "rejeitar opcionais", "apenas necessarios", "somente necessarios", "apenas essenciais",
    "somente essenciais", "usar apenas cookies necessarios", "continuar sem aceitar", "nao aceitar",
    "reject all", "reject", "decline", "only necessary", "necessary only", "continue without accepting",
    "continuar no navegador", "continuar no site", "usar o navegador", "ficar no navegador",
    "continue in browser", "continue on web", "stay on web",
    "agora nao", "nao, obrigado", "nao obrigado", "mais tarde", "fechar", "dispensar",
    "not now", "no thanks", "no, thanks", "close", "dismiss", "x", "×", "✕",
)

#: Id do botão que fecha (o "Agora não" da 5b56e6 é `download-app-bottom-banner-close`).
_ID_QUE_FECHA = re.compile(r"(^|[-_/:])(close|dismiss|reject|decline|fechar|recusar)([-_]|$)", re.IGNORECASE)

#: O que a regra nunca toca, no rótulo E no id (revisão do #308: `cookie-accept-and-close`), mesmo que algo acima
#: também case. "Got it", "Entendi" e "OK" valem como aceite num banner de consentimento implícito.
_NUNCA = re.compile(r"aceit|acept|akzept|accept|permit|allow|concord|agree|configur|personaliz|gerenciar|manage|"
                    r"settings|got[ _-]?it|entendi|\bok(ay)?\b", re.IGNORECASE)

#: O que, na árvore, tem cara de diálogo, banner ou aviso de cookies (classe, id ou texto).
_PISTAS = re.compile(r"dialog|modal|banner|cookie|popup|pop_up|overlay|consent|bottom_?sheet|privacidade|privacy",
                     re.IGNORECASE)


def _normal(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii") if texto else ""
    return " ".join(sem_acento.lower().replace("’", "'").split()) if sem_acento.strip() else texto.strip().lower()


def _dentro(e: UiElement, caixa: tuple[int, int, int, int]) -> bool:
    x1, y1, x2, y2 = caixa
    a1, b1, a2, b2 = e.bounds
    return x1 <= a1 and y1 <= b1 and a2 <= x2 and b2 <= y2


def _rotulo(e: UiElement) -> str:
    return (e.text or e.desc or "").strip()


def _caixas_de_dialogo(tree: UiTree) -> list[tuple[int, int, int, int]]:
    """As caixas dos elementos com cara de diálogo, banner ou aviso de cookies (sem repetir)."""
    caixas: list[tuple[int, int, int, int]] = []
    for e in tree.elements:
        if (_PISTAS.search(e.class_name or "") or _PISTAS.search(e.resource_id or "")
                or _PISTAS.search(_rotulo(e)[:80])) and e.bounds not in caixas:
            caixas.append(e.bounds)
    return caixas


#: O motivo literal da limpeza que acha um diálogo e não acha saída que preserve a privacidade (31.51, regra em
#: `learning/domain/falhas.py`): nunca aceita, nunca vira sucesso, nunca passa para a IA (que poderia aceitar).
MOTIVO_SEM_SAIDA = "A limpeza não fechou o diálogo do site"


def dialogo_sem_saida(tree: UiTree, area: tuple[int, int, int, int] | None = None) -> str | None:
    """O texto do diálogo que segue na tela sem botão de recusar, fechar ou continuar no navegador (o aviso de cookies
    que só oferece aceitar ou configurar, ou um diálogo não reconhecido); `None` se não há diálogo, ou se há saída."""
    if botao_que_fecha(tree, area) is not None:
        return None
    for e in tree.elements:
        # Só o que tem cara de diálogo (classe ou id), dentro da área do que cobria quando se sabe: um elemento
        # qualquer que o juiz citou (um "Voltar") não é diálogo do site e segue com a IA, como antes.
        if (_PISTAS.search(e.class_name or "") or _PISTAS.search(e.resource_id or "")) \
                and (_dentro(e, area) or e.bounds == area if area is not None else _cobre_a_tela(e, tree)):
            return (_rotulo(e) or e.resource_id or e.class_name.rsplit(".", 1)[-1])[:80]
    return None


def _cobre_a_tela(e: UiElement, tree: UiTree) -> bool:
    largura = max((x.bounds[2] for x in tree.elements), default=0)
    altura = max((x.bounds[3] for x in tree.elements), default=0)
    x1, y1, x2, y2 = e.bounds
    return largura > 0 and altura > 0 and (x2 - x1) * (y2 - y1) >= FRACAO_QUE_COBRE * largura * altura


def botao_que_fecha(tree: UiTree, area: tuple[int, int, int, int] | None = None) -> UiElement | None:
    """O botão clicável que fecha ou recusa um diálogo: dentro da `area` (o que cobria, quando se sabe) ou de algum
    elemento com cara de diálogo; senão `None` (a IA decide, como antes). O rótulo vence o id; entre rótulos, a ordem de
    `_ROTULOS_QUE_FECHAM` (recusar antes de fechar)."""
    caixas = [area] if area is not None else []
    caixas += [c for c in _caixas_de_dialogo(tree) if c not in caixas]
    if not caixas:
        return None
    candidatos: list[tuple[int, UiElement]] = []
    for e in tree.elements:
        if not e.clickable or not e.enabled or not any(_dentro(e, c) for c in caixas):
            continue
        rotulo, rid = _rotulo(e), e.resource_id or ""
        # Releitura do #308: o veto olha text, desc e id, os três; o rótulo usa só um deles (text "X", desc "Accept").
        if any(_NUNCA.search(x) for x in (e.text or "", e.desc or "", rid)):
            continue
        normal = _normal(rotulo)
        if normal in _ROTULOS_QUE_FECHAM:
            candidatos.append((_ROTULOS_QUE_FECHAM.index(normal), e))
        elif not rotulo and _ID_QUE_FECHA.search(rid):
            # Só o ícone SEM rótulo vale pelo id (revisão do #308, 1b): um "Got it" com id `*-close` é aceite.
            candidatos.append((len(_ROTULOS_QUE_FECHAM), e))
    if not candidatos:
        return None
    return min(candidatos, key=lambda par: par[0])[1]


# ------------------------------------------------------------------------------- 31.72: a regra vale para o ATOR
#: O que tem cara de aviso de CONSENTIMENTO (cookies, privacidade, LGPD), no rótulo, no id ou na classe. Mais estreito
#: que `_PISTAS`: um "OK" de um diálogo qualquer do site não é consentimento.
_CONSENTIMENTO = re.compile(r"cookie|consent|privacidade|privacy|lgpd|gdpr|rgpd", re.IGNORECASE)

#: Acima desta fração da tela, um elemento marcado é a página (o WebView com "privacidade" no título), não um aviso.
_FRACAO_DA_PAGINA = 0.6

#: A faixa do aviso: a altura do elemento marcado mais esta fração da tela acima e abaixo. O leitor da árvore descarta
#: o contêiner vazio e não clicável, e no Chrome o texto do aviso e os botões costumam ser IRMÃOS nele: sem o
#: contêiner, a zona é a faixa em volta do texto (o aviso de cookies do ML: texto e botões em ~230 px de 1280).
_MARGEM_DA_FAIXA = 0.12

#: O motivo literal do toque recusado (regra em `learning/domain/falhas.py`).
MOTIVO_ACEITE_RECUSADO = "o consentimento do site não é aceito pelo ator"

#: N8 da leitura do #386: quantas recusas desta trava, somadas na EXECUÇÃO, encerram a etapa (alternar o aceite com
#: `observe_screen` zera o `errors_in_row` e gastaria decisões até o teto).
LIMITE_DE_RECUSAS_DE_ACEITE = 4

#: B1 da leitura do #386: o `type_text` com `element_id` toca o elemento antes de escrever; no navegador, só em campo.
REJEICAO_TYPE_TEXT_FORA_DE_CAMPO = "no navegador, type_text só em campo editável"


#: K2 da releitura do #386: no RÓTULO, a palavra de aceite só conta no começo de palavra ("inaceitável" não é aceite).
#: No id fica o `_NUNCA` sem fronteira: `btnAccept` e `cookieAcceptAll` não têm fronteira antes do "Accept".
_NUNCA_NO_ROTULO = re.compile(r"\b(?:" + _NUNCA.pattern + ")", re.IGNORECASE)

#: K2: o id da interface do PRÓPRIO navegador (`com.android.chrome:id/...`). A página da web não tem id com pacote.
#: A página inicial anônima do Chrome tem "Block third-party cookies" e `cookie_controls_card` (captura de 05/10): é
#: interface do Chrome, não aviso de site, e não vira marca.
_ID_DO_NAVEGADOR = tuple(f"{p}:id/" for p in NAVEGADORES)


def _do_navegador(e: UiElement) -> bool:
    return (e.resource_id or "").startswith(_ID_DO_NAVEGADOR)


def _de_consentimento(e: UiElement) -> bool:
    if _do_navegador(e):
        return False
    return any(_CONSENTIMENTO.search(x) for x in (e.class_name or "", e.resource_id or "", _rotulo(e)[:120]))


def _diz_aceitar(e: UiElement) -> bool:
    """K1 da releitura: o rótulo EXATO da lista de fechar vence o `_NUNCA` no rótulo ("Não aceitar", "Continuar sem
    aceitar"); o `_NUNCA` no id recusa sempre (`cookie-accept-and-close`)."""
    if _NUNCA.search(e.resource_id or ""):
        return True
    if _normal(_rotulo(e)) in _ROTULOS_QUE_FECHAM:
        return False
    return any(_NUNCA_NO_ROTULO.search(x) for x in (e.text or "", e.desc or ""))


def _contem(fora: tuple[int, int, int, int], dentro: tuple[int, int, int, int]) -> bool:
    return fora[0] <= dentro[0] and fora[1] <= dentro[1] and dentro[2] <= fora[2] and dentro[3] <= fora[3]


def _area(caixa: tuple[int, int, int, int]) -> int:
    return max(0, caixa[2] - caixa[0]) * max(0, caixa[3] - caixa[1])


def _fecha_ou_recusa(e: UiElement) -> bool:
    """O critério de `botao_que_fecha`, para um elemento só: rótulo da lista fechada, ou ícone sem rótulo com id de
    fechar; nunca o que diz aceitar (`_diz_aceitar`, K1)."""
    if _diz_aceitar(e):
        return False
    rotulo = _rotulo(e)
    return _normal(rotulo) in _ROTULOS_QUE_FECHAM or (not rotulo and bool(_ID_QUE_FECHA.search(e.resource_id or "")))


def toque_que_aceita(tree: UiTree, alvo: UiElement | None,
                     ponto: tuple[int, int] | None = None) -> UiElement | None:
    """O elemento cujo toque ACEITARIA um aviso de consentimento do site; `None` quando o toque pode seguir.

    Com qualquer marca de consentimento na tela, o rótulo que diz aceitar (`_NUNCA`: "ACEITAR TODOS", "Allow all",
    "Concordo", "OK") é recusado em qualquer lugar: o "Aceitar" de um aviso alto fica fora da faixa do texto (leitura do
    #386). Na faixa da tela em volta de cada marca que não seja a página (`_MARGEM_DA_FAIXA`), só passam o recusar e o
    fechar de `botao_que_fecha` e o campo de texto; todo o resto é recusado, inclusive o que não tem a palavra
    ("Continuar", o botão com o texto só na imagem). O toque é julgado pelo PONTO tocado (`ponto`, do
    `resolve_point`), não pelo centro do elemento achado. Prefere o falso positivo (o toque recusado) ao aceite em
    silêncio."""
    if alvo is None:
        return None
    largura = max((e.bounds[2] for e in tree.elements), default=0)
    altura = max((e.bounds[3] for e in tree.elements), default=0)
    pagina = _FRACAO_DA_PAGINA * largura * altura
    # K2: só a marca que tem cara de aviso (abaixo de 60 % da tela; a interface do navegador já saiu em
    # `_de_consentimento`) liga a trava.
    marcas = [e for e in tree.elements if _de_consentimento(e) and _area(e.bounds) < pagina]
    if not marcas:
        return None
    x = ponto[0] if ponto is not None else (alvo.bounds[0] + alvo.bounds[2]) / 2
    y = ponto[1] if ponto is not None else (alvo.bounds[1] + alvo.bounds[3]) / 2
    na_zona = any(_na_zona(tree, m, x, y, pagina, _MARGEM_DA_FAIXA * altura) for m in marcas)
    if _do_navegador(alvo):
        # K2: o botão do navegador (o menu da barra de tradução por cima da folha de cookies do gov.br, 05/10) não é
        # a página: fora da zona não se julga pelo rótulo; dentro dela, só o que diz aceitar é recusado.
        return alvo if na_zona and _diz_aceitar(alvo) else None
    if _diz_aceitar(alvo):
        return alvo
    if alvo.editable:
        return None                                    # tocar num campo de texto não aceita nada
    if na_zona and not _fecha_ou_recusa(alvo):
        return alvo
    return None


def _na_zona(tree: UiTree, marca: UiElement, x: float, y: float, pagina: float, margem: float) -> bool:
    """A zona da marca. Regra da caixa (releitura do #386): quando a marca está dentro de uma caixa reconhecida (a
    maior que a contém, abaixo da fração da página, que é marca ou tem `_PISTAS`, e que contém mais que a própria
    marca), a zona é a caixa inteira: a folha de cookies do gov.br (05/10) cobre 49 % do rodapé, e a página por baixo
    dela não recebe o toque. Sem caixa, a faixa em volta da marca, porque o leitor descarta o contêiner vazio."""
    caixas = [e for e in tree.elements if _contem(e.bounds, marca.bounds) and _area(e.bounds) < pagina
              and (e is marca or _de_consentimento(e) or _PISTAS.search(e.class_name or "")
                   or _PISTAS.search(e.resource_id or ""))]
    caixa = max(caixas, key=lambda e: _area(e.bounds), default=marca)
    if any(e is not caixa and _contem(caixa.bounds, e.bounds) for e in tree.elements):
        b = caixa.bounds
        return b[0] <= x <= b[2] and b[1] <= y <= b[3]
    return marca.bounds[1] - margem <= y <= marca.bounds[3] + margem


def rotulo_para_o_ator(e: UiElement) -> str:
    """O rótulo de um elemento da página para o histórico do ator: espaços normalizados, até 60 caracteres. É texto da
    página: vai só ao ator, nunca ao `error` nem ao `status_detail` (que chegam a aviso e cartão)."""
    bruto = _rotulo(e) or e.resource_id or e.class_name.rsplit(".", 1)[-1]
    # Sem caractere de controle nem de formatação (Cc, Cf: o RTL e o zero-width da página) no histórico do ator.
    return " ".join("".join(c for c in bruto if unicodedata.category(c) not in ("Cc", "Cf")).split())[:60]


def tipo_do_elemento(e: UiElement) -> str:
    return e.class_name.rsplit(".", 1)[-1] or "elemento"
