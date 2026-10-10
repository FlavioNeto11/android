"""`uncertain` com saída: a sonda do estado real fecha o que dá para provar, e a pessoa decide o resto.

`uncertain` é a peça central de "resultado incerto sem repetir efeito externo": ele existe para que ninguém
repita às cegas uma ação que pode ter acontecido. Só que ele **entrava e nunca saía** — nem a batida do worker
(que informa o estado de cada aparelho), nem o estado ADB do central, nem uma pessoa pelo painel conseguiam
fechar o comando. Havia um caso real parado em produção desde 21/09: um `start` que virou `uncertain` por
estourar o prazo de boot enquanto o aparelho subia — e subiu, e ficou `online`, e o comando continuou "incerto".

A saída tem duas portas, e as duas estão aqui:

* **Verificação pelo estado real** (esta sonda). Para os verbos de ciclo de vida o desfecho é DIRETAMENTE
  observável: `start` promete o aparelho no ar, `stop` promete o aparelho fora. Se o estado observado é o que o
  verbo prometia, o comando deu certo — e dizer isso é registrar um fato, não adivinhar.
* **Decisão humana** (`POST /api/commands/{id}/resolve`, em `api.py`). Para o que NÃO é observável — `reset`
  apagou os dados? `install_apk` instalou? — nenhuma sonda pode responder, e inventar uma resposta seria pior
  que o silêncio.

A sonda é deliberadamente assimétrica: ela só conclui SUCESSO. Ver o aparelho no estado prometido prova que o
efeito aconteceu; NÃO ver não prova o contrário (o aparelho pode ter subido e sido desligado depois, pela
pessoa ou pelo rodízio). Fechar como `failed` por ausência de evidência seria a mesma mentira, de sinal
trocado — por isso o que a sonda não fecha fica esperando alguém.
"""
from __future__ import annotations

import logging
from typing import Any

from ..db import Database, Row, loads
from ..models import CommandState, InstanceState
from ..devices.manager import ESTADO_ALVO
from .states import COMMAND_UNSETTLED
from .store import publicar_comando

log = logging.getLogger("poc.commands")

#: Verbos cujo desfecho o estado do aparelho comprova sozinho. `reset`/`create`/`install_apk`/`open_app` ficam
#: de fora de propósito: um aparelho `online` não diz se os dados foram apagados nem se o APK entrou, e é
#: exatamente para esses que existe a decisão humana.
VERIFICAVEL_POR_ESTADO = frozenset({"start", "wake", "restart", "stop", "hibernate"})

#: `device.network` (a rede por aparelho, ADR-056) é observável pela linha `device_network`: a revisão aplicada, o estado
#: e quando o tráfego foi verificado dizem se o passo que o comando pedia chegou ao fim. `desfazer` fica de fora de
#: propósito: o que ele promete é a linha SUMIR, e isso a sonda não distingue de uma linha que nunca existiu.
VERIFICAVEL_POR_REDE = frozenset({"device.network"})
_ACOES_DE_REDE_VERIFICAVEIS = frozenset({"aplicar", "conectar", "verificar"})

#: Como o inventário do worker (estado do PROCESSO na máquina dele) se traduz no estado do aparelho — e SÓ para
#: ausência. O recorte é o do próprio agente: "o worker sabe do processo; o central sabe do Android". `running`
#: significa "o emulador está no ar", não "o Android terminou de subir": um emulador travado em ANR aparece
#: `running`, e fechar um `start` como `succeeded` com base nisso seria justamente o sucesso falso que esta fase
#: existe para eliminar (`succeeded` de `start` quer dizer "o aparelho ligou", não "o processo existe").
#: Para "está fora" não há essa ambiguidade: processo inexistente é aparelho desligado.
_AUSENCIA_NO_WORKER = {"stopped": InstanceState.stopped, "absent": InstanceState.stopped}


def _estado_no_worker(workers: Any, worker_id: str | None, instance_id: str) -> tuple[InstanceState | None, str]:
    """O que a última batida do worker disse sobre AQUELE aparelho."""
    if not worker_id:
        return None, ""
    linha = workers.db.one("SELECT devices FROM workers WHERE id=?", (worker_id,))
    if linha is None:
        return None, ""
    for bruto in loads(linha["devices"], []) or []:
        if bruto.get("instance_id") != instance_id:
            continue
        estado = _AUSENCIA_NO_WORKER.get(str(bruto.get("state") or ""))
        return estado, (f"a batida do worker relata o aparelho '{bruto.get('state')}'" if estado else "")
    return None, ""


def _prova_de_rede(db: Database, row: Row) -> str | None:
    """A frase que prova um `device.network` incerto, ou `None`. Prova só SUCESSO, e só da MESMA revisão que o comando
    tratava: a rede já estar em `trafego_verificado` na revisão `rev` do comando, com a verificação feita DEPOIS de ele
    começar. Revisão que mudou desde então (outra configuração) não prova nada deste comando, e o que não se prova
    espera a pessoa."""
    params = loads(row["params"], {}) or {}
    rev = params.get("rev")
    if params.get("acao") not in _ACOES_DE_REDE_VERIFICAVEIS or not isinstance(rev, int) or isinstance(rev, bool):
        return None
    linha = db.one("SELECT applied_rev, state, verified_at FROM device_network WHERE instance_id=?",
                   (row["instance_id"],))
    if linha is None or linha["applied_rev"] != rev or linha["state"] != "trafego_verificado":
        return None
    inicio = row["started_at"] or row["created_at"]
    if not linha["verified_at"] or not inicio or str(linha["verified_at"]) < str(inicio):
        return None
    return (f"verificado pelo estado real: a rede do aparelho está na revisão {rev}, com o tráfego verificado em "
            f"{linha['verified_at']}, que é o que 'device.network' ({params.get('acao')}) prometia")


def _prova(devices: Any, workers: Any, row: Row) -> str | None:
    """A frase que prova o sucesso daquele comando, ou `None` quando não há prova."""
    alvo = ESTADO_ALVO.get(row["verb"])
    if alvo is None or row["verb"] not in VERIFICAVEL_POR_ESTADO:
        return None
    rt = devices.devices.get(row["instance_id"])
    if rt is not None and rt.state == alvo:
        return (f"verificado pelo estado real: o aparelho está '{alvo.value}', que é o que '{row['verb']}' "
                f"prometia")
    # O inventário do worker só fecha o que ele consegue provar: AUSÊNCIA de processo. Estar "no ar" para o
    # worker não é estar pronto para o central (ver `_AUSENCIA_NO_WORKER`), e quem fecha `start`/`wake` é o
    # `online` observado aqui — que o monitor reencontra em ≤30 s, ou a readoção imediata do próprio desfecho.
    if alvo is not InstanceState.stopped:
        return None
    estado, frase = _estado_no_worker(workers, row["worker_id"], row["instance_id"])
    if estado is not None and estado == alvo:
        return f"verificado pelo estado real: {frase}, que é o que '{row['verb']}' prometia"
    return None


def verificar_comando(s: Any, row: Row) -> Row:
    """Uma passada da sonda sobre UM comando. Devolve a linha (mudada ou não) — quem chama publica o resultado.

    Chamada pelo botão "verificar agora" do painel e pelo laço periódico. Idempotente: comando que não está
    `uncertain`, ou que a sonda não consegue provar, volta como está.
    """
    if CommandState(row["state"]) not in COMMAND_UNSETTLED:
        return row
    motivo = (_prova_de_rede(s.db, row) if row["verb"] in VERIFICAVEL_POR_REDE
              else _prova(s.devices, s.workers, row))
    if motivo is None:
        return row
    try:
        novo = s.commands.transition(row["id"], CommandState.succeeded, reason=motivo)
    except Exception:  # noqa: BLE001 - comando fechado por outra via no meio da sonda não é erro
        log.info("comando %s não pôde ser fechado pela sonda", row["id"])
        return s.commands.get(row["id"]) or row
    s.bus.emit("log", f"{novo['instance_id']}: o comando {novo['id']} ({novo['verb']}) estava incerto e foi "
                      f"fechado pela verificação do estado real.", instance_id=novo["instance_id"])
    return novo


def reconciliar_incertos(s: Any) -> list[Row]:
    """Passa a sonda por todos os comandos incertos e publica o que mudou. Devolve as linhas fechadas.

    Roda na batida do worker (o instante em que chega informação nova sobre os aparelhos dele) e num laço
    periódico (o aparelho local pode ser readotado pelo monitor a qualquer momento).
    """
    fechados: list[Row] = []
    for row in s.commands.unsettled():
        try:
            novo = verificar_comando(s, row)
        except Exception:  # noqa: BLE001 - a reconciliação nunca pode derrubar quem a chamou
            log.exception("reconciliação do comando %s", row["id"])
            continue
        if novo["state"] != row["state"]:
            publicar_comando(s.bus, novo)
            fechados.append(novo)
    return fechados
