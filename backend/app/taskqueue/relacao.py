"""Item 31.41: o valor lido tem RELAÇÃO com o que foi pedido? Determinístico, só pela árvore, sem IA.

Achado real (r-20261004120701-03d58e, QA Messenger): pedido o "número do protocolo", o ator leu um id de execução
antigo de uma mensagem ("Prova 203 r-…") e a etapa fechou com sucesso. Nada na tela ligava aquele texto a "protocolo".

Evidência que vale, nesta ordem (a primeira que casar responde):
  (a) `seletor`: o elemento casa o seletor que o catálogo declara para a saída (`Capability.saidas_relacao`, `nome=sel`);
  (b) `rotulo`: um termo do nome da saída (ou um sinônimo do catálogo, `nome~termo`, ou a tradução do glossário) aparece
      no resource-id ou na descrição do próprio elemento, no texto dele como rótulo, ou num vizinho da mesma linha ou
      logo acima;
  (c) `forma`: a forma do valor confere com um tipo FECHADO inferido do nome — e-mail, data, telefone ou URL. Número
      sozinho não é forma (qualquer número da tela passaria; foi quase o 03d58e).
Nada disso: `None` = dúvida, e dúvida nunca fecha como sucesso (quem chama recusa a leitura).
"""
from __future__ import annotations

import re
import unicodedata

from ..automation.hierarchy import UiElement, UiTree

#: Palavras do nome que não identificam o dado (ordinal, artigo): "primeira_conversa" vale por "conversa".
_GENERICAS = frozenset({"primeira", "primeiro", "ultima", "ultimo", "segunda", "segundo", "valor", "texto", "item",
                        "atual", "mais", "recente", "novo", "nova", "lido", "lida"})
#: O app costuma nomear os elementos em inglês; o plano nomeia a saída em português.
_GLOSSARIO: dict[str, tuple[str, ...]] = {
    "mensagem": ("message", "msg"), "conversa": ("conversation", "chat", "thread"), "contato": ("contact", "conversation", "chat"),   # no app de mensagem, a conversa é o contato
    "assunto": ("subject",), "remetente": ("sender", "from"), "nome": ("name",), "data": ("date",),
    "hora": ("time",), "horario": ("time",), "telefone": ("phone",), "celular": ("phone", "mobile"),
    "endereco": ("address",), "email": ("mail",), "preco": ("price",), "titulo": ("title",),
    "protocolo": ("protocol",), "numero": ("number",), "codigo": ("code",), "usuario": ("user", "username"),
    "legenda": ("caption",), "comentario": ("comment",), "publicacao": ("post",), "perfil": ("profile",),
    "seguidores": ("followers",), "status": ("status",), "pedido": ("order",), "total": ("total",),
}
_FORMAS: dict[str, re.Pattern[str]] = {
    "email": re.compile(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}"),
    "data": re.compile(r"\d{1,4}[/.-]\d{1,2}[/.-]\d{1,4}|\d{1,2} de [a-zç]+( de \d{4})?|[a-z]{3,9}\.? \d{1,2},? \d{4}",
                       re.IGNORECASE),
    "telefone": re.compile(r"\+?[\d\s().-]{8,20}"),
    "url": re.compile(r"(https?://)?[\w-]+(\.[\w-]+)+(/\S*)?"),
}
_TIPO_DO_NOME: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("email", ("email", "mail")), ("data", ("data", "nascimento", "date", "vencimento", "aniversario")),
    ("telefone", ("telefone", "celular", "phone", "whatsapp", "fone")), ("url", ("url", "link", "site", "endereco_web")),
)
_FAIXA_ACIMA_PX = 80          # o rótulo logo acima do valor (formulário em duas linhas)


def _normal(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return sem_acento.casefold()


def termos_do_nome(nome: str, sinonimos: tuple[str, ...] = ()) -> set[str]:
    """Os termos que identificam a saída: as palavras do nome (sem as genéricas), as traduções do glossário e os
    sinônimos declarados no catálogo."""
    termos: set[str] = set()
    for parte in re.split(r"[_\-\s]+", _normal(nome)):
        if len(parte) < 3 or parte in _GENERICAS:
            continue
        termos.add(parte)
        termos.update(_GLOSSARIO.get(parte, ()))
    termos.update(_normal(s) for s in sinonimos if len(s) >= 3)
    return termos


def tipo_pelo_nome(nome: str) -> str | None:
    n = _normal(nome)
    return next((tipo for tipo, chaves in _TIPO_DO_NOME if any(c in n for c in chaves)), None)


def relacoes_do_catalogo(entradas: tuple[str, ...], nome: str) -> tuple[str | None, tuple[str, ...]]:
    """`Capability.saidas_relacao` → (seletor, sinônimos) desta saída. `nome=seletor` declara o seletor; `nome~termo`,
    um sinônimo do rótulo."""
    seletor: str | None = None
    sinonimos: list[str] = []
    for e in entradas:
        if "=" in e and e.split("=", 1)[0].strip() == nome and ("~" not in e or e.index("=") < e.index("~")):
            seletor = e.split("=", 1)[1].strip()
        elif "~" in e and e.split("~", 1)[0].strip() == nome:
            sinonimos.append(e.split("~", 1)[1].strip())
    return seletor, tuple(sinonimos)


def _vizinhos(arvore: UiTree, alvo: UiElement) -> list[UiElement]:
    x1, y1, x2, y2 = alvo.bounds
    perto: list[UiElement] = []
    for e in arvore.elements:
        if e.id == alvo.id or not (e.text or e.desc):
            continue
        ex1, ey1, ex2, ey2 = e.bounds
        mesma_linha = ey1 < y2 and ey2 > y1 and (ey2 - ey1) < 3 * max(1, y2 - y1)
        logo_acima = 0 <= y1 - ey2 <= _FAIXA_ACIMA_PX and ex1 < x2 and ex2 > x1
        if mesma_linha or logo_acima:
            perto.append(e)
    return perto


def relacao_do_valor(arvore: UiTree, alvo: UiElement, nome: str, valor: str, *,
                     relacoes: tuple[str, ...] = ()) -> str | None:
    """`"seletor"`, `"rotulo"` ou `"forma"` quando há evidência de relação entre o elemento lido e a saída pedida;
    `None` = dúvida (a leitura é recusada)."""
    seletor, sinonimos = relacoes_do_catalogo(relacoes, nome)
    if seletor and any(e.id == alvo.id for e in arvore.find_selector(seletor)):
        return "seletor"
    termos = termos_do_nome(nome, sinonimos)
    if termos:
        proprio = _normal(f"{alvo.resource_id.rsplit('/', 1)[-1]} {alvo.desc}")
        texto = _normal(alvo.text)
        resto_do_texto = texto.replace(_normal(valor), " ") if valor else texto
        if any(t in proprio or t in resto_do_texto for t in termos):
            return "rotulo"
        if any(t in _normal(f"{v.text} {v.desc}") for v in _vizinhos(arvore, alvo) for t in termos):
            return "rotulo"
    tipo = tipo_pelo_nome(nome)
    if tipo is not None and _FORMAS[tipo].fullmatch(valor.strip() or "-"):
        return "forma"
    return None
