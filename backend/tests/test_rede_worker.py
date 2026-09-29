"""Rede por aparelho, item 25.7 (ADR-056): aparelhos de OUTRA máquina (o notebook do worker) no servidor do central.

O que se prova aqui (tudo `simulated`: aparelho falso, processo falso no lugar do sing-box, leitura do firewall
trocada por um dublê que devolve o JSON que o PowerShell devolveria; nada de adb, emulador, firewall ou rede):
- o aparelho remoto recebe o perfil pelo `adb reverse` (o túnel do adb já o alcança; nenhuma rota do host muda) e
  disca o servidor pelo `rede.servidor.endpoint_lan` e a porta real do servidor — nunca pelo 10.0.2.2 do perfil, que
  no notebook é o próprio notebook; o emulador local segue com o 10.0.2.2;
- sem `endpoint_lan`, o remoto é recusado dizendo a chave, antes de gerar par;
- o firewall do central só é LIDO: o script não tem verbo que escreva; o estado sai da ordem do Windows (perfil
  desligado não filtra; bloqueio vence permissão; sem regra vale a entrada padrão) e vem com o comando EXATO do dono;
- firewall fechado (`bloqueado`/`sem_regra`) recusa a aplicação no remoto com o comando no erro; `desconhecido`
  segue com a nota; o `GET /api/network/server` e o `POST …/firewall-check` mostram o `remote_access`;
- o endereço da LAN não entra na assinatura do servidor (trocá-lo não derruba os outros pares).

A prova real (um aparelho do notebook com o túnel fechado no log do servidor) é `not_run`: depende da regra de
firewall que só o dono cria (docs/dominios/parque.md, "Aparelhos do worker").
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from app.commands import despacho
from app.config import RedeServidorCfg
from app.devices import rede
from app.devices import rede_aplicacao
from app.devices.rede_firewall import (FirewallDoCentral, avaliar, comando_de_desbloqueio, comando_de_liberacao,
                                       script_de_leitura)
from app.devices.rede_servidor import Desejo, Par
from app.main import create_app

from .conftest import Harness
from .test_rede_aplicacao import AparelhoFalso, Reinicios, _linha, _passo, parque  # noqa: F401 - `parque` é fixture

BIN = Path(r"C:\git\android\data\rede\sing-box-1.14.2-windows-amd64\sing-box.exe")
LAN = "192.168.1.81"
WORKER = "worker-lan-01"


def _perfis(ligado: str = "True", entrada: str = "Block") -> list[dict[str, str]]:
    return [{"nome": n, "ligado": ligado, "entrada": entrada} for n in ("Domain", "Private", "Public")]


def _leitura(regras: object = None, *, perfis: object = None, redes: object = None) -> dict[str, object]:
    """O formato que o script devolve — o desta máquina em 29/09 (leitura real, só leitura): Wi-Fi Public com
    192.168.1.81, os três perfis ligados com entrada Block e nenhuma regra para o sing-box."""
    return {"binario": str(BIN), "porta": 51820, "perfis": _perfis() if perfis is None else perfis,
            "redes": [{"interface": "Wi-Fi", "categoria": "Public",
                       "ips": ["fe80::93b1:bb26:6d64:2dde%19", LAN]}] if redes is None else redes,
            "regras": [] if regras is None else regras}


def _regra(nome: str, acao: str, *, programa: str = str(BIN), portas: object = ("Any",), protocolo: str = "UDP",
           perfis: str = "Any", habilitada: str = "True", direcao: str = "Inbound", origem: str = "Local") -> dict:
    return {"nome": nome, "exibicao": nome, "habilitada": habilitada, "direcao": direcao, "acao": acao,
            "perfis": perfis, "origem": origem, "protocolo": protocolo, "portas": list(portas)
            if not isinstance(portas, str) else portas, "programa": programa}


# ============================================================================ o script e a avaliação (puros)
def test_script_so_le_e_consulta_o_que_vale_de_fato() -> None:
    script = script_de_leitura(Path(r"C:\pasta d'o dono\sing-box.exe"), 51820)
    for verbo in ("New-Net", "Set-Net", "Enable-Net", "Disable-Net", "Remove-Net", "netsh", "Rename-Net"):
        assert verbo not in script, verbo
    assert script.count("-PolicyStore ActiveStore") >= 4                          # política de grupo incluída
    assert "'C:\\pasta d''o dono\\sing-box.exe'" in script                         # aspa simples dobrada, sem interpolar
    assert "$porta = 51820" in script and "ConvertTo-Json" in script


def test_sem_regra_na_rede_publica_da_o_comando_exato_do_dono() -> None:
    r = avaliar(_leitura(), binario=BIN, porta=51820, endpoint=LAN)
    assert r.estado == "sem_regra" and r.fechado
    assert (r.perfil, r.interface, r.endpoint_local) == ("Public", "Wi-Fi", True)
    assert r.comandos == [
        "New-NetFirewallRule -DisplayName 'Central de Aparelhos - rede por aparelho (WireGuard UDP 51820)' "
        "-Direction Inbound -Action Allow -Protocol UDP -LocalPort 51820 "
        r"-Program 'C:\git\android\data\rede\sing-box-1.14.2-windows-amd64\sing-box.exe' "
        "-RemoteAddress LocalSubnet -Profile Public"]
    # Sem endereço configurado não há perfil a escolher: vale para todos.
    assert avaliar(_leitura(), binario=BIN, porta=51820, endpoint=None).comandos[0].endswith("-Profile Any")


def test_bloqueio_vence_a_permissao_e_o_comando_desliga_a_regra() -> None:
    bloqueio = _regra("UDP Query User{A1B2}C:\\...\\sing-box.exe", "Block", perfis="Public")
    permissao = _regra("Central de Aparelhos - rede", "Allow", portas=["51820"])
    r = avaliar(_leitura([bloqueio, permissao]), binario=BIN, porta=51820, endpoint=LAN)
    assert r.estado == "bloqueado" and r.regras_que_liberam == ["Central de Aparelhos - rede"]
    assert r.comandos == [comando_de_desbloqueio("UDP Query User{A1B2}C:\\...\\sing-box.exe")]
    assert r.comandos[0].startswith("Disable-NetFirewallRule -Name 'UDP Query User")
    # Só o bloqueio (o que o Windows cria quando o aviso fica sem resposta): desligar E criar a permissão.
    so = avaliar(_leitura(bloqueio), binario=BIN, porta=51820, endpoint=LAN)       # objeto solto (PowerShell 5.1)
    assert so.estado == "bloqueado" and so.comandos[1] == comando_de_liberacao(BIN, 51820, "Public")
    # Bloqueio de política de grupo: o comando local não basta, e a frase diz.
    gpo = avaliar(_leitura([_regra("GPO", "Block", origem="GroupPolicy")]), binario=BIN, porta=51820, endpoint=LAN)
    assert "política de grupo" in gpo.detalhe


def test_permissao_so_conta_no_perfil_na_porta_e_no_programa_certos() -> None:
    def estado(*regras: dict, **kw: object) -> str:
        return avaliar(_leitura(list(regras)), binario=BIN, porta=51820, endpoint=LAN, **kw).estado  # type: ignore[arg-type]

    assert estado(_regra("ok", "Allow", portas=["51000-52000"])) == "liberado"              # faixa cobre
    assert estado(_regra("ok", "Allow", programa=str(BIN).upper())) == "liberado"           # caixa não importa
    assert estado(_regra("x", "Allow", perfis="Domain, Private")) == "sem_regra"            # a rede é Public
    assert estado(_regra("x", "Allow", portas=["51821"])) == "sem_regra"
    assert estado(_regra("x", "Allow", protocolo="TCP")) == "sem_regra"
    assert estado(_regra("x", "Allow", programa=r"C:\outro\sing-box.exe")) == "sem_regra"
    assert estado(_regra("x", "Allow", habilitada="False")) == "sem_regra"
    assert estado(_regra("x", "Allow", direcao="Outbound")) == "sem_regra"
    assert estado(_regra("x", "Block", habilitada="False"), _regra("ok", "Allow")) == "liberado"


def test_perfil_desligado_entrada_allow_e_endereco_que_nao_e_daqui() -> None:
    publico_desligado = [{"nome": "Domain", "ligado": "True", "entrada": "Block"},
                         {"nome": "Private", "ligado": "True", "entrada": "Block"},
                         {"nome": "Public", "ligado": "False", "entrada": "Block"}]
    r = avaliar(_leitura(perfis=publico_desligado), binario=BIN, porta=51820, endpoint=LAN)
    assert r.estado == "desligado" and not r.fechado and r.comandos == []
    r = avaliar(_leitura(perfis=_perfis(entrada="Allow")), binario=BIN, porta=51820, endpoint=LAN)
    assert r.estado == "liberado" and "entrada padrão" in r.detalhe
    # O IP configurado não é de nenhuma interface desta máquina: dito, qualquer que seja o estado.
    r = avaliar(_leitura(), binario=BIN, porta=51820, endpoint="192.168.1.10")
    assert r.endpoint_local is False and "não é endereço de nenhuma interface" in r.detalhe
    assert r.comandos[0].endswith("-Profile Any")


async def test_leitura_em_cache_forcar_rele_e_falha_e_desconhecido() -> None:
    chamadas: list[str] = []
    resposta = {"texto": json.dumps(_leitura())}

    def executar(script: str) -> str:
        chamadas.append(script)
        if resposta["texto"] == "erro":
            raise RuntimeError("powershell.exe não encontrado")
        return resposta["texto"]

    agora = {"t": 0.0}
    cfg = RedeServidorCfg(endpoint_lan=LAN)
    fw = FirewallDoCentral(lambda: cfg, lambda: BIN, executar=executar, relogio=lambda: agora["t"], validade_s=600)
    assert fw.ultima() is None
    assert (await fw.conferir()).estado == "sem_regra" and len(chamadas) == 1
    agora["t"] = 300
    assert (await fw.conferir()).estado == "sem_regra" and len(chamadas) == 1       # dentro da validade
    resposta["texto"] = json.dumps(_leitura(_regra("ok", "Allow")))
    assert (await fw.conferir(forcar=True)).estado == "liberado" and len(chamadas) == 2
    resposta["texto"] = "erro"
    r = await fw.conferir(forcar=True)
    assert r.estado == "desconhecido" and "powershell.exe" in r.detalhe and not r.fechado
    assert fw.ultima() is r


# ============================================================================ configuração e servidor
def test_endpoint_lan_recusa_o_que_o_notebook_nao_alcanca() -> None:
    assert RedeServidorCfg(endpoint_lan=" 192.168.1.81 ").endpoint_lan == LAN
    assert RedeServidorCfg(endpoint_lan="central.lan").endpoint_lan == "central.lan"
    assert RedeServidorCfg().endpoint_lan == ""
    for ruim in ("10.0.2.2", "127.0.0.1", "localhost", "0.0.0.0", "10.66.0.7", "169.254.1.1", "a;b", "::1"):
        with pytest.raises(ValidationError):
            RedeServidorCfg(endpoint_lan=ruim)


def test_endpoint_lan_nao_muda_a_assinatura_do_servidor() -> None:
    desejo = Desejo((Par("android-09", "pub", "10.66.0.2"),), ())
    assert desejo.assinatura(RedeServidorCfg(), "srv") == desejo.assinatura(RedeServidorCfg(endpoint_lan=LAN), "srv")
    assert desejo.assinatura(RedeServidorCfg(), "srv") != desejo.assinatura(RedeServidorCfg(porta_wireguard=51821),
                                                                            "srv")


def test_origem_do_aparelho_vem_do_external_do_gerenciador() -> None:
    assert rede_aplicacao.origem_do_aparelho(None) is None
    assert rede_aplicacao.origem_do_aparelho(SimpleNamespace(external=False, worker_id=None)) is None
    assert rede_aplicacao.origem_do_aparelho(SimpleNamespace(external=True, worker_id=WORKER)) == WORKER
    assert rede_aplicacao.origem_do_aparelho(SimpleNamespace(external=True, worker_id=None)) == "externo"


# ============================================================================ convergência (harness)
def _remoto(parque: Harness, monkeypatch: pytest.MonkeyPatch, *, leitura: dict | str | None,
            endpoint: str = LAN, iid: str = "android-02") -> tuple[AparelhoFalso, AparelhoFalso, Reinicios, list[str]]:
    """android-02 passa a ser do worker (o `external` do gerenciador, trocado só na origem: o laço de monitoramento
    do gerenciador não vê um externo que não existe); android-01 segue local. Os dois no servidor do central."""
    st = parque.state
    assert st is not None
    local, remoto = AparelhoFalso(id="android-01"), AparelhoFalso(id=iid, external=True, primeira_execucao=False)
    st.rede_convergencia._aparelho = lambda _s, rt: remoto if rt.id == iid else local
    real = rede_aplicacao.origem_do_aparelho
    monkeypatch.setattr(rede_aplicacao, "origem_do_aparelho",
                        lambda rt: WORKER if rt is not None and rt.id == iid else real(rt))
    st.rede_servidor.eh_remoto = lambda i: i == iid
    st.cfg.file.rede.servidor.endpoint_lan = endpoint
    leituras: list[str] = []

    def executar(script: str) -> str:
        leituras.append(script)
        if leitura == "erro":
            raise RuntimeError("prazo esgotado")
        return json.dumps(leitura)

    st.rede_servidor.firewall.executar = executar
    reinicios = Reinicios()
    monkeypatch.setattr(despacho, "pedir_ciclo_de_vida", reinicios)
    perfil = rede.criar_perfil(st, rede.ler_cadastro({"name": "Central", "kind": "vpn", "protocol": "singbox",
                                                      "endpoint_host": "10.0.2.2", "endpoint_port": 51820,
                                                      "params": {"servidor": "central"}}), "teste")
    rede.atribuir(st, rede.NetworkAssignBody(instance_ids=["android-01", iid], vpn_profile_id=perfil.id,
                                             policy="exigida"), "teste")
    return local, remoto, reinicios, leituras


def _peer(ap: AparelhoFalso) -> dict[str, object]:
    return json.loads(ap.baixado or b"{}")["endpoints"][0]["peers"][0]


async def test_remoto_disca_o_endereco_da_lan_e_recebe_pelo_adb_reverse(parque: Harness,
                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    st = parque.state
    assert st is not None
    liberado = _leitura([_regra("Central de Aparelhos - rede", "Allow", portas=["51820"],
                                programa=str(st.rede_servidor._binario()))])
    local, remoto, _, leituras = _remoto(parque, monkeypatch, leitura=liberado)
    assert await _passo(parque, "varredura", iid="android-01")
    assert await _passo(parque, "varredura", iid="android-02")
    await asyncio.sleep(0.3)
    assert _linha(parque, "android-01")["state"] == _linha(parque, "android-02")["state"] == "configurado"
    # O local segue o perfil (10.0.2.2 do emulador daqui); o remoto, a LAN — e o perfil chegou pelo adb reverse.
    assert (_peer(local)["address"], _peer(local)["port"]) == ("10.0.2.2", 51820)
    assert (_peer(remoto)["address"], _peer(remoto)["port"]) == (LAN, 51820)
    assert [r for r, _ in remoto.reversos] == ["reverse", "remove"] and local.reversos == []
    assert len(leituras) == 1                                                      # só o remoto lê o firewall
    # A evidência da aplicação fica no desfecho do comando `device.network` (a linha já diz o reinício pedido).
    [aplicou] = st.db.query("SELECT result FROM commands WHERE verb='device.network' AND instance_id='android-02'")
    detalhe = json.loads(aplicou["result"])["outcome"]["evidence"]
    assert f"{LAN}:51820 (servidor do central, pela LAN: aparelho de {WORKER})" in detalhe
    assert "firewall do central para a LAN: liberado" in detalhe and "adb reverse" in detalhe
    servidor = st.rede_servidor.status()
    assert {p["instance_id"]: p["remote"] for p in servidor["peers"]} == {"android-01": False, "android-02": True}
    acesso = servidor["remote_access"]
    assert acesso["lan_endpoint"] == LAN and acesso["remote_peers"] == ["android-02"]
    assert acesso["firewall"]["state"] == "liberado" and acesso["firewall"]["commands"] == []
    # O túnel "no ar" no aparelho sem conexão no log do servidor: o remoto diz o caminho pela LAN.
    assert "firewall do central liberado" in st.rede_convergencia._par_no_servidor("android-02")
    assert "firewall" not in st.rede_convergencia._par_no_servidor("android-01")


async def test_firewall_fechado_recusa_o_remoto_com_o_comando_do_dono(parque: Harness,
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    st = parque.state
    assert st is not None
    local, remoto, reinicios, _ = _remoto(parque, monkeypatch, leitura=_leitura())
    assert await _passo(parque, "varredura", iid="android-02")
    linha = _linha(parque, "android-02")
    assert linha["state"] == "pendente" and remoto.comandos == []                  # nada tocou o aparelho
    erro = str(linha["error"])
    assert erro.startswith(f"aparelho de {WORKER}: firewall do central sem_regra para o UDP 51820 da LAN")
    assert "O dono roda, num PowerShell de administrador do central, e pede Reaplicar: New-NetFirewallRule" in erro
    # O comando inteiro (o caminho do pytest é longo; o do central cabe nos 500 do `error`) está no `remote_access`.
    assert st.rede_servidor.acesso_remoto()["firewall"]["commands"] == [
        comando_de_liberacao(st.rede_servidor._binario(), 51820, "Public")]
    real = ("aparelho de worker-lan-01: firewall do central sem_regra para o UDP 51820 da LAN (perfil Public). O dono "
            "roda, num PowerShell de administrador do central, e pede Reaplicar: "
            + comando_de_liberacao(BIN, 51820, "Public"))
    assert len(real) < 500
    [cmd] = st.db.query("SELECT * FROM commands WHERE verb='device.network'")
    assert cmd["state"] == "failed" and "New-NetFirewallRule" in str(cmd["reason"])
    # O local não depende do firewall e segue.
    assert await _passo(parque, "varredura", iid="android-01")
    assert _linha(parque, "android-01")["state"] == "configurado"
    await asyncio.sleep(0.3)
    assert [i for i, _ in reinicios.pedidos] == ["android-01"]                     # o remoto não reinicia à toa


async def test_leitura_desconhecida_segue_com_a_nota(parque: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    _local, remoto, _, _ = _remoto(parque, monkeypatch, leitura="erro")
    assert await _passo(parque, "varredura", iid="android-02")
    linha = _linha(parque, "android-02")
    assert linha["state"] == "configurado" and "firewall do central para a LAN: desconhecido" in str(linha["detail"])
    assert _peer(remoto)["address"] == LAN


async def test_sem_endpoint_lan_o_remoto_e_recusado_antes_do_par(parque: Harness,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    st = parque.state
    assert st is not None
    _local, remoto, _, leituras = _remoto(parque, monkeypatch, leitura=_leitura(), endpoint="")
    assert await _passo(parque, "varredura", iid="android-02")
    linha = _linha(parque, "android-02")
    assert linha["state"] == "pendente" and "rede.servidor.endpoint_lan está vazio" in str(linha["error"])
    assert st.db.one("SELECT 1 FROM network_keys WHERE owner='android-02'") is None
    assert remoto.comandos == [] and leituras == []


async def test_rotas_mostram_e_releem_o_acesso_remoto_sem_segredo(parque: Harness,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    st = parque.state
    assert st is not None
    _remoto(parque, monkeypatch, leitura=_leitura())
    assert await _passo(parque, "varredura", iid="android-01")                     # o servidor sobe com o local
    app = create_app(parque.cfg, state=st)
    app.state.poc = st
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/network/server")
        assert r.status_code == 200, r.text
        assert r.json()["remote_access"]["firewall"] is None                       # o GET não roda PowerShell
        r = await c.post("/api/network/server/firewall-check")
        assert r.status_code == 200, r.text
        corpo = r.json()
        assert corpo["lan_endpoint"] == LAN and corpo["wireguard_udp_port"] == 51820
        assert corpo["firewall"]["state"] == "sem_regra" and corpo["firewall"]["profile"] == "Public"
        assert corpo["firewall"]["commands"][0].startswith("New-NetFirewallRule")
        r = await c.get("/api/network/server")
        assert r.json()["remote_access"]["firewall"]["state"] == "sem_regra"      # agora do cache
    ref = st.db.one("SELECT secret_ref FROM network_keys WHERE owner='servidor'")["secret_ref"]
    assert st.secrets.get_secret(ref) not in r.text


async def test_estado_liga_o_remoto_ao_external_do_gerenciador(parque: Harness) -> None:
    st = parque.state
    assert st is not None
    rt = st.devices.devices["android-03"]
    assert st.rede_servidor.eh_remoto("android-03") is False and st.rede_servidor.eh_remoto("nao-existe") is False
    antes = rt.external
    rt.external = True                                                             # sem await: o monitor não vê
    try:
        assert st.rede_servidor.eh_remoto("android-03") is True
    finally:
        rt.external = antes
