"""15.15 F4: o que mudar de lugar quando as rotas saem de `api.py` para `modules/*/presentation`.

Duas coisas se perdem em silêncio nessa mudança, e nenhuma delas é um teste de rota:
1. a ORDEM em que o roteador tenta as rotas: uma rota-modelo (`/runs/{run_id}/{op}`) antes de uma literal que ela também
   casa (`/runs/targets/resolve`) engole a literal;
2. o ciclo: um módulo de apresentação que importa `app.api` (que importa os módulos).

Nível de prova: estático e `simulated` (só enumera as rotas de `create_app`, sem subir nada).
"""
from __future__ import annotations

import ast
import re
import tempfile
from pathlib import Path

from app.main import create_app

from .conftest import make_config

#: O que o repositório já tinha ANTES do F4 e é intencional: a rota-modelo vem primeiro e a literal fica atrás dela, porque a
#: resolução de uma delas só existe por esse caminho. Não cresce; cada entrada é (método, rota-modelo, rota literal).
SOBREPOSTAS_CONHECIDAS: frozenset[tuple[str, str, str]] = frozenset()


def _rotas_na_ordem() -> list[tuple[str, str]]:
    with tempfile.TemporaryDirectory() as tmp:
        esquema = create_app(make_config(Path(tmp))).openapi()
    return [(metodo.upper(), caminho) for caminho, ops in esquema["paths"].items() for metodo in ops]


def _casa(modelo: str, literal: str) -> bool:
    """A rota `modelo` (com `{x}`) casa o caminho `literal`? Cada `{x}` vale um segmento, como no Starlette."""
    padrao = "^" + re.sub(r"\\\{[^/]+?\\\}", "[^/]+", re.escape(modelo)) + "$"
    return re.match(padrao, literal) is not None


def test_nenhuma_rota_modelo_vem_antes_de_uma_literal_que_ela_tambem_casa() -> None:
    rotas = _rotas_na_ordem()
    engolidas = []
    for i, (metodo, caminho) in enumerate(rotas):
        if "{" not in caminho:
            continue
        for metodo2, caminho2 in rotas[i + 1:]:
            if metodo2 == metodo and "{" not in caminho2 and _casa(caminho, caminho2) \
                    and (metodo, caminho, caminho2) not in SOBREPOSTAS_CONHECIDAS:
                engolidas.append((metodo, caminho, caminho2))
    assert not engolidas, ("a rota-modelo vem antes da literal e a engole (método, modelo, literal): "
                           f"{engolidas} — confira a ordem de `include_router` em main.py")


def test_o_voto_da_execucao_vem_antes_do_coringa_de_execucao() -> None:
    """O caso que a ordem de `include_router` existe para proteger (comentário em `main.py`): `POST /runs/{id}/feedback`
    (o livro de aprendizado) tem de ser tentado antes de `POST /runs/{run_id}/{op}`."""
    rotas = _rotas_na_ordem()
    voto = next((i for i, (m, c) in enumerate(rotas) if m == "POST" and c.endswith("/runs/{run_id}/feedback")
                 or (m == "POST" and re.fullmatch(r"/api/runs/\{[^/]+\}/feedback", c))), None)
    coringa = next((i for i, (m, c) in enumerate(rotas) if m == "POST" and re.fullmatch(r"/api/runs/\{[^/]+\}/\{op\}", c)),
                   None)
    assert voto is not None and coringa is not None, (voto, coringa)
    assert voto < coringa


def test_as_sete_rotas_de_releases_seguem_no_app_e_cada_uma_uma_vez() -> None:
    """15.15 F4d: as rotas de `/api/releases*` saíram de `api.py` para `modules/applications/presentation/releases.py`; o
    conjunto (método e modelo) é o de antes, sem repetição nem rota perdida."""
    rotas = [r for r in _rotas_na_ordem() if r[1].startswith("/api/releases")]
    assert sorted(rotas) == sorted([
        ("GET", "/api/releases"), ("POST", "/api/releases/import"), ("GET", "/api/releases/{release_id}/icon"),
        ("GET", "/api/releases/{release_id}/targets"), ("POST", "/api/releases/upload"),
        ("POST", "/api/releases/{release_id}/approve-signature"), ("POST", "/api/releases/{release_id}/lifecycle")])


def test_as_dez_rotas_de_workers_e_limites_seguem_no_app_e_cada_uma_uma_vez() -> None:
    """15.15 F4e: `/api/workers*` e `/api/servers/*/limits` saíram de `api.py` para `modules/fleet/presentation/workers.py`; o
    conjunto (método e modelo) é o de antes. A literal de três segmentos não é engolida pelo modelo de um."""
    rotas = [r for r in _rotas_na_ordem() if r[1].startswith(("/api/workers", "/api/servers"))]
    esperadas = [
        ("GET", "/api/workers"), ("GET", "/api/servers/limits"), ("PUT", "/api/servers/{worker_id}/limits"),
        ("GET", "/api/workers/{worker_id}"), ("GET", "/api/workers/devices/unbound"),
        ("POST", "/api/workers/{worker_id}/devices/adopt"), ("POST", "/api/workers/enroll"),
        ("POST", "/api/workers/{worker_id}/maintenance"), ("DELETE", "/api/workers/{worker_id}"),
        ("POST", "/api/workers/{worker_id}/rotate-credential")]
    assert sorted(rotas) == sorted(esperadas)
    assert not _casa("/api/workers/{worker_id}", "/api/workers/devices/unbound")        # 1 segmento x 3: sem sobreposição


def test_modulos_de_apresentacao_nao_importam_app_api() -> None:
    """Ciclo: `app.api` importa os módulos; um módulo que importa `app.api` de volta só funciona por acidente de ordem."""
    raiz = Path(__file__).resolve().parent.parent / "app" / "modules"
    culpados = []
    for arquivo in sorted(raiz.glob("*/presentation/*.py")):
        for no in ast.walk(ast.parse(arquivo.read_text(encoding="utf-8"))):
            if isinstance(no, ast.ImportFrom) and (no.module == "app.api" or (no.level and no.module == "api")):
                culpados.append(f"{arquivo.relative_to(raiz.parent)}:{no.lineno}")
            if isinstance(no, ast.Import) and any(a.name == "app.api" for a in no.names):
                culpados.append(f"{arquivo.relative_to(raiz.parent)}:{no.lineno}")
    assert not culpados, f"módulo de apresentação importa app.api (ciclo): {culpados}"
