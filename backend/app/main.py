"""Aplicação FastAPI. Processo ÚNICO POR MÁQUINA (sem --reload e sem múltiplos workers do uvicorn).

Por que "por máquina" e não "no mundo": desde a posse de etapa, o dono de uma ETAPA é `OWNER_ID` — por omissão o
hostname — e um backend de outra máquina não reconcilia a etapa viva deste. Já `uvicorn --workers N` forkaria N
processos com o MESMO hostname, e portanto o mesmo dono: cada um reconheceria as etapas dos outros como suas e as
reconciliaria no meio da execução. Para dois backends na mesma máquina é preciso `OWNER_ID` explícito.

**O que passou a valer com a fase 5** (esta docstring já afirmou que dois backends "não se atropelam" quando só a
ETAPA tinha dono; era exagero, e agora está feito): `instances.hosted_by` (migração 027) diz qual backend hospeda
cada aparelho. Despacho, rodízio e as reconciliações de partida (etapas, comandos, instalações, efeitos sociais e
planejamento) atuam SÓ no que este backend hospeda — objetivo de aparelho alheio é IGNORADO, nunca bloqueado. O
limite de chamadas de IA virou lease no banco (`ai_slots`), então o teto vale para o sistema e não por processo;
`boot_parallelism` continua por processo de propósito, porque o recurso que ele protege é a RAM desta máquina.
A guarda de relógio (`MAX_CLOCK_SKEW_S`) recusa subir como SEGUNDO dono com relógio divergente do banco.

**O que ainda NÃO é compartilhado entre backends** (achado #27): o estado dos WORKERS e do controle manual vive na
memória de um processo. Cada backend precisa dos seus próprios workers e aparelhos; não dá para dois backends
compartilharem o mesmo worker. Ver "Pendências honestas" em docs/banco.md.

`ROLE` escolhe o papel deste processo: `all` (padrão, tudo), `api` (só a API — sem Appium, aparelhos, scheduler e
reconciliações) ou `scheduler` (hospeda e despacha; não publica a API REST nem o frontend).

    python -m app.main            # a partir de backend/
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import socket
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Mapping

import uvicorn
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import ROTAS_DE_SESSAO, recusa_do_despacho, router, worker_router
from .commands.despacho import DespachoRecusado
from .config import Config, get_config
from .modules.context_retrieval.presentation.router import router as context_retrieval_router
from .modules.learning.presentation.router import router as learning_router
from .modules.pedidos.presentation.router import router as pedidos_router
from .modules.skills.presentation.router import router as skills_router
from .security.access import avaliar, publicos_de
from .security.redaction import RedactingFilter, chave_sensivel
from .security.sessions import COOKIE, OPERADOR
from .state import VERSION, AppState



def setup_logging(cfg: Config) -> None:
    cfg.logs_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter('{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":%(message)r}')
    handler = logging.handlers.TimedRotatingFileHandler(cfg.logs_dir / "backend.log", when="midnight",
                                                        backupCount=cfg.file.limits.log_retention_days, encoding="utf-8")
    handler.setFormatter(fmt)
    redacting = RedactingFilter()
    handler.addFilter(redacting)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    handlers: list[logging.Handler] = [handler]
    # Achado #144: sem um TERMINAL de verdade na frente, o StreamHandler só duplicava CADA linha já gravada (e
    # rotacionada/retida) em backend.log dentro de backend.err.log — que scripts/start.ps1 e o serviço
    # supervisionado redirecionam de stderr, sem rotação nenhuma, truncado só quando o processo reinicia
    # (medido: 413 KB em ~2,3 h). Rodando à mão (`python -m app.main` num terminal de verdade), o console
    # continua útil e entra.
    if sys.stdout is not None and sys.stdout.isatty():
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S"))
        console.addFilter(redacting)
        handlers.append(console)
    root.handlers = handlers
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
    conferir_tls(cfg)


def conferir_tls(cfg: Config) -> None:
    """A terceira condição para sair do loopback: o tráfego tem de ir cifrado (item 9.2, achado #120).

    O que a opção "porta de rede" entregava a quem escutasse a rede, em claro: o `API_TOKEN` a cada requisição
    (segredo de longa duração), a credencial do worker a cada conexão, o cookie de sessão do painel, todo
    screenshot e toda evidência. Wi-Fi com chave compartilhada — o caso do notebook do parque — é rede escutável
    por quem tem a chave, e é exatamente o cenário para o qual esta opção existe.

    Duas formas de cumprir, porque as duas são legítimas: certificado NESTE processo (`tls_cert` + `tls_key`,
    repassados ao uvicorn) ou um proxy TLS na frente, declarado em `tls_behind_proxy`. O que não é aceito é
    subir para a rede sem nenhuma das duas.
    """
    servidor = cfg.file.server
    if bool(servidor.tls_cert) != bool(servidor.tls_key):
        raise SystemExit("server.tls_cert e server.tls_key vêm juntos ou não vêm: com só um dos dois o uvicorn "
                         "sobe em HTTP simples e ninguém percebe.")
    if not cfg.tls_ativo:
        raise SystemExit("server.host fora do loopback exige TLS: declare server.tls_cert + server.tls_key (o "
                         "próprio processo sobe em https/wss) ou server.tls_behind_proxy: true quando um proxy "
                         "TLS termina o HTTPS na frente. Sem isso o API_TOKEN, a credencial do worker, o cookie "
                         "de sessão e todo screenshot vão em claro para quem estiver na rede.")
    if (par := cfg.tls_direto) is not None:
        for rotulo, caminho in zip(("server.tls_cert", "server.tls_key"), par):
            if not Path(caminho).is_file():
                raise SystemExit(f"{rotulo} aponta para {caminho!r}, que não é um arquivo legível.")
        if cfg.file.server.worker_port:
            # O listener do túnel é OUTRO socket do MESMO uvicorn, e TLS é do transporte: ligá-lo aqui faria a
            # porta 127.0.0.1:<worker_port> passar a exigir `wss://` do agente, com um certificado emitido para
            # o nome público e não para 127.0.0.1. Falharia na hora de conectar, e o erro apareceria na máquina
            # do worker — longe de quem editou este arquivo.
            raise SystemExit("server.tls_cert com server.worker_port ligado: o listener do túnel compartilha o "
                             "mesmo uvicorn e passaria a exigir TLS do agente com um certificado que não vale "
                             "para 127.0.0.1. Use server.tls_behind_proxy com um proxy na frente, ou "
                             "server.worker_port: 0 quando nenhum worker chega por túnel.")


#: Variável que SÓ a imagem do contêiner define (`deploy/central.Dockerfile`). Ver `endereco_de_escuta`.
VAR_ESCUTA_DO_CONTEINER = "CONTAINER_LISTEN_HOST"


def endereco_de_escuta(cfg: Config, ambiente: Mapping[str, str] | None = None, sistema: str | None = None) -> str:
    """Onde o socket PRINCIPAL faz `bind`. Sem a variável do contêiner, `server.host` — exatamente como sempre foi.

    Existe por causa do contêiner (`deploy/`): lá dentro, `127.0.0.1` é o loopback DO CONTÊINER, e a porta que o
    Docker publica chega pela interface de rede dele — um backend ouvindo em `127.0.0.1` fica inalcançável do host.
    Trocar `server.host` para `0.0.0.0` não é a saída: isso liga `conferir_exposicao` (TLS, `public_hosts`), que
    protege a REDE, e no contêiner quem decide a exposição à rede é a publicação da porta no compose
    (`127.0.0.1:8100:8000`, conferida por `scripts/tests/test_conteineres.py`), não este processo.

    Lida de `os.environ`, e não do `EnvSettings`, de propósito: o `EnvSettings` também lê o `.env` da raiz, e uma
    linha esquecida no `.env` de uma instalação Windows abriria a porta à rede por fora de `conferir_exposicao`.
    Pelo mesmo motivo, no Windows a variável é recusada: o central Windows sai do loopback só pelo caminho com TLS.

    O que continua valendo lá dentro: o par que chega pela porta publicada é o gateway da rede do Docker, nunca
    loopback, então `avaliar` cobra credencial de TODA chamada do navegador. Sem `API_TOKEN` o painel seria uma
    tela de login impossível de passar — daí a recusa em subir sem ele, ANTES de abrir o banco. O canal do worker
    (`server.worker_port`) NÃO acompanha: continua em 127.0.0.1 (ver `main`).
    """
    valor = ((os.environ if ambiente is None else ambiente).get(VAR_ESCUTA_DO_CONTEINER) or "").strip()
    if not valor:
        return cfg.file.server.host
    if (sistema or os.name) == "nt":
        raise SystemExit(f"{VAR_ESCUTA_DO_CONTEINER} só vale dentro do contêiner Linux (deploy/). No Windows, sair "
                         "do loopback é por server.host, com API_TOKEN, public_hosts e TLS.")
    if valor not in ("127.0.0.1", "localhost", "::1") and not cfg.api_token:
        raise SystemExit(f"{VAR_ESCUTA_DO_CONTEINER}={valor} exige API_TOKEN (deploy/.env): pela porta publicada o "
                         "par é o gateway do Docker, nunca loopback, e sem o token ninguém entraria no painel.")
    return valor


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

    @app.exception_handler(RequestValidationError)
    async def validacao_sem_segredo(_request: Request, exc: RequestValidationError) -> JSONResponse:
        """O 422 padrão devolve o `input` de cada erro — e o `input` de um campo de credencial é o próprio valor
        (medido: `credentials={"Nome Ruim": "…"}` voltava com a senha em claro). Erro cujo caminho passa por um
        nome sensível sai sem `input`/`ctx`; o resto do corpo fica no formato de sempre."""
        erros = [{k: v for k, v in e.items() if k not in ("input", "ctx")}
                 if any(chave_sensivel(p) for p in e.get("loc", ())) else e for e in exc.errors()]
        return JSONResponse(status_code=422, content={"detail": jsonable_encoder(erros)})
    # O despacho de comandos recusa sem conhecer HTTP; aqui a recusa vira o 4xx de sempre (`api.err`).
    app.add_exception_handler(DespachoRecusado, recusa_do_despacho)  # type: ignore[arg-type]
    app.add_middleware(CORSMiddleware, allow_origins=cfg.file.server.allowed_origins, allow_methods=["*"],
                       allow_headers=["*"], expose_headers=["X-Frame-Id", "X-Frame-Ts", "X-Frame-Width",
                                                            "X-Frame-Height", "X-Frame-Orientation"])
    allowed_origins = set(cfg.file.server.allowed_origins)

    @app.middleware("http")
    async def guarda(request: Request, call_next):  # type: ignore[no-untyped-def]
        """Host e credencial (a regra vive em `security.access`, porque o WebSocket precisa da MESMA), mais Origin
        contra CSRF em método que altera estado — navegador sempre manda `Origin` entre origens.

        Três coisas acontecem aqui, nesta ordem:

        1. **O cookie de sessão vira um nome.** É o que faz `<img>` e WebSocket autenticarem (nenhum dos dois
           manda `Authorization`) e é de onde sai o `requested_by` da auditoria — antes dele, toda linha dizia
           `panel`. Bearer continua valendo e continua anônimo: token compartilhado prova conhecimento, não
           identidade.
        2. **O veredito de `avaliar`**, com a sessão contando como credencial.
        3. **A exceção mínima para o login ser possível.** Sem ela, o painel aberto de outra estação levaria 401
           no próprio HTML, no bundle e no `POST /api/login` — a tela de login nunca apareceria, e o item
           inteiro seria um botão sem efeito. O que fica livre é só isto: as rotas de sessão e o que NÃO é
           `/api/` (o frontend estático, que não é segredo). `forbidden_host` não tem exceção nenhuma.

        Atenção ao mexer: este middleware **não vale para WebSocket**. O `BaseHTTPMiddleware` do Starlette devolve o
        controle sem olhar quando o scope não é `http`, então `/api/ws` confere o acesso por conta própria.
        """
        poc = getattr(request.app.state, "poc", None)
        sessoes = getattr(poc, "sessions", None)
        operador = sessoes.operador_de(request.cookies.get(COOKIE)) if sessoes is not None else None
        recusa = avaliar(par=request.client.host if request.client else None,
                         host=request.headers.get("host"), authorization=request.headers.get("authorization"),
                         publicos=publicos_de(cfg), token=cfg.api_token, sessao_valida=operador is not None)
        request.state.operador = operador
        # O `ContextVar` é o que leva o nome até as dezenas de funções que gravam auditoria sem ter o `Request`
        # na mão. É preenchido ANTES do `call_next`: a tarefa que o Starlette cria ali copia o contexto de agora.
        marca = OPERADOR.set(operador)
        try:
            #: O login precisa saber se ESTA ORIGEM teria de apresentar segredo: no loopback, não (e pedir o
            #: token ali seria uma regressão de uso); de fora, sim. A pergunta é sobre a origem, NÃO sobre esta
            #: requisição — daí o segundo `avaliar`, sem credencial nenhuma. Derivar do veredito acima parecia
            #: equivalente e não era: com a sessão já aberta, a resposta viraria "não precisa de token", o painel
            #: guardaria isso, e depois de "Sair" a tela de login voltaria SEM o campo da chave — pedindo login
            #: de um jeito que o backend recusa, sem saída a não ser recarregar a página.
            request.state.credencial_exigida = avaliar(
                par=request.client.host if request.client else None, host=request.headers.get("host"),
                authorization=None, publicos=publicos_de(cfg), token=cfg.api_token) == "unauthorized"
            caminho = request.url.path
            if recusa == "unauthorized" and (caminho in ROTAS_DE_SESSAO or not caminho.startswith("/api/")):
                recusa = None
            if recusa == "unauthorized":
                # Nada do segredo recebido entra na resposta nem no log: só o fato de não servir.
                return JSONResponse({"detail": {"code": "unauthorized", "message": "Credencial ausente ou inválida."}},
                                    status_code=401, headers={"WWW-Authenticate": "Bearer"})
            if recusa is not None:
                return JSONResponse({"detail": {"code": "forbidden_host", "message": "Host não permitido."}},
                                    status_code=403)
            origin = request.headers.get("origin")
            if request.method not in ("GET", "HEAD", "OPTIONS") and origin and origin not in allowed_origins:
                return JSONResponse({"detail": {"code": "forbidden_origin", "message": "Origem não permitida."}},
                                    status_code=403)
            return await call_next(request)
        finally:
            OPERADOR.reset(marca)

    if cfg.serve_api:
        # Livro de aprendizado (ADR-054; `/api/aprendizado*`) ANTES do `router`: o voto do D2
        # (`POST /api/runs/{id}/feedback`) casaria com `POST /runs/{run_id}/{op}` de lá e viraria 404.
        app.include_router(learning_router)
        app.include_router(pedidos_router)       # `/api/pedidos` (28.9)
        app.include_router(router)
        # Depois do `router`: `/api/skills/resolve` (fase I) mora lá e precisa casar antes de `/api/skills/{id}`.
        app.include_router(skills_router)
        app.include_router(context_retrieval_router)   # só leitura (ADR-063)
    app.include_router(worker_router)      # o canal do worker também atende na porta principal (modo (b))
    dist = cfg.root / "frontend" / "dist"
    if cfg.serve_api and dist.exists():
        app.mount("/", PainelEstatico(directory=dist, html=True), name="frontend")
    return app


class PainelEstatico(StaticFiles):
    """`StaticFiles` que diz ao navegador o que pode guardar — porque o padrão dele não diz nada.

    O `StaticFiles` do Starlette manda `ETag` e `Last-Modified` e NENHUM `Cache-Control`. Sem essa instrução o
    navegador usa cache heurístico: guarda por conta própria uma fração do tempo desde a última modificação, sem
    revalidar. Para os arquivos de `assets/` isso é inofensivo — o Vite põe o hash do conteúdo no nome, então um
    arquivo com aquele nome nunca muda. Para `index.html` é o contrário: é o ÚNICO arquivo sem hash, e é ele que
    aponta qual bundle carregar.

    Medido aqui: depois de um deploy conferido, com `frontend/dist` reconstruído e o servidor devolvendo um
    `index.html` que apontava `index-D5sunKr-.js`, o painel aberto no navegador continuava executando
    `index-q9q6a0zs.js` — um bundle que já nem existia em disco. Nada estava quebrado do lado do servidor; o
    navegador só não tinha motivo para perguntar de novo. O sintoma é o pior possível: "o deploy deu certo e o
    usuário vê o código velho", sem erro em lugar nenhum.

    O par correto é o padrão de qualquer build com hash no nome: `index.html` revalida sempre (`no-cache` NÃO é
    "não guarde" — é "guarde, mas pergunte antes de usar", e com `ETag` a resposta costuma ser um 304 de alguns
    bytes), e o que tem hash pode ser guardado por um ano sem perguntar.
    """

    def file_response(self, *args: Any, **kwargs: Any) -> Any:
        resposta = super().file_response(*args, **kwargs)
        caminho = str(getattr(resposta, "path", ""))
        tem_hash = "/assets/" in caminho.replace("\\", "/")
        resposta.headers["Cache-Control"] = ("public, max-age=31536000, immutable" if tem_hash
                                             else "no-cache, must-revalidate")
        return resposta


def create_worker_app(state: AppState) -> FastAPI:
    """O listener DEDICADO ao túnel: serve os WebSockets do worker (`/api/worker/ws` e `/api/worker/midia`) e mais nada.

    Existe por um defeito de topologia, não por gosto de separar. O túnel SSH reverso (`-R 18000:127.0.0.1:8000`)
    faz toda conexão vinda da máquina do worker chegar aqui com par `127.0.0.1` **de verdade** — e loopback isenta
    de credencial. Resultado medido: do notebook do worker, `GET http://127.0.0.1:18000/api/workers`,
    `/api/instagram/profiles` e `/api/commands` respondiam 200 sem token, e `POST /api/admin/shutdown`,
    `PUT /api/instagram/profiles/{id}/credential` e `POST /api/workers/enroll` estavam ao alcance de qualquer
    processo local daquela máquina, inclusive de usuário não-administrador. Comprometer um worker equivalia a
    comprometer o central.

    Conferir o endereço do par NÃO resolve isto: o par É 127.0.0.1. O discriminante tem de ser outra coisa, e a
    coisa mais simples que funciona é **qual porta atendeu**. Aqui não existe rota REST, então o que sobra para
    quem chega pelo túnel são os WebSockets do worker, que se autenticam na primeira mensagem: o de comando pela
    credencial, o de mídia (`observe_local`) pelo token de uso único que o central emitiu no pedido da imagem.

    Sem middleware de `Host`/CORS de propósito: não há o que isentar quando não há rota a proteger, e o próprio
    `worker_ws` confere `Host` antes do `accept()`. Sem `lifespan`: o estado é o MESMO objeto do app principal,
    que já o inicia e o encerra uma vez só — dois `AppState` no mesmo banco seriam dois donos das mesmas etapas.

    `docs_url=None, redoc_url=None, openapi_url=None` não é preferência de estilo: o FastAPI monta quatro delas
    sozinho (`/docs`, `/docs/oauth2-redirect`, `/redoc`, `/openapi.json`), e "não existe rota REST nenhuma" era literalmente falso enquanto elas estavam de pé. Medido do
    notebook do worker, pelo túnel: `GET http://127.0.0.1:18000/docs` respondia **200**. O esquema do app do
    worker é vazio (WebSocket não entra em OpenAPI), então o que vazava era pouco — mas a página ainda confirma
    que há um serviço nosso do outro lado, busca JavaScript de CDN e é superfície que ninguém precisa. A porta do
    túnel serve o WebSocket e mais nada; estas quatro não são exceção.
    """
    app = FastAPI(title="Central de Aparelhos — canal do worker", version=VERSION,
                  docs_url=None, redoc_url=None, openapi_url=None)
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


def opcoes_do_uvicorn(cfg: Config) -> dict[str, Any]:
    """Os argumentos do `uvicorn.Config`, numa função para poderem ser CONFERIDOS sem abrir porta nenhuma.

    `timeout_graceful_shutdown`: sem ele o uvicorn espera PARA SEMPRE por uma conexão/tarefa pendurada e o
    processo fica vivo sem porta, com o scheduler rodando — e o próximo start criaria um segundo dono do banco.

    `proxy_headers=False` não é enfeite: com o padrão (`True`, `forwarded_allow_ips=127.0.0.1`) o uvicorn
    REESCREVE `scope["client"]` a partir de `X-Forwarded-For` quando o par é loopback. Como a isenção de loopback
    do `avaliar` passou a olhar o par, isso devolveria o defeito por outra porta: bastaria um cabeçalho para o
    par virar o que o cliente quisesse. Continua `False` mesmo com `tls_behind_proxy`: quem termina o TLS na
    frente atende num endereço público, e o que chega aqui pelo loopback é o proxy — confiar no cabeçalho dele
    seria confiar em quem o proxy repassa, que é qualquer um.

    `ssl_certfile`/`ssl_keyfile` só aparecem com certificado declarado (item 9.2): é o que sobe `https://` e,
    junto com ele, `wss://` — o agente já converte o esquema sozinho (`worker.agent.ws_url`).
    """
    opcoes: dict[str, Any] = {"workers": 1, "reload": False, "log_level": "warning",
                              "timeout_graceful_shutdown": 10, "proxy_headers": False, "forwarded_allow_ips": [],
                              # Os padrões do uvicorn, escritos: um socket sem resposta ao ping do protocolo por
                              # 20 s + 20 s é fechado com 1006/1011 — é o terceiro motivo possível de
                              # "Reconectando ao backend", ao lado do ping da aplicação (20 s + 10 s, `ws.ts`) e da
                              # ressincronização por fila cheia (`api.py`). Explícito para ser encontrado.
                              "ws_ping_interval": 20.0, "ws_ping_timeout": 20.0}
    if (par := cfg.tls_direto) is not None:
        opcoes["ssl_certfile"], opcoes["ssl_keyfile"] = par
    return opcoes


def main() -> None:
    cfg = get_config()
    setup_logging(cfg)
    conferir_exposicao(cfg)
    host = endereco_de_escuta(cfg)     # `server.host`, salvo no contêiner; recusa ANTES de abrir o banco
    # workers=1 e reload desligado: fork traria processos com o mesmo OWNER_ID disputando as mesmas etapas
    poc = AppState(cfg)
    app = create_app(cfg, state=poc)
    sockets = [_socket_de(host, cfg.file.server.port)]
    porta_worker = int(cfg.file.server.worker_port or 0)
    if porta_worker:
        # SEMPRE em 127.0.0.1, mesmo quando `server.host` é de rede: esta porta existe para ser o alvo do `-R` do
        # túnel, e expô-la à rede recriaria a superfície que ela veio fechar. Não segue `endereco_de_escuta`: no
        # contêiner isto a deixa inalcançável de fora, e é por isso que o config do contêiner a desliga (`0`).
        sockets.append(_socket_de("127.0.0.1", porta_worker))
        alvo: Any = despachante(app, create_worker_app(poc), porta_worker)
    else:
        alvo = app
    server = uvicorn.Server(uvicorn.Config(alvo, **opcoes_do_uvicorn(cfg)))
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
