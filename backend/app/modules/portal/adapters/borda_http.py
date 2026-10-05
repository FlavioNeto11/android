"""Os pedidos do vigia da borda (29.97): GET pelo nome público, como um visitante.

Sem credencial, sem cookie guardado entre pedidos, sem seguir redirecionamento e sem retry: um pedido por conferência.
O User-Agent é o de um navegador porque a borda só injeta o beacon nesse caso (medido em 05/10). Falha de rede, nome
que não resolve e tempo esgotado viram `SemResposta`: a volta não diz nada sobre a página.
"""
from __future__ import annotations

import time
from collections.abc import Callable

import httpx

from app.modules.portal.application.vigia import Resposta, SemResposta
from app.modules.portal.domain.borda import NAVEGADOR

#: O que o cliente desfaz sozinho. Com br ou zstd (a borda abriu e recomprimiu a página) o corpo não é lido: o
#: cabeçalho já mostra o defeito.
LEGIVEL = frozenset({"", "identity", "gzip"})
#: Teto do corpo lido: a raiz tem ~40 KB, o CSS ~30 KB.
CORPO_MAX = 2 * 1024 * 1024


class BuscarPelaBorda:
    def __init__(self, prazo_s: Callable[[], int], transporte: httpx.BaseTransport | None = None) -> None:
        self._prazo_s = prazo_s
        self._transporte = transporte        # os testes passam um `httpx.MockTransport`

    def __call__(self, url: str, *, aceita: str | None) -> Resposta:
        cabecalhos = {"User-Agent": NAVEGADOR, "Accept": "text/html,application/xhtml+xml,*/*"}
        if aceita:
            cabecalhos["Accept-Encoding"] = aceita
        inicio = time.monotonic()
        try:
            with httpx.Client(transport=self._transporte, timeout=self._prazo_s(), follow_redirects=False,
                              trust_env=False) as cliente, cliente.stream("GET", url, headers=cabecalhos) as r:
                recebidos = {k.lower(): v for k, v in r.headers.items()}
                corpo = b""
                if recebidos.get("content-encoding", "").strip().lower() in LEGIVEL:
                    for pedaco in r.iter_bytes():
                        corpo += pedaco
                        if len(corpo) > CORPO_MAX:
                            break
                        # O prazo do httpx vale por fase (conexão, cada pedaço): uma resposta que pinga devagar passaria
                        # dele. O pedido inteiro também tem prazo (V13).
                        if time.monotonic() - inicio > self._prazo_s():
                            raise SemResposta("tempo esgotado")
                return Resposta(r.status_code, recebidos, corpo)
        except httpx.TimeoutException as exc:
            raise SemResposta("tempo esgotado") from exc
        except httpx.HTTPError as exc:
            raise SemResposta("rede") from exc
