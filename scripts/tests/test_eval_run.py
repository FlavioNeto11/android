"""scripts/eval_run.py é [P] mesmo com provedor simulado (POST no backend vivo + adb nos aparelhos). Sem --yes ele
tem de parar ANTES de abrir qualquer conexão ou processo: aqui o cliente HTTP e o subprocess explodem se tocados."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("eval_run", ROOT / "scripts" / "eval_run.py")
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)  # type: ignore[union-attr]


def _proibido(*_a: Any, **_k: Any) -> Any:
    raise AssertionError("sem --yes nada pode falar com backend ou adb")


def test_sem_yes_so_imprime_o_plano(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(mod.httpx, "Client", _proibido)
    monkeypatch.setattr(mod.subprocess, "run", _proibido)
    assert mod.main(["--label", "teste", "--cases", "msg-qa001"]) == 2
    saida = capsys.readouterr().out
    assert "PLANO (nada foi executado" in saida and "POST /api/runs" in saida and "msg-qa001" in saida
    assert "android-01" in saida


def test_plano_lista_os_efeitos_de_cada_caso() -> None:
    spec = {"defaults": {"instances": ["android-01"], "timeout_s": 600}}
    casos = [{"id": "a", "expect": "succeeded"},
             {"id": "b", "expect": "uncertain", "flags": {"send_fail": 1}, "relogin_after": True, "timeout_s": 90}]
    ns = mod.argparse.Namespace(base="http://127.0.0.1:8000", label="x", instances="")
    texto = mod.plano(casos, spec, ns)
    assert "- a: aparelhos android-01; espera succeeded; prazo 600 s" in texto
    assert "force-stop" in texto and "provision-qa.ps1" in texto and "prazo 90 s" in texto


def test_ajuda_diz_o_que_o_yes_libera(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        mod.main(["--help"])
    ajuda = capsys.readouterr().out
    assert "backend vivo" in ajuda and "adb" in ajuda and "só imprime o plano" in ajuda
