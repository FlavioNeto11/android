"""31.121 e 31.122: a etapa ensinada começa onde a reprodução começa e só passa quando agiu.

Achados das execuções reais de fluxos ensinados (06/10: 3 de 7 divergiram na 1ª etapa, e a prova do 31.87 pagou toda a
IA da reprodução por isso):

- **31.121.** A pessoa abre a gravação com o app já na frente, e a 1ª entrada é um toque dentro dele. Na reprodução o
  aparelho está em outra tela, a receita da 1ª etapa diverge ("tela de partida diferente") e a IA assume.
  `com_abertura` põe a abertura do app da sessão antes da 1ª entrada, só para a destilação: a gravação não muda.
- **31.122.** A IA propõe `text_visible` com um texto que já está na tela em que a etapa COMEÇA (os `screen_lines` da 1ª
  entrada dela). A etapa "passa" sem agir, e a seguinte começa na tela errada. `ja_valem` acha essas etapas pela mesma
  regra do verificador (`UiTree.contains_text`: contém, sem caixa nem espaço repetido) e sugere textos da tela
  seguinte. A prévia avisa; o `save` recusa até a pessoa trocar a pós-condição.

Só funções puras sobre a proposta e as entradas gravadas; nenhuma IA, nenhum aparelho.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence

from ..automation.hierarchy import UiElement, UiTree
from ..util import norm_text
from . import dado_da_persona

#: Quantos textos da tela seguinte a sugestão oferece.
SUGESTOES = 3
#: Abaixo disto o texto casaria com pedaço de qualquer linha (o mesmo piso do dado da persona).
MINIMO = 3
#: 31.122 F2: teto de elementos guardados por tela (uma lista rolada pode ter centenas) e de caracteres por texto.
ELEMENTOS = 400
CARACTERES = 200
#: As pós-condições que a regra do verificador decide pela tela de partida: texto visível e elemento presente.
CONFERIDAS = ("text_visible", "element_present")


def com_abertura(entradas: Sequence[Mapping[str, object]], primeira: Mapping[str, object] | None, app_id: str | None,
                 pacote: str | None) -> list[dict[str, object]]:
    """As entradas da etapa com `open_app` do app da sessão antes, quando a etapa contém a 1ª entrada da gravação, a
    gravação começou DENTRO desse app (o pacote da 1ª entrada é o do app) e não começou abrindo-o. Fora disso, iguais.
    A entrada sintética tem `seq` 0 e não vai ao banco."""
    copia = [dict(e) for e in entradas]
    if not (app_id and pacote and primeira) or primeira.get("type") == "open_app" or primeira.get("package") != pacote:
        return copia
    if not any(e.get("seq") == primeira.get("seq") for e in entradas):
        return copia
    return [{"seq": 0, "type": "open_app", "app_id": app_id, "package": pacote}, *copia]


def elementos_compactos(elementos: Iterable[UiElement], segredo: Callable[[str | None], bool]) -> list[dict[str, object]]:
    """31.122 F2: os elementos da tela para a MESMA regra do verificador (`UiTree.contains_text` e `find_selector`), em
    forma compacta: `{t, d, r, b}` (texto, descrição, id, limites), sem as chaves vazias. O campo de senha sai inteiro;
    o campo editável perde o texto (o que a pessoa digitou não fica aqui); o texto ou a descrição com cara de segredo
    (`segredo`) sai. Elemento sem nada que se confira não entra. Até `ELEMENTOS`."""
    saida: list[dict[str, object]] = []
    for e in elementos:
        if e.password:
            continue
        texto = "" if e.editable else (e.text or "")
        desc = e.desc or ""
        item: dict[str, object] = {}
        if texto and not segredo(texto):
            item["t"] = texto[:CARACTERES]
        if desc and not segredo(desc):
            item["d"] = desc[:CARACTERES]
        if e.resource_id:
            item["r"] = e.resource_id
        if not item:
            continue
        item["b"] = list(e.bounds)
        saida.append(item)
        if len(saida) >= ELEMENTOS:
            break
    return saida


def _elemento(n: int, texto: str = "", desc: str = "", rid: str = "", limites: object = None) -> UiElement:
    b = limites if isinstance(limites, list) and len(limites) == 4 and all(isinstance(x, int) for x in limites) else [0] * 4
    return UiElement(id=f"p{n}", text=texto, desc=desc, resource_id=rid, class_name="", package="",
                     bounds=(b[0], b[1], b[2], b[3]), clickable=False, enabled=True, focused=False, scrollable=False,
                     editable=False, checked=False, password=False)


def tela_de_partida(e: Mapping[str, object] | None) -> UiTree | None:
    """A tela em que a entrada foi feita, como árvore: dos `screen_elements` (31.122 F2) ou, na sessão anterior a eles,
    das `screen_lines` e do `screen_title` como textos soltos (o que se sabe; id e descrição não). Sem nada, `None`."""
    if not e:
        return None
    elementos = e.get("screen_elements")
    if isinstance(elementos, list) and elementos:
        return UiTree(elements=[_elemento(n, str(x.get("t") or ""), str(x.get("d") or ""), str(x.get("r") or ""),
                                          x.get("b")) for n, x in enumerate(elementos) if isinstance(x, dict)],
                      packages=[], sensitive=False)
    textos = [*_linhas(e), *([str(e["screen_title"])] if isinstance(e.get("screen_title"), str) else [])]
    return (UiTree(elements=[_elemento(n, t) for n, t in enumerate(textos)], packages=[], sensitive=False)
            if textos else None)


def _vale(tela: UiTree, kind: str, valor: str) -> bool:
    """A regra do verificador para a pós-condição na tela dada."""
    return tela.contains_text(valor) if kind == "text_visible" else bool(tela.find_selector(valor))


def _textos(tela: UiTree | None, e: Mapping[str, object] | None) -> list[str]:
    """Os textos candidatos a sugestão de uma tela: as linhas de conteúdo primeiro (as mais significativas), depois o
    resto dos textos e descrições."""
    return [*_linhas(e), *(tela.texts() if tela is not None else [])]


def _linhas(e: Mapping[str, object] | None) -> list[str]:
    linhas = e.get("screen_lines") if e else None
    return [str(x) for x in linhas if isinstance(x, str)] if isinstance(linhas, list) else []


def ja_valem(passos: Sequence[Mapping[str, object]], entradas: Sequence[Mapping[str, object]],
             descartadas: Iterable[int] = (), evitar: Iterable[str] = ()) -> list[dict[str, object]]:
    """As etapas cuja pós-condição `text_visible` ou `element_present` (literal, sem marcador) já vale na tela em que a
    etapa começa: `{key, titulo, kind, valor, sugestoes}`. A tela de partida é a da 1ª entrada da etapa
    (`tela_de_partida`: os elementos inteiros desde o 31.122 F2, pela regra do verificador); a sugestão, até
    `SUGESTOES` textos da tela seguinte (a 1ª entrada depois da última da etapa) que não valem na de partida. `evitar`:
    textos que nunca são sugeridos (o dado da persona). Sem tela gravada, a etapa não entra (não dá para saber)."""
    fora = set(descartadas)
    por_seq = {int(str(e["seq"])): e for e in entradas if str(e.get("seq", "")).lstrip("-").isdigit()}
    ordem = sorted(s for s in por_seq if s not in fora)
    # O valor inteiro e cada palavra dele (o primeiro nome de "Ana Souza" sozinho também não é sugerido): na dúvida, sai
    proibidos = [n for v in evitar if v for n in {norm_text(v), *(norm_text(w) for w in v.split())} if len(n) >= MINIMO]
    saida: list[dict[str, object]] = []
    for st in passos:
        post = st.get("postcondition")
        kind = post.get("kind") if isinstance(post, Mapping) else None
        valor = post.get("value") if isinstance(post, Mapping) and kind in CONFERIDAS else None
        if not isinstance(valor, str) or "{" in valor or len(norm_text(valor)) < MINIMO:
            continue
        seqs = sorted(int(i) for i in st.get("inputs") or [] if isinstance(i, int) and i in por_seq and i not in fora)  # type: ignore[union-attr]
        if not seqs:
            continue
        partida = tela_de_partida(por_seq[seqs[0]])
        if partida is None or not _vale(partida, str(kind), valor):
            continue
        depois = next((s for s in ordem if s > seqs[-1]), None)
        e_seguinte = por_seq[depois] if depois is not None else None
        sugestoes: list[str] = []
        tela_seguinte = tela_de_partida(e_seguinte)
        for linha in _textos(tela_seguinte, e_seguinte):
            n = norm_text(linha)
            if len(n) < MINIMO or "{" in linha or partida.contains_text(linha) or any(p in n for p in proibidos):
                continue
            if linha not in sugestoes:
                sugestoes.append(linha)
            if len(sugestoes) == SUGESTOES:
                break
        saida.append({"key": st.get("key"), "titulo": st.get("title") or st.get("key"), "kind": kind, "valor": valor,
                      "sugestoes": sugestoes,
                      "sugestoes_prontas": [pronta(str(kind), s, tela_seguinte) for s in sugestoes]})
    return saida


def pronta(kind: str, texto: str, tela: UiTree | None) -> dict[str, str]:
    """31.142: a pós-condição inteira que a sugestão vira, para o botão da revisão aplicar sem decidir nada.

    Achado da prova F2 (06/10): a única sugestão era "Back", a DESCRIÇÃO do botão voltar (texto vazio). Trocar só o
    valor de um `element_present` deixa o seletor puro "Back", que só olha o texto (`UiTree.find_selector`): a etapa
    nunca passaria. `text_visible` continua `text_visible` (`contains_text` lê texto e descrição). `element_present`
    vira `text==X` ou `desc==X`, pelo campo em que X está na tela seguinte. Sem achar o elemento (a linha veio só das
    `screen_lines`) ou com `|` no texto (o separador do seletor), `text_visible`, que casa do mesmo jeito."""
    if kind == "element_present" and tela is not None and "|" not in texto:
        n = norm_text(texto)
        for campo in ("text", "desc"):
            if any(norm_text(getattr(e, campo)) == n for e in tela.elements if getattr(e, campo)):
                return {"kind": "element_present", "value": f"{campo}=={texto}", "texto": texto}
    return {"kind": "text_visible", "value": texto, "texto": texto}


#: 31.123: telas que nunca comprovam a conclusão de uma etapa, mesmo vistas na demonstração (a barra do sistema e o
#: lançador, onde a pessoa passa sem que a etapa termine ali).
FORA_DOS_ACEITOS = ("com.android.systemui",)


def pacotes_vizinhos(entradas: Sequence[Mapping[str, object]], pacote_da_etapa: str | None,
                     cadastrados: Iterable[str]) -> list[str]:
    """31.123: os pacotes em que a demonstração da etapa aconteceu além do app dela, na ordem em que apareceram. Ficam
    de fora o próprio app, o systemui, o lançador (pacote com "launcher") e os apps cadastrados (esses já são o app de
    uma etapa, `step.app_id`, e não um vizinho). Sem o pacote da etapa, nada (não há com o que comparar)."""
    if not pacote_da_etapa:
        return []
    fora = {pacote_da_etapa, *FORA_DOS_ACEITOS, *cadastrados}
    saida: list[str] = []
    for e in entradas:
        pkg = e.get("package")
        if isinstance(pkg, str) and pkg and pkg not in fora and "launcher" not in pkg and pkg not in saida:
            saida.append(pkg)
    return saida


def entrada_seguinte(entradas: Sequence[Mapping[str, object]], seqs_da_etapa: Iterable[int],
                     descartadas: Iterable[int] = ()) -> Mapping[str, object] | None:
    """31.123 F2: a 1ª entrada gravada depois da última da etapa (fora as descartadas). O pacote dela é o da tela em que
    a etapa TERMINOU: a entrada guarda o pacote da tela em que foi feita. Sem entrada depois, `None`."""
    seqs = list(seqs_da_etapa)
    if not seqs:
        return None
    fora, ultima = set(descartadas), max(seqs)
    depois = sorted((e for e in entradas if str(e.get("seq", "")).lstrip("-").isdigit()
                     and int(str(e["seq"])) > ultima and int(str(e["seq"])) not in fora), key=lambda e: int(str(e["seq"])))
    return depois[0] if depois else None


def aviso_dos_vizinhos(passos: Sequence[tuple[str, Sequence[str]]]) -> list[str]:
    """A linha da prévia e do `save` para cada etapa que passou a aceitar um pacote vizinho."""
    return [f"Etapa “{titulo}”: a demonstração terminou fora do app, em {', '.join(pacotes)}; a etapa passa a aceitar "
            "a conclusão nessa tela." for titulo, pacotes in passos if pacotes]


def estruturados(achados: Sequence[Mapping[str, object]], persona: Mapping[str, str] | None = None
                 ) -> list[dict[str, object]]:
    """31.122, adendo v1.86 (`pos_condicoes_ja_valem`): cada achado como objeto, para a tela pôr o alerta dentro da
    etapa com um botão por sugestão (31.128): `{etapa, valor, sugestoes, message}`. `etapa` é a key; `valor`, a
    pós-condição que já vale; `sugestoes`, até `SUGESTOES`; `message`, a mesma linha de `aviso`. Com `persona`, o dado
    dela vira o marcador no valor e nas sugestões. 31.142 (adendo v1.91): `sugestoes_prontas`, na mesma ordem de
    `sugestoes`, cada uma como `{kind, value, texto}` (`pronta`): o botão aplica `kind` e `value` juntos."""
    def marca(texto: str) -> str:
        return dado_da_persona.com_marcador(texto, persona) if persona else texto
    return [{"etapa": a.get("key"), "valor": marca(str(a.get("valor") or "")),
             "sugestoes": [marca(str(s)) for s in a.get("sugestoes") or []],  # type: ignore[attr-defined]
             "sugestoes_prontas": [{"kind": str(x["kind"]), "value": marca(str(x["value"])), "texto": marca(str(x["texto"]))}
                                   for x in a.get("sugestoes_prontas") or []],  # type: ignore[attr-defined]
             "message": linha} for a, linha in zip(achados, aviso(achados, persona), strict=True)]


def aviso(achados: Sequence[Mapping[str, object]], persona: Mapping[str, str] | None = None) -> list[str]:
    """Uma linha por etapa, para a prévia e para a recusa do `save`. Com `persona`, o dado dela que sobrar no título ou
    no valor vira o marcador (31.87 F2): a mensagem nunca leva o dado em claro."""
    linhas = []
    for a in achados:
        sug = a.get("sugestoes") or []
        exemplo = f" Por exemplo, um texto da tela seguinte: {', '.join(f'“{s}”' for s in sug)}." if sug else ""  # type: ignore[union-attr]
        o_que = (f"o elemento “{a['valor']}” já está" if a.get("kind") == "element_present"
                 else f"o texto “{a['valor']}” já aparece")
        linhas.append(f"Etapa “{a['titulo']}”: {o_que} na tela em que ela começa, então ela "
                      f"passaria sem agir. Troque a pós-condição por um texto que só aparece depois da etapa.{exemplo}")
    return [dado_da_persona.com_marcador(linha, persona) for linha in linhas] if persona else linhas


__all__ = ["CARACTERES", "CONFERIDAS", "ELEMENTOS", "FORA_DOS_ACEITOS", "MINIMO", "SUGESTOES", "aviso", "aviso_dos_vizinhos", "com_abertura", "elementos_compactos", "entrada_seguinte", "estruturados", "ja_valem",
           "pacotes_vizinhos", "pronta", "tela_de_partida"]
