"""Provas LOCAIS de pós-condição `model_judged`: a árvore da tela comprova a etapa sem chamar o modelo.

Cada capability do catálogo pode declarar `local_proof`. Uma prova POSITIVA dispensa o verificador; negativa ou
indisponível cai para o modelo, como sempre — a prova local nunca reprova sozinha. Gramática:

- `sent_text`: o `content` da etapa apareceu num elemento não editável e sumiu do campo de escrita
  (`UiTree.sent_as_message`, achado #102);
- `selector:<seletor>`: algum elemento casa o seletor (`id=`, `desc=`, `text=`; `==` casa exato; `|` une partes
  no mesmo elemento). `{username}` e afins são resolvidos pelos bindings da etapa; `text=@ana` também casa "ana";
- `selector_band:<seletor>`: como acima, mas o elemento casado precisa estar na MESMA faixa vertical de cada
  `band_guard` da etapa — numa lista de comentários, o coração marcado do comentário de cima não prova o de baixo.

Medido em 19-24/09: 207 chamadas de verificação para 158 etapas julgadas; boa parte delas conferia coisa que a
árvore já dizia (`desc="Liked"`, título "Comments", username no `action_bar_title`).
"""
from __future__ import annotations

from typing import Any

from ..automation.hierarchy import UiTree
from ..planning.capabilities import LOCAL_PROOFS, local_proof_error  # noqa: F401  (a gramática mora no catálogo)
from .repository import resolve_templates


def variantes_de_arroba(term: str) -> tuple[str, ...]:
    """Formas aceitas de um texto de guarda ou de prova: um `@usuario` também vale escrito sem a arroba.

    O Instagram quase nunca mostra a arroba: o cabeçalho da conversa traz "thi.mnz", o autor do comentário traz o
    usuário puro, a lista de pedidos idem. Exigir o literal "@thi.mnz" reprovava o envio com a conversa CERTA aberta
    e o texto já digitado (r-20260918214743-54d31a). A arroba é notação nossa, não o que está na tela — e a guarda
    não fica mais frouxa: continua exigindo o mesmo usuário visível, só aceita as duas grafias.
    """
    t = (term or "").strip()
    return (t, t[1:]) if t.startswith("@") and len(t) > 1 else (t,)


def local_proof_holds(local_proof: str | None, step: Any, tree: UiTree) -> bool | None:
    """`True` = comprovado pela árvore, sem modelo. `False`/`None` = não dá para afirmar: o chamador julga pelo
    modelo, como antes. Nunca vira reprovação por si só."""
    if not local_proof:
        return None
    bindings = {k: str(v) for k, v in (getattr(step, "bindings", None) or {}).items() if v is not None}
    if local_proof == "sent_text":
        conteudo = bindings.get("content")
        return tree.sent_as_message(conteudo) if conteudo else None
    tipo, _, seletor = local_proof.partition(":")
    seletor = resolve_templates(seletor.strip(), bindings) or ""
    if not seletor or "{" in seletor:                 # variável sem valor nesta etapa: não há o que provar
        return None
    achados = tree.find_proof(seletor, variants=variantes_de_arroba)
    if not achados:
        return False
    if tipo == "selector":
        return True
    if tipo == "selector_band":
        guardas = [g for g in (getattr(step, "band_guard", None) or []) if g]
        if not guardas:
            return False                              # faixa sem guarda declarada não prova nada
        return any(all(any(tree.text_in_band(v, e.bounds) for v in variantes_de_arroba(g)) for g in guardas)
                   for e in achados)
    return None
