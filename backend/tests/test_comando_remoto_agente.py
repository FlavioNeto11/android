"""Comando remoto, lado do agente (`remote_exec`, 29.154): execução, limites, redação e recusas.

Prova SIMULADA: o canal é uma lista (`enviadas`) e os comandos são `python -c` locais e inofensivos. Não prova o
agente real no notebook (isso é `not_run` até o deploy, o procedimento escrito e o sim do dono para ligar).
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from app.contracts.worker.protocol import FEATURE_COMANDO_REMOTO, Exec
from app.worker.comando import ComandoRemoto, cortar

PY = sys.executable


class Canal:
    def __init__(self) -> None:
        self.enviadas: list[dict[str, Any]] = []

    async def __call__(self, payload: dict[str, Any]) -> bool:
        self.enviadas.append(payload)
        return True

    def resultado(self, exec_id: str) -> dict[str, Any]:
        achados = [m for m in self.enviadas if m["type"] == "exec_result" and m["exec_id"] == exec_id]
        assert achados, self.enviadas
        return achados[-1]

    def tipos(self) -> list[str]:
        return [m["type"] for m in self.enviadas]


def novo(tmp_path: Path, *, habilitado: bool = True, aceitas: set[str] | None = None) -> tuple[ComandoRemoto, Canal]:
    canal = Canal()
    aceitas = {FEATURE_COMANDO_REMOTO} if aceitas is None else aceitas
    return ComandoRemoto(tmp_path, habilitado=habilitado, enviar=canal, aceitas=lambda: aceitas), canal


def pedido(exec_id: str = "exec-0001", *, codigo: str = "print('oi')", timeout_s: float = 20.0, **extra: Any) -> Exec:
    return Exec(exec_id=exec_id, argv=[PY, "-c", codigo], timeout_s=timeout_s, **extra)


async def test_sucesso_devolve_saida_codigo_e_duracao(tmp_path: Path) -> None:
    cmd, canal = novo(tmp_path)
    await cmd.atender(pedido(codigo="import sys; print('oi'); print('erro', file=sys.stderr)"))
    r = canal.resultado("exec-0001")
    assert r["estado"] == "succeeded" and r["exit_code"] == 0
    assert r["stdout"].strip() == "oi" and r["stderr"].strip() == "erro"
    assert r["duration_ms"] >= 0 and r["truncated"] is False
    assert canal.tipos()[0] == "exec_ack"


async def test_codigo_de_saida_diferente_de_zero_e_failed(tmp_path: Path) -> None:
    cmd, canal = novo(tmp_path)
    await cmd.atender(pedido(codigo="import sys; sys.exit(3)"))
    r = canal.resultado("exec-0001")
    assert r["estado"] == "failed" and r["exit_code"] == 3


async def test_estouro_de_prazo_mata_a_arvore_e_diz_timed_out(tmp_path: Path) -> None:
    cmd, canal = novo(tmp_path)
    neto = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); time.sleep(60)"
    t0 = time.perf_counter()
    await cmd.atender(pedido(codigo=neto, timeout_s=1.0))
    assert time.perf_counter() - t0 < 20
    r = canal.resultado("exec-0001")
    assert r["estado"] == "timed_out" and r["exit_code"] is None


async def test_cancelamento_mata_e_diz_cancelled(tmp_path: Path) -> None:
    cmd, canal = novo(tmp_path)
    tarefa = asyncio.create_task(cmd.atender(pedido(codigo="import time; time.sleep(60)")))
    for _ in range(100):
        if "exec_ack" in canal.tipos():
            break
        await asyncio.sleep(0.05)
    cmd.cancelar("exec-0001")
    await asyncio.wait_for(tarefa, 20)
    assert canal.resultado("exec-0001")["estado"] == "cancelled"


async def test_linha_com_credencial_e_recusada_e_nao_roda(tmp_path: Path) -> None:
    cmd, canal = novo(tmp_path)
    marca = tmp_path / "rodou.txt"
    await cmd.atender(Exec(exec_id="exec-0002", linha=f"echo x > {marca} --password hunter2"))
    r = canal.resultado("exec-0002")
    assert r["estado"] == "failed" and "credencial" in r["error"]
    assert not marca.exists()
    # e a linha crua NUNCA vai ao diário em disco
    assert "hunter2" not in (tmp_path / "diario-de-comandos.json").read_text(encoding="utf-8")


async def test_desligado_ou_nao_negociado_recusa_sem_rodar(tmp_path: Path) -> None:
    for habilitado, aceitas in ((False, None), (True, set())):
        cmd, canal = novo(tmp_path, habilitado=habilitado, aceitas=aceitas)
        marca = tmp_path / f"marca-{habilitado}.txt"
        await cmd.atender(pedido(codigo=f"open(r'{marca}', 'w').write('x')"))
        assert canal.resultado("exec-0001")["estado"] == "failed"
        assert not marca.exists()
        assert not cmd.diario.pendentes()                    # recusa por interruptor não vira desfecho guardado


async def test_pasta_inexistente_falha_sem_rodar(tmp_path: Path) -> None:
    cmd, canal = novo(tmp_path)
    await cmd.atender(pedido(pasta=str(tmp_path / "nao-existe")))
    assert "pasta" in canal.resultado("exec-0001")["error"]


async def test_segredo_na_saida_sai_mascarado_antes_do_corte(tmp_path: Path) -> None:
    cmd, canal = novo(tmp_path)
    # Segredo no meio de uma saída que vai ser cortada: redigir DEPOIS do corte deixaria metade dele. O segredo é
    # MONTADO pelo filho (a linha em si não pode parecer credencial, senão o agente a recusa antes de rodar).
    codigo = ("import sys; t = 'API_TO' + 'KEN=abc123segredo'; b = '-----BEGIN RSA PRIVATE' + ' KEY-----'; "
              "e = '-----END RSA PRIVATE' + ' KEY-----'; "
              "sys.stdout.write('a'*40000 + ' ' + t + ' ' + 'b'*40000 + chr(10) + b + chr(10) + 'MIIEabc' + chr(10) + e)")
    await cmd.atender(pedido(codigo=codigo, saida_max_bytes=4096))
    r = canal.resultado("exec-0001")
    assert r["truncated"] is True and len(r["stdout"].encode()) <= 4096 + 100
    assert "abc123segredo" not in r["stdout"] and "MIIEabc" not in r["stdout"]


def test_cortar_guarda_comeco_e_fim() -> None:
    texto, cortou = cortar("começo-" + "x" * 5000 + "-fim", 1024)
    assert cortou and texto.startswith("começo-") and texto.endswith("-fim") and "cortados" in texto
    assert cortar("curto", 1024) == ("curto", False)


async def test_reentrega_devolve_o_mesmo_desfecho_sem_rodar_de_novo(tmp_path: Path) -> None:
    cmd, canal = novo(tmp_path)
    contador = tmp_path / "contador.txt"
    codigo = f"open(r'{contador}', 'a').write('x')"
    await cmd.atender(pedido(codigo=codigo))
    await cmd.atender(pedido(codigo=codigo))
    assert contador.read_text() == "x"
    assert [m for m in canal.enviadas if m["type"] == "exec_result"][0] == canal.resultado("exec-0001")


async def test_um_comando_por_vez(tmp_path: Path) -> None:
    cmd, canal = novo(tmp_path)
    longa = asyncio.create_task(cmd.atender(pedido("exec-longo1", codigo="import time; time.sleep(2)")))
    for _ in range(100):
        if "exec_ack" in canal.tipos():
            break
        await asyncio.sleep(0.05)
    await cmd.atender(pedido("exec-outro1"))
    assert "ocupado" in canal.resultado("exec-outro1")["error"]
    await asyncio.wait_for(longa, 20)
    assert canal.resultado("exec-longo1")["estado"] == "succeeded"


async def test_queda_no_meio_deixa_marcador_uncertain_e_nao_reenvia_o_que_roda(tmp_path: Path) -> None:
    cmd, canal = novo(tmp_path)
    tarefa = asyncio.create_task(cmd.atender(pedido(codigo="import time; time.sleep(2)")))
    for _ in range(100):
        if "exec_ack" in canal.tipos():
            break
        await asyncio.sleep(0.05)
    assert cmd.pendentes() == []                             # rodando agora: o marcador não é desfecho
    # um agente novo lendo o MESMO diário (reinício no meio do comando) vê `uncertain`
    outro, _ = novo(tmp_path)
    assert [p["estado"] for p in outro.pendentes()] == ["uncertain"]
    await asyncio.wait_for(tarefa, 20)


async def test_confirmar_descarta_o_corpo_mas_lembra_do_estado(tmp_path: Path) -> None:
    cmd, canal = novo(tmp_path)
    await cmd.atender(pedido(codigo="print('segredo-nenhum')"))
    cmd.confirmar("exec-0001")
    guardado = cmd.diario.desfecho("exec-0001")
    assert guardado is not None and guardado["estado"] == "succeeded" and guardado["stdout"] == ""
    assert cmd.pendentes() == []


def test_contrato_exige_linha_ou_argv_so_um() -> None:
    with pytest.raises(ValueError):
        Exec(exec_id="exec-0003")
    with pytest.raises(ValueError):
        Exec(exec_id="exec-0003", linha="dir", argv=["dir"])
    with pytest.raises(ValueError):
        Exec(exec_id="exec-0003", linha="dir", timeout_s=9999)


def test_saida_que_passou_do_teto_bruto_perde_a_linha_partida_antes_da_redacao() -> None:
    from app.worker.comando import _tratar_saida
    bruto = b"linha inteira\nAPI_TO" + b"KEN=abc12"          # o teto bruto partiu o par chave=valor ao meio
    texto, _ = _tratar_saida(bruto, 4096, passou_do_bruto=True)
    assert texto == "linha inteira\n"
