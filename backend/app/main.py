"""Aplicação FastAPI. Processo ÚNICO POR MÁQUINA (sem --reload e sem múltiplos workers do uvicorn).

Por que "por máquina" e não "no mundo": desde a posse de etapa, o dono do trabalho é `OWNER_ID` — por omissão o
hostname. Backends em máquinas DIFERENTES convivem no mesmo banco e não se atropelam. Já `uvicorn --workers N`
forkaria N processos com o MESMO hostname, e portanto o mesmo dono: cada um reconheceria as etapas dos outros como
suas e as reconciliaria no meio da execução. Para dois backends na mesma máquina é preciso `OWNER_ID` explícito.

    python -m app.main            # a partir de backend/
"""
from __future__ import annotations

import logging
import logging.handlers
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import router
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
        recusa = avaliar(host=request.headers.get("host"), authorization=request.headers.get("authorization"),
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
    dist = cfg.root / "frontend" / "dist"
    if dist.exists():
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
    return app


def main() -> None:
    cfg = get_config()
    setup_logging(cfg)
    host = cfg.file.server.host
    conferir_exposicao(cfg)
    # workers=1 e reload desligado: fork traria processos com o mesmo OWNER_ID disputando as mesmas etapas
    app = create_app(cfg)
    # timeout_graceful_shutdown: sem ele o uvicorn espera PARA SEMPRE por uma conexão/tarefa pendurada e o processo
    # fica vivo sem porta, com o scheduler rodando — e o próximo start criaria um segundo dono do banco.
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=cfg.file.server.port, workers=1, reload=False,
                                           log_level="warning", timeout_graceful_shutdown=10))
    app.state.server = server          # POST /api/admin/shutdown pede o encerramento gracioso
    server.run()
    # O estado já está no SQLite e o lifespan já fechou tudo; uma thread de aparelho presa numa chamada ao Appium/adb
    # não pode segurar o processo (o interpretador faria join nela para sempre).
    logging.shutdown()
    os._exit(0)


if __name__ == "__main__":
    main()
