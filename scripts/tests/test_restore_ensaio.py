"""29.167: o ensaio semanal de restauração roda de verdade, sobre uma cópia de mentira, numa árvore descartável.

Simulado (`simulated`): o banco é gerado pelas migrações do código, a cópia e o manifesto são montados aqui; nada toca o
`data\\` do ambiente central. O `restore-ensaio.ps1` usa a pasta-mãe do próprio script como raiz, então o teste copia
`scripts\\` para uma árvore temporária e roda LÁ (veredito, histórico e pasta de trabalho caem na árvore temporária).
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
BACKEND = ROOT / "backend"
PWSH = shutil.which("pwsh")

precisa_pwsh = pytest.mark.skipif(PWSH is None, reason="sem pwsh nesta máquina")
SENTINELA = "SENTINELA-SEGREDO-9f3c"


def _confere(banco: Path, *extra: str) -> dict:
    r = subprocess.run([sys.executable, str(SCRIPTS / "restore-ensaio-confere.py"), str(banco), *extra],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _banco_migrado(destino: Path) -> Path:
    sys.path.insert(0, str(BACKEND))
    from app.db import Database  # noqa: PLC0415

    destino.parent.mkdir(parents=True, exist_ok=True)
    Database(str(destino)).migrate()
    # O banco está em WAL: sem o checkpoint, as últimas migrações ficam só no `-wal` e a cópia do arquivo principal (shutil.copy)
    # nasce uma ou duas migrações atrás do código.
    con = sqlite3.connect(destino)
    try:
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        con.close()
    return destino


@pytest.fixture(scope="module")
def banco_modelo(tmp_path_factory) -> Path:
    return _banco_migrado(tmp_path_factory.mktemp("modelo") / "poc.sqlite3")


class TestConferencia:
    def test_banco_integro_confere_e_migra_sem_nada_a_aplicar(self, banco_modelo, tmp_path):
        copia = tmp_path / "poc.sqlite3"
        shutil.copy(banco_modelo, copia)
        r = _confere(copia, "--backend", str(BACKEND), "--migrar")
        assert r["erro"] is None
        assert r["integridade"] == "ok"
        assert r["tabelas"] > 50
        assert r["migracao_da_copia"] == r["migracao_do_codigo"]
        assert r["migracoes_aplicadas_na_copia"] == []

    def test_copia_antiga_recebe_as_migracoes_que_faltam_so_na_copia(self, banco_modelo, tmp_path):
        copia = tmp_path / "poc.sqlite3"
        shutil.copy(banco_modelo, copia)
        con = sqlite3.connect(copia)
        ultima = con.execute("SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1").fetchone()[0]
        con.execute("DELETE FROM schema_migrations WHERE version = ?", (ultima,))
        con.commit()
        con.close()
        antes = banco_modelo.read_bytes()
        r = _confere(copia, "--backend", str(BACKEND))  # sem --migrar: só lê
        assert r["migracao_da_copia"] != ultima and r["migracoes_aplicadas_na_copia"] == []
        assert banco_modelo.read_bytes() == antes, "a conferência não pode alterar o banco de origem"

    def test_arquivo_que_nao_e_banco_vira_erro_sem_ecoar_o_conteudo(self, tmp_path):
        lixo = tmp_path / "poc.sqlite3"
        lixo.write_bytes(b"isto nao e sqlite: valor-sigiloso-123" * 50)
        r = _confere(lixo)
        assert r["erro"] and "valor-sigiloso-123" not in json.dumps(r)

    def test_nao_le_valor_de_tabela(self):
        texto = (SCRIPTS / "restore-ensaio-confere.py").read_text(encoding="utf-8")
        assert "SELECT *" not in texto.upper().replace("COUNT(*)", "")


def _arvore(tmp_path: Path, banco: Path, *, idade_h: float = 1.0, tabelas_do_manifesto: int | None = None,
            migracao_do_manifesto: str | None = None, commit: str = "abc123") -> Path:
    """Árvore descartável: scripts\\ copiada e uma cópia de backup montada à mão com manifesto."""
    raiz = tmp_path / "arv"
    shutil.copytree(SCRIPTS, raiz / "scripts", ignore=shutil.ignore_patterns("tests", "__pycache__"))
    carimbo = (datetime.now() - timedelta(hours=idade_h)).strftime("%Y%m%d-%H%M%S")
    pasta = raiz / "data" / "backups" / carimbo
    pasta.mkdir(parents=True)
    # A cópia e o número do manifesto vêm da MESMA ferramenta que o backup de verdade usa (sqlite-copia.py), não de uma
    # contagem do próprio teste: o manifesto conta também as tabelas internas (sqlite_sequence, sqlite_stat1).
    fonte = tmp_path / "fonte.sqlite3"
    shutil.copy(banco, fonte)
    con = sqlite3.connect(fonte)
    con.execute("ANALYZE")  # cria sqlite_stat1, como no banco real
    con.commit()
    con.close()
    r = subprocess.run([sys.executable, str(SCRIPTS / "sqlite-copia.py"), str(fonte), str(pasta / "poc.sqlite3")],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    info = json.loads(r.stdout.splitlines()[-1])
    migracao, tabelas = info["migration"], info["tables"]
    manifesto = {"ts": (datetime.now(timezone.utc) - timedelta(hours=idade_h)).isoformat(), "origem": "diario",
                 "commit": commit, "banco": "sqlite", "arquivo": "poc.sqlite3",
                 "bytes": (pasta / "poc.sqlite3").stat().st_size,
                 "migration": migracao_do_manifesto or migracao,
                 "tables": tabelas_do_manifesto if tabelas_do_manifesto is not None else tabelas,
                 "config": False, "credentials_key": False}
    (pasta / "manifesto.json").write_text(json.dumps(manifesto), encoding="utf-8")
    # A cópia real traz a chave do cofre e a config: o restore.ps1 as copia para a pasta de trabalho, e o ensaio as apaga.
    (pasta / ("credentials" + ".key")).write_text(SENTINELA, encoding="utf-8")
    (pasta / "config").mkdir()
    (pasta / "config" / "config.yaml").write_text("x: " + SENTINELA, encoding="utf-8")
    return raiz


def _ensaio(raiz: Path, *extra: str) -> tuple[int, dict | None, str]:
    r = subprocess.run([PWSH, "-NoProfile", "-File", str(raiz / "scripts" / "restore-ensaio.ps1"),
                        "-Python", sys.executable, "-Backend", str(BACKEND), *extra],
                       capture_output=True, text=True, timeout=300)
    ultimo = raiz / "data" / "restore-ensaio" / "ultimo.json"
    return r.returncode, (json.loads(ultimo.read_text(encoding="utf-8")) if ultimo.exists() else None), r.stdout + r.stderr


@precisa_pwsh
class TestEnsaio:
    def test_cópia_boa_da_ok_migra_na_copia_e_limpa_a_pasta_de_trabalho(self, banco_modelo, tmp_path):
        raiz = _arvore(tmp_path, banco_modelo)
        codigo, v, saida = _ensaio(raiz)
        assert codigo == 0, saida
        assert v["resultado"] == "ok" and v["integridade"] == "ok"
        assert v["migracao_da_copia"] == v["migracao_do_manifesto"] == v["migracao_do_codigo"]
        assert v["commit_da_copia"] == "abc123" and 0 <= v["idade_h"] < 3
        assert not any((raiz / "data" / "restore-ensaio" / "trabalho").iterdir()), "a cópia de trabalho tem de sumir"
        linhas = (raiz / "data" / "restore-ensaio" / "historico.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(linhas) == 1 and json.loads(linhas[0])["resultado"] == "ok"

    def test_chave_restaurada_some_e_nada_vaza_no_veredito(self, banco_modelo, tmp_path):
        raiz = _arvore(tmp_path, banco_modelo)
        codigo, v, saida = _ensaio(raiz)
        assert codigo == 0, saida
        tudo = saida + json.dumps(v) + (raiz / "data" / "restore-ensaio" / "historico.jsonl").read_text(encoding="utf-8")
        assert SENTINELA not in tudo and str(raiz) not in tudo
        achados = [p for p in (raiz / "data").rglob("*") if p.is_file() and SENTINELA in p.read_text(errors="ignore")
                   and "backups" not in p.parts]
        assert achados == [], "sobrou cópia da chave fora de data\\backups"

    def test_sobra_de_ensaio_anterior_e_varrida_no_inicio(self, banco_modelo, tmp_path):
        raiz = _arvore(tmp_path, banco_modelo)
        velha = raiz / "data" / "restore-ensaio" / "trabalho" / "20200101-000000"
        velha.mkdir(parents=True)
        (velha / ("credentials" + ".key")).write_text(SENTINELA, encoding="utf-8")
        codigo, v, saida = _ensaio(raiz)
        assert codigo == 0, saida
        assert not velha.exists() and v["sobras_antigas"] == 0

    def test_manifesto_ilegivel_nao_conta_como_sucesso(self, banco_modelo, tmp_path):
        raiz = _arvore(tmp_path, banco_modelo)
        manifesto = next((raiz / "data" / "backups").iterdir()) / "manifesto.json"
        manifesto.write_text("{ isto nao e json", encoding="utf-8")
        codigo, v, _ = _ensaio(raiz)
        assert codigo == 1 and "manifesto" in v["motivo"]

    def test_falha_do_restore_nao_vaza_caminho(self, banco_modelo, tmp_path):
        raiz = _arvore(tmp_path, banco_modelo)
        (raiz / "scripts" / "restore.ps1").write_text(f'throw "falhou em {tmp_path} {SENTINELA}"', encoding="utf-8")
        codigo, v, saida = _ensaio(raiz)
        assert codigo == 1 and v["motivo"].startswith("restore.ps1 falhou")
        assert str(tmp_path) not in json.dumps(v) and SENTINELA not in json.dumps(v)
        assert str(tmp_path) not in saida.split("restore-ensaio:")[-1]

    def test_o_banco_de_origem_e_o_backup_ficam_intactos(self, banco_modelo, tmp_path):
        raiz = _arvore(tmp_path, banco_modelo)
        cópia = next((raiz / "data" / "backups").iterdir()) / "poc.sqlite3"
        antes = cópia.read_bytes()
        codigo, _, saida = _ensaio(raiz)
        assert codigo == 0, saida
        assert cópia.read_bytes() == antes
        assert not (raiz / "data" / "poc.sqlite3").exists(), "o ensaio nunca cria nem toca data\\poc.sqlite3"

    def test_migracao_do_manifesto_diferente_falha(self, banco_modelo, tmp_path):
        raiz = _arvore(tmp_path, banco_modelo, migracao_do_manifesto="001_inexistente")
        codigo, v, _ = _ensaio(raiz)
        assert codigo == 1 and v["resultado"] == "falhou" and "manifesto" in v["motivo"]

    def test_tabelas_do_manifesto_diferentes_falha(self, banco_modelo, tmp_path):
        raiz = _arvore(tmp_path, banco_modelo, tabelas_do_manifesto=3)
        codigo, v, _ = _ensaio(raiz)
        assert codigo == 1 and "tabelas" in v["motivo"]

    def test_copia_com_mais_de_48h_falha_porque_o_backup_diario_parou(self, banco_modelo, tmp_path):
        raiz = _arvore(tmp_path, banco_modelo, idade_h=60)
        codigo, v, _ = _ensaio(raiz)
        assert codigo == 1 and "48 h" in v["motivo"]

    def test_banco_corrompido_falha_sem_vazar_conteudo(self, banco_modelo, tmp_path):
        raiz = _arvore(tmp_path, banco_modelo)
        cópia = next((raiz / "data" / "backups").iterdir()) / "poc.sqlite3"
        cópia.write_bytes(b"corrompido valor-sigiloso-123 " * 200)
        codigo, v, saida = _ensaio(raiz)
        assert codigo == 1 and v["resultado"] == "falhou"
        assert "valor-sigiloso-123" not in json.dumps(v) and "valor-sigiloso-123" not in saida

    def test_sem_copia_nenhuma_e_pulado_com_codigo_2(self, tmp_path):
        raiz = tmp_path / "arv"
        shutil.copytree(SCRIPTS, raiz / "scripts", ignore=shutil.ignore_patterns("tests", "__pycache__"))
        (raiz / "data" / "backups").mkdir(parents=True)
        codigo, v, _ = _ensaio(raiz)
        assert codigo == 2 and v["resultado"] == "pulado"

    def test_instalar_nao_roda_o_ensaio(self):
        texto = (SCRIPTS / "restore-ensaio.ps1").read_text(encoding="utf-8")
        i = texto.index("if ($Instalar)")
        bloco = texto[i:texto.index("try { (Get-Process")]
        assert "return" in bloco and "farm-restore-ensaio" in texto
        assert "-Weekly" in bloco and "Sunday" in bloco
        assert "restore.ps1" not in bloco

    def test_nao_usa_confirmar_nem_mexe_no_banco_vivo(self):
        texto = (SCRIPTS / "restore-ensaio.ps1").read_text(encoding="utf-8")
        texto = texto[texto.index("#>") + 2:]  # sem o bloco de ajuda, que cita o -Confirmar para dizer que NÃO usa
        codigo = "\n".join(l for l in texto.splitlines() if not l.lstrip().startswith("#"))
        assert "-Confirmar" not in codigo
        assert "poc.sqlite3" in codigo  # só para checar a cópia mais nova
        assert "data\\poc.sqlite3" not in codigo
