"""scripts/rodada_qa_pareada.py é [P][T] com `--yes`. Sem opção, só o plano: nenhuma conexão. As pré-checagens e a
leitura são de custo zero, e o veredito segue o aceite da orquestradora (sucesso B ≥ A e p50 do B ≤ 11 s, com 6
válidas por braço)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("rodada_qa_pareada", ROOT / "scripts" / "rodada_qa_pareada.py")
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)  # type: ignore[union-attr]


def _proibido(*_a: Any, **_k: Any) -> Any:
    raise AssertionError("sem opção nada pode falar com o backend")


def test_sem_opcao_so_imprime_o_plano(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(mod.httpx, "Client", _proibido)
    monkeypatch.setattr(mod.eval_run, "main", _proibido)
    assert mod.main(["--rodada", "r1"]) == 2
    saida = capsys.readouterr().out
    assert "PLANO (nada foi executado)" in saida and "android-09" in saida and "16." in saida
    assert "--profile planejador-sonnet" in saida and "qa-par-r1-A" in saida


def test_ordem_abba_e_o_perfil_so_no_braco_b() -> None:
    assert mod.ordem_abba(["x", "y"]) == [("x", "A"), ("x", "B"), ("x", "B"), ("x", "A"),
                                          ("y", "A"), ("y", "B"), ("y", "B"), ("y", "A")]
    assert "--profile" not in mod.argv_do_eval("x", "A", "r1", "android-09")
    assert mod.argv_do_eval("x", "B", "r1", "android-09")[-2:] == ["--profile", "planejador-sonnet"]
    assert mod.argv_do_eval("x", "A", "r1", "android-09")[:2] == ["--label", "qa-par-r1-A"]


def test_repeticoes_repetem_o_bloco_abba_do_caso() -> None:
    assert mod.ordem_abba(["x", "y"], 2) == [("x", b) for b in "ABBAABBA"] + [("y", b) for b in "ABBAABBA"]
    tres = mod.ordem_abba(["a", "b", "c"], 2)
    assert sum(1 for _c, b in tres if b == "A") == sum(1 for _c, b in tres if b == "B") == 12
    assert mod.ordem_abba(["x"]) == mod.ordem_abba(["x"], 1)


def test_percentil_por_posicao() -> None:
    assert mod.percentil([], 50) is None
    assert mod.percentil([3.0, 1.0, 2.0], 50) == 2.0
    assert mod.percentil([1.0, 2.0, 3.0, 4.0], 50) == 2.0
    assert mod.percentil([1.0, 2.0, 3.0, 4.0], 90) == 4.0


def _linhas(braco: str, n: int, *, ok: int, ms: float, sem_plano: int = 0) -> list[dict[str, Any]]:
    linhas = [{"braco": braco, "pass": i < ok, "usd": 0.1, "plan_ms": ms, "plan_model": f"m-{braco}"} for i in range(n)]
    return linhas + [{"braco": braco, "pass": True, "usd": 0.0, "plan_ms": None, "plan_model": None}] * sem_plano


@pytest.mark.parametrize(("a", "b", "decisao"), [
    (_linhas("A", 8, ok=7, ms=14000), _linhas("B", 8, ok=7, ms=9000), "passa"),
    (_linhas("A", 8, ok=8, ms=14000), _linhas("B", 8, ok=7, ms=9000), "nao_passa"),       # sucesso abaixo
    (_linhas("A", 8, ok=7, ms=14000), _linhas("B", 8, ok=8, ms=12000), "nao_passa"),      # p50 acima de 11 s
    (_linhas("A", 8, ok=7, ms=14000), _linhas("B", 5, ok=5, ms=9000, sem_plano=3), "inconclusivo"),
])
def test_veredito(a: list[dict[str, Any]], b: list[dict[str, Any]], decisao: str) -> None:
    v = mod.veredito(a + b)
    assert v["decisao"] == decisao, v["motivo"]


def test_execucao_sem_planejador_sai_da_comparacao_e_fica_contada() -> None:
    v = mod.veredito(_linhas("A", 6, ok=6, ms=10000) + _linhas("B", 6, ok=6, ms=8000, sem_plano=2))
    assert v["bracos"]["B"]["execucoes"] == 8 and v["bracos"]["B"]["validas"] == 6
    assert v["bracos"]["B"]["sem_planejador"] == 2 and v["bracos"]["B"]["modelos"] == ["m-B"]


class _Http:
    """O backend falso das leituras de custo zero."""

    def __init__(self, *, commit: str = "c1", estado: str = "online", casa: frozenset[str] = frozenset(),
                 habilidade: frozenset[str] = frozenset(), uso: dict[str, Any] | None = None) -> None:
        self.commit, self.estado, self.casa, self.uso = commit, estado, casa, uso or {}
        self.habilidade = habilidade

    def get(self, caminho: str, params: dict[str, str] | None = None) -> Any:
        corpo: Any = {"/api/health": {"commit": self.commit}, "/api/instances": [{"id": "android-09", "state": self.estado}],
                      "/api/usage": self.uso.get((params or {}).get("run_id", ""), {"groups": []})}[caminho]
        return _Resposta(corpo)

    def post(self, caminho: str, json: dict[str, Any]) -> Any:
        if caminho == "/api/skills/resolve":
            assert json["instance_ids"] == ["android-09"]
            casou = any(c in json["command"] for c in self.habilidade)
            return _Resposta({"status": "resolved" if casou else "no_match"})
        assert caminho == "/api/flows/match"
        return _Resposta({"flow_id": "f"} if any(c in json["command"] for c in self.casa) else None)


class _Resposta:
    def __init__(self, corpo: Any) -> None:
        self._corpo = corpo

    def json(self) -> Any:
        return self._corpo

    status_code = 200

    def raise_for_status(self) -> None:
        return None


def test_pre_checagens_de_custo_zero() -> None:
    def sempre(_sha: str, _commit: str) -> bool:
        return True

    assert mod.pre_checagens(_Http(), mod.CASOS, "android-09", "e4a597f8", sempre) == []
    problemas = mod.pre_checagens(_Http(estado="stopped", casa=frozenset({"vá até a tela de Perfil"})), mod.CASOS,
                                  "android-09", "e4a597f8", lambda _sha, _commit: False)
    assert any("deploy 7" in p for p in problemas) and any("não está online" in p for p in problemas)
    assert any(p.startswith("abrir-tela casa com fluxo") for p in problemas)
    assert mod.pre_checagens(_Http(), ["nao-existe"], "android-09", None, sempre) == ["nao-existe não está no eval-set.yaml"]


def test_habilidade_que_casa_tira_o_caso_da_rodada() -> None:
    # 03/10 07:37Z: o abrir-tela não casava com fluxo, mas casava com uma habilidade que não compilou, e as 4
    # execuções foram a needs_input sem chamar o planejador. A pré-checagem tem de ver a RESOLVE também.
    problemas = mod.pre_checagens(_Http(habilidade=frozenset({"vá até a tela de Perfil"})), mod.CASOS,
                                  "android-09", None, lambda _sha, _commit: True)
    assert problemas == ["abrir-tela casa com habilidade (resolved): o planejador não seria chamado"]


def test_checar_diz_quando_passou(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(mod.httpx, "Client", lambda **_k: _Http())
    monkeypatch.setattr(mod.eval_run, "main", _proibido)
    assert mod.main(["--checar"]) == 0
    assert "Pré-checagens ok (custo zero): android-09 online" in capsys.readouterr().out


def test_ler_a_rodada_pelo_jsonl_e_pelo_uso(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    arquivo = tmp_path / "eval-results.jsonl"
    linhas = [{"label": "qa-par-r1-A", "case": "abrir-tela", "run_id": "r-a", "pass": True, "usd": 0.2},
              {"label": "qa-par-r1-B", "case": "abrir-tela", "run_id": "r-b", "pass": True, "usd": 0.1},
              {"label": "qa-par-r1-B", "case": "abrir-tela", "skipped": "android-09 não está online"},
              {"label": "outra", "case": "abrir-tela", "run_id": "r-x", "pass": False, "usd": 9}]
    arquivo.write_text("\n".join(json.dumps(x) for x in linhas) + "\n", encoding="utf-8")
    monkeypatch.setattr(mod, "RESULTADOS", arquivo)
    uso = {"r-a": {"groups": [{"role": "plan", "model": "claude-opus-5-5", "avg_ms": 14000}]},
           "r-b": {"groups": [{"role": "decide", "model": "x", "avg_ms": 1}]}}            # servida por fluxo
    r = mod.ler(_Http(uso=uso), "r1")
    assert [(x["braco"], x["plan_ms"]) for x in r["execucoes"]] == [("A", 14000), ("B", None)]
    assert r["bracos"]["B"]["sem_planejador"] == 1 and r["decisao"] == "inconclusivo"
