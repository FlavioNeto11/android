"""Rede por aparelho, item 25.5 (ADR-056): a sonda de saída de dentro do aparelho.

O que se prova aqui (tudo `simulated`: aparelho falso no lugar do adb e processo falso no lugar do sing-box; nenhum
emulador, nenhuma VPN, nenhum eco de IP de verdade):
- a LEITURA das saídas no formato medido no 25.1 (29/09, android-05; relatório `data/rede/piloto/medicao-25.1.md`):
  a contabilidade do `dumpsys netstats detail` por UID (Chrome coberto com o tipo 17 = tipo 1; o uid 0 com 868 B na
  física e 52 B na VPN — vazamento; o cliente VPN só na física), o eco HTTP/1.0 do `nc`, o `Permission denied` do
  bloqueio, o `Timeout` com o servidor parado, o IPv6 preso no túnel e o DNS da VPN (172.19.0.2, o hijack do
  sing-box), com o UDP de DNS passando e o NTP se perdendo (a cadeia com SOCKS5). Os valores são os do relatório;
  o IP público é um endereço qualquer (o do central não mora em teste). Não há dump bruto guardado no piloto: as
  saídas foram remontadas no formato real com os números medidos;
- a sonda: tudo como uid 2000 (root é recusado), cobertura por UID dos apps exigidos (Instagram e Outlook), o shell
  como a própria sonda, a janela dos apps acumulada desde a conexão, e o teste de vazamento com o CLIENTE VPN parado
  (sem `tun0`): só o `Permission denied` prova o bloqueio, um IP é vazamento, o `Timeout` não prova nada; depois do
  `force-stop` nada levanta; parar e sondar vão numa ida só, com novas tentativas, e o always-on que religa o cliente
  na hora (android-05, 29/09) não esconde mais o bloqueio;
- a convergência: `conectado` → verificar → `trafego_verificado` ou `parcial`; deriva no meio regride sem medir; o
  teste de vazamento vem antes da medição, uma vez por revisão, e o cliente parado é religado pelo boot (a linha volta
  a `configurado` e reinicia); o servidor do central não é tocado, e o servidor externo também tem o vazamento medido;
  a tarefa segurada pela porta abre o app parado; o pedido de verificar é atendido no próximo ponto seguro;
- a comparação entre aparelhos: o mesmo IP medido gera aviso (evento `warn`, `detail` e `egress_shared_with`).
"""
from __future__ import annotations

import asyncio
import base64
import json
import secrets as pysecrets
from dataclasses import dataclass, field

import httpx
import pytest

from app.commands import despacho
from app.config import RedeSondaCfg
from app.devices import rede
from app.devices.rede_aplicacao import RedeAplicacaoError
from app.devices.rede_medicao import Vazamento, medir, sondar_vazamento
from app.devices.sonda_rede import (CONSULTA_DNS, PEDIDO_NTP, cobertura, comando_abrir_app, comando_dns_e_udp,
                                    comando_ip_de_saida, comando_parar_e_sondar, host_valido, ler_dns_e_udp,
                                    ler_ip_de_saida, ler_netstats, ler_parar_e_sondar, ler_uids)
from app.main import create_app
from app.modules.identity.presentation.schemas import ProfileCreate

from .conftest import Harness
from .test_rede_aplicacao import (PKG, AparelhoFalso, Reinicios, _envelhecer_configuracao, _linha, _passo,  # noqa: F401
                                  parque)

IP = "45.162.8.9"                     # um IP público qualquer no lugar do medido (o do central não vai para teste)
IP_FISICO = "45.162.8.10"             # a saída pela física, sem VPN (o vazamento), também fictícia
INSTAGRAM, OUTLOOK = "com.instagram.android", "com.microsoft.office.outlook"

_WIFI = '{type=1, ratType=COMBINED, subId=-1, wifiNetworkKey="AndroidWifi", metered=false, defaultNetwork=true, ' \
        'oemManaged=OEM_NONE}'
_VPN = "{type=17, ratType=-1, metered=false, defaultNetwork=false, oemManaged=OEM_NONE}"


def _bloco(tipo: int, uid: int, rx: int, tx: int, *, tag: str = "0x0", st: int = 1790006400) -> list[str]:
    return [f"  ident=[{_VPN if tipo == 17 else _WIFI}] uid={uid} set=DEFAULT tag={tag}",
            f"      st={st} rb={rx} rp={rx // 1400 + 1} tb={tx} tp={tx // 1400 + 1} op=0"]


def _netstats(contadores: dict[tuple[int, int], tuple[int, int]]) -> str:
    linhas = ["U=2000", "UID stats:"]
    for (tipo, uid), (rx, tx) in sorted(contadores.items()):
        linhas += _bloco(tipo, uid, rx, tx)
    linhas += ["UID tag stats:", "FIM=1"]
    return "\n".join(linhas) + "\n"


# ============================================================================ leitura, com os números do 25.1
def test_contabilidade_por_uid_com_os_numeros_medidos() -> None:
    """18:00 e 18:23 do 25.1: o Chrome (uid 10150) com 783876 B recebidos no tipo 17 E no tipo 1 = coberto; o uid 0
    com 868 B enviados pela física e só 52 B pela VPN = saiu por fora; o cliente VPN (10198) só na física."""
    antes = _netstats({(1, 10150): (1000, 200), (17, 10150): (1000, 200), (1, 0): (0, 0), (17, 0): (0, 0),
                       (1, 10198): (5000, 5000)})
    depois_linhas = _netstats({(1, 10150): (784876, 200), (17, 10150): (784876, 200), (1, 0): (0, 868),
                               (17, 0): (0, 52), (1, 10198): (905000, 5000)})
    # Uma linha de etiqueta (tag≠0) é subconjunto do total do uid: não pode somar duas vezes.
    depois_linhas = depois_linhas.replace("UID tag stats:", "\n".join(_bloco(1, 10150, 99999, 0, tag="0xffff0000"))
                                          + "\nUID tag stats:")
    a, d = ler_netstats(antes), ler_netstats(depois_linhas)
    assert d[(17, 10150)] == (784876, 200) and d[(1, 10150)] == (784876, 200)
    chrome = cobertura(a, d, 10150)
    assert (chrome.vpn, chrome.fisica, chrome.resultado) == (783876, 783876, "ok")
    raiz = cobertura(a, d, 0)
    assert (raiz.vpn, raiz.fisica, raiz.resultado) == (52, 868, "fora_da_rede")
    assert cobertura(a, d, 10198).resultado == "fora_da_rede"           # o túnel cifrado sai pela física, por definição
    assert cobertura(a, d, 10196).resultado == "nao_medido"             # sem tráfego na janela: não dá para saber
    # Contador que voltou (reinício): nunca vira "coberto" por delta negativo.
    assert cobertura(d, a, 10150).resultado == "nao_medido"
    with pytest.raises(ValueError):
        ler_netstats("U=2000\nUID stats:\n")                            # truncada (sem o FIM)


def test_eco_de_ip_bloqueio_timeout_e_ipv6_preso_no_tunel() -> None:
    def saida(corpo: str, x: int = 0) -> str:
        return f"U=2000\n==SONDA-INICIO==\n{corpo}\n\n==SONDA-FIM==\nX={x}\n"

    ok = saida(f"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 10\r\nVary: Origin\r\n\r\n{IP}")
    assert ler_ip_de_saida(ok, 4).ip == IP
    # Lockdown sem túnel (18:03:57): o connect é recusado — sem IP, com o motivo.
    r = ler_ip_de_saida(saida("nc: connect: Permission denied", 1), 4)
    assert r.ip is None and "Permission denied" in r.motivo
    # Servidor parado com o túnel no ar (18:06:50): `nc: Timeout`; ou nada, com o `timeout` vencendo.
    assert "Timeout" in ler_ip_de_saida(saida("nc: Timeout", 1), 4).motivo
    assert ler_ip_de_saida(saida("", 124), 4).motivo == "sem resposta (tempo esgotado)"
    # IPv6 com `::/0 unreachable` (strict_route, 18:23:29): preso no túnel, sem vazamento e sem conectividade.
    assert ler_ip_de_saida(saida("nc: connect: No route to host", 1), 6).ip is None
    # Endereço de interface ou NAT não é a saída; família trocada também não.
    assert "não é endereço público" in ler_ip_de_saida(saida("HTTP/1.1 200 OK\r\n\r\n10.0.2.15"), 4).motivo
    assert "IPv4" in ler_ip_de_saida(ok, 6).motivo
    assert "503" in ler_ip_de_saida(saida("HTTP/1.1 503 Service Unavailable\r\n\r\nx"), 4).motivo
    with pytest.raises(ValueError):
        ler_ip_de_saida("U=2000\n", 4)


def test_dns_da_vpn_e_udp_como_na_cadeia_com_socks() -> None:
    """SFA com proxy (18:23 e 18:31): DNS 172.19.0.2 (hijack), o UDP de DNS volta (61 B) e o NTP se perde (0 B)."""
    saida = "U=2000\nDNS=DnsAddresses: [ /172.19.0.2 ]\nPDNS=null\nRES=1\nUDNS=61\nUNTP=0\nFIM=1\n"
    d = ler_dns_e_udp(saida)
    assert d.resolvedor == "172.19.0.2" and d.dns_privado is None and d.resolve
    assert d.udp_dns and not d.udp_ntp
    duas = ler_dns_e_udp(saida.replace("[ /172.19.0.2 ]", "[ /172.19.0.2,/fec0::3 ]").replace("UNTP=0", "UNTP=48"))
    assert duas.resolvedor == "172.19.0.2,fec0::3" and duas.udp_ntp
    with pytest.raises(ValueError):
        ler_dns_e_udp("U=2000\nDNS=\n")
    assert ler_uids("package:com.instagram.android uid:10201\npackage:com.android.shell uid:2000\n") == {
        INSTAGRAM: 10201, "com.android.shell": 2000}


def test_comandos_so_levam_host_e_pacote_validados() -> None:
    for ruim in ("api.ipify.org; reboot", "$(id)", "a b", "", "x'y"):
        with pytest.raises(ValueError):
            host_valido(ruim)
    with pytest.raises(ValueError):
        comando_abrir_app("com.x; reboot", 5)
    cmd = comando_ip_de_saida("api.ipify.org")
    assert "HTTP/1.0" in cmd and "nc api.ipify.org 80" in cmd and "sleep" in cmd and cmd.startswith("echo U=$(id -u)")
    udp = comando_dns_e_udp(host_de_resolucao="api.ipify.org", alvo_dns_udp="8.8.4.4", alvo_ntp="time.google.com")
    assert "nc -u 8.8.4.4 53" in udp and "nc -u time.google.com 123" in udp and "'" + CONSULTA_DNS + "'" in udp
    assert PEDIDO_NTP.startswith("\\033") and PEDIDO_NTP.count("\\000") == 47
    # O teste de vazamento numa ida só: pacote e host validados, a MESMA sonda de IP (texto igual) dentro do laço.
    with pytest.raises(ValueError):
        comando_parar_e_sondar("com.x; reboot", "api.ipify.org")
    with pytest.raises(ValueError):
        comando_parar_e_sondar(PKG, "$(id)")
    vaz = comando_parar_e_sondar(PKG, "api.ipify.org")
    assert vaz.startswith("echo U=$(id -u)") and f"am force-stop {PKG}" in vaz and "sleep 0.1" in vaz
    assert "[ -e /sys/class/net/tun0 ]" in vaz and "while [ $n -le 5 ]" in vaz and "SEM_TUN=$S" in vaz
    assert cmd.split("echo U=$(id -u); ", 1)[1] in vaz
    assert ler_parar_e_sondar("U=2000\nTENTATIVAS=5\nSEM_TUN=0\n") == (5, 0)
    with pytest.raises(ValueError):
        ler_parar_e_sondar("U=2000\n==SONDA-INICIO==\n")
    with pytest.raises(ValueError):
        RedeSondaCfg(hosts_ipv4=["api.ipify.org && reboot"])


# ============================================================================ o aparelho falso da sonda
@dataclass
class SondaFalsa(AparelhoFalso):
    """O `AparelhoFalso` da receita (25.4) mais o que a sonda pergunta: contadores por (tipo, uid) que andam com o
    tráfego, o eco de IP e o DNS/UDP, e o `am force-stop` do cliente VPN do teste de vazamento. Sem `tun0`, a sonda é
    recusada com `Permission denied` se o bloqueio estiver EM VIGOR (`regras`, o "Lockdown filtering rules" do
    dumpsys) e sai pela física se não estiver — as duas assinaturas do 25.1 (18:06:50 e 18:01:07)."""

    ip4: str | None = IP
    uids: dict[str, int] = field(default_factory=lambda: {INSTAGRAM: 10201, OUTLOOK: 10202, "com.android.chrome": 10150,
                                                          PKG: 10198, "com.android.shell": 2000})
    contadores: dict[tuple[int, int], list[int]] = field(default_factory=dict)
    servidor_fora: bool = False              # túnel no ar e servidor parado: `nc: Timeout` (25.1, 18:06:50)
    ignora_force_stop: bool = False          # o `tun0` continua depois de parar o cliente
    religa_sozinho: bool = False             # o always-on religa o cliente logo DEPOIS da sonda sem `tun0`
    # O always-on religando o cliente ANTES da sonda (android-05, 29/09: em menos de um segundo): nas primeiras
    # `religa_antes` tentativas o `tun0` cai e volta sem a janela ser vista; `religa_sempre`, em todas.
    religa_antes: int = 0
    religa_sempre: bool = False
    resposta_sem_tunel: str | None = None    # outra resposta sem `tun0` (ex.: `nc: Timeout`)
    parado: bool = False
    paradas: int = 0                         # quantos `am force-stop` do cliente (um por tentativa)
    sondas_sem_tunel: int = 0
    ao_abrir: dict[str, tuple[int, int]] = field(default_factory=dict)
    abertos: list[str] = field(default_factory=list)

    def usar(self, pkg: str, vpn: int, fisica: int) -> None:
        uid = self.uids[pkg]
        for tipo, n in ((17, vpn), (1, fisica)):
            c = self.contadores.setdefault((tipo, uid), [0, 0])
            c[0] += n // 2
            c[1] += n - n // 2

    def depois_do_boot(self, *, uptime: int = 30, tun: bool = True) -> None:
        super().depois_do_boot(uptime=uptime, tun=tun)
        self.parado = False                  # o always-on sobe o cliente no boot com o perfil selecionado

    async def shell(self, comando: str, *, timeout: float = 40) -> str:
        if "pm list packages -U" in comando:
            self.comandos.append(comando)
            return f"U={self.uid}\n" + "".join(f"package:{p} uid:{u}\n" for p, u in self.uids.items()) + "FIM=1\n"
        if "dumpsys netstats" in comando:
            self.comandos.append(comando)
            return _netstats({k: (v[0], v[1]) for k, v in self.contadores.items()}).replace("U=2000", f"U={self.uid}")
        if "am force-stop " in comando and "==SONDA-INICIO==" in comando:
            # Antes do ramo do eco: o teste de vazamento numa ida só (`comando_parar_e_sondar`).
            self.comandos.append(comando)
            return self._parar_e_sondar(comando)
        if "==SONDA-INICIO==" in comando:
            self.comandos.append(comando)
            return self._eco(comando)
        if "UDNS=" in comando:
            self.comandos.append(comando)
            return (f"U={self.uid}\nDNS=DnsAddresses: [ /172.19.0.2 ]\nPDNS=null\nRES=1\nUDNS=61\n"
                    f"UNTP={48 if self.tun else 0}\nFIM=1\n")
        if comando.startswith("echo U=$(id -u); monkey -p "):
            self.comandos.append(comando)
            pkg = comando.split("monkey -p ", 1)[1].split()[0]
            self.abertos.append(pkg)
            if pkg in self.ao_abrir:
                self.usar(pkg, *self.ao_abrir[pkg])
            return f"U={self.uid}\nFIM=1\n"
        return await super().shell(comando, timeout=timeout)

    def _parar_e_sondar(self, comando: str) -> str:
        """O laço do comando no aparelho: a cada tentativa, `force-stop`; o `tun0` cai (salvo `ignora_force_stop`) e,
        se o always-on o religar antes de a espera ver a janela, a tentativa se perde; senão a sonda roda sem túnel."""
        pkg = comando.split("am force-stop ", 1)[1].split()[0]
        assert pkg == PKG
        n = int(comando.split("while [ $n -le ", 1)[1].split()[0])
        sonda, sem_tun, t = "", 0, 0
        for t in range(1, n + 1):
            self.paradas += 1
            self.parado = True
            if self.ignora_force_stop:
                continue
            if self.religa_sempre or t <= self.religa_antes:
                continue                                # caiu e voltou dentro do passo de 0,1 s: sem janela
            self.tun = self.vpn = False
            sem_tun = t
            sonda = self._eco(comando).split("\n", 1)[1]    # sem o `U=`, que já sai no começo
            break
        return f"U={self.uid}\n{sonda}TENTATIVAS={t}\nSEM_TUN={sem_tun}\n"

    def _eco(self, comando: str) -> str:
        host = comando.split(" nc ", 1)[1].split()[0]
        v6 = "api6" in host or "ipv6" in host
        corpo, x = "", 124
        if not self.tun:
            self.sondas_sem_tunel += 1
            if self.resposta_sem_tunel is not None:
                corpo, x = self.resposta_sem_tunel, 1
            elif v6:
                corpo, x = "nc: connect: No route to host", 1
            elif self.regras:
                corpo, x = "nc: connect: Permission denied", 1
            else:
                corpo, x = f"HTTP/1.1 200 OK\r\n\r\n{IP_FISICO}", 0
            if self.religa_sozinho and self.parado:
                self.tun = self.vpn = True
                self.parado = False
        elif v6:
            corpo, x = "nc: connect: No route to host", 1
        elif self.servidor_fora:
            corpo, x = "nc: Timeout", 1
        elif self.ip4:
            corpo, x = f"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n\r\n{self.ip4}", 0
            self.usar("com.android.shell", 700, 700)          # a própria sonda: coberta (tipo 17 = tipo 1)
        return f"U={self.uid}\n==SONDA-INICIO==\n{corpo}\n\n==SONDA-FIM==\nX={x}\n"


CFG = RedeSondaCfg()
_BLOQUEADO = Vazamento(True, "teste da revisão: recusado (Permission denied)", "2026-09-29T18:06:50Z")


def _com_bloqueio(**kw: object) -> SondaFalsa:
    """Um aparelho com a rede aplicada e o bloqueio EM VIGOR (depois do boot): always-on, lockdown e as regras."""
    return SondaFalsa(tun=True, vpn=True, always_on=PKG, lockdown="1", regras=True, **kw)  # type: ignore[arg-type]


# ============================================================================ a sonda sozinha
async def test_sonda_mede_saida_dns_udp_e_cobertura_por_uid_dos_apps_exigidos() -> None:
    ap = _com_bloqueio()
    ap.usar(INSTAGRAM, 5000, 5000)                          # antes da janela: não conta
    base = ler_netstats(_netstats({k: (v[0], v[1]) for k, v in ap.contadores.items()}))
    ap.usar(INSTAGRAM, 20000, 20000)                        # na janela, pelo túnel
    ap.usar(OUTLOOK, 3000, 9000)                            # na janela, 6000 B pela física além do túnel
    r = await medir(ap, CFG, exigidos=[INSTAGRAM, OUTLOOK], linha_de_base=base, bloqueio=True, vazamento=_BLOQUEADO,
                    abrir=False)
    m = r.medicao
    assert m.egress_ipv4 == IP and m.egress_ipv6 is None and m.dns_resolver == "172.19.0.2"
    assert m.udp_ok is True and m.leak_blocked is True
    assert m.per_app == {INSTAGRAM: "ok", OUTLOOK: "fora_da_rede", "com.android.shell": "ok"}
    assert "IPv6 sem saída" in (m.detail or "") and "No route to host" in (m.detail or "")
    assert "Permission denied" in (m.detail or "") and len(m.detail or "") <= 500
    # A medição não toca no cliente VPN: o teste de vazamento é outra coisa (e vem antes, uma vez por revisão).
    assert ap.paradas == 0 and ap.sondas_sem_tunel == 0
    assert not any("su " in c or c.startswith("su") for c in ap.comandos)
    # A janela é acumulada desde a base (a conexão): sem uso novo, o Instagram segue `ok` e o Outlook segue fora —
    # um vazamento visto não some, e um app parado desde a última sonda não derruba a verificação.
    assert r.base is base
    r2 = await medir(ap, CFG, exigidos=[INSTAGRAM, OUTLOOK], linha_de_base=r.base, bloqueio=True,
                     vazamento=_BLOQUEADO, abrir=False)
    assert r2.medicao.per_app[INSTAGRAM] == "ok" and r2.medicao.per_app[OUTLOOK] == "fora_da_rede"
    assert r2.medicao.leak_blocked is True
    # Sem base (backend reiniciado depois da conexão), a janela começa na passada: o que veio antes não conta.
    r3 = await medir(ap, CFG, exigidos=[INSTAGRAM], linha_de_base=None, bloqueio=False, vazamento=None, abrir=False)
    assert r3.medicao.per_app[INSTAGRAM] == "nao_medido" and r3.medicao.leak_blocked is None
    # Com bloqueio e sem o teste da revisão, o vazamento fica sem medição — nunca presumido.
    r4 = await medir(ap, CFG, exigidos=[], linha_de_base=None, bloqueio=True, vazamento=None, abrir=False)
    assert r4.medicao.leak_blocked is None and "vazamento não medido" in (r4.medicao.detail or "")


async def test_sonda_como_root_ou_sem_saida_nao_vira_medicao_boa() -> None:
    ap = SondaFalsa(tun=True, vpn=True, uid=0)
    with pytest.raises(RedeAplicacaoError, match="uid 0"):
        await medir(ap, CFG, exigidos=[], linha_de_base=None, bloqueio=False, vazamento=None, abrir=False)
    with pytest.raises(RedeAplicacaoError, match="uid 0"):
        await sondar_vazamento(ap, CFG, PKG)
    assert ap.paradas == 0                                  # como root, o cliente nem é tocado
    # Sem saída nenhuma: o shell "falhou", e o teste de vazamento não roda (sonda que não funciona não prova bloqueio).
    ap = _com_bloqueio(ip4=None)
    r = await medir(ap, CFG, exigidos=[], linha_de_base=None, bloqueio=False, vazamento=None, abrir=False)
    assert r.medicao.egress_ipv4 is None and r.medicao.per_app["com.android.shell"] == "falhou"
    t = await sondar_vazamento(ap, CFG, PKG)
    assert t.vazamento.bloqueado is None and t.cliente_parado is False and "não prova bloqueio" in t.vazamento.texto
    assert ap.paradas == 0


async def test_vazamento_so_com_o_cliente_parado_e_so_permission_denied_prova() -> None:
    """O defeito da revisão: tirar o par do servidor deixa o túnel no ar, e a sonda dá `Timeout` com o bloqueio
    ligado ou desligado. O teste agora para o CLIENTE (sem `tun0`), e só o `Permission denied` prova o bloqueio."""
    # Bloqueio em vigor: recusado.
    ap = _com_bloqueio()
    t = await sondar_vazamento(ap, CFG, PKG)
    assert t.vazamento.bloqueado is True and t.cliente_parado is True and "Permission denied" in t.vazamento.texto
    assert ap.paradas == 1 and ap.tun is False and ap.sondas_sem_tunel == 1
    assert f"am force-stop {PKG}" in [c for c in ap.comandos if "force-stop" in c][0]
    # O caso do revisor: `always_on_vpn_lockdown 0` sem reboot (sem as regras) e o túnel no ar. Antes, com o par
    # suspenso, a sonda dava `Timeout` e o teste gravava "bloqueado"; com o cliente parado, a sonda sai: VAZOU.
    ap = SondaFalsa(tun=True, vpn=True, always_on=PKG, lockdown="0", regras=False)
    t = await sondar_vazamento(ap, CFG, PKG)
    assert t.vazamento.bloqueado is False and "VAZOU" in t.vazamento.texto and IP_FISICO in t.vazamento.texto
    # Servidor parado com o túnel no ar (`Timeout`): a sonda não sai nem antes de parar o cliente — nada é provado e o
    # cliente nem é tocado.
    ap = _com_bloqueio(servidor_fora=True)
    t = await sondar_vazamento(ap, CFG, PKG)
    assert t.vazamento.bloqueado is None and t.cliente_parado is False and ap.paradas == 0
    # Sem `tun0`, qualquer coisa que não seja a recusa do bloqueio (`Timeout`, nome que não resolve) não prova nada.
    ap = _com_bloqueio(resposta_sem_tunel="nc: Timeout")
    t = await sondar_vazamento(ap, CFG, PKG)
    assert t.vazamento.bloqueado is None and t.cliente_parado is True and "Timeout" in t.vazamento.texto
    # O túnel que continua no ar depois do force-stop: a sonda sairia por ele; não mede, e o cliente conta como tocado.
    ap = _com_bloqueio(ignora_force_stop=True)
    t = await sondar_vazamento(ap, CFG, PKG)
    assert t.vazamento.bloqueado is None and t.cliente_parado is True and "continuou no ar" in t.vazamento.texto
    assert ap.sondas_sem_tunel == 0


async def test_falha_depois_de_parar_o_cliente_nao_levanta() -> None:
    """Depois do force-stop, qualquer falha volta como `bloqueado=None` e `cliente_parado`: quem chama tem de religar o
    cliente — um erro levantado ali deixaria o aparelho sem VPN (e, com bloqueio, sem rede) sem ninguém saber."""
    ap = _com_bloqueio()
    original = ap.shell

    async def shell(comando: str, *, timeout: float = 40) -> str:
        if "am force-stop " in comando and "==SONDA-INICIO==" in comando:
            ap.paradas += 1
            ap.parado = True
            return "U=2000\n"                               # saída truncada: o force-stop rodou, o resto não veio
        return await original(comando, timeout=timeout)

    ap.shell = shell  # type: ignore[method-assign]
    t = await sondar_vazamento(ap, CFG, PKG)
    assert t.vazamento.bloqueado is None and t.cliente_parado is True and "falhou com o cliente parado" in t.vazamento.texto


async def test_always_on_que_religa_na_hora_nao_esconde_o_bloqueio() -> None:
    """O achado real (android-05, 29/09): com always-on e lockdown, o Android religa o cliente em menos de um segundo
    depois do `force-stop`. Com parar, ler o `tun0` e sondar em idas separadas, o túnel já estava de volta: 2 de 3
    testes deram "o tun0 continuou no ar" (aparelho em `parcial` e um reinício a mais). Numa ida só, com novas
    tentativas, a janela sem `tun0` é pega e o bloqueio fica provado."""
    # (a) O caso medido: o always-on religa na hora nas 2 primeiras tentativas; a 3ª pega a janela.
    ap = _com_bloqueio(religa_antes=2)
    t = await sondar_vazamento(ap, CFG, PKG)
    assert t.vazamento.bloqueado is True and t.cliente_parado is True, t.vazamento.texto
    assert "Permission denied" in t.vazamento.texto
    assert ap.paradas == 3 and ap.sondas_sem_tunel == 1
    # Uma ida só ao aparelho depois da sonda de escolha do host: nada de leitura do `tun0` separada no meio.
    depois = ap.comandos[[i for i, c in enumerate(ap.comandos) if "force-stop" in c][0]:]
    assert len(depois) == 1, depois
    # (b) Religa em TODAS as tentativas: não medido, com o porquê — nunca "bloqueado" presumido.
    ap = _com_bloqueio(religa_sempre=True)
    t = await sondar_vazamento(ap, CFG, PKG)
    assert t.vazamento.bloqueado is None and t.cliente_parado is True
    assert "religou o cliente" in t.vazamento.texto and "5 tentativas" in t.vazamento.texto
    assert ap.paradas == 5 and ap.sondas_sem_tunel == 0


async def test_abrir_app_so_quando_pedido() -> None:
    ap = SondaFalsa(tun=True, vpn=True, ao_abrir={INSTAGRAM: (4000, 4000)})
    r = await medir(ap, CFG, exigidos=[INSTAGRAM], linha_de_base=None, bloqueio=False, vazamento=None, abrir=False)
    assert ap.abertos == [] and r.medicao.per_app[INSTAGRAM] == "nao_medido"
    r = await medir(ap, RedeSondaCfg(espera_app_s=3), exigidos=[INSTAGRAM], linha_de_base=None, bloqueio=False,
                    vazamento=None, abrir=True)
    assert ap.abertos == [INSTAGRAM] and r.medicao.per_app[INSTAGRAM] == "ok" and r.abertos == (INSTAGRAM,)
    assert "aberto pela sonda" in (r.medicao.detail or "")


# ============================================================================ convergência (harness do 25.4)
def _preparar_sonda(parque: Harness, monkeypatch: pytest.MonkeyPatch, *, policy: str = "exigida_com_bloqueio",
                    iid: str = "android-01", servidor: str = "central",
                    reinicios: Reinicios | None = None) -> tuple[SondaFalsa, Reinicios]:
    st = parque.state
    assert st is not None
    ap = SondaFalsa(id=iid)
    st.rede_convergencia._aparelho = lambda _s, _rt: ap
    reinicios = reinicios or Reinicios()
    monkeypatch.setattr(despacho, "pedir_ciclo_de_vida", reinicios)
    nome = f"Perfil-{servidor}"
    existente = st.db.one("SELECT id FROM network_profiles WHERE name=?", (nome,))
    if existente is not None:
        pid = existente["id"]
    elif servidor == "central":
        pid = rede.criar_perfil(st, rede.ler_cadastro({
            "name": nome, "kind": "vpn", "protocol": "singbox", "endpoint_host": "10.0.2.2", "endpoint_port": 51820,
            "params": {"servidor": "central"}}), "teste").id
    else:
        # Um servidor WireGuard externo (provedor): chave do cliente gerada na hora, nunca um valor fixo no teste.
        chave = base64.b64encode(pysecrets.token_bytes(32)).decode()
        par = base64.b64encode(pysecrets.token_bytes(32)).decode()
        pid = rede.criar_perfil(st, rede.ler_cadastro({
            "name": nome, "kind": "vpn", "protocol": "wireguard", "endpoint_host": "vpn.provedor.example",
            "endpoint_port": 51820, "secret": chave, "params": {"peer_public_key": par, "address": "10.9.0.7"}}),
            "teste").id
    rede.atribuir(st, rede.NetworkAssignBody(instance_ids=[iid], vpn_profile_id=pid, policy=policy,
                                             confirm_real_account=[iid]), "teste")
    return ap, reinicios


async def _ate_conectado(parque: Harness, ap: SondaFalsa, iid: str = "android-01") -> None:
    assert await _passo(parque, "varredura", iid)                                   # aplicar
    await asyncio.sleep(0.2)
    await _reiniciar(parque, ap, iid)


async def _reiniciar(parque: Harness, ap: SondaFalsa, iid: str = "android-01") -> None:
    """O boot que a convergência pediu (o dublê do `restart` só o registra): o always-on sobe o cliente de novo."""
    await asyncio.sleep(0.2)                                 # o reinício é pedido depois de o trabalho soltar o aparelho
    _envelhecer_configuracao(parque, iid)
    ap.depois_do_boot(uptime=45)
    assert await _passo(parque, "ligou", iid) and _linha(parque, iid)["state"] == "conectado"


def _liberar_a_porta(parque: Harness, iid: str = "android-01") -> None:
    """A porta só dispara trabalho de 30 em 30 s por aparelho; o teste não espera o relógio."""
    parque.state.rede_convergencia.memoria(iid).ultima_tentativa = None  # type: ignore[union-attr]


async def test_convergencia_verifica_com_vazamento_e_libera_a_tarefa(parque: Harness,
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    st = parque.state
    assert st is not None
    st.social.create_profile(ProfileCreate(username="lucas.teste", instance_id="android-01"))
    exigidos = rede.apps_exigidos(st, "android-01")
    assert INSTAGRAM in exigidos
    ap, reinicios = _preparar_sonda(parque, monkeypatch)
    for i, pkg in enumerate(exigidos):
        ap.uids.setdefault(pkg, 10300 + i)
    await _ate_conectado(parque, ap)
    processos = st.rede_servidor.processos
    lancados = len(processos.lancados)                                             # type: ignore[attr-defined]
    encerrados = len(processos.encerrados)                                         # type: ignore[attr-defined]
    pedidos = len(reinicios.pedidos)
    # A porta da tarefa dispara a verificação; com bloqueio, primeiro o teste de vazamento: o cliente parado, a sonda
    # recusada pelo Android e, sem o túnel de volta, o aparelho regride a `configurado` e reinicia para religá-lo.
    assert "medição do tráfego" in (st.rede_convergencia.motivo_de_espera("android-01") or "")
    await parque.wait(lambda: len(reinicios.pedidos) == pedidos + 1, what="reinício depois do teste de vazamento")
    linha = _linha(parque)
    assert linha["state"] == "configurado" and "teste de vazamento" in str(linha["detail"])
    assert "Permission denied" in str(linha["detail"]) and "religar o cliente VPN" in reinicios.pedidos[-1][1]
    assert ap.paradas == 1 and ap.sondas_sem_tunel == 1
    assert st.db.scalar("SELECT COUNT(*) FROM network_measurements") == 0          # a medição vem depois do boot
    mem = st.rede_convergencia.memoria("android-01")
    assert mem.vazamento[1].bloqueado is True
    # O boot religa o cliente. A porta mede com o teste da revisão guardado — sem parar o cliente de novo — e, como
    # há tarefa esperando, abre os apps da conta que não usaram a rede desde a conexão.
    await _reiniciar(parque, ap)
    ap.ao_abrir = {pkg: (3000, 3000) for pkg in exigidos}
    _liberar_a_porta(parque)
    assert "medição do tráfego" in (st.rede_convergencia.motivo_de_espera("android-01") or "")
    await parque.wait(lambda: (_linha(parque) or {}).get("state") == "trafego_verificado", what="sonda pela porta")
    assert st.rede_convergencia.motivo_de_espera("android-01") is None
    [m] = st.db.query("SELECT * FROM network_measurements WHERE instance_id='android-01'")
    per_app = json.loads(m["per_app"])
    assert all(per_app[p] == "ok" for p in exigidos) and per_app["com.android.shell"] == "ok"
    assert m["egress_ipv4"] == IP and m["leak_blocked"] == 1 and m["udp_ok"] == 1 and m["dns_resolver"] == "172.19.0.2"
    assert sorted(ap.abertos) == sorted(exigidos) and ap.paradas == 1
    assert _linha(parque)["egress_ipv4"] == IP and _linha(parque)["verified_at"]
    # O servidor do central não foi parado nem reiniciado pelo teste: os outros aparelhos não perdem a conexão.
    assert len(processos.lancados) == lancados                                     # type: ignore[attr-defined]
    assert len(processos.encerrados) == encerrados                                 # type: ignore[attr-defined]
    assert [p["instance_id"] for p in st.rede_servidor.status()["peers"]] == ["android-01"]
    acoes = [json.loads(c["params"])["acao"] for c in st.db.query(
        "SELECT params FROM commands WHERE verb='device.network' ORDER BY created_at")]
    assert acoes[-1] == "verificar" and acoes.count("verificar") == 2
    # Verificado: a varredura não mede de novo sozinha (só a deriva, que é leitura). Pedido de verificar: refaz o teste
    # de vazamento (o 202 avisa do reinício), e o aparelho volta a `configurado` até o boot.
    assert await _passo(parque, "varredura") is False
    r = st.rede_convergencia.pedir_verificacao("android-01", "teste")
    assert r["executed"] is False and "próximo ponto seguro" in str(r["reason"]) and "REINICIA" in str(r["reason"])
    assert await _passo(parque, "varredura")
    assert ap.paradas == 2 and _linha(parque)["state"] == "configurado"
    assert st.db.scalar("SELECT COUNT(*) FROM network_measurements WHERE instance_id='android-01'") == 1


async def test_app_parado_fica_parcial_e_a_tarefa_segurada_abre_o_app(parque: Harness,
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    st = parque.state
    assert st is not None
    st.social.create_profile(ProfileCreate(username="lucas.teste", instance_id="android-01"))
    exigidos = rede.apps_exigidos(st, "android-01")
    ap, _ = _preparar_sonda(parque, monkeypatch, policy="exigida")
    for i, pkg in enumerate(exigidos):
        ap.uids.setdefault(pkg, 10300 + i)
    await _ate_conectado(parque, ap)
    # Sem tarefa esperando (`ligou`), a sonda não abre app de conta real: o app parado fica `nao_medido`.
    assert await _passo(parque, "ligou")
    linha = _linha(parque)
    assert linha["state"] == "parcial" and INSTAGRAM in str(linha["detail"]) and ap.abertos == []
    # A tarefa que espera diz POR QUÊ (o app sem tráfego medido), não só "aguardando".
    _liberar_a_porta(parque)
    assert f"{INSTAGRAM}=nao_medido" in (st.rede_convergencia.motivo_de_espera("android-01") or "")
    # Com a tarefa segurada, a porta mede de novo JÁ (sem esperar `reverificar_s`) e abre os apps parados: é o que a
    # tarefa faria, e sem isso ela esperaria para sempre. Este app não gerou tráfego aberto: segue `parcial`.
    await parque.wait(lambda: sorted(ap.abertos) == sorted(exigidos), what="apps abertos pela porta")
    await parque.wait(lambda: st.db.scalar("SELECT COUNT(*) FROM network_measurements") == 2, what="segunda medição")
    assert _linha(parque)["state"] == "parcial"
    # Medida assim, a espera volta a valer: nem a porta insiste a cada volta.
    _liberar_a_porta(parque)
    assert await _passo(parque, "tarefa") is False and await _passo(parque, "varredura") is False
    # O app usou a rede (a pessoa o abriu pelo painel, uma sincronização): a janela desde a conexão o vê.
    for pkg in exigidos:
        ap.usar(pkg, 2000, 2000)
    st.rede_convergencia.memoria("android-01").espera_ate = 0.0
    st.rede_convergencia.memoria("android-01").ultima_verificacao = -1e9
    assert await _passo(parque, "varredura")
    assert _linha(parque)["state"] == "trafego_verificado"
    assert ap.paradas == 0                                   # sem bloqueio pedido, nada de teste de vazamento


async def test_deriva_na_verificacao_regride_sem_medir_e_cliente_parado_religa(parque: Harness,
                                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    st = parque.state
    assert st is not None
    ap, reinicios = _preparar_sonda(parque, monkeypatch)
    await _ate_conectado(parque, ap)
    ap.tun = ap.vpn = False
    ap.uptime = 5000
    assert await _passo(parque, "tarefa")
    assert _linha(parque)["state"] == "configurado" and "túnel caído" in str(_linha(parque)["detail"])
    assert st.db.scalar("SELECT COUNT(*) FROM network_measurements") == 0          # nada medido, nada gravado
    assert ap.paradas == 0                                                         # e o cliente não foi tocado
    # Volta; o teste de vazamento para o cliente e o always-on NÃO o religa: sem medição, `configurado` e reinício.
    await _reiniciar(parque, ap)
    pedidos = len(reinicios.pedidos)
    assert await _passo(parque, "ligou")
    linha = _linha(parque)
    assert linha["state"] == "configurado" and "sem boot: reinicia" in str(linha["detail"])
    await asyncio.sleep(0.2)
    assert len(reinicios.pedidos) == pedidos + 1 and st.db.scalar("SELECT COUNT(*) FROM network_measurements") == 0
    # Depois do boot, a medição usa o teste da revisão guardado: o cliente não é parado de novo.
    await _reiniciar(parque, ap)
    assert await _passo(parque, "ligou") and _linha(parque)["state"] == "trafego_verificado"
    assert ap.paradas == 1                                                         # o teste da revisão foi guardado


async def test_cliente_religado_pelo_always_on_mede_na_mesma_passada(parque: Harness,
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    st = parque.state
    assert st is not None
    ap, reinicios = _preparar_sonda(parque, monkeypatch)
    await _ate_conectado(parque, ap)
    ap.religa_sozinho = True
    pedidos = len(reinicios.pedidos)
    assert await _passo(parque, "ligou")
    assert _linha(parque)["state"] == "trafego_verificado" and ap.paradas == 1
    [m] = st.db.query("SELECT leak_blocked FROM network_measurements")
    assert m["leak_blocked"] == 1
    await asyncio.sleep(0.2)
    assert len(reinicios.pedidos) == pedidos                                       # túnel de volta: sem reinício


async def test_servidor_externo_tambem_tem_o_vazamento_medido(parque: Harness,
                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    """Parar o cliente não depende de quem é o servidor: o provedor externo também tem o bloqueio medido (antes, sem
    como parar o servidor dele, ficava `None` e o aparelho em `parcial` para sempre)."""
    st = parque.state
    assert st is not None
    ap, _ = _preparar_sonda(parque, monkeypatch, servidor="externo")
    await _ate_conectado(parque, ap)
    assert await _passo(parque, "tarefa")
    assert _linha(parque)["state"] == "configurado" and ap.paradas == 1
    await _reiniciar(parque, ap)
    _liberar_a_porta(parque)
    assert await _passo(parque, "tarefa")
    linha = _linha(parque)
    assert linha["state"] == "trafego_verificado", linha["detail"]
    [m] = st.db.query("SELECT * FROM network_measurements")
    assert m["leak_blocked"] == 1 and "Permission denied" in str(m["detail"])
    assert st.rede_servidor.status()["peers"] == []                                # o servidor do central fora disso


async def test_mesmo_ip_em_dois_aparelhos_gera_aviso(parque: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    st = parque.state
    assert st is not None
    aps = {}
    for iid in ("android-01", "android-02"):
        aps[iid], _ = _preparar_sonda(parque, monkeypatch, policy="exigida", iid=iid)
    aparelhos = {"android-01": aps["android-01"], "android-02": aps["android-02"]}
    st.rede_convergencia._aparelho = lambda _s, rt: aparelhos[rt.id]
    for iid, ap in aparelhos.items():
        await _ate_conectado(parque, ap, iid)
        assert await _passo(parque, "tarefa", iid)
        assert _linha(parque, iid)["state"] == "trafego_verificado"                # aviso, não bloqueio
    [m] = st.db.query("SELECT * FROM network_measurements WHERE instance_id='android-02'")
    assert "aviso: a mesma saída medida em android-01" in str(m["detail"])
    avisos = [json.loads(e["data"]) for e in st.db.query(
        "SELECT data FROM events WHERE kind='network.updated' AND level='warn' ORDER BY id")]
    assert [(a["instance_id"], a["shared_with"]) for a in avisos] == [("android-02", ["android-01"])]
    app = create_app(parque.cfg, state=st)
    app.state.poc = st
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        linhas = {d["instance_id"]: d for d in (await c.get("/api/network/devices")).json()["devices"]}
        assert linhas["android-01"]["egress_shared_with"] == ["android-02"]
        assert linhas["android-02"]["egress_shared_with"] == ["android-01"]
        assert linhas["android-03"]["egress_shared_with"] == []
        # A rota de verificar registra e marca a sonda (202, nada executado ainda).
        r = await c.post("/api/network/devices/android-01/verify")
        assert r.status_code == 202 and r.json()["executed"] is False
        assert "REINICIA" not in r.json()["reason"]                               # sem bloqueio, sem reinício
    assert st.rede_convergencia.memoria("android-01").verificacao_pedida is True


async def test_vazamento_em_cache_so_vale_para_a_mesma_revisao(parque: Harness,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    """O teste de vazamento para o cliente e custa um reinício: uma vez por revisão. Revisão nova (reaplicar), teste
    novo."""
    st = parque.state
    assert st is not None
    ap, _ = _preparar_sonda(parque, monkeypatch)
    await _ate_conectado(parque, ap)
    assert await _passo(parque, "tarefa") and _linha(parque)["state"] == "configurado"
    await _reiniciar(parque, ap)
    _liberar_a_porta(parque)
    assert await _passo(parque, "tarefa") and _linha(parque)["state"] == "trafego_verificado"
    mem = st.rede_convergencia.memoria("android-01")
    assert isinstance(mem.vazamento.get(1), Vazamento) and mem.vazamento[1].bloqueado is True
    assert await _passo(parque, "ligou")                                          # trafego_verificado: só confere
    assert ap.paradas == 1
    rede.pedir_reaplicacao(st, "android-01", "teste")
    await _ate_conectado(parque, ap)
    _liberar_a_porta(parque)
    assert await _passo(parque, "tarefa") and _linha(parque)["state"] == "configurado"
    await _reiniciar(parque, ap)
    _liberar_a_porta(parque)
    assert await _passo(parque, "tarefa") and _linha(parque)["state"] == "trafego_verificado"
    assert ap.paradas == 2 and set(mem.vazamento) == {1, 2}


async def test_vazamento_adiado_com_objetivo_no_meio_e_no_celular_sem_worker(parque: Harness,
                                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    """O teste de vazamento para o cliente e só o boot o religa: sem o reinício garantido logo depois, ele nem começa.
    Com um objetivo esperando uma pessoa (a tela dele é a evidência), o reinício seria adiado e o aparelho ficaria sem
    rede — a medição sai `parcial` com o porquê, sem guardar, e a seguinte tenta de novo."""
    from types import SimpleNamespace

    from app.models import ObjectiveStatus

    st = parque.state
    assert st is not None
    ap, reinicios = _preparar_sonda(parque, monkeypatch)
    await _ate_conectado(parque, ap)
    porta = st.scheduler.rede_gate
    st.scheduler.rede_gate = lambda _iid: "segura (teste)"             # o objetivo nasce sem ser despachado
    run = parque.run(["android-01"])
    oid = f"{run.id}:android-01"
    await parque.wait(lambda: st.db.one("SELECT 1 FROM objectives WHERE id=?", (oid,)) is not None, what="objetivo")
    st.repo.set_objective(oid, ObjectiveStatus.waiting_user, detail="esperando a pessoa (teste)")
    st.scheduler.rede_gate = porta
    pedidos = len(reinicios.pedidos)
    assert await _passo(parque, "ligou")
    linha = _linha(parque)
    assert ap.paradas == 0 and linha["state"] == "parcial" and "bloqueio fora da VPN não foi provado" in str(linha["detail"])
    [m] = st.db.query("SELECT leak_blocked, detail FROM network_measurements")
    assert m["leak_blocked"] is None and "teste de vazamento adiado" in str(m["detail"])
    await asyncio.sleep(0.2)
    assert len(reinicios.pedidos) == pedidos
    assert st.rede_convergencia.memoria("android-01").vazamento == {}
    # A pessoa encerrou o objetivo: a medição seguinte faz o teste (e o reinício sai).
    st.repo.set_objective(oid, ObjectiveStatus.cancelled, detail="encerrado pela pessoa (teste)")
    st.rede_convergencia.memoria("android-01").espera_ate = 0.0
    assert await _passo(parque, "ligou")
    assert ap.paradas == 1 and _linha(parque)["state"] == "configurado"
    # O celular sem worker: o `restart` não o reinicia de verdade, e o cliente parado não voltaria.
    conv = st.rede_convergencia
    celular = SimpleNamespace(id="android-03", external=True, worker_id=None)
    assert "celular sem worker" in (conv._vazamento_adiado(celular) or "")                  # type: ignore[arg-type]
    notebook = SimpleNamespace(id="android-03", external=True, worker_id="worker-lan-01")
    assert conv._vazamento_adiado(notebook) is None                                          # type: ignore[arg-type]
