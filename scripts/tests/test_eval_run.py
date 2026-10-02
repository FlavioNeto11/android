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


# ------------------------------------------------------------ queda transitória do transporte (17.11, K-045)
class _ClienteQueCai:
    """Falha `quedas` vezes por chamada com o erro dado e depois responde; conta as chamadas e guarda os corpos."""

    def __init__(self, erro: Exception, quedas: int) -> None:
        self.erro, self.restantes, self.chamadas, self.corpos = erro, quedas, [], []

    def _responder(self, metodo: str, url: str, **kw: Any) -> Any:
        self.chamadas.append((metodo, url))
        self.corpos.append(kw.get("json"))
        if self.restantes > 0:
            self.restantes -= 1
            raise self.erro
        return f"resposta:{metodo}:{url}"

    def get(self, url: str, **kw: Any) -> Any:
        return self._responder("get", url, **kw)

    def post(self, url: str, **kw: Any) -> Any:
        return self._responder("post", url, **kw)


@pytest.mark.parametrize("erro", [mod.httpx.RemoteProtocolError("Server disconnected without sending a response"),
                                  mod.httpx.ReadError("conexão reiniciada"), mod.httpx.ConnectError("recusada")])
def test_leitura_repete_a_queda_transitoria_e_devolve_a_resposta(erro: Exception) -> None:
    esperas: list[float] = []
    falso = _ClienteQueCai(erro, quedas=2)
    r = mod.Resistente(falso, tentativas=4, espera_s=1.0, dorme=esperas.append)
    assert r.get("/api/runs/r-1") == "resposta:get:/api/runs/r-1"
    assert len(falso.chamadas) == 3 and esperas == [1.0, 2.0]          # espera crescente, sem dormir de verdade


def test_post_repete_com_o_mesmo_corpo_e_portanto_a_mesma_idempotency_key() -> None:
    corpo = {"command": "x", "idempotency_key": "eval-fixa"}
    falso = _ClienteQueCai(mod.httpx.RemoteProtocolError("caiu"), quedas=1)
    mod.Resistente(falso, dorme=lambda _s: None).post("/api/runs", json=corpo)
    assert falso.corpos == [corpo, corpo]


def test_esgotadas_as_tentativas_a_excecao_original_sobe() -> None:
    falso = _ClienteQueCai(mod.httpx.ReadError("sempre"), quedas=99)
    with pytest.raises(mod.httpx.ReadError):
        mod.Resistente(falso, tentativas=3, dorme=lambda _s: None).get("/api/usage")
    assert len(falso.chamadas) == 3                                     # nem uma a mais


def test_erro_que_nao_e_de_transporte_nao_e_repetido() -> None:
    falso = _ClienteQueCai(ValueError("não é do transporte"), quedas=1)
    with pytest.raises(ValueError):
        mod.Resistente(falso, dorme=lambda _s: None).get("/api/ai")
    assert len(falso.chamadas) == 1


def test_a_bateria_so_se_abre_com_o_cliente_resistente(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fiação: com --yes o cliente que o `main` usa é o `Resistente` (um `httpx.Client` cru voltaria ao K-045)."""
    visto: dict[str, Any] = {}

    class Parar(Exception):
        pass

    class Cru:
        def __init__(self, **kw: Any) -> None:
            visto["cru"] = kw

        def get(self, *_a: Any, **_k: Any) -> Any:
            raise Parar()

    original = mod.Resistente

    def espiao(http: Any, **kw: Any) -> Any:
        visto["embrulhado"] = isinstance(http, Cru)
        return original(http, **kw)

    monkeypatch.setattr(mod.httpx, "Client", Cru)
    monkeypatch.setattr(mod, "Resistente", espiao)
    with pytest.raises(Parar):
        mod.main(["--label", "t", "--cases", "msg-qa001", "--yes"])
    assert visto["embrulhado"] is True
