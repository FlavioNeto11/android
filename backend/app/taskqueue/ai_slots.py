"""Vagas de chamada de IA compartilhadas por TODOS os backends do mesmo banco (item 5.2; achados #35 e #94).

O problema. `max_ai_concurrency` era um `asyncio.Condition` — um semáforo **deste processo**. O recurso que ele
protege não é deste processo: é a taxa de chamadas contra o limite do provedor, que é do sistema inteiro. Com dois
backends no mesmo PostgreSQL, limite 3 virava seis chamadas simultâneas. A própria docstring do `Limiter` já
admitia o erro e apontava o conserto: lease no banco, no molde da posse de etapa.

A forma. Cada vaga é UMA LINHA (`ai_slots.slot` = 1..limite), tomada por compare-and-swap:

    UPDATE ai_slots SET holder=? … WHERE slot=? AND (holder IS NULL OR expires_at < ?)

e não por "conte as vagas vivas e insira mais uma". A contagem é o erro que `claim_step` já aprendeu: em
PostgreSQL (READ COMMITTED) dois backends leem o MESMO número e os dois acham que cabem. Com CAS por linha, só um
`UPDATE` casa e o outro recebe `rowcount=0`.

O vencimento existe para sobreviver à QUEDA do dono: uma vaga presa por um processo morto nunca seria liberada por
ninguém, e o teto encolheria em silêncio até zero. O tempo é medido pelo relógio do BANCO (`Database.agora_iso`,
item 5.3) — com o relógio de cada máquina, um backend adiantado tomaria vaga viva alheia.

O que continua por processo, de propósito: `boot_parallelism`. Lá o recurso protegido é a RAM e a CPU DESTA
máquina, e torná-lo global faria duas máquinas de 64 GB esperarem uma pela outra para ligar aparelho.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from ..db import Database, INTEGRITY_ERRORS

log = logging.getLogger("poc.ai_slots")

#: Validade de uma vaga sem renovação. Generoso pelo mesmo motivo do lease de etapa: o preço de um lease longo é
#: uma vaga demorar a voltar quando um backend morre; o preço de um curto é DUAS chamadas pagas onde cabia uma.
VAGA_TTL_S = 120.0
#: De quanto em quanto tempo o dono renova o que é dele. Quem chama é o mesmo `_manter_posse` das etapas.
RENOVAR_VAGA_S = 20.0


class VagasDeIA:
    """As vagas de chamada de IA deste backend, no banco. Um objeto por processo."""

    def __init__(self, db: Database, *, holder: str, poll_s: float = 0.2, ttl_s: float = VAGA_TTL_S):
        self.db = db
        self.holder = holder
        self.poll_s = poll_s
        self.ttl_s = ttl_s
        #: Vagas que EU tenho agora. Lista (e não conjunto) porque a devolução é "uma qualquer das minhas": as
        #: vagas são intercambiáveis, e quem sai do `async with` só precisa devolver uma.
        self._minhas: list[int] = []

    # ------------------------------------------------------------------ tomada
    def tentar(self, limite: int, role: str | None = None) -> int | None:
        """Tenta tomar uma vaga entre 1 e `limite`. Devolve o número da vaga, ou `None` quando não há nenhuma livre."""
        limite = max(1, int(limite))
        agora = self.db.agora_iso()
        vence = self.db.prazo_iso(self.ttl_s)
        for slot in range(1, limite + 1):
            cur = self.db.execute(
                "UPDATE ai_slots SET holder=?, role=?, taken_at=?, expires_at=? WHERE slot=?"
                " AND (holder IS NULL OR expires_at IS NULL OR expires_at < ?)",
                (self.holder, role, agora, vence, slot, agora))
            if (cur.rowcount or 0) == 1:
                self._minhas.append(slot)
                return slot
            if self.db.one("SELECT slot FROM ai_slots WHERE slot=?", (slot,)) is not None:
                continue                       # a linha existe e está ocupada por alguém vivo
            try:
                # A linha nasce sob demanda: o limite é um ajuste do painel e pode subir a qualquer momento.
                self.db.execute("INSERT INTO ai_slots(slot, holder, role, taken_at, expires_at) VALUES (?,?,?,?,?)",
                                (slot, self.holder, role, agora, vence))
            except INTEGRITY_ERRORS:
                continue                       # outro backend criou a mesma linha primeiro: ele ficou com a vaga
            self._minhas.append(slot)
            return slot
        return None

    async def adquirir(self, limite: int, role: str | None = None) -> int:
        """Espera até ter vaga. Sondagem e não notificação: o aviso teria de atravessar processos, e a espera aqui
        é medida em segundos de chamada de modelo — 200 ms de granularidade não custam nada perto disso."""
        while True:
            slot = self.tentar(limite, role)
            if slot is not None:
                return slot
            await asyncio.sleep(self.poll_s)

    # ------------------------------------------------------------------ devolução e manutenção
    def liberar(self, slot: int) -> None:
        """Devolve a vaga. `AND holder=?`: se ela já tinha vencido e outro a tomou, eu não apago o dono dele."""
        self.db.execute(
            "UPDATE ai_slots SET holder=NULL, role=NULL, taken_at=NULL, expires_at=NULL WHERE slot=? AND holder=?",
            (slot, self.holder))
        try:
            self._minhas.remove(slot)
        except ValueError:
            pass

    def renovar(self) -> int:
        """Renova o vencimento das vagas que este processo tem. Enquanto ele respira, ninguém as toma."""
        # 31.343: roda numa thread (o `_manter_posse` saiu do laço), enquanto o laço toma e solta vagas: a fotografia da lista
        # evita iterar uma lista que muda no meio. Uma vaga solta entre a foto e o UPDATE não é renovada à toa (o WHERE tem o dono).
        minhas = tuple(self._minhas)
        if not minhas:
            return 0
        marcas = ",".join("?" for _ in minhas)
        cur = self.db.execute(
            f"UPDATE ai_slots SET expires_at=? WHERE holder=? AND slot IN ({marcas})",   # noqa: S608 - marcadores
            (self.db.prazo_iso(self.ttl_s), self.holder, *minhas))
        return int(cur.rowcount or 0)

    def soltar_todas(self) -> int:
        """Na partida: solta o que ficou marcado como meu antes da queda.

        Sem isto, um backend que caiu com vagas tomadas e voltou com o MESMO `owner_id` encontraria as próprias
        vagas ocupadas e teria de esperar o vencimento — o teto encolheria por dois minutos a cada reinício.
        Ninguém mais pode fazer isso por ele: a vaga de outro dono só vence por tempo.
        """
        self._minhas.clear()
        cur = self.db.execute(
            "UPDATE ai_slots SET holder=NULL, role=NULL, taken_at=NULL, expires_at=NULL WHERE holder=?",
            (self.holder,))
        n = int(cur.rowcount or 0)
        if n:
            log.info("partida: %d vaga(s) de IA soltas de uma execução anterior deste backend", n)
        return n

    # ------------------------------------------------------------------ leitura
    def ocupadas(self) -> int:
        """Quantas vagas estão vivas AGORA, somando todos os backends. É o número que prova o teto global."""
        agora = self.db.agora_iso()
        return int(self.db.scalar("SELECT COUNT(*) FROM ai_slots WHERE holder IS NOT NULL AND expires_at > ?",
                                  (agora,)) or 0)

    def donos(self) -> dict[str, int]:
        """`{backend: vagas vivas}` — para o relatório de custo e para o teste enxergar a soma."""
        agora = self.db.agora_iso()
        linhas: list[Any] = self.db.query(
            "SELECT holder, COUNT(*) AS n FROM ai_slots WHERE holder IS NOT NULL AND expires_at > ?"
            " GROUP BY holder", (agora,))
        return {r["holder"]: int(r["n"]) for r in linhas}
