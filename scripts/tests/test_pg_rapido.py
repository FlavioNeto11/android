"""Testes puros do PG rápido da suíte (scripts/pg-rapido.py, 29.99), com docker e pytest de mentira."""
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location("pg_rapido", Path(__file__).resolve().parents[1] / "pg-rapido.py")
pg = importlib.util.module_from_spec(_SPEC)
sys.modules["pg_rapido"] = pg          # o `@dataclass` procura o módulo em `sys.modules`
_SPEC.loader.exec_module(pg)
WINDOWS = pg.os.name == "nt"

#: 29.117: o `matar_arvore` no Windows termina o Job Object do pytest; nos dublês, o job é um rótulo e a terminação
#: fica registrada aqui (a do sistema não é chamada).
_JOBS_TERMINADOS: list[str] = []
_JOBS_FECHADOS: list[str] = []
_JOB_OK = [True]


@pytest.fixture(autouse=True)
def _trava_de_teste(monkeypatch):
    """N3 da leitura do 29.117: nenhum teste usa a trava da rodada real (com uma em curso, o teste reprovava, e o
    teste em curso recusava a rodada real com rc 10; o scripts/tests faz parte do próprio funil)."""
    monkeypatch.setattr(pg, "NOME_DA_TRAVA", f"{pg.NOME}-teste-{pg.os.getpid()}")
    yield
    pg.soltar_trava()


@pytest.fixture(autouse=True)
def _job_de_mentira(monkeypatch):
    _JOBS_TERMINADOS.clear()
    _JOBS_FECHADOS.clear()
    _JOB_OK[0] = True
    if WINDOWS:
        def terminar(job):
            if isinstance(job, str):
                _JOBS_TERMINADOS.append(job)
                return _JOB_OK[0]
            return orig_terminar(job)

        def fechar(h):
            if isinstance(h, str):
                _JOBS_FECHADOS.append(h)
            else:
                orig_fechar(h)
        orig_terminar, orig_fechar = pg._terminar_job, pg._fechar_handle
        monkeypatch.setattr(pg, "_terminar_job", terminar)
        monkeypatch.setattr(pg, "_fechar_handle", fechar)


def _matou(docker) -> bool:
    """A árvore foi morta: o job terminado (Windows) ou o `kill` do grupo (fora dele)."""
    return bool(_JOBS_TERMINADOS) or any(c and c[0] == "kill" for c in docker.chamadas)


def _ok(stdout: str = "", rc: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], rc, stdout=stdout, stderr="")


def _disco(usado: int, wal: int = 200, base: int = 100, total: int = 4096) -> str:
    return (f"tmpfs {total} {usado} {total - usado} {usado * 100 // total}% /var/lib/postgresql/data\n"
            f"{wal}\t/var/lib/postgresql/data/pg_wal\n{base}\t/var/lib/postgresql/data/base\n")


class _Docker:
    """Responde aos comandos do script; `usos` é a sequência de MB usados que as amostras leem."""

    def __init__(self, usos: list[int], aceita_em: int = 1) -> None:
        self.usos, self.aceita_em, self.chamadas = list(usos), aceita_em, []
        self._isready = 0

    def __call__(self, cmd):
        cmd = list(cmd)
        self.chamadas.append(cmd)
        if cmd[:2] == ["docker", "exec"] and "pg_isready" in cmd:
            self._isready += 1
            return _ok(rc=0 if self._isready >= self.aceita_em else 2)
        if cmd[:2] == ["docker", "exec"] and "sh" in cmd:
            return _ok(_disco(self.usos.pop(0) if len(self.usos) > 1 else self.usos[0]))
        if cmd[:2] == ["docker", "exec"] and "psql" in cmd:
            return _ok("3 12 40 9\n")
        return _ok()


class _Pytest:
    def __init__(self, voltas: int, rc: int = 0) -> None:
        self.pid, self.voltas, self.rc, self.morto = 4242, voltas, rc, False
        self.job = "job-4242"

    def poll(self):
        if self.morto:
            return 1
        self.voltas -= 1
        return None if self.voltas >= 0 else self.rc

    def wait(self, timeout=None):
        return 1 if self.morto else self.rc


def test_o_conteiner_sobe_com_wal_minimo_tmpfs_de_4g_e_so_no_loopback():
    assert "--pull=never" in pg.comando_docker_run()                  # imagem faltando é erro, não download
    cmd = pg.comando_docker_run()
    texto = " ".join(cmd)
    for c in ("wal_level=minimal", "max_wal_senders=0", "max_wal_size=256MB", "fsync=off", "full_page_writes=off",
              "max_connections=200"):
        assert ["-c", c] == cmd[cmd.index(c) - 1:cmd.index(c) + 1]
    assert "--tmpfs /var/lib/postgresql/data:rw,size=4g" in texto
    assert f"127.0.0.1:{pg.PORTA}:5432" in texto            # o banco de teste não abre para a LAN
    assert cmd.index(pg.IMAGEM) < cmd.index("-c")              # as chaves vão ao postgres, não ao docker


def test_partes_cortam_na_ordem_sem_perder_nem_repetir():
    arquivos = [f"tests/test_{i:03}.py" for i in range(467)]
    p = pg.partes(arquivos, 2)
    assert [len(x) for x in p] == [234, 233]                   # a divisão da suíte 35
    assert sum(p, []) == arquivos
    assert [len(x) for x in pg.partes(arquivos, 3)] == [156, 156, 155]
    assert pg.partes(arquivos[:2], 5) == [[arquivos[0]], [arquivos[1]]]
    assert pg.partes([], 2) == []
    with pytest.raises(ValueError):
        pg.partes(arquivos, 0)


def test_a_amostra_le_disco_e_catalogo_e_diz_a_fracao():
    a = pg.amostrar(_Docker([1057]), hora=lambda: "05:56:50Z")
    assert a is not None and (a.usado_mb, a.total_mb, a.wal_mb, a.base_mb) == (1057, 4096, 200, 100)
    assert (a.esquemas, a.pg_class_mb, a.pg_attribute_mb, a.pg_depend_mb) == (3, 12, 40, 9)
    assert a.linha().startswith("05:56:50Z tmpfs 1057 de 4096 MB (26%) wal=200 base=100 esquemas=3")
    assert not pg.deve_abortar(a)
    assert pg.deve_abortar(pg.amostrar(_Docker([3482])))           # 85 % de 4096
    assert not pg.deve_abortar(None)                                # amostra que falha não aborta a fase


def test_amostra_sem_conteiner_ou_saida_estranha_e_none():
    assert pg.amostrar(lambda cmd: _ok(rc=1)) is None
    assert pg.amostrar(lambda cmd: _ok("lixo")) is None


def test_o_rc_do_du_nao_apaga_a_amostra_e_o_aborto_ainda_dispara():
    """A1 da leitura do #385: o `du` sai com 1 quando um arquivo some no meio; o `df` decide."""
    def du_tropeca(cmd):
        if "sh" in cmd:
            return _ok(_disco(3600), rc=1)
        return _ok("3 12 40 9\n") if "psql" in cmd else _ok()
    a = pg.amostrar(du_tropeca)
    assert a is not None and a.usado_mb == 3600 and pg.deve_abortar(a)
    assert "2>/dev/null; exit 0" in [c for c in pg_comandos(du_tropeca) if "df -m" in c][0]
    so_df = _disco(3600).splitlines()[0] + "\n105\t/var/lib/postgresql/data/pg_wal\n"   # o du da base não veio
    b = pg.amostrar(lambda cmd: _ok(so_df) if "sh" in cmd else _ok())
    assert b is not None and (b.wal_mb, b.base_mb) == (105, None) and pg.deve_abortar(b)
    assert "base=?" in b.linha()


def pg_comandos(executar):
    vistos: list[str] = []
    pg.amostrar(lambda cmd: (vistos.append(" ".join(cmd)), executar(cmd))[1])
    return vistos


def test_parte_verde_relata_aceite_contagem_pico_e_fim(tmp_path):
    saida = tmp_path / "p1.txt"
    saida.write_text("....\n5524 passed, 8 skipped in 884.68s (0:14:44)\n", encoding="utf-8")
    linhas: list[str] = []
    docker = _Docker([600, 1057, 900, 1011], aceita_em=2)
    rc = pg.rodar_parte("pg parte 1/2", ["a", "b"], saida, linhas.append, executar=docker,
                        lancar=lambda arq, s: _Pytest(voltas=3), dormir=lambda s: None)
    assert rc == 0
    assert ["docker", "rm", "-f", pg.NOME] in docker.chamadas      # contêiner novo: o tmpfs começa vazio
    assert linhas[0].startswith("pg parte 1/2 aceitou em ")
    assert any("rc=0" in ln and "5524 passed, 8 skipped" in ln for ln in linhas)
    assert any(ln.startswith("pg parte 1/2 pico:") and "tmpfs 1057 de 4096" in ln for ln in linhas)


def test_disco_acima_do_limite_mata_a_arvore_e_para_com_uma_linha(tmp_path):
    linhas: list[str] = []
    docker = _Docker([1200, 2900, 3600])
    proc = _Pytest(voltas=50)
    rc = pg.rodar_parte("pg parte 1/1", ["a"], tmp_path / "p.txt", linhas.append, executar=docker,
                        lancar=lambda arq, s: proc, dormir=lambda s: None)
    assert rc == 3
    if WINDOWS:                                                        # a árvore, não só o pai (K-099)
        assert _JOBS_TERMINADOS == ["job-4242"]                        # pelo job (29.117), nunca o taskkill /T
        assert not any(c and c[0] == "taskkill" for c in docker.chamadas)
    else:
        assert ["kill", "-KILL", "--", "-4242"] in docker.chamadas
    assert linhas[-1].startswith("pg parte 1/1 ABORTADA pelo disco:") and "tmpfs 3600 de 4096 MB (88%)" in linhas[-1]
    assert "ATENÇÃO" not in linhas[-1]


def test_o_kill_que_falha_aparece_na_linha_do_aborto(tmp_path):
    class _KillFalha(_Docker):
        def __call__(self, cmd):
            if cmd and cmd[0] == "kill":
                self.chamadas.append(list(cmd))
                return _ok(rc=128)
            return super().__call__(cmd)
    _JOB_OK[0] = False
    linhas: list[str] = []
    rc = pg.rodar_parte("pg parte 1/1", ["a"], tmp_path / "p.txt", linhas.append, executar=_KillFalha([3600]),
                        lancar=lambda arq, s: _Pytest(voltas=50), dormir=lambda s: None)
    esperado = "ATENÇÃO: o TerminateJobObject falhou" if WINDOWS else "ATENÇÃO: o kill da árvore saiu com rc=128"
    assert rc == 3 and esperado in linhas[-1]


def test_matar_arvore_de_verdade_mata_o_neto(tmp_path):
    """Prova barata do K-099: uma árvore real de dois níveis (pai → filho dormindo) e o `matar_arvore` real."""
    marca = tmp_path / "filho.pid"
    filho = f"import os,time; open(r'{marca}','w').write(str(os.getpid())); time.sleep(90)"
    pai = f"import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',{filho!r}]); time.sleep(90)"
    proc = pg.lancar_em_job([sys.executable, "-c", pai])            # como o `_lancar_pytest` (job ou grupo)
    pid_filho: int | None = None
    passou = False
    try:
        for _ in range(100):
            if marca.exists() and marca.read_text().strip():
                break
            pg.time.sleep(0.1)
        pid_filho = int(marca.read_text())
        assert pg.matar_arvore(proc, pg._executar) is None
        assert proc.poll() is not None
        # Por relógio (o sistema leva um instante para recolher); o neto dorme 90 s, bem mais que o prazo.
        assert _esperar_morte(pid_filho), "o filho do pytest ficou órfão (K-099)"
        passou = True
    finally:
        pg.fechar_job(proc)                                            # N2 da leitura do 29.117
        if proc.poll() is None:
            proc.kill()
        # Só quando o teste não passou: passando, o neto já morreu, e o PID pode ter sido reusado por outro processo.
        if not passou and pid_filho is not None and _vivo(pid_filho):
            if pg.os.name == "nt":
                subprocess.run(["taskkill", "/F", "/PID", str(pid_filho)], capture_output=True, check=False)
            else:
                pg.os.kill(pid_filho, 9)


def _vivo(pid: int) -> bool:
    if pg.os.name == "nt":
        r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True, check=False)
        return str(pid) in r.stdout
    try:
        pg.os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_conteiner_que_nao_aceita_conexao_nao_roda_o_pytest(tmp_path, monkeypatch):
    linhas: list[str] = []
    relogio = iter(range(0, 10_000, 100))
    monkeypatch.setattr(pg.time, "monotonic", lambda: next(relogio))
    lancou: list[bool] = []
    rc = pg.rodar_parte("pg parte 1/1", ["a"], tmp_path / "p.txt", linhas.append,
                        executar=_Docker([100], aceita_em=10_000), lancar=lambda a, s: lancou.append(True),
                        dormir=lambda s: None)
    assert rc == 8 and not lancou and "não aceitou conexão" in linhas[-1]


def test_simular_lista_as_partes_sem_tocar_o_docker(tmp_path, monkeypatch, capsys):
    backend = tmp_path / "backend" / "tests"
    backend.mkdir(parents=True)
    for i in range(5):
        (backend / f"test_{i}.py").write_text("")
    lista = tmp_path / "lista.txt"
    lista.write_text("\n".join([f"tests/test_{i}.py" for i in range(5)] + ["tests/conftest.py", "tests/sumiu.py"]),
                     encoding="utf-8")
    monkeypatch.setattr(pg, "RAIZ", tmp_path)
    monkeypatch.setattr(pg, "_executar", lambda cmd: pytest.fail(f"docker chamado: {cmd}"))
    assert pg.main(["--lista", str(lista), "--partes", "2", "--simular"]) == 0
    out = capsys.readouterr().out
    assert "parte 1/2: 3 arquivos (de 5)" in out and "parte 2/2: 2 arquivos (de 5)" in out
    assert "wal_level=minimal" in out


def test_executar_com_prazo_devolve_124_sem_levantar():
    """X1: o `docker exec` preso não pendura o laço; o prazo estourado vira rc 124, como o `timeout` do coreutils."""
    r = pg._executar([sys.executable, "-c", "import time; time.sleep(5)"], prazo_s=0.5)
    assert r.returncode == 124 and "excedeu" in r.stderr


def test_tres_amostras_sem_df_avisam_uma_vez(tmp_path):
    """X2: sem a linha do df, o aborto pelo disco fica cego; a 3ª amostra seguida sem ela avisa, uma vez por série."""
    class _SemDf(_Docker):
        def __call__(self, cmd):
            if cmd[:2] == ["docker", "exec"] and "df -m" in " ".join(cmd):
                self.chamadas.append(list(cmd))
                return _ok("")
            return super().__call__(cmd)
    linhas: list[str] = []
    rc = pg.rodar_parte("pg parte 1/1", ["a"], tmp_path / "p.txt", linhas.append, executar=_SemDf([100]),
                        lancar=lambda arq, s: _Pytest(voltas=7), dormir=lambda s: None, intervalo_s=30)
    avisos = [ln for ln in linhas if "SEM AMOSTRA do df há 90 s" in ln]
    assert rc == 0 and len(avisos) == 1


@pytest.mark.skipif(not WINDOWS, reason="usa _k32 (kernel32), que só existe no Windows (29.175: aparece no CI hospedado em Linux)")
def test_arvore_que_ja_saiu_nao_vira_kill_falho():
    """O pytest que saiu entre a amostra e o aborto: nada de `taskkill`, nada de ATENÇÃO."""
    chamadas: list[list[str]] = []
    assert pg.matar_arvore(_Pytest(voltas=0), lambda cmd: chamadas.append(list(cmd)) or _ok(rc=128)) is None
    assert chamadas == []


def test_docker_run_que_falha_diz_o_porque(tmp_path):
    """A imagem que falta (`--pull=never`) dizia só "não aceitou conexão"; agora o stderr do `docker run` vem junto."""
    def docker(cmd):
        if cmd[:2] == ["docker", "run"]:
            return subprocess.CompletedProcess(cmd, 125, "", "Unable to find image 'postgres:16' locally\n")
        return _ok()
    linhas: list[str] = []
    rc = pg.rodar_parte("pg parte 1/1", ["a"], tmp_path / "p.txt", linhas.append, executar=docker,
                        lancar=lambda a, s: pytest.fail("não roda o pytest"), dormir=lambda s: None)
    assert rc == 8
    assert any("docker run saiu com rc=125: Unable to find image" in ln for ln in linhas)


def test_o1_a_interrupcao_no_meio_mata_a_arvore_e_se_propaga(tmp_path):
    """O1 do 29.113: Ctrl-C durante as amostras não deixa o pytest -n 8 rodando contra o contêiner."""
    class _CtrlC(_Docker):
        def __call__(self, cmd):
            if cmd[:2] == ["docker", "exec"] and "df -m" in " ".join(cmd):
                raise KeyboardInterrupt
            return super().__call__(cmd)
    docker = _CtrlC([100])
    linhas: list[str] = []
    with pytest.raises(KeyboardInterrupt):
        pg.rodar_parte("pg parte 1/1", ["a"], tmp_path / "p.txt", linhas.append, executar=docker,
                       lancar=lambda arq, s: _Pytest(voltas=50), dormir=lambda s: None)
    assert _matou(docker)       # a árvore, não só o pai
    assert linhas[-1].startswith("pg parte 1/1 INTERROMPIDA")


def test_o1_o_relato_que_falha_tambem_mata_a_arvore(tmp_path, capsys):
    """O `--resumo` ilegível (OSError no relato) no meio da parte: a árvore morre, o erro sobe, e a linha de
    INTERROMPIDA, que o relato não consegue gravar, sai no stderr."""
    vezes = {"n": 0}

    def relatar(linha: str) -> None:
        vezes["n"] += 1
        if "SEM AMOSTRA" in linha or vezes["n"] > 1:
            raise OSError("disco do resumo cheio")
    class _SemDf(_Docker):
        def __call__(self, cmd):
            if cmd[:2] == ["docker", "exec"] and "df -m" in " ".join(cmd):
                self.chamadas.append(list(cmd))
                return _ok("")
            return super().__call__(cmd)
    docker = _SemDf([100])
    with pytest.raises(OSError):
        pg.rodar_parte("pg parte 1/1", ["a"], tmp_path / "p.txt", relatar, executar=docker,
                       lancar=lambda arq, s: _Pytest(voltas=50), dormir=lambda s: None)
    assert _matou(docker)
    assert "pg parte 1/1 INTERROMPIDA" in capsys.readouterr().err


def test_n3_o_pytest_que_saiu_antes_do_aborto_fica_com_o_rc_dele(tmp_path):
    """N3 do 29.113: a amostra passou do limite, mas o pytest já tinha saído (verde): rc 0, não "ABORTADA"."""
    class _SaiNaAmostra(_Pytest):
        """Vivo até a amostra do disco ser lida; sai (verde) enquanto ela é lida, antes do aborto."""
        def __init__(self) -> None:
            super().__init__(voltas=10**6, rc=0)
            self.saiu = False

        def poll(self):
            return 0 if self.saiu else None

        def wait(self, timeout=None):
            return 0
    proc = _SaiNaAmostra()

    class _DockerQueEncerra(_Docker):
        def __call__(self, cmd):
            if cmd[:2] == ["docker", "exec"] and "df -m" in " ".join(cmd):
                proc.saiu = True
            return super().__call__(cmd)
    docker = _DockerQueEncerra([3600])
    linhas: list[str] = []
    rc = pg.rodar_parte("pg parte 1/1", ["a"], tmp_path / "p.txt", linhas.append, executar=docker,
                        lancar=lambda arq, s: proc, dormir=lambda s: None)
    assert rc == 0 and not any("ABORTADA" in ln for ln in linhas)
    assert not _matou(docker)
    assert any("pico:" in ln and "88%" in ln for ln in linhas)


def test_n1_o_docker_stop_que_falha_avisa():
    linhas: list[str] = []
    ok = pg.parar(lambda cmd: subprocess.CompletedProcess(cmd, 124, "", "excedeu 30 s"), linhas.append)
    assert not ok and "docker stop farm-pg-rapido saiu com rc=124 (excedeu 30 s)" in linhas[-1]
    assert "docker rm -f farm-pg-rapido" in linhas[-1]
    assert pg.parar(lambda cmd: _ok(), linhas.append) and len(linhas) == 1


def _lista_de_dois(tmp_path, monkeypatch):
    backend = tmp_path / "backend" / "tests"
    backend.mkdir(parents=True)
    for i in range(2):
        (backend / f"test_{i}.py").write_text("")
    lista = tmp_path / "lista.txt"
    lista.write_text("tests/test_0.py\ntests/test_1.py", encoding="utf-8")
    monkeypatch.setattr(pg, "RAIZ", tmp_path)
    monkeypatch.setattr(pg, "ram_livre_gb", lambda: None)
    paradas: list[list[str]] = []
    monkeypatch.setattr(pg, "_executar", lambda cmd: paradas.append(list(cmd)) or _ok())
    return lista, paradas


def test_q2_interrompido_o_main_para_o_conteiner(tmp_path, monkeypatch):
    """Q2 da leitura do 29.113: Ctrl-C no meio da parte também para o contêiner (o tmpfs de 4 GB não fica na RAM)."""
    lista, paradas = _lista_de_dois(tmp_path, monkeypatch)

    def rodar(*a, **k):
        raise KeyboardInterrupt
    monkeypatch.setattr(pg, "rodar_parte", rodar)
    with pytest.raises(KeyboardInterrupt):
        pg.main(["--lista", str(lista), "--partes", "2", "--saidas", str(tmp_path)])
    assert paradas == [["docker", "stop", pg.NOME]]


def test_q2_o_conteiner_para_uma_vez_no_verde_e_no_vermelho(tmp_path, monkeypatch):
    lista, paradas = _lista_de_dois(tmp_path, monkeypatch)
    monkeypatch.setattr(pg, "rodar_parte", lambda *a, **k: 0)
    assert pg.main(["--lista", str(lista), "--partes", "2", "--saidas", str(tmp_path)]) == 0
    assert paradas == [["docker", "stop", pg.NOME]]
    paradas.clear()
    monkeypatch.setattr(pg, "rodar_parte", lambda *a, **k: 1)
    assert pg.main(["--lista", str(lista), "--partes", "2", "--saidas", str(tmp_path)]) == 1
    assert paradas == [["docker", "stop", pg.NOME]]


def test_q2_sem_parte_iniciada_nao_ha_o_que_parar(tmp_path, monkeypatch):
    lista, paradas = _lista_de_dois(tmp_path, monkeypatch)
    monkeypatch.setattr(pg, "ram_livre_gb", lambda: 0.5)
    monkeypatch.setattr(pg, "rodar_parte", lambda *a, **k: pytest.fail("não roda parte sem RAM"))
    assert pg.main(["--lista", str(lista), "--partes", "2", "--saidas", str(tmp_path)]) == 9
    assert paradas == []



def _carregar_pg() -> str:
    return (f"import importlib.util,sys; s=importlib.util.spec_from_file_location('pgr', r'{_SPEC.origin}'); "
            "m=importlib.util.module_from_spec(s); sys.modules['pgr']=m; s.loader.exec_module(m)")


def _esperar_pid(marca: Path) -> int:
    for _ in range(150):
        if marca.exists() and marca.read_text().strip():
            return int(marca.read_text())
        pg.time.sleep(0.1)
    raise AssertionError("o neto não escreveu o PID")


def _esperar_morte(pid: int, prazo_s: float = 10) -> bool:
    """Por relógio, não por voltas: o `tasklist` leva quase 1 s sob carga, e cem voltas passavam do sono do neto (a
    mutação "sem job" passava porque o neto morria sozinho)."""
    fim = pg.time.monotonic() + prazo_s
    while pg.time.monotonic() < fim:
        if not _vivo(pid):
            return True
        pg.time.sleep(0.2)
    return not _vivo(pid)


@pytest.mark.skipif(not WINDOWS, reason="Job Object é do Windows")
def test_29117_o_pai_morto_de_fora_leva_a_arvore_junto(tmp_path):
    """KILL_ON_JOB_CLOSE: o handle do job é só de quem lançou; morto de fora (TerminateProcess, como o pwsh fechado),
    o kernel mata a árvore inteira."""
    marca = tmp_path / "neto.pid"
    filho = f"import os,time; open(r'{marca}','w').write(str(os.getpid())); time.sleep(90)"
    pai = f"import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',{filho!r}]); time.sleep(90)"
    lancador = _carregar_pg() + f"; m.lancar_em_job([sys.executable,'-c',{pai!r}]); import time; time.sleep(90)"
    externo = subprocess.Popen([sys.executable, "-c", lancador])
    neto: int | None = None
    try:
        neto = _esperar_pid(marca)
        externo.kill()                                                 # o pai do pytest morre sem chance de limpar
        externo.wait(timeout=10)
        assert _esperar_morte(neto), "o neto sobreviveu ao lançador morto de fora"
        neto = None
    finally:
        if externo.poll() is None:
            externo.kill()
        if neto is not None and _vivo(neto):
            subprocess.run(["taskkill", "/F", "/PID", str(neto)], capture_output=True, check=False)


@pytest.mark.skipif(not WINDOWS, reason="Job Object é do Windows")
def test_29117_processo_fora_do_job_nunca_morre(tmp_path):
    """A regra do conserto: só morre o que é comprovadamente descendente do nosso pytest (está no job)."""
    marca = tmp_path / "filho.pid"
    filho = f"import os,time; open(r'{marca}','w').write(str(os.getpid())); time.sleep(90)"
    pai = f"import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',{filho!r}]); time.sleep(90)"
    de_fora = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(90)"])
    proc = pg.lancar_em_job([sys.executable, "-c", pai])
    try:
        neto = _esperar_pid(marca)
        assert pg.matar_arvore(proc, pg._executar) is None
        assert _esperar_morte(neto)
        assert de_fora.poll() is None, "o processo de fora do job morreu"
    finally:
        pg.fechar_job(proc)                                            # N2 da leitura do 29.117
        for p in (de_fora, proc):
            if p.poll() is None:
                p.kill()


@pytest.mark.skipif(not WINDOWS, reason="só o Windows tem o caminho sem job")
def test_29117_sem_job_so_o_proprio_processo_morre():
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(90)"])
    try:
        problema = pg.matar_arvore(proc, pg._executar)
        assert proc.poll() is not None
        assert problema and "não estava num job" in problema
    finally:
        if proc.poll() is None:
            proc.kill()


def test_29117_uma_rodada_por_vez_pela_trava(tmp_path):
    """Nota 3: a segunda rodada não toca no contêiner da primeira; a trava solta quando a primeira acaba."""
    tenta = _carregar_pg() + f"; m.NOME_DA_TRAVA = {pg.NOME_DA_TRAVA!r}; print(m.tentar_travar())"
    assert pg.tentar_travar()
    try:
        r = subprocess.run([sys.executable, "-c", tenta], capture_output=True, text=True, timeout=60, check=False)
        assert r.stdout.strip() == "False", r.stderr
    finally:
        pg.soltar_trava()
    r = subprocess.run([sys.executable, "-c", tenta], capture_output=True, text=True, timeout=60, check=False)
    assert r.stdout.strip() == "True", r.stderr


def test_29117_o_main_com_a_trava_ocupada_nao_toca_o_docker(tmp_path, monkeypatch):
    lista = tmp_path / "lista.txt"
    (tmp_path / "backend" / "tests").mkdir(parents=True)
    (tmp_path / "backend" / "tests" / "test_0.py").write_text("")
    lista.write_text("tests/test_0.py", encoding="utf-8")
    monkeypatch.setattr(pg, "RAIZ", tmp_path)
    monkeypatch.setattr(pg, "tentar_travar", lambda: False)
    monkeypatch.setattr(pg, "_executar", lambda cmd: pytest.fail(f"docker chamado: {cmd}"))
    assert pg.main(["--lista", str(lista), "--partes", "1", "--saidas", str(tmp_path)]) == 10



@pytest.mark.skipif(not WINDOWS, reason="Job Object é do Windows")
def test_29117_fora_do_job_segue_sem_job_e_diz(monkeypatch):
    """Cuidado 2: o script já num job que não aceita aninhamento. O pytest roda sem job, com o aviso, e nunca o /T."""
    def recusa(job, proc):
        raise OSError("AssignProcessToJobObject: erro 5 do Windows")
    monkeypatch.setattr(pg, "_entrar_no_job", recusa)
    proc = pg.lancar_em_job([sys.executable, "-c", "import sys; sys.exit(7)"])
    assert proc.wait(timeout=30) == 7                                  # retomado: rodou
    assert proc.job is None and "não entrou no job" in proc.aviso_do_job


def test_29117_o_aviso_do_job_vai_para_a_linha_da_parte(tmp_path):
    proc = _Pytest(voltas=0)
    proc.aviso_do_job = "o pytest não entrou no job (erro 5): segue sem job"
    linhas: list[str] = []
    pg.rodar_parte("pg parte 1/1", ["a"], tmp_path / "p.txt", linhas.append, executar=_Docker([100]),
                   lancar=lambda arq, s: proc, dormir=lambda s: None)
    assert any(ln.startswith("pg parte 1/1 ATENÇÃO: o pytest não entrou no job") for ln in linhas)


@pytest.mark.skipif(not WINDOWS, reason="Job Object é do Windows")
def test_29117_o_que_nao_retoma_morre_e_o_erro_sobe(monkeypatch):
    """Cuidado 3: o `ResumeThread` que falha não deixa um pytest suspenso para sempre."""
    criados: list = []
    popen = pg.subprocess.Popen

    def guardar(*a, **k):
        p = popen(*a, **k)
        criados.append(p)
        return p

    def falha(pid):
        raise OSError("ResumeThread: erro 5 do Windows")
    monkeypatch.setattr(pg.subprocess, "Popen", guardar)
    monkeypatch.setattr(pg, "_retomar", falha)
    with pytest.raises(OSError, match="ResumeThread"):
        pg.lancar_em_job([sys.executable, "-c", "import time; time.sleep(90)"])
    assert criados and criados[0].poll() is not None


def test_29117_o_pytest_que_nao_sobe_da_rc_11_com_a_linha(tmp_path):
    def falha(arq, s):
        raise OSError("ResumeThread: erro 5 do Windows")
    linhas: list[str] = []
    rc = pg.rodar_parte("pg parte 1/1", ["a"], tmp_path / "p.txt", linhas.append, executar=_Docker([100]),
                        lancar=falha, dormir=lambda s: None)
    assert rc == 11 and "o pytest NÃO SUBIU: ResumeThread: erro 5" in linhas[-1]


def _mutex_que_nega_tudo(nome: str) -> int:
    """Um mutex REAL com a DACL `D:(D;;GA;;;WD)` (nega tudo a todos), como o de uma rodada de outra sessão ou usuário."""
    import ctypes
    from ctypes import wintypes

    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]

    class _Atributos(ctypes.Structure):
        _fields_ = [("nLength", wintypes.DWORD), ("lpSecurityDescriptor", ctypes.c_void_p),
                    ("bInheritHandle", wintypes.BOOL)]
    sd = ctypes.c_void_p()
    assert adv.ConvertStringSecurityDescriptorToSecurityDescriptorW("D:(D;;GA;;;WD)", 1, ctypes.byref(sd), None)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.restype = wintypes.HANDLE
    k32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    attr = _Atributos(ctypes.sizeof(_Atributos), sd, False)
    h = k32.CreateMutexW(ctypes.byref(attr), False, nome)
    k32.LocalFree(sd)
    assert h, f"o mutex de teste não foi criado: erro {ctypes.get_last_error()}"
    return h


@pytest.mark.skipif(not WINDOWS, reason="mutex nomeado é do Windows")
def test_29117_o_mutex_alheio_que_nega_acesso_e_recusa_rc_10(tmp_path, monkeypatch, capsys):
    """M1 da leitura do 29.117: o erro 5 no Global\\ é o mutex de outra rodada, não falta de privilégio. Recusa (rc 10),
    sem recuar ao Local\\, que deixaria as duas rodadas correrem."""
    h = _mutex_que_nega_tudo(f"Global\\{pg.NOME_DA_TRAVA}")
    try:
        assert pg.tentar_travar() is False
        assert "existe e nega acesso (erro 5)" in pg.ESCOPO_DA_TRAVA[0]
        lista = tmp_path / "lista.txt"
        (tmp_path / "backend" / "tests").mkdir(parents=True)
        (tmp_path / "backend" / "tests" / "test_0.py").write_text("")
        lista.write_text("tests/test_0.py", encoding="utf-8")
        monkeypatch.setattr(pg, "RAIZ", tmp_path)
        monkeypatch.setattr(pg, "_executar", lambda cmd: pytest.fail(f"docker chamado: {cmd}"))
        assert pg.main(["--lista", str(lista), "--partes", "1", "--saidas", str(tmp_path)]) == 10
        assert "nega acesso (erro 5)" in capsys.readouterr().out
    finally:
        pg._fechar_handle(h)


@pytest.mark.skipif(not WINDOWS, reason="mutex nomeado é do Windows")
def test_29117_o_outro_erro_do_mutex_sobe(monkeypatch):
    def falha(h, inicial, nome):
        pg.ctypes.set_last_error(87)                                   # ERROR_INVALID_PARAMETER
        return None
    monkeypatch.setattr(pg._k32, "CreateMutexW", falha)
    with pytest.raises(OSError, match="erro 87"):
        pg.tentar_travar()


def test_29117_o_teste_nunca_usa_a_trava_da_rodada_real():
    """N3: o nome é o de teste em todo teste (a fixture), e a trava real segue livre para a rodada do funil."""
    assert pg.NOME_DA_TRAVA != pg.NOME and pg.NOME_DA_TRAVA.startswith(f"{pg.NOME}-teste-")


@pytest.mark.skipif(not WINDOWS, reason="o handle do job é do Windows")
def test_29117_n1_o_job_terminado_tem_o_handle_fechado_na_hora(tmp_path):
    """N1: aborto, interrupção e "já saiu" fecham o handle do job, sem esperar o script sair."""
    abortado = _Pytest(voltas=50)
    assert pg.matar_arvore(abortado, _Docker([100])) is None
    assert _JOBS_TERMINADOS == ["job-4242"] and _JOBS_FECHADOS == ["job-4242"] and abortado.job is None
    _JOBS_TERMINADOS.clear()
    _JOBS_FECHADOS.clear()
    saido = _Pytest(voltas=0)
    assert pg.matar_arvore(saido, _Docker([100])) is None
    assert _JOBS_FECHADOS == ["job-4242"] and saido.job is None
    _JOBS_FECHADOS.clear()

    class _JaSaiu(_Pytest):
        def poll(self):
            return 0 if self.voltas < 0 else super().poll()

    class _CtrlC(_Docker):
        def __call__(self, cmd):
            if cmd[:2] == ["docker", "exec"] and "df -m" in " ".join(cmd):
                proc.voltas = -1                                       # saiu, e o Ctrl-C chega em seguida
                raise KeyboardInterrupt
            return super().__call__(cmd)
    proc = _JaSaiu(voltas=50)
    with pytest.raises(KeyboardInterrupt):
        pg.rodar_parte("pg parte 1/1", ["a"], tmp_path / "p.txt", lambda ln: None, executar=_CtrlC([100]),
                       lancar=lambda arq, s: proc, dormir=lambda s: None)
    assert _JOBS_FECHADOS == ["job-4242"] and proc.job is None


def test_29117_a_trava_que_nao_se_cria_nao_roda_calada(tmp_path, monkeypatch):
    lista = tmp_path / "lista.txt"
    (tmp_path / "backend" / "tests").mkdir(parents=True)
    (tmp_path / "backend" / "tests" / "test_0.py").write_text("")
    lista.write_text("tests/test_0.py", encoding="utf-8")
    monkeypatch.setattr(pg, "RAIZ", tmp_path)

    def nao_cria():
        raise OSError("CreateMutex Local: erro 5 do Windows")
    monkeypatch.setattr(pg, "tentar_travar", nao_cria)
    monkeypatch.setattr(pg, "_executar", lambda cmd: pytest.fail(f"docker chamado: {cmd}"))
    linhas: list[str] = []
    monkeypatch.setattr("builtins.print", lambda *a, **k: linhas.append(" ".join(map(str, a))))
    assert pg.main(["--lista", str(lista), "--partes", "1", "--saidas", str(tmp_path)]) == 12
    assert any("a trava de uma rodada por vez não pôde ser criada" in ln for ln in linhas)



def test_n4_o_stop_que_falha_no_finally_nao_troca_o_motivo(tmp_path, monkeypatch, capsys):
    """N4 da leitura do 29.113: interrompido por um OSError do relato, o `parar` que também levanta não substitui a
    exceção original; o aviso do stop vai ao stderr."""
    lista, _ = _lista_de_dois(tmp_path, monkeypatch)

    def rodar(*a, **k):
        raise KeyboardInterrupt("o motivo de verdade")

    def parar(executar, relatar):
        raise OSError("disco do resumo cheio")
    monkeypatch.setattr(pg, "rodar_parte", rodar)
    monkeypatch.setattr(pg, "parar", parar)
    with pytest.raises(KeyboardInterrupt, match="o motivo de verdade"):
        pg.main(["--lista", str(lista), "--partes", "2", "--saidas", str(tmp_path)])
    assert "o docker stop farm-pg-rapido não terminou (OSError: disco do resumo cheio)" in capsys.readouterr().err


def test_parar_sem_conteiner_nao_da_alarme_falso():
    linhas: list[str] = []
    resposta = subprocess.CompletedProcess([], 1, "", "Error response from daemon: No such container: farm-pg-rapido")
    assert pg.parar(lambda cmd: resposta, linhas.append) and linhas == []
