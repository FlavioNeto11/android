"""31.238: o rejulgamento do "sim" com efeito é dispensado por app, quando a prova local já comprovou o efeito.

Medido em 07/10 (`GET /api/usage?days=7` e `ai_calls` em `mode=ro`): 119 rejulgamentos `sim_com_efeito` em 7 dias, 0
discordâncias (US$ 1,59); o QA Messenger tinha 115 e o Instagram 4. A guarda da orquestradora: a dispensa vale POR APP,
só para o app com pelo menos `ai.rejulgamento_dispensa_minimo` (30) rejulgamentos na janela (7 dias) e nenhuma
discordância, e só quando o "sim" veio da PROVA LOCAL do app (o marcador do catálogo, 31.57, ou o `sent_text`, 31.26),
não do juiz barato. Dispensado, fica na trilha (`kind = rejulgamento_dispensado`): é o contador da próxima medida.

O que estes testes protegem:
* a régua do direito (`efeitos_rejulgados_do_app`): só `sim_com_efeito`, só o app pedido, discordância = veredito
  diferente de "yes", só dentro da janela; o `nivel` não conta;
* app qualificado e "sim" da prova local: sem rejulgamento, com a trilha; app com menos que o mínimo ou com discordância:
  rejulga; chave desligada: rejulga; leitura que falha: rejulga;
* o "sim" do juiz barato (sem prova local) segue rejulgado mesmo no app qualificado; o verify não muda.

Nível de prova: `simulated` (banco de teste e verificador de mentira). `real`: a medida de 07/10 acima.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import app.taskqueue.executor as executor_mod
from app.db import Database
from app.taskqueue.observabilidade import efeitos_rejulgados_do_app

from .test_dm_verificador import CONTEUDO, _conversa
from .test_sent_text_dispensa_juiz import _envio_com_nivel, _Juizes, _pronto, _verificar

AGORA = "2026-10-07T10:00:00.000Z"


def _linha(db: Database, run: str, ts: str, escalate: str, verdict: str, *, ok: int = 1) -> None:
    db.execute("INSERT INTO ai_calls(ts, run_id, role, model, motivo, escalate, verdict, ok)"
               " VALUES (?,?,?,?,?,?,?,?)", (ts, run, "verify", "m", "rejulgamento", escalate, verdict, ok))


def test_a_regua_do_direito(tmp_path: Path) -> None:
    db = Database(tmp_path / "t.sqlite3")
    db.migrate()
    for run, app in (("r-qa", "qa-messenger"), ("r-ig", "instagram")):
        db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, instance_ids, app_ids, created_at)"
                   " VALUES (?,?,?,?,?,?,?,?)", (run, f"k-{run}", "x", "execute", "completed", "[]", f'["{app}"]', AGORA))
    for _ in range(3):
        _linha(db, "r-qa", "2026-10-06T00:00:00.000Z", "sim_com_efeito", "yes")
    _linha(db, "r-qa", "2026-10-06T00:00:00.000Z", "nivel", "yes")             # outro tipo: não conta
    _linha(db, "r-qa", "2026-09-01T00:00:00.000Z", "sim_com_efeito", "no")     # fora da janela
    _linha(db, "r-qa", "2026-10-06T00:00:00.000Z", "sim_com_efeito", "no", ok=0)   # chamada que falhou
    _linha(db, "r-ig", "2026-10-06T00:00:00.000Z", "sim_com_efeito", "uncertain")
    desde = "2026-09-30T00:00:00.000Z"
    assert efeitos_rejulgados_do_app(db, "qa-messenger", desde=desde) == (3, 0)
    assert efeitos_rejulgados_do_app(db, "instagram", desde=desde) == (1, 1)
    assert efeitos_rejulgados_do_app(db, "outro", desde=desde) == (0, 0)


class _Trilha:
    def __init__(self) -> None:
        self.eventos: list[dict[str, Any]] = []

    def emit(self, tipo: str, texto: str, **kw: Any) -> None:
        self.eventos.append({"tipo": tipo, "texto": texto, **kw})


def _com_direito(ex: Any, monkeypatch: pytest.MonkeyPatch, conta: tuple[int, int] | Exception) -> _Trilha:
    trilha = _Trilha()
    db = SimpleNamespace(scalar=lambda *a, **k: '["instagram"]')
    ex.repo = SimpleNamespace(decision=lambda *a, **k: None, db=db, bus=trilha)

    def efeitos(_db: Any, app: str, *, desde: str) -> tuple[int, int]:
        assert app == "instagram" and desde
        if isinstance(conta, Exception):
            raise conta
        return conta

    monkeypatch.setattr(executor_mod, "efeitos_rejulgados_do_app", efeitos)
    return trilha


@pytest.mark.parametrize("conta, ligada, rejulga", [
    ((30, 0), True, False),                  # app qualificado: dispensa
    ((29, 0), True, True),                   # menos que o mínimo
    ((119, 1), True, True),                  # alguma discordância
    ((30, 0), False, True),                  # chave desligada
    (RuntimeError("banco"), True, True),     # leitura que falha
])
async def test_o_sim_da_prova_local(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                    conta: tuple[int, int] | Exception, ligada: bool, rejulga: bool) -> None:
    juizes = _Juizes()
    ex = _pronto(tmp_path, juizes)
    ex.cfg.file.ai.rejulgamento_dispensado_por_app = ligada
    trilha = _com_direito(ex, monkeypatch, conta)
    ok, texto = await _verificar(ex, _envio_com_nivel())
    assert ok, texto
    assert (juizes.barato, juizes.escalonamento) == (0, int(rejulga))      # o barato já era dispensado (31.26)
    dispensas = [e for e in trilha.eventos if (e.get("data") or {}).get("kind") == "rejulgamento_dispensado"]
    assert len(dispensas) == int(not rejulga)
    if dispensas:
        assert dispensas[0]["data"]["app"] == "instagram" and "31.238" in dispensas[0]["texto"]


async def test_o_sim_do_juiz_barato_segue_rejulgado(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    juizes = _Juizes()
    ex = _pronto(tmp_path, juizes, [_conversa(no_campo=CONTEUDO)])           # a árvore não prova: o barato julga
    trilha = _com_direito(ex, monkeypatch, (500, 0))
    await _verificar(ex, _envio_com_nivel())
    assert juizes.barato >= 1 and juizes.escalonamento >= 1
    assert not trilha.eventos
