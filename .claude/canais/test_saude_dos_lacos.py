"""28.75: saúde do vigia, do laço de avisos de aparelho e da central, e o aviso único ao dono.

Prova `simulated`: a lista de processos, a central, o banco, o Telegram, o relógio e o `subprocess` são FALSOS (uma fixture
automática barra o `subprocess` e a rede de verdade). Nada aqui toca um processo, a central ou o Telegram reais. Rodar da
raiz: `backend/.venv/Scripts/python.exe -m pytest -q .claude/canais/test_saude_dos_lacos.py`.
"""
from __future__ import annotations

import json
import subprocess
import sys
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import saude_dos_lacos as sl  # noqa: E402

T0 = datetime(2026, 10, 7, 10, 0, tzinfo=UTC)
COOLDOWN = timedelta(minutes=30)
PID_VIGIA, PID_LACO = 4242, 7373
#: caminhos e comando FICTÍCIOS: nada disso pode aparecer no aviso
CAMINHO = r"C:\fantasia\sessao\.claude\handoffs\canais"


@pytest.fixture(autouse=True)
def _sem_efeito_real(monkeypatch):
    """Barra tudo o que tocaria o mundo: processo, rede, banco e a lista de nomes do banco."""
    def _barra(*_a, **_k):
        raise AssertionError("efeito real barrado no teste")
    monkeypatch.setattr(subprocess, "run", _barra)
    monkeypatch.setattr(subprocess, "Popen", _barra)
    monkeypatch.setattr(urllib.request, "urlopen", _barra)
    monkeypatch.setattr(sl, "listar_processos", _barra)
    monkeypatch.setattr(sl, "status_da_central", _barra)
    monkeypatch.setattr(sl, "ler_ultima_entrada", _barra)
    monkeypatch.setattr(sl, "enviar", _barra)
    monkeypatch.setattr(sl.redacao, "recarregar", lambda *_a, **_k: True)


def _cim(pid: int, linha: str, pai: int = 1000) -> dict:
    return {"ProcessId": pid, "ParentProcessId": pai, "CommandLine": linha}


def _vigia(pid: int = PID_VIGIA, base: int = 100, pai: int = 1000) -> dict:
    return _cim(pid, f'"C:\\py\\python.exe" {CAMINHO}\\vigia_dono.py {base}', pai)


def _laco(pid: int = PID_LACO, intervalo: int = 120) -> dict:
    return _cim(pid, f'"C:\\py\\python.exe" {CAMINHO}\\avisos_de_aparelho.py --laco --intervalo-s {intervalo}')


class Mundo:
    """Tudo o que a checagem lê, falso e ajustável."""

    def __init__(self, tmp_path: Path, bruto: list[dict] | None = None):
        self.bruto: list[dict] | None = [_vigia(), _laco()] if bruto is None else bruto
        self.carimbo: datetime | None = T0 - timedelta(minutes=1)
        self.central: int | None = 200
        self.ultima: int | None = 100
        self.estado = tmp_path / "estado-saude.json"
        self.enviados: list[str] = []
        self.proximo_id: int | None = 555
        self.consultas_ao_banco = 0
        self.saida: list[str] = []

    def _listar(self):
        if self.bruto is None:
            raise sl.FalhaDeLeitura("PermissionError")
        return sl.parse_processos(self.bruto)

    def _ultima(self):
        self.consultas_ao_banco += 1
        return self.ultima

    def _enviar(self, texto: str):
        self.enviados.append(texto)
        return self.proximo_id

    def rodar(self, agora: datetime = T0, **kw) -> int:
        kw.setdefault("cooldown", COOLDOWN)
        if kw.get("avisar"):
            kw.setdefault("enviar_fn", self._enviar)
        return sl.rodar(listar=self._listar, carimbo=lambda: self.carimbo, central=lambda: self.central,
                        ultima_entrada=self._ultima, estado_arq=self.estado, agora=agora,
                        imprimir=self.saida.append, **kw)

    @property
    def texto(self) -> str:
        return "\n".join(self.saida)


@pytest.fixture
def mundo(tmp_path):
    return Mundo(tmp_path)


def _estado(m: Mundo) -> dict:
    return json.loads(m.estado.read_text(encoding="utf-8"))


def _limpo(texto: str) -> None:
    """Nada de PID, caminho, comando nem nome de script no texto de um aviso."""
    for proibido in (str(PID_VIGIA), str(PID_LACO), "C:\\", "fantasia", ".py", "python", "--laco", "powershell", "pid"):
        assert proibido not in texto, proibido


# ------------------------------------------------------------------------------------------------ lista de processos
def test_parse_acha_so_o_vigia_e_o_laco():
    bruto = [
        _vigia(), _laco(),
        _cim(11, "python.exe -m pytest -q .claude/canais/test_avisos_de_aparelho.py"),
        _cim(12, "python.exe C:\\x\\avisos_de_aparelho.py --enviar"),            # ciclo avulso: não é o laço
        _cim(13, "python.exe C:\\x\\saude_dos_lacos.py --avisar"),
        _cim(14, "python.exe C:\\x\\outro_vigia_dono.py 5"),                      # nome parecido, outro script
        _cim(15, None),                                                          # sem permissão para ler a linha
    ]
    procs = sl.parse_processos(bruto)
    assert [(p.script, p.pid) for p in procs] == [(sl.VIGIA, PID_VIGIA), (sl.LACO, PID_LACO)]
    assert procs[0].base == 100
    assert procs[1].laco and procs[1].intervalo_s == 120


def test_parse_nao_guarda_a_linha_de_comando():
    p = sl.parse_processos([_vigia()])[0]
    assert CAMINHO not in repr(p)
    assert set(vars(p)) == {"pid", "script", "base", "intervalo_s", "laco"}


def test_parse_lancador_do_venv_conta_um_processo_so():
    pai = _vigia(pid=20)
    filho = _vigia(pid=21, pai=20)                  # o python do venv gera um filho com a MESMA linha
    assert [p.pid for p in sl.parse_processos([pai, filho])] == [20]


def test_parse_objeto_unico_e_ignora_o_proprio_pid():
    assert [p.pid for p in sl.parse_processos(_vigia())] == [PID_VIGIA]            # ConvertTo-Json de 1 item: objeto
    assert sl.parse_processos([_vigia()], ignorar_pid=PID_VIGIA) == []
    assert sl.parse_processos("lixo") == []


# ------------------------------------------------------------------------------------------------ tudo ativo
def test_tudo_ativo_nao_avisa(mundo):
    assert mundo.rodar(avisar=True) == 0
    assert mundo.enviados == []
    assert "vigia: ativo (1 processo)" in mundo.saida
    assert "laço de aparelhos: ativo, último ciclo há 1 min" in mundo.saida
    assert "central: 200" in mundo.saida
    assert "itens 3; com problema 0" in mundo.texto
    assert _estado(mundo)["vigia_base"] == 100        # a base do vigia visto vivo fica no estado


def test_ensaio_nao_grava_nem_envia(mundo):
    mundo.carimbo = T0 - timedelta(hours=1)
    mundo.rodar()                                       # sem --avisar
    assert not mundo.estado.exists()
    assert mundo.enviados == []
    assert "[nada foi enviado]" in mundo.texto


def test_dois_vigias_sao_dois_processos(mundo):
    mundo.bruto = [_vigia(pid=1), _vigia(pid=2), _laco()]
    mundo.rodar()
    assert "vigia: ativo (2 processos)" in mundo.saida


# ------------------------------------------------------------------------------------------------ laço
def test_laco_parado_manda_um_aviso_unico(mundo):
    mundo.carimbo = T0 - timedelta(minutes=14)
    mundo.bruto = [_vigia()]                            # e sem o processo do laço
    assert mundo.rodar(avisar=True) == 0
    assert len(mundo.enviados) == 1
    aviso = mundo.enviados[0]
    assert "laço de aparelhos: parado ou preso (último ciclo há 14 min, sem processo)" in mundo.saida
    assert "<b>Crítico:</b> o laço de avisos de aparelho." in aviso
    assert "<b>Espera você:</b> nada" in aviso
    assert "1 item parou" in aviso
    _limpo(aviso)
    assert _estado(mundo)["avisados"] == ["o laço de avisos de aparelho"]


def test_laco_preso_com_processo_vivo(mundo):
    mundo.carimbo = T0 - timedelta(minutes=7)           # > 3 x 120 s, e o processo existe
    mundo.rodar(avisar=True)
    assert "laço de aparelhos: parado ou preso (último ciclo há 7 min)" in mundo.saida
    assert len(mundo.enviados) == 1


def test_laco_limite_e_3_vezes_o_intervalo_do_processo(mundo):
    mundo.bruto = [_vigia(), _laco(intervalo=60)]       # 3 x 60 s = 3 min
    mundo.carimbo = T0 - timedelta(minutes=3)
    mundo.rodar()
    assert any(s.startswith("laço de aparelhos: ativo") for s in mundo.saida)
    mundo.saida.clear()
    mundo.carimbo = T0 - timedelta(minutes=3, seconds=1)
    mundo.rodar()
    assert any(s.startswith("laço de aparelhos: parado") for s in mundo.saida)


def test_laco_sem_carimbo_e_parado(mundo):
    mundo.carimbo = None
    mundo.rodar()
    assert "laço de aparelhos: parado ou preso (sem registro de ciclo)" in mundo.saida


def test_carimbo_do_laco_lido_do_arquivo(tmp_path):
    arq = tmp_path / "laco.json"
    assert sl.ler_carimbo_do_laco(arq) is None
    arq.write_text("lixo", encoding="utf-8")
    assert sl.ler_carimbo_do_laco(arq) is None
    arq.write_text(json.dumps({"atualizado_em": "2026-10-07T09:58:30Z"}), encoding="utf-8")
    assert sl.ler_carimbo_do_laco(arq) == datetime(2026, 10, 7, 9, 58, 30, tzinfo=UTC)


# ------------------------------------------------------------------------------------------------ cooldown e "voltou"
def test_cooldown_segura_a_repeticao_e_libera_depois(mundo):
    mundo.central = None
    mundo.rodar(avisar=True)
    assert len(mundo.enviados) == 1
    mundo.rodar(agora=T0 + timedelta(minutes=29), avisar=True)
    assert len(mundo.enviados) == 1                    # ainda parado, dentro do cooldown
    assert "aviso: em cooldown" in mundo.texto
    mundo.carimbo = T0 + timedelta(minutes=29)
    mundo.rodar(agora=T0 + timedelta(minutes=31), avisar=True)
    assert len(mundo.enviados) == 2                    # passou o cooldown e continua parado


def test_voltar_ao_normal_manda_um_aviso_voltou(mundo):
    mundo.central = None
    mundo.rodar(avisar=True)
    mundo.central = 200
    mundo.rodar(agora=T0 + timedelta(minutes=5), avisar=True)
    assert len(mundo.enviados) == 2
    voltou = mundo.enviados[1]
    assert "voltou ao normal" in voltou and "a central" in voltou
    assert "<b>Crítico:</b> nada." in voltou
    _limpo(voltou)
    assert _estado(mundo)["avisados"] == []
    mundo.rodar(agora=T0 + timedelta(minutes=10), avisar=True)
    assert len(mundo.enviados) == 2                    # normal de novo: nada mais a dizer


def test_normal_sem_nunca_ter_avisado_nao_manda_voltou(mundo):
    mundo.rodar(avisar=True)
    mundo.rodar(agora=T0 + timedelta(minutes=5), avisar=True)
    assert mundo.enviados == []


def test_envio_que_falha_nao_renova_o_cooldown_e_tenta_de_novo(mundo):
    mundo.central = None
    mundo.proximo_id = None
    assert mundo.rodar(avisar=True) == 1
    assert _estado(mundo).get("avisados", []) == [] and "ultimo_aviso_em" not in _estado(mundo)
    mundo.proximo_id = 9
    assert mundo.rodar(agora=T0 + timedelta(minutes=1), avisar=True) == 0
    assert len(mundo.enviados) == 2                    # a 2ª tentativa foi imediata (sem cooldown)


def test_varios_itens_viram_um_so_aviso(mundo):
    mundo.central = 503
    mundo.carimbo = None
    mundo.bruto = []                                   # sem vigia e sem laço
    mundo.ultima = 100
    mundo.rodar(avisar=True)
    assert len(mundo.enviados) == 1
    aviso = mundo.enviados[0]
    assert "3 itens pararam" in aviso
    assert "a central" in aviso and "o laço de avisos de aparelho" in aviso and "o vigia" in aviso
    _limpo(aviso)


# ------------------------------------------------------------------------------------------------ vigia
def test_vigia_ausente_com_recado_novo_pendente_nao_e_problema(mundo):
    mundo.rodar(avisar=True)                           # o vigia vivo grava a base 100
    mundo.bruto = [_laco()]                            # o vigia saiu de propósito...
    mundo.ultima = 101                                 # ...porque chegou o recado 101
    mundo.rodar(agora=T0 + timedelta(minutes=2), avisar=True)
    assert any(s.startswith("vigia: saiu de propósito") for s in mundo.saida)
    assert mundo.enviados == []
    assert mundo.consultas_ao_banco == 1


def _vigia_saiu_com_recado(m: Mundo) -> None:
    """O vigia vivo grava a base 100; depois some e chega o recado 101. Sem rodar a checagem com ele ausente ainda."""
    m.rodar(avisar=True)
    m.bruto = [_laco()]
    m.ultima = 101


def _rodar_em(m: Mundo, minutos: int, **kw) -> int:
    m.carimbo = T0 + timedelta(minutes=minutos) - timedelta(minutes=1)     # o laço segue vivo: só o vigia é o assunto
    return m.rodar(agora=T0 + timedelta(minutes=minutos), avisar=True, **kw)


def test_vigia_com_recado_pendente_e_esperado_dentro_do_cooldown(mundo):
    _vigia_saiu_com_recado(mundo)
    _rodar_em(mundo, 0)                                # 1ª checagem com ele ausente: o instante é registrado
    assert _estado(mundo)["vigia_saiu_em"] == "2026-10-07T10:00:00Z"
    _rodar_em(mundo, 29)
    assert any(s.startswith("vigia: saiu de propósito") for s in mundo.saida)
    assert mundo.enviados == []


def test_vigia_com_recado_pendente_vira_problema_depois_do_cooldown(mundo):
    _vigia_saiu_com_recado(mundo)
    _rodar_em(mundo, 0)
    _rodar_em(mundo, 30)
    assert "vigia: parado há mais de 30 min com recado do dono pendente" in mundo.saida
    assert len(mundo.enviados) == 1
    assert "o vigia das respostas do dono" in mundo.enviados[0] and "<b>Crítico:</b>" in mundo.enviados[0]
    _limpo(mundo.enviados[0])


def test_instante_do_saiu_persiste_entre_execucoes(mundo):
    _vigia_saiu_com_recado(mundo)
    _rodar_em(mundo, 0)
    _rodar_em(mundo, 10)                               # a 2ª execução não renova o instante da 1ª
    _rodar_em(mundo, 20)
    assert _estado(mundo)["vigia_saiu_em"] == "2026-10-07T10:00:00Z"
    assert mundo.enviados == []


def test_instante_some_quando_o_vigia_volta_e_a_contagem_recomeca(mundo):
    _vigia_saiu_com_recado(mundo)
    _rodar_em(mundo, 0)
    mundo.bruto = [_vigia(base=101), _laco()]          # a Canais leu e relançou
    _rodar_em(mundo, 40)
    assert "vigia_saiu_em" not in _estado(mundo) and _estado(mundo)["vigia_base"] == 101
    mundo.bruto = [_laco()]
    mundo.ultima = 102                                 # sai de novo, com outro recado
    _rodar_em(mundo, 41)
    assert _estado(mundo)["vigia_saiu_em"] == "2026-10-07T10:41:00Z"
    assert any(s.startswith("vigia: saiu de propósito") for s in mundo.saida)


def test_vigia_parado_com_recado_que_volta_manda_o_aviso_voltou(mundo):
    _vigia_saiu_com_recado(mundo)
    _rodar_em(mundo, 0)
    _rodar_em(mundo, 31)
    assert len(mundo.enviados) == 1
    mundo.bruto = [_vigia(base=101), _laco()]
    _rodar_em(mundo, 36)
    assert len(mundo.enviados) == 2 and "voltou ao normal" in mundo.enviados[1]


def test_listagem_que_falha_guarda_o_instante_do_saiu(mundo):
    _vigia_saiu_com_recado(mundo)
    _rodar_em(mundo, 0)
    mundo.bruto = None
    _rodar_em(mundo, 5)
    assert _estado(mundo)["vigia_saiu_em"] == "2026-10-07T10:00:00Z"


def test_religar_do_vigia_parado_com_recado_usa_a_base_antiga(mundo):
    _vigia_saiu_com_recado(mundo)
    _rodar_em(mundo, 0)
    mundo.saida.clear()
    mundo.ultima = 150
    mundo.carimbo = T0 + timedelta(minutes=34)
    mundo.rodar(agora=T0 + timedelta(minutes=35), religar=True)
    vigia = next(s for s in mundo.saida if s.startswith("religar vigia:"))
    assert vigia.split("#")[0].rstrip().endswith("vigia_dono.py 100")      # devolve o recado pendente, nada se perde


def test_vigia_ausente_sem_pendente_e_problema(mundo):
    mundo.rodar(avisar=True)
    mundo.bruto = [_laco()]
    mundo.ultima = 100                                 # nada novo: ninguém o relançou e ele não tinha por que sair
    mundo.rodar(agora=T0 + timedelta(minutes=2), avisar=True)
    assert "vigia: parado: não há processo e não há recado novo pendente" in mundo.saida
    assert len(mundo.enviados) == 1 and "o vigia das respostas do dono" in mundo.enviados[0]
    _limpo(mundo.enviados[0])


def test_vigia_ausente_sem_base_conhecida_nao_conta_como_saudavel(mundo):
    mundo.bruto = [_laco()]                            # estado vazio, sem --base: não sei se há pendente
    mundo.rodar()
    linha = next(s for s in mundo.saida if s.startswith("vigia:"))
    assert "não sei se há recado novo" in linha
    assert "com problema 1" in mundo.texto


def test_vigia_ausente_com_o_banco_ilegivel_nao_conta_como_saudavel(mundo):
    mundo.bruto = [_laco()]
    mundo.ultima = None
    mundo.rodar(base_arg=100)
    assert "com problema 1" in mundo.texto


def test_base_passada_resolve_a_pendencia(mundo):
    mundo.bruto = [_laco()]
    mundo.ultima = 150
    mundo.rodar(base_arg=120)
    assert any(s.startswith("vigia: saiu de propósito") for s in mundo.saida)


def test_vigia_vivo_nao_consulta_o_banco(mundo):
    mundo.rodar()
    assert mundo.consultas_ao_banco == 0


# ------------------------------------------------------------------------------------------------ central e listagem
@pytest.mark.parametrize("codigo,texto", [(None, "central: não respondeu"), (503, "central: não respondeu (estado 503)")])
def test_central_falhando(mundo, codigo, texto):
    mundo.central = codigo
    mundo.rodar(avisar=True)
    assert texto in mundo.saida
    assert len(mundo.enviados) == 1 and "a central" in mundo.enviados[0]
    _limpo(mundo.enviados[0])


def test_listagem_que_falha_nao_conta_como_saudavel(mundo):
    mundo.bruto = None
    mundo.rodar(avisar=True)
    assert "vigia: não verificado (não consegui listar os processos)" in mundo.saida
    assert "listagem de processos falhou: PermissionError" in mundo.texto
    assert len(mundo.enviados) == 1


def test_laco_com_carimbo_novo_segue_ativo_mesmo_sem_a_listagem(mundo):
    mundo.bruto = None
    mundo.rodar()
    assert "laço de aparelhos: ativo, último ciclo há 1 min" in mundo.saida


# ------------------------------------------------------------------------------------------------ saída
def test_saida_json(mundo):
    mundo.central = 500
    mundo.rodar(como_json=True)
    dados = json.loads(mundo.saida[-1])
    assert dados["com_problema"] == 1
    assert [i["chave"] for i in dados["itens"]] == ["vigia", "laco", "central"]
    assert {(p["script"], p["pid"]) for p in dados["processos"]} == {(sl.VIGIA, PID_VIGIA), (sl.LACO, PID_LACO)}


def test_ensaio_mostra_script_e_pid_mas_nunca_a_linha_de_comando(mundo):
    mundo.rodar()
    linha = next(s for s in mundo.saida if s.startswith("processos vistos:"))
    assert f"{sl.VIGIA} (pid {PID_VIGIA})" in linha and f"{sl.LACO} (pid {PID_LACO})" in linha
    assert "C:\\" not in mundo.texto and "--laco" not in mundo.texto


def test_aviso_nao_leva_nome_de_persona(mundo, monkeypatch):
    """O corpo passa pelo filtro do Trello: um nome de persona que entrasse no texto sairia mascarado."""
    visto = []
    monkeypatch.setattr(sl, "redigir", lambda t: (visto.append(t), t)[1])
    mundo.central = None
    mundo.rodar(avisar=True)
    assert visto, "o aviso não passou por redigir"


# ------------------------------------------------------------------------------------------------ religar
def test_religar_so_imprime_e_nao_executa_nada(mundo):
    mundo.bruto = []                                   # tudo parado
    mundo.carimbo = None
    mundo.ultima = 321
    mundo.central = None
    mundo.rodar(religar=True)
    cmds = [s for s in mundo.saida if s.startswith("religar ")]
    laco = next(s for s in cmds if s.startswith("religar laço de aparelhos:"))
    assert "avisos_de_aparelho.py --laco --intervalo-s 120" in laco
    vigia = next(s for s in cmds if s.startswith("religar vigia:"))
    assert "vigia_dono.py 321" in vigia
    assert any(s.startswith("religar central:") for s in cmds)
    assert "nada foi executado" in mundo.texto
    # a fixture automática já faria o teste falhar se subprocess.run/Popen fossem chamados


def test_religar_usa_o_base_passado_para_o_vigia(mundo):
    mundo.bruto = [_laco()]
    mundo.ultima = 100
    mundo.rodar(religar=True, base_arg=100)
    assert any(s.startswith("religar vigia:") and s.split("#")[0].rstrip().endswith("vigia_dono.py 100") for s in mundo.saida)


def test_religar_sem_base_e_sem_banco_pede_o_base(mundo):
    mundo.bruto = [_laco()]
    mundo.ultima = None
    mundo.rodar(religar=True)
    assert any("(sem comando)" in s and "passe --base" in s for s in mundo.saida if s.startswith("religar vigia:"))


def test_religar_nao_lista_o_que_esta_bem(mundo):
    mundo.rodar(religar=True)
    assert not [s for s in mundo.saida if s.startswith("religar ")]


def test_gancho_do_29_186_devolve_argv_sem_executar():
    itens = [sl.avaliar_laco([], None, T0, 120)]
    (rot, argv, _nota), = sl.comandos_de_religar(itens, intervalo_s=120, base_vigia=None)
    assert rot == "laço de aparelhos"
    assert argv[0].endswith("python.exe") and argv[2:] == ["--laco", "--intervalo-s", "120"]


# ------------------------------------------------------------------------------------------------ comando
def test_main_encadeia_as_leituras_e_nunca_envia_sem_avisar(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sl, "listar_processos", lambda: sl.parse_processos([_vigia(), _laco()]))
    monkeypatch.setattr(sl, "status_da_central", lambda base: 200)
    monkeypatch.setattr(sl, "ler_ultima_entrada", lambda: 100)
    laco = tmp_path / "laco.json"
    laco.write_text(json.dumps({"atualizado_em": (datetime.now(UTC) - timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%SZ")}),
                    encoding="utf-8")
    rc = sl.main(["--estado", str(tmp_path / "e.json"), "--estado-laco", str(laco)])
    saida = capsys.readouterr().out
    assert rc == 0 and "itens 3; com problema 0" in saida
    assert not (tmp_path / "e.json").exists()


def test_main_avisar_envia_um_aviso_pelo_envio_do_telegram(monkeypatch, tmp_path, capsys):
    enviados = []
    monkeypatch.setattr(sl, "listar_processos", lambda: [])
    monkeypatch.setattr(sl, "status_da_central", lambda base: None)
    monkeypatch.setattr(sl, "ler_ultima_entrada", lambda: 100)
    monkeypatch.setattr(sl, "enviar", lambda texto: (enviados.append(texto), 77)[1])
    rc = sl.main(["--avisar", "--base", "100", "--estado", str(tmp_path / "e.json"),
                  "--estado-laco", str(tmp_path / "nao-existe.json")])
    assert rc == 0 and len(enviados) == 1
    assert "enviado message_id=77" in capsys.readouterr().out
    assert json.loads((tmp_path / "e.json").read_text(encoding="utf-8"))["avisados"]


def test_estado_ilegivel_e_refeito_com_nota(mundo):
    mundo.estado.write_text("{lixo", encoding="utf-8")
    mundo.rodar(avisar=True)
    assert "arquivo de estado ilegível" in mundo.texto
    assert _estado(mundo)["versao"] == 1
