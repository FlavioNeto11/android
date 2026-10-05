"""O site institucional na raiz do nome público (29.77, ADR-075), servido da memória.

Não é um `StaticFiles`: a pasta `site/` é lida UMA vez, na montagem, e a superfície pública é exatamente o que estava
lá, numa lista fechada de extensões. Arquivo com extensão fora da lista, ou oculto (`.algo`), derruba a subida: um
rascunho, uma captura com telefone ou um PDF esquecido na pasta nunca chegam à internet por descuido. Fora de `/api/` o
`guarda` trata tudo como público, então o cuidado mora aqui.

O site fica na MESMA origem do painel. Um script injetado nele faria `fetch('/api/...')` com o cookie de quem está
logado, e a checagem de `Origin` do `guarda` deixaria passar. Por isso a CSP vai inteira, sem `unsafe-inline`
(`form-action`, `frame-ancestors` e `base-uri` não herdam de `default-src`), e o site não tem nenhum script nem estilo
em linha (há teste).

O `index.html` é montado por requisição: os contatos do config entram ESCAPADOS no marcador `<!--portal:contatos-->`
(sem contatos, o bloco some) e o token de tempo mínimo do formulário entra no marcador `<!--portal:token-->`. Por isso
ele sai com `no-store`; os outros arquivos saem com ETag e `no-cache` (os nomes não têm hash, então o navegador
revalida e recebe 304).
"""
from __future__ import annotations

import gzip
import hashlib
import html
import re
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable, Iterable, Mapping
from typing import Protocol

from starlette.types import Receive, Scope, Send

#: As extensões que podem existir na pasta do site. Qualquer outra derruba a subida (veja o docstring do módulo).
EXTENSOES_DO_SITE: Mapping[str, str] = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".txt": "text/plain; charset=utf-8",
    ".woff2": "font/woff2",
}

#: Cabeçalhos de toda resposta do site, além dos `CABECALHOS_DE_SEGURANCA` de sempre (que o `guarda` põe com
#: `setdefault`). A revisão do brief (A1 e A5) pediu cada uma destas diretivas; o HSTS é dado pela Cloudflare.
CABECALHOS_DO_SITE: Mapping[str, str] = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
        "form-action 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'"),
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}

MARCADOR_DOS_CONTATOS = "<!--portal:contatos-->"
MARCADOR_DO_TOKEN = "<!--portal:token-->"
#: O formulário fica entre estes dois marcadores. Com o contato desligado o servidor troca o trecho inteiro pelo aviso
#: abaixo: um formulário cuja rota responde 404 diria "tente de novo" para sempre (revisão do #333, A1).
INICIO_DO_FORMULARIO = "<!--portal:formulario-->"
FIM_DO_FORMULARIO = "<!--portal:fim-do-formulario-->"
SEM_FORMULARIO = ('<div class="formulario formulario-fora" id="formulario-contato"><p>O formulário de contato está '
                  'fora do ar no momento. Ligue ou chame no WhatsApp pelos telefones ao lado.</p></div>')
_MARCADOR = re.compile(r"<!--portal:[a-z-]+-->")


class SiteInvalido(ValueError):
    """A pasta do site tem o que não pode ir à internet. Derruba a subida de propósito."""


@dataclass(frozen=True, slots=True)
class Arquivo:
    corpo: bytes
    tipo: str
    etag: str


def ler_site(raiz: Path) -> dict[str, Arquivo]:
    """Todos os arquivos servíveis, por caminho de URL (`/assets/site.css`). Recusa a pasta inteira se houver um só
    arquivo fora da lista ou oculto: a recusa é na subida, não na hora do pedido."""
    if not (raiz / "index.html").is_file():
        raise SiteInvalido(f"{raiz}: sem index.html")
    arquivos: dict[str, Arquivo] = {}
    for caminho in sorted(raiz.rglob("*")):
        if caminho.is_dir():
            continue
        relativo = caminho.relative_to(raiz)
        if any(parte.startswith(".") for parte in relativo.parts):
            raise SiteInvalido(f"site/{relativo.as_posix()}: arquivo oculto não vai à internet")
        tipo = EXTENSOES_DO_SITE.get(caminho.suffix.lower())
        if tipo is None:
            raise SiteInvalido(f"site/{relativo.as_posix()}: extensão fora da lista do site")
        corpo = caminho.read_bytes()
        arquivos["/" + relativo.as_posix()] = Arquivo(corpo, tipo, '"' + hashlib.sha256(corpo).hexdigest()[:20] + '"')
    return arquivos


class ContatoPublico(Protocol):
    """`app.config.ContatoPublicoCfg`, visto daqui só pelos dois campos."""
    nome: str
    telefone: str


def bloco_de_contatos(contatos: Iterable[ContatoPublico]) -> str:
    """O HTML dos contatos do config, com cada valor escapado. Lista vazia vira texto vazio: o bloco some."""
    itens = []
    for contato in contatos:
        digitos = re.sub(r"\D", "", contato.telefone)
        nome = html.escape(contato.nome, quote=True)
        telefone = html.escape(contato.telefone, quote=True)
        itens.append(
            f'<li class="contato"><span class="contato-nome">{nome}</span><span class="contato-acoes">'
            f'<a href="tel:+{digitos}"><svg aria-hidden="true"><use href="#i-telefone"/></svg>{telefone}</a>'
            f'<a href="https://wa.me/{digitos}" rel="noopener noreferrer"><svg aria-hidden="true">'
            f'<use href="#i-whatsapp"/></svg>WhatsApp<span class="visualmente-oculto"> de {nome}</span></a>'
            f'</span></li>')
    if not itens:
        return ""
    return ('<div class="contato-direto"><h3 class="contato-direto-titulo">Prefere falar agora?</h3>'
            f'<ul class="contatos">{"".join(itens)}</ul></div>')


class SitePublico:
    """O app ASGI montado na raiz. Só `GET` e `HEAD`; o resto é 405. `token` devolve o token do formulário para
    esta página (string vazia quando o contato não está pronto: o POST recusa)."""

    def __init__(self, raiz: Path, contatos: Iterable[ContatoPublico], token: Callable[[Scope], str], *,
                 contato_ligado: bool) -> None:
        self.arquivos = ler_site(raiz)
        modelo = self.arquivos["/index.html"].corpo.decode("utf-8").replace(
            MARCADOR_DOS_CONTATOS, bloco_de_contatos(contatos))
        if not contato_ligado and INICIO_DO_FORMULARIO in modelo and FIM_DO_FORMULARIO in modelo:
            ini, fim = modelo.index(INICIO_DO_FORMULARIO), modelo.index(FIM_DO_FORMULARIO) + len(FIM_DO_FORMULARIO)
            modelo = modelo[:ini] + SEM_FORMULARIO + modelo[fim:]
        self._modelo = modelo
        self._token = token

    def index(self, scope: Scope) -> bytes:
        pagina = self._modelo.replace(MARCADOR_DO_TOKEN, html.escape(self._token(scope), quote=True))
        return _MARCADOR.sub("", pagina).encode("utf-8")      # marcador desconhecido nunca vaza para a página

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return
        metodo = scope["method"]
        if metodo not in ("GET", "HEAD"):
            await _responder(send, 405, b"", "text/plain; charset=utf-8", {"Allow": "GET, HEAD"}, metodo)
            return
        caminho = scope["path"]
        if caminho in ("/", "/index.html"):
            await _responder_html(scope, send, 200, self.index(scope), "no-store", metodo)
            return
        arquivo = None if caminho == "/404.html" else self.arquivos.get(caminho)   # só pelo caminho 404 (N5 do #366)
        if arquivo is None:
            # A página 404 do site (29.80), com o status 404; sem ela na pasta, o texto curto de sempre.
            pagina = self.arquivos.get("/404.html")
            if pagina is not None:
                await _responder_html(scope, send, 404, pagina.corpo, "no-cache", metodo)
            else:
                await _responder(send, 404, "Não encontrado.".encode("utf-8"), "text/plain; charset=utf-8",
                                 {"Cache-Control": "no-cache"}, metodo)
            return
        cabecalhos = {"Cache-Control": "no-cache", "ETag": arquivo.etag}
        if _cabecalho(scope, b"if-none-match") == arquivo.etag:
            await _responder(send, 304, b"", arquivo.tipo, cabecalhos, metodo)
            return
        await _responder(send, 200, arquivo.corpo, arquivo.tipo, cabecalhos, metodo)


#: Abaixo disto o gzip não compensa o cabeçalho e o tempo (a 404 e a raiz passam bem acima).
GZIP_MIN_BYTES = 1024


def _aceita_gzip(scope: Scope) -> bool:
    """O cliente aceita gzip, e não com `q=0`. Sem o cabeçalho, nada é comprimido (o `curl` puro recebe o HTML cru)."""
    for parte in (_cabecalho(scope, b"accept-encoding") or "").lower().split(","):
        nome, _, parametros = parte.strip().partition(";")
        if nome.strip() in ("gzip", "*"):
            return parametros.replace(" ", "") not in ("q=0", "q=0.0", "q=0.00", "q=0.000")
    return False


async def _responder_html(scope: Scope, send: Send, status: int, corpo: bytes, cache: str, metodo: str) -> None:
    """As páginas HTML do site (29.91). `no-transform` impede a borda de REESCREVER o HTML: a Cloudflare injetou o
    beacon do Web Analytics (29.85) e pode injetar o Rocket Loader ou a ofuscação de e-mail, e o painel dela pode ser
    religado por engano. O mesmo `no-transform` tira da borda a compressão, então a página sai comprimida daqui: sem
    isso, cada visita pagaria ~40 KB em vez de ~10 KB (medido em 05/10). Só o HTML: o CSS e o JS seguem comprimidos
    pela borda, que não mexe no conteúdo deles. Não há segredo nem eco do visitante no HTML (o token anti-robô não abre
    nada), então comprimir não abre BREACH."""
    extras = {"Cache-Control": f"{cache}, no-transform", "Vary": "Accept-Encoding"}
    if len(corpo) >= GZIP_MIN_BYTES and _aceita_gzip(scope):
        corpo = gzip.compress(corpo, compresslevel=6, mtime=0)
        extras["Content-Encoding"] = "gzip"
    await _responder(send, status, corpo, EXTENSOES_DO_SITE[".html"], extras, metodo)


def _cabecalho(scope: Scope, nome: bytes) -> str | None:
    for chave, valor in scope.get("headers", ()):
        if chave == nome:
            return valor.decode("latin-1")
    return None


async def _responder(send: Send, status: int, corpo: bytes, tipo: str, extras: Mapping[str, str],
                     metodo: str) -> None:
    cabecalhos = {**CABECALHOS_DO_SITE, **extras, "Content-Type": tipo}
    if status != 304:
        cabecalhos["Content-Length"] = str(len(corpo))
    await send({"type": "http.response.start", "status": status,
                "headers": [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in cabecalhos.items()]})
    await send({"type": "http.response.body", "body": b"" if metodo == "HEAD" or status == 304 else corpo})
