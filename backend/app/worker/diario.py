"""Diário local do agente: o que ele fez e o central ainda não confirmou.

O defeito que isto corrige: quando o WebSocket caía, o desfecho que o agente tinha para entregar era jogado fora
(`_send` volta calado com `_ws is None`) e o central ficava com `uncertain` para sempre. "A rede piscou" acabava
significando "a ação falhou" — exatamente o contrário do que o projeto promete.

Duas coisas moram aqui, e as duas precisam sobreviver ao reinício do processo do agente:

1. **Resultados não confirmados.** Gravados ANTES da tentativa de envio; o `result_ack` os tira da fila de reenvio.
2. **Os últimos resultados JÁ confirmados** (`LIMITE_CONFIRMADOS`). Esquecer o desfecho no `result_ack` (o que
   havia) deixava a reentrega do MESMO comando depois do ack — a do JetStream por `ack_wait`, ou a de um central
   que despachou duas vezes — sem resposta guardada, e ela era executada de novo: dois `reset`. Com eles, a
   reentrega recebe o mesmo desfecho, nunca o verbo outra vez.
3. **A maior cerca já executada por aparelho.** É o que permite ao agente RECUSAR um despacho de cerca que não é
   maior — uma ordem antiga que voltou do limbo, ou a reentrega de uma que já saiu do item 2.

Arquivo único, JSON, escrito por `os.replace` sobre um temporário: ou o arquivo antigo inteiro, ou o novo
inteiro, nunca meio arquivo. Queda de energia no meio da escrita não deixa diário corrompido.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger("poc.worker")

ARQUIVO = "diario-do-agente.json"

#: Quantos desfechos confirmados ficam guardados. Pequeno de propósito: o arquivo inteiro é regravado a cada
#: mudança e o corpo pode levar a cauda do log do emulador. A reentrega que isto cobre chega em minutos (prazo
#: do ack do broker, reconexão), não depois de dezenas de comandos.
LIMITE_CONFIRMADOS = 64


class DiarioDoAgente:
    def __init__(self, work_dir: str | Path, nome: str = ARQUIVO):
        self.caminho = Path(work_dir) / nome
        self.resultados: dict[str, dict[str, Any]] = {}
        #: Confirmados pelo central, do mais antigo ao mais novo. Não são reenviados; só respondem à reentrega.
        self.confirmados: dict[str, dict[str, Any]] = {}
        self.cercas: dict[str, int] = {}
        self._ler()

    # ------------------------------------------------------------------ disco
    def _ler(self) -> None:
        try:
            bruto = json.loads(self.caminho.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except Exception:  # noqa: BLE001 - diário ilegível não impede o agente de trabalhar
            log.warning("diário do agente ilegível em %s; começando vazio", self.caminho)
            return
        if not isinstance(bruto, dict):
            return
        resultados = bruto.get("resultados")
        if isinstance(resultados, dict):
            self.resultados = {k: v for k, v in resultados.items() if isinstance(v, dict)}
        # Diário de agente anterior não tem a chave: começa vazio, e o agente antigo ignora a chave nova.
        confirmados = bruto.get("confirmados")
        if isinstance(confirmados, dict):
            self.confirmados = {k: v for k, v in confirmados.items() if isinstance(v, dict)}
        cercas = bruto.get("cercas")
        if isinstance(cercas, dict):
            self.cercas = {k: int(v) for k, v in cercas.items() if isinstance(v, (int, float))}

    def _gravar(self) -> None:
        try:
            self.caminho.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.caminho.with_suffix(".tmp")
            tmp.write_text(json.dumps({"resultados": self.resultados, "confirmados": self.confirmados,
                                       "cercas": self.cercas}, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.caminho)
        except Exception:  # noqa: BLE001 - não conseguir gravar não pode derrubar o comando em si
            log.exception("gravando o diário do agente em %s", self.caminho)

    # ------------------------------------------------------------------ resultados
    def guardar(self, command_id: str, payload: dict[str, Any]) -> None:
        self.resultados[command_id] = payload
        self._gravar()

    def confirmar(self, command_id: str) -> bool:
        """O central confirmou o recebimento: o resultado sai da fila de reenvio e fica entre os confirmados.
        `False` quando não havia nada pendente com esse id."""
        corpo = self.resultados.pop(command_id, None)
        if corpo is None:
            return False
        self.confirmados.pop(command_id, None)
        self.confirmados[command_id] = corpo
        while len(self.confirmados) > LIMITE_CONFIRMADOS:
            self.confirmados.pop(next(iter(self.confirmados)))
        self._gravar()
        return True

    def desfecho(self, command_id: str) -> dict[str, Any] | None:
        """O desfecho que este agente JÁ produziu para o comando — pendente ou confirmado —, ou `None`."""
        return self.resultados.get(command_id) or self.confirmados.get(command_id)

    def pendentes(self) -> list[dict[str, Any]]:
        return list(self.resultados.values())

    # ------------------------------------------------------------------ cercas
    def cerca(self, instance_id: str) -> int:
        return self.cercas.get(instance_id, 0)

    def registrar_cerca(self, instance_id: str, fence: int) -> None:
        if fence <= self.cercas.get(instance_id, 0):
            return
        self.cercas[instance_id] = int(fence)
        self._gravar()
