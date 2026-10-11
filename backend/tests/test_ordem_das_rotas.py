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
    conjunto (método e modelo) é o de antes, sem repetição nem rota perdida (mais as 3 da ponte do igfarm)."""
    rotas = [r for r in _rotas_na_ordem() if r[1].startswith("/api/releases")]
    assert sorted(rotas) == sorted([
        ("GET", "/api/releases"), ("POST", "/api/releases/import"), ("GET", "/api/releases/{release_id}/icon"),
        ("GET", "/api/releases/{release_id}/targets"), ("POST", "/api/releases/upload"),
        ("POST", "/api/releases/{release_id}/approve-signature"), ("POST", "/api/releases/{release_id}/lifecycle")])


def test_as_rotas_de_workers_e_limites_seguem_no_app_e_cada_uma_uma_vez() -> None:
    """15.15 F4e: `/api/workers*` e `/api/servers/*/limits` saíram de `api.py` para `modules/fleet/presentation/workers.py`; o
    conjunto (método e modelo) é o de antes. A literal de três segmentos não é engolida pelo modelo de um. 29.154
    acrescentou as seis do comando remoto (interruptor, pedir, listar, ler e cancelar), no mesmo módulo."""
    rotas = [r for r in _rotas_na_ordem() if r[1].startswith(("/api/workers", "/api/servers"))]
    esperadas = [
        ("GET", "/api/workers"), ("GET", "/api/servers/limits"), ("PUT", "/api/servers/{worker_id}/limits"),
        ("GET", "/api/workers/{worker_id}"), ("GET", "/api/workers/devices/unbound"),
        ("POST", "/api/workers/{worker_id}/devices/adopt"), ("POST", "/api/workers/enroll"),
        ("POST", "/api/workers/{worker_id}/maintenance"), ("DELETE", "/api/workers/{worker_id}"),
        ("POST", "/api/workers/{worker_id}/rotate-credential"),
        # 29.154, comando remoto
        ("GET", "/api/workers/{worker_id}/comando-remoto"), ("PUT", "/api/workers/{worker_id}/comando-remoto"),
        ("POST", "/api/workers/{worker_id}/comandos"), ("GET", "/api/workers/{worker_id}/comandos"),
        ("GET", "/api/workers/{worker_id}/comandos/{exec_id}"),
        ("POST", "/api/workers/{worker_id}/comandos/{exec_id}/cancelar")]
    assert sorted(rotas) == sorted(esperadas)
    assert not _casa("/api/workers/{worker_id}", "/api/workers/devices/unbound")        # 1 segmento x 3: sem sobreposição


def test_as_onze_rotas_de_rede_seguem_no_app_e_cada_uma_uma_vez() -> None:
    """15.15 F4f: `/api/network/*` saiu de `api.py` para `modules/fleet/presentation/rede.py`; o conjunto (método e modelo) é o de
    antes, sem repetição nem rota perdida. O `PUT` do perfil (só a saída esperada) entrou no 31.291."""
    rotas = [r for r in _rotas_na_ordem() if r[1].startswith("/api/network")]
    assert sorted(rotas) == sorted([
        ("GET", "/api/network/profiles"), ("POST", "/api/network/profiles"), ("PUT", "/api/network/profiles/{profile_id}"),
        ("DELETE", "/api/network/profiles/{profile_id}"),
        ("GET", "/api/network/devices"), ("POST", "/api/network/assign"),
        ("POST", "/api/network/devices/{instance_id}/verify"), ("POST", "/api/network/devices/{instance_id}/reapply"),
        ("POST", "/api/network/devices/{instance_id}/apply"), ("GET", "/api/network/server"),
        ("POST", "/api/network/server/firewall-check")])


def test_as_dezoito_rotas_de_aparelhos_seguem_no_app_e_cada_uma_uma_vez() -> None:
    """15.15 F4g: `/api/instances*` (menos `personas`, que fica com as personas, e `training`, que é do treino) saiu de `api.py` para
    `modules/fleet/presentation/instancias.py`; o conjunto (método e modelo) é o de antes. `/instances/bulk` (literal) vem antes dos
    modelos de um segmento que ela também casaria."""
    fora = {"/api/instances/{instance_id}/personas", "/api/instances/{instance_id}/training"}
    rotas = [r for r in _rotas_na_ordem() if r[1].startswith("/api/instances") and r[1] not in fora]
    assert sorted(rotas) == sorted([
        ("GET", "/api/instances"), ("POST", "/api/instances"), ("DELETE", "/api/instances/{instance_id}"),
        ("PUT", "/api/instances/{instance_id}"), ("POST", "/api/instances/{instance_id}/app/install"),
        ("POST", "/api/instances/{instance_id}/app/verify"), ("GET", "/api/instances/{instance_id}/operational-context"),
        ("GET", "/api/instances/{instance_id}/packages"), ("POST", "/api/instances/bulk"),
        ("POST", "/api/instances/{instance_id}/actions/{action}"), ("GET", "/api/instances/{instance_id}/frame"),
        ("GET", "/api/instances/{instance_id}/hierarchy"), ("POST", "/api/instances/{instance_id}/locked-account/resolve"),
        ("PUT", "/api/instances/{instance_id}/repair-pause"), ("DELETE", "/api/instances/{instance_id}/repair-pause"),
        ("POST", "/api/instances/{instance_id}/control/take"), ("POST", "/api/instances/{instance_id}/control/release"),
        ("POST", "/api/instances/{instance_id}/input")])


def test_as_vinte_rotas_de_personas_seguem_no_app_e_cada_uma_uma_vez() -> None:
    """15.15 F4h: `/api/personas*` e `/api/instances/{id}/personas` saíram de `api.py` para `modules/identity/presentation/personas.py`;
    o conjunto (método e modelo) é o de antes, sem repetição nem rota perdida."""
    rotas = [r for r in _rotas_na_ordem()
             if r[1].startswith("/api/personas") or r[1] == "/api/instances/{instance_id}/personas"]
    assert sorted(rotas) == sorted([
        ("GET", "/api/personas"), ("POST", "/api/personas"), ("POST", "/api/personas/generate/batch"),
        ("GET", "/api/personas/generate/batch/{batch_id}"), ("GET", "/api/personas/{persona_id}"),
        ("PATCH", "/api/personas/{persona_id}"), ("DELETE", "/api/personas/{persona_id}"),
        ("POST", "/api/personas/{persona_id}/devices"), ("DELETE", "/api/personas/{persona_id}/devices/{instance_id}"),
        ("PUT", "/api/personas/{persona_id}/devices/{instance_id}/primary"),
        ("GET", "/api/instances/{instance_id}/personas"), ("POST", "/api/personas/{persona_id}/preview"),
        ("POST", "/api/personas/generate"), ("POST", "/api/personas/{persona_id}/enrich"),
        ("GET", "/api/personas/{persona_id}/images"), ("POST", "/api/personas/{persona_id}/images"),
        ("GET", "/api/personas/{persona_id}/images/{image_id}"),
        ("PUT", "/api/personas/{persona_id}/images/{image_id}/primary"),
        ("PUT", "/api/personas/{persona_id}/images/{image_id}/feita-por-ia"),
        ("DELETE", "/api/personas/{persona_id}/images/{image_id}")])


def test_as_quarenta_e_nove_rotas_do_instagram_seguem_no_app_e_cada_uma_uma_vez() -> None:
    """15.15 F4i: `/api/instagram/*` saiu de `api.py` para `modules/identity/presentation/instagram.py`; o conjunto (método e modelo) é o de
    antes, sem repetição nem rota perdida."""
    rotas = [r for r in _rotas_na_ordem() if r[1].startswith("/api/instagram")]
    assert sorted(rotas) == sorted([
        ("GET", "/api/instagram/profiles"),
        ("POST", "/api/instagram/profiles"),
        ("GET", "/api/instagram/profiles/{profile_id}"),
        ("PATCH", "/api/instagram/profiles/{profile_id}"),
        ("DELETE", "/api/instagram/profiles/{profile_id}"),
        ("GET", "/api/instagram/profiles/{profile_id}/avatar"),
        ("PUT", "/api/instagram/profiles/{profile_id}/accounts/{account_id}/credential"),
        ("DELETE", "/api/instagram/profiles/{profile_id}/accounts/{account_id}/credential"),
        ("POST", "/api/instagram/profiles/{profile_id}/accounts/{account_id}/credential/clone"),
        ("POST", "/api/instagram/profiles/{profile_id}/accounts/{account_id}/credential/consent"),
        ("POST", "/api/instagram/profiles/{profile_id}/accounts/{account_id}/session/connect"),
        ("POST", "/api/instagram/profiles/{profile_id}/accounts/{account_id}/session/verify"),
        ("POST", "/api/instagram/profiles/{profile_id}/accounts/{account_id}/session/logout"),
        ("GET", "/api/instagram/profiles/{profile_id}/accounts/{account_id}/auth-attempts"),
        ("PUT", "/api/instagram/profiles/{profile_id}/credential"),
        ("DELETE", "/api/instagram/profiles/{profile_id}/credential"),
        ("POST", "/api/instagram/profiles/{profile_id}/connect"),
        ("POST", "/api/instagram/profiles/{profile_id}/verify"),
        ("POST", "/api/instagram/profiles/{profile_id}/logout"),
        ("GET", "/api/instagram/profiles/{profile_id}/memory"),
        ("POST", "/api/instagram/profiles/{profile_id}/memory"),
        ("DELETE", "/api/instagram/profiles/{profile_id}/memory/{memory_id}"),
        ("GET", "/api/instagram/profiles/{profile_id}/capacidades"),
        ("GET", "/api/instagram/profiles/{profile_id}/interactions"),
        ("POST", "/api/instagram/profiles/{profile_id}/context"),
        ("GET", "/api/instagram/profiles/{profile_id}/policy"),
        ("PUT", "/api/instagram/profiles/{profile_id}/policy"),
        ("GET", "/api/instagram/profiles/{profile_id}/accounts"),
        ("POST", "/api/instagram/profiles/{profile_id}/accounts"),
        ("PATCH", "/api/instagram/profiles/{profile_id}/accounts/{account_id}"),
        ("POST", "/api/instagram/profiles/{profile_id}/accounts/{account_id}/retire"),
        ("DELETE", "/api/instagram/profiles/{profile_id}/accounts/{account_id}"),
        ("GET", "/api/instagram/policy-groups"),
        ("GET", "/api/instagram/policy-defaults"),
        ("POST", "/api/instagram/policy-groups"),
        ("GET", "/api/instagram/policy-groups/{group_id}"),
        ("PUT", "/api/instagram/policy-groups/{group_id}"),
        ("DELETE", "/api/instagram/policy-groups/{group_id}"),
        ("GET", "/api/instagram/profiles/{profile_id}/auth-attempts"),
        ("GET", "/api/instagram/profiles/{profile_id}/runs"),
        ("GET", "/api/instagram/profiles/{profile_id}/operational-context"),
        # Ponte android <-> igfarm (ADR-088, migração 132, adendo v1.133).
        ("GET", "/api/instagram/personas-pendentes"),
        ("POST", "/api/instagram/contas"),
        ("GET", "/api/instagram/contas/{conta_id}/cabecalhos"),
        ("POST", "/api/instagram/contas/{conta_id}/consentimento"),
        ("GET", "/api/instagram/contas/{conta_id}/ciclo"),
        ("POST", "/api/instagram/profiles/{profile_id}/accounts/{account_id}/proxy"),
        ("GET", "/api/instagram/contas/{conta_id}/codigo"),
        # Conta planejada (31.281, ADR-087, adendo v1.132).
        ("POST", "/api/instagram/profiles/{profile_id}/accounts/planned"),
        ("GET", "/api/instagram/profiles/{profile_id}/accounts/handle-suggestions"),
        ("POST", "/api/instagram/profiles/{profile_id}/accounts/{account_id}/credential/prepare"),
        ("POST", "/api/instagram/profiles/{profile_id}/accounts/{account_id}/provisioning"),
        ("POST", "/api/instagram/profiles/{profile_id}/accounts/{account_id}/provisioning/signup")])


def test_as_vinte_rotas_de_proxies_comandos_e_apps_seguem_no_app_e_cada_uma_uma_vez() -> None:
    """15.15 F4j: `/api/proxies*`, `/api/commands*` e os apps (`/api/apps*`, `app-store`, `app-catalog`, `app-state`) saíram de `api.py`
    para `fleet/presentation/proxies.py`, `execution/presentation/comandos.py` e `applications/presentation/apps.py`; o conjunto (método e
    modelo) é o de antes, sem repetição nem rota perdida."""
    prefixos = ("/api/proxies", "/api/commands", "/api/apps", "/api/app-store", "/api/app-catalog", "/api/app-state")
    rotas = [r for r in _rotas_na_ordem() if r[1].startswith(prefixos)]
    assert sorted(rotas) == sorted([
        ("GET", "/api/proxies"), ("POST", "/api/proxies"), ("DELETE", "/api/proxies/{proxy_id}"), ("POST", "/api/proxies/apply"),
        ("GET", "/api/commands/{command_id}"), ("GET", "/api/commands"), ("POST", "/api/commands/{command_id}/verify"),
        ("POST", "/api/commands/{command_id}/cancel"), ("POST", "/api/commands/{command_id}/resolve"), ("POST", "/api/commands/refine"),
        ("GET", "/api/apps-overview"), ("GET", "/api/apps/{app_id}/overview"), ("GET", "/api/apps/{pacote}/conhecimento"),
        ("GET", "/api/apps"), ("POST", "/api/apps"), ("PUT", "/api/apps/{app_id}"), ("DELETE", "/api/apps/{app_id}"),
        ("GET", "/api/app-store"), ("GET", "/api/app-catalog"), ("GET", "/api/app-state")])


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
