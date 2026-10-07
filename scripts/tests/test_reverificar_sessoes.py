"""scripts/reverificar-sessoes.py: só a verificação de leitura pela API, no máximo 2 tentativas por aparelho, o @ redigido
no motivo e a tela de conta bloqueada parando o aparelho na hora. API FALSA (o `_pedir` trocado); nada sai da máquina.

Nível de prova: `simulated`."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
SCRIPT = RAIZ / "scripts" / "reverificar-sessoes.py"
_spec = importlib.util.spec_from_file_location("reverificar_sessoes", SCRIPT)
assert _spec is not None and _spec.loader is not None
rv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rv)


class _ApiFalsa:
    def __init__(self, desfechos: dict[str, list[tuple[str, str | None]]]) -> None:
        self.desfechos = desfechos          # aparelho -> [(estado, motivo)] por tentativa
        self.pedidos: list[tuple[str, str]] = []
        self._cmd: dict[str, tuple[str, str | None]] = {}

    def __call__(self, metodo: str, url: str, timeout: float = 30.0) -> tuple[int, dict[str, object]]:
        self.pedidos.append((metodo, url))
        if metodo == "POST":
            aparelho = url.rsplit("instance_id=", 1)[1]
            cid = f"c-{len(self.pedidos)}"
            self._cmd[cid] = self.desfechos[aparelho].pop(0)
            return 202, {"accepted": True, "command_id": cid, "state": "pending"}
        cid = url.rsplit("/", 1)[1]
        estado, motivo = self._cmd[cid]
        return 200, {"id": cid, "state": estado, "reason": motivo, "finished_at": "2026-10-07T09:30:10Z"}


@pytest.fixture(autouse=True)
def _sem_espera(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rv.time, "sleep", lambda s: None)


def test_so_verifica_repete_a_que_falhou_uma_vez_e_redige_o_arroba(monkeypatch: pytest.MonkeyPatch,
                                                                   tmp_path: Path) -> None:
    api = _ApiFalsa({"android-01": [("succeeded", None)],
                     "android-03": [("failed", "a tela mostra @conta.real e não a esperada"),
                                    ("failed", "UiAutomator sem a árvore")]})
    monkeypatch.setattr(rv, "_pedir", api)
    saida = tmp_path / "r.json"
    codigo = rv.main(["android-01:ig-a:acc-a", "android-03:ig-b:acc-b", "--json", str(saida)])
    assert codigo == 1                                   # android-03 terminou sem comprovar
    posts = [u for m, u in api.pedidos if m == "POST"]
    assert all("/session/verify?instance_id=" in u for u in posts)     # só a verificação de leitura
    assert len(posts) == 3                               # 1 no android-01, 2 no android-03 (o teto)
    dados = json.loads(saida.read_text(encoding="utf-8"))
    assert "@conta.real" not in saida.read_text(encoding="utf-8")
    assert dados[1]["motivo"].startswith("a tela mostra @*** ")


def test_tela_de_conta_bloqueada_para_o_aparelho_sem_segunda_tentativa(monkeypatch: pytest.MonkeyPatch) -> None:
    api = _ApiFalsa({"android-06": [("failed", "Confirm you're human"), ("succeeded", None)]})
    monkeypatch.setattr(rv, "_pedir", api)
    assert rv.main(["android-06:ig-c:acc-c"]) == 1
    assert len([u for m, u in api.pedidos if m == "POST"]) == 1


def test_alvo_sem_os_tres_ids_e_recusado_antes_de_chamar(monkeypatch: pytest.MonkeyPatch) -> None:
    api = _ApiFalsa({})
    monkeypatch.setattr(rv, "_pedir", api)
    assert rv.main(["android-01:ig-a"]) == 2 and api.pedidos == []
