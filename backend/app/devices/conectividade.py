"""Internet DENTRO do aparelho — separada de o aparelho estar `online`.

Medido em 25/09/2026 no android-06: `online`, "pronto em 114 s", adb respondendo, tela ao vivo — e nenhum nome
resolvia. O DNS da rede móvel do emulador (`10.0.2.3`) consultava só o primeiro DNS IPv4 do host, que estava morto; os
outros aparelhos escapavam pelo Wi-Fi virtual, e o do android-06 estava desabilitado. O painel dizia "pronto", e o
Instagram tentava o login sem DNS ("An unexpected error occurred").

A sonda é UM `adb shell`, só leitura, com quatro perguntas independentes:

- rota default fora da `dummy0` (a `dummy0` existe sempre e não leva a lugar nenhum);
- DNS: o resolvedor do Android devolve endereço para o host de teste (`ping` imprime `PING host (ip)` mesmo
  quando o ICMP não passa — o que interessa aqui é a resolução);
- TCP 443 no host de teste, por nome;
- `VALIDATED` em alguma rede: é a sonda HTTPS do próprio Android (NetworkMonitor). O shell não tem `curl`, então
  HTTPS de verdade só se prova por ela.

Classificar é função pura; o gerenciador guarda o resultado e decide o aviso.
"""
from __future__ import annotations

from ..models import ConnectivityInfo

#: O host que o próprio Android usa para validar rede. Neutro, sem conta, e o que falha primeiro sem DNS.
HOST_DE_TESTE = "connectivitycheck.gstatic.com"

#: Prefixo do aviso no cartão. O gerenciador só limpa o aviso que for dele (mesma regra da pressão do convidado).
AVISO_PREFIXO = "sem internet"

#: Depois deste tempo o resultado é velho e uma ação que precisa de internet sonda de novo antes de decidir.
VALIDADE_S = 120.0

#: Intervalo da sonda periódica num aparelho `online` (a rede pode cair com o aparelho no ar).
INTERVALO_S = 300.0


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


def classificar(*, route: bool, dns: bool, tcp_443: bool, validated: bool, checked_at: str,
                host: str = HOST_DE_TESTE) -> ConnectivityInfo:
    base = dict(route=route, dns=dns, tcp_443=tcp_443, validated=validated, checked_at=checked_at)
    if not route:
        return ConnectivityInfo(state="unavailable", detail=f"{AVISO_PREFIXO}: nenhuma rota default no aparelho.",
                                **base)
    if not dns:
        return ConnectivityInfo(state="unavailable",
                                detail=f"{AVISO_PREFIXO}: DNS não responde ({host} não resolve; a rota existe).",
                                **base)
    if not tcp_443:
        return ConnectivityInfo(state="degraded",
                                detail=f"{AVISO_PREFIXO}: DNS responde, mas a conexão TCP 443 com {host} falhou.",
                                **base)
    if not validated:
        return ConnectivityInfo(state="degraded",
                                detail=f"{AVISO_PREFIXO} validada: DNS e TCP 443 respondem, mas o Android não validou "
                                       "nenhuma rede (a sonda HTTPS do sistema falhou).", **base)
    return ConnectivityInfo(state="healthy", detail="Internet ok: rota, DNS, TCP 443 e rede validada pelo Android.",
                            **base)


def desconhecida(motivo: str, checked_at: str | None = None) -> ConnectivityInfo:
    return ConnectivityInfo(state="unknown", checked_at=checked_at,
                            detail=f"Não foi possível verificar a internet agora ({motivo[:160]}).")
