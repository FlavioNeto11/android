"""Achado #166: 38 das 85 rotas não tinham nenhum teste no NÍVEL HTTP.

Teste de serviço prova a regra; ele não prova o mapeamento de erro para código HTTP, a validação do corpo nem o
formato que o frontend consome. Este arquivo não testa uma rota: ele é a REDE que impede a lacuna de voltar a
crescer em silêncio — uma rota nova que nasce sem nenhuma chamada HTTP na suíte reprova aqui, com o nome dela.

O cruzamento é por AST, não por `grep`: o `grep` não entende `f"/api/instances/{rt.id}/frame"` e foi por isso
que a contagem à mão da auditoria errou duas vezes. Aqui cada chamada de cliente HTTP nos testes vira
`(MÉTODO, caminho)` com `*` no lugar de cada `{...}`, e o casamento é segmento a segmento.
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Iterator

from fastapi.routing import APIRoute, APIWebSocketRoute

from app.main import create_app

from .conftest import make_config

#: Rotas que, de propósito, não têm chamada HTTP na suíte. Cada entrada precisa de um MOTIVO — a lista é a
#: confissão do que não está provado, não um esconderijo. Ela encolhe; nunca cresce sem alguém escrever por quê.
#:
#: Hoje está VAZIA, e isso é medido, não aspiração: as 97 rotas REST e os 2 WebSockets de `/api` têm chamada na
#: suíte. Duas entradas chegaram a ser escritas aqui por suposição (`admin/shutdown`, `worker/ws`) e caíram
#: quando a lista vazia passou — `test_canal_do_worker` chama as duas.
SEM_TESTE_HTTP: dict[tuple[str, str], str] = {}

MÉTODOS = {"get", "post", "put", "patch", "delete", "head", "options"}


def _achatar(rotas: list[Any]) -> Iterator[Any]:
    """`include_router` não copia mais as rotas para `app.routes`: ele deixa um `_IncludedRouter` que só as
    resolve no primeiro pedido (FastAPI 0.141). Enumerar `app.routes` direto devolvia 4 rotas e este teste
    passava sem olhar para nada — por isso a recursão por `original_router`."""
    for r in rotas:
        interno = getattr(r, "original_router", None)
        if interno is not None:
            yield from _achatar(list(interno.routes))
        else:
            yield r


def _rotas_do_app() -> list[tuple[str, str]]:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        app = create_app(make_config(Path(tmp)))          # só enumerar: o lifespan não roda, nada sobe
    fora: list[tuple[str, str]] = []
    for r in _achatar(list(app.routes)):
        if isinstance(r, APIWebSocketRoute) and r.path.startswith("/api"):
            fora.append(("WS", r.path))
        elif isinstance(r, APIRoute) and r.path.startswith("/api"):
            for m in sorted(r.methods or ()):
                if m not in {"HEAD", "OPTIONS"}:
                    fora.append((m, r.path))
    return sorted(set(fora))


def _caminho(node: ast.AST) -> str | None:
    """Literal ou f-string → caminho com `*` onde havia expressão. Nada mais (variável, concatenação) conta."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        partes = []
        for p in node.values:
            if isinstance(p, ast.Constant) and isinstance(p.value, str):
                partes.append(p.value)
            elif isinstance(p, ast.FormattedValue):
                partes.append("*")
            else:
                return None
        return "".join(partes)
    return None


def _chamadas_dos_testes() -> set[tuple[str, str]]:
    achadas: set[tuple[str, str]] = set()
    for arquivo in sorted(Path(__file__).parent.glob("test_*.py")):
        if arquivo.name == Path(__file__).name:
            continue                        # a lista de exceções DESTE arquivo não pode contar como cobertura
        árvore = ast.parse(arquivo.read_text(encoding="utf-8"), filename=str(arquivo))
        for nó in ast.walk(árvore):
            if not isinstance(nó, ast.Call) or not isinstance(nó.func, ast.Attribute) or not nó.args:
                continue
            attr = nó.func.attr
            if attr in MÉTODOS:
                método, alvo = attr.upper(), nó.args[0]
            elif attr in {"request", "stream"} and len(nó.args) >= 2:
                verbo = nó.args[0]
                if not (isinstance(verbo, ast.Constant) and isinstance(verbo.value, str)):
                    continue
                método, alvo = verbo.value.upper(), nó.args[1]
            elif attr in {"websocket_connect", "connect"}:
                método, alvo = "WS", nó.args[0]
            else:
                continue
            caminho = _caminho(alvo)
            if caminho and caminho.startswith("/api"):
                achadas.add((método, caminho.split("?")[0]))
    return achadas


def _casa(rota: str, chamada: str) -> bool:
    a, b = rota.strip("/").split("/"), chamada.strip("/").split("/")
    if len(a) != len(b):
        return False
    for seg_rota, seg_chamada in zip(a, b):
        if seg_rota.startswith("{") and seg_rota.endswith("}"):
            continue                        # parâmetro da rota casa com qualquer segmento
        if "*" in seg_chamada:
            continue                        # f-string: o valor só se conhece em tempo de execução
        if seg_rota != seg_chamada:
            return False
    return True


def test_toda_rota_da_api_tem_pelo_menos_uma_chamada_http_na_suite() -> None:
    chamadas = _chamadas_dos_testes()
    descobertas = []
    for método, caminho in _rotas_do_app():
        if (método, caminho) in SEM_TESTE_HTTP:
            continue
        if not any(m == método and _casa(caminho, c) for m, c in chamadas):
            descobertas.append(f"{método} {caminho}")
    assert not descobertas, (
        "rotas sem NENHUMA chamada HTTP na suíte (achado #166) — escreva o teste da rota ou declare o motivo em "
        "SEM_TESTE_HTTP:\n  " + "\n  ".join(descobertas))


def test_a_lista_de_excecoes_nao_guarda_rota_que_deixou_de_existir() -> None:
    """Exceção órfã é pior do que exceção: ela some do radar e ninguém percebe que a rota mudou de nome."""
    rotas = set(_rotas_do_app())
    órfãs = [f"{m} {c}" for (m, c) in SEM_TESTE_HTTP if (m, c) not in rotas]
    assert not órfãs, f"SEM_TESTE_HTTP aponta para rota que não existe mais: {órfãs}"
