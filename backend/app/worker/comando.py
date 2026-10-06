"""Comando remoto no agente (`remote_exec`, item 29.154): executa UMA linha de comando nesta máquina a pedido do
central e devolve saída, erro e código de saída.

O desenho (`.claude/handoffs/android/desenho-29-154.md`, revisado pelo revisor-segredos) manda quatro coisas, e todas
moram aqui:

1. **Nasce desligado.** Sem `comando_remoto: true` no `worker.yaml` o agente nem anuncia a feature; sem a feature
   aceita no `welcome`, todo `exec` é recusado sem tocar em nada.
2. **Nada de segredo na linha.** A linha com cara de credencial é recusada ANTES de rodar e nunca vai ao diário: o
   que se grava em disco é só o desfecho já redigido.
3. **Redigir antes de cortar.** A saída é lida até um teto bruto, REDIGIDA inteira e só então cortada (começo e fim):
   o corte nunca parte um segredo ao meio.
4. **Árvore de processos.** Estourou o prazo ou veio `exec_cancel`: a árvore inteira morre (`taskkill /T` no Windows,
   grupo de processos no Linux), não só o filho direto.

Um comando por vez. Queda do agente no meio da execução deixa um marcador `uncertain` no diário: o comando pode ter
feito efeito, e nunca é repetido às cegas.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Awaitable, Callable

from ..contracts.worker.protocol import FEATURE_COMANDO_REMOTO, Exec, ExecAck, ExecResult
from ..devices.sdk import ambiente_dos_filhos
from ..security.redaction import cortar_saida, linha_de_comando_suspeita, redact
from .diario import DiarioDoAgente

log = logging.getLogger("poc.worker")

ARQUIVO_DE_COMANDOS = "diario-de-comandos.json"
#: Quanto de cada fluxo se lê antes de descartar o resto (a saída é redigida inteira ANTES de cortar; sem teto o
#: filho poderia encher a memória do agente).
BRUTO_MAX_BYTES = 2 * 1024 * 1024
#: Quanto esperar o `taskkill`/`kill` fechar a árvore.
ESPERA_DA_MORTE_S = 10.0
_WINDOWS = os.name == "nt"
_UTF8_NO_POWERSHELL = "[Console]::OutputEncoding=[Text.UTF8Encoding]::new(); "


class DiarioDeComandos(DiarioDoAgente):
    """O mesmo diário do agente, em arquivo próprio e chaveado por `exec_id`. Diferença: o desfecho CONFIRMADO perde
    stdout/stderr (a reentrega só precisa saber o estado), para o arquivo regravado a cada mudança não crescer com
    64 saídas de 64 KiB."""

    def __init__(self, work_dir: str | Path):
        super().__init__(work_dir, ARQUIVO_DE_COMANDOS)

    def confirmar(self, command_id: str) -> bool:
        corpo = self.resultados.get(command_id)
        if corpo is None:
            return False
        self.resultados[command_id] = {**corpo, "stdout": "", "stderr": "",
                                       "error": corpo.get("error") or "desfecho já entregue (corpo descartado)"}
        return super().confirmar(command_id)


cortar = cortar_saida   # nome antigo, usado nos testes do agente


def _tratar_saida(bruto: bytes, max_bytes: int, *, passou_do_bruto: bool = False) -> tuple[str, bool]:
    """Decodifica, REDIGE o texto inteiro e só então corta. Saída que passou do teto BRUTO perde a última linha
    incompleta antes da redação: o corte bruto poderia ter partido um `token=…` ao meio, fora do formato que `redact`
    reconhece."""
    if passou_do_bruto:
        bruto = bruto[:bruto.rfind(b"\n") + 1]
    texto = bruto.decode("utf-8", errors="replace")
    return cortar(redact(texto) or "", max_bytes)


async def _ler_fluxo(fluxo: asyncio.StreamReader | None) -> tuple[bytes, bool]:
    """Lê até `BRUTO_MAX_BYTES` e segue DRENANDO o resto (um filho com o pipe cheio trava). `True` = passou do teto."""
    if fluxo is None:
        return b"", False
    partes: list[bytes] = []
    guardado = 0
    passou = False
    while True:
        bloco = await fluxo.read(65536)
        if not bloco:
            break
        if guardado < BRUTO_MAX_BYTES:
            fica = bloco[:BRUTO_MAX_BYTES - guardado]
            partes.append(fica)
            guardado += len(fica)
            passou = passou or len(fica) < len(bloco)
        else:
            passou = True
    return b"".join(partes), passou


async def _matar_arvore(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    if _WINDOWS:
        with contextlib.suppress(Exception):
            k = await asyncio.create_subprocess_exec("taskkill", "/T", "/F", "/PID", str(proc.pid),
                                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                                     env=ambiente_dos_filhos())
            await asyncio.wait_for(k.wait(), ESPERA_DA_MORTE_S)
    else:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGKILL)
    with contextlib.suppress(ProcessLookupError):
        proc.kill()


def argv_do_pedido(msg: Exec) -> list[str]:
    """`argv` direto (sem shell) tem preferência; `linha` vai pelo shell FIXO do sistema."""
    if msg.argv is not None:
        return list(msg.argv)
    assert msg.linha is not None
    if _WINDOWS:
        return ["powershell", "-NoProfile", "-NonInteractive", "-Command", _UTF8_NO_POWERSHELL + msg.linha]
    return ["/bin/sh", "-c", msg.linha]


#: O filho vira líder de um grupo/sessão própria: é o que permite matar a árvore inteira. No Windows são as flags de
#: criação do processo; no Linux, uma sessão nova (`killpg`). `getattr` porque as constantes só existem no Windows.
_FLAGS_DO_FILHO = (getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
                   if _WINDOWS else 0)
_SESSAO_NOVA = not _WINDOWS


class ComandoRemoto:
    """O lado do agente do `remote_exec`. `enviar` é o `_send` do canal de comando; `aceitas` devolve as features que
    o central aceitou NESTA conexão."""

    def __init__(self, work_dir: str | Path, *, habilitado: bool,
                 enviar: Callable[[dict[str, object]], Awaitable[bool]],
                 aceitas: Callable[[], set[str]]) -> None:
        self.habilitado = habilitado
        self.diario = DiarioDeComandos(work_dir)
        self.work_dir = Path(work_dir)
        self._enviar = enviar
        self._aceitas = aceitas
        self._rodando: str | None = None
        self._processo: asyncio.subprocess.Process | None = None
        self._cancelado = False

    # ------------------------------------------------------------------ o que o agente consulta
    def pendentes(self) -> list[dict[str, object]]:
        """O que o central ainda não confirmou, MENOS o que está rodando agora (o marcador de quem roda não é um
        desfecho: reenviá-lo ao reconectar diria `uncertain` de um comando que ainda vive)."""
        return [p for p in self.diario.pendentes() if p.get("exec_id") != self._rodando]

    def confirmar(self, exec_id: str) -> None:
        self.diario.confirmar(exec_id)

    def cancelar(self, exec_id: str) -> None:
        if self._rodando == exec_id:
            self._cancelado = True
            if self._processo is not None:
                asyncio.ensure_future(_matar_arvore(self._processo))

    # ------------------------------------------------------------------ atendimento
    async def _responder(self, resultado: ExecResult, *, guardar: bool) -> None:
        corpo = resultado.model_dump()
        if guardar:
            self.diario.guardar(resultado.exec_id, corpo)
        await self._enviar(corpo)

    async def _recusar(self, msg: Exec, motivo: str, *, guardar: bool) -> None:
        await self._enviar(ExecAck(exec_id=msg.exec_id, recusa=motivo).model_dump())
        await self._responder(ExecResult(exec_id=msg.exec_id, estado="failed", error=motivo), guardar=guardar)

    async def atender(self, msg: Exec) -> None:
        """Nada escapa daqui: toda falha vira `ExecResult` com o motivo."""
        try:
            await self._atender(msg)
        except Exception as exc:  # noqa: BLE001 - o canal de comando não cai por causa de um comando
            log.exception("comando remoto %s", msg.exec_id)
            motivo = (redact(f"falha do agente: {type(exc).__name__}") or "falha do agente")[:300]
            with contextlib.suppress(Exception):
                await self._responder(ExecResult(exec_id=msg.exec_id, estado="uncertain", error=motivo), guardar=True)
        finally:
            if self._rodando == msg.exec_id:
                self._rodando = None
                self._processo = None
                self._cancelado = False

    async def _atender(self, msg: Exec) -> None:
        if not self.habilitado or FEATURE_COMANDO_REMOTO not in self._aceitas():
            await self._recusar(msg, "remote_exec não está habilitado/negociado nesta conexão", guardar=False)
            return
        if self._rodando == msg.exec_id:
            return                                  # a mesma ordem chegando duas vezes, ainda rodando
        anterior = self.diario.desfecho(msg.exec_id)
        if anterior is not None:
            await self._enviar(anterior)            # reentrega: o MESMO desfecho, nunca o comando outra vez
            return
        if self._rodando is not None:
            await self._recusar(msg, "o agente está ocupado com outro comando", guardar=False)
            return
        texto = msg.linha if msg.linha is not None else " ".join(msg.argv or [])
        if linha_de_comando_suspeita(texto):
            await self._recusar(msg, "a linha parece levar credencial; segredo não vai por comando remoto",
                                guardar=True)
            return
        pasta = Path(msg.pasta) if msg.pasta else self.work_dir
        if not pasta.is_dir():
            await self._recusar(msg, "a pasta de trabalho não existe nesta máquina", guardar=True)
            return
        self._rodando = msg.exec_id
        self._cancelado = False
        # O marcador vai ANTES de o processo existir: se o agente cair daqui em diante, ele reaparece como
        # `uncertain` no próximo `hello`, e ninguém repete o comando às cegas.
        self.diario.guardar(msg.exec_id, ExecResult(
            exec_id=msg.exec_id, estado="uncertain",
            error="o agente reiniciou durante o comando; não se sabe se terminou").model_dump())
        inicio = time.perf_counter()
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv_do_pedido(msg), cwd=str(pasta), env=ambiente_dos_filhos(),
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=_FLAGS_DO_FILHO, start_new_session=_SESSAO_NOVA)
        except (OSError, ValueError) as exc:
            await self._responder(ExecResult(
                exec_id=msg.exec_id, estado="failed", error=(redact(f"não foi possível iniciar: {exc}") or "")[:300],
                duration_ms=int((time.perf_counter() - inicio) * 1000)), guardar=True)
            return
        self._processo = proc
        await self._enviar(ExecAck(exec_id=msg.exec_id).model_dump())
        leitura = asyncio.gather(_ler_fluxo(proc.stdout), _ler_fluxo(proc.stderr))
        estourou = False
        try:
            await asyncio.wait_for(proc.wait(), timeout=msg.timeout_s)
        except asyncio.TimeoutError:
            estourou = True
            await _matar_arvore(proc)
        if self._cancelado and not estourou:
            await _matar_arvore(proc)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(proc.wait(), ESPERA_DA_MORTE_S)
        try:
            (bruto_out, passou_out), (bruto_err, passou_err) = await asyncio.wait_for(leitura, ESPERA_DA_MORTE_S)
        except Exception:  # noqa: BLE001 - um neto que segurou o pipe: segue com o que houver
            leitura.cancel()
            bruto_out = bruto_err = b""
            passou_out = passou_err = False
        saida, corte_out = _tratar_saida(bruto_out, msg.saida_max_bytes, passou_do_bruto=passou_out)
        erro, corte_err = _tratar_saida(bruto_err, msg.saida_max_bytes, passou_do_bruto=passou_err)
        codigo = proc.returncode
        encerrado_de_fora = estourou or self._cancelado
        if estourou:
            estado = "timed_out"
        elif self._cancelado:
            estado = "cancelled"
        else:
            estado = "succeeded" if codigo == 0 else "failed"
        await self._responder(ExecResult(
            exec_id=msg.exec_id, estado=estado, exit_code=None if encerrado_de_fora else codigo,
            stdout=saida, stderr=erro, truncated=corte_out or corte_err or passou_out or passou_err,
            duration_ms=int((time.perf_counter() - inicio) * 1000)), guardar=True)
