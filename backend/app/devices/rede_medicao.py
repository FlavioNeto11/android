"""A sonda de saída da rede por aparelho (ADR-056, item 25.5): mede DE DENTRO do aparelho, como o uid 2000, e devolve
a medição que `rede.registrar_medicao` grava. Quem decide QUANDO medir é a convergência (`rede_convergencia`, passo
`verificar`); os comandos e as leituras moram em `sonda_rede.py` (só stdlib: vão também no agente do worker).

O que se mede (`medir`), na ordem (ajuste do plano registrado no handoff: o `nc` do shell no lugar do app de QA
estendido):

1. os UIDs dos apps exigidos (`pm list packages -U`) e a contabilidade por UID (`dumpsys netstats detail`) ANTES;
2. com `abrir` (tarefa segurada pela porta da rede, ou `rede.sonda.abrir_apps`): abrir o app exigido que não teve
   tráfego na janela;
3. o IP de saída v4 e v6, por eco HTTP/1.0 (o primeiro host da lista que devolver IP público);
4. o resolvedor DNS da rede VPN, a resolução de um nome, UDP de DNS e UDP que não é DNS (NTP);
5. a contabilidade DEPOIS: por UID, o delta na VPN (tipo 17) × o delta na física. A janela dos apps vai de quando o
   túnel conectou nesta revisão até agora — acumulada: um vazamento visto uma vez não some na medição seguinte, e um
   app que já usou a rede pelo túnel não vira `nao_medido` por estar parado desde a última sonda (o que derrubaria um
   `trafego_verificado` a cada "Verificar"). A do shell é só esta passada (é o tráfego da própria sonda).

O teste de VAZAMENTO (`sondar_vazamento`, só com a política `exigida_com_bloqueio`) é separado, porque desliga a VPN
de verdade: com o túnel saindo, o cliente VPN é PARADO (`am force-stop`, sem `tun0`) e a sonda de IPv4 roda de novo —
parar, esperar o `tun0` sumir e sondar numa ida SÓ ao aparelho (`sonda_rede.comando_parar_e_sondar`), porque com
always-on e lockdown o Android religa o cliente em menos de um segundo (android-05, 29/09) e, em idas separadas, 2 de 3
testes reais acharam o túnel de volta. Só o `Permission denied` do Android é bloqueio provado; um IP é vazamento;
qualquer outra coisa (`Timeout`, nome que não resolve, a janela sem `tun0` nunca vista) não prova nada. Parar o
servidor não serve: com o túnel no ar e o servidor fora, a sonda dá `Timeout` com o bloqueio ligado ou desligado (25.1,
18:06:50) — era o que o primeiro desenho media, e virava "bloqueado" presumido. Se o túnel não voltar sozinho depois
(no 25.1, 18:07, não voltou), quem chama religa pelo boot.

Nada aqui muda estado: a sonda só devolve o que viu. Uma leitura que não veio (adb em root, saída truncada) levanta
`RedeAplicacaoError` e nada é gravado — incerteza não vira medição.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..util import now_iso
from .rede import NetworkMeasurementInput
from .rede_aplicacao import AparelhoDaRede, RedeAplicacaoError
from .sonda_rede import (PACOTE_DO_SHELL, TENTATIVAS_DE_VAZAMENTO, UID_DO_SHELL, Contabilidade, cobertura,
                         comando_abrir_app, comando_dns_e_udp, comando_ip_de_saida, comando_netstats,
                         comando_parar_e_sondar, comando_uids, ler_dns_e_udp, ler_ip_de_saida, ler_netstats,
                         ler_parar_e_sondar, ler_uids, uid_da_saida)

if TYPE_CHECKING:
    from ..config import RedeSondaCfg

log = logging.getLogger(__name__)

#: O `method` gravado em `network_measurements` (≤ 60): quem mediu e como.
METODO = "sonda nc http/1.0 + netstats por uid (uid 2000)"
#: Tempo de cada sonda de IP (o `nc` com o stdin aberto pelo `sleep`).
_TIMEOUT_IP_S = 8
#: O teste de vazamento: tentativas de pegar a janela sem `tun0`, e a espera de cada uma (30 passos de 0,1 s no
#: comando). O prazo da ida ao aparelho soma, por tentativa, a espera e uma folga para o `am force-stop`.
_TENTATIVAS = TENTATIVAS_DE_VAZAMENTO
_ESPERA_TUN_S = 3
#: O erro do `connect` que o bloqueio do Android devolve a um app fora da VPN (lockdown sem `tun0`, 25.1 18:03:57 e
#: 18:06:50). É a ÚNICA assinatura que prova o bloqueio.
_RECUSA_DO_BLOQUEIO = "Permission denied"


@dataclass(frozen=True)
class Vazamento:
    """O resultado do teste de vazamento de UMA revisão. `bloqueado=None` = não medido (com o porquê em `texto`)."""

    bloqueado: bool | None
    texto: str
    medido_em: str


@dataclass(frozen=True)
class TesteDeVazamento:
    vazamento: Vazamento
    #: O cliente VPN foi parado (ou pode ter sido: o `force-stop` saiu e algo falhou depois). Quem chama confere o
    #: túnel e, sem ele, religa o cliente pelo boot — o aparelho não pode ficar sem VPN (e, com bloqueio, sem rede).
    cliente_parado: bool


@dataclass(frozen=True)
class ResultadoDaSonda:
    medicao: NetworkMeasurementInput
    #: A linha de base que a janela dos apps usou (a recebida ou, sem ela, a do começo desta passada): quem chama a
    #: guarda quando ainda não tinha uma para a revisão (backend reiniciado depois da conexão).
    base: Contabilidade
    #: Apps exigidos que a sonda abriu nesta passada.
    abertos: tuple[str, ...] = ()


async def _shell_2000(ap: AparelhoDaRede, comando: str, *, timeout: float) -> str:
    """Toda leitura da sonda confere o uid NA MESMA ida: o bloqueio não cobre o root, e um `adb root` no meio de
    uma medição a invalidaria sem aviso."""
    saida = await ap.shell(comando, timeout=timeout)
    uid = uid_da_saida(saida)
    if uid != UID_DO_SHELL:
        raise RedeAplicacaoError(f"a sonda de saída rodou como uid {uid}, não como o shell (2000): o bloqueio não "
                                 "cobre o root. Rode `adb unroot` neste aparelho")
    return saida


async def _ip(ap: AparelhoDaRede, hosts: list[str], familia: int) -> tuple[str | None, str]:
    """(ip, de onde) do primeiro host que devolver IP público; sem IP, (None, o motivo de cada host)."""
    motivos: list[str] = []
    for host in hosts:
        saida = await _shell_2000(ap, comando_ip_de_saida(host, timeout_s=_TIMEOUT_IP_S), timeout=_TIMEOUT_IP_S + 20)
        try:
            r = ler_ip_de_saida(saida, familia)
        except ValueError as exc:
            raise RedeAplicacaoError(str(exc)) from None
        if r.ip is not None:
            return r.ip, host
        motivos.append(f"{host}: {r.motivo}")
    return None, "; ".join(motivos)


async def contabilidade(ap: AparelhoDaRede) -> Contabilidade:
    """A contabilidade por UID agora, lida como uid 2000. A convergência a guarda quando o túnel conecta: é o começo
    da janela dos apps na primeira medição (o tráfego desde que o túnel subiu conta)."""
    try:
        return ler_netstats(await _shell_2000(ap, comando_netstats(), timeout=90))
    except ValueError as exc:
        raise RedeAplicacaoError(str(exc)) from None


async def sondar_vazamento(ap: AparelhoDaRede, cfg: RedeSondaCfg, pacote: str) -> TesteDeVazamento:
    """O teste de vazamento com a VPN DERRUBADA de verdade: o cliente VPN parado, sem `tun0`, e a sonda de IPv4 como
    uid 2000. Antes de parar, a sonda precisa sair pelo túnel (uma sonda que não funciona não prova bloqueio, e aí o
    cliente nem é tocado). Depois do `force-stop` nada levanta: qualquer falha vira `bloqueado=None` com o porquê, e
    `cliente_parado` fica verdadeiro — quem chama tem de religar o cliente de qualquer jeito."""
    # A sonda com o túnel no ar escolhe o host (o primeiro da lista que responder); o de depois usa o MESMO host, para a
    # diferença ser só o cliente parado.
    antes, host = await _ip(ap, cfg.hosts_ipv4, 4)
    if antes is None:
        return TesteDeVazamento(Vazamento(None, f"vazamento não medido: a sonda de IPv4 não saiu nem com o túnel no ar "
                                                f"({host[:100]}), e uma sonda que não funciona não prova bloqueio",
                                          now_iso()), cliente_parado=False)
    # Parar, esperar o `tun0` sumir e sondar numa ida SÓ ao aparelho (`comando_parar_e_sondar`): com always-on e
    # lockdown o Android religa o cliente em menos de um segundo, e em idas separadas o túnel já tinha voltado
    # (android-05, 29/09: 2 de 3 testes reais sem medição). Tudo, da chamada à leitura, fica no `try`: o `force-stop`
    # pode ter rodado mesmo com a saída perdida.
    try:
        saida = await _shell_2000(ap, comando_parar_e_sondar(pacote, host, timeout_s=_TIMEOUT_IP_S),
                                  timeout=_TENTATIVAS * (_ESPERA_TUN_S + 5) + _TIMEOUT_IP_S + 20)
        _usadas, sem_tun = ler_parar_e_sondar(saida)
        if not sem_tun:
            # A espera nunca viu o aparelho sem `tun0`: o cliente não parou, ou o always-on o religou antes do passo
            # de 0,1 s. Daqui não dá para separar os dois, e a sonda sairia pelo túnel — não diz nada do bloqueio.
            return TesteDeVazamento(Vazamento(None, f"vazamento não medido: o tun0 continuou no ar depois de parar o "
                                                    f"cliente — ou o always-on religou o cliente antes da sonda — em "
                                                    f"todas as {_TENTATIVAS} tentativas", now_iso()),
                                    cliente_parado=True)
        r = ler_ip_de_saida(saida, 4)
        ip, motivo = r.ip, f"{host}: {r.motivo}"
    except Exception as exc:  # noqa: BLE001 - o cliente está parado: o desfecho tem de voltar a quem o religa
        return TesteDeVazamento(Vazamento(None, f"vazamento não medido: a sonda falhou com o cliente parado "
                                                f"({str(exc)[:140]})", now_iso()), cliente_parado=True)
    if ip is not None:
        vaz = Vazamento(False, f"VAZOU: com o cliente VPN parado (sem tun0), a sonda de IPv4 como uid 2000 saiu "
                               f"direto por {ip}", now_iso())
    elif _RECUSA_DO_BLOQUEIO in motivo:
        vaz = Vazamento(True, "com o cliente VPN parado (sem tun0), o Android recusou a sonda de IPv4 como uid 2000 "
                              f"({_RECUSA_DO_BLOQUEIO}): o bloqueio fora da VPN vale", now_iso())
    else:
        vaz = Vazamento(None, f"vazamento não medido: com o cliente parado, a sonda não saiu mas o bloqueio não a "
                              f"recusou ({motivo[:100]}); só o {_RECUSA_DO_BLOQUEIO} prova o bloqueio", now_iso())
    return TesteDeVazamento(vaz, cliente_parado=True)


async def medir(ap: AparelhoDaRede, cfg: RedeSondaCfg, *, exigidos: list[str], linha_de_base: Contabilidade | None,
                bloqueio: bool, vazamento: Vazamento | None, abrir: bool) -> ResultadoDaSonda:
    """Uma medição completa, com o túnel no ar. `linha_de_base` = a contabilidade de quando o túnel conectou, DESTA
    revisão (o começo da janela dos apps); sem ela, a janela começa nesta passada. `vazamento` = o teste desta revisão
    (`sondar_vazamento`, feito antes e guardado por quem chama), que só vale com `bloqueio`. `abrir` = abrir o app
    exigido sem tráfego na janela."""
    uids = ler_uids(await _shell_2000(ap, comando_uids(), timeout=45))
    antes = await contabilidade(ap)
    base = linha_de_base if linha_de_base is not None else antes
    abertos: list[str] = []
    if abrir:
        for pkg in exigidos:
            uid = uids.get(pkg)
            if uid is not None and cobertura(base, antes, uid).resultado == "nao_medido":
                await _shell_2000(ap, comando_abrir_app(pkg, cfg.espera_app_s), timeout=cfg.espera_app_s + 45)
                abertos.append(pkg)

    v4, de_onde_v4 = await _ip(ap, cfg.hosts_ipv4, 4)
    v6, de_onde_v6 = await _ip(ap, cfg.hosts_ipv6, 6)
    try:
        dns = ler_dns_e_udp(await _shell_2000(ap, comando_dns_e_udp(
            host_de_resolucao=cfg.hosts_ipv4[0], alvo_dns_udp=cfg.udp_dns, alvo_ntp=cfg.udp_ntp), timeout=60))
    except ValueError as exc:
        raise RedeAplicacaoError(str(exc)) from None
    depois = await contabilidade(ap)

    per_app: dict[str, str] = {}
    notas: list[str] = []
    for pkg in exigidos:
        uid = uids.get(pkg)
        if uid is None:
            per_app[pkg] = "nao_medido"
            notas.append(f"{pkg} não instalado")
            continue
        c = cobertura(base, depois, uid)
        per_app[pkg] = c.resultado
        notas.append(f"{pkg}={c.resultado} ({c.descrever()}" + (", aberto pela sonda)" if pkg in abertos else ")"))
    shell = cobertura(antes, depois, UID_DO_SHELL)
    # O shell é a própria sonda: sem IP nenhum, ela "não conectou" (`falhou`), mesmo que a contabilidade diga algo.
    per_app[PACOTE_DO_SHELL] = shell.resultado if (v4 or v6) else "falhou"
    notas.append(f"shell={per_app[PACOTE_DO_SHELL]} ({shell.descrever()})")

    if bloqueio and vazamento is None:
        vazamento = Vazamento(None, "vazamento não medido nesta revisão", now_iso())
    partes = [
        f"IPv4 {v4} ({de_onde_v4})" if v4 else f"IPv4 sem saída ({de_onde_v4[:90]})",
        f"IPv6 {v6} ({de_onde_v6})" if v6 else f"IPv6 sem saída ({de_onde_v6[:70]})",
        f"DNS da VPN {dns.resolvedor or 'não lido'}" + (f", privado {dns.dns_privado}" if dns.dns_privado else "")
        + (", resolve" if dns.resolve else ", NÃO resolve"),
        f"UDP DNS {dns.udp_dns_bytes} B, NTP {dns.udp_ntp_bytes} B",
        "apps: " + "; ".join(notas),
    ]
    if bloqueio and vazamento is not None:
        partes.append(vazamento.texto[:160])
    medicao = NetworkMeasurementInput(
        method=METODO, egress_ipv4=v4, egress_ipv6=v6, dns_resolver=(dns.resolvedor or None),
        udp_ok=dns.udp_dns and dns.udp_ntp, per_app=per_app,  # type: ignore[arg-type]
        leak_blocked=vazamento.bloqueado if bloqueio and vazamento is not None else None,
        detail=" | ".join(partes)[:500])
    return ResultadoDaSonda(medicao=medicao, base=base, abertos=tuple(abertos))


__all__ = ["METODO", "ResultadoDaSonda", "TesteDeVazamento", "Vazamento", "contabilidade", "medir",
           "sondar_vazamento"]
