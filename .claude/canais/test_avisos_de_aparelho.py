"""28.73: o aviso agrupado de mudança de estado de sessão dos aparelhos de conta real.

Prova `simulated`: a central, o Telegram, o relógio e o `subprocess` são FALSOS. Nada aqui fala com a central real nem com
o Telegram. Rodar da raiz: `backend/.venv/Scripts/python.exe -m pytest -q .claude/canais/test_avisos_de_aparelho.py`.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import avisos_de_aparelho as av  # noqa: E402
from cartoes_de_aparelho import FalhaDeLeitura  # noqa: E402

#: dados FICTÍCIOS, fora da lista de reserva da redação: só a estrutura do texto os segura
NOME = "Zoraide Benevides"
HANDLE = "zoraide.benevides.ig"
EMAIL = "zoraide.b@exemplo-ficticio.com"
IP = "10.77.88.99"
SERIAL = "emulator-5999"
T0 = datetime(2026, 10, 7, 10, 0, tzinfo=UTC)
COOLDOWN = timedelta(minutes=10)


def _persona(status: str = "session_ready", *, stale: bool = False, nome: str = NOME) -> dict:
    return {"persona_id": "p-1", "name": nome, "handle": HANDLE, "email": EMAIL,
            "session": {"status": status, "stale": stale, "unknown_at_cap": False,
                        "status_since": "2026-10-07T09:00:00Z", "detail": f"{NOME} {HANDLE} {EMAIL}",
                        "attention": f"ligar para +55 11 91234-5678 {IP}"}}


def _inst(ident: str, *, state: str = "running", pausa: bool = False) -> dict:
    return {"id": ident, "state": state, "serial": SERIAL, "ip": IP, "locked_account": None,
            "repair_pause": ({"since": "2026-10-07T09:30:00Z", "reason": f"reparo de {NOME} em {IP}"} if pausa else None)}


def _central(**aparelhos: object):
    """`ler` falso. Cada chave `android_NN` vale: lista de personas (conta real), `[]` (sem conta), ou `(inst, personas)`."""
    def ler():
        insts: list[dict] = []
        personas: dict[str, list | None] = {}
        for chave, v in aparelhos.items():
            ident = chave.replace("_", "-")
            inst, ps = v if isinstance(v, tuple) else (_inst(ident), v)
            insts.append(inst)
            personas[ident] = ps
        return insts, personas, []
    return ler


class Telegram:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.enviados: list[str] = []

    def __call__(self, texto: str) -> int | None:
        self.enviados.append(texto)
        return 4242 + len(self.enviados) if self.ok else None


def _ciclo(ler, estado: Path, agora: datetime, tg: Telegram | None = None, *, gravar: bool = True) -> tuple[int, list[str]]:
    saida: list[str] = []
    rc = av.rodar_ciclo(ler, estado, agora, COOLDOWN, gravar=gravar, enviar_fn=tg, imprimir=saida.append)
    return rc, saida


def _sem_tags(h: str) -> str:
    return re.sub(r"<[^>]+>", "", h)


@pytest.fixture
def estado(tmp_path: Path) -> Path:
    return tmp_path / "canais" / "estado.json"


def test_o_primeiro_ciclo_so_grava_o_retrato_e_nao_avisa(estado: Path) -> None:
    tg = Telegram()
    rc, saida = _ciclo(_central(android_06=[_persona()], android_07=[_persona("auth_required")]), estado, T0, tg)
    assert rc == 0
    assert tg.enviados == []
    gravado = json.loads(estado.read_text(encoding="utf-8"))
    assert gravado["avisado"]["android-06"]["estado"] == "pronta"
    assert gravado["avisado"]["android-07"]["estado"] == "com erro"
    assert any("primeiro ciclo" in linha for linha in saida)


def test_mudanca_vira_aviso_com_o_molde_dos_avisos_e_critico_se_estava_pronto(estado: Path) -> None:
    tg = Telegram()
    _ciclo(_central(android_06=[_persona()]), estado, T0, tg)
    rc, saida = _ciclo(_central(android_06=[_persona("auth_required")]), estado, T0 + timedelta(minutes=30), tg)
    assert rc == 0
    assert len(tg.enviados) == 1
    texto = _sem_tags(tg.enviados[0])
    assert "Aparelhos de conta real: 1 mudou de estado" in texto
    assert "• android-06: de pronta para com erro (login necessário)" in texto
    assert "Crítico: a sessão caiu em android-06." in texto
    assert "Espera você: nada" in texto
    assert len(tg.enviados[0].splitlines()) <= 12
    assert any("enviado message_id=4243" in linha for linha in saida)
    # o retrato avançou: a mesma leitura no ciclo seguinte não repete o aviso
    _ciclo(_central(android_06=[_persona("auth_required")]), estado, T0 + timedelta(minutes=45), tg)
    assert len(tg.enviados) == 1


def test_voltar_ao_pronto_nao_e_critico(estado: Path) -> None:
    tg = Telegram()
    _ciclo(_central(android_06=[_persona("auth_required")]), estado, T0, tg)
    _ciclo(_central(android_06=[_persona()]), estado, T0 + timedelta(minutes=30), tg)
    texto = _sem_tags(tg.enviados[0])
    assert "de com erro para pronta (sessão verificada)" in texto
    assert "Crítico: nada." in texto
    assert "nada crítico" in tg.enviados[0]


def test_vencida_e_pausa_de_reparo_avisam_sem_ser_criticas(estado: Path) -> None:
    tg = Telegram()
    _ciclo(_central(android_06=[_persona()], android_07=[_persona()]), estado, T0, tg)
    _ciclo(_central(android_06=[_persona(stale=True)], android_07=(_inst("android-07", pausa=True), [_persona()])),
           estado, T0 + timedelta(minutes=30), tg)
    texto = _sem_tags(tg.enviados[0])
    assert "de pronta para vencida (verificação antiga)" in texto
    assert "de pronta para em pausa de reparo (pausa de reparo)" in texto
    assert "Crítico: nada." in texto


def test_dois_aparelhos_que_mudaram_vao_em_uma_mensagem_so(estado: Path) -> None:
    tg = Telegram()
    _ciclo(_central(android_06=[_persona()], android_07=[_persona()], android_08=[_persona()]), estado, T0, tg)
    _ciclo(_central(android_06=[_persona("auth_required")], android_07=[_persona("wrong_account")],
                    android_08=[_persona()]), estado, T0 + timedelta(minutes=30), tg)
    assert len(tg.enviados) == 1
    texto = _sem_tags(tg.enviados[0])
    assert "2 mudaram de estado" in texto
    assert "• android-06:" in texto and "• android-07:" in texto and "android-08:" not in texto
    assert "Crítico: a sessão caiu em android-06, android-07." in texto


def test_o_cooldown_segura_e_depois_libera(estado: Path) -> None:
    tg = Telegram()
    _ciclo(_central(android_06=[_persona()]), estado, T0, tg)
    _ciclo(_central(android_06=[_persona("auth_required")]), estado, T0 + timedelta(minutes=1), tg)  # avisa (1)
    assert len(tg.enviados) == 1
    # 5 min depois do aviso: o aparelho voltou a pronta; segura (cooldown), nada enviado, pendente 1
    rc, saida = _ciclo(_central(android_06=[_persona()]), estado, T0 + timedelta(minutes=6), tg)
    assert len(tg.enviados) == 1 and any("pendentes 1" in linha for linha in saida)
    # passado o cooldown (11 min depois do aviso) e ainda diferente do último avisado: entra no próximo aviso
    _ciclo(_central(android_06=[_persona()]), estado, T0 + timedelta(minutes=13), tg)
    assert len(tg.enviados) == 2
    assert "de com erro para pronta" in _sem_tags(tg.enviados[1])


def test_a_oscilacao_que_volta_ao_estado_avisado_nao_avisa(estado: Path) -> None:
    tg = Telegram()
    _ciclo(_central(android_06=[_persona()]), estado, T0, tg)
    _ciclo(_central(android_06=[_persona("auth_required")]), estado, T0 + timedelta(minutes=1), tg)  # avisa (1)
    _ciclo(_central(android_06=[_persona()]), estado, T0 + timedelta(minutes=4), tg)                 # pendente
    _ciclo(_central(android_06=[_persona("auth_required")]), estado, T0 + timedelta(minutes=8), tg)  # voltou ao avisado
    _ciclo(_central(android_06=[_persona("auth_required")]), estado, T0 + timedelta(minutes=20), tg)
    assert len(tg.enviados) == 1


def test_o_cooldown_e_por_aparelho(estado: Path) -> None:
    tg = Telegram()
    _ciclo(_central(android_06=[_persona()], android_07=[_persona()]), estado, T0, tg)
    _ciclo(_central(android_06=[_persona("auth_required")], android_07=[_persona()]), estado, T0 + timedelta(minutes=1), tg)
    # o 07 muda dentro do cooldown do 06, mas o 07 nunca foi avisado: avisa só o 07
    _ciclo(_central(android_06=[_persona("wrong_account")], android_07=[_persona("auth_required")]), estado,
           T0 + timedelta(minutes=3), tg)
    assert len(tg.enviados) == 2
    segundo = _sem_tags(tg.enviados[1])
    assert "android-07" in segundo and "android-06" not in segundo


def test_aparelho_sem_conta_real_e_ignorado(estado: Path) -> None:
    tg = Telegram()
    _ciclo(_central(android_06=[], android_07=[_persona()]), estado, T0, tg)
    assert "android-06" not in json.loads(estado.read_text(encoding="utf-8"))["avisado"]
    # o 06 ganha uma conta: só entra no retrato, sem aviso; o 07 perde a conta: sai do retrato, sem aviso
    _ciclo(_central(android_06=[_persona()], android_07=[]), estado, T0 + timedelta(minutes=30), tg)
    assert tg.enviados == []
    assert list(json.loads(estado.read_text(encoding="utf-8"))["avisado"]) == ["android-06"]


def test_leitura_de_um_aparelho_que_falha_mantem_o_retrato_dele(estado: Path) -> None:
    tg = Telegram()
    _ciclo(_central(android_06=[_persona()], android_07=[_persona()]), estado, T0, tg)
    _ciclo(_central(android_06=[_persona("auth_required")], android_07=None), estado, T0 + timedelta(minutes=30), tg)
    assert "android-07" in json.loads(estado.read_text(encoding="utf-8"))["avisado"]
    assert "android-07" not in _sem_tags(tg.enviados[0])


def test_tres_falhas_seguidas_mandam_um_aviso_so_e_nao_zeram_o_retrato(estado: Path) -> None:
    tg = Telegram()
    _ciclo(_central(android_06=[_persona()]), estado, T0, tg)

    def quebrada():
        raise FalhaDeLeitura("URLError")

    rcs = [_ciclo(quebrada, estado, T0 + timedelta(minutes=m), tg)[0] for m in (1, 2, 3, 4, 5)]
    assert rcs == [1] * 5
    assert len(tg.enviados) == 1
    texto = _sem_tags(tg.enviados[0])
    assert "não consegui ler os aparelhos" in texto and "3 vezes seguidas" in texto and "URLError" in texto
    assert json.loads(estado.read_text(encoding="utf-8"))["avisado"]["android-06"]["estado"] == "pronta"
    # a leitura volta: o contador zera e uma nova sequência de 3 falhas avisa de novo
    _ciclo(_central(android_06=[_persona()]), estado, T0 + timedelta(minutes=6), tg)
    assert json.loads(estado.read_text(encoding="utf-8"))["falhas_seguidas"] == 0
    for m in (7, 8, 9):
        _ciclo(quebrada, estado, T0 + timedelta(minutes=m), tg)
    assert len(tg.enviados) == 2


def test_duas_falhas_nao_avisam_e_central_vazia_conta_como_falha(estado: Path) -> None:
    tg = Telegram()
    _ciclo(_central(android_06=[_persona()]), estado, T0, tg)
    vazia = lambda: ([], {}, [])  # noqa: E731
    _ciclo(vazia, estado, T0 + timedelta(minutes=1), tg)
    _ciclo(vazia, estado, T0 + timedelta(minutes=2), tg)
    assert tg.enviados == []
    assert json.loads(estado.read_text(encoding="utf-8"))["falhas_seguidas"] == 2


def test_aviso_de_falha_que_nao_foi_enviado_tenta_de_novo(estado: Path) -> None:
    tg = Telegram(ok=False)

    def quebrada():
        raise FalhaDeLeitura("TimeoutError")

    for m in (1, 2, 3, 4):
        _ciclo(quebrada, estado, T0 + timedelta(minutes=m), tg)
    assert len(tg.enviados) == 2  # 3ª e 4ª falhas: a flag só sobe com message_id


def test_envio_que_falha_nao_avanca_o_retrato_e_o_proximo_ciclo_tenta_de_novo(estado: Path) -> None:
    _ciclo(_central(android_06=[_persona()]), estado, T0)
    ruim = Telegram(ok=False)
    rc, _ = _ciclo(_central(android_06=[_persona("auth_required")]), estado, T0 + timedelta(minutes=30), ruim)
    assert rc == 1 and len(ruim.enviados) == 1
    assert json.loads(estado.read_text(encoding="utf-8"))["avisado"]["android-06"]["estado"] == "pronta"
    bom = Telegram()
    _ciclo(_central(android_06=[_persona("auth_required")]), estado, T0 + timedelta(minutes=31), bom)
    assert len(bom.enviados) == 1


def test_o_ensaio_nao_grava_nem_envia(estado: Path) -> None:
    tg = Telegram()
    _ciclo(_central(android_06=[_persona()]), estado, T0, tg)
    antes = estado.read_text(encoding="utf-8")
    rc, saida = _ciclo(_central(android_06=[_persona("auth_required")]), estado, T0 + timedelta(minutes=30), None,
                       gravar=False)
    assert rc == 0 and estado.read_text(encoding="utf-8") == antes and len(tg.enviados) == 0
    assert any("de pronta para com erro" in linha for linha in saida) and any("nada foi enviado" in linha for linha in saida)


def test_o_ensaio_sem_estado_nao_cria_arquivo(estado: Path) -> None:
    _ciclo(_central(android_06=[_persona()]), estado, T0, None, gravar=False)
    assert not estado.exists()


def test_arquivo_de_estado_ilegivel_refaz_o_retrato_sem_avisar(estado: Path) -> None:
    estado.parent.mkdir(parents=True)
    estado.write_text("{isto não é json", encoding="utf-8")
    tg = Telegram()
    rc, saida = _ciclo(_central(android_06=[_persona("auth_required")]), estado, T0, tg)
    assert rc == 0 and tg.enviados == []
    assert any("ilegível" in linha for linha in saida)
    assert json.loads(estado.read_text(encoding="utf-8"))["avisado"]["android-06"]["estado"] == "com erro"


def test_nenhum_dado_de_persona_aparece_na_saida_nem_no_estado(estado: Path) -> None:
    tg = Telegram()
    saidas: list[str] = []
    _, s1 = _ciclo(_central(android_06=[_persona()], android_07=[_persona()]), estado, T0, tg)
    _, s2 = _ciclo(_central(android_06=[_persona("auth_required")],
                            android_07=(_inst("android-07", pausa=True), [_persona()])),
                   estado, T0 + timedelta(minutes=30), tg)
    saidas = s1 + s2 + tg.enviados + [estado.read_text(encoding="utf-8")]
    junto = "\n".join(saidas)
    for proibido in (NOME, NOME.split()[0], HANDLE, EMAIL, IP, SERIAL, "91234", "reparo de"):
        assert proibido not in junto, proibido


def test_um_rotulo_com_dado_pessoal_nao_vira_texto(estado: Path) -> None:
    """Só o id no formato `android-NN` é lido; um id estranho (que poderia trazer handle) é pulado."""
    tg = Telegram()
    ler = _central(android_06=[_persona()])

    def com_estranho():
        insts, personas, f = ler()
        insts.append(_inst(HANDLE))
        personas[HANDLE] = [_persona()]
        return insts, personas, f

    _ciclo(com_estranho, estado, T0, tg)
    _ciclo(lambda: (lambda r: (r[0], {**r[1], HANDLE: [_persona("auth_required")]}, r[2]))(com_estranho()), estado,
           T0 + timedelta(minutes=30), tg)
    assert HANDLE not in estado.read_text(encoding="utf-8") and tg.enviados == []


def test_muitos_aparelhos_cabem_em_12_linhas(estado: Path) -> None:
    tg = Telegram()
    antes = {f"android_{n:02d}": [_persona()] for n in range(1, 13)}
    depois = {f"android_{n:02d}": [_persona("auth_required")] for n in range(1, 13)}
    _ciclo(_central(**antes), estado, T0, tg)
    _ciclo(_central(**depois), estado, T0 + timedelta(minutes=30), tg)
    texto = tg.enviados[0]
    assert len(texto.splitlines()) <= 12
    assert "12 mudaram de estado" in _sem_tags(texto) and "e mais 5 aparelho(s)" in texto


# ---------------------------------------------------------------------------------------------------- envio e comando
class _Proc:
    def __init__(self, rc: int, saida: bytes) -> None:
        self.returncode, self.stdout = rc, saida


def test_enviar_chama_o_telegram_status_e_devolve_o_message_id(monkeypatch: pytest.MonkeyPatch) -> None:
    chamadas: list[list[str]] = []

    def falso(cmd, **kw):  # noqa: ANN001
        chamadas.append([str(c) for c in cmd])
        assert Path(cmd[2]).read_text(encoding="utf-8") == "<b>oi</b>"
        return _Proc(0, b"enviado message_id=77\n")

    monkeypatch.setattr(av.subprocess, "run", falso)
    assert av.enviar("<b>oi</b>") == 77
    assert chamadas[0][1].endswith("telegram_status.py")
    monkeypatch.setattr(av.subprocess, "run", lambda *a, **k: _Proc(1, b"falhou: sem chat\n"))
    assert av.enviar("x") is None


def _preparar_main(monkeypatch: pytest.MonkeyPatch, leituras: list) -> list[str]:
    monkeypatch.setattr(av.redacao, "recarregar", lambda *_: True)
    monkeypatch.setattr(av, "ler_da_central", lambda base: leituras.pop(0)() if leituras else leituras_vazias())
    enviados: list[str] = []
    monkeypatch.setattr(av, "enviar", lambda t: enviados.append(t) or 9001)
    return enviados


def leituras_vazias():
    raise FalhaDeLeitura("fim")


def test_main_padrao_e_ensaio_e_nao_cria_o_estado(monkeypatch: pytest.MonkeyPatch, estado: Path,
                                                   capsys: pytest.CaptureFixture[str]) -> None:
    enviados = _preparar_main(monkeypatch, [_central(android_06=[_persona()])])
    assert av.main(["--estado", str(estado)]) == 0
    assert not estado.exists() and enviados == []
    assert "ensaio: nada foi gravado nem enviado" in capsys.readouterr().out


def test_main_ciclo_grava_e_enviar_envia(monkeypatch: pytest.MonkeyPatch, estado: Path) -> None:
    enviados = _preparar_main(monkeypatch, [_central(android_06=[_persona()]),
                                            _central(android_06=[_persona("auth_required")])])
    assert av.main(["--ciclo", "--estado", str(estado)]) == 0
    assert estado.exists() and enviados == []
    assert av.main(["--enviar", "--estado", str(estado), "--cooldown-min", "0"]) == 0
    assert len(enviados) == 1 and "android-06" in enviados[0]


def test_main_recusa_enviar_com_arquivo(estado: Path) -> None:
    with pytest.raises(SystemExit):
        av.main(["--enviar", "--arquivo", "x.json", "--estado", str(estado)])


def test_o_laco_repete_ciclo_e_envio_ate_ser_interrompido(monkeypatch: pytest.MonkeyPatch, estado: Path,
                                                           capsys: pytest.CaptureFixture[str]) -> None:
    enviados = _preparar_main(monkeypatch, [_central(android_06=[_persona()]),
                                            _central(android_06=[_persona("auth_required")]),
                                            lambda: (_ for _ in ()).throw(RuntimeError("boom"))])
    dormiu: list[float] = []

    def dormir(s: float) -> None:
        dormiu.append(s)
        if len(dormiu) == 3:
            raise KeyboardInterrupt

    monkeypatch.setattr(av.time, "sleep", dormir)
    assert av.main(["--laco", "--intervalo-s", "7", "--cooldown-min", "0", "--estado", str(estado)]) == 0
    assert dormiu == [7.0, 7.0, 7.0]
    assert len(enviados) == 1
    saida = capsys.readouterr().out
    assert "laço interrompido" in saida and "boom" not in saida
