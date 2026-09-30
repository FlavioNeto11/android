"""O firewall do central diante dos aparelhos de OUTRA máquina (ADR-056, itens 25.7 e 29.8) — só leitura.

O aparelho do notebook (worker da LAN) fala com o servidor sing-box do central pela rede do notebook: o NAT do
emulador de lá leva o UDP ao `rede.servidor.endpoint_lan:51820` daqui. O servidor escuta em todas as interfaces,
IPv4 e IPv6 (medido no 25.1 e de novo em 30/09: o endpoint do sing-box não tem campo de escuta), mas o Firewall do
Windows do central decide se o pacote chega. O emulador LOCAL não passa por isso (10.0.2.2 vira 127.0.0.1 do host),
então só o remoto sente.

Mexer no firewall é proibido para a automação (decisão do dono P2: sem mudar a rede ou o firewall do sistema).
Então aqui só se LÊ e se devolve o estado e o comando EXATO que o dono roda, num PowerShell de administrador.

O que decide se o pacote do notebook passa é o que vale DE FATO para ele, e é isso que a leitura confere em cada
regra de entrada habilitada (29.8): a porta UDP; o perfil EFETIVO da interface que tem o `endpoint_lan` (uma regra
só de Private não vale numa rede Public); essa interface (uma regra presa ao cabo não vale na Wi-Fi); a origem, que
precisa conter a sub-rede IPv4 daquela interface (endereço e prefixo lidos do sistema); e o programa, quando a regra
tem um. Estados:

| estado | quer dizer |
|---|---|
| `liberado` | uma regra de entrada habilitada permite UDP na porta, vinda da LAN do endpoint, naquela interface e naquele perfil (ou a entrada padrão do perfil é Allow). Origem `Any` libera, com o aviso de que está mais aberta que o necessário |
| `bloqueado` | uma regra habilitada de entrada BLOQUEIA (no Windows o bloqueio vence a permissão) — p.ex. a que o Windows cria sozinho quando o aviso "permitir acesso" fica sem resposta |
| `regra_obsoleta` | a regra existe, mas aponta (`-Program`) para um executável que não é o binário atual do servidor — o caminho do sing-box tem a versão no nome, e a atualização deixa a regra para trás. Fechado |
| `sem_regra` | firewall ligado, entrada padrão Block e nenhuma regra que cubra (as que existem e não cobrem — outra interface, outra sub-rede, outro perfil — vão ditas no detalhe) |
| `desligado` | o firewall do perfil daquela rede está desligado |
| `desconhecido` | não deu para ler (fora do Windows, PowerShell ausente, erro, prazo) ou a regra que decidiria não pôde ser conferida (p.ex. o `endpoint_lan` não é endereço desta máquina) — não prova nem nega |

O comando proposto não leva `-Program` (morreria na atualização do sing-box): restringe pela porta, pela sub-rede
IPv4 da LAN (nada de `Any`: a Wi-Fi do central tem IPv6 público, e nada de `LocalSubnet`: ele cobre também o
vEthernet do WSL) e pela interface, com `-Profile Any` para sobreviver à reclassificação da rede. É idempotente (tira
a regra de mesmo nome antes de criar). O que o sistema não informou vira marcador explícito, nunca um valor inventado.

Limite conhecido: as candidatas são as regras UDP que cobrem a porta e as do executável. Uma regra genérica de
protocolo `Any` sem programa não é lida — nesse conjunto moram as regras de pacote de app e as presas a um usuário
local, que não dizem respeito ao sing-box (medido em 30/09: 17 delas no central).

A leitura custa segundos (o módulo NetSecurity do PowerShell), então vive em cache: quem pergunta sem pressa (o
`GET /api/network/server`) lê o cache; a aplicação num aparelho remoto e o `POST …/firewall-check` leem de novo.
Nada aqui é segredo: caminhos, portas, nomes de regra, endereços da LAN.
"""
from __future__ import annotations

import asyncio
import base64
import fnmatch
import ipaddress
import json
import logging
import os
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path, PureWindowsPath
from typing import TYPE_CHECKING, Literal

from ..util import now_iso

if TYPE_CHECKING:
    from ..config import RedeServidorCfg

log = logging.getLogger(__name__)

Estado = Literal["liberado", "bloqueado", "sem_regra", "regra_obsoleta", "desligado", "desconhecido"]
#: Os estados em que o aparelho remoto certamente não fecha o túnel: a aplicação é recusada com o comando.
FECHADOS: frozenset[str] = frozenset({"bloqueado", "sem_regra", "regra_obsoleta"})
#: O nome da regra que o comando sugerido cria. Fixo: é por ele que o comando troca a regra anterior (idempotência),
#: que a inspeção e a reversão a acham, e que a leitura reconhece como NOSSA uma regra que ficou obsoleta.
NOME_DA_REGRA = "Central de Aparelhos - rede por aparelho (WireGuard UDP {porta})"
#: O que o sistema não informou: marcadores que o PowerShell RECUSA (`<` é reservado), para o comando incompleto não
#: rodar pela metade.
SEM_SUB_REDE = "<SUB-REDE-IPV4-DA-LAN>"
SEM_INTERFACE = "<INTERFACE-DA-LAN>"
NO_WINDOW = 0x08000000 if os.name == "nt" else 0
#: `Get-NetConnectionProfile` → nome do perfil do firewall.
_CATEGORIA_PARA_PERFIL = {"public": "Public", "private": "Private", "domainauthenticated": "Domain"}
_PERFIS = ("Domain", "Private", "Public")
#: Endereços IPv6 roteáveis na internet (global unicast). Um `is_global` deixaria de fora o prefixo de documentação.
_IPV6_GLOBAL = ipaddress.ip_network("2000::/3")


def _ps(texto: str) -> str:
    """Literal de PowerShell entre aspas simples (a aspa simples dobra): nada é interpolado."""
    return "'" + str(texto).replace("'", "''") + "'"


# ============================================================================ o script (puro)
def script_de_leitura(binario: Path, porta: int) -> str:
    """O PowerShell de LEITURA: nenhum `Set-`, `New-`, `Enable-`, `Disable-` ou `Remove-`. Consulta o `ActiveStore`
    (o que vale de fato, política de grupo incluída) e devolve um JSON: perfis, redes (interface, categoria, IPs e o
    prefixo de cada IPv4) e as regras candidatas — as do executável (qualquer porta) e as de UDP que cobrem a porta
    (qualquer programa) —, cada uma com porta, perfil, origem, endereço local, interface e programa.

    Os filtros vêm em lote (uma consulta por tipo, indexada pelo `InstanceID` da regra): medido em 30/09, ~65 ms por
    tipo contra ~1 s de um `Get-…Filter` por regra candidata."""
    return "\n".join([
        "$ErrorActionPreference = 'Stop'",
        "$ProgressPreference = 'SilentlyContinue'",
        f"$bin = [System.IO.Path]::GetFullPath({_ps(str(binario))})",
        f"$porta = {int(porta)}",
        "$perfis = @(Get-NetFirewallProfile -PolicyStore ActiveStore | ForEach-Object {",
        "  [pscustomobject]@{ nome = [string]$_.Name; ligado = [string]$_.Enabled;"
        " entrada = [string]$_.DefaultInboundAction } })",
        # Todas as interfaces com endereço (a que não tem perfil de conexão também: o endpoint pode estar nela), e a
        # categoria de quem tem. O prefixo do IPv4 é o que dá a sub-rede da regra.
        "$categorias = @{}",
        "Get-NetConnectionProfile -ErrorAction SilentlyContinue | ForEach-Object {"
        " $categorias[[string]$_.InterfaceIndex] = [string]$_.NetworkCategory }",
        "$redes = @(Get-NetIPAddress -ErrorAction SilentlyContinue | Group-Object -Property InterfaceIndex |"
        " ForEach-Object {",
        "  $g = @($_.Group)",
        "  [pscustomobject]@{ interface = [string]$g[0].InterfaceAlias; categoria = [string]$categorias[[string]$_.Name];",
        "    ips = @($g | ForEach-Object { [string]$_.IPAddress });",
        "    ipv4 = @($g | Where-Object { ([string]$_.IPAddress) -notmatch ':' } | ForEach-Object {",
        "      [pscustomobject]@{ ip = [string]$_.IPAddress; prefixo = [int]$_.PrefixLength } }) } })",
        "function Indice($itens) { $h = @{}; foreach ($i in @($itens)) { $h[[string]$i.InstanceID] = $i }; return $h }",
        "$fPorta = Indice (Get-NetFirewallPortFilter -PolicyStore ActiveStore)",
        "$fEndereco = Indice (Get-NetFirewallAddressFilter -PolicyStore ActiveStore)",
        "$fInterface = Indice (Get-NetFirewallInterfaceFilter -PolicyStore ActiveStore)",
        "$fTipo = Indice (Get-NetFirewallInterfaceTypeFilter -PolicyStore ActiveStore)",
        "$fPrograma = Indice (Get-NetFirewallApplicationFilter -PolicyStore ActiveStore)",
        "$fServico = Indice (Get-NetFirewallServiceFilter -PolicyStore ActiveStore)",
        "$fSeguranca = Indice (Get-NetFirewallSecurityFilter -PolicyStore ActiveStore)",
        "$regras = @(Get-NetFirewallRule -PolicyStore ActiveStore -Direction Inbound -Enabled True | ForEach-Object {",
        "  $r = $_; $id = [string]$r.InstanceID; $pf = $fPorta[$id]; $af = $fPrograma[$id]",
        "  $programa = [string]$af.Program",
        # O caminho da regra pode vir com variável de ambiente (%SystemDrive%): compara expandido e sem caixa.
        "  $doBinario = $programa -and $programa -ne 'Any' -and"
        " ([Environment]::ExpandEnvironmentVariables($programa) -ieq $bin)",
        "  $naPorta = $false",
        "  if (@('UDP', '17') -contains [string]$pf.Protocol) { foreach ($p in @($pf.LocalPort)) { $p = [string]$p",
        "    if ($p -eq 'Any' -or $p -eq [string]$porta) { $naPorta = $true }",
        "    if ($p -match '^(\\d+)-(\\d+)$' -and [int]$matches[1] -le $porta -and $porta -le [int]$matches[2])"
        " { $naPorta = $true } } }",
        "  if (-not ($doBinario -or $naPorta)) { return }",
        "  $pacote = [string]$r.PackageFamilyName; if (-not $pacote) { $pacote = [string]$af.Package }",
        "  [pscustomobject]@{ nome = [string]$r.Name; exibicao = [string]$r.DisplayName; habilitada = [string]$r.Enabled;",
        "    direcao = [string]$r.Direction; acao = [string]$r.Action; perfis = [string]$r.Profile;",
        "    origem = [string]$r.PolicyStoreSourceType; protocolo = [string]$pf.Protocol;",
        "    portas = @($pf.LocalPort | ForEach-Object { [string]$_ }); programa = $programa;",
        "    remoto = @($fEndereco[$id].RemoteAddress | ForEach-Object { [string]$_ });",
        "    local = @($fEndereco[$id].LocalAddress | ForEach-Object { [string]$_ });",
        "    interfaces = @($fInterface[$id].InterfaceAlias | ForEach-Object { [string]$_ });",
        "    tipo_interface = [string]$fTipo[$id].InterfaceType;",
        # A quem mais a regra está presa: pacote de app, serviço, usuário local, IPsec. O sing-box não é nenhum deles.
        "    pacote = $pacote; dono = [string]$r.Owner; servico = [string]$fServico[$id].Service;",
        "    usuario_local = [string]$fSeguranca[$id].LocalUser;",
        "    autenticacao = [string]$fSeguranca[$id].Authentication } })",
        "[pscustomobject]@{ binario = $bin; porta = $porta; perfis = $perfis; redes = $redes; regras = $regras } |"
        " ConvertTo-Json -Depth 6 -Compress",
    ])


def comando_de_liberacao(porta: int, *, sub_rede: str | None, interface: str | None) -> str:
    """A regra que o dono cria, numa linha: entrada, UDP, só a porta do WireGuard, só da sub-rede IPv4 da LAN do
    endpoint e só naquela interface. Sem `-Program` (o caminho do sing-box tem a versão: a regra morreria na
    atualização) e com `-Profile Any` (a restrição de verdade é a origem e a interface; o perfil muda quando o Windows
    reclassifica a rede). Idempotente: tira a regra de mesmo nome antes de criar — rodar de novo troca, não duplica
    (`-EA 0` é `-ErrorAction SilentlyContinue`: sem regra anterior, segue calado). `sub_rede`/`interface` vazios = o
    sistema não informou: vira marcador, e o PowerShell recusa a linha inteira em vez de criar uma regra mais aberta."""
    return (f"$n={_ps(NOME_DA_REGRA.format(porta=int(porta)))}; "
            "Get-NetFirewallRule -DisplayName $n -EA 0 | Remove-NetFirewallRule; "
            f"New-NetFirewallRule -DisplayName $n -Direction Inbound -Action Allow -Protocol UDP -LocalPort {int(porta)} "
            f"-RemoteAddress {sub_rede or SEM_SUB_REDE} -InterfaceAlias {_ps(interface or SEM_INTERFACE)} -Profile Any")


def comando_de_inspecao(porta: int) -> str:
    """O que o dono roda para VER a regra (só leitura): os campos que decidem a leitura, numa lista. Sem a regra, o
    `Get-NetFirewallRule` responde que não achou nenhuma com esse nome."""
    return (f"Get-NetFirewallRule -DisplayName {_ps(NOME_DA_REGRA.format(porta=int(porta)))} | ForEach-Object {{ "
            "[pscustomobject]@{ Regra = $_.DisplayName; Habilitada = $_.Enabled; Acao = $_.Action; Perfil = $_.Profile; "
            "Protocolo = ($_ | Get-NetFirewallPortFilter).Protocol; Porta = ($_ | Get-NetFirewallPortFilter).LocalPort; "
            "Origem = ($_ | Get-NetFirewallAddressFilter).RemoteAddress; "
            "Interface = ($_ | Get-NetFirewallInterfaceFilter).InterfaceAlias; "
            "Programa = ($_ | Get-NetFirewallApplicationFilter).Program } } | Format-List")


def comando_de_reversao(porta: int) -> str:
    """Desfaz a liberação: tira a regra pelo nome (o aparelho remoto volta a ser recusado na próxima leitura)."""
    return f"Remove-NetFirewallRule -DisplayName {_ps(NOME_DA_REGRA.format(porta=int(porta)))}"


def comando_de_desbloqueio(nome: str) -> str:
    return f"Disable-NetFirewallRule -Name {_ps(nome)}"


# ============================================================================ a avaliação (pura)
@dataclass
class LeituraDoFirewall:
    """O que a leitura concluiu. `comandos` é o que FALTA o dono rodar (vazio quando nada falta) e é o que vai no
    `error` da aplicação recusada; `inspecao` e `reversao` acompanham toda leitura, fora dele."""

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
    #: A sub-rede IPv4 da interface do endpoint (endereço e prefixo do sistema): é a origem que a regra precisa conter.
    sub_rede: str | None = None
    #: Regras que liberam mas merecem atenção (origem `Any`).
    avisos: list[str] = field(default_factory=list)
    #: Regras nossas (pelo nome ou pelo executável) que apontam para um programa que não é o binário atual.
    regras_obsoletas: list[str] = field(default_factory=list)
    #: Regras para a porta que NÃO cobrem, com o porquê (outra interface, outra sub-rede, outro perfil).
    regras_que_nao_cobrem: list[str] = field(default_factory=list)
    #: O que o sistema não informou e virou marcador no comando (`interface`, `sub-rede`).
    faltou: list[str] = field(default_factory=list)
    inspecao: str = ""
    reversao: str = ""

    @property
    def fechado(self) -> bool:
        return self.estado in FECHADOS

    def como_dict(self) -> dict[str, object]:
        d = asdict(self)
        return {"state": d["estado"], "detail": d["detalhe"], "endpoint": d["endpoint"], "profile": d["perfil"],
                "interface": d["interface"], "endpoint_is_local": d["endpoint_local"],
                "allowing_rules": d["regras_que_liberam"], "blocking_rules": d["regras_que_bloqueiam"],
                "commands": d["comandos"], "checked_at": d["lido_em"], "lan_subnet": d["sub_rede"],
                "warnings": d["avisos"], "stale_rules": d["regras_obsoletas"],
                "ignored_rules": d["regras_que_nao_cobrem"], "missing": d["faltou"],
                "inspect_command": d["inspecao"], "revert_command": d["reversao"]}


#: O que uma regra é para o pacote do notebook: `cobre` (vale para ele), `nao_cobre` (é para a porta, mas não para
#: ele — com o porquê), `obsoleta` (nossa, de um executável que não é o de hoje), `alheia` (de outro programa, pacote
#: ou serviço: não diz respeito ao sing-box) e `incerta` (não deu para conferir: não prova nem nega).
_Veredito = Literal["cobre", "nao_cobre", "obsoleta", "alheia", "incerta"]


@dataclass(frozen=True)
class _Alvo:
    """O que o pacote do notebook encontra aqui: a porta, o binário que escuta, os perfis ligados (um só quando a
    interface do endpoint é conhecida), a interface, o endereço do endpoint e a LAN dele."""

    porta: int
    binario: Path
    perfil: str | None
    ligados: tuple[str, ...]
    interface: str | None
    endereco: ipaddress.IPv4Address | None
    lan: ipaddress.IPv4Network | None
    ipv6: bool = False


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


def _textos(valor: object) -> list[str]:
    """Um campo de lista da regra (`RemoteAddress`, `InterfaceAlias`): texto solto ou lista, sem os vazios. Lista
    vazia = a leitura não trouxe o campo (o Windows devolve `Any` para o que não restringe)."""
    itens = [valor] if isinstance(valor, str) else list(valor or [])              # type: ignore[call-overload]
    return [str(i).strip() for i in itens if str(i).strip()]


def _verdade(valor: object) -> bool:
    return str(valor).strip().casefold() in ("true", "1")


def _faixas_v4(itens: Sequence[str]) -> tuple[list[tuple[int, int]], set[str], list[str]]:
    """Os endereços de uma regra como o `ActiveStore` os devolve — `192.168.1.0/255.255.255.0`, `10.0.0.0/8`, um
    endereço só, `a-b` — em faixas IPv4 (primeiro, último); as palavras-chave (`Any`, `LocalSubnet`…); e o que não
    deu para entender. IPv6 fica de fora: a LAN do endpoint é IPv4."""
    faixas: list[tuple[int, int]] = []
    palavras: set[str] = set()
    ilegiveis: list[str] = []
    for item in itens:
        if ":" in item:
            continue
        try:
            if "-" in item:
                de, _, ate = item.partition("-")
                faixas.append((int(ipaddress.IPv4Address(de.strip())), int(ipaddress.IPv4Address(ate.strip()))))
            else:
                rede = ipaddress.IPv4Network(item, strict=False)
                faixas.append((int(rede.network_address), int(rede.broadcast_address)))
        except ValueError:
            if item.isalnum():
                palavras.add(item.casefold())
            else:
                ilegiveis.append(item)
    return faixas, palavras, ilegiveis


def _origem(itens: Sequence[str], lan: ipaddress.IPv4Network | None, *, ipv6: bool = False) -> tuple[str, str]:
    """A origem da regra diante da LAN do endpoint: `toda` (Any), `cobre` (contém a LAN), `parte` (só um pedaço
    dela), `fora` ou `incerta` — e o texto da origem, para a frase. `ipv6` = o endpoint é IPv6: a LAN dele não é
    conferida aqui, e só a palavra-chave decide."""
    texto = ", ".join(itens)[:80]
    if not itens:
        return "incerta", "a leitura não trouxe a origem"
    faixas, palavras, ilegiveis = _faixas_v4(itens)
    if "any" in palavras:
        return "toda", texto
    if ipv6:
        return ("cobre" if palavras & {"localsubnet", "localsubnet6"} else "incerta"), texto
    # `LocalSubnet` se expande, em tempo de execução, nas sub-redes em que a máquina está: a do endpoint entre elas.
    if palavras & {"localsubnet", "localsubnet4"}:
        return "cobre", texto
    if lan is None:
        return ("incerta" if faixas or ilegiveis else "fora"), texto
    primeiro, ultimo = int(lan.network_address), int(lan.broadcast_address)
    if any(de <= primeiro and ultimo <= ate for de, ate in faixas):
        return "cobre", texto
    if any(de <= ultimo and primeiro <= ate for de, ate in faixas):
        return "parte", texto
    return ("incerta" if ilegiveis else "fora"), texto


def _interface(itens: Sequence[str], interface: str | None) -> str:
    """`cobre`, `fora` ou `incerta`. O `InterfaceAlias` da regra aceita curinga e não tem caixa."""
    if not itens:
        return "incerta"
    if any(i.casefold() == "any" for i in itens):
        return "cobre"
    if interface is None:
        return "incerta"
    alvo = interface.casefold()
    return "cobre" if any(i.casefold() == alvo or fnmatch.fnmatchcase(alvo, i.casefold()) for i in itens) else "fora"


def _julgar(r: Mapping[str, object], alvo: _Alvo) -> tuple[_Veredito, str, str]:
    """Uma regra diante do pacote do notebook: o veredito, o porquê (quando não cobre) e o aviso (quando cobre demais).
    Vale para a que permite e para a que bloqueia; a diferença está na origem — para liberar, a regra precisa conter a
    LAN inteira; para bloquear, basta pegar um pedaço dela (não se sabe de que endereço o aparelho chega)."""
    if not _verdade(r.get("habilitada")) or str(r.get("direcao", "")).casefold() != "inbound":
        return "alheia", "", ""
    if str(r.get("protocolo") or "Any").strip().casefold() not in ("any", "udp", "17"):
        return "alheia", "", ""
    if not _cobre_a_porta(_textos(r.get("portas")), alvo.porta):
        return "alheia", "", ""
    bloqueia = str(r.get("acao", "")).casefold() == "block"
    # Pacote de app e serviço do Windows: o sing-box roda como processo comum, em modo usuário — não é nenhum dos dois.
    if str(r.get("pacote") or "").strip() or str(r.get("servico") or "Any").strip().casefold() != "any":
        return "alheia", "", ""
    programa = str(r.get("programa") or "Any").strip()
    if not _mesmo_programa(programa, alvo.binario):
        # NOSSA pelo nome que o comando dá ou pelo nome do executável (o sing-box de outra pasta). A de outro programa
        # que abre UDP em qualquer porta (TeamViewer, Teams: há várias no central) não diz respeito ao servidor.
        nossa = str(r.get("exibicao") or "") == NOME_DA_REGRA.format(porta=alvo.porta) \
            or PureWindowsPath(os.path.expandvars(programa)).name.casefold() \
            == PureWindowsPath(str(alvo.binario)).name.casefold()
        if not nossa:
            return "alheia", "", ""
        return "obsoleta", f"aponta para {programa}, que não é o binário atual do servidor ({alvo.binario})", ""
    if str(r.get("usuario_local") or "Any").strip().casefold() != "any" or str(r.get("dono") or "").strip():
        return "incerta", "vale só para um usuário local, que esta leitura não confere", ""
    if not bloqueia and str(r.get("autenticacao") or "NotRequired").strip().casefold() != "notrequired":
        return "nao_cobre", "exige conexão autenticada (IPsec), que o WireGuard do aparelho não é", ""
    perfis = str(r.get("perfis") or "Any")
    if alvo.perfil is not None or "any" in perfis.casefold():
        if not _cobre_o_perfil(perfis, alvo.ligados):
            return "nao_cobre", f"perfil {perfis}, e a rede do endpoint é {alvo.perfil}", ""
    else:
        # Sem saber a interface não se sabe o perfil: só vale com certeza a regra que cobre todos os ligados.
        cobertos = [p for p in alvo.ligados if p.casefold() in perfis.casefold()]
        if not cobertos:
            return "nao_cobre", f"perfil {perfis}, que não está ligado", ""
        if len(cobertos) < len(alvo.ligados):
            return "incerta", f"vale só no perfil {perfis}, e o perfil da rede do endpoint não foi identificado", ""
    interfaces = _textos(r.get("interfaces"))
    caso = _interface(interfaces, alvo.interface)
    if caso == "fora":
        return "nao_cobre", f"interface {', '.join(interfaces)[:80]}, e o endpoint está em {alvo.interface}", ""
    if caso == "incerta":
        return "incerta", (f"restrita à interface {', '.join(interfaces)[:80]}, e a interface do endpoint não foi "
                           "identificada" if interfaces else "a leitura não trouxe a interface"), ""
    tipo = str(r.get("tipo_interface") or "").strip()
    if tipo.casefold() != "any":
        return "incerta", (f"restrita ao tipo de interface {tipo}, que esta leitura não confere" if tipo
                           else "a leitura não trouxe o tipo de interface"), ""
    locais = _textos(r.get("local"))
    faixas, palavras, ilegiveis = _faixas_v4(locais)
    if "any" not in palavras:
        if not locais or ilegiveis or alvo.endereco is None:
            return "incerta", (f"restrita ao endereço local {', '.join(locais)[:80]}, que não pôde ser comparado ao "
                               "endpoint" if locais else "a leitura não trouxe o endereço local"), ""
        if not any(de <= int(alvo.endereco) <= ate for de, ate in faixas):
            return "nao_cobre", f"endereço local {', '.join(locais)[:80]}, e o endpoint é {alvo.endereco}", ""
    caso, origem = _origem(_textos(r.get("remoto")), alvo.lan, ipv6=alvo.ipv6)
    if caso == "incerta":
        return "incerta", (origem if origem.startswith("a leitura") else
                           f"origem {origem}, e a sub-rede da LAN do endpoint não foi identificada"), ""
    if bloqueia:
        return ("cobre" if caso in ("toda", "cobre", "parte") else "alheia"), "", ""
    if caso == "fora":
        return "nao_cobre", f"origem {origem} não contém a LAN do endpoint ({alvo.lan or 'IPv4'})", ""
    if caso == "parte":
        return "nao_cobre", f"origem {origem} cobre só parte da LAN do endpoint ({alvo.lan})", ""
    return "cobre", "", ("qualquer origem" if caso == "toda" else "")


def _rotulo(r: Mapping[str, object]) -> str:
    return str(r.get("exibicao") or r.get("nome"))


def _ipv4_da_rede(rede: Mapping[str, object]) -> list[tuple[ipaddress.IPv4Address, int | None]]:
    """Os IPv4 de uma interface com o prefixo que o sistema informou (`None` = não informou: leitura no formato antigo
    só trazia `ips`)."""
    saida: list[tuple[ipaddress.IPv4Address, int | None]] = []
    vistos: set[ipaddress.IPv4Address] = set()
    for item in _lista(rede.get("ipv4")):
        try:
            ip = ipaddress.IPv4Address(str(item.get("ip") or "").strip())
            prefixo = int(str(item.get("prefixo")))
        except ValueError:
            continue
        if 0 <= prefixo <= 32 and ip not in vistos:
            vistos.add(ip)
            saida.append((ip, prefixo))
    for texto in _textos(rede.get("ips")):
        try:
            ip = ipaddress.IPv4Address(texto)
        except ValueError:
            continue                                # IPv6 (com %zona) ou lixo
        if ip not in vistos:
            vistos.add(ip)
            saida.append((ip, None))
    return saida


def _achar(redes: Sequence[Mapping[str, object]], alvo: ipaddress.IPv4Address | ipaddress.IPv6Address,
           ) -> tuple[Mapping[str, object], int | None] | None:
    """A interface que tem o endereço do endpoint, e o prefixo dele (só do IPv4, quando o sistema informou)."""
    for rede in redes:
        if isinstance(alvo, ipaddress.IPv4Address):
            for ip, prefixo in _ipv4_da_rede(rede):
                if ip == alvo:
                    return rede, prefixo
            continue
        for texto in _textos(rede.get("ips")):
            try:
                if ipaddress.ip_address(texto.split("%")[0]) == alvo:
                    return rede, None
            except ValueError:
                continue
    return None


def _tem_ipv6_global(rede: Mapping[str, object]) -> bool:
    for texto in _textos(rede.get("ips")):
        try:
            ip = ipaddress.ip_address(texto.split("%")[0])
        except ValueError:
            continue
        if ip.version == 6 and ip in _IPV6_GLOBAL:
            return True
    return False


def avaliar(dados: Mapping[str, object], *, binario: Path, porta: int, endpoint: str | None) -> LeituraDoFirewall:
    """Do JSON da leitura ao estado. A ordem é a do Windows: perfil desligado não filtra; regra que bloqueia vence a
    que permite; sem regra, vale a ação padrão de entrada do perfil (`NotConfigured` = Block). Cada regra é julgada
    contra o que o pacote do notebook encontra DE FATO (`_julgar`): o perfil efetivo da interface do endpoint, essa
    interface, a porta UDP e a LAN dele. O que não dá para conferir não conta como liberado nem como bloqueado."""
    perfis = {str(p.get("nome")): p for p in _lista(dados.get("perfis"))}
    redes = _lista(dados.get("redes"))
    perfil: str | None = None
    interface: str | None = None
    endpoint_local: bool | None = None
    endereco: ipaddress.IPv4Address | None = None
    lan: ipaddress.IPv4Network | None = None
    ipv6_global = False
    endpoint_ipv6 = False
    aviso = ""
    faltou: list[str] = []
    if not endpoint:
        aviso = "; rede.servidor.endpoint_lan está vazio: sem ele não há interface nem sub-rede da LAN a conferir"
    else:
        try:
            alvo_ip: ipaddress.IPv4Address | ipaddress.IPv6Address | None = ipaddress.ip_address(endpoint)
        except ValueError:
            alvo_ip = None
            # Nome: não dá para achar a interface por ele (e a leitura não resolve nomes: o DNS daqui pode não ser o
            # do aparelho).
            aviso = (f"; atenção: {endpoint} é um nome, e a leitura não resolve nomes: a interface e a sub-rede da LAN "
                     "não foram identificadas (com o IP em rede.servidor.endpoint_lan a leitura confere as duas)")
        achado = _achar(redes, alvo_ip) if alvo_ip is not None else None
        if alvo_ip is not None and achado is None:
            # O endereço configurado não é de nenhuma interface desta máquina: IP errado (o DHCP mudou), ou um NAT no
            # meio. O firewall até pode estar certo, mas o notebook não chega AQUI por ele — e sem a interface não há
            # perfil, interface nem sub-rede a conferir. Diz o que a máquina tem.
            endpoint_local = False
            daqui = [f"{r.get('interface')} {ip}/{prefixo}" if prefixo is not None else f"{r.get('interface')} {ip}"
                     for r in redes for ip, prefixo in _ipv4_da_rede(r) if not (ip.is_loopback or ip.is_link_local)]
            aviso = (f"; atenção: {endpoint} não é endereço de nenhuma interface do central (confira "
                     "rede.servidor.endpoint_lan" + (f"; o central tem {', '.join(daqui)[:200]}" if daqui else "") + ")")
        elif alvo_ip is not None and achado is not None:
            rede_do_endpoint, prefixo = achado
            endpoint_local = True
            interface = str(rede_do_endpoint.get("interface") or "") or None
            perfil = _CATEGORIA_PARA_PERFIL.get(str(rede_do_endpoint.get("categoria") or "").casefold())
            ipv6_global = _tem_ipv6_global(rede_do_endpoint)
            if isinstance(alvo_ip, ipaddress.IPv6Address):
                endpoint_ipv6 = True
                aviso = (f"; atenção: {endpoint} é IPv6, e a regra proposta e a conferência da origem são da LAN IPv4: "
                         "a sub-rede fica por preencher")
            elif prefixo is None:
                endereco = alvo_ip
                aviso = (f"; atenção: o sistema não informou o prefixo de {alvo_ip} em {interface}: a sub-rede da regra "
                         "fica por preencher")
            else:
                endereco = alvo_ip
                lan = ipaddress.IPv4Network(f"{alvo_ip}/{prefixo}", strict=False)
            if perfil is None:
                aviso += (f"; o sistema não informou o perfil de rede de {interface}: só conta a regra que vale em "
                          "todos os perfis ligados")
    if interface is None:
        faltou.append("interface")
    if lan is None:
        faltou.append("sub-rede")
    sub_rede = str(lan) if lan is not None else None
    liberacao = comando_de_liberacao(porta, sub_rede=sub_rede, interface=interface)
    alvo_perfis = [perfil] if perfil else [n for n in _PERFIS if n in perfis] or list(_PERFIS)
    base = LeituraDoFirewall(estado="desconhecido", detalhe="", endpoint=endpoint, perfil=perfil, interface=interface,
                             endpoint_local=endpoint_local, sub_rede=sub_rede, faltou=faltou,
                             inspecao=comando_de_inspecao(porta), reversao=comando_de_reversao(porta))
    # Só onde o comando é proposto: o que o sistema não informou está lá como marcador, e a frase diz.
    marcador = ("; o comando proposto leva marcador no lugar de " + " e ".join(faltou)
                + " (o sistema não informou): preencha antes de rodar") if faltou else ""
    ligados = [n for n in alvo_perfis if _verdade((perfis.get(n) or {}).get("ligado", "True"))]
    if perfis and not ligados:
        base.estado, base.detalhe = "desligado", f"firewall desligado no perfil {'/'.join(alvo_perfis)}" + aviso
        return base
    alvo = _Alvo(porta=porta, binario=binario, perfil=perfil, ligados=tuple(ligados), interface=interface,
                 endereco=endereco, lan=lan, ipv6=endpoint_ipv6)
    bloqueiam: list[Mapping[str, object]] = []
    liberam: list[Mapping[str, object]] = []
    incertas: list[str] = []
    bloqueios_incertos: list[str] = []
    for r in _lista(dados.get("regras")):
        veredito, motivo, atencao = _julgar(r, alvo)
        if veredito == "alheia":
            continue
        bloqueia = str(r.get("acao", "")).casefold() == "block"
        permite = str(r.get("acao", "")).casefold() == "allow"
        if veredito == "cobre" and bloqueia:
            bloqueiam.append(r)
        elif veredito == "cobre" and permite:
            liberam.append(r)
            if atencao:
                base.avisos.append(
                    f"a regra '{_rotulo(r)}' aceita {atencao}: mais aberta que o necessário (basta a LAN "
                    f"{sub_rede or 'do endpoint'}) — o servidor escuta também em IPv6 ([::]) e "
                    + (f"a interface {interface} tem IPv6 global: a porta fica exposta fora da LAN" if ipv6_global
                       else "uma interface com IPv6 global deixaria a porta exposta fora da LAN"))
        elif veredito == "incerta":
            (bloqueios_incertos if bloqueia else incertas).append(f"'{_rotulo(r)}' ({motivo})")
        elif permite and veredito == "obsoleta":
            base.regras_obsoletas.append(_rotulo(r))
            base.regras_que_nao_cobrem.append(f"'{_rotulo(r)}' ({motivo})")
        elif permite:
            base.regras_que_nao_cobrem.append(f"'{_rotulo(r)}' ({motivo})")
    base.regras_que_bloqueiam = [_rotulo(r) for r in bloqueiam]
    base.regras_que_liberam = [_rotulo(r) for r in liberam]
    rotulo = perfil or "/".join(ligados)
    via = (f" da LAN {sub_rede}" if sub_rede else "") + (f" na interface {interface}" if interface else "")
    fora = ("; não cobrem: " + "; ".join(base.regras_que_nao_cobrem)[:400]) if base.regras_que_nao_cobrem else ""
    if bloqueiam:
        base.estado = "bloqueado"
        base.comandos = [comando_de_desbloqueio(str(r.get("nome"))) for r in bloqueiam]
        if not liberam:
            base.comandos.append(liberacao)
        gpo = [_rotulo(r) for r in bloqueiam if str(r.get("origem", "")).casefold() == "grouppolicy"]
        base.detalhe = (f"{len(bloqueiam)} regra(s) de entrada bloqueiam UDP {porta}{via} no perfil {rotulo} "
                        f"({', '.join(base.regras_que_bloqueiam)[:200]}); no Windows o bloqueio vence a permissão"
                        + (f"; {', '.join(gpo)} vem de política de grupo: desligue-a lá" if gpo else "") + aviso
                        + ("" if liberam else marcador))
        return base
    padroes = {n: str((perfis.get(n) or {}).get("entrada") or "NotConfigured") for n in ligados}
    entrada_aberta = bool(padroes) and all(v.casefold() == "allow" for v in padroes.values())
    if (liberam or entrada_aberta) and bloqueios_incertos:
        base.detalhe = (f"não deu para conferir {len(bloqueios_incertos)} regra(s) que bloqueiam UDP {porta}: "
                        + "; ".join(bloqueios_incertos)[:300] + aviso)
        return base
    if liberam:
        base.estado = "liberado"
        base.detalhe = (f"entrada UDP {porta}{via} liberada no perfil {rotulo} por "
                        f"{', '.join(base.regras_que_liberam)[:200]}"
                        + "".join(f"; atenção: {a}" for a in base.avisos) + fora + aviso)
        return base
    if entrada_aberta:
        base.estado = "liberado"
        base.detalhe = f"sem regra, mas a entrada padrão do perfil {rotulo} é Allow" + fora + aviso
        return base
    if incertas:
        # Existe regra que talvez libere e não deu para conferir (o mais comum: o endpoint_lan não é desta máquina,
        # então não há interface nem LAN contra as quais julgar). Não prova nem nega.
        base.detalhe = (f"não deu para conferir {len(incertas)} regra(s) que permitem UDP {porta}: "
                        + "; ".join(incertas)[:300] + fora + aviso)
        return base
    if base.regras_obsoletas:
        base.estado = "regra_obsoleta"
        base.comandos = [liberacao]
        base.detalhe = (f"a regra que permitiria UDP {porta} é de outro executável: "
                        + "; ".join(base.regras_que_nao_cobrem)[:400]
                        + " — o Windows descarta o pacote para o sing-box de hoje; o comando proposto não leva "
                          "-Program e troca a regra de mesmo nome" + aviso + marcador)
        return base
    base.estado = "sem_regra"
    base.comandos = [liberacao]
    base.detalhe = (f"firewall ligado no perfil {rotulo} com entrada padrão Block e nenhuma regra que permita UDP "
                    f"{porta}{via}" + fora + aviso + marcador)
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
        porta = cfg.porta_wireguard

        def nao_lido(detalhe: str) -> LeituraDoFirewall:
            # Sem leitura não há interface nem sub-rede: a inspeção e a reversão (que só dependem da porta) seguem.
            return LeituraDoFirewall(estado="desconhecido", detalhe=detalhe[:300], endpoint=endpoint,
                                     inspecao=comando_de_inspecao(porta), reversao=comando_de_reversao(porta))

        if os.name != "nt" and self.executar is executar_powershell:
            return nao_lido("fora do Windows: o firewall não é lido")
        try:
            dados = json.loads(self.executar(script_de_leitura(binario, porta)) or "{}")
        except Exception as exc:  # noqa: BLE001 - não ler não prova nem nega: `desconhecido` com o motivo
            return nao_lido(f"não foi possível ler o firewall: {exc}")
        if not isinstance(dados, Mapping):
            return nao_lido("a leitura do firewall não devolveu um objeto")
        return avaliar(dados, binario=binario, porta=porta, endpoint=endpoint)

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


__all__ = ["FECHADOS", "NOME_DA_REGRA", "FirewallDoCentral", "LeituraDoFirewall", "avaliar", "comando_de_desbloqueio",
           "comando_de_inspecao", "comando_de_liberacao", "comando_de_reversao", "executar_powershell",
           "script_de_leitura"]
