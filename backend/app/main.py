"""Aplicação FastAPI. Processo ÚNICO POR MÁQUINA (sem --reload e sem múltiplos workers do uvicorn).

Por que "por máquina" e não "no mundo": desde a posse de etapa, o dono de uma ETAPA é `OWNER_ID` — por omissão o
hostname — e um backend de outra máquina não reconcilia a etapa viva deste. Já `uvicorn --workers N` forkaria N
processos com o MESMO hostname, e portanto o mesmo dono: cada um reconheceria as etapas dos outros como suas e as
reconciliaria no meio da execução. Para dois backends na mesma máquina é preciso `OWNER_ID` explícito.

**O que isso ainda NÃO garante** (esta docstring já afirmou que dois backends "não se atropelam"; era exagero): só a
etapa tem dono. Despacho, rodízio, início de execução e a tabela `instances` continuam supondo um processo único — um
segundo backend enxerga todos os objetivos e BLOQUEIA os de aparelhos que ele não hospeda ("Instância não existe na
configuração atual"). Dois backends no mesmo banco só são seguros depois da fase 5 de docs/plano-100.md.

    python -m app.main            # a partir de backend/
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import socket
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import router, worker_router
from .config import Config, get_config
from .security.access import avaliar, publicos_de
from .security.redaction import RedactingFilter
from .state import VERSION, AppState


def setup_logging(cfg: Config) -> None:
    cfg.logs_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter('{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":%(message)r}')
    handler = logging.handlers.TimedRotatingFileHandler(cfg.logs_dir / "backend.log", when="midnight",
                                                        backupCount=cfg.file.limits.log_retention_days, encoding="utf-8")
    handler.setFormatter(fmt)
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S"))
    redacting = RedactingFilter()
    handler.addFilter(redacting)
    console.addFilter(redacting)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers = [handler, console]
    for noisy in ("httpx", "httpcore", "urllib3", "selenium", "anthropic", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def conferir_exposicao(cfg: Config) -> None:
    """Recusa subir numa porta de rede sem o que torna isso defensável. Função separada para ser testável: é uma
    decisão de segurança, e decisão de segurança que ninguém exercita é decisão de segurança que se perde num
    refactor.

    Sair do loopback é permitido de propósito — é o que deixa um worker de outra máquina falar direto com o
    central, sem túnel. O que não é permitido é fazê-lo sem segredo e sem lista de hosts.
    """
    if cfg.file.server.host in ("127.0.0.1", "localhost", "::1"):
        return
    if not cfg.api_token:
        raise SystemExit("server.host fora do loopback exige API_TOKEN no .env: subir a porta para a rede sem "
                         "autenticação exporia o parque inteiro a quem estiver nela.")
    if not cfg.file.server.public_hosts:
        raise SystemExit("server.host fora do loopback exige server.public_hosts no config.yaml: sem a lista, a "
                         "defesa contra DNS rebinding não tem como distinguir um nome legítimo de um hostil.")


def create_app(cfg: Config | None = None, state: AppState | None = None) -> FastAPI:
    cfg = cfg or get_config()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        poc = state or AppState(cfg)
        app.state.poc = poc
        await poc.start()
        try:
            yield
        finally:
            await poc.stop()

    app = FastAPI(title="Central de Aparelhos — POC", version=VERSION, lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=cfg.file.server.allowed_origins, allow_methods=["*"],
                       allow_headers=["*"], expose_headers=["X-Frame-Id", "X-Frame-Ts", "X-Frame-Width",
                                                            "X-Frame-Height", "X-Frame-Orientation"])
    allowed_origins = set(cfg.file.server.allowed_origins)

    @app.middleware("http")
    async def guarda(request: Request, call_next):  # type: ignore[no-untyped-def]
        """Host e credencial (a regra vive em `security.access`, porque o WebSocket precisa da MESMA), mais Origin
        contra CSRF em método que altera estado — navegador sempre manda `Origin` entre origens.

        Atenção ao mexer: este middleware **não vale para WebSocket**. O `BaseHTTPMiddleware` do Starlette devolve o
        controle sem olhar quando o scope não é `http`, então `/api/ws` confere o acesso por conta própria.
        """
        recusa = avaliar(par=request.client.host if request.client else None,
                         host=request.headers.get("host"), authorization=request.headers.get("authorization"),
                         publicos=publicos_de(cfg), token=cfg.api_token)
        if recusa == "unauthorized":
            # Nada do segredo recebido entra na resposta nem no log: só o fato de não servir.
            return JSONResponse({"detail": {"code": "unauthorized", "message": "Credencial ausente ou inválida."}},
                                status_code=401, headers={"WWW-Authenticate": "Bearer"})
        if recusa is not None:
            return JSONResponse({"detail": {"code": "forbidden_host", "message": "Host não permitido."}}, status_code=403)
        origin = request.headers.get("origin")
        if request.method not in ("GET", "HEAD", "OPTIONS") and origin and origin not in allowed_origins:
            return JSONResponse({"detail": {"code": "forbidden_origin", "message": "Origem não permitida."}}, status_code=403)
        return await call_next(request)

    app.include_router(router)
    app.include_router(worker_router)      # o canal do worker também atende na porta principal (modo (b))
    dist = cfg.root / "frontend" / "dist"
    if dist.exists():
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
    return app


def create_worker_app(state: AppState) -> FastAPI:
    """O listener DEDICADO ao túnel: serve `/api/worker/ws` e mais nada.

    Existe por um defeito de topologia, não por gosto de separar. O túnel SSH reverso (`-R 18000:127.0.0.1:8000`)
    faz toda conexão vinda da máquina do worker chegar aqui com par `127.0.0.1` **de verdade** — e loopback isenta
    de credencial. Resultado medido: do notebook do worker, `GET http://127.0.0.1:18000/api/workers`,
    `/api/instagram/profiles` e `/api/commands` respondiam 200 sem token, e `POST /api/admin/shutdown`,
    `PUT /api/instagram/profiles/{id}/credential` e `POST /api/workers/enroll` estavam ao alcance de qualquer
    processo local daquela máquina, inclusive de usuário não-administrador. Comprometer um worker equivalia a
    comprometer o central.

    Conferir o endereço do par NÃO resolve isto: o par É 127.0.0.1. O discriminante tem de ser outra coisa, e a
    coisa mais simples que funciona é **qual porta atendeu**. Aqui não existe rota REST, então o que sobra para
    quem chega pelo túnel é o WebSocket do worker, que autentica na primeira mensagem.

    Sem middleware de `Host`/CORS de propósito: não há o que isentar quando não há rota a proteger, e o próprio
    `worker_ws` confere `Host` antes do `accept()`. Sem `lifespan`: o estado é o MESMO objeto do app principal,
    que já o inicia e o encerra uma vez só — dois `AppState` no mesmo banco seriam dois donos das mesmas etapas.
    """
    app = FastAPI(title="Central de Aparelhos — canal do worker", version=VERSION)
    app.state.poc = state
    app.include_router(worker_router)
    return app


def _socket_de(host: str, porta: int) -> socket.socket:
    """Socket já ligado (`bind`), do jeito que `uvicorn.Server.run(sockets=[...])` espera.

    **Sem `SO_REUSEADDR` no Windows**, e isto não é descuido: lá a opção permite que DOIS processos se liguem à
    MESMA porta, cada um recebendo parte das conexões. É exatamente o que o projeto inteiro evita — dois backends
    no mesmo banco são dois donos das mesmas etapas. O `asyncio.create_server` não a liga no Windows, e o
    comportamento de "a segunda subida falha" tem de continuar valendo agora que os sockets são nossos. Em POSIX a
    opção significa outra coisa (reusar porta em `TIME_WAIT`) e continua desejável.
    """
    # A família sai do `getaddrinfo`, não de um `AF_INET` fixo: `conferir_exposicao` aceita `server.host: "::1"`
    # de propósito, e um socket IPv4 tentando ligar num endereço IPv6 falharia com a mensagem errada ("outro
    # backend já está no ar?"), mandando quem for depurar procurar um processo que não existe.
    try:
        familia, tipo, proto, _, endereco = socket.getaddrinfo(host, porta, type=socket.SOCK_STREAM)[0]
    except OSError as exc:
        raise SystemExit(f"Endereço inválido em server.host: {host!r} ({exc}).") from exc
    sock = socket.socket(familia, tipo, proto)
    if os.name != "nt":
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(endereco)
    except OSError as exc:
        sock.close()
        raise SystemExit(f"Não foi possível abrir {host}:{porta} ({exc}). Outro backend já está no ar?") from exc
    sock.set_inheritable(True)
    return sock


def despachante(principal: FastAPI, do_worker: FastAPI, porta_do_worker: int):  # type: ignore[no-untyped-def]
    """Um app ASGI que escolhe o destino pela PORTA que atendeu (`scope["server"][1]`).

    Por que um despachante e não dois `uvicorn.Server`: dois servidores no mesmo processo instalam, cada um, os
    handlers de sinal do uvicorn (`capture_signals` usa `signal.signal`), e o segundo sobrescreve o primeiro —
    Ctrl+C passaria a encerrar um só. Com um servidor e dois sockets há um laço de eventos, um `lifespan`, um
    `should_exit` e um encerramento gracioso. `POST /api/admin/shutdown` continua desligando tudo de uma vez.
    """
    async def app(scope: dict, receive: Any, send: Any) -> None:  # type: ignore[type-arg]
        if scope["type"] == "lifespan":
            # Só o app principal tem lifespan: é ele que inicia e encerra o `AppState` compartilhado.
            await principal(scope, receive, send)
            return
        servidor = scope.get("server") or (None, None)
        alvo = do_worker if servidor[1] == porta_do_worker else principal
        await alvo(scope, receive, send)
    return app


def main() -> None:
    cfg = get_config()
    setup_logging(cfg)
    host = cfg.file.server.host
    conferir_exposicao(cfg)
    # workers=1 e reload desligado: fork traria processos com o mesmo OWNER_ID disputando as mesmas etapas
    poc = AppState(cfg)
    app = create_app(cfg, state=poc)
    sockets = [_socket_de(host, cfg.file.server.port)]
    porta_worker = int(cfg.file.server.worker_port or 0)
    if porta_worker:
        # SEMPRE em 127.0.0.1, mesmo quando `server.host` é de rede: esta porta existe para ser o alvo do `-R` do
        # túnel, e expô-la à rede recriaria a superfície que ela veio fechar.
        sockets.append(_socket_de("127.0.0.1", porta_worker))
        alvo: Any = despachante(app, create_worker_app(poc), porta_worker)
    else:
        alvo = app
    # timeout_graceful_shutdown: sem ele o uvicorn espera PARA SEMPRE por uma conexão/tarefa pendurada e o processo
    # fica vivo sem porta, com o scheduler rodando — e o próximo start criaria um segundo dono do banco.
    # proxy_headers=False não é enfeite: com o padrão (`True`, `forwarded_allow_ips=127.0.0.1`) o uvicorn REESCREVE
    # `scope["client"]` a partir de `X-Forwarded-For` quando o par é loopback. Como a isenção de loopback do
    # `avaliar` passou a olhar o par, isso devolveria o defeito por outra porta: bastaria um cabeçalho para o par
    # virar o que o cliente quisesse. Não há proxy reverso na frente deste processo; não há nada para confiar.
    server = uvicorn.Server(uvicorn.Config(alvo, workers=1, reload=False, log_level="warning",
                                           timeout_graceful_shutdown=10, proxy_headers=False,
                                           forwarded_allow_ips=[]))
    app.state.server = server          # POST /api/admin/shutdown pede o encerramento gracioso
    logging.getLogger("poc.main").info(
        "ouvindo em %s:%s%s", host, cfg.file.server.port,
        f" | canal do worker em 127.0.0.1:{porta_worker}" if porta_worker else " | canal do worker desligado")
    server.run(sockets=sockets)
    # O estado já está no SQLite e o lifespan já fechou tudo; uma thread de aparelho presa numa chamada ao Appium/adb
    # não pode segurar o processo (o interpretador faria join nela para sempre).
    logging.shutdown()
    os._exit(0)


if __name__ == "__main__":
    main()
