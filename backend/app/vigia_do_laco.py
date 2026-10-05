"""O vigia do laço de eventos (29.121): pilha de todas as threads quando o laço para de bater.

O incidente de 05/10 (12:55Z a 13:13Z, cinco kills do supervisor) não deixou como saber QUEM prendia o laço: o
supervisor confere `/api/health`, mata depois de três silêncios e não guarda pilha. Aqui, uma tarefa no laço marca uma
batida por segundo, e uma thread de fora dele confere a idade da batida. Passou de `LIMITE_S`, a thread escreve a
pilha de TODAS as threads (`faulthandler`, que não depende do laço e precisa do GIL só por um instante) num arquivo
em `data/logs/`, antes de o supervisor matar o processo (ele leva três conferências, uns 40 s). Uma chamada C que
segura o GIL sem soltar deixa o vigia mudo enquanto durar; laço Python puro solta o GIL a cada 5 ms.

- **A partida conta.** A thread nasce no `main()`, antes do `AppState` (que migra o banco e lê o disco). Até a
  primeira batida, o prazo é `PARTIDA_S`: uma partida presa (a de 13:07Z não escreveu linha em 2,5 min) também deixa
  pilha. `PARTIDA_S` fica abaixo da carência do supervisor (90 s até a primeira conferência; o kill vem pelo menos
  30 s depois): com 120 s, o despejo e o kill empatavam numa partida presa síncrona, e o `terminate()` do Windows é
  tiro seco. Um falso alarme numa partida lenta custa um arquivo e uma linha.
- **Episódio.** Um travamento contínuo é um episódio: no máximo `DESPEJOS_POR_EPISODIO` despejos, espaçados de
  `REDESPEJO_S` (dois despejos mostram se a pilha andou). Quando a batida volta, uma linha diz quanto durou.
- **Teto em disco.** Ficam os `MANTER` despejos mais novos.
- O custo é uma batida por segundo no laço e uma conferência por segundo na thread.
"""
from __future__ import annotations

import asyncio
import faulthandler
import logging
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger("poc.vigia")

LIMITE_S = 10.0
PARTIDA_S = 60.0
REDESPEJO_S = 30.0
DESPEJOS_POR_EPISODIO = 3
INTERVALO_S = 1.0
MANTER = 20
PREFIXO = "laco-travado-"


class VigiaDoLaco:
    def __init__(self, pasta: Path, *, limite_s: float = LIMITE_S, partida_s: float = PARTIDA_S,
                 redespejo_s: float = REDESPEJO_S, despejos_por_episodio: int = DESPEJOS_POR_EPISODIO,
                 intervalo_s: float = INTERVALO_S, manter: int = MANTER,
                 relogio: Callable[[], float] = time.monotonic) -> None:
        self.pasta = pasta
        self.limite_s, self.partida_s, self.redespejo_s = limite_s, partida_s, redespejo_s
        self.despejos_por_episodio, self.intervalo_s, self.manter = despejos_por_episodio, intervalo_s, manter
        self._relogio = relogio
        self._ultima = relogio()          # o nascimento conta como a última batida (a partida)
        self._bateu = False
        self._episodio_desde: float | None = None
        self._despejos_no_episodio = 0
        self._ultimo_despejo = 0.0
        self._parar = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ o lado do laço
    def bater(self) -> None:
        self._ultima = self._relogio()
        self._bateu = True

    async def laco_de_batidas(self) -> None:
        while True:
            self.bater()
            await asyncio.sleep(self.intervalo_s)

    # ------------------------------------------------------------------ o lado da thread
    def conferir(self) -> Path | None:
        """Um passo da thread: o arquivo do despejo, se houve; `None` se não. Exposto para o teste."""
        agora = self._relogio()
        atraso = agora - self._ultima
        prazo = self.limite_s if self._bateu else self.partida_s
        if atraso <= prazo:
            if self._episodio_desde is not None:
                log.warning("laço de eventos voltou a bater depois de %.1f s parado (%s despejo(s) de pilha)",
                            agora - self._episodio_desde, self._despejos_no_episodio)
                self._episodio_desde, self._despejos_no_episodio = None, 0
            return None
        if self._episodio_desde is None:
            self._episodio_desde = self._ultima
        if self._despejos_no_episodio >= self.despejos_por_episodio:
            return None
        if self._despejos_no_episodio and agora - self._ultimo_despejo < self.redespejo_s:
            return None
        fase = "laço" if self._bateu else "partida (antes da primeira batida)"
        arquivo = self._despejar(atraso, fase)
        self._despejos_no_episodio += 1
        self._ultimo_despejo = agora
        return arquivo

    def _despejar(self, atraso: float, fase: str) -> Path | None:
        carimbo = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        arquivo = self.pasta / f"{PREFIXO}{carimbo}-{self._despejos_no_episodio + 1}.txt"
        try:
            self.pasta.mkdir(parents=True, exist_ok=True)
            with arquivo.open("w", encoding="utf-8") as fh:
                fh.write(f"laço de eventos sem batida há {atraso:.1f} s ({fase}); {carimbo}\n\n")
                fh.flush()
                faulthandler.dump_traceback(file=fh, all_threads=True)
        except Exception as exc:  # noqa: BLE001 - disco cheio ou saturado: o aviso sai mesmo assim, sem o arquivo
            log.warning("laço de eventos sem batida há %.1f s (%s); a pilha não pôde ser gravada: %s", atraso, fase,
                        exc)
            return None
        log.warning("laço de eventos sem batida há %.1f s (%s); pilha de todas as threads em %s", atraso, fase,
                    arquivo)
        self._podar()
        return arquivo

    def _podar(self) -> None:
        try:
            antigos = sorted(self.pasta.glob(f"{PREFIXO}*.txt"))[:-self.manter]
            for velho in antigos:
                velho.unlink(missing_ok=True)
        except OSError:
            pass

    def iniciar(self) -> None:
        if self._thread is not None:
            return

        def rodar() -> None:
            while not self._parar.wait(self.intervalo_s):
                try:
                    self.conferir()
                except Exception:  # noqa: BLE001 - o vigia nunca derruba o backend
                    log.exception("vigia do laço: falha ao conferir")

        self._thread = threading.Thread(target=rodar, name="vigia-do-laco", daemon=True)
        self._thread.start()

    def parar(self) -> None:
        self._parar.set()


def ultimo_despejo(pasta: Path, *, desde_s: float = 300.0) -> Path | None:
    """O despejo mais novo dos últimos `desde_s` segundos, para a linha do kill do supervisor."""
    agora = time.time()
    candidatos: list[tuple[float, Path]] = []
    try:
        for p in pasta.glob(f"{PREFIXO}*.txt"):
            try:
                mtime = p.stat().st_mtime
            except OSError:               # sumiu entre a lista e o stat (poda, mão): não é candidato
                continue
            if agora - mtime <= desde_s:
                candidatos.append((mtime, p))
    except OSError:
        return None
    return max(candidatos, default=(0.0, None))[1]
