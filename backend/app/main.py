"""Aplicação FastAPI. Processo ÚNICO (sem --reload e sem múltiplos workers): ele é o dono do scheduler.

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
from .state import VERSION, AppState


def setup_logging(cfg: Config) -> None:
    cfg.logs_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter('{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":%(message)r}')
    handler = logging.handlers.TimedRotatingFileHandler(cfg.logs_dir / "backend.log", when="midnight",
                                                        backupCount=cfg.file.limits.log_retention_days, encoding="utf-8")
    handler.setFormatter(fmt)
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers = [handler, console]
    for noisy in ("httpx", "httpcore", "urllib3", "selenium", "anthropic", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


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
    allowed_hosts = {"127.0.0.1", "localhost", "[::1]", "test", "testserver"}

    @app.middleware("http")
    async def local_only(request: Request, call_next):  # type: ignore[no-untyped-def]
        """Defesa contra CSRF e DNS rebinding: só hosts de loopback e, em métodos que alteram estado,
        só as origens configuradas (navegadores sempre enviam Origin em POST entre origens)."""
        host = (request.headers.get("host") or "").rsplit(":", 1)[0].lower()
        if host not in allowed_hosts:
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
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit("server.host precisa ser loopback (127.0.0.1) nesta POC.")
    # workers=1 e reload desligado: um único dono do scheduler, sem execuções duplicadas
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
