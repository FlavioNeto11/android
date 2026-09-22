"""Registro dos workers: identidade, capacidade, saúde e o canal por onde o comando chega até eles.

Três coisas que o desenho protege de propósito:

1. **Inscrição é de uso único e prazo curto.** O instalador cola um token, o agente o troca por credencial
   permanente, e o token morre. Ninguém digita segredo de longa duração numa máquina nova.
2. **Ausência de batida é o que marca indisponível — não o socket fechado.** Socket cai por rede piscando; isso
   não significa que o worker parou de trabalhar, e tratar as duas coisas como a mesma foi o defeito que o
   projeto de referência (STF) levou anos arrastando.
3. **Cerca (fencing).** Resultado com cerca velha é recusado: um worker que voltou do limbo não sobrescreve o
   estado atual.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
from typing import Any, Awaitable, Callable

from ..commands.states import COMMAND_OPEN
from ..db import Database, Row, dumps, loads
from ..models import WorkerDTO
from ..util import iso_in, now, now_iso, parse_iso, truncate
from .protocol import (PROTOCOL_VERSION, Dispatch, Heartbeat, Hello, Result, WorkerDevice, WorkerResources, Welcome)

log = logging.getLogger("poc.workers")

#: Prazo padrão da batida e quantas perdidas toleram antes de marcar offline. Configurável no `welcome`.
HEARTBEAT_S = 10.0
BATIDAS_PERDIDAS = 3
#: Validade de um token de inscrição. Curto de propósito: ele existe para a janela da instalação, não para viver.
INSCRICAO_TTL_S = 3600.0


def _hash(token: str) -> str:
    """SHA-256 basta: o token tem 32 bytes de entropia, então não há dicionário a proteger contra."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class WorkerError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


class WorkerLink:
    """Conexão viva com um worker. Some quando o socket cai; a linha no banco permanece."""

    def __init__(self, worker_id: str, send: Callable[[dict[str, Any]], Awaitable[None]]):
        self.worker_id = worker_id
        self.send = send
        #: Comandos despachados e ainda sem desfecho: id → (cerca, futuro do resultado).
        self.pendentes: dict[str, tuple[int, asyncio.Future[Result]]] = {}
        #: Comandos cujo recebimento o worker confirmou.
        self.confirmados: set[str] = set()

    def encerrar(self, motivo: str) -> None:
        """Socket caiu. Quem estava em voo vira INCERTO, nunca falha: o worker pode ter agido."""
        for cid, (_, fut) in list(self.pendentes.items()):
            if not fut.done():
                fut.set_result(Result(command_id=cid, outcome="uncertain", reason=motivo))
        self.pendentes.clear()


class WorkerRegistry:
    def __init__(self, db: Database, *, on_change: Callable[[str], None] | None = None):
        self.db = db
        self.live: dict[str, WorkerLink] = {}
        #: Chamado com o worker_id quando algo observável muda, para o painel receber evento.
        self.on_change = on_change or (lambda _wid: None)

    # ------------------------------------------------------------------ inscrição
    def criar_inscricao(self, label: str | None = None, ttl_s: float = INSCRICAO_TTL_S) -> str:
        """Devolve o token EM CLARO uma única vez. Só o hash é guardado."""
        token = secrets.token_urlsafe(32)
        self.db.execute(
            "INSERT INTO worker_enrollments(token_hash, label, created_at, expires_at) VALUES (?,?,?,?)",
            (_hash(token), truncate(label, 120), now_iso(), iso_in(ttl_s)))
        return token

    def _consumir_inscricao(self, token: str, worker_id: str) -> None:
        linha = self.db.one("SELECT * FROM worker_enrollments WHERE token_hash=?", (_hash(token),))
        if linha is None:
            raise WorkerError("unknown_enrollment", "Token de inscrição desconhecido.")
        if linha["used_at"]:
            raise WorkerError("enrollment_used", "Este token de inscrição já foi usado; gere outro no painel.")
        venceu = parse_iso(linha["expires_at"])
        if venceu is not None and venceu < now():
            raise WorkerError("enrollment_expired", "Token de inscrição vencido; gere outro no painel.")
        self.db.execute("UPDATE worker_enrollments SET used_at=?, used_by=? WHERE token_hash=?",
                        (now_iso(), worker_id, _hash(token)))

    # ------------------------------------------------------------------ conexão
    def autenticar(self, hello: Hello, *, token: str | None, enrollment: str | None) -> str:
        """Confere quem é o worker e devolve a credencial permanente quando ele acaba de se inscrever.

        Dois caminhos: **inscrição** (primeira vez, token de uso único → devolve credencial) e **credencial**
        (todas as vezes seguintes). Nunca os dois.
        """
        if hello.protocol > PROTOCOL_VERSION:
            raise WorkerError("protocol_too_new", f"O agente fala o protocolo {hello.protocol} e este servidor "
                                                  f"fala {PROTOCOL_VERSION}; atualize o servidor.")
        linha = self.db.one("SELECT * FROM workers WHERE id=?", (hello.worker_id,))
        if enrollment:
            if linha is not None:
                raise WorkerError("already_enrolled", f"O worker '{hello.worker_id}' já está inscrito; use a "
                                                      "credencial dele em vez de um token de inscrição.")
            self._consumir_inscricao(enrollment, hello.worker_id)
            credencial = secrets.token_urlsafe(32)
            self._upsert(hello, token_hash=_hash(credencial), enrolled=True)
            return credencial
        if linha is None:
            raise WorkerError("not_enrolled", f"O worker '{hello.worker_id}' não está inscrito. Gere um token de "
                                              "inscrição no painel e rode o instalador com ele.")
        if not token or not secrets.compare_digest(linha["token_hash"], _hash(token)):
            raise WorkerError("bad_credential", "Credencial de worker inválida.")
        self._upsert(hello, token_hash=linha["token_hash"], enrolled=False)
        return ""

    def _upsert(self, hello: Hello, *, token_hash: str, enrolled: bool) -> None:
        agora = now_iso()
        self.db.execute(
            "INSERT INTO workers(id, name, os, os_version, agent_version, protocol, appium_mode, appium_url,"
            " max_slots, verbs, state, state_detail, resources, devices, enrolled_at, last_seen_at, token_hash)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(id) DO UPDATE SET name=excluded.name, os=excluded.os, os_version=excluded.os_version,"
            " agent_version=excluded.agent_version, protocol=excluded.protocol, appium_mode=excluded.appium_mode,"
            " appium_url=excluded.appium_url, max_slots=excluded.max_slots, verbs=excluded.verbs,"
            " state=excluded.state, state_detail=NULL, resources=excluded.resources, devices=excluded.devices,"
            " last_seen_at=excluded.last_seen_at",
            (hello.worker_id, hello.name, hello.os, hello.os_version, hello.agent_version, hello.protocol,
             hello.appium_mode, hello.appium_url, hello.max_slots, dumps(hello.verbs), "online", None,
             dumps(hello.resources.model_dump()) if hello.resources else None,
             dumps([d.model_dump() for d in hello.devices]), agora, agora, token_hash))

    def attach(self, worker_id: str, send: Callable[[dict[str, Any]], Awaitable[None]]) -> WorkerLink:
        """Instala o canal. Conexão nova do mesmo worker derruba a anterior: um dono por worker, sempre."""
        if (antigo := self.live.get(worker_id)) is not None:
            antigo.encerrar("uma conexão nova deste worker substituiu a anterior")
        link = WorkerLink(worker_id, send)
        self.live[worker_id] = link
        self.on_change(worker_id)
        return link

    def detach(self, worker_id: str, motivo: str) -> None:
        link = self.live.pop(worker_id, None)
        if link is not None:
            link.encerrar(motivo)
        # NÃO marca offline aqui: socket cai por rede piscando, e o worker pode voltar em segundos ainda dentro do
        # prazo da batida. Quem decide "indisponível" é `reap()`, pela ausência de batida.
        self.db.execute("UPDATE workers SET state_detail=? WHERE id=?", (truncate(motivo, 200), worker_id))
        self.on_change(worker_id)

    def welcome(self, esperados: dict[str, str]) -> Welcome:
        return Welcome(server_time=now_iso(), heartbeat_s=HEARTBEAT_S, expected_devices=esperados)

    # ------------------------------------------------------------------ batida e saúde
    def on_heartbeat(self, worker_id: str, hb: Heartbeat) -> None:
        self.db.execute(
            "UPDATE workers SET last_seen_at=?, state='online', resources=COALESCE(?, resources),"
            " devices=COALESCE(?, devices) WHERE id=?",
            (now_iso(), dumps(hb.resources.model_dump()) if hb.resources else None,
             dumps([d.model_dump() for d in hb.devices]) if hb.devices else None, worker_id))
        self.on_change(worker_id)

    def reap(self) -> list[str]:
        """Marca offline quem não bate há tempo demais. Devolve quem mudou, para virar evento."""
        limite = iso_in(-HEARTBEAT_S * BATIDAS_PERDIDAS)
        candidatos = self.db.query(
            "SELECT id FROM workers WHERE state<>'offline' AND (last_seen_at IS NULL OR last_seen_at < ?)",
            (limite,))
        mudados = []
        for linha in candidatos:
            self.db.execute("UPDATE workers SET state='offline', state_detail=? WHERE id=?",
                            (f"sem batida há mais de {int(HEARTBEAT_S * BATIDAS_PERDIDAS)} s", linha["id"]))
            mudados.append(linha["id"])
            self.on_change(linha["id"])
        return mudados

    def set_maintenance(self, worker_id: str, on: bool) -> Row:
        """Manutenção interrompe novas atribuições e **não** derruba o que já está em voo."""
        if self.db.one("SELECT id FROM workers WHERE id=?", (worker_id,)) is None:
            raise WorkerError("not_found", f"Worker '{worker_id}' não existe.")
        self.db.execute("UPDATE workers SET maintenance=? WHERE id=?", (int(on), worker_id))
        self.on_change(worker_id)
        linha = self.db.one("SELECT * FROM workers WHERE id=?", (worker_id,))
        assert linha is not None
        return linha

    # ------------------------------------------------------------------ remoção e rotação de credencial
    def remove(self, worker_id: str, *, force: bool = False) -> None:
        """Remove o registro do worker — o procedimento que `docs/worker.md` promete e, até aqui, não existia.

        Recusa com 409 se o worker está conectado ou tem comando em voo, a menos que `force`; nesses dois casos
        `force` desconecta o canal (o socket cai, quem estava em voo vira `uncertain`, do mesmo jeito que uma queda
        de rede) antes de apagar. Os aparelhos amarrados a ele NUNCA ficam presos a um worker fantasma: o vínculo
        é desfeito, não a instância.
        """
        linha = self.db.one("SELECT * FROM workers WHERE id=?", (worker_id,))
        if linha is None:
            raise WorkerError("not_found", f"Worker '{worker_id}' não existe.")
        if not force and worker_id in self.live:
            raise WorkerError("connected", f"Worker '{linha['name']}' está conectado; desconecte-o antes ou "
                                          "remova com force.")
        marcadores = ",".join("?" for _ in COMMAND_OPEN)
        abertos = self.db.scalar(f"SELECT COUNT(*) FROM commands WHERE worker_id=? AND state IN ({marcadores})",
                                 (worker_id, *(s.value for s in COMMAND_OPEN)))
        if not force and abertos:
            raise WorkerError("open_commands", f"Worker '{linha['name']}' tem {abertos} comando(s) em voo; espere "
                                              "terminar ou remova com force.")
        if worker_id in self.live:
            self.detach(worker_id, "worker removido no painel")
        # O aparelho não é removido junto — só perde o dono. Continua existindo, agora sem ciclo de vida remoto,
        # até alguém amarrá-lo a outro worker ou reinscrever este.
        self.db.execute("UPDATE instances SET worker_id=NULL WHERE worker_id=?", (worker_id,))
        self.db.execute("DELETE FROM workers WHERE id=?", (worker_id,))
        self.on_change(worker_id)

    def rotate_credential(self, worker_id: str) -> str:
        """Gera credencial nova e derruba a conexão viva NA HORA — a antiga para de servir imediatamente.

        Devolve o token em claro, uma única vez: só o hash fica gravado. Cobre a máquina comprometida (o arquivo
        de credencial hoje é legível por `BUILTIN\\Users` no notebook): quem suspeitar disso roda isto e a
        credencial vazada vira inútil, sem precisar apagar e reinscrever o worker do zero.
        """
        linha = self.db.one("SELECT * FROM workers WHERE id=?", (worker_id,))
        if linha is None:
            raise WorkerError("not_found", f"Worker '{worker_id}' não existe.")
        nova = secrets.token_urlsafe(32)
        self.db.execute("UPDATE workers SET token_hash=? WHERE id=?", (_hash(nova), worker_id))
        if worker_id in self.live:
            self.detach(worker_id, "credencial rotacionada no painel: reconecte com o token novo")
        self.on_change(worker_id)
        return nova

    def verbs_de(self, worker_id: str) -> list[str] | None:
        """Verbos que aquele worker declarou, se ele está CONECTADO. Desconectado devolve `None`.

        A conexão importa: capacidade declarada por um worker que não está lá não é capacidade — é promessa.
        """
        if worker_id not in self.live:
            return None
        linha = self.db.one("SELECT verbs FROM workers WHERE id=?", (worker_id,))
        return (loads(linha["verbs"]) or None) if linha is not None else None

    def aceita_trabalho(self, worker_id: str) -> str | None:
        """`None` quando aceita; senão, a frase que explica por que não."""
        linha = self.db.one("SELECT * FROM workers WHERE id=?", (worker_id,))
        if linha is None:
            return f"worker '{worker_id}' não está inscrito"
        if linha["maintenance"]:
            return f"worker '{linha['name']}' está em manutenção: novas atribuições estão suspensas"
        if worker_id not in self.live:
            return f"worker '{linha['name']}' não está conectado"
        return None

    def motivo_manutencao(self, worker_id: str) -> str | None:
        """Só o motivo de MANUTENÇÃO — nunca 'não conectado'/'não inscrito'. Esses dois casos já têm mensagem
        própria mais específica na checagem de capacidade do aparelho (verbos declarados pelo worker), então quem
        só quer saber "a manutenção está ligada?" chama isto, não `aceita_trabalho`."""
        linha = self.db.one("SELECT * FROM workers WHERE id=?", (worker_id,))
        if linha is not None and linha["maintenance"]:
            return f"worker '{linha['name']}' está em manutenção: novas atribuições estão suspensas"
        return None

    # ------------------------------------------------------------------ despacho
    async def dispatch(self, worker_id: str, msg: Dispatch) -> Result:
        """Manda o comando e espera o desfecho.

        Prazo estourado devolve `uncertain`, nunca falha: o worker pode ter agido e não conseguido responder. É a
        regra que o projeto já aplica às ações da IA, agora entre máquinas.
        """
        link = self.live.get(worker_id)
        if link is None:
            raise WorkerError("worker_offline", f"Worker '{worker_id}' não está conectado.")
        fut: asyncio.Future[Result] = asyncio.get_running_loop().create_future()
        link.pendentes[msg.command_id] = (msg.fence, fut)
        try:
            await link.send(msg.model_dump())
        except Exception as exc:  # noqa: BLE001 - falha ao ENVIAR é o único caso em que nada aconteceu
            link.pendentes.pop(msg.command_id, None)
            raise WorkerError("send_failed", f"Não foi possível enviar o comando ao worker: {exc}") from exc
        try:
            return await asyncio.wait_for(fut, timeout=msg.timeout_s)
        except asyncio.TimeoutError:
            return Result(command_id=msg.command_id, outcome="uncertain",
                          reason=f"o worker não respondeu em {msg.timeout_s:.0f} s; resultado desconhecido")
        finally:
            link.pendentes.pop(msg.command_id, None)
            link.confirmados.discard(msg.command_id)

    def on_ack(self, worker_id: str, command_id: str) -> bool:
        link = self.live.get(worker_id)
        if link is None or command_id not in link.pendentes:
            return False
        link.confirmados.add(command_id)
        return True

    def on_result(self, worker_id: str, result: Result, *, fence: int | None = None) -> bool:
        """Entrega o desfecho. Cerca velha é RECUSADA — worker antigo não sobrescreve o presente."""
        link = self.live.get(worker_id)
        if link is None:
            return False
        pendente = link.pendentes.get(result.command_id)
        if pendente is None:
            return False
        cerca, fut = pendente
        if fence is not None and fence != cerca:
            log.warning("resultado de %s com cerca %s (esperada %s): recusado", worker_id, fence, cerca)
            return False
        if not fut.done():
            fut.set_result(result)
        return True

    async def cancel(self, worker_id: str, command_id: str) -> bool:
        link = self.live.get(worker_id)
        if link is None:
            return False
        await link.send({"type": "cancel", "command_id": command_id})
        return True

    # ------------------------------------------------------------------ leitura
    def rows(self) -> list[Row]:
        return self.db.query("SELECT * FROM workers ORDER BY name")

    def dto(self, row: Row) -> WorkerDTO:
        conectado = row["id"] in self.live
        return WorkerDTO(
            id=row["id"], name=row["name"], os=row["os"], os_version=row["os_version"],
            agent_version=row["agent_version"], appium_mode=row["appium_mode"], appium_url=row["appium_url"],
            max_slots=row["max_slots"], verbs=loads(row["verbs"]) or [],
            # `maintenance` ganha do estado observado na EXIBIÇÃO, mas os dois ficam no DTO: um worker em
            # manutenção continua online, e esconder isso atrapalharia quem está diagnosticando.
            state="maintenance" if row["maintenance"] else row["state"],
            observed_state=row["state"], maintenance=bool(row["maintenance"]), state_detail=row["state_detail"],
            connected=conectado, resources=WorkerResources.model_validate(loads(row["resources"]) or {}),
            devices=[WorkerDevice.model_validate(d) for d in (loads(row["devices"]) or [])],
            enrolled_at=row["enrolled_at"], last_seen_at=row["last_seen_at"])

    def dtos(self) -> list[WorkerDTO]:
        return [self.dto(r) for r in self.rows()]
