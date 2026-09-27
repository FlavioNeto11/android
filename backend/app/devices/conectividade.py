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

O comando e a leitura moram em `devices/sonda_rede.py` e são reexportados daqui: o `adb.py` os usa também na
máquina do worker, onde `models.py` (e, portanto, este módulo) não existe.
"""
from __future__ import annotations

from ..models import ConnectivityInfo
from .sonda_rede import HOST_DE_TESTE, comando_sonda, ler_sonda

__all__ = ["AVISO_PREFIXO", "HOST_DE_TESTE", "INTERVALO_S", "VALIDADE_S", "classificar", "comando_sonda",
           "desconhecida", "ler_sonda"]

#: Prefixo do aviso no cartão. O gerenciador só limpa o aviso que for dele (mesma regra da pressão do convidado).
AVISO_PREFIXO = "sem internet"

#: Depois deste tempo o resultado é velho e uma ação que precisa de internet sonda de novo antes de decidir.
VALIDADE_S = 120.0

#: Intervalo da sonda periódica num aparelho `online` (a rede pode cair com o aparelho no ar).
INTERVALO_S = 300.0


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
