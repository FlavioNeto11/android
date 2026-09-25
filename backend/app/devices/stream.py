"""Estado da TELA ao vivo de um aparelho — separado do estado do aparelho.

O painel só tinha `frame.stale`, e "Desatualizado — último frame …" cobria cinco situações diferentes: aparelho
desligado, hibernado, worker fora do ar, captura falhando e aparelho vivo com captura atrasada. O operador não
tinha como saber qual. Aqui cada uma tem nome, e a ordem das perguntas é a regra:

1. o aparelho não está `online` → `device_hibernated` / `device_offline` (frame antigo não prova nada);
2. o worker que o hospeda caiu → `worker_offline` (o aparelho pode seguir vivo lá; não se sabe);
3. nenhum frame ainda → `no_frame` (ou `capture_error`, se a captura já falhou);
4. frame dentro do prazo → `live`;
5. frame velho E a captura está falhando → `capture_error`;
6. frame velho sem erro registrado → `stale` (o aparelho pode responder a comandos: NÃO é offline).

Função pura: o gerenciador passa os números, e o teste cobre a máquina de estados sem aparelho.
"""
from __future__ import annotations

from ..models import StreamInfo

#: Teto do recuo da captura depois de falhas seguidas, em segundos. Sem recuo, uma captura que falha em 25 s de
#: timeout ocupava o executor do aparelho em laço; com recuo ilimitado, a tela nunca voltaria sozinha.
BACKOFF_MAX_S = 30.0


def backoff_s(interval_s: float, falhas: int) -> float:
    """Espera antes da próxima captura: o intervalo normal, dobrando a cada falha seguida, até o teto."""
    if falhas <= 0:
        return interval_s
    return min(BACKOFF_MAX_S, interval_s * (2 ** min(falhas, 6)))


def stream_status(*, device_state: str, worker_bound: bool, worker_connected: bool, frame_ts: str | None,
                  frame_age_s: float | None, max_age_s: float, capture_failures: int,
                  last_error: str | None, last_error_at: str | None) -> StreamInfo:
    """Classifica a saúde da tela. Ver a ordem das perguntas no cabeçalho do módulo."""
    base = dict(last_frame_at=frame_ts, frame_age_s=None if frame_age_s is None else round(frame_age_s, 1),
                last_capture_error=last_error, last_capture_error_at=last_error_at,
                consecutive_capture_failures=capture_failures)
    if device_state == "hibernated":
        return StreamInfo(status="device_hibernated", detail="Aparelho hibernado: não há tela ao vivo.", **base)
    if device_state != "online":
        return StreamInfo(status="device_offline",
                          detail=f"Aparelho em '{device_state}': o último frame é histórico, não tela ao vivo.",
                          **base)
    if worker_bound and not worker_connected:
        return StreamInfo(status="worker_offline",
                          detail="O servidor que hospeda este aparelho está desconectado; o estado real lá é "
                                 "desconhecido.", **base)
    if frame_age_s is None:
        if capture_failures > 0:
            return StreamInfo(status="capture_error",
                              detail=f"A captura de tela falhou {capture_failures}x seguidas e nenhum frame chegou.",
                              **base)
        return StreamInfo(status="no_frame", detail="Aguardando o primeiro frame.", **base)
    if frame_age_s <= max_age_s:
        return StreamInfo(status="live", detail="Tela ao vivo.", **base)
    if capture_failures > 0:
        return StreamInfo(status="capture_error",
                          detail=f"A captura de tela está falhando ({capture_failures}x seguidas); o aparelho segue "
                                 "online.", **base)
    return StreamInfo(status="stale",
                      detail="Aparelho online, mas sem frame novo dentro do prazo (captura atrasada ou executor "
                             "ocupado).", **base)
