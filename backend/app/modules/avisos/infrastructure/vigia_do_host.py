"""O vigia do host da Central: dois tiques que leem o PRÓPRIO host e avisam pela rotina (itens 28.60 e 28.58).

- o veredito do ensaio de restauração (`data/restore-ensaio/ultimo.json`, gravado pelo `scripts/restore-ensaio.ps1`):
  `falhou`, `pulado`, ilegível ou velho viram um aviso (`domain.host`). O script do host NÃO enfileira aviso (o canal só
  aceita tipos montados no backend): quem avisa é este vigia, que lê o arquivo, e por isso não há rota nova;
- o livre do disco do central: abaixo do piso, e de novo a cada degrau abaixo dele, um aviso com o que ocupa o espaço.

Só no líder da trava `avisos`: o arquivo e o disco são os da máquina que hospeda o central, e uma réplica em outra máquina
mediria o disco errado. A chave de cada aviso é a do fato, então uma segunda leitura do mesmo fato não repete a mensagem.

Nada aqui apaga, move ou escreve arquivo. A medida das pastas é a única leitura pesada: roda numa thread, em modo de fundo
do Windows (prioridade de CPU e de disco baixas), no máximo uma vez por hora, só quando o disco está abaixo do piso, e
nunca desce no Docker nem segue junção ou link simbólico (o que não tem leitura barata sai "não medido").
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from app.config import Config
from app.modules.avisos.domain.host import (
    Veredito,
    aviso_do_disco,
    aviso_do_ensaio,
    aviso_do_ensaio_ilegivel,
    aviso_do_ensaio_velho,
    degrau_do_disco,
    veredito_de,
)
from app.modules.avisos.domain.mensagem import Aviso
from app.util import now

log = logging.getLogger("poc.avisos")

#: A subida e a saúde nunca esperam o vigia.
PRIMEIRA_VOLTA_S = 60.0
#: O passo do laço; cada assunto tem o próprio `intervalo_min` por cima dele.
PASSO_S = 60.0
#: Quanto a medida das pastas vale antes de refazer, e quanto cada pasta pode levar antes de virar "não medido".
CACHE_DA_OCUPACAO_S = 3600.0
ORCAMENTO_DA_PASTA_S = 30.0
#: Leituras ruins SEGUIDAS do `ultimo.json` antes de avisar "ilegível": o script grava o arquivo inteiro de uma vez, mas
#: uma leitura no meio da escrita não deve virar mensagem.
ILEGIVEIS_PARA_AVISAR = 2

_THREAD_MODE_BACKGROUND_BEGIN, _THREAD_MODE_BACKGROUND_END = 0x00010000, 0x00020000


@contextmanager
def _modo_de_fundo() -> Iterator[None]:
    """No Windows, a thread da medida entra no modo de fundo (CPU e E/S de prioridade baixa) e sai dele no fim; a thread
    volta ao pool do `to_thread` como estava. Fora do Windows, ou se a chamada falhar, nada muda."""
    iniciou = False
    if os.name == "nt":
        try:
            import ctypes

            k = ctypes.windll.kernel32  # type: ignore[attr-defined]
            k.GetCurrentThread.restype = ctypes.c_void_p
            k.SetThreadPriority.argtypes = [ctypes.c_void_p, ctypes.c_int]
            iniciou = bool(k.SetThreadPriority(k.GetCurrentThread(), _THREAD_MODE_BACKGROUND_BEGIN))
        except Exception:  # noqa: BLE001 - prioridade é cortesia; medir sem ela é aceitável
            iniciou = False
    try:
        yield
    finally:
        if iniciou:
            try:
                import ctypes

                k = ctypes.windll.kernel32  # type: ignore[attr-defined]
                k.SetThreadPriority(k.GetCurrentThread(), _THREAD_MODE_BACKGROUND_END)
            except Exception:  # noqa: BLE001
                pass


def medir_pasta_gb(pasta: Path, orcamento_s: float = ORCAMENTO_DA_PASTA_S) -> float | None:
    """O tamanho da pasta em GB, só dos arquivos dela, sem seguir link simbólico nem junção. `None` se a pasta não existe
    ou se a leitura passou do orçamento de tempo (pasta grande demais para medir de graça: sai "não medido")."""
    if not pasta.is_dir():
        return None
    limite = time.monotonic() + orcamento_s
    total, vistos, pilha = 0, 0, [str(pasta)]
    with _modo_de_fundo():
        while pilha:
            try:
                entradas = os.scandir(pilha.pop())
            except OSError:
                continue
            with entradas:
                for e in entradas:
                    vistos += 1
                    if vistos % 500 == 0:
                        time.sleep(0.002)               # cede a vez: a medida nunca disputa com o que roda no central
                    if time.monotonic() >= limite:
                        return None
                    try:
                        e_junc = getattr(e, "is_junction", None)
                        if e.is_symlink() or (e_junc is not None and e_junc()):
                            continue
                        if e.is_dir(follow_symlinks=False):
                            pilha.append(e.path)
                        elif e.is_file(follow_symlinks=False):
                            total += e.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
    return total / 2**30


class VigiaDoHost:
    def __init__(self, cfg: Config, avisar: Callable[[Aviso], bool], *, pronto: Callable[[], bool],
                 ler_disco: Callable[[Path], tuple[float, float] | None],
                 relogio: Callable[[], datetime] = now,
                 medir: Callable[[], Mapping[str, float | None]] | None = None):
        """`avisar` é o `ServicoDeAvisos.enfileirar_aviso`; `pronto` diz se o canal está ligado e com segredo (sem ele o
        tique nem lê, para não gastar o fato); `ler_disco` é o leitor da saúde (`devices.diagnostics.ler_disco`), passado
        de fora para o módulo de aviso não importar `devices`."""
        self.cfg = cfg
        self._avisar = avisar
        self._pronto = pronto
        self._ler_disco = ler_disco
        self._relogio = relogio
        self._medir = medir or self._medir_as_pastas
        self._ilegiveis = 0
        #: O degrau (GB) mais fundo já avisado neste episódio; `None` = disco no piso ou acima (armado).
        self._degrau: float | None = None
        #: Sobe cada vez que o disco volta ao piso depois de avisar: a recaída no mesmo dia tem chave nova. Na subida do
        #: processo volta a 0, e a chave do dia já gravada segura o aviso repetido (o custo de um reinício é, no máximo,
        #: um aviso por degrau por dia).
        self._episodio = 0
        self._ocupacao: tuple[float, dict[str, float | None]] | None = None

    # ------------------------------------------------------------------ 28.60: o ensaio de restauração
    def _veredito(self) -> tuple[str, object]:
        """`("ausente", None)`, `("ilegivel", None)` ou `("lido", <JSON>)`."""
        caminho = self.cfg.path(self.cfg.file.avisos.restore_ensaio.ultimo_json)
        try:
            texto = caminho.read_text(encoding="utf-8-sig")
        except FileNotFoundError:
            return "ausente", None
        except (OSError, ValueError):
            return "ilegivel", None
        try:
            return "lido", json.loads(texto)
        except ValueError:
            return "ilegivel", None

    def conferir_ensaio(self) -> list[Aviso]:
        """Uma volta do ensaio. Devolve os avisos entregues à fila nesta volta (os que já estavam lá voltam à fila com a
        mesma chave e não duplicam). Arquivo ausente: nada (a tarefa semanal ainda não rodou, ou não foi instalada; sem
        o primeiro veredito não há como saber o que esperar, então não há aviso de "velho" na primeira semana)."""
        cfg = self.cfg.file.avisos.restore_ensaio
        if not cfg.enabled:
            return []
        agora = self._relogio()
        estado, dados = self._veredito()
        if estado == "ausente":
            self._ilegiveis = 0
            return []
        v: Veredito | None = veredito_de(dados) if estado == "lido" else None
        if v is None:
            self._ilegiveis += 1
            log.warning("avisos: veredito do ensaio de restauração ilegível (%d leitura(s) seguida(s))", self._ilegiveis)
            return self._enfileirar([aviso_do_ensaio_ilegivel(agora)]) if self._ilegiveis >= ILEGIVEIS_PARA_AVISAR else []
        self._ilegiveis = 0
        avisos: list[Aviso] = []
        do_veredito = aviso_do_ensaio(v)
        if do_veredito is not None:
            avisos.append(do_veredito)
        if (agora - v.ts).total_seconds() > cfg.idade_max_h * 3600:
            avisos.append(aviso_do_ensaio_velho(v.ts, agora))
        return self._enfileirar(avisos)

    # ------------------------------------------------------------------ 28.58: disco baixo no central
    def _medir_as_pastas(self) -> dict[str, float | None]:
        return {"backups": medir_pasta_gb(self.cfg.data_dir / "backups"), "AVDs": medir_pasta_gb(self.cfg.avd_home),
                "capturas": medir_pasta_gb(self.cfg.evidence_dir),
                # Sem leitura barata e segura: descer no Docker seria uma varredura sem fim. Diz "não medido".
                "Docker": None}

    async def _quanto_ocupa(self) -> Mapping[str, float | None]:
        if self._ocupacao is not None and time.monotonic() - self._ocupacao[0] < CACHE_DA_OCUPACAO_S:
            return self._ocupacao[1]
        try:
            medido = dict(await asyncio.to_thread(self._medir))
        except Exception as exc:  # noqa: BLE001 - sem a medida o aviso sai, só que com "não medido"
            log.warning("avisos: medida das pastas do disco falhou: %s", type(exc).__name__)
            medido = {"backups": None, "AVDs": None, "capturas": None, "Docker": None}
        self._ocupacao = (time.monotonic(), medido)
        return medido

    async def conferir_disco(self) -> Aviso | None:
        """Uma volta do disco. Avisa quando entra num degrau mais fundo que o último avisado; ao voltar ao piso, rearma."""
        cfg = self.cfg.file.avisos.disco
        if not cfg.enabled:
            return None
        lido = self._ler_disco(self.cfg.root)
        if lido is None:
            log.warning("avisos: o disco do central não pôde ser lido")
            return None
        livre, total = lido
        degrau = degrau_do_disco(livre, cfg.piso_gb, cfg.degrau_gb)
        if degrau is None:
            if self._degrau is not None:
                self._degrau, self._episodio = None, self._episodio + 1
            return None
        if self._degrau is not None and degrau >= self._degrau:
            return None
        aviso = aviso_do_disco(livre, total, degrau, await self._quanto_ocupa(), critico_gb=cfg.critico_gb,
                               proximo=degrau - cfg.degrau_gb, episodio=self._episodio, agora=self._relogio())
        self._degrau = degrau
        self._enfileirar([aviso])
        return aviso

    # ------------------------------------------------------------------ laço
    def _enfileirar(self, avisos: list[Aviso]) -> list[Aviso]:
        for aviso in avisos:
            self._avisar(aviso)
        return avisos

    async def laco(self, lider: Callable[[], int | None]) -> None:
        """Um passo por minuto; cada assunto roda no próprio `intervalo_min`, só no líder da trava `avisos` e com o canal
        pronto. O `pronto` vem ANTES do líder: com o aviso desligado este laço não toma a trava (28.35)."""
        await asyncio.sleep(PRIMEIRA_VOLTA_S)
        proximo = {"ensaio": 0.0, "disco": 0.0}
        while True:
            try:
                if self._pronto() and lider() is not None:
                    agora = time.monotonic()
                    if agora >= proximo["ensaio"]:
                        proximo["ensaio"] = agora + self.cfg.file.avisos.restore_ensaio.intervalo_min * 60.0
                        self.conferir_ensaio()
                    if agora >= proximo["disco"]:
                        proximo["disco"] = agora + self.cfg.file.avisos.disco.intervalo_min * 60.0
                        await self.conferir_disco()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - o vigia nunca derruba o processo
                log.exception("avisos: volta do vigia do host")
            await asyncio.sleep(PASSO_S)
