"""As sondas DENTRO do aparelho: os comandos e a leitura da saída, sem decidir estado nenhum.

Duas sondas moram aqui:
- a de **internet** (rota, validação, DNS, TCP 443), que classifica o cartão do aparelho (`devices/conectividade.py`);
- a de **saída** da rede por aparelho (ADR-056, item 25.5): IP de saída v4 e v6 visto de fora, o resolvedor DNS do
  túnel, UDP e a contabilidade por UID do `dumpsys netstats`. Quem a orquestra e grava a medição é
  `devices/rede_medicao.py`; aqui fica só o comando e a leitura, para o agente do worker levar junto (25.7).

Separada de `devices/conectividade.py` por causa do agente do worker. `Adb.connectivity_probe` precisa só do
comando e da leitura; a classificação devolve `models.ConnectivityInfo`, e `models.py` não vai para a máquina do
worker. Enquanto as duas coisas moravam juntas, o `adb.py` importava `conectividade` dentro da função para não
arrastar `models` — e o agente instalado dava `ModuleNotFoundError: app.models` no dia em que algum caminho dele
chamasse a sonda (relatório 04 §1.1). Aqui só há stdlib: o módulo vai no manifesto do agente
(`backend/worker-manifest.txt`) e o `adb.py` o importa no topo.

O que cada linha da sonda pergunta está em `devices/conectividade.py`.
"""
from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass

#: O host que o próprio Android usa para validar rede. Neutro, sem conta, e o que falha primeiro sem DNS.
HOST_DE_TESTE = "connectivitycheck.gstatic.com"


def comando_sonda(host: str = HOST_DE_TESTE) -> str:
    # DNS e TCP têm uma segunda tentativa. Medido no android-06 logo depois do reset (load 26 em 2 vCPU): a sonda
    # deu TCP 443 falho às 21:16:39 e o mesmo `nc` passou 3/3 em 1 s um minuto depois. Um tropeço sob carga não
    # pode virar "sem internet" no cartão.
    return (
        "echo R=$(ip route show table all 2>/dev/null | grep '^default' | grep -v dummy0 | grep -c .); "
        "echo V=$(dumpsys connectivity 2>/dev/null | grep -E 'NetworkAgentInfo[{]' | grep -c VALIDATED); "
        f"if ping -c1 -W3 {host} 2>&1 | grep -q '^PING' || {{ sleep 2; ping -c1 -W3 {host} 2>&1 | grep -q '^PING'; }}; "
        "then echo D=1; else echo D=0; fi; "
        f"if {{ echo | timeout 8 nc {host} 443; }} >/dev/null 2>&1 || {{ sleep 2; echo | timeout 8 nc {host} 443; }} "
        ">/dev/null 2>&1; then echo T=1; else echo T=0; fi"
    )


def ler_sonda(saida: str) -> dict[str, bool]:
    """`R=n V=n D=0|1 T=0|1` → booleanos. Levanta `ValueError` se faltar alguma resposta (não dá para saber)."""
    vals: dict[str, str] = {}
    for linha in (saida or "").splitlines():
        chave, _, valor = linha.strip().partition("=")
        if chave in ("R", "V", "D", "T") and valor.strip().isdigit():
            vals[chave] = valor.strip()
    if set(vals) != {"R", "V", "D", "T"}:
        raise ValueError(f"sonda de conectividade incompleta: {sorted(vals)}")
    return {"route": int(vals["R"]) > 0, "validated": int(vals["V"]) > 0, "dns": vals["D"] == "1",
            "tcp_443": vals["T"] == "1"}


# ============================================================================ sonda de SAÍDA (ADR-056, item 25.5)
# Tudo roda como o uid do shell (2000), nunca como root: o bloqueio fora da VPN não cobre o uid 0 (medido no 25.1:
# 868 B do uid 0 pela física com o lockdown ativo), e uma medição como root "provaria" o que não vale para os apps.
UID_DO_SHELL = 2000
#: O pacote do uid 2000: é como o próprio tráfego da sonda aparece no `per_app` da medição.
PACOTE_DO_SHELL = "com.android.shell"
#: `ConnectivityManager.TYPE_VPN` no `ident` do `dumpsys netstats`. Todo outro tipo (1 wifi, 0 móvel, 9 ethernet) é
#: a interface física.
TIPO_VPN = 17
#: Um app é `fora_da_rede` quando saiu pela física mais do que isto além do que passou pelo túnel. Na medição, um app
#: coberto teve o tipo 17 IGUAL ao tipo 1 (o Android reatribui ao app o tráfego do cliente VPN); a folga só absorve
#: um balde contado entre a leitura e o `--poll`. O vazamento medido do uid 0 foi de 816 B: acima da folga mínima.
FOLGA_MIN_BYTES = 512
FOLGA_FRACAO = 0.02

_HOST = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}"
                   r"[A-Za-z0-9])?)*$")
_PACOTE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+$")
_INICIO, _FIM = "==SONDA-INICIO==", "==SONDA-FIM=="


def host_valido(host: str) -> str:
    """O host vai para dentro de um comando de shell: só nome ou IP, nada que o shell interprete."""
    host = (host or "").strip()
    if not _HOST.match(host):
        try:
            ipaddress.ip_address(host)
        except ValueError:
            raise ValueError(f"host da sonda inválido: {host!r}") from None
    return host


def pacote_valido(pacote: str) -> str:
    if not _PACOTE.match(pacote or ""):
        raise ValueError(f"pacote inválido para a sonda: {pacote!r}")
    return pacote


def _octal(dados: bytes) -> str:
    # `printf` com escapes octais (POSIX): o datagrama sai byte a byte, sem `%` nem aspas no argumento.
    return "".join(f"\\{b:03o}" for b in dados)


#: Consulta DNS (id 0x1234, recursão pedida) do tipo A de example.com: a resposta tem mais de 12 bytes (o cabeçalho).
CONSULTA_DNS = _octal(bytes.fromhex("123401000001000000000000") + b"\x07example\x03com\x00\x00\x01\x00\x01")
#: Pedido NTP (modo cliente, versão 3): a resposta tem 48 bytes. É o UDP que NÃO é DNS — na cadeia com SOCKS5 do 25.1
#: o DNS seguia (vai pelo `wg-out`) e o NTP se perdia; medir só o DNS esconderia isso.
PEDIDO_NTP = _octal(b"\x1b" + b"\x00" * 47)


def comando_ip_de_saida(host: str, *, porta: int = 80, timeout_s: int = 8) -> str:
    """O IP de saída visto DE FORA, por HTTP/1.0 com o `nc` do Android (não há `curl`). O `sleep` segura o stdin
    aberto: sem ele o `nc` fecha antes da resposta (medido no 25.1). A família vem do host (api.ipify.org só tem A,
    api6.ipify.org só AAAA). O `X=` é o código de saída do `nc` (124 = o `timeout` venceu)."""
    return "echo U=$(id -u); " + _sonda_ip(host, porta=porta, timeout_s=timeout_s)


def _sonda_ip(host: str, *, porta: int, timeout_s: int) -> str:
    # O corpo da sonda de IP, sem o `U=`: o mesmo texto serve à sonda solta e à do teste de vazamento
    # (`comando_parar_e_sondar`), e os marcadores e o `X=` ficam iguais por construção — `ler_ip_de_saida` lê os dois.
    host = host_valido(host)
    pedido = f"GET / HTTP/1.0\\r\\nHost: {host}\\r\\nUser-Agent: sonda-de-rede\\r\\nConnection: close\\r\\n\\r\\n"
    return (f"echo '{_INICIO}'; "
            f"(printf '{pedido}'; sleep {max(1, timeout_s - 2)}) | timeout {timeout_s} nc {host} {int(porta)} 2>&1; "
            f"X=$?; echo; echo '{_FIM}'; echo X=$X")


def comando_dns_e_udp(*, host_de_resolucao: str, alvo_dns_udp: str, alvo_ntp: str, timeout_s: int = 5) -> str:
    """Uma ida só: o resolvedor da rede VPN (as `DnsAddresses` das `LinkProperties` do `tun0` no `dumpsys
    connectivity`), o DNS privado do Android, se um nome resolve, e dois datagramas UDP de ida e volta — DNS a um
    resolvedor explícito (o cliente o sequestra e resolve pelo túnel) e NTP (UDP que não é DNS)."""
    h, d, n = host_valido(host_de_resolucao), host_valido(alvo_dns_udp), host_valido(alvo_ntp)
    espera = max(1, timeout_s - 2)
    return (
        "echo U=$(id -u); "
        "D=$(dumpsys connectivity 2>/dev/null); "
        "echo \"DNS=$(echo \"$D\" | grep -o 'InterfaceName: tun0[^}]*' | grep -o 'DnsAddresses: [[][^]]*[]]' "
        "| head -1)\"; "
        "echo PDNS=$(settings get global private_dns_mode 2>/dev/null); "
        f"if ping -c1 -W3 {h} 2>&1 | grep -q '^PING'; then echo RES=1; else echo RES=0; fi; "
        f"echo UDNS=$( (printf '{CONSULTA_DNS}'; sleep {espera}) | timeout {timeout_s} nc -u {d} 53 2>/dev/null "
        "| wc -c); "
        f"echo UNTP=$( (printf '{PEDIDO_NTP}'; sleep {espera}) | timeout {timeout_s} nc -u {n} 123 2>/dev/null "
        "| wc -c); "
        "echo FIM=1"
    )


def comando_uids() -> str:
    """`pacote uid` de tudo que está instalado (Android 8+: `pm list packages -U`)."""
    return "echo U=$(id -u); pm list packages -U 2>/dev/null; echo FIM=1"


def comando_netstats() -> str:
    """A contabilidade por UID, só a seção que interessa. O `--poll` fecha os contadores antes (sem ele a leitura
    atrasa); o `sed`/`grep` cortam a saída (o `detail` inteiro passa de centenas de KB)."""
    return ("echo U=$(id -u); dumpsys netstats --poll >/dev/null 2>&1; "
            "dumpsys netstats detail 2>/dev/null | sed -n '/^UID stats:/,/^UID tag stats:/p' "
            "| grep -E '^UID |ident=|rb='; echo FIM=1")


def comando_abrir_app(pacote: str, espera_s: int) -> str:
    """Abre o app pela tela inicial dele, espera e volta ao início: tráfego do UID do app para a contabilidade. Roda
    quando uma tarefa está segurada pela porta da rede, ou sempre com `rede.sonda.abrir_apps` (a decisão está em
    `docs/dominios/parque.md`)."""
    pacote = pacote_valido(pacote)
    return (f"echo U=$(id -u); monkey -p {pacote} -c android.intent.category.LAUNCHER 1 >/dev/null 2>&1; "
            f"sleep {int(espera_s)}; input keyevent KEYCODE_HOME; echo FIM=1")


#: Quantas vezes o teste de vazamento para o cliente atrás da janela sem `tun0` (ver `comando_parar_e_sondar`).
TENTATIVAS_DE_VAZAMENTO = 5
#: Por quanto tempo, em passos de 0,1 s, cada tentativa espera o `tun0` sumir depois do `force-stop`.
_PASSOS_SEM_TUN = 30
_TUN0 = "/sys/class/net/tun0"


def comando_parar_e_sondar(pacote: str, host: str, *, tentativas: int = TENTATIVAS_DE_VAZAMENTO, porta: int = 80,
                           timeout_s: int = 8) -> str:
    """O teste de vazamento numa ida só ao shell (uid 2000): para o cliente VPN, espera o `tun0` sumir e, NO INSTANTE
    em que some, roda a mesma sonda de IPv4 (`_sonda_ip`, os mesmos marcadores e `X=`) ao mesmo host. Sem o processo
    do cliente, só o bloqueio do Android (lockdown) segura o tráfego fora da VPN; o shell pode dar o `force-stop`
    (medido no 25.1, 18:06:50).

    Por que numa ida só: com always-on e lockdown, o Android religa o cliente em menos de um segundo depois do
    `force-stop` (android-05, 29/09). Com parar, ler o `tun0` e sondar em três idas ao aparelho, o túnel já tinha
    voltado entre elas: em 3 testes reais, 1 deu `Permission denied` e 2 deram "o tun0 continuou no ar", o que deixava
    o aparelho em `parcial` e custava um reinício a mais. Aqui a espera é em passos de 0,1 s por até 3 s, e sem a
    janela a tentativa se repete (até `tentativas`).

    Saída: `U=`, a sonda (só se pegou a janela), `TENTATIVAS=<n usadas>` e `SEM_TUN=<n da tentativa que pegou a janela,
    ou 0>`. Com `SEM_TUN=0` não há sonda nenhuma (sem marcadores).

    Escrito para o `sh` do Android (mksh) com o toybox, de forma POSIX conservadora: `[ -e … ]` (o `test` embutido; o
    `/sys/class/net/tun0` é um link simbólico, e `-e` o segue), `$((…))`, `while`/`break`, e `sleep 0.1` (o `sleep` do
    toybox aceita fração de segundo). NÃO foi rodado num aparelho quando escrito: a prova do comando é a do ambiente
    real, registrada em `docs/dominios/parque.md`.

    Limite conhecido: entre ver o `tun0` ausente e o `connect` do `nc` há milissegundos (a resolução do nome e o
    `connect`); um cliente que volte exatamente aí leva a sonda pelo túnel, e o IP dela seria lido como VAZOU. É o lado
    conservador (uma falha a mais, nunca um "bloqueado" falso: o `Permission denied` só existe sem `tun0`). Comparar
    com o IP do túnel não resolve: com o servidor no central atrás do mesmo NAT, a saída da VPN e a da física podem ser
    o mesmo endereço, e a comparação esconderia um vazamento de verdade."""
    pacote = pacote_valido(pacote)
    sonda = _sonda_ip(host, porta=porta, timeout_s=timeout_s)
    # `T` guarda a última tentativa começada: é o que `TENTATIVAS=` diz, com ou sem a janela (o `n` passa do limite
    # quando o laço se esgota).
    return (f"echo U=$(id -u); n=1; S=0; T=0; "
            f"while [ $n -le {int(tentativas)} ]; do T=$n; "
            f"am force-stop {pacote} >/dev/null 2>&1; i=0; "
            f"while [ -e {_TUN0} ] && [ $i -lt {_PASSOS_SEM_TUN} ]; do sleep 0.1; i=$((i+1)); done; "
            f"if [ ! -e {_TUN0} ]; then S=$n; {sonda}; break; fi; "
            f"n=$((n+1)); done; "
            f"echo TENTATIVAS=$T; echo SEM_TUN=$S")


def ler_parar_e_sondar(saida: str) -> tuple[int, int]:
    """(tentativas usadas, tentativa que pegou a janela sem `tun0` ou 0). Levanta `ValueError` sem o `SEM_TUN=` (saída
    truncada: não dá para saber se a sonda rodou sem o túnel). A sonda em si se lê com `ler_ip_de_saida`."""
    texto = saida or ""
    sem_tun = re.search(r"^SEM_TUN=(\d+)\s*$", texto, re.M)
    usadas = re.search(r"^TENTATIVAS=(\d+)\s*$", texto, re.M)
    if sem_tun is None or usadas is None:
        raise ValueError("o teste de vazamento veio incompleto (sem o SEM_TUN)")
    return int(usadas.group(1)), int(sem_tun.group(1))


def uid_da_saida(saida: str) -> int | None:
    m = re.search(r"^U=(\d+)\s*$", saida or "", re.M)
    return int(m.group(1)) if m else None


@dataclass(frozen=True)
class IpDeSaida:
    ip: str | None
    motivo: str                     # "ok" ou por que não há IP (o erro do nc, o HTTP, a resposta que não é IP)


def ler_ip_de_saida(saida: str, familia: int) -> IpDeSaida:
    """A resposta do eco: HTTP 200 e o corpo é um IP PÚBLICO da família pedida. Qualquer outra coisa é "sem IP",
    com o motivo (o `nc: connect: Permission denied` do bloqueio, o `Timeout` do servidor fora, o 5xx do eco)."""
    texto = saida or ""
    if _INICIO not in texto or _FIM not in texto:
        raise ValueError("a sonda de IP de saída veio incompleta (sem os marcadores)")
    corpo = texto.split(_INICIO, 1)[1].split(_FIM, 1)[0].replace("\r\n", "\n").strip()
    codigo = re.search(r"^X=(\d+)", texto.split(_FIM, 1)[1], re.M)
    if not corpo:
        venceu = codigo is not None and codigo.group(1) == "124"
        return IpDeSaida(None, "sem resposta (tempo esgotado)" if venceu else "sem resposta")
    if not corpo.startswith("HTTP/"):
        return IpDeSaida(None, corpo.splitlines()[0][:120])
    status = corpo.splitlines()[0]
    if " 200" not in status:
        return IpDeSaida(None, f"o eco respondeu {status[:60]}")
    _cabecalho, _, resto = corpo.partition("\n\n")
    linhas = [ln.strip() for ln in resto.splitlines() if ln.strip()]
    if not linhas:
        return IpDeSaida(None, "HTTP 200 sem corpo")
    try:
        ip = ipaddress.ip_address(linhas[-1])
    except ValueError:
        return IpDeSaida(None, f"o corpo não é IP ({linhas[-1][:40]!r})")
    if ip.version != familia:
        return IpDeSaida(None, f"o eco devolveu IPv{ip.version}, não IPv{familia}")
    if not ip.is_global:
        return IpDeSaida(None, f"{ip} não é endereço público")
    return IpDeSaida(str(ip), "ok")


@dataclass(frozen=True)
class DnsEUdp:
    resolvedor: str | None          # o DNS da rede VPN (ex.: 172.19.0.2, o `hijack-dns` do sing-box)
    dns_privado: str | None
    resolve: bool
    udp_dns_bytes: int
    udp_ntp_bytes: int

    @property
    def udp_dns(self) -> bool:
        return self.udp_dns_bytes > 12             # o cabeçalho DNS tem 12 bytes; menos que isso não é resposta

    @property
    def udp_ntp(self) -> bool:
        return self.udp_ntp_bytes >= 48


def ler_dns_e_udp(saida: str) -> DnsEUdp:
    vals: dict[str, str] = {}
    for linha in (saida or "").splitlines():
        chave, sep, valor = linha.strip().partition("=")
        if sep and chave in ("DNS", "PDNS", "RES", "UDNS", "UNTP", "FIM"):
            vals[chave] = valor.strip()
    if "FIM" not in vals or not {"RES", "UDNS", "UNTP"} <= set(vals):
        raise ValueError("a sonda de DNS e UDP veio incompleta")
    validos = []
    for e in re.findall(r"/([0-9a-fA-F:.]+)", vals.get("DNS", "")):
        try:
            validos.append(str(ipaddress.ip_address(e.rstrip(".:"))))
        except ValueError:
            continue

    def num(chave: str) -> int:
        return int(vals[chave]) if vals.get(chave, "").isdigit() else 0

    pdns = vals.get("PDNS") or None
    return DnsEUdp(resolvedor=",".join(validos) or None, dns_privado=None if pdns in (None, "null") else pdns,
                   resolve=vals["RES"] == "1", udp_dns_bytes=num("UDNS"), udp_ntp_bytes=num("UNTP"))


def ler_uids(saida: str) -> dict[str, int]:
    """`package:com.x uid:10123` → {pacote: uid}."""
    return {m.group(1): int(m.group(2)) for m in re.finditer(r"^package:(\S+)\s+uid:(\d+)", saida or "", re.M)}


#: (tipo de rede, uid) → (bytes recebidos, bytes enviados), somados sobre os baldes e os `set` (DEFAULT, FOREGROUND).
Contabilidade = dict[tuple[int, int], tuple[int, int]]


def ler_netstats(saida: str) -> Contabilidade:
    """A seção "UID stats" do `dumpsys netstats detail`, como o `netstats_uid.py` do piloto a leu no 25.1: cada
    `ident=[{type=N…}] uid=U set=S tag=0x0` abre um bloco, e cada `rb=… rp=… tb=…` dele soma. Só `tag=0x0` (o total
    do uid; as etiquetas são subconjuntos e contariam duas vezes)."""
    if not re.search(r"^FIM=1\s*$", saida or "", re.M):
        raise ValueError("a contabilidade do netstats veio incompleta")
    out: dict[tuple[int, int], list[int]] = {}
    atual: tuple[int, int] | None = None
    em_uid = False
    for linha in (saida or "").splitlines():
        if linha.startswith("UID stats:"):
            em_uid = True
            continue
        if linha.startswith("UID tag stats:"):
            break
        if not em_uid:
            continue
        m = re.search(r"ident=\[\{type=(\d+).*?\}\] uid=(-?\d+) set=(\w+) tag=0x([0-9a-fA-F]+)", linha)
        if m:
            atual = (int(m.group(1)), int(m.group(2))) if int(m.group(4), 16) == 0 else None
            continue
        m = re.search(r"rb=(\d+) rp=\d+ tb=(\d+)", linha)
        if m and atual is not None:
            soma = out.setdefault(atual, [0, 0])
            soma[0] += int(m.group(1))
            soma[1] += int(m.group(2))
    return {k: (v[0], v[1]) for k, v in out.items()}


@dataclass(frozen=True)
class Cobertura:
    vpn: int                        # bytes (rx+tx) do uid na rede VPN, na janela
    fisica: int                     # bytes do uid na interface física, na janela

    @property
    def resultado(self) -> str:
        """`ok` / `fora_da_rede` / `nao_medido` (os valores de `rede.ResultadoPorApp`). Coberto = o que passou pela
        física também passou pelo túnel (tipo 17 = tipo 1 na medição); sem tráfego na janela, não dá para saber."""
        if self.vpn <= 0 and self.fisica <= 0:
            return "nao_medido"
        if self.vpn <= 0 or self.fisica - self.vpn > max(FOLGA_MIN_BYTES, FOLGA_FRACAO * self.fisica):
            return "fora_da_rede"
        return "ok"

    def descrever(self) -> str:
        return f"VPN {self.vpn} B × física {self.fisica} B"


def cobertura(antes: Contabilidade, depois: Contabilidade, uid: int) -> Cobertura:
    """O delta do uid na janela, separado em VPN e física. Contador que andou para trás (reinício do aparelho, o
    netstats reescrito) conta como zero naquele tipo: melhor `nao_medido` que um delta negativo virar "coberto"."""
    vpn = fisica = 0
    for (tipo, u), (rx, tx) in depois.items():
        if u != uid:
            continue
        rx0, tx0 = antes.get((tipo, u), (0, 0))
        delta = max(0, rx - rx0) + max(0, tx - tx0)
        if tipo == TIPO_VPN:
            vpn += delta
        else:
            fisica += delta
    return Cobertura(vpn, fisica)
