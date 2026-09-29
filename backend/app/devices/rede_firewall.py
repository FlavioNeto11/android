"""O firewall do central diante dos aparelhos de OUTRA máquina (ADR-056, item 25.7) — só leitura.

O aparelho do notebook (worker da LAN) fala com o servidor sing-box do central pela rede do notebook: o NAT do
emulador de lá leva o UDP ao `rede.servidor.endpoint_lan:51820` daqui. O servidor escuta em todas as interfaces
(medido no 25.1: o endpoint do sing-box não tem campo de escuta), mas o Firewall do Windows do central decide se o
pacote chega. O emulador LOCAL não passa por isso (10.0.2.2 vira 127.0.0.1 do host), então só o remoto sente.

Mexer no firewall é proibido para a automação (decisão do dono P2: sem mudar a rede ou o firewall do sistema).
Então aqui só se LÊ — perfis, a rede em que o endereço da LAN está e as regras que tocam o executável ou a porta —
e se devolve o estado e o comando EXATO que o dono roda, num PowerShell de administrador. Estados:

| estado | quer dizer |
|---|---|
| `liberado` | uma regra habilitada de entrada permite UDP na porta ao executável (ou a entrada padrão do perfil é Allow) |
| `bloqueado` | uma regra habilitada de entrada BLOQUEIA (no Windows o bloqueio vence a permissão) — p.ex. a que o Windows cria sozinho quando o aviso "permitir acesso" fica sem resposta |
| `sem_regra` | firewall ligado, entrada padrão Block e nenhuma regra que permita |
| `desligado` | o firewall do perfil daquela rede está desligado |
| `desconhecido` | não deu para ler (fora do Windows, PowerShell ausente, erro, prazo) — não prova nem nega |

A leitura custa segundos (o módulo NetSecurity do PowerShell), então vive em cache: quem pergunta sem pressa (o
`GET /api/network/server`) lê o cache; a aplicação num aparelho remoto e o `POST …/firewall-check` leem de novo.
Nada aqui é segredo: caminhos, portas, nomes de regra.
"""
from __future__ import annotations

import asyncio
import base64
import ipaddress
import json
import logging
import os
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from ..util import now_iso

if TYPE_CHECKING:
    from ..config import RedeServidorCfg

log = logging.getLogger(__name__)

Estado = Literal["liberado", "bloqueado", "sem_regra", "desligado", "desconhecido"]
#: Os estados em que o aparelho remoto certamente não fecha o túnel: a aplicação é recusada com o comando.
FECHADOS: frozenset[str] = frozenset({"bloqueado", "sem_regra"})
#: O nome da regra que o comando sugerido cria. Fixo: a leitura seguinte a encontra pelo executável e pela porta, não
#: pelo nome, mas o nome diz ao dono, no "Firewall do Windows", de onde ela veio.
NOME_DA_REGRA = "Central de Aparelhos - rede por aparelho (WireGuard UDP {porta})"
NO_WINDOW = 0x08000000 if os.name == "nt" else 0
#: `Get-NetConnectionProfile` → nome do perfil do firewall.
_CATEGORIA_PARA_PERFIL = {"public": "Public", "private": "Private", "domainauthenticated": "Domain"}
_PERFIS = ("Domain", "Private", "Public")


def _ps(texto: str) -> str:
    """Literal de PowerShell entre aspas simples (a aspa simples dobra): nada é interpolado."""
    return "'" + str(texto).replace("'", "''") + "'"


# ============================================================================ o script (puro)
def script_de_leitura(binario: Path, porta: int) -> str:
    """O PowerShell de LEITURA: nenhum `Set-`, `New-`, `Enable-`, `Disable-` ou `Remove-`. Consulta o `ActiveStore`
    (o que vale de fato, política de grupo incluída) e devolve um JSON: perfis, redes (interface, categoria, IPs) e as
    regras candidatas — as do executável (qualquer porta) e as de UDP que cobrem a porta (qualquer programa)."""
    return "\n".join([
        "$ErrorActionPreference = 'Stop'",
        "$ProgressPreference = 'SilentlyContinue'",
        f"$bin = [System.IO.Path]::GetFullPath({_ps(str(binario))})",
        f"$porta = {int(porta)}",
        "$perfis = @(Get-NetFirewallProfile -PolicyStore ActiveStore | ForEach-Object {",
        "  [pscustomobject]@{ nome = [string]$_.Name; ligado = [string]$_.Enabled;"
        " entrada = [string]$_.DefaultInboundAction } })",
        "$redes = @(Get-NetConnectionProfile -ErrorAction SilentlyContinue | ForEach-Object {",
        "  [pscustomobject]@{ interface = [string]$_.InterfaceAlias; categoria = [string]$_.NetworkCategory;",
        "    ips = @(Get-NetIPAddress -InterfaceIndex $_.InterfaceIndex -ErrorAction SilentlyContinue |"
        " ForEach-Object { [string]$_.IPAddress }) } })",
        "$ids = @{}",
        # O caminho da regra pode vir com variável de ambiente (%SystemDrive%): compara expandido e sem caixa.
        "Get-NetFirewallApplicationFilter -PolicyStore ActiveStore | Where-Object {",
        "  $_.Program -and $_.Program -ne 'Any' -and",
        "  ([Environment]::ExpandEnvironmentVariables($_.Program) -ieq $bin) } | ForEach-Object { $ids[$_.InstanceID] = 1 }",
        "Get-NetFirewallPortFilter -PolicyStore ActiveStore | Where-Object {",
        "  $pf = $_; if (@('UDP', '17') -notcontains [string]$pf.Protocol) { return $false }",
        "  foreach ($p in @($pf.LocalPort)) { $p = [string]$p",
        "    if ($p -eq [string]$porta) { return $true }",
        "    if ($p -match '^(\\d+)-(\\d+)$' -and [int]$matches[1] -le $porta -and $porta -le [int]$matches[2])"
        " { return $true } }",
        "  return $false } | ForEach-Object { $ids[$_.InstanceID] = 1 }",
        "$regras = @(foreach ($id in @($ids.Keys)) {",
        "  $r = Get-NetFirewallRule -PolicyStore ActiveStore -Name $id -ErrorAction SilentlyContinue",
        "  if ($null -eq $r) { continue }",
        "  $pf = $r | Get-NetFirewallPortFilter",
        "  $af = $r | Get-NetFirewallApplicationFilter",
        "  [pscustomobject]@{ nome = [string]$r.Name; exibicao = [string]$r.DisplayName; habilitada = [string]$r.Enabled;",
        "    direcao = [string]$r.Direction; acao = [string]$r.Action; perfis = [string]$r.Profile;",
        "    origem = [string]$r.PolicyStoreSourceType; protocolo = [string]$pf.Protocol;",
        "    portas = @($pf.LocalPort | ForEach-Object { [string]$_ });",
        "    programa = [string]$af.Program } })",
        "[pscustomobject]@{ binario = $bin; porta = $porta; perfis = $perfis; redes = $redes; regras = $regras } |"
        " ConvertTo-Json -Depth 5 -Compress",
    ])


def comando_de_liberacao(binario: Path, porta: int, perfil: str | None) -> str:
    """A regra que o dono cria: entrada, UDP, só a porta do WireGuard, só o executável do sing-box e só da sub-rede
    local (o notebook está na mesma LAN; a internet não chega no par). No perfil da rede do endereço da LAN."""
    return (f"New-NetFirewallRule -DisplayName {_ps(NOME_DA_REGRA.format(porta=int(porta)))} -Direction Inbound "
            f"-Action Allow -Protocol UDP -LocalPort {int(porta)} -Program {_ps(str(binario))} "
            f"-RemoteAddress LocalSubnet -Profile {perfil or 'Any'}")


def comando_de_desbloqueio(nome: str) -> str:
    return f"Disable-NetFirewallRule -Name {_ps(nome)}"


# ============================================================================ a avaliação (pura)
@dataclass
class LeituraDoFirewall:
    """O que a leitura concluiu. `comandos` é o que o dono roda (vazio quando nada falta)."""

    estado: Estado
    detalhe: str
    endpoint: str | None = None
    perfil: str | None = None
    interface: str | None = None
    endpoint_local: bool | None = None
    regras_que_liberam: list[str] = field(default_factory=list)
    regras_que_bloqueiam: list[str] = field(default_factory=list)
    comandos: list[str] = field(default_factory=list)
    lido_em: str = field(default_factory=now_iso)

    @property
    def fechado(self) -> bool:
        return self.estado in FECHADOS

    def como_dict(self) -> dict[str, object]:
        d = asdict(self)
        return {"state": d["estado"], "detail": d["detalhe"], "endpoint": d["endpoint"], "profile": d["perfil"],
                "interface": d["interface"], "endpoint_is_local": d["endpoint_local"],
                "allowing_rules": d["regras_que_liberam"], "blocking_rules": d["regras_que_bloqueiam"],
                "commands": d["comandos"], "checked_at": d["lido_em"]}


def _mesmo_programa(programa: str, binario: Path) -> bool:
    p = os.path.expandvars((programa or "").strip())
    if not p or p.casefold() == "any":
        return True
    return os.path.normcase(os.path.normpath(p)) == os.path.normcase(os.path.normpath(str(binario)))


def _cobre_a_porta(portas: Sequence[str], porta: int) -> bool:
    for p in portas or ["Any"]:
        p = str(p).strip()
        if p.casefold() == "any" or p == str(porta):
            return True
        de, _, ate = p.partition("-")
        if ate and de.isdigit() and ate.isdigit() and int(de) <= porta <= int(ate):
            return True
    return False


def _cobre_o_perfil(perfis_da_regra: str, alvo: Sequence[str]) -> bool:
    texto = (perfis_da_regra or "Any").casefold()
    return "any" in texto or any(p.casefold() in texto for p in alvo)


def _lista(valor: object) -> list[dict[str, object]]:
    """O `ConvertTo-Json` do PowerShell 5.1 devolve um objeto solto quando a lista tem um item só."""
    if valor is None:
        return []
    if isinstance(valor, Mapping):
        return [dict(valor)]
    return [dict(v) for v in valor if isinstance(v, Mapping)]         # type: ignore[union-attr]


def _verdade(valor: object) -> bool:
    return str(valor).strip().casefold() in ("true", "1")


def avaliar(dados: Mapping[str, object], *, binario: Path, porta: int, endpoint: str | None) -> LeituraDoFirewall:
    """Do JSON da leitura ao estado. A ordem é a do Windows: perfil desligado não filtra; regra que bloqueia vence a
    que permite; sem regra, vale a ação padrão de entrada do perfil (`NotConfigured` = Block)."""
    perfis = {str(p.get("nome")): p for p in _lista(dados.get("perfis"))}
    redes = _lista(dados.get("redes"))
    perfil: str | None = None
    interface: str | None = None
    endpoint_local: bool | None = None
    if endpoint:
        try:
            alvo_ip = ipaddress.ip_address(endpoint)
        except ValueError:
            alvo_ip = None                          # nome: não dá para achar a interface por ele
        if alvo_ip is not None:
            endpoint_local = False
            for r in redes:
                ips = r.get("ips") or []
                ips = [ips] if isinstance(ips, str) else ips
                if any(str(ip).split("%")[0] == str(alvo_ip) for ip in ips):       # type: ignore[union-attr]
                    endpoint_local = True
                    interface = str(r.get("interface") or "") or None
                    perfil = _CATEGORIA_PARA_PERFIL.get(str(r.get("categoria") or "").casefold())
                    break
    alvo = [perfil] if perfil else [n for n in _PERFIS if n in perfis] or list(_PERFIS)
    base = LeituraDoFirewall(estado="desconhecido", detalhe="", endpoint=endpoint, perfil=perfil, interface=interface,
                             endpoint_local=endpoint_local)
    aviso = ""
    if endpoint and endpoint_local is False:
        # O endereço configurado não é de nenhuma interface desta máquina: IP errado, ou um NAT no meio. O firewall
        # até pode estar certo, mas o notebook não chega AQUI por ele.
        aviso = (f"; atenção: {endpoint} não é endereço de nenhuma interface do central (confira "
                 "rede.servidor.endpoint_lan)")
    ligados = [n for n in alvo if _verdade((perfis.get(n) or {}).get("ligado", "True"))]
    if perfis and not ligados:
        base.estado, base.detalhe = "desligado", f"firewall desligado no perfil {'/'.join(alvo)}" + aviso
        return base
    relevantes = []
    for r in _lista(dados.get("regras")):
        if not _verdade(r.get("habilitada")) or str(r.get("direcao", "")).casefold() != "inbound":
            continue
        if str(r.get("protocolo") or "Any").strip().casefold() not in ("any", "udp", "17"):
            continue
        portas = r.get("portas") or ["Any"]
        portas = [portas] if isinstance(portas, str) else portas
        if not _cobre_a_porta([str(p) for p in portas], porta):                   # type: ignore[union-attr]
            continue
        if not _mesmo_programa(str(r.get("programa") or "Any"), binario):
            continue
        if not _cobre_o_perfil(str(r.get("perfis") or "Any"), ligados):
            continue
        relevantes.append(r)
    bloqueiam = [r for r in relevantes if str(r.get("acao", "")).casefold() == "block"]
    liberam = [r for r in relevantes if str(r.get("acao", "")).casefold() == "allow"]
    base.regras_que_bloqueiam = [str(r.get("exibicao") or r.get("nome")) for r in bloqueiam]
    base.regras_que_liberam = [str(r.get("exibicao") or r.get("nome")) for r in liberam]
    rotulo = perfil or "/".join(ligados)
    if bloqueiam:
        base.estado = "bloqueado"
        base.comandos = [comando_de_desbloqueio(str(r.get("nome"))) for r in bloqueiam]
        if not liberam:
            base.comandos.append(comando_de_liberacao(binario, porta, perfil))
        gpo = [str(r.get("exibicao") or r.get("nome")) for r in bloqueiam
               if str(r.get("origem", "")).casefold() == "grouppolicy"]
        base.detalhe = (f"{len(bloqueiam)} regra(s) de entrada bloqueiam UDP {porta} ao sing-box no perfil {rotulo} "
                        f"({', '.join(base.regras_que_bloqueiam)[:200]}); no Windows o bloqueio vence a permissão"
                        + (f"; {', '.join(gpo)} vem de política de grupo: desligue-a lá" if gpo else "") + aviso)
        return base
    if liberam:
        base.estado = "liberado"
        base.detalhe = (f"entrada UDP {porta} liberada ao sing-box no perfil {rotulo} por "
                        f"{', '.join(base.regras_que_liberam)[:200]}" + aviso)
        return base
    padroes = {n: str((perfis.get(n) or {}).get("entrada") or "NotConfigured") for n in ligados}
    if padroes and all(v.casefold() == "allow" for v in padroes.values()):
        base.estado = "liberado"
        base.detalhe = f"sem regra, mas a entrada padrão do perfil {rotulo} é Allow" + aviso
        return base
    base.estado = "sem_regra"
    base.comandos = [comando_de_liberacao(binario, porta, perfil)]
    base.detalhe = (f"firewall ligado no perfil {rotulo} com entrada padrão Block e nenhuma regra que permita UDP "
                    f"{porta} ao sing-box" + aviso)
    return base


# ============================================================================ a leitura (borda trocável) e o cache
def executar_powershell(script: str, *, timeout: float = 60.0) -> str:
    """Roda o script de leitura. `-EncodedCommand` (UTF-16LE em base64): o texto vai inteiro, sem briga de aspas."""
    codificado = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    res = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                          "-EncodedCommand", codificado], capture_output=True, text=True, timeout=timeout,
                         creationflags=NO_WINDOW)
    if res.returncode != 0:
        cauda = (res.stderr or res.stdout or "").strip().splitlines()[-1:] or [""]
        raise RuntimeError(f"a leitura do firewall saiu com {res.returncode}: {cauda[0][:200]}")
    return res.stdout


class FirewallDoCentral:
    """A leitura do firewall com cache. `executar` é a borda: o teste troca por um dublê que devolve o JSON."""

    def __init__(self, cfg: Callable[[], RedeServidorCfg], binario: Callable[[], Path], *,
                 executar: Callable[[str], str] | None = None, relogio: Callable[[], float] = time.monotonic,
                 validade_s: float = 600.0) -> None:
        self._cfg, self._binario = cfg, binario
        self.executar = executar or executar_powershell
        self._agora = relogio
        self.validade_s = validade_s
        self._ultima: LeituraDoFirewall | None = None
        self._lida_em: float | None = None
        self._lock = asyncio.Lock()

    def ultima(self) -> LeituraDoFirewall | None:
        return self._ultima

    def _ler(self) -> LeituraDoFirewall:
        cfg = self._cfg()
        endpoint = (cfg.endpoint_lan or "").strip() or None
        binario = Path(os.path.abspath(self._binario()))
        if os.name != "nt" and self.executar is executar_powershell:
            return LeituraDoFirewall(estado="desconhecido", detalhe="fora do Windows: o firewall não é lido",
                                     endpoint=endpoint)
        try:
            dados = json.loads(self.executar(script_de_leitura(binario, cfg.porta_wireguard)) or "{}")
        except Exception as exc:  # noqa: BLE001 - não ler não prova nem nega: `desconhecido` com o motivo
            return LeituraDoFirewall(estado="desconhecido", detalhe=f"não foi possível ler o firewall: {exc}"[:300],
                                     endpoint=endpoint)
        if not isinstance(dados, Mapping):
            return LeituraDoFirewall(estado="desconhecido", detalhe="a leitura do firewall não devolveu um objeto",
                                     endpoint=endpoint)
        return avaliar(dados, binario=binario, porta=cfg.porta_wireguard, endpoint=endpoint)

    async def conferir(self, *, forcar: bool = False) -> LeituraDoFirewall:
        """A leitura dentro da validade, ou uma nova (fora do laço de eventos: são segundos de PowerShell)."""
        async with self._lock:
            if not forcar and self._ultima is not None and self._lida_em is not None \
                    and self._agora() - self._lida_em < self.validade_s:
                return self._ultima
            leitura = await asyncio.to_thread(self._ler)
            if self._ultima is None or leitura.estado != self._ultima.estado:
                log.info("firewall do central para a rede remota: %s — %s", leitura.estado, leitura.detalhe)
            self._ultima, self._lida_em = leitura, self._agora()
            return leitura


__all__ = ["FECHADOS", "FirewallDoCentral", "LeituraDoFirewall", "avaliar", "comando_de_desbloqueio",
           "comando_de_liberacao", "executar_powershell", "script_de_leitura"]
