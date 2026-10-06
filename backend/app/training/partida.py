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

from collections.abc import Iterable, Mapping, Sequence

from ..util import norm_text
from . import dado_da_persona

#: Quantos textos da tela seguinte a sugestão oferece.
SUGESTOES = 3
#: Abaixo disto o texto casaria com pedaço de qualquer linha (o mesmo piso do dado da persona).
MINIMO = 3


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


def _linhas(e: Mapping[str, object] | None) -> list[str]:
    linhas = e.get("screen_lines") if e else None
    return [str(x) for x in linhas if isinstance(x, str)] if isinstance(linhas, list) else []


def _contem(linhas: Iterable[str], texto: str) -> bool:
    n = norm_text(texto)
    return bool(n) and any(n in norm_text(t) for t in linhas)


def ja_valem(passos: Sequence[Mapping[str, object]], entradas: Sequence[Mapping[str, object]],
             descartadas: Iterable[int] = (), evitar: Iterable[str] = ()) -> list[dict[str, object]]:
    """As etapas cuja pós-condição `text_visible` (literal, sem marcador) já vale na tela em que a etapa começa: `{key,
    titulo, valor, sugestoes}`. A tela de partida são os `screen_lines` da 1ª entrada da etapa; a sugestão, até
    `SUGESTOES` linhas da tela seguinte (a 1ª entrada depois da última da etapa) que não estão na de partida. `evitar`:
    textos que nunca são sugeridos (o dado da persona). Sem tela gravada, a etapa não entra (não dá para saber)."""
    fora = set(descartadas)
    por_seq = {int(str(e["seq"])): e for e in entradas if str(e.get("seq", "")).lstrip("-").isdigit()}
    ordem = sorted(s for s in por_seq if s not in fora)
    proibidos = [norm_text(v) for v in evitar if v and len(v.strip()) >= MINIMO]
    saida: list[dict[str, object]] = []
    for st in passos:
        post = st.get("postcondition")
        valor = post.get("value") if isinstance(post, Mapping) and post.get("kind") == "text_visible" else None
        if not isinstance(valor, str) or "{" in valor or len(norm_text(valor)) < MINIMO:
            continue
        seqs = sorted(int(i) for i in st.get("inputs") or [] if isinstance(i, int) and i in por_seq and i not in fora)  # type: ignore[union-attr]
        if not seqs:
            continue
        partida = _linhas(por_seq[seqs[0]])
        if not partida or not _contem(partida, valor):
            continue
        depois = next((s for s in ordem if s > seqs[-1]), None)
        seguinte = _linhas(por_seq[depois]) if depois is not None else []
        sugestoes: list[str] = []
        for linha in seguinte:
            n = norm_text(linha)
            if len(n) < MINIMO or "{" in linha or _contem(partida, linha) or any(p in n for p in proibidos):
                continue
            if linha not in sugestoes:
                sugestoes.append(linha)
            if len(sugestoes) == SUGESTOES:
                break
        saida.append({"key": st.get("key"), "titulo": st.get("title") or st.get("key"), "valor": valor,
                      "sugestoes": sugestoes})
    return saida


def aviso(achados: Sequence[Mapping[str, object]], persona: Mapping[str, str] | None = None) -> list[str]:
    """Uma linha por etapa, para a prévia e para a recusa do `save`. Com `persona`, o dado dela que sobrar no título ou
    no valor vira o marcador (31.87 F2): a mensagem nunca leva o dado em claro."""
    linhas = []
    for a in achados:
        sug = a.get("sugestoes") or []
        exemplo = f" Por exemplo, um texto da tela seguinte: {', '.join(f'“{s}”' for s in sug)}." if sug else ""  # type: ignore[union-attr]
        linhas.append(f"Etapa “{a['titulo']}”: o texto “{a['valor']}” já aparece na tela em que ela começa, então ela "
                      f"passaria sem agir. Troque a pós-condição por um texto que só aparece depois da etapa.{exemplo}")
    return [dado_da_persona.com_marcador(linha, persona) for linha in linhas] if persona else linhas


__all__ = ["MINIMO", "SUGESTOES", "aviso", "com_abertura", "ja_valem"]
