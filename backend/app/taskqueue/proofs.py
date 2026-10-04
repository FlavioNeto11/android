"""Provas LOCAIS de pós-condição `model_judged`: a árvore da tela comprova a etapa sem chamar o modelo.

Cada capability do catálogo pode declarar `local_proof`. Uma prova POSITIVA dispensa o verificador; negativa ou
indisponível cai para o modelo, como sempre — a prova local nunca reprova sozinha. Gramática:

- `sent_text`: o `content` da etapa apareceu num elemento não editável e sumiu do campo de escrita
  (`UiTree.sent_as_message`, achado #102);
- `sent_text:<seletor>`: o mesmo, com o campo de escrita DA CONVERSA declarado — ele precisa estar na tela e sem o
  texto. É o critério objetivo da DM (ADR-055): bolha com o texto e campo vazio = enviada. Sem o campo na tela, não
  dá para dizer que ele está vazio, e a árvore não afirma. A marca de pendente ("Sending…", `pending_marks`) é
  conferida por quem chama, antes desta prova: bolha com "Sending…" embaixo também passaria aqui;
- `selector:<seletor>`: algum elemento casa o seletor (`id=`, `desc=`, `text=`; `==` casa exato; `|` une partes
  no mesmo elemento). `{username}` e afins são resolvidos pelos bindings da etapa; `text=@ana` também casa "ana".
  `&` exige vários seletores na mesma tela, cada um no seu elemento: `text=={username}&id=composer` (27/09);
- `selector_band:<seletor>`: como acima, mas o elemento casado precisa estar na MESMA faixa vertical de cada
  `band_guard` da etapa — numa lista de comentários, o coração marcado do comentário de cima não prova o de baixo.

Com `card_guard` na etapa (a legenda da publicação alvo, já resolvida — `planning.capabilities.guardas_do_cartao`),
o elemento casado pelo primeiro seletor precisa também estar no CARTÃO que traz cada texto (`UiTree.text_in_card`):
num feed de publicações, o `desc==Liked` de outro cartão não prova a curtida desta (r-20260928165254-e31953). Sem
`card_guard`, nada muda.

Medido em 19-24/09: 207 chamadas de verificação para 158 etapas julgadas; boa parte delas conferia coisa que a
árvore já dizia (`desc="Liked"`, título "Comments", username no `action_bar_title`).
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from ..automation.hierarchy import UiTree
from ..planning.capabilities import LOCAL_PROOFS, local_proof_error  # noqa: F401  (a gramática mora no catálogo)
from ..util import norm_text
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


def marcas_pendentes_na_tela(marcas: Iterable[str], tree: UiTree) -> list[str]:
    """As marcas de efeito PENDENTE (`pending_marks` do catálogo, ex.: "Sending…") visíveis nesta tela.

    Por texto, como as marcas de falha: qualquer elemento, texto ou descrição. Não se exclui a bolha que traz o
    conteúdo: o Instagram pode juntar mensagem e status na mesma descrição de acessibilidade, e excluí-la daria por
    enviada justamente a DM pendente. O preço é o lado seguro: uma DM cujo texto contenha "Sending…" fica pendente
    até o prazo e vira incerta, nunca enviada sem prova."""
    return [m for m in marcas if m and tree.contains_text(m)]


#: Ordem dos níveis que um marcador afirma, para ficar com o maior quando a mesma linha tem dois.
_ORDEM_DO_MARCADOR = {"sent": 1, "delivered": 2, "read": 3}


def nivel_pelo_marcador(marcas: Iterable[tuple[str, str]], conteudo: str | None, tree: UiTree, *,
                        pendentes: Iterable[str] = (), falhas: Iterable[str] = ()) -> str | None:
    """31.57: o nível de entrega que o MARCADOR declarado no catálogo afirma para a mensagem DESTA execução, ou `None`
    (o juiz decide, como antes). Só afirma quando tudo vale:

    - o texto saiu (`sent_as_message`: numa bolha, fora do campo de escrita);
    - nenhuma marca de pendente ("Sending…") nem de falha ("Not delivered") na tela;
    - o marcador é a PRIMEIRA linha de texto logo abaixo da bolha mais baixa com o texto. O "Seen" de uma mensagem
      antiga fica acima dela, e uma mensagem depois da nossa ficaria entre as duas; nos dois casos não casa.

    A comparação é de texto inteiro normalizado (o "Seen" de uma frase não conta). Nunca reprova: `None` só devolve a
    pergunta ao juiz."""
    pares = [(nivel, norm_text(texto)) for nivel, texto in marcas if norm_text(texto)]
    n = norm_text(conteudo or "")
    if not pares or not n or tree.sent_as_message(conteudo or "") is not True:
        return None
    if marcas_pendentes_na_tela(pendentes, tree) or marcas_pendentes_na_tela(falhas, tree):
        return None
    bolhas = [e for e in tree.elements if not e.editable and n in norm_text(f"{e.text} {e.desc}")]
    if not bolhas:
        return None
    bolha = max(bolhas, key=lambda e: e.bounds[3])
    abaixo = [e for e in tree.elements if e is not bolha and norm_text(f"{e.text} {e.desc}")
              and e.bounds[1] >= bolha.bounds[3]]
    if not abaixo:
        return None
    topo = min(e.bounds[1] for e in abaixo)
    linha = {norm_text(r) for e in abaixo if e.bounds[1] == topo for r in (e.text, e.desc) if norm_text(r)}
    casados = [nivel for nivel, marca in pares if marca in linha]
    return max(casados, key=lambda nivel: _ORDEM_DO_MARCADOR[nivel]) if casados else None


def local_proof_holds(local_proof: str | None, step: Any, tree: UiTree) -> bool | None:
    """`True` = comprovado pela árvore, sem modelo. `False`/`None` = não dá para afirmar: o chamador julga pelo
    modelo, como antes. Nunca vira reprovação por si só."""
    if not local_proof:
        return None
    bindings = {k: str(v) for k, v in (getattr(step, "bindings", None) or {}).items() if v is not None}
    tipo, _, bruto = local_proof.partition(":")
    if tipo == "sent_text":
        conteudo = bindings.get("content")
        if not conteudo:
            return None
        if bruto:
            campo = resolve_templates(bruto.strip(), bindings) or ""
            if not campo or "{" in campo:
                return None
            compositores = tree.find_proof(campo, variants=variantes_de_arroba)
            # O campo de escrita da conversa precisa ESTAR na tela para se dizer que está vazio; com o texto ainda
            # nele, a mensagem não saiu (a dica "Message…" do campo vazio não contém o texto).
            if not compositores or any(norm_text(conteudo) in norm_text(c.text) for c in compositores):
                return False
        return tree.sent_as_message(conteudo)
    if tipo == "count_gt":
        return _contagem_maior(bruto, bindings, tree)
    # O `&` é separado ANTES de resolver as variáveis: um valor de binding nunca vira operador da prova.
    seletores = [resolve_templates(p.strip(), bindings) or "" for p in bruto.split("&")]
    if any(not s or "{" in s for s in seletores):     # variável sem valor nesta etapa: não há o que provar
        return None
    seletor, *tambem = seletores
    achados = tree.find_proof(seletor, variants=variantes_de_arroba)
    cartao = [c for c in (getattr(step, "card_guard", None) or []) if c]
    if cartao:
        achados = [e for e in achados
                   if all(any(tree.text_in_card(v, e) for v in variantes_de_arroba(c)) for c in cartao)]
    if not achados:
        return False
    if tipo == "selector":
        return all(tree.find_proof(s, variants=variantes_de_arroba) for s in tambem)
    if tipo == "selector_band":
        guardas = [g for g in (getattr(step, "band_guard", None) or []) if g]
        if not guardas:
            return False                              # faixa sem guarda declarada não prova nada
        return any(all(any(tree.text_in_band(v, e.bounds) for v in variantes_de_arroba(g)) for g in guardas)
                   for e in achados)
    return None


# Um inteiro com ou sem separador de milhar ("1,234", "1.234", "12"). Grupos de 3 dígitos depois do primeiro: "1.2" não
# é milhar (é decimal) e fica com o "1" só se vier colado a um sufixo, caso que `_primeiro_inteiro` recusa antes.
_INTEIRO = re.compile(r"(?<![\d.,])(\d{1,3}(?:[.,]\d{3})+|\d+)(?![\d])")
# Sufixo que abrevia a contagem ("1.2K", "3 mil", "2M"): o texto não traz o número exato.
_ABREVIADO = re.compile(r"^\s*(?:[kKmM]\b|mil\b|milh)", re.IGNORECASE)


def _primeiro_inteiro(texto: str) -> int | None:
    """O primeiro inteiro do texto, ou `None` quando não dá para afirmar o valor: sem número, decimal ("1.2K") ou
    abreviado ("1 mil", "3M"). Uma contagem abreviada nunca é comparada: o chute faria a prova mentir."""
    achado = _INTEIRO.search(texto or "")
    if achado is None:
        return None
    resto = (texto or "")[achado.end():]
    if _ABREVIADO.match(resto) or re.match(r"^[.,]\d", resto):
        return None
    return int(re.sub(r"[.,]", "", achado.group(1)))


def _contagem_maior(bruto: str, bindings: dict[str, str], tree: UiTree) -> bool | None:
    """`count_gt:<binding>:<seletor>` (29.30): o número do elemento é MAIOR que o do binding (a contagem lida ANTES).

    `True` = maior; `False` = igual ou menor (nenhuma publicação nova apareceu); `None` = não dá para afirmar
    (binding ausente ou não numérico, elemento ausente, texto sem número ou abreviado). Nunca reprova por si só.

    30.60: guardas depois de `&` (`count_gt:posts_antes:<contagem>&<guarda>`) precisam TODAS casar na tela, com as
    variáveis resolvidas pelos argumentos; sem elas, a contagem de OUTRO perfil (um sugerido aberto depois do Share)
    provaria a publicação. Guarda com variável sem valor ou que não casa: `None` (o modelo julga), nunca `False`."""
    nome, _, resto = bruto.partition(":")
    seletor, *guardas = [p.strip() for p in resto.split("&")]
    antes = bindings.get(nome.strip())
    if antes is None or not re.fullmatch(r"\s*\d+\s*", antes) or not seletor:
        return None
    for guarda in guardas:
        resolvida = resolve_templates(guarda, bindings) or ""
        if not resolvida or "{" in resolvida or not tree.find_proof(resolvida, variants=variantes_de_arroba):
            return None
    achados = tree.find_proof(seletor, variants=variantes_de_arroba)
    if not achados:
        return None
    numeros = [n for e in achados if (n := _primeiro_inteiro(e.text or e.desc or "")) is not None]
    if not numeros:
        return None
    return max(numeros) > int(antes)
