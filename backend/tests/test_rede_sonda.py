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
import re
import secrets as pysecrets
from dataclasses import dataclass, field

import httpx
import pytest

from app.commands import despacho
from app.config import RedeSondaCfg
from app.devices import rede, rede_medicao, sonda_rede
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
    # UDP (item 29.5): em qual datagrama cada perna responde (1 = no primeiro; 0 = em nenhum). O NTP só responde com o
    # túnel no ar, como antes. `prazo_udp` = o `timeout` que a sonda deu ao shell na ida do DNS/UDP.
    udp_dns_na: int = 1
    udp_ntp_na: int = 1
    prazo_udp: float | None = None

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
            self.prazo_udp = timeout
            return self._dns_e_udp(comando)
        if comando.startswith("echo U=$(id -u); monkey -p "):
            self.comandos.append(comando)
            pkg = comando.split("monkey -p ", 1)[1].split()[0]
            self.abertos.append(pkg)
            if pkg in self.ao_abrir:
                self.usar(pkg, *self.ao_abrir[pkg])
            return f"U={self.uid}\nFIM=1\n"
        return await super().shell(comando, timeout=timeout)

    def _dns_e_udp(self, comando: str) -> str:
        """As duas pernas de UDP como o aparelho as responde. O `nc -u` do toybox lê até o prazo mesmo com resposta:
        cada datagrama custa o `timeout` inteiro, com ou sem resposta (medido em 30/09: cada perna levava sempre ~5 s).
        Um comando SEM o laço (o de antes do 29.5) manda um datagrama só: a perna que responderia no segundo dá 0 B."""
        cabeca = f"U={self.uid}\nDNS=DnsAddresses: [ /172.19.0.2 ]\nPDNS=null\nRES=1\n"
        lacos = [int(n) for n in re.findall(r"while \[ \$n -le (\d+) \]", comando)]
        pernas = (("DNS", 53, 61, self.udp_dns_na), ("NTP", 123, 48, self.udp_ntp_na if self.tun else 0))
        if not lacos:
            dns, ntp = (tam if na == 1 else 0 for _, _, tam, na in pernas)
            return f"{cabeca}UDNS={dns}\nUNTP={ntp}\nFIM=1\n"
        assert len(lacos) == 2, comando
        relogio, linhas = float(self.uptime), []
        for (nome, porta, tam, na), limite in zip(pernas, lacos):
            prazo = int(re.search(rf"timeout (\d+) nc -u \S+ {porta} ", comando).group(1))  # type: ignore[union-attr]
            respondeu = na if 1 <= na <= limite else 0
            antes = relogio
            relogio += (respondeu or limite) * prazo
            linhas += [f"U{nome}={tam if respondeu else 0}", f"T{nome}={respondeu}/{limite}",
                       f"S{nome}={antes:.2f} {relogio:.2f}"]
        return cabeca + "\n".join(linhas) + "\nFIM=1\n"

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


# ============================================================================ UDP com repetição (item 29.5)
# O defeito (medido em 30/09): um datagrama só por perna. Em 32 medições reais, 2 perderam uma perna (a #6 do
# android-03, `UDP DNS 83 B, NTP 0 B`, com carga 18,8 em 2 vCPU no minuto; a #21 do android-05, `DNS 0 B, NTP 48 B`), e
# a repetição manual deu 12/12. UDP perde datagrama; uma perda não é "UDP não passa".
def test_comando_de_udp_repete_o_datagrama_numa_ida_so_e_diz_tentativa_e_tempo() -> None:
    cmd = comando_dns_e_udp(host_de_resolucao="api.ipify.org", alvo_dns_udp="8.8.4.4", alvo_ntp="time.google.com")
    n, espera = sonda_rede.TENTATIVAS_UDP, sonda_rede.ESPERA_UDP_S
    assert (n, espera) == (3, 2)
    # Uma ida só ao shell, como uid 2000 conferido na mesma ida, e a leitura sabe que chegou ao fim.
    assert cmd.startswith("echo U=$(id -u); ") and cmd.endswith("echo FIM=1") and "\n" not in cmd
    # Um laço por perna, parando no primeiro datagrama com resposta; cada datagrama espera `espera` segundos.
    assert cmd.count(f"while [ $n -le {n} ]; do") == 2 and cmd.count("break; fi; n=$((n+1)); done") == 2
    assert f"timeout {espera} nc -u 8.8.4.4 53 " in cmd and f"timeout {espera} nc -u time.google.com 123 " in cmd
    assert "if [ $B -ge 13 ]; then T=$n; break" in cmd and "if [ $B -ge 48 ]; then T=$n; break" in cmd
    # Por perna: bytes (as chaves de antes), a tentativa que respondeu e os dois instantes do `/proc/uptime`.
    for chave in ("echo UDNS=$B", "echo UNTP=$B", f"echo TDNS=$T/{n}", f"echo TNTP=$T/{n}", "echo SDNS=$A ",
                  "echo SNTP=$A "):
        assert chave in cmd, chave
    assert cmd.count("/proc/uptime") == 4 and "date +%N" not in cmd
    # POSIX conservador para o mksh/toybox: nada de `[[`, `function`, `+=`, `$'…'`, `let` nem `local`.
    # (Só no trecho das pernas: o `grep` do resolvedor, antes dele, tem um `[[]` que é expressão regular.)
    pernas = cmd.split("echo RES=0; fi; ", 1)[1]
    for bashismo in ("[[", "function ", "+=", "$'", "let ", "local ", "((n", "&>"):
        assert bashismo not in pernas.replace("$((", "$<<"), bashismo
    # O pior caso das duas pernas (nenhuma responde) não passa muito do de antes (2 × 5 s), e o sucesso de primeira
    # fica mais rápido (2 s por perna no lugar de 5).
    assert sonda_rede.pior_caso_udp_s() == 2 * n * espera == 12 and espera < 5


def test_leitura_de_udp_por_perna_e_a_saida_antiga_ainda_e_lida() -> None:
    base = "U=2000\nDNS=DnsAddresses: [ /172.19.0.2 ]\nPDNS=null\nRES=1\n"
    nova = ler_dns_e_udp(base + "UDNS=83\nTDNS=2/3\nSDNS=512.40 516.47\nUNTP=0\nTNTP=0/3\nSNTP=516.50 522.61\nFIM=1\n")
    assert (nova.udp_dns_bytes, nova.udp_dns_tentativa, nova.udp_dns_s) == (83, 2, 4.1) and nova.udp_dns
    assert (nova.udp_ntp_bytes, nova.udp_ntp_tentativa, nova.udp_ntp_s) == (0, 0, 6.1) and not nova.udp_ntp
    assert nova.udp_tentativas == 3
    assert nova.descrever_udp() == "UDP DNS 83 B (2ª de 3, 4,1 s), NTP 0 B (0 de 3, 6,1 s)"
    # A saída de antes do 29.5 (um agente ou um comando antigo): sem tentativa nem tempo, e sem erro.
    antiga = ler_dns_e_udp(base + "UDNS=83\nUNTP=0\nFIM=1\n")
    assert (antiga.udp_dns_bytes, antiga.udp_dns_tentativa, antiga.udp_dns_s, antiga.udp_tentativas) == (83, None, None,
                                                                                                        None)
    assert antiga.udp_dns and not antiga.udp_ntp and antiga.descrever_udp() == "UDP DNS 83 B, NTP 0 B"
    # Chave nova ilegível (relógio que não veio, texto no lugar do número) não derruba a leitura: só não diz o tempo.
    torta = ler_dns_e_udp(base + "UDNS=61\nTDNS=1/3\nSDNS= 9.30\nUNTP=48\nTNTP=x\nSNTP=a b\nFIM=1\n")
    assert (torta.udp_dns_tentativa, torta.udp_dns_s, torta.udp_ntp_tentativa, torta.udp_ntp_s) == (1, None, None, None)
    assert torta.descrever_udp() == "UDP DNS 61 B (1ª de 3), NTP 48 B"
    # O relógio que andou para trás (reinício no meio) não vira tempo negativo.
    assert ler_dns_e_udp(base + "UDNS=61\nTDNS=1/3\nSDNS=900.00 3.10\nUNTP=48\nTNTP=1/3\nSNTP=1 3\nFIM=1\n").udp_dns_s is None

    # O formato do `detail` lido de volta: o novo, o de antes, e no meio de um `detail` com o aviso na frente.
    pernas = sonda_rede.pernas_udp
    assert pernas(nova.descrever_udp()) == {
        "dns": {"bytes": 83, "ok": True, "tentativa": 2, "tentativas": 3, "segundos": 4.1},
        "ntp": {"bytes": 0, "ok": False, "tentativa": 0, "tentativas": 3, "segundos": 6.1}}
    assert pernas("UDP DNS 83 B, NTP 0 B") == {
        "dns": {"bytes": 83, "ok": True, "tentativa": None, "tentativas": None, "segundos": None},
        "ntp": {"bytes": 0, "ok": False, "tentativa": None, "tentativas": None, "segundos": None}}
    detalhe = (f"aviso: a mesma saída medida em android-02 | IPv4 {IP} (api.ipify.org) | IPv6 sem saída (x) | DNS da "
               "VPN 172.19.0.2, resolve | UDP DNS 0 B (0 de 3, 6,0 s), NTP 48 B (1ª de 3, 2,0 s) | apps: shell=ok")
    lido = pernas(detalhe)
    assert lido is not None and (lido["dns"]["ok"], lido["ntp"]["ok"], lido["ntp"]["tentativa"]) == (False, True, 1)
    # 12 B é só o cabeçalho DNS e 47 B não é resposta NTP: os limiares são os da sonda.
    curto = pernas("UDP DNS 12 B, NTP 47 B")
    assert curto is not None and (curto["dns"]["ok"], curto["ntp"]["ok"]) == (False, False)
    # Sem o trecho (medição de outro método, `detail` cortado antes dele, ou vazio): não dá para saber.
    assert pernas(None) is None and pernas("") is None and pernas(f"IPv4 {IP} (api.ipify.org) | UDP DNS 8") is None


async def test_falha_transitoria_de_udp_nao_derruba_a_perna_e_a_persistente_fica_registrada() -> None:
    # (a) O caso medido: o primeiro datagrama de DNS se perde, o segundo tem resposta. Perna ok, e o `detail` diz.
    ap = SondaFalsa(tun=True, vpn=True, udp_dns_na=2)
    m = (await medir(ap, CFG, exigidos=[], linha_de_base=None, bloqueio=False, vazamento=None, abrir=False)).medicao
    assert m.udp_ok is True, m.detail
    assert "UDP DNS 61 B (2ª de 3, 4,0 s), NTP 48 B (1ª de 3, 2,0 s)" in (m.detail or "")
    # Tudo numa ida só ao shell, e com prazo que cobre o pior caso das duas pernas mais o resto da ida (o `dumpsys
    # connectivity`, o `ping` de 3 s): a repetição não pode virar "saída truncada".
    assert len([c for c in ap.comandos if "UDNS=" in c]) == 1
    assert ap.prazo_udp is not None and ap.prazo_udp >= sonda_rede.pior_caso_udp_s() + 3 + 30
    # (b) Persistente numa perna só (o NTP na cadeia com SOCKS5): 0 B, "0 de 3", o tempo das três esperas — e a outra ok.
    ap = SondaFalsa(tun=True, vpn=True, udp_ntp_na=0)
    m = (await medir(ap, CFG, exigidos=[], linha_de_base=None, bloqueio=False, vazamento=None, abrir=False)).medicao
    assert m.udp_ok is False and "UDP DNS 61 B (1ª de 3, 2,0 s), NTP 0 B (0 de 3, 6,0 s)" in (m.detail or "")
    lido = sonda_rede.pernas_udp(m.detail)
    assert lido is not None and lido["dns"]["ok"] is True and lido["ntp"]["ok"] is False
    # (c) A outra perna: o DNS nunca responde e o NTP só no terceiro datagrama.
    ap = SondaFalsa(tun=True, vpn=True, udp_dns_na=0, udp_ntp_na=3)
    m = (await medir(ap, CFG, exigidos=[], linha_de_base=None, bloqueio=False, vazamento=None, abrir=False)).medicao
    assert m.udp_ok is False and "UDP DNS 0 B (0 de 3, 6,0 s), NTP 48 B (3ª de 3, 6,0 s)" in (m.detail or "")
    # (d) Quem só responderia no quarto datagrama não respondeu: a sonda manda três.
    ap = SondaFalsa(tun=True, vpn=True, udp_dns_na=4)
    m = (await medir(ap, CFG, exigidos=[], linha_de_base=None, bloqueio=False, vazamento=None, abrir=False)).medicao
    assert m.udp_ok is False and "UDP DNS 0 B (0 de 3" in (m.detail or "") and len(m.detail or "") <= 500
    assert rede_medicao._PRAZO_DNS_E_UDP_S == ap.prazo_udp


async def test_perna_de_udp_falha_nao_segura_a_tarefa_e_aparece_por_perna(parque: Harness,
                                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    """UDP ainda NÃO é critério de `trafego_verificado` (29.5 não muda a regra que libera tarefa): a medição com uma
    perna falha e o resto ok verifica o aparelho, e a perna aparece na listagem."""
    st = parque.state
    assert st is not None
    ap, _ = _preparar_sonda(parque, monkeypatch, policy="exigida")
    ap.udp_ntp_na = 0                                        # o NTP se perde sempre; o DNS responde
    await _ate_conectado(parque, ap)
    assert await _passo(parque, "tarefa")
    linha = _linha(parque)
    assert linha["state"] == "trafego_verificado", linha["detail"]
    assert st.rede_convergencia.motivo_de_espera("android-01") is None          # a porta da tarefa não espera por UDP
    [m] = st.db.query("SELECT udp_ok, detail FROM network_measurements WHERE instance_id='android-01'")
    assert m["udp_ok"] == 0 and "NTP 0 B (0 de 3, 6,0 s)" in str(m["detail"])
    # A regra, direto: `udp_ok` falso não entra no que falta.
    assert rede._falta_para_verificar(rede.NetworkMeasurementInput(
        method="x", egress_ipv4=IP, udp_ok=False, per_app={"com.android.shell": "ok"}), "exigida", [],
        provado=True) == []
    # A listagem acrescenta as duas pernas, derivadas do `detail` (sem coluna nova).
    visao = {d["instance_id"]: d for d in rede.listar_aparelhos(st)["devices"]}
    ultima = visao["android-01"]["last_measurement"]
    assert (ultima["udp_ok"], ultima["udp_dns_ok"], ultima["udp_ntp_ok"]) == (False, True, False)
    # Medição de antes do 29.5 (o `detail` antigo) também é lida; sem o trecho de UDP, as pernas ficam sem valor.
    rede.registrar_medicao(st, "android-02", rede.NetworkMeasurementInput(
        method="sonda antiga", egress_ipv4=IP_FISICO, udp_ok=False, detail="DNS da VPN x | UDP DNS 0 B, NTP 48 B"),
        rev=None)
    rede.registrar_medicao(st, "android-03", rede.NetworkMeasurementInput(method="app_qa", egress_ipv4="45.162.8.11"),
                           rev=None)
    visao = {d["instance_id"]: d for d in rede.listar_aparelhos(st)["devices"]}
    antiga, sem = visao["android-02"]["last_measurement"], visao["android-03"]["last_measurement"]
    assert (antiga["udp_dns_ok"], antiga["udp_ntp_ok"]) == (False, True)
    assert (sem["udp_ok"], sem["udp_dns_ok"], sem["udp_ntp_ok"]) == (None, None, None)


# ============================================================================ saída esperada (item 29.6)
async def test_sonda_que_mede_outra_saida_que_a_esperada_fica_parcial_e_a_tarefa_espera(
        parque: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    """O caminho inteiro, pela sonda: o perfil declara a saída (`params.egress_esperado`), o aparelho sai por outra, e a
    medição — com IP, apps e tudo o mais provado — deixa o aparelho `parcial`. Com a política exigida a tarefa espera
    e diz por quê; medida a saída esperada, verifica."""
    st = parque.state
    assert st is not None
    ap = SondaFalsa(id="android-01")                         # a sonda mede `IP`
    st.rede_convergencia._aparelho = lambda _s, _rt: ap
    monkeypatch.setattr(despacho, "pedir_ciclo_de_vida", Reinicios())
    pid = rede.criar_perfil(st, rede.ler_cadastro({
        "name": "Dedicada-01", "kind": "vpn", "protocol": "singbox", "endpoint_host": "10.0.2.2", "endpoint_port": 51820,
        "params": {"servidor": "central", "egress_esperado": IP_FISICO}}), "teste").id
    rede.atribuir(st, rede.NetworkAssignBody(instance_ids=["android-01"], vpn_profile_id=pid, policy="exigida"), "teste")
    await _ate_conectado(parque, ap)
    assert await _passo(parque, "tarefa")
    linha = _linha(parque)
    assert linha["state"] == "parcial" and linha["egress_ipv4"] == IP
    assert f"saída medida {IP}, esperada {IP_FISICO} do perfil Dedicada-01" in str(linha["detail"])
    espera = st.rede_convergencia.motivo_de_espera("android-01") or ""
    assert "parcial" in espera and f"esperada {IP_FISICO}" in espera
    visao = {d["instance_id"]: d for d in rede.listar_aparelhos(st)["devices"]}
    assert visao["android-01"]["egress_matches"] is False
    # O aparelho passa a sair pela saída declarada (o provedor entregou o IP): a medição seguinte verifica.
    ap.ip4 = IP_FISICO
    st.rede_convergencia.memoria("android-01").espera_ate = 0.0
    st.rede_convergencia.memoria("android-01").ultima_verificacao = -1e9
    assert await _passo(parque, "varredura")
    assert _linha(parque)["state"] == "trafego_verificado"
    assert st.rede_convergencia.motivo_de_espera("android-01") is None
    visao = {d["instance_id"]: d for d in rede.listar_aparelhos(st)["devices"]}
    assert visao["android-01"]["egress_matches"] is True and visao["android-01"]["egress_expected"]["ipv4"] == IP_FISICO


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
    # O desfecho já está NA LINHA (item 29.2), preso à revisão e à instalação do cliente, com o ensaio fechado.
    assert (linha["leak_rev"], linha["leak_result"], linha["leak_pending"]) == (1, 1, 0)
    assert linha["leak_client"] == ap.cliente and "Permission denied" in str(linha["leak_detail"])
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
    assert (_linha(parque)["leak_rev"], _linha(parque)["leak_result"]) == (1, 1)
    assert await _passo(parque, "ligou")                                          # trafego_verificado: só confere
    assert ap.paradas == 1
    rede.pedir_reaplicacao(st, "android-01", "teste")
    # A prova da rev 1 continua escrita, mas não vale para a rev 2: nem para a porta, nem para a medição.
    assert _linha(parque)["leak_rev"] == 1 and rede.bloqueio_provado(st.db.one(
        "SELECT * FROM device_network WHERE instance_id='android-01'")) is False
    await _ate_conectado(parque, ap)
    _liberar_a_porta(parque)
    assert await _passo(parque, "tarefa") and _linha(parque)["state"] == "configurado"
    await _reiniciar(parque, ap)
    _liberar_a_porta(parque)
    assert await _passo(parque, "tarefa") and _linha(parque)["state"] == "trafego_verificado"
    assert ap.paradas == 2 and (_linha(parque)["leak_rev"], _linha(parque)["leak_result"]) == (2, 1)


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
    assert linha["leak_rev"] is None and linha["leak_pending"] == 0            # adiado não é desfecho: nada gravado
    # Enquanto o teste segue adiado, a sonda NÃO roda de novo para escrever o mesmo `parcial` (android-05, 30/09: 34
    # medições em seis horas com um objetivo parado). A passada confere, dispensa a medição e volta a esperar.
    st.rede_convergencia.memoria("android-01").espera_ate = 0.0
    st.rede_convergencia.memoria("android-01").ultima_verificacao = -1e9
    assert await _passo(parque, "varredura")
    assert st.db.scalar("SELECT COUNT(*) FROM network_measurements") == 1 and ap.paradas == 0
    [ultimo] = st.db.query("SELECT result FROM commands WHERE verb='device.network' ORDER BY created_at DESC LIMIT 1")
    assert "medição dispensada" in str(ultimo["result"])
    assert await _passo(parque, "varredura") is False                          # e a espera volta a valer
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


async def test_objetivo_de_execucao_encerrada_nao_adia_o_teste_de_vazamento(parque: Harness) -> None:
    """Item 25.12 (achado real do 03/10, android-03): o objetivo `waiting_user` de 02/10 estava numa execução
    `completed_with_issues` (o rollup de quem espera uma pessoa sem nada rodando), e o teste de vazamento ficou adiado
    por ~1 h ("há um objetivo no meio"). Execução em estado terminal não segura; a viva (`running`) segura."""
    from app.util import now_iso

    st = parque.state
    assert st is not None
    conv, rt = st.rede_convergencia, st.devices.devices["android-01"]
    assert conv._vazamento_adiado(rt) is None                                                  # type: ignore[arg-type]
    st.db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, instance_ids, created_at)"
                  " VALUES ('r-teste-25-12','k-teste-25-12','abrir','execute','running','[\"android-01\"]',?)",
                  (now_iso(),))
    st.db.execute("INSERT INTO objectives(id, run_id, instance_id, status) VALUES ('o-teste-25-12','r-teste-25-12',"
                  "'android-01','waiting_user')")
    assert "há um objetivo no meio" in (conv._vazamento_adiado(rt) or "")                      # type: ignore[arg-type]
    assert conv._quem_segura_o_reinicio(rt) == "objetivo o-teste-25-12 em andamento"           # type: ignore[arg-type]
    st.db.execute("UPDATE runs SET status='completed_with_issues' WHERE id='r-teste-25-12'")
    assert conv._vazamento_adiado(rt) is None                                                  # type: ignore[arg-type]
    assert conv._quem_segura_o_reinicio(rt) is None                                            # type: ignore[arg-type]


# ============================================================================ prova durável de vazamento (item 29.2)
# O defeito (P16, medido em 30/09): a prova do teste de vazamento vivia só na memória da convergência. Um reinício do
# backend a perdia, e a remedição seguinte (a 90% da validade) parava o cliente VPN de novo em todo aparelho com
# bloqueio — 11 reinícios de aparelho por um reinício do backend. Aqui o "reinício do backend" é o que ele é para a
# convergência: a memória some, o banco fica.
def _reiniciar_o_backend(parque: Harness) -> None:
    conv = parque.state.rede_convergencia                                           # type: ignore[union-attr]
    conv._mem.clear()
    conv._reinicio_agendado.clear()


def _envelhecer_medicao(parque: Harness, horas: float, iid: str = "android-01") -> None:
    """A última medição passa a ter `horas` de idade (a linha e a medição que ela aponta, juntas)."""
    from datetime import datetime, timedelta, timezone

    st = parque.state
    assert st is not None
    antes = st.db.one("SELECT verified_at FROM device_network WHERE instance_id=?", (iid,))["verified_at"]
    quando = (datetime.now(timezone.utc) - timedelta(hours=horas)).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z")
    st.db.execute("UPDATE network_measurements SET measured_at=? WHERE instance_id=? AND measured_at=?",
                  (quando, iid, antes))
    st.db.execute("UPDATE device_network SET verified_at=? WHERE instance_id=?", (quando, iid))


async def _ate_verificado(parque: Harness, ap: SondaFalsa, iid: str = "android-01") -> None:
    """Aplicar, conectar, o teste de vazamento (o cliente parado, o reinício) e a medição: `trafego_verificado`."""
    await _ate_conectado(parque, ap, iid)
    assert await _passo(parque, "tarefa", iid) and _linha(parque, iid)["state"] == "configurado"
    await _reiniciar(parque, ap, iid)
    _liberar_a_porta(parque, iid)
    assert await _passo(parque, "tarefa", iid) and _linha(parque, iid)["state"] == "trafego_verificado"
    assert ap.paradas == 1


async def test_reinicio_do_backend_com_prova_valida_nao_para_o_cliente_nem_reinicia_o_aparelho(
        parque: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    st = parque.state
    assert st is not None
    ap, reinicios = _preparar_sonda(parque, monkeypatch)
    await _ate_verificado(parque, ap)
    pedidos = len(reinicios.pedidos)
    # O backend reinicia (deploy). A readoção confere; a medição perto de vencer é refeita pela varredura — a janela
    # em que, antes, o teste de vazamento era refeito e o aparelho reiniciava.
    _reiniciar_o_backend(parque)
    assert await _passo(parque, "ligou")                                            # conferir: leitura, sem efeito
    _envelhecer_medicao(parque, 5.5)
    assert st.rede_convergencia._acao(st.db.one("SELECT * FROM device_network"), "varredura") == "verificar"
    assert await _passo(parque, "varredura")
    await asyncio.sleep(0.2)
    linha = _linha(parque)
    assert ap.paradas == 1, "o cliente VPN foi parado de novo depois do reinício do backend"
    assert len(reinicios.pedidos) == pedidos, "o aparelho foi reiniciado de novo depois do reinício do backend"
    assert linha["state"] == "trafego_verificado" and st.rede_convergencia.motivo_de_espera("android-01") is None
    # A medição NOVA leva o bloqueio provado, vindo da prova da linha (não de um teste novo).
    novas = st.db.query("SELECT leak_blocked, detail FROM network_measurements ORDER BY id")
    assert len(novas) == 2 and novas[-1]["leak_blocked"] == 1 and "Permission denied" in str(novas[-1]["detail"])
    assert (linha["leak_rev"], linha["leak_result"], linha["leak_client"]) == (1, 1, ap.cliente)
    # O relógio da medição barata não apaga a prova: vencida de vez (7 h), a porta pede medição e ela sai sem teste.
    _reiniciar_o_backend(parque)
    _envelhecer_medicao(parque, 7)
    assert "venceu" in (st.rede_convergencia.motivo_de_espera("android-01") or "")
    await parque.wait(lambda: st.db.scalar("SELECT COUNT(*) FROM network_measurements") == 3, what="remedição")
    await parque.wait(lambda: st.rede_convergencia.motivo_de_espera("android-01") is None, what="porta liberada")
    assert ap.paradas == 1 and len(reinicios.pedidos) == pedidos


async def test_cliente_vpn_de_outra_instalacao_invalida_a_prova(parque: Harness,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    st = parque.state
    assert st is not None
    ap, _ = _preparar_sonda(parque, monkeypatch)
    await _ate_verificado(parque, ap)
    assert st.rede_convergencia.motivo_de_espera("android-01") is None
    # O cliente foi atualizado (ou reinstalado) e o perfil segue lá: a pasta de instalação mudou. A conferência apaga
    # a prova, a porta deixa de aceitá-la (ela só lê o banco) e o passo seguinte refaz o teste.
    ap.instalacao = "inst2"
    st.rede_convergencia.memoria("android-01").ultima_conferencia = None
    assert await _passo(parque, "ligou")
    linha = _linha(parque)
    assert linha["state"] == "trafego_verificado" and linha["leak_rev"] is None and linha["leak_result"] is None
    assert "o cliente VPN instalado mudou" in str(linha["leak_detail"]) and "inst1" in str(linha["leak_detail"])
    _liberar_a_porta(parque)
    espera = st.rede_convergencia.motivo_de_espera("android-01") or ""
    assert "não tem prova que valha" in espera
    await parque.wait(lambda: ap.paradas == 2, what="teste refeito com o cliente novo")
    await parque.wait(lambda: (_linha(parque) or {}).get("leak_result") == 1, what="desfecho gravado")
    assert _linha(parque)["leak_client"] == ap.cliente and "inst2" in ap.cliente


async def test_wipe_apaga_a_prova(parque: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    st = parque.state
    assert st is not None
    ap, _ = _preparar_sonda(parque, monkeypatch)
    await _ate_verificado(parque, ap)
    st.rede_convergencia.invalidar("android-01", "wipe")
    linha = _linha(parque)
    assert linha["state"] == "pendente"
    assert (linha["leak_rev"], linha["leak_client"], linha["leak_result"], linha["leak_at"]) == (None, None, None, None)
    assert "dados do aparelho apagados (wipe)" in str(linha["leak_detail"])


async def test_verify_apaga_a_prova_e_o_pedido_sobrevive_ao_reinicio_do_backend(
        parque: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    st = parque.state
    assert st is not None
    ap, _ = _preparar_sonda(parque, monkeypatch)
    await _ate_verificado(parque, ap)
    r = st.rede_convergencia.pedir_verificacao("android-01", "teste")
    assert "REINICIA" in str(r["reason"]) and "a tarefa com rede exigida" in str(r["reason"])
    linha = _linha(parque)
    assert linha["state"] == "trafego_verificado" and linha["leak_rev"] is None
    assert "verificação pedida por teste" in str(linha["leak_detail"])
    # O pedido estava só na memória; o backend reinicia antes do ponto seguro. A prova apagada está no banco, e é a
    # falta dela que dispara o teste: o pedido de refazer não se perde.
    _reiniciar_o_backend(parque)
    assert "não tem prova que valha" in (st.rede_convergencia.motivo_de_espera("android-01") or "")
    await parque.wait(lambda: ap.paradas == 2, what="teste refeito depois do reinício do backend")
    await parque.wait(lambda: (_linha(parque) or {}).get("leak_result") == 1, what="desfecho gravado")


async def test_tunel_caido_mantem_a_prova_e_o_bloqueio(parque: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    st = parque.state
    assert st is not None
    ap, reinicios = _preparar_sonda(parque, monkeypatch)
    await _ate_verificado(parque, ap)
    comandos = len(ap.comandos)
    # O túnel cai (o cliente morreu) com a configuração no lugar: deriva → `configurado` → reinício. A prova fica, e
    # nada afrouxa o bloqueio: o aparelho segue sem saída fora da VPN até o túnel voltar.
    ap.tun = ap.vpn = False
    ap.uptime = 5000
    st.rede_convergencia.memoria("android-01").ultima_conferencia = None
    assert await _passo(parque, "ligou")
    linha = _linha(parque)
    assert linha["state"] == "configurado" and "túnel caído" in str(linha["detail"])
    assert (linha["leak_rev"], linha["leak_result"]) == (1, 1)
    assert ap.lockdown == "1" and ap.always_on == PKG and ap.regras is True
    assert not any("always_on_vpn" in c and "settings put" in c for c in ap.comandos[comandos:])
    assert "reinício" in (st.rede_convergencia.motivo_de_espera("android-01") or "")     # a tarefa espera
    # O boot religa; a medição usa a prova da linha, sem parar o cliente de novo.
    await _reiniciar(parque, ap)
    assert await _passo(parque, "ligou") and _linha(parque)["state"] == "trafego_verificado"
    assert ap.paradas == 1


async def test_inconclusivo_nunca_aprova_e_nao_se_repete(parque: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    st = parque.state
    assert st is not None
    ap, reinicios = _preparar_sonda(parque, monkeypatch)
    await _ate_conectado(parque, ap)
    ap.religa_sempre = True                 # o always-on religa o cliente antes da sonda, em todas as tentativas
    assert await _passo(parque, "tarefa")
    linha = _linha(parque)
    assert ap.paradas == 5 and linha["state"] == "parcial"
    assert (linha["leak_rev"], linha["leak_result"], linha["leak_pending"]) == (1, None, 0)
    assert "não medido" in str(linha["leak_detail"])
    [m] = st.db.query("SELECT leak_blocked FROM network_measurements")
    assert m["leak_blocked"] is None
    espera = st.rede_convergencia.motivo_de_espera("android-01") or ""
    assert "não foi provado" in espera and "peça Verificar" in espera
    # Não se refaz sozinho: nem pela varredura, nem pela porta, nem depois de um reinício do backend — cada teste
    # para o cliente VPN, e um inconclusivo repetido seria um aparelho em laço.
    for motivo in ("varredura", "tarefa"):
        st.rede_convergencia.memoria("android-01").espera_ate = 0.0
        st.rede_convergencia.memoria("android-01").ultima_verificacao = -1e9
        _liberar_a_porta(parque)
        assert await _passo(parque, motivo) is False
    _reiniciar_o_backend(parque)
    assert await _passo(parque, "ligou")
    await asyncio.sleep(0.2)
    assert ap.paradas == 5 and _linha(parque)["state"] == "parcial"
    assert st.db.scalar("SELECT COUNT(*) FROM network_measurements") == 1          # nem medição à toa
    # Quem destrava é a pessoa: o pedido apaga o desfecho e o teste é refeito (agora o always-on não atrapalha).
    ap.religa_sempre = False
    st.rede_convergencia.pedir_verificacao("android-01", "teste")
    assert await _passo(parque, "varredura")
    assert ap.paradas == 6 and _linha(parque)["leak_result"] == 1


async def test_intencao_gravada_antes_do_force_stop_e_ensaio_interrompido_nao_se_repete(
        parque: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.devices import rede_convergencia

    st = parque.state
    assert st is not None
    ap, reinicios = _preparar_sonda(parque, monkeypatch)
    await _ate_conectado(parque, ap)
    visto: dict[str, object] = {}

    async def backend_cai_no_meio(aparelho: SondaFalsa, _cfg: object, _pkg: str) -> object:
        # No instante em que o cliente vai ser parado, a intenção JÁ está no banco.
        visto.update(_linha(parque) or {})
        aparelho.paradas += 1
        aparelho.parado = True
        aparelho.tun = aparelho.vpn = False
        raise asyncio.CancelledError()                     # o backend para com o cliente VPN parado

    monkeypatch.setattr(rede_convergencia, "sondar_vazamento", backend_cai_no_meio)
    with pytest.raises(asyncio.CancelledError):
        await _passo(parque, "tarefa")
    assert (visto["leak_pending"], visto["leak_rev"], visto["leak_client"], visto["leak_result"]) == (
        1, 1, ap.cliente, None)
    linha = _linha(parque)
    assert linha["leak_pending"] == 1 and linha["leak_result"] is None             # sem desfecho gravado
    monkeypatch.undo()
    monkeypatch.setattr(despacho, "pedir_ciclo_de_vida", reinicios)
    # O backend volta. O aparelho está sem túnel (o cliente ficou parado) e com o bloqueio: deriva → `configurado` →
    # reinício, pelo caminho de sempre. O cliente NÃO é parado de novo.
    _reiniciar_o_backend(parque)
    ap.uptime = 5000
    pedidos = len(reinicios.pedidos)
    assert await _passo(parque, "ligou")
    assert _linha(parque)["state"] == "configurado" and ap.paradas == 1
    assert await _passo(parque, "varredura")                       # `configurado` sem boot desde a queda: pede o reinício
    await parque.wait(lambda: len(reinicios.pedidos) == pedidos + 1, what="reinício que religa o cliente")
    await _reiniciar(parque, ap)
    # Conectado de novo: o ensaio que ficou sem desfecho é fechado como inconclusivo — não é repetido às cegas.
    assert await _passo(parque, "ligou")
    linha = _linha(parque)
    assert ap.paradas == 1, "o teste destrutivo foi repetido depois do reinício no meio do ensaio"
    assert (linha["leak_pending"], linha["leak_rev"], linha["leak_result"]) == (0, 1, None)
    assert "interrompido" in str(linha["leak_detail"]) and linha["state"] == "parcial"
    assert "não foi provado" in (st.rede_convergencia.motivo_de_espera("android-01") or "")


async def test_vazou_fica_parcial_e_a_tarefa_espera(parque: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    st = parque.state
    assert st is not None
    ap, _ = _preparar_sonda(parque, monkeypatch)
    await _ate_conectado(parque, ap)
    # O dumpsys diz que há regras de bloqueio, e mesmo assim a sonda sem túnel sai pela física: é para isto que o
    # teste existe. O always-on religa o cliente em seguida, e a medição sai na mesma passada.
    eco = ap._eco

    def vazando(comando: str) -> str:
        if ap.tun:
            return eco(comando)
        ap.regras = False
        try:
            return eco(comando)
        finally:
            ap.regras = True

    ap._eco = vazando                                                              # type: ignore[method-assign]
    ap.religa_sozinho = True
    assert await _passo(parque, "tarefa")
    linha = _linha(parque)
    assert (linha["leak_rev"], linha["leak_result"]) == (1, 0) and "VAZOU" in str(linha["leak_detail"])
    assert linha["state"] == "parcial" and "não foi provado" in str(linha["detail"])
    [m] = st.db.query("SELECT leak_blocked FROM network_measurements")
    assert m["leak_blocked"] == 0
    assert st.rede_convergencia.motivo_de_espera("android-01") is not None
    _liberar_a_porta(parque)
    st.rede_convergencia.memoria("android-01").espera_ate = 0.0
    assert await _passo(parque, "tarefa") is False and ap.paradas == 1              # nem teste nem medição de novo


async def test_cliente_parado_pelo_teste_e_religado_pelo_start_da_interface_sem_reiniciar_o_aparelho(
        parque: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    """Item 29.3 + W8: o teste de vazamento deixa o cliente parado. Antes do reinício, o Start da interface (o tile não
    recalcula o `serviceMode`); religado, a medição sai na mesma passada e o teste deixa de custar um reinício."""
    st = parque.state
    assert st is not None
    ap, reinicios = _preparar_sonda(parque, monkeypatch)
    await _ate_conectado(parque, ap)
    ap.ui_religa, ap.modo_vpn = True, False                                          # o stale do 09: o tile não serviria
    pedidos = len(reinicios.pedidos)
    assert await _passo(parque, "tarefa")
    await asyncio.sleep(0.2)
    linha = _linha(parque)
    assert ap.paradas == 1 and ap.starts_na_ui == 1 and ap.cliques_no_tile == 0 and len(reinicios.pedidos) == pedidos
    assert linha["state"] == "trafego_verificado" and (linha["leak_rev"], linha["leak_result"]) == (1, 1)
    [m] = st.db.query("SELECT leak_blocked FROM network_measurements")
    assert m["leak_blocked"] == 1 and ap.lockdown == "1" and ap.regras is True


def _como_antes_da_063(parque: Harness, iid: str = "android-01") -> None:
    """A linha como a migração 063 a encontra num aparelho que o código anterior verificou: sem nada em `leak_*`; a
    prova só existe no histórico (o comando que fez o teste e as medições com o bloqueio provado)."""
    parque.state.db.execute(                                                        # type: ignore[union-attr]
        "UPDATE device_network SET leak_rev=NULL, leak_client=NULL, leak_result=NULL, leak_at=NULL, leak_detail=NULL,"
        " leak_pending=0 WHERE instance_id=?", (iid,))


async def test_prova_anterior_a_migracao_e_adotada_sem_parar_o_cliente(parque: Harness,
                                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    st = parque.state
    assert st is not None
    ap, reinicios = _preparar_sonda(parque, monkeypatch)
    await _ate_verificado(parque, ap)
    _como_antes_da_063(parque)
    _reiniciar_o_backend(parque)                                                    # o deploy que traz a 063
    pedidos = len(reinicios.pedidos)
    # Sem prova na linha, a porta não aceita o `trafego_verificado` (ela só lê o banco) ...
    assert "não tem prova que valha" in (st.rede_convergencia.motivo_de_espera("android-01") or "")
    # ... e a passada que ela dispara ADOTA a prova do histórico, em vez de parar o cliente de novo.
    await parque.wait(lambda: (_linha(parque) or {}).get("leak_result") == 1, what="prova adotada")
    await parque.wait(lambda: st.rede_convergencia.motivo_de_espera("android-01") is None, what="porta liberada")
    await asyncio.sleep(0.2)
    linha = _linha(parque)
    assert ap.paradas == 1 and len(reinicios.pedidos) == pedidos
    assert (linha["leak_rev"], linha["leak_client"], linha["leak_pending"]) == (1, ap.cliente, 0)
    assert "adotada do histórico" in str(linha["leak_detail"]) and "2023-11-14" in str(linha["leak_detail"])
    assert linha["state"] == "trafego_verificado"
    ultima = st.db.query("SELECT leak_blocked, detail FROM network_measurements ORDER BY id")[-1]
    assert ultima["leak_blocked"] == 1 and "adotada do histórico" in str(ultima["detail"])
    assert any("stat -c %Y" in c for c in ap.comandos)                              # a data do cliente veio do aparelho


@pytest.mark.parametrize("caso", ["cliente_mais_novo_que_o_teste", "ultima_medicao_sem_prova", "prova_apagada",
                                  "relogio_do_aparelho_atrasado"])
async def test_prova_anterior_que_nao_se_demonstra_nao_e_adotada(parque: Harness, monkeypatch: pytest.MonkeyPatch,
                                                                caso: str) -> None:
    """Sem a correspondência demonstrada, nada de adoção: o teste é feito (e é ele que prova)."""
    st = parque.state
    assert st is not None
    ap, _ = _preparar_sonda(parque, monkeypatch)
    await _ate_verificado(parque, ap)
    _como_antes_da_063(parque)
    if caso == "cliente_mais_novo_que_o_teste":
        import time as _time
        ap.instalado_em = int(_time.time()) + 60            # o cliente foi gravado DEPOIS do teste que está no histórico
    elif caso == "ultima_medicao_sem_prova":                # o caso do android-05 em 30/09: a última medição não provou
        st.db.execute("UPDATE network_measurements SET leak_blocked=NULL WHERE id=(SELECT MAX(id) FROM"
                      " network_measurements)")
    elif caso == "relogio_do_aparelho_atrasado":
        # A data do APK é do relógio do aparelho: com ele uma hora atrás, "anterior ao teste" não prova nada.
        ap.relogio_atrasado_s = 3600
    else:                                                   # a prova foi apagada depois da 063 (pedido, cliente, wipe)
        assert rede.apagar_prova_de_vazamento(st, "android-01", "teste") is True
    _reiniciar_o_backend(parque)
    row = st.db.one("SELECT * FROM device_network WHERE instance_id='android-01'")
    if caso in ("ultima_medicao_sem_prova", "prova_apagada"):
        assert rede.prova_anterior(st, row) is None
    assert await _passo(parque, "varredura")
    assert ap.paradas == 2 and "adotada" not in str(_linha(parque)["leak_detail"])


async def test_adocao_sem_a_data_do_cliente_nao_adota_nem_para_o_cliente(parque: Harness,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    st = parque.state
    assert st is not None
    ap, _ = _preparar_sonda(parque, monkeypatch)
    await _ate_verificado(parque, ap)
    _como_antes_da_063(parque)
    _reiniciar_o_backend(parque)
    ap.instalado_em = None                                  # a leitura do aparelho não trouxe a data
    assert await _passo(parque, "varredura")
    linha = _linha(parque)
    assert ap.paradas == 1 and linha["leak_rev"] is None and linha["leak_pending"] == 0
    [ultimo] = st.db.query("SELECT state, reason FROM commands WHERE verb='device.network' ORDER BY created_at DESC"
                           " LIMIT 1")
    assert ultimo["state"] == "failed" and "data de instalação" in str(ultimo["reason"])
    assert await _passo(parque, "varredura") is False       # espera antes de tentar de novo


@pytest.mark.parametrize("falha", ["data_do_apk_nao_lida", "leitura_como_root", "tunel_caido_na_primeira_passada"])
async def test_falha_antes_da_adocao_nao_vira_teste_destrutivo_na_passada_seguinte(
        parque: Harness, monkeypatch: pytest.MonkeyPatch, falha: str) -> None:
    """Achado da revisão (A1): a primeira passada depois do deploy falha por um motivo qualquer (uma leitura que não
    veio, o túnel caído), e o comando que falhou vira o mais novo do histórico. Ele não é evidência de nada: a passada
    seguinte ainda adota a prova, em vez de parar o cliente VPN de um aparelho com conta real."""
    st = parque.state
    assert st is not None
    ap, reinicios = _preparar_sonda(parque, monkeypatch)
    await _ate_verificado(parque, ap)
    _como_antes_da_063(parque)
    _reiniciar_o_backend(parque)
    if falha == "data_do_apk_nao_lida":
        ap.instalado_em = None
        assert await _passo(parque, "varredura")
        ap.instalado_em = 1_700_000_000
    elif falha == "leitura_como_root":
        ap.uid = 0
        assert await _passo(parque, "ligou")
        ap.uid = 2000
    else:
        ap.tun = ap.vpn = False
        ap.uptime = 5000
        assert await _passo(parque, "varredura") and _linha(parque)["state"] == "configurado"
        assert await _passo(parque, "varredura")                       # pede o reinício que religa o cliente
        await _reiniciar(parque, ap)
    assert ap.paradas == 1 and _linha(parque)["leak_rev"] is None
    mem = st.rede_convergencia.memoria("android-01")
    mem.espera_ate, mem.ultima_verificacao = 0.0, -1e9
    assert await _passo(parque, "ligou")
    await asyncio.sleep(0.2)
    linha = _linha(parque)
    assert ap.paradas == 1, "a falha da primeira passada virou teste destrutivo na segunda"
    assert "adotada do histórico" in str(linha["leak_detail"]) and linha["state"] == "trafego_verificado"


@pytest.mark.parametrize("gesto", ["verify", "wipe"])
async def test_pedido_de_refazer_e_wipe_valem_tambem_na_linha_de_antes_da_063(
        parque: Harness, monkeypatch: pytest.MonkeyPatch, gesto: str) -> None:
    """Achado da revisão (A2): na linha que o mecanismo novo nunca escreveu não há prova a apagar — mas o MOTIVO tem de
    ficar, porque é a falta dele que permite a adoção. Sem isso, o `verify` respondia "o teste é refeito" e a passada
    seguinte adotava a prova antiga, sem teste nenhum."""
    st = parque.state
    assert st is not None
    ap, _ = _preparar_sonda(parque, monkeypatch)
    await _ate_verificado(parque, ap)
    _como_antes_da_063(parque)
    _reiniciar_o_backend(parque)
    if gesto == "verify":
        st.rede_convergencia.pedir_verificacao("android-01", "teste")
        assert "verificação pedida por teste" in str(_linha(parque)["leak_detail"])
        assert await _passo(parque, "varredura")
        assert ap.paradas == 2 and "adotada" not in str(_linha(parque)["leak_detail"])
    else:
        st.rede_convergencia.invalidar("android-01", "wipe")
        linha = _linha(parque)
        assert linha["state"] == "pendente" and "dados do aparelho apagados (wipe)" in str(linha["leak_detail"])
        assert rede.prova_anterior(st, st.db.one("SELECT * FROM device_network WHERE instance_id='android-01'")) is None


async def test_linha_apagada_e_recriada_nao_adota_a_prova_da_anterior(parque: Harness,
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    """Achado da revisão (A3): a rede foi tirada do aparelho (a linha sai) e pedida de novo. O histórico do mesmo id —
    comandos `verificar` da rev 1 com o bloqueio provado — é de outra configuração: a linha nova nasce marcada e faz o
    seu próprio teste."""
    st = parque.state
    assert st is not None
    ap, reinicios = _preparar_sonda(parque, monkeypatch)
    await _ate_verificado(parque, ap)
    st.rede_convergencia._apagar_linha("android-01", "rede tirada (teste)")
    ap2, _ = _preparar_sonda(parque, monkeypatch, reinicios=reinicios)
    linha = _linha(parque)
    assert linha["desired_rev"] == 1 and "nenhum teste de vazamento" in str(linha["leak_detail"])
    assert rede.prova_anterior(st, st.db.one("SELECT * FROM device_network WHERE instance_id='android-01'")) is None
    await _ate_conectado(parque, ap2)
    _liberar_a_porta(parque)
    assert await _passo(parque, "tarefa")
    assert ap2.paradas == 1 and "adotada" not in str(_linha(parque)["leak_detail"])


async def test_desfecho_que_nao_aprova_com_a_sonda_sem_ip_nao_mede_a_cada_passada(
        parque: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    """Achado da revisão (A4): com o teste inconclusivo gravado, a medição só é refeita para a saída não envelhecer. Mas
    sem IP medido (o eco fora, o servidor fora) o `verified_at` não anda, a medição segue "envelhecida" e cada passada
    media de novo. A espera da medição que não verificou vale aqui também."""
    st = parque.state
    assert st is not None
    ap, _ = _preparar_sonda(parque, monkeypatch)
    await _ate_conectado(parque, ap)
    ap.religa_sempre = True
    assert await _passo(parque, "tarefa") and _linha(parque)["state"] == "parcial"
    _envelhecer_medicao(parque, 5.5)
    ap.ip4 = None
    mem = st.rede_convergencia.memoria("android-01")
    mem.espera_ate, mem.ultima_verificacao = 0.0, -1e9
    antes = st.db.scalar("SELECT COUNT(*) FROM network_measurements")
    passadas = []
    for _ in range(6):
        _liberar_a_porta(parque)
        passadas.append(await _passo(parque, "varredura"))
    assert passadas == [True, False, False, False, False, False]
    assert st.db.scalar("SELECT COUNT(*) FROM network_measurements") == antes + 1 and ap.paradas == 5


def test_desfecho_de_revisao_antiga_nao_vira_prova_da_nova(parque: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    """O CAS do desfecho: a reaplicação que chega no meio do ensaio (revisão nova) não herda o resultado dele."""
    st = parque.state
    assert st is not None
    ap, _ = _preparar_sonda(parque, monkeypatch)
    st.db.execute("UPDATE device_network SET state='conectado', applied_rev=1 WHERE instance_id='android-01'")
    # Só com a revisão pedida APLICADA o ensaio começa.
    assert rede.marcar_ensaio_de_vazamento(st, "android-01", rev=2, cliente=ap.cliente) is False
    assert rede.marcar_ensaio_de_vazamento(st, "android-01", rev=1, cliente=ap.cliente) is True
    rede.pedir_reaplicacao(st, "android-01", "teste")                               # rev 2, no meio do ensaio
    assert rede.gravar_prova_de_vazamento(st, "android-01", rev=1, cliente=ap.cliente, resultado=True,
                                          quando="2026-09-30T12:00:00Z", detalhe="Permission denied") is False
    row = st.db.one("SELECT * FROM device_network WHERE instance_id='android-01'")
    assert row["leak_result"] is None and rede.bloqueio_provado(row) is False
    assert rede.prova_de_vazamento(row).situacao(2, ap.cliente) == "outra_revisao"
    # As sete situações, pela ordem em que decidem.
    P = rede.ProvaDeVazamento
    assert P(None, None, None, None, None, False).situacao(1) == "ausente"
    assert P(1, "c", True, "t", "d", False).situacao(2) == "outra_revisao"
    assert P(1, "c", None, "t", "d", True).situacao(1, "c") == "interrompida"
    assert P(1, "c", True, "t", "d", False).situacao(1, "outro") == "outro_cliente"
    assert P(1, "c", True, "t", "d", False).situacao(1, "c") == "vale"
    assert P(1, "c", False, "t", "d", False).situacao(1, "c") == "vazou"
    assert P(1, "c", None, "t", "d", False).situacao(1, "c") == "inconclusiva"


# ============================================================================ túnel morto (item 25.12)
# Caso real: 03/10, android-03 — o reinício do backend derrubou o túnel; o cliente seguia "no ar" e a sonda não media IP
# (medição #216), mas o `trafego_verificado` ficava como estava e a internet só voltou com um reinício manual. Aqui o
# aparelho é o falso da sonda (`simulated`): nada de emulador, nada de VPN, nenhum IP de verdade.
@dataclass
class SondaDeTunelMorto(SondaFalsa):
    """O `am force-stop` do cliente (o gesto da convergência, fora do teste de vazamento): o `tun0` cai e o always-on
    sobe o cliente de novo. `volta_na`: a partir de qual `force-stop` (1 = o primeiro) o túnel religado SAI (0 = nunca
    sai: o cliente volta, mas a sonda segue sem IP); `sem_tun_depois`: o cliente não volta (sem `tun0`)."""

    volta_na: int = 0
    sem_tun_depois: bool = False
    forcados: int = 0

    async def shell(self, comando: str, *, timeout: float = 40) -> str:
        saida = await super().shell(comando, timeout=timeout)
        if comando.startswith("am force-stop ") and "==SONDA-INICIO==" not in comando:
            self.forcados += 1
            self.tun = self.vpn = not self.sem_tun_depois
            if self.volta_na and self.forcados >= self.volta_na:
                self.ip4 = IP
        return saida


async def _verificado_e_morto(parque: Harness, monkeypatch: pytest.MonkeyPatch, *, policy: str = "exigida_com_bloqueio",
                              **kw: object) -> tuple[SondaDeTunelMorto, Reinicios]:
    """`trafego_verificado` e, depois, o túnel morre (a sonda deixa de medir IP) e a verificação seguinte é pedida."""
    st = parque.state
    assert st is not None
    _, reinicios = _preparar_sonda(parque, monkeypatch, policy=policy)
    ap = SondaDeTunelMorto(id="android-01", **kw)                                   # type: ignore[arg-type]
    st.rede_convergencia._aparelho = lambda _s, _rt: ap
    conv = st.rede_convergencia
    conv.pausa_do_force_stop_s = conv.espera_do_religar_s = 0.0
    if policy == "exigida_com_bloqueio":
        await _ate_verificado(parque, ap)
    else:
        await _ate_conectado(parque, ap)
        assert await _passo(parque, "tarefa") and _linha(parque)["state"] == "trafego_verificado"
    ap.ip4 = None                                                                   # o túnel morreu: a sonda não sai
    conv.memoria("android-01").verificacao_pedida = True                            # o gesto do backend ao subir (B)
    return ap, reinicios


def _avisos_de_tunel_morto(parque: Harness) -> list[dict[str, object]]:
    return [d for d in (json.loads(e["data"]) for e in parque.state.db.query(       # type: ignore[union-attr]
        "SELECT data FROM events WHERE kind='network.updated' AND level='warn' ORDER BY id"))
        if d.get("acao") == "tunel_morto"]


async def test_tunel_morto_religa_o_cliente_sem_reiniciar_e_mede_de_novo(parque: Harness,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    st = parque.state
    assert st is not None
    ap, reinicios = await _verificado_e_morto(parque, monkeypatch, volta_na=1)
    pedidos, medicoes, paradas = len(reinicios.pedidos), st.db.scalar("SELECT COUNT(*) FROM network_measurements"), ap.paradas
    antes = ap.forcados                                                             # a aplicação também para o cliente
    assert await _passo(parque, "varredura")
    linha = _linha(parque)
    assert linha["state"] == "trafego_verificado" and linha["egress_ipv4"] == IP
    assert ap.forcados - antes == 1                                                 # um gesto bastou
    assert st.db.scalar("SELECT COUNT(*) FROM network_measurements") == medicoes + 2    # a morta (#N) e a refeita
    morta = st.db.one("SELECT egress_ipv4 FROM network_measurements WHERE id=?",
                      (st.db.scalar("SELECT MAX(id) FROM network_measurements") - 1,))
    assert morta["egress_ipv4"] is None
    assert len(reinicios.pedidos) == pedidos                                        # sem reinício
    assert ap.paradas == paradas                                                    # o teste de vazamento NÃO foi refeito
    assert (linha["leak_rev"], linha["leak_result"]) == (1, 1)                       # e a prova segue na linha
    [aviso] = _avisos_de_tunel_morto(parque)
    assert aviso["instance_id"] == "android-01" and aviso["state"] == "conectado"
    assert st.rede_convergencia.motivo_de_espera("android-01") is None              # a tarefa passa de novo


@pytest.mark.parametrize("variante", ["cliente_volta_sem_saida", "cliente_nao_volta"])
async def test_tunel_morto_sem_volta_em_duas_tentativas_reinicia_sem_wipe(parque: Harness,
                                                                          monkeypatch: pytest.MonkeyPatch,
                                                                          variante: str) -> None:
    st = parque.state
    assert st is not None
    ap, reinicios = await _verificado_e_morto(parque, monkeypatch, sem_tun_depois=variante == "cliente_nao_volta")
    st.rede_convergencia.cfg.cliente_atividade = ""                                 # sem o Start da interface (outro teste)
    pedidos, paradas, antes = len(reinicios.pedidos), ap.paradas, ap.forcados
    assert await _passo(parque, "varredura")
    linha = _linha(parque)
    assert ap.forcados - antes == 2                                                         # duas tentativas, nenhuma a mais
    assert linha["state"] == "configurado"                                          # saiu de trafego_verificado
    assert "túnel morto" in str(linha["detail"]) and "não bastou em 2 tentativas" in str(linha["detail"])
    assert "sem apagar dados" in str(linha["detail"])
    assert ("o cliente não voltou" if variante == "cliente_nao_volta" else "a sonda segue sem IP") in str(linha["detail"])
    # Sem porta aberta para a tarefa (a linha já não é trafego_verificado) e com o rastro do motivo e do evento.
    assert "reinício" in (st.rede_convergencia.motivo_de_espera("android-01") or "")
    [aviso] = _avisos_de_tunel_morto(parque)
    assert aviso["acao"] == "tunel_morto"
    await asyncio.sleep(0.2)                                                        # o reinício sai depois do trabalho
    assert len(reinicios.pedidos) == pedidos + 1 and "túnel morto" in reinicios.pedidos[-1][1]
    assert not any("wipe" in c or "pm clear" in c for c in ap.comandos)             # nunca apaga dados
    assert ap.paradas == paradas and (linha["leak_rev"], linha["leak_result"]) == (1, 1)
    # Depois do boot o túnel sobe e sai: conectar → verificar → trafego_verificado, sem refazer o teste de vazamento.
    ap.ip4, ap.sem_tun_depois = IP, False
    await _reiniciar(parque, ap)
    _liberar_a_porta(parque)
    assert await _passo(parque, "tarefa") and _linha(parque)["state"] == "trafego_verificado"
    assert ap.paradas == paradas


async def test_tunel_morto_com_objetivo_no_meio_nao_abre_a_interface_do_cliente(parque: Harness,
                                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    """O Start da interface abre a tela do cliente VPN: com um objetivo no meio (a tela é a evidência dele) ele não é
    tentado. O `force-stop` do cliente não toca na tela e segue valendo; o reinício passa pela guarda de sempre."""
    from app.models import ObjectiveStatus

    st = parque.state
    assert st is not None
    ap, _ = await _verificado_e_morto(parque, monkeypatch, sem_tun_depois=True)
    porta = st.scheduler.rede_gate
    st.scheduler.rede_gate = lambda _iid: "segura (teste)"                          # o objetivo nasce sem ser despachado
    run = parque.run(["android-01"])
    oid = f"{run.id}:android-01"
    await parque.wait(lambda: st.db.one("SELECT 1 FROM objectives WHERE id=?", (oid,)) is not None, what="objetivo")
    st.repo.set_objective(oid, ObjectiveStatus.waiting_user, detail="esperando a pessoa (teste)")
    st.scheduler.rede_gate = porta
    antes, comandos = ap.forcados, len(ap.comandos)
    assert await _passo(parque, "varredura")
    linha = _linha(parque)
    assert linha["state"] == "configurado" and "Start da interface não tentado" in str(linha["detail"])
    assert ap.forcados - antes == 2 and not any(c.startswith("am start -n") for c in ap.comandos[comandos:])


async def test_sem_ip_fora_de_trafego_verificado_nao_dispara_o_gesto_de_tunel_morto(parque: Harness,
                                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """O gesto é só do `trafego_verificado`: o `conectado`/`parcial` sem IP (eco fora, servidor fora) segue só esperando a
    próxima medição, sem parar o cliente nem reiniciar."""
    st = parque.state
    assert st is not None
    _, reinicios = _preparar_sonda(parque, monkeypatch, policy="exigida")
    ap = SondaDeTunelMorto(id="android-01")
    st.rede_convergencia._aparelho = lambda _s, _rt: ap
    await _ate_conectado(parque, ap)
    ap.ip4, antes = None, ap.forcados
    assert await _passo(parque, "tarefa")                                            # conectado → medida sem IP
    assert _linha(parque)["state"] == "conectado" and ap.forcados == antes and _avisos_de_tunel_morto(parque) == []
    await asyncio.sleep(0.2)
    assert len(reinicios.pedidos) == 1                                              # só o da aplicação


@pytest.mark.parametrize("falha", ["webdriver_timeout", "trava"])
async def test_tunel_morto_start_da_interface_que_falha_ou_trava_conta_como_tentativa_e_reinicia(
        parque: Harness, monkeypatch: pytest.MonkeyPatch, falha: str) -> None:
    """Dado real de 03/10 (log do central, 06:09Z, android-06): o Start da interface do cliente falhou com
    `WebDriverException ... Timed out ... waiting for the root AccessibilityNodeInfo`. O gesto que falha, ou que trava,
    vale UMA tentativa (com teto de tempo), nunca laço: depois da segunda vem o reinício sem wipe."""
    from app.devices import rede_convergencia

    st = parque.state
    assert st is not None
    ap, reinicios = await _verificado_e_morto(parque, monkeypatch, sem_tun_depois=True)
    conv = st.rede_convergencia
    conv.prazo_da_interface_no_tunel_morto_s = 0.2
    chamadas: list[int] = []

    async def interface(*_a: object, **_k: object) -> object:
        chamadas.append(1)
        if falha == "trava":
            await asyncio.sleep(30)
        raise RuntimeError("WebDriverException: Timed out while waiting for the root AccessibilityNodeInfo")

    monkeypatch.setattr(rede_convergencia, "religar_pela_interface", interface)
    pedidos, antes = len(reinicios.pedidos), ap.forcados
    inicio = asyncio.get_running_loop().time()
    assert await _passo(parque, "varredura")
    assert asyncio.get_running_loop().time() - inicio < 5                           # o teto vale: nada de espera longa
    assert len(chamadas) == 2 and ap.forcados - antes == 2                          # duas tentativas, nenhuma a mais
    assert _linha(parque)["state"] == "configurado"
    assert "não bastou em 2 tentativas" in str(_linha(parque)["detail"])
    await asyncio.sleep(0.2)
    assert len(reinicios.pedidos) == pedidos + 1
    assert not any("wipe" in c or "pm clear" in c for c in ap.comandos)


# ============================================================================ o backend ao subir (item 25.12, B)
async def test_backend_ao_subir_mede_o_trafego_sem_apagar_a_prova_de_vazamento(parque: Harness,
                                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    """Todo reinício do backend derruba os túneis; o `trafego_verificado` não pode seguir valendo até a medição vencer.
    A medição é pedida SEM o `verify` (que apagaria a prova de vazamento e reiniciaria o aparelho com bloqueio)."""
    st = parque.state
    assert st is not None
    ap, reinicios = _preparar_sonda(parque, monkeypatch)
    await _ate_verificado(parque, ap)
    antes = _linha(parque)
    medicoes, paradas, pedidos = st.db.scalar("SELECT COUNT(*) FROM network_measurements"), ap.paradas, len(reinicios.pedidos)
    _reiniciar_o_backend(parque)                                                    # a memória some, o banco fica
    conv = st.rede_convergencia
    assert conv.verificar_ao_subir() == ["android-01"]
    # Sem a marca, `ligou` só CONFERIA (validade em dia): com ela, mede.
    assert await _passo(parque, "ligou")
    linha = _linha(parque)
    assert st.db.scalar("SELECT COUNT(*) FROM network_measurements") == medicoes + 1
    assert linha["state"] == "trafego_verificado"
    assert ap.paradas == paradas and len(reinicios.pedidos) == pedidos              # nada de teste de vazamento nem reinício
    assert all(linha[c] == antes[c] for c in ("leak_rev", "leak_client", "leak_result", "leak_at", "leak_detail"))
    assert "verificação pedida" not in str(linha["detail"])                         # o `detail` não é reescrito como no verify
    # Dessa vez o túnel morreu com o backend: a medição sem IP é o túnel morto, e a prova também fica.
    _reiniciar_o_backend(parque)
    conv.verificar_ao_subir()
    ap.ip4 = None
    conv.pausa_do_force_stop_s = conv.espera_do_religar_s = 0.0
    assert await _passo(parque, "ligou")
    assert _linha(parque)["state"] == "configurado" and "túnel morto" in str(_linha(parque)["detail"])
    assert _linha(parque)["leak_result"] == antes["leak_result"] and ap.paradas == paradas


async def test_backend_ao_subir_so_marca_politica_exigida_com_a_rede_conectada(parque: Harness,
                                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    st = parque.state
    assert st is not None
    _preparar_sonda(parque, monkeypatch, policy="livre")
    assert st.rede_convergencia.verificar_ao_subir() == []                           # `livre` nunca; `pendente` ainda não
    st.db.execute("UPDATE device_network SET state='conectado', applied_rev=1 WHERE instance_id='android-01'")
    assert st.rede_convergencia.verificar_ao_subir() == []                           # livre mesmo conectado
    st.db.execute("UPDATE device_network SET policy='exigida' WHERE instance_id='android-01'")
    assert st.rede_convergencia.verificar_ao_subir() == ["android-01"]
    assert st.rede_convergencia.memoria("android-01").verificacao_pedida is True
    st.db.execute("UPDATE device_network SET state='pendente' WHERE instance_id='android-01'")
    st.rede_convergencia._mem.clear()
    assert st.rede_convergencia.verificar_ao_subir() == []                           # ainda sem rede aplicada: nada a medir
