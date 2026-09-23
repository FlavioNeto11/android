"""Item 5.2 — o teto de chamadas de IA vale para o SISTEMA, não para cada processo (achados #35 e #94).

O defeito: `max_ai_concurrency` era um `asyncio.Condition`. Com dois backends no mesmo banco, limite 3 virava
seis chamadas simultâneas contra o provedor. Aqui a soma é provada com DOIS donos no mesmo banco, que é o menor
tamanho em que o problema existe.

O que NÃO muda, de propósito: `boot_parallelism` continua por processo — o recurso que ele protege é a RAM desta
máquina, e torná-lo global faria duas máquinas de 64 GB esperarem uma pela outra para ligar aparelho.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.db import Database
from app.devices.manager import Limiter
from app.taskqueue.ai_slots import VagasDeIA
from app.util import iso_in

from .conftest import make_config

AQUI = "servidor-central"
LAH = "notebook-lan-01"


def _banco(tmp_path: Path) -> Database:
    cfg = make_config(tmp_path)
    cfg.ensure_dirs()
    db = Database(cfg.db_dsn)      # `db_dsn` e não `db_path`: segue `TEST_DATABASE_URL` quando ela existe
    db.migrate()
    return db


def test_a_soma_dos_dois_backends_nao_passa_do_limite(tmp_path: Path) -> None:
    """O teste que o achado #35 pede: dois donos, limite 2, e a terceira chamada não sai."""
    db = _banco(tmp_path)
    aqui, lah = VagasDeIA(db, holder=AQUI), VagasDeIA(db, holder=LAH)

    assert aqui.tentar(2) == 1
    assert lah.tentar(2) == 2          # a segunda vaga é do OUTRO backend: o teto é compartilhado
    assert aqui.tentar(2) is None, "o limite de 2 deixou passar uma terceira chamada simultânea"
    assert lah.tentar(2) is None
    assert aqui.ocupadas() == 2
    assert aqui.donos() == {AQUI: 1, LAH: 1}


def test_vaga_devolvida_volta_ao_mercado(tmp_path: Path) -> None:
    db = _banco(tmp_path)
    aqui, lah = VagasDeIA(db, holder=AQUI), VagasDeIA(db, holder=LAH)
    slot = aqui.tentar(1)
    assert slot == 1
    assert lah.tentar(1) is None

    aqui.liberar(slot)
    assert lah.tentar(1) == 1          # o outro backend ocupa a vaga que este devolveu
    assert aqui.tentar(1) is None


def test_vaga_de_backend_que_caiu_vence_sozinha(tmp_path: Path) -> None:
    """Sem vencimento, uma vaga presa por um processo morto nunca voltaria: o teto encolheria até zero."""
    db = _banco(tmp_path)
    aqui, lah = VagasDeIA(db, holder=AQUI), VagasDeIA(db, holder=LAH)
    aqui.tentar(1)
    assert lah.tentar(1) is None

    db.execute("UPDATE ai_slots SET expires_at=? WHERE holder=?", (iso_in(-1), AQUI))   # ninguém renova: caiu
    assert lah.tentar(1) == 1
    assert db.one("SELECT holder FROM ai_slots WHERE slot=1")["holder"] == LAH

    # e o dono antigo não apaga o dono novo ao "devolver" a vaga que já não é dele
    aqui.liberar(1)
    assert db.one("SELECT holder FROM ai_slots WHERE slot=1")["holder"] == LAH


def test_renovar_impede_que_a_vaga_seja_tomada(tmp_path: Path) -> None:
    db = _banco(tmp_path)
    aqui, lah = VagasDeIA(db, holder=AQUI), VagasDeIA(db, holder=LAH)
    aqui.tentar(1)
    db.execute("UPDATE ai_slots SET expires_at=? WHERE holder=?", (iso_in(-1), AQUI))

    assert aqui.renovar() == 1         # o processo está vivo e diz isso a tempo
    assert lah.tentar(1) is None


def test_partida_solta_as_vagas_que_ficaram_marcadas_como_minhas(tmp_path: Path) -> None:
    """Reinício com o MESMO `owner_id`: sem isto o backend encontraria as próprias vagas ocupadas e o teto do
    sistema ficaria menor por dois minutos a cada reinício."""
    db = _banco(tmp_path)
    caiu = VagasDeIA(db, holder=AQUI)
    caiu.tentar(2)
    caiu.tentar(2)

    voltou = VagasDeIA(db, holder=AQUI)          # processo novo, mesma identidade
    assert voltou.soltar_todas() == 2
    assert voltou.ocupadas() == 0
    assert VagasDeIA(db, holder=LAH).tentar(2) is not None


def test_limite_maior_cria_vaga_nova_sob_demanda(tmp_path: Path) -> None:
    """`max_ai_concurrency` é ajuste do painel: as linhas nascem quando o limite sobe, sem migração."""
    db = _banco(tmp_path)
    v = VagasDeIA(db, holder=AQUI)
    assert v.tentar(1) == 1
    assert v.tentar(1) is None
    assert v.tentar(3) == 2
    assert v.tentar(3) == 3
    assert v.tentar(3) is None


@pytest.mark.asyncio
async def test_dois_limiters_no_mesmo_banco_nao_passam_do_teto(tmp_path: Path) -> None:
    """O caminho de verdade: dois `Limiter` (um por backend) com limite 1 cada.

    Antes, cada processo tinha o seu semáforo e os dois entravam juntos — duas chamadas pagas onde o operador
    pediu uma. Agora o segundo espera no lease, e a prova é o pico observado.
    """
    db = _banco(tmp_path)
    dentro = 0
    pico = 0

    async def chamada(lim: Limiter) -> None:
        nonlocal dentro, pico
        async with lim:
            dentro += 1
            pico = max(pico, dentro)
            await asyncio.sleep(0.05)
            dentro -= 1

    a = Limiter(1, vagas=VagasDeIA(db, holder=AQUI, poll_s=0.01))
    b = Limiter(1, vagas=VagasDeIA(db, holder=LAH, poll_s=0.01))
    await asyncio.gather(chamada(a), chamada(b), chamada(a), chamada(b))

    assert pico == 1, f"{pico} chamadas simultâneas com o teto do sistema em 1"
    assert db.scalar("SELECT COUNT(*) FROM ai_slots WHERE holder IS NOT NULL") == 0, "vaga não devolvida no fim"


@pytest.mark.asyncio
async def test_boot_parallelism_continua_por_processo(tmp_path: Path) -> None:
    """A metade que NÃO muda: o `Limiter` sem `vagas` não toca no banco — é a RAM desta máquina que ele protege."""
    db = _banco(tmp_path)
    local = Limiter(2)
    async with local:
        assert local.active == 1
    assert db.scalar("SELECT COUNT(*) FROM ai_slots") == 0
