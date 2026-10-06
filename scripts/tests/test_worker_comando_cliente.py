"""29.154, fatia 3: scripts/worker-comando.py, o cliente do comando remoto. Um servidor HTTP FALSO (em memória, numa porta efêmera
do loopback) faz o papel da central; nenhum worker, central ou parque de verdade é tocado.

Nível de prova: `simulated`."""
from __future__ import annotations

import argparse
import importlib.util
import io
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("worker_comando", RAIZ / "scripts" / "worker-comando.py")
cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cli)


def _args(**kw) -> argparse.Namespace:
    base = dict(worker="worker-lan-01", linha="Get-Date", argv_json=None, pasta=None, timeout=None, chave=None,
                base="http://central.invalido", espera=None, intervalo=0.0)
    base.update(kw)
    return argparse.Namespace(**base)


class Roteiro:
    """Transporte falso: devolve as respostas na ordem e guarda o que foi pedido."""

    def __init__(self, *respostas: tuple[int, dict]):
        self.respostas, self.pedidos = list(respostas), []

    def __call__(self, metodo, url, corpo, cookie, origem):
        self.pedidos.append((metodo, url, corpo, cookie, origem))
        return self.respostas.pop(0) if len(self.respostas) > 1 else self.respostas[0]


def _rodar(args, transporte, cookie="valor-da-sessao"):
    out, err, relogio = io.StringIO(), io.StringIO(), iter(range(0, 10_000))
    codigo = cli.executar(args, cookie, transporte=transporte, dormir=lambda _s: None,
                          agora=lambda: float(next(relogio)), saida=out, erro=err)
    return codigo, out.getvalue(), err.getvalue()


def test_sucesso_imprime_a_saida_da_central_e_sai_com_o_codigo_do_comando():
    t = Roteiro((202, {"id": "c-1", "state": "created"}), (200, {"id": "c-1", "state": "running"}),
                (200, {"id": "c-1", "state": "succeeded", "exit_code": 0, "stdout": "2026-10-06\n", "stderr": "", "duration_ms": 120}))
    codigo, out, err = _rodar(_args(), t)
    assert codigo == 0 and out == "2026-10-06\n"
    assert "estado succeeded, código 0" in err and "(id c-1)" in err
    metodo, url, corpo, cookie, origem = t.pedidos[0]
    assert (metodo, url) == ("POST", "http://central.invalido/api/workers/worker-lan-01/comandos")
    assert corpo["linha"] == "Get-Date" and len(corpo["idempotency_key"]) >= 8
    assert cookie == "valor-da-sessao" and origem == "http://central.invalido"
    assert [p[0] for p in t.pedidos[1:]] == ["GET", "GET"]


def test_comando_que_terminou_com_erro_devolve_o_codigo_dele():
    t = Roteiro((202, {"id": "c-2", "state": "created"}),
                (200, {"id": "c-2", "state": "failed", "exit_code": 7, "stdout": "", "stderr": "falhou\n"}))
    codigo, out, err = _rodar(_args(), t)
    assert codigo == 7 and "falhou" in err


def test_uncertain_sai_com_2_e_nao_reenvia():
    t = Roteiro((202, {"id": "c-3", "state": "created"}),
                (200, {"id": "c-3", "state": "uncertain", "reason": "agent_lost", "stdout": "", "stderr": ""}))
    codigo, _out, err = _rodar(_args(), t)
    assert codigo == 2 and "agent_lost" in err
    assert [p[0] for p in t.pedidos].count("POST") == 1


@pytest.mark.parametrize("estado,esperado", [("rejected", 2), ("cancelled", 2), ("timed_out", 3)])
def test_estados_finais_sem_codigo(estado, esperado):
    t = Roteiro((202, {"id": "c-4", "state": "created"}), (200, {"id": "c-4", "state": estado, "stdout": "", "stderr": ""}))
    assert _rodar(_args(), t)[0] == esperado


@pytest.mark.parametrize("status,codigo_esperado", [(401, 4), (403, 4), (404, 4), (409, 2), (422, 2)])
def test_recusas_do_pedido(status, codigo_esperado):
    t = Roteiro((status, {"detail": {"code": "comando_remoto_desligado", "message": "interruptor desligado"}}))
    codigo, _out, err = _rodar(_args(), t)
    assert codigo == codigo_esperado and "comando_remoto_desligado" in err
    assert len(t.pedidos) == 1


def test_a_espera_local_acabar_sem_estado_final_sai_com_3_e_manda_conferir_pelo_id():
    t = Roteiro((202, {"id": "c-5", "state": "created"}), (200, {"id": "c-5", "state": "running"}))
    codigo, _out, err = _rodar(_args(timeout=1.0, espera=3.0), t)
    assert codigo == 3 and "c-5" in err and "Não reenvie" in err


def test_argv_vai_sem_shell_e_pasta_e_prazo_seguem_no_corpo():
    t = Roteiro((202, {"id": "c-6", "state": "created"}), (200, {"id": "c-6", "state": "succeeded", "exit_code": 0}))
    _rodar(_args(linha=None, argv_json='["adb","devices"]', pasta="C:/farm", timeout=30.0, chave="minha-chave-01"), t)
    corpo = t.pedidos[0][2]
    assert corpo == {"idempotency_key": "minha-chave-01", "argv": ["adb", "devices"], "pasta": "C:/farm", "timeout_s": 30.0}


@pytest.mark.parametrize("ruim", ["{}", '"adb"', "[]", "[1,2]"])
def test_argv_invalido_nem_chama_a_central(ruim):
    t = Roteiro((202, {"id": "x"}))
    codigo, _out, err = _rodar(_args(linha=None, argv_json=ruim), t)
    assert codigo == 2 and "pedido inválido" in err and t.pedidos == []


def test_central_fora_do_ar_sai_com_5():
    def cai(*_a):
        raise ConnectionRefusedError("recusada")
    assert _rodar(_args(), cai)[0] == 5


def test_sem_sessao_no_ambiente_nem_tenta(monkeypatch, capsys):
    monkeypatch.delenv("CENTRAL_SESSAO", raising=False)
    assert cli.main(["--worker", "w", "--linha", "x"]) == 4
    assert "CENTRAL_SESSAO" in capsys.readouterr().err


def test_linha_e_argv_nao_se_combinam_e_um_dos_dois_e_obrigatorio():
    with pytest.raises(SystemExit):
        cli.main(["--worker", "w", "--linha", "x", "--argv-json", "[]"])
    with pytest.raises(SystemExit):
        cli.main(["--worker", "w"])


class _Falsa(BaseHTTPRequestHandler):
    visto: list[dict] = []

    def _responder(self, status, corpo):
        dados = json.dumps(corpo).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(dados)))
        self.end_headers()
        self.wfile.write(dados)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        _Falsa.visto.append({"cookie": self.headers.get("Cookie"), "origin": self.headers.get("Origin"),
                             "corpo": json.loads(self.rfile.read(n))})
        self._responder(202, {"id": "c-http", "state": "created"})

    def do_GET(self):
        self._responder(200, {"id": "c-http", "state": "succeeded", "exit_code": 0, "stdout": "ok\n", "stderr": ""})

    def log_message(self, *_a):
        pass


def test_o_transporte_real_leva_cookie_e_origem_a_um_servidor_http():
    _Falsa.visto.clear()
    srv = HTTPServer(("127.0.0.1", 0), _Falsa)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        out, err = io.StringIO(), io.StringIO()
        codigo = cli.executar(_args(base=base), "sessao-abc", dormir=lambda _s: None, saida=out, erro=err)
    finally:
        srv.shutdown()
        srv.server_close()
    assert codigo == 0 and out.getvalue() == "ok\n"
    assert _Falsa.visto[0]["cookie"] == "parque_sessao=sessao-abc" and _Falsa.visto[0]["origin"] == base
    assert _Falsa.visto[0]["corpo"]["linha"] == "Get-Date"
