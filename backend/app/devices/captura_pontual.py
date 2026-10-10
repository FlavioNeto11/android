"""A captura de tela de UM aparelho, pedida pelo dono por um canal (item 28.24, exceção (a) do dono, 04/10 15:17Z).

Não abre caminho novo até o aparelho: usa a MESMA prévia do painel (`rt.frame`), com as regras dela. Isso importa:
- nenhuma tela é escondida (ADR-089): a de senha, de código ou da loja sai como qualquer outra;
- quando ninguém olha o aparelho a prévia não captura (`preview_mode = on_demand`); então o pedido registra, por poucos
  segundos, um interesse de FOCO nele (o mesmo gesto de um painel que abre o aparelho) e espera o frame novo. Se a IA está
  operando o aparelho, a prévia não põe screencap na fila dele: vale o frame que a observação da IA acabou de publicar;
- não há segundo screencap por fora da fila do aparelho.

Devolve `(jpeg, None)` ou `(None, motivo)`; o motivo é português simples. Quem manda o arquivo (e só ao chat do dono) é a
conversa do canal (`ConversaDoCanal.enviar_conteudo`); aqui não existe canal.
"""
from __future__ import annotations

import asyncio
import math
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from app.models import InstanceState

if TYPE_CHECKING:
    from app.devices.manager import DeviceManager

#: Quanto esperar o frame novo depois de acordar a prévia (o ritmo do foco é de 1 s; sobra para um aparelho lento).
ESPERA_S = 8.0
#: O interesse que o pedido registra vale ao menos o piso do gerenciador (5 s) e SEMPRE mais que a espera: com TTL menor
#: que a espera, num aparelho lento a prévia deixava de ser pedida antes do prazo e os últimos segundos só esperavam.
#: Ele é solto assim que o frame chega, então a folga não prolonga nada.
TTL_DO_INTERESSE_S = 5
FOLGA_DO_INTERESSE_S = 2


def ttl_do_interesse(espera_s: float) -> int:
    return max(TTL_DO_INTERESSE_S, math.ceil(espera_s) + FOLGA_DO_INTERESSE_S)


async def capturar_para_o_dono(gerenciador: DeviceManager, instance_id: str, *, espera_s: float = ESPERA_S,
                               dormir: Callable[[float], Awaitable[None]] = asyncio.sleep) -> tuple[bytes | None, str | None]:
    try:
        rt = gerenciador.get(instance_id)
    except KeyError:
        return None, f"Não conheço o aparelho {instance_id}."
    if rt.state != InstanceState.online:
        return None, f"O {instance_id} não está online agora."
    idade_max = gerenciador.get_settings().frame_max_age_ms / 1000
    quadro = rt.frame
    if quadro is None or time.monotonic() - quadro.mono > idade_max:
        antes = quadro.mono if quadro is not None else 0.0
        # Uma chave por PEDIDO: o interesse de uma conexão é substituído, então com chave fixa dois /captura do mesmo
        # aparelho dividiam o mesmo interesse e o `finally` do primeiro soltava o do segundo no meio da espera.
        conexao = f"captura-canal-{instance_id}-{uuid.uuid4().hex[:8]}"
        gerenciador.registrar_interesse(conexao, [], instance_id, ttl_do_interesse(espera_s))
        try:
            limite = time.monotonic() + espera_s
            quadro = None
            while time.monotonic() < limite:
                if rt.frame is not None and rt.frame.mono > antes:
                    quadro = rt.frame
                    break
                await dormir(0.25)
        finally:
            gerenciador.soltar_interesse(conexao)
        if quadro is None:
            return None, f"Não consegui uma captura nova do {instance_id} agora. Tente de novo em instantes."
    if not quadro.jpeg_full:
        return None, f"O {instance_id} ainda não tem imagem de tela para enviar."
    return quadro.jpeg_full, None
