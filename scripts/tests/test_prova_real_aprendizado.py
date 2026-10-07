"""`scripts/prova-real-aprendizado.py` (31.268): o roteiro de prova real do aprendizado, num banco sintético.

Prova `simulated`: banco SQLite temporário migrado, com uma operação montada aqui e o ancestral do git trocado por uma
função. Confere: o item fora do central sai `nao_no_ar`; o `presente` vai a `resultados` como `real`; o `divergente`
vai a `divergencias` e nunca a `resultados`; o `ausente` e o `sem_caso` vão a `pendentes` (`not_run`); o commit no ar
sai da linha do tempo do CHANGELOG; um JSON por item; o usuário da conta nunca sai na saída; o banco abre só para
leitura.
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.db import Database  # noqa: E402

_spec = importlib.util.spec_from_file_location("prova_real_aprendizado", ROOT / "scripts" / "prova-real-aprendizado.py")
prova = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = prova          # o @dataclass do script procura o módulo em sys.modules
_spec.loader.exec_module(prova)  # type: ignore[union-attr]

TS = "2026-10-08T10:00:00.000Z"
OP = "op-1"
CONTA = "conta.inventada"                # o usuário da conta da persona: nunca pode sair na saída


def _banco(tmp_path: Path) -> Path:
    caminho = tmp_path / "poc.sqlite3"
    db = Database(caminho)
    db.migrate()
    db.close()
    w = sqlite3.connect(caminho)
    x = w.execute
    x("INSERT INTO operacoes(id, command, app_id, acao_final, max_usd, status, idempotency_key, corpo_sha256,"
      " created_at, updated_at, parametros) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
      (OP, "c", "instagram", "executar", 1.0, "concluida", "k-op", "h", TS, TS, json.dumps({"username": "x"})))
    x("INSERT INTO profile_accounts(id, profile_id, app_id, handle, created_at, updated_at) VALUES (?,?,?,?,?,?)",
      ("pa-1", "pf-1", "instagram", CONTA, TS, TS))
    for n in (1, 2):
        run = f"r-{n}"
        x("INSERT INTO runs(id, idempotency_key, command, mode, status, instance_ids, created_at, operacao_id)"
          " VALUES (?,?,?,?,?,?,?,?)", (run, f"k{n}", "c", "execute", "completed", '["android-01"]', TS, OP))
        x("INSERT INTO objectives(id, run_id, instance_id, status, plan_version, profile_id) VALUES (?,?,?,?,1,?)",
          (f"{run}:o", run, "android-01", "completed", "pf-1"))
        # 31.237: o 1º plano grava o cache, o irmão lê
        x("INSERT INTO ai_calls(ts, run_id, role, model, motivo, cache_read, cache_write, ok) VALUES (?,?,?,?,?,?,?,1)",
          (f"2026-10-08T10:00:0{n}.000Z", run, "plan", "m", "plano", 0 if n == 1 else 900, 900 if n == 1 else 0))
    x("INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal, postcondition,"
      " timeout_s, max_attempts, status, side_effect, capability) VALUES (?,?,?,?,1,1,'comment','t','g','{}',60,3,"
      "'succeeded',1,'CREATE_COMMENT')", ("r-1:s1", "r-1", "r-1:o", "android-01"))
    # 31.232: uma decisão de commit no forte com imagem sem motivo (divergente)
    x("INSERT INTO ai_calls(ts, run_id, step_id, role, model, tier, with_image, image_reason, escalate, ok)"
      " VALUES (?,?,?,?,?,?,?,?,?,1)", (TS, "r-1", "r-1:s1", "decide", "forte", 1, 1, "primeira_julgada", "efeito"))
    # 31.239: o comentário comprovado pela árvore (presente)
    x("INSERT INTO events(ts, kind, message, run_id, step_id) VALUES (?,?,?,?,?)",
      (TS, "decision", "android-01 · t: comentário comprovado pela árvore local; o primeiro julgamento foi dispensado",
       "r-1", "r-1:s1"))
    # 31.243: o usuário da conta num evento (divergente) — e nunca na saída
    x("INSERT INTO events(ts, kind, message, run_id) VALUES (?,?,?,?)", (TS, "log", f"entrou como @{CONTA}", "r-2"))
    # 31.242: uma nota mascarada (presente)
    x("INSERT INTO evidence(run_id, instance_id, ts, kind, note) VALUES (?,?,?,?,?)",
      ("r-1", "android-01", TS, "screenshot", "comentário de {conta_instagram_usuario} visto"))
    w.commit()
    w.close()
    return caminho


def _ro(caminho: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{caminho.as_posix()}?mode=ro", uri=True)


def test_cada_achado_vai_ao_seu_lugar_e_o_banco_e_so_leitura(tmp_path: Path) -> None:
    c = _ro(_banco(tmp_path))
    fora = prova.ITENS["31.231"].commit
    saida = prova.marcar(c, OP, "abc12345" * 5, ancestral=lambda item, _no_ar: item != fora,
                         agora="2026-10-08T11:00Z", maquina="central")
    achados = {k: v["achado"] for k, v in saida["por_item"].items()}
    assert achados["31.231"] == "nao_no_ar"
    assert achados["31.237"] == "presente" and achados["31.239"] == "presente" and achados["31.242"] == "presente"
    assert achados["31.232"] == "divergente" and achados["31.243"] == "divergente"
    assert achados["31.250"] == "sem_caso" and achados["31.262"] == "sem_caso"
    assert achados["31.236"] == "presente"                                  # parâmetro fixo, nada em needs_input
    reais = {i["id"] for g in saida["resultados"] for i in g["items"]}
    assert reais == {k for k, v in achados.items() if v == "presente"}
    assert all(i["proof"] == "real" and "central abc12345, operação op-1" in i["evidence"]
               for g in saida["resultados"] for i in g["items"])
    assert {d["id"] for d in saida["divergencias"]} == {"31.232", "31.243"}
    assert all(p["proof"] == "not_run" for p in saida["pendentes"])
    assert CONTA not in json.dumps(saida, ensure_ascii=False)               # só contagens e ids
    with pytest.raises(sqlite3.OperationalError):
        c.execute("DELETE FROM runs")
    c.close()


def test_execucao_parada_com_parametro_fixo_e_divergente_e_sem_comentario_e_sem_caso(tmp_path: Path) -> None:
    caminho = _banco(tmp_path)
    w = sqlite3.connect(caminho)
    w.execute("UPDATE runs SET status='needs_input' WHERE id='r-2'")
    w.execute("DELETE FROM steps")
    w.execute("UPDATE ai_calls SET cache_read=0 WHERE role='plan'")
    w.commit()
    w.close()
    c = _ro(caminho)
    saida = prova.marcar(c, OP, "f" * 40, ancestral=lambda _i, _n: True, agora="x", maquina="m")
    achados = {k: v["achado"] for k, v in saida["por_item"].items()}
    assert (achados["31.236"], achados["31.237"], achados["31.239"]) == ("divergente", "divergente", "sem_caso")
    c.close()


def test_o_commit_no_ar_sai_da_linha_do_tempo_do_changelog() -> None:
    texto = "\n".join([
        "## 2026-10-08 — Deploy 61 (x)", "", "- **Implantado** às 18:10Z: central em `bbbbbbb1234`, sem migração.",
        "## 2026-10-08 — 31.250: outra coisa", "- **Implantado** às 19:00Z: central em `ccccccc`  (não é deploy)",
        "## 2026-10-07 — Deploy 60 (y)", "- **Implantado** às 15:40Z: central em `aaaaaaa1234`, migração 128."])
    linha = prova.deploys(texto)
    assert [(d[1], d[2]) for d in linha] == [("60", "aaaaaaa1234"), ("61", "bbbbbbb1234")]
    assert prova.commit_na_hora("2026-10-08T12:00:00.000Z", linha) == ("aaaaaaa1234", "deploy 60")
    assert prova.commit_na_hora("2026-10-08T18:30:00.000Z", linha) == ("bbbbbbb1234", "deploy 61")
    assert prova.commit_na_hora("2026-10-07T10:00:00.000Z", linha) is None


def test_um_json_por_item_no_formato_do_aplicar(tmp_path: Path) -> None:
    c = _ro(_banco(tmp_path))
    saida = prova.marcar(c, OP, "f" * 40, ancestral=lambda _i, _n: True, agora="x", maquina="m")
    c.close()
    feitos = prova.gravar_por_item(saida, tmp_path / "saida")
    assert len(feitos) == len(prova.ITENS)
    presente = json.loads((tmp_path / "saida" / "aprendizado-real-31-239.json").read_text(encoding="utf-8"))
    assert presente["resultados"][0]["items"][0]["id"] == "31.239"
    divergente = json.loads((tmp_path / "saida" / "aprendizado-real-31-243.json").read_text(encoding="utf-8"))
    assert divergente["resultados"] == [] and divergente["divergencias"][0]["id"] == "31.243"
    sem = json.loads((tmp_path / "saida" / "aprendizado-real-31-262.json").read_text(encoding="utf-8"))
    assert sem["resultados"] == [] and sem["pendentes"][0]["achado"] == "sem_caso"


def test_main_com_commit_dado_imprime_a_tabela(tmp_path: Path, capsys: pytest.CaptureFixture[str],
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(prova, "no_ar", lambda *_a, **_k: False)
    assert prova.main(["--operacao", OP, "--banco", str(_banco(tmp_path)), "--commit", "d" * 40, "--tabela"]) == 0
    saida = capsys.readouterr().out
    assert "op-1" in saida and "nao_no_ar" in saida and CONTA not in saida
    assert prova.main(["--operacao", "op-nao-existe", "--banco", str(tmp_path / "poc.sqlite3"), "--commit", "d"]) == 2


def test_a_saude_so_aceita_http() -> None:
    with pytest.raises(ValueError):
        prova.commit_da_saude("file:///etc/passwd")
