"""A saída do PRÓPRIO central e a pergunta "este aparelho ainda sai pela casa?" (ADR-056, item 29.20).

O objetivo do dono é que nenhum Android saia pelo IP da rede da casa dele. A plataforma já mede, de dentro de cada
aparelho, o IP público de saída (`rede_medicao`, eco HTTP/1.0 por `nc`); faltava a outra metade: a saída de quem está
ATRÁS da casa. O central é essa referência: o backend roda na máquina da casa, então o IP que um eco vê dele é o da
casa. Aparelho com a MESMA saída medida (IPv4 ou IPv6) sai pela casa — o aparelho do notebook da LAN e o perfil
`vpn-central-wireguard` (WireGuard que termina no central com SNAT) são exatamente esse caso, e a acusação é o ponto.

Este módulo mede o central com os mesmos hosts de eco da sonda do aparelho (`rede.sonda.hosts_ipv4/hosts_ipv6`), com
a família forçada por socket (`asyncio.open_connection(family=…)`), e o mesmo julgamento da resposta
(`sonda_rede.ip_da_resposta_http`). Duas regras que valem para a medida e para o veredito:

- **Falha de medida é "não medida", com o motivo; nunca "não é casa".** Sem IPv6 no central, sem resposta do eco,
  medida vencida ou nunca feita: o veredito de quem depende dela é `None`, não `False`.
- **A medida não bloqueia nada.** Quem a faz é o laço próprio (`SaidaDoCentral.laco`, iniciado ao lado do da
  convergência; a cada volta de 60 s só mede quando a anterior passou de `rede.sonda.central_ttl_s`); a API só LÊ o cache
  (`atual`). Uma réplica `ROLE=api` não roda o laço e, portanto, não mede: o motivo diz isso.

Sem estado em banco (sem migração): o cache vive em memória e o que importa por aparelho já está em
`device_network.egress_*`.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from .rede_servidor import gerenciado_pelo_central
from .sonda_rede import IpDeSaida, host_valido, ip_da_resposta_http

if TYPE_CHECKING:
    from ..config import RedeSondaCfg

log = logging.getLogger(__name__)

#: Uma medida vale por esta quantidade de TTLs; depois disso o central volta a "não medido" (a saída pode ter mudado:
#: o provedor da casa troca o IPv4 dinâmico, e comparar com um IP velho esconderia ou inventaria uma acusação).
_VALIDADE_EM_TTLS = 3
#: Uma família sem valor (falhou, ou o central não a tem) é tentada de novo depois disto, sem esperar o TTL inteiro.
_RETENTATIVA_S = 120.0
#: A porta do eco (HTTP puro, como na sonda do aparelho). Constante de módulo só para o teste apontar para um eco local.
_PORTA = 80
#: Teto da resposta lida do eco (o corpo é um IP; mais que isso não é eco).
_LIMITE_DA_RESPOSTA = 8192

#: Mede os hosts de uma família, na ordem, e devolve o primeiro IP público (ou o motivo). Injetável: o teste não abre socket.
Medidor = Callable[[list[str], int, float], Awaitable[IpDeSaida]]


async def _um_host(host: str, familia: int, prazo_s: float) -> IpDeSaida:
    host = host_valido(host)
    pedido = (f"GET / HTTP/1.0\r\nHost: {host}\r\nUser-Agent: sonda-de-rede\r\nConnection: close\r\n\r\n").encode()
    fam = socket.AF_INET if familia == 4 else socket.AF_INET6

    async def _fala() -> bytes:
        leitor, escritor = await asyncio.open_connection(host, _PORTA, family=fam)
        try:
            escritor.write(pedido)
            await escritor.drain()
            dados = b""
            while len(dados) < _LIMITE_DA_RESPOSTA:
                parte = await leitor.read(1024)
                if not parte:
                    break
                dados += parte
            return dados
        finally:
            escritor.close()

    try:
        bruto = await asyncio.wait_for(_fala(), timeout=prazo_s)
    except (asyncio.TimeoutError, TimeoutError):
        return IpDeSaida(None, f"{host}: sem resposta em {prazo_s:g} s")
    except socket.gaierror:
        return IpDeSaida(None, f"{host}: sem endereço IPv{familia} para o nome")
    except OSError as exc:
        return IpDeSaida(None, f"{host}: {(exc.strerror or type(exc).__name__)[:80]}")
    corpo = bruto.decode("utf-8", "replace").replace("\r\n", "\n").strip()
    if not corpo:
        return IpDeSaida(None, f"{host}: sem resposta")
    lido = ip_da_resposta_http(corpo, familia)
    return lido if lido.ip else IpDeSaida(None, f"{host}: {lido.motivo}")


async def medir_familia(hosts: list[str], familia: int, prazo_s: float) -> IpDeSaida:
    """O medidor de verdade: o primeiro host da lista que devolver um IP público da família. Nunca levanta: o que
    deu errado vira o motivo (o de cada host, juntos)."""
    motivos: list[str] = []
    for host in hosts:
        try:
            lido = await _um_host(host, familia, prazo_s)
        except Exception as exc:  # noqa: BLE001 - medir não pode derrubar a varredura; vira motivo
            lido = IpDeSaida(None, f"{host}: {type(exc).__name__}")
        if lido.ip:
            return lido
        motivos.append(lido.motivo)
    return IpDeSaida(None, "; ".join(motivos)[:300] or "nenhum host de eco configurado")


@dataclass
class _Familia:
    ip: str | None = None
    em: float | None = None                       # relógio (epoch) da última medida COM IP
    motivo: str | None = None                     # por que a última tentativa não deu IP (None = deu)


class SaidaDoCentral:
    """A saída pública do central, por família, com cache de memória. `cfg` devolve a seção da sonda (relida a cada
    uso: o `config.yaml` pode mudar sem reinício do objeto)."""

    def __init__(self, cfg: Callable[[], RedeSondaCfg], *, medidor: Medidor = medir_familia,
                 relogio: Callable[[], float] = time.time) -> None:
        self.cfg, self.medidor, self.relogio = cfg, medidor, relogio
        self._fam: dict[int, _Familia] = {4: _Familia(), 6: _Familia()}
        self._tentou_em: float | None = None
        self._trava: asyncio.Lock | None = None

    # ------------------------------------------------------------------ leitura (sem rede, sem espera)
    def _vale(self, f: _Familia, agora: float, ttl: float) -> bool:
        return f.ip is not None and f.em is not None and agora - f.em <= ttl * _VALIDADE_EM_TTLS

    def atual(self) -> dict[str, object]:
        """`{ipv4, ipv6, measured_at, reason}`: o que vale agora. Família sem medida válida = `None` (nunca "limpo"),
        e `reason` diz por quê: desligada, ainda não medida, sem IP da família (com o erro) ou medida vencida."""
        cfg = self.cfg()
        agora = self.relogio()
        if not cfg.medir_central:
            return {"ipv4": None, "ipv6": None, "measured_at": None,
                    "reason": "medida do central desligada (rede.sonda.medir_central: false)"}
        valores: dict[int, str | None] = {}
        motivos: list[str] = []
        for versao in (4, 6):
            f = self._fam[versao]
            if self._vale(f, agora, cfg.central_ttl_s):
                valores[versao] = f.ip
                if f.motivo:                      # a última volta falhou, mas a medida anterior ainda vale
                    motivos.append(f"IPv{versao}: medida anterior mantida (a última tentativa falhou: {f.motivo})")
                continue
            valores[versao] = None
            if f.ip is not None:
                motivos.append(f"IPv{versao}: a medida de {_iso(f.em)} venceu (mais de "
                               f"{_VALIDADE_EM_TTLS}x rede.sonda.central_ttl_s)")
            elif f.motivo:
                motivos.append(f"IPv{versao}: {f.motivo}")
            elif self._tentou_em is None:
                motivos.append(f"IPv{versao}: ainda não medido (o laço do central mede ao subir; réplica ROLE=api "
                               "não mede)")
        medidas = [self._fam[v].em for v in (4, 6) if valores[v] is not None and self._fam[v].em is not None]
        return {"ipv4": valores[4], "ipv6": valores[6],
                "measured_at": _iso(max(medidas)) if medidas else None,
                "reason": "; ".join(motivos) if motivos else "ok"}

    # ------------------------------------------------------------------ medida (assíncrona, só pela varredura)
    def _pede_medida(self, agora: float, ttl: float) -> bool:
        if self._tentou_em is None:
            return True
        idade = agora - self._tentou_em
        if idade >= ttl:
            return True
        sem_ip = any(not self._vale(f, agora, ttl) for f in self._fam.values())
        return sem_ip and idade >= min(_RETENTATIVA_S, ttl)

    async def atualizar_se_vencida(self) -> bool:
        """Mede de novo se a medida passou do TTL (ou se uma família ficou sem IP e já faz `_RETENTATIVA_S`). Devolve
        se mediu. Uma medida por vez; falha vira motivo, nunca exceção."""
        cfg = self.cfg()
        if not cfg.medir_central:
            return False
        if self._trava is None:
            self._trava = asyncio.Lock()
        async with self._trava:
            agora = self.relogio()
            if not self._pede_medida(agora, cfg.central_ttl_s):
                return False
            hosts = {4: list(cfg.hosts_ipv4), 6: list(cfg.hosts_ipv6)}
            lidos = await asyncio.gather(*(self._medir(hosts[v], v, cfg.central_prazo_s) for v in (4, 6)))
            self._tentou_em = self.relogio()
            for versao, lido in zip((4, 6), lidos, strict=True):
                f = self._fam[versao]
                if lido.ip:
                    f.ip, f.em, f.motivo = lido.ip, self._tentou_em, None
                else:
                    f.motivo = lido.motivo        # o IP anterior fica até vencer: uma falha solta não o apaga
            return True

    async def laco(self, intervalo_s: float = 60.0) -> None:
        """Uma volta por `intervalo_s`: `atualizar_se_vencida` decide se já é hora (o TTL é da config)."""
        while True:
            try:
                await self.atualizar_se_vencida()
            except Exception as exc:  # noqa: BLE001 - o laço não morre por uma volta ruim
                log.info("saída do central: %s", exc)
            await asyncio.sleep(intervalo_s)

    async def _medir(self, hosts: list[str], versao: int, prazo_s: float) -> IpDeSaida:
        try:
            return await self.medidor(hosts, versao, prazo_s)
        except Exception as exc:  # noqa: BLE001 - um medidor injetado ou futuro não derruba a varredura
            log.info("saída do central IPv%s: %s", versao, exc)
            return IpDeSaida(None, f"falha ao medir ({type(exc).__name__})")


def _iso(epoch: float | None) -> str | None:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat(timespec="seconds") if epoch is not None else None


# ============================================================================ o veredito (funções puras)
def mesma_saida(versao: int, ip_aparelho: str | None, ip_central: str | None) -> bool | None:
    """A saída medida do aparelho é a do central, na família `versao`? `None` se faltar qualquer um dos dois (o que
    não foi medido não é "limpo"). IPv4: o mesmo endereço. IPv6: o mesmo /64 — o endereço de saída costuma ser temporário
    (privacidade), e dois endereços do mesmo /64 são a mesma rede da casa."""
    if not ip_aparelho or not ip_central:
        return None
    try:
        a, c = ipaddress.ip_address(ip_aparelho), ipaddress.ip_address(ip_central)
    except ValueError:
        return None
    if a.version != versao or c.version != versao:
        return None
    if versao == 4:
        return a == c
    return ipaddress.ip_network(f"{a}/64", strict=False) == ipaddress.ip_network(f"{c}/64", strict=False)


#: O que o IPv6 global é: o perfil "leva IPv6" se alguma rota dele cobre `2000::/3` (inclui `::/0`).
_IPV6_GLOBAL = ipaddress.ip_network("2000::/3")


def _lista(valor: object) -> list[str]:
    if isinstance(valor, str):
        return [p.strip() for p in valor.split(",") if p.strip()]
    if isinstance(valor, list):
        return [str(p).strip() for p in valor if str(p).strip()]
    return []


def perfil_leva_ipv6(params: Mapping[str, object] | None, protocolo: str | None) -> bool:
    """O perfil de VPN carrega tráfego IPv6? Perfil gerenciado pelo central: não (o endereço do aparelho no túnel é um
    /32 IPv4: ver `rede_aplicacao.montar_plano`). WireGuard externo: precisa de endereço IPv6 em `params.address` E de
    rota IPv6 global em `params.allowed_ips` (ausente = o padrão do plano, `0.0.0.0/0` e `::/0`). Sem perfil: não leva."""
    if params is None or gerenciado_pelo_central(params) or protocolo != "wireguard":
        return False
    try:
        tem_endereco = any(ipaddress.ip_interface(e).version == 6 for e in _lista(params.get("address")))
        rotas = _lista(params.get("allowed_ips")) or ["0.0.0.0/0", "::/0"]
        redes = [ipaddress.ip_network(r, strict=False) for r in rotas]
        tem_rota = any(isinstance(r, ipaddress.IPv6Network) and r.supernet_of(_IPV6_GLOBAL) for r in redes)
    except ValueError:
        return False                               # perfil malformado: não se presume que leve IPv6
    return tem_endereco and tem_rota


@dataclass(frozen=True)
class SaidaPelaCasa:
    """O veredito de um aparelho. `ipv4`/`ipv6`: a saída medida é a do central (`None` = sem medida de um dos lados);
    `ipv6_outside_profile`: o aparelho mediu IPv6 e o perfil de VPN dele não leva IPv6 (esse IPv6 sai direto pela
    rede de quem hospeda, fora do túnel); `leaves_by_home`: o resumo (`True` se qualquer um acusa; `False` só com o
    IPv4 medido diferente e nenhum IPv6 acusando nem incerto; senão `None`)."""

    ipv4: bool | None
    ipv6: bool | None
    ipv6_outside_profile: bool | None
    reason: str

    @property
    def leaves_by_home(self) -> bool | None:
        if self.ipv4 or self.ipv6 or self.ipv6_outside_profile:
            return True
        return False if self.ipv4 is False and self.ipv6 is not None else None

    def como_dict(self) -> dict[str, object]:
        return {"ipv4": self.ipv4, "ipv6": self.ipv6, "ipv6_outside_profile": self.ipv6_outside_profile,
                "leaves_by_home": self.leaves_by_home, "reason": self.reason}


def veredito(*, medido: bool, ipv4: str | None, ipv6: str | None, central: Mapping[str, object],
             leva_ipv6: bool, com_rede_pedida: bool) -> SaidaPelaCasa:
    """Aparelho × central. `medido` = a saída da linha é a da revisão pedida (estado `trafego_verificado`/`parcial`):
    fora disso só o POSITIVO vale (a saída velha ainda é o que o aparelho faz, e acusar é o lado seguro), e um
    "não é casa" vira `None` (a revisão nova pode ter mudado a saída: incerteza não é limpa). Aparelho sem saída
    medida nenhuma é `None` em tudo, com o motivo.

    IPv6 sem saída medida no aparelho, com o IPv4 medido, é "o aparelho não tem saída IPv6" (a sonda mede as duas
    famílias na mesma passada): `ipv6 = False` e `ipv6_outside_profile = False`, sem incerteza."""
    if not (ipv4 or ipv6):
        quando = ("a saída do aparelho nunca foi medida" if com_rede_pedida
                  else "sem rede pedida: a saída do aparelho nunca foi medida (a sonda só roda com perfil atribuído)")
        return SaidaPelaCasa(None, None, None, quando)
    motivos: list[str] = []
    c4, c6 = central.get("ipv4"), central.get("ipv6")
    v4 = mesma_saida(4, ipv4, str(c4) if c4 else None)
    v6 = mesma_saida(6, ipv6, str(c6) if c6 else None) if ipv6 else (False if ipv4 else None)
    fora = (not leva_ipv6) if ipv6 else (False if ipv4 else None)
    if v4 is None and ipv4:
        motivos.append(f"saída IPv4 do central sem medida ({central.get('reason')})")
    if v6 is None and ipv6:
        motivos.append(f"saída IPv6 do central sem medida ({central.get('reason')})")
    if v4:
        motivos.append(f"o aparelho sai pelo IPv4 do central ({ipv4})")
    if v6 and ipv6:
        motivos.append(f"o aparelho sai pelo mesmo /64 IPv6 do central ({ipv6})")
    if fora:
        motivos.append(f"IPv6 medido ({ipv6}) com o perfil sem IPv6: sai direto, fora do túnel")
    if not medido:
        # Revisão pedida ainda não medida: só o positivo é firme.
        v4 = True if v4 else None
        v6 = True if v6 else None
        fora = True if fora else None
        motivos.append("a revisão de rede pedida ainda não foi medida: só o que acusa vale")
    return SaidaPelaCasa(v4, v6, fora, "; ".join(motivos) if motivos else "a saída medida não é a do central")
