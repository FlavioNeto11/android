"""Resultado tardio não reescreve o presente do aparelho (frente F4, item A4).

A pergunta: aplicar o `result` de um comando ANTIGO — um `start` que volta depois de um `stop` mais novo já
concluído no mesmo aparelho — muda o estado ATUAL do aparelho, ou só o registro do comando?

Veredito (conferido no código em 26/09): só o registro. `_resultado_tardio` transita o comando e publica
`command.updated`; quem traduz desfecho em estado do aparelho é `aplicar_desfecho_remoto`, chamado apenas no
caminho em voo (`_do_action_no_worker`), que é serializado por aparelho pelo `rt.op_lock` — um comando mais novo
daquele aparelho só sai depois de o anterior ter seu desfecho (ou virar `uncertain` pela queda do canal). Este
arquivo trava essa propriedade: o desfecho é a verdade histórica do comando; o aparelho é descrito pelo mais novo
e pela observação.
"""
from __future__ import annotations

from pathlib import Path

from app.api import _tratar_mensagem_do_worker
from app.models import CommandState, InstanceState
from app.workers.protocol import Result, parse_upstream

from .test_contrato_de_worker import AgenteDeContrato, _acao, _desfecho, _parque


async def test_start_antigo_que_volta_depois_de_um_stop_mais_novo_nao_religa_o_aparelho(tmp_path: Path) -> None:
    h, rt, cliente, worker_id = await _parque(tmp_path, "agente")
    s = h.state
    assert s is not None
    try:
        reg = s.workers
        async with cliente as c:
            await _desfecho(h, await _acao(c, rt, "stop", "tardio-0"))
            # O agente recebe o `start` e a rede cai antes do desfecho: o comando fica em voo, sem resposta.
            reg.live[worker_id].send.__self__.responder = False  # type: ignore[attr-defined]
            cid_start = await _acao(c, rt, "start", "tardio-1")
            await h.wait(lambda: cid_start in reg.live[worker_id].pendentes, what="o start entrar em voo")
            cerca_start = int(s.commands.get(cid_start)["fence"])

            # Reconexão: o canal novo substitui o antigo e o `start` em voo vira `uncertain` (o worker pode ter
            # agido). O `stop` seguinte, mais novo, sai pelo canal novo e conclui.
            agente = AgenteDeContrato(h, worker_id)
            agente.link = reg.attach(worker_id, agente.send)
            await _desfecho(h, cid_start, (CommandState.uncertain.value,))
            cid_stop = await _acao(c, rt, "stop", "tardio-2")
            await _desfecho(h, cid_stop)
        assert int(s.commands.get(cid_stop)["fence"]) > cerca_start
        assert rt.state == InstanceState.stopped and rt.desired_state == InstanceState.stopped.value

        # O desfecho do `start` antigo chega agora, do diário do agente, com a cerca certa.
        tardio = Result(command_id=cid_start, outcome="succeeded", fence=cerca_start)
        await _tratar_mensagem_do_worker(s, worker_id, agente.link, parse_upstream(tardio.model_dump()))

        linha = s.commands.get(cid_start)
        assert linha["state"] == CommandState.succeeded.value, "o registro do comando antigo recebe a verdade"
        assert rt.state == InstanceState.stopped, "o start antigo religou (ou marcou ligando) o aparelho"
        assert rt.desired_state == InstanceState.stopped.value, "o start antigo desfez a decisão do stop mais novo"
        assert s.db.scalar("SELECT desired_state FROM instances WHERE id=?", (rt.id,)) == InstanceState.stopped.value
        # Confirmado mesmo assim, para o agente tirá-lo da fila de reenvio.
        assert [p for p in agente.vistos if p.get("type") == "result_ack"][-1] == {
            "type": "result_ack", "command_id": cid_start}
    finally:
        await s.stop()
