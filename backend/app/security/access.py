"""Quem pode falar com esta API, e por qual nome.

Existe como módulo próprio por um motivo concreto, não por gosto de organizar: a decisão precisa ser tomada em
**dois** lugares que o framework trata de formas diferentes. O middleware `@app.middleware("http")` do Starlette é
um `BaseHTTPMiddleware`, e ele **devolve o controle sem olhar** quando `scope["type"] != "http"` — ou seja,
WebSocket não passa por middleware nenhum. Enquanto o backend só atendia `127.0.0.1` isso era inofensivo. No
instante em que passou a poder atender num endereço de rede, deixaria `/api/ws` — o fluxo de eventos do painel,
com repetição de histórico — aberto a quem estivesse na rede, sem credencial e sem conferência de `Host`.

Uma função, duas chamadas. Duplicar a regra seria garantir que um dia elas divergissem, e a que divergisse por
esquecimento seria justamente a que ninguém olha.
"""
from __future__ import annotations

import hmac
from typing import Any, Literal

#: Nomes que significam "esta máquina". `test`/`testserver` são os hosts que os clientes ASGI de teste usam.
LOOPBACK = frozenset({"127.0.0.1", "localhost", "[::1]", "test", "testserver"})

Recusa = Literal["unauthorized", "forbidden_host"]


def host_de(cabecalho: str | None) -> str:
    """Só o nome, sem a porta e em minúsculas — `Host` chega como `parque.local:8000`."""
    return (cabecalho or "").rsplit(":", 1)[0].lower()


def token_ok(authorization: str | None, esperado: str | None) -> bool:
    """`compare_digest` em vez de `==`: a comparação ingênua sai no primeiro byte diferente, e o tempo dela conta
    quantos bytes o atacante acertou.

    Compara em **bytes**: `compare_digest` levanta `TypeError` para `str` com caractere fora de ASCII, e um
    cabeçalho com acento viraria erro 500 dentro do portão de autenticação em vez de uma recusa limpa.

    Sem token configurado, nada serve. Recusar é o certo — o contrário seria um backend que se abre sozinho quando
    a configuração falta.
    """
    if not esperado or not authorization:
        return False
    tipo, _, valor = authorization.partition(" ")
    if tipo.lower() != "bearer":
        return False
    return hmac.compare_digest(valor.strip().encode("utf-8", "surrogatepass"),
                               esperado.encode("utf-8", "surrogatepass"))


def avaliar(*, host: str | None, authorization: str | None, publicos: frozenset[str] | set[str],
            token: str | None) -> Recusa | None:
    """`None` quando pode passar; o código da recusa quando não.

    A ordem importa e é a mais barata primeiro:

    1. **Loopback passa sem token**, de propósito. Quem já está na máquina tem o banco e o adb na mão: exigir
       segredo ali não protegeria nada e quebraria o frontend servido localmente.
    2. **Host declarado exige credencial.**
    3. **Qualquer outro host é recusado** — e a credencial certa NÃO compra a exceção. Esta é a defesa contra DNS
       rebinding: um nome que o atacante controla e faz resolver para `127.0.0.1`, para que o navegador de quem
       confia nele passe a falar com este backend. O que salva ali não é saber quem chamou, é saber por qual nome.
    """
    nome = host_de(host)
    if nome in LOOPBACK:
        return None
    if nome in publicos:
        return None if token_ok(authorization, token) else "unauthorized"
    return "forbidden_host"


def publicos_de(cfg: Any) -> frozenset[str]:
    """Os nomes declarados em `server.public_hosts`, normalizados. Lido da configuração a cada chamada de propósito:
    a lista é barata e assim um ajuste vale sem reiniciar o processo."""
    return frozenset(h.strip().lower() for h in cfg.file.server.public_hosts if h.strip())
