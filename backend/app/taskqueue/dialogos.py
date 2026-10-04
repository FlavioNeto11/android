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

#: Rótulos que fecham ou recusam, na ordem de preferência (recusar o não essencial antes de só fechar). Comparados sem
#: acento e sem caixa, com o rótulo INTEIRO (não "contém"): "Não aceitar cookies" não casa com "aceitar".
_ROTULOS_QUE_FECHAM: tuple[str, ...] = (
    "rejeitar todos", "rejeitar tudo", "rejeitar", "recusar todos", "recusar tudo", "recusar",
    "recusar opcionais", "rejeitar opcionais", "apenas necessarios", "somente necessarios", "apenas essenciais",
    "somente essenciais", "usar apenas cookies necessarios", "continuar sem aceitar", "nao aceitar",
    "reject all", "reject", "decline", "only necessary", "necessary only", "continue without accepting",
    "agora nao", "nao, obrigado", "nao obrigado", "mais tarde", "fechar", "dispensar",
    "not now", "no thanks", "no, thanks", "close", "dismiss", "x", "×", "✕",
)

#: Id do botão que fecha (o "Agora não" da 5b56e6 é `download-app-bottom-banner-close`).
_ID_QUE_FECHA = re.compile(r"(^|[-_/:])(close|dismiss|reject|decline|fechar|recusar)([-_]|$)", re.IGNORECASE)

#: O que a regra nunca toca, mesmo que o id ou o rótulo também case com algo acima.
_NUNCA = re.compile(r"aceit|accept|permit|allow|concord|agree|configur|personaliz|gerenciar|manage|settings|ok\b",
                    re.IGNORECASE)

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
        rotulo = _rotulo(e)
        if _NUNCA.search(rotulo):
            continue
        normal = _normal(rotulo)
        if normal in _ROTULOS_QUE_FECHAM:
            candidatos.append((_ROTULOS_QUE_FECHAM.index(normal), e))
        elif not rotulo and _ID_QUE_FECHA.search(e.resource_id or ""):
            candidatos.append((len(_ROTULOS_QUE_FECHAM), e))
        elif _ID_QUE_FECHA.search(e.resource_id or "") and not _NUNCA.search(e.resource_id or ""):
            candidatos.append((len(_ROTULOS_QUE_FECHAM) + 1, e))
    if not candidatos:
        return None
    return min(candidatos, key=lambda par: par[0])[1]
