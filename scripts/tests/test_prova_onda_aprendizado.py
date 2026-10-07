"""`scripts/prova-onda-aprendizado.py` (31.192): a marcação da prova da onda, num banco sintético.

Prova `simulated`: banco SQLite temporário migrado, com uma operação montada aqui e o ancestral do git trocado por uma
função. Confere: o item cujo commit não está no ar sai `not_run` com o motivo; o que está no ar e foi exercitado sai
`real` com os ids; o que está no ar e não foi exercitado sai `not_run` com o que faltou; as linhas `not_run` nunca vão
a `resultados` (o `aplicar` apagaria a prova simulada); o banco abre só para leitura.
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

_spec = importlib.util.spec_from_file_location("prova_onda_aprendizado", ROOT / "scripts" / "prova-onda-aprendizado.py")
prova = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = prova          # o @dataclass do script procura o módulo em sys.modules
_spec.loader.exec_module(prova)  # type: ignore[union-attr]

TS = "2026-10-07T10:00:00.000Z"
DEPOIS = "2026-10-07T10:30:00.000Z"
OP = "op-1"


def _banco(tmp_path: Path) -> Path:
    caminho = tmp_path / "poc.sqlite3"
    db = Database(caminho)
    db.migrate()
    x = db.execute
    x("INSERT INTO runs(id, idempotency_key, command, mode, status, instance_ids, created_at, operacao_id, finished_at)"
      " VALUES (?,?,?,?,?,?,?,?,?)", ("r-1", "k1", "c", "execute", "completed", '["android-01"]', TS, OP, DEPOIS))
    x("INSERT INTO objectives(id, run_id, instance_id, status, plan_version) VALUES (?,?,?,?,1)",
      ("r-1:o", "r-1", "android-01", "completed"))
    x("INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal, postcondition,"
      " timeout_s, max_attempts, status, driven_by) VALUES (?,?,?,?,1,1,'open_profile_1','t','g','{}',60,3,"
      "'succeeded','recipe')", ("r-1:s1", "r-1", "r-1:o", "android-01"))
    x("INSERT INTO recipes(app_package, app_version, app_signature, variant, step_hash, step_key, version, status,"
      " actions, learned_from_step, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
      ("com.instagram.android", "1", "s", "v", "h", "open_profile_1", 1, "active", "[]", "training:trn-1", TS))
    receita = int(db.scalar("SELECT id FROM recipes WHERE step_hash='h'"))
    x("INSERT INTO attempts(id, step_id, number, status, started_at, strategy, recipe_id) VALUES (?,?,1,?,?,?,?)",
      ("r-1:s1:a1", "r-1:s1", "succeeded", TS, "recipe", receita))
    # 31.178: a favor gravada ANTES de a execução terminar
    x("INSERT INTO learning_evidence(item_ref, stance, origin_ref, run_id, instance_id, simulated, detail, observed_at)"
      " VALUES (?,?,?,?,?,?,?,?)", (f"receita:{receita}", "for", "reproducao:r-1", "r-1", "android-01", 0, "x", TS))
    # 31.179: a leitura do alvo; nenhum fato de pesquisa confirmado por ela
    x("INSERT INTO pedido_observacoes(id, operacao_id, ocorrencia_id, nome, tipo, situacao, valor, capturado_em)"
      " VALUES (?,?,?,?,?,?,?,?)", ("ob-leitura", OP, "r-1", "leitura_do_alvo", "text", "observado", "post", TS))
    x("INSERT INTO pedido_memoria(id, operacao_id, chave, tipo, valor, atualizada_em, origem, confianca, evidencia)"
      " VALUES (?,?,?,?,?,?,?,?,?)", ("m-1", OP, "pesquisa.a", "descoberta", "fato", TS, "pesquisa", "hipotese",
                                      json.dumps(["ob-fonte"])))
    db.close()
    return caminho


def test_marca_real_o_exercitado_e_not_run_com_o_motivo(tmp_path: Path) -> None:
    c = sqlite3.connect(f"file:{_banco(tmp_path).as_posix()}?mode=ro", uri=True)
    no_ar = {prova.ITENS["31.165"][0], prova.ITENS["31.179"][0]}          # o 31.178 fora do central
    saida = prova.marcar(c, OP, "abc12345" * 5, ancestral=lambda item, _no_ar: item in no_ar,
                         agora="2026-10-07T10:40Z", maquina="central")
    (grupo,) = saida["resultados"]
    (real,) = grupo["items"]
    assert real["id"] == "31.165" and real["proof"] == "real"
    assert "central abc12345, operação op-1" in real["evidence"] and "r-1:s1" in real["evidence"]
    pend = {p["id"]: p["motivo"] for p in saida["pendentes"]}
    assert pend["31.178"].startswith("o commit 918bbc85 do item não está no central")
    assert "nenhuma hipótese da pesquisa tinha as âncoras" in pend["31.179"]
    assert all(p["proof"] == "not_run" for p in saida["pendentes"])
    with pytest.raises(sqlite3.OperationalError):                          # só leitura
        c.execute("DELETE FROM runs")
    c.close()


def test_a_evidencia_do_31_178_e_o_fato_confirmado_do_31_179(tmp_path: Path) -> None:
    caminho = _banco(tmp_path)
    w = sqlite3.connect(caminho)
    w.execute("UPDATE pedido_memoria SET confianca='confirmado', evidencia=? WHERE id='m-1'",
              (json.dumps(["ob-fonte", "ob-leitura"]),))
    w.commit()
    w.close()
    c = sqlite3.connect(f"file:{caminho.as_posix()}?mode=ro", uri=True)
    saida = prova.marcar(c, OP, "f" * 40, ancestral=lambda _i, _n: True, agora="x", maquina="m")
    assert {i["id"] for i in saida["resultados"][0]["items"]} == {"31.165", "31.178", "31.179"}
    assert saida["pendentes"] == []
    assert prova.marcar(c, "op-sem-nada", "f" * 40, ancestral=lambda _i, _n: None)["resultados"] == []
    c.close()


def test_a_saude_so_aceita_http() -> None:
    with pytest.raises(ValueError):
        prova.commit_da_saude("file:///etc/passwd")
