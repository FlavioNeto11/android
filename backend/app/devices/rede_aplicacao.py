"""A receita da rede NO APARELHO (ADR-056, item 25.4), exatamente a medida em 29/09 no android-05 (medição 25.1).

Cliente: sing-box (SFA, `io.nekohasekai.sfa`), o único que compõe VPN e proxy no mesmo `VpnService`. A receita:

1. o cliente instalado pelo fluxo de releases (quem instala é a convergência, que conhece a loja);
2. `cmd appops set <pkg> ACTIVATE_VPN allow`: a permissão de VPN sem o diálogo;
3. com a VPN DESLIGADA (`am force-stop`): com ela ligada, o download do perfil sai pelo túnel e não alcança o host;
4. o perfil do aparelho (com a chave dele) servido UMA vez por um HTTP efêmero em 127.0.0.1 do central, montado e
   entregue pelo consumidor restrito do cofre (`segredos_entregues`): o aparelho local chega por `10.0.2.2` (o
   medido); o do worker, por `adb reverse` (o túnel do adb já o alcança; prova só simulada, o aparelho do worker
   com rede é `not_run`, item 29.9);
5. `am start … sing-box://import-remote-profile?url=…#<nome>` e os toques achados pelo TEXTO na árvore de tela da
   plataforma: "No, thanks" (só na primeira execução), "OK" e "Create";
6. `sync` — OBRIGATÓRIO: o `restart` da plataforma era `emu kill` sem `sync`, e o perfil importado 26 s antes se
   perdeu (o `stop_process` agora sincroniza também, no central e no agente do worker);
7. `settings put secure always_on_vpn_app <pkg>` e, com a política `exigida_com_bloqueio`,
   `always_on_vpn_lockdown 1` (senão 0); relidos;
8. apagar `…/files/crash_reports/` (a queda do cliente grava a configuração COM a chave privada ali) e `sync`.

Always-on e bloqueio só valem no BOOT: quem pede o reinício é a convergência, depois que o trabalho solta o aparelho.
A conferência (`observar`) roda como uid 2000, nunca como root: o bloqueio não cobre o uid 0, e uma leitura como
root "provaria" o que não vale para os apps. O desfazer é o espelho: `settings delete`, `lockdown 0`, `force-stop`,
`sync` e o reinício (o bloqueio em memória só sai com reboot).

Nada aqui conhece o scheduler nem o comando: a porta `AparelhoDaRede` é o aparelho (shell, árvore de tela, toque,
`adb reverse`), e o teste a troca por um dublê.
"""
from __future__ import annotations

import asyncio
import functools
import http.server
import ipaddress
import json
import logging
import re
import secrets as pysecrets
import threading
import time
import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Protocol

from ..db import loads
from ..security.segredo_de_rede import Fonte, SegredoDeRedeError, segredos_entregues
from .rede_servidor import endereco_do_servidor, gerenciado_pelo_central, usuario_do_proxy

if TYPE_CHECKING:
    from ..config import RedeCfg
    from ..db import Database, Row
    from ..security.secret_store import SecretStore
    from ..state import AppState
    from .manager import DeviceRuntime
    from .rede_servidor import ServidorDeRede

log = logging.getLogger(__name__)

#: Os botões da importação no SFA 1.14.2 (release 739), na ordem medida. O primeiro só existe na primeira execução.
BOTAO_ATUALIZACAO = "No, thanks"
BOTAO_CONFIRMAR = "OK"
BOTAO_CRIAR = "Create"
#: Onde o SFA grava o relatório de falha — com a configuração inteira, chave privada incluída (medido em 18:29:04).
CRASH_REPORTS = "/sdcard/Android/data/{pkg}/files/crash_reports"
#: O endereço do host visto de dentro do emulador local (o NAT do emulador o leva ao 127.0.0.1 do central).
HOST_DO_EMULADOR = "10.0.2.2"
#: O endereço do túnel do aparelho no cliente (o `tun` do sing-box): o do piloto.
ENDERECO_DO_TUN = "172.19.0.1/30"
UID_DO_SHELL = 2000


class RedeAplicacaoError(RuntimeError):
    """Recusa ou falha da receita, com a frase que vai para o `detail`/`error` do aparelho. Nunca carrega segredo."""


# ============================================================================ o plano (o que o aparelho recebe)
@dataclass(frozen=True)
class TunelWg:
    """A VPN do cliente, sem o segredo: o segredo vem de `fonte_da_chave` (a chave do aparelho gerada pela
    plataforma, ou a do perfil de um servidor WireGuard externo)."""

    fonte_da_chave: Fonte
    address: str
    peer_host: str
    peer_port: int
    peer_public_key: str
    mtu: int = 1280
    allowed_ips: tuple[str, ...] = ("0.0.0.0/0", "::/0")
    keepalive: int = 25


@dataclass(frozen=True)
class ProxyDoCliente:
    tipo: Literal["socks", "http"]
    host: str
    port: int
    username: str | None
    fonte_da_senha: Fonte | None


@dataclass(frozen=True)
class Plano:
    """O que vai para ESTE aparelho na revisão `rev`. `vazio` = desfazer (nada pedido, algo aplicado antes)."""

    instance_id: str
    rev: int
    policy: str
    vpn: TunelWg | None
    proxy: ProxyDoCliente | None
    dns: str
    gerenciado: bool = False
    #: O aparelho está em OUTRA máquina (25.7): o id do worker, ou "externo" (celular sem worker). `None` = emulador
    #: deste host. Remoto recebe o perfil por `adb reverse` e alcança o servidor pelo `rede.servidor.endpoint_lan`.
    remoto: str | None = None

    @property
    def bloqueio(self) -> bool:
        return self.policy == "exigida_com_bloqueio"

    @property
    def vazio(self) -> bool:
        return self.vpn is None and self.proxy is None

    def fontes(self) -> dict[str, Fonte]:
        saida: dict[str, Fonte] = {}
        if self.vpn is not None:
            saida["chave"] = self.vpn.fonte_da_chave
        if self.proxy is not None and self.proxy.fonte_da_senha is not None:
            saida["senha_do_proxy"] = self.proxy.fonte_da_senha
        return saida

    def descrever(self) -> str:
        partes = []
        if self.vpn is not None:
            partes.append(f"VPN {self.vpn.address} → {self.vpn.peer_host}:{self.vpn.peer_port}"
                          + (" (servidor do central" + (f", pela LAN: aparelho de {self.remoto}" if self.remoto
                                                        else "") + ")" if self.gerenciado else ""))
        if self.proxy is not None:
            partes.append(f"proxy {self.proxy.tipo} {self.proxy.host}:{self.proxy.port}")
        partes.append("com bloqueio fora da VPN" if self.bloqueio else "sem bloqueio")
        return ", ".join(partes)


def _perfil(st: AppState, profile_id: str | None, kind: str) -> Row | None:
    if profile_id is None:
        return None
    row = st.db.one("SELECT * FROM network_profiles WHERE id=?", (profile_id,))
    if row is None or row["kind"] != kind:
        raise RedeAplicacaoError(f"o perfil de {kind} {profile_id} pedido para o aparelho não existe mais")
    return row


def origem_do_aparelho(rt: DeviceRuntime | None) -> str | None:
    """`None` = emulador deste host (alcança o central pelo 10.0.2.2); senão, de onde ele é: o worker, ou "externo"
    (celular no adb daqui). O `external` do gerenciador é a fonte: é ele que diz que o adb chega por um túnel ou
    por um cabo, e não pelo emulador local."""
    if rt is None or not getattr(rt, "external", False):
        return None
    return str(getattr(rt, "worker_id", None) or "externo")


def endpoint_do_central(cfg: RedeCfg, vpn_row: Row, remoto: str | None) -> tuple[str, int]:
    """Onde o aparelho acha o servidor do central. Local: o do perfil (o 10.0.2.2 medido no 25.1). Remoto: o
    endereço da LAN configurado e a porta em que o servidor escuta de fato — o 10.0.2.2 do notebook é o notebook.
    Nada muda na rota do host nem no túnel SSH: o UDP sai pela rede do notebook até a LAN do central."""
    if remoto is None:
        return str(vpn_row["endpoint_host"]), int(vpn_row["endpoint_port"])
    if not cfg.servidor.endpoint_lan:
        raise RedeAplicacaoError(
            f"o aparelho é de outra máquina ({remoto}) e rede.servidor.endpoint_lan está vazio: configure o IP da LAN "
            "do central (o que o notebook alcança) no config.yaml e libere a entrada UDP "
            f"{cfg.servidor.porta_wireguard} no firewall daqui (GET /api/network/server → remote_access)")
    return cfg.servidor.endpoint_lan, int(cfg.servidor.porta_wireguard)


def montar_plano(st: AppState, row: Row, servidor: ServidorDeRede, *, rt: DeviceRuntime | None = None) -> Plano:
    """Traduz o pedido (`device_network`) no plano do cliente. Perfil de VPN:

    - `params.servidor: "central"` → o sing-box do central: chave do aparelho gerada pela plataforma (e registrada
      no servidor), endereço próprio no túnel, par = chave pública do servidor, endpoint = o do perfil (como o
      aparelho o vê: `10.0.2.2:51820` para o emulador local; para o aparelho de OUTRA máquina, o
      `rede.servidor.endpoint_lan` e a porta do servidor, item 25.7);
    - senão, protocolo `wireguard` → um servidor WireGuard externo (provedor, P1): o segredo do perfil é a chave
      privada do cliente, e `params` traz `peer_public_key` e `address` (e, opcionais, `dns`, `mtu`, `allowed_ips`);
    - `singbox` sem `servidor: central` é recusado: a plataforma não sabe gerar o cliente para um servidor que ela
      não descreve.
    Perfil de proxy: `socks5` ou `http`, com a senha no segredo e `params.username`; `servidor: central` é o proxy
    autenticado do central, alcançado pelo túnel (exige a VPN do central)."""
    iid, rev, policy = str(row["instance_id"]), int(row["desired_rev"]), str(row["policy"])
    cfg = st.cfg.file.rede
    remoto = origem_do_aparelho(rt if rt is not None else st.devices.devices.get(iid))
    vpn_row = _perfil(st, row["vpn_profile_id"], "vpn")
    proxy_row = _perfil(st, row["proxy_profile_id"], "proxy")
    vpn: TunelWg | None = None
    gerenciado = False
    dns = cfg.dns
    if vpn_row is not None:
        params = loads(vpn_row["params"], {}) or {}
        if params.get("dns"):
            dns = str(params["dns"][0] if isinstance(params["dns"], list) else params["dns"])
        mtu = int(params.get("mtu") or 1280)
        if gerenciado_pelo_central(params):
            gerenciado = True
            host, porta = endpoint_do_central(cfg, vpn_row, remoto)      # antes da chave: recusa não gera par
            par = servidor.par_do_aparelho(iid)
            vpn = TunelWg(fonte_da_chave=Fonte("chave", iid), address=f"{par.address}/32", peer_host=host,
                          peer_port=porta, peer_public_key=servidor.chave_publica(), mtu=mtu)
        elif vpn_row["protocol"] == "wireguard":
            falta = [c for c in ("peer_public_key", "address") if not params.get(c)]
            if falta or not vpn_row["secret_ref"]:
                raise RedeAplicacaoError(
                    f"o perfil de VPN {vpn_row['name']} (servidor WireGuard externo) precisa de "
                    + ", ".join([*(f"params.{c}" for c in falta), *([] if vpn_row["secret_ref"] else ["secret (a "
                                                                                                    "chave privada)"])]))
            endereco = str(params["address"])
            if "/" not in endereco:
                endereco += "/32"
            ips = params.get("allowed_ips")
            vpn = TunelWg(fonte_da_chave=Fonte("perfil", str(vpn_row["id"])), address=endereco,
                          peer_host=str(vpn_row["endpoint_host"]), peer_port=int(vpn_row["endpoint_port"]),
                          peer_public_key=str(params["peer_public_key"]), mtu=mtu,
                          allowed_ips=tuple(ips) if isinstance(ips, list) and ips else ("0.0.0.0/0", "::/0"))
        else:
            raise RedeAplicacaoError(
                f"o perfil de VPN {vpn_row['name']} é sing-box sem `params.servidor: \"central\"`: a plataforma só gera "
                "o cliente para o servidor do central ou para um servidor WireGuard (protocol wireguard)")
    proxy: ProxyDoCliente | None = None
    if proxy_row is not None:
        params = loads(proxy_row["params"], {}) or {}
        tipo: Literal["socks", "http"] = "socks" if proxy_row["protocol"] == "socks5" else "http"
        senha = Fonte("perfil", str(proxy_row["id"])) if proxy_row["secret_ref"] else None
        if gerenciado_pelo_central(params):
            if not gerenciado:
                raise RedeAplicacaoError(f"o proxy {proxy_row['name']} é o do central, alcançado só pelo túnel: peça "
                                         "junto um perfil de VPN do central")
            if senha is None:
                raise RedeAplicacaoError(f"o proxy do central {proxy_row['name']} precisa da senha (secret)")
            proxy = ProxyDoCliente(tipo=tipo, host=endereco_do_servidor(cfg.servidor.sub_rede),
                                   port=cfg.servidor.porta_proxy,
                                   username=usuario_do_proxy(str(proxy_row["id"]), params), fonte_da_senha=senha)
        else:
            username = str(params.get("username") or "").strip() or None
            if username and senha is None:
                raise RedeAplicacaoError(f"o proxy {proxy_row['name']} tem usuário e não tem senha (secret)")
            proxy = ProxyDoCliente(tipo=tipo, host=str(proxy_row["endpoint_host"]),
                                   port=int(proxy_row["endpoint_port"]), username=username, fonte_da_senha=senha)
    return Plano(instance_id=iid, rev=rev, policy=policy, vpn=vpn, proxy=proxy, dns=dns, gerenciado=gerenciado,
                 remoto=remoto)


def config_do_cliente(plano: Plano, valores: Mapping[str, str]) -> dict[str, object]:
    """O perfil do sing-box no aparelho (o do piloto, `gerar_config.py::perfil_sfa`). Só é chamada DENTRO do
    `montar` do consumidor restrito: `valores` traz a chave e a senha abertas.

    App → `tun` (strict_route: IPv6 inalcançável em vez de vazar) → proxy autenticado (se houver) → túnel
    WireGuard → saída. DNS sequestrado e resolvido pelo túnel. Com proxy HTTP, o UDP que não é DNS é recusado
    (o HTTP não o carrega, e deixá-lo sair direto furaria o proxy; ADR-056 T3). Com SOCKS5, o UDP vai pelo proxy
    — a medição viu as associações do SOCKS5 expirarem e o UDP que não é DNS se perder (o 25.5 mede `udp_ok`)."""
    outbounds: list[dict[str, object]] = [{"type": "direct", "tag": "direct"}]
    endpoints: list[dict[str, object]] = []
    regras: list[dict[str, object]] = [{"action": "sniff"}, {"protocol": "dns", "action": "hijack-dns"}]
    final = "direct"
    dns_server: dict[str, object] = {"type": "udp", "tag": "dns-remoto", "server": plano.dns}
    if plano.vpn is not None:
        v = plano.vpn
        endpoints.append({"type": "wireguard", "tag": "wg-out", "mtu": v.mtu, "address": [v.address],
                          "private_key": valores["chave"],
                          "peers": [{"address": v.peer_host, "port": v.peer_port, "public_key": v.peer_public_key,
                                     "allowed_ips": list(v.allowed_ips),
                                     "persistent_keepalive_interval": v.keepalive}]})
        dns_server["detour"] = "wg-out"
        final = "wg-out"
    if plano.proxy is not None:
        p = plano.proxy
        saida: dict[str, object] = {"type": p.tipo, "tag": "proxy-out", "server": p.host, "server_port": p.port}
        if p.tipo == "socks":
            saida["version"] = "5"
        if p.username:
            saida["username"] = p.username
        if p.fonte_da_senha is not None:
            saida["password"] = valores["senha_do_proxy"]
        if plano.vpn is not None:
            saida["detour"] = "wg-out"              # o proxy é alcançado PELO túnel (medido: 18:31:20)
        else:
            # Sem VPN, o DNS vai pelo proxy em TCP: UDP não atravessa um proxy HTTP.
            dns_server = {"type": "tcp", "tag": "dns-remoto", "server": plano.dns, "detour": "proxy-out"}
        outbounds.insert(0, saida)
        final = "proxy-out"
        if p.tipo == "http":
            regras.append({"network": "udp", "action": "reject"})
    return {
        "log": {"level": "info", "timestamp": True},
        "dns": {"servers": [dns_server], "final": "dns-remoto"},
        "endpoints": endpoints,
        "inbounds": [{"type": "tun", "tag": "tun-in", "address": [ENDERECO_DO_TUN], "mtu": 1400,
                      "auto_route": True, "strict_route": True, "stack": "mixed"}],
        "outbounds": outbounds,
        "route": {"rules": regras, "final": final, "auto_detect_interface": True,
                  "default_domain_resolver": "dns-remoto"},
    }


# ============================================================================ o HTTP de uso único
class ServidorDeUmaVez:
    """Serve UM arquivo, UMA vez, só em 127.0.0.1 — o transporte do consumidor restrito para o import do SFA.

    Cumpre `TransporteDeArquivoPrivado`: `gravar_arquivo_privado` guarda os bytes EM MEMÓRIA (nada no disco do host
    nem do convidado) e começa a servir `/<nome>`; devolve a URL como o aparelho a vê. `apagar_arquivo_privado`
    encerra e solta os bytes. O nome carrega um token aleatório: outro caminho recebe 404 e não conta. Depois de um
    GET atendido o servidor fecha — um segundo pedido (inclusive de outro processo da máquina que tenha lido a URL
    num argumento) não recebe nada, e a importação do aparelho falharia em vez de passar calada."""

    def __init__(self, *, host_do_aparelho: str, serial: str, prazo_s: float = 180.0) -> None:
        self.serial = serial
        self.host_do_aparelho = host_do_aparelho
        self.prazo_s = prazo_s
        self.entregues = 0
        self.recusados = 0
        self._conteudo: bytes | None = None
        self._caminho: str | None = None
        self._fim = threading.Event()
        dono = self

        class _Pedido(http.server.BaseHTTPRequestHandler):
            # Uma conexão que abre e não pede nada não pode prender o laço até o fim do prazo.
            timeout = 10

            def do_GET(self) -> None:          # noqa: N802 - nome do http.server
                conteudo = dono._conteudo
                if conteudo is None or dono._caminho is None or self.path.split("?")[0] != dono._caminho \
                        or dono.entregues >= 1:
                    dono.recusados += 1
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(conteudo)))
                self.end_headers()
                self.wfile.write(conteudo)
                dono.entregues += 1
                dono._fim.set()

            def log_message(self, fmt: str, *args: object) -> None:
                # O caminho tem o token: o log diz só QUE houve pedido.
                log.info("perfil de rede: pedido HTTP de %s", self.client_address[0])

        self._srv = http.server.HTTPServer(("127.0.0.1", 0), _Pedido)
        self._srv.timeout = 0.25
        self.porta = int(self._srv.server_address[1])
        self._thread: threading.Thread | None = None

    def gravar_arquivo_privado(self, nome: str, conteudo: bytes) -> str:
        self._conteudo = bytes(conteudo)
        self._caminho = "/" + urllib.parse.quote(nome)
        self._thread = threading.Thread(target=self._servir, name=f"perfil-de-rede-{self.porta}", daemon=True)
        self._thread.start()
        return f"http://{self.host_do_aparelho}:{self.porta}{self._caminho}"

    def enviar_arquivo_privado(self, local: str, nome: str, *, tamanho: int) -> str:
        raise NotImplementedError("o perfil vai pela memória, nunca por arquivo")

    def apagar_arquivo_privado(self, nome: str) -> None:
        self._fim.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._conteudo = None
        self._srv.server_close()

    def _servir(self) -> None:
        limite = time.monotonic() + self.prazo_s
        while not self._fim.is_set() and time.monotonic() < limite:
            self._srv.handle_request()


# ============================================================================ o aparelho, pela plataforma
@dataclass(frozen=True)
class Elemento:
    texto: str
    pacote: str
    centro: tuple[int, int]
    habilitado: bool = True


@dataclass(frozen=True)
class NoDaTela:
    """Um nó da árvore de tela COMPLETA (com os que não têm texto): é o que prova que um rótulo pertence a um botão. No
    Compose o `Start` do SFA é um filho de texto NÃO clicável de um contêiner clicável sem texto (android-09, 01/10)."""

    texto: str
    pacote: str
    limites: tuple[int, int, int, int]
    clicavel: bool
    habilitado: bool = True

    @property
    def centro(self) -> tuple[int, int]:
        x0, y0, x1, y1 = self.limites
        return (x0 + x1) // 2, (y0 + y1) // 2

    @property
    def area(self) -> int:
        x0, y0, x1, y1 = self.limites
        return max(0, x1 - x0) * max(0, y1 - y0)

    def contem(self, outro: NoDaTela) -> bool:
        return (self.limites[0] <= outro.limites[0] and self.limites[1] <= outro.limites[1]
                and self.limites[2] >= outro.limites[2] and self.limites[3] >= outro.limites[3])


class AparelhoDaRede(Protocol):
    """O que a receita precisa do aparelho. `AparelhoPeloAdb` é o de verdade; o teste passa um dublê."""

    id: str
    serial: str
    external: bool

    async def shell(self, comando: str, *, timeout: float = 40) -> str: ...
    async def elementos(self) -> list[Elemento]: ...
    async def arvore(self) -> list[NoDaTela]: ...
    async def tocar(self, x: int, y: int) -> None: ...
    async def reverso(self, porta: int) -> None: ...
    async def desfazer_reverso(self, porta: int) -> None: ...


class AparelhoPeloAdb:
    """O aparelho pela plataforma: o adb e a fila do aparelho (`rt.executor`), e a árvore de tela pelo mesmo
    caminho do `GET /hierarchy` (o `uiautomator dump` é morto enquanto o Appium está ativo; medido no 25.1)."""

    def __init__(self, st: AppState, rt: DeviceRuntime) -> None:
        self.st, self.rt = st, rt
        self.id, self.serial, self.external = rt.id, rt.serial, bool(rt.external)

    async def shell(self, comando: str, *, timeout: float = 40) -> str:
        chamada = functools.partial(self.rt.adb.shell, comando, timeout=timeout)
        return str(await self.rt.executor.run(chamada, timeout=timeout + 10, label="rede do aparelho") or "")

    async def elementos(self) -> list[Elemento]:
        arvore = await self.st.devices.hierarchy(self.rt)
        return [Elemento(texto=(e.text or e.desc or "").strip(), pacote=e.package, centro=e.center,
                         habilitado=e.enabled) for e in arvore.elements if (e.text or e.desc)]

    async def arvore(self) -> list[NoDaTela]:
        arvore = await self.st.devices.hierarchy(self.rt)
        return [NoDaTela(texto=(e.text or e.desc or "").strip(), pacote=e.package, limites=tuple(e.bounds),
                         clicavel=bool(e.clickable), habilitado=bool(e.enabled)) for e in arvore.elements]

    async def tocar(self, x: int, y: int) -> None:
        await self.rt.executor.run(self.rt.adb.tap, x, y, timeout=20, label="toque da importação da rede")

    async def reverso(self, porta: int) -> None:
        await self.rt.executor.run(self.rt.adb.reverse, porta, timeout=25, label="adb reverse do perfil")

    async def desfazer_reverso(self, porta: int) -> None:
        await self.rt.executor.run(self.rt.adb.remove_reverse, porta, timeout=25, label="adb reverse --remove")


# ============================================================================ a receita
def _achar(elementos: list[Elemento], rotulo: str, pacote: str) -> Elemento | None:
    alvo = rotulo.casefold()
    return next((e for e in elementos if e.habilitado and e.pacote == pacote and e.texto.casefold() == alvo), None)


async def tocar_importacao(ap: AparelhoDaRede, pacote: str, *, prazo_s: float = 60.0,
                           pausa_s: float = 1.5) -> list[str]:
    """Os toques da importação, achados pelo TEXTO no pacote do cliente (nunca por coordenada fixa, nunca num
    diálogo de outro pacote). "No, thanks" é tocado sempre que aparecer (a atualização da primeira execução); "OK"
    confirma a importação; "Create" cria o perfil — e só depois de "OK", na ordem medida. Devolve os toques."""
    tocados: list[str] = []
    vistos: list[str] = []
    fim = time.monotonic() + prazo_s
    while time.monotonic() < fim:
        els = await ap.elementos()
        vistos = [e.texto for e in els if e.pacote == pacote][:10]
        passo = None
        if (e := _achar(els, BOTAO_ATUALIZACAO, pacote)) is not None:
            passo = (BOTAO_ATUALIZACAO, e)
        elif BOTAO_CONFIRMAR not in tocados and (e := _achar(els, BOTAO_CONFIRMAR, pacote)) is not None:
            passo = (BOTAO_CONFIRMAR, e)
        elif BOTAO_CONFIRMAR in tocados and (e := _achar(els, BOTAO_CRIAR, pacote)) is not None:
            passo = (BOTAO_CRIAR, e)
        if passo is not None:
            await ap.tocar(*passo[1].centro)
            tocados.append(passo[0])
            if passo[0] == BOTAO_CRIAR:
                return tocados
        await asyncio.sleep(pausa_s)
    raise RedeAplicacaoError(f"a importação do perfil no {pacote} não chegou ao fim em {prazo_s:.0f} s (tocados: "
                             f"{', '.join(tocados) or 'nenhum'}; na tela: {', '.join(vistos) or 'nada do cliente'})")


def _crash_reports(pacote: str) -> str:
    return CRASH_REPORTS.format(pkg=pacote)


def _ler_pares(saida: str, chaves: tuple[str, ...]) -> dict[str, str]:
    vals: dict[str, str] = {}
    for linha in (saida or "").splitlines():
        chave, _, valor = linha.strip().partition("=")
        if chave in chaves:
            vals[chave] = valor.strip()
    return vals


async def _reler_always_on(ap: AparelhoDaRede) -> tuple[str, str]:
    lido = _ler_pares(await ap.shell("echo A=$(settings get secure always_on_vpn_app); "
                                     "echo L=$(settings get secure always_on_vpn_lockdown)", timeout=30), ("A", "L"))
    return lido.get("A", ""), lido.get("L", "")


async def provisionar(ap: AparelhoDaRede, db: Database, secrets: SecretStore, plano: Plano, cfg: RedeCfg, *,
                      prazo_da_tela_s: float = 60.0, prazo_do_download_s: float = 30.0,
                      pausa_s: float = 1.5) -> str:
    """Passos 2 a 8 da receita. Devolve a evidência (sem segredo, sem a URL de uso único). O cliente já instalado
    é pré-condição (a convergência instala pela loja). Não reinicia: always-on só vale no boot."""
    if plano.vazio:
        raise RedeAplicacaoError("plano sem VPN nem proxy: isso é desfazer, não provisionar")
    pkg = cfg.cliente_pacote
    await ap.shell(f"cmd appops set {pkg} ACTIVATE_VPN allow", timeout=30)
    modo = (await ap.shell(f"cmd appops get {pkg} ACTIVATE_VPN", timeout=30)).strip()
    if "allow" not in modo:
        raise RedeAplicacaoError(f"a permissão de VPN do {pkg} não ficou em allow (lido: {modo[:80] or 'vazio'})")
    await ap.shell(f"am force-stop {pkg}", timeout=30)          # a VPN desligada durante a importação
    host = "127.0.0.1" if ap.external else HOST_DO_EMULADOR
    servidor = ServidorDeUmaVez(host_do_aparelho=host, serial=ap.serial)
    nome = f"perfil-{pysecrets.token_urlsafe(18)}.json"
    rotulo = f"plataforma-{plano.instance_id}-r{plano.rev}"
    tocados: list[str] = []
    try:
        if ap.external:
            # O aparelho do worker não tem o 10.0.2.2 deste host; o `adb reverse` leva o 127.0.0.1 dele até aqui
            # pelo túnel do adb, sem mexer em rota nem no túnel.
            await ap.reverso(servidor.porta)
        with segredos_entregues(db, secrets, plano.fontes(), servidor, nome_do_arquivo=nome,
                                montar=lambda v: json.dumps(config_do_cliente(plano, v))) as entrega:
            link = ("sing-box://import-remote-profile?url=" + urllib.parse.quote(entrega.caminho, safe="")
                    + "#" + urllib.parse.quote(rotulo, safe=""))
            try:
                saida = await ap.shell(f"am start -a android.intent.action.VIEW -d '{link}'", timeout=30)
                abriu = "Error" not in saida
            except Exception:  # noqa: BLE001 - a mensagem do adb ecoaria o link com o token; a nossa é fixa
                abriu = False
            if not abriu:
                raise RedeAplicacaoError(f"o {pkg} não aceitou o pedido de importação do perfil (am start)")
            tocados = await tocar_importacao(ap, pkg, prazo_s=prazo_da_tela_s, pausa_s=pausa_s)
            fim = time.monotonic() + prazo_do_download_s
            while servidor.entregues < 1 and time.monotonic() < fim:
                await asyncio.sleep(min(pausa_s, 0.5))
            if servidor.entregues < 1:
                raise RedeAplicacaoError(f"o {pkg} não baixou o perfil ({', '.join(tocados)} tocados, nenhum GET "
                                         "no HTTP de uso único)")
    except SegredoDeRedeError as exc:
        raise RedeAplicacaoError(str(exc)) from None
    finally:
        servidor.apagar_arquivo_privado(nome)       # idempotente: o socket fecha mesmo se nada foi servido
        if ap.external:
            try:
                await ap.desfazer_reverso(servidor.porta)
            except Exception:  # noqa: BLE001 - o mapeamento morre com o próximo boot; não derruba a provisão
                log.info("%s: adb reverse --remove %s falhou", ap.id, servidor.porta)
    await ap.shell("sync", timeout=60)
    bloqueio = "1" if plano.bloqueio else "0"
    await ap.shell(f"settings put secure always_on_vpn_app {pkg}", timeout=30)
    await ap.shell(f"settings put secure always_on_vpn_lockdown {bloqueio}", timeout=30)
    app, lockdown = await _reler_always_on(ap)
    if app != pkg or lockdown != bloqueio:
        raise RedeAplicacaoError(f"always-on não ficou gravado (lido: app={app or 'vazio'}, lockdown="
                                 f"{lockdown or 'vazio'}; pedido: {pkg}, {bloqueio})")
    await ap.shell(f"rm -rf {_crash_reports(pkg)}", timeout=30)
    await ap.shell("sync", timeout=60)
    return (f"perfil {rotulo} importado no {pkg} ({' → '.join(tocados)}; 1 GET no HTTP de uso único, "
            f"{'adb reverse' if ap.external else HOST_DO_EMULADOR}); {plano.descrever()}; permissão de VPN allow; "
            f"always-on={app}, lockdown={lockdown} relidos; relatórios de falha apagados; sync")


#: Quanto esperar, no DESFAZER, depois do `am force-stop` do cliente VPN e antes do reinício: o estado `stopped` do pacote só chega ao disco
#: depois de um instante; reiniciando na hora ele se perde e o cliente (que lembra que estava ligado) religa sozinho no boot, com o `tun0` para um
#: par que já saiu do servidor (W8, r2, 02/10/2026; com ~10 s de espera no r3 o cliente não religou). 0 desliga a espera (testes).
PARADA_PERSISTIR_S = 10.0


async def desfazer(ap: AparelhoDaRede, cfg: RedeCfg) -> str:
    """O espelho da provisão: tira o always-on e o bloqueio, para o cliente e sincroniza. O bloqueio que está na
    MEMÓRIA do Android só sai com o reinício, que a convergência pede depois. O perfil fica dentro do app (sem root
    não há como apagá-lo) e deixa de ser usado; os relatórios de falha, que teriam a chave, saem."""
    pkg = cfg.cliente_pacote
    await ap.shell("settings delete secure always_on_vpn_app", timeout=30)
    await ap.shell("settings put secure always_on_vpn_lockdown 0", timeout=30)
    await ap.shell(f"am force-stop {pkg}", timeout=30)
    await ap.shell(f"rm -rf {_crash_reports(pkg)}", timeout=30)
    await ap.shell("sync", timeout=60)
    app, lockdown = await _reler_always_on(ap)
    if app not in ("", "null") or lockdown not in ("0", "", "null"):
        raise RedeAplicacaoError(f"o always-on não saiu (lido: app={app or 'vazio'}, lockdown={lockdown or 'vazio'})")
    parado = await _esperar_parada_persistir(ap, pkg, PARADA_PERSISTIR_S)
    return (f"always-on e bloqueio tirados (relidos: app={app or 'null'}, lockdown={lockdown or '0'}); {pkg} parado"
            f"{f' ({parado})' if parado else ''}; sync")


async def _esperar_parada_persistir(ap: AparelhoDaRede, pkg: str, espera_s: float) -> str:
    """O `force-stop` só vale no boot seguinte se o estado `stopped` do pacote chegou ao disco (W8 §13, F1): espera `espera_s` e lê
    `stopped=` (melhor esforço: a leitura não derruba o desfazer). Devolve o que leu, ou vazio sem espera nem leitura."""
    if espera_s <= 0:
        return ""
    await asyncio.sleep(espera_s)
    try:
        lido = await ap.shell(f"dumpsys package {pkg} | grep -m1 -o 'stopped=[a-z]*'", timeout=30)
    except Exception:  # noqa: BLE001 - a leitura é só evidência
        return "estado stopped não lido"
    return lido.strip() or "estado stopped não lido"


async def parar_cliente_solto(ap: AparelhoDaRede, pkg: str, *, espera_s: float = 2.0) -> None:
    """O cliente VPN religou sozinho (auto-início) depois de a rede ter sido tirada: `force-stop`, sem reiniciar o aparelho. O `tun0`
    cai com o processo; quem chama relê a observação."""
    await ap.shell(f"am force-stop {pkg}", timeout=30)
    await asyncio.sleep(espera_s)


# ============================================================================ a conferência (uid 2000)
@dataclass
class Observacao:
    """O que o aparelho mostra, lido como uid 2000. `None` = a linha não veio (não dá para saber)."""

    uid: int | None = None
    always_on: str = ""
    lockdown: str = ""
    tun: bool = False
    vpn_conectada: bool = False
    regras_de_bloqueio: bool = False
    relatorios_de_falha: int = 0
    cliente_instalado: bool = False
    #: A instalação do cliente VPN que está no aparelho (`identidade_do_cliente`): versão e pasta de instalação. A
    #: prova de vazamento vale para ESTA instalação; atualizar ou reinstalar o cliente muda a pasta e a invalida.
    cliente: str = ""
    uptime_s: int | None = None
    extras: dict[str, str] = field(default_factory=dict)

    def configuracao_ok(self, pacote: str, bloqueio: bool) -> bool:
        return (self.cliente_instalado and self.always_on == pacote
                and self.lockdown == ("1" if bloqueio else "0"))

    def conectada(self, bloqueio: bool) -> bool:
        return self.tun and self.vpn_conectada and self.regras_de_bloqueio == bloqueio

    def removida(self) -> bool:
        return (self.always_on in ("", "null") and self.lockdown in ("", "0", "null") and not self.tun
                and not self.regras_de_bloqueio)

    def cliente_solto(self) -> bool:
        """A rede foi tirada (always-on e bloqueio fora, sem regras) mas o cliente VPN ficou ligado por conta própria (auto-início do cliente
        no boot): só o túnel sobrou. Reiniciar não resolve (ele religa de novo); o remédio é parar o cliente."""
        return (self.always_on in ("", "null") and self.lockdown in ("", "0", "null") and not self.regras_de_bloqueio
                and (self.tun or self.vpn_conectada))

    def descrever(self, pacote: str) -> str:
        return (f"uid {self.uid}; always-on={self.always_on or 'null'}; lockdown={self.lockdown or 'null'}; "
                f"tun0 {'no ar' if self.tun else 'ausente'}; "
                f"{'VPN CONNECTED' if self.vpn_conectada else 'sem rede VPN conectada'} no dumpsys; "
                f"{'regras de bloqueio ativas' if self.regras_de_bloqueio else 'sem regras de bloqueio'}; "
                f"{pacote} {'instalado' if self.cliente_instalado else 'AUSENTE'}"
                + (f"; {self.relatorios_de_falha} relatório(s) de falha" if self.relatorios_de_falha else "")
                + (f"; ligado há {self.uptime_s} s" if self.uptime_s is not None else ""))


def comando_de_observacao(pacote: str) -> str:
    """Uma ida ao aparelho só, tudo como o uid do shell. O `dumpsys connectivity` é lido UMA vez (é grande)."""
    return (
        "echo U=$(id -u); "
        "echo A=$(settings get secure always_on_vpn_app); "
        "echo L=$(settings get secure always_on_vpn_lockdown); "
        "echo T=$(ip -o addr show tun0 2>/dev/null | grep -c inet); "
        "D=$(dumpsys connectivity 2>/dev/null); "
        "echo V=$(echo \"$D\" | grep -c 'ni[{]VPN CONNECTED'); "
        # Conta as REGRAS, não o cabeçalho: o `dumpsys connectivity` do Android 14 imprime "Lockdown filtering rules:"
        # sempre, com a lista vazia sem bloqueio. Contando o cabeçalho, todo aparelho tinha "regras ativas", e com a
        # política `exigida` (sem bloqueio) o túnel no ar nunca valia como conectado — a rede reiniciava em cadeia
        # (android-09, W4, 01/10/2026: tun0 no ar, VPN CONNECTED, lockdown=0, 2 reinícios).
        "echo R=$(echo \"$D\" | grep -A8 'Lockdown filtering rules' | grep -c 'UIDs:'); "
        f"echo C=$(ls {_crash_reports(pacote)} 2>/dev/null | grep -c .); "
        f"echo P=$(pm path {pacote} 2>/dev/null | grep -c package:); "
        # A instalação do cliente, numa linha (o `echo` sem aspas junta as três): `codePath` (a pasta tem um sufixo
        # sorteado a cada instalação ou atualização), `versionCode` e `versionName`. Lido como o shell, sem root.
        f"echo K=$(dumpsys package {pacote} 2>/dev/null | grep -m3 -E 'codePath=|versionCode=|versionName='); "
        "echo S=$(cut -d. -f1 /proc/uptime)"
    )


_CODE_PATH = re.compile(r"codePath=(\S+)")
_VERSION_CODE = re.compile(r"versionCode=(\d+)")
_VERSION_NAME = re.compile(r"versionName=(\S+)")


def identidade_do_cliente(linha: str) -> str:
    """`<versão> (<código>) <pasta de instalação>` a partir da linha `K` da observação; vazio se não der para ler as
    três partes. A pasta (`/data/app/~~…==/<pacote>-…==`) é sorteada pelo Android a cada instalação: a mesma versão
    reinstalada é OUTRA instalação, e não depende do fuso do aparelho como o `lastUpdateTime`."""
    pasta, codigo, nome = (r.search(linha or "") for r in (_CODE_PATH, _VERSION_CODE, _VERSION_NAME))
    if not (pasta and codigo and nome):
        return ""
    return f"{nome.group(1)} ({codigo.group(1)}) {pasta.group(1)}"


def comando_da_instalacao(pacote: str) -> str:
    """Quando o APK do cliente foi gravado no aparelho, em segundos desde 1970 (independente do fuso do aparelho, ao
    contrário do `lastUpdateTime` do `dumpsys`). Lido como o shell; a pasta do pacote é legível por todos."""
    return (f"echo M=$(stat -c %Y $(pm path {pacote} 2>/dev/null | head -1 | cut -d: -f2) 2>/dev/null); "
            "echo N=$(date +%s); echo U=$(id -u)")


async def instalado_em(ap: AparelhoDaRede, pacote: str) -> tuple[int, int]:
    """(quando o APK do cliente foi gravado, o relógio do aparelho agora), os dois em segundos desde 1970. O relógio
    vai junto porque a data do APK é a do relógio DO APARELHO: quem a compara com um horário do servidor confere antes
    que os dois relógios andam juntos."""
    v = _ler_pares(await ap.shell(comando_da_instalacao(pacote), timeout=30), ("M", "N", "U"))
    if not v.get("M", "").isdigit() or not v.get("N", "").isdigit():
        raise RedeAplicacaoError(f"a data de instalação do cliente VPN {pacote} não veio do aparelho")
    return int(v["M"]), int(v["N"])


def ler_observacao(saida: str) -> Observacao:
    v = _ler_pares(saida, ("U", "A", "L", "T", "V", "R", "C", "P", "K", "S"))
    faltam = sorted({"U", "A", "L", "T", "V", "R", "P", "K"} - set(v))
    if faltam:
        raise RedeAplicacaoError(f"a leitura da rede no aparelho veio incompleta (faltou {', '.join(faltam)})")

    def num(chave: str) -> int:
        try:
            return int(v.get(chave, "") or 0)
        except ValueError:
            return 0

    uid = int(v["U"]) if v["U"].isdigit() else None
    cliente = identidade_do_cliente(v["K"])
    if num("P") > 0 and not cliente:
        # Instalado e sem identidade legível: não dá para saber A QUAL instalação a prova de vazamento pertence. É
        # leitura incompleta (repete depois), nunca "cliente desconhecido" — que levaria a refazer o teste à toa.
        raise RedeAplicacaoError("a leitura da rede no aparelho veio incompleta (o cliente VPN está instalado, mas a "
                                 "versão e a pasta de instalação não vieram)")
    return Observacao(uid=uid, always_on=v["A"], lockdown=v["L"], tun=num("T") > 0, vpn_conectada=num("V") > 0,
                      regras_de_bloqueio=num("R") > 0, relatorios_de_falha=num("C"), cliente_instalado=num("P") > 0,
                      cliente=cliente, uptime_s=int(v["S"]) if v.get("S", "").isdigit() else None)


async def observar(ap: AparelhoDaRede, pacote: str, *, esperar_tun_s: float = 0.0, intervalo_s: float = 5.0) -> Observacao:
    """Lê a rede do aparelho como uid 2000. Com `esperar_tun_s`, relê até o `tun0` aparecer ou o prazo acabar
    (medido: ~40 s depois do boot sob carga). Leitura como root é recusada: o bloqueio não cobre o uid 0."""
    fim = time.monotonic() + esperar_tun_s
    while True:
        obs = ler_observacao(await ap.shell(comando_de_observacao(pacote), timeout=45))
        if obs.uid != UID_DO_SHELL:
            raise RedeAplicacaoError(
                f"a leitura da rede rodou como uid {obs.uid}, não como o shell (2000): o bloqueio não cobre o root, e "
                "a conferência precisa do uid de um app comum. Rode `adb unroot` neste aparelho")
        if obs.tun or time.monotonic() >= fim:
            return obs
        await asyncio.sleep(intervalo_s)


# ============================================================================ o tile do cliente (29.3) — NÃO é o caminho de recuperação
# ATENÇÃO (W8, 01/10/2026): o que está medido abaixo valeu no android-05 porque o `serviceMode` do cliente ali JÁ era VPN
# (o tile chama `BoxService.start()` sem recalcular o modo). Num cliente que só importou o perfil (o fluxo da plataforma),
# o tile inicia o ProxyService e o serviço se encerra sem tun0 (android-09). A convergência usa
# `religar_pela_interface` (Start da UI); `religar_pelo_tile` e `comando_de_religar` ficam só para o diagnóstico
# (scripts/diag-w8-tile.py importa o comando) e para um aparelho cujo modo já se provou VPN. Nunca é fallback automático.
# Medido em 30/09 no android-05 (7 boots, host com carga): o `startAlwaysOnVpn` do sistema roda UMA vez por boot, e em
# 5 de 7 a primeira tentativa falhou — ANR de início do serviço com o convidado sem CPU (3), ou o serviço subindo e
# parando sozinho (2). Reiniciar de novo rola o mesmo dado. O que religou sem reinício, 5 de 5 com o SystemUI
# estável: o tile de configurações rápidas do próprio cliente, ADICIONADO NA HORA (um tile que já estava na barra
# não responde) e clicado pelo `cmd statusbar`, como o shell. Always-on, bloqueio e as regras ficaram como estavam,
# e a sonda seguiu saindo pelo túnel. `am force-stop` não religa (0 de 4), e o serviço não é exportado.
_TILE = re.compile(r"^[A-Za-z0-9_.]+/[A-Za-z0-9_.$]+$")


def comando_de_religar(tile: str) -> str:
    """Uma ida ao aparelho: tira o tile (se havia), põe de novo, espera o SystemUI processar e clica — SÓ se o
    SystemUI não reiniciou no meio (no boot ele entra em laço de ANR e renasce sem o tile), se o tile entrou na barra
    e se NÃO há `tun0` (o tile é um alternador: clicado com o túnel no ar, desligaria a VPN)."""
    if not _TILE.match(tile or ""):
        raise RedeAplicacaoError(f"tile do cliente VPN inválido: {tile!r}")
    na_barra = f"settings get secure sysui_qs_tiles | grep -c -F 'custom({tile})'"
    return (
        f"Q0=$({na_barra}); echo Q0=$Q0; "
        "P1=$(pidof com.android.systemui); "
        f"cmd statusbar remove-tile {tile} >/dev/null 2>&1; sleep 2; "
        f"cmd statusbar add-tile {tile} >/dev/null 2>&1; sleep 3; "
        "P2=$(pidof com.android.systemui); "
        f"Q=$({na_barra}); "
        "T=$(ip -o addr show tun0 2>/dev/null | grep -c inet); "
        "echo P1=$P1; echo P2=$P2; echo Q=$Q; echo T=$T; "
        'if [ -n "$P1" ] && [ "$P1" = "$P2" ] && [ "$Q" -gt 0 ] && [ "$T" -eq 0 ]; then '
        f"cmd statusbar click-tile {tile} >/dev/null 2>&1; echo CLICOU=1; else echo CLICOU=0; fi"
    )


async def religar_pelo_tile(ap: AparelhoDaRede, pacote: str, tile: str, *, espera_s: float = 20.0) -> tuple[Observacao | None, str]:
    """Tenta subir o túnel pelo tile do cliente, sem reiniciar o aparelho. Devolve a observação com o túnel no ar (ou
    `None`) e o que aconteceu. Não mexe em always-on nem em bloqueio; a barra volta a como estava.
    NÃO usar como recuperação: o tile não recalcula o `serviceMode` do cliente (ver a nota da seção)."""
    v = _ler_pares(await ap.shell(comando_de_religar(tile), timeout=60), ("Q0", "P1", "P2", "Q", "T", "CLICOU"))
    try:
        if v.get("CLICOU") != "1":
            if v.get("T", "0") != "0":
                return None, "tile não acionado: já há tun0 (clicar desligaria a VPN)"
            if not v.get("P1") or v.get("P1") != v.get("P2"):
                return None, "tile não acionado: o SystemUI reiniciou durante a preparação"
            return None, "tile não acionado: o SystemUI não pôs o tile na barra a tempo"
        obs = await observar(ap, pacote, esperar_tun_s=espera_s, intervalo_s=2.0)
        if obs.tun and not obs.vpn_conectada and espera_s > 0:
            await asyncio.sleep(2.0)                   # o `tun0` aparece um instante antes do CONNECTED no dumpsys
            obs = await observar(ap, pacote)
        if obs.tun and obs.vpn_conectada:
            return obs, "túnel religado pelo tile do cliente, sem reinício"
        return None, f"o tile foi clicado e o túnel não subiu em {espera_s:g} s"
    finally:
        if v.get("Q0", "0") == "0":                    # o tile não estava na barra antes: sai de novo
            try:
                await ap.shell(f"cmd statusbar remove-tile {tile} >/dev/null 2>&1; true", timeout=20)
            except Exception as exc:  # noqa: BLE001 - arrumação: não muda o desfecho
                logging.getLogger(__name__).info("tile do cliente VPN não removido da barra — %s", exc)


# ============================================================================ religar pela interface do cliente (W8)
# O tile NÃO é o mecanismo de recuperação (W8, 01/10/2026). Fonte de verdade: SFA 1.14.2, commit upstream
# fc21909df7a3f0fc9435f3866fb6a4960711aa5f (`app/src/main/java/io/nekohasekai/sfa/`):
#   TileService.onClick → toggleService → BoxService.start() → Settings.serviceClass()   (NÃO chama rebuildServiceMode);
#   Settings.serviceClass(): serviceMode == VPN → VPNService; qualquer outro valor → ProxyService (o padrão é NORMAL);
#   MainActivity.startService() → startService0(): rebuildServiceMode() → Libbox.hasTunInbound(perfil selecionado) →
#     serviceMode → serviceClass() → startForegroundService;
#   a importação (ProfileManager.create(andSelect = true)) SELECIONA o perfil sem recalcular o serviceMode.
# ProxyService com perfil que tem `tun`: openTun lança "android: tun inbound requires VPN service" → stopAndAlert →
# stopSelf (1 a 2 s, sem tun0). Foi o que o tile fez no android-09 (cliente instalado e perfil importado, nunca um Start
# pela interface: serviceMode ficou no padrão NORMAL). No android-05 o tile funcionava porque o serviceMode já era VPN.
# O Start da interface (UM toque) fez o 09 subir o VPNService em menos de 1 s (docs/handoffs/w8-diagnostico-android09.md
# §18). Não se escreve o serviceMode (é do SFA: sem root, sem banco privado); usa-se o caminho oficial.
#: O commit upstream do SFA 1.14.2 (versionCode 739) em que a lógica acima foi lida (`version.properties` = 1.14.2/739).
SFA_COMMIT_DE_REFERENCIA = "fc21909df7a3f0fc9435f3866fb6a4960711aa5f"
#: Os rótulos do SFA 1.14.2 (commit `fc21909`), por locale. O Start é `R.string.action_start` (`MainActivity.kt`: o
#: `contentDescription` do FAB e o texto do botão estendido) e o Stop é `R.string.stop`. A ORIGEM da tabela é o que o
#: próprio commit traz em `app/src/main/res/values*/strings.xml`: o padrão (inglês) e `values-fa`, `values-ru-rRU`,
#: `values-zh-rCN`, `values-zh-rTW` (não há outros locales traduzidos nessa versão; o Android cai no padrão quando o
#: locale do aparelho não tem recurso). Não é uma lista de traduções "plausíveis": cada par vem desses arquivos.
#: Não há identificação independente de locale: o Compose do SFA não expõe `resource-id` nem `testTag` nos botões, e o
#: contêiner clicável não tem texto. Por isso o rótulo é uma PROVA a mais (junto do pacote, do contêiner e da unicidade),
#: nunca o único critério, e um rótulo fora desta tabela não é tocado (falha fechada).
ROTULOS_DO_CLIENTE: dict[str, tuple[str, str]] = {
    "en": ("Start", "Stop"),
    "fa": ("شروع", "توقف"),
    "ru": ("Начать", "Остановить"),
    "zh-CN": ("启动", "停止"),
    "zh-TW": ("啟動", "停止"),
}
#: O contêiner clicável do botão não pode ser enorme perto do rótulo (um clicável que cobre a tela inteira não prova
#: nada): no android-09 o contêiner tem ~5x a área do rótulo.
LIMITE_DO_CONTEINER = 40
_PACOTE = re.compile(r"^[A-Za-z0-9_.]+$")
#: `MM-DD HH:MM:SS.mmm`: o formato de `logcat -v time` e de `-T` (a hora do aparelho).
_HORA_DO_LOGCAT = re.compile(r"^\d\d-\d\d \d\d:\d\d:\d\d\.\d{3}$")

RELIGADO = "religado_pela_interface"
JA_HA_TUN = "ja_ha_tun"
ABERTURA_FALHOU = "abertura_falhou"
START_NAO_PROVADO = "start_nao_provado"
INTERFACE_MOSTRA_STOP = "interface_mostra_stop"
FOCO_NAO_E_O_CLIENTE = "foco_nao_e_o_cliente"
TUN_NAO_SUBIU = "tun_nao_subiu"
VPN_NAO_CONECTADA = "vpn_nao_conectada"
#: Guard D: o Start da interface iniciou o ProxyService (serviceMode não-VPN) num plano que exige TUN. Não é "o cliente
#: começou": é incompatibilidade de classe/modo, e a recuperação NÃO está concluída.
CLASSE_ERRADA_PARA_TUN = "wrong_service_class_for_tun"

#: A classe de serviço que ESTE Start iniciou, lida SÓ na janela dele (eventos depois do baseline tirado logo antes do toque).
CLASSE_VPN = "VPNService"
CLASSE_PROXY = "ProxyService"
CLASSE_AMBAS = "ambas"                    # as duas na janela (corrida): não é "ProxyService sem VPNService"
CLASSE_NENHUMA = "nenhuma_na_janela"      # buffer íntegro e nenhum serviço do cliente iniciado na janela
CLASSE_DESCONHECIDA = "UNKNOWN"           # não se prova: baseline/buffer ilegível, buffer rotacionado ou comando falhou


@dataclass(frozen=True)
class ReligadoPelaInterface:
    obs: Observacao | None
    codigo: str
    detalhe: str
    #: O que o desfecho observou (para a evidência e para o smoke real): a classe de serviço da janela do Start, o rótulo
    #: e o método do alvo, o locale lido do aparelho e o tempo até o `tun0`.
    classe: str = CLASSE_DESCONHECIDA
    rotulo: str = ""
    metodo: str = ""
    locale: str = ""
    tun_apos_s: float | None = None

    @property
    def religado(self) -> bool:
        return self.codigo == RELIGADO and self.obs is not None


def idioma_do_recurso(locale: str) -> str | None:
    """O recurso do SFA 1.14.2 que o Android escolheria para o `locale` do aparelho (`pt-BR`, `zh_TW`, `ru-RU`…): a chave
    de `ROTULOS_DO_CLIENTE`. Sem recurso traduzido, o Android usa o padrão (`en`). Vazio/ilegível: `None` (não se sabe)."""
    bruto = (locale or "").strip().replace("_", "-")
    if not bruto or bruto.lower() == "null":
        return None
    partes = [p for p in bruto.split("-") if p]
    lingua = partes[0].lower()
    if lingua == "zh":
        resto = {p.lower() for p in partes[1:]}
        return "zh-TW" if resto & {"tw", "hk", "mo", "hant"} else "zh-CN"
    return lingua if lingua in ROTULOS_DO_CLIENTE else "en"


def rotulos_para(locale: str) -> tuple[tuple[str, ...], tuple[str, ...], str | None]:
    """(rótulos aceitos de Start, de Stop, o idioma do recurso). Locale lido: SÓ os do idioma dele (o Android usa um recurso por
    vez). Locale ilegível: a união de todos os idiomas do SFA 1.14.2 — o resto do localizador (pacote, contêiner clicável,
    unicidade) continua valendo, e um rótulo fora da tabela segue sem toque."""
    idioma = idioma_do_recurso(locale)
    if idioma is None:
        return (tuple(v[0] for v in ROTULOS_DO_CLIENTE.values()), tuple(v[1] for v in ROTULOS_DO_CLIENTE.values()), None)
    inicio, parada = ROTULOS_DO_CLIENTE[idioma]
    return (inicio,), (parada,), idioma


def achar_botao(nos: list[NoDaTela], pacote: str, rotulo: str | tuple[str, ...]) -> tuple[NoDaTela | None, str]:
    """O ponto de toque do botão do cliente cujo rótulo é UM dos `rotulo` (um texto ou os do locale), PROVADO pela árvore
    (nunca coordenada fixa, nunca outro pacote): exatamente UM nó habilitado do `pacote` com esse texto, dentro do menor
    contêiner clicável e habilitado do MESMO pacote que o contém (e não enorme perto dele). O toque vai no centro do
    RÓTULO, que está dentro do contêiner. Qualquer dúvida: `(None, motivo)` — falha fechada."""
    aceitos = (rotulo,) if isinstance(rotulo, str) else tuple(rotulo)
    alvos = {r.casefold() for r in aceitos}
    nome = "/".join(repr(r) for r in aceitos)
    do_pacote = [n for n in nos if n.pacote == pacote and len(n.limites) == 4]
    rotulados = [n for n in do_pacote if n.texto.casefold() in alvos]
    if not rotulados:
        fora = sum(1 for n in nos if n.pacote != pacote and n.texto.casefold() in alvos)
        return None, (f"nenhum {nome} do {pacote} na tela" + (f" ({fora} em outro pacote: não é tocado)" if fora else ""))
    if len(rotulados) > 1:
        return None, f"{len(rotulados)} nós {nome} no {pacote}: ambíguo"
    rotulo_no = rotulados[0]
    if not rotulo_no.habilitado:
        return None, f"o {nome} do {pacote} está desabilitado"
    conteineres = [n for n in do_pacote if n.clicavel and n.habilitado and n.contem(rotulo_no)]
    if not conteineres:
        return None, f"o {nome} do {pacote} não está dentro de um contêiner clicável e habilitado"
    menor = min(conteineres, key=lambda n: n.area)
    if rotulo_no.area <= 0 or menor.area > LIMITE_DO_CONTEINER * rotulo_no.area:
        return None, f"o contêiner clicável do {nome} é grande demais para ser o botão"
    return rotulo_no, ""


def comando_do_estado_da_interface(pacote: str) -> str:
    """Uma ida ao aparelho: o foco da tela, o `tun0`, as classes de serviço do cliente, o locale (três fontes) e o
    BASELINE: a hora do aparelho agora, no formato do `logcat -v time` (a janela do Start começa aqui)."""
    if not _PACOTE.match(pacote or ""):
        raise RedeAplicacaoError(f"pacote do cliente VPN inválido: {pacote!r}")
    servicos = f"dumpsys activity services {pacote} 2>/dev/null"
    return (
        "echo F=$(dumpsys window 2>/dev/null | grep -m1 mCurrentFocus); "
        "echo T=$(ip -o addr show tun0 2>/dev/null | grep -c inet); "
        f"echo SR=$({servicos} | grep -o '{pacote}/[.]bg[.][A-Za-z]*' | sort -u | tr '\\n' ','); "
        "echo L1=$(getprop persist.sys.locale); "
        "echo L2=$(settings get system system_locales 2>/dev/null | cut -d, -f1); "
        "echo L3=$(getprop ro.product.locale); "
        "echo BASE=$(date '+%m-%d %H:%M:%S.%N' | cut -c1-18)"
    )


def comando_da_janela_do_start(pacote: str, base: str) -> str:
    """Quantas vezes o ActivityManager iniciou cada classe do cliente em primeiro plano DEPOIS do `base` (buffer `events`:
    o `ProxyService` do 09 vivia 1 a 2 s e só ali ficava rastro), mais a hora da entrada mais antiga do buffer (se ela é
    posterior ao `base`, o buffer girou e a janela não está inteira) e o `tun0`."""
    if not _PACOTE.match(pacote or ""):
        raise RedeAplicacaoError(f"pacote do cliente VPN inválido: {pacote!r}")
    if not _HORA_DO_LOGCAT.match(base or ""):
        raise RedeAplicacaoError(f"baseline de hora inválido: {base!r}")
    janela = f"logcat -b events -d -v time -T '{base}' 2>/dev/null | grep am_foreground_service_start"
    return (
        "echo T=$(ip -o addr show tun0 2>/dev/null | grep -c inet); "
        f"echo OLD=$(logcat -b events -d -v time 2>/dev/null | grep -m1 '^[0-9][0-9]-' | cut -c1-18); "
        f"echo EVP=$({janela} | grep -c '{pacote}/[.]bg[.]ProxyService'); "
        f"echo EVV=$({janela} | grep -c '{pacote}/[.]bg[.]VPNService'); "
        f"echo SR=$(dumpsys activity services {pacote} 2>/dev/null | grep -o '{pacote}/[.]bg[.][A-Za-z]*' | sort -u | tr '\\n' ',')"
    )


def comando_de_devolver_o_foco(pacote: str) -> str:
    """HOME, e só se o foco ainda é do cliente (o que ESTE gesto abriu); outro app em primeiro plano não é tocado."""
    return (f"F=$(dumpsys window 2>/dev/null | grep -m1 mCurrentFocus); case \"$F\" in *{pacote}*) "
            "input keyevent KEYCODE_HOME;; esac; true")


def _lido(saida: str) -> dict[str, str]:
    return _ler_pares(saida, ("F", "T", "SR", "L1", "L2", "L3", "BASE", "OLD", "EVP", "EVV"))


def _inteiro(v: dict[str, str], chave: str) -> int | None:
    return int(v[chave]) if v.get(chave, "").isdigit() else None


def locale_do_aparelho(lido: dict[str, str]) -> str:
    """O primeiro locale legível: `persist.sys.locale` (a escolha do usuário), o primeiro de `system_locales`, e por fim
    `ro.product.locale` (o do produto, o que vale quando nada foi escolhido)."""
    for chave in ("L1", "L2", "L3"):
        v = (lido.get(chave) or "").strip()
        if v and v.lower() != "null":
            return v
    return ""


def classe_na_janela(base: str, janela: dict[str, str]) -> tuple[str, str]:
    """(classe, por quê): a classe de serviço que ESTE Start iniciou, provada só pelos eventos DEPOIS do `base`. Sem prova
    (baseline ou buffer ilegível, buffer que girou desde o baseline, contagem que não veio): `UNKNOWN`, nunca uma conclusão
    inventada. `ProxyService` só quando houve início dele na janela e nenhum de `VPNService` (a corrida entre os dois
    é `ambas`)."""
    if not _HORA_DO_LOGCAT.match(base or ""):
        return CLASSE_DESCONHECIDA, "o baseline de hora não foi lido"
    antiga = (janela.get("OLD") or "").strip()
    if not _HORA_DO_LOGCAT.match(antiga):
        return CLASSE_DESCONHECIDA, "a hora da entrada mais antiga do buffer events não foi lida"
    if antiga > base:
        return CLASSE_DESCONHECIDA, (f"o buffer events girou: a entrada mais antiga ({antiga}) é posterior ao baseline "
                                     f"({base}); a janela do Start não está inteira")
    proxy, vpn = _inteiro(janela, "EVP"), _inteiro(janela, "EVV")
    if proxy is None or vpn is None:
        return CLASSE_DESCONHECIDA, "a contagem de eventos da janela não veio do aparelho"
    if proxy and vpn:
        return CLASSE_AMBAS, f"ProxyService ({proxy}) e VPNService ({vpn}) iniciados na janela do Start"
    if proxy:
        return CLASSE_PROXY, f"ProxyService iniciado {proxy} vez(es) na janela do Start, nenhum VPNService"
    if vpn:
        return CLASSE_VPN, f"VPNService iniciado {vpn} vez(es) na janela do Start"
    return CLASSE_NENHUMA, "nenhum serviço do cliente iniciado na janela do Start"


async def religar_pela_interface(ap: AparelhoDaRede, pacote: str, atividade: str, *, espera_s: float = 25.0,
                                 prazo_do_botao_s: float = 15.0, pausa_s: float = 1.0) -> ReligadoPelaInterface:
    """Sobe o túnel pelo Start da interface do cliente (`MainActivity.startService0`, que recalcula o serviceMode),
    sem reiniciar o aparelho: abre a atividade, acha o botão de Start PELA ÁRVORE (rótulo do locale do aparelho, em
    `ROTULOS_DO_CLIENTE`), toca UMA vez e observa.
    Sucesso SÓ com `tun0` E VPN CONNECTED. Nunca toca com `tun0` já no ar (um Start/Stop cego desligaria a VPN), nunca
    com o foco fora do cliente, nunca em nó de outro pacote nem com rótulo fora da tabela; sem fallback para o tile.
    A classe de serviço é lida SÓ na janela deste Start (baseline de hora → toque → eventos posteriores).
    Devolve o código do desfecho e o que foi observado."""
    if not _TILE.match(atividade or ""):
        raise RedeAplicacaoError(f"atividade do cliente VPN inválida: {atividade!r}")
    antes = _lido(await ap.shell(comando_do_estado_da_interface(pacote), timeout=45))
    locale = locale_do_aparelho(antes)
    iniciar, parar_rotulos, idioma = rotulos_para(locale)
    visto = {"locale": locale or "não lido", "rotulo": "", "metodo": ""}
    if antes.get("T", "0") not in ("0", ""):
        return ReligadoPelaInterface(None, JA_HA_TUN, "Start não tocado: já há tun0 (tocar desligaria a VPN)",
                                     locale=visto["locale"])
    abriu = False
    try:
        try:
            saida = await ap.shell(f"am start -n {atividade}", timeout=30)
        except Exception as exc:  # noqa: BLE001 - o desfecho é um código, e quem chama decide (sem cair no tile)
            return ReligadoPelaInterface(None, ABERTURA_FALHOU, f"o app não abriu: {type(exc).__name__}: {str(exc)[:160]}",
                                         locale=visto["locale"])
        abriu = True
        if re.search(r"\bError\b|Exception|does not exist", saida or ""):
            return ReligadoPelaInterface(None, ABERTURA_FALHOU, f"o app não abriu: {(saida or '').strip()[:160]}",
                                         locale=visto["locale"])
        fim = time.monotonic() + prazo_do_botao_s
        while True:
            nos = await ap.arvore()
            botao, motivo = achar_botao(nos, pacote, iniciar)
            if botao is not None:
                break
            parar, _ = achar_botao(nos, pacote, parar_rotulos)
            if parar is not None:
                return ReligadoPelaInterface(None, INTERFACE_MOSTRA_STOP, "a interface do cliente mostra Stop sem tun0: "
                                             "o cliente se acha iniciado; Start/Stop não é alternado às cegas",
                                             rotulo=parar.texto, locale=visto["locale"])
            if time.monotonic() >= fim:
                vistos = ", ".join(n.texto for n in nos if n.pacote == pacote and n.texto)[:200] or "nada do cliente"
                return ReligadoPelaInterface(None, START_NAO_PROVADO,
                                             f"{motivo} (locale {visto['locale']}; na tela: {vistos}); nenhum toque",
                                             locale=visto["locale"])
            await asyncio.sleep(pausa_s)
        visto["rotulo"] = botao.texto
        visto["metodo"] = (f"árvore: rótulo `action_start` ({botao.texto!r}, recurso {idioma or 'qualquer idioma do SFA'}) "
                           "+ menor contêiner clicável do pacote")
        agora = _lido(await ap.shell(comando_do_estado_da_interface(pacote), timeout=45))
        if pacote not in agora.get("F", ""):
            return ReligadoPelaInterface(None, FOCO_NAO_E_O_CLIENTE, "o foco da tela não é do cliente VPN; nenhum toque "
                                         f"(foco: {agora.get('F', 'não lido')[:120]})", rotulo=botao.texto,
                                         metodo=visto["metodo"], locale=visto["locale"])
        if agora.get("T", "0") not in ("0", ""):
            return ReligadoPelaInterface(None, JA_HA_TUN, "Start não tocado: o tun0 subiu enquanto a tela abria",
                                         rotulo=botao.texto, metodo=visto["metodo"], locale=visto["locale"])
        base = (agora.get("BASE") or "").strip()                   # o baseline da janela: logo antes do toque
        t_toque = time.monotonic()
        await ap.tocar(*botao.centro)
        obs = await observar(ap, pacote, esperar_tun_s=espera_s, intervalo_s=2.0)
        tun_apos = round(time.monotonic() - t_toque, 1) if obs.tun else None
        if obs.tun and not obs.vpn_conectada and espera_s > 0:
            await asyncio.sleep(2.0)                   # o `tun0` aparece um instante antes do CONNECTED no dumpsys
            obs = await observar(ap, pacote)
        try:
            janela = _lido(await ap.shell(comando_da_janela_do_start(pacote, base), timeout=45))
            classe, porque = classe_na_janela(base, janela)
        except Exception as exc:  # noqa: BLE001 - sem a leitura, a classe é desconhecida; nunca uma conclusão inventada
            janela, classe, porque = {}, CLASSE_DESCONHECIDA, f"a janela do Start não foi lida: {type(exc).__name__}"
        comum = {"classe": classe, "rotulo": botao.texto, "metodo": visto["metodo"], "locale": visto["locale"],
                 "tun_apos_s": tun_apos}
        # Guard D ANTES do sucesso: o ProxyService iniciado por ESTE Start (e nenhum VPNService) num plano com TUN não
        # vale como recuperação, mesmo que um tun0 esteja no ar por outro caminho (o always-on em paralelo).
        if classe == CLASSE_PROXY:
            return ReligadoPelaInterface(None, CLASSE_ERRADA_PARA_TUN,
                                         "o Start da interface iniciou o ProxyService (serviceMode não-VPN) num plano que "
                                         f"exige TUN: incompatibilidade de classe/modo, recuperação NÃO concluída ({porque})",
                                         **comum)
        if obs.tun and obs.vpn_conectada:
            nota = {CLASSE_VPN: "", CLASSE_AMBAS: f" ({porque})"}.get(classe, f" (classe do serviço: {classe}; {porque})")
            return ReligadoPelaInterface(obs, RELIGADO, "túnel religado pelo Start da interface do cliente, sem reinício"
                                         + nota, **comum)
        if obs.tun:
            return ReligadoPelaInterface(None, VPN_NAO_CONECTADA, "o tun0 apareceu e a VPN não ficou CONNECTED no dumpsys",
                                         **comum)
        return ReligadoPelaInterface(None, TUN_NAO_SUBIU, f"o Start foi tocado e o túnel não subiu em {espera_s:g} s "
                                     f"(classe do serviço: {classe}; serviços do cliente: {janela.get('SR') or 'nenhum'})",
                                     **comum)
    finally:
        if abriu:
            try:
                await ap.shell(comando_de_devolver_o_foco(pacote), timeout=20)
            except Exception as exc:  # noqa: BLE001 - arrumação: não muda o desfecho
                logging.getLogger(__name__).info("foco não devolvido ao launcher — %s", exc)


async def apagar_relatorios_de_falha(ap: AparelhoDaRede, pacote: str) -> None:
    await ap.shell(f"rm -rf {_crash_reports(pacote)}", timeout=30)
    await ap.shell("sync", timeout=60)


def endereco_no_tunel(plano_ou_address: str) -> str:
    """`10.66.0.3/32` → `10.66.0.3` (o que o log do servidor mostra)."""
    return str(ipaddress.ip_interface(plano_ou_address).ip)


__all__ = ["AparelhoDaRede", "AparelhoPeloAdb", "Elemento", "NoDaTela", "Observacao", "Plano", "ProxyDoCliente",
           "ReligadoPelaInterface", "SFA_COMMIT_DE_REFERENCIA", "ROTULOS_DO_CLIENTE", "achar_botao", "classe_na_janela",
           "idioma_do_recurso", "rotulos_para", "comando_da_janela_do_start", "locale_do_aparelho", "religar_pela_interface", "comando_do_estado_da_interface",
           "comando_de_devolver_o_foco",
           "RedeAplicacaoError", "ServidorDeUmaVez", "TunelWg", "apagar_relatorios_de_falha", "comando_de_observacao",
           "comando_da_instalacao", "config_do_cliente", "desfazer", "endereco_no_tunel", "endpoint_do_central",
           "identidade_do_cliente", "instalado_em", "ler_observacao", "montar_plano", "observar", "origem_do_aparelho",
           "comando_de_religar", "provisionar", "religar_pelo_tile", "tocar_importacao"]
