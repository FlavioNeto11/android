"""Item 29.56: a trava de tentativas é POR CLIENTE, vale também para o Bearer, e o acesso local nunca se tranca.

O defeito que estes testes travam (revisão de risco do #171, 29.54): a `PortaoDeLogin` era uma só para o processo e,
pelo túnel da Cloudflare, todo par é `127.0.0.1`. Oito chutes de qualquer pessoa na internet trancavam o login de
fora do dono, e o Bearer em `/api/*` não tinha trava nenhuma (o mesmo oráculo de força bruta, sem limite).
"""
from __future__ import annotations

import httpx
import pytest
from pydantic import SecretStr

from app.main import CABECALHOS_DE_SEGURANCA, create_app
from app.security.access import CLIENTE_LOCAL, cliente_de
from app.security.sessions import PortaoDeLogin

from .conftest import Harness

SEGREDO = "tk-tranca-5e2a91c07d4b8f3"     # token de teste, não existe fora daqui
NOME = "Ana Ribeiro"
PUBLICO = "portal.exemplo.test"
PUBLICOS = frozenset({PUBLICO})


# ---------------------------------------------------------------------------------------------- quem é o cliente
def test_par_e_nome_locais_sao_o_cliente_local() -> None:
    assert cliente_de(par="127.0.0.1", host="127.0.0.1:8000", ip_na_borda="203.0.113.9", publicos=PUBLICOS,
                      atras_de_proxy=True) == CLIENTE_LOCAL


def test_pelo_tunel_com_proxy_declarado_o_cliente_e_o_ip_da_borda() -> None:
    assert cliente_de(par="127.0.0.1", host=PUBLICO, ip_na_borda=" 203.0.113.9 ", publicos=PUBLICOS,
                      atras_de_proxy=True) == "ip:203.0.113.9"
    assert cliente_de(par="::1", host=PUBLICO, ip_na_borda="2001:db8::1", publicos=PUBLICOS,
                      atras_de_proxy=True) == "ip:2001:db8::1"


def test_sem_proxy_declarado_o_cabecalho_nao_compra_nada() -> None:
    # Sem `tls_behind_proxy`, ninguém disse que há uma borda na frente: o cabeçalho é texto de quem chamou.
    assert cliente_de(par="127.0.0.1", host=PUBLICO, ip_na_borda="203.0.113.9", publicos=PUBLICOS,
                      atras_de_proxy=False) == "ip:127.0.0.1"


def test_par_da_rede_vale_pelo_proprio_endereco_mesmo_forjando_o_cabecalho() -> None:
    assert cliente_de(par="192.168.1.50", host=PUBLICO, ip_na_borda="203.0.113.9", publicos=PUBLICOS,
                      atras_de_proxy=True) == "ip:192.168.1.50"


def test_pelo_tunel_sem_ip_valido_todos_caem_numa_chave_so() -> None:
    for lixo in (None, "", "não é ip", "1.2.3.4, 5.6.7.8"):
        assert cliente_de(par="127.0.0.1", host=PUBLICO, ip_na_borda=lixo, publicos=PUBLICOS,
                          atras_de_proxy=True) == "tunel"


# ---------------------------------------------------------------------------------------------- o portão
def test_a_trava_de_um_cliente_nao_tranca_o_outro() -> None:
    p = PortaoDeLogin(limite=3, janela_s=60, bloqueio_s=60)
    for i in range(3):
        p.registrar_falha(100.0 + i, "ip:203.0.113.9")
    assert p.segundos_de_espera(103.0, "ip:203.0.113.9") > 0
    assert p.segundos_de_espera(103.0, "ip:198.51.100.7") == 0
    p.registrar_acerto("ip:203.0.113.9")
    assert p.segundos_de_espera(103.0, "ip:203.0.113.9") == 0


def test_trocar_de_ip_a_cada_chute_nao_cresce_a_memoria_sem_limite() -> None:
    p = PortaoDeLogin(limite=3, janela_s=60, bloqueio_s=60)
    p.MAX_CLIENTES = 50
    for i in range(3):
        p.registrar_falha(1.0 + i, "ip:bloqueado")
    for i in range(500):
        p.registrar_falha(10.0, f"ip:10.0.{i // 256}.{i % 256}")
    assert len(p._clientes) <= 50
    # A poda leva primeiro quem não está bloqueado: o bloqueio vigente sobrevive à enxurrada.
    assert p.segundos_de_espera(10.0, "ip:bloqueado") > 0


# ---------------------------------------------------------------------------------------------- de ponta a ponta
def _pelo_tunel(h: Harness) -> None:
    h.cfg.file.server.public_hosts = [PUBLICO]
    h.cfg.file.server.tls_behind_proxy = True
    h.cfg.file.server.allowed_origins = [*h.cfg.file.server.allowed_origins, f"https://{PUBLICO}"]
    h.cfg.env.api_token = SecretStr(SEGREDO)


def _cliente(h: Harness, *, host: str, ip: str | None = None) -> httpx.AsyncClient:
    """O par é sempre o loopback, como o `cloudflared` desta máquina; `ip` é o `CF-Connecting-IP` da borda."""
    app = create_app(h.cfg, state=h.state)
    app.state.poc = h.state
    cab = {"Origin": f"https://{host}"} if host == PUBLICO else {}
    if ip:
        cab["CF-Connecting-IP"] = ip
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 4242)),
                             base_url=f"http://{host}", headers=cab)


async def test_chutes_de_um_ip_pelo_tunel_nao_trancam_o_login_do_dono(harness: Harness) -> None:
    _pelo_tunel(harness)
    async with _cliente(harness, host=PUBLICO, ip="203.0.113.9") as atacante:
        vistos = [(await atacante.post("/api/login", json={"operator": NOME, "token": f"chute-{i}"})).status_code
                  for i in range(9)]
        assert 429 in vistos, vistos
        travado = await atacante.post("/api/login", json={"operator": NOME, "token": SEGREDO})
        assert travado.status_code == 429
        assert travado.headers["retry-after"].isdigit()
        assert travado.json()["detail"]["code"] == "too_many_attempts"
    # O dono, de outro IP pelo mesmo túnel, entra com a chave certa.
    async with _cliente(harness, host=PUBLICO, ip="198.51.100.7") as dono:
        assert (await dono.post("/api/login", json={"operator": NOME, "token": SEGREDO})).status_code == 200
    # E o acesso local não passa pela trava.
    async with _cliente(harness, host="127.0.0.1") as local:
        assert (await local.post("/api/login", json={"operator": NOME})).status_code == 200


async def test_o_bearer_errado_tambem_tranca_e_so_quem_chutou(harness: Harness) -> None:
    _pelo_tunel(harness)
    async with _cliente(harness, host=PUBLICO, ip="203.0.113.9") as atacante:
        vistos = [(await atacante.get("/api/instances", headers={"Authorization": f"Bearer chute-{i}"})).status_code
                  for i in range(9)]
        assert vistos[0] == 401 and 429 in vistos, vistos
        # Bloqueado, nem o token certo passa: é o que faz da trava uma trava.
        travado = await atacante.get("/api/instances", headers={"Authorization": f"Bearer {SEGREDO}"})
        assert travado.status_code == 429
        assert int(travado.headers["retry-after"]) >= 1
        # O login do mesmo cliente também está trancado: os dois caminhos chutam o mesmo segredo.
        assert (await atacante.post("/api/login", json={"operator": NOME, "token": SEGREDO})).status_code == 429
    async with _cliente(harness, host=PUBLICO, ip="198.51.100.7") as dono:
        assert (await dono.get("/api/instances", headers={"Authorization": f"Bearer {SEGREDO}"})).status_code == 200
    async with _cliente(harness, host="127.0.0.1") as local:
        assert (await local.get("/api/instances", headers={"Authorization": "Bearer qualquer"})).status_code == 200


async def test_sem_bearer_o_401_nao_conta_como_chute(harness: Harness) -> None:
    """O painel aberto antes do login toma 401 em várias rotas de uma vez: isso não pode trancar o dono."""
    _pelo_tunel(harness)
    async with _cliente(harness, host=PUBLICO, ip="198.51.100.7") as dono:
        for _ in range(12):
            assert (await dono.get("/api/instances")).status_code == 401
        assert (await dono.post("/api/login", json={"operator": NOME, "token": SEGREDO})).status_code == 200


@pytest.mark.parametrize("caminho", ["/api/instances", "/api/session", "/central/naoexiste"])
async def test_toda_resposta_leva_os_cabecalhos_de_seguranca(harness: Harness, caminho: str) -> None:
    _pelo_tunel(harness)
    async with _cliente(harness, host=PUBLICO, ip="198.51.100.7") as c:
        r = await c.get(caminho)
    for nome, valor in CABECALHOS_DE_SEGURANCA.items():
        assert r.headers.get(nome) == valor, (caminho, r.status_code, nome)
