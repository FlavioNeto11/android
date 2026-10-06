"""Compatibilidade: o contrato central↔worker mora em `app.contracts.worker.protocol`. Não acrescente nada aqui.

Reexporta os MESMOS objetos, não cópias: o central despacha por `isinstance(msg, Heartbeat)`
(`api._tratar_mensagem_do_worker`), e uma classe redefinida aqui faria a mensagem não cair em ramo nenhum, sem erro
(`tests/test_contratos_do_worker.py` confere a identidade). O agente do worker já não importa este módulo; o central
e os testes antigos migram quando convier.
"""
from __future__ import annotations

from ..contracts.worker.protocol import (DOWNSTREAM, FEATURE_COMANDO_REMOTO, FEATURE_OBSERVACAO_LOCAL, FEATURE_RESERVA_DE_BOOT,
                                         LADO_MAX_ACEITO, MARCA_DE_FILA, MIDIA_CABECALHO_MAX, MIDIA_MAX_BYTES,
                                         PARTES_DE_MIDIA, PRAZO_MAX_OBSERVACAO_S, PROTOCOL_MIN, PROTOCOL_VERSION,
                                         RECUSA_CERCA_NAO_MAIOR, UPSTREAM, Ack, AppiumMode, Cancel,
                                         ContadorAgregado, Dispatch, EnvioDeMidia, Exec, ExecAck, ExecCancel,
                                         ExecResult, ExecResultAck, Heartbeat, Hello, Limits,
                                         ObserveImage, ObserveResult, Progress, Refused, Result, ResultAck, Welcome,
                                         WorkerDevice, WorkerResources, desempacotar_midia, empacotar_midia,
                                         parse_upstream)

__all__ = ["DOWNSTREAM", "FEATURE_COMANDO_REMOTO", "Exec", "ExecAck", "ExecCancel", "ExecResult",
           "ExecResultAck", "FEATURE_OBSERVACAO_LOCAL", "FEATURE_RESERVA_DE_BOOT", "LADO_MAX_ACEITO", "MARCA_DE_FILA",
           "MIDIA_CABECALHO_MAX", "MIDIA_MAX_BYTES", "PARTES_DE_MIDIA", "PRAZO_MAX_OBSERVACAO_S", "PROTOCOL_MIN",
           "PROTOCOL_VERSION", "RECUSA_CERCA_NAO_MAIOR", "UPSTREAM", "Ack", "AppiumMode", "Cancel",
           "ContadorAgregado", "Dispatch", "EnvioDeMidia", "Heartbeat", "Hello", "Limits", "ObserveImage",
           "ObserveResult", "Progress", "Refused", "Result", "ResultAck", "Welcome", "WorkerDevice",
           "WorkerResources", "desempacotar_midia", "empacotar_midia", "parse_upstream"]
