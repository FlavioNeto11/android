"""29.156 (fatia 1): o amostrador permanente do host roda de verdade, em pasta descartável.

Simulado (`simulated`): contadores reais do host de teste, mas a saída vai para uma pasta temporária e o banco de avisos é um
SQLite montado aqui. O registro da tarefa agendada (`-Instalar`) NÃO é executado: só o texto do bloco é conferido.
"""
from __future__ import annotations

import re
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
PWSH = shutil.which("pwsh")
AMOSTRADOR = SCRIPTS / "amostrador-host.ps1"
CABECALHO = ("ts_utc,cpu_host_pct,vm_convidado_nucleos,vmmem_ws_mb,qemu_host_pct,ram_livre_mb,disco_livre_gb,"
             "processos_top,avisos_pressao,cpu_media_pct,demais_processos_pct,nao_atribuido_pct")

precisa_pwsh = pytest.mark.skipif(PWSH is None or sys.platform != "win32", reason="precisa de pwsh no Windows")


_mutex_do_teste = ""


@pytest.fixture(autouse=True)
def _mutex_proprio():
    """Nome de mutex só deste teste: o amostrador real do host (`Global\\farm-amostrador-host`) nunca pode fazer um teste sair com 3."""
    global _mutex_do_teste
    _mutex_do_teste = f"Local\\farm-amostrador-teste-{uuid.uuid4().hex}"
    yield
    _mutex_do_teste = ""


def _rodar(saida: Path, *extra: str, amostras: int = 2, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run([PWSH, "-NoProfile", "-File", str(AMOSTRADOR), "-Saida", str(saida), "-Amostras", str(amostras),
                           "-IntervaloS", "3", "-JanelaS", "1", "-Python", sys.executable, "-NomeDoMutex", _mutex_do_teste, *extra],
                          capture_output=True, text=True, timeout=timeout)


def _linhas(saida: Path) -> list[str]:
    arquivos = sorted(saida.glob("*.csv"))
    assert arquivos, "nenhum CSV gravado"
    return arquivos[-1].read_text(encoding="utf-8").splitlines()


@precisa_pwsh
class TestAmostrador:
    def test_grava_cabecalho_e_linhas_com_formato_estavel(self, tmp_path):
        r = _rodar(tmp_path / "s")
        assert r.returncode == 0, r.stderr + r.stdout
        linhas = _linhas(tmp_path / "s")
        assert linhas[0] == CABECALHO and len(linhas) == 3
        for linha in linhas[1:]:
            assert not linha.split(",")[1] == "erro", linha
            colunas = linha.split(",")
            assert len(colunas) == 12, linha
            assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", colunas[0])
            assert re.fullmatch(r"\d+\.\d", colunas[1]), "decimal com ponto, em qualquer cultura do host"
            assert re.fullmatch(r"\d+\.\d", colunas[6])
        assert linhas[1].split(",")[4] == "", "qemu_host_pct vazio na 1ª linha (sem referência anterior)"

    def test_colunas_da_media_do_minuto_e_do_que_o_topo_nao_mostra(self, tmp_path):
        r = _rodar(tmp_path / "s", amostras=3)
        assert r.returncode == 0, r.stderr + r.stdout
        linhas = _linhas(tmp_path / "s")
        assert linhas[1].split(",")[9:] == ["", "", ""], "1ª linha sem referência anterior: colunas novas vazias"
        for linha in linhas[2:]:
            media, demais, nao_atrib = linha.split(",")[9:]
            assert re.fullmatch(r"\d+\.\d", media) and 0.0 <= float(media) <= 100.0, linha
            assert re.fullmatch(r"\d+\.\d", demais), linha
            assert re.fullmatch(r"-?\d+\.\d", nao_atrib), linha

    def test_arquivo_do_dia_de_versao_antiga_ganha_o_novo_cabecalho_uma_vez(self, tmp_path):
        saida = tmp_path / "s"
        saida.mkdir()
        antigo = CABECALHO.rsplit(",", 3)[0]
        hoje = datetime.now(timezone.utc).strftime("%Y%m%d")
        (saida / f"{hoje}.csv").write_text(antigo + "\n2026-01-01T00:00:00Z,10.0,,,,,,,\n", encoding="utf-8")
        r = _rodar(saida, amostras=2)
        assert r.returncode == 0, r.stderr + r.stdout
        linhas = (saida / f"{hoje}.csv").read_text(encoding="utf-8").splitlines()
        assert linhas[0] == antigo and linhas.count(CABECALHO) == 1 and linhas.index(CABECALHO) == 2, linhas[:4]

    def test_processos_top_traz_so_nomes_e_percentuais(self, tmp_path):
        _rodar(tmp_path / "s")
        for linha in _linhas(tmp_path / "s")[1:]:
            top = linha.split(",")[7]
            for item in filter(None, top.split(";")):
                assert re.fullmatch(r"[^:;,\\/ ]+:\d+\.\d", item), f"item inesperado (caminho/linha de comando?): {item}"

    def test_avisos_de_pressao_saem_por_aparelho_do_banco_em_somente_leitura(self, tmp_path):
        banco = tmp_path / "poc.sqlite3"
        con = sqlite3.connect(banco)
        con.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, ts TEXT, kind TEXT, instance_id TEXT, message TEXT)")
        agora = datetime.now(timezone.utc)
        for i, inst in enumerate(["android-05", "android-05", "android-01"]):
            # no futuro próximo: o amostrador sobe 1-2 s depois e só olha para trás ($IntervaloS); o filtro é `ts >= desde`
            ts = (agora + timedelta(seconds=30 + i)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
            con.execute("INSERT INTO events (ts, kind, instance_id, message) VALUES (?,?,?,?)",
                        (ts, "instance.updated", inst, f"{inst}: Convidado sob pressão de CPU: load 9.0 em 2 vCPU"))
        con.execute("INSERT INTO events (ts, kind, instance_id, message) VALUES (?,?,?,?)",
                    ((agora + timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%S.000Z"), "instance.updated", "android-09", "outro aviso qualquer"))
        con.commit()
        con.close()
        antes = banco.read_bytes()
        r = _rodar(tmp_path / "s", "-Banco", str(banco), amostras=1)
        assert r.returncode == 0, r.stderr
        avisos = _linhas(tmp_path / "s")[1].split(",")[8]
        assert avisos == "android-05:2;android-01:1", avisos
        assert "android-09" not in avisos
        assert banco.read_bytes() == antes, "o amostrador não escreve no banco"

    def test_medido_sem_aviso_e_zero_e_nao_vazio(self, tmp_path):
        banco = tmp_path / "poc.sqlite3"
        con = sqlite3.connect(banco)
        con.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, ts TEXT, kind TEXT, instance_id TEXT, message TEXT)")
        con.commit()
        con.close()
        r = _rodar(tmp_path / "s", "-Banco", str(banco), amostras=1)
        assert r.returncode == 0, r.stderr
        assert _linhas(tmp_path / "s")[1].split(",")[8] == "0", "'0' = medido e nenhum aviso; vazio = não medido"

    def test_saida_que_e_juncao_e_recusada_e_nada_e_apagado(self, tmp_path):
        alvo = tmp_path / "alvo"
        alvo.mkdir()
        velho = alvo / f"{(datetime.now(timezone.utc).date() - timedelta(days=20)):%Y%m%d}.csv"
        velho.write_text("x", encoding="utf-8")
        juncao = tmp_path / "juncao"
        subprocess.run(["cmd", "/c", "mklink", "/J", str(juncao), str(alvo)], check=True, capture_output=True)
        try:
            r = _rodar(juncao, amostras=1)
            assert r.returncode == 4, r.stdout + r.stderr
            assert velho.exists(), "a retenção não pode apagar através da junção"
        finally:
            subprocess.run(["cmd", "/c", "rmdir", str(juncao)], capture_output=True)  # remove só a junção, nunca o alvo

    def test_banco_ausente_deixa_a_coluna_vazia_e_nao_para(self, tmp_path):
        r = _rodar(tmp_path / "s", "-Banco", str(tmp_path / "nao-existe.sqlite3"), amostras=1)
        assert r.returncode == 0, r.stderr
        assert _linhas(tmp_path / "s")[1].split(",")[8] == ""

    def test_retencao_apaga_so_csv_de_dia_mais_velho_que_7_dias(self, tmp_path):
        saida = tmp_path / "s"
        saida.mkdir()
        hoje = datetime.now(timezone.utc).date()
        velho = saida / f"{(hoje - timedelta(days=9)):%Y%m%d}.csv"
        limite = saida / f"{(hoje - timedelta(days=6)):%Y%m%d}.csv"
        outro = saida / "anotacao.csv"
        txt = saida / f"{(hoje - timedelta(days=30)):%Y%m%d}.txt"
        for f in (velho, limite, outro, txt):
            f.write_text("x", encoding="utf-8")
        fora = tmp_path / f"{(hoje - timedelta(days=30)):%Y%m%d}.csv"
        fora.write_text("x", encoding="utf-8")
        r = _rodar(saida, amostras=1)
        assert r.returncode == 0, r.stderr
        assert not velho.exists()
        assert limite.exists() and outro.exists() and txt.exists(), "só AAAAMMDD.csv, e só os de mais de 7 dias"
        assert fora.exists(), "nada fora da pasta de saída é apagado"

    def test_so_uma_instancia_por_host(self, tmp_path):
        primeiro = subprocess.Popen([PWSH, "-NoProfile", "-File", str(AMOSTRADOR), "-Saida", str(tmp_path / "a"), "-Amostras", "3",
                                     "-IntervaloS", "4", "-JanelaS", "1", "-Python", sys.executable, "-NomeDoMutex", _mutex_do_teste],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            time.sleep(4)  # o pwsh do primeiro já pegou o mutex
            segundo = _rodar(tmp_path / "b", amostras=1)
            assert segundo.returncode == 3, segundo.stdout + segundo.stderr
            assert "ja ha um em execucao" in segundo.stdout
            assert not (tmp_path / "b").exists() or not list((tmp_path / "b").glob("*.csv"))
        finally:
            primeiro.wait(timeout=60)
        assert primeiro.returncode == 0

    def test_nome_de_processo_e_sanitizado_antes_de_ir_ao_csv(self):
        texto = AMOSTRADOR.read_text(encoding="utf-8")
        assert "-replace '[^A-Za-z0-9._-]', '_'" in texto and "-replace '^[=+@-]+', '_'" in texto

    def test_o_mutex_padrao_continua_global_e_so_o_parametro_o_troca(self):
        texto = AMOSTRADOR.read_text(encoding="utf-8")
        assert "[string]$NomeDoMutex = 'Global\\farm-amostrador-host'" in texto
        assert "New-Object Threading.Mutex($false, $NomeDoMutex)" in texto

    def test_o_script_baixa_a_propria_prioridade(self):
        texto = AMOSTRADOR.read_text(encoding="utf-8")
        assert "PriorityClass = 'Idle'" in texto


class TestTextoDoScript:
    def _codigo(self) -> str:
        texto = AMOSTRADOR.read_text(encoding="utf-8")
        texto = texto[texto.index("#>") + 2:]
        return "\n".join(l for l in texto.splitlines() if not l.lstrip().startswith("#"))

    def test_nao_toca_segredo_nem_config_da_instalacao(self):
        codigo = self._codigo() + (SCRIPTS / "amostrador-host-pressao.py").read_text(encoding="utf-8")
        for proibido in (".env", "config.yaml", "credentials", "secret", "Invoke-WebRequest", "Invoke-RestMethod", "curl"):
            assert proibido not in codigo, proibido

    def test_instalar_registra_sem_iniciar_e_com_uma_instancia(self):
        texto = AMOSTRADOR.read_text(encoding="utf-8")
        bloco = texto[texto.index("if ($Instalar)"):texto.index("try { (Get-Process")]
        assert "return" in bloco and "farm-amostrador-host" in texto
        assert "-AtStartup" in bloco and "-Daily" in bloco
        assert "IgnoreNew" in bloco and "RestartCount" in bloco
        assert "Start-ScheduledTask" not in bloco

    def test_a_retencao_so_olha_a_pasta_de_saida(self):
        codigo = self._codigo()
        assert "Remove-Item" in codigo
        assert codigo.count("Remove-Item") == 1 and "Get-ChildItem -LiteralPath $Saida" in codigo


def test_ajudante_de_pressao_rejeita_instante_malformado_e_nunca_falha(tmp_path):
    banco = tmp_path / "poc.sqlite3"
    sqlite3.connect(banco).close()
    for args in ([str(banco), "ontem"], [str(banco)], [str(tmp_path / "nada.sqlite3"), "2026-10-06T00:00:00"]):
        r = subprocess.run([sys.executable, str(SCRIPTS / "amostrador-host-pressao.py"), *args], capture_output=True, text=True)
        assert r.returncode == 0 and r.stdout.strip() == ""
