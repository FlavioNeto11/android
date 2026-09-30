"""Rede por aparelho, item 25.7 (ADR-056): aparelhos de OUTRA máquina (o notebook do worker) no servidor do central.

O que se prova aqui (tudo `simulated`: aparelho falso, processo falso no lugar do sing-box, leitura do firewall
trocada por um dublê que devolve o JSON que o PowerShell devolveria; nada de adb, emulador, firewall ou rede):
- o aparelho remoto recebe o perfil pelo `adb reverse` (o túnel do adb já o alcança; nenhuma rota do host muda) e
  disca o servidor pelo `rede.servidor.endpoint_lan` e a porta real do servidor — nunca pelo 10.0.2.2 do perfil, que
  no notebook é o próprio notebook; o emulador local segue com o 10.0.2.2;
- sem `endpoint_lan`, o remoto é recusado dizendo a chave, antes de gerar par;
- o firewall do central só é LIDO: o script não tem verbo que escreva; o estado sai da ordem do Windows (perfil
  desligado não filtra; bloqueio vence permissão; sem regra vale a entrada padrão) e vem com o comando EXATO do dono;
- a leitura julga cada regra contra o que o pacote do notebook encontra de fato (29.8): porta UDP, perfil efetivo da
  interface do `endpoint_lan`, essa interface e uma origem que contenha a LAN dela; regra de outra interface ou de
  outra sub-rede não libera; `-Program` de outra versão do sing-box é `regra_obsoleta`; origem `Any` libera com aviso;
- o comando proposto não leva `-Program`, leva a sub-rede calculada do endereço e do prefixo lidos, a interface e
  `-Profile Any`, e é idempotente; vêm junto a inspeção e a reversão; o que o sistema não informou vira marcador;
- firewall fechado (`bloqueado`/`sem_regra`/`regra_obsoleta`) recusa a aplicação no remoto com o comando no erro;
  `desconhecido` segue com a nota; o `GET /api/network/server` e o `POST …/firewall-check` mostram o `remote_access`;
- o endereço da LAN não entra na assinatura do servidor (trocá-lo não derruba os outros pares).

A prova real (um aparelho do notebook com o túnel fechado no log do servidor) é `not_run`: depende da regra de
firewall que só o dono cria (docs/dominios/parque.md, "Aparelhos do worker"). O leitor rodou de verdade, só leitura,
no central (30/09: `sem_regra`); a regra criada e lida como `liberado` também é `not_run`.
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
from app.devices import rede_firewall
from app.devices.rede_firewall import (FirewallDoCentral, LeituraDoFirewall, avaliar, comando_de_desbloqueio,
                                       comando_de_liberacao, script_de_leitura)
from app.devices.rede_servidor import Desejo, Par
from app.main import create_app

from .conftest import Harness
from .test_rede_aplicacao import AparelhoFalso, Reinicios, _linha, _passo, parque  # noqa: F401 - `parque` é fixture

BIN = Path(r"C:\git\android\data\rede\sing-box-1.14.2-windows-amd64\sing-box.exe")
LAN = "192.168.1.81"
WORKER = "worker-lan-01"


def _perfis(ligado: str = "True", entrada: str = "Block") -> list[dict[str, str]]:
    return [{"nome": n, "ligado": ligado, "entrada": entrada} for n in ("Domain", "Private", "Public")]


def _rede(interface: str = "Wi-Fi", categoria: str = "Public", ip: str = LAN, prefixo: int | None = 24,
          outros: tuple[str, ...] = ("fe80::93b1:bb26:6d64:2dde%19",)) -> dict[str, object]:
    """Uma interface como o script a devolve: `ips` (todos os endereços, como sempre) e `ipv4` (endereço e prefixo —
    é dele que sai a sub-rede da regra). `prefixo=None` = o sistema não informou o prefixo."""
    rede: dict[str, object] = {"interface": interface, "categoria": categoria, "ips": [*outros, ip]}
    if prefixo is not None:
        rede["ipv4"] = [{"ip": ip, "prefixo": prefixo}]
    return rede


def _leitura(regras: object = None, *, perfis: object = None, redes: object = None) -> dict[str, object]:
    """O formato que o script devolve — o desta máquina em 30/09 (leitura real, só leitura): Wi-Fi Public com
    192.168.1.81/24, os três perfis ligados com entrada Block e nenhuma regra para o sing-box nem para a UDP 51820.
    (O IPv6 global que a Wi-Fi de verdade tem fica de fora do molde: entra só no caso que fala dele.)"""
    return {"binario": str(BIN), "porta": 51820, "perfis": _perfis() if perfis is None else perfis,
            "redes": [_rede()] if redes is None else redes,
            "regras": [] if regras is None else regras}


def _regra(nome: str, acao: str, *, programa: str = str(BIN), portas: object = ("Any",), protocolo: str = "UDP",
           perfis: str = "Any", habilitada: str = "True", direcao: str = "Inbound", origem: str = "Local",
           remoto: object = ("Any",), local: object = ("Any",), interfaces: object = ("Any",),
           tipo_interface: str = "Any") -> dict:
    """Uma regra como o script a devolve. `origem` é de onde a regra veio (Local ou política de grupo); `remoto` é o
    `RemoteAddress`, `local` o `LocalAddress` e `interfaces` o `InterfaceAlias` — `Any` é o que o Windows devolve
    para a regra criada sem o parâmetro."""
    def lista(valor: object) -> object:
        return valor if isinstance(valor, str) else list(valor)                   # type: ignore[call-overload]

    return {"nome": nome, "exibicao": nome, "habilitada": habilitada, "direcao": direcao, "acao": acao,
            "perfis": perfis, "origem": origem, "protocolo": protocolo, "portas": lista(portas), "programa": programa,
            "remoto": lista(remoto), "local": lista(local), "interfaces": lista(interfaces),
            "tipo_interface": tipo_interface}


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
    # 29.8: sem `-Program`, com a sub-rede e a interface lidas do sistema, `-Profile Any` e a troca da regra de mesmo
    # nome antes de criar (antes: `New-NetFirewallRule … -Program '<sing-box com a versão>' -RemoteAddress LocalSubnet
    # -Profile Public`).
    assert r.comandos == [
        "$n='Central de Aparelhos - rede por aparelho (WireGuard UDP 51820)'; "
        "Get-NetFirewallRule -DisplayName $n -EA 0 | Remove-NetFirewallRule; "
        "New-NetFirewallRule -DisplayName $n -Direction Inbound -Action Allow -Protocol UDP -LocalPort 51820 "
        "-RemoteAddress 192.168.1.0/24 -InterfaceAlias 'Wi-Fi' -Profile Any"]
    # Sem endereço configurado não há interface nem sub-rede a ler: marcadores, e a frase diz o que faltou.
    sem = avaliar(_leitura(), binario=BIN, porta=51820, endpoint=None)
    assert sem.comandos[0].endswith("-Profile Any") and sem.faltou == ["interface", "sub-rede"]
    assert "<SUB-REDE-IPV4-DA-LAN>" in sem.comandos[0] and "endpoint_lan está vazio" in sem.detalhe


def test_bloqueio_vence_a_permissao_e_o_comando_desliga_a_regra() -> None:
    bloqueio = _regra("UDP Query User{A1B2}C:\\...\\sing-box.exe", "Block", perfis="Public")
    permissao = _regra("Central de Aparelhos - rede", "Allow", portas=["51820"])
    r = avaliar(_leitura([bloqueio, permissao]), binario=BIN, porta=51820, endpoint=LAN)
    assert r.estado == "bloqueado" and r.regras_que_liberam == ["Central de Aparelhos - rede"]
    assert r.comandos == [comando_de_desbloqueio("UDP Query User{A1B2}C:\\...\\sing-box.exe")]
    assert r.comandos[0].startswith("Disable-NetFirewallRule -Name 'UDP Query User")
    # Só o bloqueio (o que o Windows cria quando o aviso fica sem resposta): desligar E criar a permissão.
    so = avaliar(_leitura(bloqueio), binario=BIN, porta=51820, endpoint=LAN)       # objeto solto (PowerShell 5.1)
    assert so.estado == "bloqueado" and so.comandos[1] == comando_de_liberacao(51820, sub_rede="192.168.1.0/24",
                                                                               interface="Wi-Fi")
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
    assert estado(_regra("x", "Allow", programa=r"C:\outro\wireguard.exe")) == "sem_regra"
    # O MESMO executável em outro caminho segue fechado, agora com estado próprio (29.8; antes: `sem_regra`).
    assert estado(_regra("x", "Allow", programa=r"C:\outro\sing-box.exe")) == "regra_obsoleta"
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


# ============================================================================ porta, interface, origem e perfil EFETIVOS (29.8)
NOME = "Central de Aparelhos - rede por aparelho (WireGuard UDP 51820)"
SUB_REDE = "192.168.1.0/24"
ANTIGO = r"C:\git\android\data\rede\sing-box-1.13.0-windows-amd64\sing-box.exe"


def _avaliar(*regras: dict, endpoint: str | None = LAN, redes: object = None) -> LeituraDoFirewall:
    return avaliar(_leitura(list(regras), redes=redes), binario=BIN, porta=51820, endpoint=endpoint)


def test_script_le_origem_interface_e_prefixo_de_cada_regra_e_de_cada_endereco() -> None:
    script = script_de_leitura(BIN, 51820)
    for leitor in ("Get-NetFirewallPortFilter", "Get-NetFirewallAddressFilter", "Get-NetFirewallInterfaceFilter",
                   "Get-NetFirewallInterfaceTypeFilter", "Get-NetFirewallApplicationFilter", "Get-NetIPAddress",
                   "Get-NetConnectionProfile", "Get-NetFirewallProfile"):
        assert leitor in script, leitor
    for campo in ("RemoteAddress", "LocalAddress", "InterfaceAlias", "InterfaceType", "PrefixLength"):
        assert campo in script, campo
    for verbo in ("New-Net", "Set-Net", "Enable-Net", "Disable-Net", "Remove-Net", "netsh", "Rename-Net"):
        assert verbo not in script, verbo


def test_comando_proposto_sem_program_com_a_sub_rede_calculada_e_idempotente() -> None:
    r = _avaliar()
    assert r.estado == "sem_regra" and (r.sub_rede, r.interface, r.perfil) == (SUB_REDE, "Wi-Fi", "Public")
    [comando] = r.comandos
    assert comando == rede_firewall.comando_de_liberacao(51820, sub_rede=SUB_REDE, interface="Wi-Fi")
    # Sem `-Program`: o caminho do sing-box tem a versão no nome e a regra morreria na atualização.
    assert "-Program" not in comando and "sing-box" not in comando
    assert "-Protocol UDP -LocalPort 51820 -RemoteAddress 192.168.1.0/24 -InterfaceAlias 'Wi-Fi'" in comando
    assert comando.endswith("-Profile Any")                    # sobrevive à reclassificação da rede (Public↔Private)
    # Idempotente: tira a regra de mesmo nome ANTES de criar, numa linha só; rodar duas vezes deixa uma regra.
    assert "\n" not in comando and comando.count("New-NetFirewallRule") == 1
    assert comando.index("Get-NetFirewallRule -DisplayName $n -EA 0 | Remove-NetFirewallRule;") \
        < comando.index("New-NetFirewallRule -DisplayName $n ")
    assert comando.startswith(f"$n='{NOME}'; ")
    # A sub-rede e a interface saem do que o sistema informou (endereço e prefixo), não de um literal.
    outra = [_rede("Ethernet d'o dono", "Private", "10.20.30.40", 20)]
    r = _avaliar(endpoint="10.20.30.40", redes=outra)
    assert (r.sub_rede, r.interface, r.perfil, r.faltou) == ("10.20.16.0/20", "Ethernet d'o dono", "Private", [])
    assert "-RemoteAddress 10.20.16.0/20 -InterfaceAlias 'Ethernet d''o dono' -Profile Any" in r.comandos[0]
    assert "192.168." not in r.comandos[0]
    # A porta é a configurada (e o nome da regra a carrega).
    assert "(WireGuard UDP 51821)'; " in rede_firewall.comando_de_liberacao(51821, sub_rede=SUB_REDE, interface="Wi-Fi")


def test_inspecao_e_reversao_acompanham_toda_leitura() -> None:
    inspecao, reversao = rede_firewall.comando_de_inspecao(51820), rede_firewall.comando_de_reversao(51820)
    assert reversao == f"Remove-NetFirewallRule -DisplayName '{NOME}'"
    # A inspeção só lê, e mostra o que decide a leitura: porta, origem, interface (e o programa, se houver).
    assert inspecao.startswith(f"Get-NetFirewallRule -DisplayName '{NOME}'")
    for verbo in ("New-Net", "Set-Net", "Enable-Net", "Disable-Net", "Remove-Net", "netsh"):
        assert verbo not in inspecao, verbo
    for leitor in ("Get-NetFirewallPortFilter", "Get-NetFirewallAddressFilter", "Get-NetFirewallInterfaceFilter",
                   "Get-NetFirewallApplicationFilter"):
        assert leitor in inspecao, leitor
    # Fechado ou liberado, a leitura as carrega — fora de `comandos`, que é só o que FALTA rodar (e vai no `error`).
    for r in (_avaliar(), _avaliar(_regra(NOME, "Allow", programa="Any", portas=["51820"], remoto=[SUB_REDE],
                                          interfaces=["Wi-Fi"]))):
        assert (r.inspecao, r.reversao) == (inspecao, reversao)
        assert inspecao not in r.comandos and reversao not in r.comandos
        d = r.como_dict()
        assert (d["inspect_command"], d["revert_command"], d["lan_subnet"]) == (inspecao, reversao, SUB_REDE)


def test_regra_restrita_a_sub_rede_e_a_interface_certas_e_liberado() -> None:
    def regra(**kw: object) -> dict:
        return _regra(NOME, "Allow", programa="Any", portas=["51820"], **{"interfaces": ["Wi-Fi"], **kw})  # type: ignore[arg-type]

    # A regra proposta, como o ActiveStore a devolve (a máscara por extenso), e as formas equivalentes.
    r = _avaliar(regra(remoto=["192.168.1.0/255.255.255.0"]))
    assert r.estado == "liberado" and not r.fechado and r.comandos == [] and r.avisos == []
    assert r.regras_que_liberam == [NOME] and "192.168.1.0/24" in r.detalhe and "Wi-Fi" in r.detalhe
    for origem in (["192.168.1.0/24"], ["192.168.1.0-192.168.1.255"], ["192.168.0.0/16"], ["LocalSubnet4"],
                   ["10.0.0.0/8", "192.168.1.0/24"]):
        assert _avaliar(regra(remoto=origem)).estado == "liberado", origem
    assert _avaliar(regra(remoto=[SUB_REDE], interfaces=["Any"])).estado == "liberado"
    assert _avaliar(regra(remoto=[SUB_REDE], interfaces=["wi-fi"])).estado == "liberado"      # o alias não tem caixa
    assert _avaliar(regra(remoto=[SUB_REDE], local=[LAN])).estado == "liberado"
    # `-Profile Any` é o que sobrevive à reclassificação: a mesma regra vale com a Wi-Fi em Private.
    r = _avaliar(regra(remoto=[SUB_REDE]), redes=[_rede(categoria="Private")])
    assert (r.estado, r.perfil) == ("liberado", "Private")


def test_regra_de_outra_interface_ou_de_outra_sub_rede_e_fechado() -> None:
    def fechada(motivo: str, **kw: object) -> LeituraDoFirewall:
        r = _avaliar(_regra("regra do dono", "Allow", programa="Any", portas=["51820"], **kw))  # type: ignore[arg-type]
        assert r.estado == "sem_regra" and r.fechado and r.regras_que_liberam == [], kw
        assert "regra do dono" in r.detalhe and motivo in r.detalhe, r.detalhe
        assert r.comandos == [rede_firewall.comando_de_liberacao(51820, sub_rede=SUB_REDE, interface="Wi-Fi")]
        return r

    # Outra interface: o pacote do notebook chega pela Wi-Fi, e a regra só vale no cabo (ou no vEthernet do WSL).
    fechada("interface", interfaces=["Ethernet"])
    fechada("interface", interfaces=["vEthernet (WSL (Hyper-V firewall))"], remoto=["LocalSubnet"])
    # Outra sub-rede, só IPv6, ou uma palavra-chave que não é a LAN.
    fechada("origem", remoto=["10.0.0.0/8"])
    fechada("origem", remoto=["192.168.2.0/255.255.255.0"])
    fechada("origem", remoto=["LocalSubnet6"])
    fechada("origem", remoto=["fe80::/64"])
    fechada("origem", remoto=["DefaultGateway"])
    # Só um pedaço da LAN (um endereço, meia sub-rede): não contém a LAN do endpoint, e a frase diz que é parte.
    assert "parte" in fechada("origem", remoto=["192.168.1.19"]).detalhe
    assert "parte" in fechada("origem", remoto=["192.168.1.0/25"]).detalhe
    # Outro endereço LOCAL: a regra vale para um IP que não é o endpoint.
    fechada("endereço local", local=["192.168.1.50"], remoto=[SUB_REDE])
    # O bloqueio segue a mesma leitura: o de outra interface não bloqueia a Wi-Fi; o que pega um pedaço da LAN, sim.
    boa = _regra(NOME, "Allow", programa="Any", portas=["51820"], remoto=[SUB_REDE], interfaces=["Wi-Fi"])
    assert _avaliar(boa, _regra("bloqueio do cabo", "Block", interfaces=["Ethernet"])).estado == "liberado"
    assert _avaliar(boa, _regra("bloqueio de fora", "Block", programa="Any", remoto=["10.0.0.0/8"])).estado == "liberado"
    r = _avaliar(boa, _regra("bloqueio do notebook", "Block", programa="Any", remoto=["192.168.1.19"]))
    assert r.estado == "bloqueado" and r.regras_que_bloqueiam == ["bloqueio do notebook"]


def test_regra_com_program_de_outra_versao_e_obsoleta_e_fechada() -> None:
    # A regra da proposta antiga: `-Program` com a versão no caminho. O sing-box foi atualizado e o Windows descarta.
    antiga = _regra(NOME, "Allow", programa=ANTIGO, portas=["51820"], remoto=["LocalSubnet"], perfis="Public")
    r = _avaliar(antiga)
    assert r.estado == "regra_obsoleta" and r.fechado and r.regras_que_liberam == []
    assert r.regras_obsoletas == [NOME] and r.como_dict()["stale_rules"] == [NOME]
    assert ANTIGO in r.detalhe and "binário atual" in r.detalhe and str(BIN) in r.detalhe
    # O comando é o mesmo de sempre: por tirar a regra de mesmo nome antes de criar, ele TROCA a obsoleta.
    assert r.comandos == [rede_firewall.comando_de_liberacao(51820, sub_rede=SUB_REDE, interface="Wi-Fi")]
    # O mesmo executável em outro caminho, com outro nome de regra: obsoleta também (é o sing-box de outra pasta).
    assert _avaliar(_regra("sing-box", "Allow", programa=r"D:\antigo\SING-BOX.EXE")).estado == "regra_obsoleta"
    # Regra de OUTRO programa que abre UDP em qualquer porta (TeamViewer, Teams: há várias no central) não é nossa:
    # não libera o sing-box e não é "obsoleta".
    alheia = _regra("TeamViewer Remote Control Application", "Allow", perfis="Public",
                    programa=r"C:\Program Files\TeamViewer\TeamViewer.exe")
    r = _avaliar(alheia)
    assert r.estado == "sem_regra" and r.regras_obsoletas == [] and "TeamViewer" not in r.detalhe
    # Com uma regra boa ao lado, a obsoleta não fecha nada (só fica dita).
    boa = _regra("regra nova", "Allow", programa="Any", portas=["51820"], remoto=[SUB_REDE], interfaces=["Wi-Fi"])
    r = _avaliar(antiga, boa)
    assert r.estado == "liberado" and r.regras_que_liberam == ["regra nova"] and r.regras_obsoletas == [NOME]
    # Bloqueio que aponta para o executável antigo não bloqueia o de hoje.
    assert _avaliar(boa, _regra("UDP Query User{A1B2}", "Block", programa=ANTIGO)).estado == "liberado"


def test_origem_any_e_liberado_com_aviso_de_que_esta_aberta_demais() -> None:
    r = _avaliar(_regra(NOME, "Allow", programa="Any", portas=["51820"], remoto=["Any"], interfaces=["Wi-Fi"]))
    assert r.estado == "liberado" and not r.fechado and r.comandos == []
    [aviso] = r.avisos
    assert NOME in aviso and "qualquer origem" in aviso and "IPv6" in aviso and SUB_REDE in aviso
    assert r.como_dict()["warnings"] == [aviso] and "mais aberta que o necessário" in r.detalhe
    # Medido no central: a Wi-Fi tem IPv6 global e o servidor escuta em [::] — a frase deixa de ser hipótese.
    com_v6 = [_rede(outros=("fe80::93b1:bb26:6d64:2dde%19", "2001:db8:1234::81"))]
    r = _avaliar(_regra(NOME, "Allow", programa="Any", portas=["51820"]), redes=com_v6)
    assert r.estado == "liberado" and "a interface Wi-Fi tem IPv6 global" in r.avisos[0]
    assert "2001:db8" not in r.detalhe and "2001:db8" not in r.avisos[0]           # o endereço em si não vai à frase


def test_endpoint_que_nao_e_de_nenhuma_interface_e_explicado_e_vira_placeholder() -> None:
    r = _avaliar(endpoint="192.168.1.10")
    assert r.endpoint_local is False and (r.interface, r.sub_rede, r.perfil) == (None, None, None)
    assert r.estado == "sem_regra" and r.faltou == ["interface", "sub-rede"]
    # O porquê, com o que a máquina tem de fato (é o que o dono põe no endpoint_lan).
    assert "192.168.1.10 não é endereço de nenhuma interface do central" in r.detalhe
    assert "rede.servidor.endpoint_lan" in r.detalhe and "Wi-Fi 192.168.1.81/24" in r.detalhe
    # O comando não inventa: o que o sistema não informou vira um marcador que o PowerShell recusa (`<` é reservado).
    [comando] = r.comandos
    assert "-RemoteAddress <SUB-REDE-IPV4-DA-LAN> -InterfaceAlias '<INTERFACE-DA-LAN>' -Profile Any" in comando
    assert "192.168." not in comando and r.como_dict()["missing"] == ["interface", "sub-rede"]
    # Uma regra restrita não pode ser conferida sem saber a interface e a LAN: não prova nem nega.
    restrita = _regra(NOME, "Allow", programa="Any", portas=["51820"], remoto=[SUB_REDE], interfaces=["Wi-Fi"])
    r = _avaliar(restrita, endpoint="192.168.1.10")
    assert r.estado == "desconhecido" and not r.fechado and r.regras_que_liberam == []
    assert "não é endereço de nenhuma interface" in r.detalhe and NOME in r.detalhe
    # Nome em vez de IP: a leitura não resolve nomes, e diz.
    r = _avaliar(endpoint="central.lan")
    assert r.endpoint_local is None and r.faltou == ["interface", "sub-rede"] and "central.lan" in r.detalhe
    assert "<SUB-REDE-IPV4-DA-LAN>" in r.comandos[0]
    # A interface veio, o prefixo não: só a sub-rede vira marcador.
    r = _avaliar(redes=[_rede(prefixo=None)])
    assert (r.interface, r.sub_rede, r.faltou) == ("Wi-Fi", None, ["sub-rede"]) and "prefixo" in r.detalhe
    assert "-RemoteAddress <SUB-REDE-IPV4-DA-LAN> -InterfaceAlias 'Wi-Fi' -Profile Any" in r.comandos[0]
    # Com a interface e sem a LAN, a regra de sub-rede explícita não pode ser conferida; a de `LocalSubnet`, sim.
    assert _avaliar(restrita, redes=[_rede(prefixo=None)]).estado == "desconhecido"
    assert _avaliar(_regra("r", "Allow", programa="Any", remoto=["LocalSubnet"]),
                    redes=[_rede(prefixo=None)]).estado == "liberado"
    # Endpoint IPv6 (a config aceita): a interface é achada, mas a sub-rede da regra é IPv4 e fica por preencher.
    v6 = [_rede(outros=("fe80::93b1:bb26:6d64:2dde%19", "2001:db8:1234::81"))]
    r = _avaliar(endpoint="2001:db8:1234::81", redes=v6)
    assert (r.endpoint_local, r.interface, r.perfil, r.faltou) == (True, "Wi-Fi", "Public", ["sub-rede"])
    assert "é IPv6" in r.detalhe and _avaliar(restrita, endpoint="2001:db8:1234::81", redes=v6).estado == "desconhecido"
    # A interface do endpoint sem perfil de conexão (o sistema não classificou a rede): só vale a regra de todos os perfis.
    sem_perfil = [_rede(categoria="")]
    assert _avaliar(_regra("r", "Allow", programa="Any", perfis="Public"), redes=sem_perfil).estado == "desconhecido"
    r = _avaliar(_regra("r", "Allow", programa="Any", perfis="Any", remoto=[SUB_REDE]), redes=sem_perfil)
    assert (r.estado, r.perfil) == ("liberado", None) and "não informou o perfil" in r.detalhe


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
    # 29.8: o comando deixou de levar o caminho do binário (antes começava por `New-NetFirewallRule` e terminava em
    # `-Program … -Profile Public`); agora é o mesmo aqui e no central, e cabe INTEIRO nos 500 do `error`.
    comando = comando_de_liberacao(51820, sub_rede="192.168.1.0/24", interface="Wi-Fi")
    assert erro == ("aparelho de worker-lan-01: firewall do central sem_regra para o UDP 51820 da LAN (perfil Public). "
                    "O dono roda, num PowerShell de administrador do central, e pede Reaplicar: " + comando)
    assert len(erro) < 500 and "-Program" not in erro and "New-NetFirewallRule -DisplayName $n" in erro
    acesso = st.rede_servidor.acesso_remoto()["firewall"]
    assert acesso["commands"] == [comando] and acesso["lan_subnet"] == "192.168.1.0/24"
    assert acesso["inspect_command"].startswith("Get-NetFirewallRule -DisplayName '")
    assert acesso["revert_command"].startswith("Remove-NetFirewallRule -DisplayName '")
    [cmd] = st.db.query("SELECT * FROM commands WHERE verb='device.network'")
    assert cmd["state"] == "failed" and "New-NetFirewallRule" in str(cmd["reason"])
    # O local não depende do firewall e segue.
    assert await _passo(parque, "varredura", iid="android-01")
    assert _linha(parque, "android-01")["state"] == "configurado"
    await asyncio.sleep(0.3)
    assert [i for i, _ in reinicios.pedidos] == ["android-01"]                     # o remoto não reinicia à toa


async def test_regra_obsoleta_recusa_o_remoto_e_o_erro_cabe(parque: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    """O estado novo (29.8) é fechado para a convergência como os outros dois: recusa, com o comando que troca a regra."""
    st = parque.state
    assert st is not None
    antiga = _regra(NOME, "Allow", programa=ANTIGO, portas=["51820"], remoto=["LocalSubnet"], perfis="Public")
    _local, remoto, _, _ = _remoto(parque, monkeypatch, leitura=_leitura([antiga]))
    assert await _passo(parque, "varredura", iid="android-02")
    linha = _linha(parque, "android-02")
    assert linha["state"] == "pendente" and remoto.comandos == []                  # nada tocou o aparelho
    erro = str(linha["error"])
    assert erro.startswith(f"aparelho de {WORKER}: firewall do central regra_obsoleta para o UDP 51820 da LAN")
    assert erro.endswith(comando_de_liberacao(51820, sub_rede="192.168.1.0/24", interface="Wi-Fi")) and len(erro) < 500
    acesso = st.rede_servidor.acesso_remoto()["firewall"]
    assert acesso["state"] == "regra_obsoleta" and acesso["stale_rules"] == [NOME] and ANTIGO in acesso["detail"]


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
        # 29.8: o comando troca a regra de mesmo nome antes de criar (antes começava por `New-NetFirewallRule`).
        assert corpo["firewall"]["commands"] == [comando_de_liberacao(51820, sub_rede="192.168.1.0/24",
                                                                      interface="Wi-Fi")]
        assert "New-NetFirewallRule" in corpo["firewall"]["commands"][0]
        assert corpo["firewall"]["inspect_command"] and corpo["firewall"]["revert_command"]
        assert corpo["firewall"]["missing"] == [] and corpo["firewall"]["warnings"] == []
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
