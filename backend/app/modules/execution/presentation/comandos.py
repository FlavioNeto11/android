"""Os comandos do aparelho (`/api/commands*`): ler, listar, verificar, cancelar, resolver e refinar, com a triagem de credencial da nota
(`_decisao_sobre_comando`, 409 `note_looks_secret` antes de qualquer escrita). Saíram de `api.py` no 15.15 F4 (corte 10, F4j) sem mudar
caminho, método, corpo nem resposta.

Montado em `main.py` no MESMO lugar em que `api.router` entra, depois do das rotas de Instagram. Este módulo não importa `app.api` (ciclo): o estado é
`request.app.state.poc` (`AppState` só em `TYPE_CHECKING`).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from fastapi import APIRouter, HTTPException, Query, Request

from app.commands.despacho import _entregar_cancelamento, _publish_command
from app.commands.reconciler import VERIFICAVEL_POR_ESTADO, verificar_comando
from app.commands.states import COMMAND_OPEN, COMMAND_UNSETTLED, InvalidCommandTransition
from app.commands.store import command_dto
from app.db import Row, loads
from app.models import CommandCancelBody, CommandResolveBody, CommandState
from app.modules.execution.domain.command_refinement import CommandRefinement
from app.modules.execution.presentation.comum import autor_do_sinal, run_error
from app.modules.fleet.presentation.comum import quem
from app.modules.learning.infrastructure.segredo import TriagemDeCredencial
from app.shared.costuras import PAINEL, ResolucaoDeComando, avisar
from app.taskqueue.assistente import ComandoAssistido, CommandRefineBody
from app.taskqueue.service import RunError
from app.util import now_iso

if TYPE_CHECKING:
    from app.state import AppState

router = APIRouter(prefix="/api")


def _st(request: Request) -> AppState:
    state: AppState = request.app.state.poc
    return state


def _err(status: int, code: str, message: str, **extra: object) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message, **extra})


#: A triagem de credencial do voto do D2 (`registrar_sinal(recusar_nota=True)`), para a nota livre que uma pessoa escreve numa rota daqui e que
#: entraria crua no banco e no evento (segredo nunca em evento). Sem estado: `api.py` tem a sua para a nota das exceções de política.
_TRIAGEM_DE_NOTA = TriagemDeCredencial()


#: O contexto que o painel punha NA NOTA até 29/09 ("decidido no painel a partir de <aparelho>: <texto>"). Com a
#: triagem, um id de aparelho com maiúscula, dígito e símbolo (um AVD como `Pixel_7a-Lab.02`) recusava toda decisão
#: pelo painel por causa do prefixo, não do texto da pessoa. Hoje o painel manda `origin=panel` e o backend compõe o
#: contexto; a aba aberta antes do deploy ainda manda o prefixo, que só é reconhecido com o id do PRÓPRIO comando.
_PREFIXO_ANTIGO_DO_PAINEL = {"resolve": "decidido no painel a partir de ", "cancel": "cancelado no painel a partir de "}


def _decisao_sobre_comando(row: Row, nota: str | None, requested_by: str | None, origem: str | None, autor: str,
                           gesto: Literal["resolve", "cancel"], o_que: str) -> tuple[str | None, str]:
    """O texto da pessoa (sem o contexto) e o contexto que o motivo do comando acrescenta depois de "por <autor>":
    ", no painel a partir de <aparelho>" — ou só ", a partir de <aparelho>" quando o autor já é `panel` (ninguém se
    identificou; "por panel, no painel" repetiria o painel) —, ou nada fora do painel. Recusa com 409
    `note_looks_secret` ANTES de qualquer escrita, pela triagem do voto do D2:

    - a nota, só o texto da pessoa — o contexto é do backend e nunca passa pela triagem;
    - o `requested_by` do corpo, sempre que vier, COM ou sem sessão: sem sessão ele é o autor gravado cru no motivo,
      em `result.resolved_by` e no evento do comando; com sessão é ignorado, mas um rótulo com cara de credencial
      não tem uso legítimo e a regra fica uma só."""
    texto = (nota or "").strip()
    antigo = f"{_PREFIXO_ANTIGO_DO_PAINEL[gesto]}{row['instance_id']}"
    do_painel = origem == "panel"
    if texto == antigo or texto.startswith(f"{antigo}:"):
        texto, do_painel = texto[len(antigo) + 1:].strip(), True
    if texto and _TRIAGEM_DE_NOTA.recusa(texto):
        # A nota iria crua para `commands.reason` (e `result.note`), e dali para o evento do comando no bus. Nada é
        # gravado, nem a decisão: o comando segue como estava até vir uma nota limpa.
        raise _err(409, "note_looks_secret", f"A nota tem formato ou assunto de credencial e não foi gravada, nem "
                                            f"{o_que}. Reescreva a observação sem o segredo.")
    rotulo = (requested_by or "").strip()
    if rotulo and _TRIAGEM_DE_NOTA.recusa(rotulo):
        raise _err(409, "note_looks_secret", f"O requested_by tem formato ou assunto de credencial e não foi gravado, "
                                            f"nem {o_que}. Mande um rótulo sem o segredo, ou nenhum (com sessão, o "
                                            f"autor é o operador dela).")
    if not do_painel:
        return texto or None, ""
    return texto or None, f", {'' if autor == PAINEL else 'no painel '}a partir de {row['instance_id']}"


@router.get("/commands/{command_id}", response_model=None)
async def get_command(request: Request, command_id: str) -> object:
    row = _st(request).commands.get(command_id)
    if row is None:
        raise _err(404, "not_found", f"Comando {command_id} não existe.")
    return command_dto(row)


@router.get("/commands", response_model=None)
async def list_commands(request: Request, instance_id: str | None = None, unsettled: bool = False,
                        limit: int = Query(50, ge=1, le=200)) -> object:
    """`unsettled=true` devolve só os comandos que terminaram sem desfecho conhecido — a fila de quem ainda
    espera uma resposta (da sonda ou de uma pessoa). É o que o painel precisa para eles pararem de sumir."""
    s = _st(request)
    linhas = s.commands.unsettled(limit) if unsettled else s.commands.recent(instance_id, limit)
    if unsettled and instance_id:
        linhas = [r for r in linhas if r["instance_id"] == instance_id]
    return [command_dto(r) for r in linhas]


@router.post("/commands/{command_id}/verify", response_model=None)
async def verify_command(request: Request, command_id: str) -> object:
    """"Verificar agora": pergunta ao estado real se aquele comando incerto deu certo.

    Para os verbos de ciclo de vida o desfecho é observável (`start` promete o aparelho no ar, `stop` promete o
    contrário), e ver o estado prometido é prova de sucesso. Não ver NÃO é prova de fracasso — então o comando
    que a sonda não fecha volta como está, esperando a decisão de alguém. Sempre 200: "continua incerto" é
    resposta legítima, e não erro.
    """
    s = _st(request)
    row = s.commands.get(command_id)
    if row is None:
        raise _err(404, "not_found", f"Comando {command_id} não existe.")
    novo = verificar_comando(s, row)
    mudou = novo["state"] != row["state"]
    if mudou:
        _publish_command(s, novo)
    return {"command": command_dto(novo).model_dump(mode="json"), "changed": mudou,
            "verifiable": row["verb"] in VERIFICAVEL_POR_ESTADO}


@router.post("/commands/{command_id}/cancel", response_model=None)
async def cancel_command(request: Request, command_id: str, body: CommandCancelBody | None = None) -> object:
    """Pedir o cancelamento de um comando ABERTO — a ponta que faltava do que a máquina de estados já previa.

    `cancel_requested` não encerra nada: ele diz "quero que pare" e o desfecho continua sendo de quem executa.
    Por isso a resposta é sempre 200 com o comando como está, mais o que foi possível fazer: um `start` remoto de
    540 s é interrompido no agente, um boot local é interrompido aqui, e um verbo sem ponto seguro apenas fica
    registrado — mentir sobre isso seria pior do que a espera.

    Repetir o pedido é seguro: o estado não muda de novo e o sinal é reenviado, que é o que alguém faz quando o
    worker acabou de reconectar. A nota ou o `requested_by` com cara de credencial é recusado (409
    `note_looks_secret`) antes de qualquer escrita; `origin=panel` acrescenta o contexto ao motivo
    (`_decisao_sobre_comando`).
    """
    s = _st(request)
    row = s.commands.get(command_id)
    if row is None:
        raise _err(404, "not_found", f"Comando {command_id} não existe.")
    if CommandState(row["state"]) not in COMMAND_OPEN:
        raise _err(409, "not_open", f"O comando {command_id} está em '{row['state']}': só um comando aberto pode "
                                   "ser cancelado.")
    corpo = body or CommandCancelBody()
    # `quem` só lê; o `requested_by` que ele pode devolver é triado logo abaixo, antes de qualquer escrita.
    autor = quem(request, corpo.requested_by)
    nota, contexto = _decisao_sobre_comando(row, corpo.note, corpo.requested_by, corpo.origin, autor, "cancel",
                                            "o pedido de cancelamento")
    if CommandState(row["state"]) is not CommandState.cancel_requested:
        motivo = f"cancelamento pedido por {autor}{contexto}" + (f": {nota}" if nota else "")
        try:
            row = s.commands.transition(command_id, CommandState.cancel_requested, reason=motivo)
        except InvalidCommandTransition as exc:
            # O desfecho chegou entre a leitura e a escrita: o comando já fechou sozinho, e não há o que cancelar.
            atual = s.commands.get(command_id)
            raise _err(409, "not_open", f"O comando {command_id} fechou antes do cancelamento "
                                       f"('{atual['state'] if atual else '?'}').") from exc
        _publish_command(s, row)
    entregue, detalhe = await _entregar_cancelamento(s, row)
    s.bus.emit("log", f"{row['instance_id']}: cancelamento do comando {command_id} ({row['verb']}) pedido por "
                      f"{autor} — {detalhe}", level="warn", instance_id=row["instance_id"])
    atual = s.commands.get(command_id) or row
    return {"command": command_dto(atual).model_dump(mode="json"), "delivered": entregue, "detail": detalhe}


@router.post("/commands/{command_id}/resolve", response_model=None)
async def resolve_command(request: Request, command_id: str, body: CommandResolveBody) -> object:
    """A decisão humana que tira um comando de `uncertain` — a outra porta de saída, para o que nenhuma sonda
    prova (o `reset` apagou os dados? o APK entrou?).

    Só `uncertain` é resolvível: comando terminal já tem desfecho, e reabrir seria apagar história. Quem
    resolveu e por quê ficam gravados no comando, porque "alguém decidiu" sem dizer quem é o mesmo tipo de
    afirmação vaga que esta fase inteira existe para eliminar. A nota ou o `requested_by` com cara de credencial é
    recusado (409 `note_looks_secret`) antes de qualquer escrita; `origin=panel` acrescenta o contexto ao motivo, e
    `result.note` guarda só o texto da pessoa (`_decisao_sobre_comando`).
    """
    s = _st(request)
    row = s.commands.get(command_id)
    if row is None:
        raise _err(404, "not_found", f"Comando {command_id} não existe.")
    if CommandState(row["state"]) not in COMMAND_UNSETTLED:
        raise _err(409, "not_unsettled", f"O comando {command_id} está em '{row['state']}': só um comando "
                                        "'uncertain' é resolvido à mão.")
    autor = quem(request, body.requested_by)       # só lê: a triagem abaixo vem antes de qualquer escrita
    nota, contexto = _decisao_sobre_comando(row, body.note, body.requested_by, body.origin, autor, "resolve",
                                            "a resolução")
    alvo = {"succeeded": CommandState.succeeded, "failed": CommandState.failed,
            "cancelled": CommandState.cancelled}[body.outcome]
    motivo = f"resolvido à mão por {autor}{contexto}" + (f": {nota}" if nota else "")
    anterior = loads(row["result"], {}) if row["result"] else {}
    dados = {**(anterior or {}), "resolved_by": autor, "resolved_at": now_iso(), "resolution": body.outcome,
             "note": nota, "previous_reason": row["reason"], **({"origin": "panel"} if contexto else {})}
    novo = s.commands.transition(command_id, alvo, reason=motivo, result=dados)
    _publish_command(s, novo)
    s.bus.emit("log", f"{novo['instance_id']}: o comando {command_id} ({novo['verb']}) era incerto e foi "
                      f"marcado como '{body.outcome}' por {autor}.", level="warn",
               instance_id=novo["instance_id"])
    # Sinal `comando_incerto_resolvido` (ADR-054), só depois de a decisão valer. A falha do livro nunca desfaz nem
    # derruba a resolução (`avisar`).
    avisar(s.costuras.comando_incerto_resolvido, ResolucaoDeComando(
        command_id=command_id, resolucao=body.outcome, nota=nota, quem=autor_do_sinal(request),
        simulated=s.provider.simulated))
    return command_dto(novo)


@router.post("/commands/refine")
async def refine_command(request: Request, body: CommandRefineBody) -> CommandRefinement:
    """Assistente do comando (ADR-047): o comando reescrito em blocos, o que ainda falta e se está pronto para
    planejar. Uma chamada de IA pelo papel `plan`; não cria execução. Com `run_id`, fecha as perguntas daquela
    execução em `needs_input` (as de destino ficam de fora: 409 `pergunta_de_destino`)."""
    try:
        return await ComandoAssistido(_st(request).runs).refinar(body)
    except RunError as exc:
        raise run_error(exc) from exc
