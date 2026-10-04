"""31.52/31.54: endereços (URL) como saem da máquina, ao prompt da IA, ao histórico do ator, ao diagnóstico e ao
`observed_result` gravado.

O redator (`redaction.redact`) pega segredo no formato que conhece (`senha=…`), não dado pessoal nem `?code=`,
`token=`, e-mail em `%40`, UUID, JWT ou base64url num link de redefinição ou convite. Por isso o endereço tem
limpeza própria, que corta o caminho em vez de reconhecer token: uma lista de formatos sempre deixa um passar."""
from __future__ import annotations

import re
from urllib.parse import unquote

#: 31.52: o que torna o 1º pedaço do caminho opaco (link de redefinição, convite, sessão): UUID, JWT, ou 16+ caracteres
#: de token (letras, dígitos e `_-=.`) com pelo menos um dígito. Um slug sem dígito ("como-fazer-bolo") fica.
_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_JWT = re.compile(r"[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+")
_TRECHO_DE_TOKEN = re.compile(r"[A-Za-z0-9_\-=.]{16,}")
#: `usuario@` ou `usuario:senha@` antes do host. A senha pode ter `/`, `?`, `#` e `@`; o `@` que fecha o usuário é o
#: que deixa depois dele um host sem `@` até o primeiro `/`, `?`, `#` ou o fim. `:dígitos` seguido de `/`, `?`, `#` ou
#: do fim é a porta, não senha (revisão 15c: sem isso, `site:8080/perfil/pessoa@exemplo` virava o host `exemplo`).
_USUARIO_NA_URL = re.compile(r"^[^/@:?#\s]+(?::(?!\d+(?:[/?#]|$))\S*?)?@(?=[^/?#@\s]+(?:[/?#]|$))")
#: Teto do texto que a limpeza lê: as regex abaixo são lineares nesse tamanho, e o histórico não precisa de mais.
_TETO_DO_TEXTO = 2000


def _pedaco_opaco(pedaco: str) -> bool:
    if len(pedaco) > 200:              # longo assim é opaco, e as regex abaixo crescem com o quadrado do tamanho
        return True
    p = unquote(pedaco)
    return ("@" in p or bool(_UUID.search(p)) or bool(_JWT.search(p))
            or any(any(ch.isdigit() for ch in m.group(0)) for m in _TRECHO_DE_TOKEN.finditer(p)))


def endereco_para_o_prompt(texto: str) -> str:
    """31.52: um endereço como ele vai à IA (árvore, histórico do ator) e ao diagnóstico: o host e o 1º pedaço do
    caminho; o resto do caminho vira `/…`, a query `?…` e o fragmento `#…`.

    O redator pega segredo no formato que conhece (`senha=…`), não dado pessoal nem `?code=`, `token=`, e-mail em
    `%40`, UUID, JWT ou base64url no caminho de um link de redefinição ou convite (revisão da orquestradora, 04/10:
    uma lista de formatos sempre deixa um passar). O 1º pedaço também vira `…` se, decodificado, tiver `@` ou casar
    `_pedaco_opaco`. Usuário e senha antes do host somem. É o que o ator precisa para saber em que site e seção está."""
    t = (texto or "").strip()[:_TETO_DO_TEXTO]
    esquema = ""
    if "://" in t:
        esquema, t = t.split("://", 1)
        esquema += "://"
    t = _USUARIO_NA_URL.sub("", t, count=1)
    cauda = ""
    for marca in ("?", "#"):
        if marca in t:
            t, _ = t.split(marca, 1)
            cauda = cauda or f"{marca}…"
    host, barra, caminho = t.partition("/")
    if not barra:
        return esquema + host + cauda
    primeiro, _, resto = caminho.partition("/")
    primeiro = "…" if primeiro and _pedaco_opaco(primeiro) else primeiro
    return esquema + host + "/" + primeiro + ("/…" if resto else ("/" if caminho.endswith("/") else "")) + cauda


#: Um endereço no meio de um texto (erro do driver, resultado de ação): com esquema, `www.`, ou host com `/` ou `?`.
_URL_NO_TEXTO = re.compile(r"(?:https?://|\bwww\.)[^\s'\"<>]+|\b(?:[a-z0-9-]+\.)+[a-z]{2,}(?=[/?])[^\s'\"<>]*", re.I)


def enderecos_limpos(texto: str) -> str:
    """31.52: o texto com cada endereço passado por `endereco_para_o_prompt` (histórico do ator). Cortado em
    `_TETO_DO_TEXTO` antes da regex: um erro do driver com um blob de 100 mil caracteres travava o laço por minutos."""
    texto = texto or ""
    cortado = len(texto) > _TETO_DO_TEXTO           # o corte não é silencioso: quem lê vê o `…`
    limpo = _URL_NO_TEXTO.sub(lambda m: endereco_para_o_prompt(m.group(0)), texto[:_TETO_DO_TEXTO])
    return limpo + "…" if cortado else limpo


__all__ = ["endereco_para_o_prompt", "enderecos_limpos"]
