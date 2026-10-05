"""Testes puros do PG rápido da suíte (scripts/pg-rapido.py, 29.99), com docker e pytest de mentira."""
import importlib.util
import subprocess
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location("pg_rapido", Path(__file__).resolve().parents[1] / "pg-rapido.py")
pg = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(pg)


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

    def poll(self):
        if self.morto:
            return 1
        self.voltas -= 1
        return None if self.voltas >= 0 else self.rc

    def wait(self, timeout=None):
        return 1 if self.morto else self.rc


def test_o_conteiner_sobe_com_wal_minimo_tmpfs_de_4g_e_so_no_loopback():
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
    if pg.os.name == "nt":
        assert ["taskkill", "/T", "/F", "/PID", "4242"] in docker.chamadas   # a árvore, não só o pai (K-099)
    assert linhas[-1].startswith("pg parte 1/1 ABORTADA pelo disco:") and "tmpfs 3600 de 4096 MB (88%)" in linhas[-1]


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
