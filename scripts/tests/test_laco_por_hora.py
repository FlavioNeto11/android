"""31.320: `scripts/laco-por-hora.py` lê os logs do backend e os despejos do vigia (só leitura) e devolve o laço parado por hora e por período.
Nível de prova: `simulated` (logs escritos aqui; nenhum backend, banco ou aparelho)."""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "laco-por-hora.py"


def _modulo() -> ModuleType:
    spec = importlib.util.spec_from_file_location("laco_por_hora", SCRIPT)
    assert spec is not None and spec.loader is not None
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m                    # o `@dataclass` do script resolve as anotações por `sys.modules`
    spec.loader.exec_module(m)
    return m


def _linha(ts: str, logger: str, msg: str) -> str:
    return '{"ts":"%s","level":"WARNING","logger":"%s","msg":%s}\n' % (ts, logger, repr(msg))


def _logs(pasta: Path) -> None:
    # as linhas do log estão em UTC-3: 15:06 local é 18:06Z
    (pasta / "backend.log").write_text(
        _linha("2026-10-10 15:06:33,830", "poc.vigia", "laço de eventos sem batida há 10.6 s (laço); pilha de todas as threads em x.txt")
        + _linha("2026-10-10 15:06:36,843", "poc.vigia", "laço de eventos voltou a bater depois de 13.6 s parado (1 despejo(s) de pilha)")
        + _linha("2026-10-10 15:31:31,879", "poc.vigia", "laço de eventos sem batida há 10.7 s (laço); pilha de todas as threads em y.txt")
        + _linha("2026-10-10 15:31:32,911", "poc.vigia", "laço de eventos voltou a bater depois de 12.5 s parado (1 despejo(s) de pilha)")
        + _linha("2026-10-10 17:00:01,000", "poc.db", "consulta síncrona no laço de eventos levou 2.3 s (servico.py:48 em laco): o laço ficou parado")
        + _linha("2026-10-10 17:00:02,000", "poc.db", "a trava de escrita do banco ficou presa 3.4 s por ThreadPoolExecutor-0_1 (state.py:1860 em _retencao): divida")
        + _linha("2026-10-10 17:00:03,000", "poc.outro", "laço de eventos voltou a bater depois de 99.0 s parado")      # outro logger: ignorado
        + "linha que não é do log\n", encoding="utf-8")
    (pasta / "backend.log.2026-10-09").write_text(
        _linha("2026-10-09 23:10:00,000", "poc.vigia", "laço de eventos voltou a bater depois de 30.0 s parado (1 despejo(s) de pilha)"),
        encoding="utf-8")
    (pasta / "laco-travado-20261010T180633Z-1.txt").write_text("laço de eventos sem batida há 10.3 s (laço); 20261010T180633Z\n\nThread 0x1\n", encoding="utf-8")
    (pasta / "laco-travado-20261009T080000Z-1.txt").write_text("laço de eventos sem batida há 14.0 s (laço); x\n", encoding="utf-8")


def test_a_hora_o_maior_parado_e_o_periodo(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _logs(tmp_path)
    rc = _modulo().main(["--logs", str(tmp_path), "--fuso-log", "-3", "--marco", "71=2026-10-10T18:35:00Z"])
    saida = capsys.readouterr().out
    assert rc == 0
    # 18h UTC: dois episódios (13,6 s e 12,5 s) e o despejo da mesma hora NÃO soma de novo (já há "voltou a bater")
    assert "| 2026-10-10 18h | 2 | 13.6 | 26.1 |" in saida
    # 02h UTC de 10/10 (23h local de 9/10): o log do dia anterior entra, 30 s
    assert "| 2026-10-10 02h | 1 | 30.0 | 30.0 |" in saida
    # o despejo de 9/10 08h UTC não tem "voltou a bater" nos logs: entra como piso, com o atraso visto no despejo
    assert "| 2026-10-09 08h | 1 | 14.0 | 14.0 |" in saida
    # a consulta lenta e a posse da 20h UTC (17h local): contadas por hora e por chamador
    assert "| 2026-10-10 20h | 0 | 0.0 | 0.0 | 1 | 2.3 | 1 | 3.4 |" in saida
    assert "1× servico.py:48 em laco" in saida and "ThreadPoolExecutor-0_1 (state.py:1860 em _retencao)" in saida
    # outro logger e linha estranha não contam; o período "antes" vai ao marco 71
    assert "99.0" not in saida
    assert "| antes |" in saida and "| 71 |" in saida


def test_sem_logs_diz_que_nao_ha_nada_e_pasta_inexistente_falha(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    m = _modulo()
    assert m.main(["--logs", str(tmp_path)]) == 0
    assert "Nada nos logs" in capsys.readouterr().out
    assert m.main(["--logs", str(tmp_path / "nao-existe")]) == 2


def test_marco_invalido_e_recusado(tmp_path: Path) -> None:
    assert _modulo().main(["--logs", str(tmp_path), "--marco", "sem-igual"]) == 2


def test_so_le_nao_altera_a_pasta_de_logs(tmp_path: Path) -> None:
    _logs(tmp_path)
    antes = {p.name: (p.stat().st_size, os.stat(p).st_mtime_ns) for p in tmp_path.iterdir()}
    _modulo().main(["--logs", str(tmp_path), "--fuso-log", "-3"])
    depois = {p.name: (p.stat().st_size, os.stat(p).st_mtime_ns) for p in tmp_path.iterdir()}
    assert antes == depois
