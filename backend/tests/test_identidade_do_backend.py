"""Quem responde em /api/health é a Farm? — regressões do deploy de 26/09/2026.

O `cartorio-api-1` (outro projeto na mesma máquina) publica `0.0.0.0:8000`. Com a Farm parada, o
`/api/health` caiu nele (`404 {"detail":"Not Found"}`) e o supervisor, que aceitava QUALQUER resposta HTTP como
"backend vivo", recusou-se a subir a Farm. Aqui: a identidade (`service`), o reconhecimento legado estrito, a
sonda HTTP de verdade contra servidores falsos, o supervisor nos dois cenários e a paridade com os scripts.
"""
from __future__ import annotations

import http.server
import json
import re
import shutil
import socket
import subprocess
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest

from app.identidade import SERVICO, corpo_e_da_farm
from app.main import create_app
from app.supervisor import Supervisor, saude_responde

from .conftest import Harness
from .test_supervisao_do_central import ProcessoFalso

RAIZ = Path(__file__).resolve().parents[2]

FARM = {"service": SERVICO, "status": "ok", "version": "0.1.0", "commit": "a" * 40, "migration": "039_x",
        "ai": {}, "appium": {"running": True, "port": 4723}, "sdk": {"found": True}, "problems": []}
#: A Farm antes do campo `service` — em outro commit, com o esquema antigo completo. Continua sendo NOSSA.
FARM_LEGADA = {k: v for k, v in FARM.items() if k != "service"} | {"commit": "b" * 40, "status": "degraded"}
CARTORIO_404 = b'{"detail":"Not Found"}'


# ---------------------------------------------------------------- identidade pelo corpo
@pytest.mark.parametrize(("corpo", "esperado"), [
    (json.dumps(FARM), True),
    (json.dumps(FARM | {"status": "degraded"}), True),
    (json.dumps(FARM_LEGADA), True),
    (CARTORIO_404, False),
    (b'{"status":"ok","version":"2.3"}', False),                       # health de outro serviço
    (b'{"status":"degraded"}', False),                                 # JSON sem identidade
    (json.dumps(FARM | {"service": "cartorio-api"}), False),           # identidade de outro
    (b"<html><body>Not Found</body></html>", False),
    (b'{"status": "ok",', False),                                      # malformado
    (b"[1, 2, 3]", False),
    (b"", False),
    (json.dumps({k: v for k, v in FARM_LEGADA.items() if k != "sdk"}), False),   # legado incompleto
])
def test_corpo_identifica_so_a_farm(corpo: bytes | str, esperado: bool) -> None:
    assert corpo_e_da_farm(corpo) is esperado


# ---------------------------------------------------------------- a sonda HTTP de verdade
@contextmanager
def _servidor(status: int, corpo: bytes, tipo: str = "application/json", atraso_s: float = 0.0) -> Iterator[str]:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:                      # noqa: N802 - assinatura do http.server
            if atraso_s:
                time.sleep(atraso_s)
            self.send_response(status)
            self.send_header("Content-Type", tipo)
            self.end_headers()
            self.wfile.write(corpo)

        def log_message(self, *a: object) -> None:     # silêncio no pytest
            return

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_port}"
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.mark.parametrize(("status", "corpo", "tipo", "esperado"), [
    (404, CARTORIO_404, "application/json", False),                    # o Cartório no lugar da Farm parada
    (200, b'{"status":"ok","service":"cartorio"}', "application/json", False),
    (200, b"<html>ok</html>", "text/html", False),
    (503, json.dumps(FARM | {"status": "degraded"}).encode(), "application/json", True),   # Farm viva, degradada
    (200, json.dumps(FARM).encode(), "application/json", True),
    (200, json.dumps(FARM_LEGADA).encode(), "application/json", True),  # Farm em commit anterior, sem `service`
])
def test_sonda_http_reconhece_so_a_farm(status: int, corpo: bytes, tipo: str, esperado: bool) -> None:
    with _servidor(status, corpo, tipo) as base:
        assert saude_responde(f"{base}/api/health", timeout=3) is esperado


def test_conexao_recusada_nao_e_farm_viva() -> None:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        porta = s.getsockname()[1]                    # porta livre: ninguém escuta depois do `with`
    assert saude_responde(f"http://127.0.0.1:{porta}/api/health", timeout=2) is False


def test_tempo_esgotado_nao_e_farm_viva() -> None:
    with _servidor(200, json.dumps(FARM).encode(), atraso_s=3.0) as base:
        assert saude_responde(f"{base}/api/health", timeout=0.5) is False


# ---------------------------------------------------------------- o supervisor nos dois cenários
def _supervisor(url: str) -> tuple[Supervisor, list[ProcessoFalso]]:
    processos: list[ProcessoFalso] = []

    def iniciar() -> ProcessoFalso:
        p = ProcessoFalso(pid=2000 + len(processos))
        processos.append(p)
        return p
    sup = Supervisor(iniciar=iniciar, saudavel=lambda: saude_responde(url, timeout=2), encerrar=lambda _p: None,
                     dormir=lambda _s: None, carencia_s=0.0, intervalo_s=0.0)
    return sup, processos


def test_supervisor_com_servico_estrangeiro_na_porta_sobe_a_farm() -> None:
    with _servidor(404, CARTORIO_404) as base:
        sup, processos = _supervisor(f"{base}/api/health")
        sup.run(ciclos=1)
    assert len(processos) == 1 and sup.relatorio.recusou_por_ja_haver_backend == 0, \
        "o 404 do Cartório não pode segurar a subida da Farm"


def test_supervisor_diante_de_farm_verdadeira_nao_sobe_segundo_backend() -> None:
    for corpo in (FARM, FARM_LEGADA):                 # inclusive a Farm antiga, anterior a `service`
        with _servidor(200, json.dumps(corpo).encode()) as base:
            sup, processos = _supervisor(f"{base}/api/health")
            sup.run(ciclos=1)
        assert processos == [] and sup.relatorio.recusou_por_ja_haver_backend == 1, "dois donos do banco"


# ---------------------------------------------------------------- o /api/health real e a paridade com os scripts
async def test_health_real_carrega_a_identidade(harness: Harness) -> None:
    app = create_app(harness.cfg, state=harness.state)
    app.state.poc = harness.state
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/health")
    assert r.json()["service"] == SERVICO and corpo_e_da_farm(r.content)


def test_scripts_usam_o_mesmo_identificador_do_backend() -> None:
    lib = (RAIZ / "scripts" / "lib" / "farm-health.ps1").read_text(encoding="utf-8")
    m = re.search(r"^\$FarmServico\s*=\s*'([^']+)'", lib, re.M)
    assert m and m.group(1) == SERVICO
    for nome in ("deploy.ps1", "start.ps1", "stop.ps1", "restore.ps1", "loja-janela.ps1"):
        texto = (RAIZ / "scripts" / nome).read_text(encoding="utf-8")
        assert "farm-health.ps1" in texto, f"{nome} ainda decide por 'alguém respondeu na porta'"
        assert not re.search(r"Invoke-RestMethod[^\n]*api/health[^\n]*\$(up|alive|vivo|backendNoAr)\s*=\s*\$true",
                             texto), f"{nome}: sonda ingênua de /api/health"


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="pwsh não instalado")
@pytest.mark.parametrize(("status", "corpo", "esperado"), [
    (404, CARTORIO_404, "nao"),
    (503, json.dumps(FARM | {"status": "degraded"}).encode(), "farm"),
    (200, json.dumps(FARM_LEGADA).encode(), "farm"),
    (200, b"<html>ok</html>", "nao"),
])
def test_get_farmhealth_do_powershell_decide_igual(status: int, corpo: bytes, esperado: str) -> None:
    lib = RAIZ / "scripts" / "lib" / "farm-health.ps1"
    with _servidor(status, corpo) as base:
        saida = subprocess.run(
            ["pwsh", "-NoProfile", "-Command", f". '{lib}'; if (Get-FarmHealth '{base}' 3) {{ 'farm' }} else {{ 'nao' }}"],
            capture_output=True, text=True, timeout=60)
    assert saida.stdout.strip().splitlines()[-1] == esperado, saida.stderr
