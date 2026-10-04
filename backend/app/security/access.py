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
import ipaddress
from typing import Any, Literal

#: Nomes de `Host` que significam "esta máquina". Note o que **não** está aqui: `test`/`testserver`, os nomes que os
#: clientes ASGI de teste usam. Eles moravam neste conjunto e valiam em produção — qualquer cliente da rede passava
#: pelo portão mandando `Host: testserver`. Agora a suíte os injeta em `LOOPBACK_DE_TESTE`, e o binário que roda no
#: parque não conhece nome nenhum de teste.
LOOPBACK = frozenset({"127.0.0.1", "localhost", "[::1]", "::1"})

#: Endereços do PAR (o outro lado do TCP, `request.client.host`) que significam "esta máquina". Conjunto separado
#: do de cima de propósito: `Host` chega como texto digitável (`localhost`, `[::1]:8000`) e o par chega como
#: endereço já resolvido pelo sistema operacional (`127.0.0.1`, `::1`, sem colchetes e sem porta).
PARES_LOOPBACK = frozenset({"127.0.0.1", "::1"})

#: Nomes extras tratados como loopback. **Vazio em produção** e é assim que tem de continuar: existe só para a
#: suíte declarar os hosts sintéticos dos clientes ASGI (`test`, `testserver`, `testclient`) sem que eles entrem no
#: conjunto que o parque usa. Lido a cada chamada para um `monkeypatch` valer.
LOOPBACK_DE_TESTE: frozenset[str] = frozenset()

Recusa = Literal["unauthorized", "forbidden_host"]


def host_de(cabecalho: str | None) -> str:
    """Só o nome, sem a porta e em minúsculas — `Host` chega como `parque.local:8000`."""
    return (cabecalho or "").rsplit(":", 1)[0].lower()


def par_e_local(par: str | None) -> bool:
    """O par é esta máquina? `par` é `request.client.host` / `websocket.client.host` — o endereço de quem abriu o
    TCP, que o cliente não escolhe.

    Ausente (`None`) conta como **não** local: transporte que não sabe dizer quem ligou não compra isenção.
    """
    return (par or "").strip().lower().strip("[]") in PARES_LOOPBACK


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


def avaliar(*, par: str | None, host: str | None, authorization: str | None,
            publicos: frozenset[str] | set[str], token: str | None,
            sessao_valida: bool = False) -> Recusa | None:
    """`None` quando pode passar; o código da recusa quando não.

    `sessao_valida` é a segunda credencial aceita: um cookie de sessão do painel que `security.sessions` já
    reconheceu. Ela existe porque o `Authorization` não alcança dois clientes que o painel PRECISA usar — o
    `WebSocket` do navegador, que não deixa definir cabeçalho, e a `<img>` do frame/evidência/avatar, que também
    não. Vale exatamente onde o Bearer valeria, e em lugar nenhum além disso: a isenção de loopback continua
    sendo a do par local, e `forbidden_host` continua não tendo perdão — cookie de sessão é justamente o que um
    ataque de DNS rebinding faria o navegador enviar sozinho.

    `par` é o endereço de quem abriu a conexão (`request.client.host` no HTTP, `websocket.client.host` no
    WebSocket). Ele existe aqui por um defeito concreto: a isenção de loopback era decidida **só pelo cabeçalho
    `Host`**, que quem chama escreve. Com `server.host: 0.0.0.0`, qualquer cliente da rede mandava
    `Host: localhost` e atravessava o portão sem credencial — a autenticação inteira cabia num cabeçalho. Agora a
    isenção pede as duas coisas: o par tem de ser desta máquina **e** o nome tem de ser de loopback.

    A ordem importa e é a mais barata primeiro:

    1. **Loopback de verdade passa sem token**, de propósito: quem já está na máquina tem o banco e o adb na mão,
       exigir segredo ali não protegeria nada e quebraria o frontend servido localmente. "De verdade" = par local.
    2. **Nome de loopback vindo de fora é exatamente a forja** — não ganha isenção; vira "host declarado", isto é,
       passa só com a credencial certa. Sem ela, `unauthorized` (401), não `forbidden_host`: o nome não é hostil,
       o que falta é o segredo.
    3. **Host declarado exige credencial.**
    4. **Qualquer outro host é recusado** — e a credencial certa NÃO compra a exceção. Esta é a defesa contra DNS
       rebinding: um nome que o atacante controla e faz resolver para `127.0.0.1`, para que o navegador de quem
       confia nele passe a falar com este backend. O que salva ali não é saber quem chamou, é saber por qual nome.

    O que esta função **não** resolve, e por isso está escrito aqui: túnel SSH reverso. A conexão que chega pelo
    `-R` tem par `127.0.0.1` de verdade, então olhar o par não distingue a máquina do worker da máquina local. O
    que separa os dois é o listener dedicado de `main.create_worker_app`, que só serve `/api/worker/ws`.
    """
    nome = host_de(host)
    credenciado = sessao_valida or token_ok(authorization, token)
    if nome in LOOPBACK or nome in LOOPBACK_DE_TESTE:
        if par_e_local(par):
            return None
        return None if credenciado else "unauthorized"
    if nome in publicos:
        return None if credenciado else "unauthorized"
    return "forbidden_host"


#: Cabeçalho com o IP de quem chamou, posto pela borda da Cloudflare (ADR-073). A borda SOBRESCREVE o valor que o
#: cliente mandar, e o `cloudflared` o repassa ao backend pelo loopback; é por isso que vale só com par loopback.
CABECALHO_DO_IP_NA_BORDA = "cf-connecting-ip"

#: O cliente que nunca se tranca: par e nome desta máquina. Quem já está aqui tem o banco e o adb na mão.
CLIENTE_LOCAL = "local"


def cliente_de(*, par: str | None, host: str | None, ip_na_borda: str | None,
               publicos: frozenset[str] | set[str], atras_de_proxy: bool) -> str:
    """Quem é o cliente para a trava de tentativas (29.56): uma chave por origem, para que os chutes de um não
    tranquem os outros.

    Pelo túnel, todo par é `127.0.0.1`: sem isto, oito chutes de qualquer pessoa na internet trancavam o login de
    fora do dono. O IP da borda só é aceito quando as três coisas valem juntas: par loopback (é o `cloudflared`
    desta máquina que entrega), `tls_behind_proxy` declarado (há mesmo um proxy na frente) e nome em
    `public_hosts` (o pedido veio pelo endereço público). Fora disso o cabeçalho é texto de quem chamou e não
    compra nada: um par da rede vale pelo próprio endereço, que o cliente não escolhe.

    `local` (par e nome de loopback) fica de fora da trava por quem chama. Pelo túnel sem o cabeçalho, todos caem
    numa chave só (`tunel`), a mesma situação de antes, mas só para quem não se identifica.
    """
    nome = host_de(host)
    if par_e_local(par) and (nome in LOOPBACK or nome in LOOPBACK_DE_TESTE):
        return CLIENTE_LOCAL
    if par_e_local(par) and atras_de_proxy and nome in publicos:
        try:
            return f"ip:{ipaddress.ip_address((ip_na_borda or '').strip())}"
        except ValueError:
            return "tunel"
    return f"ip:{(par or '').strip().lower() or 'desconhecido'}"


def publicos_de(cfg: Any) -> frozenset[str]:
    """Os nomes declarados em `server.public_hosts`, normalizados. Lido da configuração a cada chamada de propósito:
    a lista é barata e assim um ajuste vale sem reiniciar o processo."""
    return frozenset(h.strip().lower() for h in cfg.file.server.public_hosts if h.strip())
