"""Item 5.3 — guarda de relógio e cerca na escrita da etapa (achado #32).

Duas metades do mesmo problema:

1. **O vencimento do lease era escrito com o relógio de QUEM assumiu e lido com o de QUEM pergunta.** Um backend
   adiantado dois minutos enxerga como vencido o lease de uma etapa em plena execução e a adota — o oposto do que
   o lease existe para impedir. A correção é medir o tempo pelo relógio do BANCO, e recusar subir como SEGUNDO
   dono quando o relógio desta máquina está longe do dele.
2. **As escritas da etapa não conferiam posse.** Um dono que ficou lento (pausa longa da VM, partição com o
   banco) e foi adotado continuava gravando o desfecho por cima de quem agora executa.
"""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from app.db import Database
from app.models import StepStatus
from app.state import RelogioDivergente
from app.taskqueue.repository import POSSE_TTL_S, PosseDaEtapaPerdida
from app.util import iso_in, now, parse_iso

from .conftest import Harness, make_config
from .test_posse_de_etapa import _dois_backends, _etapa_pronta

EU = "notebook-lan-01"
OUTRO = "servidor-central"


# --------------------------------------------------------------------------- o relógio do banco
def test_o_vencimento_do_lease_sai_do_relogio_do_banco(tmp_path: Path) -> None:
    """Escrita e leitura do lease passam a usar `Database.agora`, não o relógio desta máquina.

    O teste troca o relógio do BANCO por um instante distante do local: se o código ainda usasse `now_iso()`, o
    vencimento gravado ficaria perto de agora e a conta não fecharia.
    """
    aqui, lah, db = _dois_backends(tmp_path)
    fixo = now() + timedelta(days=1)
    db.agora = lambda: fixo  # type: ignore[method-assign]

    sid = _etapa_pronta(db)
    assert aqui.claim_step(sid) is not None

    venceu_em = parse_iso(db.one("SELECT claim_expires_at FROM steps WHERE id=?", (sid,))["claim_expires_at"])
    assert venceu_em is not None
    assert abs((venceu_em - (fixo + timedelta(seconds=POSSE_TTL_S))).total_seconds()) < 2

    # e a pergunta "isto venceu?" usa o MESMO relógio: com o do banco ainda no futuro, nada venceu
    assert lah.abandoned_steps() == []
    db.execute("UPDATE steps SET claim_expires_at=? WHERE id=?",
               (db.prazo_iso(-1), sid))
    assert [s["id"] for s in lah.abandoned_steps()] == [sid]


def test_desvio_do_relogio_e_zero_no_sqlite(tmp_path: Path) -> None:
    """No SQLite o banco é um arquivo desta máquina: os dois relógios são o mesmo, e a guarda não inventa desvio."""
    cfg = make_config(tmp_path)
    cfg.ensure_dirs()
    db = Database(cfg.db_dsn)
    db.migrate()
    if db.dialect != "sqlite":
        pytest.skip("este teste descreve o SQLite; no PostgreSQL o desvio é medido de verdade")
    assert db.desvio_do_relogio() == 0.0


# --------------------------------------------------------------------------- recusar subir como segundo dono
@pytest.mark.asyncio
async def test_relogio_torto_com_um_backend_so_e_apenas_aviso(tmp_path: Path) -> None:
    """Enquanto ninguém mais hospeda aparelho neste banco, um relógio errado não atropela ninguém — derrubar o
    backend por causa dele seria pior que o problema. Vira `clock_skew` em `/api/health`."""
    h = Harness(tmp_path, 2, owner_id=EU)
    s = await h.boot()
    try:
        s.db.desvio_do_relogio = lambda: 900.0  # type: ignore[method-assign]
        assert s.conferir_relogio() == 900.0
        assert "clock_skew" in {p.code for p in s.health().problems}
    finally:
        await h.state.stop()  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_recusa_subir_como_segundo_dono_com_relogio_divergente(tmp_path: Path) -> None:
    """A partir do momento em que OUTRO backend hospeda aparelhos aqui, o desvio significa adotar etapa viva
    alheia. Aí o certo é recusar subir."""
    h = Harness(tmp_path, 2, owner_id=EU)
    s = await h.boot()
    try:
        s.db.execute("UPDATE instances SET hosted_by=? WHERE id='android-01'", (OUTRO,))
        s.db.desvio_do_relogio = lambda: 900.0  # type: ignore[method-assign]
        with pytest.raises(RelogioDivergente) as exc:
            s.conferir_relogio()
        assert "900" in str(exc.value)

        # dentro do limite, sobe normalmente mesmo com o outro backend presente
        s.db.desvio_do_relogio = lambda: 1.0  # type: ignore[method-assign]
        assert s.conferir_relogio() == 1.0
    finally:
        await h.state.stop()  # type: ignore[union-attr]


# --------------------------------------------------------------------------- cerca na escrita
def test_quem_perdeu_a_posse_nao_escreve_mais_na_etapa(tmp_path: Path) -> None:
    """O cenário do achado, inteiro: A assume, fica lento, o lease vence, B adota — e A tenta gravar o desfecho.

    Antes, `transition_step` era `UPDATE steps ... WHERE id=?`: a escrita de A passava por cima do trabalho de B.
    """
    aqui, lah, db = _dois_backends(tmp_path)
    sid = _etapa_pronta(db)
    aqui.claim_step(sid)
    db.execute("UPDATE steps SET claim_expires_at=? WHERE id=?", (iso_in(-1), sid))
    assert lah.take_over(lah.abandoned_steps()[0]) is True

    with pytest.raises(PosseDaEtapaPerdida) as exc:
        aqui.transition_step(sid, StepStatus.verifying, detail="terminei (mas já não era minha)")
    assert exc.value.dono == "notebook-lan-01"
    assert db.one("SELECT status FROM steps WHERE id=?", (sid,))["status"] == "running", "a escrita passou"

    # quem tem a posse escreve normalmente
    lah.transition_step(sid, StepStatus.verifying)
    lah.transition_step(sid, StepStatus.succeeded, detail="terminei")
    assert db.one("SELECT status FROM steps WHERE id=?", (sid,))["status"] == "succeeded"


def test_a_cerca_nao_atrapalha_etapa_que_nao_esta_em_execucao(tmp_path: Path) -> None:
    """`claimed_by` fica gravado depois que a etapa sai de `running`. Cercar por ele em `ready`/`retry_wait`
    recusaria escrita legítima — por exemplo o cancelamento de uma etapa que voltou para a fila."""
    aqui, lah, db = _dois_backends(tmp_path)
    sid = _etapa_pronta(db)
    aqui.claim_step(sid)
    aqui.transition_step(sid, StepStatus.ready)          # devolvida à fila, com o `claimed_by` de A ainda na linha
    assert db.one("SELECT claimed_by FROM steps WHERE id=?", (sid,))["claimed_by"] == "servidor-central"

    lah.transition_step(sid, StepStatus.cancelled, detail="execução cancelada")
    assert db.one("SELECT status FROM steps WHERE id=?", (sid,))["status"] == "cancelled"


def test_etapa_sem_dono_registrado_continua_escrevivel(tmp_path: Path) -> None:
    """Banco anterior à migração 016: `claimed_by IS NULL` não pode virar escrita recusada."""
    aqui, _, db = _dois_backends(tmp_path)
    sid = _etapa_pronta(db)
    db.execute("UPDATE steps SET status='running', claimed_by=NULL WHERE id=?", (sid,))

    aqui.transition_step(sid, StepStatus.verifying)
    assert db.one("SELECT status FROM steps WHERE id=?", (sid,))["status"] == "verifying"
